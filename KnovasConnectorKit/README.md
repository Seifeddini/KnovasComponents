# Knovas connector kit

**Status: draft, for connector protocol 2.0 (v2.0, approved by the founder on 2026-11-10, amended the same day: D-14,
D-15).** BET-2026-11-write-back, item
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
| `knovas_connector_kit.permits` | Decoding and verifying permits (JWS compact, Ed25519, the key's window checked at the permit's `iat`; only the one canonical encoding; an unreadable token is `permit_bad_signature`, never an error), the pinned key ring |
| `knovas_connector_kit.connector` | `Connector`: the base class; `handle(job)` checks the job and the answer against the contract, re-checks that each permit binds its own step, and turns an exception into `failed internal` logged by its type only |
| `knovas_connector_kit.gate` | `LinkGate`: a reference of Knovas Link's checks in the Alloy model's order, with the used-permit store (a permit reserved before the connector runs, its first result never replaced) and the write log (every refusal too), for tests and the suite (Link has its own implementation) |
| `knovas_connector_kit.example` | A fake source of records and an example connector over it, with its harness |
| `knovas_connector_kit.conformance` | The conformance suite: seven properties, a report with its SHA-256 |

## Writing a connector

Subclass `Connector`, return the declaration (`declaration.schema.json`), and implement `read` and, for each action
you declare, `preview`, `act`, `reread`, and for `native_review` also `review_outcome` and `unstage`. Rules the suite
checks:

- a read of the same source from the same state gives the same stream; absence is reported only through a complete
  `generation` or a `tombstone`, never from a listing the source cut;
- `preview` writes nothing and is `exact: false` whenever it cannot say exactly what will change (a computed field);
- every change, value, difference and conflict names its `step`, the index into the job's `args.steps` (for a receipt,
  the step of its permit in its `act` job: keep it with the marker of the write, so a `reread` or `review_outcome`
  names it too), so two actions on one item are told apart (D-14). `handle` refuses a `preview` or `act` answer whose
  steps are not the job's (`failed internal`); give the harness `same_item_steps()` so the suite checks two on one
  item;
- `act` writes only while each target is at its permit's `expected_version` (else `conflict`, nothing written), keeps
  the permit id with the change in the source, re-reads after writing and answers receipts from that re-read;
- `grouping` (D-24): `atomic` writes several steps of one act all or none on any items, `item` all or none on one item
  (the Brain sends one act per item; `handle` refuses an act spanning items `grouping_unsupported`), `none` one step
  per act; a preview's `grouping` is what its steps share (`connector.grouping_of`);
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

`tests/test_conformance_suite.py` runs the suite on the example connector (every property passes, also when its actions
declare only `native_review`) and on one mutant per property (`tests/mutants.py`), each of which fails exactly its own
property; the further mutants (`EXTRA`: one writes past the preview's version, one names changes by their item instead
of their step, one stages a repeat a second time, one stages past the preview's version, one's own permit check
ignores the staging and the site, one writes an act's steps one by one) fail only theirs. With `set_fields` declared
`grouping: item` (D-24) the example passes too, and two more mutants fail `preview_equals_receipt`: one writes an act
whose steps span items, one writes an item's steps one by one (partly, when the second finds the item moved).

The write properties check each staging the connector's actions declare (the verifier's finding M3 on Phase A): a
connector that only stages passes them, its receipts taken from the accepted review; `exactly_once` delivers a staging
`act` twice and wants the same stage back; `permit_refusals` checks the connector's own binding of the staging and the
site.

A permit lives at most 3 hours (`permits.MAX_LIFETIME_SECONDS = 10800`, D-15); the suite's stand-in for the Brain signs
for that long.
