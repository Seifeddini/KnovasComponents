# Knovas Jira connector

**Status: draft, off.** Connector protocol 2.0 (approved 2026-11-10). BET-2026-11-write-back, item WB-07; feature plan
FEAT-2026-11-10-write-back, Phase D (tasks J-1, J-2). Built on the [connector kit](../KnovasConnectorKit/README.md) and
tested against a fake Jira only: no image, deployment or Builder offer names it yet (J-4), and no test calls a real
Jira. Card: [`L3-JIRA-CONNECTOR`](../docs/components/L3-jira-connector.md); what it relies on in Jira:
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

## Tests

```bash
cd KnovasConnectorJira && pip install -r requirements.txt "pytest>=7.4,<9" && python -m pytest -q tests
```

`tests/fake_jira.py` is an in-process Jira written from Atlassian's REST API v3 document; `tests/harness.py` is the
conformance harness (`python -m knovas_connector_kit.conformance harness:make_harness` from `tests/` with both `src`
folders on the path).
