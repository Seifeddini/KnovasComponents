# The connection layer: the parts in this repository

How a company's sources are connected to its Brain, and which part of this repository does what. This page is the
entry point; it links to the pages that own each detail and repeats none of them. For setting up a site step by step
(in German, for consultants) read [connect-a-site.md](connect-a-site.md).

## In plain words

- A **site** is one machine at the company. On it runs **Knovas Link**, the only program there that talks to Knovas,
  and only outwards. Next to it run **components** that read the company's sources, today the **sync component**.
  The components talk only to Link.
- The **Brain Builder** (part of the Platform) is where a person sets everything up and watches it: the site and its
  install package, the sources and their two switches, the mappings, the approval of proposed changes, and the
  health of each site.
- Three things travel through a site:
  1. **Sync:** the sync component reads a source, plans what changes in the Brain, shows a dry run, and applies it
     after a person's approval. On the schedule «bei neuen Commits» it plans only when a GitHub source has a new
     commit, and only the mappings the commit concerns; where a person switched on «automatisch anwenden» for the
     source, it applies a plan right after it is made, only when nothing in it needs a person (KG-2).
  2. **Originals:** when an agent asks for an original document and may read it, Knovas asks the site; the sync
     component reads the file from the source, and its bytes pass through Link to the agent. Knovas does not store
     them.
  3. **Write back:** an agent may propose a change to a fact. A person accepts it in the Builder. The sync component
     then opens a pull request in the source. The Brain shows the new value only after the next sync reads it.
- The source's credentials stay at the site. They never pass through the Platform.

## The parts and where their code is

| Part | Runs | What it does | Code | Owner page |
|---|---|---|---|---|
| Knovas Link | the site | enrolment, the site's key and certificate, heartbeat, the components' Brain calls on an allow-list, the work channel (jobs) and probes, its local API | [`KnovasLink/`](../../KnovasLink/) | [Link README](../../KnovasLink/README.md); local API: [`KnovasLink/contracts/local-api.md`](../../KnovasLink/contracts/local-api.md) |
| Sync component, link mode | the site, beside Link | reads GitHub, HTTP/JSON and file shares; runs brain-runner; answers `fetch` (a GitHub blob) and `write` (a pull request) jobs | [`components/sync_component/`](../components/sync_component/) | [sync component README](../components/sync_component/README.md) |
| Jira connector (protocol 2, draft) | the site, beside Link, as its own component (kind `jira`) | registers its declaration; answers `preview`, `act`, `reread` for a Jira project's issues; `dump-stream` writes a source's record stream for a mapping run; it runs no mappings itself | [`KnovasConnectorJira/`](../../KnovasConnectorJira/) | [Jira connector README](../../KnovasConnectorJira/README.md); site file `deploy/docker-compose.jira.yml` |
| Brain Builder: sites | the Platform | the site page, its install package, «Token erneuern», the certificate step, «Jetzt prüfen»; per source «Quellen und Abgleich»: the last synced commit, the last run's counts, the next check, the switch «automatisch anwenden» (KG-2) | `components/docbridge_integration/src/builder/sites.py`, `builder/components.py` (`sync_rows`, `auto_apply_of`) | [connect-a-site.md](connect-a-site.md) §§ 1–2, 5 |
| Brain Builder: a component's schedule | the Platform | manual, regularly (from 1 minute), in a window, or «bei neuen Commits» (poll every 15–3600 s); each component behind Link has its own | `builder/components.py` (`check_schedule`), `builder/protocol.py` (`components_of`) | [connect-a-site.md](connect-a-site.md) § 5 |
| Brain Builder: sources and switches | the Platform | per source «Agenten dürfen Originale abrufen» and «Änderungen dürfen zurückgeschrieben werden», both off until switched on and published; with `BUILDER_PROTOCOL_V2` (off) also a source of a kind a connector at a site declared and the Builder has no form for, created with the generic settings form (names and values, never a credential) and served to the site as entered (SPEC-write-back-13) | `builder/sources.py`, `builder/source_edits.py`, `builder/protocol_v2.py` | [connect-a-site.md](connect-a-site.md) § 3 |
| Brain Builder: mapping panels | the Platform | «Zugriff» (who reads the documents) and «Zurückschreiben» (which facts may travel back) | `builder/mapping_edits.py`, `builder/mappings.py` | [connect-a-site.md](connect-a-site.md) § 4 |
| Brain Builder: «Betrieb › Freigaben» | the Platform | accept or reject write proposals (permission «Veröffentlichen»); with `BUILDER_PROTOCOL_V2` (off) also change sets of protocol 2 with their previews, approved only where every preview is exact, with the source's staging (`direct` unless set) | `builder/approvals.py`, `builder/connection_api.py`, `builder/protocol_v2.py` | [connect-a-site.md](connect-a-site.md) § 5 |
| Brain Builder: «Betrieb › Verbindung» | the Platform | one health card per site: green only after a probe went round in the last 10 minutes | `builder/sites.py`, `builder/connection_api.py` | [connect-a-site.md](connect-a-site.md) § 5 |
| Connector protocol, the Platform's side | the Platform | enrolment, heartbeat, configuration, package, commands, reports | `builder/protocol.py`, `web_interface/connector_protocol.py` | [connector-protocol-v1.md](connector-protocol-v1.md) |
| Client-certificate terminator | host nginx in front of the Platform | checks Link's certificate against the client CA and passes its SHA-256 as `X-Client-Cert-Fingerprint` | [`deploy/host-nginx/`](../deploy/host-nginx/) (`knovas-client-cert.js`, `knovas-connector-mtls.conf`); installed by `scripts/host-https.sh <fqdn> <email> <client CA>` (its header says how) | [connect-a-site.md](connect-a-site.md) § 2, «Der TLS-Terminator vor der Plattform» |
| Image release | GitHub Actions | builds, signs and pushes the Platform, `knovas-link` and `sync-component`; a site names them by digest | `.github/workflows/release-images.yml` | [deployment/release-images.md](deployment/release-images.md) |

The Brain's side (the `/secured/` routes Link calls: the work channel, the fetch of originals, source references, write
proposals) runs at Knovas. What a site must send and may expect is in [connector-protocol-v1.md](connector-protocol-v1.md),
from "Sites and Knovas Link" on.

```mermaid
flowchart LR
  subgraph site["Site: one machine, one Docker network"]
    SC["sync component"] -- "local API, token per component" --> L["Knovas Link"]
  end
  SC -- "the company's own token" --> SRC["the company's sources"]
  L -- "control plane, client certificate" --> T["host nginx terminator"] --> P["Platform with the Brain Builder"]
  L -- "Brain calls and work channel, site certificate" --> B["Knovas: the Brain"]
  P -- "the Platform's own certificate" --> B
```

## Who proves what

| From → to | Proof | Refused when |
|---|---|---|
| component → Link | `Authorization: Bearer <token>` (Link writes one per component to `/link/<component>/token`) and `X-Component` | the token does not belong to the name (401) |
| Link → Platform | the enrolment token once (24 hours, single use), then a control token and the certificate fingerprint the terminator sets | no certificate recorded yet: only the heartbeat is taken (`certificate_not_recorded`); no fingerprint: refused unless `BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=false` |
| Link → the Brain | the site's own client certificate (one per site), signed by Knovas's client CA | until it is installed Link claims no job and relays nothing (`waiting_for_certificate`); a path not on the allow-list is 403 `not_relayed` |
| the Platform → the Brain | the Platform's own client certificate, and the signed-in person's assertion | accepting a proposal needs «Veröffentlichen» in the Builder |
| sync component → source | the company's credential, `SYNC_CREDENTIAL_<KEY>` in `knovas-sources.env` | the source refuses it; a token that cannot write gives `token_cannot_write` |
| a connector of its own kind → source (Jira) | the company's credential and its host, `SYNC_CREDENTIAL_<KEY>` and `SYNC_CREDENTIAL_<KEY>_HOST` in the connector's own `knovas-<kind>.env` (`knovas-jira.env`), which no other service reads | a site setting naming another host (`jira.bad_settings`), no host line (`jira.no_credential`); the sync component never uses a credential with a `_HOST` line, a host line, or another kind's line, whatever key the Platform names (verifier KC-3 N5) |

The install package puts every service on the internal network `knovas-site` and on `knovas-egress` (to reach its own
sources). "Only Link talks to Knovas" holds because only Link holds a Knovas certificate and token, not because the
network is closed (`builder/sites.py`).

## Settings that turn parts on or off

| Setting | Effect | Default |
|---|---|---|
| Source switch «Agenten dürfen Originale abrufen» | Link passes `fetch` jobs for that source; otherwise `refused fetch_disabled` | off |
| Source switch «Änderungen dürfen zurückgeschrieben werden» (`access: read_write`) | Link passes `write` jobs; otherwise `refused write_disabled`. Offered for GitHub only, and only when the component declares `write:github` | off |
| `SYNC_LINK_URL` | the sync component runs in link mode (the install package sets it) | unset: direct mode |
| A component's schedule «Bei neuen Commits» | it asks each GitHub source for its head every `poll_seconds` and plans only the mappings new commits concern (KG-2) | manual |
| Source switch «Automatisch anwenden, wenn nichts zur Freigabe ansteht» (whoever manages sites) | the component applies a scheduled plan of that source right after it is made, only when nothing in it needs a person; a guard hold, drift or retire entry still waits | off |
| `BUILDER_PROTOCOL_V2` | the Builder decides on the connectors' protocol 2 declarations: writing back and the staging per source where a connector declared actions and passed the conformance checks, change sets in «Freigaben», and a source of a kind the Builder has no form for, with the generic settings form, where a connector declared that kind and passed the read checks | off |
| `LINK_PROTOCOL_V2` (Link) | Link takes connectors' declarations, declares its site to the Brain, checks permits and routes the jobs of protocol 2 | off |
| `LINK_COMPONENTS` (Link) | further component names Link writes a token for from its start (a connector of its own kind, e.g. `jira-connector`) | none |
| `SYNC_PROTOCOL_V2` (sync component) | the GitHub connector's write path of protocol 2 besides 1.x; reading, `fetch` and 1.x write jobs stay ([connect-a-site.md](connect-a-site.md) § 6.2) | off |
| `BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT` | the Platform requires the terminator's fingerprint on connector calls | `true` |
| `BUILDER_CONNECTOR_ISSUE_CERTIFICATES` | the Platform asks the Brain to sign a component's certificate at enrolment. Where the Brain cannot sign, set `false`: Knovas Link then waits for a certificate an admin signed | `true` |
| `BUILDER_CONNECTOR_CLIENT_CA` | the client CA Link learns at enrolment and a recorded site certificate must chain to | empty |
| `LINK_HEARTBEAT_SECONDS`, `LINK_PROBE_SECONDS` | heartbeat and probe intervals | 30, 300 |
| `LINK_TEST_ONLY_SHARED_CERTIFICATE` | test only, never for a company: Link uses the tenant's own certificate | off |

## What is built and what is not (2026-11-10)

- **Built on this branch, not deployed at any company:** Knovas Link, the sync component's link mode and its GitHub
  `fetch` and `write`, the Builder's sites, switches, panels, «Freigaben» and «Verbindung», the terminator, the release
  workflow. Since 2026-11-10 (KG-2, protocol 1.2, Link 1.2.0, sync component 0.3.0): diff polling «bei neuen
  Commits», the switch «automatisch anwenden» per source, each component's own schedule and pause behind Link, and the
  last synced commit and run per source on «Verbinden» and the site page. None of it runs anywhere until an image
  release carries it.
- **Protocol 2 at a site (2026-11-10, J-4, C-5):** the Jira connector has its image (`jira-connector`, release
  workflow) and a compose file beside the site package; Link writes its token from its start (`LINK_COMPONENTS`); the
  sync component serves a GitHub source on protocol 2 with `SYNC_PROTOCOL_V2` while its reads stay 1.x
  ([connect-a-site.md](connect-a-site.md) § 6).
- **No image is released yet.** GitHub offers the release workflow only once its file is on the default branch
  ([release-images.md](deployment/release-images.md), "Once per repository").
- **Images behind Link need brain-runner 0.3.2 or later.** The Platform's and the sync component's Dockerfiles pin
  0.3.3 (`BRAIN_RUNNER_VERSION`, `BRAIN_RUNNER_GIT_REF`, KnowledgeBase commit `7961dd1e5`) since 2026-11-12 (0.3.2,
  `961b2f72b`, before); both builds stop on another version. 0.3.2 refuses the `records` extractor (`jira-issues`). 0.3.1 has no `affected --source github`, so «bei neuen Commits» would plan every mapping;
  0.1.1 cannot call the Brain through Link (`BRAIN_LOCAL_TOKEN`) and has no `compile-write` (sync component README,
  "Release coupling"). The release run prints the version in its step "Build and check the image".
- **Not built:** sources other than GitHub for `fetch` and `write`; the Knovas Connector behind Link; permissions from
  the source; credentials typed into the Builder; monitoring history and notifications; autonomous writes to a source
(automatic apply writes to the Brain only, and only what needs no person); polling sources other than GitHub. The Builder
  records who accepted a proposal, but the Brain does not yet bind the accept to a verified person.
- **A new source kind needs no Platform change** (SPEC-write-back-13, behind `BUILDER_PROTOCOL_V2`): what a source can
  write comes from its connector's declaration, and a kind the Builder has no form for is created with one generic
  settings form. The declaration describes no settings, so the form takes names and values (text, number, yes/no, list
  of texts) and a connector's documentation says which; the form refuses what looks like a credential by its form,
  not every secret (the credential scan, WB-23, is the real guard). The connector behind Link may register under its
  own component kind: the Platform keeps it when its declaration is one the protocol's schema allows (migration 0025),
  and drops it as before when it is not. Typed fields from the connector would need an addition to the declaration:
  deferred to WB-20. **Open:** a source of such a kind cannot be mapped in the Builder yet; brain-runner reads
  records (J-3), the Builder's record-mapping path is part of J-4.
- **Known limits:** jobs of one tenant are not yet bound to one site, so add a second site only after that item; Link
  relays every method below `secured/graph/` ([connector-protocol-v1.md](connector-protocol-v1.md), "Sites and Knovas
  Link").

## Where to read next

- The contract between Link, the components and Knovas: [connector-protocol-v1.md](connector-protocol-v1.md) and
  [`KnovasLink/contracts/`](../../KnovasLink/contracts/).
- Setting up a site, in German: [connect-a-site.md](connect-a-site.md).
- Knovas Link: [`KnovasLink/README.md`](../../KnovasLink/README.md). The sync component:
  [`components/sync_component/README.md`](../components/sync_component/README.md).
- Releasing images and deploying them by digest: [deployment/release-images.md](deployment/release-images.md).
- HTTPS in front of the Platform: [deployment/host-nginx-internal.md](deployment/host-nginx-internal.md); the connectors'
  terminator: the header of `scripts/host-https.sh` and [connect-a-site.md](connect-a-site.md) § 2.
- Knovas's own deployment of the Platform and its site: [deployment/knovas-tenant.md](deployment/knovas-tenant.md)
  §§ 4–5.
- What each part offers, promises and relies on, one card per component with the tests behind each line:
  [`docs/components/`](../../docs/components/) (where each component's code begins and ends, and why:
  `boundaries.yaml`).
