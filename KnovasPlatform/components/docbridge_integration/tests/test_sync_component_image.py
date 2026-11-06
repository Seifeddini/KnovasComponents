"""The sync component's image carries a pinned kustomize, checked before it is unpacked (V1-G6).

brain-runner's ``kustomize_build`` renders a kustomization with ``kustomize build``; without it the folder is read
as files and the mapping is stopped. The Dockerfile installs one pinned release and checks the archive against the
SHA-256 its release publishes before ``tar`` touches it. Static checks: they read the Dockerfile, never build it.
"""
from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "sync_component" / "Dockerfile"


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
