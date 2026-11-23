from knovas_discovery import heuristics as h
from knovas_discovery.probes.mssql import database_facts, match_product, server_facts

from conftest import col, practice_catalog, tbl


def test_changed_at_columns_need_a_date_type_and_a_name():
    cols = [col("MutiertAm", "datetime", 8), col("ErfasstAm", "datetime2", 8), col("ModifiedOn", "date", 3),
            col("LastUpdated", "smalldatetime", 4), col("CreatedBy", "varchar", 10), col("Datum", "datetime", 8),
            col("geändertAm", "datetimeoffset", 10), col("angelegt", "datetime", 8)]
    assert h.changed_at_columns(cols) == ["MutiertAm", "ErfasstAm", "ModifiedOn", "LastUpdated", "geändertAm",
                                          "angelegt"]


def test_rowversion():
    assert h.has_rowversion([col("TS", "timestamp", 8)])
    assert h.has_rowversion([col("RowVer", "rowversion", 8)])
    assert not h.has_rowversion([col("TS", "binary", 8)])


def test_delete_flags_need_a_yes_no_type():
    cols = [col("Geloescht", "bit", 1), col("Aktiv", "smallint", 2), col("Inaktiv", "bit", 1),
            col("IsDeleted", "tinyint", 1), col("Storno", "char", 1), col("Archiviert", "nchar", 2),
            col("GeloeschtAm", "datetime", 8), col("Aktivitaet", "nvarchar", 100), col("Active", "char", 10)]
    assert h.delete_flag_columns(cols) == ["Geloescht", "Aktiv", "Inaktiv", "IsDeleted", "Storno", "Archiviert"]


def test_leftover_and_software_names():
    for name in ("AKTEN_BACKUP_20190312", "tmp_Import_Adressen", "Adressen_alt", "KUNDEN_OLD", "Kopie von Akten",
                 "TEST_DATEN", "AKTEN20190312", "temp_load"):
        assert h.is_leftover_name(name), name
    for name in ("TESTAMENTE", "TEMPLATES", "AKTEN", "LEISTUNGEN2"):
        assert not h.is_leftover_name(name), name
    for name in ("SYS_PARAMETER", "sys_users", "CFG_X", "LOG_EVENTS", "AUDIT_TRAIL", "SETTINGS", "PARAMS", "LOCKS",
                 "AKTEN_SPERREN", "NUMMERNKREISE", "SYS_BENUTZER", "WEB_SESSIONS", "VERSION", "DBVERSION"):
        assert h.is_software_name(name), name
    for name in ("DOKUMENTVERSIONEN", "AKTEN", "BENUTZERGRUPPEN"):
        assert not h.is_software_name(name), name


def test_custom_field_and_rights_names():
    for name in ("ZUSATZFELDER", "ZUSATZFELDWERTE", "CUSTOM_FIELDS", "UserFields", "FREIFELDER", "EAV_VALUES"):
        assert h.is_custom_field_table(name), name
    for name in ("CUSTOMERS", "LEAVE_REQUESTS", "AKTEN"):
        assert not h.is_custom_field_table(name), name
    assert h.is_rights_table("AKTENRECHTE", "records")
    assert h.is_rights_table("ZUGRIFF_AKTEN", "records")
    assert not h.is_rights_table("RECHTSGEBIETE", "lookup")      # areas of law: a lookup
    assert not h.is_rights_table("SYS_RECHTE", "records")        # the software's own
    assert not h.is_rights_table("AKTEN", "records")


def test_roles():
    tables = practice_catalog()["tables"]
    keys = h.single_keys(tables)
    roles = {t["name"]: h.guess_role(t, keys) for t in tables}
    assert roles == {
        "SYS_CONFIG": "software",
        "NUMBERS": "lookup",
        "CLIENTS": "records",
        "MATTERS": "records",
        "MATTER_PARTIES": "records",        # link table: three other tables' keys
        "ROLES": "lookup",
        "MATTER_PERMISSIONS": "records",    # link table
        "USERS": "records",                 # 20 rows but 6 columns
        "TIME_ENTRIES": "records",
        "CUSTOM_FIELD_VALUES": "records",   # composite key: never a lookup
        "CUSTOMERS_OLD": "leftover",
        "MATTERS_20190101": "leftover",     # no key (and a date suffix)
        "NOTES": "records",
    }


def test_link_table_and_lookup_rules():
    keys = {"akteNr".lower(): "AKTEN", "adrnr": "ADRESSEN"}
    link = tbl("BETEILIGTE", [col("AkteNr"), col("AdrNr")], ["AkteNr", "AdrNr"])
    assert h.is_link_table(link, keys)
    assert not h.is_link_table(tbl("X", [col("AkteNr"), col("Pos")], ["AkteNr", "Pos"]), keys)
    assert h.is_lookup(tbl("L", [col("a"), col("b"), col("c"), col("d")], ["a"], rows=100000))
    assert h.is_lookup(tbl("L", [col(c) for c in "abcde"], ["a"], rows=50))
    assert not h.is_lookup(tbl("L", [col(c) for c in "abcde"], ["a"], rows=51))
    assert not h.is_lookup(tbl("L", [col("a"), col("b")], ["a", "b"], rows=5))


def test_vocabulary():
    names = ["AKTEN", "ADRESSEN", "LEISTUNGEN", "FRISTEN", "X_DOKUMENTE", "MANDATE", "KUNDEN"]
    assert h.vocabulary_hits(names, h.VOCABULARIES["legal_practice"]) == ["AKTE", "MANDAT", "ADRESS", "LEISTUNG",
                                                                          "FRIST", "DOKUMENT"]


def test_database_facts():
    f = database_facts(practice_catalog(), {"version": "15.0.2000.5", "edition": "Standard Edition (64-bit)",
                                            "integrated_only": 1})
    assert f["version_name"] == "SQL Server 2019" and f["authentication"] == "windows"
    assert f["tables"] == 13
    assert f["table_names"][0] == "CLIENTS" and "MATTERS_20190101" in f["table_names"]
    assert f["record_tables"] == 8 and f["lookup_tables"] == 2 and f["software_tables"] == 1
    assert f["leftover_tables"] == 2
    assert f["tables_with_rowversion"] == 1
    assert f["tables_with_changed_at"] == 2
    assert f["record_tables_without_change_marker"] == 6
    assert f["tables_with_delete_flag"] == 2
    assert f["record_tables_without_delete_flag"] == 6
    assert f["rights_tables"] == 1
    assert f["custom_field_tables"] == 1
    assert f["foreign_keys"] == 0
    assert f["views"] == 2 and f["procedures"] == 0
    assert f["rows_total"] == sum(t["rows"] for t in practice_catalog()["tables"])
    assert f["record_rows"] == 900 + 1200 + 3000 + 40 + 20 + 50000 + 800 + 3000
    assert f["vocabulary"] == {"legal_practice": 0}
    assert f["product"] is None
    clients = next(t for t in f["table_list"] if t["name"] == "CLIENTS")
    assert clients == {"name": "CLIENTS", "rows": 900, "columns": 5, "key": ["ClientNo"], "foreign_keys": 0,
                       "rowversion": True, "changed_at": ["ModifiedAt"], "delete_flag": ["Inactive"], "role": "records"}


def test_server_facts_and_product():
    assert server_facts({"version": "10.50.6000.34", "edition": "Express", "integrated_only": 0}) == {
        "version": "10.50.6000.34", "version_name": "SQL Server 2008 R2", "edition": "Express",
        "authentication": "mixed"}
    cat = {"entries": [{"key": "acme", "signature": {"tables": ["A", "B"]}},
                       {"key": "half", "signature": {"tables": ["A", "Z"], "min_share": 0.5}}]}
    assert match_product(["a", "b", "c"], cat) == "acme"
    assert match_product(["a", "c"], cat) == "half"
    assert match_product(["c"], cat) is None
