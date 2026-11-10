"""The Jira connector of connector protocol 2.0 (FEAT-2026-11-10-write-back J-1, J-2; BET-2026-11-write-back WB-07), on
the connector kit (``knovas_connector_kit``). Everything specific to Jira lives here, none of it in the protocol
(SPEC-write-back-16).

**Reading** (``read``): the project's issues as ``record`` messages (``records.py``), one enhanced JQL search
(``POST /rest/api/3/search/jql``) page after the other with the change log and the edit metadata expanded, asking for
the settings' fields and the parent only. Of the change log only the newest entry's id reaches the record (its
version), never an earlier value, an author or a name; of the edit metadata only which of the settings' fields are
computed; no issue property is read (the permit markers are read only by ``act`` and ``reread``). Only a
listing Jira marked as its last page is complete: then a ``tombstone`` for every issue the state saw that is gone,
a ``generation`` and the new ``state``. A cut listing (a page that is not the last but names no next page, or more
pages than the bound) yields its records and nothing that reports an absence (GI-CONN-02).

**Three actions**, each ``preview: exact``, ``staging: [direct]``, ``inverse: null`` (no undo in V1, the founder's
decision of 2026-11-10):

- ``set_assignee {assignee}``: an account id, or null to unassign; through the edit (``PUT /issue/{key}``), and only
  for someone Jira says may be assigned (``/user/assignable/search``), else ``assignee_not_assignable``;
- ``set_due_date {due_date}``: ``YYYY-MM-DD``, or null to clear; through the edit;
- ``transition_issue {status}``: the name of the status to move to; through the one available transition to it
  (``POST /issue/{key}/transitions``); none is ``transition_unavailable``, several ``transition_ambiguous``, one whose
  screen requires a field ``transition_needs_fields``.

The two field edits declare ``grouping: item`` (protocol v2.0 as amended 2026-11-10, D-24): several of them on one
issue are one edit call and one change-log entry, written all or none, with one receipt per step; the Brain sends one
act per issue. A transition declares ``grouping: none`` and goes alone; an act that mixes it with an edit is refused
``grouping_unsupported``. An act whose steps name several issues is the Brain's error and is refused
``grouping_unsupported`` too, before anything is read: Jira cannot write two issues all or none. Two steps that set
the same field of one issue are refused ``same_field_twice``.

A field the edit metadata does not let this credential set, or one the settings name as set by an automation
(``computed_fields``), makes the preview inexact (``computed_field``): it can never be approved (SPEC-write-back-06).
A value equal to the current one is refused (``no_change``): there is nothing to approve.

**Writing** (``act``, ``direct`` only): each permit's ``expected_version`` is the issue's change-log position the
approved preview read. The connector reads the issue; when its markers for these permits are there, the write happened
before (a repeat, or a result lost on the way): it answers the receipts of a fresh read and writes nothing
(SPEC-write-back-10). When the change log moved past an expected version it answers ``conflict`` ``version_changed``
for the first such step, with each step's field then and now, and writes nothing (SPEC-write-back-32). Otherwise it
writes the change and, in the same request, one issue property ``knovas.permit.<permit id>`` per step (the marker,
which keeps the step's index; Jira sets properties as part of an edit or a transition), reads the issue again and
answers one receipt per step: the value before and as read again, a difference from the approved value, the fields
Jira changed besides (by name) and the new change-log position (SPEC-write-back-07). Jira has no conditional edit: a
change that lands between the read and the write is not refused, but shows in the receipt (a side effect, or a
difference when it touched the same field).

Every change, difference and conflict names its ``step``, the index into the job's ``args.steps`` (D-14); a receipt
read again later names the step of its permit in the act job, from the marker.

The credential goes only to the host the site's environment binds to it (``client.credential_from_environment``); a
source whose ``site`` setting names another host is refused ``jira.bad_settings`` before any request (GI-CONN-07).

Logs carry codes and counts only: never an issue key, a value, a site or the credential (GI-DATA-02, GI-DATA-04).
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from knovas_connector_kit import PROTOCOL, SUITE_VERSION
from knovas_connector_kit.canonical import digest
from knovas_connector_kit.connector import Connector, grouping_of, outcome

from knovas_connector_jira import SOURCE_KIND, VERSION
from knovas_connector_jira.client import (Credential, JiraClient, JiraError, Settings, issue_key, key_order,
                                          settings_of)
from knovas_connector_jira.records import (RECORD_KIND, changed_fields, editable, entries_since, fields_of, record,
                                           requested_fields, value_of, version_of)

log = logging.getLogger("knovas_connector_jira")

MARK_PREFIX = "knovas.permit."
MAX_PAGES = 500
ACCOUNT_ID = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
#: The action, the Jira field it changes and the parameter that carries the new value.
ACTIONS = {"set_assignee": ("assignee", "assignee"), "set_due_date": ("duedate", "due_date"),
           "transition_issue": ("status", "status")}
#: The fields the actions write, which a preview and an act read whatever the settings name.
ACTION_FIELDS = tuple(dict.fromkeys(field for field, _param in ACTIONS.values()))
#: Field edits of one issue are one edit call and one change-log entry, so several of them in one act are written all
#: or none, one issue per act (grouping item, D-24); a transition is its own call and goes alone.
GROUPING = {"set_assignee": "item", "set_due_date": "item", "transition_issue": "none"}
#: The conformance properties the package passed (tests/test_conformance.py runs the kit's suite against a fake Jira).
#: review_outcome is not applicable: no action declares native_review.
CONFORMANCE_PASSED = ["replay_changes_nothing", "deletion_guard", "preview_equals_receipt", "exactly_once",
                      "permit_refusals", "no_credential_leaves_site"]


class _Inexact(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@functools.lru_cache(maxsize=None)
def package_sha256() -> str:
    """SHA-256 over the package's Python files (path and content), the digest the conformance report is made for."""
    root = Path(__file__).resolve().parent
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return digest([[str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest()] for p in files])


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JiraConnector(Connector):
    """``source(key)`` the source Link assigned (``{key, kind, access, settings}``), ``credential(source)`` its
    credential at the site with the host it is bound to (``client.credential_from_environment``)."""

    def __init__(self, *, source: Callable[[str], Optional[dict]],
                 credential: Callable[[Mapping], Optional[Credential]],
                 session: Any = None, report_sha256: str = "0" * 64, sleep: Optional[Callable[[float], None]] = None):
        self._source, self._credential = source, credential
        self.session, self.report_sha256, self.sleep = session, report_sha256, sleep

    # ------------------------------------------------------------------ the declaration

    def declaration(self) -> dict:
        def action(name: str, params: list) -> dict:
            return {"name": name, "target_kind": RECORD_KIND, "params": params, "preview": "exact",
                    "staging": ["direct"], "grouping": GROUPING[name], "inverse": None}
        return {
            "protocol": PROTOCOL,
            "connector": {"name": "jira", "version": VERSION, "package_sha256": package_sha256()},
            "source_kind": SOURCE_KIND,
            "streams": ["record", "tombstone", "generation", "state"],
            "fetch": False,
            "actions": [
                action("set_assignee", [{"name": "assignee", "kind": "reference", "required": True,
                                         "max_length": 128}]),
                action("transition_issue", [{"name": "status", "kind": "string", "required": True,
                                             "max_length": 255}]),
                action("set_due_date", [{"name": "due_date", "kind": "date", "required": True}]),
            ],
            "conformance": {"suite": SUITE_VERSION, "passed": list(CONFORMANCE_PASSED),
                            "report_sha256": self.report_sha256},
        }

    # ------------------------------------------------------------------ helpers

    def _open(self, source_key: str) -> tuple[JiraClient, Settings, dict]:
        source = self._source(source_key)
        if not isinstance(source, Mapping) or source.get("kind") != SOURCE_KIND:
            raise JiraError("unsupported", "unsupported")
        settings = settings_of(source)
        kwargs = {"session": self.session} if self.session is not None else {}
        if self.sleep is not None:
            kwargs["sleep"] = self.sleep
        return JiraClient(settings.site, self._credential(source), **kwargs), settings, dict(source)

    def _issue(self, client: JiraClient, settings: Settings, key: str,
               marks: tuple = ()) -> Optional[dict]:
        """The issue with the settings' fields and the fields the actions write, its newest change-log entries and its
        edit metadata (and the permit markers asked for, no other property); None when Jira has no such issue for this
        credential."""
        params = {"fields": ",".join(requested_fields(settings, ACTION_FIELDS)), "expand": "changelog,editmeta"}
        if marks:
            params["properties"] = ",".join(marks)
        try:
            return client.call("GET", f"/issue/{key}", params=params)[1] or {}
        except JiraError as err:
            if err.code == "jira.http_404":
                return None
            raise

    @staticmethod
    def _histories(issue: Mapping) -> list:
        return list(((issue.get("changelog") or {}).get("histories")) or ())

    def _params(self, step: dict) -> Optional[str]:
        """``params_invalid`` unless the step carries exactly its action's parameter, of its kind."""
        field, param = ACTIONS[step["action"]]
        params = step["params"]
        if set(params) != {param}:
            return "params_invalid"
        value = params[param]
        if step["action"] == "set_assignee":
            ok = value is None or (isinstance(value, str) and ACCOUNT_ID.match(value))
        elif step["action"] == "set_due_date":
            ok = value is None
            if isinstance(value, str) and DATE.match(value):
                try:
                    dt.date.fromisoformat(value)
                    ok = True
                except ValueError:
                    ok = False
        else:
            ok = isinstance(value, str) and 0 < len(value) <= 255 and value.isprintable()
        return None if ok else "params_invalid"

    def _plan(self, client: JiraClient, settings: Settings, step: dict, issue: dict) -> tuple[Any, Any, dict]:
        """(before, after, body part) of one step on the issue as read: ``{"fields": {...}}`` for a field edit,
        ``{"transition": {...}}`` for a transition; ``_Inexact`` when it cannot be computed exactly, ``JiraError``
        (refused) when it cannot be carried out."""
        action, key = step["action"], step["target"]
        field, param = ACTIONS[action]
        after = step["params"][param]
        before = value_of(issue, field)
        if field in settings.computed_fields:
            raise _Inexact("computed_field")
        if action == "transition_issue":
            _s, answer = client.call("GET", f"/issue/{key}/transitions", params={"expand": "transitions.fields"})
            found = [t for t in (answer or {}).get("transitions") or ()
                     if isinstance(t, Mapping) and ((t.get("to") or {}).get("name")) == after
                     and t.get("isAvailable", True) is not False]
            if not found:
                raise JiraError("transition_unavailable", "refused")
            if len(found) > 1:
                raise JiraError("transition_ambiguous", "refused")
            needs = [f for f, meta in (found[0].get("fields") or {}).items()
                     if (meta or {}).get("required") and not (meta or {}).get("hasDefaultValue")]
            if needs:
                raise JiraError("transition_needs_fields", "refused")
            if before == after:
                raise JiraError("no_change", "refused")
            return before, after, {"transition": {"id": str(found[0]["id"])}}
        if field not in editable(issue.get("editmeta")):
            raise _Inexact("computed_field")
        if before == after:
            raise JiraError("no_change", "refused")
        if action == "set_assignee":
            if after is not None:
                _s, users = client.call("GET", "/user/assignable/search",
                                        params={"issueKey": key, "accountId": after, "maxResults": "1"})
                if not any(isinstance(u, Mapping) and u.get("accountId") == after for u in users or ()):
                    raise JiraError("assignee_not_assignable", "refused")
            return before, after, {"fields": {"assignee": {"accountId": after} if after else None}}
        return before, after, {"fields": {"duedate": after}}

    @staticmethod
    def _same_field_twice(steps: list) -> bool:
        """Two steps of one job that set the same field of one issue: their order would decide, so neither is exact."""
        pairs = [(s["target"], ACTIONS[s["action"]][0]) for s in steps]
        return len(pairs) != len(set(pairs))

    # ------------------------------------------------------------------ reading

    def read(self, source: str, state: Optional[dict]) -> list[dict]:
        """The source's stream from ``state`` on. A failed call raises ``JiraError`` (a code only)."""
        client, settings, _src = self._open(source)
        seen = dict((state or {}).get("seen") or {})
        issues: dict[str, dict] = {}
        token, complete = None, False
        for _page in range(MAX_PAGES):
            body = {"jql": f'project = "{settings.project}" ORDER BY key ASC',
                    "fields": requested_fields(settings), "expand": "changelog,editmeta",
                    "maxResults": settings.page_size}
            if token:
                body["nextPageToken"] = token
            _s, page = client.call("POST", "/search/jql", body=body)
            for issue in (page or {}).get("issues") or ():
                key = (issue or {}).get("key")
                if issue_key(key, settings) and key not in issues:
                    issues[key] = issue
            if (page or {}).get("isLast") is True:
                complete = True
                break
            token = (page or {}).get("nextPageToken")
            if not isinstance(token, str) or not token:
                break                                     # cut: Jira said there is more but named no next page
        out = []
        for key in sorted(issues, key=key_order):
            issue = issues[key]
            editmeta = issue.get("editmeta")
            if not isinstance(editmeta, Mapping):
                editmeta = client.call("GET", f"/issue/{key}/editmeta")[1] or {}
            out.append(record(source, issue, settings, editmeta))
        log.info("jira.read", extra={"records": len(out), "complete": complete})
        if not complete:
            return out
        now = {m["external_id"]: m["version"] for m in out}
        for gone in sorted(set(seen) - set(now), key=key_order):
            out.append({"type": "tombstone", "source": source, "external_id": gone})
        out.append({"type": "generation", "source": source,
                    "payload": {"id": "g-" + digest(sorted(now.items()))[:16], "complete": True,
                                "counts": {"record": len(now)}}})
        out.append({"type": "state", "source": source, "state": {"seen": now}})
        return out

    # ------------------------------------------------------------------ preview

    def preview(self, source, steps, staging, at_version):
        if self._same_field_twice(steps):
            return outcome("refused", "same_field_twice")
        try:
            client, settings, _src = self._open(source)
            changes, versions, exact, code = [], {}, True, None
            issues: dict[str, dict] = {}
            for i, step in enumerate(steps):
                key = issue_key(step["target"], settings)
                bad = self._params(step)
                if bad:
                    return outcome("refused", bad)
                if key and key not in issues:
                    found = self._issue(client, settings, key)
                    if found is not None:
                        issues[key] = found
                issue = issues.get(key) if key else None
                if issue is None:
                    return outcome("refused", "target_not_found")
                version = version_of(self._histories(issue))
                if at_version is not None and version != at_version:
                    return outcome("conflict", "version_changed", conflict={
                        "step": i, "target": key, "expected_version": at_version, "found_version": version})
                versions.setdefault(key, version)
                try:
                    before, after, _body = self._plan(client, settings, step, issue)
                except _Inexact as inexact:
                    exact, code = False, inexact.code
                    continue
                changes.append({"step": i, "target": key, "field": ACTIONS[step["action"]][0], "before": before,
                                "after": after})
        except JiraError as err:
            return outcome(err.status, err.code)
        grouping = grouping_of(GROUPING[s["action"]] for s in steps)
        preview = {"exact": exact, "read_versions": [{"target": t, "version": v} for t, v in versions.items()],
                   "changes": changes if exact else [], "grouping": grouping}
        if not exact:
            preview["inexact_code"] = code
        return outcome("ok", None if exact else "preview_inexact", preview=preview)

    # ------------------------------------------------------------------ act

    def _receipt(self, settings: Settings, issue: dict, *, permit_id: str, mark: Mapping) -> dict:
        """The receipt of one step's write from the issue as read again: the value before (from the marker), as read
        now, a difference when it is not the approved value, the fields Jira changed besides since the expected
        version (the fields written with it excluded). Its step is the step of its permit in the act job."""
        key, field, step = issue["key"], mark["field"], int(mark.get("step", 0))
        now = value_of(issue, field)
        histories = self._histories(issue)
        written = set(mark.get("fields") or [field])
        differences = [] if now == mark["after"] else [
            {"step": step, "target": key, "field": field, "previewed": mark["after"], "found": now}]
        side = [f for f in changed_fields(entries_since(histories, mark["expected_version"])) if f not in written][:100]
        return {"permit_id": permit_id, "outcome": "written", "expected_version": mark["expected_version"],
                "version_after": version_of(histories), "reread_at": _now(),
                "values": [{"step": step, "target": key, "field": field, "before": mark["before"], "after": now}],
                "differences": differences, "side_effects": [{"target": key, "field": f} for f in side],
                "source_ref": f"{settings.site}/browse/{key}"}

    def _conflict(self, issue: dict, steps: list, first: int, expected: str, found: str) -> dict:
        """The first step whose issue left its expected version, and per step on that issue what its field held at
        the expected version (from the change log since) and holds now."""
        key, changes = issue["key"], []
        entries = entries_since(self._histories(issue), expected)
        for i, step in enumerate(steps):
            if step["target"] != key:
                continue
            field = ACTIONS[step["action"]][0]
            now = then = value_of(issue, field)
            for entry in entries:
                item = next((it for it in entry.get("items") or () if (it or {}).get("fieldId") == field), None)
                if item is not None:
                    then = item.get("fromString") if field == "status" else item.get("from")
                    break
            changes.append({"step": i, "target": key, "field": field, "before": then, "after": now})
        return outcome("conflict", "version_changed", conflict={"step": first, "target": key,
                                                                "expected_version": expected, "found_version": found,
                                                                "changes": changes})

    def act(self, source, steps, permits, staging):
        """One step, or several field edits of one issue (grouping item): one call writes them all with every step's
        permit marker, so they are written all or none. Steps on several issues are the Brain's error (it sends one
        act per issue) and are refused grouping_unsupported before anything is read: Jira cannot write two issues all
        or none."""
        if staging != "direct":
            return outcome("refused", "staging_unsupported")
        if not steps or len(steps) != len(permits) or (
                len(steps) > 1 and any(GROUPING[s["action"]] != "item" for s in steps)):
            return outcome("refused", "grouping_unsupported")
        for step in steps:
            bad = self._params(step)
            if bad:
                return outcome("refused", bad)
        if self._same_field_twice(steps):
            return outcome("refused", "same_field_twice")
        try:
            client, settings, src = self._open(source)
            if src.get("access") != "read_write":
                return outcome("refused", "write_disabled")
            keys = [issue_key(s["target"], settings) for s in steps]
            if any(k is None for k in keys):
                return outcome("refused", "target_not_found")
            if len(set(keys)) > 1:
                return outcome("refused", "grouping_unsupported")
            marks = [MARK_PREFIX + p["permit_id"] for p in permits]
            issues: dict[str, dict] = {}
            for key in dict.fromkeys(keys):
                issue = self._issue(client, settings, key, tuple(m for k, m in zip(keys, marks) if k == key))
                if issue is None:
                    return outcome("refused", "target_not_found")
                issues[key] = issue
            earlier = [(issues[k].get("properties") or {}).get(m) for k, m in zip(keys, marks)]
            if any(isinstance(e, Mapping) for e in earlier):          # written before: answer, write nothing
                if not all(isinstance(e, Mapping) and e.get("permit_id") == p["permit_id"]
                           for e, p in zip(earlier, permits)):
                    return outcome("failed", "jira.marker_incomplete")
                log.info("jira.act", extra={"outcome": "found_earlier_write", "steps": len(steps)})
                return outcome("ok", receipts=[self._receipt(settings, issues[k], permit_id=p["permit_id"], mark=e)
                                               for k, p, e in zip(keys, permits, earlier)])
            for i, (key, permit) in enumerate(zip(keys, permits)):
                found = version_of(self._histories(issues[key]))
                if found != permit["expected_version"]:
                    return self._conflict(issues[key], steps, i, permit["expected_version"], found)
            key, issue = keys[0], issues[keys[0]]
            body: dict = {}
            fields = [ACTIONS[s["action"]][0] for s in steps]
            written = []
            for i, (step, permit) in enumerate(zip(steps, permits)):
                try:
                    before, after, part = self._plan(client, settings, step, issue)
                except _Inexact as inexact:
                    return outcome("refused", inexact.code)
                if "fields" in part:
                    body.setdefault("fields", {}).update(part["fields"])
                else:
                    body.update(part)
                written.append({"v": 1, "permit_id": permit["permit_id"], "change_set_id": permit.get("change_set_id"),
                                "step": i, "action": step["action"], "field": fields[i], "fields": fields,
                                "expected_version": permit["expected_version"], "before": before, "after": after})
            body["properties"] = [{"key": m, "value": w} for m, w in zip(marks, written)]
            method, path = ("POST", f"/issue/{key}/transitions") if "transition" in body else ("PUT", f"/issue/{key}")
            client.call(method, path, body=body, ok=(200, 204))
            again = self._issue(client, settings, key)
            if again is None:
                return outcome("failed", "jira.gone_after_write")
            log.info("jira.act", extra={"outcome": "written", "steps": len(steps)})
            return outcome("ok", receipts=[self._receipt(settings, again, permit_id=w["permit_id"], mark=w)
                                           for w in written])
        except JiraError as err:
            log.warning("jira.act_failed", extra={"code": err.code})
            return outcome(err.status, err.code)

    # ------------------------------------------------------------------ reread

    def reread(self, source, targets, fields, permit_ids):
        try:
            client, settings, _src = self._open(source)
            marks = tuple(MARK_PREFIX + pid for pid in permit_ids)
            read, receipts = [], []
            for target in targets:
                key = issue_key(target, settings)
                issue = self._issue(client, settings, key, marks) if key else None
                if issue is None:
                    read.append({"target": target, "version": None})
                    continue
                values = {k: v for k, v in fields_of(issue, settings).items() if not fields or k in fields}
                read.append({"target": key, "version": version_of(self._histories(issue)),
                             "values": dict(sorted(values.items())[:50])})
                for mark_key in marks:
                    mark = (issue.get("properties") or {}).get(mark_key)
                    if isinstance(mark, Mapping) and mark_key == MARK_PREFIX + str(mark.get("permit_id")):
                        receipts.append(self._receipt(settings, issue, permit_id=mark["permit_id"], mark=mark))
        except JiraError as err:
            return outcome(err.status, err.code)
        return outcome("ok", read=read, **({"receipts": receipts} if receipts else {}))
