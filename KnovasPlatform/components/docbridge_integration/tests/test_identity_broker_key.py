"""The broker signing key: created once, owner-only, and never silently replaced.

The failure this file guards against is a *regenerated* key. A fresh key still
signs perfectly well, so the Platform would look healthy while every assertion
it minted was refused by a backend still holding the old public half -- and
the symptom would surface days later as "search returns nothing".

Plan: docs/superpowers/plans/2026-09-02-auth-assertion-end-to-end.md, Task 3.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from identity.broker_key import (
    BrokerKeyUnavailableError,
    load_or_create_signer,
    public_pem,
)


def test_first_call_creates_a_key(tmp_path: Path):
    signer = load_or_create_signer(tmp_path)
    token = signer.mint(subject="u1", tenant="t1", groups=["g1"])
    assert token.count(".") == 2


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX mode bits are not enforced on Windows; meaningful in the Linux container",
)
def test_key_file_is_owner_only(tmp_path: Path):
    load_or_create_signer(tmp_path)
    mode = (tmp_path / "broker_ed25519.pem").stat().st_mode
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


def test_second_call_reuses_the_same_key(tmp_path: Path):
    assert not (tmp_path / "broker_ed25519.pem").exists()
    load_or_create_signer(tmp_path)
    once = public_pem(tmp_path)
    load_or_create_signer(tmp_path)
    assert public_pem(tmp_path) == once


def test_key_id_is_stable_across_loads(tmp_path: Path):
    """The kid is what the backend selects the registered key by. A load that
    minted a new kid would sign tokens no registered key matches."""
    import base64
    import json

    def kid_of(token: str) -> str:
        header = token.split(".")[0]
        header += "=" * (-len(header) % 4)
        return json.loads(base64.urlsafe_b64decode(header))["kid"]

    first = kid_of(load_or_create_signer(tmp_path).mint(subject="u", tenant="t", groups=[]))
    second = kid_of(load_or_create_signer(tmp_path).mint(subject="u", tenant="t", groups=[]))
    assert first == second
    assert (tmp_path / "broker_ed25519.kid").read_text().strip() == first


def test_unreadable_key_raises_rather_than_regenerating(tmp_path: Path):
    load_or_create_signer(tmp_path)
    (tmp_path / "broker_ed25519.pem").write_bytes(b"not a pem")
    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path)
    # And the corrupt file is still there -- nothing was overwritten.
    assert (tmp_path / "broker_ed25519.pem").read_bytes() == b"not a pem"


def test_directory_missing_raises(tmp_path: Path):
    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path / "nope" / "deeper")


def test_public_pem_without_a_key_raises(tmp_path: Path):
    with pytest.raises(BrokerKeyUnavailableError):
        public_pem(tmp_path)


def test_half_restored_bundle_raises_rather_than_minting_a_new_key(tmp_path: Path):
    """The dangerous case: the private half is gone, its siblings remain.

    "Create if absent" would quietly mint a key that signs perfectly while
    Knovas still holds the old public half, so every assertion would be
    refused days later, far from the cause.
    """
    load_or_create_signer(tmp_path)
    original_pub = (tmp_path / "broker_ed25519.pub").read_bytes()
    (tmp_path / "broker_ed25519.pem").unlink()

    with pytest.raises(BrokerKeyUnavailableError) as excinfo:
        load_or_create_signer(tmp_path)

    assert "Incomplete broker key bundle" in str(excinfo.value)
    # The surviving artifacts are untouched, so a restore is still possible.
    assert (tmp_path / "broker_ed25519.pub").read_bytes() == original_pub
    assert not (tmp_path / "broker_ed25519.pem").exists()


def test_a_stray_sibling_alone_also_raises(tmp_path: Path):
    (tmp_path / "broker_ed25519.kid").write_text("bk-leftover")
    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path)


def test_private_key_is_created_readable_only_by_its_owner(tmp_path: Path):
    load_or_create_signer(tmp_path)
    mode = stat.S_IMODE((tmp_path / "broker_ed25519.pem").stat().st_mode)
    assert mode == 0o600
