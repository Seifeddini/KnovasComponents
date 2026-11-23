import json

import pytest

from knovas_discovery import intake as intake_mod
from knovas_discovery.cli import main


def test_init_writes_a_template_that_loads(tmp_path, capsys):
    assert main(["init", str(tmp_path / "c")]) == 0
    data = intake_mod.load(tmp_path / "c" / "intake.yaml")
    assert data["client"]["key"] == "example"
    assert data["targets"]["m365"] == []


def test_init_refuses_to_overwrite(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 0
    assert main(["init", str(tmp_path)]) == 2
    assert "exists" in capsys.readouterr().err
    assert main(["init", str(tmp_path), "--force"]) == 0


def test_catalogue(capsys):
    assert main(["catalogue", "--check"]) == 0
    assert "no problem" in capsys.readouterr().out
    assert main(["catalogue"]) == 0
    out = capsys.readouterr().out
    assert "| practice-database | Database of a legal practice software (product not recognised) | C | none |" in out


def test_scan_and_report_from_the_command_line(tmp_path, capsys):
    share = tmp_path / "share" / "Docs"
    share.mkdir(parents=True)
    (share / "a.pdf").write_bytes(b"x")
    intake = tmp_path / "intake.yaml"
    intake.write_text(
        "client: {key: t, sector: law-firm, people: 2, lawyers: 1}\n"
        "software: []\n"
        f"targets:\n  fileshares:\n    - {{key: s, path: '{(tmp_path / 'share').as_posix()}', roots: [Docs]}}\n"
        "  directories:\n    - {key: ad, url: 'ldaps://127.0.0.1:1', bind_user: u, password_env: KD_TEST_UNSET_PW}\n"
        "knovas: {api_host: 127.0.0.1, api_port: 9}\n", encoding="utf-8")
    out = tmp_path / "out"
    assert main(["scan", "--intake", str(intake), "--out", str(out), "--only", "fileshare,directory"]) == 0
    findings = json.loads((out / "findings.json").read_text("utf-8"))
    assert findings["probes"]["environment"]["status"] == "skipped"
    assert findings["probes"]["fileshares"][0]["facts"]["files"] == 1
    assert findings["probes"]["directories"][0]["error"] == "password_missing:KD_TEST_UNSET_PW"
    assert main(["report", "--out", str(out)]) == 0
    plan = (out / "connection-plan.md").read_text("utf-8")
    assert "## Phase 1: standard (class A)" in plan and "`s:Docs`" in plan
    assert "| directory | `ad` | failed | password_missing:KD_TEST_UNSET_PW |" in plan
    assert main(["scan", "--intake", str(intake), "--out", str(out), "--only", "nonsense"]) == 2
