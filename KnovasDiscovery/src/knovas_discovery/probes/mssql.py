"""mssql: the schema of each database a read-only login can see, from the catalogue views only.

No row of a user table is ever read: every query below names sys.*, INFORMATION_SCHEMA or SERVERPROPERTY. The SQL
access is a thin reader (`PymssqlReader`) with three methods; `database_facts()` computes the facts from plain
table descriptions, which the tests feed with fake metadata.
"""

from __future__ import annotations

import os
import tempfile

from .. import heuristics as h
from ..util import ProbeError, error_code, result
from . import Context, Output

FACTS = {
    "version": "SQL Server version (SERVERPROPERTY ProductVersion)",
    "version_name": "the release that version belongs to (SQL Server 2019, 2022 ...)",
    "edition": "SQL Server edition (SERVERPROPERTY Edition)",
    "authentication": "windows (Windows logins only) or mixed (SQL and Windows logins), from SERVERPROPERTY IsIntegratedSecurityOnly",
    "tables": "user tables in the database",
    "table_names": "their names in upper case (schema-qualified when the schema is not dbo)",
    "rows_total": "rows in all tables, from sys.partitions (no table is read)",
    "record_rows": "rows in the tables whose role is records",
    "foreign_keys": "declared foreign keys",
    "views": "views the login can see",
    "procedures": "stored procedures the login can see",
    "record_tables": "tables whose guessed role is records",
    "lookup_tables": "tables whose guessed role is lookup (a code and its text)",
    "software_tables": "tables whose guessed role is software (settings, numbering, logins, locks)",
    "leftover_tables": "tables whose guessed role is leftover (backups, imports, copies; or no key)",
    "tables_with_rowversion": "tables with a rowversion (timestamp) column",
    "tables_with_changed_at": "tables with a date column named like changed/entered (MutiertAm, ErfasstAm, ModifiedOn ...)",
    "record_tables_without_change_marker": "record tables with neither a rowversion nor a changed-at column",
    "tables_with_delete_flag": "tables with a yes/no column named like deleted/inactive/cancelled/archived",
    "record_tables_without_delete_flag": "record tables without such a column: their rows disappear when removed",
    "rights_tables": "record tables whose name speaks of rights, access or locks (not SYS tables)",
    "custom_field_tables": "tables whose name speaks of custom fields (Zusatz, custom, Freifeld, EAV ...)",
    "vocabulary.legal_practice": "how many of the stems AKTE, MANDAT, ADRESS, LEISTUNG, FRIST, HONORAR, RECHNUNG, BETEILIGT, DOKUMENT, TERMIN occur in table names",
    "product": "the catalogue key of the product whose table signature matches, or null",
    "table_list": "per table: name, rows, columns, key, foreign_keys, rowversion, changed_at, delete_flag, role",
}

VERSION_NAMES = {"8": "SQL Server 2000", "9": "SQL Server 2005", "10": "SQL Server 2008", "10.5": "SQL Server 2008 R2",
                 "11": "SQL Server 2012", "12": "SQL Server 2014", "13": "SQL Server 2016", "14": "SQL Server 2017",
                 "15": "SQL Server 2019", "16": "SQL Server 2022", "17": "SQL Server 2025"}

# --------------------------------------------------------------------------- queries: catalogue views only
Q_SERVER = ("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)), "
            "CAST(SERVERPROPERTY('Edition') AS nvarchar(128)), "
            "CAST(SERVERPROPERTY('IsIntegratedSecurityOnly') AS int)")
Q_DATABASES = ("SELECT name FROM sys.databases WHERE database_id > 4 AND state = 0 AND HAS_DBACCESS(name) = 1 "
               "ORDER BY name")
Q_TABLES = ("SELECT t.object_id, s.name, t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "WHERE t.is_ms_shipped = 0")
Q_COLUMNS = ("SELECT c.object_id, c.column_id, c.name, ty.name, c.max_length FROM sys.columns c "
             "JOIN sys.tables t ON t.object_id = c.object_id "
             "JOIN sys.types ty ON ty.user_type_id = c.system_type_id "
             "WHERE t.is_ms_shipped = 0")
Q_KEYS = ("SELECT i.object_id, i.index_id, i.is_primary_key, ic.key_ordinal, c.name FROM sys.indexes i "
          "JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
          "JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
          "JOIN sys.tables t ON t.object_id = i.object_id "
          "WHERE t.is_ms_shipped = 0 AND (i.is_primary_key = 1 OR i.is_unique = 1) AND i.has_filter = 0 "
          "AND ic.key_ordinal > 0")
Q_FOREIGN_KEYS = "SELECT parent_object_id, COUNT(*) FROM sys.foreign_keys GROUP BY parent_object_id"
Q_ROWS = ("SELECT p.object_id, SUM(p.rows) FROM sys.partitions p JOIN sys.tables t ON t.object_id = p.object_id "
          "WHERE t.is_ms_shipped = 0 AND p.index_id IN (0, 1) GROUP BY p.object_id")
Q_VIEWS = "SELECT COUNT(*) FROM sys.views WHERE is_ms_shipped = 0"
Q_PROCEDURES = "SELECT COUNT(*) FROM sys.procedures WHERE is_ms_shipped = 0"
QUERIES = (Q_SERVER, Q_DATABASES, Q_TABLES, Q_COLUMNS, Q_KEYS, Q_FOREIGN_KEYS, Q_ROWS, Q_VIEWS, Q_PROCEDURES)


def quote_ident(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


# --------------------------------------------------------------------------- the thin SQL layer
class PymssqlReader:
    """An encrypted connection (encryption=require) with pymssql/FreeTDS.

    verify_tls false: the server certificate is not checked (a self-signed one is accepted).
    verify_tls true: FreeTDS checks it against `ca_file`, or the system's CA store, and checks the host name.
    """

    def __init__(self, host: str, port: int, user: str, password: str, verify_tls: bool = True,
                 ca_file: str | None = None, timeout: int = 30):
        import pymssql

        self._pymssql = pymssql
        self._conf = None
        if verify_tls:
            fd, self._conf = tempfile.mkstemp(prefix="knovas-discovery-freetds-", suffix=".conf")
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write("[global]\n\tencryption = require\n"
                        f"\tca file = {ca_file or 'system'}\n\tcheck certificate hostname = yes\n")
            os.environ["FREETDSCONF"] = self._conf
        try:
            self._conn = pymssql.connect(server=host, port=str(port), user=user, password=password,
                                         login_timeout=timeout, timeout=120, encryption="require",
                                         appname="knovas-discovery", autocommit=True)
        except pymssql.OperationalError as exc:
            raise ProbeError(_sql_error_code(exc)) from None

    def _all(self, sql: str) -> list[tuple]:
        cur = self._conn.cursor()
        try:
            cur.execute(sql)
            return list(cur.fetchall())
        finally:
            cur.close()

    def server(self) -> dict:
        version, edition, integrated = self._all(Q_SERVER)[0]
        return {"version": version, "edition": edition, "integrated_only": integrated}

    def databases(self) -> list[str]:
        return [r[0] for r in self._all(Q_DATABASES)]

    def catalog(self, database: str) -> dict:
        cur = self._conn.cursor()
        cur.execute("USE " + quote_ident(database))
        cur.close()
        tables = {oid: {"schema": s, "name": n, "columns": [], "pk": [], "unique": {}, "rows": 0, "foreign_keys": 0}
                  for oid, s, n in self._all(Q_TABLES)}
        for oid, colid, name, typ, maxlen in sorted(self._all(Q_COLUMNS), key=lambda r: (r[0], r[1])):
            if oid in tables:
                tables[oid]["columns"].append({"name": name, "type": typ, "max_length": maxlen})
        for oid, index_id, is_pk, ordinal, col in sorted(self._all(Q_KEYS), key=lambda r: (r[0], r[1], r[3])):
            if oid not in tables:
                continue
            if is_pk:
                tables[oid]["pk"].append(col)
            else:
                tables[oid]["unique"].setdefault(index_id, []).append(col)
        for oid, n in self._all(Q_FOREIGN_KEYS):
            if oid in tables:
                tables[oid]["foreign_keys"] = int(n)
        for oid, n in self._all(Q_ROWS):
            if oid in tables:
                tables[oid]["rows"] = int(n or 0)
        out = []
        for t in tables.values():
            unique = [t["unique"][i] for i in sorted(t["unique"])]
            key = t["pk"] or (unique[0] if unique else [])
            name = t["name"] if t["schema"].lower() == "dbo" else f"{t['schema']}.{t['name']}"
            out.append({"name": name, "rows": t["rows"], "columns": t["columns"], "key": key,
                        "foreign_keys": t["foreign_keys"]})
        return {"tables": sorted(out, key=lambda t: t["name"].upper()),
                "views": int(self._all(Q_VIEWS)[0][0]), "procedures": int(self._all(Q_PROCEDURES)[0][0])}

    def close(self) -> None:
        try:
            self._conn.close()
        finally:
            if self._conf:
                try:
                    os.unlink(self._conf)
                except OSError:
                    pass


def _sql_error_code(exc: Exception) -> str:
    """A code from a driver error without its text (which can carry the login and the server's words)."""
    args = getattr(exc, "args", ())
    number = None
    if args and isinstance(args[0], tuple) and args[0] and isinstance(args[0][0], int):
        number = args[0][0]
    if number == 18456:
        return "sql_login_failed"
    if number == 20002 or "20002" in str(exc):
        return "sql_connect_failed"
    return f"sql_error:{number}" if number else "sql_connect_failed"


# --------------------------------------------------------------------------- pure functions (unit-tested)
def server_facts(server: dict) -> dict:
    version = str(server.get("version") or "")
    parts = version.split(".")
    if parts[0] == "10" and len(parts) > 1 and parts[1].startswith("5"):
        name = VERSION_NAMES["10.5"]  # 10.50.x is 2008 R2
    else:
        name = VERSION_NAMES.get(parts[0])
    integrated = server.get("integrated_only")
    auth = "windows" if integrated == 1 else ("mixed" if integrated == 0 else None)
    return {"version": version or None, "version_name": name, "edition": server.get("edition"),
            "authentication": auth}


def match_product(table_names: list[str], catalogue: dict | None) -> str | None:
    """The catalogue entry whose `signature.tables` all occur (or at least `signature.min_share` of them)."""
    if not catalogue:
        return None
    names = {n.upper() for n in table_names}
    best, best_share = None, 0.0
    for entry in catalogue.get("entries", []):
        sig = entry.get("signature") or {}
        wanted = [t.upper() for t in sig.get("tables") or []]
        if not wanted:
            continue
        share = sum(1 for t in wanted if t in names) / len(wanted)
        if share >= float(sig.get("min_share", 1.0)) and share > best_share:
            best, best_share = entry["key"], share
    return best


def database_facts(catalog: dict, server: dict, catalogue: dict | None = None) -> dict:
    tables = catalog["tables"]
    keys_of_tables = h.single_keys(tables)
    table_list = []
    for t in tables:
        role = h.guess_role(t, keys_of_tables)
        table_list.append({
            "name": t["name"],
            "rows": int(t.get("rows") or 0),
            "columns": len(t["columns"]),
            "key": list(t.get("key") or []),
            "foreign_keys": int(t.get("foreign_keys") or 0),
            "rowversion": h.has_rowversion(t["columns"]),
            "changed_at": h.changed_at_columns(t["columns"]),
            "delete_flag": h.delete_flag_columns(t["columns"]),
            "role": role,
        })
    records = [t for t in table_list if t["role"] == "records"]
    names = [t["name"] for t in table_list]
    facts = dict(server_facts(server))
    facts.update({
        "tables": len(table_list),
        "table_names": sorted(n.upper() for n in names),
        "rows_total": sum(t["rows"] for t in table_list),
        "record_rows": sum(t["rows"] for t in records),
        "foreign_keys": sum(t["foreign_keys"] for t in table_list),
        "views": int(catalog.get("views") or 0),
        "procedures": int(catalog.get("procedures") or 0),
        "record_tables": len(records),
        "lookup_tables": sum(1 for t in table_list if t["role"] == "lookup"),
        "software_tables": sum(1 for t in table_list if t["role"] == "software"),
        "leftover_tables": sum(1 for t in table_list if t["role"] == "leftover"),
        "tables_with_rowversion": sum(1 for t in table_list if t["rowversion"]),
        "tables_with_changed_at": sum(1 for t in table_list if t["changed_at"]),
        "record_tables_without_change_marker": sum(1 for t in records if not t["rowversion"] and not t["changed_at"]),
        "tables_with_delete_flag": sum(1 for t in table_list if t["delete_flag"]),
        "record_tables_without_delete_flag": sum(1 for t in records if not t["delete_flag"]),
        "rights_tables": sum(1 for t in table_list if h.is_rights_table(t["name"], t["role"])),
        "custom_field_tables": sum(1 for t in table_list if h.is_custom_field_table(t["name"])),
        "vocabulary": {k: len(h.vocabulary_hits(names, stems)) for k, stems in h.VOCABULARIES.items()},
        "product": match_product(names, catalogue),
        "table_list": table_list,
    })
    return facts


# --------------------------------------------------------------------------- the probe
def run(target: dict, ctx: Context) -> list[Output]:
    key = target["key"]
    if str(target.get("kind", "mssql")).lower() != "mssql":
        return [Output(result(key, "skipped", "kind_not_supported"))]
    password = ctx.secrets.get(target.get("password_env"), f"database {key} ({target.get('user', '')})")
    if not password:
        return [Output(result(key, "failed", f"password_missing:{target.get('password_env') or 'password_env'}"))]
    factory = ctx.sql_factory or PymssqlReader
    reader = None
    try:
        reader = factory(target.get("host", ""), int(target.get("port") or 1433), target.get("user", ""), password,
                         verify_tls=bool(target.get("verify_tls", True)), ca_file=target.get("ca_file"))
        server = reader.server()
        wanted = target.get("databases")  # optional: limit to these databases
        names = [d for d in reader.databases() if not wanted or d in wanted]
        if not names:
            return [Output(result(key, "partial", "no_database_visible", server_facts(server)), {})]
        outputs = []
        for db in names:
            dkey = f"{key}:{db}"
            try:
                facts = database_facts(reader.catalog(db), server, ctx.catalogue)
                ctx.log(f"mssql {dkey}: ok, {facts['tables']} tables, {facts['rows_total']:,} rows")
                outputs.append(Output(result(dkey, "ok", None, facts), {"database": db}))
            except Exception as exc:
                ctx.log(f"mssql {dkey}: failed ({error_code(exc)})")
                outputs.append(Output(result(dkey, "failed", error_code(exc)),
                                      {"message": ctx.secrets.scrub(f"{type(exc).__name__}: {exc}")}))
        return outputs
    except Exception as exc:
        ctx.log(f"mssql {key}: failed ({error_code(exc)})")
        return [Output(result(key, "failed", error_code(exc)),
                       {"message": ctx.secrets.scrub(f"{type(exc).__name__}: {exc}")})]
    finally:
        if reader is not None and hasattr(reader, "close"):
            reader.close()
