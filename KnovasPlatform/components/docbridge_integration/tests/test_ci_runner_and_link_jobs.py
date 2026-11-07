"""CI runs Knovas Link's suite and installs brain-runner for the Platform's (V1-G11, FEAT-2026-11-05-knovas-brain-v1).

Static checks on ``.github/workflows/ci.yml``: the ``knovas-link`` job routes like the other jobs and pins Python
3.11 with a SHA-pinned setup-python; the ``knovas-platform`` job installs brain-runner at a pinned commit with the
read-only ``KB_READ_TOKEN``, fails (never skips) without it, and never prints it.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

CI = Path(__file__).resolve().parents[4] / ".github" / "workflows" / "ci.yml"
SETUP_PYTHON_PIN = "actions/setup-python@7f4fc3e22c37d6ff65e88745f38bd3157c663f7c"
DEFAULT_RUNNER_SHA = "646f222124324fab28cf42002ac3962ab3876127"


def _jobs():
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _step(job: str, name: str) -> dict:
    found = [s for s in _jobs()[job]["steps"] if s.get("name") == name]
    assert len(found) == 1, name
    return found[0]


def test_link_job_routes_like_the_others_and_pins_python_3_11():
    jobs = _jobs()
    link = jobs["knovas-link"]
    assert link["runs-on"] == jobs["knovas-platform"]["runs-on"] == jobs["knovas-connector"]["runs-on"]
    assert link["defaults"]["run"]["working-directory"] == "KnovasLink"
    setup = [s for s in link["steps"] if str(s.get("uses", "")).startswith("actions/setup-python@")]
    assert len(setup) == 1 and setup[0]["uses"] == SETUP_PYTHON_PIN and setup[0]["with"]["python-version"] == "3.11"
    assert "# v4.9.1" in CI.read_text(encoding="utf-8")
    install = _step("knovas-link", "Install dependencies")["run"]
    assert "pip install -r requirements.txt" in install and "pytest" in install
    assert _step("knovas-link", "Pytest")["run"].strip() == "python -m pytest -q tests"
    assert "secrets." not in yaml.safe_dump(link)                       # Link's suite needs no secret


def test_platform_job_installs_the_pinned_runner_before_its_tests():
    steps = [s.get("name") for s in _jobs()["knovas-platform"]["steps"]]
    assert steps.index("Install dependencies") < steps.index("brain-runner (pinned, from KnowledgeBase)") \
        < steps.index("Pytest")
    step = _step("knovas-platform", "brain-runner (pinned, from KnowledgeBase)")
    assert step["env"]["KB_READ_TOKEN"] == "${{ secrets.KB_READ_TOKEN }}"
    assert step["env"]["BRAIN_RUNNER_SHA"] == "${{ vars.BRAIN_RUNNER_SHA || '" + DEFAULT_RUNNER_SHA + "' }}"
    run = step["run"]
    assert re.search(r'pip install --no-cache-dir --no-deps "brain-runner @ git\+https://x-access-token:\$\{KB_READ_TOKEN\}'
                     r'@github\.com/[\w.-]+/[\w.-]+\.git@\$\{BRAIN_RUNNER_SHA\}#subdirectory=scripts/brain_runner"', run)
    assert "import brain_runner" in run                                 # installed, or the step fails


def test_the_token_is_only_in_that_step_and_never_printed():
    text = CI.read_text(encoding="utf-8")
    assert text.count("secrets.KB_READ_TOKEN") == 1
    run = _step("knovas-platform", "brain-runner (pinned, from KnowledgeBase)")["run"]
    assert "set -x" not in run and "set -o xtrace" not in run
    for line in run.splitlines():
        if re.search(r"\b(echo|printf)\b", line):
            assert "$KB_READ_TOKEN" not in line and "${KB_READ_TOKEN}" not in line, line


def test_without_the_token_the_step_fails_and_says_why():
    run = _step("knovas-platform", "brain-runner (pinned, from KnowledgeBase)")["run"]
    guard = run[:run.index("pip install")]
    assert 'if [ -z "${KB_READ_TOKEN:-}" ]; then' in guard
    assert "::error" in guard and "KB_READ_TOKEN" in guard and "exit 1" in guard
    assert "exit 0" not in run and "continue-on-error" not in yaml.safe_dump(_jobs()["knovas-platform"])
    assert re.search(r"\^\[0-9a-f\]\{40\}\$", run)                    # a full SHA, never a branch
