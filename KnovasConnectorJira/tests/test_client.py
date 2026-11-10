"""The credential stays at the site (GI-CONN-07, SPEC-write-back-20's connector half): read from the environment there,
sent only in the ``Authorization`` header and only to the host the site's environment binds to it, never in a result,
a stream, a log line or an error, never to a host the Platform's settings name instead, never along a redirect.
Settings name the site and the project; the package names neither (the pilot project is configuration, not code).
Rate limits wait as Jira says, and a write is never repeated by the client."""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

from fake_jira import HOST, SITE, Answer
from harness import SOURCE, JiraHarness
from knovas_connector_jira import client as C
from knovas_connector_jira.connector import JiraConnector
from knovas_connector_kit.conformance.suite import Brain

OTHER = "collector.example.invalid"
ONE = {"action": "set_assignee", "target": "DEMO-1", "params": {"assignee": "acct-7"}}

SRC = Path(__file__).resolve().parents[1] / "src" / "knovas_connector_jira"


def test_the_credential_comes_from_the_sites_environment_by_source_then_by_kind_with_the_host_beside_it():
    env = {"SYNC_CREDENTIAL_JIRA": "kind-wide", "SYNC_CREDENTIAL_JIRA_HOST": "kind.example.invalid",
           "SYNC_CREDENTIAL_JIRA_DEMO": "per-source", "SYNC_CREDENTIAL_JIRA_DEMO_HOST": " Source.Example.Invalid "}
    assert C.credential_from_environment({"key": "jira-demo"}, env) == C.Credential("per-source",
                                                                                    "source.example.invalid")
    assert C.credential_from_environment({"key": "other"}, env) == C.Credential("kind-wide", "kind.example.invalid")
    assert C.credential_from_environment({"key": "other"}, {}) is None
    assert "per-source" not in repr(C.credential_from_environment({"key": "jira-demo"}, env))


@pytest.mark.parametrize("host", [None, "", "https://jira.example.invalid", "jira.example.invalid/path",
                                  "user@jira.example.invalid", "jira.example.invalid:8443", "localhost"])
def test_a_credential_without_a_host_name_beside_it_is_none_and_never_takes_the_other_lines_host(host):
    env = {"SYNC_CREDENTIAL_JIRA_DEMO": "per-source", "SYNC_CREDENTIAL_JIRA": "kind-wide",
           "SYNC_CREDENTIAL_JIRA_HOST": HOST}
    if host is not None:
        env["SYNC_CREDENTIAL_JIRA_DEMO_HOST"] = host
    assert C.credential_from_environment({"key": "jira-demo"}, env) is None


class Recorder:
    """A session that notes every request it is handed (host, and whether it carried the credential) before the fake
    Jira answers it, whichever host it names."""

    def __init__(self, jira):
        self.jira, self.sent = jira, []

    def request(self, method, url, **kw):
        self.sent.append((urlsplit(url).hostname, "Authorization" in (kw.get("headers") or {})))
        return self.jira.request(method, url, **kw)


def _connector_from_environment(h: JiraHarness, site: str, env: dict) -> tuple[JiraConnector, Recorder]:
    source = {"key": "jira-main", "kind": "jira", "access": "read_write",
              "settings": {"site": site, "project": "DEMO"}}
    recorder = Recorder(h.jira)
    connector = JiraConnector(source=lambda key: dict(source) if key == "jira-main" else None,
                              credential=lambda s: C.credential_from_environment(s, env), session=recorder,
                              sleep=lambda seconds: None)
    return connector, recorder


def _every_job(connector: JiraConnector) -> list[dict]:
    brain = Brain("jira-main")
    return [connector.handle(brain.preview([ONE])), connector.handle(brain.act([ONE], ["0"])),
            connector.handle(brain.job("reread", {"targets": ["DEMO-1"]}))]


def test_a_site_the_credential_is_not_bound_to_is_refused_and_nothing_is_sent_there_or_anywhere():
    """The verifier's N1: the Platform's settings name another https host; the first read posted the header there."""
    h = JiraHarness()
    env = {"SYNC_CREDENTIAL_JIRA_MAIN": h.credential, "SYNC_CREDENTIAL_JIRA_MAIN_HOST": HOST}
    connector, recorder = _connector_from_environment(h, f"https://{OTHER}", env)
    with pytest.raises(C.JiraError) as raised:
        connector.read("jira-main", None)
    assert raised.value.code == "jira.bad_settings"
    assert [(r["status"], r["code"]) for r in _every_job(connector)] == [("failed", "jira.bad_settings")] * 3
    assert recorder.sent == [] and h.jira.calls == [] and h.jira.writes == {}


def test_a_credential_without_its_host_line_goes_nowhere():
    h = JiraHarness()
    connector, recorder = _connector_from_environment(h, SITE, {"SYNC_CREDENTIAL_JIRA_MAIN": h.credential})
    with pytest.raises(C.JiraError) as raised:
        connector.read("jira-main", None)
    assert raised.value.code == "jira.no_credential"
    assert [(r["status"], r["code"]) for r in _every_job(connector)] == [("failed", "jira.no_credential")] * 3
    assert recorder.sent == []


def test_the_site_the_credential_is_bound_to_is_read_and_written_and_every_request_goes_there():
    h = JiraHarness()
    env = {"SYNC_CREDENTIAL_JIRA_MAIN": h.credential, "SYNC_CREDENTIAL_JIRA_MAIN_HOST": HOST.upper()}
    connector, recorder = _connector_from_environment(h, SITE, env)
    assert len([m for m in connector.read("jira-main", None) if m["type"] == "record"]) == 6
    brain = Brain("jira-main")
    assert connector.handle(brain.preview([ONE]))["status"] == "ok"
    assert connector.handle(brain.act([ONE], ["0"]))["status"] == "ok" and h.jira.writes == {"DEMO-1": 1}
    assert recorder.sent and set(recorder.sent) == {(HOST, True)}


class Redirecting(requests.adapters.BaseAdapter):
    """A transport under a real ``requests.Session``: the credential's host answers every request with a redirect to
    another host; every request the session sends, to any host, is noted."""

    def __init__(self, status: int):
        super().__init__()
        self.status, self.seen = status, []

    def send(self, request, **kw):
        self.seen.append((urlsplit(request.url).hostname, request.headers.get("Authorization")))
        answer = requests.Response()
        answer.status_code, answer.url, answer.request, answer._content = self.status, request.url, request, b""
        answer.headers["Location"] = f"https://{OTHER}{urlsplit(request.url).path}"
        return answer

    def close(self):
        pass


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_to_another_host_is_not_followed_so_the_credential_never_reaches_it(status):
    h = JiraHarness()
    adapter = Redirecting(status)
    session = requests.Session()
    session.mount("https://", adapter)
    connector = JiraConnector(source=lambda key: dict(SOURCE) if key == SOURCE["key"] else None,
                              credential=lambda s: C.Credential(h.credential, HOST), session=session,
                              sleep=lambda seconds: None)
    with pytest.raises(C.JiraError) as raised:
        connector.read(SOURCE["key"], None)
    assert raised.value.code == f"jira.http_{status}"
    got = connector.handle(Brain(SOURCE["key"]).preview([ONE]))
    assert (got["status"], got["code"]) == ("failed", f"jira.http_{status}")
    assert [host for host, _auth in adapter.seen] == [HOST, HOST]           # one call each, none to the other host
    assert all(auth for _host, auth in adapter.seen)


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
    h.connector._credential = lambda s: C.Credential("someone@example.invalid:wrong-token", HOST)
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
