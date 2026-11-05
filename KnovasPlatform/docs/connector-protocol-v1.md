# Connector protocol v1: the contract

**Status: draft (KC-7a, BET-2026-11-sources-and-access).** The control plane and the records path are built and tested. The decisions marked *proposed* wait for DEC-0056; a change to them before the first company runs a component is cheap, and after that it is not.

**Version 1.1** (MVP-2, KC-7a): additions to 1.0 only. It adds sites and Knovas Link, streams, jobs on a work channel, probes, write proposals and fetching originals, in the sections from "Sites and Knovas Link" on. None of them is built yet. The paths stay `/api/connectors/v1`, and a component that knows only 1.0 keeps working unchanged. The contracts are files in `KnovasComponents/KnovasLink/contracts/`, pinned by `contracts.lock`.

A connector is a separately deployed sync component, registered for one company (DEC-0040). This document is what any connector must do, in any language. The Platform's side is `docbridge_integration/src/builder/protocol.py`; the reference component is `components/sync_component`.

## Principles

- **Outbound only.** A component calls the Platform; the Platform never calls in. A company's network accepts no inbound call.
- **The tenant comes from the certificate.** A component never names it.
- **The workspace model is the only record.** A component is configured from the Platform, not by hand.
- **Reports carry codes and counts.** No document name and no customer text reaches the Platform's logs or events (GI-DATA-02/04). What does not fit that shape is refused.
- **Nothing is written to a tenant without a shown dry run and a person's approval.** A schedule only runs dry runs.
- **A credential of a source stays at the company,** in the component's own environment.

## Enrolment

`POST /api/connectors/v1/enrol` with `{token, csr, version, capabilities}`, using the one-time token (24 hours). The answer is `{component, control_token, certificate, certificate_chain, config, brain_url, brain_ca}`; `brain_ca` is the public CA the Brain's TLS certificate is checked against, so nothing has to be copied to the company by hand. The private key is made by the component and never leaves it. The Brain signs the request for this Platform's tenant. The control token is shown this once and kept as a hash.

**Where signing is not available.** Production's Secure API cannot sign today: it does not hold the client CA key, and D-GNB-28 moves that key to an HSM that only a two-person job can use. So automatic signing at enrolment conflicts with that decision; how a component gets its certificate is open in DEC-0056. For a test on one's own machine, `BUILDER_CONNECTOR_ISSUE_CERTIFICATES=false` skips signing: the answer has no certificate, and the component is given the tenant's files (`SYNC_BRAIN_CERT`, `SYNC_BRAIN_KEY`, or the connector's `SEMANTIX_CLIENT_*_PATH`). Its page says so. This is an exception to DEC-0040, not for a company: a leak exposes the tenant, and one component cannot be revoked.

## Every other call

`Authorization: Bearer <control token>`, `X-Component: <id>`, and the client certificate issued at enrolment. A one-time token the component did not enrol with (the Platform's *Token erneuern*) makes it enrol again. A TLS terminator verifies the certificate and sets `X-Client-Cert-Fingerprint` (SHA-256 of the DER, hex); it must never pass on one the client sent. Without a terminator that does this, the deployment says so explicitly (`BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=false`) and the control token alone authenticates.

| Call | Body | Answer |
|---|---|---|
| `POST /heartbeat` | `{state: online\|degraded, version, lag_seconds, open_errors, capabilities}` | `{paused, schedule, commands: [{id, command, args}]}`; a command is delivered once |
| `GET /config` | | `{component, paused, schedule, brain_url, sources: [{workspace, key, kind, access, settings}], mappings: [{workspace, id, source}]}` |
| `GET /package` | | `{workspaces: [{workspace, files: {path: base64}}]}`: the compiled configuration of the workspaces it serves; definitions and source settings, no credential and no data |
| `POST /runs` | `{kind: dry_run\|apply, status: ok\|held\|failed, started_at, finished_at, duration_ms, added, changed, retired, errors, held: [{mapping, retire: {type: n}, share}], command}` | `{id}` |
| `POST /commands/<id>/done` | `{result: ok\|failed\|refused}` | |
| `POST /events` | `{events: [{code, counts}]}`, up to 100 | |
| `POST /checks` | `{checks: [{workspace, source, code, counts}]}`: can it read each source it serves? `source.ok` or a code; the latest only | `{stored}` |

**Commands:** `run {kind}`, `pause`, `resume`, `approve_plan {plan, mapping, reason}`, `reject_plan {plan, mapping}`, `reload_config`, `check`. A component that does not know a command answers `refused`. A `run` is finished by the `POST /runs` that names it in `command`; every other command by `/commands/<id>/done`.

**Compatibility:** within v1 only additions; a component ignores fields it does not know, and declares what it can in `capabilities`.

## The data path

*Proposed (DEC-0056, option A).* **A component writes to the Brain directly, with its own certificate; the Platform is the control plane only.** No customer data passes through the Platform.

Version 1.1 proposes that a site's Knovas Link makes these calls for the components on its machine, with one certificate per site (see "Sites and Knovas Link"). That is an input to DEC-0056, not a decision.

### Records: built

The component fetches the source and writes the document where the compiled mappings read it. On a dry run, the mapping runner plans; the plan stays on site, and only its counts are reported. On an apply, the runner writes exactly that plan, once.

- **State:** the runner's state per source, and its provenance record on the tenant.
- **Deletions:** what the source no longer states is retired, never deleted. The deletion guard holds a plan that would retire too much (`status: held`), until a person approves it with a reason (`approve_plan`).

### Documents: built, without deletions

The Knovas Connector (`KnovasConnector/src/platform_link`) speaks this protocol, with `capabilities: ["documents"]`. It uploads to the Brain itself with its own certificate. On a dry run it scans and keeps the list of what it would send; only counts are reported. On an apply it sends only that list, once, from the configuration the dry run used. A document that appeared since waits for the next dry run.

- **What to read:** the workspace's sources of kind `knovas_connector`. An installation enrolled with none keeps its saved folder list.
- **Access:** each source must name its access groups, or `alle` for the whole tenant. A folder nobody chose for is refused, not sent unrestricted.
- **Deletions:** none. A document removed from the share stays in the Brain, because a dry run does not show removals yet.
- **Schedule:** dry runs only, as for records. So new documents arrive when a person applies; continuous transfer under a person's standing approval is not built.

### Permissions: not built

*Proposed, for SA-05.* Declared through `capabilities: ["permissions"]`. Each item has a stable external id; a run ends with a marker that its listing was complete, so what is missing can be deleted safely. A permission names the source's own principals, which the Platform maps to access groups. Version 1.1 reserves the stream types for it (`principal`, `group`, `membership`, `permission`) and the marker (`generation`); see "Streams".

### Inventory and sample: not built

*Proposed, for the data explorer (FEAT-2026-11-02-builder-data-explorer, its decision D1).* `inventory {path}` and `sample {path, limit}` would carry names and field values into the Platform, which the rule above forbids today. It waits for that decision.

Until then, designing a mapping for a source read at the company works like this: a file share is designed on a made-up example, because a file's fields are always the same. A GitHub repository or a public JSON source needs a read token set in the Platform as well, only for designing. An internal JSON source cannot be previewed. A plan against the Brain always runs on the component.

### Outbound: not built

*Proposed, for Jira (FEAT-2026-11-03-jira-sync).* A component with `capabilities: ["outbound"]` fetches the desired state from the Platform and writes it to another system, reporting per-item results and drift as counts.

## Read only, or read and write

Each source has an `access`: `read`, or `read_write`. A kind of integration declares which it allows (`builder/sources.py`), and a person chooses only where there is a choice. A source without the field reads. A component that cannot write to a source reports `source.write_unsupported` and does not run it, rather than quietly reading.

**Today every kind reads:** no component can write to a source, so `read_write` is offered nowhere. A share is also mounted read only, and a GitHub token needs only `Contents: read`. The first integration to write back is meant to be Jira (FEAT-2026-11-03-jira-sync). A write to a source falls under the same rule as one to the Brain: a dry run shown, and a person's approval.

Version 1.1 adds the first write, not built yet: a GitHub source, as a pull request (see "Write proposals"). A component that can write to a kind of source declares `write:<kind>` in `capabilities`, for GitHub `write:github`. `read_write` is offered for a source only when its kind allows it and a component on its site declares that capability, and it stays off until a person switches it on and publishes.

## Which component reads what

| Source | Component | How |
|---|---|---|
| JSON over HTTP | sync component | fetches the document; the runner reads it |
| GitHub repository | sync component | the runner reads the repository through the GitHub API, incrementally |
| File share, as records | sync component | the runner reads the mounted folder (`-v <folder>:/sources:ro`) with the files' own dates |
| File share, as documents | Knovas Connector | scan and upload (above) |

An empty or missing folder is refused rather than read: a share that did not mount looks exactly like one whose documents were all deleted.

## A new kind of source

A function that reads the source, or tells the runner where to read: `sync_component/fetchers.py`. See the component's README.

## Sites and Knovas Link

*Proposed, version 1.1. Not built.*

A **site** is one machine at the company, with one Docker network. **Knovas Link** (`KnovasComponents/KnovasLink/`, container `knovas-link`) is one component per site and the only process there that talks to Knovas: enrolment, the control plane, the Brain calls and the work channel. The site's other components talk only to Link, through its local API: `KnovasLink/contracts/local-api.md`. They hold no Knovas certificate and open no port. A new kind of source is a new local component, never a change to Link.

- **Enrolment.** Link enrols as described above, as kind `knovas_link`, with the site's one-time token. Its key is made on site and never leaves it. Its components register with Link, not with the Platform; Link's heartbeat reports them, and the Platform shows them under the site.
- **Certificates and the data path are DEC-0056's (proposed).** This version proposes one certificate per site, held by Link, as an input to that decision: a site then needs one certificate instead of one per component. Until a certificate comes by the route DEC-0056 decides, Link enrols and runs the control plane, but claims no job and writes nothing to the tenant (`waiting_for_certificate`).
- **Direct enrolment keeps working.** A component enrolled directly, as described above, keeps calling the Platform and the Brain itself. A component in link mode never calls the Platform: it does not enrol and holds no certificate, and its heartbeat, commands, configuration, package, reports, jobs and Brain calls go through Link.
- **Which component serves a source.** `GET /config` names for each source the component on the site that serves it, as `"component": "<name>"`. A component registers with Link under its name and gets the sources assigned to that name.
- **Commands fan out.** In the Platform's heartbeat answer to Link, a command meant for one of its components carries `"component": "<name>"`; Link delivers it on that component's next local heartbeat. Link reports the outcome with `POST /commands/<id>/done`, adding the same `"component": "<name>"`. A command without `component` is Link's own.
- **Additions to existing calls.** `GET /config` gives each source `fetch`: true when fetching originals is switched on, false by default, also for sources published before. Link's `POST /heartbeat` adds `certificate: present|waiting`, `work_channel: connected|down`, `probe: {last_at, ok, round_trip_ms}`, `components: [{name, kind, version, state, capabilities, local_ping_ms}]`, per source `{last_read_ok_at, last_run_status}`, and job counts by kind and outcome since the last beat. All of them are codes, counts, versions and times.

## Streams

*Proposed, version 1.1. Not built.*

A stream moves items, deletions and state from a source towards the Brain. Each message is one JSON object, `{type, source, external_id, version, content_hash, payload, state}`: `KnovasLink/contracts/stream-message.schema.json`.

| Type | Carries | Needs |
|---|---|---|
| `document` | an item and its indexed text | `source`, `external_id`, `version`, `payload {title, path, text}` |
| `tombstone` | an item the source no longer has | `source`, `external_id` |
| `generation` | the end of a complete listing | `source`, `payload {id, complete: true, counts}` |
| `state` | the source's position after everything before it | `source`, `state` |
| `record` | reserved for records | refused by every 1.1 receiver: `unsupported_type` |
| `principal` | reserved for permissions | refused by every 1.1 receiver: `unsupported_type` |
| `group` | reserved for permissions | refused by every 1.1 receiver: `unsupported_type` |
| `membership` | reserved for permissions | refused by every 1.1 receiver: `unsupported_type` |
| `permission` | reserved for permissions | refused by every 1.1 receiver: `unsupported_type` |

- **Identity before content.** `source` is the workspace source key (not the runner's `source_key` fact). `external_id` is the item's key in the source; for GitHub its path. `version` is the source's own version of the original; for GitHub the blob sha, the git hash of the raw bytes. `content_hash` is the Brain's hash of the normalised text it indexed. `version` and `content_hash` are never compared, and nothing is matched by name.
- **At least once, and idempotent.** A message may arrive twice; a replayed stream changes nothing.
- **Deletions** reach the Brain only through a `tombstone` or through absence from a completed `generation`, under the deletion guard, never through an incremental run.
- **A `state` is acknowledged** only after the receiver holds everything before it.
- **Where streams run in 1.1.** A stream is made and read inside the component that reads the source (the runner's GitHub and local sources). What reaches the Brain goes through today's upload routes, through Link, with a source reference `{source, external_id, version}`. A document uploaded before gets its reference without being sent again, through `PUT /secured/documents/source_ref`. A route for streams at Knovas comes later, with records.

## Jobs and the work channel

*Proposed, version 1.1. Not built.*

Knovas asks a site for work with **jobs**, `{id, kind, source, target_serial, deadline, actor, args}`: `KnovasLink/contracts/job.schema.json`. The answer to a job is a **result**: `KnovasLink/contracts/job-result.schema.json`.

| Kind | Args | What the component does |
|---|---|---|
| `fetch` | `{external_id, version?, max_bytes}`, `max_bytes` at most 25 MiB; the job's `source` is required | reads the original and posts it in parts, then the result ("Fetching originals") |
| `write` | `{mapping, node_key, external_id, expected_version, facts, mode: "proposal"}`; the job's `source` is required | stages the change where the source reviews it ("Write proposals") |
| `probe` | `{}` | nothing; the round trip measures the channel ("Probes") |

- **Not jobs.** `run`, `pause`, `check` and the other commands stay on the heartbeat, as above. `inventory` and `sample` stay proposed.
- **The actor** `{kind: user|agent|component|system, id, on_behalf_of?: {kind, id}}` is on every job. Whoever asks declares it. It is recorded, and never trusted for a decision until agent identity and the brokered principal assertion exist.
- **The work channel.** Link long-polls secure-api with the site's certificate:
  - `GET /secured/connector/jobs?source=a,b&wait=25` answers 200 `{job}` or 204. It claims one job of the caller's tenant for those sources, or a probe addressed to the caller's certificate serial. A claim holds a lease of 60 seconds. Every job it hands out carries `lease`, an opaque string of 16 to 128 characters that names this claim.
  - `POST /secured/connector/jobs/<id>/part?n=` takes the bytes of one part.
  - `POST /secured/connector/jobs/<id>/result` takes the result and answers 200 `{"accepted": true}`, or 409 `lease_lost` when the lease ended or the job has an outcome already. A late answer is discarded and counted.
  - Link sends the job's lease back as the header `X-Knovas-Lease` on every part and result. A missing lease, a stale one, or one of another claim of the same job is 409 `lease_lost`. Components never see the lease: Link removes it before it hands a job on, and its own local jobs carry none.
  - Long-polls and waiting fetches hold a server thread each, so they are capped per pod and per tenant; beyond the caps the answer is 429 with `Retry-After`.
- **On the site.** Link hands each job to the component that registered its kind and to which its source is assigned, and passes the parts and the result on unchanged and in order. No component for the kind: Link answers `unsupported`. Silent past the deadline: `unreachable`. The source's switch off: `refused` with `fetch_disabled` or `write_disabled`.
- **The tenant comes from the certificate,** on every claim, part and result. A job of one tenant is never visible to, claimable by or answerable from another.
- **Results.** The status is `ok`, `refused`, `conflict`, `unsupported`, `unreachable` or `failed`. A code says why: one of `KnovasLink/contracts/codes.json`, or a component's own of the same shape. A result holds codes, counts, versions, hashes and one reference, never text.

## Probes

*Proposed, version 1.1. Not built.*

A heartbeat says that a process runs. A probe says that the whole channel works.

- `POST /secured/connector/probe` answers 201 `{job_id}`. secure-api queues a `probe` addressed to the caller's certificate serial (`target_serial`); Link claims it through its long-poll and answers `ok`. The round trip is the measure.
- Link probes every 5 minutes, and when a person asks in the Builder. With each probe it also hands every registered component that answers probes a local `probe` job, with no `source` and no `target_serial`; the time to its result is the component's `local_ping_ms`. Link reports the last round trips in its heartbeat (`probe`, `local_ping_ms`).
- A probe from secure-api always carries `target_serial`; only a local probe has none.
- A site counts as online only when a probe went round recently; the Builder asks for one within the last 10 minutes. A state nobody measured is shown as unknown, never as fine.

## Write proposals

*Proposed, version 1.1. Not built.*

A write to a source is an intent with an actor, an expected version and a mode. Version 1.1 has one mode, `proposal`: the change is staged where the source reviews it, for GitHub a pull request, and never applied directly. `standing` is reserved for autonomous agents; a component refuses it with `mode_unsupported`.

1. An agent or a person proposes: `POST /secured/source-writes {node_id, facts, reason?, actor}`. The node must be visible, owned by a mapping, still in the source, and have a recorded `source_version`; otherwise the answer is 409 and names what is missing. A change that was rejected before is refused with `previously_rejected`: a rejection is a tombstone.
2. A person accepts it in the Builder (`POST /secured/source-writes/<id>/accept {actor}`) or rejects it with a reason.
3. Accepting queues a `write` job. Its `expected_version` is the node's `source_version`, its `mode` is `proposal`, the accepter is the actor and the proposer is `on_behalf_of`.
4. The component compiles the change from the mapping's `writes`: only facts the mapping names, each a plain field, never a computed attribute, with values checked by the source's own rules. It refuses with `not_writable`, `computed_attribute`, `value_not_allowed`, `not_invertible` or `unknown_mapping`.
5. When the item's version is not `expected_version`, the result is `conflict` and nothing is written. A credential that cannot write gives `token_cannot_write`. Otherwise the component stages the change, and the result is `ok` with `source_ref`, for GitHub the pull request's URL.
6. The Brain's value changes only with the next sync, after the source took the change.

Writing needs the source's `access` to be `read_write`, which is off until a person switches it on; otherwise Link refuses with `write_disabled`. Accepting a proposal is not yet bound to a verified person: secure-api records the declared actor, but the route authenticates the tenant's certificate.

## Fetching originals

*Proposed, version 1.1. Not built.*

The Brain never holds the original. Someone who may read a document gets the original from its source, through the work channel, as the source holds it.

- `GET /secured/documents/original?pointer=…`. The pointer is checked exactly as search filters it; one the caller may not see answers 404.
- The document needs a source reference `{source, external_id, version}`, set on upload or later with `PUT /secured/documents/source_ref`. Without one the answer is 409 `no_source_ref`.
- secure-api queues a `fetch` with a deadline of 20 seconds and `max_bytes` of 25 MiB. The component reads the original at `version` (for GitHub the blob) and posts it in parts of at most 4 MiB, numbered from 0 and in order, then the result with `version`, `bytes_sha256`, `mime`, `bytes` and `parts`.
- secure-api streams the bytes to the caller as they arrive, with `X-Knovas-Source-Version` and `Content-Type`, and recomputes the SHA-256. A gap or a mismatch ends the answer with an error, never with a short file reported complete. Nothing is written to disk, to a database or to a cache, and no gateway retries a fetch.
- No answer before the deadline gives 503 `source_unreachable`; a refusal gives 403 `source_refused` with the component's code. **Never the indexed text instead.**
- A fetched `version` that differs from the recorded one marks the document's reference stale.
- Every fetch writes one audit row: the declared actor, the pointer's hash, the bytes and the outcome.
- Fetching is off for a source until a person switches it on; otherwise Link refuses with `fetch_disabled`.

For GitHub the caller can check what it got: the git blob hash of the bytes equals `version`.
