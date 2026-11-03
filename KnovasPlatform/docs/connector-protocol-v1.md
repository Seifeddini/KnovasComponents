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

`POST /api/connectors/v1/enrol` with `{token, csr, version, capabilities}`, using the one-time token (24 hours). The answer is `{component, control_token, certificate, certificate_chain, config}`. The private key is made by the component and never leaves it. The Brain signs the request for this Platform's tenant. The control token is shown this once and kept as a hash.

## Every other call

`Authorization: Bearer <control token>`, `X-Component: <id>`, and the client certificate issued at enrolment. A TLS terminator verifies the certificate and sets `X-Client-Cert-Fingerprint` (SHA-256 of the DER, hex); it must never pass on one the client sent. Without a terminator that does this, the deployment says so explicitly (`BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=false`) and the control token alone authenticates.

| Call | Body | Answer |
|---|---|---|
| `POST /heartbeat` | `{state: online\|degraded, version, lag_seconds, open_errors, capabilities}` | `{paused, schedule, commands: [{id, command, args}]}`; a command is delivered once |
| `GET /config` | | `{component, paused, schedule, brain_url, sources: [{workspace, key, kind, settings}], mappings: [{workspace, id, source}]}` |
| `GET /package` | | `{workspaces: [{workspace, files: {path: base64}}]}`: the compiled configuration of the workspaces it serves; definitions and source settings, no credential and no data |
| `POST /runs` | `{kind: dry_run\|apply, status: ok\|held\|failed, started_at, finished_at, duration_ms, added, changed, retired, errors, held: [{mapping, retire: {type: n}, share}], command}` | `{id}` |
| `POST /commands/<id>/done` | `{result: ok\|failed\|refused}` | |
| `POST /events` | `{events: [{code, counts}]}`, up to 100 | |

**Commands:** `run {kind}`, `pause`, `resume`, `approve_plan {plan, mapping, reason}`, `reject_plan {plan, mapping}`, `reload_config`. A component that does not know a command answers `refused`. A `run` is finished by the `POST /runs` that names it in `command`; every other command by `/commands/<id>/done`.

**Compatibility:** within v1 only additions; a component ignores fields it does not know, and declares what it can in `capabilities`.

## The data path

*Proposed (DEC-0056, option A).* **A component writes to the Brain directly, with its own certificate; the Platform is the control plane only.** No customer data passes through the Platform.

### Records: built

The component fetches the source and writes the document where the compiled mappings read it. On a dry run, the mapping runner plans; the plan stays on site, and only its counts are reported. On an apply, the runner writes exactly that plan, once.

- **State:** the runner's state per source, and its provenance record on the tenant.
- **Deletions:** what the source no longer states is retired, never deleted. The deletion guard holds a plan that would retire too much (`status: held`), until a person approves it with a reason (`approve_plan`).

### Documents and permissions: not built

*Proposed, for KN-1 and SA-05.* Declared through `capabilities: ["content"]` and `["permissions"]`. Each item has a stable external id; a run ends with a marker that its listing was complete, so what is missing can be deleted safely. A permission names the source's own principals, which the Platform maps to access groups.

### Inventory and sample: not built

*Proposed, for the data explorer (FEAT-2026-11-02-builder-data-explorer, its decision D1).* `inventory {path}` and `sample {path, limit}` would carry names and field values into the Platform, which the rule above forbids today. It waits for that decision.

### Outbound: not built

*Proposed, for Jira (FEAT-2026-11-03-jira-sync).* A component with `capabilities: ["outbound"]` fetches the desired state from the Platform and writes it to another system, reporting per-item results and drift as counts.

## A new kind of source

A function that reads the source and writes the document its mappings read: `sync_component/fetchers.py`. See the component's README.
