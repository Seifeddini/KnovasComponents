"""The GitHub connector of connector protocol 2.0 (draft; FEAT-2026-11-10-write-back C-3, BET-2026-11-write-back WB-12),
built on the connector kit (``knovas_connector_kit``, vendored beside this package). Only while ``SYNC_PROTOCOL_V2``
is on; protocol 1.x's fetch and write jobs (``github_jobs.py``) are unchanged.

Everything specific to GitHub lives here, none of it in the protocol (SPEC-write-back-16):

- **Source kind** ``github``. An item's ``external_id`` is its path, its ``version`` the file's blob sha. Reading
  stays protocol 1.x's for now (brain-runner reads the repository); the connector declares no stream.
- **One action, ``set_front_matter``**: change top-level front-matter values of one Markdown file, as brain-runner's
  inverse mapping allows them (``compile-write``: only facts a mapping's ``writes`` names, never a computed one).
  Parameters: ``mapping`` (required) and one optional parameter per fact a published mapping of this component's
  sources writes. ``preview: exact``, ``staging: [direct, native_review]``, ``grouping: none`` (one file per step),
  ``inverse: null`` (no undo in V1, the founder's decision of 2026-11-10).
- **preview** reads the file at the head of the source's branch (or at ``at_version``), compiles the change and
  edits the front matter in memory: before and after per key and a diff, at the blob sha it read. A computed fact
  makes the preview inexact (``computed_field``); nothing is written.
- **act, ``direct``** (the default for the Knovas tenant, D-3): one commit on the source's branch through the
  contents API with the expected blob sha, so a file that changed since the preview is a ``conflict`` and nothing is
  written. The commit message carries ``Knovas-Permit: <permit id>``; a repeat finds that commit and answers its
  receipt without writing again. The receipt comes from reading the file again.
- **act, ``native_review``**: today's pull request (``github_jobs.GitHub.propose``) on branch
  ``knovas/write-<permit id>``; ``stage_ref`` is the pull request's address. ``review_outcome`` answers pending,
  accepted (merged; receipts from the branch's file) or declined (closed without merge). No ``unstage`` in V1.
- **reread** reads files again and finds earlier writes by their permit ids.
- **Steps** (v2.0 as amended 2026-11-10, D-14): every change, difference and conflict names its step, the index into
  the job's ``args.steps``. A preview may hold several steps, also two on one file; an ``act`` holds one (grouping
  none), so a receipt's step is always 0, also in a ``reread`` or ``review_outcome`` result.

Logs carry codes and counts only: never a path, a value, a token or a repository (GI-DATA-02, GI-DATA-04).
"""
from __future__ import annotations

import base64
import difflib
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import quote

import yaml

from knovas_connector_kit import PROTOCOL, SUITE_VERSION
from knovas_connector_kit.connector import Connector, outcome
from sync_component import front_matter_edit
from sync_component.github_jobs import MAX_BYTES, GitHub, Outcome, _repository, valid_path

log = logging.getLogger("sync_component.github_v2")

ACTION = "set_front_matter"
SOURCE_KIND = "github"
VERSION = "0.1.0"
BRANCH_PREFIX = "knovas/write-"
TRAILER = "Knovas-Permit: "
_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
_PULL = re.compile(r"/pull/([0-9]{1,9})$")
#: The conformance properties this connector's suite run passed (tests/test_sync_component_github_v2.py runs it).
#: Reading stays protocol 1.x's for now (brain-runner reads the repository, GI-CONN-01/02 hold there), so the
#: connector declares no stream and the read properties are not applicable.
CONFORMANCE_PASSED = ["preview_equals_receipt", "exactly_once", "permit_refusals", "no_credential_leaves_site",
                      "review_outcome"]


def writable_facts(package: Mapping[str, Any]) -> list[str]:
    """The facts the package's mappings write (``writes: {type: {fact: …}}``), as declaration names."""
    found: set = set()
    for workspace in package.get("workspaces") or []:
        for name, raw in ((workspace or {}).get("files") or {}).items():
            if not str(name).endswith((".yaml", ".yml")):
                continue
            try:
                docs = list(yaml.safe_load_all(base64.b64decode(raw)))
            except (ValueError, yaml.YAMLError):
                continue
            for doc in docs:
                mappings = doc.get("mappings") if isinstance(doc, dict) and isinstance(doc.get("mappings"), list) \
                    else [doc]
                for m in mappings:
                    writes = m.get("writes") if isinstance(m, dict) else None
                    if isinstance(writes, dict):
                        for facts in writes.values():
                            if isinstance(facts, dict):
                                found |= {f for f in facts if isinstance(f, str) and _NAME.match(f) and f != "mapping"}
    return sorted(found)


class GitHubConnector(Connector):
    """``source(key)`` the source Link assigned; ``credential(source)`` its token at the site; ``compile(source)``
    brain-runner's compile-write for a v1-shaped write job; ``facts()`` the declared writable facts."""

    def __init__(self, *, source: Callable[[str], Optional[dict]], credential: Callable[[Mapping], Optional[str]],
                 compile: Callable[[Mapping], Callable[[Mapping], tuple[str, Any]]], facts: Callable[[], list],
                 data_dir: Path, session: Any = None, report_sha256: str = "0" * 64, api_base: Optional[str] = None):
        self._source, self._credential, self._compile, self._facts = source, credential, compile, facts
        self.data_dir = Path(data_dir)
        self.session = session
        self.report_sha256 = report_sha256
        self.api_base = api_base

    # ------------------------------------------------------------------ the declaration

    def declaration(self) -> dict:
        params = [{"name": "mapping", "kind": "string", "required": True}]
        params += [{"name": f, "kind": "string", "required": False, "max_length": 4096} for f in self._facts()][:49]
        return {
            "protocol": PROTOCOL,
            "connector": {"name": "github", "version": VERSION, "package_sha256": "0" * 64},
            "source_kind": SOURCE_KIND,
            "streams": [],
            "fetch": True,
            "actions": [{"name": ACTION, "target_kind": "file", "params": params, "preview": "exact",
                         "staging": ["direct", "native_review"], "grouping": "none", "inverse": None}],
            "conformance": {"suite": SUITE_VERSION, "passed": list(CONFORMANCE_PASSED),
                            "report_sha256": self.report_sha256},
        }

    # ------------------------------------------------------------------ helpers

    def _gh(self, source_key: str) -> tuple[GitHub, dict, str]:
        source = self._source(source_key)
        if source is None or source.get("kind") != SOURCE_KIND:
            raise Outcome("unsupported", "unsupported")
        repo, ref = _repository(source)
        gh = GitHub(repo, self._credential(source), base=self.api_base, session=self.session)
        return gh, source, ref or gh.default_branch()

    def _values(self, source: dict, step: dict, expected: str, jid: str) -> dict:
        """{front matter key: value} that compile-write makes of the step's facts; Outcome on a refusal."""
        facts = {k: v for k, v in step["params"].items() if k != "mapping"}
        if not facts or not isinstance(step["params"].get("mapping"), str):
            raise Outcome("refused", "params_invalid")
        job = {"id": jid, "kind": "write", "args": {"mapping": step["params"]["mapping"], "node_key": step["target"],
                                                     "external_id": step["target"], "expected_version": expected,
                                                     "facts": facts, "mode": "proposal"}}
        status, change = self._compile(source)(job)
        if status != "ok":
            if change == "computed_attribute":
                raise Outcome("ok", "computed_field")
            raise Outcome(status, change)
        values = {}
        for e in (change or {}).get("edits") or []:
            parts = str((e or {}).get("path") or "").split(".")
            if len(parts) != 2 or parts[0] != "front_matter" or not parts[1] or parts[1] in values:
                raise Outcome("refused", "edit_unsupported")
            values[parts[1]] = e.get("value")
        if not values:
            raise Outcome("failed", "runner.bad_change")
        return values

    @staticmethod
    def _shown(values: Mapping) -> dict:
        return {k: front_matter_edit.shown(v) for k, v in values.items()}

    def _file(self, gh: GitHub, path: str, ref: str) -> tuple[str, bytes]:
        sha = gh.file(path, ref)["sha"]
        return sha, gh.blob(sha, MAX_BYTES)

    def _commit_of(self, gh: GitHub, branch: str, path: str, permit_id: str) -> Optional[dict]:
        """The commit on the branch that carries this permit id, if any (the repeat's marker)."""
        answer = gh.call("GET", "/commits", params={"sha": branch, "path": path, "per_page": "50"})
        if answer.status_code != 200:
            raise gh.failed(answer)
        for c in gh.json(answer) or []:
            message = str(((c or {}).get("commit") or {}).get("message") or "")
            if (TRAILER + permit_id) in message.splitlines():
                return c
        return None

    def _receipt(self, gh: GitHub, *, permit_id: str, path: str, ref: str, before: dict, expected: str,
                 previewed: dict, outcome_name: str = "written", source_ref: Optional[str] = None,
                 step: int = 0) -> dict:
        """``step``: the permit's step in its act job, always 0 here (one file per act, grouping none)."""
        sha, data = self._file(gh, path, ref)
        now = front_matter_edit.read(data)
        values = [{"step": step, "target": path, "field": k, "before": before.get(k), "after": now.get(k)}
                  for k in previewed]
        differences = [{"step": step, "target": path, "field": k, "previewed": v, "found": now.get(k)}
                       for k, v in previewed.items() if now.get(k) != v]
        return {"permit_id": permit_id, "outcome": outcome_name, "expected_version": expected, "version_after": sha,
                "reread_at": _now(), "values": values, "differences": differences, "side_effects": [],
                "source_ref": source_ref}

    # ------------------------------------------------------------------ preview

    def preview(self, source, steps, staging, at_version):
        try:
            gh, src, branch = self._gh(source)
            changes, versions, exact, code = [], [], True, None
            for index, step in enumerate(steps):
                if not valid_path(step["target"]):
                    return outcome("refused", "target_not_found")
                if at_version is not None:
                    if not _SHA.match(at_version):
                        return outcome("refused", "target_not_found")
                    found = gh.file(step["target"], branch)["sha"]
                    if found != at_version:
                        return outcome("conflict", "version_changed", conflict={
                            "step": index, "target": step["target"], "expected_version": at_version,
                            "found_version": found})
                sha, data = self._file(gh, step["target"], branch)
                if not any(v["target"] == step["target"] for v in versions):
                    versions.append({"target": step["target"], "version": sha})
                try:
                    values = self._values(src, step, sha, "preview")
                except Outcome as out:
                    if out.result.get("status") == "ok":           # a computed fact: not exactly previewable
                        exact, code = False, "computed_field"
                        continue
                    raise
                try:
                    new = front_matter_edit.edit(data, values)
                except front_matter_edit.EditError as exc:
                    return outcome("refused", exc.code)
                old_values = front_matter_edit.read(data)
                diff = "".join(difflib.unified_diff(data.decode("utf-8", "replace").splitlines(True),
                                                    new.decode("utf-8", "replace").splitlines(True),
                                                    fromfile=step["target"], tofile=step["target"]))[:262144]
                for k, v in self._shown(values).items():
                    changes.append({"step": index, "target": step["target"], "field": k, "before": old_values.get(k),
                                    "after": v})
                changes.append({"step": index, "target": step["target"], "field": None, "before": None, "after": None,
                                "diff": diff})
        except Outcome as out:
            return out.result
        preview = {"exact": exact, "read_versions": versions, "changes": changes if exact else [], "grouping": "none"}
        if not exact:
            preview["inexact_code"] = code
        return outcome("ok", None if exact else "preview_inexact", preview=preview)

    # ------------------------------------------------------------------ act

    def act(self, source, steps, permits, staging):
        if len(steps) != 1:
            return outcome("refused", "grouping_unsupported")
        step, permit = steps[0], permits[0]
        pid, expected, path = permit["permit_id"], permit["expected_version"], step["target"]
        if not valid_path(path) or not _SHA.match(expected):
            return outcome("refused", "target_not_found")
        try:
            gh, src, branch = self._gh(source)
            if src.get("access") != "read_write":
                return outcome("refused", "write_disabled")
            if staging == "native_review":
                return self._stage(gh, src, branch, step, permit)
            done = self._commit_of(gh, branch, path, pid)
            if done is not None:                                 # written before: answer its receipt, write nothing
                parent = ((done.get("parents") or [{}])[0] or {}).get("sha")
                before = front_matter_edit.read(gh.blob(gh.file(path, parent)["sha"], MAX_BYTES)) if parent else {}
                values = self._shown(self._values(src, step, expected, pid))
                return outcome("ok", receipts=[self._receipt(gh, permit_id=pid, path=path, ref=branch, before=before,
                                                             expected=expected, previewed=values,
                                                             source_ref=done.get("html_url"))])
            found, data = self._file(gh, path, branch)
            if found != expected:
                return outcome("conflict", "version_changed",
                               conflict={"step": 0, "target": path, "expected_version": expected,
                                         "found_version": found})
            values = self._values(src, step, expected, pid)
            try:
                new = front_matter_edit.edit(data, values)
            except front_matter_edit.EditError as exc:
                return outcome("refused", exc.code)
            before = front_matter_edit.read(data)
            message = f"Knovas: approved change, permit {pid[:8]}\n\n{TRAILER}{pid}\n"
            answer = gh._write(gh.call("PUT", "/contents/" + quote(path, safe="/"),
                                       body={"message": message, "content": base64.b64encode(new).decode("ascii"),
                                             "sha": expected, "branch": branch}), (200, 201, 409))
            if answer.status_code == 409:                         # the file moved between the read and the write
                found = gh.file(path, branch)["sha"]
                return outcome("conflict", "version_changed",
                               conflict={"step": 0, "target": path, "expected_version": expected,
                                         "found_version": found})
            commit_url = ((gh.json(answer) or {}).get("commit") or {}).get("html_url")
            return outcome("ok", receipts=[self._receipt(gh, permit_id=pid, path=path, ref=branch, before=before,
                                                         expected=expected, previewed=self._shown(values),
                                                         source_ref=commit_url if isinstance(commit_url, str)
                                                         else None)])
        except Outcome as out:
            if out.result.get("status") == "ok":
                return outcome("refused", "computed_field")
            return out.result

    def _staged_path(self, permit_id: str) -> Path:
        return self.data_dir / "staged" / f"{permit_id}.json"

    def _stage(self, gh: GitHub, src: dict, branch: str, step: dict, permit: dict) -> dict:
        pid, expected, path = permit["permit_id"], permit["expected_version"], step["target"]
        head = gh.branch_head(branch)
        found, data = self._file(gh, path, head)
        if found != expected:
            return outcome("conflict", "version_changed",
                           conflict={"step": 0, "target": path, "expected_version": expected, "found_version": found})
        values = self._values(src, step, expected, pid)
        try:
            new = front_matter_edit.edit(data, values)
        except front_matter_edit.EditError as exc:
            return outcome("refused", exc.code)
        message = f"Knovas: approved change for review, permit {pid[:8]}\n\n{TRAILER}{pid}\n"
        body = (f"A change approved in Knovas, staged here for this repository's own review.\n\n- Permit: `{pid}`\n\n"
                f"Only the approved values in the file's front matter were changed.\n")
        url = gh.propose(branch=BRANCH_PREFIX + pid, head=head, base=branch, path=path, expected=expected, new=new,
                         message=message, title=f"Knovas: approved change {pid[:8]}", body=body)
        record = {"path": path, "expected": expected, "before": front_matter_edit.read(data),
                  "values": self._shown(values), "permit_id": pid, "stage_ref": url}
        self._staged_path(pid).parent.mkdir(parents=True, exist_ok=True)
        self._staged_path(pid).write_text(json.dumps(record), encoding="utf-8")
        return outcome("ok", receipts=[{"permit_id": pid, "outcome": "staged", "expected_version": expected,
                                        "version_after": None, "reread_at": _now(), "values": [], "differences": [],
                                        "side_effects": [], "source_ref": url}])

    # ------------------------------------------------------------------ review outcome and reread

    def review_outcome(self, source, stage_ref, permit_ids):
        m = _PULL.search(stage_ref)
        records = [r for r in (self._record(pid) for pid in permit_ids) if r is not None and r["stage_ref"] == stage_ref]
        if m is None or not records:
            return outcome("refused", "not_staged")
        try:
            gh, src, branch = self._gh(source)
            answer = gh.call("GET", f"/pulls/{m.group(1)}")
            if answer.status_code == 404:
                return outcome("refused", "not_staged")
            if answer.status_code != 200:
                raise gh.failed(answer)
            pull = gh.json(answer) or {}
            if pull.get("merged"):
                receipts = [self._receipt(gh, permit_id=r["permit_id"], path=r["path"], ref=branch, before=r["before"],
                                          expected=r["expected"], previewed=r["values"], source_ref=stage_ref)
                            for r in records]
                return outcome("ok", review={"state": "accepted", "stage_ref": stage_ref, "receipts": receipts})
            state = "declined" if pull.get("state") == "closed" else "pending"
            return outcome("ok", review={"state": state, "stage_ref": stage_ref})
        except Outcome as out:
            return out.result

    def _record(self, permit_id: str) -> Optional[dict]:
        try:
            return json.loads(self._staged_path(permit_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def reread(self, source, targets, fields, permit_ids):
        try:
            gh, src, branch = self._gh(source)
            read = []
            for path in targets:
                if not valid_path(path):
                    read.append({"target": path, "version": None})
                    continue
                try:
                    sha, data = self._file(gh, path, branch)
                except Outcome as out:
                    if out.result.get("code") == "not_found":
                        read.append({"target": path, "version": None})
                        continue
                    raise
                values = front_matter_edit.read(data)
                read.append({"target": path, "version": sha,
                             "values": {k: v for k, v in values.items() if not fields or k in fields}})
            receipts = []
            for pid in permit_ids:
                for path in targets:
                    done = self._commit_of(gh, branch, path, pid) if valid_path(path) else None
                    if done is not None:
                        parent = ((done.get("parents") or [{}])[0] or {}).get("sha")
                        old = gh.blob(gh.file(path, parent)["sha"], MAX_BYTES) if parent else b""
                        before = front_matter_edit.read(old)
                        _sha, data = self._file(gh, path, done.get("sha") or branch)
                        changed = {k: v for k, v in front_matter_edit.read(data).items() if before.get(k) != v}
                        receipts.append(self._receipt(gh, permit_id=pid, path=path, ref=branch, before=before,
                                                      expected=gh.file(path, parent)["sha"] if parent else "unknown",
                                                      previewed=changed, source_ref=done.get("html_url")))
        except Outcome as out:
            return out.result
        return outcome("ok", read=read, **({"receipts": receipts} if receipts else {}))


def _now() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
