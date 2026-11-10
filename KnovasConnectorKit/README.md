# Knovas connector kit

**Status: draft, for connector protocol 2.0 (Draft v2.0 — for the founder's review).** BET-2026-11-write-back, item
WB-11; feature plan FEAT-2026-11-10-write-back, Phase A (tasks A-3, A-4). Nothing at a site runs it yet: Knovas Link
speaks v2 from task A-5, the GitHub connector moves onto the kit in Phase C, Jira follows in Phase D.

A **connector** reads one kind of source at a company's site and carries out the actions it declares, behind the
site's Knovas Link ([connector protocol v2](../KnovasPlatform/docs/connector-protocol-v2.md)). This kit is what every
connector is built on, so that a new kind of source is one connector package and no change to the Brain, Link, the
runner or the Platform (SPEC-write-back-13).

| Module | What |
|---|---|
| `knovas_connector_kit.contracts` | The v2 schemas and codes (a byte-for-byte copy of `KnovasLink/contracts/v2`, checked against its lock) and validators |
| `knovas_connector_kit.canonical` | Canonical JSON (RFC 8785, for the values the protocol allows) and the parameter hash a permit binds |
| `knovas_connector_kit.permits` | Decoding and verifying permits (JWS compact, Ed25519, the key's window checked at the permit's `iat`), the pinned key ring |
| `knovas_connector_kit.connector` | `Connector`: the base class; `handle(job)` checks the job and the answer against the contract, re-checks that each permit binds its own step, and turns an exception into `failed internal` logged by its type only |
| `knovas_connector_kit.gate` | `LinkGate`: a reference of Knovas Link's checks in the Alloy model's order, with the used-permit store and the write log, for tests and the suite (Link has its own implementation) |
| `knovas_connector_kit.example` | A fake source of records and an example connector over it, with its harness |
| `knovas_connector_kit.conformance` | The conformance suite: seven properties, a report with its SHA-256 |

## Writing a connector

Subclass `Connector`, return the declaration (`declaration.schema.json`), and implement `read` and, for each action
you declare, `preview`, `act`, `reread`, and for `native_review` also `review_outcome` and `unstage`. Rules the suite
checks:

- a read of the same source from the same state gives the same stream; absence is reported only through a complete
  `generation` or a `tombstone`, never from a listing the source cut;
- `preview` writes nothing and is `exact: false` whenever it cannot say exactly what will change (a computed field);
- `act` writes only while each target is at its permit's `expected_version` (else `conflict`, nothing written), keeps
  the permit id with the change in the source, re-reads after writing and answers receipts from that re-read;
- a repeat of a permit (also after a crash between the write and the answer) finds the earlier write by its permit id
  and answers its receipt without writing again;
- no result, stream message, log line or error carries the source's credential: log exceptions by their type only.

## Passing the suite

Give the suite a harness (`knovas_connector_kit/conformance/harness.py` says what it holds) and run it:

```bash
python -m knovas_connector_kit.conformance my_connector.harness:make_harness
```

The report lists the properties passed; the declaration's `conformance` names them and the report's SHA-256. The
Builder offers a source for reading only with `replay_changes_nothing`, `deletion_guard` and
`no_credential_leaves_site`, for writing only with `preview_equals_receipt`, `exactly_once`, `permit_refusals` and
`no_credential_leaves_site`, and `native_review` only with `review_outcome` (GI-CONN-08).

## Tests

```bash
cd KnovasConnectorKit && pip install -r requirements.txt "pytest>=7.4,<9" && python -m pytest -q tests
```

`tests/test_conformance_suite.py` runs the suite on the example connector (every property passes) and on one mutant
per property (`tests/mutants.py`), each of which fails exactly its own property.
