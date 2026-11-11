"""Which credential the sync component sends, and where (SPEC-write-back-20: credentials only at the site, sent only
where they belong; GI-CONN-07; FEAT-2026-11-10-write-back J-4; BET-2026-11-write-back WB-07, WB-20; verifier KC-3 N5).

The Platform names a source's key, its kind and, for HTTP/JSON, the address the credential is sent to. Whoever controls
the Platform's configuration could therefore publish an HTTP/JSON source keyed like the site's Jira source on the sync
component and receive the Jira credential at the next check. The Jira credential now lives in a file only the Jira
connector reads (knovas-jira.env), and as a second wall the sync component never uses a credential the site bound to a
host (``SYNC_CREDENTIAL_<KEY>_HOST``), nor a host line, nor another kind's line. GitHub sources keep working as before,
and their credential goes to the GitHub API only. The site, its addresses and the canary are made up.
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

import pytest

SYNC_SRC = Path(__file__).resolve().parents[2] / "sync_component" / "src"
sys.path.insert(0, str(SYNC_SRC))

from sync_component.agent import Agent  # noqa: E402
from sync_component.fetchers import (check, check_github, credential_for, fetch_github, fetch_http_json,  # noqa: E402
                                     head_github)
from sync_component.jobs import JobWorker  # noqa: E402

CANARY = "someone@example.invalid:CANARY-n5-0f3c9e1d7a2b"
KIND_CANARY = "someone@example.invalid:CANARY-n5-kind-5d1e"
JIRA_HOST = "muster-partner.example.invalid"
COLLECTOR = "https://collector.example.invalid/x"
GITHUB_TOKEN = "ghp_fake-github-token-for-tests"
#: A site environment that holds the Jira pair beside GitHub's lines: what the sync component got before the fix when
#: knovas-sources.env carried the Jira lines. With knovas-jira.env it no longer gets them; the second wall is tested so.
SITE = {"SYNC_CREDENTIAL_JIRA_PROJEKTE": CANARY, "SYNC_CREDENTIAL_JIRA_PROJEKTE_HOST": JIRA_HOST,
        "SYNC_CREDENTIAL_JIRA": KIND_CANARY, "SYNC_CREDENTIAL_JIRA_HOST": JIRA_HOST,
        "SYNC_CREDENTIAL_AKTEN_REPO": GITHUB_TOKEN}


class Recorder:
    """``requests.get`` standing in: every request it was asked to send, and a JSON (or commit) answer."""

    def __init__(self, text: str = "0" * 40):
        self.sent: list[dict] = []
        self.text = text

    def __call__(self, url, **kw):
        self.sent.append({"url": url, **kw})
        recorder = self

        class Answer:
            status_code = 200
            headers = {}
            text = recorder.text

            @staticmethod
            def iter_content(size):
                yield b'{"issues": []}'
        return Answer()

    def carried(self) -> str:
        return repr(self.sent)


class Platform:
    """The Platform's (or Link's) configuration as the attacker published it, and the checks the component reports."""

    def __init__(self, sources):
        self.sources = sources
        self.reported: list = []

    def config(self):
        return {"sources": self.sources, "mappings": []}

    def report_checks(self, items):
        self.reported.append(items)


def attacker_sources():
    """HTTP/JSON sources keyed like the Jira source, like the Jira kind's line and like that line's host line."""
    return [{"workspace": "w", "key": key, "kind": "http_json", "access": "read", "settings": {"url": COLLECTOR}}
            for key in ("jira-projekte", "jira", "JIRA PROJEKTE", "jira-projekte-host", "jira_host")]


class TestTheCanaryScenario:
    """The verifier's scenario: the Jira pair is in the sync component's environment and the Platform publishes
    HTTP/JSON sources keyed like it, pointing at an address it controls. Nothing carries the credential there."""

    @pytest.mark.parametrize("key", ["jira-projekte", "jira", "Jira Projekte", "jira-projekte-host", "jira_host"])
    def test_a_credential_bound_to_a_host_or_a_host_line_is_never_handed_out(self, key):
        assert credential_for(key, "http_json", environ=SITE) is None
        assert credential_for(key, "", environ=SITE) is None

    def test_the_kind_wide_http_json_line_does_not_replace_a_refused_one(self):
        env = {**SITE, "SYNC_CREDENTIAL_HTTP_JSON": "for-every-json-source"}
        assert credential_for("jira-projekte", "http_json", environ=env) is None
        assert credential_for("kunden-api", "http_json", environ=env) == "for-every-json-source", "1.x behaviour stays"

    def test_a_host_line_even_empty_binds_its_credential(self):
        env = {"SYNC_CREDENTIAL_AKTEN": "bound", "SYNC_CREDENTIAL_AKTEN_HOST": ""}
        assert credential_for("akten", "github", environ=env) is None

    def test_the_check_the_component_runs_by_itself_sends_nothing_to_the_platforms_address(self):
        get = Recorder()
        platform = Platform(attacker_sources())
        agent = Agent(platform, None, fetchers={"http_json": functools.partial(fetch_http_json, get=get)}, checks={},
                      environ=SITE)
        agent.check_sources()
        assert len(get.sent) == len(attacker_sources()) and all(r["url"] == COLLECTOR for r in get.sent)
        assert all("Authorization" not in r["headers"] for r in get.sent)
        assert "CANARY" not in get.carried() and JIRA_HOST not in get.carried()
        assert "CANARY" not in repr(platform.reported) and JIRA_HOST not in repr(platform.reported)

    def test_a_dry_runs_fetch_and_a_job_get_no_credential_either(self, tmp_path):
        get = Recorder()
        agent = Agent(Platform([]), None, environ=SITE)
        worker = JobWorker(Platform([]), None, environ=SITE, workers=1)
        try:
            for source in attacker_sources():
                assert agent._credential(source) is None and worker.credential(source) is None
                fetch_http_json(source["settings"], agent._credential(source), tmp_path, get=get)
        finally:
            worker.pool.shutdown(wait=False)
        assert get.sent and "CANARY" not in get.carried() and all("Authorization" not in r["headers"] for r in get.sent)


class TestGitHubStillWorks:
    def test_a_github_source_gets_its_own_line_and_the_kinds_line_as_before(self):
        assert credential_for("akten-repo", "github", environ=SITE) == GITHUB_TOKEN
        env = {"SYNC_CREDENTIAL_GITHUB": "for-every-repository"}
        assert credential_for("andere", "github", environ=env) == "for-every-repository"
        assert credential_for("github", "github", environ=env) == "for-every-repository"

    def test_its_check_head_and_read_send_the_token_to_the_github_api_only(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BRAIN_GITHUB_API", raising=False)
        get = Recorder()
        token = credential_for("akten-repo", "github", environ=SITE)
        settings = {"repository": "muster/akten", "ref": "main"}
        assert check("github", settings, token, checks={"github": functools.partial(check_github, get=get)}) == \
            ("source.ok", {})
        head_github(settings, token, get=get)
        reading = fetch_github(settings, token, tmp_path, get=get)
        assert reading.env == {"BRAIN_GITHUB_TOKEN": GITHUB_TOKEN}, "the runner reads with it, as before"
        assert len(get.sent) == 3
        for request in get.sent:
            assert request["url"].startswith("https://api.github.com/repos/muster/akten/")
            assert request["headers"]["Authorization"] == f"Bearer {GITHUB_TOKEN}"
            assert request["allow_redirects"] is False

    def test_an_http_json_source_keyed_like_a_kind_does_not_get_that_kinds_line(self):
        env = {"SYNC_CREDENTIAL_GITHUB": "for-every-repository"}
        assert credential_for("github", "http_json", environ=env) is None
        assert credential_for("GitHub", "http_json", environ=env) is None
        assert credential_for("http-json", "github", environ={"SYNC_CREDENTIAL_HTTP_JSON": "x"}) is None
