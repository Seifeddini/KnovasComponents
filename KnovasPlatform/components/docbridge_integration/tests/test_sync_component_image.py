"""The sync component's image carries a pinned kustomize, checked before it is unpacked (V1-G6), and the one connector
kit, installed from KnovasConnectorKit.

brain-runner's ``kustomize_build`` renders a kustomization with ``kustomize build``; without it the folder is read
as files and the mapping is stopped. The Dockerfile installs one pinned release and checks the archive against the
SHA-256 its release publishes before ``tar`` touches it. The image is built from the repository's root, so that the
connector kit is in its context, and its ``Dockerfile.dockerignore`` lets in nothing else. Static checks: they read
the Dockerfile, never build it.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

DOCKERFILE = Path(__file__).resolve().parents[2] / "sync_component" / "Dockerfile"
COMPONENT = DOCKERFILE.parent
REPO = Path(__file__).resolve().parents[4]
KIT = REPO / "KnovasConnectorKit"


def _text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _arg(name: str) -> str:
    found = re.findall(rf"^ARG {name}=(\S+)\s*$", _text(), re.M)
    assert len(found) == 1, f"{name}: {found}"
    return found[0]


def _kustomize_stage() -> str:
    m = re.search(r"^FROM \S+ AS kustomize\s*$(.*?)(?=^FROM )", _text(), re.M | re.S)
    assert m, "no build stage named kustomize"
    return m.group(1)


def test_the_version_is_pinned_to_one_release():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _arg("KUSTOMIZE_VERSION"))


def test_each_architecture_has_its_own_sha256():
    sums = {arch: _arg(f"KUSTOMIZE_SHA256_{arch}") for arch in ("AMD64", "ARM64")}
    for arch, value in sums.items():
        assert re.fullmatch(r"[0-9a-f]{64}", value), arch
    assert len(set(sums.values())) == 2
    stage = _kustomize_stage()
    assert 'amd64) sha="$KUSTOMIZE_SHA256_AMD64"' in stage and 'arm64) sha="$KUSTOMIZE_SHA256_ARM64"' in stage
    # Any other architecture stops the build instead of installing an unchecked archive.
    assert re.search(r'\*\) echo "kustomize: no pinned checksum[^"]*" >&2; exit 1', stage)


def test_the_archive_is_checked_before_it_is_unpacked_and_never_piped_into_tar():
    stage = _kustomize_stage()
    assert "set -eu" in stage
    download = stage.index('curl -fsSL -o "$tgz"')
    check = stage.index('echo "$sha  $tgz" | sha256sum -c -')
    unpack = stage.index('tar -xzf "$tgz"')
    assert download < check < unpack
    assert "kustomize_v${KUSTOMIZE_VERSION}_linux_${arch}.tar.gz" in stage
    assert not re.search(r"(curl|wget)[^\n]*\|\s*(tar|sh|bash)", _text())


def test_the_runtime_image_takes_only_the_binary_and_runs_it_once():
    runtime = _text().split("\nFROM python:3.11-slim\n", 1)[1]
    assert "COPY --from=kustomize /usr/local/bin/kustomize /usr/local/bin/kustomize" in runtime
    assert "RUN kustomize version" in runtime
    assert "curl" not in runtime


def test_the_image_installs_the_one_connector_kit_from_the_repositorys_root():
    """No copy of the kit beside the component: the image installs KnovasConnectorKit itself (so its version is that
    package's), checks its contracts against their lock while it is built, and copies nothing else of the repository."""
    text = _text()
    copied = re.findall(r"^COPY\s+(?!--from=)(\S+)\s", text, re.M)
    assert copied == ["KnovasPlatform/components/sync_component/requirements.txt", "KnovasConnectorKit/",
                      "KnovasPlatform/components/sync_component/src/"]
    assert "pip install --no-cache-dir --no-deps --no-build-isolation /tmp/knovas-connector-kit" in text
    assert "contracts.schemas(); print('kit', version('knovas-connector-kit'))" in text
    assert re.search(r"^ENV PYTHONPATH=/app/src ", text, re.M)
    assert not [p for p in (COMPONENT / "src" / "knovas_connector_kit").rglob("*") if p.suffix in (".py", ".json")]
    service = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["sync-component"]
    assert service["build"]["context"] == "."
    assert service["build"]["dockerfile"] == "KnovasPlatform/components/sync_component/Dockerfile"


def test_the_build_context_holds_only_what_the_image_needs():
    lines = [line.strip() for line in (COMPONENT / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    assert lines[0] == "*"
    assert {line for line in lines if line.startswith("!")} == {
        "!KnovasPlatform/components/sync_component/requirements.txt", "!KnovasPlatform/components/sync_component/src",
        "!KnovasConnectorKit/pyproject.toml", "!KnovasConnectorKit/README.md", "!KnovasConnectorKit/src"}
    assert {"**/__pycache__", "**/*.pyc", "**/*.egg-info"} <= set(lines)


def test_the_requirements_pin_every_dependency_of_the_kit_inside_its_range():
    """The kit is installed without its dependencies, so the component's requirements carry them: each one at a
    version, or between bounds, that the kit's pyproject allows."""
    import tomllib

    from packaging.requirements import Requirement
    from packaging.version import Version

    wanted = [Requirement(d) for d in
              tomllib.loads((KIT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]]
    lines = (COMPONENT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pins = {r.name.lower(): r for r in (Requirement(line.strip()) for line in lines
                                        if line.strip() and not line.lstrip().startswith("#"))}
    assert wanted
    for dep in wanted:
        pin = pins.get(dep.name.lower())
        assert pin is not None, f"{dep.name} is not in the component's requirements"
        for spec in pin.specifier:
            if spec.operator in ("==", ">="):
                assert dep.specifier.contains(spec.version), (str(pin), str(dep))
            elif spec.operator == "<":
                uppers = [Version(s.version) for s in dep.specifier if s.operator == "<"]
                assert uppers and all(Version(spec.version) <= u for u in uppers), (str(pin), str(dep))
