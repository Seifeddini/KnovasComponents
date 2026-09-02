import os
import stat
from pathlib import Path

import pytest

from src.identity.broker_key import (
    BrokerKeyUnavailableError,
    load_or_create_signer,
    public_pem,
)


def test_first_call_creates_a_key(tmp_path: Path):
    signer = load_or_create_signer(tmp_path)
    token = signer.mint(subject="u1", tenant="t1", groups=["g1"])
    assert token.count(".") == 2


@pytest.mark.skipif(os.name == "nt", reason="Unix file modes not enforced on Windows")
def test_key_file_is_owner_only(tmp_path: Path):
    load_or_create_signer(tmp_path)
    mode = (tmp_path / "broker_ed25519.pem").stat().st_mode
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


def test_second_call_reuses_the_same_key(tmp_path: Path):
    first = public_pem(tmp_path) if (tmp_path / "broker_ed25519.pem").exists() else None
    load_or_create_signer(tmp_path)
    once = public_pem(tmp_path)
    load_or_create_signer(tmp_path)
    assert public_pem(tmp_path) == once
    assert first is None


def test_unreadable_key_raises_rather_than_regenerating(tmp_path: Path):
    """The dangerous failure is a silent new key.

    A regenerated key still signs, so the Platform looks healthy while every
    assertion it mints is rejected by a backend holding the old public key.
    Refuse loudly instead.
    """
    load_or_create_signer(tmp_path)
    (tmp_path / "broker_ed25519.pem").write_bytes(b"not a pem")
    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path)


def test_leftover_public_key_raises_rather_than_regenerating(tmp_path: Path):
    (tmp_path / "broker_ed25519.pub").write_bytes(b"registered public key")

    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path)


def test_leftover_key_id_raises_rather_than_regenerating(tmp_path: Path):
    (tmp_path / "broker_ed25519.kid").write_text("registered-key-id")

    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path)


def test_directory_missing_raises(tmp_path: Path):
    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path / "nope" / "deeper")
