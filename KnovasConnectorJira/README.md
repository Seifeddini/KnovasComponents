# Knovas Jira connector

**Status: draft, off unless a site runs it.** Connector protocol 2.0 (approved 2026-11-10). BET-2026-11-write-back,
item WB-07; feature plan FEAT-2026-11-10-write-back, Phase D (tasks J-1, J-2, J-4). Built on the
[connector kit](../KnovasConnectorKit/README.md) and tested against a fake Jira only (no test calls a real Jira). Since
J-4 it has an image (`jira-connector`, from the release workflow) and runs at a site as its own component behind
Knovas Link ("At a site" below), and `dump-stream` writes a source's record stream for a Brain mapping run. Card:
[`L3-JIRA-CONNECTOR`](../docs/components/L3-jira-connector.md); what it relies on in Jira:
[`EXT-JIRA-API`](../docs/components/EXT-jira-api.md).

It reads one Jira Cloud project's issues as `record` streams and carries out three actions behind the site's Knovas
Link, each under one permit the Brain signed after a person approved an exact preview.

## Configuration (at the site)

A source of kind `jira` names its site and project in its settings; nothing in the package names either.

```json
{"key": "jira-main", "kind": "jira", "access": "read_write",
 "settings": {"site": "https://<your-site>.atlassian.net", "project": "<PROJECT KEY>",
              "fields": ["summary", "status", "assignee", "duedate"], "computed_fields": []}}
```

- `site`: `https://` and the host only (no user, password or path); `project`: the project key.
- `fields` (optional): the field ids a record carries; default `summary, status, assignee, reporter, duedate,
  priority, labels, issuetype, resolution, created, resolutiondate`. Links, the parent and sub-tasks are read as the
  record's `links`.
- `computed_fields` (optional): fields an automation sets. They are never written, and a preview that would set one is
  inexact (it cannot be approved). Fields the edit metadata does not let the credential set count as computed too.
- The credential is read from the site's environment: `SYNC_CREDENTIAL_<SOURCE KEY>` (upper case, other characters as
  `_`), else `SYNC_CREDENTIAL_JIRA`. `<account email>:<API token>` is sent as basic authentication, a bare token as a
  bearer token. It never leaves the site: not in a result, a stream message, a log line or an error.
- The one host the credential may be sent to is named beside it, in `SYNC_CREDENTIAL_<SOURCE KEY>_HOST` (for
  `SYNC_CREDENTIAL_JIRA`: `SYNC_CREDENTIAL_JIRA_HOST`), the host name only, never taken from the other line:

  ```bash
  # knovas-sources.env at the site
  SYNC_CREDENTIAL_JIRA_MAIN=<account email>:<API token>
  SYNC_CREDENTIAL_JIRA_MAIN_HOST=<your-site>.atlassian.net
  ```

  The settings come from the Platform, so `site` must be `https://` and exactly that host: a `site` naming any other
  host is refused `jira.bad_settings` before any request is made, and a credential without its host line is
  `jira.no_credential`. Every request goes to that host, and a redirect is never followed (it answers
  `jira.http_3xx`), so the credential reaches no other host (GI-CONN-07).

The account needs *Browse projects* (reading), *Edit issues*, *Assign issues* and *Transition issues* (the actions).

## What it reads

One `record` per issue: `external_id` the issue key, `version` the issue's **change-log position** (the id of its newest
change-log entry, `0` for none), `payload.fields` by field id (a person as their account id; whole numbers as
integers, other numbers as strings), `payload.computed` (from the edit metadata and the settings), `payload.links`.
A listing Jira marks as its last page is complete: then `tombstone`s for issues gone since the last state, a
`generation` and a `state`. A cut listing reports no absence.

## What it writes

| Action | Parameter | Jira call | Refused when |
|---|---|---|---|
| `set_assignee` | `assignee`: an account id, or null | edit (`PUT /rest/api/3/issue/{key}`) | the account cannot be assigned (`assignee_not_assignable`) |
| `transition_issue` | `status`: the status's name | the one available transition to it | none (`transition_unavailable`), several (`transition_ambiguous`), required screen fields (`transition_needs_fields`) |
| `set_due_date` | `due_date`: `YYYY-MM-DD`, or null | edit | — |

All three: `preview: exact`, `staging: [direct]`, `inverse: null` (no undo in V1). The two field edits declare
`grouping: item` (protocol v2.0 as amended 2026-11-10, D-24): several of them on one issue go in one edit call (one
change-log entry), all or none, and come back as one receipt per step; the Brain sends one act per issue.
`transition_issue` declares `grouping: none` and goes alone. An act whose steps name several issues is the Brain's
error and is refused `grouping_unsupported` before any issue is read (Jira cannot write two issues all or none), two
steps setting one field of one issue `same_field_twice`. A value equal to the current one is refused (`no_change`). Each write carries one issue property
`knovas.permit.<permit id>` per step in the same request: a repeat, or a delivery after a lost answer, finds them and
answers the receipts without writing again. A receipt comes from reading the issue again (values before and after,
differences from the approved value, side effects by field name, the new change-log position). A change log that moved
past a permit's expected version is a `conflict` (`version_changed`) naming the first such step, and nothing is
written. Every change, difference and conflict names its `step` (the index into the job's steps).

## At a site (behind Knovas Link)

The image `jira-connector` (`Dockerfile`; built from this folder alone, so the kit is the byte-for-byte copy under
`vendor/`) runs `knovas-connector-jira serve`: it registers with Link as its own component, kind `jira`, with its
declaration and the job kinds `probe`, `preview`, `act`, `reread`; sends its heartbeat every 30 seconds; reports per
source whether it can read it (`source.ok`, or the Jira code such as `jira.unauthorized`) on the command `check` and
whenever the sources or their settings changed; refuses `run` (it runs no mappings); and answers Link's jobs through
the connector. It holds no Knovas certificate or token and opens no port.

| Setting | |
|---|---|
| `SYNC_LINK_URL` | Link's local API, `http://knovas-link:8460` |
| `SYNC_NAME` | the name Link wrote its token for (default `jira-connector`) |
| `SYNC_LINK_TOKEN_FILE` | default `/link/token` (Link's folder for it, mounted read only) |
| `SYNC_INTERVAL_SECONDS` | between heartbeats (default 30) |
| `SYNC_CREDENTIAL_<KEY>`, `SYNC_CREDENTIAL_<KEY>_HOST` | the source's credential and its host, from `knovas-sources.env` |

[`deploy/docker-compose.jira.yml`](deploy/docker-compose.jira.yml) adds it to the Builder's install package without
changing the package: copy it beside the package's `docker-compose.yml` and put in the site's `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml:docker-compose.jira.yml
JIRA_CONNECTOR_IMAGE=ghcr.io/<owner, lower case>/jira-connector
JIRA_CONNECTOR_DIGEST=sha256:<from the release run's summary>
```

It also sets, on `knovas-link`, `LINK_PROTOCOL_V2=true` (Link takes declarations and routes v2 jobs only then) and
`LINK_COMPONENTS=jira-connector` (Link writes this component's token from its start, before any source names it).
Then `docker compose up -d`. The Platform needs `BUILDER_PROTOCOL_V2=true` to keep a component of the kind `jira`
and offer its sources (the generic settings form; [connect-a-site.md](../KnovasPlatform/docs/connect-a-site.md)
§ 3). The site and the project come from the source's settings in the Builder, after Prüfen & Veröffentlichen; the
credential and its host from `knovas-sources.env` (above), never from the Platform.

## `dump-stream`: a source's record stream for the Brain mapping

```
knovas-connector-jira dump-stream --source <source key> --out <file.jsonl | -> [--settings <file.json>]
```

This interface is stable; a mapping run (brain-runner's record source, `RecordSource.from_file`) builds on it.

- **Output:** one complete read of the source from no earlier state, as JSON Lines, one stream message
  (`stream-message.schema.json`, protocol 2.0) per line, keys sorted, in the stream's order: a `record` per issue of
  the project (by key), then one `generation` with `complete: true` and its counts, then the `state`. Every message
  names the source key as `source`. The same project gives the same bytes. `-` writes to standard output; a file is
  written whole or not at all, readable by its owner only.
- **Settings:** without `--settings`, the source of that key as Knovas Link holds it from the Builder's published
  configuration for this component (`SYNC_LINK_URL`, `SYNC_NAME`, `SYNC_LINK_TOKEN_FILE`, as for `serve`). With
  `--settings`, a JSON object of a Jira source's settings, no other keys: `{"site": "https://<your-site>.atlassian.net",
  "project": "<PROJECT KEY>", "fields": [...]}`, optional `computed_fields`, `page_size`.
- **Credential:** `SYNC_CREDENTIAL_<SOURCE KEY>` and `SYNC_CREDENTIAL_<SOURCE KEY>_HOST` (else the `_JIRA` pair) from
  the environment, as for every Jira call; never an argument, never in the output.
- **Exit codes:** `0` written; `2` usage, settings or configuration (`dump.bad_settings_file`, `dump.no_link`,
  `dump.unknown_source`, `dump.not_a_jira_source`, `jira.bad_settings`, `jira.no_credential`); `3` a Jira call failed
  (`jira.unauthorized`, `jira.http_<status>`, `jira.rate_limited`, `jira.unreachable`); `4` Jira cut the listing
  (`dump.listing_cut`): not complete, nothing written. Standard error carries one line, `dump-stream: <code>` or
  `dump-stream: <n> records, generation <id>, <m> messages`; never a key, a value, the site or the credential.

Through the container, with the service's environment:

```bash
# at the site (settings from the Builder through Link, credential from knovas-sources.env)
docker compose run --rm -T jira-connector dump-stream --source <source key> --out - > <source key>.jsonl
# from a checkout of this repository, without Link (knovas.env: JIRA_CONNECTOR_SOURCES_ENV names the credential file)
docker compose --env-file knovas.env --profile jira-connector run --rm -T \
  -v "$PWD/jira-settings.json:/settings.json:ro" jira-connector \
  dump-stream --source <source key> --settings /settings.json --out - > <source key>.jsonl
```

Or without Docker, from this folder: `PYTHONPATH=src:vendor python -m knovas_connector_jira dump-stream …` (after
`pip install -r requirements.txt`), with the two credential lines in the environment.

## Tests

```bash
cd KnovasConnectorJira && pip install -r requirements.txt "pytest>=7.4,<9" PyYAML && python -m pytest -q tests
```

`tests/fake_jira.py` is an in-process Jira written from Atlassian's REST API v3 document, `tests/fake_link.py` an
in-process Knovas Link local API; `tests/harness.py` is the conformance harness
(`python -m knovas_connector_kit.conformance harness:make_harness` from `tests/` with both `src` folders on the path).
`test_site.py` runs the component behind Link, `test_dump.py` the dump, `test_image.py` the image, the vendored kit and
both compose files.
