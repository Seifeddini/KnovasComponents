"""J-4: the Jira connector at a site, behind Knovas Link (``site.py``): it registers as its own component of kind
``jira`` with its protocol 2.0 declaration, sends its heartbeat, answers ``check`` per source and refuses what it does
not run, and answers Link's jobs (probe, preview, act, reread) through the connector, with the source's settings from
Link's configuration and its credential from the site's environment only (GI-CONN-07). The site, the project and the
issues are made up (``fake_jira.py``, ``fake_link.py``)."""
from __future__ import annotations

import logging

import pytest

from fake_jira import HOST, PROJECT, SITE, FakeJira
from fake_link import TOKEN, URL, FakeLink
from knovas_connector_jira import site as S
from knovas_connector_kit import contracts
from knovas_connector_kit.conformance.suite import Brain

KEY = "jira-main"
SECRET = "someone@example.invalid:site-token-0123456789"
ENV = {"SYNC_CREDENTIAL_JIRA_MAIN": SECRET, "SYNC_CREDENTIAL_JIRA_MAIN_HOST": HOST}
SOURCE = {"workspace": "ws-1", "key": KEY, "kind": "jira", "access": "read_write", "component": "jira-connector",
          "settings": {"site": SITE, "project": PROJECT, "fields": ["summary", "status", "assignee", "duedate"]}}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def world():
    jira = FakeJira(SECRET)
    jira.add(1, summary="Write the onboarding guide", assignee="acct-1")
    jira.add(2, summary="Review the release notes")
    link = FakeLink([dict(SOURCE)])
    clock = Clock()
    site = S.Site(S.LinkClient(URL, "jira-connector", TOKEN, session=link), environ=dict(ENV), jira_session=jira,
                  clock=clock, sleep=lambda seconds: None)
    return {"jira": jira, "link": link, "site": site, "clock": clock}


def everything_sent(link: FakeLink) -> str:
    return repr([link.registered, link.beats, link.checks, link.results, link.headers_seen])


# ------------------------------------------------------------------ registration and heartbeat

def test_it_registers_as_its_own_component_with_its_declaration_and_beats_right_away(world):
    world["site"].tick()
    [reg] = world["link"].registered
    assert reg["component"] == "jira-connector" and reg["kind"] == "jira"
    assert reg["job_kinds"] == ["probe", "preview", "act", "reread"]
    assert contracts.valid("declaration.schema.json", reg["declaration"])
    assert reg["declaration"]["source_kind"] == reg["kind"] == "jira"
    assert [b["state"] for b in world["link"].beats] == ["online"], "the first heartbeat follows the registration"
    assert world["link"].checks == [[{"workspace": "ws-1", "source": KEY, "code": "source.ok", "counts": {}}]], \
        "the first heartbeat reports whether each source can be read"
    world["site"].tick()
    assert len(world["link"].registered) == 1 and len(world["link"].beats) == 2
    world["clock"].now += S.REGISTER_SECONDS + 1
    world["site"].tick()
    assert len(world["link"].registered) == 2, "registers again, so a restarted Link knows it again"


def test_check_reports_per_source_whether_it_can_be_read_and_a_wrong_credential_degrades_it(world):
    world["link"].commands = [{"id": "c1", "command": "check", "args": {}}]
    world["site"].tick()
    assert world["link"].done == {"c1": "ok"}
    assert world["link"].checks == [[{"workspace": "ws-1", "source": KEY, "code": "source.ok", "counts": {}}]]
    world["site"].environ = dict(ENV, SYNC_CREDENTIAL_JIRA_MAIN="someone@example.invalid:wrong")
    world["link"].commands = [{"id": "c2", "command": "check", "args": {}}]
    world["site"].tick()
    assert world["link"].checks[-1][0]["code"] == "jira.unauthorized"
    world["site"].tick()
    assert world["link"].beats[-1]["state"] == "degraded" and world["link"].beats[-1]["open_errors"] == 1


def test_a_heartbeat_checks_again_only_when_the_sources_changed(world):
    world["site"].tick()
    world["site"].tick()
    assert len(world["link"].checks) == 1, "nothing changed: no second check"
    world["link"].sources[0]["settings"] = dict(SOURCE["settings"], fields=["summary"])
    world["clock"].now += S.SOURCES_SECONDS + 1
    world["site"].tick()
    assert len(world["link"].checks) == 2 and world["link"].checks[-1][0]["code"] == "source.ok"


def test_a_site_setting_naming_another_host_is_reported_bad_settings_and_nothing_is_sent(world):
    world["link"].sources[0]["settings"] = dict(SOURCE["settings"], site="https://elsewhere.example.invalid")
    world["link"].commands = [{"id": "c1", "command": "check", "args": {}}]
    world["site"].tick()
    assert world["link"].checks[-1][0]["code"] == "jira.bad_settings" and world["jira"].calls == []


def test_it_runs_no_mappings_so_run_is_refused_and_the_rest_acknowledged(world):
    world["link"].commands = [{"id": "r1", "command": "run", "args": {"kind": "dry_run"}},
                              {"id": "r2", "command": "approve_plan", "args": {}},
                              {"id": "r3", "command": "reload_config", "args": {}},
                              {"id": "r4", "command": "pause", "args": {}}]
    world["site"].tick()
    assert world["link"].done == {"r1": "refused", "r2": "refused", "r3": "ok", "r4": "ok"}


# ------------------------------------------------------------------ jobs

def test_a_probe_is_answered_at_once_and_an_unknown_kind_is_unsupported(world):
    world["link"].jobs = [{"id": "p1", "kind": "probe", "args": {}}, {"id": "f1", "kind": "fetch", "args": {}}]
    assert world["site"].poll_once(0) and world["site"].poll_once(0)
    assert world["link"].results["p1"]["status"] == "ok" and "code" not in world["link"].results["p1"]
    assert (world["link"].results["f1"]["status"], world["link"].results["f1"]["code"]) == ("unsupported", "unsupported")
    assert world["site"].poll_once(0) is False


def test_preview_then_act_through_link_write_once_with_the_settings_from_link(world):
    brain = Brain(KEY)
    one = {"action": "set_assignee", "target": f"{PROJECT}-2", "params": {"assignee": "acct-7"}}
    job = brain.preview([one])
    world["link"].jobs = [job]
    world["site"].poll_once(0)
    previewed = world["link"].results[job["id"]]
    assert contracts.valid("result.schema.json", previewed) and previewed["preview"]["exact"] is True
    assert previewed["preview"]["changes"] == [{"step": 0, "target": f"{PROJECT}-2", "field": "assignee",
                                               "before": None, "after": "acct-7"}]
    assert world["jira"].writes.get(f"{PROJECT}-2", 0) == 0, "a preview writes nothing"
    expected = previewed["preview"]["read_versions"][0]["version"]
    act = brain.act([dict(one, expected_version=expected)], [expected])
    world["link"].jobs = [act]
    world["site"].poll_once(0)
    first = world["link"].results[act["id"]]
    world["link"].jobs = [act]                                  # delivered again (a lost answer)
    world["site"].poll_once(0)
    again = world["link"].results[act["id"]]
    assert first["status"] == "ok" and first["receipts"][0]["values"][0]["after"] == "acct-7"
    assert again["status"] == "ok" and again["receipts"][0]["permit_id"] == first["receipts"][0]["permit_id"]
    assert world["jira"].writes[f"{PROJECT}-2"] == 1, "the repeat finds the permit marker and writes nothing"
    reread = brain.job("reread", {"targets": [f"{PROJECT}-2"], "permit_ids": [first["receipts"][0]["permit_id"]]})
    world["link"].jobs = [reread]
    world["site"].poll_once(0)
    assert world["link"].results[reread["id"]]["receipts"][0]["values"][0]["after"] == "acct-7"


def test_a_source_switched_to_read_in_the_builder_is_never_written(world):
    world["link"].sources[0]["access"] = "read"
    brain = Brain(KEY)
    one = {"action": "set_due_date", "target": f"{PROJECT}-1", "params": {"due_date": "2026-12-01"}}
    job = brain.preview([one])
    world["link"].jobs = [job]
    world["site"].poll_once(0)
    expected = world["link"].results[job["id"]]["preview"]["read_versions"][0]["version"]
    act = brain.act([dict(one, expected_version=expected)], [expected])
    world["link"].jobs = [act]
    world["site"].poll_once(0)
    assert (world["link"].results[act["id"]]["status"], world["link"].results[act["id"]]["code"]) == \
        ("refused", "write_disabled")
    assert world["jira"].writes.get(f"{PROJECT}-1", 0) == 0


def test_a_source_link_does_not_assign_to_it_is_unsupported(world):
    one = {"action": "set_assignee", "target": f"{PROJECT}-1", "params": {"assignee": "acct-7"}}
    job = Brain("someone-elses").preview([one])
    world["link"].jobs = [job]
    world["site"].poll_once(0)
    assert world["link"].results[job["id"]]["status"] == "unsupported" and world["jira"].calls == []


def test_a_job_that_raises_is_failed_internal_and_logged_by_its_type_only(world, caplog, monkeypatch):
    def boom(job):
        raise RuntimeError(f"boom with {SECRET}")
    monkeypatch.setattr(world["site"], "answer", boom)
    world["link"].jobs = [{"id": "b1", "kind": "preview", "args": {}}]
    with caplog.at_level(logging.DEBUG):
        world["site"].poll_once(0)
    assert (world["link"].results["b1"]["status"], world["link"].results["b1"]["code"]) == ("failed", "internal")
    assert SECRET not in caplog.text and "RuntimeError" in caplog.text


def test_the_credential_and_the_issues_never_reach_link_or_a_log_line(world, caplog):
    brain = Brain(KEY)
    one = {"action": "set_assignee", "target": f"{PROJECT}-1", "params": {"assignee": "acct-7"}}
    world["link"].commands = [{"id": "c", "command": "check", "args": {}}]
    world["link"].jobs = [brain.preview([one]), {"id": "q", "kind": "probe", "args": {}}]
    with caplog.at_level(logging.DEBUG):
        world["site"].tick()
        world["site"].poll_once(0)
        world["site"].poll_once(0)
    sent = everything_sent(world["link"])
    for secret in (SECRET, SECRET.split(":", 1)[1]):
        assert secret not in sent and secret not in caplog.text
    assert HOST not in caplog.text and f"{PROJECT}-1" not in caplog.text and "onboarding" not in caplog.text


# ------------------------------------------------------------------ starting

def test_serve_needs_links_address_and_its_token(tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        assert S.serve({}, once=True) == 2
        assert S.serve({"SYNC_LINK_URL": URL, "SYNC_LINK_TOKEN_FILE": str(tmp_path / "none")}, once=True) == 2
    assert "SYNC_LINK_URL" in caplog.text and "LINK_COMPONENTS" in caplog.text
    token = tmp_path / "token"
    token.write_text("two words\n", encoding="ascii")
    assert S.serve({"SYNC_LINK_URL": URL, "SYNC_LINK_TOKEN_FILE": str(token)}, once=True) == 2


def test_the_client_reads_its_token_and_names_itself(tmp_path):
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n", encoding="ascii")
    client = S.link_client({"SYNC_LINK_URL": URL + "/", "SYNC_LINK_TOKEN_FILE": str(token)})
    assert client.url == URL + "/local/v1" and client.name == S.DEFAULT_NAME == "jira-connector"
    assert client.headers == {"Authorization": f"Bearer {TOKEN}", "X-Component": "jira-connector"}
    named = S.link_client({"SYNC_LINK_URL": URL, "SYNC_LINK_TOKEN_FILE": str(token), "SYNC_NAME": "jira-2"})
    assert named.name == "jira-2"
