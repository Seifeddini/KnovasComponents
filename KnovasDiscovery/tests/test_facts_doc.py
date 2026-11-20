"""docs/facts.md lists exactly the facts the probes produce."""

from pathlib import Path

from knovas_discovery import facts as facts_doc
from knovas_discovery.probes import Context, all_facts, directory, environment, fileshare, m365
from knovas_discovery.probes.mssql import database_facts
from knovas_discovery.secrets import Secrets

from conftest import NOW, FakeSession, directory_entries, practice_catalog

DOC = Path(__file__).parent.parent / "docs" / "facts.md"


def leaves(facts: dict, registered: set[str], prefix: str = "") -> set[str]:
    out = set()
    for k, v in facts.items():
        path = f"{prefix}{k}"
        if path not in registered and isinstance(v, dict):
            out |= leaves(v, registered, path + ".")
        else:
            out.add(path)
    return out


def produced(tmp_path) -> dict[str, dict]:
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "a.txt").write_text("x")
    ctx = Context(now=NOW, secrets=Secrets(environ={"S": "x"}, interactive=False), http_session=FakeSession(),
                  knovas={}, out_dir=tmp_path)
    e = directory_entries()
    return {
        "environment": environment.run(ctx)[0].result["facts"],
        "fileshare": fileshare.scan_root(str(tmp_path), "r", NOW)[0],
        "directory": directory.directory_facts(e["root"], e["users"], e["groups"], e["computers"], e["ous"])[0],
        "mssql": database_facts(practice_catalog(), {"version": "16.0.1", "edition": "x", "integrated_only": 0}),
        "m365": m365.run({"key": "t", "tenant_id": "t", "client_id": "c", "secret_env": "S"}, ctx)[0].result["facts"],
    }


def test_probes_produce_exactly_the_registered_facts(tmp_path):
    registered = all_facts()
    for probe, facts in produced(tmp_path).items():
        assert leaves(facts, set(registered[probe])) == set(registered[probe]), probe


def test_facts_md_is_current():
    assert DOC.read_text("utf-8") == facts_doc.render(), \
        "docs/facts.md is stale: python -m knovas_discovery.facts > docs/facts.md"
