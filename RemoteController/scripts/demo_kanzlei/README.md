# Demo-Kanzlei-Korpus

Generates a synthetic Swiss law-firm file base (Akten, correspondence, fee
notes) on top of the licence-clean library in `scripts/demo_corpus/`.

The world model (`world.json`) is **never** written into the watch root.
Knovas sees only documents.

## Formats

| Kind | Extension | Notes |
|------|-----------|--------|
| Long documents | `.docx` `.pdf` `.txt` | rotated across the Akte |
| Emails | `.msg` | Outlook OLE, extracted by `knovas-extract[msg]` |
| Scanned post | `.pdf` | image-only; OCR path |

No `.xlsx`. Emails are **not** `.eml`.

## Build (staged, with a gate after each stage)

From `RemoteController/`:

```bash
pip install -r scripts/demo_kanzlei/requirements.txt
python scripts/demo_kanzlei/cli.py build --out ../corpus/kanzlei --skip-zefix
python scripts/demo_kanzlei/cli.py verify --out ../corpus/kanzlei
```

Default is **pilot**: one hero matter (`2024-017`) plus a small Kanzlei
register. `--full` builds every mandate in `world.toml` with a mix that
follows the mandate (opening, correspondence, work product, fees; lawsuits
only where the practice area and status warrant it).

On disk the tree is a numbered Aktenplan, so Explorer sorts like a real
file base:

```
Mandanten/{Mandant}/{Aktenzeichen}-{Titel}/
  01-Eroeffnung/01-2024-01-15-Mandatsvereinbarung/...
  02-Korrespondenz/...
  03-Interna/...
  04-Beilagen/...
  05-Rechtsschriften/...
  06-Gerichtliches/...
  07-Finanzen/...
Kanzlei/01-Vorlagen|02-Organisation|03-Compliance/...
```

Each file still uses the AutoDoc name `{GUID}_{AkteID}_{Typ}.{ext}`.

`--until-stage world|plan|write|file|mess|ground_truth|verify` stops after that
gate. Work files (world, plan, prose cache, ground truth) go to `--work`
(default: sibling `.{out}-work`), **outside** the watch root.

## Freshness

The Remote Controller default incremental body drops files whose **mtime** is
older than 30 days. `verify` warns at 25 days. Before a demo:

```bash
python scripts/demo_kanzlei/cli.py touch --out ../corpus/kanzlei
```
