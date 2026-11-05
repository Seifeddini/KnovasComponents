# Knovas Link

One component per site, and the only process there that talks to Knovas (connector protocol 1.1, KC-7a). The other
components on the site (the sync component, the Knovas Connector, any later reader of a source) talk only to Link,
through its local API; they hold no Knovas certificate and open no port. A new kind of source is a new local component,
never a change to Link.

**Status: built ahead of its bet's approval, not deployed.** Certificates wait for DEC-0056 (see "The certificate").
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
docker build -t knovas-link:1.1.0 KnovasLink
```

Settings come from the environment only; the install package's `knovas-site.env` holds the first two:

| Setting | |
|---|---|
| `LINK_PLATFORM_URL` | the Platform's protocol address, `https://…/api/connectors/v1` (required) |
| `LINK_ENROL_TOKEN` | the site's one-time token: for the first start, and again after «Token erneuern» |
| `LINK_PLATFORM_CA` | the CA of the Platform's TLS certificate, when it is not a public one |
| `LINK_BRAIN_CA` | the CA of the Brain's TLS certificate, when the enrolment answer brings none |
| `LINK_DATA_DIR`, `LINK_TOKENS_DIR` | `/data` and `/link` (the two volumes) |
| `LINK_PORT` | `8460` |
| `LINK_HEARTBEAT_SECONDS`, `LINK_PROBE_SECONDS` | `30` and `300` |

Volumes and network, as the install package (MVP-23) is to lay them out:

- **`/data`**: the site's key, certificate, control token and the last configuration and package. The key is made
  here at the first start and never leaves; every file is readable by Link's user (10460) only.
- **`/link`**: Link writes one random token per component to `/link/<component>/token`: for `sync-component` and
  `knovas-connector` always, and for every component the Platform's configuration names. A token file already there is
  kept, so a restart does not lock components out. Each component mounts **only its own folder**, at `/link`, and
  reads `/link/token`.
- **Network:** the local API listens on 8460 on the site's internal network `knovas-site` (`internal: true`) with **no
  published port**. Link alone is also on an outbound network.

```yaml
services:
  knovas-link:
    image: knovas-link:1.1.0
    env_file: knovas-site.env
    volumes: [link-data:/data, link-tokens:/link]
    networks: [knovas-site, outbound]
  sync-component:                     # in link mode (MVP-20): reaches Link at http://knovas-link:8460
    volumes:
      - {type: volume, source: link-tokens, target: /link, read_only: true, volume: {subpath: sync-component}}
    networks: [knovas-site]
    depends_on: {knovas-link: {condition: service_healthy}}
networks:
  knovas-site: {internal: true}
  outbound: {}
volumes:
  link-data: {}
  link-tokens: {}
```

At start Link enrols if it has not, or if `LINK_ENROL_TOKEN` is a token it did not enrol with («Token erneuern»), as
kind `knovas_link`. A refused or expired token stops it with `enrol.<code>` in the log; nothing is kept then, and an
earlier key stays with its certificate.

## The certificate (DEC-0056, open)

If the Platform's enrolment answer carries a certificate, Link uses it for secure-api. **Otherwise Link is in the
state `waiting_for_certificate`:** it runs its control plane (heartbeat, configuration, package, commands, reports),
but claims no job, sends no probe, and relays no Brain call (503 `waiting_for_certificate`). Its heartbeat says
`site.certificate: "waiting"` and `GET /local/v1/ping` says `certificate: "waiting"`. No other signing path is built:
how a site gets its certificate is DEC-0056's to decide.

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
  `refused` `fetch_disabled` / `write_disabled` (the source's switch is off). Reconnects with backoff; a 429 waits out
  `Retry-After`.
- **Probes** (MVP-18): every 5 minutes and on the Platform's `check` command: `POST /secured/connector/probe`, answered
  when it comes round, and a local probe job to each component that named `probe` (its `local_ping_ms`).
- **Heartbeat** (MVP-19): protocol v1's, plus `site`: `certificate`, `work_channel`, `probe {last_at, ok,
  round_trip_ms}`, `components [{name, kind, version, state, capabilities, local_ping_ms}]`, `sources [{workspace,
  key, last_read_ok_at, last_run_status}]`, `jobs {kind: {status: n}}` since the last beat. A value nobody measured is
  `null`, never a good one.

**Logs** carry codes, counts and durations only: never a path, a document's name, a token or a certificate. There is
no access log, because a relayed path or query may name a document.

## Tests

```bash
python3 -m pip install starlette uvicorn httpx jsonschema cryptography pytest
python3 -m pytest KnovasLink/tests -q -o addopts="" -p no:cacheprovider </dev/null
```

In-process fakes of the Platform and of secure-api implement the shared contract (`tests/conftest.py`); the companies
and sources in the tests are made up. `tests/test_generic.py` fails on any client's tenant, name, repository, mapping
or type in Link's code, image or this page.

## Not built yet

- Any signing path for the site's certificate (DEC-0056).
- The install package and the Builder's site page (MVP-23, MVP-28); the sync component's link mode (MVP-20).
- Credentials sealed to Link's key (CL-1, I2).
