"""The connection layer in the Builder is the generic product (MVP-23..MVP-28, KC-7b).

Everything built for sites, the install package, credentials, source switches, the mapping panels, «Freigaben» and
the connection card must deploy for any company. This guard reads every file that work added or changed and fails on
the specifics of one company: a tenant id, the company's own name or its first-client programme, a site, mapping,
type or repository name of it. The product's own names (Knovas, Knovas Link) are allowed.

A file that existed before and already names such a thing elsewhere is read only between the markers the
connection layer put around its own lines (``PARTIAL``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = Path(__file__).resolve().parents[3] / "docs"

#: Built from parts, so this file does not name them itself.
FORBIDDEN = [
    re.compile("af63" + "9810"),
    re.compile(r"Knovas\s+" + "GmbH"),
    re.compile(r"client[\s_-]?" + "zero", re.IGNORECASE),
    re.compile("Knovas " + "Büro"),
    re.compile("brain" + "-facts"),
    re.compile(r"\b" + "Cla" + r"im\b"),
    re.compile("Seifed" + "dini"),
    re.compile("Knowledge" + "Base"),
]

#: Every file the connection layer added or changed, whole.
COVERED = [
    "src/builder/approvals.py",
    "src/builder/codes.py",
    "src/builder/components.py",
    "src/builder/connection.py",
    "src/builder/connection_api.py",
    "src/builder/mapping_edits.py",
    "src/builder/mappings.py",
    "src/builder/protocol.py",
    "src/builder/sites.py",
    "src/builder/source_edits.py",
    "src/builder/sources.py",
    "src/builder/store.py",
    "src/identity/migrations/0022_builder_sites.sql",
    "src/web_interface/builder_routes.py",
    "src/web_interface/connector_protocol.py",
    "src/web_interface/static/js/builder/approvals.js",
    "src/web_interface/static/js/builder/builder.js",
    "src/web_interface/static/js/builder/components.js",
    "src/web_interface/static/js/builder/connection.js",
    "src/web_interface/static/js/builder/mapping.js",
    "src/web_interface/static/js/builder/site.js",
    "src/web_interface/static/js/builder/sources.js",
    "src/web_interface/templates/builder/_alerts.html",
    "src/web_interface/templates/builder/_base.html",
    "src/web_interface/templates/builder/_connection_cards.html",
    "src/web_interface/templates/builder/approvals.html",
    "src/web_interface/templates/builder/components.html",
    "src/web_interface/templates/builder/connection.html",
    "src/web_interface/templates/builder/mapping.html",
    "src/web_interface/templates/builder/operate.html",
    "src/web_interface/templates/builder/site.html",
    "tests/builder_harness.py",
    "tests/test_builder_approvals.py",
    "tests/test_builder_mapping_writes.py",
    "tests/test_builder_monitoring_mvp.py",
    "tests/test_builder_site_credentials.py",
    "tests/test_builder_sites.py",
    "tests/test_builder_source_switches.py",
]
#: Files that named such things before this work: only the connection layer's block is read.
PARTIAL = {
    "src/web_interface/app.py": ("# connection layer (MVP-27, MVP-28): begin", "# connection layer (MVP-27, MVP-28): end"),
}
DOC_PAGES = ["connect-a-site.md"]


def _hits(text: str) -> list[str]:
    return [m.group(0) for rx in FORBIDDEN for m in rx.finditer(text)]


@pytest.mark.parametrize("path", COVERED)
def test_a_covered_file_names_no_company(path):
    assert _hits((ROOT / path).read_text(encoding="utf-8")) == [], path


@pytest.mark.parametrize("path,markers", sorted(PARTIAL.items()))
def test_the_connection_layer_s_blocks_name_no_company(path, markers):
    text = (ROOT / path).read_text(encoding="utf-8")
    start, end = markers
    blocks = re.findall(re.escape(start) + r"(.*?)" + re.escape(end), text, re.S)
    assert blocks, f"{path}: the markers around the connection layer's lines are missing"
    assert text.count(start) == text.count(end) == len(blocks)
    assert [h for b in blocks for h in _hits(b)] == [], path


@pytest.mark.parametrize("name", DOC_PAGES)
def test_the_consultant_s_page_names_no_company(name):
    assert _hits((DOCS / name).read_text(encoding="utf-8")) == [], name


def test_the_guard_finds_what_it_is_for():
    for sample in ("tenant af63" + "9810", "Knovas " + "GmbH", "Client " + "Zero", "client" + "-zero", "Knovas " + "Büro",
                   "brain" + "-facts", "type Cla" + "im", "Seifed" + "dini/Knowledge" + "Base"):
        assert _hits(sample), sample
    assert _hits("Knovas Link am Standort von Muster & Partner") == []
