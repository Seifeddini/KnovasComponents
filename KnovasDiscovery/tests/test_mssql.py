import re

from knovas_discovery.probes import Context, mssql
from knovas_discovery.secrets import Secrets
from knovas_discovery.util import ProbeError

from conftest import NOW, FakeSql, practice_catalog


def ctx(**kw):
    return Context(now=NOW, secrets=Secrets(environ={"PW": "x"}, interactive=False), **kw)


TARGET = {"key": "praxis", "kind": "mssql", "host": "sql", "port": 1433, "user": "ro", "password_env": "PW",
          "verify_tls": False}


def test_queries_name_only_catalogue_views():
    """Rule 1: no row of a client table is read. Every FROM/JOIN names sys.* (or INFORMATION_SCHEMA)."""
    for q in mssql.QUERIES:
        targets = re.findall(r"\b(?:FROM|JOIN)\s+([\w.\[\]]+)", q, re.IGNORECASE)
        for t in targets:
            assert t.lower().startswith(("sys.", "information_schema.")), (t, q)
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|EXEC|CREATE|ALTER|DROP)\b", q, re.IGNORECASE), q


def test_one_result_per_database_with_server_facts():
    fake = FakeSql({"Praxis": practice_catalog(), "Other": {"tables": [], "views": 0, "procedures": 0}})
    outs = mssql.run(TARGET, ctx(sql_factory=fake))
    assert [o.result["key"] for o in outs] == ["praxis:Other", "praxis:Praxis"]
    other, praxis = (o.result for o in outs)
    assert other["status"] == "ok" and other["facts"]["tables"] == 0
    assert praxis["facts"]["version"] == "16.0.4215.2"
    assert praxis["facts"]["version_name"] == "SQL Server 2022"
    assert praxis["facts"]["authentication"] == "mixed"
    assert praxis["facts"]["record_tables"] == 8
    assert set(praxis["facts"]) == set(mssql.FACTS) - {"vocabulary.legal_practice"} | {"vocabulary"}


def test_limit_to_named_databases_and_none_visible():
    fake = FakeSql({"Praxis": practice_catalog(), "Other": practice_catalog()})
    outs = mssql.run({**TARGET, "databases": ["Praxis"]}, ctx(sql_factory=fake))
    assert [o.result["key"] for o in outs] == ["praxis:Praxis"]
    [out] = mssql.run(TARGET, ctx(sql_factory=FakeSql({})))
    assert out.result["status"] == "partial" and out.result["error"] == "no_database_visible"


def test_connect_failure_and_other_kinds():
    [out] = mssql.run(TARGET, ctx(sql_factory=FakeSql({}, fail=ProbeError("sql_login_failed"))))
    assert (out.result["status"], out.result["error"]) == ("failed", "sql_login_failed")
    [out] = mssql.run({**TARGET, "kind": "oracle"}, ctx())
    assert (out.result["status"], out.result["error"]) == ("skipped", "kind_not_supported")


def test_quote_ident():
    assert mssql.quote_ident("Kanzlei]DB") == "[Kanzlei]]DB]"


def test_error_code_of_driver_errors():
    class Op(Exception):
        pass

    assert mssql._sql_error_code(Op((18456, b"Login failed for user 'someone'."))) == "sql_login_failed"
    assert mssql._sql_error_code(Op((20002, b"DB-Lib error message 20002"))) == "sql_connect_failed"
    assert mssql._sql_error_code(Op("anything")) == "sql_connect_failed"
