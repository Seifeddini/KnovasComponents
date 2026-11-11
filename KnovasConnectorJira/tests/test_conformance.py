"""J-1, J-2: the connector kit's conformance suite against the Jira connector over a fake Jira. The read properties
(replay_changes_nothing, deletion_guard, no_credential_leaves_site) and the write properties (preview_equals_receipt,
exactly_once, permit_refusals) pass; review_outcome is not applicable (no native_review). The declaration names
exactly what passed (SPEC-write-back-12, -14; GI-CONN-08)."""
from __future__ import annotations

from harness import make_harness
from knovas_connector_jira.connector import CONFORMANCE_PASSED
from knovas_connector_kit import contracts
from knovas_connector_kit.conformance import READ_PROPERTIES, WRITE_PROPERTIES, run


def test_the_suite_passes_every_read_and_write_property():
    report = run(make_harness)
    assert report["failed"] == {}
    assert set(READ_PROPERTIES) | set(WRITE_PROPERTIES) <= set(report["passed"])
    assert report["not_applicable"] == ["review_outcome"]


def test_the_declaration_names_exactly_what_passed():
    report = run(make_harness)
    declaration = make_harness().connector.declaration()
    assert contracts.valid("declaration.schema.json", declaration)
    assert sorted(declaration["conformance"]["passed"]) == sorted(report["passed"]) == sorted(CONFORMANCE_PASSED)


def test_the_report_is_reproducible():
    assert run(make_harness)["report_sha256"] == run(make_harness)["report_sha256"]
