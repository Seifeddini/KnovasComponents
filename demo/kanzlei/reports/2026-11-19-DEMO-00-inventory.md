# DEMO-00 — Inventory of the demo law firm generator (19 November 2026)

Item `DEMO-00` of `FEAT-2026-11-19-demo-law-firm` (bet `BET-2026-11-brain-builder`). Serves all scenarios.
A report only; no code was changed.

Base: KnovasComponents `origin/main` at `5a47f16` (15 Nov 2026), worktree `.worktrees/demo-kanzlei`.

## 1. Was a full build kept?

**No, as far as this PC shows.** No `manifest.jsonl` from a previous build under `E:\Knovas`, `E:\corpus` or the
user's documents. The development VM was not checked (no access attempted). Treat the full build as never kept.

## 2. Do the generator's tests pass on `main`?

**Yes: 30 passed** (`KnovasConnector/tests/unit/demo_kanzlei`, `DEMO_CORPUS_REQUIRED=1`, Python 3.13, 6 s).

Getting there needed the Connector's own dependencies, because `KnovasConnector/tests/conftest.py` imports Flask:

- `knovas-extract` is not on PyPI; the Connector requires `>=0.4.0a1`. The local checkout
  `E:\Knovas\KnovasExtract\knovas-extract-python` is `0.3.0` and does not satisfy it.
- What works: the CI pin from `scripts/ci/check_knovas_extract_pin.sh`
  (`0.4.0a1`, git `2c95cbc0a8e1cae072f0e0b6f63200d84a29a672`), installed from GitHub, then `pip install -e ".[dev]"`.

## 3. The default build

`cli.py build --out … --work … --skip-zefix`: **31 files in 4 s, every gate green.**
9 `.docx`, 9 `.pdf`, 8 `.txt`, 4 `.msg`, 1 `manifest.jsonl`. One matter (`2024-017`) plus the firm's own folder.

## 4. The full build

`--full`: 644 files (163 `.docx`, 167 `.pdf`, 164 `.txt`, 149 `.msg`, 1 `.jsonl`), 22 MB, 10 clients, 50 s.
**The `verify` gate failed** on this PC: four scanned PDFs need an OCR engine (tesseract), which is not installed here.

## 5. What does not match the plan

| # | Plan says | What is true | Consequence |
|---|-----------|--------------|-------------|
| 1 | The full build is about 5,330 files | **644 files.** The 5,330 of the design of 6 Sept counted ~2,860 texts written by a language model and 1,200 court decisions filed from `demo_corpus`. Neither was built: prose comes from templates (`write_documents.py`, `TEMPLATE_VERSION = "akten-v3"`) and nothing is taken from `demo_corpus` | §3, §8.2, §9 "later" and §13 rest on a size that does not exist. The real-size share in §4 must come from DEMO-20's placeholder files, not from the generator |
| 2 | Writing all documents by a language model costs about 70 USD; cap in D-B1 | No language model is called. `world.toml` has an `[llm]` section that no code reads | D-B1's cost cap is moot unless someone decides to add model-written prose |
| 3 | POST-01: two builds from the same world and seed are identical, file for file | **19 of 31 files differ** between two default builds (all `.docx`, `.pdf`, `.msg`: embedded creation times). The `.txt` files and every path are identical | POST-01 needs work in the generator (pinned document dates) or a narrower definition (same paths, same extracted text) |
| 4 | The firm exists only in its documents; nothing tells Knovas who the clients and matters are | `manifest.jsonl` is written **into the share** with `matter`, `doc_type` and `date` per file. The Connector does not sync `.jsonl` (`SYNCABLE_EXTENSIONS`), but a client's share has no such file and the Builder's file browsing may see it | Move the manifest to the work folder with the other truth files (part of DEMO-10 or DEMO-20) |
| 5 | Truth files of §6 | Today the generator writes `world.json`, `questions.json` (simple templated questions, no `kind`) and `entities.json`. No `rights.csv`, `people.json`, `processes.json` | Expected: DEMO-11 builds them |
| 6 | The pilot build's `verify` | Passes with a warning when OCR is missing; the full build fails on the same condition | The gate is inconsistent; run `verify` where OCR is installed (the Connector image) |

## 6. Ready for the next item?

Yes for C1's code once D-B1 is decided. DEMO-10 additionally needs DEMO-02 (tenant, certificate, site), which a
person creates.
