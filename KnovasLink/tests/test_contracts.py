"""Connector protocol 1.1 contracts (MVP-1, MVP-2; KC-7a).

The schemas, codes and local API text in KnovasLink/contracts are what Knovas Link, the components, secure-api and the
runner build against. These tests hold them to the shared contract, to their vectors, to contracts.lock and to the
protocol text in KnovasPlatform/docs/connector-protocol-v1.md.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

LINK = Path(__file__).resolve().parents[1]
CONTRACTS = LINK / "contracts"
VECTORS = CONTRACTS / "vectors"
REPO = LINK.parent
PROTOCOL_DOC = REPO / "KnovasPlatform" / "docs" / "connector-protocol-v1.md"
PLATFORM_PROTOCOL = REPO / "KnovasPlatform" / "components" / "docbridge_integration" / "src" / "builder" / "protocol.py"
LOCAL_API = CONTRACTS / "local-api.md"

ID_PREFIX = "https://knovas.ch/connector/v1.1/"
SCHEMA_OF_PREFIX = {"stream": "stream-message.schema.json", "job": "job.schema.json", "result": "job-result.schema.json"}

# The shared contract, written out once more so that a schema edit that drifts from it fails here.
STREAM_TYPES = ["document", "tombstone", "generation", "state", "record", "principal", "group", "membership", "permission"]
MVP_STREAM_TYPES = ["document", "tombstone", "generation", "state"]
RESERVED_STREAM_TYPES = ["record", "principal", "group", "membership", "permission"]
JOB_KINDS = ["fetch", "write", "probe"]
ACTOR_KINDS = ["user", "agent", "component", "system"]
RESULT_STATUSES = ["ok", "refused", "conflict", "unsupported", "unreachable", "failed"]
MAX_FETCH_BYTES = 26214400          # 25 MiB
MAX_PART_BYTES = 4194304            # 4 MiB
CODES = ["unsupported_type", "unsupported", "unreachable", "lease_lost", "source_unreachable", "source_refused",
         "no_source_ref", "not_found", "conflict", "mode_unsupported", "token_cannot_write", "fetch_disabled",
         "write_disabled", "not_writable", "computed_attribute", "value_not_allowed", "not_invertible", "unknown_mapping",
         "previously_rejected", "waiting_for_certificate", "not_relayed", "invalid_job", "part_not_accepted",
         "too_large", "version_mismatch", "edit_unsupported", "front_matter_missing", "no_change", "github.rate_limited",
         "github.branch_left", "github.branch_exists", "github.unreachable", "github.bad_repository", "github.bad_answer",
         "github.base_not_a_branch", "runner.bad_change", "bad_field", "internal"]

# Required fields per schema and discriminator: (schema, "type=…" / "kind=…" / "", path).
REQUIRED = {
    ("stream-message.schema.json", "", ("type",)), ("stream-message.schema.json", "", ("source",)),
    ("stream-message.schema.json", "type=document", ("external_id",)),
    ("stream-message.schema.json", "type=document", ("version",)),
    ("stream-message.schema.json", "type=document", ("payload",)),
    ("stream-message.schema.json", "type=document", ("payload", "title")),
    ("stream-message.schema.json", "type=document", ("payload", "path")),
    ("stream-message.schema.json", "type=document", ("payload", "text")),
    ("stream-message.schema.json", "type=tombstone", ("external_id",)),
    ("stream-message.schema.json", "type=generation", ("payload",)),
    ("stream-message.schema.json", "type=generation", ("payload", "id")),
    ("stream-message.schema.json", "type=generation", ("payload", "complete")),
    ("stream-message.schema.json", "type=generation", ("payload", "counts")),
    ("stream-message.schema.json", "type=state", ("state",)),
    ("job.schema.json", "", ("id",)), ("job.schema.json", "", ("kind",)), ("job.schema.json", "", ("deadline",)),
    ("job.schema.json", "", ("actor",)), ("job.schema.json", "", ("args",)),
    ("job.schema.json", "", ("actor", "kind")), ("job.schema.json", "", ("actor", "id")),
    ("job.schema.json", "", ("actor", "on_behalf_of", "kind")), ("job.schema.json", "", ("actor", "on_behalf_of", "id")),
    ("job.schema.json", "kind=fetch", ("source",)), ("job.schema.json", "kind=write", ("source",)),
    ("job.schema.json", "kind=fetch", ("args", "external_id")), ("job.schema.json", "kind=fetch", ("args", "max_bytes")),
    *{("job.schema.json", "kind=write", ("args", f)) for f in
      ("mapping", "node_key", "external_id", "expected_version", "facts", "mode")},
    ("job-result.schema.json", "", ("status",)), ("job-result.schema.json", "", ("duration_ms",)),
}

# The contracts are the product for any company: no tenant, company, repository, mapping or type of one client.
# Built from pieces so that this file does not hold them either.
COMPANY_SPECIFIC = [
    re.compile("af63" + "9810"),
    re.compile("client" + r"[\s_-]?" + "zero", re.I),
    re.compile("brain" + "-facts"),
    re.compile(r"\b" + "Cla" + r"im\b"),
    re.compile("Seif" + "eddini", re.I),
    re.compile("Knowledge" + "Base"),
    re.compile("Knovas" + r"\s+" + "GmbH", re.I),
    re.compile(r"\bPERS-"),
    re.compile(r"\bAGENT-"),
]

# Request errors of protocol v1 that the local API reuses; they are protocol v1's, not part of codes.json.
PROTOCOL_V1_REQUEST_CODES = {"bad_field", "bad_token", "unknown_component", "unknown_command"}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_files():
    return sorted(CONTRACTS.glob("*.schema.json"))


def _schemas():
    return {p.name: _load(p) for p in _schema_files()}


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(_schemas()[name])


def _vectors(kind: str):
    return sorted((VECTORS / kind).glob("*.json"))


def _field_paths(error) -> list[tuple]:
    """The field an error is about: the property a `required` misses, the property `additionalProperties` refuses,
    otherwise the place of the bad value."""
    path = list(error.absolute_path)
    if error.validator == "required":
        m = re.fullmatch(r"'(.+)' is a required property", error.message)
        assert m, error.message
        return [tuple(path + [m.group(1)])]
    if error.validator == "additionalProperties":
        known = set(error.schema.get("properties", {}))
        return [tuple(path + [k]) for k in error.instance if k not in known]
    return [tuple(path)]


def _discriminator(schema_name: str, instance) -> str:
    if not isinstance(instance, dict):
        return ""
    if schema_name == "stream-message.schema.json" and "type" in instance:
        return f"type={instance['type']}"
    if schema_name == "job.schema.json" and "kind" in instance:
        return f"kind={instance['kind']}"
    return ""


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"section «{heading}» missing"
    return m.group(1)


def _table_first_column(section: str) -> list[str]:
    return re.findall(r"^\| `([a-z_]+)` \|", section, re.M)


# ------------------------------------------------------------------ the schemas themselves

@pytest.mark.parametrize("path", _schema_files(), ids=lambda p: p.name)
def test_schema_is_draft_2020_12_with_its_id(path):
    schema = _load(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == ID_PREFIX + path.name


def test_the_three_schemas_exist():
    assert sorted(_schemas()) == sorted(SCHEMA_OF_PREFIX.values())


def test_stream_types_are_the_shared_ones_and_reserved_types_are_in_the_enum():
    enum = _schemas()["stream-message.schema.json"]["properties"]["type"]["enum"]
    assert enum == STREAM_TYPES
    for t in RESERVED_STREAM_TYPES:
        assert t in enum


def test_job_kinds_actor_kinds_and_write_mode():
    s = _schemas()["job.schema.json"]
    assert s["properties"]["kind"]["enum"] == JOB_KINDS
    assert s["$defs"]["actor"]["properties"]["kind"]["enum"] == ACTOR_KINDS
    assert s["$defs"]["write_args"]["properties"]["mode"] == {"const": "proposal"}
    assert s["$defs"]["fetch_args"]["properties"]["max_bytes"]["maximum"] == MAX_FETCH_BYTES
    branches = {b["if"]["properties"]["kind"]["const"] for b in s["allOf"]}
    assert branches == set(JOB_KINDS), "every job kind has its args"


def test_lease_is_optional_opaque_and_documented():
    s = _schemas()["job.schema.json"]
    lease = s["properties"]["lease"]
    assert "lease" not in s["required"]
    assert (lease["type"], lease["minLength"], lease["maxLength"]) == ("string", 16, 128)
    names = {p.name for p in _vectors("valid")} | {p.name for p in _vectors("invalid")}
    assert {"job.fetch-with-lease.json", "job.lease-too-short.json"} <= names
    local = LOCAL_API.read_text(encoding="utf-8")
    assert "X-Knovas-Lease" in local and "never sees it" in local
    jobs = _section(PROTOCOL_DOC.read_text(encoding="utf-8"), "Jobs and the work channel")
    assert "X-Knovas-Lease" in jobs and "Components never see the lease" in jobs


def test_source_ref_is_a_string_address():
    """An applied write's result names where the source staged it (GitHub: the pull request) as a string."""
    ref = _schemas()["job-result.schema.json"]["properties"]["source_ref"]
    assert ref["type"] == ["string", "null"] and ref["minLength"] == 1
    ok_write = _load(VECTORS / "valid" / "result.ok-write.json")["instance"]
    assert isinstance(ok_write["source_ref"], str) and ok_write["source_ref"].startswith("https://")


def test_links_own_failures_are_documented():
    text = LOCAL_API.read_text(encoding="utf-8")
    codes = _section(text, "Where each code appears")
    for code in ("invalid_job", "part_not_accepted"):
        assert re.search(rf"^\| `{code}` \| Link \| job result `failed`", codes, re.M), code


def test_result_statuses_and_fields():
    s = _schemas()["job-result.schema.json"]
    assert s["properties"]["status"]["enum"] == RESULT_STATUSES
    assert set(s["properties"]) == {"status", "code", "version", "bytes_sha256", "mime", "bytes", "parts", "source_ref",
                                    "duration_ms"}
    assert s["properties"]["bytes"]["maximum"] == MAX_FETCH_BYTES


def _required_in(node: dict, prefix: tuple, root: dict) -> set[tuple]:
    """Every required path below ``node``, following $ref and object properties (not the if/then branches)."""
    if "$ref" in node:
        node = root["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    out = {prefix + (r,) for r in node.get("required", [])}
    for name, sub in node.get("properties", {}).items():
        out |= _required_in(sub, prefix + (name,), root)
    return out


def test_required_fields_are_exactly_the_shared_contract():
    found = set()
    for name, schema in _schemas().items():
        found |= {(name, "", p) for p in _required_in(schema, (), schema)}
        for branch in schema.get("allOf", []):
            (field, cond), = branch["if"]["properties"].items()
            disc = f"{field}={cond['const']}"
            found |= {(name, disc, p) for p in _required_in(branch["then"], (), schema)}
    assert found == REQUIRED


# ------------------------------------------------------------------ vectors

@pytest.mark.parametrize("path", _vectors("valid") + _vectors("invalid"), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_vector_names_its_schema_by_its_file_name(path):
    v = _load(path)
    prefix = path.name.split(".", 1)[0]
    assert v["schema"] == SCHEMA_OF_PREFIX[prefix]
    assert set(v) == ({"schema", "description", "instance", "breaks"} if path.parent.name == "invalid"
                      else {"schema", "description", "instance"})


@pytest.mark.parametrize("path", _vectors("valid"), ids=lambda p: p.name)
def test_valid_vector_passes(path):
    v = _load(path)
    errors = list(_validator(v["schema"]).iter_errors(v["instance"]))
    assert not errors, [e.message for e in errors]


@pytest.mark.parametrize("path", _vectors("invalid"), ids=lambda p: p.name)
def test_invalid_vector_fails_naming_the_field(path):
    v = _load(path)
    errors = list(_validator(v["schema"]).iter_errors(v["instance"]))
    assert errors, "passes but should fail"
    named = {p for e in errors for p in _field_paths(e)}
    assert named == {tuple(v["breaks"])}, [(list(e.absolute_path), e.message) for e in errors]


def test_valid_vectors_cover_every_mvp_stream_type_job_kind_and_result_status():
    seen = {"stream": set(), "job": set(), "result": set()}
    for path in _vectors("valid"):
        v = _load(path)
        prefix = path.name.split(".", 1)[0]
        key = {"stream": "type", "job": "kind", "result": "status"}[prefix]
        seen[prefix].add(v["instance"][key])
    assert set(MVP_STREAM_TYPES) <= seen["stream"]
    assert seen["job"] == set(JOB_KINDS)
    assert seen["result"] == set(RESULT_STATUSES)


def test_every_required_field_has_an_invalid_vector_that_drops_it():
    covered = set()
    for path in _vectors("invalid"):
        v = _load(path)
        validator = _validator(v["schema"])
        if any(e.validator == "required" for e in validator.iter_errors(v["instance"])):
            covered.add((v["schema"], _discriminator(v["schema"], v["instance"]), tuple(v["breaks"])))
    # A top-level field is covered by a vector of any type or kind.
    covered |= {(s, "", p) for (s, _d, p) in covered}
    missing = {r for r in REQUIRED if r not in covered}
    assert not missing, sorted(missing)


def test_the_rules_the_shared_contract_names_have_invalid_vectors():
    names = {p.name for p in _vectors("invalid")}
    for wanted in ("job.write-mode-standing.json", "job.missing-actor.json", "job.fetch-max-bytes-over-25-mib.json",
                   "result.bytes-over-25-mib.json", "stream.generation-not-complete.json", "job.fetch-source-null.json",
                   "job.write-source-null.json"):
        assert wanted in names


def test_a_probe_needs_no_source_and_no_target_serial():
    """secure-api sets target_serial on the probes it sends Link; Link's local probes to a component carry neither."""
    s = _schemas()["job.schema.json"]
    probe = next(b for b in s["allOf"] if b["if"]["properties"]["kind"]["const"] == "probe")
    assert "required" not in probe["then"]
    assert "source" not in probe["then"].get("properties", {})
    local = _load(VECTORS / "valid" / "job.probe-local.json")["instance"]
    assert "source" not in local and "target_serial" not in local


def test_fetch_result_vector_is_the_git_blob_of_its_bytes():
    """The example agrees with itself: the fetched bytes' git blob hash is the stream's version and the job's."""
    doc = _load(VECTORS / "valid" / "stream.document.json")["instance"]
    job = _load(VECTORS / "valid" / "job.fetch.json")["instance"]
    result = _load(VECTORS / "valid" / "result.ok-fetch.json")["instance"]
    raw = doc["payload"]["text"].encode("utf-8")
    assert hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest() == doc["version"] == job["args"]["version"] == result["version"]
    assert hashlib.sha256(raw).hexdigest() == result["bytes_sha256"]
    assert len(raw) == result["bytes"] <= MAX_PART_BYTES and result["parts"] == 1


# ------------------------------------------------------------------ codes and lock

def test_codes_are_the_shared_list_each_with_one_line():
    codes = _load(CONTRACTS / "codes.json")
    assert set(codes) == {"codes"}
    assert [c["code"] for c in codes["codes"]] == CODES
    pattern = re.compile(_schemas()["job-result.schema.json"]["properties"]["code"]["pattern"])
    for c in codes["codes"]:
        assert set(c) == {"code", "meaning"}
        assert pattern.fullmatch(c["code"]), c["code"]
        assert c["meaning"].strip() and "\n" not in c["meaning"], c["code"]


def test_github_http_family_is_a_documented_pattern_not_a_list():
    text = LOCAL_API.read_text(encoding="utf-8")
    m = re.search(r"the pattern is `(\^github\\\.http_[^`]+)`", text)
    assert m, "local-api.md states the github.http_<status> pattern"
    family = re.compile(m.group(1))
    result_code = re.compile(_schemas()["job-result.schema.json"]["properties"]["code"]["pattern"])
    for example in ("github.http_404", "github.http_502"):
        assert family.fullmatch(example) and result_code.fullmatch(example)
    assert not family.fullmatch("github.http_42") and not family.fullmatch("github.rate_limited")
    listed = [c["code"] for c in _load(CONTRACTS / "codes.json")["codes"]]
    assert not [c for c in listed if family.fullmatch(c)], "the family is a pattern, not entries in codes.json"


def test_contracts_lock_matches_the_files():
    lock = _load(CONTRACTS / "contracts.lock")
    assert set(lock) == {"version", "files"}
    assert lock["version"] == "1.1.0"
    expected = {p.name for p in _schema_files()} | {"codes.json", "local-api.md"}
    assert set(lock["files"]) == expected
    for name, digest in lock["files"].items():
        assert hashlib.sha256((CONTRACTS / name).read_bytes()).hexdigest() == digest, f"{name}: contracts.lock is stale"


# ------------------------------------------------------------------ the texts

def test_local_api_names_every_endpoint_and_the_limits():
    text = LOCAL_API.read_text(encoding="utf-8")
    for endpoint in ("POST /local/v1/register", "POST /local/v1/heartbeat", "POST /local/v1/commands/<id>/done",
                     "GET /local/v1/config", "GET /local/v1/package", "GET /local/v1/ping",
                     "GET /local/v1/jobs?wait=25", "POST /local/v1/jobs/<id>/part?n=", "POST /local/v1/jobs/<id>/result",
                     "ANY /local/v1/brain/<path>", "POST /local/v1/report/runs", "POST /local/v1/report/events",
                     "POST /local/v1/report/checks"):
        assert endpoint in text, endpoint
    for needle in ("Authorization: Bearer", "/link/<component>/token", "X-Component", str(MAX_PART_BYTES),
                   str(MAX_FETCH_BYTES)):
        assert needle in text, needle


def test_local_api_answers_only_known_codes_and_names_every_code():
    text = LOCAL_API.read_text(encoding="utf-8")
    answers = _section(text, "Endpoints")
    named = set(re.findall(r"\d{3} `([a-z_]+)`", answers))
    known = set(CODES) | PROTOCOL_V1_REQUEST_CODES
    assert named, "the endpoint table names its codes as «<status> `code`»"
    assert named <= known, sorted(named - known)
    platform = PLATFORM_PROTOCOL.read_text(encoding="utf-8")
    for code in PROTOCOL_V1_REQUEST_CODES:
        assert re.search(rf'ProtocolError\(\d{{3}}, "{code}"', platform), f"{code} is not a protocol v1 code"
    for code in CODES:
        assert f"`{code}`" in text, f"local-api.md does not say where {code} appears"


def test_local_api_writes_down_the_control_plane_and_source_assignment():
    text = LOCAL_API.read_text(encoding="utf-8")
    for needle in ('"component": "<name>"', "never calls the Platform", "{paused, schedule, commands",
                   "{result: ok|failed|refused}", "sources assigned to this component's name", "local_ping_ms"):
        assert needle in text, needle


def test_local_api_says_which_objects_are_closed():
    section = _section(LOCAL_API.read_text(encoding="utf-8"), "Open and closed objects")
    for obj in ("stream message", "job", "actor", "args", "job result"):
        assert obj in section.lower(), obj


def test_protocol_doc_is_version_1_1_with_the_new_sections():
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    assert re.search(r"\*\*Version 1\.1\*\*", text)
    for heading in ("Sites and Knovas Link", "Streams", "Jobs and the work channel", "Probes", "Write proposals",
                    "Fetching originals"):
        assert re.search(rf"^## {re.escape(heading)}$", text, re.M), heading
    assert "DEC-0056" in _section(text, "Sites and Knovas Link")
    link = _section(text, "Sites and Knovas Link")
    assert "Direct enrolment" in link
    assert '"component": "<name>"' in link and "never calls the Platform" in link


def test_every_contract_file_the_protocol_doc_names_exists():
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    named = set(re.findall(r"KnovasLink/contracts/([A-Za-z0-9_.-]+)", text))
    assert set(SCHEMA_OF_PREFIX.values()) <= named
    for name in named:
        assert (CONTRACTS / name).is_file(), name


def test_every_job_kind_the_protocol_doc_names_exists_and_none_is_missing():
    section = _section(PROTOCOL_DOC.read_text(encoding="utf-8"), "Jobs and the work channel")
    assert _table_first_column(section) == JOB_KINDS


def test_every_stream_type_the_protocol_doc_names_exists_and_none_is_missing():
    section = _section(PROTOCOL_DOC.read_text(encoding="utf-8"), "Streams")
    assert _table_first_column(section) == STREAM_TYPES


def test_every_code_the_protocol_doc_names_is_known():
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    new = "\n".join(_section(text, h) for h in ("Sites and Knovas Link", "Streams", "Jobs and the work channel", "Probes",
                                                "Write proposals", "Fetching originals"))
    named = set(re.findall(r"\d{3} `([a-z_]+)`", new)) | set(re.findall(r"`(?:refused|conflict): ([a-z_]+)`", new))
    assert named <= set(CODES), sorted(named - set(CODES))


# ------------------------------------------------------------------ generic, for any company

def _company_specific(text: str) -> list[str]:
    return [p.pattern for p in COMPANY_SPECIFIC if p.search(text)]


@pytest.mark.parametrize("path", sorted(p for p in CONTRACTS.rglob("*") if p.is_file()) + [Path(__file__)],
                         ids=lambda p: str(p.relative_to(LINK)))
def test_contracts_name_no_particular_company(path):
    assert not _company_specific(path.read_text(encoding="utf-8")), path.name


def test_protocol_1_1_text_names_no_particular_company():
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    added = text[text.index("**Version 1.1**"):text.index("\n", text.index("**Version 1.1**"))]
    added += "\n".join(_section(text, h) for h in ("Sites and Knovas Link", "Streams", "Jobs and the work channel",
                                                   "Probes", "Write proposals", "Fetching originals"))
    assert not _company_specific(added)
