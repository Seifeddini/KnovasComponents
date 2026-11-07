"""The release workflow builds, signs and pushes the images from any branch (V1-G10, FEAT-2026-11-05-knovas-brain-v1).

Static checks on ``.github/workflows/release-images.yml`` and its helper ``scripts/ci/release_image.sh``: started by
hand only, on any branch; single-use runners, never the shared box; minimal permissions; every action pinned by SHA;
no secret but the run's GITHUB_TOKEN and the read-only KB_READ_TOKEN, which reaches one step as a build secret and is
never printed; cosign pinned by checksum, the pushed digest signed and verified against the run's own identity; the
digest, branch and commit in the summary; the image names the deployment already uses.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
WORKFLOW = REPO / ".github" / "workflows" / "release-images.yml"
HELPER = REPO / "scripts" / "ci" / "release_image.sh"
COMPOSE = REPO / "docker-compose.yml"
EPHEMERAL = "${{ vars.CI_EPHEMERAL_RUNNER && fromJSON('[\"self-hosted\",\"knovas-ci-ephemeral\"]') || 'ubuntu-24.04' }}"
#: The cosign KnowledgeBase's brain-engine-image.yml signs with, and its checksum.
COSIGN = ("v2.6.1", "064954c5d8c7e3b28188eee5b1727b31c411550bc5fefd41aa672d3c761d103a")
COMMIT = "5749dfb0e1a2b3c4d5e6f708192a3b4c5d6e7f80"


def _wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict:
    jobs = _wf()["jobs"]
    assert list(jobs) == ["image"]
    return jobs["image"]


def _step(name: str) -> dict:
    found = [s for s in _job()["steps"] if s.get("name") == name]
    assert len(found) == 1, name
    return found[0]


def _helper(*args: str) -> dict:
    out = subprocess.run(["bash", str(HELPER), *args], capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines())


def test_started_by_hand_only_with_a_choice_of_images():
    wf = _wf()
    on = wf.get("on", wf.get(True))                                     # PyYAML reads the key `on` as True
    assert list(on) == ["workflow_dispatch"]
    images = on["workflow_dispatch"]["inputs"]["images"]
    assert images["type"] == "choice" and images["default"] == "all"
    assert set(images["options"]) == {"all", "platform", "site", "knovas-link", "sync-component"}


def test_any_branch_and_every_choice_maps_to_images():
    job = _job()
    assert job["if"] == "startsWith(github.ref, 'refs/heads/')"        # any branch, never only main
    expr = job["strategy"]["matrix"]["image"]
    table = re.fullmatch(r"\$\{\{ fromJSON\('(.*)'\)\[inputs\.images\] \}\}", expr)
    assert table, expr
    import json

    mapping = json.loads(table.group(1))
    options = _wf().get("on", _wf().get(True))["workflow_dispatch"]["inputs"]["images"]["options"]
    assert set(mapping) == set(options)
    assert mapping["all"] == ["platform", "knovas-link", "sync-component"]
    assert mapping["site"] == ["knovas-link", "sync-component"]
    assert all(set(v) <= set(mapping["all"]) and v for v in mapping.values())
    assert job["strategy"]["fail-fast"] is False


def test_single_use_runners_never_the_shared_box():
    job = _job()
    assert job["runs-on"] == EPHEMERAL
    assert "vars.CI_RUNNER" not in yaml.safe_dump(_wf())                 # the shared box runs pull-request code


def test_permissions_are_minimal():
    assert _wf()["permissions"] == {}
    assert _job()["permissions"] == {"contents": "read", "packages": "write", "id-token": "write"}


def test_every_action_is_pinned_by_sha():
    uses = [s["uses"] for s in _job()["steps"] if "uses" in s]
    assert uses, "no action used"
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u), u
    text = WORKFLOW.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40} # v\d", line), line
    assert _step("Check out")["with"]["persist-credentials"] is False


def test_only_the_run_token_and_the_read_only_kb_token():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"GITHUB_TOKEN", "KB_READ_TOKEN"}
    assert text.count("secrets.KB_READ_TOKEN") == 1 and text.count("secrets.GITHUB_TOKEN") == 1
    build = _step("Build and check the image")
    assert build["env"]["KB_READ_TOKEN"] == "${{ secrets.KB_READ_TOKEN }}"
    assert _step("Log in to GHCR")["env"]["GHCR_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    for step in _job()["steps"]:
        run = step.get("run", "")
        assert "set -x" not in run and "set -o xtrace" not in run
        for line in run.splitlines():
            if re.search(r"\b(echo|printf)\b", line):
                assert "KB_READ_TOKEN}" not in line and "$KB_READ_TOKEN" not in line, line
    assert "--password-stdin" in _step("Log in to GHCR")["run"]


def test_the_token_reaches_the_build_as_a_secret_and_is_required():
    run = _step("Build and check the image")["run"]
    assert '--secret "id=kb_read_token,env=KB_READ_TOKEN"' in run          # BuildKit secret: no layer, no log
    assert "--build-arg KB_READ_TOKEN=" not in run
    guard = run[run.index('if [ "$RUNNER" = "yes" ]'):run.index('"${build[@]}"')]
    assert 'if [ -z "${KB_READ_TOKEN:-}" ]' in guard and "::error" in guard and "exit 1" in guard
    assert "import brain_runner" in run                                   # installed, or the step fails
    assert "docker history --no-trunc" in run and "grep -qF" in run
    assert "--pull --no-cache" in run
    assert "continue-on-error" not in yaml.safe_dump(_job())


def test_cosign_is_pinned_and_the_digest_signed_and_verified():
    env = _job()["env"]
    assert (env["COSIGN_VERSION"], env["COSIGN_SHA256"]) == COSIGN
    assert env["ISSUER"] == "https://token.actions.githubusercontent.com"
    install = _step("Install cosign (pinned by checksum)")["run"]
    assert 'sha256sum -c -' in install and "${COSIGN_VERSION}/cosign-linux-amd64" in install
    sign = _step("Sign keylessly and verify (this run's identity and commit)")
    assert sign["env"]["REF"] == "${{ steps.what.outputs.repository }}@${{ steps.push.outputs.digest }}"
    run = sign["run"]
    assert '"$cosign" sign --yes "$REF"' in run
    assert 'SIGNER="${GITHUB_SERVER_URL}/${GITHUB_WORKFLOW_REF}"' in run
    verify = run[run.index('"$cosign" verify'):]
    for flag in ('--certificate-identity "$SIGNER"', '--certificate-oidc-issuer "$ISSUER"',
                 '--certificate-github-workflow-sha "$GITHUB_SHA"', "--certificate-github-workflow-trigger workflow_dispatch"):
        assert flag in verify, flag
    assert "--certificate-identity-regexp" not in run
    assert "unset SIGSTORE_ROOT_FILE" in run and 'TUF_ROOT="$RUNNER_TEMP/' in run
    steps = [s.get("name") for s in _job()["steps"]]
    assert steps.index("Push") < steps.index("Sign keylessly and verify (this run's identity and commit)") \
        < steps.index("Summary")


def test_the_push_reads_the_digest_and_both_tags_name_it():
    run = _step("Push")["run"]
    assert "digest: sha256:[0-9a-f]{64}" in run and 'echo "digest=$DIGEST" >> "$GITHUB_OUTPUT"' in run
    assert '"$IMAGE:$SHORT_TAG"' in run and '"$IMAGE:$BRANCH_TAG"' in run


def test_the_summary_names_digest_branch_and_commit():
    run = _step("Summary")["run"]
    for part in ("${IMAGE}@${DIGEST}", "${REF_NAME}", "${GITHUB_SHA}", "$GITHUB_STEP_SUMMARY"):
        assert part in run, part
    for line in ("DOCBRIDGE_WEB_IMAGE=${IMAGE}@${DIGEST}", "KNOVAS_LINK_DIGEST=${DIGEST}", "SYNC_COMPONENT_DIGEST=${DIGEST}"):
        assert line in run, line


def test_the_image_names_are_the_ones_the_deployment_uses():
    from builder import sites

    owner_free = {k: _helper(k, "Example-Org", "main", COMMIT) for k in ("platform", "knovas-link", "sync-component")}
    assert owner_free["knovas-link"]["package"] == sites.CONTAINERS["knovas-link"]["image"]
    assert owner_free["sync-component"]["package"] == sites.CONTAINERS["sync-component"]["image"]
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    default = re.fullmatch(r"\$\{DOCBRIDGE_WEB_IMAGE:-([\w.-]+):latest\}", compose["docbridge-web"]["image"])
    assert default and owner_free["platform"]["package"] == default.group(1)
    assert compose["sync-component"]["image"].split(":")[0] == owner_free["sync-component"]["package"]
    for key, out in owner_free.items():
        assert out["repository"] == "ghcr.io/example-org/" + out["package"]      # GHCR wants lower case
        dockerfile = REPO / out["context"] / "Dockerfile"
        assert dockerfile.is_file(), dockerfile
        installs_runner = "brain-runner @ git+" in dockerfile.read_text(encoding="utf-8")
        assert (out["runner"] == "yes") == installs_runner, key


@pytest.mark.parametrize("branch, tag", [
    ("cl/jolly-fermat-ro3vkk", "cl-jolly-fermat-ro3vkk"),
    ("feature/Ä x", "feature----x"),            # Ä is two bytes
    ("-.hidden", "hidden"),
    ("deadbeef", "branch-deadbeef"),
    ("x" * 200, "x" * 128),
])
def test_the_branch_tag_is_a_valid_docker_tag(branch, tag):
    out = _helper("knovas-link", "Example-Org", branch, COMMIT)
    assert out["branch_tag"] == tag
    assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", out["branch_tag"])
    assert out["short_tag"] == COMMIT[:12]


@pytest.mark.parametrize("args", [
    ("nope", "Example-Org", "main", COMMIT),
    ("platform", "Example-Org", "main", "abc123"),
    ("platform", "bad/owner", "main", COMMIT),
    ("platform", "Example-Org", "...", COMMIT),
])
def test_the_helper_refuses_what_it_cannot_name(args):
    assert subprocess.run(["bash", str(HELPER), *args], capture_output=True).returncode != 0
