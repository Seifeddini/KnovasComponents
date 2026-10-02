"""Knovas document fields in the sync contracts (spec 3.1, WP-C).

`sources[]` items are closed (`additionalProperties: false`), so the three
per-source keys the Platform compiles -- `fields`, `field_templates` and
`metadata_fields` -- must be declared before any Platform may send them.
A body without them must validate exactly as before (an old Platform keeps
working), and the response schema must accept what a new RC answers:
`document_sync.fields_changed` and the top-level `doc_fields` summary. A
closed `document_sync` without `fields_changed` turns /sync into a 500.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest
from jsonschema import Draft202012Validator

from util.schema import validate

CONTRACTS = pathlib.Path(__file__).resolve().parents[1] / "contracts"
PLATFORM_COPY = (
    pathlib.Path(__file__).resolve().parents[2]
    / "KnovasPlatform" / "components" / "docbridge_integration"
    / "src" / "identity" / "rc_contracts" / "sync_request.schema.json"
)

SYSTEM_KEYS = ("title", "description", "path", "ingested_at", "pointer")
METADATA_ITEMS = ("language", "email_date", "email_doc_type", "email_author", "document_author")


def _body(**source_extra):
    source = {"path": "/data/Mandate", "recursive": True}
    source.update(source_extra)
    return {
        "mode": "incremental",
        "sources": [source],
        "filters": {"include_globs": ["*.pdf"]},
        "ingestion": {"identifier_prefix": "rc-sync"},
    }


def _errors(body):
    return validate(body, "sync_request.schema.json")


def _response(**extra):
    out = {
        "status": "success",
        "scheduler_status": "idle",
        "files_scanned": 3,
        "files_uploaded": 2,
        "files_skipped": 1,
        "ingestion_requests_sent": 2,
        "transmissions": [{"path": "a.pdf", "status": "ok", "parts": 1}],
        "errors": [],
    }
    out.update(extra)
    return out


class TestEverySchemaIsASchema:
    def test_ci_glob_sees_only_schemas(self):
        # Mirrors the CI step: every contracts/*.json (not recursive) must be
        # a schema, which is why the template vectors live in a subfolder.
        names = sorted(p.name for p in CONTRACTS.glob("*.json"))
        assert "sync_request.schema.json" in names
        for path in CONTRACTS.glob("*.json"):
            Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


class TestRequestBackwardCompatible:
    def test_body_without_new_keys_still_validates(self):
        assert _errors(_body()) == []

    def test_access_groups_body_still_validates(self):
        assert _errors(_body(access_groups=["litigation"])) == []

    def test_unknown_source_key_is_still_refused(self):
        assert _errors(_body(field_defaults={"doc_type": "invoice"}))


class TestRequestAcceptsFields:
    def test_static_fields_of_every_scalar_kind(self):
        body = _body(fields={
            "doc_type": "invoice",
            "mandant": "Muster AG",
            "amount": "CHF 1234.50",
            "fiscal_year": 2024,
            "ratio": 0.5,
            "privileged": True,
            "legal_area": ["corporate", "tax"],
        })
        assert _errors(body) == []

    def test_templates_and_metadata_items(self):
        body = _body(
            field_templates=["{mandant}/{period}/**", "Archiv/*/{period}"],
            metadata_fields=list(METADATA_ITEMS),
        )
        assert _errors(body) == []

    def test_limits_are_inclusive(self):
        body = _body(
            fields={f"k{i}": "x" * 256 for i in range(64)},
            field_templates=["t" * 512] * 8,
        )
        body["sources"][0]["fields"]["k0"] = ["v"] * 32
        assert _errors(body) == []

    def test_empty_containers_are_allowed(self):
        # The Platform omits empty keys; the schema does not forbid them.
        assert _errors(_body(fields={}, field_templates=[], metadata_fields=[])) == []


class TestRequestRefusesBadFields:
    @pytest.mark.parametrize("key", SYSTEM_KEYS)
    def test_system_keys(self, key):
        assert _errors(_body(fields={key: "x"}))

    @pytest.mark.parametrize("key", ["Mandant", "1period", "doc-type", "", "a" * 65, "doc type"])
    def test_keys_outside_the_registry_pattern(self, key):
        assert _errors(_body(fields={key: "x"}))

    def test_more_than_64_keys(self):
        assert _errors(_body(fields={f"k{i}": "x" for i in range(65)}))

    def test_value_over_256_characters(self):
        assert _errors(_body(fields={"doc_type": "x" * 257}))

    def test_list_item_over_256_characters(self):
        assert _errors(_body(fields={"keywords": ["ok", "x" * 257]}))

    def test_empty_string(self):
        assert _errors(_body(fields={"doc_type": ""}))

    def test_more_than_32_items(self):
        assert _errors(_body(fields={"keywords": ["x"] * 33}))

    def test_empty_list(self):
        assert _errors(_body(fields={"keywords": []}))

    @pytest.mark.parametrize("value", [
        {"amount": "1234.50", "currency": "CHF"},
        {"node_id": "00000000-0000-0000-0000-000000000001"},
        [{"name": "Muster AG"}],
        [["nested"]],
        None,
    ])
    def test_objects_nested_lists_and_null(self, value):
        assert _errors(_body(fields={"mandant": value}))

    def test_fields_not_an_object(self):
        assert _errors(_body(fields=["doc_type"]))

    def test_more_than_8_templates(self):
        assert _errors(_body(field_templates=["{a}/**"] * 9))

    def test_template_over_512_characters(self):
        assert _errors(_body(field_templates=["t" * 513]))

    def test_empty_template(self):
        assert _errors(_body(field_templates=[""]))

    @pytest.mark.parametrize("item", ["email_message_id", "sender", "recipients", "created", "LANGUAGE"])
    def test_unknown_metadata_items(self, item):
        assert _errors(_body(metadata_fields=[item]))

    def test_duplicate_metadata_items(self):
        assert _errors(_body(metadata_fields=["language", "language"]))


class TestIngestionUnchanged:
    def test_metadata_fields_stay_per_source(self):
        # An older Platform would crash on new top-level profile keys, so the
        # new keys live inside sources[] only (spec 2.5, Platform downgrade).
        body = _body()
        body["ingestion"]["metadata_fields"] = ["language"]
        assert _errors(body)
        schema = json.loads((CONTRACTS / "sync_request.schema.json").read_text(encoding="utf-8"))
        assert set(schema["properties"]["ingestion"]["properties"]) == {
            "identifier_prefix", "part_max_chars", "description", "delete_on_remove",
        }


class TestPlatformCopy:
    def test_platform_copy_is_byte_identical(self):
        if not PLATFORM_COPY.is_file():
            pytest.skip("KnovasPlatform is not in this checkout")
        assert PLATFORM_COPY.read_bytes() == (CONTRACTS / "sync_request.schema.json").read_bytes()


class TestResponse:
    def test_response_without_new_keys_still_validates(self):
        assert validate(_response(), "sync_response.schema.json") == []

    def test_document_sync_with_fields_changed(self):
        body = _response(document_sync={
            "total": 10, "synced": 6, "pending": 1, "modified": 1,
            "excluded_max_age": 0, "fields_changed": 2,
        })
        assert validate(body, "sync_response.schema.json") == []

    def test_fields_changed_must_be_a_count(self):
        body = _response(document_sync={"fields_changed": -1})
        assert validate(body, "sync_response.schema.json")

    def test_document_sync_stays_closed(self):
        body = _response(document_sync={"total": 1, "fields_drift": 1})
        assert validate(body, "sync_response.schema.json")

    def test_doc_fields_summary_and_transmission_fields(self):
        body = _response(
            doc_fields={
                "staged": 1, "not_accepted": 0, "cleared": 0, "none": 1,
                "refused": {"unknown_field": 1}, "rel_collisions": 0,
                "warnings": {"unresolved_entity": 1},
            },
        )
        tx = copy.deepcopy(body["transmissions"][0])
        tx["fields"] = {"outcome": "staged", "staged": 2, "warning_codes": ["unresolved_entity"]}
        body["transmissions"] = [tx]
        assert validate(body, "sync_response.schema.json") == []

    def test_doc_fields_must_be_an_object(self):
        assert validate(_response(doc_fields=["staged"]), "sync_response.schema.json")
