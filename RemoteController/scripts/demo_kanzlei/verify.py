"""Extraction gate, manifest check, freshness warning, required format mix."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("demo-kanzlei")

REQUIRED_SUFFIXES = (".docx", ".pdf", ".txt", ".msg")
EXTRACTABLE = {".md", ".txt", ".docx", ".pdf", ".eml", ".msg"}


class VerifyError(RuntimeError):
    pass


def newest_mtime(root: Path) -> float:
    mtimes = [p.stat().st_mtime for p in root.rglob("*") if p.is_file()]
    return max(mtimes) if mtimes else 0.0


def warn_if_stale(root: Path, warn_days: int = 25) -> bool:
    newest = newest_mtime(root)
    if not newest:
        return False
    age_days = (time.time() - newest) / 86400
    if age_days > warn_days:
        log.warning(
            "newest file is %.0f days old (warn at %d). Default RC sync "
            "drops files older than 30 days (mtime). Run 'touch' before the demo.",
            age_days,
            warn_days,
        )
        return True
    return False


def require_format_mix(root: Path) -> None:
    found = {p.suffix.lower() for p in root.rglob("*") if p.is_file()}
    missing = [s for s in REQUIRED_SUFFIXES if s not in found]
    if missing:
        raise VerifyError(
            f"watch root missing required formats {missing}; have {sorted(found)}"
        )


def verify_manifest(root: Path) -> tuple[int, int, int]:
    manifest_path = root / "manifest.jsonl"
    if not manifest_path.exists():
        raise VerifyError(f"kein manifest.jsonl in {root}")
    missing = bad = ok = 0
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        path = root / entry["path"]
        if not path.exists():
            log.error("fehlt: %s", entry["path"])
            missing += 1
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            log.error("Hash weicht ab: %s", entry["path"])
            bad += 1
        else:
            ok += 1
    return ok, missing, bad


def _is_image_only_pdf(path: Path) -> bool:
    if path.suffix.lower() != ".pdf":
        return False
    import fitz

    doc = fitz.open(path)
    try:
        return not any((page.get_text() or "").strip() for page in doc)
    finally:
        doc.close()


def verify_extraction(root: Path, *, skip_scan_ocr: bool = False) -> list[str]:
    from sync.document_text import ConversionError, extract_document

    problems: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name in {"manifest.jsonl", "LICENSES.md"}:
            continue
        if path.suffix.lower() not in EXTRACTABLE:
            continue
        if skip_scan_ocr and _is_image_only_pdf(path):
            log.warning("skipping image-only PDF without OCR: %s", path.name)
            continue
        try:
            doc = extract_document(path)
        except ConversionError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        if path.name != "00_HINWEIS_DEMODATEN.txt" and "Aktenzeichen:" not in doc.text:
            if path.suffix.lower() in {".txt", ".docx", ".msg"}:
                problems.append(f"{path.name}: letterhead Aktenzeichen missing from extracted text")
    return problems


def verify_corpus(root: Path, cfg: dict, *, skip_scan_ocr: bool = False) -> int:
    require_format_mix(root)
    warn_if_stale(root, int(cfg.get("verify", {}).get("freshness_warn_days", 25)))
    ok, missing, bad = verify_manifest(root)
    log.info("manifest: %d ok, %d fehlend, %d abweichend", ok, missing, bad)
    if missing or bad:
        return 1
    problems = verify_extraction(root, skip_scan_ocr=skip_scan_ocr)
    for item in problems:
        log.error("%s", item)
    return 1 if problems else 0
