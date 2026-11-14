"""Custom views stay generic (DEC-0037, SS-628): the product code names no client, tenant, work item,
node type or relation. The words come from the brief plus every type and relation name in the shared
fixture graph, read at test time, so a new name there is guarded without touching this file."""

import re
from pathlib import Path

import pytest

from views_fixture import client_zero_graph

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

FIXED_WORDS = ["af639810", "Knovas GmbH", "client-zero", "BET-", "FEAT-", "GOAL-", "SS-", "jira", "atlassian"]

# Generic English words that a fixture type or relation happens to share. Each one is allowed only
# because ordinary code uses it in its plain meaning; never add a Knovas-specific word here.
ALLOWED = {
    "event": "plain English word (message events, DOM events)",
    "process": "plain English word (process, to process)",
    "phase": "plain English word",
    "gate": "plain English word",
    "component": "plain English word",
    "touches": "plain English verb",
    "describes": "plain English verb",
    "affects": "plain English verb",
    "keeps": "plain English verb",
    "verifies": "plain English verb",
}


def scanned_files():
    files = sorted((SRC / "custom_views").rglob("*"))
    files += [
        SRC / "web_interface" / "custom_views_routes.py",
        SRC / "web_interface" / "templates" / "custom_views.html",
        SRC / "web_interface" / "static" / "js" / "custom_views.js",
        SRC / "web_interface" / "static" / "css" / "custom_views.css",
    ]
    return [f for f in files if f.is_file() and f.suffix in {".py", ".js", ".json", ".html", ".css"}]


def fixture_names():
    graph = client_zero_graph()
    names = set(graph.types) | {edge["relation"] for edge in graph.edges}
    return {n for n in names if n.casefold() not in ALLOWED}


def pattern(word):
    tail = "" if word.endswith("-") else r"(?![A-Za-z0-9_])"  # "BET-" is a prefix: BET-2026-... must hit
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(word) + tail, re.IGNORECASE)


def test_the_scan_covers_the_files():
    names = {f.name for f in scanned_files()}
    assert {"manifest.py", "registry.py", "query.py", "budget.py", "read.py", "document.py", "settings.py",
            "bridge_client.js", "manifest.schema.json", "custom_views_routes.py"} <= names


def test_the_fixture_names_are_read():
    names = fixture_names()
    assert "FeaturePlan" in names and "instance_of" in names and "AlloyCheck" in names


def test_every_allowed_word_is_a_fixture_name():
    graph = client_zero_graph()
    names = {n.casefold() for n in set(graph.types) | {e["relation"] for e in graph.edges}}
    assert set(ALLOWED) <= names, "an allow-list entry that no fixture name needs must go"


@pytest.mark.parametrize("path", scanned_files(), ids=lambda p: p.name)
def test_no_client_or_fixture_word(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for word in [*FIXED_WORDS, *sorted(fixture_names())]:
        for match in pattern(word).finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{path.name}:{line} {word}")
    assert not hits, hits
