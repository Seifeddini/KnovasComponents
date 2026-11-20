"""Guesses about database tables from their catalogue entries only (names, column types, keys, row counts).

Each rule is a small named function so it can be tested and argued about on its own. The rules are general: they
name the traits such schemas share (German and English names), never the tables of one client.

A table is a dict: {name, rows, columns: [{name, type, max_length}], key: [column names]}.
"""

from __future__ import annotations

import re

DATETIME_TYPES = {"datetime", "datetime2", "smalldatetime", "datetimeoffset", "date"}
ROWVERSION_TYPES = {"timestamp", "rowversion"}
FLAG_INT_TYPES = {"bit", "tinyint", "smallint", "int"}
CHAR_TYPES = {"char": 1, "nchar": 2}  # type -> bytes per character in sys.columns.max_length

CHANGED_AT = re.compile(r"mutiert|geaendert|geändert|modified|changed|updated|lastchange|erfasst|created|erstellt|angelegt",
                        re.IGNORECASE)
DELETE_FLAG = re.compile(r"geloescht|gelöscht|deleted|inaktiv|inactive|aktiv|active|storno|archiviert", re.IGNORECASE)
RIGHTS = re.compile(r"recht|berechtig|zugriff|permission|acl|sperr", re.IGNORECASE)
# "custom" but not "customer"; "eav" only as a word of its own (not inside "leave").
CUSTOM_FIELDS = re.compile(r"zusatz|custom(?!er)|userfield|benutzerfeld|freifeld|(?:^|_)eav(?:_|$)", re.IGNORECASE)
# "temp" but not "template"; "test" but not "testament" (a will, at a law firm).
LEFTOVER_NAME = re.compile(r"backup|bak|_alt|_old|tmp|temp(?!lat)|import|kopie|copy|test(?!ament)", re.IGNORECASE)
LEFTOVER_DATE_SUFFIX = re.compile(r"(?<!\d)\d{6,8}$")
SOFTWARE_PREFIX = re.compile(r"^(sys|cfg|log|audit|setting|param|lock)", re.IGNORECASE)
# "version" only as the whole name (VERSION, SYS_VERSION, DBVERSION); DOKUMENTVERSIONEN holds records.
SOFTWARE_NAME = re.compile(r"sperr|nummern|benutzer$|session|^(?:sys_?|db_?|schema_?|app_?)?versions?(?:info)?$",
                           re.IGNORECASE)
SYS_TABLE = re.compile(r"^sys", re.IGNORECASE)

ROLES = ("records", "lookup", "software", "leftover")

VOCABULARIES = {
    "legal_practice": ["AKTE", "MANDAT", "ADRESS", "LEISTUNG", "FRIST", "HONORAR", "RECHNUNG", "BETEILIGT", "DOKUMENT",
                       "TERMIN"],
}


# --------------------------------------------------------------------------- columns
def changed_at_columns(columns: list[dict]) -> list[str]:
    """Date/time columns whose name says the row was entered or changed (MutiertAm, ErfasstAm, ModifiedOn ...)."""
    return [c["name"] for c in columns if c["type"].lower() in DATETIME_TYPES and CHANGED_AT.search(c["name"])]


def has_rowversion(columns: list[dict]) -> bool:
    """A rowversion (timestamp) column: SQL Server changes it on every write."""
    return any(c["type"].lower() in ROWVERSION_TYPES for c in columns)


def is_flag_type(column: dict) -> bool:
    t = column["type"].lower()
    if t in FLAG_INT_TYPES:
        return True
    return t in CHAR_TYPES and column.get("max_length") == CHAR_TYPES[t]


def delete_flag_columns(columns: list[dict]) -> list[str]:
    """Yes/no columns whose name says the row is deleted, inactive, cancelled or archived."""
    return [c["name"] for c in columns if is_flag_type(c) and DELETE_FLAG.search(c["name"])]


# --------------------------------------------------------------------------- table names
def is_leftover_name(name: str) -> bool:
    """Backups, copies, imports and tests: by name, or a name that ends in a date (6 to 8 digits)."""
    return bool(LEFTOVER_NAME.search(name) or LEFTOVER_DATE_SUFFIX.search(name))


def is_software_name(name: str) -> bool:
    """Tables the software keeps for itself: settings, numbering, logins, locks, logs."""
    return bool(SOFTWARE_PREFIX.match(name) or SOFTWARE_NAME.search(name))


def is_custom_field_table(name: str) -> bool:
    return bool(CUSTOM_FIELDS.search(name))


def is_rights_table(name: str, role: str) -> bool:
    """A table that says who may see what: its name speaks of rights or locks, and it holds records.

    A lookup cannot say who may see what (RECHTSGEBIETE lists areas of law), and SYS tables belong to the software.
    """
    return bool(RIGHTS.search(name)) and not SYS_TABLE.match(name) and role == "records"


def vocabulary_hits(table_names: list[str], stems: list[str]) -> list[str]:
    """The stems that occur in at least one table name."""
    upper = [n.upper() for n in table_names]
    return [s for s in stems if any(s in n for n in upper)]


# --------------------------------------------------------------------------- roles
def single_keys(tables: list[dict]) -> dict[str, str]:
    """Column name (lower case) -> table, for every table whose key is a single column."""
    out = {}
    for t in tables:
        if len(t.get("key") or []) == 1:
            out.setdefault(t["key"][0].lower(), t["name"])
    return out


def is_link_table(table: dict, keys_of_tables: dict[str, str]) -> bool:
    """A composite key made only of other tables' key columns (AkteNr + AdrNr + RolleID)."""
    key = table.get("key") or []
    if len(key) < 2:
        return False
    return all(k.lower() in keys_of_tables and keys_of_tables[k.lower()] != table["name"] for k in key)


def is_lookup(table: dict) -> bool:
    """A code and its text: a key of one column, and few columns (at most 4, or at most 5 with at most 50 rows).

    A key of several columns means the row belongs to something else, so such a table holds records.
    """
    ncols = len(table.get("columns") or [])
    rows = table.get("rows") or 0
    return len(table.get("key") or []) == 1 and (ncols <= 4 or (rows <= 50 and ncols <= 5))


def guess_role(table: dict, keys_of_tables: dict[str, str]) -> str:
    """leftover, software, lookup or records; in that order of precedence."""
    name = table["name"]
    if not table.get("key") or is_leftover_name(name):
        return "leftover"
    if is_software_name(name):
        return "software"
    if is_link_table(table, keys_of_tables):
        return "records"
    if is_lookup(table):
        return "lookup"
    return "records"
