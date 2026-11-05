"""The sync component is the generic product for any company (MVP-31): it names no client. No tenant id, company name,
repository, folder, mapping id, key prefix or type name of any client's package may appear in its code, its image,
its README, or the link-mode tests and their helpers (which use a made-up company). The product names "Knovas" and
"Knovas Link" are not client-specific.

One exception, and only that line: the image installs brain-runner, the product's own mapping runner, from the
repository it is released in (``Dockerfile``, ``pip install "brain-runner @ git+https://…#subdirectory=scripts/
brain_runner"``). It names where the product's code lives, not a client's source.

The forbidden tokens are built from pieces, so that this file does not hold them either; the list is Knovas Link's
(``KnovasLink/tests/test_generic.py``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
COMPONENT = TESTS.parents[1] / "sync_component"

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
#: The one line allowed to name a repository: where the image installs the product's mapping runner from.
RUNNER_INSTALL = re.compile(r'^\s*"brain-runner @ git\+https://github\.com/[\w.-]+/[\w.-]+\.git@\$\{BRAIN_RUNNER_GIT_REF\}'
                            r'#subdirectory=scripts/brain_runner" \\$')
LINK_MODE_TESTS = ("fake_knovas_link.py", "fake_github_rest.py", "sync_link_world.py", "test_github_jobs.py",
                   "test_github_write.py", "test_front_matter_edit.py", "test_sync_component.py",
                   "test_sync_component_generic.py")


def _files() -> list[Path]:
    covered = [p for p in (COMPONENT / "src").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    covered += [COMPONENT / name for name in ("README.md", "Dockerfile", "requirements.txt")]
    covered += [TESTS / name for name in LINK_MODE_TESTS]
    return sorted(covered)


def _found(path: Path) -> list[str]:
    found = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if path.name == "Dockerfile" and RUNNER_INSTALL.match(line):
            continue
        found += [f"{n}: {p.pattern}" for p in FORBIDDEN if p.search(line)]
    return found


def test_the_guard_covers_the_code_the_image_the_readme_and_the_link_mode_tests():
    names = {p.name for p in _files()}
    assert {"README.md", "Dockerfile", "__main__.py", "agent.py", "engine.py", "fetchers.py", "link_client.py", "jobs.py",
            "github_jobs.py", "front_matter_edit.py", "runner_cli.py", "test_github_write.py"} <= names
    for p in _files():
        assert p.is_file(), p


def test_the_exception_is_exactly_the_runner_install_line():
    lines = [line for line in (COMPONENT / "Dockerfile").read_text(encoding="utf-8").splitlines() if RUNNER_INSTALL.match(line)]
    assert len(lines) == 1


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.relative_to(TESTS.parents[1]).as_posix())
def test_no_client_of_the_product_is_named(path):
    assert _found(path) == []


@pytest.mark.parametrize("text", [
    "tenant af63" + "9810", "Knovas" + " GmbH", "client" + "-zero", "Client " + "Zero", "Seif" + "eddini/Knowledge" + "Base",
    "brain" + "-facts", "a " + "Cla" + "im node", "evidence" + "_on: 2026-11-05", "KZ" + "_CERT",
])
def test_the_guard_finds_what_it_must(text):
    assert any(p.search(text) for p in FORBIDDEN)
