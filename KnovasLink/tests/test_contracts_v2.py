"""Connector protocol 2.0 contracts, draft (BET-2026-11-write-back WB-10; SPEC-write-back -10, -12, -15, -16, -21, -22).

The schemas in KnovasLink/contracts/v2 are the draft of the generic read and write protocol, for the founder's review
(KnovasPlatform/docs/connector-protocol-v2.md). Nothing runs them yet: Knovas Link, the connectors and the Brain build
against them from WB-11 on. These tests hold the draft to itself: every example validates or fails naming its field,
the example permits verify with the example key and bind the parameters of their act steps, the folder matches its own
lock, version 1.x's contracts stay untouched, and neither the schemas nor the protocol text name a source kind.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

LINK = Path(__file__).resolve().parents[1]
V2 = LINK / "contracts" / "v2"
EXAMPLES = V2 / "examples"
PROTOCOL_DOC = LINK.parent / "KnovasPlatform" / "docs" / "connector-protocol-v2.md"
V1_CODES = LINK / "contracts" / "codes.json"

ID_PREFIX = "https://knovas.ch/connector/v2/"
SCHEMA_OF_PREFIX = {"declaration": "declaration.schema.json", "site": "site.schema.json",
                    "permit-keys": "permit-keys.schema.json", "permit": "permit.schema.json",
                    "job": "job.schema.json", "result": "result.schema.json", "stream": "stream-message.schema.json"}
JOB_KINDS = ["probe", "fetch", "preview", "act", "reread", "review_outcome", "unstage"]
WRITE_PATH_KINDS = {"preview", "act", "reread", "review_outcome", "unstage"}
RESULT_STATUSES = ["ok", "refused", "conflict", "unsupported", "unreachable", "failed"]
CONFORMANCE = ["replay_changes_nothing", "deletion_guard", "preview_equals_receipt", "exactly_once", "permit_refusals",
               "no_credential_leaves_site", "review_outcome"]
PERMIT_CLAIMS = {"v", "permit_id", "tenant", "site", "source", "change_set", "action_id", "action", "target",
                 "params_sha256", "expected_version", "staging", "issued_at", "expires_at", "approved_by"}
MAX_PERMIT_SECONDS = 3600

# SPEC-write-back-16: the protocol names no source kind, no source-specific operation and no source-specific field.
# Built from pieces so that this file does not hold the words either.
SOURCE_SPECIFIC = re.compile(r"\b(" + "|".join([
    "git" + "hub", "git" + "lab", "ji" + "ra", "one" + "drive", "share" + "point", "conflu" + "ence", "dro" + "pbox",
    "pull[ -]" + "requests?", "commits?", "branch(es)?", "merged?", "blob", "repositor(y|ies)", "issues?",
]) + r")\b", re.I)
EXAMPLES_HEADING = "Examples (informative)"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_files():
    return sorted(V2.glob("*.schema.json"))


def _schemas():
    return {p.name: _load(p) for p in _schema_files()}


def _registry() -> Registry:
    return Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in _schemas().values())


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(_schemas()[name], registry=_registry())


def _examples(kind: str):
    return sorted((EXAMPLES / kind).glob("*.json"))


def _example(name: str) -> dict:
    return _load(EXAMPLES / "valid" / f"{name}.json")["instance"]


def _jcs(obj) -> bytes:
    """RFC 8785 for what the protocol allows: strings, integers, booleans, null, arrays and objects with ASCII keys."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _leaf_paths(error) -> set[tuple]:
    """The fields an error is about, descending into oneOf/anyOf branches."""
    if error.context:
        return {p for sub in error.context for p in _leaf_paths(sub)}
    path = list(error.absolute_path)
    if error.validator == "required":
        m = re.fullmatch(r"'(.+)' is a required property", error.message)
        return {tuple(path + [m.group(1)])}
    if error.validator == "additionalProperties":
        known = set(error.schema.get("properties", {}))
        return {tuple(path + [k]) for k in error.instance if k not in known}
    return {tuple(path)}


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"section «{heading}» missing"
    return m.group(1)


# ------------------------------------------------------------------ the schemas themselves

@pytest.mark.parametrize("path", _schema_files(), ids=lambda p: p.name)
def test_schema_is_draft_2020_12_with_its_v2_id(path):
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == ID_PREFIX + path.name
    assert "(connector protocol 2.0, draft)" in schema["title"]


def test_the_v2_messages_are_exactly_these():
    assert sorted(_schemas()) == sorted(SCHEMA_OF_PREFIX.values())


def test_job_kinds_keep_probe_and_fetch_and_add_the_write_path():
    s = _schemas()["job.schema.json"]
    assert s["properties"]["kind"]["enum"] == JOB_KINDS
    branches = {b["if"]["properties"]["kind"]["const"]: b["then"] for b in s["allOf"]}
    assert set(branches) == set(JOB_KINDS), "every job kind has its args"
    for kind in WRITE_PATH_KINDS:
        assert {"source", "site"} <= set(branches[kind]["required"]), f"{kind} is addressed to one site (F5)"
    assert "write" not in s["properties"]["kind"]["enum"], "the 1.x write job stays a 1.x job"


def test_result_statuses_are_1_1_s():
    s = _schemas()["result.schema.json"]
    assert s["properties"]["status"]["enum"] == RESULT_STATUSES
    assert s["additionalProperties"] is False


def test_permit_binds_what_spec_21_names_and_carries_no_value():
    s = _schemas()["permit.schema.json"]
    claims = s["$defs"]["claims"]
    assert set(claims["required"]) == PERMIT_CLAIMS == set(claims["properties"])
    assert claims["additionalProperties"] is False and s["additionalProperties"] is False
    assert s["properties"]["alg"] == {"const": "EdDSA", "description": s["properties"]["alg"]["description"]}


def test_conformance_properties_are_the_kits():
    s = _schemas()["declaration.schema.json"]
    assert s["properties"]["conformance"]["properties"]["passed"]["items"]["enum"] == CONFORMANCE
    assert s["$defs"]["action"]["properties"]["staging"]["items"]["enum"] == ["direct", "native_review"]


def test_no_floating_point_anywhere_a_hash_is_taken():
    """A parameter is hashed into the permit, so it must have one canonical spelling."""
    s = _schemas()["job.schema.json"]
    text = json.dumps(s["$defs"]["params"]) + json.dumps(s["$defs"]["scalar"])
    assert '"number"' not in text


# ------------------------------------------------------------------ examples

@pytest.mark.parametrize("path", _examples("valid") + _examples("invalid"), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_example_names_its_schema_by_its_file_name(path):
    v = _load(path)
    assert v["schema"] == SCHEMA_OF_PREFIX[path.name.split(".", 1)[0]]
    assert set(v) == ({"schema", "description", "instance", "breaks"} if path.parent.name == "invalid"
                      else {"schema", "description", "instance"})


@pytest.mark.parametrize("path", _examples("valid"), ids=lambda p: p.name)
def test_valid_example_passes(path):
    v = _load(path)
    errors = list(_validator(v["schema"]).iter_errors(v["instance"]))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize("path", _examples("invalid"), ids=lambda p: p.name)
def test_invalid_example_fails_naming_the_field(path):
    v = _load(path)
    errors = list(_validator(v["schema"]).iter_errors(v["instance"]))
    assert errors, "passes but should fail"
    named = set().union(*(_leaf_paths(e) for e in errors))
    assert tuple(v["breaks"]) in named, sorted(named)
    assert all(p[:len(v["breaks"])] == tuple(v["breaks"]) or tuple(v["breaks"])[:len(p)] == p for p in named), sorted(named)


def test_valid_examples_cover_every_job_kind_and_every_result_part():
    kinds = {_load(p)["instance"]["kind"] for p in _examples("valid") if p.name.startswith("job.")}
    assert kinds == set(JOB_KINDS)
    parts = set()
    for p in _examples("valid"):
        if p.name.startswith("result."):
            parts |= set(_load(p)["instance"]) & {"preview", "receipts", "review", "conflict", "read", "repeat"}
    assert parts == {"preview", "receipts", "review", "conflict", "read", "repeat"}


def test_example_permits_verify_with_the_pinned_key_and_bind_their_steps():
    keys = {k["kid"]: Ed25519PublicKey.from_public_bytes(_b64(k["public_key"]))
            for k in _example("permit-keys.one-key")["keys"]}
    for job_name, permit_name in (("job.act-direct", "permit.direct"), ("job.act-native-review", "permit.native-review")):
        job = _example(job_name)
        step = job["args"]["steps"][0]
        permit = step["permit"]
        assert permit == _example(permit_name)
        keys[permit["kid"]].verify(_b64(permit["sig"]), _jcs(permit["claims"]))
        c = permit["claims"]
        assert c["params_sha256"] == hashlib.sha256(_jcs(step["params"])).hexdigest()
        assert (c["action"], c["target"], c["source"], c["site"], c["staging"]) == (
            step["action"], step["target"], job["source"], job["site"], job["args"]["staging"])
        issued, expires = (datetime.fromisoformat(c[k].replace("Z", "+00:00")) for k in ("issued_at", "expires_at"))
        assert 0 < (expires - issued).total_seconds() <= MAX_PERMIT_SECONDS


def test_a_changed_claim_breaks_the_signature():
    keys = {k["kid"]: Ed25519PublicKey.from_public_bytes(_b64(k["public_key"]))
            for k in _example("permit-keys.one-key")["keys"]}
    permit = _example("permit.direct")
    for field, value in (("site", "1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"), ("expected_version", "v-4812"),
                         ("expires_at", "2026-11-10T23:00:00Z")):
        claims = dict(permit["claims"], **{field: value})
        with pytest.raises(InvalidSignature):
            keys[permit["kid"]].verify(_b64(permit["sig"]), _jcs(claims))


def test_receipts_of_the_examples_match_their_previews():
    """preview equals receipt, as the conformance property states it, on the examples themselves."""
    preview = _example("result.preview-exact")["preview"]
    receipt = _example("result.act-written")["receipts"][0]
    assert receipt["values"] == preview["changes"] and receipt["differences"] == []
    assert receipt["expected_version"] == preview["read_versions"][0]["version"]
    assert receipt["permit_id"] == _example("permit.direct")["claims"]["permit_id"]
    assert _example("result.act-repeat")["receipts"] == _example("result.act-written")["receipts"]


# ------------------------------------------------------------------ codes and lock

def test_v2_codes_are_new_codes_of_the_shared_shape():
    codes = _load(V2 / "codes.json")
    assert set(codes) == {"codes"}
    pattern = re.compile(_schemas()["result.schema.json"]["properties"]["code"]["pattern"])
    v1 = {c["code"] for c in _load(V1_CODES)["codes"]}
    names = [c["code"] for c in codes["codes"]]
    assert len(names) == len(set(names))
    for c in codes["codes"]:
        assert set(c) == {"code", "meaning"}
        assert pattern.fullmatch(c["code"]) and c["code"] not in v1, c["code"]
        assert c["meaning"].strip() and "\n" not in c["meaning"]
    for wanted in ("permit_missing", "permit_mismatch", "permit_used", "permit_expired", "permit_wrong_site",
                   "permit_bad_signature", "version_changed", "preview_inexact", "action_not_declared"):
        assert wanted in names


def test_v2_lock_matches_its_files_and_1_x_lock_is_untouched():
    lock = _load(V2 / "contracts.lock")
    assert set(lock) == {"version", "status", "files"}
    assert lock["version"] == "2.0.0-draft" and lock["status"] == "draft"
    assert set(lock["files"]) == {p.name for p in _schema_files()} | {"codes.json"}
    for name, digest in lock["files"].items():
        assert hashlib.sha256((V2 / name).read_bytes()).hexdigest() == digest, f"{name}: v2 contracts.lock is stale"
    assert _load(LINK / "contracts" / "contracts.lock")["version"] == "1.2.0"


# ------------------------------------------------------------------ the protocol text

def _normative(text: str) -> str:
    start = text.index(f"## {EXAMPLES_HEADING}")
    rest = text[start + 3:]
    end = rest.find("\n## ")
    return text[:start] + (rest[end:] if end >= 0 else "")


def test_protocol_text_is_a_draft_for_the_founders_review():
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    assert "Draft v2.0 — for the founder's review" in text.splitlines()[2] or "Draft v2.0 — for the founder's review" in text[:600]


def test_protocol_text_names_every_message_kind_and_code():
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    for name in SCHEMA_OF_PREFIX.values():
        assert f"KnovasLink/contracts/v2/{name}" in text, name
    jobs = _section(text, "Jobs")
    assert re.findall(r"^\| `([a-z_]+)` \|", jobs, re.M) == JOB_KINDS
    for c in _load(V2 / "codes.json")["codes"]:
        assert f"`{c['code']}`" in text, c["code"]
    for heading in ("Compatibility with 1.x", "Permits", "Receipts", "Idempotency and exactly once", "Conflicts",
                    "Staging: direct and native_review", "Declaring a connector", EXAMPLES_HEADING):
        assert re.search(rf"^## {re.escape(heading)}$", text, re.M), heading


@pytest.mark.parametrize("path", sorted(p for p in V2.rglob("*") if p.is_file()), ids=lambda p: str(p.relative_to(V2)))
def test_contracts_v2_name_no_source_kind(path):
    """SPEC-write-back-16: no source kind, source operation or source field in the protocol's messages."""
    found = SOURCE_SPECIFIC.findall(path.read_text(encoding="utf-8"))
    assert not found, (path.name, found)


def test_protocol_text_names_no_source_kind_outside_its_examples():
    found = SOURCE_SPECIFIC.findall(_normative(PROTOCOL_DOC.read_text(encoding="utf-8")))
    assert not found, found
