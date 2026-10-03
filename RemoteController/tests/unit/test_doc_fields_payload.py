"""The upload ``fields`` library (spec sections 3.3, 3.6 and 3.7).

Pure functions only: assembling the payload with its precedence and caps,
the config digest, the init echo, refusal classification and the outcome
records the state DB stores.
"""
import ast
import inspect
import json
import logging
import pickle
from collections import Counter
from pathlib import Path

import pytest

import sync.doc_fields_payload as payload_mod
from sync import metadata_fields
from sync.doc_fields_payload import (
    ASSERTION_REJECTED,
    DROP_REASONS,
    EMPTY_SOURCE_SPEC,
    FIELD_REFUSAL_CODES,
    FIELD_SHAPE_CODES,
    FIELDS_PAYLOAD_VERSION,
    MAX_KEYS,
    MAX_PAYLOAD_BYTES,
    REFUSAL_OTHER,
    REUPLOAD_FAILURE_CLASSES,
    TRANSIENT_REFUSAL_CODES,
    FieldsEcho,
    FieldsOutcome,
    FieldsRecord,
    Payload,
    SourceSpec,
    TemplateError,
    assemble,
    classify_init_refusal,
    config_digest,
    encoded_size,
    fields_to_send,
    is_doc_fields_unavailable,
    is_transient_refusal,
    outcome_after_init,
    parse_init_echo,
    record_for,
    refused_outcome,
    reupload_failed_record,
    spec_from_source,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "sync"
NEW_MODULES = ("doc_fields_payload.py", "field_templates.py", "metadata_fields.py")

MAIL_MD = {"author": "Muster AG <info@muster.example>", "created": "2024-03-15T10:22:00+01:00"}


def _spec(**source):
    return spec_from_source({"path": "/data/Mandate", **source})


# --- spec_from_source ------------------------------------------------------------


def test_spec_from_a_source_without_fields_is_empty():
    spec = spec_from_source({"path": "/data", "access_groups": ["grp-a"]})
    assert spec.access_groups == ("grp-a",)
    assert not spec.has_fields
    assert spec == SourceSpec(access_groups=("grp-a",))
    assert not EMPTY_SOURCE_SPEC.has_fields
    assert spec_from_source(None) == EMPTY_SOURCE_SPEC


def test_spec_from_source_reads_all_three_keys():
    spec = _spec(
        fields={"doc_type": "invoice", "legal_area": ["corporate", "tax"]},
        field_templates=["{mandant}/{period}/**", "{mandant}/**"],
        metadata_fields=["email_date", "email_doc_type"],
    )
    assert spec.fields == {"doc_type": "invoice", "legal_area": ("corporate", "tax")}
    assert [t.source for t in spec.templates] == ["{mandant}/{period}/**", "{mandant}/**"]
    assert spec.metadata_fields == frozenset({"email_date", "email_doc_type"})
    assert spec.has_fields


def test_spec_from_source_ignores_unknown_metadata_items():
    spec = _spec(metadata_fields=["email_message_id", "language"])
    assert spec.metadata_fields == frozenset({"language"})


def test_bad_template_raises_with_its_code():
    with pytest.raises(TemplateError) as exc:
        _spec(field_templates=["{mandant}/**", "{title}/**"])
    assert exc.value.code == "system_key"


def test_templates_that_are_not_a_list_are_a_syntax_error():
    with pytest.raises(TemplateError) as exc:
        _spec(field_templates="{mandant}/**")
    assert exc.value.code == "syntax"


def test_spec_is_hashable_and_picklable():
    spec = _spec(access_groups=["g"], fields={"doc_type": "invoice"}, field_templates=["{mandant}/**"])
    hash(spec)
    clone = pickle.loads(pickle.dumps(spec))
    assert clone == spec
    assert config_digest("Muster AG/a.pdf", clone) == config_digest("Muster AG/a.pdf", spec)


# --- assemble: precedence ----------------------------------------------------------


def test_capture_beats_static_beats_metadata():
    spec = _spec(
        fields={"doc_type": "invoice", "author": "Beispiel GmbH"},
        field_templates=["{doc_type}/**"],
        metadata_fields=["email_doc_type", "email_author", "email_date"],
    )
    result = assemble("contract/Brief.eml", spec, MAIL_MD, ".eml")
    assert result.values == {
        "doc_type": "contract",                          # capture over static and metadata
        "author": "Beispiel GmbH",                       # static over metadata
        "document_date": "2024-03-15T10:22:00+01:00",    # metadata alone
    }
    assert result.dropped == Counter()


def test_empty_capture_does_not_shadow_the_static_value():
    spec = _spec(fields={"period": "GJ 2024"}, field_templates=["{mandant}/{period}/**"])
    result = assemble("Muster AG/   /Rechnung.pdf", spec, {}, ".pdf")
    assert result.values == {"mandant": "Muster AG", "period": "GJ 2024"}


def test_a_dropped_capture_never_lets_the_static_value_through():
    spec = _spec(fields={"mandant": "Beispiel GmbH"}, field_templates=["{mandant}/**"])
    result = assemble(("M" * 300) + "/Rechnung.pdf", spec, {}, ".pdf")
    assert result.values == {}
    assert result.dropped == Counter({"value_too_long": 1})


def test_template_captures_are_sent_as_strings():
    spec = _spec(field_templates=["{mandant}/{period}/**"])
    result = assemble("Muster AG/GJ 2024/Belege/Rechnung_17.pdf", spec, {}, ".pdf")
    assert result.values == {"mandant": "Muster AG", "period": "GJ 2024"}


# --- assemble: nothing applicable ---------------------------------------------------------


def test_nothing_configured_gives_an_empty_payload():
    assert assemble("a/b.pdf", EMPTY_SOURCE_SPEC, MAIL_MD, ".pdf") == Payload({}, Counter())
    assert assemble("a/b.pdf", None, MAIL_MD, ".pdf").values == {}


def test_pdf_in_an_email_metadata_only_source_gives_an_empty_payload():
    spec = _spec(metadata_fields=["email_date", "email_doc_type", "email_author"])
    result = assemble("Postfach/Rechnung.pdf", spec, {"author": "Muster AG", "created": "2024-01-01"}, ".pdf")
    assert result.values == {}
    assert result.dropped == Counter()


def test_a_path_no_template_matches_gives_an_empty_payload():
    spec = _spec(field_templates=["{mandant}/{period}/**"])
    assert assemble("Rechnung.pdf", spec, {}, ".pdf").values == {}
    assert assemble("Muster AG/Rechnung.pdf", spec, {}, ".pdf").values == {}


def test_missing_source_metadata_is_tolerated():
    spec = _spec(metadata_fields=["email_doc_type", "email_date"])
    assert assemble("Postfach/a.eml", spec, None, ".eml").values == {"doc_type": "correspondence.email"}


# --- assemble: drops and caps -------------------------------------------------------------


@pytest.mark.parametrize("key", ["title", "description", "path", "ingested_at", "pointer"])
def test_system_keys_are_dropped(key):
    result = assemble("a/b.pdf", SourceSpec(fields={key: "x", "doc_type": "invoice"}), {}, ".pdf")
    assert result.values == {"doc_type": "invoice"}
    assert result.dropped == Counter({"system_key": 1})


def test_system_key_check_folds_the_key():
    result = assemble("a/b.pdf", SourceSpec(fields={"Title": "x", "Ingested-At": "y"}), {}, ".pdf")
    assert result.values == {}
    assert result.dropped == Counter({"system_key": 2})


def test_strings_over_256_characters_are_dropped():
    spec = SourceSpec(fields={"note": "x" * 257, "ok": "x" * 256, "tags": ("a", "b" * 257, "c")})
    result = assemble("a/b.pdf", spec, {}, ".pdf")
    assert result.values == {"ok": "x" * 256, "tags": ["a", "c"]}
    assert result.dropped == Counter({"value_too_long": 2})


def test_long_metadata_values_are_dropped():
    spec = _spec(metadata_fields=["document_author"])
    result = assemble("a/b.docx", spec, {"author": "A" * 300}, ".docx")
    assert result.values == {}
    assert result.dropped == Counter({"value_too_long": 1})


def test_lists_over_32_items_are_dropped_whole():
    spec = SourceSpec(fields={"legal_area": tuple(f"a{i}" for i in range(33)), "tags": tuple("abc")})
    result = assemble("a/b.pdf", spec, {}, ".pdf")
    assert result.values == {"tags": ["a", "b", "c"]}
    assert result.dropped == Counter({"cap_exceeded": 1})
    ok = assemble("a/b.pdf", SourceSpec(fields={"x": tuple(f"a{i}" for i in range(32))}), {}, ".pdf")
    assert len(ok.values["x"]) == 32


def test_invalid_values_are_dropped():
    spec = SourceSpec(
        fields={"a": "", "b": "  ", "c": (), "d": {"x": 1}, "e": float("nan"), "f": None, "g": ("ok", None)}
    )
    result = assemble("a/b.pdf", spec, {}, ".pdf")
    assert result.values == {"g": ["ok"]}
    assert result.dropped == Counter({"invalid_value": 7})


def test_entity_values_never_go_as_node_ids():
    # D4: the RC sends names; an object (a {"node_id"} or {"name"}) never
    # reaches the wire, whatever the configuration says.
    spec = SourceSpec(fields={"mandant": {"node_id": "0b6f"}, "client": ({"name": "Muster AG"}, "Muster AG")})
    result = assemble("a/b.pdf", spec, {}, ".pdf")
    assert result.values == {"client": ["Muster AG"]}
    assert result.dropped == Counter({"invalid_value": 2})


def test_windows_separators_in_rel():
    spec = _spec(field_templates=["{mandant}/{period}/**"])
    assert assemble("Muster AG\\GJ 2024\\Rechnung.pdf", spec, {}, ".pdf").values == {
        "mandant": "Muster AG", "period": "GJ 2024",
    }


def test_scalars_keep_their_json_type():
    spec = SourceSpec(fields={"privileged": True, "count": 3, "amount": 12.5, "doc_type": "invoice"})
    assert assemble("a/b.pdf", spec, {}, ".pdf").values == {
        "amount": 12.5, "count": 3, "doc_type": "invoice", "privileged": True,
    }


def test_keys_outside_the_key_pattern_are_dropped():
    result = assemble("a/b.pdf", SourceSpec(fields={"Doc Type": "invoice", "1x": "a"}), {}, ".pdf")
    assert result.values == {}
    assert result.dropped == Counter({"invalid_value": 2})


def test_more_than_64_keys_drop_the_lowest_precedence_first():
    static = {f"s{i:02d}": "v" for i in range(60)}
    templates = "/".join("{c%d}" % i for i in range(6)) + "/**"
    spec = _spec(fields=static, field_templates=[templates], metadata_fields=["email_doc_type", "email_date"])
    rel = "/".join(f"seg{i}" for i in range(6)) + "/mail.eml"
    result = assemble(rel, spec, MAIL_MD, ".eml")
    # 6 captures + 60 static + 2 metadata = 68: both metadata keys go, then the
    # two last static keys.
    assert len(result.values) == MAX_KEYS
    assert "doc_type" not in result.values and "document_date" not in result.values
    assert "s59" not in result.values and "s58" not in result.values and "s57" in result.values
    assert all(f"c{i}" in result.values for i in range(6))
    assert result.dropped == Counter({"cap_exceeded": 4})


def test_more_than_64_captures_alone_are_capped():
    template = "/".join("{k%02d}" % i for i in range(70))
    rel = "/".join(f"d{i}" for i in range(70)) + "/a.pdf"
    result = assemble(rel, _spec(field_templates=[template]), {}, ".pdf")
    assert len(result.values) == MAX_KEYS
    assert "k69" not in result.values and "k00" in result.values
    assert result.dropped == Counter({"cap_exceeded": 6})


def test_payload_over_16_kib_drops_metadata_then_static_then_captures():
    # 22 static values of 256 three-byte characters (776 bytes per entry)
    # plus a 250-character metadata author: over 16384 bytes by about 1.4 KB.
    static = {f"s{i:02d}": "\u20ac" * 256 for i in range(22)}
    spec = _spec(
        fields=static,
        field_templates=["{mandant}/**"],
        metadata_fields=["email_author"],
    )
    md = {"author": "\u20ac" * 250}
    result = assemble("Muster AG/a.eml", spec, md, ".eml")
    assert encoded_size(result.values) <= MAX_PAYLOAD_BYTES
    assert "author" not in result.values           # metadata went first
    assert result.values["mandant"] == "Muster AG"  # the capture stayed
    assert result.dropped["too_large"] >= 2
    dropped_static = [k for k in static if k not in result.values]
    assert len(dropped_static) == result.dropped["too_large"] - 1


def test_size_cap_uses_the_servers_serialisation():
    values = {"a": "\u00e4" * 3, "b": [1, True]}
    expected = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert encoded_size(values) == len(expected)


def test_captures_are_dropped_last_for_size():
    template = "/".join("{k%02d}" % i for i in range(64)) + "/**"
    rel = "/".join("\u20ac" * 100 for _ in range(64)) + "/a.pdf"
    result = assemble(rel, _spec(field_templates=[template]), {}, ".pdf")
    assert encoded_size(result.values) <= MAX_PAYLOAD_BYTES
    assert 0 < len(result.values) < 64
    assert set(result.dropped) == {"too_large"}


def test_drop_reasons_are_a_closed_set():
    assert DROP_REASONS == {"system_key", "value_too_long", "cap_exceeded", "too_large", "invalid_value"}


def test_assemble_output_is_json_and_sorted():
    spec = _spec(fields={"z": ("a",), "b": "x"}, field_templates=["{m}/**"])
    values = assemble("Muster AG/a.pdf", spec, {}, ".pdf").values
    assert list(values) == sorted(values)
    assert json.loads(json.dumps(values)) == values


# --- config_digest -------------------------------------------------------------------------


def test_digest_is_empty_without_configuration():
    assert config_digest("a/b.pdf", EMPTY_SOURCE_SPEC) == ""
    assert config_digest("a/b.pdf", None) == ""
    assert config_digest("a/b.pdf", SourceSpec(access_groups=("g",))) == ""


def test_digest_is_empty_when_no_template_matches_and_nothing_else_is_set():
    spec = _spec(field_templates=["{mandant}/{period}/**"])
    assert config_digest("Brief.pdf", spec) == ""
    assert config_digest("Muster AG/GJ 2024/Brief.pdf", spec) != ""


def test_digest_is_stable():
    a = _spec(fields={"doc_type": "invoice", "legal_area": ["tax", "corporate"]}, metadata_fields=["language", "email_date"])
    b = _spec(fields={"legal_area": ["tax", "corporate"], "doc_type": "invoice"}, metadata_fields=["email_date", "language"])
    assert config_digest("x/y.pdf", a) == config_digest("x/y.pdf", b)
    digest = config_digest("x/y.pdf", a)
    assert len(digest) == 64 and int(digest, 16) >= 0


def test_digest_changes_with_the_configuration():
    base = _spec(fields={"doc_type": "invoice"}, field_templates=["{mandant}/**"], metadata_fields=["language"])
    rel = "Muster AG/a.pdf"
    digest = config_digest(rel, base)
    assert config_digest(rel, _spec(fields={"doc_type": "receipt"}, field_templates=["{mandant}/**"], metadata_fields=["language"])) != digest
    assert config_digest(rel, _spec(fields={"doc_type": "invoice"}, field_templates=["{client}/**"], metadata_fields=["language"])) != digest
    assert config_digest(rel, _spec(fields={"doc_type": "invoice"}, field_templates=["{mandant}/**"], metadata_fields=["language", "email_date"])) != digest
    assert config_digest("Beispiel GmbH/a.pdf", base) != digest


def test_digest_ignores_template_changes_that_keep_this_paths_captures():
    rel = "Muster AG/a.pdf"
    a = _spec(field_templates=["{mandant}/**"])
    b = _spec(field_templates=["Kunden/{x}/**", "{mandant}/**"])
    assert config_digest(rel, a) == config_digest(rel, b)


def test_digest_ignores_extractor_values_and_access_groups():
    spec = _spec(metadata_fields=["email_date"])
    with_groups = _spec(metadata_fields=["email_date"], access_groups=["g"])
    assert config_digest("a/b.eml", spec) == config_digest("a/b.eml", with_groups)
    assert list(inspect.signature(config_digest).parameters) == ["rel", "spec"]


def test_digest_follows_both_rule_versions(monkeypatch):
    spec = _spec(metadata_fields=["email_date"])
    before = config_digest("a/b.eml", spec)
    monkeypatch.setattr(metadata_fields, "METADATA_MAPPING_VERSION", 2)
    assert config_digest("a/b.eml", spec) != before
    monkeypatch.setattr(metadata_fields, "METADATA_MAPPING_VERSION", 1)
    monkeypatch.setattr(payload_mod, "FIELDS_PAYLOAD_VERSION", FIELDS_PAYLOAD_VERSION + 1)
    assert config_digest("a/b.eml", spec) != before


def test_digest_is_non_empty_for_a_metadata_source_even_when_the_payload_is_empty():
    # Stored for every outcome (section 3.7): a PDF in an e-mail-only source
    # must not look changed again on the next cycle.
    spec = _spec(metadata_fields=["email_date"])
    assert assemble("a/b.pdf", spec, {}, ".pdf").values == {}
    assert config_digest("a/b.pdf", spec) != ""


# --- fields_to_send ------------------------------------------------------------------------


def test_fields_to_send_truth_table():
    filled = Payload({"doc_type": "invoice"}, Counter())
    empty = Payload({}, Counter())
    assert fields_to_send(filled, False) == {"doc_type": "invoice"}
    assert fields_to_send(filled, True) == {"doc_type": "invoice"}
    assert fields_to_send(empty, True) == {}
    assert fields_to_send(empty, False) is None


# --- parse_init_echo -------------------------------------------------------------------------


def test_echo_present():
    body = {
        "status": "success",
        "transmission_key_id": "k",
        "fields": {
            "staged": 3,
            "mapped_keys": {"Mandant": "mandant"},
            "unknown_keys": ["mandat", "mandat", "Bad Key"],
            "warnings": [
                {"key": "mandant", "path": "fields.mandant", "code": "unresolved_entity"},
                {"key": "period", "path": "fields.period", "code": "ambiguous_date"},
                {"key": "x", "path": "fields.x", "code": "unresolved_entity"},
                {"key": "y", "path": "fields.y", "code": "Muster AG"},
                "not-a-dict",
            ],
            "suggest": {"mandat": ["mandant", "mandate", "mandat_nr", "fourth"], "Bad Key": ["x"]},
        },
    }
    echo = parse_init_echo(body)
    assert echo == FieldsEcho(
        staged=3,
        unknown_keys=("mandat",),
        warning_codes=Counter({"unresolved_entity": 2, "ambiguous_date": 1, "other": 1}),
        suggest={"mandat": ("mandant", "mandate", "mandat_nr")},
    )


def test_echo_minimal():
    echo = parse_init_echo({"transmission_key_id": "k", "fields": {"staged": 0}})
    assert echo == FieldsEcho(staged=0)


def test_echo_absent():
    assert parse_init_echo({"status": "success", "transmission_key_id": "k"}) is None
    assert parse_init_echo({}) is None
    assert parse_init_echo(None) is None


@pytest.mark.parametrize(
    "fields",
    [None, "staged", [], {"unknown_keys": []}, {"staged": True}, {"staged": -1}, {"staged": "3"}, {"staged": 1.0}],
)
def test_echo_malformed_reads_as_no_echo(fields):
    assert parse_init_echo({"transmission_key_id": "k", "fields": fields}) is None


def test_echo_tolerates_malformed_sub_keys():
    echo = parse_init_echo({"fields": {"staged": 1, "unknown_keys": "mandat", "warnings": {}, "suggest": []}})
    assert echo == FieldsEcho(staged=1)


# --- classify_init_refusal -------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 422])
@pytest.mark.parametrize("code", sorted(FIELD_SHAPE_CODES))
def test_field_shape_codes_are_refusals(status, code):
    assert classify_init_refusal(status, {"status": "error", "error_code": code}) == code


@pytest.mark.parametrize("path", ["fields", "fields.mandant", "fields.mandant[2]", "fields_mode", "fields_strict"])
def test_a_fields_path_is_a_refusal(path):
    assert classify_init_refusal(400, {"error_code": "invalid_fields", "path": path}) == "invalid_fields"
    assert classify_init_refusal(400, {"error_code": "invalid_value", "path": path}) == REFUSAL_OTHER
    assert classify_init_refusal(422, {"path": path}) == REFUSAL_OTHER


@pytest.mark.parametrize(
    "status, body",
    [
        (400, {"status": "error", "error": "Title too long", "type": "validation_error"}),
        (400, {"error_code": "invalid_value", "path": "access_groups"}),
        (400, {"error_code": "invalid_value", "path": "title"}),
        (422, {"error_code": "key_looks_personal"}),
        (400, None),
        (400, "not json"),
    ],
)
def test_other_4xx_are_not_field_refusals(status, body):
    assert classify_init_refusal(status, body) is None


@pytest.mark.parametrize("code", sorted(TRANSIENT_REFUSAL_CODES))
def test_transient_503(code):
    assert classify_init_refusal(503, {"error_code": code}) == code
    assert is_transient_refusal(code)


def test_other_503_is_not_a_field_refusal():
    assert classify_init_refusal(503, {"error_code": "TRANSMISSION_INIT_FAILED"}) is None
    assert classify_init_refusal(503, {}) is None


def test_assertion_rejected_401():
    assert classify_init_refusal(401, {"error_code": "assertion_rejected"}) == ASSERTION_REJECTED
    assert classify_init_refusal(401, {"error_code": "UNAUTHORIZED"}) is None
    assert classify_init_refusal(403, {"error_code": "assertion_rejected"}) is None


@pytest.mark.parametrize("status", [404, 409, 429, 500, 502, 504])
def test_other_statuses_are_not_field_refusals(status):
    assert classify_init_refusal(status, {"error_code": "invalid_fields", "path": "fields"}) is None


def test_transient_versus_permanent():
    assert TRANSIENT_REFUSAL_CODES == {"doc_fields_ingest_unavailable", "doc_fields_unavailable"}
    for code in FIELD_SHAPE_CODES | {ASSERTION_REJECTED, REFUSAL_OTHER}:
        assert not is_transient_refusal(code)
    assert not is_transient_refusal(None)


def test_classification_only_returns_closed_codes():
    seen = set()
    for status in (400, 401, 422, 503):
        for code in sorted(FIELD_REFUSAL_CODES) + ["Muster AG", "", "invalid_value"]:
            for path in (None, "fields", "title"):
                body = {"error_code": code}
                if path:
                    body["path"] = path
                found = classify_init_refusal(status, body)
                if found is not None:
                    seen.add(found)
    assert seen <= FIELD_REFUSAL_CODES


def test_doc_fields_unavailable_fast_fail():
    assert is_doc_fields_unavailable(503, {"error_code": "doc_fields_unavailable"})
    assert is_doc_fields_unavailable(503, {"error_code": "doc_fields_ingest_unavailable"})
    assert not is_doc_fields_unavailable(503, {"error_code": "TRANSMISSION_INIT_FAILED"})
    assert not is_doc_fields_unavailable(400, {"error_code": "doc_fields_unavailable"})
    assert not is_doc_fields_unavailable(503, None)


# --- outcomes and records ------------------------------------------------------------------


def test_outcome_none_when_no_fields_key_was_sent():
    out = outcome_after_init(None, None, "d1")
    assert out == FieldsOutcome("none", digest="d1")
    assert not out.fields_sent


def test_outcome_staged():
    echo = FieldsEcho(2, ("mandat",), Counter({"unresolved_entity": 1}), {"mandat": ("mandant",)})
    out = outcome_after_init({"mandant": "Muster AG", "mandat": "x"}, echo, "d1")
    assert out.outcome == "staged"
    assert out.staged == 2
    assert out.unknown_keys == ("mandat",)
    assert out.suggest == {"mandat": ("mandant",)}
    assert out.as_tx_entry() == {"outcome": "staged", "staged": 2, "warning_codes": ["unresolved_entity"]}


def test_outcome_not_accepted_without_echo():
    assert outcome_after_init({"doc_type": "invoice"}, None, "d1").outcome == "not_accepted"


def test_clear_is_cleared_only_when_echoed():
    assert outcome_after_init({}, FieldsEcho(0), "").outcome == "cleared"
    # Without an echo nothing was cleared; it is repeated once accepted (H7).
    assert outcome_after_init({}, None, "").outcome == "not_accepted"


def test_refused_outcomes():
    permanent = refused_outcome("unknown_field", "d1")
    assert permanent.outcome == "refused:unknown_field"
    assert permanent.refusal_code == "unknown_field"
    assert not permanent.transient
    transient = refused_outcome("doc_fields_unavailable", "d1")
    assert transient.transient
    assert refused_outcome("Muster AG", "d1").outcome == "refused:other"


@pytest.mark.parametrize(
    "outcome, expected",
    [
        (FieldsOutcome("staged", 3, Counter({"b": 1, "a": 2}), digest="d"), FieldsRecord("d", "staged", True, ("a", "b"))),
        (FieldsOutcome("cleared", digest="d"), FieldsRecord("d", "cleared", False)),
        (FieldsOutcome("not_accepted", digest="d"), FieldsRecord("d", "not_accepted", None)),
        (FieldsOutcome("none", digest="d"), FieldsRecord("d", "none", None)),
        (FieldsOutcome("refused:unknown_field", digest="d"), FieldsRecord("d", "refused:unknown_field", None)),
        (FieldsOutcome("refused:assertion_rejected", digest="d"), FieldsRecord("d", "refused:assertion_rejected", None)),
        (
            FieldsOutcome("refused:doc_fields_unavailable", digest="d", transient=True),
            FieldsRecord(None, "refused:doc_fields_unavailable", None, count_attempt=True),
        ),
    ],
)
def test_record_per_outcome(outcome, expected):
    assert record_for(outcome) == expected


def test_record_for_refuses_unknown_outcomes():
    with pytest.raises(ValueError):
        record_for(FieldsOutcome("stored"))


def test_reupload_failed_record():
    assert reupload_failed_record("d", "init_403") == FieldsRecord("d", "reupload_failed:init_403")
    assert reupload_failed_record("d", "Muster AG").outcome == "reupload_failed:other"
    assert REUPLOAD_FAILURE_CLASSES == {
        "init_401", "init_403", "init_4xx", "init_5xx", "fields_unavailable", "extract", "other",
    }


def test_warning_codes_json():
    assert FieldsRecord("d", "staged", True, ("a", "b")).warning_codes_json() == '["a", "b"]'
    assert FieldsRecord("d", "none").warning_codes_json() == "[]"


def test_log_line_has_codes_and_counts_only():
    out = outcome_after_init(
        {"mandant": "Muster AG"}, FieldsEcho(1, (), Counter({"unresolved_entity": 2})), "d"
    )
    assert out.log_line() == "doc_fields outcome=staged staged=1 warnings=2"


# --- privacy and hygiene -----------------------------------------------------------------------


def test_library_never_logs_values(caplog):
    sentinel = "Sentinel Muster AG 4711"
    spec = _spec(
        fields={"mandant": sentinel},
        field_templates=["{client}/{period}/**"],
        metadata_fields=list(metadata_fields.METADATA_ITEMS),
    )
    md = {"author": sentinel, "created": sentinel, "language": "de"}
    with caplog.at_level(logging.DEBUG):
        rel = f"{sentinel}/{sentinel}/x.eml"
        payload = assemble(rel, spec, md, ".eml")
        config_digest(rel, spec)
        echo = parse_init_echo({"fields": {"staged": 1, "warnings": [{"key": "mandant", "code": sentinel}]}})
        out = outcome_after_init(payload.values, echo, config_digest(rel, spec))
        record_for(out)
        classify_init_refusal(400, {"error_code": sentinel, "path": "fields." + sentinel})
    assert payload.values["mandant"] == sentinel
    assert caplog.records == []
    assert sentinel not in out.log_line()
    assert sentinel not in json.dumps(out.as_tx_entry())
    assert sentinel not in record_for(out).warning_codes_json()


@pytest.mark.parametrize("name", NEW_MODULES)
def test_new_modules_are_ascii_and_import_no_http_client(name):
    source = (SRC / name).read_text(encoding="utf-8")
    assert source.isascii(), name
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"requests", "urllib3", "http", "socket", "logging"}, name


def test_new_test_files_are_ascii():
    tests = Path(__file__).resolve().parent
    for name in (
        "test_field_templates.py",
        "test_metadata_fields.py",
        "test_doc_fields_payload.py",
        "test_document_text_source_metadata.py",
    ):
        assert (tests / name).read_text(encoding="utf-8").isascii(), name


def test_library_limits_match_the_sync_request_schema():
    schema_path = Path(__file__).resolve().parents[2] / "contracts" / "sync_request.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    source = schema["properties"]["sources"]["items"]["properties"]
    if "fields" not in source:
        pytest.skip("sync_request.schema.json predates the doc-fields keys")
    from sync.field_templates import KEY_RE, MAX_TEMPLATE_CHARS, SYSTEM_KEYS

    names = source["fields"]["propertyNames"]
    assert names["pattern"] == KEY_RE.pattern
    assert set(names["not"]["enum"]) == SYSTEM_KEYS
    assert source["fields"]["maxProperties"] == MAX_KEYS
    assert source["field_templates"]["items"]["maxLength"] == MAX_TEMPLATE_CHARS
    assert set(source["metadata_fields"]["items"]["enum"]) == set(metadata_fields.METADATA_ITEMS)
    scalar_string = schema["$defs"]["fieldScalar"]["oneOf"][0]
    assert scalar_string["maxLength"] == payload_mod.MAX_VALUE_CHARS
    as_list = schema["$defs"]["fieldValue"]["oneOf"][1]
    assert as_list["maxItems"] == payload_mod.MAX_VALUES_PER_KEY
