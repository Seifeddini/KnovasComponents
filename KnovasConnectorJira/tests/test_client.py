"""The credential stays at the site (GI-CONN-07, SPEC-write-back-20's connector half): read from the environment there,
sent only in the ``Authorization`` header, never in a result, a stream, a log line or an error. Settings name the
site and the project; the package names neither (the pilot project is configuration, not code). Rate limits wait as
Jira says, and a write is never repeated by the client."""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path

import pytest

from fake_jira import SITE, Answer
from harness import SOURCE, JiraHarness
from knovas_connector_jira import client as C
from knovas_connector_kit.conformance.suite import Brain

SRC = Path(__file__).resolve().parents[1] / "src" / "knovas_connector_jira"


def test_the_credential_comes_from_the_sites_environment_by_source_then_by_kind():
    env = {"SYNC_CREDENTIAL_JIRA": "kind-wide", "SYNC_CREDENTIAL_JIRA_DEMO": "per-source"}
    assert C.credential_from_environment({"key": "jira-demo"}, env) == "per-source"
    assert C.credential_from_environment({"key": "other"}, env) == "kind-wide"
    assert C.credential_from_environment({"key": "other"}, {}) is None


def test_an_email_and_token_is_basic_a_bare_token_is_bearer_and_none_is_refused():
    assert C.authorization("a@example.invalid:t") == "Basic " + base64.b64encode(b"a@example.invalid:t").decode()
    assert C.authorization("t") == "Bearer t"
    for bad in (None, "", "a\r\nX-Injected: 1"):
        with pytest.raises(C.JiraError) as raised:
            C.authorization(bad)
        assert raised.value.code == "jira.no_credential"


@pytest.mark.parametrize("settings", [
    {}, {"site": SITE}, {"project": "DEMO"}, {"site": "http://jira.example.invalid", "project": "DEMO"},
    {"site": "https://user:secret@jira.example.invalid", "project": "DEMO"},
    {"site": "https://jira.example.invalid/path", "project": "DEMO"},
    {"site": SITE, "project": "demo"}, {"site": SITE, "project": "DEMO", "fields": ["summary; drop"]},
    {"site": SITE, "project": "DEMO", "fields": ["issuelinks"]}, {"site": SITE, "project": "DEMO", "page_size": 500},
])
def test_settings_that_are_not_of_their_form_are_refused(settings):
    with pytest.raises(C.JiraError) as raised:
        C.settings_of({"settings": settings})
    assert raised.value.code == "jira.bad_settings"


def test_the_package_names_no_site_no_project_and_no_issue():
    text = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))
    assert "atlassian.net" not in text
    keys = re.findall(r"(?<![A-Za-z0-9-])([A-Z][A-Z0-9_]{1,9})-[0-9]+\b", text)     # GI-DATA-04: DATA follows a dash
    assert set(keys) <= {"WB", "FEAT", "BET", "SHA"}, keys        # the repository's ids (WB-07, FEAT-2026-…), SHA-256


def test_a_rate_limit_waits_as_jira_says_then_answers():
    h = JiraHarness()
    waits = []
    h.connector.sleep = waits.append
    h.jira.rate_limit_next = 2
    assert len([m for m in h.connector.read(h.source, None) if m["type"] == "record"]) == 6
    assert waits == [2.0, 2.0]


def test_a_failed_write_is_not_sent_again_by_the_client():
    h = JiraHarness()
    one = {"action": "set_assignee", "target": "DEMO-1", "params": {"assignee": "acct-7"}}
    brain = Brain(h.source)
    job = brain.act([one], ["0"])
    original = h.jira.request
    calls = []

    def failing_writes(method, url, **kw):
        if method == "PUT":
            calls.append(method)
            return Answer(503, {"errorMessages": ["busy"]})
        return original(method, url, **kw)
    h.jira.request = failing_writes
    got = h.connector.handle(job)
    assert (got["status"], got["code"]) == ("failed", "jira.http_503") and calls == ["PUT"]


def test_a_read_answering_5xx_is_retried():
    h = JiraHarness()
    h.connector.sleep = lambda s: None
    h.jira.fail_reads_with = 502
    one = {"action": "set_assignee", "target": "DEMO-1", "params": {"assignee": "acct-7"}}
    got = h.connector.handle(Brain(h.source).preview([one]))
    assert (got["status"], got["code"]) == ("failed", "jira.http_502")
    assert sum(1 for c in h.jira.calls if c == ("GET", "/issue/DEMO-1")) == 4      # once and three retries


def test_a_wrong_credential_is_unauthorized_and_the_error_carries_no_secret():
    h = JiraHarness()
    h.connector._credential = lambda s: "someone@example.invalid:wrong-token"
    got = h.connector.handle(Brain(h.source).preview([{"action": "set_assignee", "target": "DEMO-1",
                                                       "params": {"assignee": "acct-7"}}]))
    assert (got["status"], got["code"]) == ("failed", "jira.unauthorized") and "wrong-token" not in json.dumps(got)


def test_nothing_that_leaves_the_connector_carries_the_credential_or_an_issue(caplog):
    caplog.set_level(logging.DEBUG)
    h = JiraHarness()
    token = h.credential.split(":", 1)[1]
    header = base64.b64encode(h.credential.encode()).decode()
    brain = Brain(h.source)
    out = [h.connector.read(h.source, None)]
    one = {"action": "set_assignee", "target": "DEMO-1", "params": {"assignee": "acct-7"}}
    prev = h.connector.handle(brain.preview([one]))
    out += [prev, h.connector.handle(brain.act([one], ["0"]))]
    for _ in range(2):
        h.jira.fail_next_with_secret = True
        out.append(h.connector.handle(brain.preview([one])))
    h.jira.fail_next_with_secret = True
    with pytest.raises(C.JiraError):
        h.connector.read(h.source, None)
    text = json.dumps(out) + "\n".join(r.getMessage() + json.dumps(r.__dict__, default=str) for r in caplog.records)
    assert token not in text and header not in text
    logged = "\n".join(r.getMessage() + json.dumps(r.__dict__, default=str) for r in caplog.records)
    assert caplog.records and "DEMO-" not in logged and "acct-" not in logged
