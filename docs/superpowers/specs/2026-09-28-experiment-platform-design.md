# Design: Experimente — one place for every experiment

Date: 2026-09-28 (revised the same day: open decisions resolved, see "Decisions")
Status: accepted; implemented as the Experiments module of the Knovas Platform
Plan (the contract every part codes against): [../plans/2026-09-28-experiments-module.md](../plans/2026-09-28-experiments-module.md)
Operator and user documentation: [KnovasPlatform/docs/features/experiments.md](../../../KnovasPlatform/docs/features/experiments.md)
Mockup: [Experiment Hub design canvas](https://claude.ai/artifact/QC75P7tQECPYi2qLfJ4oDH) (9 boards: 7 screens, 2 plan boards)

The proposal ran under the working title "Assay". It ships as the module
**Experimente** (code name `experiments`) inside the Knovas Platform, not as a
product of its own.

## Context

Experiments are spread across tools that do not know about each other: ML and search
evaluations sit in notebooks and CI logs, A/B tests in the ad platform, sales trials in CRM
reports and spreadsheets. Nobody can answer "what have we already tried, and what did we
learn?" in one place, and an evaluation written for one experiment is rarely reused on the next.

The goal is one system that:

1. Tracks any kind of experiment — engineering, marketing, sales, product, and domains added
   later — each with its own fields, lifecycle and goals.
2. Tracks the measurements those experiments produce: rates, means, durations, money, ratios,
   ordinal scores and categories, plus text (notes, interviews) next to the numbers.
3. Runs evaluations inside the system, built in and in user-written Python and Julia, so
   results are computed the same way every time and can be re-run.
4. Is configurable enough that a new domain needs configuration, not a release.
5. Makes everything it holds — hypotheses, results, decisions, learnings — findable with
   the Knovas search people already use.

Two constraints shaped every decision below. The module lives in KnovasComponents and must
not clutter the standard search view for people who never run experiments; and it must be
possible to switch it off entirely.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Where it runs | A module of the Platform web app: Flask blueprints `experiments` (pages, session API) and `experiments_api` (machine API), Jinja templates, vanilla JS, charts as inline SVG. Same login, roles, audit log, deployment and look as the rest of the Platform. | A separate service (the proposal's FastAPI + React stack) would have needed its own identity, audit, deployment and design system, and would still have had to reach into the Platform for users and search. |
| Hidden | Visible only with role `experimenter`, `experiments_manager` or `admin`. Everyone else gets no navigation item, 404 on every module route, and never an experiment hit or pointer in a search response. Each experimenter can switch experiment hits in the normal search off for themselves; managers can for everyone. | The standard search view stays exactly as it is for everyone else. |
| Off switch | `EXPERIMENTS_ENABLED`, default **false**. Off: blueprints not registered, a gate answers redirect (pages) or 404 (API) on module paths, no worker threads, experiment hits stripped from every search response. Data stays in the database. | Required; and a firm that never wants the module must not pay for it. |
| Storage | platform-db PostgreSQL (migration `0003_experiments.sql`). Measurements as sufficient statistics (value sum, count, sum of squares, denominator) with a covering index for index-only aggregation; every insert is a batch in `exp_batches` and can be undone as one. Designed for 10^8 rows; declarative partitioning of `exp_measurements` is the documented next step. **No TimescaleDB, no ClickHouse.** | platform-db already exists, is backed up and holds users, roles and the audit log. Experiment data is small by warehouse standards; sufficient statistics keep aggregates exact and cheap without a time-series engine. |
| Search | One Knovas document per experiment (pointer `experiments/<domain>/<KEY>`), re-uploaded after changes (debounced, rate-limited). Hits are recognised by the pointer prefix and rendered from the Platform database. **Knovas search instead of pgvector**: semantic search over hypotheses, notes, decisions and learnings is what Knovas already does. | No second embedding store to run and keep in sync; people search where they already search. |
| Knovas-side visibility | Uploads carry `EXPERIMENTS_ACCESS_GROUPS`. With no group configured the indexer refuses to upload (fail closed) unless `EXPERIMENTS_INDEX_UNRESTRICTED=true`, documented for use with a folder rule on the prefix. | An unrestricted document would be visible to the whole tenant. The Platform additionally strips experiment hits for everyone without a viewing role. |
| Background work | A job queue in PostgreSQL (`exp_jobs`: `FOR UPDATE SKIP LOCKED`, leases, fencing, priorities, retries with backoff, coalescing by dedupe key, dead letters with `on_dead` hooks). Two daemon threads per gunicorn process: one for index, unindex and pipeline, one for evaluations. A shared rate slot keeps Knovas uploads under `EXPERIMENTS_INDEX_PER_MINUTE` across all workers. | No broker to operate; survives restarts; works with any number of gunicorn workers. |
| Indexing identity | A second `KnovasAPIClient` without principal broker (like RemoteController's), built by `experiments.indexer.make_index_client(config)`. | Uploads happen in the background, without a signed-in person. |
| Python and Julia | Built-in statistics run in-process, pure Python. **User evaluators run only in `experiments-runner`**: its own image (Python venv with numpy, scipy, pandas, statsmodels; Julia 1.11 with JSON3, Distributions, HypothesisTests, StatsBase, DataFrames), **no network at all** (`network_mode: none`), reached **over a unix socket** on a shared volume, non-root, read-only root filesystem, no capabilities, no-new-privileges, CPU/memory/pid limits, a subprocess per job with rlimits, a wall-clock kill and removal of stray processes, no secrets mounted. Optional compose profile `experiments`. | Containment without a VM or gVisor dependency the deployments do not have. The remaining risks are accepted and written down (feature doc, "Bewusst getragene Risiken"). |
| Machine access (CI) | Personal access tokens (`exp_api_tokens`, stored as SHA-256, expiry required, max 365 days) on `/api/experiments/v1/*` with `Authorization: Bearer`. A token acts as its owner with the owner's roles at request time. Python SDK (standard library only) and Julia client. | CI logs runs with per-query rows, triggers the pipeline and reads verdicts, without a browser session. |
| Configurability | Domains, experiment types (versioned documents: fields, states, gated transitions, variant rules, default metrics, evaluation pipeline with scope), metrics (kind, unit, direction, bounds, levels) and evaluators are data, editable in the UI and exportable/importable as YAML packs (config-as-code by export, not by a git sync). | A new domain is configuration. Packs move configuration between installations and into git for review. |
| Name | UI label **Experimente**, code name `experiments`. | It is a module, not a product. |
| First use | Knovas' own experiments in four packs, usable at once: search quality and engineering (runs from CI), LinkedIn and marketing tests, sales outreach, product and usability. | The data exists today; engineering from CI needs no manual entry at all. |

## The core idea: a small kernel, everything else is configuration

The kernel only knows domain-free concepts: experiment, variant, run, measurement,
evaluation, decision, learning, note. It has no tables or code for "campaign", "commit" or
"deal". Everything domain-specific is **configuration**: experiment types, metrics,
evaluators and domains, stored as versioned data in platform-db and moved around as YAML
packs.

The proposal also foresaw plugins for field types, measurement kinds, connectors, views,
automation actions and AI skills. The first version keeps the extension points that pay
for themselves and fixes the rest in code:

- **Evaluators** are the plugin point: built-in ones in the Platform, user-written ones in
  Python or Julia in the runner, both with a declared contract (accepted kinds, parameter
  schema, input and output shapes) that the Platform validates.
- **Field types** (8) and **measurement kinds** (8) are fixed in code; a new kind is a code
  change with tests.
- **Connectors** and **automations** are not part of the first version: data arrives by
  form, CSV or the API, and the only automation is the evaluation pipeline that runs after
  new data.

Principles, and how the first version keeps them:

1. **The kernel knows no domain.** Domains, types and metrics are rows, not code.
2. **Configuration is code** — versioned (type and evaluator versions; experiments pin
   their type version), reviewable (YAML export into git), diffable.
3. **Every result can be re-run.** An evaluation stores the evaluator version, parameters,
   scope and an input digest; the pipeline reuses an evaluation whose inputs did not change.
   The runner image is versioned; per-evaluation lockfiles are not kept.
4. **Contracts, not trust.** Evaluator inputs and outputs have fixed shapes; every output is
   validated, clipped and escaped before it is stored, shown or indexed.
5. **One record of what happened**: the audit log (who did what, never content) and the
   job queue (what happens next). There is no separate event bus.

## Concepts

| Concept | What it is | Kind |
|---|---|---|
| Domain | An area with its own key, name, colour and id prefix (`MKT` → `MKT-58`): Engineering, Marketing, Sales, Product, or your own. Installed from a pack or created in the UI. | config |
| Pack | A domain with its metrics, types and custom evaluators as one YAML document. Shipped (`core`, `engineering`, `marketing`, `sales`, `product`) or exported. | config |
| Experiment type | The template: fields, states with phases, transitions with requirements and roles, variant rules, metric slots (primary, secondary, guardrail), evaluation pipeline with scope, decision rule. Versioned; experiments pin their version. | config |
| Metric | A reusable definition: kind, unit, direction ("higher is better"), decimals, bounds, levels. Its kind is frozen once measurements exist. | config |
| Measurement kind | The shape of a measurement (proportion, mean, count, duration, currency, ratio, ordinal, categorical). Decides how a row is read, the estimate and the default evaluators. | code |
| Experiment | One instance of a type: hypothesis, field values, tags, variants, metrics, owner, status. | record |
| Variant | One arm: key, name, whether it is the control, allocation. | record |
| Run | One execution that produces measurements: a CI evaluation, a campaign week. Parameters, environment, commit, status. | record |
| Measurement | One row of sufficient statistics: metric, variant, run, value, count, denominator, sum of squares, time, dimensions, batch. | record |
| Evaluator | Built-in function or versioned Python/Julia code with a contract: accepted kinds, parameter schema, output shape. | config + code |
| Evaluation | One evaluator run on one metric of one experiment with a scope: output, logs, verdict, headline. | record |
| Decision and learning | The verdict (ship, iterate, stop, inconclusive), its rationale, and a written learning. | record |
| Note | Free text of kind note, observation, interview, feedback, or status (the reason given for a status change). | record |

## Configurability, concretely

An experiment type as it is stored and exported (from the `sales` pack, without the fields' help texts):

```yaml
key: playbook
name: Playbook-Test
definition:
  fields:
    - {key: segment, label: Segment, type: enum,
       options: [Kanzlei klein, Kanzlei mittel, Kanzlei gross, Rechtsabteilung, Sonstiges]}
    - {key: territory, label: Gebiet, type: enum, options: [Schweiz, Deutschland, Österreich, Sonstiges]}
    - {key: sequence, label: Sequenz, type: text}
    - {key: planned_n, label: Geplante Kontakte je Variante, type: integer, min: 1}
  states:
    - {key: draft, label: Entwurf}
    - {key: running, label: Läuft, phase: running}
    - {key: analysis, label: Auswertung}
    - {key: decided, label: Entschieden, phase: decided}
    - {key: stopped, label: Abgebrochen, phase: stopped}
  initial: draft
  transitions:
    - {from: draft, to: running, label: Starten, requires: [hypothesis, primary_metric, "variants:2"]}
    - {from: running, to: analysis, label: Zur Auswertung, requires: [measurements, "n_planned:planned_n"]}
    - {from: analysis, to: decided, label: Entscheiden, requires: [decision]}
    - {from: analysis, to: running, label: Weiterlaufen lassen}
    - {from: "*", to: stopped, label: Abbrechen}
  variants:
    min: 2
    max: 10
    defaults: [{key: A, name: Kontrolle, is_control: true}, {key: B, name: Variante B}]
  metrics:
    - {metric: meeting_rate, role: primary}
    - {metric: reply_rate, role: secondary}
    - {metric: pipeline_value, role: secondary}
    - {metric: unsubscribe_rate, role: guardrail, op: max, value: 0.02}
  evaluation:
    - {evaluator: builtin.describe, metric: all}
    - {evaluator: builtin.bayes_proportion, metric: primary}
    - {evaluator: builtin.two_proportion, metric: primary}
  decision: {require_learning: true}
```

A metric:

```yaml
key: meeting_rate
name: Terminquote
kind: proportion          # value = contacts with a meeting, count = contacts written to
unit: "%"
direction: higher
definition: {decimals: 1}
```

The proposal's richer constructs — type inheritance (`extends`), computed fields
(`formula`), links to external records (`type: link`), free-form gate expressions — are
not in the first version. Gates are a fixed vocabulary (`hypothesis`, `primary_metric`,
`variants:N`, `measurements`, `evaluation`, `decision`, `learning`, `field:<key>`,
`n_planned:<field>`) plus per-transition roles, which covers the lifecycles of the four
packs and keeps every gate explainable in the UI ("Es gibt noch keine Messwerte.").

Versioning rules:

- Experiments pin the type version they were created with; a new version applies to new
  experiments. Saving an identical definition creates no version.
- A metric's kind cannot change once measurements exist; its name, unit, description and
  display settings can, and apply everywhere.
- Evaluators are versioned. Every evaluation records the version it ran with; the next
  evaluation uses the current version, old evaluations keep their results.

YAML and JSON are parsed only through a safe loader that refuses anchors, aliases and
tags, with size limits before parsing.

## Measurement kinds

| Kind | Example | Stored as (per row) | Estimate | Default evaluators |
|---|---|---|---|---|
| Proportion | CTR, meeting rate, task success | successes, trials | Σ successes / Σ trials | describe, Bayes beta-binomial, two-proportion |
| Mean | NDCG@10 per query, SUS per person | value sum, count, sum of squares | Σ values / Σ count | describe, Welch t, paired t |
| Duration | p95 latency per run, time to value | as mean | as mean | describe, Welch t, paired t |
| Currency | pipeline value per contact | as mean | as mean | describe, Welch t |
| Count (rate) | errors per 1000 requests | events, exposure | Σ events / Σ exposure | describe, Poisson rate |
| Ratio | cost per click, cost per lead | numerator, denominator (0 allowed), units | Σ numerator / Σ denominator | describe, delta-method ratio |
| Ordinal | satisfaction 1–5 | level, units at that level | weighted mean plus distribution | describe, Welch t, chi-square |
| Categorical | preferred option | category code, units | distribution | describe, chi-square |

How the proposal's other kinds are covered:

- **Quantile** (p95 latency): one value per run as a duration; the estimate is the mean over
  runs. Raw per-request latencies go in as a duration per request.
- **Ranking** (NDCG, recall): a mean with one row per query and run (`dims.query`), compared
  query by query with the paired t-test over the newest run of each variant.
- **Time series**: every kind is charted over time (day, week, month buckets of the
  observation time); there is no separate time-series kind or interrupted-time-series test.
- **Text notes**: notes of kind interview, observation, feedback, indexed into Knovas with
  the experiment. No automatic theme summary.
- **Files and artifacts**: not stored. Fields of type `url` link to reports elsewhere.
- **Censored durations** (survival): not supported; durations are means.

## Evaluation runtime

- **Built-in evaluators** (describe, two-proportion, Bayes proportion, Welch t, paired t,
  Poisson rate, delta-method ratio, chi-square) are pure Python inside the Platform,
  deterministic, and computed during the request. Multiple comparisons are Holm-corrected.
- **User evaluators** are Python or Julia code with `evaluate(data)` returning the output
  contract. They run only in `experiments-runner`: a separate container without any
  network, reached by the Platform over a unix socket on a shared volume. Each job is its own
  process in its own session with rlimits (CPU time, address space, file size, open files,
  processes), a fresh 0700 directory, a scrubbed environment, a wall-clock kill of the
  process group and removal of every process a job leaves behind. The container is non-root
  with a read-only root filesystem, no capabilities, no-new-privileges, and CPU, memory and
  pid limits; no secret is mounted.
- **Environment**: one versioned image (`knovas-experiments-runner`) with pinned Python
  packages and the Julia packages installed at build time. There are no notebooks and no
  Jupyter kernels; evaluator code is written in the UI from a template that sets every
  contract key, and a "Testen" button runs unsaved code against a real experiment.
- **Data access**: the Platform builds the input (experiment, metric, variants, complete
  aggregates, up to 100 000 rows in the evaluation's scope) and sends it with the code; the
  code has no other way to reach data. The output comes back as data, is sanitised and size
  limited, and only then stored, shown and indexed.
- **Scope** decides which rows count: all, the newest finished run per variant, given runs,
  a time window, or a dimension filter.
- **Pipeline**: 30 seconds after new data the type's evaluation list runs as a job; an
  unchanged input reuses the existing evaluation.
- **Same data from outside**: CI and scripts log runs with the SDK:

```python
from knovas_experiments import Client

client = Client()  # KNOVAS_URL, KNOVAS_EXPERIMENTS_TOKEN
with client.run("ENG-142", variant="stemmer-v2", params=cfg, commit=GIT_SHA) as run:
    for query_id, scores in evaluate(index, queries="legal-de-v3").items():
        run.add_row("recall_at_20", scores.recall(20), dims={"query": query_id})
        run.add_row("ndcg_at_10", scores.ndcg(10), dims={"query": query_id})
    run.log(latency_p95_ms=latency.p(95))
verdicts = [e["verdict"] for e in client.evaluate("ENG-142")]
```

```julia
include("KnovasExperiments.jl"); using .KnovasExperiments
client = Client()
log_run(client, "MKT-58"; variant="B", metrics=Dict("ctr" => Dict("value" => 175, "count" => 10714)))
evaluate(client, "MKT-58")
```

## Domain packs shipped first

| Pack | Types | Typical metrics | Data arrives by |
|---|---|---|---|
| Engineering (`ENG`) | Offline evaluation, Performance change, Feature rollout | NDCG@10, Recall@20, MRR, p95 latency, CI minutes, error rate | Python/Julia SDK from CI (GitHub Actions) |
| Marketing (`MKT`) | A/B test, Campaign, Content test | CTR, conversion, demo request rate, bounce rate, cost per click, cost per lead | CSV exports (LinkedIn Campaign Manager, weekly, wide format), manual entry |
| Sales (`SAL`) | Playbook test, Pricing test | reply rate, meeting rate, pilot conversion, win rate, unsubscribe rate, pipeline value, deal value, cycle days | CSV (weekly per variant), manual entry |
| Product (`PRD`) | Usability test, Feature rollout | task success, SUS, time to value, satisfaction, preferred option | manual entry, CSV; interviews as notes |

A global `core` pack provides the generic type "Allgemeine Hypothese" (every new domain
starts with it) and two commented example evaluators, one in Python and one in Julia.
Connectors to LinkedIn Ads, GA4, HubSpot or Salesforce are not part of the first version.

## Search and the learnings library

Every experiment is one Knovas document: title "KEY · title", the hypothesis as description,
a folder path `/Experimente/<domain>/<type>/<KEY title>`, and a Markdown body with
hypothesis, description, fields, variants, metric estimates, the newest evaluation per
evaluator and metric, runs, notes, decisions and learnings. No account names or e-mail
addresses; free text as written. Documents are split into parts of at most 40 000
characters and capped at 400 000 (older evaluations, then runs, then notes are dropped
first).

- The normal search shows experiment hits as their own cards for people with a viewing
  role, rendered from the Platform database (so title, status and domain are always
  current), linking to the experiment page and never to a file.
- The module's own search asks Knovas and the database and merges the hits; without Knovas
  (unreachable, indexing off, or the person lacks the access group) it falls back to the
  database and says so.
- This is the proposal's "learnings library": semantic search across hypotheses, decisions
  and learnings, done by the search engine the firm already runs.

## Architecture

```
 Browser (Jinja pages, vanilla JS)          CI / scripts (Python SDK, Julia client)
        │ session + CSRF                              │ Authorization: Bearer kxp_…
        ▼                                             ▼
 ┌──────────────────────── docbridge-web (Flask, gunicorn) ────────────────────────┐
 │ blueprint experiments (pages, /api/experiments)   blueprint experiments_api (/v1) │
 │                 └──────── ExperimentService (Flask-free) ───────┘                 │
 │ store (SQL)   schema/packs (YAML, validation)   kinds/stats/evaluators (built-in) │
 │ search integration (strip + re-add hits)   job workers (2 threads per process)     │
 └───────┬───────────────────────────────┬────────────────────────────┬──────────────┘
         │ psycopg                        │ HTTPS (mTLS)               │ HTTP over unix socket
         ▼                                ▼                            ▼
   platform-db (PostgreSQL)        Knovas API (index, search)   experiments-runner
   exp_* tables, exp_jobs,                                        (profile experiments,
   audit_log, users, roles                                         network_mode: none)
```

- **Web layer** (`web_interface/experiments_routes.py`): pages and the session API behind
  the Platform's login and CSRF gates; the machine API accepts nothing but a bearer token.
- **Service** (`experiments/service.py`): permissions, validation, audit and job enqueueing
  for every operation; Flask-free, so the CLI and the workers use the same code.
- **Store** (`experiments/store.py`): all SQL; aggregates are computed in SQL from the
  sufficient statistics.
- **Jobs** (`experiments/jobs.py`, `tasks.py`): the PostgreSQL queue and its workers;
  `python -m experiments` offers status, reindex, purge-index, install-pack and a worker for
  operators.
- **Indexer** (`experiments/indexer.py`): renders the Markdown document and uploads it with
  the configured access groups, within the shared rate slot.
- **Runner** (`KnovasPlatform/components/experiments_runner`): standard-library HTTP server on
  a unix socket, one subprocess per job.

Deployment is the existing Docker Compose stack: no new service unless the `experiments`
profile is enabled for the runner, one new named volume for its socket.

## Data model

Migration `KnovasPlatform/components/docbridge_integration/src/identity/migrations/0003_experiments.sql`:

- Configuration: `exp_domains`, `exp_types` + `exp_type_versions`, `exp_metrics`,
  `exp_evaluators` + `exp_evaluator_versions`.
- Records: `exp_experiments` (pinned to a type version; fields as JSONB; optimistic
  concurrency by `row_version`; Knovas index state), `exp_variants`,
  `exp_experiment_metrics` (role, guardrail), `exp_runs`, `exp_batches`, `exp_measurements`
  (insert-only, undone by batch), `exp_notes`, `exp_evaluations`, `exp_decisions`.
- Machine access and operations: `exp_api_tokens`, `exp_jobs`, `exp_rate_slots`,
  `exp_index_documents` (every pointer ever written to Knovas, so deletions can be repeated).
- Users, roles (`experimenter`, `experiments_manager`) and the audit log are the Platform's.

Key relations: a type has versions and experiments; an experiment has variants, metric
assignments, runs, batches, measurements, notes, evaluations and decisions; a measurement
belongs to one batch, one metric, optionally one variant and one run of the same experiment
(composite foreign keys make cross-experiment references impossible); an evaluation
references one evaluator version.

## Delivered and later

Delivered in the first version (the proposal's phases 0–2 in large part, plus the search part
of phase 4): records and lifecycle gates, type and metric registry, four packs, CSV import,
REST API with tokens, Python and Julia clients, built-in evaluators, sandboxed Python and
Julia evaluators, pipeline after new data, experiment pages with charts and verdicts, Knovas
indexing and search, audit trail.

Later, when the need is real:

| Topic | Next step |
|---|---|
| Volume | Declarative partitioning of `exp_measurements` by experiment or time. |
| Connectors | Scheduled imports from LinkedIn Ads, GA4, HubSpot; write-back (pause a variant). |
| Automations | Rules beyond the pipeline: notifications, guardrail actions. |
| Statistics | Sequential tests, CUPED, SRM checks, meta-analysis across experiments. |
| Workbench | Notebooks on live experiment data, "publish as evaluator". |
| Permissions | Rights per domain or experiment. |
| Intelligence | AI-drafted summaries and learnings (human-approved), design help beyond the sample-size calculator. |
| Isolation | A stronger sandbox runtime (gVisor, microVMs) where the deployment offers one. |

## Mockup boards

The mockup shows the original proposal. Boards 04 (workbench with notebooks) and 07 (data
sources and automations) describe later work; the others match the first version in
substance, with the Platform's own look.

| Board | Shows |
|---|---|
| 01 Portfolio | All experiments across domains; domain filter, stage, effect with interval, evidence, guardrail alerts, decisions pending, latest learnings |
| 02 Experiment — A/B test | Hypothesis, lifecycle with gates, lift chart with hover, measurements table by role, evaluator verdict and decision rule progress, guardrails, type-defined details |
| 03 Experiment — offline evaluation | Runs logged from CI with parameters, commit and environment; recall by document class; SDK snippet |
| 04 Workbench | Python / Julia switch, notebook cells on live experiment data, posterior chart, evaluator contract, sandbox, tests, publish |
| 05 Type builder | Types grouped by pack, fields, lifecycle gates, metric slots, evaluation pipeline, synced YAML |
| 06 Metric registry | Measurement kinds, registry, metric definition, sample-size calculator, data checks |
| 07 Data sources and automations | Connectors, automation rules, selected rule as a flow |
| 08 Architecture | Layers and principles (superseded by "Architecture" above) |
| 09 Data model and roadmap | Entities and relations, phases (superseded by "Data model" and "Delivered and later") |

All numbers, names and results in the mockup are illustrative.
