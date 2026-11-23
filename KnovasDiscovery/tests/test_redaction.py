"""Rule 2: findings.json and the reports hold no name of a person, client, file, folder or group; detail.json does.
Rule 3: no secret in any output, on the console, or in an error."""

import pytest

from knovas_discovery import intake as intake_mod
from knovas_discovery.cli import build_parser
from knovas_discovery.report import make_report
from knovas_discovery.scan import run_scan
from knovas_discovery.secrets import Secrets

from conftest import NOW, FakeLdap, FakeSession, FakeSql, directory_entries, practice_catalog

MARKERS = {"person": "ZZPERSONMARK", "client": "ZZCLIENTMARK", "folder": "ZZFOLDERMARK", "file": "ZZFILEMARK",
           "group": "ZZPERSONMARK", "refused": "ZZREFUSEDMARK", "site": "SITEMARKER"}
SECRET = "Pw-ZZSECRET-42!"
GUID = "7b9859a2-70f9-5199-ab0b-906686e0be31"


def make_intake(share_path):
    return intake_mod.normalise({
        "client": {"key": "c", "name": f"{MARKERS['client']} AG", "sector": "law-firm", "people": 5, "lawyers": 3},
        "software": [{"category": "practice", "product": "unknown"}],
        "targets": {
            "fileshares": [{"key": "share", "path": str(share_path), "roots": ["Firm/Common", "Firm/Store"]}],
            "directories": [{"key": "ad", "url": "ldaps://dc", "bind_user": "svc@x", "password_env": "PW_LDAP",
                             "base_dn": "DC=ad,DC=example,DC=test", "verify_tls": False}],
            "databases": [{"key": "praxis", "kind": "mssql", "host": "sql", "port": 1433, "user": "ro",
                           "password_env": "PW_SQL", "verify_tls": False}],
            "m365": [{"key": "tenant", "tenant_id": "t", "client_id": "c", "secret_env": "M365"}],
        },
        "knovas": {"api_host": "127.0.0.1", "api_port": 9},
    })


@pytest.fixture
def site(tmp_path, refused_dir):
    share = tmp_path / "share"
    common = share / "Firm" / "Common"
    (common / f"{MARKERS['folder']} Mandant").mkdir(parents=True)
    (common / f"{MARKERS['folder']} Mandant" / f"{MARKERS['file']} Vertrag.docx").write_bytes(b"x" * 10)
    (common / "Scan_20240101.pdf").write_bytes(b"x")
    store = share / "Firm" / "Store" / "000001-000500"
    store.mkdir(parents=True)
    for i in range(3):
        (store / f"{GUID}_{MARKERS['client']}-{i}_Brief.docx").write_bytes(b"x" * 5)
    locked = common / f"{MARKERS['refused']} Lohn"
    locked.mkdir()
    (locked / f"{MARKERS['file']} Lohn.pdf").write_bytes(b"x")
    refused = refused_dir(locked)
    return share, refused


def scan_all(share, out, secrets, ldap=None, sql=None, session=None):
    overrides = {"ldap_factory": ldap or FakeLdap(directory_entries(MARKERS["person"])),
                 "sql_factory": sql or FakeSql({"Praxis": practice_catalog()}),
                 "http_session": session or FakeSession()}
    return run_scan(make_intake(share), out, secrets=secrets, now=NOW, ctx_overrides=overrides)


def outputs(out):
    return {name: (out / name).read_text("utf-8") for name in
            ("findings.json", "report.json", "report.md", "connection-plan.md")}


def test_no_names_leave_the_site(site, tmp_path):
    share, refused = site
    out = tmp_path / "out"
    secrets = Secrets(environ={"PW_LDAP": "a", "PW_SQL": "b", "M365": "c"}, interactive=False)
    findings, detail = scan_all(share, out, secrets)
    make_report(out)
    for name, text in outputs(out).items():
        for kind, marker in MARKERS.items():
            assert marker not in text, f"{kind} name in {name}"
    detail_text = (out / "detail.json").read_text("utf-8")
    # The markers are really there to be found: the detail (which stays at the client) has them.
    for kind in ("person", "folder", "file", "site"):
        assert MARKERS[kind] in detail_text, kind
    if refused:
        assert MARKERS["refused"] in detail_text
        assert findings["probes"]["fileshares"][0]["facts"]["refused_folders"] == 1
    # The client's key is shared on purpose; its name is not.
    assert findings["client"] == {"key": "c", "sector": "law-firm", "people": 5, "lawyers": 3, "it_provider": None}


def test_no_secret_anywhere(site, tmp_path, capsys):
    share, _ = site
    out = tmp_path / "out"

    class Leaky(Exception):
        pass

    secrets = Secrets(environ={"PW_LDAP": SECRET, "PW_SQL": SECRET, "M365": SECRET}, interactive=False)
    # Drivers that put the password into their error text: the scrubber must catch it.
    ldap = FakeLdap({}, fail=Leaky(f"bind failed for svc@x with password {SECRET}"))
    sql = FakeSql({}, fail=Leaky(f"login failed ({SECRET})"))
    findings, _ = scan_all(share, out, secrets, ldap=ldap, sql=sql, session=FakeSession(token_status=401))
    make_report(out)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    for path in out.iterdir():
        assert SECRET not in path.read_text("utf-8"), path.name
    assert "***" in (out / "detail.json").read_text("utf-8")
    assert {r["error"] for r in findings["probes"]["directories"] + findings["probes"]["databases"]} == {"error:Leaky"}


def test_intake_refuses_a_password_and_the_cli_has_no_password_option():
    with pytest.raises(intake_mod.IntakeError):
        intake_mod.normalise({"client": {"key": "c"},
                              "targets": {"databases": [{"key": "db", "password": "x"}]}})
    options = [a for action in build_parser()._subparsers._group_actions[0].choices.values()
               for act in action._actions for a in act.option_strings]
    assert not [o for o in options if "pass" in o or "secret" in o or "token" in o]


def test_prompt_only_on_a_terminal():
    asked = []
    s = Secrets(environ={}, prompt=lambda text: asked.append(text) or "typed", interactive=True)
    assert s.get("PW_X", "directory ad") == "typed" and "PW_X" in asked[0]
    assert s.scrub("x typed y") == "x *** y"
    assert Secrets(environ={}, interactive=False).get("PW_X", "directory ad") is None
