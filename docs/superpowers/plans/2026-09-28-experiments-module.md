# Plan: Experiments module (Experimente) inside KnovasPlatform

Date: 2026-09-28
Design: [../specs/2026-09-28-experiment-platform-design.md](../specs/2026-09-28-experiment-platform-design.md)
Component: `KnovasPlatform/components/docbridge_integration` (the Platform web app) plus an optional
sandbox service `KnovasPlatform/components/experiments_runner`.

This document is the contract every part of the implementation codes against. Where it names a
function, a key, a table column, a JSON field or a German UI string, use exactly that name.

## 0. Requirements and resolved decisions

User requirements:

1. Lives inside KnovasComponents; hidden so the standard search view is not cluttered.
2. Can be disabled entirely.
3. Users can create new domains (not only the shipped four).
4. Robust and scalable technical choices, decided (not left open).
5. A version usable right away for Knovas' own experiments: search quality and engineering,
   LinkedIn/marketing tests, sales outreach, product/usability.
6. All texts, decisions and results searchable with Knovas.

Resolved decisions (they replace the open questions of the design doc):

| Question | Decision | Why |
|---|---|---|
| Where it runs | A module of the existing Platform web app (Flask, Jinja, vanilla JS), code name `experiments`, UI label **Experimente**. | Same login, roles, audit, deployment and look; no new stack to operate. |
| Hidden | Visible only to people with role `experimenter` or `experiments_manager` (or `admin`). Nobody else gets the nav item, the pages (404) or experiment hits in search. | The standard view stays exactly as it is for everyone else. |
| Disable | `EXPERIMENTS_ENABLED` (default **false**). Off = blueprints not registered, a before_request gate answers 404/redirect, no worker thread, experiment hits stripped from search. | Same pattern as `CORTEX_ENABLED`, but default off. |
| Storage | Existing `platform-db` PostgreSQL, migration `0003_experiments.sql`. Measurements stored as sufficient statistics (sum, count, sum of squares, denominator) so one row can be a single observation or a pre-aggregated block. | Postgres is already run, backed up and shared by all workers. Pre-aggregated rows keep volume small; the index plan and BRIN on time scale to 10^8 rows; declarative partitioning is the next step if ever needed. No TimescaleDB or ClickHouse to operate. |
| Search | Every experiment is one Knovas document (pointer `experiments/<domain>/<KEY>`), re-uploaded on change. Hits are recognised by the pointer prefix and rendered from the Platform database. | Knovas is the search engine; no pgvector. One document per experiment keeps well inside the tenant's init rate limit. |
| Background work | A Postgres job queue (`exp_jobs`, `FOR UPDATE SKIP LOCKED`, leases, retries with backoff, coalescing) polled by one daemon thread per gunicorn worker. A shared rate slot (`exp_rate_slots`) keeps Knovas uploads under `EXPERIMENTS_INDEX_PER_MINUTE` across all workers. | No new broker (Redis/Celery). Survives worker crashes and restarts. |
| Indexing identity | A second `KnovasAPIClient` without a principal broker uploads (as RemoteController does). Knovas-side visibility is set by `EXPERIMENTS_ACCESS_GROUPS` (sent as `access_groups` on init) or by a folder rule on the prefix; the Platform additionally strips hits for people without the role. | The request-bound client refuses to call Knovas without a signed-in user, and indexing happens in the background. |
| Python and Julia | Built-in statistics run in-process (pure Python, trusted code). User-written evaluators run only in `experiments-runner`: its own image (Python with numpy/scipy/pandas/statsmodels, Julia 1.11), non-root, read-only root FS, all capabilities dropped, no-new-privileges, CPU/memory/pid limits, per-job subprocess with rlimits and a wall-clock kill, on an `internal: true` network with no egress and no secrets mounted. Optional compose profile `experiments`. | docbridge-web runs as root and holds the mTLS key, broker key and DB password; user code must never run there. No docker socket needed. |
| Machine access (CI) | Personal access tokens (`exp_api_tokens`, SHA-256 hashed) on `/api/experiments/v1/*` with `Authorization: Bearer`. Python SDK (single file, stdlib only) and a Julia client. | Engineering experiments are logged from CI. |
| Configurability | Domains, experiment types (versioned JSON/YAML documents with fields, states, transitions with gates, variant rules, default metrics, evaluation pipeline), metrics, evaluators — all data, editable in the UI, exportable/importable as YAML packs (config-as-code in git). | "Ultra modular and configurable" without code changes. |

## 1. File ownership

Each part is owned by exactly one implementer. Do not edit files owned by another part; if a
contract is missing something, implement the smallest compatible addition in your own files and
report it.

| Part | Files (paths relative to `KnovasPlatform/components/docbridge_integration/` unless absolute) |
|---|---|
| FOUNDATION (done) | `src/identity/migrations/0003_experiments.sql`, `src/experiments/__init__.py`, `errors.py`, `permissions.py`, `settings.py` |
| A schema+packs | `src/experiments/kinds.py`, `src/experiments/schema.py`, `src/experiments/packs.py`, `src/experiments/packs/*.yaml`, `tests/test_experiments_schema.py`, `tests/test_experiments_packs.py` |
| B stats+evaluators | `src/experiments/stats.py`, `src/experiments/evaluators.py`, `tests/test_experiments_stats.py`, `tests/test_experiments_evaluators.py` |
| C store+service | `src/experiments/store.py`, `src/experiments/service.py`, `tests/test_experiments_store.py`, `tests/test_experiments_service.py` |
| D jobs+index+search | `src/experiments/jobs.py`, `src/experiments/indexer.py`, `src/experiments/runner_client.py`, `src/experiments/tasks.py`, `src/experiments/search.py`, `src/experiments/cli.py`, `src/experiments/__main__.py`, the method `upload_text_document` added to `src/knovas_client.py`, `tests/test_experiments_jobs.py`, `tests/test_experiments_indexer.py`, `tests/test_experiments_search.py` |
| E web | `src/web_interface/experiments_routes.py`, edits in `src/web_interface/app.py`, `src/identity/webauth.py` (bearer endpoints), `src/web_interface/admin.py` (ASSIGNABLE_ROLES), `config/config.yaml` (`experiments:` section), `tests/test_experiments_routes.py`, `tests/test_experiments_switch.py`, `tests/test_experiments_api_tokens.py` |
| F frontend | `src/web_interface/templates/experiments_list.html`, `experiments_detail.html`, `experiments_manage.html`, `_sidebar.html` (nav item), `src/web_interface/static/js/experiments_common.js`, `experiments_list.js`, `experiments_detail.js`, `experiments_manage.js`, `static/css/experiments.css`, edits in `static/js/app.js` (experiment hit cards) |
| G runner+deploy | `KnovasPlatform/components/experiments_runner/**`, `/docker-compose.yml`, `/.github/workflows/ci.yml`, `/scripts/doctor.sh`, `/knovas.env.example`, `/docs/client/README.md` (settings rows) |
| H docs+sdk | `KnovasPlatform/experiments-sdk/**`, `KnovasPlatform/docs/features/experiments.md`, `/RELEASE_NOTES.md`, `/docs/superpowers/specs/2026-09-28-experiment-platform-design.md` |

Conventions for all parts:

- Python 3.11, no new dependency in `requirements.txt` (psycopg 3, PyYAML, jsonschema, requests,
  Flask are available). Python source ASCII only (German in Python strings as `ä` escapes or
  transliterated `ae/oe/ue/ss` is fine; templates and JS use real umlauts).
- UI copy German (Swiss-style `ss` is used in places; use real umlauts in templates/JS, `ss`
  instead of `ß`). Identifiers, comments, commit messages English.
- Error messages returned to the browser are German, never contain exception text; use the
  classes in `experiments/errors.py`.
- psycopg rows are tuples; index by position or zip with a column tuple. `%s` placeholders.
  JSONB written as `json.dumps(...)` (or `psycopg.types.json.Jsonb`), read back as dict.
  Connections are autocommit; wrap multi-statement writes in `with conn.transaction():`.
- Timestamps in JSON are ISO 8601 strings with timezone (`dt.isoformat()`), ids are strings.
- Tests: `cd KnovasPlatform/components/docbridge_integration && <venv>/bin/pytest tests/<file>`.
  The venv with all requirements is
  `/tmp/claude-0/-home-user-KnovasComponents/8edc8a0c-cb19-5849-9edf-35f6fb3bc4c4/scratchpad/venv`.
  A PostgreSQL 16 is running locally; export
  `PLATFORM_DB_TEST_DSN=postgresql://platform:testpw@127.0.0.1:5432/knovas_platform_test`
  for DB tests. DB tests use the `platform_db` fixture from `tests/conftest.py` (a migrated
  per-test schema) and are marked
  `pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")`.

## 2. Configuration (`config/config.yaml`, part E)

Add a top-level section (values come from knovas.env via `.env.generated`):

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
  runner:
    url: "${EXPERIMENTS_RUNNER_URL:-http://experiments-runner:8090}"
    timeout_seconds: "${EXPERIMENTS_RUNNER_TIMEOUT:-120}"
  worker:
    enabled: "${EXPERIMENTS_WORKER_ENABLED:-true}"
    poll_seconds: "${EXPERIMENTS_WORKER_POLL_SECONDS:-5}"
```

`experiments.settings.load_settings(config, identity_enabled=...)` reads it (already written).

## 3. Measurement rows (the storage contract)

Table `exp_measurements`. Every row belongs to one experiment, one metric, optionally one variant
and one run. Columns `value`, `count` (>= 1), `denominator`, `sum_sq` are read by metric kind:

| kind | value | count | denominator | sum_sq | estimate per variant |
|---|---|---|---|---|---|
| `proportion` | successes (0 <= value <= count) | trials | - | - | sum(value)/sum(count) |
| `mean`, `duration`, `currency` | sum of the observations | number of observations | - | sum of squares (optional; for count = 1 it is value^2) | sum(value)/sum(count) |
| `count` | number of events | exposure units (e.g. days, sessions) | - | - | sum(value)/sum(count) (a rate) |
| `ratio` | numerator sum | units | denominator sum (> 0) | - | sum(value)/sum(denominator) |
| `ordinal` | the level (a number, e.g. 1..5 or 0..100) | units at that level | - | - | mean level = sum(value*count)/sum(count) |
| `categorical` | the category code (integer) | units in that category | - | - | none (a distribution) |

Note ordinal/categorical rows are level rows: `value` is the level, not a sum. For ordinal the
aggregate `value_sum` is `sum(value*count)` and `sum_sq` is `sum(value^2*count)`; for categorical
`levels` carries the distribution.

A category's label lives in the metric `definition.levels` (`{"1": "sehr unzufrieden", ...}`).

`dims` (JSONB object, string values) carries segments (e.g. `{"segment": "SMB"}`); `observed_at`
the time the value belongs to; `batch_id` groups one import so it can be undone.

Aggregate (as returned by `store.aggregates` and passed to evaluators), one per variant (plus one
with `variant: null` for rows without a variant):

```json
{"variant": "B", "rows": 12, "n": 10714, "value_sum": 175.0, "denominator_sum": null,
 "sum_sq": null, "estimate": 0.016334, "levels": null}
```

- `n` = sum(count). `value_sum` as in the table above. `denominator_sum` for ratio, else null.
- `sum_sq`: sum over rows of `sum_sq`, where a row with count = 1 and null sum_sq contributes
  value^2 (ordinal: value^2*count). If any row with count > 1 has null sum_sq (non-ordinal), the
  aggregate `sum_sq` is null (variance unknown).
- `levels`: for ordinal and categorical, `{"<level>": units}` with the level formatted by
  `kinds.level_key(value)` (integers without `.0`); else null.

## 4. Experiment type definitions (part A validates, part C stores)

A type version's `definition` (JSONB) is exactly this shape (JSON Schema in `schema.py`):

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
    {"from": "*", "to": "stopped", "label": "Abbrechen", "roles": ["experiments_manager"]}
  ],
  "variants": {"min": 2, "max": 10,
               "defaults": [{"key": "A", "name": "Kontrolle", "is_control": true},
                            {"key": "B", "name": "Variante B"}]},
  "metrics": [{"metric": "ctr", "role": "primary"},
              {"metric": "bounce_rate", "role": "guardrail", "op": "max", "value": 0.7}],
  "evaluation": [{"evaluator": "builtin.bayes_proportion", "metric": "primary", "params": {}}],
  "decision": {"require_learning": true}
}
```

Rules:

- `fields[].type` one of `text` (<= 500 chars), `longtext` (<= 20000), `number`, `integer`,
  `enum` (needs `options`, 1-50 strings), `multi_enum` (same), `date` (`YYYY-MM-DD`), `url`
  (http/https), `boolean`. Optional `min`/`max` for number/integer. `key` matches
  `^[a-z][a-z0-9_]{0,39}$`, unique; at most 40 fields. `label` 1-80 chars. `help` <= 300.
- `states`: 2-12, keys `^[a-z][a-z0-9_]{0,31}$`, unique; `phase` optional, one of `running`
  (entering it the first time sets `started_at`), `decided` (sets `decided_at` and `ended_at`),
  `stopped` (sets `ended_at`). At most one state per phase. `initial` must be a state.
- `transitions`: `from` is a state key or `"*"` (any state except `to`); `to` a state key;
  `label` 1-40 chars; `requires` from the vocabulary below; `roles` optional list from
  `experimenter`, `experiments_manager` (admin always allowed; empty or missing = any viewer).
- Requirement vocabulary (German messages when unmet are produced by `schema.check_transition`):
  `hypothesis` ("Die Hypothese fehlt."), `primary_metric` ("Es ist keine primäre Metrik
  festgelegt."), `variants:N` ("Es braucht mindestens N Varianten."), `measurements` ("Es gibt
  noch keine Messwerte."), `evaluation` ("Es gibt noch keine abgeschlossene Auswertung."),
  `decision` ("Es ist noch keine Entscheidung festgehalten."), `learning` ("Die Erkenntnis
  fehlt."), `field:<key>` ("Das Feld «<label>» ist leer.").
- `variants`: `min` 0-10, `max` 1-20, `defaults` 0-10 items (`key` as in exp_variants, `name`,
  optional `is_control`, optional `allocation`).
- `metrics`: metric keys resolved in the experiment's domain first, then global; roles
  `primary` (at most one), `secondary`, `guardrail` (needs `op` `max`|`min` and `value`).
  Unknown metric keys are an error when the definition is saved.
- `evaluation`: `evaluator` is an evaluator key; `metric` is `"primary"`, `"all"` (every metric
  whose kind the evaluator accepts) or a metric key; `params` object.
- `decision.require_learning`: the decision form requires a learning.

## 5. Packs (part A)

A pack is YAML (also accepted as JSON). The shipped packs live in `src/experiments/packs/`:
`core.yaml`, `engineering.yaml`, `marketing.yaml`, `sales.yaml`, `product.yaml`. Export of a
domain produces the same format, so a domain can be kept in git and imported elsewhere.

```yaml
pack: marketing            # name, ^[a-z][a-z0-9-]{1,31}$
title: Marketing
description: A/B-Tests, Kampagnen und Content-Tests.
version: 1
domain:                    # omitted in core.yaml (global types/metrics)
  key: marketing
  name: Marketing
  id_prefix: MKT
  color: "#eb6834"
  description: ...
metrics:
  - {key: ctr, name: Klickrate, kind: proportion, unit: "%", direction: higher,
     description: ..., definition: {}}
types:
  - key: ab_test
    name: A/B-Test
    description: ...
    definition: {...}      # section 4
evaluators:                # optional; python/julia only (builtins are always present)
  - {key: mkt.bootstrap_mean, name: ..., language: python, description: ...,
     input_kinds: [mean], params_schema: {}, code: "..."}
```

Shipped content ("our scenario"):

- `core`: global type `hypothesis` "Allgemeine Hypothese" (fields: none; states draft, running,
  analysis, decided, stopped), global metrics none. Also the example custom evaluators
  `example.bootstrap_mean_py` (Python, mean-like kinds: bootstrap CI of the difference, uses
  numpy) and `example.beta_binomial_jl` (Julia, proportion: posterior P(better) by sampling with
  the stdlib `Random` only). Both need the runner.
- `engineering` (ENG, `#2a78d6`): types `offline_eval` "Offline-Evaluation" (fields: component
  enum [Suche, Ingestion, Vorschau, Cortex, RemoteController, Plattform, Sonstiges], query_set
  text, baseline_ref text, candidate_ref text), `performance` "Performance-Änderung" (fields:
  component, environment enum [lokal, CI, Staging, Produktion]), `rollout` "Feature-Rollout"
  (fields: feature_flag text, rollout_percent number 0-100). Metrics: `recall_at_20` Recall@20
  (mean, higher, unit "", definition decimals 3), `ndcg_at_10` NDCG@10 (mean, higher),
  `mrr` MRR (mean, higher), `latency_p95_ms` "Latenz p95" (duration, lower, "ms"),
  `index_size_gb` "Indexgrösse" (mean, lower, "GB"), `ci_minutes` "CI-Dauer" (duration, lower,
  "min"), `error_rate` Fehlerrate (proportion, lower, "%").
- `marketing` (MKT, `#eb6834`): types `ab_test` "A/B-Test" (fields: channel enum [LinkedIn,
  Google Ads, E-Mail, Website, Webinar, Messe, Sonstiges], audience text, budget number,
  campaign_ref text), `campaign` "Kampagne" (fields: channel, audience, budget, goal longtext),
  `content_test` "Content-Test". Metrics: `ctr` Klickrate (proportion, higher, "%"),
  `conversion_rate` Konversionsrate (proportion, higher, "%"), `demo_request_rate`
  "Demo-Anfragen" (proportion, higher, "%"), `bounce_rate` Absprungrate (proportion, lower, "%"),
  `cost_per_click` "Kosten pro Klick" (currency, lower, "CHF"), `cost_per_lead` "Kosten pro Lead"
  (currency, lower, "CHF").
- `sales` (SAL, `#1baf7a`): types `playbook` "Playbook-Test" (fields: segment enum [Kanzlei klein,
  Kanzlei mittel, Kanzlei gross, Rechtsabteilung, Sonstiges], territory enum [Schweiz,
  Deutschland, Österreich, Sonstiges], sequence text, planned_n integer), `pricing` "Preis-Test".
  Metrics: `reply_rate` Antwortrate (proportion, higher), `meeting_rate` Terminquote
  (proportion, higher), `pilot_conversion` "Pilot → Vertrag" (proportion, higher),
  `pipeline_value` Pipeline-Wert (currency, higher, "CHF"), `unsubscribe_rate` Abmelderate
  (proportion, lower), `cycle_days` "Verkaufszyklus" (duration, lower, "Tage").
- `product` (PRD, `#4a3aa7`): types `usability` "Nutzertest" (fields: sessions integer,
  persona text, script longtext), `feature` "Feature-Rollout". Metrics: `task_success`
  Aufgabenerfolg (proportion, higher), `sus_score` "SUS-Wert" (ordinal, higher, "", levels not
  needed), `time_to_value_s` "Zeit bis Ergebnis" (duration, lower, "s"), `satisfaction`
  Zufriedenheit (ordinal 1-5 with labels), `preferred_option` "Bevorzugte Variante"
  (categorical with levels).

Each type sets sensible `states`/`transitions` (as in section 4), `variants` (A/B types: min 2 with
defaults A=Kontrolle, B; offline eval: defaults `baseline` (control) and `candidate`), `metrics`
defaults, and `evaluation` pipeline (proportion primaries: `builtin.bayes_proportion` and
`builtin.two_proportion`; mean-like: `builtin.welch_t`; always `builtin.describe` on `all`).

`src/experiments/packs.py` API:

```python
PACKS_DIR: pathlib.Path
def available_packs() -> list[dict]          # [{"name","title","description","version","domain_key"}] sorted, core first
def load_pack(name: str) -> dict            # parsed + validated shipped pack; NotFound if unknown
def parse_pack_text(text: str) -> dict      # YAML or JSON -> validated pack dict; ValidationError
def validate_pack(pack: dict) -> dict       # normalised copy; every type definition validated
                                            # with schema.validate_type_definition; metric
                                            # references resolvable within pack + builtin names
def dump_pack(pack: dict) -> str            # YAML text (sort_keys=False, allow_unicode=True)
```

## 6. Part A module APIs

`src/experiments/kinds.py`:

```python
@dataclass(frozen=True)
class KindSpec:
    key: str; label: str; description: str
    needs_denominator: bool; is_distribution: bool
    value_label: str; count_label: str          # German labels for the entry form
    default_evaluators: tuple[str, ...]
KINDS: dict[str, KindSpec]    # the 8 kinds of section 3, German labels:
    # proportion "Anteil", mean "Mittelwert", count "Rate (Ereignisse je Einheit)",
    # duration "Dauer", currency "Geldbetrag", ratio "Verhältnis", ordinal "Skala",
    # categorical "Kategorie"
def validate_row(kind: str, row: dict) -> dict   # normalised {value,count,denominator,sum_sq};
                                                 # ValidationError (German) on e.g. value > count
def estimate(kind: str, agg: dict) -> float | None  # from an aggregate dict (section 3)
def level_key(value: float) -> str
def format_value(kind: str, x: float | None, unit: str = "", decimals: int | None = None) -> str
    # German display: proportion as percent "1,63 %", others with unit, "–" for None,
    # thousands separator "'" (Swiss), decimal comma
```

`src/experiments/schema.py`:

```python
FIELD_TYPES: tuple[str, ...]
TYPE_DEFINITION_SCHEMA: dict
def validate_type_definition(definition: dict) -> dict          # normalised; ValidationError(fields=...)
def parse_definition_text(text: str) -> dict                   # JSON or YAML text -> dict (not yet validated)
def validate_field_values(definition: dict, values: dict, *, partial: bool = False) -> dict
    # normalised values; unknown keys -> ValidationError; required-missing only when not partial;
    # empty string / None removes the key
def display_field_value(field: dict, value) -> str             # German display text
def initial_state(definition: dict) -> str
def state_label(definition: dict, state: str) -> str           # falls back to the key
def state_phase(definition: dict, state: str) -> str | None
def transitions_from(definition: dict, state: str) -> list[dict]  # [{"to","label","requires","roles"}]
def check_transition(definition: dict, from_state: str, to_state: str, *,
                     facts: dict, roles: frozenset) -> list[str]
    # ValidationError if no such transition; Forbidden if roles do not allow it;
    # returns the German messages of unmet requirements ([] = allowed).
    # facts: {"hypothesis": bool, "primary_metric": bool, "variants": int,
    #         "measurements": int, "evaluations": int, "decision": bool,
    #         "learning": bool, "fields": {key: bool}}
def metric_refs(definition: dict) -> list[str]   # metric keys referenced by metrics/evaluation
```

## 7. Part B module APIs

`src/experiments/stats.py` — pure Python (math, random), no numpy. Each function documents its
method and returns a dict of floats; tests compare with reference values computed with scipy.

```python
def normal_cdf(x) ; def normal_ppf(p)
def student_t_cdf(t, df) ; def student_t_ppf(p, df)
def chi2_sf(x, df)
def betainc(a, b, x)              # regularized incomplete beta I_x(a, b)
def gammaincc(s, x)               # regularized upper incomplete gamma Q(s, x)
def two_proportion_test(s1, n1, s2, n2, alpha=0.05) -> dict
    # keys: p1, p2, diff (p2-p1), ci_low, ci_high (Wald on diff), z, p_value (two-sided,
    # pooled SE), relative_lift (diff/p1 or None)
def bayes_beta_binomial(s1, n1, s2, n2, prior_a=1.0, prior_b=1.0, draws=40000, seed=7) -> dict
    # keys: prob_better (P(p2 > p1)), expected_loss (E[max(p1-p2, 0)]), diff_mean,
    # ci_low, ci_high (95% equal-tailed of p2-p1), deterministic for a given seed
def welch_t_test(mean1, var1, n1, mean2, var2, n2, alpha=0.05) -> dict
    # keys: diff (mean2-mean1), ci_low, ci_high, t, df, p_value
def poisson_rate_test(e1, t1, e2, t2, alpha=0.05) -> dict
    # keys: rate1, rate2, ratio (rate2/rate1), ci_low, ci_high (on ratio, log-normal), p_value
def chi_square_independence(table: list[list[float]]) -> dict
    # keys: chi2, df, p_value, cramers_v
def sample_size_proportion(p_base, mde_abs, alpha=0.05, power=0.8) -> int   # per arm
def sample_size_mean(sd, mde_abs, alpha=0.05, power=0.8) -> int
```

`src/experiments/evaluators.py`:

```python
@dataclass(frozen=True)
class BuiltinSpec:
    key: str; name: str; description: str
    input_kinds: tuple[str, ...]; params_schema: dict
    fn: Callable[[dict, dict], dict]
BUILTINS: dict[str, BuiltinSpec]
    # 'builtin.describe'         "Beschreibung je Variante"   all kinds
    # 'builtin.two_proportion'   "Zwei-Anteile-Test"          proportion
    # 'builtin.bayes_proportion' "Bayes-Vergleich (Anteile)"  proportion
    # 'builtin.welch_t'          "Welch-t-Test"               mean, duration, currency, ordinal
    # 'builtin.poisson_rate'     "Raten-Vergleich"            count
    # 'builtin.chi_square'       "Chi-Quadrat-Test"           categorical, ordinal
def build_input(*, experiment: dict, metric: dict, variants: list[dict],
                aggregates: list[dict], rows: list[dict] | None,
                rows_truncated: bool, params: dict) -> dict      # the input contract below
def run_builtin(key: str, data: dict) -> dict                    # output contract below; params in data["params"]
def sanitize_output(raw) -> dict                                 # ValidationError if not an object;
                                                                 # clips sizes, drops unknown keys
def default_params(key: str) -> dict
```

Builtins compare every non-control variant with the control (the variant with `is_control`,
else the first variant). Verdict: considering `metric.direction` (`lower` flips the sign;
`none` gives `n/a`): Bayes: `better` if prob_better >= threshold (param `threshold`, default
0.95) for the best variant, `worse` if <= 1 - threshold, else `inconclusive`; frequentist
tests: `better`/`worse` if p_value < alpha (param `alpha`, default 0.05) with the sign of the
effect, else `inconclusive`; describe: `n/a`. Too little data (a variant with n < 2, or missing
variance for welch) gives `inconclusive` with a German warning, never an exception.

Evaluator input contract (what `build_input` returns and what custom evaluators receive):

```json
{
  "experiment": {"key": "MKT-1", "title": "...", "hypothesis": "...", "domain": "marketing",
                 "type": "ab_test", "status": "running", "fields": {"channel": "LinkedIn"}},
  "metric": {"key": "ctr", "name": "Klickrate", "kind": "proportion", "unit": "%",
             "direction": "higher", "role": "primary", "definition": {}},
  "variants": [{"key": "A", "name": "Kontrolle", "is_control": true}, {"key": "B", "name": "Variante B", "is_control": false}],
  "aggregates": [ {"variant": "A", "rows": 12, "n": 10688, "value_sum": 129.0, "denominator_sum": null,
                   "sum_sq": null, "estimate": 0.01207, "levels": null} ],
  "rows": [ {"variant": "A", "run": null, "value": 11.0, "count": 900, "denominator": null,
             "sum_sq": null, "observed_at": "2026-09-16T00:00:00+00:00", "dims": {}} ],
  "rows_truncated": false,
  "params": {}
}
```

Output contract (builtins produce it; custom output passes `sanitize_output`):

```json
{
  "verdict": "better | worse | inconclusive | n/a",
  "headline": "P(B besser als A) = 99,6 %",
  "summary": "Markdown, <= 20000 chars; this text is indexed into Knovas",
  "comparisons": [{"variant": "B", "baseline": "A", "label": "Differenz",
                   "estimate": 0.0042, "ci_low": 0.0011, "ci_high": 0.0073,
                   "p_value": null, "prob_better": 0.996, "unit": "pts"}],
  "variants": [{"variant": "A", "n": 10688, "value": 0.01207, "sd": null}],
  "values": {"prob_better": 0.996},
  "table": {"headers": ["..."], "rows": [["..."]]},
  "warnings": ["..."]
}
```

Limits in `sanitize_output`: headline <= 200, summary <= 20000, comparisons <= 50,
variants <= 50, values <= 100 keys (numbers, strings <= 500, booleans), table <= 50 headers and
<= 500 rows of <= 50 cells (cells stringified, <= 200 chars), warnings <= 20 of <= 300 chars;
unknown verdict -> `n/a`; NaN/Infinity -> null. Units: proportion differences in `pts`
(percentage points, estimate in fraction units 0..1), others in the metric's unit, ratios `x`.

## 8. Part C: store and service

`src/experiments/store.py` holds every SQL statement about domains, types, metrics, evaluators,
experiments, variants, metrics assignment, runs, measurements, notes, evaluations, decisions,
tokens and runtime settings (jobs SQL is part D). Functions take `conn` first. Besides whatever
the service needs, these are used by other parts and must exist with these signatures:

```python
def load_snapshot(conn, key_or_id: str) -> dict | None      # section 9 shape; None if not found
def lookup_by_keys(conn, keys: list[str]) -> dict[str, dict]
    # {KEY: {"key","title","hypothesis","status","status_label","archived",
    #        "domain_key","domain_name","domain_color","type_name"}}
def aggregates(conn, experiment_id: str, metric_id: str) -> list[dict]     # section 3
def evaluator_rows(conn, experiment_id: str, metric_id: str, limit: int) -> tuple[list[dict], bool]
def set_index_state(conn, experiment_id: str, state: str, error: str | None = None) -> None
    # 'indexed' also sets indexed_at = now(); error truncated to 500 chars
def experiment_ids_for_reindex(conn) -> list[tuple[str, str, str]]   # (id, key, domain_key), all
def resolve_api_token(conn, plaintext: str) -> dict | None
    # {"token_id","user_id"} for an unrevoked, unexpired token; updates last_used_at at most once a minute
def get_runtime_setting(conn, key: str)                      # value or settings.RUNTIME_DEFAULTS[key]
def set_runtime_setting(conn, key: str, value, actor) -> None
def get_evaluation_record(conn, evaluation_id: str) -> dict | None
    # {"id","experiment_id","evaluator_id","evaluator_key","language","code","version",
    #  "metric_id","params","status"}
def mark_evaluation(conn, evaluation_id: str, *, status: str, output: dict | None = None,
                    error: str | None = None, logs: str | None = None,
                    duration_ms: int | None = None) -> None
```

Experiment keys: allocated atomically with
`UPDATE exp_domains SET next_seq = next_seq + 1, updated_at = now() WHERE id = %s RETURNING id_prefix, next_seq - 1`.

Builtin evaluators are rows too: on first use (`store.ensure_builtin_evaluators(conn)`,
idempotent, called by the service before listing or running evaluators) one `exp_evaluators`
row per `evaluators.BUILTINS` key with `language='builtin'` and version 1 (`code` = the key).

`src/experiments/service.py`:

```python
class ExperimentService:
    def __init__(self, conn, actor, settings, *, runner=None, knovas_search=None): ...
        # actor: identity.users.User (has .id, .email, .display_name, .roles)
        # runner: runner_client.RunnerClient | None
        # knovas_search: callable(query: str, limit: int) -> dict  (the request-bound
        #                api_client.search_documents) | None
```

Every public method first checks `permissions.can_view(actor)` (else `NotFound("Seite nicht
gefunden.")`, so the module stays invisible) and, where marked [manage], `can_manage` (else
`Forbidden("Nur für Verantwortliche der Experimente.")`). Every mutation writes
`identity.audit.record(conn, action='experiment.<verb>' | 'experiments.<noun>.<verb>', actor=actor,
target_type='experiment' | 'exp_domain' | 'exp_type' | 'exp_metric' | 'exp_evaluator' | 'exp_token',
target_id=<experiment KEY or id>, detail={...})`, and every mutation of an experiment
bumps `row_version` and `updated_at` and calls `self._queue_index(experiment_id)`:
if `settings.index_enabled` then `jobs.JobQueue(conn).enqueue('index', {"experiment_id": id},
dedupe_key=f"index:{id}", delay_seconds=settings.index_debounce_seconds)` and
`index_state='pending'`, else `index_state='off'`.

Methods and their return values (these ARE the JSON payloads of the web API, section 10):

```python
# domains
list_domains(include_archived=False) -> list[dict]
    # {"id","key","name","description","color","id_prefix","pack","archived",
    #  "experiment_count","running_count"}
create_domain(data) -> dict                 # [manage] data: key,name,id_prefix,color?,description?
update_domain(key, data) -> dict            # [manage] name,color,description,archived(bool)
# types
list_types(domain=None, include_archived=False) -> list[dict]
    # {"id","key","name","description","domain_key"|None,"current_version","archived","definition"}
    # domain=<key>: that domain's types plus global ones
get_type(type_id) -> dict                   # + "versions": [{"version","created_at","created_by"}]
create_type(data) -> dict                   # [manage] domain(key|None),key,name,description?,definition|definition_text
add_type_version(type_id, data) -> dict     # [manage] definition|definition_text, name?, description?
                                            # identical definition -> no new version
set_type_archived(type_id, archived: bool) -> dict   # [manage]
# metrics
list_metrics(domain=None, include_archived=False) -> list[dict]
    # {"id","key","name","kind","kind_label","unit","direction","description","definition",
    #  "domain_key"|None,"archived","version","measurement_count"}
create_metric(data) -> dict                 # [manage]
update_metric(metric_id, data) -> dict      # [manage] kind change refused if measurements exist
# evaluators
list_evaluators(include_archived=False) -> list[dict]
    # {"id","key","name","language","description","input_kinds","current_version","archived",
    #  "builtin": bool, "params_schema"}
get_evaluator(evaluator_id) -> dict         # + "code" (current), "versions"
create_evaluator(data) -> dict              # [manage] key,name,language(python|julia),description,code,input_kinds,params_schema
add_evaluator_version(evaluator_id, data) -> dict   # [manage]
test_evaluator(evaluator_id, data) -> dict  # [manage] data: experiment (KEY), metric (key), params,
                                            # code? (unsaved code to test); runs synchronously
                                            # through the runner; returns {"ok","output","error","logs","duration_ms"}
# experiments
list_experiments(*, domain=None, status=None, q=None, include_archived=False,
                 after=None, limit=50) -> dict
    # {"items": [summary], "next_after": str|None, "total": int}
    # summary: {"key","title","status","status_label","archived","domain": {"key","name","color"},
    #           "type": {"key","name"}, "owner": {"id","display_name"}|None,
    #           "primary_metric": {"key","name","unit","kind"}|None,
    #           "latest": {"headline","verdict","finished_at"}|None,  # newest done evaluation of the primary metric
    #           "updated_at","index_state"}
    # order: updated_at DESC, id DESC; after = opaque cursor string; q matches key, title,
    # hypothesis (ILIKE); limit 1..200
create_experiment(data) -> dict             # snapshot; data: domain (key), type (id or key),
                                            # title, hypothesis?, description?, fields?, tags?,
                                            # variants? (else type defaults), metrics? (else type defaults)
get_experiment(key) -> dict                 # snapshot + "definition" (pinned type version)
                                            # + "transitions": [{"to","label","allowed","missing":[...]}]
                                            # + "evaluators": list_evaluators() filtered to this experiment's metric kinds
update_experiment(key, data) -> dict        # data: row_version (required), title?, hypothesis?,
                                            # description?, fields? (partial), tags?, owner_id?, archived?
                                            # Conflict("Das Experiment wurde inzwischen geändert. Bitte neu laden.")
transition(key, data) -> dict               # data: to, comment?; snapshot
set_variants(key, data) -> dict             # data: variants (full list; ids kept by key); a variant
                                            # with measurements cannot be removed (ValidationError)
set_metrics(key, data) -> dict              # data: metrics [{metric (key), role, guardrail_op?, guardrail_value?}]
add_measurements(key, data, source='manual') -> dict
    # data: rows [{metric, variant?, value, count?, denominator?, sum_sq?, observed_at?, dims?, run_id?}]
    # (metric/variant by key) <= settings.max_rows_per_request rows, all-or-nothing;
    # returns {"batch_id","inserted"}; after insert enqueues a 'pipeline' job
    # (dedupe "pipeline:<id>", delay 30 s)
import_csv(key, content: bytes, filename: str) -> dict
    # UTF-8 (BOM tolerated), delimiter auto (',' ';' tab). Header row required; columns:
    # metric, variant, value, count, denominator, sum_sq, observed_at, run, plus any other
    # column becomes a dim. Numbers accept decimal comma. Returns {"batch_id","inserted"} or
    # ValidationError listing up to 20 line errors ("Zeile 5: ...").
list_batches(key) -> list[dict]             # {"batch_id","source","rows","metrics":[keys],"created_at","created_by"}
delete_batch(key, batch_id) -> dict         # {"deleted": n}
add_run(key, data, source='manual') -> dict
    # data: name?, variant?, status?, params?, environment?, commit?, started_at?, ended_at?,
    # metrics? {metric_key: number | {value,count,denominator,sum_sq}}, note?
    # -> run dict {"id","name","variant","status","params","environment","commit","source",
    #              "started_at","ended_at","created_at","metrics": {key: estimate}}
list_runs(key, after=None, limit=50) -> dict   # {"items":[run], "next_after"}
add_note(key, data) -> dict                 # data: body, kind?, variant?, run_id?
delete_note(key, note_id) -> dict           # {"deleted": 1}; author or manager
run_evaluation(key, data) -> dict           # data: evaluator (key), metric (key), params?
    # builtin: computed now, status 'done'; custom: status 'queued' + 'evaluate' job
    # (Unavailable("Die Rechenumgebung ist nicht eingerichtet.") if no runner configured)
run_pipeline(key) -> list[dict]             # runs the type's evaluation list; builtins now, custom queued
get_evaluation(key, evaluation_id) -> dict
decide(key, data) -> dict                   # data: verdict, rationale?, learning?; snapshot
delete_experiment(key) -> dict              # [manage] {"deleted": KEY}; enqueues 'unindex'
                                            # with {"pointer": ...} BEFORE deleting
reindex(key) -> dict                        # enqueue now; {"queued": true}
activity(key, limit=50) -> list[dict]       # audit_log entries: {"at","action","label","actor","detail"}
# machine tokens (every viewer manages their own)
list_tokens() -> list[dict]                 # {"id","name","hint","created_at","last_used_at","expires_at","revoked"}
create_token(data) -> dict                  # data: name, expires_days? -> same + "token" (plaintext, once)
                                            # token format: "kxp_" + secrets.token_urlsafe(32)
revoke_token(token_id) -> dict
# operations [manage]
index_status() -> dict                      # {"enabled","counts":{state: n},"jobs":{status: n},
                                            #  "failures":[{"kind","error","at","payload"}],"runner":{...}}
reindex_all() -> dict                       # {"queued": n}
get_settings() -> dict                      # {"show_in_search": bool}   (any viewer may read)
update_settings(data) -> dict               # [manage]
list_packs() -> list[dict]                  # packs.available_packs() + "installed": bool
install_pack(name) -> dict                  # [manage] idempotent; {"domain","types","metrics","evaluators"} counts
import_pack(data) -> dict                   # [manage] data: text (YAML/JSON)
export_domain(key) -> str                   # [manage] YAML text of a pack
search(q, limit=30) -> dict                 # Knovas search restricted to experiments; falls back
    # to the database: {"source": "knovas"|"database", "items": [summary + "snippet"]}
```

Module-level functions used by the worker (part D calls them from `tasks.py`):

```python
def execute_evaluation(conn, evaluation_id: str, *, settings, runner) -> None
    # status running -> done/failed; builds the input with store.aggregates and
    # store.evaluator_rows (<= settings.evaluator_max_rows); custom via runner.run(...);
    # output through evaluators.sanitize_output; enqueues index. Runner unavailable:
    # raises jobs.RetryLater(60, ...) so the job waits without failing.
def run_pipeline_job(conn, experiment_id: str, *, settings, runner) -> None
    # the type's evaluation list without an actor (system); builtins in-process, custom queued
```

## 9. Experiment snapshot (store.load_snapshot and service.get_experiment)

```json
{
  "id": "uuid", "key": "MKT-1", "title": "...", "hypothesis": "...", "description": "...",
  "status": "running", "status_label": "Läuft", "archived": false,
  "domain": {"id": "...", "key": "marketing", "name": "Marketing", "color": "#eb6834", "id_prefix": "MKT"},
  "type": {"id": "...", "key": "ab_test", "name": "A/B-Test", "version": 1},
  "fields": [{"key": "channel", "label": "Kanal", "type": "enum", "value": "LinkedIn", "display": "LinkedIn"}],
  "field_values": {"channel": "LinkedIn"},
  "tags": [],
  "owner": {"id": "...", "display_name": "..."},
  "variants": [{"id","key","name","description","is_control","allocation","position"}],
  "metrics": [{"id","key","name","kind","kind_label","unit","direction","role",
               "guardrail_op","guardrail_value","definition",
               "aggregates": [ /* section 3, one per variant */ ]}],
  "evaluations": [{"id","evaluator_key","evaluator_name","language","evaluator_version",
                   "metric_key","params","status","output","error","logs",
                   "created_at","finished_at","duration_ms","requested_by": {"display_name"}|null}],
                   // newest first, at most 100
  "decisions": [{"id","verdict","verdict_label","rationale","learning",
                 "decided_by": {"id","display_name"}|null,"decided_at"}],   // newest first
  "notes": [{"id","kind","kind_label","body","variant","run_id",
             "created_by": {"id","display_name"}|null,"created_at"}],        // newest first, <= 200
  "runs": [/* run dicts, newest first, <= 100 */],
  "run_count": 0, "measurement_count": 0, "batch_count": 0,
  "created_at","updated_at","started_at","ended_at","decided_at",
  "row_version": 3,
  "index": {"state": "pending", "indexed_at": null, "error": null}
}
```

German labels: verdicts ship "Übernehmen", iterate "Weiterentwickeln", stop "Verwerfen",
inconclusive "Ohne klares Ergebnis"; note kinds note "Notiz", observation "Beobachtung",
interview "Interview", feedback "Rückmeldung"; evaluation verdicts better "besser",
worse "schlechter", inconclusive "offen", n/a "–".

## 10. HTTP API (part E routes, part F consumes)

All JSON. Success: `{"success": true, <key>: <value>}` with the key named below. Failure:
`{"success": false, "error": "<German>", "fields": {...}?}` with the error's `status`
(400/403/404/409/503); unexpected exceptions: 500 with `"Interner Serverfehler"` (log with
exc_info). Session routes need the signed-in user (global gate), the X-CSRF-Token header on
non-GET (global gate), and a viewing role (else 404 page / 404 JSON "Nicht gefunden.").

Pages (blueprint `experiments`, endpoint names in brackets):

| Path | Endpoint | Template |
|---|---|---|
| `GET /experiments` | `experiments.list_page` | `experiments_list.html` |
| `GET /experiments/<key>` | `experiments.detail_page` | `experiments_detail.html` |
| `GET /experiments/verwaltung` | `experiments.manage_page` | `experiments_manage.html` (any viewer; manager-only tabs hidden and refused server-side) |

The key pattern is `^[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}$`; route converter `string`; `verwaltung` is
matched first. Page context: `active_nav='experiments'`, `**page_context()` (sidebar values),
`app_title`, `brand`, `csrf_token`, `asset_version`, plus `experiments_can_manage` (bool),
`experiment_key` (detail), `pointer_prefix`. Pages carry `<meta name="csrf-token">` and load
`experiments_common.js` + the page script with `?v={{ asset_version }}`.

JSON API (blueprint `experiments`, prefix `/api/experiments`):

| Method, path | Service call | Response key |
|---|---|---|
| GET `/api/experiments?domain=&status=&q=&archived=0&after=&limit=` | list_experiments | `result` |
| POST `/api/experiments` | create_experiment | `experiment` (201) |
| GET `/api/experiments/search?q=` | search | `result` |
| GET `/api/experiments/<key>` | get_experiment | `experiment` |
| PATCH `/api/experiments/<key>` | update_experiment | `experiment` |
| DELETE `/api/experiments/<key>` | delete_experiment | `result` |
| POST `/api/experiments/<key>/transition` | transition | `experiment` |
| PUT `/api/experiments/<key>/variants` | set_variants | `experiment` |
| PUT `/api/experiments/<key>/metrics` | set_metrics | `experiment` |
| POST `/api/experiments/<key>/measurements` | add_measurements | `result` (201) |
| POST `/api/experiments/<key>/measurements/csv` (multipart field `file`, <= 20 MB) | import_csv | `result` (201) |
| GET `/api/experiments/<key>/batches` | list_batches | `batches` |
| DELETE `/api/experiments/<key>/batches/<batch_id>` | delete_batch | `result` |
| GET `/api/experiments/<key>/runs?after=` | list_runs | `result` |
| POST `/api/experiments/<key>/runs` | add_run | `run` (201) |
| POST `/api/experiments/<key>/notes` | add_note | `note` (201) |
| DELETE `/api/experiments/<key>/notes/<note_id>` | delete_note | `result` |
| POST `/api/experiments/<key>/evaluations` | run_evaluation | `evaluation` (201) |
| POST `/api/experiments/<key>/pipeline` | run_pipeline | `evaluations` |
| GET `/api/experiments/<key>/evaluations/<evaluation_id>` | get_evaluation | `evaluation` |
| POST `/api/experiments/<key>/decisions` | decide | `experiment` (201) |
| POST `/api/experiments/<key>/reindex` | reindex | `result` |
| GET `/api/experiments/<key>/activity` | activity | `activity` |
| GET/POST `/api/experiments/domains` | list_domains/create_domain | `domains`/`domain` |
| PATCH `/api/experiments/domains/<domain_key>` | update_domain | `domain` |
| GET `/api/experiments/domains/<domain_key>/export` | export_domain | `text` |
| GET/POST `/api/experiments/types?domain=&archived=` | list_types/create_type | `types`/`type` |
| GET `/api/experiments/types/<type_id>` | get_type | `type` |
| POST `/api/experiments/types/<type_id>/versions` | add_type_version | `type` |
| POST `/api/experiments/types/<type_id>/archive` (body `{"archived": bool}`) | set_type_archived | `type` |
| GET/POST `/api/experiments/metrics?domain=` | list_metrics/create_metric | `metrics`/`metric` |
| PATCH `/api/experiments/metrics/<metric_id>` | update_metric | `metric` |
| GET/POST `/api/experiments/evaluators` | list_evaluators/create_evaluator | `evaluators`/`evaluator` |
| GET `/api/experiments/evaluators/<evaluator_id>` | get_evaluator | `evaluator` |
| POST `/api/experiments/evaluators/<evaluator_id>/versions` | add_evaluator_version | `evaluator` |
| POST `/api/experiments/evaluators/<evaluator_id>/test` | test_evaluator | `result` |
| GET/POST `/api/experiments/tokens` | list_tokens/create_token | `tokens`/`token` |
| DELETE `/api/experiments/tokens/<token_id>` | revoke_token | `token` |
| GET `/api/experiments/index` | index_status | `index` |
| POST `/api/experiments/index/reindex` | reindex_all | `result` |
| GET/PUT `/api/experiments/settings` | get_settings/update_settings | `settings` |
| GET `/api/experiments/packs` | list_packs | `packs` |
| POST `/api/experiments/packs/<name>/install` | install_pack | `result` |
| POST `/api/experiments/packs/import` (body `{"text": "..."}`) | import_pack | `result` |
| GET `/api/experiments/meta` | kinds, field types, verdict labels, runner status, can_manage | `meta` |

`meta` = `{"kinds": [{"key","label","description","needs_denominator","is_distribution","value_label","count_label"}],
"field_types": [...], "verdicts": {"ship": "Übernehmen", ...}, "note_kinds": {...},
"can_manage": bool, "runner": {"configured": bool, "ok": bool, "languages": {...}},
"index_enabled": bool, "pointer_prefix": "experiments"}` (runner health cached 30 s per worker).

Request bodies never use the key `access_groups` (the app rejects such bodies globally).
Route parameters are never named `doc_id` (that name triggers the document wall).

Machine API (blueprint `experiments_api`, prefix `/api/experiments/v1`, bearer tokens only):

| Method, path | Service call | Response key |
|---|---|---|
| GET `/api/experiments/v1/ping` | - | `user` (`{"display_name","roles"}`) |
| GET `/api/experiments/v1/experiments/<key>` | get_experiment (trimmed: key,title,status,domain,type,variants,metrics without aggregates) | `experiment` |
| POST `/api/experiments/v1/experiments/<key>/runs` | add_run(source='api') | `run` (201) |
| POST `/api/experiments/v1/experiments/<key>/measurements` | add_measurements(source='api') | `result` (201) |
| POST `/api/experiments/v1/experiments/<key>/notes` | add_note | `note` (201) |

Bearer rules: header `Authorization: Bearer kxp_...`; the token resolves to an active user with a
viewing role at request time (else 401 `{"success": false, "error": "Ungültiger oder
abgelaufener Zugangsschlüssel."}`); the session cookie is never consulted; these endpoints are
exempt from the login gate (via `IdentityGate.bearer_endpoints`) and from the CSRF header gate
(prefix `experiments_api.`) precisely because they accept nothing but a bearer token.

## 11. Knovas documents (part D)

- Pointer: `f"{settings.pointer_prefix}/{domain_key}/{KEY}"`, e.g. `experiments/marketing/MKT-1`.
  `search.parse_pointer(pointer)` returns the KEY for such a pointer (also when it starts with
  `/`), else None.
- `title`: `f"{KEY} · {title}"` (<= 500). `description`: hypothesis (<= 2000). `path`:
  `f"/Experimente/{domain name}/{type name}/{KEY} {title}"` (<= 2000; the path gets a BM25 boost).
- Body: Markdown rendered by `indexer.render_markdown(snapshot)`:

```
# MKT-1 · <title>

Experiment im Bereich <domain> · Typ <type> · Status <status_label> · aktualisiert <date>

## Hypothese
<hypothesis>

## Beschreibung
<description>

## Angaben
- <label>: <display>

## Varianten
- <key> (Kontrolle): <name> – <description>

## Metriken
- <name> (<kind_label>, <direction German>, Rolle <primär|sekundär|Leitplanke ≤/≥ value>):
  <variant>: <formatted estimate> (n = <n>); ...

## Auswertungen
### <evaluator_name> – <metric name> – <date>
<headline>
<summary>

## Läufe
- <run name> (<variant>, <date>): <metric>: <value>; ...   (at most 50)

## Notizen
### <kind_label> – <date>
<body>

## Entscheidungen
### <verdict_label> – <date>
Begründung: <rationale>
Erkenntnis: <learning>
```

  Empty sections are omitted. No personal names or e-mail addresses go into the document.
  Parts: split at `## ` boundaries into chunks <= 40000 characters (a single section longer
  than that is split at paragraph boundaries); total document capped at 400000 characters
  (runs and notes truncated first, with a line saying so).
- Upload: `KnovasAPIClient.upload_text_document(identifier, *, title, description, path, parts,
  access_groups=None) -> dict` (new method, part D): init via `_request_no_retry('POST',
  endpoints['init_transmission'], data={identifier, part_count, title, description, path,
  [access_groups]})` then each part via `_secured_transmit_part_payload`. `access_groups` is
  sent only when the tuple is non-empty. Same identifier replaces the previous version.
- Delete: `client.delete_information_object(pointer)`; HTTP 404 counts as done.
- Error classes: connection errors, timeouts, HTTP 408/409/425/429/5xx -> retry with backoff;
  other 4xx -> dead job and `index_state='error'` with a German message
  ("Knovas hat das Dokument abgelehnt (HTTP 400).").
- Before every init: `jobs.take_rate_slot(conn, 'knovas_init', settings.index_per_minute)`;
  when it returns a wait > 0 the handler raises `RetryLater(wait)`.

## 12. Part D module APIs

`src/experiments/jobs.py`:

```python
class RetryLater(Exception):          # wait without consuming an attempt
    def __init__(self, delay_seconds: float, reason: str = ""): ...
class PermanentError(Exception): ...  # dead immediately
@dataclass
class Job: id: int; kind: str; payload: dict; attempts: int; max_attempts: int
class JobQueue:
    def __init__(self, conn): ...
    def enqueue(self, kind, payload, *, dedupe_key=None, delay_seconds=0, max_attempts=8) -> int | None
        # INSERT ... ON CONFLICT (dedupe_key) WHERE status = 'pending' DO NOTHING; None when coalesced
    def claim(self, worker_id: str, lease_seconds: int = 300) -> Job | None
        # pending & due, or running with an expired lease; FOR UPDATE SKIP LOCKED; attempts += 1
    def complete(self, job_id) -> None
    def retry(self, job, error: str) -> None     # backoff min(3600, 30 * 2**(attempts-1)) s; dead at max_attempts
    def defer(self, job, delay_seconds, reason="") -> None   # attempts -= 1, run_after = now + delay
    def fail(self, job, error: str) -> None      # dead
    def counts(self) -> dict                     # {status: n}
    def recent_failures(self, limit=20) -> list[dict]
    def purge_finished(self, older_than_days=7) -> int
def take_rate_slot(conn, name: str, per_minute: int) -> float   # 0.0 = taken now, else seconds to wait
class JobWorker(threading.Thread):
    def __init__(self, *, connect, handlers: dict, poll_seconds: float, worker_id: str | None = None): ...
    def run(self) -> None        # loop: claim, dispatch, sleep with jitter when idle; reconnect with
                                 # backoff on DB errors; purge finished jobs about hourly; never dies
    def stop(self) -> None
    def run_once(self, conn) -> bool   # process at most one job; True if one was processed (tests)
```

`src/experiments/tasks.py`:

```python
def build_handlers(*, settings, index_client, runner) -> dict[str, Callable[[conn, dict], None]]
    # 'index': indexer.index_experiment(conn, payload['experiment_id'], client=index_client, settings=settings)
    #          (index_client None -> set index_state 'off' and return)
    # 'unindex': indexer.unindex_pointer(index_client, payload['pointer'])
    # 'evaluate': service.execute_evaluation(conn, payload['evaluation_id'], settings=settings, runner=runner)
    # 'pipeline': service.run_pipeline_job(conn, payload['experiment_id'], settings=settings, runner=runner)
```

`src/experiments/indexer.py`: `pointer_for(settings, domain_key, key)`, `render_markdown(snapshot)`,
`split_parts(markdown, max_chars=40000) -> list[str]`, `document_for(snapshot, settings) -> dict`
(`{"identifier","title","description","path","parts":[{"snippet"}],"access_groups":[...]}`),
`index_experiment(conn, experiment_id, *, client, settings)`, `unindex_pointer(client, pointer)`.

`src/experiments/runner_client.py`:

```python
class RunnerClient:
    def __init__(self, base_url: str, *, timeout_seconds: int = 120): ...
    def health(self) -> dict      # {"ok": bool, "languages": {"python": "3.x", "julia": "1.11.x"}, "error": str?}
                                  # cached 30 s per instance; never raises
    def run(self, *, language: str, code: str, data: dict, timeout_seconds: int | None = None) -> dict
        # {"ok": bool, "output": dict | None, "error": str | None, "logs": str, "duration_ms": int}
        # raises errors.Unavailable("Die Rechenumgebung ist nicht erreichbar.") on connection
        # errors or HTTP 503 (busy)
```

`src/experiments/search.py`:

```python
def parse_pointer(prefix: str, pointer: str) -> str | None
class SearchIntegration:
    def __init__(self, *, settings, connect_current, current_user, show_in_search): ...
        # connect_current(): the request's DB connection; current_user(): identity User | None;
        # show_in_search(conn) -> bool (runtime setting)
    def split(self, results: dict) -> tuple[dict, list[dict]]
        # takes experiment hits (pointer/doc_id/path under the prefix) out of results["results"];
        # returns a shallow copy of results without them, and the removed hit rows
    def rows(self, hits: list[dict]) -> list[dict]
        # [] when disabled, no user, no viewing role or show_in_search off; else one row per hit
        # whose KEY still exists: hit fields kept (doc_id, path, score, final_score, cosine_*),
        # plus "result_kind": "experiment", "title": "KEY · title", "app_url": "/experiments/KEY",
        # "experiment": {"key","domain_key","domain_name","domain_color","status_label","type_name"},
        # "context_snippet": first text of hit.top_chunks if any (str or dict text/snippet/content),
        # else the hypothesis (<= 300 chars), "file_exists": False, "can_open": False
```

`src/experiments/cli.py` (`python -m experiments <command>` via `__main__.py`), run inside the
docbridge-web container with the app's config: `status`, `reindex [KEY ...|--all]`,
`purge-index [--yes]` (deletes every experiment document from Knovas; works while the module is
switched off), `install-pack NAME`, `worker --once`.

## 13. app.py integration (part E)

1. After the identity block: `experiments_settings = load_settings(config,
   identity_enabled=identity_enabled)`; if `config.get_bool('experiments.enabled')` but identity
   is off, log a warning.
2. `_sidebar_context()` gains `'experiments_nav': _experiments_nav_visible()` — True only when
   enabled, identity on, and `permissions.can_view(identity_gate.current_user())`.
3. `@app.before_request refuse_experiments_when_switched_off` (after the CSRF gate, next to the
   Cortex gate): when disabled, `/experiments` and `/experiments/...` redirect to `index`,
   `/api/experiments` and below answer 404 `{"success": false, "error": "Experimente sind
   deaktiviert."}`.
4. When enabled (inside `if identity_gate is not None:`): register both blueprints; call
   `identity_gate.allow_bearer_endpoints(<experiments_api endpoint names>)`; build
   `experiments_index_client = KnovasAPIClient(config)` if `index_enabled` (no broker!), a
   `RunnerClient` if `runner_url`; start one `JobWorker` daemon thread per process if
   `worker_enabled` (connect: `PLATFORM_DB_DSN` if set else `identity.db.connect()`), handlers
   from `tasks.build_handlers`.
5. `/api/search`: right after `results = api_client.search_documents(...)` (and for test
   fixtures too) call `results, experiment_hits = experiments_search.split(results)` (always, even
   when disabled, so stale experiment documents never show up as missing files); after
   `_supplement_results_from_enrichment_filenames`, merge
   `experiments_search.rows(experiment_hits)` passed through the same
   `_apply_search_refinement({'results': rows}, query, filters, config)` into `final_results`,
   re-sorted by score (descending), truncated to `limit`.
6. `_prevent_stale_ui_assets`: `/experiments` and below are no-store.
7. `CSRF gate`: endpoints starting with `experiments_api.` are exempt (bearer only).
8. `webauth.IdentityGate`: new attribute `bearer_endpoints: frozenset` (default empty) and method
   `allow_bearer_endpoints(names)`; `guard()` returns None for them.
9. `admin.ASSIGNABLE_ROLES` gains `experimenter`, `experiments_manager`.

## 14. UI (part F)

Look: the existing Platform design tokens (style.css `:root`), IBM Plex, cards with
`--radius-lg`, no new colours except each domain's own colour as a small dot. German copy.
Vanilla JS, no build, no external libraries; charts are inline SVG built in JS. Every page
includes `_sidebar.html` with `active_nav='experiments'`.

Sidebar (`_sidebar.html`): after the Cortex item, `{% if experiments_nav is defined and
experiments_nav %}` an item "Experimente" (icon: flask, Lucide path
`M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3M7 15h10`), active when
`active_nav == 'experiments'`.

`experiments_common.js` exposes `window.KX` with: `csrfToken()`, `api(method, url, body)`
(JSON, X-CSRF-Token, 401 -> `/login`, returns parsed body or throws `Error` with `.fields`),
`upload(url, file)`, `esc(s)`, `fmtDate(iso)`, `fmtNumber(x, decimals)` (de-CH: `'` thousands,
`.` decimal is Swiss convention — use `Intl.NumberFormat('de-CH')`), `fmtEstimate(kind, x, unit)`
(proportion as percent), `toast(message, kind)`, `dialog(options)` (native `<dialog>`),
`verdictChip(verdict)`, `statusChip(label)`, `domainDot(color)`, `intervalBar(estimate, lo, hi,
{direction})` (SVG, zero line, point and interval), `renderMarkdown(md)` (uses
`window.KnovasMarkdown.render` if loaded — include `markdown.js`), `renderFieldInput(field,
value)` / `readFieldInput(field, el)` for type-driven forms.

`experiments_list.html` + `experiments_list.js` (`/experiments`):
- Header: h1 "Experimente", subtitle "Hypothesen, Messwerte und Entscheidungen – über alle
  Bereiche."; buttons "Neues Experiment" (primary) and "Verwaltung" (link to
  `/experiments/verwaltung`).
- Search box "In Knovas suchen …" (calls `/api/experiments/search`, shows hits with snippet and
  a note "Gefunden mit Knovas" or "Datenbanksuche (Knovas nicht erreichbar)").
- Domain chips (all + each domain with colour dot and count), status select, "Archivierte
  zeigen" checkbox, text filter (debounced, server `q`).
- Table: Schlüssel, Experiment (title + type), Bereich, Status, Primäre Metrik, Letztes Ergebnis
  (headline + verdict chip), Aktualisiert; rows link to the detail page; "Mehr laden" with
  keyset cursor.
- Empty state when no domain exists: "Noch keine Bereiche." with buttons to install packs
  (managers) or a hint to ask a manager.
- "Neues Experiment" dialog: Bereich select, Typ select (types of that domain + global), Titel,
  Hypothese (textarea), then the type's fields rendered from its definition; submit -> POST, then
  navigate to the new experiment.

`experiments_detail.html` + `experiments_detail.js` (`/experiments/<KEY>`): loads
`GET /api/experiments/<KEY>` and renders:
- Header: KEY, domain dot + name, type name + version, status chip, title (editable inline),
  archive toggle, "Neu indexieren", index state ("In Knovas: aktuell / ausstehend / Fehler: …
  / aus").
- Lifecycle strip: states of the type in order, current highlighted; buttons for
  `transitions` — disabled ones show their `missing` messages as a tooltip and a list.
- Hypothesis and description (editable, markdown preview), type fields (form, save with
  row_version; 409 -> "Das Experiment wurde inzwischen geändert." + reload button).
- Varianten (table editable: key, name, description, control radio, allocation) and Metriken
  (assign metric with role; guardrail op/value).
- Messwerte: per metric a card: aggregates table per variant (n, estimate formatted, rows) and
  an interval chart of the newest comparison; forms: "Messwert erfassen" (metric, variant,
  value/count/denominator labels from the kind), "CSV importieren" (file input; show the CSV
  format help), list of batches with "Rückgängig" (delete batch).
- Läufe (runs table with params/commit/metrics; "Lauf erfassen" dialog).
- Auswertungen: "Auswertung starten" (evaluator select filtered by kind + metric select + params
  JSON textarea), "Alle Auswertungen des Typs ausführen"; result cards: evaluator, metric,
  verdict chip, headline, comparisons with `intervalBar`, variants table, table, warnings,
  summary markdown, logs (collapsible, custom only); queued/running evaluations poll every 3 s.
- Notizen (list + add form with kind select; delete own).
- Entscheidung (form: verdict select, Begründung, Erkenntnis — required when the type says so;
  history list).
- Aktivität (from `/activity`).

`experiments_manage.html` + `experiments_manage.js` (`/experiments/verwaltung`), tabs:
- "Bereiche" (managers): list, create (key, name, prefix, colour, description), edit, archive,
  export YAML (download), import YAML (textarea), install shipped packs ("Pakete").
- "Typen" (managers): list per domain; editor with a textarea for the definition as YAML/JSON,
  "Prüfen" (client shows server validation errors per field), "Als neue Version speichern",
  version list; a read-only preview of fields/states/transitions.
- "Metriken" (managers): list/create/edit (key, name, kind, unit, direction, description,
  levels for ordinal/categorical).
- "Auswerter" (managers): list incl. builtins; create/edit python/julia evaluators with a code
  textarea (monospace, tab inserts spaces), input kinds, params schema; "Testen" against an
  experiment+metric shows output and logs; runner status line.
- "Zugangsschlüssel" (every viewer): own tokens, create (name, expiry days) shows the token once
  with copy button and a usage example (curl + Python SDK), revoke.
- "Index" (managers): counts, job counts, recent failures, "Alles neu indexieren", setting
  "Experimente in der normalen Suche zeigen" (checkbox, PUT settings).

`app.js` (search cards): in `createDocumentCard`, when `result.result_kind === 'experiment'`,
render a card with a flask icon, metaline "Experiment · <domain_name> · <status_label>", the
title, the snippet, no file badge; clicking or Enter navigates to `result.app_url`
(`window.location.assign`), not the preview. Keep method names unique (test_frontend_static).

## 15. Runner (part G)

`KnovasPlatform/components/experiments_runner/`:

- `Dockerfile`: `FROM julia:1.11.9-bookworm`; apt `python3 python3-venv` ; venv `/opt/venv` with
  `requirements.txt` (numpy, scipy, pandas, statsmodels, pinned); Julia depot `/opt/julia-depot`
  with `Project.toml` packages (JSON3, Distributions, HypothesisTests, StatsBase, DataFrames)
  added and precompiled at build; user `10001`; `ENV JULIA_DEPOT_PATH=/tmp/julia-depot:/opt/julia-depot:`;
  `CMD ["python3", "/app/runner.py"]`. The harnesses must not need any Julia package: the Julia
  harness carries its own small JSON reader/writer, so the protocol works even if a package
  fails to install.
- `runner.py` (stdlib only): `ThreadingHTTPServer` on `RUNNER_PORT` (8090). `GET /health` ->
  `{"ok": true, "languages": {"python": "...", "julia": "..."}, "busy": n, "max_concurrent": n}`.
  `POST /v1/run` body `{"language": "python"|"julia", "code": str (<= 200000), "data": object,
  "timeout_seconds": int}` (body <= 64 MB) -> 200 `{"ok": bool, "output": object|null,
  "error": str|null, "logs": str, "duration_ms": int}`; 400 bad request; 503 when
  `RUNNER_MAX_CONCURRENT` (default 2) jobs are running and a slot does not free within 10 s.
  Each job: fresh temp dir under `/tmp`, code and input written there, child process
  (`python3 -I -S harness.py` with the venv python / `julia --startup-file=no --history-file=no
  -O1 harness.jl`), `start_new_session=True`, scrubbed environment (PATH, HOME=job dir, LANG,
  JULIA_DEPOT_PATH, OPENBLAS/MKL/OMP threads = 1), rlimits in the child (CPU seconds = timeout +
  5, file size 64 MB, open files 256, core 0; address space 4 GB for Python only), wall-clock
  timeout -> kill the process group, stdout+stderr captured to at most 64 KB, output read from
  `output.json` (<= 8 MB), temp dir removed. The user's code must define `evaluate(data)`
  (Python) / `evaluate(data)` (Julia, `data` a `Dict{String,Any}`) returning a dict/Dict.
- `tests/` (pytest, stdlib + pytest): python harness happy path, exception -> ok false with
  traceback in logs, timeout kills, output too large, bad JSON output, non-dict return; Julia
  tests skip when `julia` is not on PATH.

`docker-compose.yml`: service `experiments-runner` with `profiles: [experiments]`, build
context `./KnovasPlatform/components/experiments_runner`, image `knovas-experiments-runner:0.1.0`,
`expose: ["8090"]`, `user: "10001:10001"`, `read_only: true`, `tmpfs: ["/tmp:size=1g,mode=1777"]`,
`cap_drop: [ALL]`, `security_opt: ["no-new-privileges:true"]`, `pids_limit: 256`,
`mem_limit: ${EXPERIMENTS_RUNNER_MEMORY:-2g}`, `cpus: ${EXPERIMENTS_RUNNER_CPUS:-2}`,
environment `RUNNER_MAX_CONCURRENT`, `RUNNER_MAX_SECONDS`, healthcheck via python urllib on
`/health`, `restart: unless-stopped`, `networks: [experiments-sandbox]`. New network
`experiments-sandbox: {internal: true}`; docbridge-web joins it in addition to knovas-internal.
No `container_name`, no `${X:?}` interpolation, no plain `depends_on` on the profiled service.

CI (`.github/workflows/ci.yml`): run the runner's pytest (Python 3.11; Julia tests skip);
`docker compose --env-file scripts/lib/fixtures/knovas.env.fixture config --quiet` must still
pass; add `docker compose --env-file scripts/lib/fixtures/knovas.env.fixture --profile
experiments build experiments-runner`.

`scripts/doctor.sh`: section "Experimente": off -> ok "ausgeschaltet (EXPERIMENTS_ENABLED=false)"
and skip; on -> report identity requirement, tables present (probe), job queue counts and dead
jobs (WARN if any), index states (WARN on errors), runner reachability if COMPOSE_PROFILES
contains experiments (WARN if configured but unreachable). A default-off flag needs its own
helper (the existing `flag_on` treats empty as on).

`knovas.env.example`: commented block for EXPERIMENTS_ENABLED, EXPERIMENTS_ACCESS_GROUPS,
EXPERIMENTS_INDEX_PER_MINUTE, COMPOSE_PROFILES=experiments with EXPERIMENTS_RUNNER_URL,
EXPERIMENTS_RUNNER_MEMORY/CPUS. `docs/client/README.md`: rows in the settings table.

## 16. SDK and docs (part H)

- `KnovasPlatform/experiments-sdk/python/knovas_experiments.py`: stdlib only (urllib, json,
  ssl). `Client(base_url, token, *, verify=True|cafile, timeout=30)` with `ping()`,
  `experiment(key)`, `log_run(key, *, name=None, variant=None, params=None, metrics=None,
  environment=None, commit=None, status="finished", started_at=None, ended_at=None, note=None)`,
  `add_measurements(key, rows)`, `add_note(key, body, kind="note")`, and a context manager
  `run(key, variant=..., name=..., params=...)` collecting `log(**metrics)` and posting on exit
  (status failed on exception). Env fallbacks `KNOVAS_URL`, `KNOVAS_EXPERIMENTS_TOKEN`.
  README with CI example (GitHub Actions step).
- `KnovasPlatform/experiments-sdk/julia/KnovasExperiments.jl`: a module using HTTP.jl and JSON3
  with `log_run`, `add_measurements`, `add_note`; README.
- `KnovasPlatform/docs/features/experiments.md` (German): what it is, switching on (env, roles,
  profile for Python/Julia), domains/types/metrics/evaluators, measurement kinds table,
  CSV format, evaluations, decisions, search with Knovas, API tokens and SDK, operations (index,
  doctor, CLI, backup), security model of the runner, limits.
- `RELEASE_NOTES.md`: a German `### Experimente` section under `## KnovasPlatform`.
- Design doc: add "Resolved decisions" (section 0 of this plan) and correct the stack/search
  statements (Flask module, Knovas instead of pgvector, platform-db instead of TimescaleDB).

## 17. Tests (every part)

- Pure units: stats (reference values from scipy, tolerance 1e-6 relative for closed forms,
  0.01 absolute for Monte Carlo), kinds, schema, packs (all shipped packs validate), evaluators
  (verdicts incl. direction lower, too little data, sanitize limits), indexer (markdown,
  parts, document_for, upload calls on a fake client), search (split/rows), runner client
  (fake HTTP server).
- Postgres: store (key allocation under concurrency, aggregates per kind, tokens), jobs (claim
  with SKIP LOCKED from two connections, dedupe, retry/defer/dead, lease expiry, rate slot),
  service (create -> measurements -> evaluation -> transition gates -> decision -> snapshot;
  permissions; CSV import; delete batch; pack install idempotent).
- Web (Postgres, `tests/conftest.py` personas; grant roles with `identity_repo.grant_role`):
  switch off (nav hidden, page redirect, API 404, search strips experiment hits), role gate
  (member -> 404 page and API, experimenter ok, manager-only -> 403), CSRF (403 without header),
  `access_groups` body -> 400, token API (401 without/invalid token, cookie alone refused,
  revoked token refused, run logging works), search integration with a fake Knovas client
  returning experiment pointers.
