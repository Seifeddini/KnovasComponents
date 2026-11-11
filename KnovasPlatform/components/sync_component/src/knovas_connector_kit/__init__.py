"""Knovas connector kit (connector protocol 2.0, draft; BET-2026-11-write-back WB-11).

A connector reads one kind of source at a company's site and carries out the actions it declares, behind the site's
Knovas Link. The kit gives it the protocol's messages and checks (``contracts``, ``canonical``, ``permits``), a base
class that answers jobs (``connector.Connector``), a reference of Link's checks for tests (``gate.LinkGate``), a fake
source with an example connector (``example``), and the conformance suite (``conformance``) a connector package passes
before it may declare a property. The kit names no source kind: everything specific to a source lives in a connector.
"""
__version__ = "0.1.0"
PROTOCOL = "2.0"
SUITE_VERSION = "2.0.0"
