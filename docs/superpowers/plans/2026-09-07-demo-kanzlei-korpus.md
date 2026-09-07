# Demo-Kanzlei-Korpus Generator — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans (tasks are a sequential pipeline).
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a synthetic Swiss law-firm document estate from a seeded world model, with a staged CLI that verifies each stage before the next one runs.

**Architecture:** World model first (`world.json` outside the watch root). A planner expands matters into document plan rows. A prose writer (template for tests/pilot; Anthropic for the full run) fills bodies. Renderers emit `.docx`/`.pdf`/scan-PDF/`.eml`. Filing applies the AutoDoc filename convention and the letterhead block. Mess and ground truth are last. Knovas never sees the world model.

**Tech Stack:** Python 3.11+, pymupdf, python-docx, Pillow, requests. Optional `anthropic` for LLM prose.

**Spec:** `docs/superpowers/specs/2026-09-06-demo-kanzlei-korpus-design.md`

## Global Constraints

- Matter linkage lives in document **text** (letterhead: Unser Zeichen / Aktenzeichen / In Sachen), not only the filename.
- Underscores in generated filenames appear **only** as the three field separators `{GUID}_{AkteID}_{Typ}.{ext}`. Everything else uses hyphens. `Typ` may contain underscores.
- Syncable extensions only: `.md .txt .docx .pdf .eml .msg`. No `.xlsx`.
- World model and `ground_truth/` live **outside** the watch root and are gitignored.
- Fictional parties are never inserted into real judgments.
- `verify` warns when the newest file mtime is older than 25 days.
- Pipeline is resumable: cache prose by plan-row hash; skip existing rendered files.
- Default `build` is `--pilot` (1 matter, ~20 docs). `--full` is opt-in.
- Open questions in spec §10: use spec defaults (Zürich, 7 staff, 40 matters, 1300 emails, RC watch-root ingest). Lawyer review of hero tier is documented, not automated.

## Files

Create under `RemoteController/scripts/demo_kanzlei/`:

- `world.toml` — knobs
- `models.py` — dataclasses
- `filenames.py` — letterhead + AutoDoc names
- `screen_names.py` — Zefix gate
- `catalog.py` — seeded people/clients/matter templates
- `build_world.py` — `world.json`
- `plan_documents.py` — plan rows
- `write_documents.py` — prose + cache
- `render.py` — docx/pdf/scan/eml
- `file_corpus.py` — watch-root layout + real-material filing
- `mess.py` — engineered mess
- `ground_truth.py` — scoring key + 60 questions
- `verify.py` — extraction gate, manifest, freshness
- `pipeline.py` — staged runner with gates
- `cli.py` — `build` / `verify` / `upload` / `list` / `touch`
- `README.md`, `requirements.txt`

Modify:

- `.gitignore` — add `ground_truth/`
- `RemoteController/scripts/demo_corpus/README.md` — 30-day freshness note

Test: `RemoteController/tests/unit/demo_kanzlei/`

---

### Task 1: Filenames and letterhead

Hard constraint from spec §2.2. Tests first.

### Task 2: Zefix screening

Reject exact active-name collisions. Tests mock HTTP.

### Task 3: World model

Seeded, deterministic. 7 staff with voices, ~10 clients, matters as dated event lists. Pilot = 1 hero matter `2024-017`.

### Task 4: Document planner

One plan row per document. Core 12–25 per matter from events; remaining tier quotas distributed. Stable hash.

### Task 5: Prose writer + cache

Template writer (tests/pilot). Anthropic writer behind `--writer anthropic`. Cache by plan-row hash.

### Task 6: Renderers + filing + mess + verify

Formats in `SYNCABLE_EXTENSIONS`. Filename convention. Mess rates from toml. Scans must extract ≥200 chars through `document_text.py` when knovas-extract OCR is available; otherwise fail the scan gate in `--full` and skip OCR-only files in `--pilot` with a warning.

### Task 7: Staged CLI

Stages: `screen → world → plan → write → render → file → mess → ground_truth → verify`. Each stage has a gate; failure stops the run. `--from-stage` / `--until-stage` for resume.

### Task 8: Pilot run

`build --pilot --writer template --skip-zefix` produces ~20 docs, verify passes, ground truth exists outside the watch root.
