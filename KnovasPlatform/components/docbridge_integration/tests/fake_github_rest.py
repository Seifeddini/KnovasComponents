"""An in-process GitHub REST API for one repository, over real HTTP: enough of contents, blobs, refs, commits, compare
and pull requests for the sync component's fetch and write jobs (MVP-21, MVP-22) and its diff polling (KG-2). A test helper only; the repository and
company are made up.

Tokens: ``read`` tokens may read, ``write`` tokens may also create branches, commit and open pull requests; any other
write answers 403 as GitHub does for a token without contents or pull requests write (``deny_with = 404`` answers as
GitHub does in places, to hide a repository's writes). ``rate_limited`` makes the next write answer 403 with
``X-RateLimit-Remaining: 0``. ``faults`` makes one write step answer a given status: keys ``ref`` (create a branch),
``put`` (commit a file), ``pull`` (open a pull request), ``delete`` (delete a branch).
"""
from __future__ import annotations

import base64
import hashlib
import threading
from types import SimpleNamespace

from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

REPO = "example-org/handbook"


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGitHub:
    def __init__(self, repo: str = REPO, *, tokens: dict[str, str] | None = None, default_branch: str = "main"):
        self.repo = repo
        self.tokens = tokens or {"read-token": "read", "write-token": "write"}
        self.default_branch = default_branch
        self.blobs: dict[str, bytes] = {}
        self.commits: dict[str, dict] = {}
        self.branches: dict[str, str] = {}
        self.pulls: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.rate_limited = False
        self.deny_with = 403
        self.faults: dict[str, int] = {}
        self.lock = threading.Lock()
        self.app = self._app()

    # ------------------------------------------------------------------ building the repository

    def commit(self, files: dict[str, bytes | None], branch: str | None = None, message: str = "change") -> str:
        branch = branch or self.default_branch
        parent = self.branches.get(branch)
        tree = dict(self.commits[parent]["tree"]) if parent else {}
        for path, data in files.items():
            if data is None:
                tree.pop(path, None)
            else:
                self.blobs[blob_sha(data)] = data
                tree[path] = blob_sha(data)
        sha = hashlib.sha1(f"{parent}:{sorted(tree.items())}:{message}:{len(self.commits)}".encode()).hexdigest()
        self.commits[sha] = {"tree": tree, "parent": parent, "message": message}
        self.branches[branch] = sha
        return sha

    def file_at(self, ref: str, path: str) -> bytes | None:
        commit = self.branches.get(ref, ref)
        sha = (self.commits.get(commit) or {}).get("tree", {}).get(path)
        return self.blobs.get(sha) if sha else None

    # ------------------------------------------------------------------ the API

    def _app(self) -> Flask:
        app = Flask("fake_github_rest")
        gh = self
        prefix = f"/repos/{self.repo}"

        def access() -> str | None:
            auth = request.headers.get("Authorization", "")
            return gh.tokens.get(auth[7:]) if auth.startswith("Bearer ") else None

        def read():
            gh.calls.append((request.method, request.path))
            if access() is None:
                return jsonify({"message": "Bad credentials"}), 401
            return None

        def write(step: str):
            gh.calls.append((request.method, request.path))
            if step in gh.faults:
                status = gh.faults[step]
                return Response('{"message": "fault"}', status=status,
                                headers={"X-RateLimit-Remaining": "4999", "Content-Type": "application/json"})
            if gh.rate_limited:
                gh.rate_limited = False
                return Response('{"message": "API rate limit exceeded"}', status=403,
                                headers={"X-RateLimit-Remaining": "0", "Content-Type": "application/json"})
            level = access()
            if level is None:
                return jsonify({"message": "Bad credentials"}), 401
            if level != "write":
                message = "Resource not accessible by personal access token" if gh.deny_with == 403 else "Not Found"
                return Response('{"message": "%s"}' % message, status=gh.deny_with,
                                headers={"X-RateLimit-Remaining": "4999", "Content-Type": "application/json"})
            return None

        @app.get(prefix)
        def repository():
            return read() or jsonify({"full_name": gh.repo, "default_branch": gh.default_branch})

        @app.get(prefix + "/contents/<path:path>")
        def contents(path):
            denied = read()
            if denied:
                return denied
            ref = request.args.get("ref") or gh.default_branch
            commit = gh.branches.get(ref, ref)
            tree = (gh.commits.get(commit) or {}).get("tree")
            if tree is None:
                return jsonify({"message": "No commit found for the ref"}), 404
            if path in tree:
                data = gh.blobs[tree[path]]
                return jsonify({"type": "file", "path": path, "name": path.rsplit("/", 1)[-1], "sha": tree[path],
                                "size": len(data), "encoding": "base64",
                                "content": base64.b64encode(data).decode() if len(data) < 1_000_000 else ""})
            below = [p for p in tree if p.startswith(path.rstrip("/") + "/")]
            if below:
                return jsonify([{"type": "file", "path": p, "sha": tree[p]} for p in below])
            return jsonify({"message": "Not Found"}), 404

        @app.get(prefix + "/git/blobs/<sha>")
        def blob(sha):
            denied = read()
            if denied:
                return denied
            if sha not in gh.blobs:
                return jsonify({"message": "Not Found"}), 404
            if request.headers.get("Accept") != "application/vnd.github.raw+json":
                return jsonify({"sha": sha, "size": len(gh.blobs[sha]), "encoding": "base64",
                                "content": base64.b64encode(gh.blobs[sha]).decode()})
            return Response(gh.blobs[sha], mimetype="application/vnd.github.raw")

        @app.get(prefix + "/commits/<path:ref>")
        def commit(ref):
            denied = read()
            if denied:
                return denied
            sha = gh.branches.get(ref, ref if ref in gh.commits else None)
            if sha is None:
                return jsonify({"message": "Not Found"}), 404
            return Response(sha, mimetype="application/vnd.github.sha")

        @app.get(prefix + "/compare/<path:spec>")
        def compare(spec):
            """base...head as GitHub answers it: status, total_commits and the files that differ (no renames)."""
            denied = read()
            if denied:
                return denied
            base, _, head = spec.partition("...")
            if base not in gh.commits or head not in gh.commits:
                return jsonify({"message": "Not Found"}), 404
            chain, at = [], head
            while at is not None and at != base:
                chain.append(at)
                at = gh.commits[at]["parent"]
            status = "identical" if base == head else ("ahead" if at == base else "diverged")
            old, new = gh.commits[base]["tree"], gh.commits[head]["tree"]
            files = [{"filename": f, "status": "removed" if f not in new else "added" if f not in old else "modified"}
                     for f in sorted(set(old) | set(new)) if old.get(f) != new.get(f)]
            return jsonify({"status": status, "total_commits": len(chain) if status == "ahead" else 0, "files": files,
                            "commits": []})

        @app.get(prefix + "/git/ref/heads/<path:branch>")
        def ref(branch):
            denied = read()
            if denied:
                return denied
            if branch not in gh.branches:
                return jsonify({"message": "Not Found"}), 404
            return jsonify({"ref": "refs/heads/" + branch, "object": {"type": "commit", "sha": gh.branches[branch]}})

        @app.post(prefix + "/git/refs")
        def create_ref():
            denied = write("ref")
            if denied:
                return denied
            body = request.get_json()
            name = str(body.get("ref") or "")
            if not name.startswith("refs/heads/") or body.get("sha") not in gh.commits:
                return jsonify({"message": "Invalid request"}), 422
            branch = name[len("refs/heads/"):]
            with gh.lock:
                if branch in gh.branches:
                    return jsonify({"message": "Reference already exists"}), 422
                gh.branches[branch] = body["sha"]
            return jsonify({"ref": name, "object": {"sha": body["sha"]}}), 201

        @app.delete(prefix + "/git/refs/heads/<path:branch>")
        def delete_ref(branch):
            denied = write("delete")
            if denied:
                return denied
            with gh.lock:
                if gh.branches.pop(branch, None) is None:
                    return jsonify({"message": "Reference does not exist"}), 422
            return Response(status=204)

        @app.put(prefix + "/contents/<path:path>")
        def put(path):
            denied = write("put")
            if denied:
                return denied
            body = request.get_json()
            branch = body.get("branch") or gh.default_branch
            if branch not in gh.branches:
                return jsonify({"message": "Branch not found"}), 404
            current = gh.commits[gh.branches[branch]]["tree"].get(path)
            if current is not None and body.get("sha") != current:
                return jsonify({"message": "does not match"}), 409
            data = base64.b64decode(body["content"])
            sha = gh.commit({path: data}, branch=branch, message=body["message"])
            return jsonify({"content": {"sha": blob_sha(data)}, "commit": {"sha": sha}}), 200

        @app.post(prefix + "/pulls")
        def open_pull():
            denied = write("pull")
            if denied:
                return denied
            body = request.get_json()
            if body.get("head") not in gh.branches or body.get("base") not in gh.branches:
                return jsonify({"message": "Validation Failed"}), 422
            if any(p["head"] == body["head"] for p in gh.pulls):
                return jsonify({"message": "A pull request already exists"}), 422
            n = len(gh.pulls) + 1
            pull = {"number": n, "html_url": f"https://github.example.test/{gh.repo}/pull/{n}", "head": body["head"],
                    "base": body["base"], "title": body["title"], "body": body["body"]}
            gh.pulls.append(pull)
            return jsonify(pull), 201

        @app.get(prefix + "/pulls")
        def list_pulls():
            denied = read()
            if denied:
                return denied
            owner = gh.repo.split("/")[0]
            head = request.args.get("head", "")
            return jsonify([p for p in gh.pulls if f"{owner}:{p['head']}" == head])

        return app

    def serve(self) -> SimpleNamespace:
        server = make_server("127.0.0.1", 0, self.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        return SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", server=server, thread=thread)
