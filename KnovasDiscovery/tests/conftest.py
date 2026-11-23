"""Fakes for the thin access layers (LDAP, SQL, HTTP) and builders for test data."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
NOW = dt.datetime(2026, 11, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
BASE = "DC=ad,DC=example,DC=test"


# --------------------------------------------------------------------------- LDAP
def sid(rid: int) -> bytes:
    return bytes([1, 5, 0, 0, 0, 0, 0, 5, 21, 0, 0, 0]) + (1).to_bytes(4, "little") * 3 + rid.to_bytes(4, "little")


def user(cn: str, sam: str, ou: str = "OU=Staff,OU=Firm", rid: int = 1200, disabled: bool = False) -> dict:
    return {"dn": f"CN={cn},{ou},{BASE}", "samaccountname": [sam], "useraccountcontrol": [str(514 if disabled else 512)],
            "objectsid": [sid(rid)]}


def group(cn: str, members: list[str], ou: str = "OU=Groups,OU=Firm", rid: int = 1300) -> dict:
    return {"dn": f"CN={cn},{ou},{BASE}", "samaccountname": [cn], "cn": [cn], "member": members,
            "objectsid": [sid(rid)]}


def directory_entries(marker: str = "") -> dict:
    """A small firm: 3 people enabled, 1 disabled, 2 service accounts, built-ins; 3 nested groups, 1 empty."""
    m = marker
    people = [user(f"Anna {m}Muster", f"amuster{m}", rid=1201), user(f"Beat {m}Beispiel", "bbeispiel", rid=1202),
              user("Carla Test", "ctest", rid=1203), user("Dora Alt", "dalt", ou="OU=Former,OU=Firm", rid=1204,
                                                          disabled=True)]
    services = [user("svc-backup", "svc-backup", ou="OU=Dienstkonten,OU=Firm", rid=1210),
                user("scanner", "scanner", ou="OU=Service Accounts,OU=Firm", rid=1211)]
    builtins = [user("Administrator", "Administrator", ou="CN=Users", rid=500),
                user("Guest", "Guest", ou="CN=Users", rid=501, disabled=True),
                user("krbtgt", "krbtgt", ou="CN=Users", rid=502, disabled=True)]
    partners = group(f"GG_{m}Partner", [people[0]["dn"]], rid=1301)
    lawyers = group("GG_Lawyers", [people[1]["dn"], partners["dn"]], rid=1302)
    everyone = group("GG_All", [lawyers["dn"], people[2]["dn"]], rid=1303)
    empty = group("GG_Empty", [], rid=1304)
    defaults = [group("Domain Admins", [builtins[0]["dn"]], ou="CN=Users", rid=512),
                group("Domain Users", [], ou="CN=Users", rid=513),
                group("DnsAdmins", [], ou="CN=Users", rid=1101),
                group("Administrators", [builtins[0]["dn"]], ou="CN=Builtin", rid=544)]
    return {
        "root": {"dn": "", "defaultnamingcontext": [BASE], "supportedcapabilities": ["1.2.840.113556.1.4.800"]},
        "users": people + services + builtins,
        "groups": [partners, lawyers, everyone, empty] + defaults,
        "computers": [{"dn": f"CN=PC01,OU=Computers,OU=Firm,{BASE}"}, {"dn": f"CN=SRV01,OU=Domain Controllers,{BASE}"}],
        "ous": [{"dn": f"OU=Firm,{BASE}"}, {"dn": f"OU=Staff,OU=Firm,{BASE}"}, {"dn": f"OU=Groups,OU=Firm,{BASE}"}],
    }


class FakeLdap:
    """Stands for Ldap3Reader; the factory call records what it was given."""

    def __init__(self, entries: dict, fail: Exception | None = None):
        self.entries = entries
        self.fail = fail
        self.calls: list = []

    def __call__(self, url, user, password, verify_tls=True, ca_file=None):
        self.calls.append((url, user, verify_tls))
        if self.fail:
            raise self.fail
        return self

    def root_dse(self):
        return self.entries["root"]

    def search(self, base, flt, attrs):
        from knovas_discovery.probes import directory as d

        return {d.USER_FILTER: self.entries["users"], d.GROUP_FILTER: self.entries["groups"],
                d.COMPUTER_FILTER: self.entries["computers"], d.OU_FILTER: self.entries["ous"]}[flt]

    def close(self):
        pass


# --------------------------------------------------------------------------- SQL
def col(name: str, typ: str = "int", max_length: int = 4) -> dict:
    return {"name": name, "type": typ, "max_length": max_length}


def tbl(name: str, columns: list[dict], key: list[str], rows: int = 100, fks: int = 0) -> dict:
    return {"name": name, "rows": rows, "columns": columns, "key": key, "foreign_keys": fks}


def practice_catalog() -> dict:
    """A practice-software schema of the usual kind (general names, not the demo's)."""
    dt_ = lambda n: col(n, "datetime", 8)  # noqa: E731
    return {
        "tables": [
            tbl("SYS_CONFIG", [col("Name", "varchar", 50), col("Value", "nvarchar", 800)], ["Name"], 12),
            tbl("NUMBERS", [col("Kind", "varchar", 20), col("Next")], ["Kind"], 5),
            tbl("CLIENTS", [col("ClientNo"), col("Name", "nvarchar", 200), col("Inactive", "bit", 1),
                            dt_("ModifiedAt"), col("TS", "timestamp", 8)], ["ClientNo"], 900),
            tbl("MATTERS", [col("MatterNo"), col("ClientNo"), col("Title", "nvarchar", 400), dt_("OpenedOn"),
                            dt_("CreatedAt"), col("Status", "char", 1)], ["MatterNo"], 1200),
            tbl("MATTER_PARTIES", [col("MatterNo"), col("ClientNo"), col("RoleId")], ["MatterNo", "ClientNo", "RoleId"],
                3000),
            tbl("ROLES", [col("RoleId"), col("Label", "nvarchar", 80)], ["RoleId"], 6),
            tbl("MATTER_PERMISSIONS", [col("MatterNo"), col("UserCode", "varchar", 5), col("Right", "char", 1)],
                ["MatterNo", "UserCode"], 40),
            tbl("USERS", [col("UserCode", "varchar", 5), col("Name", "nvarchar", 120), col("Active", "smallint", 2),
                          dt_("Joined"), col("Rate", "money", 8), col("Login", "varchar", 50)], ["UserCode"], 20),
            tbl("TIME_ENTRIES", [col("EntryNo"), col("MatterNo"), dt_("Day"), col("Hours", "decimal", 5),
                                 dt_("EnteredAt")], ["EntryNo"], 50000),
            tbl("CUSTOM_FIELD_VALUES", [col("FieldId"), col("RecordKey"), col("Value", "nvarchar", 500)],
                ["FieldId", "RecordKey"], 800),
            tbl("CUSTOMERS_OLD", [col("No"), col("Name", "nvarchar", 100)], ["No"], 30),
            tbl("MATTERS_20190101", [col("MatterNo"), col("Title", "nvarchar", 400)], [], 700),
            tbl("NOTES", [col("NoteNo"), col("MatterNo"), dt_("At"), col("Text", "nvarchar", -1), col("Author", "varchar", 5),
                          col("Kind", "char", 1)], ["NoteNo"], 3000),
        ],
        "views": 2,
        "procedures": 0,
    }


class FakeSql:
    def __init__(self, databases: dict[str, dict], server: dict | None = None, fail: Exception | None = None):
        self.dbs = databases
        self.srv = server or {"version": "16.0.4215.2", "edition": "Developer Edition (64-bit)", "integrated_only": 0}
        self.fail = fail

    def __call__(self, host, port, user, password, verify_tls=True, ca_file=None):
        if self.fail:
            raise self.fail
        return self

    def server(self):
        return self.srv

    def databases(self):
        return sorted(self.dbs)

    def catalog(self, database):
        return self.dbs[database]

    def close(self):
        pass


# --------------------------------------------------------------------------- HTTP (Microsoft Graph)
class FakeResponse:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.headers = {}

    def json(self):
        return self._body


class FakeSession:
    """Answers from the recorded Graph examples in fixtures/m365_graph.json, by path and query."""

    def __init__(self, routes: dict | None = None, token_status: int = 200):
        self.routes = routes if routes is not None else json.loads((FIXTURES / "m365_graph.json").read_text("utf-8"))
        self.token_status = token_status
        self.requests: list[str] = []

    def post(self, url, data=None, timeout=None):
        self.requests.append("POST " + url)
        if self.token_status != 200:
            return FakeResponse(self.token_status, {"error": "invalid_client", "error_description": "AADSTS7000215"})
        return FakeResponse(200, self.routes["token"])

    def get(self, url, headers=None, timeout=None):
        self.requests.append("GET " + url)
        assert headers and headers["Authorization"].startswith("Bearer ")
        path = url.replace("https://graph.microsoft.com/v1.0", "")
        for prefix, answer in self.routes["get"].items():
            if path.startswith(prefix):
                return FakeResponse(answer["status"], answer["body"])
        return FakeResponse(404, {"error": {"code": "itemNotFound"}})


# --------------------------------------------------------------------------- refused folders
def make_refused(path: Path) -> bool:
    """Make a folder unlistable for the current user, where the OS allows it. True if it worked."""
    if os.name == "nt":
        who = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
        r = subprocess.run(["icacls", str(path), "/deny", f"{who}:(OI)(CI)(RD)"], capture_output=True)
        if r.returncode != 0:
            return False
    else:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return False
        os.chmod(path, 0)
    try:
        os.listdir(path)
    except PermissionError:
        return True
    undo_refused(path)
    return False


def undo_refused(path: Path) -> None:
    if os.name == "nt":
        who = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
        subprocess.run(["icacls", str(path), "/remove:d", who], capture_output=True)
    else:
        os.chmod(path, 0o755)


@pytest.fixture
def refused_dir(tmp_path):
    made = []

    def make(path: Path) -> bool:
        ok = make_refused(path)
        if ok:
            made.append(path)
        return ok

    yield make
    for p in made:
        undo_refused(p)


def set_age(path: Path, days: float) -> None:
    ts = NOW.timestamp() - days * 86400 - 60  # a minute more, so float rounding cannot make it a day younger
    os.utime(path, (ts, ts))
