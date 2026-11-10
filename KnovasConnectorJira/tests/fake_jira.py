"""An in-process Jira Cloud site (REST API version 3) for one project: enough of the enhanced JQL search, issues
(fields, change log, edit metadata, properties), the edit, transitions and the assignable-user search for the Jira
connector. Written from Atlassian's OpenAPI document of the platform REST API version 3; a test helper only, the
site, project, people and issues are made up. It is a ``requests``-like session: the connector's client calls
``request(method, url, ...)`` on it and nothing leaves the process.

What it keeps like Jira does, as far as the connector relies on it:

- every change to a tracked field adds a change-log entry with a new, larger id; setting a property adds none;
- an edit or a transition sets the ``properties`` of its body in the same request;
- the edit accepts only fields in the issue's edit metadata, an assignee only when assignable;
- ``expand=changelog`` lists the entries newest first (at most ``changelog_window``), the search pages with
  ``nextPageToken`` and ``isLast``;
- a transition to Done sets the resolution and its date (a post function: a field the preview does not show).

Switches for the tests and the conformance harness: ``cut_listing`` (the search answers a page that is not the last
but names no next page), ``crash_after_next_write`` (the next write lands, its answer is a 500),
``fail_next_with_secret`` (the next call raises an error that quotes the credential), ``alter_next_write`` (an
automation changes the written field right after the write), ``rate_limit_next`` (the next calls answer 429).
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import requests

SITE = "https://jira.example.invalid"
PROJECT = "DEMO"
AUTOMATION = "automation-account"
STATUSES = {"To Do": "1", "In Progress": "3", "Done": "10001", "Rejected": "10002"}
#: (transition id, name, from statuses (None: any), to status, fields the transition screen requires)
WORKFLOW = [("11", "Start", ["To Do"], "In Progress", []), ("21", "Stop", ["In Progress"], "To Do", []),
            ("31", "Finish", ["To Do", "In Progress"], "Done", []), ("41", "Reopen", ["Done"], "To Do", []),
            ("51", "Reject", ["In Progress"], "Rejected", ["customfield_10090"])]
EDITABLE = ["summary", "assignee", "reporter", "duedate", "priority", "labels", "customfield_10016"]


class Answer:
    def __init__(self, status: int, body: Any = None, headers: Optional[dict] = None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeJira:
    def __init__(self, credential: str, *, site: str = SITE, project: str = PROJECT, page_size: Optional[int] = None):
        self.credential, self.site, self.project = credential, site, project
        self.auth = {"Basic " + base64.b64encode(credential.encode()).decode()} if ":" in credential \
            else {"Bearer " + credential}
        self.issues: dict[str, dict] = {}
        self.next_history = 10000
        self.next_id = 20000
        self.assignable = {f"acct-{n}" for n in range(1, 10)}
        self.workflow = list(WORKFLOW)
        self.writes: dict[str, int] = {}
        self.calls: list[tuple[str, str]] = []
        self.page_size = page_size
        self.changelog_window = 100
        self.editmeta_in_search = True
        self.cut_listing = False
        self.crash_after_next_write = False
        self.fail_next_with_secret = False
        self.alter_next_write = False
        self.rate_limit_next = 0
        self.fail_reads_with: Optional[int] = None

    # ------------------------------------------------------------------ building the project

    def add(self, number: int, *, summary: str, status: str = "To Do", assignee: Optional[str] = None,
            duedate: Optional[str] = None, labels: Optional[list] = None, issuetype: str = "Task",
            editable: Optional[list] = None, links: Optional[list] = None, parent: Optional[str] = None,
            story_points: Optional[float] = None) -> str:
        key = f"{self.project}-{number}"
        self.next_id += 1
        self.issues[key] = {
            "id": str(self.next_id), "key": key, "history": [], "properties": {},
            "editable": list(EDITABLE if editable is None else editable),
            "fields": {"summary": summary, "status": self._status(status), "assignee": self._person(assignee),
                       "reporter": self._person("acct-1"), "duedate": duedate,
                       "priority": {"id": "3", "name": "Medium"}, "labels": list(labels or []),
                       "issuetype": {"id": "10001", "name": issuetype}, "resolution": None,
                       "created": "2026-11-01T09:00:00.000+0000", "resolutiondate": None,
                       "issuelinks": [{"id": str(9000 + i), "type": {"name": "Blocks", "outward": "blocks",
                                                                      "inward": "is blocked by"},
                                       "outwardIssue": {"key": other}} for i, other in enumerate(links or [])],
                       "parent": {"key": parent} if parent else None, "subtasks": [],
                       "customfield_10016": story_points, "customfield_10090": None}}
        return key

    @staticmethod
    def _person(account: Optional[str]) -> Optional[dict]:
        return None if account is None else {"accountId": account, "displayName": "Person " + account[-1:],
                                             "emailAddress": f"{account}@example.invalid"}

    @staticmethod
    def _status(name: str) -> dict:
        return {"id": STATUSES[name], "name": name, "statusCategory": {"key": "new"}}

    def _record(self, key: str, author: str, items: list) -> None:
        self.next_history += 1
        self.issues[key]["history"].append({"id": str(self.next_history), "author": {"accountId": author},
                                            "created": "2026-11-10T12:00:00.000+0000", "items": items})

    def _set(self, key: str, field: str, value: Any, author: str) -> None:
        fields = self.issues[key]["fields"]
        old = fields.get(field)
        fields[field] = value

        def raw(v):
            return v.get("accountId") or v.get("id") if isinstance(v, dict) else v

        def text(v):
            return v.get("displayName") or v.get("name") if isinstance(v, dict) else v
        self._record(key, author, [{"field": field, "fieldId": field, "fieldtype": "jira", "from": raw(old),
                                    "fromString": text(old), "to": raw(value), "toString": text(value)}])

    def edit_by_someone(self, key: str, **fields: Any) -> None:
        """Someone edits the issue in Jira (not through Knovas)."""
        for field, value in fields.items():
            self._set(key, field, value, "acct-someone")

    def delete(self, key: str) -> None:
        self.issues.pop(key)

    def status_of(self, key: str) -> str:
        return self.issues[key]["fields"]["status"]["name"]

    # ------------------------------------------------------------------ the session

    def request(self, method: str, url: str, *, params: Optional[dict] = None, json: Any = None,
                headers: Optional[dict] = None, timeout: Any = None, allow_redirects: bool = True) -> Answer:
        if self.fail_next_with_secret:
            self.fail_next_with_secret = False
            raise requests.ConnectionError(f"connection reset while calling with {self.credential}")
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != self.site or not parts.path.startswith("/rest/api/3/"):
            return Answer(404, {"errorMessages": ["not found"]})
        path = parts.path[len("/rest/api/3"):]
        self.calls.append((method, path))
        if (headers or {}).get("Authorization") not in self.auth:
            return Answer(401, {"errorMessages": ["unauthorized"]})
        if self.rate_limit_next:
            self.rate_limit_next -= 1
            return Answer(429, {"errorMessages": ["rate limited"]}, {"Retry-After": "2"})
        if method == "GET" and self.fail_reads_with:
            return Answer(self.fail_reads_with, {"errorMessages": ["unavailable"]})
        params = params or {}
        if method == "POST" and path == "/search/jql":
            return self._search(json or {})
        if method == "GET" and path == "/user/assignable/search":
            account = params.get("accountId")
            if params.get("issueKey") not in self.issues:
                return Answer(404, {"errorMessages": ["not found"]})
            return Answer(200, [{"accountId": account}] if account in self.assignable else [])
        m = re.match(r"^/issue/([A-Z][A-Z0-9_]*-[0-9]+)(/transitions|/editmeta)?$", path)
        if not m or m.group(1) not in self.issues:
            return Answer(404, {"errorMessages": ["Issue does not exist or you do not have permission to see it."]})
        key, rest = m.group(1), m.group(2)
        if method == "GET" and rest is None:
            return Answer(200, self._issue(key, params.get("fields", ""), params.get("expand", ""),
                                           params.get("properties", "")))
        if method == "GET" and rest == "/editmeta":
            return Answer(200, self._editmeta(key))
        if method == "GET" and rest == "/transitions":
            return Answer(200, {"transitions": self._transitions(key, "transitions.fields" in params.get("expand", ""))})
        if method == "PUT" and rest is None:
            return self._write(key, json or {}, transition=False)
        if method == "POST" and rest == "/transitions":
            return self._write(key, json or {}, transition=True)
        return Answer(405, {"errorMessages": ["method not allowed"]})

    # ------------------------------------------------------------------ reads

    def _editmeta(self, key: str) -> dict:
        return {"fields": {f: {"required": False, "name": f, "key": f, "operations": ["set"]}
                           for f in self.issues[key]["editable"]}}

    def _issue(self, key: str, fields: str, expand: str, properties: str) -> dict:
        issue = self.issues[key]
        wanted = [f for f in fields.split(",") if f] if fields else list(issue["fields"])
        out = {"id": issue["id"], "key": key, "fields": {f: issue["fields"].get(f) for f in wanted}}
        if "changelog" in expand:
            newest_first = list(reversed(issue["history"]))[:self.changelog_window]
            out["changelog"] = {"startAt": 0, "maxResults": len(newest_first), "total": len(newest_first),
                                "histories": newest_first}
        if "editmeta" in expand:
            out["editmeta"] = self._editmeta(key)
        if properties:
            out["properties"] = {p: issue["properties"][p] for p in properties.split(",") if p in issue["properties"]}
        return out

    def _search(self, body: dict) -> Answer:
        m = re.match(r'^project = "([A-Z][A-Z0-9_]*)" ORDER BY key ASC$', str(body.get("jql") or ""))
        if not m:
            return Answer(400, {"errorMessages": ["unbounded or unknown query"]})
        keys = sorted((k for k in self.issues if k.startswith(m.group(1) + "-")), key=lambda k: int(k.split("-")[1]))
        size = self.page_size or int(body.get("maxResults") or 50)
        start = int(body.get("nextPageToken") or 0)
        page = keys[start:start + size]
        expand = str(body.get("expand") or "")
        if not self.editmeta_in_search:
            expand = expand.replace("editmeta", "")
        issues = [self._issue(k, ",".join(body.get("fields") or []), expand, "") for k in page]
        last = start + size >= len(keys)
        if self.cut_listing and not last:
            return Answer(200, {"issues": issues, "isLast": False})
        answer = {"issues": issues, "isLast": last}
        if not last:
            answer["nextPageToken"] = str(start + size)
        return Answer(200, answer)

    def _transitions(self, key: str, with_fields: bool) -> list:
        current = self.status_of(key)
        out = []
        for tid, name, sources, to, required in self.workflow:
            if sources is not None and current not in sources:
                continue
            t = {"id": tid, "name": name, "hasScreen": bool(required), "isAvailable": True, "isConditional": False,
                 "isGlobal": sources is None, "isInitial": False,
                 "to": {"id": STATUSES[to], "name": to, "statusCategory": {"key": "done" if to == "Done" else "new"}}}
            if with_fields:
                t["fields"] = {f: {"required": True, "hasDefaultValue": False, "name": f, "key": f,
                                   "operations": ["set"]} for f in required}
            out.append(t)
        return out

    # ------------------------------------------------------------------ writes

    def _write(self, key: str, body: dict, *, transition: bool) -> Answer:
        issue = self.issues[key]
        properties = body.get("properties") or []
        if any(not isinstance(p, dict) or not p.get("key") or "value" not in p
               or len(json.dumps(p["value"])) > 32768 for p in properties):
            return Answer(400, {"errorMessages": ["bad property"]})
        if transition:
            tid = str((body.get("transition") or {}).get("id") or "")
            found = next((t for t in self._transitions(key, True) if t["id"] == tid), None)
            if found is None:
                return Answer(400, {"errorMessages": ["transition not valid"]})
            if any(f not in (body.get("fields") or {}) for f in found.get("fields") or {}):
                return Answer(400, {"errors": {"field": "required"}})
            to = found["to"]["name"]
            changes = {"status": self._status(to)}
            if to == "Done":
                changes["resolution"] = {"id": "10000", "name": "Done"}
            elif issue["fields"]["resolution"] is not None:
                changes["resolution"] = None
        else:
            fields = body.get("fields") or {}
            if not fields or any(f not in issue["editable"] for f in fields):
                return Answer(400, {"errors": {"field": "cannot be set"}})
            changes = {}
            for f, v in fields.items():
                if f == "assignee":
                    account = (v or {}).get("accountId") if v is not None else None
                    if account is not None and account not in self.assignable:
                        return Answer(400, {"errors": {"assignee": "cannot be assigned"}})
                    changes[f] = self._person(account)
                else:
                    changes[f] = v
        items = []
        for f, v in changes.items():
            old = issue["fields"].get(f)
            issue["fields"][f] = v
            items.append({"field": f, "fieldId": f, "fieldtype": "jira",
                          "from": (old or {}).get("accountId") or (old or {}).get("id") if isinstance(old, dict) else old,
                          "fromString": (old or {}).get("name") if isinstance(old, dict) else old,
                          "to": (v or {}).get("accountId") or (v or {}).get("id") if isinstance(v, dict) else v,
                          "toString": (v or {}).get("name") if isinstance(v, dict) else v})
        if "resolution" in changes:
            issue["fields"]["resolutiondate"] = "2026-11-10T12:00:00.000+0000" if changes["resolution"] else None
        self._record(key, "acct-knovas", items)
        for p in properties:
            issue["properties"][p["key"]] = json.loads(json.dumps(p["value"]))
        self.writes[key] = self.writes.get(key, 0) + 1
        if self.alter_next_write:
            self.alter_next_write = False
            field = next(iter(changes))
            if field == "status":
                self._set(key, "status", self._status("Rejected"), AUTOMATION)
            elif field == "assignee":
                self._set(key, "assignee", self._person("acct-9"), AUTOMATION)
            else:
                self._set(key, field, "2099-01-01", AUTOMATION)
        if self.crash_after_next_write:
            self.crash_after_next_write = False
            return Answer(500, {"errorMessages": ["internal error"]})
        return Answer(204)
