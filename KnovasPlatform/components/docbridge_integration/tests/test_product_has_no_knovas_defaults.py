"""Product code names no Knovas tenant default (V1-G4, FEAT-2026-11-05-knovas-brain-v1).

KnovasComponents is the generic product. Knovas is one of its tenants, and its values (its knowledge-base
repository, its ``client-zero`` folders, its tenant id, its company name) belong in that tenant's deployment
(``KnovasPlatform/docs/deployment/knovas-tenant.md``), never in a default the product ships. A product default
that names them makes every other company's deployment quietly read Knovas's repository or folders.

This guard reads every tracked file of the repository and fails on those four tokens, except where the short
allow-list below says why. Each allow-list entry must still be needed: an entry that matches nothing fails too,
so the list only shrinks. The tokens are built from pieces so that this file does not hold them.

The per-component guards of MVP-31 (``KnovasLink/tests/test_generic.py``, ``tests/test_sync_component_generic.py``)
are stricter for their components and stay as they are.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

REPO = Path(__file__).resolve().parents[4]

FORBIDDEN = [
    re.compile("Seif" + "eddini/" + "Knowledge" + "Base", re.I),     # Knovas's knowledge-base repository
    re.compile("client" + "-" + "zero", re.I),                        # its tenant folders in that repository
    re.compile("af63" + "9810"),                                       # its tenant id
    re.compile("Knovas" + r"[\s_-]+" + "GmbH", re.I),                  # its company
]


@dataclass(frozen=True)
class Allowed:
    pattern: str                    #: fnmatch on the repository path
    line: Optional[str]             #: None: the whole file; else a regex the line must match
    why: str


ALLOWED: Tuple[Allowed, ...] = (
    Allowed("*/tests/*", None,
            "Tests and their fixtures: the Knovas bundle fixture is Knovas's own data on purpose, tests on it set "
            "the Knovas tenant's values explicitly (views_fixture.KNOVAS_REPO_LAYOUT), and the guard lists of "
            "MVP-31 name what they forbid."),
    Allowed("KnovasPlatform/docs/deployment/knovas-tenant.md", None,
            "The Knovas tenant's deployment document: where Knovas's own values belong."),
    Allowed("docs/superpowers/*", None,
            "Dated plans and reports: the history of how the Platform was built for its first tenant. Not shipped, "
            "no default."),
    Allowed("KnovasPlatform/components/docbridge_integration/Dockerfile",
            r'.*"brain-runner @ git\+https://github\.com/[\w.-]+/[\w.-]+\.git@\$\{BRAIN_RUNNER_GIT_REF\}'
            r'#subdirectory=scripts/brain_runner".*',
            "Where the image installs brain-runner, the product's own mapping runner, from: the repository it is "
            "released in, not a tenant's source (the exception MVP-31 makes for the sync component)."),
    Allowed("KnovasPlatform/components/sync_component/Dockerfile",
            r'\s*"brain-runner @ git\+https://github\.com/[\w.-]+/[\w.-]+\.git@\$\{BRAIN_RUNNER_GIT_REF\}'
            r'#subdirectory=scripts/brain_runner" \\',
            "The same runner install as the Platform's image."),
    Allowed(".github/workflows/ci.yml",
            r'\s*pip install --no-cache-dir --no-deps "brain-runner @ git\+https://x-access-token:\$\{KB_READ_TOKEN\}'
            r'@github\.com/[\w.-]+/[\w.-]+\.git@\$\{BRAIN_RUNNER_SHA\}#subdirectory=scripts/brain_runner"',
            "CI installs the same runner for the Platform's tests, from the repository it is released in (V1-G11)."),
    Allowed("knovas.env.example", r"# built\. A fine-grained GitHub token, repository \S+ only,",
            "KB_READ_TOKEN: the build token that reads the runner's source repository (above), not a tenant value."),
    Allowed("scripts/start.sh", r'.*(https://api\.github\.com/repos/\S+ \|\| true\)"'
                                r'|why="the token cannot see \S+ \(repository access: add it, Contents: read\)").*',
            "start.sh checks that KB_READ_TOKEN can read the runner's source repository before the build."),
    Allowed("KnovasPlatform/components/docbridge_integration/src/*/schema*/*.schema.json",
            r'\s*"\$id": "https://knovas\.ch/[a-z-]+/[a-z-]+\.schema\.json",',
            "KnowledgeBase's definition schemas, kept with their published identifier ($id); it names the schema, "
            "not a default, and nothing resolves it."),
    Allowed("KnovasPlatform/components/docbridge_integration/src/identity/migrations/*.sql", r"--.*",
            "A comment in an applied migration: the migration ledger checksums each file, so it is not edited."),
)


def _tracked() -> List[str]:
    try:
        out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:      # the guard never passes without reading the files
        pytest.fail(f"cannot list the repository's files ({type(exc).__name__}); the guard needs a git checkout")
    return sorted(p for p in out.decode("utf-8").split("\0") if p)


def _text(rel: str) -> Optional[str]:
    path = REPO / rel
    if not path.is_file():
        return None
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None                                              # binary: no default lives there
    return data.decode("utf-8", errors="replace")


def _findings() -> List[Tuple[str, int, str, str]]:
    """(path, line number, pattern, line) for every forbidden token in a tracked text file."""
    out = []
    for rel in _tracked():
        text = _text(rel)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            for p in FORBIDDEN:
                if p.search(line):
                    out.append((rel, n, p.pattern, line))
    return out


def _allowed_by(rel: str, line: str) -> Optional[Allowed]:
    for a in ALLOWED:
        if fnmatch.fnmatchcase(rel, a.pattern) and (a.line is None or re.fullmatch(a.line, line)):
            return a
    return None


def test_the_guard_reads_the_product():
    tracked = set(_tracked())
    assert {"KnovasPlatform/components/docbridge_integration/config/config.yaml",
            "KnovasPlatform/components/docbridge_integration/src/graph_views/change_requests.py",
            "KnovasPlatform/components/docbridge_integration/src/graph_views/plan_file.py",
            "KnovasPlatform/components/docbridge_integration/src/graph_views/writers.py",
            "KnovasPlatform/components/sync_component/Dockerfile", "knovas.env.example"} <= tracked
    assert any(p.startswith("KnovasLink/src/") for p in tracked) and any(p.startswith("KnovasConnector/") for p in tracked)


def test_no_product_file_names_a_knovas_default():
    bad = [f"{rel}:{n}: {pattern}" for rel, n, pattern, line in _findings() if _allowed_by(rel, line) is None]
    assert bad == [], "Knovas values belong in the tenant's deployment (knovas-tenant.md), not in product code:\n" \
                      + "\n".join(bad)


def test_every_allow_list_entry_is_still_needed():
    used = {_allowed_by(rel, line) for rel, _, _, line in _findings()}
    assert [a.pattern for a in ALLOWED if a not in used] == []


def test_every_allow_list_entry_says_why():
    assert len(ALLOWED) <= 10
    for a in ALLOWED:
        assert len(a.why) > 40, a.pattern


@pytest.mark.parametrize("text", [
    "github_repo: \"${VIEWS_KB_GITHUB_REPO:-" + "Seif" + "eddini/" + "Knowledge" + "Base}\"",
    "docs/ModernDocs/" + "client" + "-zero/ontology/decisions",
    "Client" + "-Zero",
    "tenant " + "af63" + "9810-afc7",
    "Knovas" + " GmbH", "Knovas" + "_GmbH",
])
def test_the_guard_finds_each_token(text):
    assert any(p.search(text) for p in FORBIDDEN)


@pytest.mark.parametrize("text", ["Knovas Platform", "knovas-link", "example-org/handbook", "client zero-shot",
                                  "docs/ModernDocs/plans/", "views.change_requests.decisions_dir"])
def test_the_guard_lets_generic_text_through(text):
    assert not any(p.search(text) for p in FORBIDDEN)


def test_a_line_allowance_covers_that_line_only():
    dockerfile = "KnovasPlatform/components/sync_component/Dockerfile"
    install = ('       "brain-runner @ git+https://github.com/' + "Seif" + "eddini/" + "Knowledge" + "Base"
               '.git@${BRAIN_RUNNER_GIT_REF}#subdirectory=scripts/brain_runner" \\')
    assert _allowed_by(dockerfile, install) is not None
    assert _allowed_by(dockerfile, "ARG REPO=" + "Seif" + "eddini/" + "Knowledge" + "Base") is None
    assert _allowed_by("KnovasPlatform/components/docbridge_integration/config/config.yaml",
                       "  repository: \"${BRAIN_SYNC_REPOSITORY:-" + "Seif" + "eddini/" + "Knowledge" + "Base}\"") is None
