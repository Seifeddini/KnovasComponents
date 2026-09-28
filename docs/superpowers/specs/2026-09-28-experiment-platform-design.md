# Design: Assay — one platform for every experiment

Date: 2026-09-28
Status: proposal, for review
Mockup: [Experiment Hub design canvas](https://claude.ai/artifact/QC75P7tQECPYi2qLfJ4oDH) (9 boards: 7 screens, 2 plan boards)

"Assay" is a working title.

## Context

Experiments are spread across tools that do not know about each other: ML and search
evaluations sit in notebooks and CI logs, A/B tests in the ad platform, sales trials in CRM
reports and spreadsheets. Nobody can answer "what have we already tried, and what did we
learn?" in one place, and an evaluation written for one experiment is rarely reused on the next.

The goal is one system that:

1. Tracks any kind of experiment — engineering, marketing, sales, product, and domains added
   later — each with its own fields, lifecycle and goals.
2. Tracks any kind of measurement: rates, means, quantiles, rankings, durations, money,
   ordinal scores, text notes, files.
3. Runs evaluations inside the system, in Python and Julia, so results are computed the same
   way every time and can be re-run.
4. Is modular and configurable enough that a new domain needs configuration, not a release.

## The core idea: a small kernel, everything else is configuration or a plugin

The kernel only knows domain-free concepts: experiment, variant, run, measurement,
evaluation, decision, learning. It has no tables or code for "campaign", "commit" or "deal".
Everything domain-specific arrives in one of two ways:

- **Configuration documents**: experiment types, metric definitions, evaluator settings,
  views, automations and domain packs. These are versioned YAML/JSON documents. The UI and
  git edit the same file (a config repo, `assay-config`), and publishing a change opens a pull
  request.
- **Plugins** with declared contracts: field types, measurement kinds, connectors,
  evaluators, views and widgets, automation actions, lifecycle gates, AI skills. The kernel
  validates a plugin's inputs and outputs before running it.

Five principles follow from this, and every design decision below should be checked against
them:

1. The kernel knows no domain.
2. Configuration is code (versioned, reviewable, diffable).
3. Every result can be re-run: it stores its data snapshot, code version and environment lock.
4. Contracts, not trust: plugins declare inputs and outputs; the kernel validates them.
5. One event stream: automations, audit, webhooks and the AI layer read the same events.

## Concepts

| Concept | What it is | Kind |
|---|---|---|
| Domain pack | A bundle of types, metrics, evaluators, views and connector mappings for one area (Engineering, Marketing, Sales, Product, or your own). Installable, forkable, upgradable. | config |
| Experiment type | The template: field schema, lifecycle states and gates, metric slots (primary, secondary, guardrail), evaluation pipeline, decision rule, default views. Types can extend each other (`extends: core.hypothesis`). | config |
| Metric definition | A reusable definition of one measurement: kind, how it is computed from source data, unit, direction ("higher is better"), segments, power settings. | config |
| Measurement kind | The shape of a measurement (proportion, mean, quantile, …). Decides storage, default charts and default statistical tests. | plugin |
| Experiment | One instance of a type: hypothesis, field values, variants, owner, state, links. Pinned to a type version. | record |
| Variant | One arm: key, whether it is the control, configuration, allocation. | record |
| Run | One execution that produces measurements: a CI evaluation, a campaign flight, a sales cohort. Stores parameters, commit, environment lock and source. | record |
| Measurement | One observed value: metric@version, value, unit, timestamp, dimensions, source. Stored in a time-series table. | record |
| Evaluator | Versioned Python, Julia or SQL code with a typed contract: which metric kinds it accepts, its parameters, its outputs. | config + code |
| Evaluation | One evaluator run on one experiment: input snapshot, outputs, logs. | record |
| Decision and learning | The verdict (ship, iterate, stop), its rationale, and a short written learning linked back to the hypothesis. | record |
| Connector | A plugin that brings data in (and optionally writes back, e.g. to pause a variant): auth, schema mapping, schedule. | plugin + config |
| Automation | "When … if … then …" rules over the event stream. | config |

## Configurability, concretely

An experiment type as it lives in the config repo (the type builder board shows the same
file side by side with the visual editor):

```yaml
kind: ExperimentType
id: sales.playbook-trial
version: 4
extends: core.hypothesis
pack: sales@1.3

fields:
  - key: segment
    type: enum
    options: [SMB, Mid-market, Enterprise]
    required: true
  - key: sequence
    type: link
    target: hubspot.sequence
  - key: territory
    type: enum
    options: [DACH, Benelux, Nordics]
  - key: planned_n
    type: number
    formula: power(metrics.primary)

lifecycle:
  states: [draft, review, running, analysis, decided]
  gates:
    review:   { approve: sales-lead }
    running:  { set: [metrics.primary, planned_n] }
    analysis: "n_per_arm >= planned_n or days >= 42"
    decided:  { set: [verdict, learning] }

metrics:
  primary: meeting_rate
  secondary: [reply_rate, pipeline_value]
  guardrails:
    - { metric: unsubscribe_rate, max: 0.02 }

evaluation:
  - use: two-proportion@1.4     # python
  - use: sequential-bound@0.9   # julia
decide:
  ship_if: "p < 0.05 and guardrails.ok"
```

A metric definition:

```yaml
id: meeting_rate
kind: proportion
numerator:   hubspot.meeting_booked(within: 14d)
denominator: hubspot.enrolled(unique: contact)
direction: higher
segments: [segment, territory, rep, firm_size]
```

Versioning rules:

- Experiments pin the type version they were created with; a new type version applies to new
  experiments, and existing ones can be migrated explicitly.
- Measurements pin the metric version. Changing a metric definition creates a new version and
  (via an automation) recomputes affected evaluations, keeping old values in history.
- Evaluators are pinned per experiment; publishing `bayes-ab 2.2` leaves experiments on `2.1`
  until they are migrated.

## Measurement kinds

| Kind | Example | Stored as | Default view | Default evaluators |
|---|---|---|---|---|
| Proportion | CTR, meeting rate | successes, trials per unit | lift with interval | two-proportion, Bayesian A/B |
| Mean | session length | value per unit | lift with interval | Welch t, CUPED |
| Count / rate | errors per 1k requests | count, exposure | rate over time | Poisson rate |
| Quantile | p95 latency | raw values or sketches | box / quantile plot | bootstrap quantile |
| Duration | time to first open | time, censoring flag | survival curve | Kaplan–Meier, log-rank |
| Currency | cost per lead | amount, currency | trimmed mean | trimmed mean, bootstrap |
| Ratio | revenue per session | numerator and denominator sums | ratio with interval | delta method |
| Ranking | NDCG@10, Recall@20 | per-query scores | per-class dumbbell | paired bootstrap |
| Ordinal | SUS, Likert, task success | level per unit | stacked distribution | ordinal regression |
| Categorical | chosen plan | label per unit | share per label | chi-square |
| Time series | weekly signups | value per timestamp | line with events | interrupted time series |
| Text notes | interview notes, comments | text + tags | note list, themes | AI theme summary (human-reviewed) |
| File / artifact | reports, model weights | object-store URI + hash | preview | none |

New kinds are plugins: a schema for the stored value, views, default evaluators, and test
fixtures.

## Evaluation runtime: Python and Julia inside the system

- **Kernels.** Evaluators and notebooks run on the Jupyter kernel protocol: IPython for
  Python, IJulia for Julia, plus a SQL runner against the measurement store or a connected
  warehouse.
- **Sandboxes.** Each evaluation runs in its own container with CPU, memory, time and network
  limits (no network by default). Isolation choice (gVisor containers vs. microVMs) is an open
  decision.
- **Environments.** Versioned images (`python-stats`, `julia-stats`) plus a lockfile per
  evaluator (`uv.lock`, `Manifest.toml`). The image digest and lockfile hash are stored with
  every evaluation.
- **Data access.** An SDK inside the kernel gives read-only access to the experiment's
  measurements; results are written through `assay.output(...)` and must match the
  evaluator's declared outputs.
- **From notebook to evaluator.** Exploratory work happens in a notebook attached to an
  experiment. "Publish as evaluator" turns it into versioned code with a contract (inputs:
  metric kind, grouping; parameters; outputs: headline number, distribution, verdict), tests
  (A/A data gives "unclear", a planted effect is recovered), and triggers (schedule, new data).
- **Same SDK outside.** Code logs runs from anywhere with the same SDK:

```python
import assay

with assay.run("ENG-142", variant="stemmer-v2", params=cfg, commit=GIT_SHA) as run:
    res = evaluate(index, queries="legal-de-v3")
    run.log(recall_20=res.recall(20), ndcg_10=res.ndcg(10), latency_p95_ms=res.latency.p(95))
    run.artifact("index/stats.json")
```

```julia
using Assay
exp = experiment("MKT-058")
df  = measurements(exp, "ctr"; by = :variant)
```

## Domain packs shipped first

| Pack | Types | Typical metrics | Connectors |
|---|---|---|---|
| Engineering | Offline evaluation, Performance change, Feature-flag rollout | NDCG, recall, latency quantiles, CI time, error rate | Python/Julia SDK, GitHub Actions, SQL |
| Marketing | A/B test, Campaign flight, Content test | CTR, cost per lead, conversion, bounce rate | LinkedIn Ads, GA4, HubSpot |
| Sales | Playbook trial, Pricing test | reply rate, meeting rate, pipeline value, win rate | HubSpot, Salesforce, Stripe |
| Product | Usability study, Feature rollout | task success, SUS, time to value, retention | product events (Postgres), manual entry |

A generic `core.hypothesis` type works for anything else (ops, hiring, research) until a pack
exists.

## Intelligence layer

- **Design help**: from a hypothesis, suggest a type, primary and guardrail metrics, and a
  sample size from the metric's baseline and power settings.
- **Learnings library**: every decision produces a written learning; semantic search across
  learnings and hypotheses ("have we tested outcome-focused ad copy before?").
- **Summaries**: AI-drafted result summaries and learnings that the owner edits and approves;
  always with links to the numbers they came from.
- **Checks**: sample-ratio mismatch, stale data, anomalies in guardrails.
- **Meta-analysis**: pool effects across related experiments.
- The AI layer is an MCP server and a set of plugins over the same API, so it can be swapped or
  turned off.

## Architecture

Layers, top to bottom (board 08):

1. Interfaces: web app, Python SDK, Julia SDK, CLI, REST and GraphQL API, webhooks, MCP server.
2. Domain packs (configuration in git).
3. Extension points (plugin contracts).
4. Kernel: schema registry, records, lifecycle engine, event bus, scheduler, access and audit,
   plugin host, search.
5. Compute: Python and Julia kernels, SQL runner, images and lockfiles, job queue.
6. Storage: PostgreSQL with JSONB for type-defined fields, TimescaleDB for measurements,
   object storage for artifacts and notebooks, pgvector for learning search, a git repo for
   configuration.

Proposed stack: Python (FastAPI) for API and kernel, React and TypeScript for the web app,
Vega-Lite for charts, Jupyter kernel protocol for evaluation. Deployment as Docker Compose,
like the other Knovas components, with a Helm chart later.

## Data model

Board 09 draws it. Configuration entities (versioned documents): DomainPack, ExperimentType,
MetricDefinition, Evaluator, Environment, Connector. Records: Experiment, Variant, Run,
Measurement, Artifact, Evaluation, Decision, Learning, ExternalLink. Every change appends to
an Event log.

Key relations: a type has many experiments; an experiment has many variants, runs, evaluations
and one decision; a run belongs to one variant and has many measurements and artifacts; a
measurement references one metric version; an evaluation references one evaluator version and
the environment it ran in; a decision yields learnings.

Type-defined fields are stored as JSONB on the experiment and validated against the pinned
type version's JSON Schema; frequently filtered fields get generated indexes.

## Roadmap

Rough sizing for 2–3 engineers; each phase ends with something usable.

| Phase | Weeks | Scope | Usable result |
|---|---|---|---|
| 0 Foundations | 1–4 | Kernel records, schema registry, event log, Postgres + TimescaleDB, REST API, Python SDK, CSV import, generic hypothesis type | Log runs from code and see them in one table |
| 1 Evaluate | 5–10 | Sandboxed Python and Julia kernels, notebooks, evaluator contracts and registry, scheduled runs, experiment page with charts and verdicts | Write an evaluator once, run it on every matching experiment |
| 2 Configure | 11–16 | Type builder with YAML and git sync, lifecycle gates, metric registry and kinds, first four domain packs | Marketing and sales run experiments without an engineer |
| 3 Connect | 17–22 | Connectors (GitHub Actions, HubSpot, LinkedIn Ads, GA4, SQL), automations engine, notifications, write-back, roles per domain, audit trail | Data arrives by itself; guardrails act by themselves |
| 4 Intelligence | 23+ | Learnings library and semantic search, AI design help, summaries, anomaly and SRM alerts, meta-analysis | The platform remembers what you already learned |

## Open decisions

1. Self-hosted next to the other Knovas components, or hosted first?
2. TimescaleDB throughout, or move measurements to ClickHouse once volume demands it?
3. Sandbox isolation: gVisor containers or microVMs?
4. Product name.
5. Which domain goes first as the pilot — engineering (search quality work already produces
   the data) is the lowest-effort start.

## Mockup boards

| Board | Shows |
|---|---|
| 01 Portfolio | All experiments across domains; domain filter, stage, effect with interval, evidence, guardrail alerts, decisions pending, latest learnings |
| 02 Experiment — A/B test | Hypothesis, lifecycle with gates, lift chart with hover, measurements table by role, evaluator verdict and decision rule progress, guardrails, type-defined details |
| 03 Experiment — offline evaluation | Runs logged from CI with parameters, commit and environment; recall by document class; SDK snippet |
| 04 Workbench | Python / Julia switch, notebook cells on live experiment data, posterior chart, evaluator contract, sandbox, tests, publish |
| 05 Type builder | Types grouped by pack, fields, lifecycle gates, metric slots, evaluation pipeline, synced YAML |
| 06 Metric registry | Measurement kinds, registry, metric definition, sample-size calculator, data checks |
| 07 Data sources and automations | Connectors, automation rules, selected rule as a flow |
| 08 Architecture | Layers and principles |
| 09 Data model and roadmap | Entities and relations, phases |

All numbers, names and results in the mockup are illustrative.
