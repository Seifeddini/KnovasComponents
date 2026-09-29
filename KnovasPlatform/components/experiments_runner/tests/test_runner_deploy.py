"""How the stack deploys the runner: compose, Dockerfile, CI and doctor.sh.

These files live outside this directory but decide what the runner's own
guarantees are worth, so the checks run with the runner's tests. They skip
when the runner directory is used outside the repository.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from conftest import RUNNER_DIR

REPO = RUNNER_DIR.parents[2]
COMPOSE = REPO / "docker-compose.yml"
DOCKERFILE = RUNNER_DIR / "Dockerfile"
CI = REPO / ".github" / "workflows" / "ci.yml"
DOCTOR = REPO / "scripts" / "doctor.sh"
READ_ENV = REPO / "KnovasPlatform" / "scripts" / "lib" / "read_env.sh"
RC_DOCKERFILE = REPO / "RemoteController" / "Dockerfile"

pytestmark = pytest.mark.skipif(not COMPOSE.exists(), reason="not inside the repository")


def _runner_service(compose: str) -> str:
    match = re.search(r"\n  experiments-runner:\n(.*?)(?=\n  \S|\n\S)", compose, re.S)
    assert match, "no experiments-runner service in docker-compose.yml"
    return match.group(1)


def test_the_runner_uid_is_its_own_and_the_same_everywhere():
    # review-deploy-2: RLIMIT_NPROC counts every process of a uid on the host,
    # so the runner must not share RemoteController's uid.
    compose = COMPOSE.read_text(encoding="utf-8")
    service = _runner_service(compose)
    user = re.search(r'user:\s*"(\d+):(\d+)"', service)
    volume = re.search(r"experiments_runner_socket:\n(?:\s+.*\n)*?\s+o:\s*\"[^\"]*uid=(\d+),gid=(\d+)",
                       compose)
    image_user = re.search(r"^USER (\d+)$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    ci_uid = re.search(r"os\.getuid\(\) != (\d+)", CI.read_text(encoding="utf-8"))
    assert user and volume and image_user and ci_uid
    uids = {user.group(1), user.group(2), volume.group(1), volume.group(2), image_user.group(1),
            ci_uid.group(1)}
    assert len(uids) == 1, uids
    if RC_DOCKERFILE.exists():
        rc_uid = re.search(r"--uid (\d+)", RC_DOCKERFILE.read_text(encoding="utf-8"))
        assert rc_uid and rc_uid.group(1) not in uids


def test_compose_declares_the_socket_volume_the_runners_own():
    # review-security-2: the runner empties the volume at start.
    service = _runner_service(COMPOSE.read_text(encoding="utf-8"))
    assert re.search(r'RUNNER_SOCKET_DIR_EXCLUSIVE:\s*"true"', service)


def _doctor_cpu_check() -> str:
    text = DOCTOR.read_text(encoding="utf-8")
    start = text.index("# Docker refuses to create a container whose CPU limit")
    end = text.index("\nfi\n", start) + len("\nfi\n")
    return text[start:end]


@pytest.mark.parametrize("env_file, ncpu, shell, expected", [
    ("COMPOSE_PROFILES=experiments\n", "1", {}, "FAIL EXPERIMENTS_RUNNER_CPUS=2, but Docker has only 1"),
    ("COMPOSE_PROFILES=experiments\n", "4", {}, "OK   experiments-runner CPU limit 2 of the host's 4"),
    ("COMPOSE_PROFILES=a,experiments\nEXPERIMENTS_RUNNER_CPUS=1.5\n", "1", {},
     "FAIL EXPERIMENTS_RUNNER_CPUS=1.5"),
    ("COMPOSE_PROFILES=experiments\nEXPERIMENTS_RUNNER_CPUS=\n", "2", {},
     "OK   experiments-runner CPU limit 2 of the host's 2"),
    ("COMPOSE_PROFILES=experiments\nEXPERIMENTS_RUNNER_CPUS=zwei\n", "2", {}, "WARN"),
    ("COMPOSE_PROFILES=experiments\n", "", {}, ""),                    # no daemon: say nothing
    ("COMPOSE_PROFILES=\n", "1", {}, ""),                              # profile off
    ("", "2", {"COMPOSE_PROFILES": "experiments", "EXPERIMENTS_RUNNER_CPUS": "3"},
     "FAIL EXPERIMENTS_RUNNER_CPUS=3, but Docker has only 2"),         # the shell wins, as in compose
])
def test_doctor_reports_a_cpu_limit_above_the_hosts_cpus(tmp_path, env_file, ncpu, shell, expected):
    # review-deploy-3: Docker refuses to create a container with more CPUs
    # than the host has, and start.sh stops before any health check.
    if not DOCTOR.exists() or not READ_ENV.exists():
        pytest.skip("scripts/doctor.sh not present")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "docker").write_text(
        '#!/bin/sh\nif [ -n "$FAKE_NCPU" ]; then echo "$FAKE_NCPU"; '
        'else echo 0; echo "no daemon" >&2; exit 1; fi\n')
    os.chmod(fake / "docker", 0o755)
    (tmp_path / "knovas.env").write_text(env_file)
    script = tmp_path / "check.sh"
    script.write_text(
        "set -uo pipefail\n"
        'source "%s"\n'
        'ok()   { echo "OK   $*"; }\nwarn() { echo "WARN $*"; }\nbad()  { echo "FAIL $*"; }\n'
        'KNOVAS_ENV="%s"\n%s' % (READ_ENV, tmp_path / "knovas.env", _doctor_cpu_check()))
    env = {"PATH": "%s:%s" % (fake, os.environ.get("PATH", "/usr/bin:/bin")), "FAKE_NCPU": ncpu}
    env.update(shell)
    done = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    if expected:
        assert done.stdout.startswith(expected), done.stdout
    else:
        assert done.stdout == "", done.stdout
