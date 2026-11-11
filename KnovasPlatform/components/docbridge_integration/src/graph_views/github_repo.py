"""The few GitHub REST calls a change request needs, on one repository.

Least privilege: the token needs Contents (read/write) and Pull requests
(read/write) on KnowledgeBase and nothing else. The same client and token read
a ``github:`` definitions bundle (``bundle.py``, ``views.config_source``). It is read from a file
(``views.change_requests.github_token_file``) or the environment, never from
the request, and never logged.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


class GitHubError(RuntimeError):
    """A GitHub call failed; the message carries the status, never the token."""

    def __init__(self, what: str, status: int, message: str = ""):
        super().__init__(f"GitHub {what}: HTTP {status}{f' ({message})' if message else ''}")
        self.status = status


class GitHubRepo:
    def __init__(self, repo: str, token: str, *, api: str = "https://api.github.com",
                 session: Optional[requests.Session] = None, timeout: float = 20):
        self.repo = repo
        self._api = api.rstrip("/")
        self._s = session or requests.Session()
        self._headers = {"Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "knovas-platform-views"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._timeout = timeout

    def _call(self, method: str, path: str, what: str, *, ok=(200, 201), **kw) -> Any:
        r = self._s.request(method, f"{self._api}/repos/{self.repo}{path}", headers=self._headers,
                            timeout=self._timeout, **kw)
        if r.status_code == 404 and method == "GET":
            return None
        if r.status_code not in ok:
            try:
                message = str((r.json() or {}).get("message") or "")[:200]
            except ValueError:
                message = ""
            raise GitHubError(what, r.status_code, message)
        return r.json() if r.content else {}

    def get_file(self, path: str, ref: str) -> Optional[Tuple[str, str]]:
        """(text, blob sha) of a file at a ref, or None."""
        data = self._call("GET", f"/contents/{quote(path)}?ref={quote(ref)}", "read file")
        if not data or data.get("type") != "file":
            return None
        return base64.b64decode(data.get("content") or "").decode("utf-8"), data["sha"]

    def access(self) -> Dict[str, bool]:
        """Whether the token reads (and may push to) the repository; GitHubError when it cannot see it."""
        data = self._call("GET", "", "read repository")
        if not data:
            raise GitHubError("read repository", 404, "nicht gefunden oder kein Zugriff")
        perms = data.get("permissions") or {}
        return {"read": bool(perms.get("pull", True)), "write": bool(perms.get("push"))}

    def get_raw(self, path: str, ref: str) -> Optional[str]:
        """A file's text at a ref, or None; raw media type, so files over the 1 MB of ``get_file`` work too."""
        r = self._s.get(f"{self._api}/repos/{self.repo}/contents/{quote(path)}?ref={quote(ref)}",
                        headers={**self._headers, "Accept": "application/vnd.github.raw+json"}, timeout=self._timeout)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise GitHubError("read file", r.status_code)
        return r.content.decode("utf-8")

    def list_dir(self, path: str, ref: str) -> List[str]:
        data = self._call("GET", f"/contents/{quote(path)}?ref={quote(ref)}", "list directory") or []
        return [d["name"] for d in data if isinstance(d, dict)]

    def branch_sha(self, branch: str) -> str:
        data = self._call("GET", f"/git/ref/heads/{quote(branch)}", "read branch")
        if not data:
            raise GitHubError("read branch", 404, branch)
        return data["object"]["sha"]

    def create_branch(self, name: str, sha: str) -> None:
        self._call("POST", "/git/refs", "create branch", json={"ref": f"refs/heads/{name}", "sha": sha})

    def put_file(self, path: str, text: str, message: str, branch: str, sha: Optional[str] = None) -> None:
        body: Dict[str, Any] = {"message": message, "branch": branch,
                                "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if sha:
            body["sha"] = sha
        self._call("PUT", f"/contents/{quote(path)}", "write file", json=body)

    def create_pull(self, title: str, body: str, head: str, base: str) -> Dict[str, Any]:
        data = self._call("POST", "/pulls", "open pull request",
                          json={"title": title, "body": body, "head": head, "base": base})
        return {"number": data["number"], "url": data["html_url"]}

    def get_pull(self, number: int) -> Optional[Dict[str, Any]]:
        data = self._call("GET", f"/pulls/{int(number)}", "read pull request")
        if not data:
            return None
        return {"number": data["number"], "url": data["html_url"], "state": data["state"],
                "merged": bool(data.get("merged")), "merged_at": data.get("merged_at")}
