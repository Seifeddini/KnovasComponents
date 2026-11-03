# Connector protocol v1: the contract

**Status: draft (KC-7a, BET-2026-11-sources-and-access).** The control plane and the records path are built and tested. The decisions marked *proposed* wait for DEC-0056; a change to them before the first company runs a component is cheap, and after that it is not.

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
| `GET /config` | | `{component, paused, schedule, brain_url, sources: [{workspace, key, kind, settings}], mappings: [{workspace, id, source}]}` |
| `GET /package` | | `{workspaces: [{workspace, files: {path: base64}}]}`: the compiled configuration of the workspaces it serves; definitions and source settings, no credential and no data |
| `POST /runs` | `{kind: dry_run\|apply, status: ok\|held\|failed, started_at, finished_at, duration_ms, added, changed, retired, errors, held: [{mapping, retire: {type: n}, share}], command}` | `{id}` |
| `POST /commands/<id>/done` | `{result: ok\|failed\|refused}` | |
| `POST /events` | `{events: [{code, counts}]}`, up to 100 | |
| `POST /checks` | `{checks: [{workspace, source, code, counts}]}`: can it read each source it serves? `source.ok` or a code; the latest only | `{stored}` |

**Commands:** `run {kind}`, `pause`, `resume`, `approve_plan {plan, mapping, reason}`, `reject_plan {plan, mapping}`, `reload_config`, `check`. A component that does not know a command answers `refused`. A `run` is finished by the `POST /runs` that names it in `command`; every other command by `/commands/<id>/done`.

**Compatibility:** within v1 only additions; a component ignores fields it does not know, and declares what it can in `capabilities`.

## The data path

*Proposed (DEC-0056, option A).* **A component writes to the Brain directly, with its own certificate; the Platform is the control plane only.** No customer data passes through the Platform.

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

*Proposed, for SA-05.* Declared through `capabilities: ["permissions"]`. Each item has a stable external id; a run ends with a marker that its listing was complete, so what is missing can be deleted safely. A permission names the source's own principals, which the Platform maps to access groups.

### Inventory and sample: not built

*Proposed, for the data explorer (FEAT-2026-11-02-builder-data-explorer, its decision D1).* `inventory {path}` and `sample {path, limit}` would carry names and field values into the Platform, which the rule above forbids today. It waits for that decision.

Until then, designing a mapping for a source read at the company works like this: a file share is designed on a made-up example, because a file's fields are always the same. A GitHub repository or a public JSON source needs a read token set in the Platform as well, only for designing. An internal JSON source cannot be previewed. A plan against the Brain always runs on the component.

### Outbound: not built

*Proposed, for Jira (FEAT-2026-11-03-jira-sync).* A component with `capabilities: ["outbound"]` fetches the desired state from the Platform and writes it to another system, reporting per-item results and drift as counts.

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
