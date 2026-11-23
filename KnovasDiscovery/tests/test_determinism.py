"""Rule 5: the same findings give the same report, byte for byte."""

import json
import shutil

from knovas_discovery.report import make_report
from knovas_discovery.scan import run_scan
from knovas_discovery.secrets import Secrets

from conftest import NOW, FakeLdap, FakeSession, FakeSql, directory_entries, practice_catalog
from test_redaction import make_intake


def test_same_findings_same_report_bytes(tmp_path):
    share = tmp_path / "share" / "Firm" / "Common"
    share.mkdir(parents=True)
    (share / "a.docx").write_bytes(b"x")
    a = tmp_path / "a"
    run_scan(make_intake(tmp_path / "share"), a, secrets=Secrets(environ={"PW_LDAP": "1", "PW_SQL": "2", "M365": "3"},
                                                                 interactive=False),
             now=NOW, ctx_overrides={"ldap_factory": FakeLdap(directory_entries()),
                                     "sql_factory": FakeSql({"Praxis": practice_catalog()}),
                                     "http_session": FakeSession()})
    b = tmp_path / "b"
    b.mkdir()
    # The same findings with every key in another order.
    data = json.loads((a / "findings.json").read_text("utf-8"))

    def reverse(node):
        if isinstance(node, dict):
            return {k: reverse(node[k]) for k in reversed(list(node))}
        if isinstance(node, list):
            return [reverse(x) for x in node]
        return node

    (b / "findings.json").write_text(json.dumps(reverse(data), indent=1), encoding="utf-8")
    make_report(a)
    make_report(b)
    for name in ("report.json", "report.md", "connection-plan.md"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    first = (a / "report.md").read_bytes()
    make_report(a)
    assert (a / "report.md").read_bytes() == first
    shutil.rmtree(b)
