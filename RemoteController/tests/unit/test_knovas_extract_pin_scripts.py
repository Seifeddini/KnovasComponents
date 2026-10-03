"""scripts/ci/assert_knovas_extract_pin.py: CI holds both test jobs and both
images to the pin that check_knovas_extract_pin.sh prints.

The script runs here against the library this suite runs on, so the tests
hold in CI (a git install of the pin) and in a local editable install alike.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "ci" / "assert_knovas_extract_pin.py"

pytestmark = pytest.mark.skipif(
    not (REPO / "scripts" / "lib").is_dir(), reason="not a KnovasComponents checkout"
)


def _installed() -> tuple[str, str]:
    import knovas_extract

    url = json.loads(distribution("knovas-extract").read_text("direct_url.json") or "{}")
    return knovas_extract.__version__, (url.get("vcs_info") or {}).get("commit_id") or ""


def _run(**pin: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KNOVAS_EXTRACT_")}
    env.update(pin)
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=120)


def test_the_installed_library_passes_as_its_own_pin():
    version, commit = _installed()
    done = _run(KNOVAS_EXTRACT_VERSION=version, KNOVAS_EXTRACT_GIT_REF=commit)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith(f"knovas-extract {version} (")


def test_another_version_fails_and_names_both():
    version, commit = _installed()
    done = _run(KNOVAS_EXTRACT_VERSION=version + ".post9", KNOVAS_EXTRACT_GIT_REF=commit)
    assert done.returncode == 1
    assert f"version {version}, the pin is {version}.post9" in done.stderr


def test_another_git_revision_fails():
    version, commit = _installed()
    other = "e" * 40 if commit == "f" * 40 else "f" * 40
    done = _run(KNOVAS_EXTRACT_VERSION=version, KNOVAS_EXTRACT_GIT_REF=other)
    assert done.returncode == 1
    assert f"the pin is {other}" in done.stderr


def test_without_a_pin_it_is_a_usage_error():
    done = _run()
    assert done.returncode == 2
    assert "KNOVAS_EXTRACT_VERSION is not set" in done.stderr
