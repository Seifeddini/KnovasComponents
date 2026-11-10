"""The conformance suite catches each mutant, and only for its own property (WB-11; SPEC-write-back -13, -14;
GI-CONN-08). The example connector passes every property."""
from __future__ import annotations

import pytest

from knovas_connector_kit.conformance import PROPERTIES, run
from knovas_connector_kit.example import make_harness

from mutants import EXTRA, MUTANTS, factory


def test_the_example_connector_passes_every_property():
    report = run(make_harness)
    assert report["passed"] == PROPERTIES and report["failed"] == {} and report["not_applicable"] == []
    assert len(report["report_sha256"]) == 64


def test_the_report_is_reproducible():
    assert run(make_harness)["report_sha256"] == run(make_harness)["report_sha256"]


def test_every_property_has_a_mutant():
    assert set(MUTANTS) == set(PROPERTIES)


@pytest.mark.parametrize("prop", sorted(MUTANTS))
def test_the_suite_fails_the_mutant_on_its_property_only(prop):
    report = run(factory(MUTANTS[prop]))
    assert set(report["failed"]) == {prop}, report["failed"]
    assert set(report["passed"]) == set(PROPERTIES) - {prop}


@pytest.mark.parametrize("prop", sorted(EXTRA))
def test_the_extra_mutants_fail_their_property(prop):
    assert prop in run(factory(EXTRA[prop]))["failed"]


def test_a_read_only_connector_is_not_offered_for_writing():
    from knovas_connector_kit.example import ExampleHarness, ExampleRecordsConnector

    class ReadOnly(ExampleRecordsConnector):
        def declaration(self):
            d = super().declaration()
            d["actions"] = []
            return d

    report = run(lambda: ExampleHarness(ReadOnly))
    assert {"preview_equals_receipt", "exactly_once", "permit_refusals"} <= set(report["not_applicable"])
    assert {"replay_changes_nothing", "deletion_guard", "no_credential_leaves_site"} <= set(report["passed"])
