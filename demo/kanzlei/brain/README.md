# The Brain of Quarzfels Advokatur (demo tenant 817f2330)

The demo law firm's Brain: everything the firm's sources say, as connected things a lawyer can open and an AI can
read. Built with brain-runner from the firm's own sources; nothing in it is hand-entered. Everything is invented
(see `../README.md`).

## What it holds

| Type | From | How many (2026-11-28) |
|---|---|---|
| Akte | KanzleiDB AKTEN, with AKTENBETEILIGTE, AKTENRECHTE, ZUSATZFELDWERTE and totals per matter | 1,500 |
| Partei | ADRESSEN, with its counts across all matters | 2,500 |
| Namensgruppe | ADRESSEN: names equal in normal form (no legal form, umlauts, word order) | 201 |
| Frist, Termin, Rechnung | FRISTEN, TERMINE, RECHNUNGEN | 2,296 / 1,168 / 3,363 |
| Dokument | DOKUMENTE of the 60 matters kept electronically (Word, PDF, text, mail in AutoDoc) | 1,195 |
| Mitarbeiter, Rechtsgebiet, Kanzlei | MITARBEITER, RECHTSGEBIETE | 20 / 22 / 1 |
| Begriff | Kanzlei-ABC on the common drive | 43 |
| Process, ProcessStep | the five Weisungen (`ontology/processes.yaml`) | 5 / 20 |

The runner derives two kinds of edge for the conflict check: `vertreten_gegen` (client against opponent in one
matter) and `moeglicherweise_identisch` (two addresses in one same-name group). Each matter in scope stands at a step
of each Weisung that applies to it (mappings `kanzleidb-ablauf-*`): the first step whose evidence is missing, else the
last. The views show what is missing there (requirement paths in `processes.yaml`).

Scope of the rule checks: Weisung 1 for matters opened since its date (11 March 2023) and kept electronically;
Weisungen 3, 4, 5 for electronic matters (5: closed ones); Weisung 2 for every open deadline that is not itself a
Wiedervorlage. The 1,440 matters held only as records have no documents to check, so they are not checked.

## Files

| Path | What |
|---|---|
| `brain-config.yaml` | the manifest: ontology, processes, pages, profiles, mappings, derived edges |
| `ontology/` | types (`terms.yaml`), relations, the Weisungen (`processes.yaml`; `generated/` is built from it), pages, the spine, descriptions, where each fact is corrected (`edit-sources.yaml`) |
| `mappings/` | `weisungen` (definitions source), `kanzleidb-*` (record stream) |
| `profiles/` | Partner, Anwalt, Sekretariat: their views |
| `export/kanzleidb_stream.py` | the stand-in record connector: KanzleiDB (read-only login) and the Kanzlei-ABC -> one record stream |
| `export/build_definitions.py` | `processes.yaml` -> `generated/process-definitions.json` |

## Run it (on server01, `~/quarzfels-brain`)

```bash
. ./env.sh                       # BRAIN_API_URL and the demo tenant's certificate (certs/, 0600)
venv/bin/python config/export/build_definitions.py
venv/bin/brain-runner validate --config config/brain-config.yaml
venv/bin/brain-runner ontology apply --config config/brain-config.yaml
# definitions (the Weisungen)
venv/bin/brain-runner plan --config config/brain-config.yaml --mapping weisungen --source folder --root config \
  --source-key definitions --out run/plan-weisungen.json && venv/bin/brain-runner apply --plan run/plan-weisungen.json
# records: export, then all nine kanzleidb mappings in ONE plan (they share nodes and edges)
./apply.sh                       # plan.sh + apply in the background; log in run/apply.log
venv/bin/brain-runner bundle --config config/brain-config.yaml --out run/bundle.json
cp run/bundle.json ~/demo-kanzlei/brain/bundle.json   # the Platform reads it (VIEWS_CONFIG_SOURCE)
```

A full first load is about 200,000 requests (one per fact; the Core has no bulk write) at about 8 a second for the
tenant: 6 to 7 hours. A later run writes only what changed. During a load the Platform's search shares the tenant's
rate limit and may answer 429.

brain-runner is 0.3.3 from KnowledgeBase `develop` (8c25ae1a3), installed in `venv`. The Core accepts the datatypes
date, money, enum, text and entity_ref: amounts and counts are text here (money needs {amount, currency}).
