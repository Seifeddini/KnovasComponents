# Knovas Link: the local API (protocol 1.1)

**Status: contract, not built (MVP-1, KC-7a).** Knovas Link is the one component per site that talks to Knovas. The other
components on the site talk only to Link, through this API. Outside, Link speaks connector protocol v1
(`KnovasPlatform/docs/connector-protocol-v1.md`). The JSON shapes are the schemas in this folder; `contracts.lock` pins
every file this page relies on.

## Where it listens

- Only on the site's internal Docker network `knovas-site` (`internal: true`), with no published port. Link alone is
  also on the outbound network. A component reaches Link at the address the install package gives it.
- Plain HTTP. Names and bytes travel on this network, and never leave the machine except through Link.
- A component opens no port and holds no Knovas certificate. It calls Link; Link never calls it.
- A component in link mode never calls the Platform or the Brain. Its heartbeat, commands, configuration, package,
  reports, jobs and Brain calls all go through this API.

## Authentication

Every call carries two headers:

- `Authorization: Bearer <token>`: the token Link wrote to `/link/<component>/token`. Link writes one random token per
  component. Each component mounts only its own folder.
- `X-Component: <component name>`: the name the token belongs to.

A call without both, or with a token that does not belong to the name, is refused with 401 (see Endpoints). Every
endpoint below needs them.

## Endpoints

Codes are written as the HTTP status and the code, for example 409 `lease_lost`. `bad_field`, `bad_token`,
`unknown_component` and `unknown_command` are protocol v1's request errors, used here as the Platform uses them; every
other code is in `codes.json`.

| Endpoint | Body | Answer | Other answers |
|---|---|---|---|
| `POST /local/v1/register` | `{component, kind, version, capabilities, job_kinds}` | 200 `{sources}` | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component`, 503 `unreachable` |
| `POST /local/v1/heartbeat` | as protocol v1's `POST /heartbeat` | 200 `{paused, schedule, commands}` | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component` |
| `POST /local/v1/commands/<id>/done` | `{result: ok\|failed\|refused}` | the Platform's answer | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component`, 404 `unknown_command`, 503 `unreachable` |
| `GET /local/v1/config` | | 200, protocol v1's `GET /config` answer | 401 `bad_token`, 401 `unknown_component`, 503 `unreachable` |
| `GET /local/v1/package` | | 200, protocol v1's `GET /package` answer | 401 `bad_token`, 401 `unknown_component`, 503 `unreachable` |
| `GET /local/v1/ping` | | 200 `{version, certificate, work_channel}` | 401 `bad_token`, 401 `unknown_component` |
| `GET /local/v1/jobs?wait=25` | | 200 `{job}`, or 204 | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component` |
| `POST /local/v1/jobs/<id>/part?n=` | bytes, `application/octet-stream` | 200 `{"accepted": true}` | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component`, 409 `lease_lost`, 413 `bad_field`, 503 `unreachable` |
| `POST /local/v1/jobs/<id>/result` | a job result | 200 `{"accepted": true}` | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component`, 409 `lease_lost`, 503 `unreachable` |
| `ANY /local/v1/brain/<path>` | as secure-api's route | secure-api's answer, unchanged | 401 `bad_token`, 401 `unknown_component`, 403 `not_relayed`, 503 `waiting_for_certificate`, 503 `unreachable` |
| `POST /local/v1/report/runs` | as protocol v1's `POST /runs` | the Platform's answer | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component`, 503 `unreachable` |
| `POST /local/v1/report/events` | as protocol v1's `POST /events` | the Platform's answer | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component`, 503 `unreachable` |
| `POST /local/v1/report/checks` | as protocol v1's `POST /checks` | the Platform's answer | 400 `bad_field`, 401 `bad_token`, 401 `unknown_component`, 503 `unreachable` |

An error answer is `{"error": "<code>", "message": "…"}`, as the Platform's protocol routes answer. The message is
English and never holds a name or a value.

### `POST /local/v1/register`

```json
{"component": "sync", "kind": "sync_component", "version": "0.2.0",
 "capabilities": ["dry_run", "apply", "package", "source_checks", "write:github"], "job_kinds": ["fetch", "write"]}
```

- `component` is the name in `X-Component`. `kind` is the component's kind as the Platform knows it.
- `capabilities` as in protocol v1. A component that can write to a source of kind `k` declares `write:<k>`.
- `job_kinds`: the job kinds it answers, from `fetch`, `write`, `probe`. A kind Link does not know is ignored.
- A component registers when it starts. A later registration replaces the earlier one.

The answer lists the sources assigned to this component's name. The Platform's configuration, which Link receives
through protocol v1's `GET /config`, names for each source the component that serves it, as `"component": "<name>"`;
Link answers a component with the sources whose `component` is the name it registered. The answer holds no credential.

```json
{"sources": [{"workspace": "ws-1", "key": "handbook", "kind": "github", "access": "read_write",
              "settings": {"repository": "example-org/handbook", "ref": "main"}, "fetch": true}]}
```

`access` is `read` or `read_write`; `fetch` is true when fetching originals is switched on for the source. Both are off
until a person switches them on and publishes. 503 `unreachable`: Link has never had a configuration from the Platform.

### `POST /local/v1/heartbeat` and `POST /local/v1/commands/<id>/done`

The control plane of protocol v1, through Link. A component in link mode sends its heartbeat here, never to the
Platform.

- The body is exactly protocol v1's heartbeat body: `{state: online|degraded, version, lag_seconds, open_errors,
  capabilities}`. Link reports what it learns in its own heartbeat's `components`.
- The answer is shaped as the Platform's: `{paused, schedule, commands: [{id, command, args}]}`. `paused` and
  `schedule` are those of the Platform's last answer to Link.
- **Commands fan out.** In the Platform's heartbeat answer to Link, a command meant for a component carries
  `"component": "<name>"`. Link delivers it once, on that component's next local heartbeat, without the `component`
  field. A command without `component` is Link's own.
- The component finishes a command with `POST /local/v1/commands/<id>/done {result: ok|failed|refused}`. Link passes
  it to the Platform's `POST /commands/<id>/done` with `"component": "<name>"` added, and answers with the Platform's
  answer. 404 `unknown_command`: no open command of that id was delivered to this component. 503 `unreachable`: the
  Platform did not answer.
- A `run` command is finished, as in protocol v1, by the run report that names it in `command`
  (`POST /local/v1/report/runs`).

### `GET /local/v1/config` and `GET /local/v1/package`

The bodies of protocol v1's `GET /config` and `GET /package`, as Link last had them from the Platform, limited to the
sources assigned to this component's name and their workspaces. 503 `unreachable`: Link has no copy and cannot reach the Platform.

### `GET /local/v1/ping`

```json
{"version": "1.1.0", "certificate": "present", "work_channel": "connected"}
```

`certificate` is `present` or `waiting`; `work_channel` is `connected` or `down`. Ping never calls Knovas. A container
health check and the components use it to see that Link is up and what it is waiting for.

### `GET /local/v1/jobs?wait=25`

A long-poll. `wait` is whole seconds from 0 to 25; a larger number counts as 25, and anything else is 400 `bad_field`.
The answer is 200 `{"job": <job>}` (`job.schema.json`) or, when nothing came within `wait`, 204.

- A component gets only jobs of a kind it named in `job_kinds`, for a source assigned to its name.
- **Components never see a lease.** secure-api sets an opaque `lease` on every job it hands Link. Link keeps it and
  removes it before it hands the job out; Link's own local jobs carry none.
- A job is handed out once. Link answers for the component if it stays silent: past the job's `deadline` Link reports
  `unreachable` to Knovas, and the component's late part or result gets 409 `lease_lost`.
- While Link waits for its certificate it claims no job, so this answers 204; ping says why.
- A job whose kind no registered component named is answered `unsupported` by Link and never handed out. A job for a
  source whose switch is off is answered by Link with `refused` and `fetch_disabled` or `write_disabled`.
- **Local probes.** Link measures each component's local round trip with a probe job of its own: `kind` `probe`,
  `args` `{}`, no `source` and no `target_serial`, handed out through this endpoint with each channel probe. A component
  names `probe` in `job_kinds` and answers it at once with `{"status": "ok", "duration_ms": …}`. The time from hand-out
  to result is the component's `local_ping_ms` in Link's heartbeat. A component that did not name `probe` has no local
  round trip, and it is shown as unknown, never as fine.

### `POST /local/v1/jobs/<id>/part?n=`

Only for a `fetch` job the caller holds. The body is raw bytes of the original, `application/octet-stream`.

- At most 4 MiB per part: 4194304 bytes. A larger body is 413 `bad_field`.
- `n` counts from 0 and goes up by one: 0, 1, 2, … A missing `n`, a gap, a repeat, an empty part, or a part for a job
  that is not a fetch is 400 `bad_field`.
- A refused part ends the fetch: Link answers the job `failed` to Knovas, and every later part or result for it is
  409 `lease_lost`. A fetch is never reported complete with a part missing.
- Link passes each accepted part on unchanged and in order, with the job's lease as the header `X-Knovas-Lease`.
  503 `unreachable`: Knovas did not take the part; the fetch has failed.

### `POST /local/v1/jobs/<id>/result`

The body is one job result (`job-result.schema.json`); one that does not validate is 400 `bad_field`. Link passes it on
unchanged, with the job's lease as the header `X-Knovas-Lease`. The answer is secure-api's: 200 `{"accepted": true}`, or
409 `lease_lost` when the job's lease ended, the job has an outcome already, or the lease sent is missing, stale or
another claim's. 503 `unreachable`: secure-api did not answer.

### The lease towards secure-api

Every job secure-api hands out carries `lease`, an opaque string of 16 to 128 visible ASCII characters. Link sends it
back as the header `X-Knovas-Lease` on every `POST /secured/connector/jobs/<id>/part` and
`POST /secured/connector/jobs/<id>/result` of that job. A missing lease, a stale one, or one of another claim of the
same job is 409 `lease_lost`. The lease stays inside Link: a component never sees it and never sends it.

A fetch's result is posted after its parts:

- `parts` is the number of parts posted and `bytes` their total, at most 26214400 (25 MiB) and at most the job's
  `max_bytes`. An empty original has no parts: `bytes` 0, `parts` 0.
- `bytes_sha256` is the SHA-256 of all parts in order, as 64 lower-case hex digits. `version` is the version read, for
  GitHub the blob sha, which is the git hash of the same bytes.
- secure-api checks parts, size and hash while it relays the bytes, and ends the fetch with an error on any gap or
  mismatch.

Which status and code a component answers with:

| Status | Code | When |
|---|---|---|
| ok | none (`null`) | done; for a write, `source_ref` is where the source staged it (GitHub: the pull request) |
| refused | `not_found` | the item is not in the source at that version |
| refused | `mode_unsupported` | a write whose `mode` is not `proposal`, even though such a job fails `job.schema.json` |
| refused | `token_cannot_write` | the credential cannot write (GitHub: needs contents and pull requests write) |
| refused | `not_writable`, `computed_attribute`, `value_not_allowed`, `not_invertible`, `unknown_mapping` | the inverse mapping refused the write |
| conflict | `conflict` | the item's version is not `expected_version`; `version` is the one found; nothing written |
| failed | any code, or none | anything else |

A component may answer its own codes of the same shape (`^[a-z][a-z0-9_.]{1,60}$`), never text or a name. Link answers
three outcomes itself: status `unsupported` with code `unsupported`, `unreachable` with `unreachable`, and `refused`
with `fetch_disabled` or `write_disabled`.

### `ANY /local/v1/brain/<path>`

Relayed to secure-api at `/<path>`, with the same method, query and body, and the site's certificate. The answer is
secure-api's, unchanged. Only an allow-list is relayed: exact paths, except `secured/graph/`, which covers everything
below it.

- `secured/graph/**`
- the document upload and delete routes: `secured/init_document_transmission`, `secured/transmit_document_part`,
  `secured/delete_information_object`
- `secured/documents`, `secured/documents/source_ref`, `secured/folder_rules`

Anything else, for example `secured/documents/original` or `secured/connector/jobs`, is 403 `not_relayed`. Without a
certificate Link relays nothing: 503 `waiting_for_certificate`. 503 `unreachable`: secure-api did not answer. Link
never retries a relayed call.

### `POST /local/v1/report/{runs,events,checks}`

The bodies and answers of protocol v1's `POST /runs`, `POST /events` and `POST /checks`; Link forwards them to the
Platform as a sub-component. Link first checks that they hold only codes and counts, as the Platform does, and refuses
anything else with 400 `bad_field`. 503 `unreachable`: the Platform did not answer.

## Where each code appears

| Code | Who answers it | Where |
|---|---|---|
| `unsupported_type` | every stream receiver of 1.1 | a `record`, `principal`, `group`, `membership` or `permission` message |
| `unsupported` | Link | job result `unsupported`: no component named the job's kind for its source |
| `unreachable` | Link | job result `unreachable` past the deadline; 503 on the local API when Knovas or the Platform did not answer |
| `lease_lost` | secure-api, Link | 409 on a part or result after the lease ended or the job had an outcome, or with a missing, stale or other claim's `X-Knovas-Lease` |
| `source_unreachable` | secure-api | 503 on `GET /secured/documents/original`: no answer before the deadline |
| `source_refused` | secure-api | 403 on `GET /secured/documents/original`: the job was refused; the component's code is given |
| `no_source_ref` | secure-api | 409 on `GET /secured/documents/original`: the document has no source reference |
| `not_found` | component | job result `refused` |
| `conflict` | component | job result `conflict` |
| `mode_unsupported` | component | job result `refused` |
| `token_cannot_write` | component | job result `refused` |
| `fetch_disabled` | Link | job result `refused`: fetching is off for the source |
| `write_disabled` | Link | job result `refused`: the source's `access` is `read` |
| `not_writable` | component | job result `refused`, from the inverse mapping |
| `computed_attribute` | component | job result `refused`, from the inverse mapping |
| `value_not_allowed` | component | job result `refused`, from the inverse mapping |
| `not_invertible` | component | job result `refused`, from the inverse mapping |
| `unknown_mapping` | component | job result `refused`, from the inverse mapping |
| `previously_rejected` | secure-api | `POST /secured/source-writes`: the same change was rejected before |
| `waiting_for_certificate` | Link | 503 on `/local/v1/brain/…`; `certificate: waiting` in ping and in Link's heartbeat |
| `not_relayed` | Link | 403 on `/local/v1/brain/…` for a route outside the allow-list |

## Open and closed objects

Protocol v1 says a component ignores fields it does not know. These contracts keep that wherever ignoring is safe, and
close an object where an unknown field must not be ignored or must not ride along.

| Object | | Why |
|---|---|---|
| stream message | open | new fields and types come with later steps; a receiver ignores what it does not know |
| its `payload` and `state` | open | the item's own content and the source's own position |
| job | open | Knovas may add a field; a component ignores it. Its `lease` never reaches a component |
| job `actor` and its `on_behalf_of` | closed | recorded as declared in audit rows; nothing else rides along |
| job `args` (fetch, write, probe) | closed | instructions: one a component does not know is never ignored |
| job result | closed | stored as the job's outcome and counted: codes, counts, versions, hashes and one reference, never text |

## Files in this folder

- `stream-message.schema.json`, `job.schema.json`, `job-result.schema.json`: JSON Schema 2020-12, `$id` under
  `https://knovas.ch/connector/v1.1/`.
- `codes.json`: every code of this layer, each with a one-line meaning.
- `contracts.lock`: `{"version", "files": {name: sha256 of the file's bytes}}` for the schemas, `codes.json` and this
  page. Change a file, then its line in the lock, in the same commit. A copy elsewhere (the runner's stream schema) is
  pinned by the same hash. `.gitattributes` keeps the files' line endings LF on every checkout, so the hashes hold.
- `vectors/valid/*.json` and `vectors/invalid/*.json`: `{schema, description, instance}`; an invalid one also has
  `breaks`, the path of the field it breaks. A file is named `<stream|job|result>.<what it shows or breaks>.json`.
  `KnovasLink/tests/test_contracts.py` runs them.
