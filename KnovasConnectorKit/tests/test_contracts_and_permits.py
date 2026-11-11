"""The kit's copy of the v2 contracts, canonical JSON and permits (WB-10, WB-21; SPEC-write-back -21, -22, -10)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from knovas_connector_kit import contracts
from knovas_connector_kit.canonical import CanonicalError, canonical, params_hash
from knovas_connector_kit.permits import (MAX_LIFETIME_SECONDS, KeyRing, PermitError, Signer, b64e, decode,
                                          permit_id_unverified, verify)

KIT_V2 = Path(contracts.FOLDER)
LINK_V2 = Path(__file__).resolve().parents[2] / "KnovasLink" / "contracts" / "v2"
EXAMPLES = LINK_V2 / "examples" / "valid"


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))["instance"]


def test_the_kits_contracts_are_links_v2_contracts_byte_for_byte():
    names = sorted(p.name for p in KIT_V2.iterdir() if p.is_file())
    assert names == sorted(p.name for p in LINK_V2.iterdir() if p.is_file())
    for name in names:
        assert (KIT_V2 / name).read_bytes() == (LINK_V2 / name).read_bytes(), name
    assert contracts.schemas()          # the lock matches


def test_canonical_json_sorts_keys_by_utf16_and_refuses_floats():
    assert canonical({"b": 1, "a": [True, None, "x"]}) == b'{"a":[true,null,"x"],"b":1}'
    assert canonical({"\U0001F600": 1, "￿": 2}) == '{"\U0001F600":1,"￿":2}'.encode()   # UTF-16 order
    assert canonical("tab\there") == b'"tab\\there"'
    for bad in (1.5, 2 ** 53, {1: "x"}):
        with pytest.raises(CanonicalError):
            canonical(bad)


def test_the_examples_permits_verify_and_bind_their_steps():
    ring = KeyRing.from_document(_example("permit-keys.one-key"))
    for name in ("job.act-direct", "job.act-native-review"):
        job = _example(name)
        for step, token in zip(job["args"]["steps"], job["permits"]):
            d = verify(token, ring)
            assert d.payload["params_hash"] == params_hash(step["target"], step["params"])


def test_a_key_is_checked_at_the_permits_iat_not_at_the_verifiers_clock():
    """A repeat after the key's retirement still verifies, so Link can answer it with the first result (B2-Q8)."""
    signer = Signer()
    payload = {"v": 1, "permit_id": "6e5d4c3b-2a19-4f08-8e7d-6c5b4a392817", "tenant": "3f0c2a8e-5b1d-4c7a-9e21-6d4b8a0f1c55",
               "site": "731948205517", "source": "s", "action_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
               "change_set_id": "c5d6e7f8-0a1b-4c2d-8e3f-4a5b6c7d8e9f", "action": "set_fields",
               "params_hash": params_hash("t", {}), "expected_version": "v-1", "staging": "direct",
               "iat": 1794405600, "exp": 1794409200}
    token = signer.sign(payload)
    retired = KeyRing.from_document(signer.keys_document("2026-01-01T00:00:00Z", "2026-11-20T00:00:00Z"))
    assert verify(token, retired).payload == payload            # iat 2026-11-11 lies in the window
    later = KeyRing.from_document(signer.keys_document("2026-11-12T00:00:00Z", "2027-01-01T00:00:00Z"))
    with pytest.raises(PermitError, match="permit_bad_signature"):
        verify(token, later)                                     # the key was not valid when it was signed


def test_a_permit_lives_up_to_three_hours_and_not_a_second_longer():
    """D-15 (v2.0 as amended 2026-11-10): exp - iat at most 10800 s; one second more is permit_bad_signature."""
    signer = Signer()
    ring = KeyRing.from_document(signer.keys_document())
    payload = decode(_example("job.act-direct")["permits"][0]).payload
    assert MAX_LIFETIME_SECONDS == 10800
    assert verify(signer.sign(dict(payload, exp=payload["iat"] + 10800)), ring).payload["exp"] == payload["iat"] + 10800
    for exp in (payload["iat"] + 10801, payload["iat"]):
        with pytest.raises(PermitError, match="permit_bad_signature"):
            verify(signer.sign(dict(payload, exp=exp)), ring)


def test_a_token_has_one_encoding_and_a_changed_payload_fails():
    signer = Signer()
    ring = KeyRing.from_document(signer.keys_document())
    job = _example("job.act-direct")
    payload = decode(job["permits"][0]).payload
    token = signer.sign(payload)
    h, p, s = token.split(".")
    spaced = b64e(json.dumps(payload).encode())                  # not canonical: spaces after separators
    with pytest.raises(PermitError, match="permit_bad_signature"):
        verify(f"{h}.{spaced}.{s}", ring)
    changed = b64e(canonical(dict(payload, expected_version="v-0")))
    with pytest.raises(PermitError, match="permit_bad_signature"):
        verify(f"{h}.{changed}.{s}", ring)
    assert hashlib.sha256(signer.public_raw).hexdigest()[:16] == signer.kid[4:]


def _unsigned(header, payload) -> str:
    def part(value):
        return value if isinstance(value, str) else b64e(canonical(value))
    return part(header) + "." + part(payload) + "." + "A" * 86


@pytest.mark.parametrize("case", ["an iat past the year 9999", "a deeply nested header", "a deeply nested payload",
                                  "longer than any token"])
def test_an_untrusted_token_is_refused_and_never_raises(case):
    """Verifier, Phase A, L1: an unsigned token whose iat no calendar date holds, or whose header or payload nests
    deeper than the parser goes, is permit_bad_signature (it raised ValueError or RecursionError before)."""
    signer = Signer()
    ring = KeyRing.from_document(signer.keys_document())
    header = {"alg": "EdDSA", "kid": signer.kid, "typ": "knovas-permit+jws"}
    payload = decode(_example("job.act-direct")["permits"][0]).payload
    token = {
        "an iat past the year 9999": lambda: _unsigned(header, dict(payload, iat=2 ** 53 - 100, exp=2 ** 53 - 50)),
        "a deeply nested header": lambda: _unsigned(b64e(b"[" * 2000 + b"]" * 2000), payload),
        "a deeply nested payload": lambda: _unsigned(header, b64e(b"[" * 1500 + b"]" * 1500)),
        "longer than any token": lambda: "A" * 9000 + ".B.C",
    }[case]()
    with pytest.raises(PermitError, match="permit_bad_signature"):
        verify(token, ring)
    permit_id_unverified(token)                                  # never raises either


@pytest.mark.parametrize("part", [0, 1, 2])
def test_a_part_spelled_another_way_is_refused(part):
    """Verifier, Phase A, L5: a part whose length is not a multiple of 4 has unused bits in its last character; setting
    them spells the same bytes another way. The signature's last character could be changed so before, and the token
    still verified; now only the one canonical encoding is read."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signer = Signer()
    ring = KeyRing.from_document(signer.keys_document())
    payload = decode(_example("job.act-direct")["permits"][0]).payload
    tokens = [signer.sign(dict(payload, source=payload["source"] + "x" * k)) for k in range(3)]
    token = next(t for t in tokens if len(t.split(".")[part]) % 4)
    verify(token, ring)
    parts = token.split(".")
    other = parts[part][:-1] + alphabet[alphabet.index(parts[part][-1]) ^ 1]
    for bad in (other, parts[part][:len(parts[part]) - len(parts[part]) % 4] + "A"):
        with pytest.raises(PermitError, match="permit_bad_signature"):
            verify(".".join(parts[:part] + [bad] + parts[part + 1:]), ring)
