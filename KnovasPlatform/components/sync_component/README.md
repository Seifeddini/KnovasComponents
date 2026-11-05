# Sync component

One deployable connection between a company's source and its Brain, under [connector protocol v1](../../docs/connector-protocol-v1.md). It runs at the company, calls out only, and takes its whole configuration from the Platform.

It runs in one of two modes: **link mode** behind [Knovas Link](../../../KnovasLink/README.md) (MVP-20, below), where it talks to Link only, or **direct mode**, where it enrols itself with the Platform as described next. Direct mode is unchanged for existing installations.

## A new connection

1. **In the Platform:** Builder → *Verbinden* → *Komponente anmelden* → *Sync-Komponente* or *GitHub-Repository*. The last step shows one `docker run` line with a one-time token (24 hours).
2. **At the company:** run that line. The component makes its own key, enrols, and shows as online.
3. **In the Platform:** create the source on that component and map it onto the Brain, as for any source. The component's page says, per source, whether it can read it and what to fix if not; *Verbindung prüfen* asks again.
4. **Dry run, then apply:** both from the component's page. The component writes to the Brain only on a command a person gave, and an apply writes exactly the plan the dry run made. A schedule only runs dry runs.

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
  sync-component:
    image: sync-component:0.1.0
    env_file: knovas-sources.env          # the source credentials only
    environment: {SYNC_LINK_URL: "http://knovas-link:8460"}
    volumes:
      - {type: volume, source: link-tokens, target: /link, read_only: true, volume: {subpath: sync-component}}
      - sync-data:/data
    networks: [knovas-site, outbound]     # Link on the site's network; outbound only to reach its own sources
    depends_on: {knovas-link: {condition: service_healthy}}
```

Every call carries `Authorization: Bearer <token>` and `X-Component: <name>` and goes to Link's local API (`KnovasLink/contracts/local-api.md`): it registers at start (and every five minutes, so a restarted Link knows it again) with its capabilities, `fetch:github` and `write:github` among them, and the job kinds `fetch`, `write`, `probe`; it sends its heartbeat to Link and gets the Platform's commands back there, finishing them through `/local/v1/commands/<id>/done`; it reads its configuration and package from Link and reports runs, events and checks through `/local/v1/report/*`. The runner's Brain calls go through Link's relay: the runner gets `BRAIN_API_URL=<Link>/local/v1/brain` and `BRAIN_LOCAL_TOKEN=<token>` and sends no client certificate. It never calls the Platform or secure-api itself. A GitHub source switched to «Änderungen dürfen zurückgeschrieben werden» (`access: read_write`) is still read for dry runs and applies.

**Streams.** The component keeps, per source, the stream the last successful apply acknowledged (the plan's `source.stream`, in `stream-acknowledged.json` beside the plan) and gives it to the next plan as brain-runner's `previous`, with the source's key as the stream's source; a file gone since then becomes a tombstone. A dry run, a failed apply and a held apply acknowledge nothing. (`sync_component.runner_cli` passes both to brain-runner's `make_plan`, which its command line does not take.)

**Jobs.** The component long-polls `GET /local/v1/jobs?wait=25` and answers each job once, with codes, counts, versions and hashes only:

- **probe**: at once, `{"status": "ok", "duration_ms": …}`; Link measures the component's local round trip with it.
- **fetch** (MVP-21): the original's bytes from GitHub, raw: the blob at `args.version`, or the blob the source's ref holds now. The bytes are checked before anything leaves (at most `max_bytes`, else `refused` `too_large`; their git blob hash must be the version, else `failed` `version_mismatch`), then posted in parts of at most 4 MiB to `/local/v1/jobs/<id>/part?n=0,1,…`, and the result is `{"status": "ok", "version", "bytes_sha256", "mime", "bytes", "parts", "duration_ms"}`. Not in the repository at that version: `refused` `not_found`. A source whose fetch switch is off: `refused` `fetch_disabled`.
- **write** (MVP-22), **as a pull request, never on the branch itself**: `brain-runner compile-write` turns the job into the change of one file (its refusal, e.g. `computed_attribute`, is the job's). The file is read at the head of the source's branch; a blob sha other than `expected_version` is `conflict` (with the version found) and nothing is written. Otherwise only the changed values' text in the front matter is replaced (`front_matter_edit.py`: body, key order, comments, line endings and BOM stay byte-identical; a key not there yet is added as the front matter's last line), committed on the new branch `knovas/write-<job id>` with a message naming the declared agent and the first 12 hex digits of the SHA-256 of the declared person's id, and a pull request is opened whose body names the proposal by its job id. The result is `ok` with the pull request's URL as `source_ref`. A mode other than `proposal`: `refused` `mode_unsupported`; GitHub answering 403 to a write: `refused` `token_cannot_write` (the credential needs contents and pull requests write); a front matter the editor cannot change safely: `refused` `edit_unsupported` or `front_matter_missing`; the same values as now: `refused` `no_change`. A job claimed again after Link's lease ran out finds its branch and pull request and answers the same URL.

A new kind of source answers jobs with one function and one entry in `HANDLERS` (`src/sync_component/jobs.py`). Logs carry codes, counts and durations only: never a path, a file name, a value, a repository or a token.

**Release coupling.** Link mode needs a brain-runner that takes `BRAIN_LOCAL_TOKEN` (MVP-20) and `compile-write` (MVP-13); the image's pinned runner (`BRAIN_RUNNER_GIT_REF`) moves to that release together with the Platform's.

## Getting the image to a company

There is no image registry yet. Build it once, then copy it:

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

`docbridge_integration/tests/test_sync_component.py`: the real component over real HTTP against the Platform's protocol (direct mode), and against an in-process Knovas Link (`tests/fake_knovas_link.py`) with the real brain-runner (link mode). `test_github_jobs.py` (fetch, probe) and `test_github_write.py` (write) run the jobs against an in-process GitHub (`tests/fake_github_rest.py`), `test_front_matter_edit.py` the editor's round trip, and `test_sync_component_generic.py` fails on any client's tenant, name, repository, mapping or type in the component, its image, this page or the link-mode tests; the companies there are made up (`tests/sync_link_world.py`).

```bash
cd KnovasPlatform/components/docbridge_integration
PYTHONPATH=<folder that holds brain_runner> python3 -m pytest tests/test_sync_component.py tests/test_github_jobs.py \
  tests/test_github_write.py tests/test_front_matter_edit.py tests/test_sync_component_generic.py -q -o addopts="" </dev/null
```
