# Sync component

One deployable connection between a company's source and its Brain, under [connector protocol v1](../../docs/connector-protocol-v1.md). It runs at the company, calls out only, and takes its whole configuration from the Platform.

It runs in one of two modes: **link mode** behind [Knovas Link](../../../KnovasLink/README.md) (MVP-20, below), where it talks to Link only, or **direct mode**, where it enrols itself with the Platform as described next. Direct mode is unchanged for existing installations. How the component fits with Link and the Brain Builder: [the connection layer](../../docs/connection-layer.md).

## A new connection

1. **In the Platform:** Builder → *Verbinden* → *Komponente anmelden* → *Sync-Komponente* or *GitHub-Repository*. The last step shows one `docker run` line with a one-time token (24 hours).
2. **At the company:** run that line. The component makes its own key, enrols, and shows as online.
3. **In the Platform:** create the source on that component and map it onto the Brain, as for any source. The component's page says, per source, whether it can read it and what to fix if not; *Verbindung prüfen* asks again.
4. **Dry run, then apply:** both from the component's page. The component writes to the Brain only on a command a person gave, and an apply writes exactly the plan the dry run made. A schedule only runs dry runs, unless a person switched on «Automatisch anwenden» for the source (below, "Diff polling and automatic apply").

A source's credential stays at the company: add `-e SYNC_CREDENTIAL_<KEY>=…` (the source's key in upper case), or `-e SYNC_CREDENTIAL_GITHUB=…` for every GitHub source. It never travels through the Platform. A file share is mounted read only: `-v <folder>:/sources:ro`.

## Link mode (MVP-20)

**Status: built ahead of its bet's approval, not deployed.** On a site that runs Knovas Link the component neither enrols nor holds a certificate. It is turned on by `SYNC_LINK_URL`:

| Setting | |
|---|---|
| `SYNC_LINK_URL` | Link's local API on the site's network, e.g. `http://knovas-link:8460` |
| `SYNC_LINK_TOKEN_FILE` | the token Link wrote for this component (default `/link/token`: Link's folder for it, mounted read only) |
| `SYNC_NAME` | the name Link knows it by and wrote its token for (default `sync-component`) |
| `SYNC_CREDENTIAL_<KEY>`, `SYNC_CREDENTIAL_GITHUB` | a source's credential, as in direct mode (from `knovas-sources.env`) |

```yaml
  sync-component:                         # as the Builder's install package writes it (builder/sites.py)
    image: sync-component@${SYNC_COMPONENT_DIGEST}
    env_file: [knovas-sources.env]        # the source credentials only
    environment: {SYNC_LINK_URL: "http://knovas-link:8460", SYNC_DATA_DIR: /data}
    volumes:
      - sync-component-data:/data
      - {type: volume, source: link-tokens, target: /link, read_only: true, volume: {subpath: sync-component}}
    networks: [knovas-site, knovas-egress]   # Link on the internal network; the egress network reaches its own sources
    depends_on: [knovas-link]
```

Every call carries `Authorization: Bearer <token>` and `X-Component: <name>` and goes to Link's local API (`KnovasLink/contracts/local-api.md`): it registers at start (and every five minutes, so a restarted Link knows it again) with its capabilities, `fetch:github` and `write:github` among them, and the job kinds `fetch`, `write`, `probe`; it sends its heartbeat to Link and gets the Platform's commands back there, finishing them through `/local/v1/commands/<id>/done`; it reads its configuration and package from Link and reports runs, events and checks through `/local/v1/report/*`. The runner's Brain calls go through Link's relay: the runner gets `BRAIN_API_URL=<Link>/local/v1/brain` and `BRAIN_LOCAL_TOKEN=<token>` and sends no client certificate. It never calls the Platform or secure-api itself. A GitHub source switched to «Änderungen dürfen zurückgeschrieben werden» (`access: read_write`) is still read for dry runs and applies.

**Streams.** The component keeps, per source, the stream the last successful apply acknowledged (the plan's `source.stream`, in `stream-acknowledged.json` beside the plan) and gives it to the next plan as brain-runner's `previous`, with the source's key as the stream's source; a file gone since then becomes a tombstone. A dry run, a failed apply and a held apply acknowledge nothing. (`sync_component.runner_cli` passes both to brain-runner's `make_plan`, which its command line does not take.)

**Jobs.** The component long-polls `GET /local/v1/jobs?wait=25` and answers each job once, with codes, counts, versions and hashes only:

- **probe**: at once, `{"status": "ok", "duration_ms": …}`; Link measures the component's local round trip with it.
- **fetch** (MVP-21): the original's bytes from GitHub, raw: the blob at `args.version`, or the blob the source's ref holds now. The bytes are checked before anything leaves (at most `max_bytes`, else `refused` `too_large`; their git blob hash must be the version, else `failed` `version_mismatch`), then posted in parts of at most 4 MiB to `/local/v1/jobs/<id>/part?n=0,1,…`, and the result is `{"status": "ok", "version", "bytes_sha256", "mime", "bytes", "parts", "duration_ms"}`. Not in the repository at that version: `refused` `not_found`. A source whose fetch switch is off: `refused` `fetch_disabled`.
- **write** (MVP-22), **as a pull request, never on the branch itself**: `brain-runner compile-write` turns the job into the change of one file (its refusal, e.g. `computed_attribute`, is the job's). The file is read at the head of the source's branch; a blob sha other than `expected_version` is `conflict` (with the version found) and nothing is written. Otherwise only the changed values' text in the front matter is replaced (`front_matter_edit.py`: body, key order, comments, line endings and BOM stay byte-identical; a key not there yet is added as the front matter's last line), committed on the new branch `knovas/write-<job id>` with a message naming the declared agent and the first 12 hex digits of the SHA-256 of the declared person's id, and a pull request is opened whose body names the proposal by its job id. The result is `ok` with the pull request's URL as `source_ref`. A mode other than `proposal`: `refused` `mode_unsupported`; GitHub answering 403 to a write step, or 404 after the file was read (its answer to a token that may read but not write): `refused` `token_cannot_write` (the credential needs contents and pull requests write); a step failing after the branch was made deletes the branch again, and if that fails the code is `github.branch_left`; a front matter the editor cannot change safely: `refused` `edit_unsupported` or `front_matter_missing`; the same values as now: `refused` `no_change`. A job claimed again after Link's lease ran out finds its branch and pull request and answers the same URL.

The component reports version 0.3.0 (link mode, jobs, diff polling and automatic apply) in its registration and heartbeat; the image tag stays `sync-component:0.1.0` until the compose file, the Builder's wizard line and the test that pins them move with it.

A new kind of source answers jobs with one function and one entry in `HANDLERS` (`src/sync_component/jobs.py`). Logs carry codes, counts and durations only: never a path, a file name, a value, a repository or a token.

**Release coupling.** Link mode needs a brain-runner that takes `BRAIN_LOCAL_TOKEN` (MVP-20) and `compile-write` (MVP-13), and «bei neuen Commits» one with `affected --source github` (KG-2): version 0.3.2 or later. The image pins 0.3.2 (`BRAIN_RUNNER_VERSION` and `BRAIN_RUNNER_GIT_REF` in the `Dockerfile`, the same commit as the Platform's and as CI's `BRAIN_RUNNER_SHA` in `.github/workflows/ci.yml`; the build stops on another version). With 0.3.1 `affected` fails and every new commit plans every mapping (`runner.affected_exit_2` in the events); an image that still pins 0.1.1 (`43ea868e…`, the pin before 2026-11-07) cannot plan, apply or answer write jobs behind Link at all. A release run prints the version its image carries in the step "Build and check the image".

**kustomize in the image.** The runner renders a Kubernetes kustomization (`kustomize_build`) with `kustomize build`; without it the folder is read as files, patches and components are not applied, and the mapping is stopped. The image carries kustomize v5.4.3, downloaded in a build stage of its own and checked against the SHA-256 its release publishes before it is unpacked (`Dockerfile`, `KUSTOMIZE_VERSION` and one checksum per architecture, amd64 and arm64; `tests/test_sync_component_image.py` pins that).

## Diff polling and automatic apply (KG-2, connector protocol 1.2)

**Status: built, not deployed.** Both modes, link and direct.

- **«Bei neuen Commits»** (the Builder's schedule `{mode: commits, poll_seconds}`, 15 to 3600 s, default 30). Every
  `poll_seconds` the component asks each GitHub source for the commit its ref names now (`fetchers.head_github`: one
  request, with the last answer's `ETag`, so «unchanged» is a 304 GitHub's rate limit does not count). When it is the
  commit last synced or last planned: nothing more, no plan and no Brain call. When it is new, `brain-runner affected
  --source github --since <synced> --json` names the mappings the commits concern, and only those are planned (a
  mapping is still planned whole); the plan is reported as any dry run. A change `affected` cannot narrow (the
  manifest or ontology, a cut list, diverged histories, a runner without the command), a new package or no synced
  commit yet plans every mapping of the source. Commits that concern no mapping plan nothing and report nothing. A
  failed plan of a head is tried again after five minutes, not on every poll. Sources without commits (HTTP/JSON, a
  share) are not polled under this schedule. The configuration the poll reads is cached for five minutes and read
  again on `reload_config`; a plan always reads it fresh.
- **The sync state** (`sync-state.json` beside the plan, per source): the commit the last apply without errors synced
  and when (written only then; for a plan of some mappings it is the commit `affected` compared to, for a full plan the
  commit the plan read), the package it ran, the last check, the commit the last poll planned, why the last plan was
  not applied on its own, and what the last apply wrote (counts, `auto`). `sync-pending.json` holds what the plan on
  disk would sync.
- **Automatic apply** (`auto_apply` on a source in the configuration; a person who manages the sites switches it in
  the Builder, off by default). A plan the schedule made (any scheduled mode) is applied right after its dry run is
  reported, as a second run (`kind: apply`, no command) with the event `auto_apply.applied`, only when its status is
  `ok`, it has no errors and nothing held, and `engine.attention` finds nothing a person must decide: a deletion-guard
  hold (nodes or documents), drift (facts, computed attributes, derived edges), a held retire entry, a release,
  documents stopped, reported or held, an unreadable source document, anything unresolved, blocked derived edges, or a
  plan version it does not know. Otherwise it sends `auto_apply.withheld` and the reasons as codes, and the plan waits
  for a person as before. The apply never approves anything (no `--approve-guard`, `--approve-retire` or
  `--overwrite-drift`). A dry run a person asked for (`run`) is never applied on its own.
- **The heartbeat** carries `sources: [{workspace, key, synced_commit, synced_at, checked_at, next_check_at,
  planned_commit, held_back, last_apply}]`: commits, times, codes and counts only. In link mode Knovas Link passes them
  on; the Builder shows them on the site page and the component's page.
- **Behind Knovas Link** the component follows its own schedule and pause, as its page in the Builder sets them (Link
  1.2 passes them; an older Link gives Link's own).

## Connector protocol 2.0: GitHub on the connector kit (draft, `SYNC_PROTOCOL_V2`, off by default)

FEAT-2026-11-10-write-back C-3 ([the protocol](../../docs/connector-protocol-v2.md)). With `SYNC_PROTOCOL_V2=true`
(and Knovas Link's `LINK_PROTOCOL_V2=true`) the component also registers the GitHub connector's declaration and
answers `preview`, `act`, `reread` and `review_outcome` (`src/sync_component/github_v2.py`), built on the connector kit
(a byte-for-byte copy under `src/knovas_connector_kit/`, because the image is built from this folder):

- one action, `set_front_matter`: parameter `mapping` plus one optional parameter per fact a published mapping
  `writes`; brain-runner's compile-write decides what may change, as for the 1.x write job;
- `direct` (the default for the Knovas tenant): one commit on the source's branch through the contents API with the
  expected blob sha, so a file changed since the preview is a conflict and nothing is written; the commit carries
  `Knovas-Permit: <id>`, so a repeat finds it and answers its receipt without writing again; no pull request. The token
  needs `Contents: write`, and the branch's protection must let it push;
- `native_review`: today's pull request on `knovas/write-<permit id>` (then also `Pull requests: write`);
  `review_outcome` follows its merge or close;
- no `unstage` and no inverse in V1 (the founder, 2026-11-10); reading stays protocol 1.x's;
- every change, difference and conflict names its `step` (protocol v2.0 as amended 2026-11-10, D-14): a preview's the
  index of its step, also with two steps on one file; an `act` holds one file, so its receipt and conflict name step 0.

The kit's conformance suite runs against it in `docbridge_integration/tests/test_sync_component_github_v2.py`.

## Getting the image to a company

**Behind Knovas Link:** the release workflow builds, signs and pushes the image to GHCR, and the site's install package names it by digest (`SYNC_COMPONENT_DIGEST`): [release-images.md](../../docs/deployment/release-images.md). A copied image (`docker save` / `docker load`) has no digest and cannot start the package.

**Direct mode** (the Builder's `docker run` line names the tag `sync-component:0.1.0`): build it once, then copy it:

```bash
docker compose --env-file knovas.env --profile sync-component build sync-component   # KB_READ_TOKEN from knovas.env
docker save sync-component:0.1.0 | gzip > sync-component.tar.gz   # on the company's machine: docker load < sync-component.tar.gz
```

## A new kind of source

One function and one entry in `src/sync_component/fetchers.py`: it reads the source and writes the document the source's mappings read, or tells the runner where to read. Today: `http_json`, `github`, `knovas_connector` (a mounted share). A new kind also needs its entry in the Platform's `builder/sources.py`, and a compiled mapping that reads what the function writes.

## Settings

`python -m sync_component --help` and the top of `src/sync_component/__main__.py`.

## What it does not do yet

- Documents: only records, through the mapping runner. The Knovas Connector transfers documents, under the same protocol (`KnovasConnector/docs/platform-link.md`).
- Write back to YAML or JSON files (`data.*` paths) or to anything below the front matter's top level: refused `edit_unsupported`. Fetch and write for sources other than GitHub.
- Permissions: in the contract as *not built*.
- Renew its certificate by itself: it holds for 365 days; *Token erneuern* in the Platform enrols it again.

## Tests

`docbridge_integration/tests/test_sync_component_autosync.py`: diff polling, the sync state, automatic apply and what it never takes, the heartbeat's sync status and the GitHub head request, with the runner and the Platform standing in. `docbridge_integration/tests/test_sync_component.py`: the real component over real HTTP against the Platform's protocol (direct mode), and against an in-process Knovas Link (`tests/fake_knovas_link.py`) with the real brain-runner (link mode). `test_github_jobs.py` (fetch, probe) and `test_github_write.py` (write) run the jobs against an in-process GitHub (`tests/fake_github_rest.py`), `test_front_matter_edit.py` the editor's round trip, `test_sync_component_image.py` that the image pins kustomize and checks its checksum, and `test_sync_component_generic.py` fails on any client's tenant, name, repository, mapping or type in the component, its image, this page or the link-mode tests; the companies there are made up (`tests/sync_link_world.py`).

```bash
cd KnovasPlatform/components/docbridge_integration
PYTHONPATH=<folder that holds brain_runner> python3 -m pytest tests/test_sync_component.py tests/test_sync_component_autosync.py tests/test_github_jobs.py \
  tests/test_github_write.py tests/test_front_matter_edit.py tests/test_sync_component_generic.py -q -o addopts="" </dev/null
```
