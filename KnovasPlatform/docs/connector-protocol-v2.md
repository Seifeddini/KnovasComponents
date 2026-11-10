# Connector protocol v2: reading and writing any source of truth

**Status: Draft v2.0 — for the founder's review.** Written on 2026-11-10 for BET-2026-11-write-back, item WB-10
(feature plan FEAT-2026-11-10-write-back, Phase A). Its five design choices are **decided** (the founder, 2026-11-10:
"D-2: option A; D-3: approved; D-4: approved all"; DEC-0071 and D-4 in the plan's §1.1); the full text and its
schemas wait for the founder's review. Nothing of it is built: Knovas Link, the connectors and the Brain build against
it from WB-11 on. Until the text is approved, every detail outside the five choices may still change; after the first
company runs a v2 connector, a change is a new minor version with additions only.

The machine-readable contract is in `KnovasComponents/KnovasLink/contracts/v2/`, pinned by its own
`contracts.lock` (`2.0.0-draft`), with valid and invalid examples under `examples/` and the contract test
`KnovasLink/tests/test_contracts_v2.py`. Version 1.x (`connector-protocol-v1.md`, `KnovasLink/contracts/`,
`1.2.0`) is unchanged and keeps working.

## In plain words

1. Every system a company keeps its knowledge in (a file share, a code host, a ticket system, a document library) is
   reached through one **connector** at the company's site, behind its Knovas Link. Version 2 is one contract for all
   of them, for reading and for writing. Adding a system means adding a connector that passes the shared tests; the
   Brain, Knovas Link, the runner and the Platform do not change (SPEC-write-back-13).
2. A connector **declares** what it can do: which kind of source it serves (a name nobody else interprets), what it
   reads, and which **actions** it can carry out. Knovas offers only what was declared.
3. A change is never a raw edit. It is an **action** with parameters ("set these fields of this item", "move this item
   to the state closed"). Before anyone approves it, the connector computes a **preview** at the site, without
   writing: exactly what will change, and the version of the item it read.
4. When a person approves, the Brain signs one **permit** per action: single use, short-lived, bound to this tenant,
   this site, this source, this action, this item, a hash of the parameters and the version the preview read.
5. Knovas Link checks the permit before the connector sees the job. The connector writes, either **directly** or into
   the source's **own review step** if the source has one, reads the item again and answers with a **receipt**: what
   the source now holds, and every difference from the preview.
6. If the item changed after the preview, nothing is written: it is a **conflict**, and the approver sees what
   changed. If the same permit arrives twice, the second answer is the first receipt; nothing is written twice.
7. The keys to the company's systems stay at the site. A permit, a job, a result and a log line carry none.
8. The Brain's own facts still change only when the next read of the source sees the change (GI-CONN-04).

## What this version decides

| Choice (D-4, decided 2026-11-10) | How v2 does it |
|---|---|
| 1. The protocol names no source kind | `source_kind` is an opaque token; no message, no rule of the Brain and no rule of Link depends on its value. The contract test searches the schemas, the codes, the examples and this text (outside "Examples (informative)") for source names, operations and fields (SPEC-write-back-16, GI-CONN-08) |
| 2. Staging is declared by the connector | Each action declares `staging: [direct, native_review]`; the Brain and Link treat both through the same jobs |
| 3. Job kinds | `preview`, `act`, `reread`, `review_outcome`, plus `unstage` (see "Differences from the plan's §1.1"), next to 1.1's `probe` and `fetch` and the read streams |
| 4. Permits | Ed25519 signatures with a key id, pinned by Link from the Brain; bound to tenant, site, source, action, target, parameter hash, expected version and expiry; used once |
| 5. One record per action, 1.x keeps working | A step names one `target`; several steps in one job only where the connector declared `grouping: atomic`. A 1.x Link never receives a v2 job |

## Principles

Version 1.x's principles hold unchanged: outbound only, the tenant comes from the certificate, the workspace model is
the only record, reports carry codes and counts, nothing is written to a tenant without a shown dry run and a person's
approval, and a credential of a source stays at the company. Version 2 adds:

- **Declared, not assumed.** What a connector can do is what it declared and what its package passed in the conformance
  suite. A capability without its conformance properties is not offered (SPEC-write-back-14).
- **Approve what you saw.** An action is approved only on an exact preview, and written only while its item is still
  at the version that preview read (GI-CONN-09, GI-CONN-10).
- **A write is under a permit, once.** Link carries out a write only under a valid permit, and a permit at most once
  (GI-CONN-05).
- **A receipt is a re-read.** The answer to a write is what the source holds afterwards, read again, never a copy of
  the preview (GI-CONN-06).
- **Generic, both ways.** Everything specific to a source lives in its connector (GI-CONN-08).

## The messages

| Message | Schema | Direction |
|---|---|---|
| Connector declaration | `KnovasLink/contracts/v2/declaration.schema.json` | connector → Link (registration), inside the site declaration |
| Site declaration | `KnovasLink/contracts/v2/site.schema.json` | Link → Brain, `PUT /secured/connector/v2/site`; the same declarations reach the Platform in Link's heartbeat |
| Permit keys | `KnovasLink/contracts/v2/permit-keys.schema.json` | Brain → Link, `GET /secured/connector/v2/permit-keys` |
| Permit | `KnovasLink/contracts/v2/permit.schema.json` | Brain → Link → connector, inside an `act` job |
| Job | `KnovasLink/contracts/v2/job.schema.json` | Brain → Link → connector, on the work channel |
| Result | `KnovasLink/contracts/v2/result.schema.json` | connector → Link → Brain |
| Stream message | `KnovasLink/contracts/v2/stream-message.schema.json` | connector, while reading |
| Codes | `KnovasLink/contracts/v2/codes.json` | the codes v2 adds; 1.x's `codes.json` stays valid |

## Declaring a connector

A connector registers with Knovas Link (local API, as in 1.x) and hands over its **declaration**
(`declaration.schema.json`):

- `protocol` (`2.x`), `connector {name, version, package_sha256}`;
- `source_kind`: an opaque token. It is matched only by equality with the kind of the sources a person maps to this
  connector in the Builder. No rule reads its value;
- `streams`: the stream message types it produces when it reads (`document`, `record`, `tombstone`, `generation`,
  `state`; the permission types stay reserved);
- `fetch`: whether it answers `fetch` jobs;
- `actions`: per action its `name`, the `target_kind` it changes, its `params` (name, kind, required; an `enum`
  lists its values), `preview` (`exact` or `none`), `staging` (`direct`, `native_review` or both), `grouping`
  (`atomic` or `none`) and `inverse` (the action that undoes it from a receipt's before-values, or `null`);
- `conformance {suite, passed, report_sha256}`: the properties its package passed in the conformance suite of the
  connector kit (WB-11).

**What the declaration decides.** The Builder offers a source for reading only when its connector passed
`replay_changes_nothing`, `deletion_guard` and `no_credential_leaves_site`; for writing only the declared actions, and
only when it passed `preview_equals_receipt`, `exactly_once`, `permit_refusals` and `no_credential_leaves_site`;
`native_review` staging also needs `review_outcome`. An action with `preview: none` can be offered, but never approved
(SPEC-write-back-06). A connector that fails `exactly_once` may still enrol to read (SPEC-write-back-14).

**Where the declaration goes.** Link sends the declarations of all connectors behind it, with the source keys each
serves, as one **site declaration** (`site.schema.json`) to the Brain with the site certificate:
`PUT /secured/connector/v2/site {site, protocol, link_version, connectors: [{declaration, sources}]}`, at start and
whenever a connector registers or leaves. `site` is Link's component id from its enrolment with the Platform. The Brain
binds that id to the certificate that sent it (the tenant from the certificate, never from the body), queues v2 jobs
only for a site that declared protocol 2, and lets only that site claim a job that names it. Link's heartbeat carries
the same declarations to the Platform, so the Builder offers exactly what was declared (SPEC-write-back-12).

**The ontology maps to the declaration.** The tenant's ontology declares its action types (WB-02: node type,
parameters, preconditions, facts changed, inverse, risk class) and, per source kind, which connector action carries
each out and how its parameters map. The protocol carries only the connector's action and its parameters; the
ontology's action type stays in the Brain.

## Reading

Reading works as in 1.1: the connector reads its source and produces a stream (`stream-message.schema.json`); runs and
dry runs are commands on the heartbeat; uploads and graph writes go through Link's relay; deletions only through a
`tombstone` or a complete `generation`, under the deletion guard (GI-CONN-01, GI-CONN-02). Version 2 defines one type
that 1.1 reserved:

- **`record`**: an item that is a set of fields rather than a document: `{type: record, source, external_id, version,
  payload {record_kind, fields, computed?, links?}}`. `record_kind` is the connector's `target_kind`; `fields` holds
  strings, integers, booleans, null or lists of them (decimals as strings); `computed` names the fields the source
  computes, which are never writable and never previewed as exact; `links` names other items by `external_id` and an
  opaque relation. A 1.1 receiver still refuses `record` with `unsupported_type`, so a connector sends records only to
  a v2 receiver.

`version` is the source's own version of the item, an opaque string: what a preview reads and a permit's
`expected_version` names. Each connector says in its own documentation what it uses.

## Jobs

Knovas asks a site for work on 1.1's work channel: Link long-polls `GET /secured/connector/jobs?source=…&wait=25`,
posts parts and results to `POST /secured/connector/jobs/<id>/part?n=` and `…/result`, with the lease in
`X-Knovas-Lease`, as in 1.1. A v2 job (`job.schema.json`) carries `protocol: "2.x"`; every write-path job also carries
`site`.

| Kind | Args | What the connector does |
|---|---|---|
| `probe` | `{}` | nothing; as in 1.1 |
| `fetch` | `{external_id, version?, max_bytes}` | reads the original bytes and posts them in parts, as in 1.1 |
| `preview` | `{steps: [{action, target, params}], staging, at_version?}` | computes, without writing, exactly what the steps would change, in the source's own terms, and the version of each target it read; answers `preview` |
| `act` | `{steps: [{action, target, params, permit}], staging}` | after Link's permit checks: writes the steps (directly, or into the source's own review step), reads the targets again and answers with one receipt per step |
| `reread` | `{targets, fields?, permit_ids?}` | reads the targets again and answers what they hold and their versions; with `permit_ids`, the receipt of those permits' earlier write. Writes nothing |
| `review_outcome` | `{stage_ref, permit_ids}` | reports the state of a staged change in the source's own review: `pending`, `accepted` (with the receipts of a re-read), `declined` or `withdrawn` |
| `unstage` | `{stage_ref, permit_ids}` | withdraws a staged change from the source's review (the change set was withdrawn or refused in the Brain) |

- **Not jobs.** `run`, `pause`, `check` and the other commands stay on the heartbeat. The 1.x `write` job is not a v2
  kind; it stays a 1.x job (see "Compatibility with 1.x").
- **Only the named site claims.** The Brain hands a write-path job only to the certificate bound to the job's `site`
  (closes finding F5 for v2), and Link itself refuses a permit naming another site: both checks stay, because each one
  alone is the only guard when the other fails (SPEC-write-back-23).
- **Deadlines.** `preview` 30 seconds, `act` the permit's expiry, `reread`, `review_outcome` and `unstage` 60 seconds.
  Silent past the deadline: `unreachable`, as in 1.1.
- **When the Brain asks for a review outcome.** While a change is staged, every 5 minutes and when a person asks in the
  Builder. A connector never calls in by itself (outbound only).

## The write path, step by step

```mermaid
sequenceDiagram
  autonumber
  participant A as Proposer (person or agent)
  participant B as Brain
  actor P as Approver (Builder)
  participant L as Knovas Link (site)
  participant C as Connector (site)
  participant S as Source
  A->>B: propose a change set of typed actions (with evidence)
  B->>B: action types declared, parameters of their kinds, preconditions hold
  B->>L: job preview {steps, staging}
  L->>C: the job (action declared? staging declared?)
  C->>S: read the targets (no write)
  C-->>B: preview {exact, read_versions, changes, grouping}
  P->>B: approve (only an exact preview)
  B->>B: one permit per action, signed
  B->>L: job act {steps with permits, staging}
  L->>L: verify permits, record them in the write log, used before? then the first result
  L->>C: the job
  C->>S: still at expected_version? write (direct) or stage (native_review)
  C->>S: read again
  C-->>L: receipts
  L->>L: keep the result under each permit id
  L-->>B: the result
  B->>B: change set applied, partly applied, differing, conflict or staged; no Brain fact changes
  Note over S,B: The next read of the source changes the Brain's facts (GI-CONN-04)
```

## Staging: direct and native_review

A connector declares per action how a write can reach the source:

- **`direct`**: the connector writes to the item after the Brain's approval, only while the item is still at the
  permit's `expected_version`; otherwise the result is `conflict` and nothing is written. The Brain's change set is
  the review. The receipt's outcome is `written`, `partly_written` or `not_written`.
- **`native_review`**: the connector stages the change in the source's own review step and answers `staged` with a
  `stage_ref`, an address it alone interprets. The Brain asks for the outcome with `review_outcome` jobs, applies the
  change set when the source accepts it (the receipt comes from the re-read at acceptance) and refuses it when the
  source declines it (SPEC-write-back-35). Withdrawing the change set in the Brain sends `unstage`.

Which mode a source uses is set per source in the Builder, from the modes its connector declared; both modes run
through the same jobs and fields. The Brain and Link never ask what a staged change looks like in the source.

**The approval is the review in `direct` mode.** Until RBAC and verified identity land (WB-40), an approval is bound to
the tenant certificate and a declared actor only. The founder decided (D-3, 2026-11-10; GI-CONN-04's new wording) that
`direct` is the default for the Knovas tenant's own sources, with only the founder approving writes until WB-40.

## Permits

The Brain signs one permit per approved action (`permit.schema.json`, SPEC-write-back-21):

```json
{"claims": {"v": 2, "permit_id": "…", "tenant": "…", "site": "…", "source": "…", "change_set": "…",
            "action_id": "…", "action": "…", "target": "…", "params_sha256": "…", "expected_version": "…",
            "staging": "direct", "issued_at": "2026-11-10T14:00:00Z", "expires_at": "2026-11-10T15:00:00Z",
            "approved_by": {"kind": "user", "id": "…"}},
 "alg": "EdDSA", "kid": "…", "sig": "…"}
```

- **Signature.** Ed25519 (RFC 8032) over the canonical JSON (RFC 8785) of `claims`, base64url without padding. Times
  are UTC with `Z` and whole seconds, and parameters hold no floating-point numbers, so every claim has one spelling.
- **Keys.** The Brain answers `GET /secured/connector/v2/permit-keys` (`permit-keys.schema.json`) over Link's mTLS
  channel: up to four keys, each with `kid`, the public key, `not_before` and `not_after`. Link pins them and accepts a
  new key only from that route, never from the Platform or a job. A key id lets keys rotate: the Brain signs with the
  newest and keeps the old one valid until every permit it signed has expired. The private key stays in the Brain
  (where exactly is an open question below).
- **Parameter hash.** `params_sha256` is SHA-256 over the canonical JSON of the step's `params`. The permit carries
  the hash, never the values: a permit is safe to log by its id.
- **Lifetime.** `expires_at` is at most 60 minutes after `issued_at`. A first delivery after it is refused
  `permit_expired`; a repeat of a permit written before it still gets its first result (SPEC-write-back-10).
- **Approver.** `approved_by` is the declared approver, recorded and never trusted for a decision until verified
  identity exists (SPEC-write-back-40). Link's write log keeps a person's id only as a hash.

## What Knovas Link checks before a connector sees an `act`

In this order, for every step; the first that fails answers for the whole job, and nothing is written:

1. A permit is there: else `refused permit_missing`.
2. The permit id is in Link's used-permit store: with the same job content, the stored first result with
   `repeat: true`; with other content, `refused permit_used`.
3. The permit's `kid` is a pinned key inside its validity: else `refused permit_unknown_key`; the signature verifies:
   else `refused permit_bad_signature`.
4. `tenant` is the tenant of Link's certificate and `site` is Link's own site id: else `refused permit_mismatch` or
   `refused permit_wrong_site`.
5. `source`, `action`, `target`, `staging` and the hash of the step's `params` equal the job's: else
   `refused permit_mismatch`.
6. Now is before `expires_at`: else `refused permit_expired`.
7. The source's switch allows writing (`access: read_write`): else `refused write_disabled` (1.x code); the connector
   serving the source declared the action with this staging: else `refused action_not_declared` or
   `refused staging_unsupported`; several steps only for `grouping: atomic`: else `refused grouping_unsupported`.
8. Link records each permit in its write log at the site before it hands the job on: else `failed
   write_log_unavailable`.

After the connector's result Link stores it under every permit id of the job before it passes the result on, and
records the outcome in its write log. The log and the Brain's record reconcile by permit id (SPEC-write-back-26,
WB-25). The connector checks the expected version itself; Link cannot, because it does not read the source.

## Receipts

The answer to an `act` carries one **receipt** per step (`result.schema.json`, `$defs/receipt`; SPEC-write-back-07):

- `permit_id`, `outcome` (`written`, `partly_written`, `not_written`, `staged`), `expected_version`, `version_after`
  (null when nothing was written or the change is only staged), `reread_at`;
- `values`: per previewed field the value before (at `expected_version`) and after (as read again);
- `differences`: every previewed field whose re-read value is not the approved one. Not empty: the Brain marks the
  change set differing and holds that action on that source until a person clears it (SPEC-write-back-08);
- `side_effects`: fields the source changed by itself that the preview did not show, by name only;
- `source_ref`: an address of the change in the source, if it has one.

The connector reads the source again in the same job, right after writing, so no other change can fall between the
write and the receipt unnoticed: a re-read that finds a version other than the one its own write produced reports the
values it found, and they appear as differences. A receipt never changes afterwards (GI-CONN-06). For `native_review`
the receipts come with the `review_outcome` result that reports `accepted`.

## Idempotency and exactly once

The **permit id is the idempotency key** of the write, end to end (SPEC-write-back-10, GI-CONN-05):

- **Link** answers every repeat of a permit it already carried out with the stored first result, `repeat: true`, also
  after the permit's expiry, and keeps the store for at least 30 days after the expiry.
- **The connector** makes each write recognisable by its permit id where the source allows it (a marker the source
  keeps with the change), so that a write whose result was lost between the source and Link's store is found on the
  next delivery and answered with a receipt, not written again. A connector that cannot do this does not pass
  `exactly_once` and is offered for reading only.
- **The Brain** never signs a second permit for an action it already sent: a lost result is recovered with `reread`
  and the permit ids, never by a new permit (the Alloy mutant `write_back_permits__brain_reissues_on_retry` is the
  failure this rule prevents).

## Conflicts

When a target is not at the permit's `expected_version`, the connector writes nothing and answers `status: conflict`,
`code: version_changed`, with `conflict {target, expected_version, found_version, changes?}`: the previewed fields'
values at the expected version and now (SPEC-write-back-32). The Brain sets the change set to `conflict` and shows the
approver what changed. A reversal is previewed with `at_version` set to the version its original's receipt recorded,
never at the current one, and is a conflict when the source changed since (SPEC-write-back-33, -34).

## Grouping: all or none

An action declares `grouping: atomic` when the connector can write several steps on one source all or none. Then the
Brain sends a change set's steps on that source in one `act` job, and the receipts are all `written` or all
`not_written`. With `grouping: none` the Brain sends one step per job; the preview's `grouping` tells the approver
before approving that the steps are written one by one, and a change set where some steps were written and some not is
`partly applied` (SPEC-write-back-31).

## Results and codes

The statuses are 1.1's: `ok`, `refused`, `conflict`, `unsupported`, `unreachable`, `failed`. A `code` is one of 1.x's
`codes.json`, one of v2's below, or a connector's own of the same shape, never a sentence.

| Code | Meaning |
|---|---|
| `protocol_unsupported` | The site or connector does not speak the job's protocol version |
| `site_not_declared` | The Brain has no site declaration for this certificate yet |
| `action_not_declared` | The connector did not declare this action |
| `params_invalid` | A parameter is missing, unknown or of the wrong kind |
| `staging_unsupported` | The action did not declare the job's staging |
| `grouping_unsupported` | Several steps for an action without `grouping: atomic` |
| `preview_inexact` | The connector cannot compute the change exactly; not approvable |
| `computed_field` | The change would set a field the source computes |
| `target_not_found` | The target does not exist in the source |
| `version_changed` | The target is no longer at the expected version (status `conflict`) |
| `permit_missing` | An act step without a permit |
| `permit_mismatch` | The permit and the job disagree (tenant, source, action, target, parameters, staging) |
| `permit_bad_signature` | The signature does not verify |
| `permit_unknown_key` | The key id is not pinned, or outside its validity |
| `permit_expired` | First delivery after the expiry |
| `permit_wrong_site` | The permit names another site |
| `permit_used` | The permit was used for other job content |
| `partly_written` | Some steps written, some not (grouping `none`) |
| `not_staged` | `review_outcome` or `unstage` for an unknown stage |
| `write_log_unavailable` | Link could not record the permit in its write log; nothing written |

**What a result may carry.** A v2 result holds the values of the change (a preview, receipts, a conflict, a re-read):
customer data. It travels only on the work channel to the Brain, which keeps it with the change set on cloudscale
(GI-DATA-03). It never enters Link's logs, the Platform's reports, a heartbeat or a metric label (GI-DATA-02,
GI-DATA-04); Link logs job ids, kinds, statuses, codes, counts and durations only, as in 1.x. Previews and receipts are
capped (500 changes, 64 KiB per value, 256 KiB per diff); a larger change is refused by the connector with its own code.

## Compatibility with 1.x

- **Version 1.x stays as it is.** Its paths, contracts (`KnovasLink/contracts/`, `1.2.0`), jobs (`fetch`, `write`,
  `probe`), proposals (`/secured/source-writes`) and codes do not change. A component that speaks 1.x keeps its fetch
  and write jobs next to v2 connectors (SPEC-write-back-15, GI-CONN-08's `v1_components_keep_working`).
- **v2 is negotiated per site.** A Link declares protocol 2 with its site declaration; the Brain queues v2 jobs only
  for such a site, so a 1.x Link never receives a job it would refuse as `invalid_job`. A Link that speaks 2 also
  validates 1.x jobs against 1.x's schemas.
- **Connectors behind one Link may differ.** A v2 Link serves 1.x components (local API 1.x) and v2 connectors side by
  side; it hands each job only to a connector that declared its kind and protocol.
- **Moving a source to v2.** A source's write path moves from 1.x proposals to v2 change sets when its connector
  declares v2 actions and a person switches the source over in the Builder; open 1.x proposals for that source are
  settled first (WB-12 moves the first source kind this way). Reading needs no switch.
- **Within v2 only additions.** A receiver ignores fields it does not know in open objects (job, stream message);
  closed objects (declaration, permit, args, result) change only with a new minor version both sides declare.

## The conformance suite

The connector kit (WB-11) ships the suite every connector package runs before it may declare a property. Each
property is a test against the connector and a fake source the kit provides; the connector's own tests add a real
source where one is available.

| Property | What the suite checks |
|---|---|
| `replay_changes_nothing` | Reading the same source twice yields the same stream; an apply of a replayed stream changes nothing (GI-CONN-01) |
| `deletion_guard` | Absence is reported only through a complete `generation` or a `tombstone`; a cut listing yields neither (GI-CONN-02) |
| `preview_equals_receipt` | For every declared action with `preview: exact`, the receipt's values equal the preview's changes on an unchanged source; a computed field makes the preview inexact |
| `exactly_once` | A repeated `act` with the same permit, also after a simulated loss of the result between the write and Link, writes once and answers with the first receipt |
| `permit_refusals` | Every refusal in "What Knovas Link checks" is produced for its case, and nothing is written (run against the kit's Link) |
| `no_credential_leaves_site` | No result, part, stream message, log line or error carries the source's credential (the kit plants a canary credential) |
| `review_outcome` | For `native_review`: staged, then accepted gives receipts, declined gives no write, `unstage` withdraws |

The suite's report (`report_sha256`) and the package digest go into the declaration. In this draft the declaration is
the connector's own statement; see the open questions.

## Differences from the plan's §1.1

The decided choices (D-4) name the job kinds `preview`, `act`, `reread` and `review_outcome`. This draft keeps them,
and two details go beyond them; they are for the founder's review of the full text:

1. **adds `unstage`**, so that withdrawing or refusing a change set in the Brain also withdraws its staged change from
   the source's review, instead of leaving it open there;
2. **puts the receipt into `act`'s result**: the connector re-reads in the same job right after writing, which keeps
   the window between the write and the re-read small. `reread` is the separate kind for a lost result, a conflict's
   display and a reversal's preview, as §1.1 says.

## Open questions for the founder

1. **Where the Brain's permit key lives**: in secure-api's Key Vault secret like other keys today, or with the client
   CA key's custody (DEC-0064 left the HSM out of the basis). The protocol only needs a key id and rotation.
2. **Who vouches for a conformance report**: in this draft the connector's declaration states the properties it
   passed. Later a Knovas-signed report per package digest could be required before the Builder offers writing.
3. **Defaults**: permit lifetime 60 minutes, review polling every 5 minutes, write log kept 30 days after expiry.

## Examples (informative)

These examples name real systems to show how a connector maps its source onto the generic messages. None of it is
part of the protocol: the GitHub, Jira and file-share connectors are WB-12, WB-07 and WB-13.

- **GitHub, `direct`.** A connector for GitHub repositories declares `source_kind: "github"`, streams `document`,
  `tombstone`, `generation`, `state`, and an action `set_front_matter` (target kind `file`, preview exact,
  `staging: [direct, native_review]`, `grouping: atomic`, inverse `set_front_matter`). An item's `external_id` is its
  path, its `version` the git blob sha. `act` in `direct` mode reads the file at the head of the target branch,
  compares the blob sha with `expected_version` (else `conflict`), writes one commit with all steps' files (atomic
  through the Git data API: one tree, one commit, the branch moved only if it still points to the commit read),
  carries the permit ids in commit trailers (`Knovas-Permit: <id>`) so a repeat finds the earlier write, re-reads the
  files and answers receipts whose `version_after` is the new blob sha and `source_ref` the commit's URL. The token
  needs `Contents: write` and must be allowed to push to the target branch.
- **GitHub, `native_review`.** The same action commits to a new branch and opens a pull request; `stage_ref` is the
  pull request's URL; `review_outcome` answers `accepted` when it was merged (with receipts from the target branch),
  `declined` when it was closed without merge; `unstage` closes it and deletes the branch.
- **Jira.** A connector declares `source_kind: "jira"`, streams `record`, `tombstone`, `generation`, `state`, and
  actions such as `set_assignee`, `transition_issue` and `set_due_date` (`staging: [direct]`, `grouping: none`). An
  item is an issue (`external_id` its key, `version` its `updated` time or change-log id); a field an automation
  computes is in `computed`, so a preview that would set it is inexact.
- **A file share.** A connector for a mounted folder declares documents and an action that replaces a file through a
  temporary file and a rename, with the file's hash as its version.

## Not in v2.0

Bulk actions (more than one record per step), schema changes in a source, autonomous (standing) writes (they need a
role-based policy that signs their permit: BET-2026-11-earned-autonomy), credentials typed into the Builder and sealed
to a site (WB-20), local MCP servers behind Link (WB-24), and the permission stream types (SA-05).
