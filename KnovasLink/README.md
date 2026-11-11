# Knovas Link

One component per site, and the only process there that talks to Knovas (connector protocol 1.2: 1.1 from KC-7a, and
since KG-2 each component's own schedule and pause, its sync status per source and the switch «automatisch anwenden»). The other
components on the site (the sync component, the Knovas Connector, any later reader of a source) talk only to Link,
through its local API; they hold no Knovas certificate and open no port. A new kind of source is a new local component,
never a change to Link. How Link fits with the sync component and the Brain Builder:
[the connection layer](../KnovasPlatform/docs/connection-layer.md).

**Status: built ahead of its bet's approval, not deployed.** The site's certificate follows DEC-0056 (option A, A1; see
"The certificate").
The contracts it is built against are in [`contracts/`](contracts/): the local API
([`local-api.md`](contracts/local-api.md)), the job, result and stream schemas, `codes.json`, pinned by
`contracts.lock`; Link refuses to start when a file it uses does not match its lock. Outside, Link speaks
[connector protocol v1.1](../KnovasPlatform/docs/connector-protocol-v1.md).

```
 site: one machine, one Docker network                                      Knovas
┌──────────────────────────────────────────────────────────┐
│ sync-component ──┐                                        │     ┌ Platform: enrolment, heartbeat, config,
│ knovas-connector ┼─ local API :8460 ─▶ knovas-link ───────┼────▶│   package, commands, reports
│ (any reader)  ───┘  token per component  (site key,        │     └ secure-api: Brain calls (allow-list),
│                     internal network     certificate)      │       work channel, probes
└──────────────────────────────────────────────────────────┘
```

## Running it

```bash
docker build -t knovas-link:1.2.0 KnovasLink
```

Settings come from the environment only; the install package's `knovas-link.env` holds the first two:

| Setting | |
|---|---|
| `LINK_PLATFORM_URL` | the Platform's protocol address, `https://…/api/connectors/v1` (required) |
| `LINK_ENROL_TOKEN` | the site's one-time token: for the first start, and again after «Token erneuern» |
| `LINK_PLATFORM_CA` | the CA of the Platform's TLS certificate, when it is not a public one |
| `LINK_BRAIN_CA` | the CA of the Brain's TLS certificate, when the enrolment answer brings none |
| `LINK_TENANT` | the site's tenant (client id), when the enrolment answer names none; `install-certificate` takes only a certificate for it |
| `LINK_CLIENT_CA` | a file with Knovas's client CA (PEM), when the enrolment answer brings none; `install-certificate` takes only a certificate it signed |
| `LINK_DATA_DIR`, `LINK_TOKENS_DIR` | `/data` and `/link` (the two volumes) |
| `LINK_PORT` | `8460` |
| `LINK_HEARTBEAT_SECONDS`, `LINK_PROBE_SECONDS` | `30` and `300` |
| `LINK_COMPONENTS` | further component names Link writes a token for from its start, comma separated (e.g. `jira-connector`): a connector of its own kind can register and declare itself before any source names it. Letters, digits, `-` and `_`; any other value stops Link (`settings.link_components`) |
| `LINK_PROTOCOL_V2` | `true`: connector protocol 2.0 (draft): Link takes connectors' declarations, declares its site, checks permits and routes `preview`, `act`, `reread`, `review_outcome`, `unstage`; off by default |

Volumes and network, as the Builder's install package lays them out (MVP-23,
`KnovasPlatform/components/docbridge_integration/src/builder/sites.py`):

- **`/data`**: the site's key, certificate, control token and the last configuration and package. The key is made
  here at the first start and never leaves; every file is readable by Link's user (10460) only.
- **`/link`**: Link writes one random token per component to `/link/<component>/token`: for `sync-component` and
  `knovas-connector` always, for every name in `LINK_COMPONENTS`, and for every component the Platform's configuration
  names. A token file already there is
  kept, so a restart does not lock components out. Each component mounts **only its own folder**, at `/link`, and
  reads `/link/token`.
- **Network:** the local API listens on 8460 on the site's internal network `knovas-site` (`internal: true`) with **no
  published port**. Every service is also on the egress network `knovas-egress`, so that each component reaches its
  own sources. Only Link can reach Knovas because only Link holds a Knovas certificate and token; a component holds
  neither and knows only Link's local address.

```yaml
services:
  knovas-link:
    image: knovas-link@${KNOVAS_LINK_DIGEST}          # by digest (release-images.md)
    env_file: [knovas-link.env]
    volumes: [link-data:/data, {type: volume, source: link-tokens, target: /link}]
    networks: [knovas-site, knovas-egress]
  sync-component:                                     # in link mode (MVP-20)
    image: sync-component@${SYNC_COMPONENT_DIGEST}
    env_file: [knovas-sources.env]                    # its sources' credentials only (a connector of its own kind
                                                      # reads its own knovas-<kind>.env, never this file)
    environment: {SYNC_LINK_URL: "http://knovas-link:8460", SYNC_DATA_DIR: /data}
    volumes:
      - sync-component-data:/data
      - {type: volume, source: link-tokens, target: /link, read_only: true, volume: {subpath: sync-component}}
    networks: [knovas-site, knovas-egress]
    depends_on: [knovas-link]
networks:
  knovas-site: {internal: true}
  knovas-egress: {}
volumes:
  link-data: {}
  link-tokens: {}
  sync-component-data: {}
```

At start Link enrols if it has not, or if `LINK_ENROL_TOKEN` is a token it did not enrol with («Token erneuern»), as
kind `knovas_link`. A refused or expired token stops it with `enrol.<code>` in the log; nothing is kept then, and an
earlier key stays with its certificate.

## The certificate (DEC-0056, option A with A1)

Every site's Link has **its own certificate from Knovas's client CA**, for its own key. If the Platform's enrolment
answer carries a certificate, Link uses it for secure-api. Production's secure-api cannot sign
(`/secured/sign_certificate` answers 503), so the Platform's deployment sets `BUILDER_CONNECTOR_ISSUE_CERTIFICATES=false`
and issues none at enrolment. **Link is then in the state `waiting_for_certificate`:** it runs its control plane
(heartbeat, configuration, package, commands, reports), but claims no job, sends no probe, and relays no Brain call
(503 `waiting_for_certificate`). Its heartbeat says `site.certificate: "waiting"` and `GET /local/v1/ping` says
`certificate: "waiting"`.

At enrolment the Platform records the **fingerprint of Link's request** (SHA-256 of the public key Link made) for the
site, and its answer names the site's **tenant** and **Knovas's client CA** (`BUILDER_CONNECTOR_CLIENT_CA`), which Link
keeps beside its key (S-4 M2; BB-07 B3/B4). The certificate comes this way:

1. **At the site:** the request, from the key enrolment made (the key never leaves the volume; only the request is
   printed). Its subject is `CN=knovas-link` (`LINK_NAME`) and nothing else. `certificate-fingerprint` prints the
   fingerprint the Builder's site page shows.

   ```bash
   docker compose exec -T knovas-link python -m knovas_link certificate-request > site.csr
   docker compose exec -T knovas-link python -m knovas_link certificate-fingerprint
   ```

2. **At Knovas:** one Knovas admin confirms and signs today through internal-api's `/sign_certificate`; later the
   issuance job signs with the HSM-held key (DEC-0062).
   - **Compare first.** The request's subject is the common name only (`openssl req -in site.csr -noout -subject`), and
     its fingerprint is the one on the site page:
     `openssl req -in site.csr -noout -pubkey | openssl pkey -pubin -outform DER | sha256sum`. If it differs, the
     request is not this site's: do not sign it.
   - **Sign in-cluster only.** With an admin JWT, `/sign_certificate` answers only through a port-forward to
     internal-api or `kubectl exec`, never on the public host (404 there, ADR-0005):

     ```bash
     kubectl port-forward deployment/internal-api 5000:5000 -n knovas-prod   # knovas-dev for dev
     curl -s -X POST http://localhost:5000/sign_certificate \
       -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' \
       -d '{"csr": "<site.csr>", "customer_id": "<the tenant client id>", "organisation": "<company name>", "validity_days": 365}'
     ```

     The CA sets the tenant from `customer_id` (its one OU `Semantix-Customer-<id>`), stores the certificate record and
     answers `certificate`, `certificate_chain`, `serial_number`, `expires_at`. Reference: the Knovas
     Employee Kit, `api/Certificate_Management.md`.
   - **Record it in the Builder:** the `certificate` on the site page, «Zertifikat erfassen». The Builder takes it only
     for the recorded request's key and the Platform's tenant (and the client CA, where configured); from then on the
     Platform requires that certificate's fingerprint on every call of this Link.
   - The `certificate` (optionally followed by the chain) goes back to the site as `site.crt`. The site's commands stay
     the same once the issuance job signs.

3. **At the site:** the certificate, installed.

   ```bash
   docker compose exec -T knovas-link python -m knovas_link install-certificate < site.crt
   ```

   `install-certificate [FILE|-]` (standard input by default) writes `component.crt`, readable by Link's user only,
   only when the certificate is for Link's stored key, valid now, issued for client authentication (extended key
   usage `clientAuth`), signed by Knovas's client CA and for the site's tenant. Otherwise it writes nothing and exits 1
   with a code from `codes.json`, in English and German: `certificate.wrong_key`, `certificate.not_valid_now`,
   `certificate.not_client_auth`, `certificate.wrong_issuer`, `certificate.wrong_tenant`, `certificate.unreadable`, or
   `certificate.not_enrolled` (no key yet: start Link with its token first). The client CA and the tenant are those the
   Platform named at enrolment; `LINK_CLIENT_CA` (a file) and `LINK_TENANT` name them where it did not, and win. Knowing
   neither, Link installs nothing (`certificate.no_client_ca`, `certificate.no_tenant`): a Link that enrolled before the
   Platform named them enrols again after «Token erneuern». On success it prints the serial.

**Until the certificate is recorded in the Builder** (with `BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=true`, the default),
the Platform takes Link's heartbeat only and gives it no command; configuration, package, commands and reports are
refused (`certificate_not_recorded`). Link keeps beating and keeps its last configuration.

**No restart is needed:** a Link waiting for its certificate picks it up at its next heartbeat
(`LINK_HEARTBEAT_SECONDS`, 30 s by default), logs `certificate.picked_up`, and from then on claims jobs, probes and
relays. A certificate that **replaces** one Link already uses (a renewal) is taken at the next start:
`docker compose restart knovas-link`; the command says so. `-T` keeps docker compose from adding a terminal, which
would turn the request's line ends into CRLF and refuse the redirected standard input.

**Revocation:** an admin revokes the site's certificate by its serial through internal-api's
`POST /revoke_certificate`; secure-api then refuses the site, and only this site stops. «Token erneuern» makes Link
enrol again with a new key, so the certificate step is repeated after it.

**Only to test on one's own machine, never for a company.** Like the sync component's `SYNC_BRAIN_CERT` and
`SYNC_BRAIN_KEY`, Link takes the tenant's own certificate from `LINK_BRAIN_CERT` and `LINK_BRAIN_KEY`, but only with
`LINK_TEST_ONLY_SHARED_CERTIFICATE=true` and only while the Platform issued none; without the switch they are ignored
with a warning. With it, Link warns loudly at start and sends `warnings: ["certificate.test_only_shared"]` in every
heartbeat. A leak exposes the whole tenant, and the site cannot be revoked on its own (DEC-0040, DEC-0056).

## What Link does

- **Local API** ([`local-api.md`](contracts/local-api.md)): every call carries `Authorization: Bearer <token>` and
  `X-Component: <name>`; otherwise 401 `bad_token` or `unknown_component`. `register` answers the sources assigned to
  the component's name (no credential); `heartbeat` hands over the Platform's commands for that component once;
  `commands/<id>/done`, `config`, `package` (cached) and `ping` (never calls Knovas). `config`'s `brain_url` is Link's
  relay, `/local/v1/brain`.
- **Brain relay** (MVP-17): `/local/v1/brain/<path>` to secure-api with the site's certificate, method, query and body
  unchanged, secure-api's answer unchanged, never retried; only `secured/graph/…`, the upload and delete routes,
  `secured/documents`, `secured/documents/source_ref` and `secured/folder_rules`. Anything else is 403 `not_relayed`.
- **Reports** (MVP-17): runs, events and checks go to the Platform with `X-Sub-Component: <name>`, after the
  Platform's own checks of codes and counts. Where the Platform would drop a count that does not fit, Link refuses the
  report (400 `bad_field`), as it does an unknown field or a code that ends like a file name.
- **Work channel** (MVP-18): one long-poll `GET /secured/connector/jobs?source=…&wait=25`; jobs queued per component,
  which long-poll `GET /local/v1/jobs?wait=25`. Parts (at most 4 MiB, numbered from 0) are streamed through, never held
  whole; results are checked against the schema and passed on unchanged. The claim's `lease` stays with Link and goes
  back as `X-Knovas-Lease` on every part and result; after 409 `lease_lost` nothing more of that claim is relayed.
  Link answers itself: `unsupported` (no component for the kind), `unreachable` (silent past the deadline),
  `refused` `fetch_disabled` / `write_disabled` (the source's switch is off), `failed` `invalid_job` (a job that does
  not validate, never handed out) and `failed` `part_not_accepted` (a part refused by Link or not taken by Knovas
  ends the fetch; nothing more of it is relayed). Reconnects with backoff; a 429 waits out
  `Retry-After`.
- **Probes** (MVP-18): every 5 minutes and on the Platform's `check` command: `POST /secured/connector/probe`, answered
  when it comes round, and a local probe job to each component that named `probe` (its `local_ping_ms`).
- **Heartbeat** (MVP-19): protocol v1's, plus `site`: `certificate`, `work_channel`, `probe {last_at, ok,
  round_trip_ms}`, `components [{name, kind, version, state, capabilities, local_ping_ms}]`, `sources [{workspace,
  key, last_read_ok_at, last_run_status}]`, `jobs {kind: {status: n}}` since the last beat. A value nobody measured is
  `null`, never a good one.
- **Protocol 1.2 (KG-2):** a component's local heartbeat may carry its sync status per source (`synced_commit`,
  `synced_at`, `checked_at`, `next_check_at`, `planned_commit`, `held_back`, `last_apply`; `sync_status.py` checks it
  and refuses anything shaped like a name with 400 `bad_field`); Link keeps it for the component's own sources and adds
  it to that source's entry in `site.sources`. The Platform's `components` (in its heartbeat answer and `/config`) give
  each component its own `schedule` and `paused`; Link answers the component's heartbeat and configuration with them
  (paused when the site or the component is), and with its own where the Platform names none. Each source passes on
  `auto_apply` (register and configuration).

- **Connector protocol 2.0 (draft, `LINK_PROTOCOL_V2=true`; off by default)** ([the protocol
  text](../KnovasPlatform/docs/connector-protocol-v2.md), contracts in `contracts/v2/` with their own lock; FEAT-2026-11-10-write-back
  A-5). A connector registers with its declaration; Link declares its site to the Brain (`PUT
  /secured/connector/v2/site`: its component id, protocol, version, and per connector its declaration and the assigned
  sources of the kind it declared), pins the Brain's permit keys from `GET /secured/connector/v2/permit-keys` only
  (`<data>/state/v2/permit-keys.json`), and routes `preview`, `act`, `reread`, `review_outcome` and `unstage` to the
  connector that declared the action and staging (`refused action_not_declared` / `staging_unsupported`,
  `write_disabled` for an act on a source that only reads). Before anything else about an `act` (its deadline, the
  source's switch, the connector's declaration) Link checks every permit in the Alloy model's order (`write_path.py`):
  `permit_missing`, `permit_bad_signature`, `permit_wrong_site`, `permit_mismatch`, a used permit answered with its
  first result (`repeat: true`, `permit_used`, also after expiry and whatever changed at the site since),
  `permit_expired`. It reserves the permits in its used-permit store (`used-permits.json`, 0600) and writes the site's
  write log (`write-log.jsonl`) before the job goes on, else `failed write_log_unavailable`; a second delivery of a
  permit whose first is still with the connector waits for that result and never reaches the connector, a claim of the
  same act again keeps the running hand-out under the new lease, and the first result is kept under every permit id and
  never replaced. A permit whose delivery ended without a result (Link restarted, the connector stayed silent past the
  deadline) goes on again before its expiry, so the connector's marker of the write answers, and is refused
  `permit_expired` after it. Every refusal is a line of the write log, a permit that did not verify under the id it
  names. A token Link cannot read (any spelling but the one canonical encoding, a time past the year 9999, deep
  nesting) is `permit_bad_signature`, never an error (the verifier's findings on Phase A, M1, M2, L1, L2, L5). With
  the switch off a v2 job is answered `unsupported protocol_unsupported` and a declaration is ignored; 1.x jobs go
  their 1.x way either way.

**Logs** carry codes, counts and durations only: never a path, a document's name, a token, a permit or a certificate.
There is no access log, because a relayed path or query may name a document.

## Tests

```bash
python3 -m pip install starlette uvicorn httpx jsonschema cryptography pytest
python3 -m pytest KnovasLink/tests -q -o addopts="" -p no:cacheprovider </dev/null
```

In-process fakes of the Platform and of secure-api implement the shared contract (`tests/conftest.py`); the companies
and sources in the tests are made up. `tests/test_generic.py` fails on any client's tenant, name, repository, mapping
or type in Link's code, image or this page.

## Not built yet

- The issuance job that signs with the HSM-held client CA key (DEC-0062); until then one Knovas admin confirms and
  signs (above).
- Credentials sealed to Link's key (CL-1, I2).
- Jobs bound to one site: a `fetch` or `write` job names no certificate serial yet, so any certificate of the tenant
  that names the source can claim it. Needed before a tenant has a second site. A v2 `act` is bound to one site by its permits (Link
  refuses a permit naming another certificate serial); the Brain's claim by serial is task B2-4.
- A narrower relay: everything below `secured/graph/` passes with every method (accepted for now, see
  [connector protocol v1.1](../KnovasPlatform/docs/connector-protocol-v1.md), "Sites and Knovas Link").

Built since this page first listed them: the install package and the Builder's site page (MVP-23, MVP-28) and the
sync component's link mode (MVP-20). No image is released yet: see
[release-images.md](../KnovasPlatform/docs/deployment/release-images.md).
