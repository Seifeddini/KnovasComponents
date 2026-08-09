#!/usr/bin/env python3
"""
fetch_demo_corpus.py — assemble the lawyer demo corpus and stage it for the Remote Controller.

Builds ~8,300 licence-clean legal documents (Swiss case law, Swiss federal
legislation, leading decisions with official headnotes, English commercial
contracts) as individual files on disk, so the Remote Controller can discover
and sync them like any customer document tree.

Every slice is CC0, CC BY 4.0, or Swiss federal open data. Nothing here is
scraped, licence-encumbered, or personal correspondence.

Subcommands
-----------
build produce the corpus locally (streams parquet over HTTP range requests)
upload rsync the corpus to the Remote Controller host (dry-run by default)
verify re-hash the corpus against manifest.jsonl
list show configured slices and their targets

Examples
--------
 python RemoteController/scripts/demo_corpus/fetch_demo_corpus.py build --out corpus/
 python RemoteController/scripts/demo_corpus/fetch_demo_corpus.py build --out corpus/ --only caselaw,slds
 python RemoteController/scripts/demo_corpus/fetch_demo_corpus.py verify --out corpus/
 python RemoteController/scripts/demo_corpus/fetch_demo_corpus.py upload --out corpus/ \\
 --host rc-demo.example.ch --remote-path /home/master/KnovasInternal/corpus --execute
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any, Iterator

_REQUIREMENTS = Path(__file__).resolve().parent / "requirements.txt"

try:
    import pyarrow.parquet as pq
    import requests
except ImportError:
    sys.exit(f"missing dependencies — run: pip install -r {_REQUIREMENTS}")

log = logging.getLogger("demo-corpus")

CONFIG_PATH = Path(__file__).with_name("slices.toml")
SLICE_DIRS = {
 "caselaw": "01_rechtsprechung",
 "fedlex": "02_gesetzgebung",
 "slds": "03_leitentscheide",
 "contracts": "04_vertraege",
}
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# --------------------------------------------------------------------------- io

class HttpFile(io.RawIOBase):
 """Seekable read-only file over HTTP range requests."""

 def __init__(self, url: str, session: requests.Session):
 self.session = session
 head = session.head(url, allow_redirects=True, timeout=30)
 head.raise_for_status()
 if head.headers.get("Accept-Ranges") == "none":
 raise RuntimeError(f"server does not support range requests: {url}")
 self.url = head.url
 self.size = int(head.headers["Content-Length"])
 self.pos = 0

 def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
 if whence == os.SEEK_SET:
 self.pos = offset
 elif whence == os.SEEK_CUR:
 self.pos += offset
 else:
 self.pos = self.size + offset
 return self.pos

 def tell(self) -> int:
 return self.pos

 def seekable(self) -> bool:
 return True

 def readable(self) -> bool:
 return True

 def read(self, size: int = -1) -> bytes:
 if size is None or size < 0:
 size = self.size - self.pos
 if size <= 0 or self.pos >= self.size:
 return b""
 end = min(self.pos + size, self.size) - 1
 resp = self.session.get(
 self.url, headers={"Range": f"bytes={self.pos}-{end}"}, timeout=120
 )
 resp.raise_for_status()
 chunk = resp.content
 self.pos += len(chunk)
 return chunk

def make_session() -> requests.Session:
 session = requests.Session()
 session.headers["User-Agent"] = "knovas-demo-corpus/1.0"
 adapter = requests.adapters.HTTPAdapter(max_retries=5, pool_maxsize=8)
 session.mount("https://", adapter)
 return session

def download(session: requests.Session, url: str, dest: Path) -> bool:
 """Fetch url to dest. Returns False if the file was already present."""
 if dest.exists() and dest.stat().st_size > 0:
 return False
 dest.parent.mkdir(parents=True, exist_ok=True)
 tmp = dest.with_suffix(dest.suffix + ".part")
 with session.get(url, stream=True, timeout=300) as resp:
 resp.raise_for_status()
 with tmp.open("wb") as handle:
 for chunk in resp.iter_content(chunk_size=1 << 20):
 handle.write(chunk)
 tmp.replace(dest)
 return True

def safe_name(value: str, limit: int = 120) -> str:
 cleaned = UNSAFE.sub("-", (value or "").strip()).strip("-.")
 return (cleaned or "unnamed")[:limit]

def sha256_of(path: Path) -> str:
 digest = hashlib.sha256()
 with path.open("rb") as handle:
 for chunk in iter(lambda: handle.read(1 << 20), b""):
 digest.update(chunk)
 return digest.hexdigest()

# ---------------------------------------------------------------------- manifest

class Manifest:
 def __init__(self, root: Path):
 self.root = root
 self.entries: list[dict[str, Any]] = []

 def add(
 self,
 path: Path,
 slice_name: str,
 source: str,
 source_url: str,
 licence: str,
 meta: dict[str, Any],
 ) -> None:
 self.entries.append(
 {
 "path": str(path.relative_to(self.root)),
 "slice": slice_name,
 "source": source,
 "source_url": source_url,
 "license": licence,
 "bytes": path.stat().st_size,
 "sha256": sha256_of(path),
 "meta": {k: v for k, v in meta.items() if v not in (None, "", [])},
 }
 )

 def write(self) -> None:
 target = self.root / "manifest.jsonl"
 with target.open("w", encoding="utf-8") as handle:
 for entry in self.entries:
 handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
 log.info("manifest: %d Dokumente -> %s", len(self.entries), target)

# ----------------------------------------------------------------------- caselaw

CASELAW_COLUMNS = [
 "decision_id", "court", "canton", "chamber", "docket_number", "decision_date",
 "publication_date", "language", "title", "legal_area", "regeste", "outcome",
 "decision_type", "judges", "collection", "source_url", "pdf_url",
 "bge_reference", "cited_decisions", "content_hash", "has_full_text",
 "text_length", "full_text",
]

MAX_ROW_GROUPS = 16

def evenly_spaced(count: int, total: int) -> list[int]:
 if count >= total:
 return list(range(total))
 step = total / count
 return sorted({min(total - 1, int(i * step)) for i in range(count)})

def stride(rows: list[dict], wanted: int) -> Iterator[dict]:
 """Walk a row group in even steps so a slice spans it instead of its head."""
 step = max(1, len(rows) // max(1, wanted * 2))
 for offset in range(step):
 for index in range(offset, len(rows), step):
 yield rows[index]

def build_caselaw(cfg: dict, root: Path, session: requests.Session, manifest: Manifest) -> int:
 out_root = root / SLICE_DIRS["caselaw"]
 licence = cfg["license"]
 min_len = int(cfg.get("min_text_length", 0))
 written_total = 0

 for court, target in cfg["courts"].items():
 url = f"{cfg['base_url']}/{court}.parquet"
 log.info("[caselaw] %s — Ziel %d Dokumente", court, target)
 try:
 handle = HttpFile(url, session)
 parquet = pq.ParquetFile(handle)
 except Exception as exc: # noqa: BLE001 - a missing court must not kill the run
 log.warning("[caselaw] %s uebersprungen: %s", court, exc)
 continue

 groups = parquet.metadata.num_row_groups
 touched = min(groups, max(4, min(MAX_ROW_GROUPS, -(-target // 100))))
 quota = -(-target // touched)
 court_dir = out_root / court
 written = 0

 for index in evenly_spaced(touched, groups):
 if written >= target:
 break
 table = parquet.read_row_group(index, columns=CASELAW_COLUMNS)
 taken = 0
 for row in stride(table.to_pylist(), quota):
 if written >= target or taken >= quota:
 break
 text = row.get("full_text") or ""
 if not row.get("has_full_text") or len(text) < min_len:
 continue
 stem = safe_name(row.get("decision_id") or row.get("docket_number") or "")
 doc = court_dir / f"{stem}.txt"
 if doc.exists():
 continue
 doc.parent.mkdir(parents=True, exist_ok=True)
 doc.write_text(text, encoding="utf-8")
 meta = {k: row.get(k) for k in CASELAW_COLUMNS if k != "full_text"}
 doc.with_suffix(".meta.json").write_text(
 json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
 )
 manifest.add(
 doc, "caselaw", "OpenCaseLaw", row.get("source_url") or url, licence,
 {
 "court": row.get("court"),
 "canton": row.get("canton"),
 "decision_date": row.get("decision_date"),
 "language": row.get("language"),
 "legal_area": row.get("legal_area"),
 "docket_number": row.get("docket_number"),
 },
 )
 written += 1
 taken += 1

 log.info("[caselaw] %s: %d geschrieben", court, written)
 written_total += written

 return written_total

# ------------------------------------------------------------------------ fedlex

FEDLEX_QUERY = """
PREFIX jolux: 
PREFIX skos: 
SELECT DISTINCT ?srNotation ?title ?dateApplicability ?pdfUrl WHERE {
 ?ca a jolux:ConsolidationAbstract ;
 jolux:classifiedByTaxonomyEntry/skos:notation ?notation ;
 jolux:inForceStatus ;
 jolux:isRealizedBy ?caExpr .
 BIND(STR(?notation) AS ?srNotation)
 ?caExpr jolux:language ;
 jolux:title ?title .
 ?cons jolux:isMemberOf ?ca ;
 jolux:dateApplicability ?dateApplicability ;
 jolux:isRealizedBy ?expr .
 ?expr jolux:language ;
 jolux:isEmbodiedBy ?pdfUrl .
 FILTER(STRENDS(STR(?pdfUrl), "/%(lang)s/pdf-a"))
 FILTER(!STRSTARTS(?srNotation, "0."))
}
ORDER BY ?srNotation ?dateApplicability
"""

LANG_URI = {"de": "DEU", "fr": "FRA", "it": "ITA"}

def sr_sort_key(notation: str) -> tuple:
 return tuple(int(p) if p.isdigit() else 0 for p in notation.split("."))

def build_fedlex(cfg: dict, root: Path, session: requests.Session, manifest: Manifest) -> int:
 lang = cfg.get("language", "de")
 query = FEDLEX_QUERY % {"lang": lang, "lang_uri": LANG_URI[lang]}
 log.info("[fedlex] SPARQL-Abfrage laeuft (kann ~1 Minute dauern)")
 resp = session.post(
 cfg["endpoint"],
 data={"query": query},
 headers={"Accept": "application/sparql-results+json"},
 timeout=600,
 )
 resp.raise_for_status()
 bindings = resp.json()["results"]["bindings"]

 acts: dict[str, dict[str, Any]] = {}
 for row in bindings:
 notation = row["srNotation"]["value"]
 entry = acts.setdefault(notation, {"title": row["title"]["value"], "versions": []})
 entry["versions"].append((row["dateApplicability"]["value"], row["pdfUrl"]["value"]))
 for entry in acts.values():
 entry["versions"] = sorted(set(entry["versions"]))
 log.info("[fedlex] %d Erlasse, %d Fassungen verfuegbar", len(acts), len(bindings))

 priority = [sr for sr in cfg.get("priority_sr", []) if sr in acts]
 rest = sorted((sr for sr in acts if sr not in set(priority)), key=sr_sort_key)
 ordered = priority + rest
 selected = ordered[: int(cfg["acts"])]
 chain_set = set(selected[: int(cfg.get("version_chain_acts", 0))])

 out_root = root / SLICE_DIRS["fedlex"]
 licence = cfg["license"]
 written = 0

 max_versions = int(cfg.get("max_versions_per_act", 10))
 for notation in selected:
 entry = acts[notation]
 versions = entry["versions"][-max_versions:] if notation in chain_set else entry["versions"][-1:]
 for date, pdf_url in versions:
 doc = out_root / f"SR-{safe_name(notation)}" / f"SR-{safe_name(notation)}_{date}.pdf"
 try:
 download(session, pdf_url, doc)
 except Exception as exc: # noqa: BLE001 - individual acts may 404
 log.warning("[fedlex] SR %s %s: %s", notation, date, exc)
 continue
 if not doc.exists() or doc.stat().st_size == 0:
 continue
 manifest.add(
 doc, "fedlex", "Fedlex", pdf_url, licence,
 {
 "sr_number": notation,
 "title": entry["title"],
 "date_applicability": date,
 "language": lang,
 "version_chain": notation in chain_set,
 },
 )
 written += 1
 if written and written % 100 == 0:
 log.info("[fedlex] %d Dateien", written)

 log.info("[fedlex] %d Dateien geschrieben (%d Versionsketten)", written, len(chain_set))
 return written

# -------------------------------------------------------------------------- slds

def build_slds(cfg: dict, root: Path, session: requests.Session, manifest: Manifest) -> int:
 out_root = root / SLICE_DIRS["slds"]
 licence = cfg["license"]
 target = int(cfg["docs"])
 config = cfg.get("config", "de_de")
 written = 0

 for split in ("train-00000-of-00001", "validation-00000-of-00001", "test-00000-of-00001"):
 if written >= target:
 break
 url = f"{cfg['base_url']}/{config}/{split}.parquet"
 try:
 parquet = pq.ParquetFile(HttpFile(url, session))
 except Exception as exc: # noqa: BLE001
 log.warning("[slds] %s uebersprungen: %s", split, exc)
 continue

 for group in range(parquet.metadata.num_row_groups):
 if written >= target:
 break
 for row in parquet.read_row_group(group).to_pylist():
 if written >= target:
 break
 decision = row.get("decision") or ""
 headnote = row.get("headnote") or ""
 if not decision or not headnote:
 continue
 stem = safe_name(str(row.get("decision_id") or row.get("sample_id")))
 doc = out_root / f"{stem}.txt"
 if doc.exists():
 continue
 doc.parent.mkdir(parents=True, exist_ok=True)
 doc.write_text(decision, encoding="utf-8")
 (out_root / f"{stem}.regeste.txt").write_text(headnote, encoding="utf-8")
 manifest.add(
 doc, "slds", "SLDS", row.get("url") or url, licence,
 {
 "decision_id": row.get("decision_id"),
 "law_area": row.get("law_area"),
 "year": row.get("year"),
 "volume": row.get("volume"),
 "decision_language": row.get("decision_language"),
 "gold_headnote_file": f"{stem}.regeste.txt",
 },
 )
 written += 1

 log.info("[slds] %d Entscheide mit amtlichem Regeste", written)
 return written

# ---------------------------------------------------------------------- contracts

def _extract_zip(archive: Path, out_dir: Path, pattern: str) -> list[Path]:
 written: list[Path] = []
 with zipfile.ZipFile(archive) as bundle:
 for member in bundle.namelist():
 if member.endswith("/") or not Path(member).match(pattern):
 continue
 target = out_dir / safe_name(Path(member).name, 160)
 if target.exists():
 written.append(target)
 continue
 target.parent.mkdir(parents=True, exist_ok=True)
 with bundle.open(member) as src, target.open("wb") as dst:
 shutil.copyfileobj(src, dst)
 written.append(target)
 return written

def build_contracts(cfg: dict, root: Path, session: requests.Session, manifest: Manifest) -> int:
 out_root = root / SLICE_DIRS["contracts"]
 licence = cfg["license"]
 written = 0

 with tempfile.TemporaryDirectory() as tmpdir:
 cache = Path(tmpdir)

 cuad = cfg["cuad"]
 archive = cache / "cuad.zip"
 log.info("[contracts] CUAD laedt (~500 MB)")
 download(session, cuad["url"], archive)
 for path in _extract_zip(archive, out_root / "cuad", cuad["member_glob"]):
 manifest.add(path, "contracts", "CUAD v1", cuad["url"], licence,
 {"collection": "cuad", "language": "en"})
 written += 1

 maud = cfg["maud"]
 log.info("[contracts] MAUD laedt")
 listing = session.get(maud["api"], timeout=60).json()
 members = [
 s["rfilename"] for s in listing.get("siblings", [])
 if s["rfilename"].startswith(maud["member_prefix"]) and s["rfilename"].endswith(".txt")
 ]
 for member in members:
 url = f"{maud['base_url']}/{member}"
 target = out_root / "maud" / safe_name(Path(member).name, 160)
 try:
 download(session, url, target)
 except Exception as exc: # noqa: BLE001
 log.warning("[contracts] MAUD %s: %s", member, exc)
 continue
 manifest.add(target, "contracts", "MAUD v1", url, licence,
 {"collection": "maud", "language": "en"})
 written += 1

 nli = cfg["contractnli"]
 archive = cache / "contract-nli.zip"
 log.info("[contracts] ContractNLI laedt")
 download(session, nli["url"], archive)
 written += _extract_contractnli(archive, out_root / "contractnli", nli, licence, manifest)

 log.info("[contracts] %d Vertraege", written)
 return written

def _extract_contractnli(
 archive: Path, out_dir: Path, cfg: dict, licence: str, manifest: Manifest
) -> int:
 written = 0
 seen: set[str] = set()
 with zipfile.ZipFile(archive) as bundle:
 for member in bundle.namelist():
 if not member.endswith(".json"):
 continue
 with bundle.open(member) as handle:
 try:
 payload = json.load(handle)
 except json.JSONDecodeError:
 continue
 for doc in payload.get("documents", []):
 text = doc.get("text") or ""
 name = str(doc.get("file_name") or doc.get("id") or "")
 if not text or name in seen:
 continue
 seen.add(name)
 target = out_dir / f"{safe_name(Path(name).stem, 160)}.txt"
 if not target.exists():
 target.parent.mkdir(parents=True, exist_ok=True)
 target.write_text(text, encoding="utf-8")
 manifest.add(target, "contracts", "ContractNLI", cfg["url"], licence,
 {"collection": "contractnli", "language": "en",
 "document_id": doc.get("id")})
 written += 1
 return written

# ------------------------------------------------------------------------ output

def write_licences(cfg: dict, root: Path, counts: dict[str, int]) -> None:
 lines = [
 "# Lizenzen und Namensnennung",
 "",
 "Dieser Korpus besteht ausschliesslich aus offen lizenzierten Rechtsdokumenten.",
 "Er enthaelt keine personenbezogene Korrespondenz und keine Quellen mit",
 "Share-alike-Pflicht (ODbL) oder ungeklaerten Wiki-Lizenzen.",
 "",
 "| Slice | Dokumente | Lizenz | Namensnennung |",
 "|---|---:|---|---|",
 ]
 rows = [
 ("caselaw", cfg["caselaw"]["license"], cfg["caselaw"]["attribution"]),
 ("fedlex", cfg["fedlex"]["license"], cfg["fedlex"]["attribution"]),
 ("slds", cfg["slds"]["license"], cfg["slds"]["attribution"]),
 ("contracts", cfg["contracts"]["license"], cfg["contracts"]["cuad"]["attribution"]),
 ("contracts", cfg["contracts"]["license"], cfg["contracts"]["maud"]["attribution"]),
 ("contracts", cfg["contracts"]["license"], cfg["contracts"]["contractnli"]["attribution"]),
 ]
 for name, licence, attribution in rows:
 lines.append(f"| {name} | {counts.get(name, 0)} | {licence} | {attribution} |")
 lines += [
 "",
 "Schweizer Entscheide werden von den Gerichten vor der Publikation bezueglich",
 "natuerlicher Personen anonymisiert. Firmennamen bleiben regelmaessig sichtbar —",
 "in der Demo also 'gerichtsanonymisiert', nicht 'vollstaendig anonym' sagen.",
 "",
 ]
 (root / "LICENSES.md").write_text("\n".join(lines), encoding="utf-8")

def cmd_build(args: argparse.Namespace, cfg: dict) -> int:
 root = args.out.expanduser().resolve()
 root.mkdir(parents=True, exist_ok=True)
 session = make_session()
 manifest = Manifest(root)
 selected = [s.strip() for s in args.only.split(",")] if args.only else list(SLICE_DIRS)
 builders = {
 "caselaw": build_caselaw,
 "fedlex": build_fedlex,
 "slds": build_slds,
 "contracts": build_contracts,
 }

 counts: dict[str, int] = {}
 for name in selected:
 if name not in builders:
 log.error("unbekannter Slice: %s (bekannt: %s)", name, ", ".join(builders))
 return 2
 log.info("=== Slice %s ===", name)
 counts[name] = builders[name](cfg[name], root, session, manifest)

 manifest.write()
 write_licences(cfg, root, counts)
 total = sum(counts.values())
 log.info("Fertig: %d Dokumente in %s", total, root)
 for name, count in counts.items():
 log.info(" %-10s %6d", name, count)
 return 0

def cmd_verify(args: argparse.Namespace, _cfg: dict) -> int:
 root = args.out.expanduser().resolve()
 manifest_path = root / "manifest.jsonl"
 if not manifest_path.exists():
 log.error("kein manifest.jsonl in %s", root)
 return 2
 missing = bad = ok = 0
 for line in manifest_path.read_text(encoding="utf-8").splitlines():
 entry = json.loads(line)
 path = root / entry["path"]
 if not path.exists():
 log.error("fehlt: %s", entry["path"])
 missing += 1
 elif sha256_of(path) != entry["sha256"]:
 log.error("Hash weicht ab: %s", entry["path"])
 bad += 1
 else:
 ok += 1
 log.info("verify: %d ok, %d fehlend, %d abweichend", ok, missing, bad)
 return 0 if not (missing or bad) else 1

def cmd_upload(args: argparse.Namespace, _cfg: dict) -> int:
 root = args.out.expanduser().resolve()
 if not (root / "manifest.jsonl").exists():
 log.error("kein manifest.jsonl in %s — zuerst 'build' laufen lassen", root)
 return 2
 remote = args.remote_path.rstrip("/")
 if args.host:
 target = f"{args.user + '@' if args.user else ''}{args.host}:{remote}/"
 else:
 target = f"{remote}/"
 cmd = ["rsync", "-a", "--partial", "--stats", "-v", f"{root}/", target]
 if not args.execute:
 cmd.insert(1, "--dry-run")
 log.info("Probelauf (kein Schreibzugriff). Mit --execute wirklich uebertragen.")
 log.info("$ %s", " ".join(cmd))
 return subprocess.call(cmd)

def cmd_list(_args: argparse.Namespace, cfg: dict) -> int:
 print(f"Zielgroesse: {cfg.get('total_target')} Dokumente\n")
 courts = cfg["caselaw"]["courts"]
 print(f"caselaw {sum(courts.values()):>6} {cfg['caselaw']['license']}")
 for court, count in courts.items():
 print(f" {court:<26} {count:>6}")
 print(f"fedlex {cfg['fedlex']['acts']:>6} {cfg['fedlex']['license']}"
 f" (+{cfg['fedlex']['version_chain_acts']} Versionsketten)")
 print(f"slds {cfg['slds']['docs']:>6} {cfg['slds']['license']}")
 print(f"contracts {1217:>6} {cfg['contracts']['license']}"
 f" (CUAD 510 + MAUD 100 + ContractNLI 607)")
 return 0

def main() -> int:
 parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
 parser.add_argument("--config", type=Path, default=CONFIG_PATH)
 parser.add_argument("-v", "--verbose", action="store_true")
 sub = parser.add_subparsers(dest="command", required=True)

 build = sub.add_parser("build", help="Korpus lokal erzeugen")
 build.add_argument("--out", type=Path, required=True)
 build.add_argument("--only", help="Slices kommagetrennt, z.B. caselaw,slds")
 build.set_defaults(func=cmd_build)

 verify = sub.add_parser("verify", help="Korpus gegen manifest.jsonl pruefen")
 verify.add_argument("--out", type=Path, required=True)
 verify.set_defaults(func=cmd_verify)

 upload = sub.add_parser(
     "upload",
     help="bereits gebauten Korpus per rsync auf einen anderen Host kopieren (nicht download)",
 )
 upload.add_argument("--out", type=Path, required=True)
 upload.add_argument("--host", help="ohne Angabe wird --remote-path lokal behandelt")
 upload.add_argument("--remote-path", required=True)
 upload.add_argument("--user")
 upload.add_argument("--execute", action="store_true", help="ohne dies nur Probelauf")
 upload.set_defaults(func=cmd_upload)

 listing = sub.add_parser("list", help="konfigurierte Slices zeigen")
 listing.set_defaults(func=cmd_list)

 args = parser.parse_args()
 logging.basicConfig(
 level=logging.DEBUG if args.verbose else logging.INFO,
 format="%(asctime)s %(levelname)-7s %(message)s",
 datefmt="%H:%M:%S",
 )
 with args.config.open("rb") as handle:
 cfg = tomllib.load(handle)
 return args.func(args, cfg)

if __name__ == "__main__":
 sys.exit(main())
