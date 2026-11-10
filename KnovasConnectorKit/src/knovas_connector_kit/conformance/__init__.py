"""The conformance suite of connector protocol 2.0 (draft; BET-2026-11-write-back WB-11, SPEC-write-back -13, -14).

A connector package passes it before its declaration may name a property; the Builder offers a source for reading
only with the read properties and for writing only with the write properties (GI-CONN-08). Run it on a package's
harness (``harness.Harness``):

    from knovas_connector_kit.conformance import run
    report = run(make_harness)          # {"suite", "connector", "passed", "failed", "not_applicable", "report_sha256"}

or ``python -m knovas_connector_kit.conformance <module>:<factory>``.
"""
from knovas_connector_kit.conformance.suite import PROPERTIES, READ_PROPERTIES, WRITE_PROPERTIES, run  # noqa: F401
