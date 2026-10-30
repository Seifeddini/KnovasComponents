# Copied for the Platform's sync tests from KnowledgeBase scripts/brain_runner/tests/fake_github.py (brain-runner 0.1.0.dev2,
# 1ff84917): the in-memory GitHub REST API brain_runner's own tests run against. A test helper only.
"""An in-memory GitHub REST API for one repository: enough of refs, trees, blobs, compare and commits for GitHubSource.

It stands in for `requests.Session` (`get(url, headers=, params=, timeout=)`), counts every call by kind, can be told
to answer the next calls with given responses (rate limits, server errors), to cut the recursive tree (`truncate`),
and to refuse a call without the expected token.
"""
from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlencode, urlparse

BASE = "https://api.github.com"


class Resp:
    def __init__(self, status: int, data=None, headers: dict | None = None, text: str | None = None):
        self.status_code, self._data, self.headers = status, data, dict(headers or {})
        self.text = text if text is not None else (json.dumps(data) if data is not None else "")

    def json(self):
        if self._data is None:
            raise ValueError("no JSON body")
        return self._data


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(content) + content).hexdigest()


class FakeGitHub:
    def __init__(self, repo: str = "acme/warehouse", token: str | None = None):
        self.repo, self.token = repo, token
        self.commits: dict[str, dict] = {}
        self.refs: dict[str, str] = {}
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, tuple[str, str]] = {}          # tree sha → (commit, folder)
        self.calls: list[tuple[str, str]] = []                # (kind, path)
        self.queue: list = []                                  # answers (Resp or exception) for the next calls
        self.truncate = False
        self.now, self.slept = 1_800_000_000.0, []
        self.files_page = 300

    # -- building the repository ---------------------------------------------------------------------------------

    def commit(self, files: dict, message: str = "change", date: str = "2026-10-01T10:00:00Z",
               author: tuple[str, str] = ("A Developer", "dev@example.com"), ref: str = "main", parents: list[str] | None = None) -> str:
        parent = self.refs.get(ref)
        parents = parents if parents is not None else ([parent] if parent else [])
        tree = dict(self.commits[parents[0]]["files"]) if parents else {}
        for p, content in files.items():
            if content is None:
                tree.pop(p, None)
            else:
                tree[p] = content.encode("utf-8") if isinstance(content, str) else content
        for c in tree.values():
            self.blobs[blob_sha(c)] = c
        sha = hashlib.sha1(json.dumps([parents, sorted((p, blob_sha(c)) for p, c in tree.items()), message, date]).encode()).hexdigest()
        self.commits[sha] = {"files": tree, "parents": parents, "message": message, "date": date, "author": author}
        self.refs[ref] = sha
        return sha

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now

    # -- the API ---------------------------------------------------------------------------------------------------

    def get(self, url, headers=None, params=None, timeout=None):
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        q.update({k: str(v) for k, v in (params or {}).items()})
        pre = f"/repos/{self.repo}/"
        assert u.path.startswith(pre), u.path
        path = u.path[len(pre):]
        kind = self._kind(path, q, headers or {})
        self.calls.append((kind, path))
        if self.queue:
            answer = self.queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        if self.token and (headers or {}).get("Authorization") != f"Bearer {self.token}":
            return Resp(401, {"message": "Bad credentials"})
        return getattr(self, "_" + kind)(path, q, headers or {})

    @staticmethod
    def _kind(path: str, q: dict, headers: dict) -> str:
        if path.startswith("git/trees/"):
            return "tree"
        if path.startswith("git/blobs/"):
            return "blob"
        if path.startswith("compare/"):
            return "compare"
        if path == "commits":
            return "list"
        if headers.get("Accept") == "application/vnd.github.sha":
            return "ref"
        return "commit"

    def _resolve(self, ref: str) -> str | None:
        if ref == "HEAD":
            ref = "main"
        if ref in self.refs:
            return self.refs[ref]
        return ref if ref in self.commits else None

    def _ref(self, path, q, headers):
        sha = self._resolve(path.split("/", 1)[1])
        return Resp(200, text=sha) if sha else Resp(404, {"message": "No commit found"})

    def _tree_sha(self, commit: str, folder: str) -> str:
        files = self.commits[commit]["files"]
        pre = folder + "/" if folder else ""
        sha = hashlib.sha1(json.dumps(sorted((p, blob_sha(c)) for p, c in files.items() if p.startswith(pre))).encode()).hexdigest()
        self.trees[sha] = (commit, folder)
        return sha

    def _tree(self, path, q, headers):
        ident = path.split("/", 2)[2]
        if ident in self.commits:
            commit, folder = ident, ""
        elif ident in self.trees:
            commit, folder = self.trees[ident]
        else:
            return Resp(404, {"message": "Not Found"})
        files = self.commits[commit]["files"]
        pre = folder + "/" if folder else ""
        entries, folders = [], set()
        for p, c in sorted(files.items()):
            if not p.startswith(pre):
                continue
            rest = p[len(pre):]
            parts = rest.split("/")
            if q.get("recursive"):
                for i in range(1, len(parts)):
                    folders.add("/".join(parts[:i]))
                entries.append({"path": rest, "mode": "100644", "type": "blob", "sha": blob_sha(c), "size": len(c)})
            elif len(parts) == 1:
                entries.append({"path": rest, "mode": "100644", "type": "blob", "sha": blob_sha(c), "size": len(c)})
            else:
                folders.add(parts[0])
        entries += [{"path": d, "mode": "040000", "type": "tree", "sha": self._tree_sha(commit, pre + d)} for d in sorted(folders)]
        if q.get("recursive") and self.truncate:
            return Resp(200, {"sha": commit, "tree": entries[:1], "truncated": True})
        return Resp(200, {"sha": commit, "tree": entries, "truncated": False})

    def _blob(self, path, q, headers):
        content = self.blobs.get(path.rsplit("/", 1)[1])
        if content is None:
            return Resp(404, {"message": "Not Found"})
        b64 = base64.b64encode(content).decode()
        return Resp(200, {"sha": path.rsplit("/", 1)[1], "size": len(content), "encoding": "base64",
                          "content": "\n".join(b64[i:i + 60] for i in range(0, len(b64), 60))})

    def _ancestors(self, sha: str) -> list[str]:
        out, todo = [], [sha]
        while todo:
            s = todo.pop(0)
            if s in out:
                continue
            out.append(s)
            todo += self.commits[s]["parents"]
        return out

    def _changes(self, old: dict, new: dict) -> list[dict]:
        removed = {p: blob_sha(c) for p, c in old.items() if p not in new}
        added = {p: blob_sha(c) for p, c in new.items() if p not in old}
        out = []
        for p, sha in sorted(added.items()):
            prev = next((r for r, rs in removed.items() if rs == sha), None)
            if prev:
                removed.pop(prev)
                out.append({"filename": p, "status": "renamed", "sha": sha, "previous_filename": prev})
            else:
                out.append({"filename": p, "status": "added", "sha": sha})
        out += [{"filename": p, "status": "removed", "sha": sha} for p, sha in sorted(removed.items())]
        out += [{"filename": p, "status": "modified", "sha": blob_sha(new[p])} for p in sorted(new)
                if p in old and old[p] != new[p]]
        return out

    def _compare(self, path, q, headers):
        base, head = path.split("/", 1)[1].split("...")
        if base not in self.commits or head not in self.commits:
            return Resp(404, {"message": "Not Found"})
        if base == head:
            status = "identical"
        elif base in self._ancestors(head):
            status = "ahead"
        elif head in self._ancestors(base):
            status = "behind"
        else:
            status = "diverged"
        files = self._changes(self.commits[base]["files"], self.commits[head]["files"])
        return Resp(200, {"status": status, "files": files[:300], "commits": []})

    def _entry(self, sha: str) -> dict:
        c = self.commits[sha]
        return {"sha": sha, "parents": [{"sha": p} for p in c["parents"]],
                "commit": {"message": c["message"], "author": {"name": c["author"][0], "email": c["author"][1], "date": c["date"]},
                           "committer": {"name": "GitHub", "email": "noreply@github.com", "date": c["date"]}}}

    def _commit(self, path, q, headers):
        sha = self._resolve(path.split("/", 1)[1])
        if not sha:
            return Resp(404, {"message": "No commit found"})
        c = self.commits[sha]
        files = self._changes(self.commits[c["parents"][0]]["files"] if c["parents"] else {}, c["files"])
        page = int(q.get("page") or 1)
        chunk = files[(page - 1) * self.files_page: page * self.files_page]
        headers = {}
        if page * self.files_page < len(files):
            headers["Link"] = f'<{BASE}/repos/{self.repo}/commits/{sha}?page={page + 1}>; rel="next"'
        return Resp(200, {**self._entry(sha), "files": chunk}, headers)

    def _list(self, path, q, headers):
        head = self._resolve(q.get("sha") or "main")
        shas = sorted(self._ancestors(head), key=lambda s: self.commits[s]["date"], reverse=True)
        if q.get("since"):
            shas = [s for s in shas if self.commits[s]["date"] >= q["since"]]
        if q.get("path"):
            want = q["path"]

            def touches_path(s: str) -> bool:
                c = self.commits[s]
                before = self.commits[c["parents"][0]]["files"] if c["parents"] else {}
                return any(f["filename"] == want or f["filename"].startswith(want + "/") for f in self._changes(before, c["files"]))
            shas = [s for s in shas if touches_path(s)]
        per, page = int(q.get("per_page") or 30), int(q.get("page") or 1)
        chunk = shas[(page - 1) * per: page * per]
        headers = {}
        if page * per < len(shas):
            nq = {k: v for k, v in q.items() if k != "page"} | {"page": str(page + 1)}
            headers["Link"] = f'<{BASE}/repos/{self.repo}/commits?{urlencode(nq)}>; rel="next"'
        return Resp(200, [self._entry(s) for s in chunk], headers)
