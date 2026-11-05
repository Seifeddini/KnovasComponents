"""A GitHub repository as a source of originals and a target of write proposals (MVP-21, MVP-22), for the jobs Knovas
Link hands this component (``Link's contracts/local-api.md``). Everything goes through the GitHub REST API with the
source's own credential (``SYNC_CREDENTIAL_<KEY>`` or ``SYNC_CREDENTIAL_GITHUB``), which never leaves the site.

**fetch** reads one file's bytes, raw: the blob at ``args.version`` (the blob sha the Brain recorded), or, without a
version, the blob the source's ``ref`` (else the repository's default branch) holds now. The bytes are checked before
anything leaves: at most ``max_bytes`` (else refused ``too_large``), and their git blob hash must be the version (else
failed ``version_mismatch``). They then go to Link in parts of at most 4 MiB, numbered from 0, and the result names the
version, their SHA-256, the media type, the size and the number of parts. 404 is refused ``not_found``.

**write** stages a change as a pull request, never on the branch itself. brain-runner's ``compile-write`` turns the
job into the change of one file (its refusal is the job's); the file is read at the head of the source's branch; a
blob sha other than ``expected_version`` is a ``conflict`` and nothing is written; otherwise only the changed values'
text in the front matter is replaced (``front_matter_edit``), committed on a new branch ``knovas/write-<job id>`` with
a message naming the declared agent and a short hash of the declared person, and a pull request is opened whose body
names the proposal by its job id. The result is ``ok`` with the pull request's URL as ``source_ref``. A mode other
than ``proposal`` is refused ``mode_unsupported``; 403 from GitHub on a write step, or 404 (GitHub's answer to a token
that may read but not write, here only after the file was read), is refused ``token_cannot_write``. When a step fails
after the branch was made, the branch is deleted again, best effort; if that fails, the result's code is
``github.branch_left``.

Logs carry codes, counts and durations only: never a path, a file name, a value, a token or a repository.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import os
import re
from typing import Any, Callable, Mapping, Optional
from urllib.parse import quote

import requests

from sync_component import front_matter_edit

log = logging.getLogger("sync_component.github")
# urllib3 logs every request's path at DEBUG; a path names a file (GI-DATA-02).
logging.getLogger("urllib3").setLevel(logging.WARNING)

API_VERSION = "2022-11-28"
#: A part posted to Link: at most 4 MiB (local-api.md).
PART_BYTES = 4 * 1024 * 1024
#: The most a fetch may carry, and the most a write reads (25 MiB).
MAX_BYTES = 26214400
BRANCH_PREFIX = "knovas/write-"
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_FRONT_MATTER = "front_matter"
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/markdown", ".markdown")
mimetypes.add_type("application/yaml", ".yaml")
mimetypes.add_type("application/yaml", ".yml")


def git_blob_sha(data: bytes) -> str:
    """The git hash of a file's bytes, which on GitHub is the file's blob sha."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def person_hash(person_id: str) -> str:
    """A declared person, as commits and logs may name them: SHA-256 of the id, 12 hex digits."""
    return hashlib.sha256(person_id.encode("utf-8")).hexdigest()[:12]


def result(status: str, code: Optional[str] = None, **fields: Any) -> dict:
    """A job result (job-result.schema.json); ``duration_ms`` is set by the caller."""
    out: dict = {"status": status, "code": code}
    out.update(fields)
    return out


class Outcome(Exception):
    """Ends a job early with its result."""

    def __init__(self, status: str, code: Optional[str], **fields: Any):
        super().__init__(code or status)
        self.result = result(status, code, **fields)


class PartRefused(Exception):
    """Link did not take a part (lease lost, or the fetch failed there): the job has ended; no result follows."""


def valid_path(path: Any) -> bool:
    if not isinstance(path, str) or not path or len(path) > 1024 or path.startswith("/"):
        return False
    if any(c in path for c in ("\\", "\0", "\r", "\n")):
        return False
    return all(segment not in ("", ".", "..") for segment in path.split("/"))


class GitHub:
    """The few REST calls the jobs need, for one repository, with the source's credential."""

    def __init__(self, repository: str, credential: Optional[str], *, base: Optional[str] = None,
                 session: Any = None, timeout: float = 60):
        if not isinstance(repository, str) or not _REPOSITORY.match(repository) or repository.startswith("-"):
            raise Outcome("failed", "github.bad_repository")
        self.repository = repository
        self.base = (base or os.environ.get("BRAIN_GITHUB_API") or "https://api.github.com").rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION}
        if credential:
            self.headers["Authorization"] = f"Bearer {credential}"

    def _url(self, path: str) -> str:
        return f"{self.base}/repos/{self.repository}{path}"

    def call(self, method: str, path: str, *, params: Optional[dict] = None, body: Optional[dict] = None,
             accept: Optional[str] = None, stream: bool = False) -> Any:
        headers = dict(self.headers)
        if accept:
            headers["Accept"] = accept
        try:
            return self.session.request(method, self._url(path), params=params, json=body, headers=headers,
                                        timeout=self.timeout, stream=stream, allow_redirects=False)
        except requests.RequestException:
            raise Outcome("failed", "github.unreachable") from None

    @staticmethod
    def failed(answer: Any) -> Outcome:
        if answer.status_code == 403 and str(answer.headers.get("X-RateLimit-Remaining", "")) == "0":
            return Outcome("failed", "github.rate_limited")
        return Outcome("failed", f"github.http_{answer.status_code}")

    @staticmethod
    def json(answer: Any) -> Any:
        try:
            return answer.json()
        except ValueError:
            raise Outcome("failed", "github.bad_answer") from None

    def file(self, path: str, ref: Optional[str]) -> dict:
        """The file's entry at ``ref`` (default branch when None): {sha, size}. 404, or not a file: not_found."""
        answer = self.call("GET", "/contents/" + quote(path, safe="/"), params={"ref": ref} if ref else None)
        if answer.status_code == 404:
            raise Outcome("refused", "not_found")
        if answer.status_code != 200:
            raise self.failed(answer)
        data = self.json(answer)
        if not isinstance(data, dict) or data.get("type") != "file" or not _SHA.match(str(data.get("sha") or "")):
            raise Outcome("refused", "not_found")
        return {"sha": data["sha"], "size": data.get("size") if isinstance(data.get("size"), int) else None}

    def blob(self, sha: str, max_bytes: int) -> bytes:
        """The raw bytes of a blob, at most ``max_bytes`` (else refused too_large), whose git hash is ``sha``."""
        answer = self.call("GET", f"/git/blobs/{sha}", accept="application/vnd.github.raw+json", stream=True)
        try:
            if answer.status_code in (404, 422):
                raise Outcome("refused", "not_found")
            if answer.status_code != 200:
                raise self.failed(answer)
            length = answer.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise Outcome("refused", "too_large")
            data = bytearray()
            try:
                for chunk in answer.iter_content(1 << 16):
                    data += chunk
                    if len(data) > max_bytes:
                        raise Outcome("refused", "too_large")
            except requests.RequestException:
                raise Outcome("failed", "github.unreachable") from None
        finally:
            answer.close()
        data = bytes(data)
        if git_blob_sha(data) != sha:
            raise Outcome("failed", "version_mismatch")
        return data

    def default_branch(self) -> str:
        answer = self.call("GET", "")
        if answer.status_code == 404:
            raise Outcome("refused", "not_found")
        if answer.status_code != 200:
            raise self.failed(answer)
        branch = (self.json(answer) or {}).get("default_branch")
        if not isinstance(branch, str) or not _REF.match(branch):
            raise Outcome("failed", "github.bad_answer")
        return branch

    def branch_head(self, branch: str) -> str:
        """The commit a branch points at; a ref that is not a branch cannot take a pull request."""
        answer = self.call("GET", "/git/ref/heads/" + quote(branch, safe="/"))
        if answer.status_code == 404:
            raise Outcome("failed", "github.base_not_a_branch")
        if answer.status_code != 200:
            raise self.failed(answer)
        sha = ((self.json(answer) or {}).get("object") or {}).get("sha")
        if not isinstance(sha, str) or not _SHA.match(sha):
            raise Outcome("failed", "github.bad_answer")
        return sha

    def _write(self, answer: Any, ok: tuple) -> Any:
        """A write step's answer. 403, and 404 (GitHub's answer to a token that may read but not write, here only
        ever after the file was read), are a credential that cannot write; a rate limit is not."""
        if answer.status_code in ok:
            return answer
        if answer.status_code == 404 or (answer.status_code == 403
                                         and str(answer.headers.get("X-RateLimit-Remaining", "")) != "0"):
            raise Outcome("refused", "token_cannot_write")
        raise self.failed(answer)

    def delete_branch(self, branch: str) -> bool:
        """Best effort: whether the branch is gone."""
        try:
            answer = self.call("DELETE", "/git/refs/heads/" + quote(branch, safe="/"))
        except Outcome:
            return False
        return answer.status_code in (204, 404, 422)

    def find_pull(self, branch: str) -> Optional[str]:
        owner = self.repository.split("/", 1)[0]
        answer = self.call("GET", "/pulls", params={"head": f"{owner}:{branch}", "state": "all"})
        if answer.status_code != 200:
            raise self.failed(answer)
        found = self.json(answer)
        if isinstance(found, list) and found and isinstance(found[0], dict) and found[0].get("html_url"):
            return str(found[0]["html_url"])
        return None

    def propose(self, *, branch: str, head: str, base: str, path: str, expected: str, new: bytes,
                message: str, title: str, body: str) -> str:
        """Branch, commit and pull request; the pull request's URL. A job claimed again after its lease ran out
        finds its own branch and pull request and ends with the same URL. When a step after the branch fails, the
        branch is deleted again (best effort); if that fails too, the result's code is ``github.branch_left``."""
        created = self._write(self.call("POST", "/git/refs", body={"ref": "refs/heads/" + branch, "sha": head}), (201, 422))
        commit = True
        if created.status_code == 422:                       # the branch is there: this job ran before
            url = self.find_pull(branch)
            if url:
                return url
            try:
                on_branch = self.file(path, branch)["sha"]
            except Outcome:                                  # not a branch that holds the file: another 422
                raise self.failed(created) from None
            if on_branch == git_blob_sha(new):
                commit = False
            elif on_branch != expected:                      # someone else's change: never deleted
                raise Outcome("failed", "github.branch_exists")
        try:
            return self._commit_and_open(commit=commit, branch=branch, base=base, path=path, expected=expected, new=new,
                                         message=message, title=title, body=body)
        except Outcome as out:
            if not self.delete_branch(branch):
                raise Outcome(out.result["status"], "github.branch_left") from None
            raise

    def _commit_and_open(self, *, commit: bool, branch: str, base: str, path: str, expected: str, new: bytes,
                         message: str, title: str, body: str) -> str:
        if commit:
            self._write(self.call("PUT", "/contents/" + quote(path, safe="/"),
                                  body={"message": message, "content": base64.b64encode(new).decode("ascii"),
                                        "sha": expected, "branch": branch}), (200, 201))
        opened = self._write(self.call("POST", "/pulls", body={"title": title, "head": branch, "base": base, "body": body}),
                             (201, 422))
        if opened.status_code == 422:
            url = self.find_pull(branch)
            if not url:
                raise self.failed(opened)
            return url
        url = (self.json(opened) or {}).get("html_url")
        if not isinstance(url, str) or not url:
            raise Outcome("failed", "github.bad_answer")
        return url


def _repository(source: Mapping[str, Any]) -> tuple[str, Optional[str]]:
    settings = source.get("settings") or {}
    repo, ref = str(settings.get("repository") or ""), settings.get("ref")
    if not _REPOSITORY.match(repo) or repo.startswith("-"):
        raise Outcome("failed", "github.bad_repository")
    if ref in (None, "", "HEAD"):
        return repo, None
    ref = str(ref)
    if not _REF.match(ref) or ".." in ref or ref.startswith(("-", "/")):
        raise Outcome("failed", "github.bad_repository")
    return repo, ref


# ------------------------------------------------------------------ fetch (MVP-21)

def fetch_original(job: Mapping[str, Any], source: Mapping[str, Any], credential: Optional[str], *,
                   post_part: Callable[[int, bytes], None], session: Any = None) -> dict:
    """The original's bytes to Link in parts, and the result. Raises PartRefused when Link took a part no more."""
    args = job.get("args") or {}
    if source.get("fetch") is not True:                      # Link answers this first; the component checks again
        return result("refused", "fetch_disabled")
    path, version, max_bytes = args.get("external_id"), args.get("version"), args.get("max_bytes")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
        return result("failed", "bad_field")
    if not valid_path(path) or (version is not None and (not isinstance(version, str) or not _SHA.match(version))):
        return result("refused", "not_found")
    try:
        repo, ref = _repository(source)
        gh = GitHub(repo, credential, session=session)
        if version is None:
            entry = gh.file(path, ref)
            version = entry["sha"]
            if entry["size"] is not None and entry["size"] > max_bytes:
                return result("refused", "too_large")
        data = gh.blob(version, max_bytes)
    except Outcome as out:
        return out.result
    parts = 0
    for start in range(0, len(data), PART_BYTES):
        post_part(parts, data[start:start + PART_BYTES])
        parts += 1
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return result("ok", None, version=version, bytes_sha256=hashlib.sha256(data).hexdigest(), mime=mime,
                  bytes=len(data), parts=parts)


# ------------------------------------------------------------------ write (MVP-22)

def declared(actor: Any) -> tuple[Optional[str], Optional[str]]:
    """(the declared agent's id, the declared person's id) of a job's actor and its on_behalf_of."""
    who = [actor] if isinstance(actor, dict) else []
    if who and isinstance(actor.get("on_behalf_of"), dict):
        who.append(actor["on_behalf_of"])
    agent = next((str(w["id"]) for w in who if w.get("kind") == "agent" and w.get("id")), None)
    person = next((str(w["id"]) for w in who if w.get("kind") == "user" and w.get("id")), None)
    return agent, person


def texts(job_id: str, agent: Optional[str], person: Optional[str]) -> tuple[str, str, str]:
    """(commit message, pull request title, pull request body): the proposal by its job id, the declared agent, and
    the declared person only as a short hash."""
    who_agent = agent or "none declared"
    who_person = person_hash(person) if person else "none declared"
    message = (f"Knovas: proposed change, job {job_id}\n\n"
               f"Proposed by agent: {who_agent}\n"
               f"Person: {who_person} (SHA-256 of the declared person id, first 12 hex digits)\n"
               f"Knovas-Job: {job_id}\n")
    title = f"Knovas: proposed change {job_id[:8]}"
    body = (f"A change proposed in Knovas and accepted there, staged here for this repository's own review.\n\n"
            f"- Proposal: write job `{job_id}`\n"
            f"- Proposed by agent: `{who_agent}`\n"
            f"- Person: `{who_person}` (SHA-256 of the declared person id, first 12 hex digits)\n\n"
            f"Only the proposed values in the file's front matter were changed; every other byte of the file is as "
            f"it was. The Brain shows the change after this pull request is merged and the next sync has run.\n")
    return message, title, body


def _edits(change: Any, args: Mapping[str, Any]) -> dict:
    """The compiled change's edits as {front matter key: value}, for the file the job names."""
    if not isinstance(change, dict) or change.get("external_id") != args.get("external_id") \
            or change.get("expected_version") != args.get("expected_version"):
        raise Outcome("failed", "runner.bad_change")
    values: dict = {}
    for e in change.get("edits") or []:
        parts = str((e or {}).get("path") or "").split(".")
        if len(parts) != 2 or parts[0] != _FRONT_MATTER or not parts[1] or parts[1] in values:
            raise Outcome("refused", "edit_unsupported")
        values[parts[1]] = e.get("value")
    if not values:
        raise Outcome("failed", "runner.bad_change")
    return values


def write_proposal(job: Mapping[str, Any], source: Mapping[str, Any], credential: Optional[str], *,
                   compile_change: Callable[[Mapping[str, Any]], tuple[str, Any]], session: Any = None) -> dict:
    """The write as a pull request, and the result. ``compile_change(job)`` runs brain-runner's compile-write and
    answers ("ok", change), ("refused", code) or ("failed", code)."""
    args = job.get("args") or {}
    if args.get("mode") != "proposal":
        return result("refused", "mode_unsupported")
    if source.get("access") != "read_write":
        return result("refused", "write_disabled")
    path, expected, jid = args.get("external_id"), args.get("expected_version"), str(job.get("id") or "")
    if not valid_path(path) or not isinstance(expected, str) or not _SHA.match(expected):
        return result("refused", "not_found")
    try:
        status, change = compile_change(job)
        if status != "ok":
            return result(status, change)
        values = _edits(change, args)
        repo, ref = _repository(source)
        gh = GitHub(repo, credential, session=session)
        base = ref or gh.default_branch()
        head = gh.branch_head(base)
        found = gh.file(path, head)["sha"]
        if found != expected:
            return result("conflict", "conflict", version=found)
        old = gh.blob(found, MAX_BYTES)
        try:
            new = front_matter_edit.edit(old, values)
        except front_matter_edit.EditError as exc:
            return result("refused", exc.code)
        if new == old:
            return result("refused", "no_change")
        message, title, body = texts(jid, *declared(job.get("actor")))
        url = gh.propose(branch=BRANCH_PREFIX + jid, head=head, base=base, path=path, expected=expected, new=new,
                         message=message, title=title, body=body)
    except Outcome as out:
        return out.result
    return result("ok", None, source_ref=url)
