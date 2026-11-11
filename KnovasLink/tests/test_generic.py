"""Knovas Link is the generic product for any company (MVP-31): it names no client. No tenant id, company name,
repository, folder, mapping id, key prefix or type name of any client's package may appear in its code, its image or
its README. The product name "Knovas Link" is not client-specific.

The forbidden tokens are built from pieces, so that this file does not hold them either.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

LINK = Path(__file__).resolve().parents[1]

FORBIDDEN = [
    # the Knovas tenant and company
    re.compile("af63" + "9810"),
    re.compile("Knovas" + r"[\s_-]+" + "GmbH", re.I),
    re.compile("client" + r"[\s_-]?" + "zero", re.I),
    re.compile("Knovas" + r"\s+" + "B(ü|ue)ro", re.I),
    # its repositories and their owner
    re.compile("Knowledge" + "Base"),
    re.compile("Seif" + "eddini", re.I),
    # its package: mapping ids, folders, key prefixes, settings and bet items
    re.compile("brain" + r"[-_]" + "facts"),
    re.compile("brain/" + "facts"),
    re.compile(r"\b" + "PERS" + "-"),
    re.compile(r"\b" + "AGENT" + "-"),
    re.compile(r"\b" + "CLAIM" + "-"),
    re.compile(r"\b" + "KZ" + "_[A-Z]"),
    re.compile(r"\b" + "BF" + r"-0\d"),
    # the type and relation names its ontology defines and no product code needs
    *(re.compile(r"(?<![A-Za-z0-9_])" + name + r"(?![A-Za-z0-9_])") for name in (
        "Cla" + "im", "Source" + "File", "Evidence" + "Record", "Feature" + "Plan", "Spec" + "Item", "Work" + "Item",
        "Value" + "Stream", "Pre" + "Mortem", "Trip" + "wire", "Spine" + "Level", "Alloy" + "Check", "Glossary" + "Term",
        "Bounded" + "Context", "Key" + "Result", "ApiService" + "Group", "Marketing" + "Asset", "Platform" + "Tier",
        "Component" + "Contract", "evidenced" + "_by", "evidence" + "_on", "has_key" + "_result", "tripwire" + "_on")),
]


def _files() -> list[Path]:
    covered = [p for p in (LINK / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    covered += [LINK / name for name in ("README.md", "Dockerfile", "pyproject.toml", "requirements.txt")]
    return sorted(covered)


def _found(text: str) -> list[str]:
    return [p.pattern for p in FORBIDDEN if p.search(text)]


def test_the_guard_covers_the_code_the_image_and_the_readme():
    names = {p.relative_to(LINK).as_posix() for p in _files()}
    assert {"README.md", "Dockerfile", "src/knovas_link/link.py", "src/knovas_link/jobs.py",
            "src/knovas_link/local_api.py"} <= names
    for p in _files():
        assert p.is_file(), p


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.relative_to(LINK).as_posix())
def test_link_names_no_client(path):
    assert not _found(path.read_text(encoding="utf-8")), path.name


@pytest.mark.parametrize("sample", [
    "tenant " + "af63" + "9810", "Knovas " + "GmbH", "Client " + "Zero", "client" + "-zero", "client" + "_zero",
    "Knovas " + "Büro", "Seif" + "eddini/" + "Knowledge" + "Base", "brain" + "-facts", "PERS" + "-siran",
    "AGENT" + "-claude-code", "CLAIM" + "-weaviate", "KZ" + "_CERT", "the " + "Cla" + "im node", "BF" + "-03",
    "evidence" + "_on: 2026-11-04",
])
def test_the_guard_catches_each_kind_of_token(sample):
    assert _found(sample), sample


@pytest.mark.parametrize("sample", ["Knovas Link", "knovas-link", "knovas_link", "sync-component", "Component",
                                    "claims no job", "claimed", "Muster Treuhand AG", "example-org/handbook"])
def test_the_guard_lets_the_product_and_made_up_examples_through(sample):
    assert not _found(sample), sample
