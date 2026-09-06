# Demo-Kanzlei-Korpus — design

**Date:** 2026-09-06
**Status:** proposed design — open questions in §10 block the build gate, not the
review.
**Covers:** generation of a synthetic Swiss law-firm document estate (~5,300
files) that turns the existing licence-clean legal *library* into a plausible
*Kanzlei-Aktenbestand*, plus the ground-truth key that scores what the graph
recovers from it.
**Repositories:** `KnovasComponents` only. No `KnowledgeBase` change is
required; the corpus is consumed through the existing ingest contract.
**Builds on:** `RemoteController/scripts/demo_corpus/` (the library), which
stays unchanged and becomes one tier of this corpus.

---

## 1 · Problem

`RemoteController/scripts/demo_corpus/fetch_demo_corpus.py` already builds ~8,800
licence-clean documents — Swiss case law, Fedlex statutes, SLDS gold Regeste,
CUAD/MAUD/ContractNLI contracts — with a manifest, licence file, and an RC
watch-root mount (`docker-compose.corpus.yml` → `/data/corpus`,
`setup_server_corpus.sh` → `RC_WATCH_ROOTS`). That problem is solved.

But that corpus is **a law library, not a law firm's file base**. It has no
Akten, no clients, no opposing parties, no correspondence, no Honorarnoten, no
version chains, no scans, no misfiled post. Every matter-shaped surface the
product is being built toward — matter page, chronology, dossier, party
register, conflict check, Fristen — has nothing to render against. A prospect
opening the demo sees a search engine over published case law, which is not
what Knovas sells.

The gap is the **firm layer**: the documents a Kanzlei itself produces and
receives. This design generates that layer.

### 1.1 · The constraint that shapes everything

The firm layer is **documents only**. There is no seeded database of clients,
matters, or people. The firm, its staff, its clients and its mandates exist
*inside* the documents, and Knovas has to derive them. That is the demo: the
graph is impressive precisely because nothing told it the answer.

It also gives us a scoring key for free — see §6.

---

## 2 · What the ingest path actually does

Four findings from reading the ingest code on 2026-09-06 shape the design more
than any stylistic choice. Two of them contradict the obvious approach.

### 2.1 · The Akte ID in the filename is not parsed on the corpus path

The `{GUID}_{AkteID}_{Typ}.{ext}` convention is real
(`KnovasPlatform/components/docbridge_integration/src/file_utils.py:20`,
parsed at `:40`). But `AutoDocFileHandler` is imported by exactly one consumer —
the DocBridge web interface (`web_interface/app.py:33`), which serves the
AutoDoc path (`/mnt/autodoc`). **The RemoteController watch-root sync never
calls it.** A corpus mounted at `/data/corpus`, the way the existing demo corpus
is, has its filenames used as filenames and nothing more.

Where the Akte *does* reach the index on the DocBridge path, it does so as
document text: `knovas_client.py:481` prepends a literal `Akte: {akten_id}` line
to the document body.

**Consequence — the load-bearing design decision.** Matter linkage must live in
the **document text**, not only the filename. Every generated document carries a
letterhead reference block:

```
Unser Zeichen:   2024-017 / MB-lz
Aktenzeichen:    2024-017
In Sachen:       Meierhans Bau AG ./. Rüegg
```

This is what a real Swiss Kanzlei document carries anyway, so realism and
robustness point the same way. Filenames additionally follow the convention so
the corpus is usable on the DocBridge path unchanged — but nothing depends on
that.

### 2.2 · The filename parser is a naive split

`file_utils.py:61` is `stem.split('_')`, with `guid = parts[0]`,
`akten_id = parts[1]`, `doc_type = '_'.join(parts[2:])`. There is no GUID
validation and no field-count check. **Any additional underscore shifts the
fields**, and a stray one in a client name silently corrupts `akten_id`.

Hard constraint for the generator: **underscores appear in generated filenames
only as the three field separators.** Everything else uses hyphens. The `Typ`
token may contain underscores (it absorbs `parts[2:]`), which is the one place
they are safe.

This also gives us the *correct* mechanism for the broken-filename mess case
(§5): a stem with fewer than two parts yields `akten_id = None` — a genuine
"Ohne Aktenbezug" document — rather than a corrupted ID pointing at a real Akte.

### 2.3 · Spreadsheets do not ingest

`SYNCABLE_EXTENSIONS` (`RemoteController/src/sync/document_text.py:71`) is
`{.md, .txt, .docx, .pdf, .eml, .msg}`, matched by the include globs in
`default_sync_body.py:9-16`. `.xlsx`/`.pptx` are **not** on it — that is
Pflichtenheft F2, planned but not built.

So no Leistungserfassung or Honorar spreadsheet in this corpus. Time records
and fee notes are rendered as `.pdf`/`.docx` (which is what a client actually
receives), not as workbooks that would sit inert in the watch root.

Note the asymmetry: `file_utils.py` *does* list `.xlsx`/`.xls`. That is the
AutoDoc path's ambition, not the sync path's behaviour. We target the
intersection.

### 2.4 · OCR is wired, with a version guard

`document_text.py` OCRs image-only PDFs when `RC_PDF_OCR_ENABLED` is true
(default) and Tesseract is present; language comes from `RC_TESSERACT_LANG`,
defaulting to **`deu+eng`** (`:82`), not `deu`. `extract_accepts_ocr()` (`:50`)
guards against a pre-0.3 `knovas-extract`, which would otherwise ingest every
scan as an empty document.

Scanned incoming post is therefore a **feature demo**, not a liability — subject
to the extraction gate in §5.

### 2.5 · The 30-day freshness trap

`default_sync_body.py:29` sets `max_document_age_seconds: 2592000` — 30 days.
The filter is on file mtime, not on the dates *inside* the documents, so a
corpus dated 2019–2026 is fine on the day it is built. But **a corpus built more
than 30 days before the demo syncs nothing at all** on the default incremental
body.

This already applies to the existing demo corpus and is worth stating in both
READMEs. The generator's `verify` command warns when the newest file is older
than 25 days. Mitigation is a touch pass or an explicit override at demo setup.

---

## 3 · Approach

Three ways to build the firm layer:

| | How | Verdict |
|---|---|---|
| **A. World model first** *(recommended)* | One seeded `world.json` (firm, staff, clients, matters, dated event timeline). A planner expands each matter into a document plan. The LLM writes only *prose bodies* from a tight brief. Renderers emit `.docx`/`.pdf`/scan/`.eml`. | Every fact is consistent across documents — the same Frist appears in the letter, the memo and the Aktennotiz. That consistency is what makes the graph look intelligent. Regeneratable, resumable, ground truth for free. |
| B. LLM writes whole matters end-to-end | Hand a model a matter brief, let it emit the folder. | Fastest to first output, but dates, amounts and names drift between documents in the same Akte. A lawyer notices in thirty seconds. No reliable ground truth. |
| C. Templates only, no LLM | Pure procedural. | Free and fast; 1,500 documents read as 12 templates. Fails the prospect-firm bar outright. |

**A**, with one addition: for ~8 **hero matters** (the ones actually opened on
screen), a second LLM pass reads the whole finished folder and polishes
cross-references, so those documents read as if one hand wrote them over
eighteen months.

The critical property: **the world model is never given to Knovas.** It lives
outside the watch root. Knovas sees only documents and must derive the firm from
them. The same file becomes the scoring key (§6).

---

## 4 · The firm and its matters

### 4.1 · Firm

A Zürich Kanzlei, founded 1998, 19 people: 4 Partner, 7 Associates, 3
Substituten, 5 Sekretariat/Buchhaltung. Each has initials (`MB/lz` in
Aktennotizen), a signature block, a phone extension, and — this matters — a
**writing voice**: the senior partner writes four-line letters, one associate
over-explains, the Substitut is formal to a fault. Voice profiles are what keep
~2,900 generated documents from reading like one author.

The firm's name is chosen at build time and screened before use (§7).

~85 clients (Zürcher KMU: Bau, Treuhand, Gastro, Immobilien, IT,
Pharma-Zulieferer; ~20 private individuals; one Gemeinde, one Stiftung).
**~120 Mandate, 2019–2026**: 35 laufend, 75 abgeschlossen, 10 sistiert.

Practice areas are chosen to match where the *existing* corpus is rich, so that
decisions filed into a matter are genuinely on point: Arbeitsrecht, Mietrecht,
Vertrags-/Handelsrecht, Gesellschaftsrecht, Erb-/Familienrecht, Datenschutz
(EDÖB), Kartellrecht (WEKO), Finanzmarkt (FINMA), Bau-/Planungsrecht,
Wirtschaftsstrafrecht. `slices.toml` already fetches EDÖB (300), WEKO (200) and
FINMA (100) decisions, so those three areas have real filed material.

### 4.2 · Matters as event timelines

Each matter is an **ordered event list** with real dates — Mandatsannahme →
Vollmacht → Abmahnung → Klage → Klageantwort → Verhandlung → Urteil →
Honorarnote — and each event emits documents. That is what gives a chronology
surface something true to show, and it is what makes dates consistent across
documents by construction rather than by luck.

### 4.3 · What's in a matter folder

12–25 documents per Akte, typed via the filename `Typ` token *and* the
letterhead block:

- **Eröffnung** — Mandatsvereinbarung, Vollmacht, Konfliktprüfung
- **Korrespondenz** — Brief an Mandant / Gegenanwalt / Gericht, E-Mail-Threads
- **Interna** — Aktennotiz, Telefonnotiz, Besprechungsprotokoll,
  Rechtsgutachten, Pendenzenliste
- **Rechtsschriften** — Klage, Klageantwort, Replik, Duplik, Berufung,
  Beschwerde, Stellungnahme
- **Gerichtliches** — Verfügung, Vorladung, Urteil, Protokoll, Kostennote
- **Beilagen** — Arbeitsvertrag, Mietvertrag, Werkvertrag, Kündigung, Abmahnung,
  Lohnabrechnung, Rechnungen
- **Finanzen** — Honorarnote, Kostenvorschuss, Leistungserfassung, Mahnung
  (as PDF, per §2.3)
- **Recherche** — **real** BGer decisions and Fedlex extracts from the existing
  corpus, filed into the matter that cites them

---

## 5 · Composition and engineered mess

### 5.1 · How we reach ~5,300

| Tier | Count | Source |
|---|---:|---|
| Firm-authored long documents | 1,560 | LLM prose + renderers |
| E-Mail-Threads (individual `.eml`/`.msg`) | 1,300 | LLM, short — email is 40–50% of a real file base |
| Scanned incoming post (image PDF, OCR path) | 420 | Rendered to page images |
| Versions & duplicates | 600 | Derived from the above |
| Real decisions/statutes filed into matters | 1,200 | Existing `demo_corpus` build |
| Kanzlei-wide non-matter documents | 250 | Vorlagen, Merkblätter, GwG-Weisung, Partnersitzungsprotokolle, Newsletter |
| **Total** | **≈5,330** | tunable via `world.toml` |

Reaching 5k *with realistic email mass* means ~2,860 LLM-written texts, not
1,500 — but emails are short, so the cost barely moves (§8).

### 5.2 · The mess, engineered

Each mess type earns its place by demoing something:

| Mess | Rate | What it proves |
|---|---:|---|
| Exact duplicates across two Akten | ~180 | sha256 dedup |
| Version chains (`-v1`, `-v2`, `-final`, `Kopie-von`) | ~420 | "welche Fassung gilt?" |
| Scans without text layer | 420 | `RC_PDF_OCR_ENABLED` + Tesseract |
| Wrong Akte | ~2% | sort proposals / bootstrap |
| Broken filename (<2 underscore fields) | ~60 | `akten_id` absent → "Ohne Aktenbezug" |
| Empty / 3-byte files | ~25 | robustness, empty states |
| Latin-1 umlauts | ~40 | extraction hardening |

Note the version-chain suffixes use **hyphens**, per §2.2.

**Extraction gate.** Every scanned PDF is verified to extract ≥200 characters
through the real `document_text.py` path before it ships. Bad OCR on stage is
the one realism risk not worth taking. Files failing the gate are re-rendered at
higher DPI or dropped.

---

## 6 · The ground-truth key (never ingested)

`ground_truth/`, outside the watch root and gitignored: `world.json` +
per-matter expected entities, relations and timelines + **60 seeded demo
questions** with the documents that answer them ("Welche Frist läuft in Akte
2024-017?", "Welche Mandate betreffen Kündigung während Krankheit?").

It triples as demo script, search-quality benchmark, and the scorecard for what
the graph actually recovered: *did it find the 85 clients, the opposing parties,
the deadlines we planted?*

`.gitignore` gains `ground_truth/` alongside the existing `corpus/` entry
(`.gitignore:74`).

---

## 7 · Fictional but plausible

All persons and companies are invented. `screen_names.py` checks every generated
company name — **including the firm's own** — against the Zefix API and rejects
collisions before generation starts.

Real decisions stay verbatim and keep their licence; the existing
`LICENSES.md`/`manifest.jsonl` provenance carries over, including CC-BY
attribution for SLDS. **Fictional parties are never inserted into real
judgments** — the two tiers never mix inside one file.

A `00_HINWEIS_DEMODATEN.txt` sits at the corpus root and provenance lives in
`manifest.jsonl`. Deliberately *not* a visible watermark on every page, which
would destroy the realism we are paying for.

---

## 8 · Generator, effort, cost

New `RemoteController/scripts/demo_kanzlei/`, reusing `fetch_demo_corpus.py`'s
`build` / `verify` / `upload` / `list` command shape (`fetch_demo_corpus.py:560`
onward) so the ops story is unchanged:

```
world.toml          knobs: firm size, matter count, tier mix, seed
build_world.py      seeded world model  → world.json
plan_documents.py   world → one plan row per document
write_documents.py  LLM prose, concurrent, cached by plan-row hash
render.py           → .docx / .pdf / scan-PDF / .eml / .msg
file_corpus.py      filenames + letterhead refs + real-material filing
mess.py             §5.2
verify.py           extraction gate, manifest, freshness warning (§2.5)
```

Caching by plan-row hash makes reruns free and interrupted runs resumable —
the same property the existing builder has ("ein abgebrochener Lauf lässt sich
erneut starten").

**Model:** `claude-opus-5` throughout, `effort: medium` for the bulk tier and
`high`/`xhigh` for hero documents after a short sweep. At $5/$25 per MTok,
~6.5M input and ~2.3M output ≈ **$70**; the **Batch API halves it to ~$35** —
this is the textbook non-latency-sensitive workload. Prompt caching on the
shared world-model prefix cuts the input side further. Comfortably inside a
CHF 100–400 budget, so there is no reason to trade quality down.

---

## 9 · Phasing and risks

- **Days 1–2** — world model + planner + **one matter end-to-end (20 docs)**.
  Hard gate: read as a lawyer before anything scales.
- **Days 3–5** — renderers, mess, real-material filing; 10 matters; verify
  extraction through the real RC path on the dev box.
- **Days 6–9** — full run (~5,330), cost-capped and resumable.
- **Days 10–12** — hero polish on 8 matters, 60 demo questions, drive the real
  UI, fix what looks wrong.

| Risk | Mitigation |
|---|---|
| ~2,900 documents read same-y | Per-author voice profiles, varied lengths, typos in emails, per-doc seeds |
| Legal plausibility | Hero tier reviewed by a Swiss lawyer — **unresolved, see §10** |
| Ingest time at 5,330 docs | Measure in phase 2; `RemoteController/tests/unit/test_sync_large_corpus.py` exists |
| Name collision with a real firm | Zefix screening gate before generation |
| Corpus stale at demo time | §2.5 freshness warning in `verify` |

---

## 10 · Open questions

1. **Swiss-lawyer review of the hero tier.** Is one available? Without it, legal
   plausibility on the eight matters actually opened on screen is asserted, not
   verified. This is the highest-severity open risk and it is not one the
   generator can close.
2. **Firm profile** — size (19), practice mix, and Zürich as the seat. All are
   `world.toml` knobs, but changing them after the full run costs a rebuild.
3. **Email share** — 1,300 `.eml`/`.msg` is realistic for a real file base but
   is a lot of generated text. Reducible to ~700 without breaking the demo.
4. **Ingest path** — this design targets the RC watch root (per §2.1). If the
   demo is instead to run through the DocBridge AutoDoc path, the filename
   convention becomes load-bearing and §2.2's underscore rule becomes a
   correctness requirement rather than a robustness one.

## 11 · Dependency note

The Section C matters design
(`docs/superpowers/specs/2026-08-14-matters-and-typed-nodes-design.md`), which
defines the matter page, chronology, dossier and bootstrap surfaces this corpus
feeds, is **not on this branch or on `origin`** — it lives on
`design/matters-and-typed-nodes`, which `git fetch` cannot resolve. Its scope is
visible second-hand through
`2026-08-15-pflichtenheft-d-j-design.md:114` and `:60-62`. Section numbers from
it are deliberately not cited here, because they could not be read.

Nothing in this design blocks on it: the corpus is defined against the ingest
contract (§2), which was read directly.
