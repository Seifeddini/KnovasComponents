"""J-4: ``knovas-connector-jira dump-stream --source <key> --out <file.jsonl | -> [--settings <file.json>]``, the
interface a Brain mapping run builds on: one complete read of the source as JSON Lines (records, then the complete
generation and the state), every line a valid stream message, the same bytes for the same project, the settings from a
file or from Knovas Link, the credential from the site's environment only; a cut listing, a missing credential or a
failed call writes nothing and exits with its code. The site, the project and the issues are made up."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fake_jira import HOST, PROJECT, SITE, FakeJira
from fake_link import TOKEN, URL, FakeLink
from knovas_connector_jira import dump
from knovas_connector_jira.__main__ import main as cli
from knovas_connector_jira.site import LinkClient
from knovas_connector_kit import contracts

PACKAGE = Path(__file__).resolve().parents[1]
KEY = "jira-main"
SECRET = "someone@example.invalid:site-token-0123456789"
ENV = {"SYNC_CREDENTIAL_JIRA_MAIN": SECRET, "SYNC_CREDENTIAL_JIRA_MAIN_HOST": HOST}
SETTINGS = {"site": SITE, "project": PROJECT, "fields": ["summary", "status", "assignee", "duedate"], "page_size": 2}


@pytest.fixture
def jira():
    j = FakeJira(SECRET)
    j.add(1, summary="Write the onboarding guide", assignee="acct-1")
    j.add(2, summary="Review the release notes", links=[f"{PROJECT}-1"])
    j.add(3, summary="Plan the next review", duedate="2026-11-20")
    return j


def settings_file(tmp_path, settings=SETTINGS):
    path = tmp_path / "jira-settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    return str(path)


def run(jira, tmp_path, *, out=None, settings=SETTINGS, env=ENV, link=None):
    out = out or str(tmp_path / "jira.jsonl")
    stdout, stderr = io.StringIO(), io.StringIO()
    code = dump.main(KEY, out, settings_file(tmp_path, settings) if settings is not None else None, environ=dict(env),
                     session=jira, link=link, stdout=stdout, stderr=stderr, sleep=lambda seconds: None)
    return code, out, stdout.getvalue(), stderr.getvalue()


def lines(text):
    return [json.loads(line) for line in text.splitlines()]


def test_the_dump_is_the_complete_stream_as_json_lines(jira, tmp_path):
    code, out, stdout, stderr = run(jira, tmp_path)
    assert code == 0 and stdout == ""
    messages = lines(Path(out).read_text(encoding="utf-8"))
    assert [m["type"] for m in messages] == ["record", "record", "record", "generation", "state"]
    assert all(contracts.valid("stream-message.schema.json", m) and m["source"] == KEY for m in messages)
    assert [m["external_id"] for m in messages[:3]] == [f"{PROJECT}-1", f"{PROJECT}-2", f"{PROJECT}-3"]
    assert messages[0]["payload"]["fields"]["assignee"] == "acct-1"
    assert messages[3]["payload"]["complete"] is True and messages[3]["payload"]["counts"] == {"record": 3}
    assert (os.stat(out).st_mode & 0o777) == 0o600, "issue data: readable by its owner only"
    assert stderr.strip() == f"dump-stream: 3 records, generation {messages[3]['payload']['id']}, 5 messages"


def test_the_same_project_gives_the_same_bytes(jira, tmp_path):
    first = Path(run(jira, tmp_path, out=str(tmp_path / "a.jsonl"))[1]).read_bytes()
    second = Path(run(jira, tmp_path, out=str(tmp_path / "b.jsonl"))[1]).read_bytes()
    assert first == second and first.endswith(b"\n")


def test_dash_writes_the_stream_alone_to_standard_output(jira, tmp_path):
    code, _out, stdout, stderr = run(jira, tmp_path, out="-")
    assert code == 0 and [m["type"] for m in lines(stdout)][-2:] == ["generation", "state"]
    assert "dump-stream:" in stderr and "dump-stream" not in stdout


def test_without_settings_the_source_comes_from_knovas_link(jira, tmp_path):
    link = FakeLink([{"workspace": "ws-1", "key": KEY, "kind": "jira", "access": "read", "component": "jira-connector",
                      "settings": dict(SETTINGS)}])
    code, out, _o, _e = run(jira, tmp_path, settings=None, link=LinkClient(URL, "jira-connector", TOKEN, session=link))
    assert code == 0 and link.config_reads == 1
    assert sum(1 for m in lines(Path(out).read_text(encoding="utf-8")) if m["type"] == "record") == 3


@pytest.mark.parametrize("sources, says", [
    ([], "dump.unknown_source"),
    ([{"workspace": "ws-1", "key": KEY, "kind": "github", "settings": {}}], "dump.not_a_jira_source"),
])
def test_a_source_link_does_not_hold_as_jira_is_refused(jira, tmp_path, sources, says):
    link = LinkClient(URL, "jira-connector", TOKEN, session=FakeLink(sources))
    code, out, _o, stderr = run(jira, tmp_path, settings=None, link=link)
    assert (code, stderr.strip()) == (2, f"dump-stream: {says}") and not Path(out).exists() and jira.calls == []


def test_without_settings_and_without_link_it_says_so(jira, tmp_path):
    code, _out, _o, stderr = run(jira, tmp_path, settings=None)
    assert (code, stderr.strip()) == (2, "dump-stream: dump.no_link")


def test_a_cut_listing_is_not_complete_so_nothing_is_written(jira, tmp_path):
    jira.cut_listing = True
    target = tmp_path / "jira.jsonl"
    target.write_text("the earlier dump\n", encoding="utf-8")
    code, out, _o, stderr = run(jira, tmp_path, out=str(target))
    assert (code, stderr.strip()) == (4, "dump-stream: dump.listing_cut")
    assert target.read_text(encoding="utf-8") == "the earlier dump\n", "an earlier dump stays as it was"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["jira-settings.json", "jira.jsonl"], "no temporary file left"


@pytest.mark.parametrize("env, settings, code, says", [
    ({}, SETTINGS, 2, "jira.no_credential"),
    ({"SYNC_CREDENTIAL_JIRA_MAIN": SECRET}, SETTINGS, 2, "jira.no_credential"),
    (ENV, dict(SETTINGS, site="https://elsewhere.example.invalid"), 2, "jira.bad_settings"),
    (ENV, dict(SETTINGS, project="not a key"), 2, "jira.bad_settings"),
    (dict(ENV, SYNC_CREDENTIAL_JIRA_MAIN="someone@example.invalid:wrong"), SETTINGS, 3, "jira.unauthorized"),
])
def test_what_cannot_be_read_writes_nothing_and_exits_with_its_code(jira, tmp_path, env, settings, code, says):
    got, out, stdout, stderr = run(jira, tmp_path, settings=settings, env=env)
    assert (got, stderr.strip()) == (code, f"dump-stream: {says}") and not Path(out).exists() and stdout == ""
    assert SECRET not in stderr and "wrong" not in stderr


@pytest.mark.parametrize("content", ['["site"]', '{"site": "https://x.example.invalid", "token": "abc"}', "not json"])
def test_a_settings_file_holds_a_jira_sources_settings_only(jira, tmp_path, content):
    path = tmp_path / "jira-settings.json"
    path.write_text(content, encoding="utf-8")
    stderr = io.StringIO()
    code = dump.main(KEY, str(tmp_path / "x.jsonl"), str(path), environ=dict(ENV), session=jira, stderr=stderr)
    assert (code, stderr.getvalue().strip()) == (2, "dump-stream: dump.bad_settings_file") and jira.calls == []


def test_the_credential_and_the_issues_stay_off_standard_error(jira, tmp_path, capfd):
    code, _out, _o, stderr = run(jira, tmp_path)
    assert code == 0
    for text in (stderr, capfd.readouterr().err):
        assert SECRET not in text and HOST not in text and f"{PROJECT}-1" not in text and "onboarding" not in text


def test_the_command_line_names_the_interface():
    with pytest.raises(SystemExit) as missing:
        cli(["dump-stream", "--source", KEY])
    assert missing.value.code == 2
    usage = subprocess.run([sys.executable, "-m", "knovas_connector_jira", "dump-stream", "--help"],
                           capture_output=True, text=True, env=image_env(), cwd=PACKAGE).stdout
    for flag in ("--source", "--out", "--settings"):
        assert flag in usage


def image_env() -> dict:
    """The import path the image sets (Dockerfile: PYTHONPATH=/app/src:/app/vendor): the vendored kit, not the
    repository's."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SYNC_") and k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join([str(PACKAGE / "src"), str(PACKAGE / "vendor")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_in_the_image_layout_the_dump_refuses_without_a_credential_before_any_request(tmp_path):
    out = tmp_path / "jira.jsonl"
    done = subprocess.run([sys.executable, "-m", "knovas_connector_jira", "dump-stream", "--source", KEY, "--out",
                           str(out), "--settings", settings_file(tmp_path)],
                          capture_output=True, text=True, env=image_env(), cwd=tmp_path, timeout=60)
    assert (done.returncode, done.stdout) == (2, "") and done.stderr.strip().endswith("dump-stream: jira.no_credential")
    assert not out.exists()
