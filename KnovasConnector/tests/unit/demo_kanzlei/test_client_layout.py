"""The share as at a client, the firm's records and the truth files (``--layout client``).

Contract statements of L2-CASE-STUDY-KANZLEI checked here: PRE-01 (work folder
outside every synced folder), POST-01 (two builds identical), POST-02 (every
truth line refers to this build), POST-03 (addresses end in ``.example``).
"""
import csv
import hashlib
import json
import re
from pathlib import Path

import pytest

from demo_kanzlei import fillers
from demo_kanzlei.build_world import build_world, load_config
from demo_kanzlei.directory import build_acl, build_directory, members_of, readers
from demo_kanzlei.estate import bucket_name
from demo_kanzlei.matter_numbers import year_counts
from demo_kanzlei.pipeline import GateError, run_pipeline
from demo_kanzlei.records import build_records

CONFIG = Path(__file__).resolve().parents[3] / "scripts" / "demo_kanzlei" / "world.toml"
FIRM = "Quarzfels"
AUTODOC = f"{FIRM}/Praxis/Autodoc"


def _build(base: Path, name: str, layout: str = "client") -> tuple[Path, Path]:
    out, work = base / name, base / f"{name}-work"
    rc = run_pipeline(cfg_path=CONFIG, out_root=out, work_root=work, pilot=True, skip_zefix=True,
                      until_stage="ground_truth", layout=layout)
    assert rc == 0
    return out, work


def _hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out, work = _build(tmp_path_factory.mktemp("client"), "share")
    manifest = [json.loads(line) for line in (work / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    truth = work / "truth"
    return {
        "out": out,
        "work": work,
        "manifest": {row["path"]: row for row in manifest},
        "questions": [json.loads(line) for line in (truth / "questions.jsonl").read_text(encoding="utf-8").splitlines()],
        "rights": list(csv.DictReader((truth / "rights.csv").open(encoding="utf-8"))),
        "people": json.loads((truth / "people.json").read_text(encoding="utf-8")),
        "processes": json.loads((truth / "processes.json").read_text(encoding="utf-8")),
        "inventory": json.loads((truth / "inventory.json").read_text(encoding="utf-8")),
        "directory": json.loads((work / "site" / "directory.json").read_text(encoding="utf-8")),
        "acl": json.loads((work / "site" / "acl.json").read_text(encoding="utf-8")),
    }


@pytest.fixture(scope="module")
def records():
    cfg = load_config(CONFIG)
    world = build_world(cfg, pilot=True, skip_zefix=True)
    return world, build_records(world, cfg, pilot=True), cfg


# --- POST-01 -----------------------------------------------------------------

def test_two_client_builds_are_identical_file_for_file(site, tmp_path):
    out, work = _build(tmp_path, "again")
    assert _hashes(out) == _hashes(site["out"])
    for part in ("manifest.jsonl", "plan.jsonl", "world.json"):
        assert (work / part).read_bytes() == (site["work"] / part).read_bytes(), part
    assert _hashes(work / "site") == _hashes(site["work"] / "site")
    assert _hashes(work / "truth") == _hashes(site["work"] / "truth")
    first = {p.relative_to(out).as_posix(): int(p.stat().st_mtime) for p in out.rglob("*") if p.is_file()}
    second = {p.relative_to(site["out"]).as_posix(): int(p.stat().st_mtime) for p in site["out"].rglob("*") if p.is_file()}
    assert first == second, "file times come from the world, not from the clock"


def test_two_default_builds_are_identical_file_for_file(tmp_path):
    one, _ = _build(tmp_path, "one", layout="aktenplan")
    two, _ = _build(tmp_path, "two", layout="aktenplan")
    assert _hashes(one) == _hashes(two)


# --- PRE-01 ------------------------------------------------------------------

def test_nothing_on_the_share_says_who_the_clients_are(site):
    names = {Path(path).name for path in site["manifest"]}
    assert not names & {"manifest.jsonl", "world.json", "questions.jsonl", "rights.csv", "people.json", "plan.jsonl"}
    on_disk = {p.relative_to(site["out"]).as_posix() for p in site["out"].rglob("*") if p.is_file()}
    assert on_disk == set(site["manifest"]), "the share is exactly what the manifest lists"


def test_work_folder_inside_the_share_is_refused(tmp_path):
    with pytest.raises(GateError):
        run_pipeline(cfg_path=CONFIG, out_root=tmp_path / "share", work_root=tmp_path / "share" / "work", pilot=True,
                     skip_zefix=True, until_stage="world", layout="client")


def test_a_folder_the_generator_did_not_build_is_not_emptied(tmp_path):
    out = tmp_path / "share"
    out.mkdir()
    (out / "wichtig.txt").write_text("nicht löschen", encoding="utf-8")
    with pytest.raises(GateError):
        run_pipeline(cfg_path=CONFIG, out_root=out, work_root=tmp_path / "work", pilot=True, skip_zefix=True,
                     until_stage="file", layout="client")
    assert (out / "wichtig.txt").exists()


# --- the share ---------------------------------------------------------------

def test_store_is_numbered_folders_of_autodoc_names(site):
    stored = [row for path, row in site["manifest"].items() if path.startswith(AUTODOC + "/")]
    assert stored
    folders = {path.split("/")[3] for path in site["manifest"] if path.startswith(AUTODOC + "/")}
    assert all(re.fullmatch(r"\d{6}-\d{6}", folder) for folder in folders)
    assert "000000-000500" in folders
    for row in stored:
        if row["mess"] in {"broken_name", "lock"}:
            continue
        name = Path(row["path"]).name
        guid, akte, _typ = name.rsplit(".", 1)[0].split("_", 2)
        assert re.fullmatch(r"[0-9a-f-]{36}", guid)
        assert akte == row["matter"]


def test_bucket_names_follow_the_practice_software():
    assert bucket_name(1, 500) == "000000-000500"
    assert bucket_name(500, 500) == "000000-000500"
    assert bucket_name(501, 500) == "000501-001000"
    assert bucket_name(10234, 500) == "010001-010500"


def test_placeholders_are_formats_the_connector_skips(site):
    from sync.document_text import SYNCABLE_EXTENSIONS

    assert fillers.SYNCABLE == set(SYNCABLE_EXTENSIONS), "the truth files assume the Connector's own list"
    unread = [row for row in site["manifest"].values() if not row["syncable"]]
    assert len(unread) > len(site["manifest"]) / 2, "most of a client's share is not read"
    for ext in ("doc", "rtf", "wpd", "xls", "xlsx", "tif", "jpg", "zip", "dss", "htm", "pst", "bak"):
        assert f".{ext}" not in fillers.SYNCABLE
        assert fillers.make(ext, key="k", text="Aktenzeichen: 2020-001\nText") == fillers.make(ext, key="k", text="Aktenzeichen: 2020-001\nText")


def test_file_times_are_the_documents_dates(site):
    row = next(r for r in site["manifest"].values() if r["plan_id"] == "2024-017-e09" and r["mess"] is None)
    import datetime as dt

    stamp = dt.datetime.fromtimestamp((site["out"] / row["path"]).stat().st_mtime, dt.timezone.utc)
    assert stamp.date().isoformat() == row["date"] == "2024-04-12"


def test_addresses_are_not_real(site, records):
    _world, recs, _cfg = records
    assert all(user["mail"].endswith(".example") for user in site["directory"]["users"] if user["mail"])
    assert all(a.email.endswith(".example") for a in recs.addresses if a.email)
    assert all(person["email"].endswith(".example") for person in site["people"])


# --- the directory and folder permissions -------------------------------------

def test_directory_has_the_thirteen_people_leavers_and_service_accounts(site):
    users = site["directory"]["users"]
    people = [u for u in users if u["enabled"] and not u["service"]]
    assert len(people) == 13
    assert sum(1 for u in people if u["department"] == "Sekretariat") == 3
    assert any(not u["enabled"] for u in users), "people who left are still there, disabled"
    assert {"svc-knovas", "svc-praxis", "svc-scan", "svc-backup"} <= {u["sam"] for u in users if u["service"]}
    assert members_of(site["directory"], "GG_Alle") == {u["sam"] for u in people}, "through nested groups"


def test_knovas_account_reads_the_store_but_not_wages_homes_or_mail(site):
    def may(path: str) -> bool:
        return "svc-knovas" in readers(site["directory"], site["acl"], path)[0]

    assert may(f"{AUTODOC}/000000-000500/x.docx")
    assert may(f"{FIRM}/Gemeinsam/Personal/x.pdf")
    assert not may(f"{FIRM}/Gemeinsam/Buchhaltung/Lohn/x.pdf")
    assert not may(f"{FIRM}/Users/lzimmermann/Dokumente/x.docx")
    assert not may(f"{FIRM}/Mail/lzimmermann/x.pst")
    wages = next(r for p, r in site["manifest"].items() if "/Lohn/Lohnabrechnung" in p)
    assert wages["syncable"] and not wages["reachable"]


# --- the firm's records -------------------------------------------------------

def test_written_matter_is_in_the_records_and_its_billing_matches_its_documents(records):
    _world, recs, _cfg = records
    hero = recs.matter("2024-017")
    assert hero.in_world and hero.verantwortlich == "MB" and hero.eroeffnet == "2024-01-15"
    entries = [e for e in recs.time_entries if e.akte_nr == hero.nr]
    assert round(sum(e.betrag for e in entries), 2) == 21840.00, "the fee note in the file says CHF 21'840"
    invoice = next(i for i in recs.invoices if i.akte_nr == hero.nr and i.art == "R")
    assert invoice.honorar + invoice.auslagen == 21840.00 and invoice.datum == "2024-10-20"
    assert all(e.rechnung == invoice.nr for e in entries)
    assert any(r.recht == "X" and r.kuerzel == "NM" for r in recs.rights_for(hero.nr))


def test_register_is_dense_and_opening_days_rise_with_the_running_number(records):
    _world, recs, cfg = records
    refs = [m.aktenzeichen for m in recs.matters]
    assert len(refs) == len(set(refs)) == int(cfg["records"]["matters_pilot"])
    assert [m.nr for m in recs.matters] == list(range(1, len(refs) + 1))
    by_year: dict[str, list] = {}
    for matter in recs.matters:
        by_year.setdefault(matter.aktenzeichen[:4], []).append(matter)
    for matters in by_year.values():
        assert [int(m.aktenzeichen[5:]) for m in matters] == list(range(1, len(matters) + 1))
        assert [m.eroeffnet for m in matters] == sorted(m.eroeffnet for m in matters)
    people = {e.kuerzel: e for e in recs.employees}
    for entry in recs.time_entries:
        assert people[entry.kuerzel].at_work(entry.datum), "nobody books time before joining or after leaving"


def test_year_counts_add_up_and_cover_the_written_matters():
    cfg = load_config(CONFIG)
    counts = year_counts(cfg, ["2024-017", "2024-112", "2022-088"], pilot=False)
    assert sum(counts.values()) == int(cfg["records"]["matters_total"])
    assert counts[2024] >= 112 and counts[2022] >= 88
    assert counts[1998] < counts[2010] < counts[2025]


def test_document_index_matches_the_store_except_for_planted_gaps(site):
    sql = (site["work"] / "site" / "db" / "60_dokumente.sql").read_text(encoding="utf-8")
    known = set(re.findall(r"N'([0-9a-f-]{36}_[^']+)'", sql))
    stored = {Path(p).name for p, row in site["manifest"].items() if row["kind"] == "autodoc" and row["dok_nr"]}
    assert stored <= known
    gone = site["inventory"]["mess"]["row_without_file"] + site["inventory"]["mess"]["deleted_in_software"]
    assert sorted(known - stored) == sorted(gone)
    assert len(site["inventory"]["mess"]["row_without_file"]) == 1, "deleted in the Explorer: the software does not know"
    assert len(site["inventory"]["mess"]["deleted_in_software"]) == 1, "removed in the software: the row is flagged"
    index = [json.loads(line) for line in
             (site["work"] / "truth" / "records" / "DOKUMENTE.jsonl").read_text(encoding="utf-8").splitlines()]
    flagged = [row["Dateiname"] for row in index if row["Geloescht"]]
    assert flagged == site["inventory"]["mess"]["deleted_in_software"]
    assert all(row["GeloeschtAm"] for row in index if row["Geloescht"])


def test_sql_files_carry_no_credentials_and_load_in_order(site):
    files = sorted((site["work"] / "site" / "db").glob("*.sql"))
    assert files[0].name == "00_schema.sql"
    schema = files[0].read_text(encoding="utf-8")
    assert schema.startswith("IF DB_ID(N'KanzleiDB') IS NULL")
    for table in ("ADRESSEN", "AKTEN", "AKTENBETEILIGTE", "AKTENRECHTE", "DOKUMENTE", "LEISTUNGEN", "RECHNUNGEN", "FRISTEN", "MITARBEITER"):
        assert f"CREATE TABLE dbo.{table} (" in schema
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"password|kennwort|CREATE LOGIN", text, re.IGNORECASE), path.name
    akten = (site["work"] / "site" / "db" / "30_akten.sql").read_text(encoding="utf-8")
    assert "N'2024-017'" in akten and "Rüegg" in akten
    assert akten.count("\n(") + akten.count("VALUES\n(") >= 60


# --- POST-02: the truth files describe this build ------------------------------

def test_every_truth_line_refers_to_this_build(site):
    keys = {person["key"] for person in site["people"]}
    assert len(keys) == 13
    shown = {path for path, row in site["manifest"].items() if row["syncable"] and row["reachable"]}
    assert {row["pointer"] for row in site["rights"]} == shown, "one row per person for every file Knovas could show"
    assert {row["person"] for row in site["rights"]} == keys
    assert len(site["rights"]) == len(shown) * 13
    assert {row["may_read"] for row in site["rights"]} == {"true", "false"}
    for question in site["questions"]:
        assert question["kind"] in {"fact", "document", "process", "negative", "rights"}
        assert question["asked_as"] in keys | {""}
        for pointer in question["supporting_pointers"]:
            assert pointer.startswith("db:") or pointer in shown, pointer
        if question["kind"] == "negative":
            assert question["expected_points"] == [] and question["supporting_pointers"] == []
    assert [p["mandat"] for p in site["processes"]] == ["2024-017"]
    assert set(site["processes"][0]) == {"mandat", "process", "current_step", "done_steps", "missing"}


def test_expected_points_are_literally_in_the_supporting_text_files(site):
    checked = 0
    for question in site["questions"]:
        if question["kind"] != "fact":
            continue
        for pointer in question["supporting_pointers"]:
            if pointer.endswith(".txt"):
                text = (site["out"] / pointer).read_text(encoding="utf-8")
                assert all(point in text for point in question["expected_points"]), question["id"]
                checked += 1
    assert checked


def test_rights_follow_the_firms_rules(site):
    def rows(person: str, needle: str) -> list[dict]:
        return [r for r in site["rights"] if r["person"] == person and needle in r["pointer"]]

    assert all(r["may_read"] == "true" for r in site["rights"] if r["person"] == "mb"), "the managing partner reads everything"
    barrier = rows("nm", "_2024-017_")
    assert barrier and all(r["may_read"] == "false" and r["rule"] == "conflict:2024-017" for r in barrier)
    assert all(r["may_read"] == "true" for r in rows("lz", "_2024-017_"))
    fee = "_2024-017_Honorarnote"
    assert all(r["may_read"] == "false" and r["rule"] == "role:sekretariat-keine-finanzen" for r in rows("ms", fee))
    assert all(r["may_read"] == "true" for r in rows("rg", fee)), "the bookkeeper is in the secretariat and reads fee notes"
    assert all(r["may_read"] == "true" for r in rows("ms", "_2024-017_Vollmacht"))
    personnel = "/Gemeinsam/Personal/"
    assert rows("pw", personnel) and all(r["may_read"] == "true" and r["rule"] == "group:GG_Partner" for r in rows("pw", personnel))
    assert all(r["may_read"] == "false" for r in rows("sk", personnel))


def test_rights_questions_are_asked_by_someone_who_may_read_none_of_the_files(site):
    allowed = {(r["person"], r["pointer"]) for r in site["rights"] if r["may_read"] == "true"}
    asked = [q for q in site["questions"] if q["kind"] == "rights"]
    assert asked
    for question in asked:
        assert question["asked_as"]
        assert not any((question["asked_as"], pointer) in allowed for pointer in question["supporting_pointers"])


def test_people_file_carries_the_group_tree(site):
    by_key = {person["key"]: person for person in site["people"]}
    assert by_key["mb"]["groups"][:2] == ["kanzlei/partner", "kanzlei/team-prozess"]
    assert "kanzlei/buchhaltung" in by_key["rg"]["groups"]
    assert by_key["nm"]["role"] == "associate"
    assert sum(1 for p in site["people"] if p["role"] == "sekretariat") == 3
    assert sum(1 for p in site["people"] if p["role"] in {"partner", "associate"}) == 10


def test_inventory_counts_what_a_first_read_should_find(site):
    inv = site["inventory"]
    assert inv["files_total"] == len(site["manifest"])
    store = inv["sources"]["autodoc"]
    assert store["root"] == AUTODOC
    assert store["syncable_and_reachable"] == sum(
        1 for p, r in site["manifest"].items() if p.startswith(AUTODOC + "/") and r["syncable"] and r["reachable"])
    assert f"{FIRM}/Gemeinsam/Buchhaltung/Lohn" in inv["not_reachable_by_knovas_account"]
    assert set(inv["mess"]) >= {"duplicate", "empty", "latin1", "truncated", "lock", "row_without_file"}


def test_acl_and_directory_use_only_known_principals():
    cfg = load_config(CONFIG)
    world = build_world(cfg, pilot=True, skip_zefix=True)
    directory = build_directory(world, cfg)
    known = {u["sam"] for u in directory["users"]} | {g["name"] for g in directory["groups"]} | {"Domain Admins"}
    for entry in build_acl(world, cfg):
        assert {g["principal"] for g in entry["grants"]} <= known, entry["path"]
        assert {g["access"] for g in entry["grants"]} <= {"read", "modify", "full"}


# --- the database as a connector finds it --------------------------------------

@pytest.fixture(scope="module")
def database(records):
    from demo_kanzlei.sqlgen import tables

    world, recs, _cfg = records
    return {t.name: t for t in tables(recs, firm_name=world.firm.name, autodoc_unc="//SRV-DC01/shares$")}


def _column(table, name):
    index = table.columns.index(name)
    return [row[index] for row in table.rows]


def test_codes_point_to_rows_that_exist_though_no_foreign_key_says_so(database):
    assert "REFERENCES" not in "".join(t.ddl for t in database.values()), "relations are implied by names only"
    addresses = set(_column(database["ADRESSEN"], "AdrNr"))
    matters = set(_column(database["AKTEN"], "AkteNr"))
    people = set(_column(database["MITARBEITER"], "Kuerzel"))
    assert set(_column(database["AKTEN"], "MandantAdrNr")) <= addresses
    assert set(_column(database["AKTEN"], "RgID")) <= set(_column(database["RECHTSGEBIETE"], "RgID"))
    assert set(_column(database["AKTEN"], "VerantwAnwalt")) <= people
    assert set(_column(database["AKTENBETEILIGTE"], "AdrNr")) <= addresses
    assert set(_column(database["AKTENBETEILIGTE"], "RolleID")) <= set(_column(database["ROLLEN"], "RolleID"))
    assert set(_column(database["LEISTUNGEN"], "LeistArt")) <= set(_column(database["LEISTUNGSARTEN"], "LeistArt"))
    assert set(_column(database["FRISTEN"], "FristArtID")) - {None} <= set(_column(database["FRISTARTEN"], "FristArtID"))
    assert {n for n in _column(database["LEISTUNGEN"], "RechNr") if n} <= set(_column(database["RECHNUNGEN"], "RechNr"))
    for name in ("LEISTUNGEN", "RECHNUNGEN", "FRISTEN", "JOURNAL", "DOKUMENTE", "AKTENRECHTE"):
        assert set(_column(database[name], "AkteNr")) <= matters, name
    assert {n for n in _column(database["TERMINE"], "AkteNr") if n} <= matters
    assert None in _column(database["TERMINE"], "AkteNr"), "the firm's own appointments have no matter"


def test_database_has_the_traits_a_connector_must_cope_with(database):
    roles = {t.role for t in database.values()}
    assert roles == {"records", "lookup", "software", "leftover"}
    assert database["AKTEN_BACKUP_20190312"].key == [] and database["tmp_Import_Adressen"].key == []
    # three kinds of change marker
    assert database["AKTEN"].changed_by[0] == "TS" and "TS rowversion" in database["AKTEN"].ddl
    assert database["FRISTEN"].changed_by == ["MutiertAm", "ErfasstAm"] and "rowversion" not in database["FRISTEN"].ddl
    assert database["AKTENBETEILIGTE"].changed_by == []
    # three kinds of deletion
    assert database["DOKUMENTE"].deleted_by == "flag:Geloescht"
    assert database["LEISTUNGEN"].deleted_by == "removed"
    assert database["AKTEN"].deleted_by == "never"
    # old types and conventions
    assert set(_column(database["MITARBEITER"], "Aktiv")) == {-1, 0}
    assert "Bemerkung ntext" in database["ADRESSEN"].ddl
    late = [1 for day, entered in zip(_column(database["LEISTUNGEN"], "Datum"), _column(database["LEISTUNGEN"], "ErfasstAm"))
            if entered[:10] > day[:10]]
    assert late, "time is often entered days after its date"
    assert all(entered[:10] >= day[:10] for day, entered in
               zip(_column(database["LEISTUNGEN"], "Datum"), _column(database["LEISTUNGEN"], "ErfasstAm")))


def test_counters_are_one_past_the_highest_number(database):
    counters = dict(database["SYS_NUMMERN"].rows)
    assert counters["AKTE"] == max(_column(database["AKTEN"], "AkteNr")) + 1
    assert counters["ADRESSE"] == max(_column(database["ADRESSEN"], "AdrNr")) + 1
    assert counters["DOKUMENT"] >= 1 and counters["LEISTUNG"] == max(_column(database["LEISTUNGEN"], "LeistNr")) + 1


def test_court_number_of_the_written_matter_is_a_field_the_firm_defined(database, records):
    _world, recs, _cfg = records
    hero = recs.matter("2024-017").nr
    assert (1, hero, "CG240017") in database["ZUSATZFELDWERTE"].rows
    assert (1, "AKTEN", "Geschäfts-Nr. Gericht", "T") in database["ZUSATZFELDER"].rows


def test_every_table_has_its_truth_and_the_catalogue_says_how_to_read_it(site):
    records_dir = site["work"] / "truth" / "records"
    sources = json.loads((site["work"] / "truth" / "sources.json").read_text(encoding="utf-8"))
    assert set(sources) >= {"database", "share", "directory", "mail"}
    listed = {t["table"]: t for t in sources["database"]["tables"]}
    schema = (site["work"] / "site" / "db" / "00_schema.sql").read_text(encoding="utf-8")
    assert set(listed) == set(re.findall(r"CREATE TABLE dbo\.(\w+) \(", schema))
    for name, entry in listed.items():
        lines = (records_dir / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == entry["rows"] == site["inventory"]["database"][name], name
        if lines:
            assert list(json.loads(lines[0])) == [c for c in entry["columns"] if c != "TS"]
        assert entry["role"] in {"records", "lookup", "software", "leftover"}
    assert "password" not in json.dumps(sources).lower().replace("passwords are in the site's .env", "")
