# Lumora AG — a fictional client for the views demo

Lumora AG (Zürich) builds **Lumora Desk**, document search for Swiss Treuhand firms, and the
**Lumora API** for software partners. It is run the way Knovas runs itself (Client Zero): the
same seven processes, the same roles, value streams and spines, the same report rules. Every
person, customer, plan, decision, risk, release, deal and experiment in it is invented.

```bash
cd KnovasPlatform/components/docbridge_integration
python serve_views_demo.py          # http://127.0.0.1:5077/views
```

No Knovas server, certificate, database or login. The real views blueprint reads `graph.json`
through `src/graph_views/offline.py`. The visitor counts as the operator, so the Definition tab
shows its (empty) errors, gaps and differences. Saved views and change requests need the
platform database and answer 409 here.

## What is in it

| Process | Instances (step) |
|---|---|
| Feature lifecycle | 8 plans: e-mail intake and OCR tables (brief), usage billing (approved), fuzzy name search (implementing), tenant export (verified), Microsoft sign-in (implemented), ranking v2 (released), desktop sync (killed) |
| Decision | DEC-0001 … DEC-0007: decided, reviewed, proposed, superseded (by DEC-0006), rejected |
| Risk review | RISK-1 … RISK-5: mitigating, owned, assessed, accepted, closed |
| Release | 2026.09.2 announced, 2026.10.0 deployed, 2026.10.1 candidate |
| Customer onboarding | Aebi Treuhand (first query), Zollinger & Partner (certificate issued), Berg Fiduciaire (client created), Steuerbüro Wyss (organisation created) |
| Sales pipeline | 8 deals from qualified to won and lost, with MEDDPICC, value and next step |
| Experiment | setup checklist (measure), pricing page (keep), Treuhand webinar (measure), German vendor outreach (design) |

People: Lea Brunner (CEO, commercial owner), Marco Keller (CTO, engine owner), Nina Hofer (product,
ontology steward), Daniel Frei (sales), Sara Meier (engineering). Product spine: 2 products, 3 tiers,
6 capabilities, 4 software areas, 9 components with contracts, invariants, calls and source files.

Left incomplete on purpose, so the Lücken view, the board and the documentation have something to show:

- the e-mail intake brief has no plan record, invariant or Alloy check yet (3 missing);
- RISK-3 has no mitigation or acceptance (R4) and its review date passed (P-review);
- the fuzzy-name-search Alloy check failed;
- two experiments in *measure* have no result or decision yet;
- Invoicing has no documentation at all and the usage meter no responsibility.

## Files

| File | What it is | Made by |
|---|---|---|
| `company.py` | Lumora's content, as Python data, and how it becomes nodes and edges | by hand |
| `build.py` | Builds everything below from a KnowledgeBase checkout | by hand |
| `bundle.json` | Knovas's definitions bundle (KnowledgeBase KB-7) with Lumora's names; `owners` names Lumora's people; empty edit map | `build.py` |
| `graph.json` | Lumora's tenant: definition nodes as KnowledgeBase's loader writes them, then the instances | `build.py` |
| `process-completeness.json`, `docs-completeness.json`, `findings.json` | The reports the views read, computed over `graph.json` by KnowledgeBase's `brain_runner evaluate` | `build.py` |

## Rebuild

After a change to `company.py`, or to pick up newer definitions from KnowledgeBase:

```bash
pip install -r <KnowledgeBase>/scripts/brain_runner/requirements.txt
python demo_data/lumora/build.py --kb <KnowledgeBase>
python -m pytest tests/test_views_demo.py
```

`--at` sets the evaluation time (default `2026-10-30T08:00:00+00:00`). The build stops when a Knovas
name, person, tenant id or product term is left in the output. When KnowledgeBase adds a description
that names Knovas, add its replacement to `RENAMES` in `build.py`.
