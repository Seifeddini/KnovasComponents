# Plan: Experiments module (Experimente) inside KnovasPlatform

Date: 2026-09-28 (revision 2, after a four-lens review of revision 1); revision 3 on 2026-09-29
Design: [../specs/2026-09-28-experiment-platform-design.md](../specs/2026-09-28-experiment-platform-design.md)
Component: `KnovasPlatform/components/docbridge_integration` (the Platform web app) plus an optional
sandbox service `KnovasPlatform/components/experiments_runner`.

**Revision 3** records what the verification rounds changed; the sections below are corrected in
place. In short: aggregates sum counts as `float8` and a row's count is at most 10**12 (§1, §3);
a level kind with more than 50 distinct values has `levels: None` (§3); the core pack is version 3
with five global `generic_*` metrics, and `offline_eval` describes with scope `{runs: latest}` too
(§5); `sample_size` takes `comparisons` (Bonferroni) and answers `alpha_used` (§8); summaries carry
`status_phase`, list pages may append `moved` items, evaluations carry `superseded`, and reuse and
retention follow the group rules in §8; `reindex` answers `{"queued": bool}`; the snapshot has
`runs_next_after` (§9); CSV imports are one per process (503) and may name a run (§8, §10); a
deadlock or serialization failure is a 409, wrong methods and the `/api/experiments/v1` root answer
in JSON, a too deeply nested JSON body is a 400 everywhere (§10); the index `on_dead` hook, the
maintenance, `purge_all(notes=)` and the shutdown hand-back are in §12; the search over-fetch and
`PLATFORM_TRUSTED_PROXY_HOPS` are in §13; the runner runs as uid 10101 with
`RUNNER_SOCKET_DIR_EXCLUSIVE` (§15).

**Values added to selection fields** (after revision 3, from "I can't add new segments"): migration
`0004_experiments_field_options.sql` adds `exp_field_options (domain_id, field_key, value)`, unique
per `lower(value)`. The values belong to the domain, not to a type version: `schema.validate_field_values(...,
extra_options=store.field_option_values(conn, domain_id))` accepts them for every `enum`/`multi_enum`
field with that key unless the field says `extensible: false` (`schema.is_extensible`,
`schema.effective_options`). `ExperimentService.list_field_options` / `add_field_option` (viewers;
the field must be an extensible selection field of a type usable in the domain, or of the
experiment named in `experiment`; another spelling of an existing value returns it; at most
`store.MAX_FIELD_OPTIONS` = 200 per field) / `delete_field_option` (managers; refused with 409 while
an experiment uses the value). Routes: `GET|POST /api/experiments/domains/<key>/field-options`,
`DELETE .../field-options/<id>`. The snapshot carries `field_options` (field key -> values); the
forms offer "+ Neuer Wert …". Packs: `domain.field_options` (export writes it, import adds values
and never removes one). Audit: `experiments.field_option.create` / `.delete`.

This document is the contract every part of the implementation codes against. Where it names a
function, a key, a table column, a JSON field or a German UI string, use exactly that name. The
migration `src/identity/migrations/0003_experiments.sql` is part of the contract: read it.

## 0. Requirements and resolved decisions

User requirements:

1. Lives inside KnovasComponents; hidden so the standard search view is not cluttered.
2. Can be disabled entirely.
3. Users can create new domains (not only the shipped four).
4. Robust and scalable technical choices, decided (not left open).
5. A version usable right away for Knovas' own experiments: search quality and engineering
   (from CI), LinkedIn/marketing tests, sales outreach, product/usability.
6. All texts, decisions and results searchable with Knovas.

| Question | Decision |
|---|---|
| Where it runs | A module of the Platform web app (Flask, Jinja, vanilla JS), code name `experiments`, UI label **Experimente**. Same login, roles, audit, deployment, look. |
| Hidden | Visible only to people with role `experimenter` or `experiments_manager` (or `admin`). Everyone else gets no nav item, 404 on every module route, and never sees experiment hits or pointers in search responses. Each experimenter can also switch experiment hits in the normal search off for themselves. |
| Disable | `EXPERIMENTS_ENABLED` (default **false**). Off = blueprints not registered, a gate answers redirect/404 on module paths, no worker threads, experiment hits and pointers stripped from every search response. |
| Storage | platform-db PostgreSQL (migration 0003). Measurements as sufficient statistics (sum, count, sum of squares, denominator) with a covering index for index-only aggregation; imports tracked in `exp_batches`. Scales to 10^8 rows; declarative partitioning is the documented next step. No TimescaleDB/ClickHouse. |
| Search | One Knovas document per experiment (pointer `experiments/<domain>/<KEY>`), re-uploaded after changes (debounced, rate-limited). Hits are recognised by pointer prefix and rendered from the Platform database. Knovas-side visibility: uploads carry `EXPERIMENTS_ACCESS_GROUPS`; with no group configured the indexer refuses to upload (fail closed) unless `EXPERIMENTS_INDEX_UNRESTRICTED=true` (documented for a folder rule on the prefix). |
| Background work | Postgres job queue `exp_jobs` (`FOR UPDATE SKIP LOCKED`, leases, fencing, priorities, retries with backoff, coalescing, dead-letter with `on_dead` hooks). Two daemon threads per gunicorn process (one for index/unindex/pipeline, one for evaluate). A shared rate slot keeps Knovas uploads under `EXPERIMENTS_INDEX_PER_MINUTE` across workers. |
| Indexing identity | A second KnovasAPIClient without principal broker (like RemoteController), built by `experiments.indexer.make_index_client(config)`. |
| Python and Julia | Built-in statistics in-process (pure Python). User evaluators run only in `experiments-runner`: own image (Python venv with numpy/scipy/pandas/statsmodels, Julia 1.11 with JSON3/Distributions/HypothesisTests/StatsBase/DataFrames), **no network at all** (`network_mode: none`), reached over a unix socket on a shared volume, non-root, read-only root FS, no capabilities, no-new-privileges, CPU/memory/pid limits, per-job subprocess with rlimits, wall-clock kill and cleanup of stray processes, no secrets mounted. Optional compose profile `experiments`. |
| Machine access (CI) | Personal access tokens (`exp_api_tokens`, SHA-256, expiry required) on `/api/experiments/v1/*` with `Authorization: Bearer`. Python SDK (stdlib only), Julia client. CI can log runs with per-query rows, trigger the pipeline and read verdicts. |
| Configurability | Domains, experiment types (versioned documents: fields, states, gated transitions, variant rules, default metrics, evaluation pipeline with scope), metrics (kind, unit, direction, min/max, levels), evaluators — all data, editable in the UI, exportable/importable as YAML packs (config-as-code). |

## 1. File ownership and working rules

Each part is owned by exactly one implementer. Do not edit files owned by another part. If a
contract is missing something, implement the smallest compatible addition in your own files and
report it in your final message. **Nobody edits the migration** (it is final; report schema needs).

| Part | Files (relative to `KnovasPlatform/components/docbridge_integration/` unless absolute) |
|---|---|
| FOUNDATION (done) | `src/identity/migrations/0003_experiments.sql`, `src/experiments/__init__.py`, `errors.py`, `permissions.py`, `settings.py`, the experiments fixtures at the end of `tests/conftest.py` |
| A schema+packs | `src/experiments/kinds.py`, `labels.py`, `schema.py`, `packs.py`, `packs/*.yaml`, `tests/test_experiments_schema.py`, `tests/test_experiments_kinds.py`, `tests/test_experiments_packs.py` |
| B stats+evaluators | `src/experiments/stats.py`, `evaluators.py`, `tests/test_experiments_stats.py`, `tests/test_experiments_evaluators.py` |
| C store+service | `src/experiments/store.py`, `service.py`, `csv_import.py`, `tests/test_experiments_store.py`, `tests/test_experiments_service.py`, `tests/test_experiments_csv.py` |
| D jobs+index+search | `src/experiments/jobs.py`, `indexer.py`, `runner_client.py`, `tasks.py`, `search.py`, `cli.py`, `__main__.py`, method `upload_text_document` in `src/knovas_client.py`, `tests/test_experiments_jobs.py`, `tests/test_experiments_indexer.py`, `tests/test_experiments_search.py`, `tests/test_experiments_runner_client.py` |
| E web | `src/web_interface/experiments_routes.py`, edits in `src/web_interface/app.py`, `src/identity/webauth.py`, `src/web_interface/admin.py`, `config/config.yaml`, `tests/test_experiments_routes.py`, `tests/test_experiments_switch.py`, `tests/test_experiments_api_tokens.py`, `tests/test_experiments_search_integration.py` |
| F frontend | `src/web_interface/templates/experiments_list.html`, `experiments_detail.html`, `experiments_manage.html`, `_sidebar.html` (nav item), `src/web_interface/static/js/experiments_common.js`, `experiments_list.js`, `experiments_detail.js`, `experiments_manage.js`, `static/css/experiments.css`, edits in `static/js/app.js`, `tests/test_experiments_frontend.py` |
| G runner+deploy | `KnovasPlatform/components/experiments_runner/**`, `/docker-compose.yml`, `/.github/workflows/ci.yml`, `/scripts/doctor.sh`, `/knovas.env.example`, `/docs/client/README.md` (settings rows) |
| H docs+sdk | `KnovasPlatform/experiments-sdk/**`, `KnovasPlatform/docs/features/experiments.md`, `/RELEASE_NOTES.md`, `/docs/superpowers/specs/2026-09-28-experiment-platform-design.md` |

Conventions for all parts:

- Python 3.11. No new dependency in `requirements.txt`. Python source ASCII only (German in
  Python strings via `ä`-style escapes). Templates, JS and YAML packs use real umlauts;
  write `ss` for `ß`. Identifiers, comments, commit messages English. UI copy German.
- Errors to the browser: German, never exception text; use `experiments/errors.py`
  (`NotFound()` default text "Nicht gefunden.").
- psycopg 3: rows are tuples; `%s` placeholders; JSONB via `psycopg.types.json.Jsonb(...)` or
  `json.dumps`; autocommit connections, `with conn.transaction():` for multi-statement writes.
  Aggregates cast in SQL so Python sees only int/float/str/None: `sum(count)::float8`
  (a BIGINT sum can overflow; float8 is exact to 2**53, and `kinds.MAX_COUNT` = 10**12 per
  row keeps sums exact), `count(*)::int`, `sum(...)::float8`, ids `::text`. Never return
  `Decimal`.
- JSON: timestamps ISO 8601 with timezone, ids strings, no NaN/Infinity anywhere (reject on
  input, `None` on output).
- Tests: `cd KnovasPlatform/components/docbridge_integration &&
  /tmp/claude-0/-home-user-KnovasComponents/8edc8a0c-cb19-5849-9edf-35f6fb3bc4c4/scratchpad/venv/bin/pytest tests/<file>`.
  PostgreSQL 16 runs locally. **Each part uses its own database**:
  `PLATFORM_DB_TEST_DSN=postgresql://platform:testpw@127.0.0.1:5432/knovas_platform_test_<a..h>`
  (part letter; all exist). DB tests use the `platform_db` fixture and
  `pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")`.
  Test apps never run the worker thread (the shared `experiments_app` fixture sets
  `worker.enabled: false`); tests drive jobs with `JobWorker.run_once(conn)`.
- Shared fixtures (end of `tests/conftest.py`): `EXPERIMENTS_TEST_YAML`, `FakeIndexClient`,
  `fake_index_client`, `experiments_app` (module on, index off, worker off,
  `experiments.indexer.make_index_client` patched to return `fake_index_client`),
  personas `experimenter` (eva@knovas.ch), `exp_manager` (max@knovas.ch), `member`,
  `platform_admin`, and signed-in clients `experimenter_client`, `exp_manager_client`,
  `exp_member_client`, `exp_admin_client` (they add X-CSRF-Token automatically).
  `_identity_app(..., extra_yaml=...)` appends config for tests that need other settings.

## 2. Configuration (`config/config.yaml`, part E)

```yaml
experiments:
  # Experimente: off unless switched on. Needs identity.enabled (per-user accounts).
  enabled: "${EXPERIMENTS_ENABLED:-false}"
  pointer_prefix: "${EXPERIMENTS_POINTER_PREFIX:-experiments}"
  index:
    enabled: "${EXPERIMENTS_INDEX_ENABLED:-true}"
    per_minute: "${EXPERIMENTS_INDEX_PER_MINUTE:-2}"
    debounce_seconds: "${EXPERIMENTS_INDEX_DEBOUNCE_SECONDS:-60}"
    access_groups: "${EXPERIMENTS_ACCESS_GROUPS:-}"
    unrestricted: "${EXPERIMENTS_INDEX_UNRESTRICTED:-false}"
  runner:
    # unix:///run/experiments-runner/runner.sock with COMPOSE_PROFILES=experiments
    url: "${EXPERIMENTS_RUNNER_URL:-}"
    timeout_seconds: "${EXPERIMENTS_RUNNER_TIMEOUT:-90}"
  worker:
    enabled: "${EXPERIMENTS_WORKER_ENABLED:-true}"
    poll_seconds: "${EXPERIMENTS_WORKER_POLL_SECONDS:-5}"
  max_csv_rows: "${EXPERIMENTS_MAX_CSV_ROWS:-200000}"
```

`experiments.settings.load_settings(config, identity_enabled=...)` reads it (written; a
set-but-empty variable means "default").

## 3. Measurement rows (storage contract)

Table `exp_measurements` (see migration). Every row belongs to one experiment, one metric, one
batch, optionally one variant and one run of the same experiment. Columns by metric kind:

| kind | value | count | denominator | sum_sq | estimate per variant |
|---|---|---|---|---|---|
| `proportion` | successes, integer, 0 <= value <= count | trials (every kind: integer 1..10**12) | NULL | NULL | sum(value)/sum(count) |
| `mean`, `duration`, `currency` | sum of the observations | number of observations | NULL | sum of squares (count = 1: exactly value^2, filled in automatically; count > 1: optional, must be >= value^2/count) | sum(value)/sum(count) |
| `count` | number of events, integer >= 0 | exposure units | NULL | NULL | sum(value)/sum(count) (a rate) |
| `ratio` | numerator sum (>= 0 for costs, any finite otherwise) | units (usually 1 per row) | denominator sum, >= 0 (0 allowed, e.g. a week without leads) | NULL | sum(value)/sum(denominator); NULL while sum(denominator) = 0 |
| `ordinal` | the level (a number) | units at that level (integer) | NULL | NULL (ignored) | sum(value*count)/sum(count) |
| `categorical` | the category code (integer) | units in that category (integer) | NULL | NULL | NULL (a distribution) |

Metric `definition` (validated by `schema.validate_metric_definition`, see §6): optional
`decimals` (0-6), `min`, `max` (value bounds for mean-like, ordinal), `levels` (object level
key -> label <= 80 chars, 2-50 entries; required for categorical, optional for ordinal; when
present, values must be defined levels).

`dims`: object, <= 20 keys, key `^[A-Za-z0-9_.-]{1,40}$`, values strings <= 200 chars (numbers
are stringified). `observed_at`: when the value happened. Every insert creates one
`exp_batches` row (`rows`, `metric_keys`, `source`, `filename`, `run_id`) — counts shown in the
UI come from `exp_batches`, never from counting `exp_measurements`.

**Scope** (which rows an aggregate or evaluation uses), a JSON object, `{}` = all rows:
`{"runs": "latest" | [run_id, ...], "since": iso, "until": iso, "dims": {key: value}}`.
`"latest"` = for each variant its newest run with status `finished` that has measurements of
this metric, plus rows without a run for variants that have no such run.

**Aggregate** (store returns, evaluators receive), one per variant key plus one with
`"variant": null` for rows without a variant (omitted when there are none):

```json
{"variant": "B", "rows": 12, "n": 10714, "value_sum": 175.0, "denominator_sum": null,
 "sum_sq": null, "estimate": 0.016334, "levels": null}
```

`n` = sum(count). `value_sum`: sum(value), except ordinal sum(value*count). `sum_sq`: for
mean-like kinds sum(sum_sq) with count=1 rows contributing value^2; NULL if any count>1 row
lacks sum_sq; ordinal sum(value*value*count); others NULL. `levels`: ordinal/categorical
`{level_key: units}` (level_key = `kinds.level_key(value)`), else NULL; also NULL for a level
kind whose group has more than `kinds.MAX_AGGREGATE_LEVELS` (50) distinct values (an ordinal
metric without defined levels) -- evaluators read that as "no distribution", not "no data"
(`chi_square`: n/a "nicht anwendbar: mehr als 50 Stufen"). `estimate` =
`kinds.estimate(kind, aggregate)`.

## 4. Experiment type definitions (part A validates, part C stores)

A type version's `definition` has exactly this shape:

```json
{
  "fields": [
    {"key": "channel", "label": "Kanal", "type": "enum", "options": ["LinkedIn", "E-Mail"],
     "required": false, "help": "Wo die Varianten laufen."}
  ],
  "states": [
    {"key": "draft", "label": "Entwurf"},
    {"key": "running", "label": "Läuft", "phase": "running"},
    {"key": "analysis", "label": "Auswertung"},
    {"key": "decided", "label": "Entschieden", "phase": "decided"},
    {"key": "stopped", "label": "Abgebrochen", "phase": "stopped"}
  ],
  "initial": "draft",
  "transitions": [
    {"from": "draft", "to": "running", "label": "Starten",
     "requires": ["hypothesis", "primary_metric", "variants:2"]},
    {"from": "running", "to": "analysis", "label": "Zur Auswertung", "requires": ["measurements"]},
    {"from": "analysis", "to": "decided", "label": "Entscheiden", "requires": ["decision"]},
    {"from": "*", "to": "stopped", "label": "Abbrechen"}
  ],
  "variants": {"min": 2, "max": 10,
               "defaults": [{"key": "A", "name": "Kontrolle", "is_control": true},
                            {"key": "B", "name": "Variante B"}]},
  "metrics": [{"metric": "ctr", "role": "primary"},
              {"metric": "bounce_rate", "role": "guardrail", "op": "max", "value": 0.7}],
  "evaluation": [{"evaluator": "builtin.bayes_proportion", "metric": "primary", "params": {},
                  "scope": {}}],
  "decision": {"require_learning": true}
}
```

Rules (`schema.validate_type_definition` enforces; `ValidationError(fields={path: message})`):

- `fields[].type`: `text` (<= 500 chars), `longtext` (<= 20000), `number`, `integer`, `enum`
  and `multi_enum` (need `options`, 1-50 unique strings <= 80; optional `extensible`, default true:
  the domain's added values count as options, see revision 3), `date` (`YYYY-MM-DD`), `url`
  (http/https, <= 2000), `boolean`. Optional `min`/`max` for number/integer. `key`
  `^[a-z][a-z0-9_]{0,39}$`, unique; <= 40 fields; `label` 1-80; `help` <= 300; `required` bool.
- `states`: 2-12, keys `^[a-z][a-z0-9_]{0,31}$`, unique; `label` 1-40; `phase` optional, one of
  `running` (entering it first time sets `started_at`), `decided` (sets `decided_at`, `ended_at`),
  `stopped` (sets `ended_at`); at most one state per phase. `initial` is a state without phase
  `decided`/`stopped`.
- `transitions`: 1-40; `from` a state key or `"*"` (every state except `to`); `to` a state key;
  `label` 1-40; `requires` list from the vocabulary; `roles` optional list from `experimenter`,
  `experiments_manager` (admin always allowed; missing/empty = any viewer). No duplicate
  (from, to).
- Requirement vocabulary and the German message when unmet (from `schema.check_transition`):
  `hypothesis` "Die Hypothese fehlt." · `primary_metric` "Es ist keine primäre Metrik
  festgelegt." · `variants:N` "Es braucht mindestens N Varianten." · `measurements` "Es gibt noch
  keine Messwerte." · `evaluation` "Es gibt noch keine abgeschlossene Auswertung." · `decision`
  "Es ist noch keine Entscheidung festgehalten." · `learning` "Die Erkenntnis fehlt." ·
  `field:<key>` "Das Feld «<label>» ist leer." · `n_planned:<field_key>` "Die geplante
  Stichprobe ist noch nicht erreicht (<n> von <planned> je Variante)." (the field must be an
  integer/number field; satisfied when every non-null variant's primary-metric `n` >= the value;
  unsatisfied when the field is empty).
- `variants`: `min` 0-10, `max` 1-20 (min <= max), `defaults` 0-10 items {`key`
  (exp_variants.key pattern), `name` <= 120, optional `is_control` (at most one), optional
  `allocation` 0..1}.
- `metrics`: 0-30 items; `metric` a metric key; `role` `primary` (at most one), `secondary`,
  `guardrail` (needs `op` `max`|`min` and finite `value`). Resolved in the experiment's domain
  first, then global; unresolved keys are refused when the definition is saved (the service
  checks, schema only checks shape).
- `evaluation`: 0-20 items; `evaluator` an evaluator key; `metric` `"primary"`, `"all"` (every
  assigned metric whose kind the evaluator accepts) or a metric key; `params` object validated
  against the evaluator's params schema (service); `scope` optional (§3 shape).
- `decision.require_learning`: bool.

## 5. Packs (part A)

YAML or JSON text. Parsing is only through `packs.parse_pack_text` / `schema.parse_definition_text`:
`json.loads` when the stripped text starts with `{`, otherwise `yaml.load(text,
Loader=NoAliasSafeLoader)` where `NoAliasSafeLoader(yaml.SafeLoader)` raises
`ValidationError("Anker und Verweise (&/*) sind nicht erlaubt.")` on any alias. Never
`yaml.load` with another loader, `full_load` or `unsafe_load`. Size limits before parsing:
definition text 200 KB, pack text 2 MB. `dump_pack` uses `yaml.safe_dump(sort_keys=False,
allow_unicode=True)`.

```yaml
pack: marketing            # ^[a-z][a-z0-9-]{1,31}$
title: Marketing
description: A/B-Tests, Kampagnen und Content-Tests.
version: 1
domain:                    # omitted in core.yaml
  {key: marketing, name: Marketing, id_prefix: MKT, color: "#eb6834", description: "..."}
metrics:
  - {key: ctr, name: Klickrate, kind: proportion, unit: "%", direction: higher,
     description: "...", definition: {decimals: 2}}
types:
  - {key: ab_test, name: A/B-Test, description: "...", definition: {...}}
evaluators:                # python/julia only; keys never start with "builtin."
  - {key: example.bootstrap_mean_py, name: "...", language: python, description: "...",
     input_kinds: [mean, duration, currency], params_schema: {...}, code: "..."}
requires_metrics: []       # global metric keys a type uses (export writes them)
```

`packs.py` API:

```python
PACKS_DIR: pathlib.Path
def available_packs() -> list[dict]      # [{"name","title","description","version","domain_key"}], core first
def load_pack(name: str) -> dict        # shipped pack, validated; NotFound("Das Paket gibt es nicht.")
def parse_pack_text(text: str) -> dict  # -> validate_pack(...) result; ValidationError
def validate_pack(pack: dict, *, known_metrics=(), known_evaluators=()) -> dict
    # normalised copy. Every type definition through schema.validate_type_definition; every
    # metric definition through schema.validate_metric_definition; metric references in types
    # must resolve within the pack's metrics or known_metrics (global keys); evaluator
    # references within the pack, evaluators.BUILTINS keys (import lazily) or known_evaluators.
def dump_pack(pack: dict) -> str
```

Shipped packs (`src/experiments/packs/*.yaml`). "A/B states" below means: draft "Entwurf",
running "Läuft" (phase running), analysis "Auswertung", decided "Entschieden" (phase decided),
stopped "Abgebrochen" (phase stopped); transitions draft→running "Starten", running→analysis
"Zur Auswertung" requires [measurements], analysis→decided "Entscheiden" requires [decision],
analysis→running "Weiterlaufen lassen", *→stopped "Abbrechen". `decision.require_learning: true`
everywhere. Every evaluation list starts with `{evaluator: builtin.describe, metric: all}`.

- `core` (global, version 3): five global metrics any domain resolves, also new ones —
  `generic_success_rate` "Erfolgsquote" (proportion, higher, "%"), `generic_events_per_period`
  "Ereignisse je Zeitraum" (count, higher), `generic_duration_s` "Dauer in Sekunden" (duration,
  lower, "s", min 0), `generic_score` "Messwert" (mean, higher), `generic_rating` "Bewertung 1–5"
  (ordinal, higher, levels 1–5); `ensure_core_pack` adds missing ones at every start and keeps
  edited ones. Type `hypothesis` "Allgemeine Hypothese" — no fields; A/B states with
  draft→running requires [hypothesis]; variants {min 0, max 10, defaults []}; metrics [];
  evaluation [describe/all]. Evaluators `example.bootstrap_mean_py` (Python, kinds mean,
  duration, currency, ordinal: bootstrap 95 % CI of each variant's mean difference to the
  control using numpy, needs rows) and `example.beta_binomial_jl` (Julia, kind proportion:
  P(better) by sampling Beta posteriors with the `Random` stdlib only). Both follow the output
  contract and are commented as templates.
- `engineering` (ENG, `#2a78d6`, "Engineering"): metrics
  `recall_at_20` "Recall@20" (mean, higher, "", decimals 3, min 0, max 1, description "Ein
  Messwert je Anfrage (dims.query) und Lauf."), `ndcg_at_10` "NDCG@10" (same), `mrr` "MRR"
  (same), `latency_p95_ms` "Latenz p95" (duration, lower, "ms", decimals 0, description "Ein
  Messwert je Benchmark-Lauf: das 95. Perzentil dieses Laufs. Die Schätzung ist das Mittel über
  die Läufe."), `latency_ms` "Latenz (Mittel)" (duration, lower, "ms", "Ein Wert je Anfrage."),
  `index_size_gb` "Indexgrösse" (mean, lower, "GB", one per run), `ci_minutes` "CI-Dauer"
  (duration, lower, "min", one per pipeline run), `error_rate` "Fehlerrate" (proportion, lower,
  "%", value = errors, count = requests). Types:
  `offline_eval` "Offline-Evaluation" (fields component enum [Suche, Ingestion, Vorschau,
  Cortex, RemoteController, Plattform, Sonstiges], query_set text, baseline_ref text,
  candidate_ref text; variants min 2 defaults `baseline` "Ausgangsstand" (control),
  `candidate` "Kandidat"; metrics primary ndcg_at_10, secondary recall_at_20, mrr, guardrail
  latency_p95_ms max 250; evaluation describe/all and `builtin.paired_t` on ndcg_at_10,
  recall_at_20, mrr with params {pair_by: query}, every step with scope {runs: latest} (pack
  version 2; version 1 had it on the t-tests only, so CI passes the scope explicitly)),
  `performance` "Performance-Änderung" (fields component, environment enum [lokal, CI,
  Staging, Produktion]; variants `vorher` "Vorher" (control), `nachher` "Nachher"; primary
  latency_p95_ms, guardrail error_rate max 0.01; evaluation describe, welch_t on primary),
  `rollout` "Feature-Rollout" (fields feature_flag text, rollout_percent number 0-100;
  variants `aus` "Flag aus" (control), `an` "Flag an"; primary error_rate, guardrail
  latency_p95_ms max 300; evaluation describe, two_proportion and bayes_proportion on primary).
- `marketing` (MKT, `#eb6834`, "Marketing"): metrics `ctr` "Klickrate" (proportion, higher,
  "%", clicks/impressions), `conversion_rate` "Konversionsrate" (proportion, higher),
  `demo_request_rate` "Demo-Anfragequote" (proportion, higher), `bounce_rate` "Absprungrate"
  (proportion, lower), `cost_per_click` "Kosten pro Klick" (ratio, lower, "CHF", value =
  Kosten, denominator = Klicks, one row per day or week), `cost_per_lead` "Kosten pro Lead"
  (ratio, lower, "CHF", value = Kosten, denominator = Leads). Types:
  `ab_test` "A/B-Test" (fields channel enum [LinkedIn, Google Ads, E-Mail, Website, Webinar,
  Messe, Sonstiges], audience text, budget number, campaign_ref text; A/B variants; primary
  ctr, secondary conversion_rate, cost_per_click, guardrail bounce_rate max 0.7; evaluation
  describe, bayes_proportion and two_proportion on primary, ratio_delta on cost_per_click),
  `campaign` "Kampagne" (fields channel, audience, budget, goal longtext; variants min 0
  defaults []; primary demo_request_rate, secondary ctr, cost_per_lead; transitions without
  variants; evaluation describe/all), `content_test` "Content-Test" (fields channel, format
  enum [Text, Bild, Karussell, Video, Dokument]; A/B; primary ctr; evaluation describe, bayes).
- `sales` (SAL, `#1baf7a`, "Vertrieb"): metrics `reply_rate` "Antwortrate", `meeting_rate`
  "Terminquote", `pilot_conversion` "Pilot → Vertrag", `win_rate` "Abschlussquote" (all
  proportion, higher), `unsubscribe_rate` "Abmelderate" (proportion, lower), `pipeline_value`
  "Pipeline-Wert" (currency, higher, "CHF", "ein Wert je angeschriebenem Kontakt, 0 ohne
  Opportunity"), `deal_value` "Vertragswert" (currency, higher, "CHF", one per offer, 0 if
  lost), `cycle_days` "Verkaufszyklus" (duration, lower, "Tage"). Types: `playbook`
  "Playbook-Test" (fields segment enum [Kanzlei klein, Kanzlei mittel, Kanzlei gross,
  Rechtsabteilung, Sonstiges], territory enum [Schweiz, Deutschland, Österreich, Sonstiges],
  sequence text, planned_n integer min 1; A/B variants; primary meeting_rate, secondary
  reply_rate, pipeline_value, guardrail unsubscribe_rate max 0.02; running→analysis requires
  [measurements, n_planned:planned_n]; evaluation describe, bayes, two_proportion), `pricing`
  "Preis-Test" (fields segment, price_model text; variants A "Aktueller Preis" (control), B
  "Neuer Preis"; primary win_rate, secondary deal_value; evaluation describe, bayes, welch_t on
  deal_value).
- `product` (PRD, `#4a3aa7`, "Produkt"): metrics `task_success` "Aufgabenerfolg"
  (proportion, higher), `sus_score` "SUS-Wert" (mean, higher, "Punkte", min 0, max 100,
  decimals 1, one per participant), `time_to_value_s` "Zeit bis Ergebnis" (duration, lower,
  "s"), `satisfaction` "Zufriedenheit" (ordinal, higher, levels {"1": "sehr unzufrieden", "2":
  "unzufrieden", "3": "neutral", "4": "zufrieden", "5": "sehr zufrieden"}), `preferred_option`
  "Bevorzugte Variante" (categorical, none, levels {"1": "Variante A", "2": "Variante B", "3":
  "Variante C", "0": "Keine Präferenz"}). Types: `usability` "Nutzertest" (fields sessions
  integer, persona text, script longtext; variants min 0 defaults [] ; primary task_success,
  secondary sus_score, satisfaction, time_to_value_s; transitions without variants;
  evaluation describe/all with params {target: 0.8} on task_success), `feature`
  "Feature-Rollout" (variants A "Ohne Feature" (control), B "Mit Feature"; primary
  task_success; evaluation describe, bayes).

## 6. Part A module APIs

`src/experiments/labels.py` (German labels, `\u` escapes in source):

```python
DECISION_VERDICT_LABELS = {"ship": "Übernehmen", "iterate": "Weiterentwickeln",
                           "stop": "Verwerfen", "inconclusive": "Ohne klares Ergebnis"}
EVALUATION_VERDICT_LABELS = {"better": "besser", "worse": "schlechter",
                             "inconclusive": "offen", "n/a": "–"}
NOTE_KIND_LABELS = {"note": "Notiz", "observation": "Beobachtung", "interview": "Interview",
                    "feedback": "Rückmeldung", "status": "Statuswechsel"}
DIRECTION_LABELS = {"higher": "höher ist besser", "lower": "tiefer ist besser",
                    "none": "ohne Richtung"}
METRIC_ROLE_LABELS = {"primary": "primär", "secondary": "sekundär", "guardrail": "Leitplanke"}
RUN_STATUS_LABELS = {"running": "läuft", "finished": "abgeschlossen",
                     "failed": "fehlgeschlagen", "cancelled": "abgebrochen"}
EVALUATION_STATUS_LABELS = {"queued": "wartet", "running": "läuft", "done": "fertig",
                            "failed": "fehlgeschlagen"}
INDEX_STATE_LABELS = {"pending": "ausstehend", "indexed": "aktuell", "error": "Fehler", "off": "aus"}
SOURCE_LABELS = {"manual": "von Hand", "api": "API", "csv": "CSV"}
```

`src/experiments/kinds.py`:

```python
@dataclass(frozen=True)
class KindSpec:
    key: str; label: str; description: str
    needs_denominator: bool; is_distribution: bool; integral_value: bool
    value_label: str; count_label: str; denominator_label: str; sum_sq_label: str
    default_evaluators: tuple[str, ...]
KINDS: dict[str, KindSpec]
    # proportion "Anteil" (value "Erfolge", count "Versuche"), mean "Mittelwert" (value "Summe
    # der Werte", count "Anzahl"), count "Rate" (value "Ereignisse", count "Einheiten"),
    # duration "Dauer", currency "Geldbetrag", ratio "Verhältnis" (value "Zähler", denominator
    # "Nenner", count "Einheiten"), ordinal "Skala" (value "Stufe", count "Anzahl"),
    # categorical "Kategorie" (value "Kategorie", count "Anzahl"); sum_sq_label
    # "Quadratsumme (optional)"
def validate_row(kind: str, row: dict, definition: dict | None = None) -> dict
    # -> {"value","count","denominator","sum_sq"} normalised per §3; ValidationError with a
    # German message: non-finite ("Der Wert muss eine endliche Zahl sein."), count < 1 or not
    # integral, proportion value not integral ("Erfolge müssen eine ganze Zahl sein.") or > count,
    # count kind value not integral/negative ("Ereignisse müssen eine ganze Zahl sein."),
    # ratio denominator missing/negative, sum_sq inconsistent ("Die Quadratsumme passt nicht
    # zum Wert."), min/max violated ("Der Wert liegt ausserhalb von <min>–<max>."), level not
    # defined ("Wert <v> ist keine Stufe von «<name>»." -- name from definition.get("_name")).
def estimate(kind: str, agg: dict) -> float | None
def level_key(value: float) -> str        # 3.0 -> "3", 2.5 -> "2.5"
def format_number(x: float | None, decimals: int = 2) -> str
    # decimal comma, ASCII apostrophe thousands: 10714.5 -> "10'714,50"; None -> "–"
def format_value(kind: str, x: float | None, unit: str = "", decimals: int | None = None) -> str
    # proportion -> percent "1,63 %"; others format_number + " " + unit (unit "%" also gets a space)
def format_diff(kind: str, x: float | None, unit: str = "", decimals: int | None = None) -> str
    # signed; proportion -> "+0,42 Pp."; others "+12,5 ms"
```

`src/experiments/schema.py`:

```python
FIELD_TYPES: tuple[str, ...]
REQUIREMENT_KEYS: tuple[str, ...]
TYPE_DEFINITION_SCHEMA: dict
def parse_definition_text(text: str) -> dict               # §5 safe parsing; ValidationError
def validate_type_definition(definition: dict) -> dict     # normalised; ValidationError(fields=...)
def validate_metric_definition(kind: str, definition: dict | None) -> dict
def validate_scope(scope: dict | None) -> dict             # §3 scope; ValidationError
def validate_field_values(definition: dict, values: dict, *, partial: bool = False) -> dict
    # unknown keys -> ValidationError; required-missing only when not partial; "" / None removes;
    # number/integer must be finite
def display_field_value(field: dict, value) -> str        # German ("ja"/"nein", dates TT.MM.JJJJ)
def initial_state(definition) -> str
def state_label(definition, state) -> str
def state_phase(definition, state) -> str | None
def phase_state(definition, phase: str) -> str | None      # the state carrying that phase
def transitions_from(definition, state) -> list[dict]      # [{"to","label","requires","roles"}]
def check_transition(definition, from_state, to_state, *, facts: dict, roles: frozenset) -> list[str]
    # ValidationError("Dieser Statuswechsel ist nicht vorgesehen.") if undefined;
    # Forbidden("Diesen Statuswechsel dürfen nur ... ausführen.") if roles do not allow;
    # returns unmet messages ([] = allowed). facts: {"hypothesis": bool, "primary_metric": bool,
    # "variants": int, "measurements": int, "evaluations": int, "decision": bool,
    # "learning": bool, "fields": {key: value}, "variant_n": {variant_key: n of primary metric}}
def metric_refs(definition) -> list[str]
def evaluator_refs(definition) -> list[str]
```

## 7. Part B module APIs

`src/experiments/stats.py` — pure Python (math, random). Deterministic. Every function
documents method and returns a dict of floats/ints (NaN never returned: None instead).

```python
normal_cdf(x); normal_ppf(p); student_t_cdf(t, df); student_t_ppf(p, df)
chi2_sf(x, df); betainc(a, b, x); gammaincc(s, x); binom_cdf(k, n, p)
wilson_interval(s, n, alpha=0.05) -> (lo, hi)
poisson_interval(k, t, alpha=0.05) -> (lo, hi)          # exact (chi-square) CI of rate k/t
t_interval(mean, var, n, alpha=0.05) -> (lo, hi)
two_proportion_test(s1, n1, s2, n2, alpha=0.05) -> {p1, p2, diff, ci_low, ci_high, z, p_value, relative_lift}
bayes_beta_binomial(s1, n1, s2, n2, prior_a=1.0, prior_b=1.0, draws=40000, seed=7)
    -> {prob_better, expected_loss, diff_mean, ci_low, ci_high}     # draws/seed internal only
welch_t_test(mean1, var1, n1, mean2, var2, n2, alpha=0.05) -> {diff, ci_low, ci_high, t, df, p_value}
paired_t_test(diffs: list[float], alpha=0.05) -> {mean_diff, ci_low, ci_high, t, df, p_value, n_pairs}
poisson_rate_test(e1, t1, e2, t2, alpha=0.05) -> {rate1, rate2, ratio, ci_low, ci_high, p_value}
ratio_delta(num1: list, den1: list, num2: list, den2: list, alpha=0.05)
    -> {ratio1, ratio2, diff, ci_low, ci_high, p_value}   # delta method, units = rows
chi_square_independence(table: list[list[float]]) -> {chi2, df, p_value, cramers_v}
chi_square_goodness_of_fit(observed: list[float], expected: list[float] | None = None) -> {chi2, df, p_value}
binomial_test(k, n, p=0.5) -> {p_value}                   # two-sided exact
holm(p_values: list[float]) -> list[float]
sample_size_proportion(p_base, mde_abs, alpha=0.05, power=0.8) -> int     # per arm
sample_size_mean(sd, mde_abs, alpha=0.05, power=0.8) -> int
variance(sum_, sum_sq, n) -> float | None   # max(0.0, (sum_sq - sum_**2/n)/(n-1)); None if n < 2 or sum_sq None
```

`src/experiments/evaluators.py`:

```python
@dataclass(frozen=True)
class BuiltinSpec:
    key: str; name: str; description: str
    input_kinds: tuple[str, ...]; params_schema: dict; needs_rows: bool
    fn: Callable[[dict], dict]
BUILTINS: dict[str, BuiltinSpec]
  # key                        name (German)                     kinds                              rows
  # builtin.describe           "Beschreibung je Variante"        all                                no
  # builtin.two_proportion     "Zwei-Anteile-Test"               proportion                         no
  # builtin.bayes_proportion   "Bayes-Vergleich (Anteile)"       proportion                         no
  # builtin.welch_t            "Welch-t-Test"                    mean, duration, currency, ordinal  no
  # builtin.paired_t           "Gepaarter t-Test"                mean, duration, currency, ordinal  yes
  # builtin.poisson_rate       "Raten-Vergleich"                 count                              no
  # builtin.ratio_delta        "Verhältnis-Vergleich"            ratio                              yes
  # builtin.chi_square         "Chi-Quadrat-Test"                categorical, ordinal               no
  # params_schema: closed JSON Schemas (additionalProperties false):
  #   describe {target: number}; two_proportion/welch_t/poisson_rate/chi_square/ratio_delta
  #   {alpha: 0.001..0.2, correction: "holm"|"none"}; paired_t {alpha, correction,
  #   pair_by: string ^[A-Za-z0-9_.-]{1,40}$ (default "query")}; bayes_proportion
  #   {threshold: 0.5..0.999, prior_a: 0.01..1000, prior_b: 0.01..1000}; chi_square also
  #   {expected: array of numbers > 0, <= 50}
def validate_params(schema: dict, params: dict | None) -> dict   # jsonschema; ValidationError(fields); json <= 4 KB
def build_input(*, snapshot: dict, metric: dict, aggregates: list[dict], rows: list[dict] | None,
                rows_truncated: bool, params: dict, scope: dict) -> dict
def run_builtin(key: str, data: dict) -> dict          # validates data["params"] first
def sanitize_output(raw, *, evaluator_name: str = "") -> dict
def default_params(key: str) -> dict
```

`build_input` maps the §9 snapshot: `experiment` = {key, title, hypothesis, domain:
snapshot.domain.key, type: snapshot.type.key, status, fields: snapshot.field_values, tags};
`metric` = the snapshot metric entry without `aggregates`, plus `guardrail` = {op, value} or
null and `definition`; `variants` = [{key, name, is_control}]. Evaluator input contract:

```json
{
  "experiment": {"key": "MKT-1", "title": "...", "hypothesis": "...", "domain": "marketing",
                 "type": "ab_test", "status": "running", "fields": {"channel": "LinkedIn"}, "tags": []},
  "metric": {"key": "ctr", "name": "Klickrate", "kind": "proportion", "unit": "%",
             "direction": "higher", "role": "primary", "definition": {}, "guardrail": null},
  "variants": [{"key": "A", "name": "Kontrolle", "is_control": true}],
  "aggregates": [{"variant": "A", "rows": 12, "n": 10688, "value_sum": 129.0,
                  "denominator_sum": null, "sum_sq": null, "estimate": 0.01207, "levels": null}],
  "rows": [{"variant": "A", "run": null, "value": 11.0, "count": 900, "denominator": null,
            "sum_sq": null, "observed_at": "2026-09-16T00:00:00+00:00", "dims": {}}],
  "rows_truncated": false,
  "scope": {},
  "params": {}
}
```

`rows` is present for custom evaluators and builtins with `needs_rows`, else `[]`.

Output contract:

```json
{
  "verdict": "better | worse | inconclusive | n/a",
  "headline": "P(B besser als A) = 99,6 %",
  "summary": "Markdown <= 20000 chars (indexed into Knovas)",
  "comparisons": [{"variant": "B", "baseline": "A", "label": "Differenz", "estimate": 0.0042,
                   "ci_low": 0.0011, "ci_high": 0.0073, "p_value": null, "prob_better": 0.996,
                   "relative": 0.348, "unit": "Pp.", "verdict": "better"}],
  "variants": [{"variant": "A", "n": 10688, "value": 0.01207, "sd": null, "sum": 129.0,
                "ci_low": 0.0101, "ci_high": 0.0143}],
  "values": {"prob_better": 0.996, "guardrail_ok": true},
  "table": {"headers": ["..."], "rows": [["..."]]},
  "warnings": ["..."]
}
```

Rules: compare each non-control variant with the control (`is_control`, else the first variant;
rows/aggregates with variant null are not compared). A difference's `estimate` is in the
metric's natural units (proportion: fraction 0..1, `unit` "Pp."; ratio_delta: unit of the metric;
poisson_rate: ratio, unit "x"). `direction` `lower` flips "better"; `none` gives `n/a`
verdicts but still reports numbers. Per comparison: Bayes `better` when prob_better >=
threshold (default 0.95), `worse` when <= 1-threshold; frequentist `better`/`worse` when the
(Holm-adjusted when > 1 comparison and correction != "none", with warning "p-Werte nach Holm
korrigiert.") p_value < alpha (default 0.05). Overall verdict: `better` if any comparison is
better (headline names the best), else `worse` if any is worse, else `inconclusive`.
`describe`: per variant n, estimate, 95 % interval (Wilson for proportion, t for mean-like and
ordinal when variance is known and > 0 -- zero spread gives no interval and the warning
"<label>: keine Streuung in den Daten; kein Konfidenzintervall.", exact Poisson for count, none
for ratio/categorical), `sum`; guardrail check
(metric.guardrail: a variant whose estimate violates it gets the warning "Leitplanke verletzt:
Variante B 78,0 % > 70,0 %." and `values.guardrail_ok = false`, verdict `worse`); with param
`target`, verdict better/worse when every variant's interval lies on the good/bad side of the
target (respecting direction), else inconclusive, headline e.g. "Aufgabenerfolg 75,0 %
(95 %-KI 40,9–92,9 %) – Ziel 80,0 % nicht belegt."; a proportion target outside 0..1 gives no
target verdict, only the warning "Ziel ausserhalb 0..1 – für Anteile 0,8 statt 80 angeben." (the
service refuses such a target first); else verdict `n/a`. `chi_square`: with
>= 2 variants having data independence test, else goodness of fit against equal shares (or
`expected`), with the exact binomial test when 2 categories and n < 30; every comparison's
verdict is `n/a`, and so is the overall one when there is a comparison (a distribution test has
no direction, also on a scale: the mean level is `welch_t`'s question); a tested group with `levels` None gives `n/a` "Chi-Quadrat-Test:
nicht anwendbar: mehr als 50 Stufen" ("… Kategorien"). `paired_t`: pairs control
and variant rows with the same `dims[pair_by]` (row values averaged per key and variant);
warning "N Zeilen ohne Partner ignoriert." Too little data (n < 2, no pairs, missing variance)
gives `inconclusive` with a German warning, never an exception.

`sanitize_output(raw, evaluator_name=...)`: not an object -> ValidationError("Der Auswerter
hat kein Objekt zurückgegeben."). Keeps the contract keys; unknown top-level keys with
scalar values move into `values` (warning "Nicht vorgesehene Felder nach values verschoben: a, b"),
others are dropped (warning "Nicht übernommen: c"). Limits: headline <= 200 (missing ->
f"{evaluator_name}: {len(values)} Werte"), summary <= 20000, comparisons <= 50, variants <= 50,
values <= 100 keys (numbers, strings <= 500, booleans), table <= 20 headers and <= 200 rows of
<= 20 cells (stringified, <= 200 chars), warnings <= 20 of <= 300; unknown verdicts -> `n/a`;
NaN/Infinity -> null; total `json.dumps` <= 256 KB (clip table, then summary; warning
"Ausgabe gekürzt.").

## 8. Part C: store, service, CSV

`src/experiments/store.py` holds all SQL except the job queue (part D). Functions take `conn`
first. Rules:

- Version bumps: `UPDATE exp_types SET current_version = current_version + 1, updated_at =
  now() WHERE id=%s RETURNING current_version` then INSERT the version row, one transaction (same
  for evaluators; metrics bump `version` on update).
- Idempotent inserts (`ensure_builtin_evaluators`, `ensure_core_pack`, `install_pack`): `INSERT
  ... ON CONFLICT ... DO NOTHING`, then re-SELECT. `psycopg.errors.UniqueViolation` ->
  `Conflict` with a German message ("Der Schlüssel «x» ist schon vergeben.").
- Experiment keys: `UPDATE exp_domains SET next_seq = next_seq + 1, updated_at = now() WHERE
  id = %s RETURNING id_prefix, next_seq - 1`.
- `row_version` is bumped only by changes to the experiment's own editable state:
  update_experiment, transition, set_variants, set_metrics, decide, archive. Those start with
  `SELECT ... FROM exp_experiments WHERE key = %s FOR UPDATE` in a transaction and, when the
  caller sent `row_version`, raise `Conflict("Das Experiment wurde inzwischen geändert. Bitte
  neu laden.")` on mismatch (required for update_experiment, optional elsewhere). Child writes
  (measurements, batches, runs, notes, evaluations) and worker writes set only `updated_at`.
  `set_index_state` touches neither.

Functions other parts use (exact signatures):

```python
def load_snapshot(conn, key_or_id: str, *, actor=None) -> dict | None      # §9; actor for can_delete flags
def lookup_by_keys(conn, keys: list[str]) -> dict[str, dict]
    # {KEY: {"key","title","hypothesis","status","status_label","archived","domain_key",
    #        "domain_name","domain_color","type_name","updated_at"}}
def aggregates(conn, experiment_id: str, metric_id: str, *, kind: str, scope: dict | None = None) -> list[dict]
def evaluator_rows(conn, experiment_id: str, metric_id: str, limit: int, *, scope: dict | None = None) -> tuple[list[dict], bool]
    # newest `limit` rows (ORDER BY id DESC) returned in ascending id order, input-contract row
    # shape (variant key, run id); second value = more rows exist
def timeseries(conn, experiment_id: str, metric_id: str, *, kind: str, bucket: str, scope=None) -> list[dict]
    # bucket 'day'|'week'|'month' (date_trunc on observed_at, UTC):
    # [{"bucket_start","variant","n","value_sum","denominator_sum","estimate"}]
def set_index_state(conn, experiment_id: str, state: str, error: str | None = None, *,
                    if_updated_at: str | None = None) -> None
    # 'indexed' sets indexed_at=now() and is skipped when if_updated_at is given and the row's
    # updated_at differs (a newer edit keeps 'pending'); error text <= 500 chars (German, fixed),
    # kept for 'error' and 'off': purge-index writes 'off' with INDEX_OFF_PURGED, while 'off'
    # without a text means "indexing was switched off" (maintenance re-uploads those, §12)
def record_index_document(conn, pointer: str, experiment_id: str | None) -> None   # upsert exp_index_documents
def forget_index_document(conn, pointer: str) -> None
def index_documents(conn, after: str | None = None, limit: int = 500) -> list[str]  # pointers, ordered
def experiments_for_reindex(conn, *, domain_id=None, type_id=None, states=None,
                            switched_off=False) -> list[str]
    # experiment ids, newest change first; switched_off adds the 'off' rows without a text
def resolve_api_token(conn, plaintext: str) -> dict | None
    # {"token_id","user_id"} for an unrevoked, unexpired token (hash compare in SQL on the
    # SHA-256 hex); updates last_used_at at most once a minute
def get_runtime_setting(conn, key: str)        # only keys in settings.RUNTIME_DEFAULTS, else ValidationError
def set_runtime_setting(conn, key: str, value, actor) -> None   # same whitelist + type check
def get_user_show_in_search(conn, user_id) -> bool              # USER_PREF_SHOW_IN_SEARCH, default True
def set_user_show_in_search(conn, user_id, value: bool) -> None
def get_evaluation_record(conn, evaluation_id: str) -> dict | None
    # {"id","experiment_id","experiment_key","evaluator_id","evaluator_key","language","code",
    #  "version","params_schema","metric_id","params","scope","status","trigger","created_at"}
def mark_evaluation(conn, evaluation_id: str, *, status: str, output=None, error=None,
                    logs=None, duration_ms=None) -> None
    # 'running' sets started_at; 'done'/'failed' set finished_at = clock_timestamp();
    # logs <= 64 KB; error a fixed German message
def ensure_builtin_evaluators(conn) -> None
def ensure_core_pack(conn) -> None             # installs packs.load_pack('core') if missing
```

`src/experiments/csv_import.py` (Flask-free):

```python
def parse_csv(content: bytes, *, metrics: dict[str, dict], variants: set[str], max_rows: int,
              runs=None) -> dict
    # -> {"rows": [row dicts as for add_measurements], "ignored_columns": [...], "lines": [...]}
    # or ValidationError listing <= 20 errors "Zeile 5: ...". runs: {name: [run ids]} (or a
    # callable returning it, called once) so the run column may name a run.
```

UTF-8 (BOM tolerated), delimiter sniffed among `,` `;` tab. Header required, names
`^[A-Za-z0-9_. -]{1,60}$`. **Long format** (header has `metric`): columns metric, variant,
value, count, denominator, sum_sq, observed_at, run (a run id, or the name of exactly one of the
experiment's runs; a shared name is refused "N Läufe heissen «…»; bitte die Lauf-ID angeben."),
`dim.<name>`. **Wide format** (no
`metric` column): a column named exactly a metric key is that metric's value;
`<key>.count`, `<key>.denominator`, `<key>.sum_sq` its other fields; plus variant,
observed_at, run, `dim.<name>`; one row per metric column with a non-empty value. Unknown
columns are ignored and returned in `ignored_columns`; at most 20 dim columns. Numbers: strip
`'`, `’`, spaces and NBSP; a single `,` without `.` is a decimal comma; reject nan/inf.
Dates: ISO 8601 or `TT.MM.JJJJ` (UTC midnight). Cells <= 200 chars. Rows > max_rows ->
ValidationError("Die Datei hat mehr als <max> Zeilen; bitte aufteilen.").

`src/experiments/service.py`:

```python
class ExperimentService:
    def __init__(self, conn, actor, settings, *, runner=None, knovas_search=None,
                 request_meta: dict | None = None): ...
        # actor: identity.users.User; runner: RunnerClient | None;
        # knovas_search: callable(query, limit) -> {"results": [...]} (request-bound client) | None;
        # request_meta: {"ip","user_agent","token_id"} passed to audit.record (token_id into detail)
```

Every public method first checks `permissions.can_view(actor)` (else `NotFound()`); [manage]
methods check `can_manage` (else `Forbidden("Nur für Verantwortliche der Experimente.")`).
Audit: `identity.audit.record(conn, action=..., actor=actor, target_type=..., target_id=...,
detail=..., ip=..., user_agent=...)` after every mutation; `target_type='experiment'` ->
target_id the experiment KEY; `exp_domain` -> domain key; `exp_type`/`exp_metric`/
`exp_evaluator`/`exp_token`/`exp_settings` -> row id or setting key. Detail holds ids, keys,
counts only — never bodies, values, code or tokens. After experiment content changes:
`_queue_index(experiment_id, priority=10)` = if `settings.index_enabled`: `JobQueue(conn).enqueue(
'index', {"experiment_id": id}, dedupe_key=f"index:{id}", delay_seconds=settings.index_debounce_seconds,
priority=10)` and `index_state='pending'`; else `index_state='off'`. After measurements are
added (any path): `JobQueue(conn).enqueue('pipeline', {"experiment_id": id},
dedupe_key=f"pipeline:{id}", delay_seconds=30)`.

Methods (return values are the JSON payloads of §10):

```python
# domains
list_domains(include_archived=False) -> list[dict]
    # {"id","key","name","description","color","id_prefix","pack","archived","experiment_count","running_count"}
create_domain(data) -> dict        # [manage] key, name, id_prefix, color?, description?
update_domain(key, data) -> dict   # [manage] name?, color?, description?, archived?; a changed name
                                   # re-queues index for all its experiments (priority 200)
export_domain(key) -> str          # [manage] YAML pack: domain, its metrics and types, the custom
                                   # evaluators its types reference, requires_metrics (global keys)
# types
list_types(domain=None, include_archived=False) -> list[dict]
    # {"id","key","name","description","domain_key"|None,"current_version","archived","definition",
    #  "experiment_count"}; domain=<key> -> that domain's plus global
get_type(type_id) -> dict          # + "versions": [{"version","created_at","created_by": display name}]
                                   # + "definition_yaml" (the editor's text, natural key order)
validate_type(data) -> dict        # [manage] {definition|definition_text, domain} -> normalised
                                   # definition; checks metric/evaluator resolution and params
create_type(data) -> dict          # [manage] domain (key|None), key, name, description?,
                                   # definition|definition_text|copy_from (type id)
add_type_version(type_id, data) -> dict   # [manage]; identical definition -> no new version;
                                          # changed name re-queues its experiments' index
set_type_archived(type_id, archived) -> dict   # [manage]
# metrics
list_metrics(domain=None, include_archived=False) -> list[dict]
    # {"id","key","name","kind","kind_label","unit","direction","direction_label","description",
    #  "definition","domain_key"|None,"archived","version","in_use": bool}
create_metric(data) -> dict        # [manage] domain (key|None), key, name, kind, unit?, direction?,
                                   # description?, definition?
update_metric(metric_id, data) -> dict   # [manage] kind change refused when
                                         # EXISTS(measurements) ("Die Art lässt sich nicht mehr
                                         # ändern, es gibt schon Messwerte.")
# evaluators
list_evaluators(include_archived=False) -> list[dict]
    # {"id","key","name","language","description","input_kinds","current_version","archived",
    #  "builtin": bool,"params_schema","needs_rows": bool}
get_evaluator(evaluator_id) -> dict          # + "code" (current), "versions"
create_evaluator(data) -> dict               # [manage] key (not "builtin."), name, language
                                             # python|julia, description, code, input_kinds, params_schema
add_evaluator_version(evaluator_id, data) -> dict   # [manage]
test_evaluator(evaluator_id, data) -> dict   # [manage] experiment (KEY), metric (key), params?,
    # scope?, code? (unsaved); runner.run with timeout min(settings.runner_timeout_seconds, 60);
    # audits "experiments.evaluator.test" {experiment, metric, code_sha256, unsaved};
    # -> {"ok","output","error","logs","duration_ms"}
sample_size(data) -> dict                    # {kind: proportion|mean, base|sd, mde, alpha?, power?,
    # comparisons? (1..50, default 1: variants compared with the control)} -> {"per_variant": n,
    # "alpha_used": alpha / comparisons (Bonferroni), "comparisons"}
# experiments
list_experiments(*, domain=None, status=None, q=None, tag=None, include_archived=False,
                 after=None, limit=50) -> dict
    # {"items": [summary], "next_after": str|None, "total": int}; summary: {"key","title","status",
    #  "status_label","archived","tags","domain": {"key","name","color"},"type": {"key","name"},
    #  "owner": {"id","display_name"}|None,"primary_metric": {"key","name","unit","kind"}|None,
    #  "latest": {"headline","verdict","finished_at"}|None, "guardrail_violations": int,
    #  "updated_at","index_state","status_phase"}; order updated_at DESC, id DESC; q ILIKE on
    #  key/title/hypothesis. The cursor carries when its page was served; the next page appends,
    #  flagged "moved": true, up to `limit` experiments above the cursor changed since then (clients
    #  merge items by key).
    # latest = among the primary metric's current evaluations (not superseded, see run_pipeline:
    # the newest of each group) that are done, the newest with verdict <> 'n/a', else the newest
create_experiment(data) -> dict   # snapshot; domain (key), type (id or key), title, hypothesis?,
                                  # description?, fields?, tags?, variants?, metrics? (defaults from type)
get_experiment(key) -> dict       # snapshot + "definition" + "transitions" [{"to","label","allowed",
                                  # "missing": [...], "needs_comment": bool, "decides": bool}]
                                  # + "evaluators" (usable for this experiment's metric kinds)
update_experiment(key, data) -> dict   # row_version required; title?, hypothesis?, description?,
                                       # fields? (partial), tags? (<= 20 x 50), owner_id?, archived?
transition(key, data) -> dict     # to, comment?, row_version?; entering a 'stopped'-phase state needs a
    # comment (ValidationError fields {"comment": "Bitte einen Grund für den Abbruch angeben."});
    # a transition into the 'decided' phase is done by decide(), here it is refused with
    # ValidationError("Bitte die Entscheidung über das Formular festhalten."); a non-empty comment
    # becomes an exp_notes row kind 'status', body "«<from>» → «<to>»: <comment>"
set_variants(key, data) -> dict   # variants (full list; kept by key), row_version?; min/max from the
                                  # type; removing a variant with data -> ValidationError("Die Variante
                                  # «B» hat Messwerte und kann nicht entfernt werden.")
set_metrics(key, data) -> dict    # metrics [{metric, role, guardrail_op?, guardrail_value?}], row_version?
add_measurements(key, data, source='manual') -> dict
    # rows [{metric, variant?, value, count?, denominator?, sum_sq?, observed_at?, dims?, run_id?}]
    # <= settings.max_rows_per_request, all-or-nothing; -> {"batch_id","inserted"}
import_csv(key, content: bytes, filename: str) -> dict   # -> {"batch_id","inserted","ignored_columns"}
list_batches(key, after=None, limit=50) -> dict   # {"items": [{"batch_id","source","source_label","rows",
                                                  #  "metric_keys","filename","created_at","created_by"}], "next_after"}
delete_batch(key, batch_id) -> dict               # {"deleted": rows}
add_run(key, data, source='manual') -> dict
    # name?, variant?, status (finished|failed|cancelled, default finished), params? (<= 16 KB),
    # environment? (<= 16 KB), commit?, started_at?, ended_at?, metrics? {key: number (mean-like
    # kinds only) | {value,count,denominator,sum_sq}}, rows? [measurement rows without run_id;
    # variant defaults to the run's], note?; one transaction, one batch; queues pipeline
    # -> run {"id","name","variant","status","status_label","params","environment","commit","source",
    #         "started_at","ended_at","created_at","metrics": {key: estimate}}
list_runs(key, after=None, limit=50) -> dict      # {"items": [run], "next_after"}
add_note(key, data) -> dict                       # body (1..50000), kind?, variant?, run_id?
delete_note(key, note_id) -> dict                 # author or manager
run_evaluation(key, data, trigger='manual') -> dict   # evaluator (key), metric (key), params?, scope?
    # params validated against the evaluator's params schema; builtin -> computed now (done);
    # custom -> runner None: Unavailable("Die Rechenumgebung ist nicht eingerichtet."),
    # runner.health() not ok: Unavailable("Die Rechenumgebung ist nicht erreichbar."), else
    # status queued + 'evaluate' job (dedupe f"evaluate:{evaluation_id}")
run_pipeline(key, data=None, trigger='manual') -> list[dict]   # the type's evaluation list (optional
    # scope override): describe first; builtins now; custom queued (skipped with a warning
    # entry when the runner is missing). Reuse: the newest evaluation of the group (evaluator,
    # metric, params, scope), when it is not failed and has the same input_digest and evaluator
    # version, is returned instead of a new one; an older one of the group is `superseded` (a
    # newer one exists in any status) and never reused. trigger 'api' also prunes (as the job)
get_evaluation(key, evaluation_id) -> dict        # includes logs
decide(key, data) -> dict        # verdict, rationale?, learning? (required if the type says so),
    # row_version?; allowed only when the current state has a transition to the 'decided'-phase
    # state (else ValidationError("Eine Entscheidung ist in diesem Status nicht vorgesehen.")),
    # checks that transition's roles/requires with the new decision counted, inserts the
    # decision and performs the transition in one transaction; types without a decided phase
    # only record. -> snapshot
delete_experiment(key) -> dict   # [manage] one transaction: enqueue 'unindex' {"pointer",
    # "experiment_id"} (delay 330 s, priority 10) when index_state <> 'off' or indexed_at is set,
    # DELETE pending 'index:<id>'/'pipeline:<id>' jobs, DELETE the experiment -> {"deleted": KEY}
reindex(key) -> dict             # enqueue with priority 10 and no delay (ON CONFLICT pulls an existing
                                 # pending job forward) -> {"queued": true}; with indexing off
                                 # records 'off' -> {"queued": false}
activity(key, limit=50) -> list[dict]   # audit_log target experiment KEY: {"at","action","label" (German),
                                        # "actor" (display name, "Gelöschtes Konto" if gone), "detail"}
get_timeseries(key, metric_key, bucket='week', scope=None) -> list[dict]
search(q, limit=30) -> dict
    # Knovas (knovas_search(q, min(200, limit*5)), keep hits whose pointer
    # search.parse_pointer(settings.pointer_prefix, ...) accepts) merged by KEY with a database
    # search (ILIKE on key, title, hypothesis, description, decisions' learning/rationale, notes'
    # body) -> {"source": "knovas"|"database"|"knovas+database", "warning": str|None,
    # "items": [summary + "snippet"]}. Database only when knovas_search is None, raises,
    # indexing is off, or the actor lacks a configured access group (warning "Ihnen fehlt die
    # Knovas-Zugriffsgruppe für Experimente; gezeigt werden Datenbanktreffer.")
# tokens (own tokens only)
list_tokens() -> list[dict]      # {"id","name","hint","created_at","last_used_at","expires_at","revoked"}
create_token(data) -> dict       # name, expires_days (default 90, 1..365) -> + "token" ("kxp_" +
                                 # secrets.token_urlsafe(32), shown once); hint "kxp_…" + last 4
revoke_token(token_id) -> dict   # WHERE id AND user_id = actor, else NotFound
# preferences and operations
get_preferences() -> dict        # {"show_in_search": bool}  (the actor's own)
update_preferences(data) -> dict # {"show_in_search": bool}
get_settings() -> dict           # {"show_in_search": bool}  (global)
update_settings(data) -> dict    # [manage] exactly {"show_in_search": bool}, else ValidationError
index_status() -> dict           # [manage] {"enabled","unrestricted","access_groups","counts": {state: n},
    # "orphans": documents of deleted experiments still recorded (tasks.orphan_count),
    # "jobs": {status: n}, "failures": [{"kind","error","at"}], "access_warnings":
    # [{"user","missing_groups"}], "runner": runner.health() or {"configured": false}}
reindex_all() -> dict            # [manage] priority 200 -> {"queued": n}
list_packs() -> list[dict]       # available_packs() + "installed"
install_pack(name) -> dict       # [manage] idempotent -> {"domain","types","metrics","evaluators"} counts
import_pack(data) -> dict        # [manage] {"text"}; refuses "builtin." evaluator keys
```

Module-level functions for the worker (part D calls them):

```python
def execute_evaluation(conn, evaluation_id: str, *, settings, runner) -> None
    # missing evaluation -> return. runner None -> mark failed "Die Rechenumgebung ist nicht
    # eingerichtet.". Builds the input (store.aggregates / store.evaluator_rows with the scope,
    # <= settings.evaluator_max_rows) in one short transaction, then calls the runner outside any
    # transaction, then writes in a new one. runner raises Unavailable: if its first attempt
    # started more than 30 min ago (started_at is kept across retries; time in the queue does not
    # count) mark failed "Die Rechenumgebung war 30 Minuten nicht erreichbar.", else raise
    # jobs.RetryLater(60). Output through sanitize_output; queues index.
def run_pipeline_job(conn, experiment_id: str, *, settings, runner) -> None
    # run_pipeline without actor (trigger 'pipeline', audit actor None); missing experiment -> return;
    # keeps the newest 10 done automatic evaluations (trigger 'pipeline' or 'api') per (evaluator,
    # metric, params, scope), deletes older; 'manual' ones are all kept
def on_evaluation_dead(conn, evaluation_id: str) -> None   # mark failed "Die Auswertung konnte nicht ausgeführt werden."
```

## 9. Experiment snapshot

```json
{
  "id": "uuid", "key": "MKT-1", "title": "...", "hypothesis": "...", "description": "...",
  "status": "running", "status_label": "Läuft", "status_phase": "running", "archived": false,
  "domain": {"id": "...", "key": "marketing", "name": "Marketing", "color": "#eb6834", "id_prefix": "MKT"},
  "type": {"id": "...", "key": "ab_test", "name": "A/B-Test", "version": 1},
  "fields": [{"key": "channel", "label": "Kanal", "type": "enum", "value": "LinkedIn", "display": "LinkedIn"}],
  "field_values": {"channel": "LinkedIn"},
  "tags": [],
  "owner": {"id": "...", "display_name": "..."},
  "variants": [{"id","key","name","description","is_control","allocation","position","has_data"}],
  "metrics": [{"id","key","name","kind","kind_label","unit","direction","direction_label","role",
               "role_label","guardrail_op","guardrail_value","definition","guardrail_status",
               "aggregates": [...]}],        // guardrail_status "ok" | "violated" | null
  "evaluations": [{"id","evaluator_key","evaluator_name","language","evaluator_version","metric_key",
                   "params","scope","trigger","status","status_label","verdict","headline",
                   "output","error","created_at","finished_at","duration_ms",
                   "requested_by": {"display_name"}|null,"superseded"}],
      // newest first, <= 60; "output" only for the newest 20 (else null); never "logs";
      // superseded: a newer evaluation of the same (evaluator, metric, params, scope) exists
  "decisions": [{"id","verdict","verdict_label","rationale","learning",
                 "decided_by": {"id","display_name"}|null,"decided_at"}],     // newest first
  "notes": [{"id","kind","kind_label","body","variant","run_id",
             "created_by": {"id","display_name"}|null,"created_at","can_delete"}],   // newest first, <= 200
  "runs": [/* run dicts, newest first, <= 100 */],
  "runs_next_after": null,   // cursor for list_runs after the last shown run, null when all are shown
  "run_count": 0, "measurement_count": 0, "batch_count": 0,
  "created_at","updated_at","started_at","ended_at","decided_at",
  "row_version": 3,
  "index": {"state": "pending", "state_label": "ausstehend", "indexed_at": null, "error": null}
}
```

## 10. HTTP API (part E routes, part F consumes)

JSON. Success `{"success": true, <key>: <value>}`; failure `{"success": false, "error":
"<German>", "fields": {...}?}` with the error's status (400/403/404/409/413/503); unexpected
exceptions 500 `"Interner Serverfehler"` (logged with exc_info). Session routes: signed-in user
(global gate), X-CSRF-Token on non-GET (global gate), viewing role (else 404 page / JSON
"Nicht gefunden."). Route parameters are never named `doc_id`; bodies never use the key
`access_groups`. `app.config['MAX_CONTENT_LENGTH'] = 32 MB`; the CSV route also refuses
`request.content_length > 20 MB` with 413 "Die Datei ist grösser als 20 MB.", and runs at most
one import per process (`CSV_IMPORTS_PER_PROCESS`): another one meanwhile gets 503 "Es läuft
gerade schon ein CSV-Import. Bitte in einem Moment noch einmal versuchen." with `Retry-After: 10`
before its upload is read. A rolled-back transaction, serialization failure or deadlock (SQLSTATE
40000, 40001 or 40P01: psycopg's TransactionRollback, SerializationFailure, DeadlockDetected,
matched by sqlstate because psycopg 3 has no common base class) is a 409 "Gleichzeitige Änderung;
bitte erneut versuchen." (nothing was written); 40002 and 40003 (commit outcome unknown) stay 500. A JSON body nested beyond the parser's recursion limit is a 400 "Die Anfrage ist
kein gültiges JSON." on every route of the app, not a 500. A wrong method on a module route
answers like the module: 404 "Nicht gefunden." (JSON on `/api/...`, the 404 page otherwise) for
callers without a viewing role, a JSON 405 "Diese Methode ist hier nicht erlaubt." with `Allow`
on `/api/experiments...` for viewers, Flask's own 405 on pages; the rest of the app is
unchanged.

Pages (blueprint `experiments`): `GET /experiments` (`experiments.list_page`,
`experiments_list.html`), `GET /experiments/verwaltung` (`experiments.manage_page`,
`experiments_manage.html`, any viewer; manager tabs hidden and refused server-side),
`GET /experiments/<key>` (`experiments.detail_page`, `experiments_detail.html`; 404 unless
`^[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}$` and the experiment exists). Context: `active_nav='experiments'`,
`**page_context()`, `app_title`, `brand`, `csrf_token`, `asset_version`, `experiments_can_manage`,
`experiment_key` (detail; templates emit it with `|tojson`), `pointer_prefix`.

| Method, path (prefix `/api/experiments`) | Service call | Response key |
|---|---|---|
| GET `?domain=&status=&q=&tag=&archived=0&after=&limit=` | list_experiments | `result` |
| POST `` | create_experiment | `experiment` (201) |
| GET `/meta` | (see below) | `meta` |
| GET `/search?q=&limit=` | search | `result` |
| GET `/sample-size?kind=&base=&sd=&mde=&alpha=&power=&comparisons=` | sample_size | `result` |
| GET/PUT `/preferences` | get_preferences / update_preferences | `preferences` |
| GET/PUT `/settings` | get_settings / update_settings | `settings` |
| GET `/<key>` | get_experiment | `experiment` |
| PATCH `/<key>` | update_experiment | `experiment` |
| DELETE `/<key>` | delete_experiment | `result` |
| POST `/<key>/transition` | transition | `experiment` |
| PUT `/<key>/variants` | set_variants | `experiment` |
| PUT `/<key>/metrics` | set_metrics | `experiment` |
| POST `/<key>/measurements` | add_measurements | `result` (201) |
| POST `/<key>/measurements/csv` (multipart `file`) | import_csv | `result` (201) |
| GET `/<key>/batches?after=` | list_batches | `result` |
| DELETE `/<key>/batches/<batch_id>` | delete_batch | `result` |
| GET `/<key>/runs?after=` | list_runs | `result` |
| POST `/<key>/runs` | add_run | `run` (201) |
| POST `/<key>/notes` | add_note | `note` (201) |
| DELETE `/<key>/notes/<note_id>` | delete_note | `result` |
| POST `/<key>/evaluations` | run_evaluation | `evaluation` (201) |
| POST `/<key>/pipeline` | run_pipeline | `evaluations` |
| GET `/<key>/evaluations/<evaluation_id>` | get_evaluation | `evaluation` |
| GET `/<key>/metrics/<metric_key>/timeseries?bucket=week` | get_timeseries | `series` |
| POST `/<key>/decisions` | decide | `experiment` (201) |
| POST `/<key>/reindex` | reindex | `result` |
| GET `/<key>/activity` | activity | `activity` |
| GET/POST `/domains` | list_domains / create_domain | `domains` / `domain` |
| PATCH `/domains/<domain_key>` | update_domain | `domain` |
| GET `/domains/<domain_key>/export` | export_domain | `text` |
| GET/POST `/types?domain=&archived=` | list_types / create_type | `types` / `type` |
| POST `/types/validate` | validate_type | `definition` |
| GET `/types/<type_id>` | get_type | `type` |
| POST `/types/<type_id>/versions` | add_type_version | `type` |
| POST `/types/<type_id>/archive` (`{"archived": bool}`) | set_type_archived | `type` |
| GET/POST `/metrics?domain=&archived=` | list_metrics / create_metric | `metrics` / `metric` |
| PATCH `/metrics/<metric_id>` | update_metric | `metric` |
| GET/POST `/evaluators` | list_evaluators / create_evaluator | `evaluators` / `evaluator` |
| GET `/evaluators/<evaluator_id>` | get_evaluator | `evaluator` |
| POST `/evaluators/<evaluator_id>/versions` | add_evaluator_version | `evaluator` |
| POST `/evaluators/<evaluator_id>/test` | test_evaluator | `result` |
| GET/POST `/tokens` | list_tokens / create_token | `tokens` / `token` |
| DELETE `/tokens/<token_id>` | revoke_token | `token` |
| GET `/index` | index_status | `index` |
| POST `/index/reindex` | reindex_all | `result` |
| GET `/packs` | list_packs | `packs` |
| POST `/packs/<name>/install` | install_pack | `result` |
| POST `/packs/import` (`{"text"}`) | import_pack | `result` |

Static paths (`meta`, `search`, `domains`, `types`, `metrics`, `evaluators`, `tokens`, `index`,
`packs`, `settings`, `preferences`, `sample-size`) are registered before `/<key>`; `<key>`
routes 404 for anything not matching the key pattern.

`meta` = `{"me": {"id","display_name"}, "can_manage": bool, "kinds": [KindSpec as dict],
"field_types": [...], "decision_verdicts": {...}, "evaluation_verdicts": {...},
"note_kinds": {...}, "directions": {...}, "metric_roles": {...}, "run_statuses": {...},
"evaluation_statuses": {...}, "index_states": {...}, "runner": {"configured": bool, "ok": bool,
"languages": {...}}, "index_enabled": bool, "pointer_prefix": str, "max_csv_rows": int,
"max_rows_per_request": int}` (runner health cached 30 s per process).

Machine API (blueprint `experiments_api`, prefix `/api/experiments/v1`, bearer tokens only):

| Method, path | Service call | Response key |
|---|---|---|
| GET `/ping` | - | `user` (`{"display_name","roles"}`) |
| POST `/experiments` | create_experiment | `experiment` (201; trimmed) |
| GET `/experiments/<key>` | get_experiment trimmed to key, title, status, domain, type, variants, metrics (no aggregates), row_version | `experiment` |
| POST `/experiments/<key>/runs` | add_run(source='api') | `run` (201) |
| POST `/experiments/<key>/measurements` | add_measurements(source='api') | `result` (201) |
| POST `/experiments/<key>/notes` | add_note | `note` (201) |
| POST `/experiments/<key>/pipeline` | run_pipeline(trigger='api') | `evaluations` |
| GET `/experiments/<key>/evaluations?metric=&limit=20` | snapshot evaluations (no logs), filtered | `evaluations` |

After the token check, any other path or method under `/api/experiments/v1` -- the root `/v1`
and `/v1/` included, which would otherwise route to the session API's `/<key>` -- is a JSON 404
"Nicht gefunden." (a CI job with a typo is never told to sign in).

Bearer rules (part E): header `Authorization: Bearer kxp_...` only (the session cookie is never
read); `store.resolve_api_token` -> `UserRepository(conn).get(user_id)`; admitted only if
`user.is_active and not user.is_locked and not user.must_change_password and can_view(user)`;
otherwise 401 `{"success": false, "error": "Ungültiger oder abgelaufener Zugangsschlüssel."}`.
The service gets `request_meta={"ip","user_agent","token_id"}`. These endpoints are exempt from
the login gate (`IdentityGate.bearer_endpoints`) and from the CSRF header gate (endpoint prefix
`experiments_api.`) because they accept nothing but a bearer token.

## 11. Knovas documents (part D)

- Pointer: `f"{prefix}/{domain_key}/{KEY}"`. `search.parse_pointer(prefix, pointer)` -> KEY or
  None (tolerates a leading `/`; the KEY must match the key pattern).
- `title`: `f"{KEY} · {title}"` (<= 500). `description`: hypothesis (<= 2000). `path`:
  `"/Experimente/" + "/".join(seg(domain name), seg(type name), seg(f"{KEY} {title}"))` where
  `seg` replaces `/` and `\` with `-` and collapses whitespace (<= 2000).
- Body `indexer.render_markdown(snapshot)`:

```
# MKT-1 · <title>

Experiment im Bereich <domain> · Typ <type> · Status <status_label> · aktualisiert <TT.MM.JJJJ>
[· Schlagwörter: a, b]

## Hypothese
## Beschreibung
## Angaben            - <label>: <display>
## Varianten          - <key> (Kontrolle): <name> – <description>
## Metriken           - <name> (<kind_label>, <direction_label>, <role_label>[ <= / >= value]):
                        <variant>: <format_value(estimate)> (n = <n>); ...  [Leitplanke verletzt]
## Auswertungen       ### <evaluator_name> – <metric name> – <TT.MM.JJJJ>
                      <headline> (<verdict label>) \n <summary>
                      (only the newest done evaluation per evaluator, metric, scope)
## Läufe              - <name> (<variant>, <TT.MM.JJJJ>): <metric>: <value>; ...  (<= 50)
## Notizen            ### <kind_label> – <TT.MM.JJJJ> \n <body>
## Entscheidungen     ### <verdict_label> – <TT.MM.JJJJ> \n Begründung: … \n Erkenntnis: …
```

  Empty sections omitted. No personal names or e-mail addresses. Parts: split at `## `
  boundaries into chunks <= 40000 chars (long sections at paragraph, then hard, boundaries).
  Total cap 400000: drop older evaluations first, then runs, then notes (oldest first), then
  hard cut with the line "Gekürzt."
- Upload (`indexer.index_experiment`): snapshot missing -> return (done). Indexing disabled
  -> `index_state 'off'`, return. No access groups and not unrestricted -> `index_state
  'error'` "Für Experimente ist keine Knovas-Zugriffsgruppe festgelegt
  (EXPERIMENTS_ACCESS_GROUPS).", return (job done). Rate slot (§12) else `RetryLater`. Read the
  snapshot and remember its `updated_at` (short transaction), upload outside any transaction via
  `client.upload_text_document(identifier, title=, description=, path=, parts=[{"snippet": ...}],
  access_groups=<tuple or None>)`, then: `store.record_index_document(pointer, id)`; if the
  experiment no longer exists, `client.delete_information_object(pointer)` and
  `forget_index_document`; else `set_index_state('indexed', if_updated_at=<remembered>)`.
- `KnovasAPIClient.upload_text_document(identifier, *, title, description, path, parts,
  access_groups=None) -> dict` (new, part D): `_request_no_retry` init with identifier,
  part_count, title, description, path (normalised with `_normalize_semantix_path_for_init`),
  `access_groups` only when non-empty; then each part via `_secured_transmit_part_payload`.
  `experiments.indexer.make_index_client(config)` returns `KnovasAPIClient(config)` (no broker).
- Unindex (`indexer.unindex_pointer(conn, client, pointer)`): client None -> `RetryLater(3600)`;
  404 = done; then `forget_index_document`.
- Error mapping (handlers; exception text only to the log): connection error/timeout, HTTP
  5xx other than 503 -> retry with backoff and message "Knovas nicht erreichbar."; HTTP 429/503
  -> `RetryLater(Retry-After or 60)`; HTTP 401/403/408/409/425 -> retry ("Knovas hat die Anfrage
  vorübergehend nicht angenommen (HTTP <code>)."; 401/403 come from the credentials, e.g. a
  certificate being renewed, not from the document); other 4xx -> dead,
  `index_state 'error'` "Knovas hat das Dokument abgelehnt (HTTP <code>)."

## 12. Part D module APIs

`src/experiments/jobs.py`:

```python
class RetryLater(Exception):
    def __init__(self, delay_seconds: float, reason: str = ""): ...
class PermanentError(Exception): ...
@dataclass
class Job: id: int; kind: str; payload: dict; attempts: int; max_attempts: int; locked_by: str; created_at: datetime
class JobQueue:
    def __init__(self, conn): ...
    def enqueue(self, kind, payload, *, dedupe_key=None, delay_seconds=0, priority=100,
                max_attempts=8) -> int | None
        # INSERT ... ON CONFLICT (dedupe_key) WHERE status = 'pending' DO UPDATE SET
        #   run_after = LEAST(exp_jobs.run_after, EXCLUDED.run_after),
        #   priority = LEAST(exp_jobs.priority, EXCLUDED.priority) RETURNING id
    def claim(self, worker_id: str, *, kinds: tuple[str, ...], lease_seconds: int) -> Job | None
        # UPDATE ... SET status='running', attempts=attempts+1, locked_by, locked_until=
        # clock_timestamp()+lease WHERE id = (SELECT id FROM exp_jobs WHERE kind = ANY(kinds) AND
        # ((status='pending' AND run_after <= clock_timestamp()) OR (status='running' AND
        # locked_until < clock_timestamp() AND attempts < max_attempts)) AND (kind <> 'index' OR
        # (SELECT next_at FROM exp_rate_slots WHERE name='knovas_init') <= clock_timestamp())
        # ORDER BY priority, run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING ...
    def complete(self, job) -> bool
    def retry(self, job, error: str) -> bool     # backoff min(3600, 30 * 2**(attempts-1)); dead at max
    def defer(self, job, delay_seconds, reason="") -> bool   # attempts - 1; dead with "Zu lange
                                                            # zurückgestellt." when job older than 24 h
    def fail(self, job, error: str) -> bool      # dead
    def release(self, job) -> bool               # shutdown hand-back: pending now, attempt not
                                                 # counted, no 24 h defer cap
        # All five: fenced with WHERE id AND status='running' AND locked_by AND attempts; False =
        # lease lost (log, do nothing). Putting a job back to pending when another pending job
        # with the same dedupe_key exists closes this one as done ("superseded") instead; a
        # UniqueViolation race takes the same path (savepoint).
    def sweep_expired(self) -> list[Job]         # running, lease expired, attempts >= max -> dead; returned
    def counts(self) -> dict
    def recent_failures(self, limit=20) -> list[dict]   # {"kind","error","at"} (German messages only)
    def purge_finished(self, older_than_days=7) -> int
def take_rate_slot(conn, name: str, per_minute: int) -> float
    # UPDATE exp_rate_slots SET next_at = GREATEST(next_at, clock_timestamp()) +
    # make_interval(secs => 60.0/per_minute) WHERE name=%s AND next_at <= clock_timestamp()
    # RETURNING 0.0; else seconds until next_at
class JobWorker(threading.Thread):
    def __init__(self, *, connect, handlers: dict, on_dead: dict, kinds: tuple[str, ...],
                 lease_seconds: int, poll_seconds: float, worker_id: str | None = None,
                 maintenance: Callable[[conn], None] | None = None): ...
        # worker_id default f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
    def run(self): ...   # own connection (SET statement_timeout '120s',
                         # idle_in_transaction_session_timeout '60s'); loop claim -> handler(conn, job)
                         # -> complete / RetryLater -> defer / PermanentError -> fail / other
                         # exception -> retry; on dead -> on_dead[kind](conn, job); sleep with
                         # jitter when idle; reconnect with backoff; every ~10 min sweep_expired
                         # (+ on_dead), maintenance(conn), purge_finished; never dies
    def stop(self): ...
    def run_once(self, conn) -> bool
def start_workers_once(*, settings, connect, handlers, on_dead, maintenance) -> list[JobWorker]
    # at most once per process (guard keyed by os.getpid()): thread A kinds ('index','unindex',
    # 'pipeline') lease 600 s; thread B kinds ('evaluate',) lease runner_timeout_seconds + 120;
    # registers an atexit hook (_shutdown_at_exit, only in the pid that started the threads, so
    # not in a child forked by gunicorn --preload) that calls shutdown_workers()
def stop_workers(workers, *, timeout=5.0) -> int
    # stop, join up to `timeout` in all, then JobWorker.release_unfinished() (JobQueue.release
    # over a connection of its own) for every thread still busy; returns how many jobs were
    # handed back; never raises. cli `worker` uses it too.
def shutdown_workers(timeout=5.0) -> int      # stop_workers for this process's threads
```

`src/experiments/tasks.py`:

```python
def build_handlers(*, settings, index_client, runner) -> tuple[dict, dict, Callable]
    # handlers: 'index' -> indexer.index_experiment(conn, job.payload['experiment_id'], client, settings)
    #           'unindex' -> indexer.unindex_pointer(conn, client, job.payload['pointer'])
    #           'evaluate' -> service.execute_evaluation(conn, job.payload['evaluation_id'], ...)
    #           'pipeline' -> service.run_pipeline_job(conn, job.payload['experiment_id'], ...)
    # on_dead: 'index' -> skipped while a newer job with its dedupe key is active; else one guarded
    #          UPDATE (mark_index_dead): 'error' unless the experiment is 'indexed' with indexed_at
    #          >= the dead job's created_at (a later job uploaded it). Message "Knovas war nicht
    #          erreichbar." when the last error says unreachable/busy/temporary (401/403 included),
    #          else "Der Upload wurde nicht abgeschlossen (Details im Protokoll)." (unexpected
    #          error, expired leases, deferred too long);
    #          'evaluate' -> service.on_evaluation_dead(...); 'unindex', 'pipeline' log
    # maintenance(conn), in this order:
    #   1. whenever an index client exists (also with indexing off): an 'unindex' job (priority
    #      100, dedupe "unindex:<pointer>") for each orphan -- a pointer in exp_index_documents
    #      whose experiment is gone and that no pending/running job deletes; after a dead job
    #      only 1 h later, 24 h after a refusal ("Knovas hat das Löschen abgelehnt"); <= 500 a pass.
    #      orphan_count(conn) feeds `status`, index_status and doctor.sh;
    #   2. only with indexing on, a client and groups (or unrestricted): re-enqueue 'index'
    #      (priority 100) for 'pending' experiments and for 'error' ones whose index_error is in
    #      RETRYABLE_INDEX_ERRORS (unreachable, upload incomplete, no access group) or NULL, unless
    #      a job is active -- never a refusal by Knovas or a bad key;
    #   3. then the switched-off ones ('off' with index_error NULL, not INDEX_OFF_PURGED): set
    #      'pending' and enqueue at priority 200, <= 500 a pass, row before job slot
```

`src/experiments/indexer.py`: `make_index_client(config)`, `pointer_for(settings, domain_key,
key)`, `render_markdown(snapshot)`, `split_parts(markdown, max_chars=40000)`,
`document_for(snapshot, settings) -> {"identifier","title","description","path","parts",
"access_groups"}`, `index_experiment(conn, experiment_id, client, settings)`,
`unindex_pointer(conn, client, pointer)`, `purge_all(conn, client, settings, *, knovas_listing=True,
notes=None) -> int` (deletes every pointer in exp_index_documents, then every document Knovas
lists under the prefix via `client.iter_documents(prefix=...)` when available -- the unsigned
client sees only documents listed without an access group; a refused listing appends a German
note to `notes` and ends the listing pass, Knovas unreachable raises Unavailable
"Knovas konnte die Liste der Experiment-Dokumente nicht liefern; der Befehl kann wiederholt
werden."). cli `purge-index` first cancels pending index jobs, then -- whatever the outcome --
sets every experiment without a recorded document to 'off' with `store.INDEX_OFF_PURGED`, and
exits 1 after a failure, a remaining document or a note.

`src/experiments/runner_client.py`:

```python
class RunnerClient:
    def __init__(self, url: str, *, timeout_seconds: int = 90): ...
        # url "unix:///path/runner.sock" (HTTP over AF_UNIX via http.client) or "http://host:port"
    def health(self) -> dict   # {"configured": True, "ok": bool, "languages": {...}, "busy": n}; cached 30 s; never raises
    def run(self, *, language: str, code: str, data: dict, timeout_seconds: int) -> dict
        # {"ok","output","error","logs","duration_ms"}; HTTP read timeout = timeout + 30 s.
        # Unavailable("Die Rechenumgebung ist nicht erreichbar.") on connect errors and 503;
        # a read timeout after acceptance -> {"ok": False, "error": "Zeitlimit überschritten.", ...}
```

`src/experiments/search.py`:

```python
def parse_pointer(prefix: str, pointer: str) -> str | None
class SearchIntegration:
    def __init__(self, *, settings, connection, current_user, enabled: bool): ...
        # connection(): the request's DB connection; current_user(): User | None
    def split(self, results: dict) -> tuple[dict, list[dict]]
        # copy of results without experiment hits in "results"; results["semantix"] copied with
        # "pointers" filtered the same way (str items or dicts with pointer/identifier/doc_id)
        # and "result_count" reduced; "total" recomputed. Works when the module is disabled.
    def rows(self, hits: list[dict]) -> list[dict]
        # [] unless enabled, a user with a viewing role, global show_in_search and the user's
        # preference; else per hit whose KEY exists: hit's doc_id, path, score, final_score,
        # cosine_* kept, plus "result_kind": "experiment", "title": "KEY · title",
        # "app_url": "/experiments/KEY", "experiment": {"key","domain_key","domain_name",
        # "domain_color","status_label","type_name"}, "context_snippet" and "snippet" (the same
        # text: first text of hit.top_chunks (str or dict text/snippet/content) else the
        # hypothesis, <= 300 chars), "file_exists": False, "can_open": False
```

`src/experiments/cli.py` (`python -m experiments <command>` in the docbridge-web container):
`status`, `reindex [KEY ...|--all]`, `purge-index [--yes]` (works while the module is off),
`install-pack NAME`, `worker --once`. Loads config via `config_loader.get_config()` and connects
with `PLATFORM_DB_DSN` or `identity.db.connect()`.

## 13. app.py integration (part E)

1. `app.config['MAX_CONTENT_LENGTH'] = config.get_int('web.max_request_bytes', 32*1024*1024)`.
2. After the identity block: `experiments_settings = load_settings(config, identity_enabled=...)`
   (log a warning when EXPERIMENTS_ENABLED is on but identity is off).
3. `@app.before_request refuse_experiments_when_switched_off`, **registered before
   `require_company_login`**: when disabled, `/experiments` and below redirect to `index`,
   `/api/experiments` and below answer 404 `{"success": false, "error": "Experimente sind nicht
   eingeschaltet."}`. When enabled it does nothing.
4. `require_readable_document`: a `doc_id` starting with `<prefix>/` -> 404 (experiment
   documents are never served as files).
5. `_sidebar_context()` gains `'experiments_nav': ...` (enabled, identity on, can_view(current user)).
6. When enabled (inside `if identity_gate is not None:`): `store.ensure_builtin_evaluators` and
   `ensure_core_pack` once at startup (boot connection, under the identity advisory lock or
   idempotently); register blueprints `experiments` and `experiments_api`;
   `identity_gate.allow_bearer_endpoints(...)`; index client from
   `experiments.indexer.make_index_client(config)` (always when enabled; `index_enabled` gates
   uploads, not deletions); `RunnerClient(settings.runner_url, ...)` if `runner_url`; workers via
   `jobs.start_workers_once(...)` if `worker_enabled`, with `connect` bound at construction to
   `PLATFORM_DB_DSN` (if set) else `identity.db.connect`. Everything in
   `app.extensions['experiments'] = {"settings", "search", "index_client", "runner", "workers"}`.
7. `/api/search`: right after results are obtained (Knovas and test fixtures alike):
   `results, experiment_hits = experiments_search.split(results)` (always, also when disabled).
   So that the hits `split` takes out do not leave the page short, Knovas is asked for
   `max(limit, min(200, limit + min(limit, 20)))` hits (`_search_fetch_size`) and, when experiment
   hits came back, the page is still short and Knovas filled the question, once more for
   `min(200, 2 * fetch)` (a failed second question keeps the first answer); the document hits are
   cut back to `limit`, and the answer's `has_more` says whether Knovas may hold more (it filled
   the question, documents were cut, or experiment rows displaced some);
   after the grants loop and the OneDrive loop, just before `literal_hits`, merge
   `rows = experiments_search.rows(experiment_hits)` passed through
   `_apply_search_refinement({'results': rows}, query, filters, config)['results']` into
   `final_results`, re-sorted by score descending, truncated to `limit`. Experiment rows are
   never granted and never get open hints.
8. `_prevent_stale_ui_assets`: `/experiments` and below no-store.
9. CSRF gate: endpoints starting with `experiments_api.` exempt.
10. `webauth.IdentityGate`: attribute `bearer_endpoints` (frozenset, default empty), method
    `allow_bearer_endpoints(names)`, `guard()` returns None for them.
11. `admin.ASSIGNABLE_ROLES` gains `experimenter`, `experiments_manager`.
12. `identity.webauth.client_ip()` is the address recorded for a session (`sessions.ip`) and in
    the module's audit rows (`request_meta["ip"]`): the X-Forwarded-For entry
    `PLATFORM_TRUSTED_PROXY_HOPS` places from the right (read per request; default 1, negative
    = 0), the connection's address when the header has fewer entries or the entry is not an
    address; never the leftmost entry, which the client writes. Root `docker-compose.yml` sets
    `${PLATFORM_TRUSTED_PROXY_HOPS:-2}` for docbridge-web: host nginx and docbridge-web-nginx
    both append (`$proxy_add_x_forwarded_for`); 1 when docbridge-web-nginx is published directly,
    0 without a proxy (knovas.env.example, docs/deployment/host-nginx-internal.md).

## 14. UI (part F)

Look: existing design tokens (style.css `:root`), IBM Plex, `--radius-lg` cards; each domain's
colour only as a small dot. German copy. Vanilla JS, no build, no libraries; charts inline SVG.
Pages include `_sidebar.html` (`active_nav='experiments'`), `<meta name="csrf-token">`,
`markdown.js`, `experiments_common.js` and the page script with `?v={{ asset_version }}`.

Sidebar: after the Cortex item, `{% if experiments_nav is defined and experiments_nav %}` an
item "Experimente" (flask icon path `M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0
1.5-2.5L14 9V3M7 15h10`), active when `active_nav == 'experiments'`; update the header comment.

Security rules: every server string (names, labels, units, headlines, table cells, warnings,
logs, messages, notes) goes in via `textContent` or `KX.esc`; Markdown only through
`window.KnovasMarkdown.render` (fallback `<pre>` + escaped text); links only from `app_url`
values starting with `/experiments/`.

`experiments_common.js` (`window.KX`): `csrfToken()`, `api(method, url, body)` (JSON,
X-CSRF-Token, 401 -> `/login`, throws Error with `.status`, `.fields`), `upload(url, file)`
(FormData + X-CSRF-Token; 413 message), `esc`, `el(tag, attrs, ...children)`, `fmtDate(iso)`
(TT.MM.JJJJ), `fmtDateTime`, `fmtNumber(x, d)` and `fmtEstimate(kind, x, unit, d)` and
`fmtDiff(kind, x, unit, d)` with **the same rules as kinds.format_***: decimal comma, ASCII
`'` thousands, percent with a space, proportion differences in "Pp." (x*100, signed) — do not use
Intl de-CH; `toast`, `dialog({title, body, actions})` (native `<dialog>`), `verdictChip`,
`statusChip`, `domainDot(color)` (validate `#RRGGBB`), `intervalBar(estimate, lo, hi, {direction,
unit, kind})` (SVG with zero line), `lineChart(series, {kind, unit})` (SVG, one line per variant,
hover tooltips via `<title>`), `renderMarkdown(md)`, `renderFieldInput(field, value)` /
`readFieldInput(field, el)`, `meta()` (cached GET `/api/experiments/meta`).

`/experiments` (list): h1 "Experimente", subtitle "Hypothesen, Messwerte und Entscheidungen –
über alle Bereiche."; buttons "Neues Experiment" (primary), "Verwaltung". Search box "In Knovas
suchen …" -> `/search`; result note "Gefunden mit Knovas" / "Knovas und Datenbank" /
"Datenbanksuche" (+ warning text). Domain chips (all + each with dot and count), status select,
tag filter, "Archivierte zeigen", text filter (debounced). Table: Schlüssel, Experiment (title +
type), Bereich, Status, Primäre Metrik, Letztes Ergebnis (headline + verdict chip +
"Leitplanke verletzt" chip), Aktualisiert; "Mehr laden". Empty states: no domains -> "Noch
keine Bereiche." + managers "Pakete installieren" (engineering, marketing, sales, product) /
others "Bitten Sie eine verantwortliche Person, Bereiche einzurichten."; domain without
experiments -> "Noch keine Experimente in diesem Bereich." + "Neues Experiment". New-experiment
dialog: Bereich, Typ (domain + global), Titel, Hypothese, the type's fields; POST then navigate.

`/experiments/<KEY>` (detail) renders `GET /api/experiments/<KEY>`:
- Header: KEY, domain dot + name, type + version, status chip, title (inline edit), tags (chips,
  add/remove), archive toggle, "Neu indexieren", index state ("In Knovas: aktuell / ausstehend /
  Fehler: … / aus").
- Lifecycle strip: the type's states in order, current highlighted, `transitions` as buttons;
  disabled ones list their `missing` messages; `needs_comment` opens a dialog with required
  "Grund"; `decides` scrolls to/opens the decision form.
- Hypothese, Beschreibung (edit, markdown preview), type fields (form; 409 -> message + "Neu
  laden").
- Varianten (editable table: key, name, description, control radio, allocation; `has_data`
  rows cannot be removed) and Metriken (assign metric with role, guardrail op/value; small
  sample-size calculator "Stichprobe planen" until the experiment starts, via `/sample-size`,
  with `comparisons` = variants − 1 when there are more than two, showing `alpha_used`).
- Messwerte: per metric a card with aggregates per variant (n, estimate, interval if an
  evaluation has one), guardrail chip, week/day/month line chart (`/timeseries`); forms
  "Messwert erfassen" (labels from the kind; select of level labels when the metric has levels),
  "CSV importieren" (help showing long and wide format incl. a LinkedIn example
  `variant;observed_at;ctr;ctr.count;cost_per_click;cost_per_click.denominator`, the row limit
  `max_csv_rows` and 20 MB; shows `ignored_columns`), batches with "Rückgängig". Empty state:
  "Noch keine Messwerte. Erfassen Sie Werte von Hand, laden Sie eine CSV-Datei hoch oder senden
  Sie sie aus CI (Zugangsschlüssel unter Verwaltung)."
- Läufe: table (name, variant, status, commit, params, metrics); "Lauf erfassen" dialog.
- Auswertungen: "Auswertung starten" (evaluator select filtered by kind; python/julia disabled
  with hint "Python- und Julia-Auswerter brauchen die Rechenumgebung (Profil experiments)." when
  `meta.runner.ok` is false; metric select; scope: Alle Daten / Neuester Lauf je Variante /
  Zeitraum; params JSON), "Alle Auswertungen des Typs ausführen". Cards: evaluator, metric,
  scope, verdict chip, headline, comparisons with `intervalBar` and per-comparison chip,
  variants table (n, value, interval), table, warnings, summary markdown; "Protokoll" loads
  `get_evaluation` logs (custom only). queued/running cards poll every 3 s (stop after 10 min).
  Empty: "Noch keine Auswertung. «Alle Auswertungen des Typs ausführen» startet die vorgesehenen."
- Notizen (list, add with kind select, delete where `can_delete`).
- Entscheidung (shown when a `decides` transition exists or decisions exist): guardrail
  violations listed first, verdict select, Begründung, Erkenntnis (required per type); history.
- Aktivität.

`/experiments/verwaltung` tabs: "Bereiche" [manage] (list, create with note "Neue Bereiche
starten mit dem Typ «Allgemeine Hypothese» und den allgemeinen Metriken des Grundpakets …", edit, archive, export (download .yaml), import
(textarea), "Pakete"), "Typen" [manage] (per domain; YAML/JSON editor textarea, opened with the type's `definition_yaml`; "Prüfen" ->
`/types/validate` showing field errors; "Als neue Version speichern"; "Kopieren nach …"; version
list; read-only preview of fields/states/transitions), "Metriken" [manage] (create/edit incl.
levels, min, max, decimals), "Auswerter" [manage] (list incl. builtins; create/edit python/julia
with code textarea pre-filled from a template returning every contract key; Tab inserts 4
spaces; "Testen" against experiment + metric shows output + logs; runner status), "Zugangsschlüssel"
(every viewer: own tokens, create shows the token once with copy button and curl + Python SDK
example, revoke; checkbox "Experimente in meiner normalen Suche zeigen" -> `/preferences`),
"Index" [manage] (counts, `orphans` when > 0, jobs, failures, access warnings, "Alles neu indexieren", global
checkbox "Experimente in der normalen Suche zeigen" -> `/settings`).

`app.js`: in `_onResultsClick` and the results keydown handler, when
`this.currentResults[idx]?.result_kind === 'experiment'` and `app_url` starts with
`/experiments/`, `window.location.assign(app_url)` instead of `openPreview`; `stepPreview`
skips experiment rows; `createDocumentCard` renders experiment rows with a flask icon, metaline
"Experiment · <domain_name> · <status_label>", `doc.title` as is (not `displayTitle`), the
snippet escaped, no file badge. Keep class method names unique (test_frontend_static).

## 15. Runner (part G)

`KnovasPlatform/components/experiments_runner/`:

- `Dockerfile`: `FROM julia:1.11.9-bookworm`; apt `python3 python3-venv ca-certificates`; venv
  `/opt/venv` from `requirements.txt` (numpy, scipy, pandas, statsmodels, pinned); Julia
  packages into the depot's default environment at build: `RUN JULIA_DEPOT_PATH=/opt/julia-depot
  julia --startup-file=no -e 'using Pkg; Pkg.add(["JSON3","Distributions","HypothesisTests",
  "StatsBase","DataFrames"]); Pkg.precompile()'` (default optimisation level; the harness runs
  with default flags too), then `chmod -R a+rX /opt/julia-depot`; `groupadd --gid 10101 runner`,
  `useradd --uid 10101 --gid 10101 runner` (a uid no other image of the stack uses:
  RemoteController is 10001, and RLIMIT_NPROC counts per uid across the host);
  `mkdir -p /run/experiments-runner && chown 10101:10101 /run/experiments-runner`; `USER 10101`;
  `CMD ["/opt/venv/bin/python3", "-I", "/app/runner.py"]`. No `ENV JULIA_DEPOT_PATH` with a
  shared writable entry.
- `runner.py` (stdlib only): listens on `RUNNER_LISTEN` (default
  `unix:/run/experiments-runner/runner.sock`; `tcp:0.0.0.0:8090` for development), socket mode
  0660. Exits (so the container restarts) if the socket file disappears. With
  `RUNNER_SOCKET_DIR_EXCLUSIVE` true (default for `/run/experiments-runner`; compose sets it) the
  socket directory is the runner's alone and is emptied at start, so nothing a job planted there
  outlives the restart it causes. `GET /health` ->
  `{"ok": true, "languages": {"python": "...", "julia": "..."}, "busy": n, "max_concurrent": n}`.
  `POST /v1/run` `{"language", "code" (<= 200000), "data" (object), "timeout_seconds"}`
  (body <= 64 MB) -> 200 `{"ok","output","error","logs","duration_ms"}`; 400 bad request; 503
  when `RUNNER_MAX_CONCURRENT` (2) jobs run and no slot frees in 10 s, or tmpfs has < 256 MB free.
  Per job: fresh dir under `/tmp` (0700), `code`, `input.json` there; child
  `/opt/venv/bin/python3 -I harness.py` (no `-S`) or `julia --startup-file=no --history-file=no
  harness.jl`; `start_new_session=True`; env only PATH, HOME=<jobdir>, LANG=C.UTF-8,
  OPENBLAS_NUM_THREADS/MKL_NUM_THREADS/OMP_NUM_THREADS=1, JULIA_NUM_THREADS=1,
  JULIA_DEPOT_PATH=<jobdir>/depot:/opt/julia-depot:, JULIA_LOAD_PATH=@:@v#.#:@stdlib (packages
  resolve from the read-only depot's default environment); preexec rlimits CPU = timeout + 5 s,
  AS 1.5 GB for Python and 6 GB for Julia (which reserves address space; plus
  `--heap-size-hint=1G`), FSIZE 64 MB, NOFILE 256, NPROC 128 (the kernel counts it per uid on
  the host: all jobs, the server and a second stack's runner share it), CORE 0, and
  oom_score_adj 1000;
  wall-clock timeout -> SIGKILL the process group; after every job SIGKILL every process of the
  runner uid whose session id is neither the server's nor a running job's; stdout+stderr
  captured to <= 64 KB; `output.json` opened with `O_NOFOLLOW`, must be a regular file <= 8 MB;
  job dir removed. User code defines `evaluate(data)` returning a dict (Python: executed in a
  fresh namespace with `exec(compile(code, "evaluator.py", "exec"), ns)`; Julia:
  `include_string` into a fresh module, `data::Dict{String,Any}`). The Julia harness has its
  own small JSON reader/writer (no package needed for the protocol). Exceptions -> ok false,
  traceback in logs, error "Der Auswerter ist mit einem Fehler abgebrochen."
- `tests/` (pytest; Julia tests skip without `julia` on PATH, numpy tests skip without numpy):
  happy path, exception, timeout kill, output too large, symlinked output, non-dict return,
  double-fork survivor killed, health while busy, per-job Julia depot isolation.

`docker-compose.yml`: service `experiments-runner`, `profiles: [experiments]`, build
`./KnovasPlatform/components/experiments_runner`, image `knovas-experiments-runner:0.1.0`,
`network_mode: none`, `volumes: [experiments_runner_socket:/run/experiments-runner]`,
`user: "10101:10101"`, `read_only: true`, `tmpfs: ["/tmp:size=1g,mode=1777"]`, `cap_drop:
[ALL]`, `security_opt: ["no-new-privileges:true"]`, `pids_limit: 256`, `mem_limit:
${EXPERIMENTS_RUNNER_MEMORY:-3g}`, `cpus: ${EXPERIMENTS_RUNNER_CPUS:-2}` (at most the host's
CPUs; setup.sh writes 1 on a 1-CPU host when knovas.env has no value, doctor.sh checks a set
one), environment `RUNNER_MAX_CONCURRENT`, `RUNNER_SOCKET_DIR_EXCLUSIVE: "true"`,
`RUNNER_MAX_SECONDS: ${EXPERIMENTS_RUNNER_TIMEOUT:-90}`, healthcheck (python over the socket),
`restart: unless-stopped`. docbridge-web mounts `experiments_runner_socket:/run/experiments-runner:ro`
(always; harmless when the profile is off). New named volume `experiments_runner_socket`: a
tmpfs (`size=1m,nr_inodes=1024,uid=10101,gid=10101,mode=0770,nosuid,nodev,noexec`; a volume
created with another uid must be removed once). No `container_name`, no `${X:?}`, no plain
`depends_on` on the profiled service.

CI: runner pytest (Python 3.11); compose config with the fixture env still passes; `docker
compose --env-file scripts/lib/fixtures/knovas.env.fixture --profile experiments build
experiments-runner`; smoke `docker run --rm knovas-experiments-runner:0.1.0 /opt/venv/bin/python3
-I -c 'import numpy, scipy, pandas, statsmodels'` and `julia -e 'using JSON3, Distributions'`.

`scripts/doctor.sh` section "Experimente" (its own default-off flag helper): off -> ok and skip;
on -> identity requirement, tables (probe), job counts and dead jobs (WARN), index states (WARN
on error), empty EXPERIMENTS_ACCESS_GROUPS without EXPERIMENTS_INDEX_UNRESTRICTED (WARN
"Experimente werden nicht in Knovas indexiert: keine Zugriffsgruppe"), unrestricted without
groups (WARN "Experimente sind in Knovas für alle Nutzer des Mandanten sichtbar"), runner
reachability when EXPERIMENTS_RUNNER_URL is set.

`knovas.env.example`: commented block for EXPERIMENTS_ENABLED, EXPERIMENTS_ACCESS_GROUPS (with
the note that every experimenter needs these groups under Verwaltung → Personen),
EXPERIMENTS_INDEX_UNRESTRICTED, EXPERIMENTS_INDEX_PER_MINUTE, COMPOSE_PROFILES=experiments with
EXPERIMENTS_RUNNER_URL=unix:///run/experiments-runner/runner.sock, EXPERIMENTS_RUNNER_MEMORY/CPUS.
`docs/client/README.md`: rows in the settings table.

## 16. SDK and docs (part H)

- `KnovasPlatform/experiments-sdk/python/knovas_experiments.py` (stdlib only): `Client(base_url,
  token, *, cafile=None, verify=True, timeout=30)` (env fallbacks `KNOVAS_URL`,
  `KNOVAS_EXPERIMENTS_TOKEN`) with `ping()`, `experiment(key)`, `create_experiment(domain,
  type, title, hypothesis="", fields=None)`, `log_run(key, *, name=None, variant=None,
  params=None, metrics=None, rows=None, environment=None, commit=None, status="finished",
  started_at=None, ended_at=None, note=None)`, `add_measurements(key, rows)`, `add_note(key,
  body, kind="note")`, `evaluate(key, scope=None) -> list`, `evaluations(key, metric=None)`,
  and `run(key, variant=..., name=..., params=..., commit=...)` context manager with
  `log(**metrics)` and `add_rows(rows)` posting on exit (status failed on exception). A clear
  error when the server answers 404 "Experimente sind nicht eingeschaltet.". README with a GitHub
  Actions example that logs per-query rows (`dims: {"query": id}`) and fails the job on a
  `worse` verdict.
- `KnovasPlatform/experiments-sdk/julia/KnovasExperiments.jl` (HTTP.jl + JSON3): `log_run`,
  `add_measurements`, `add_note`, `evaluate`; README.
- `KnovasPlatform/docs/features/experiments.md` (German): what it is; switching on (env, roles,
  Knovas access group + granting it to experimenters, profile for Python/Julia); domains, types,
  metrics, evaluators; measurement kinds table with how to enter real cases (per-query ranking
  scores, weekly ad data wide CSV, costs as ratio, interviews as notes, SUS); CSV formats;
  evaluations and scope; decisions and learnings; search with Knovas; tokens and SDK;
  operations (index, doctor, CLI, backup of platform_db_data); runner security model (no
  network, unix socket, limits) and accepted risks; limits.
- `RELEASE_NOTES.md`: German `### Experimente` section under `## KnovasPlatform`.
- Design doc: replace open decisions with §0 of this plan; correct stack/search statements.

## 17. Tests (every part)

- Pure units: stats against scipy reference values (pinned numbers in the test; closed forms
  1e-6 relative, Monte Carlo 0.01 absolute), kinds (validation, formatting), schema, packs
  (every shipped pack validates; alias bomb and python tag refused), evaluators (verdicts incl.
  direction lower, multiple variants + Holm, guardrail, target, too little data, params
  refused, sanitize limits and moved keys), csv (long, wide, decimal comma, errors), indexer
  (markdown, parts, truncation, document_for, path segments, access-group refusal), search
  (split incl. semantix pointers, rows), runner client (fake unix and TCP servers).
- Postgres: store (key allocation under concurrency, aggregates per kind incl. scope latest,
  sum_sq rules, casts, batches, timeseries, tokens, settings whitelist), jobs (two connections +
  SKIP LOCKED, dedupe + supersede, fencing, priority, rate slot, lease expiry, sweep, defer cap),
  service (create -> measurements -> evaluation -> transition gates -> decide -> snapshot;
  permissions incl. 404 for non-viewers; CSV import; delete batch; pack install idempotent;
  export/import round trip; pipeline digest reuse; delete with unindex job).
- Web: switch off (nav hidden, page redirect, API 404, bearer 404, search strips hits and
  semantix pointers), role gate (member -> 404, experimenter ok, manager-only -> 403), CSRF 403,
  `access_groups` body 400, token API (401 without/invalid/revoked/expired token, cookie alone
  refused, locked or must-change user refused, run logging with rows, pipeline + evaluations),
  search integration with the Dummy client returning experiment pointers (viewer sees cards,
  member sees nothing, no grants for experiment pointers, preference off hides).
- Frontend: templates render under StrictUndefined with the admin-test base context plus the
  new keys; `_sidebar.html` without `experiments_nav` shows no item; JS files parse with
  `node --check` when node is available.
