"""The conformance harness of the Jira connector (``knovas_connector_kit/conformance/harness.py``): a fresh connector
over a fake Jira (``fake_jira.py``) the suite may change. Six issues of a made-up project; the credential is a canary
the suite looks for in every result, stream message, log line and error.

    python -m knovas_connector_kit.conformance harness:make_harness     (from tests/, with both src folders on the path)
"""
from __future__ import annotations

import uuid

from fake_jira import PROJECT, SITE, FakeJira
from knovas_connector_jira.connector import JiraConnector

SOURCE = {"key": "jira-demo", "kind": "jira", "access": "read_write", "workspace": "ws-1",
          "settings": {"site": SITE, "project": PROJECT, "page_size": 2}}


class JiraHarness:
    source = SOURCE["key"]

    def __init__(self, source: dict = SOURCE):
        self.credential = f"conformance@example.invalid:canary-token-{uuid.uuid4().hex[:16]}"
        self.jira = FakeJira(self.credential)
        self.jira.add(1, summary="Write the onboarding guide", assignee="acct-1")
        self.jira.add(2, summary="Review the release notes", links=[f"{PROJECT}-1"])
        self.jira.add(3, summary="Plan the next review", duedate="2026-11-20", labels=["planning"])
        self.jira.add(4, summary="Fix the export", issuetype="Bug",
                      editable=["summary", "assignee", "priority", "labels"])     # the due date is not editable
        self.jira.add(5, summary="Sub-task of the guide", parent=f"{PROJECT}-1", story_points=2.5)
        self.jira.add(6, summary="Archive old pages", status="In Progress", story_points=3.0)
        self.source_config = dict(source)
        self.connector = JiraConnector(source=lambda key: dict(self.source_config) if key == source["key"] else None,
                                       credential=lambda s: self.credential, session=self.jira,
                                       sleep=lambda seconds: None)

    def sample_steps(self) -> list[dict]:
        return [{"action": "set_assignee", "target": f"{PROJECT}-1", "params": {"assignee": "acct-7"}},
                {"action": "transition_issue", "target": f"{PROJECT}-2", "params": {"status": "In Progress"}},
                {"action": "set_due_date", "target": f"{PROJECT}-3", "params": {"due_date": "2026-12-01"}}]

    def computed_step(self) -> dict:
        return {"action": "set_due_date", "target": f"{PROJECT}-4", "params": {"due_date": "2026-12-01"}}

    def change_behind(self, target: str) -> None:
        self.jira.edit_by_someone(target, summary="Changed in Jira by someone else")

    def delete_behind(self, target: str) -> None:
        self.jira.delete(target)

    def set_cut_listing(self, on: bool) -> None:
        self.jira.cut_listing = on

    def writes(self, target: str) -> int:
        return self.jira.writes.get(target, 0)

    def crash_after_next_write(self) -> None:
        self.jira.crash_after_next_write = True

    def fail_next_call_with_secret(self) -> None:
        self.jira.fail_next_with_secret = True

    def alter_next_write(self) -> None:
        self.jira.alter_next_write = True

    def review(self, stage_ref: str, accept: bool) -> None:
        raise AssertionError("the Jira connector declares no native_review")


def make_harness() -> JiraHarness:
    return JiraHarness()
