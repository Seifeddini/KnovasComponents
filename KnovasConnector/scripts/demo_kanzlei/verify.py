"""Extraction gate, manifest check, freshness warning, required format mix."""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from pathlib import Path

_RC_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_RC_SRC) not in sys.path:
    sys.path.insert(0, str(_RC_SRC))

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


def verify_manifest(root: Path, manifest_path: Path) -> tuple[int, int, int]:
    if not manifest_path.exists():
        raise VerifyError(f"kein Manifest: {manifest_path}")
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


def read_manifest(manifest_path: Path) -> list[dict]:
    return [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _is_image_only_pdf(path: Path) -> bool:
    if path.suffix.lower() != ".pdf":
        return False
    import fitz

    try:
        doc = fitz.open(path)
    except Exception:
        # Engineered mess (truncated / wrong-bytes "PDF") is not a scan.
        return False
    try:
        return not any((page.get_text() or "").strip() for page in doc)
    finally:
        doc.close()


def _no_ocr_engine(exc: Exception) -> bool:
    """The extractor could not read a scan because this machine has no OCR engine.

    That is a property of the machine, not of the corpus: the gate says "not
    checked" and goes on, in the small and in the full build alike.
    """
    return "OCR engine" in str(exc) and "missing optional dependency" in str(exc)


def verify_extraction(root: Path, manifest_path: Path | None = None, *, skip_scan_ocr: bool = False) -> list[str]:
    """Extract every listed file the way the Connector does.

    The manifest is in the work folder. A build made before it moved there has
    it in the share itself; that place is still read when no path is given.
    """
    from sync.document_text import ConversionError, extract_document

    manifest_path = manifest_path or root / "manifest.jsonl"
    listed = {entry["path"] for entry in read_manifest(manifest_path)} if manifest_path.exists() else None
    problems: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name in {"manifest.jsonl", "LICENSES.md"}:
            continue
        rel = path.relative_to(root).as_posix()
        if listed is not None and rel not in listed:
            continue
        if path.suffix.lower() not in EXTRACTABLE:
            continue
        if skip_scan_ocr and _is_image_only_pdf(path):
            log.warning("skipping image-only PDF without OCR: %s", path.name)
            continue
        try:
            doc = extract_document(path)
        except ConversionError as exc:
            if _no_ocr_engine(exc) and _is_image_only_pdf(path):
                log.warning("scan not checked, no OCR engine here: %s", path.name)
                continue
            problems.append(f"{path.name}: {exc}")
            continue
        if path.name != "00_HINWEIS_DEMODATEN.txt" and "Aktenzeichen:" not in doc.text:
            if path.suffix.lower() in {".txt", ".docx", ".msg"}:
                problems.append(f"{path.name}: letterhead Aktenzeichen missing from extracted text")
    return problems


def verify_corpus(root: Path, cfg: dict, *, manifest_path: Path, skip_scan_ocr: bool = False) -> int:
    require_format_mix(root)
    warn_if_stale(root, int(cfg.get("verify", {}).get("freshness_warn_days", 25)))
    ok, missing, bad = verify_manifest(root, manifest_path)
    log.info("manifest: %d ok, %d fehlend, %d abweichend", ok, missing, bad)
    if missing or bad:
        return 1
    problems = verify_extraction(root, manifest_path, skip_scan_ocr=skip_scan_ocr)
    for item in problems:
        log.error("%s", item)
    return 1 if problems else 0


#: Copies that hold the whole text of the original.
_WHOLE = {None, "duplicate", "misfiled", "broken_name"}


def verify_client(root: Path, cfg: dict, *, manifest_path: Path, skip_scan_ocr: bool = False) -> int:
    """The client layout: the share is exactly the manifest, and what should be readable is."""
    from sync.document_text import ConversionError, extract_document

    del cfg
    require_format_mix(root)
    entries = read_manifest(manifest_path)
    ok, missing, bad = verify_manifest(root, manifest_path)
    log.info("manifest: %d ok, %d fehlend, %d abweichend", ok, missing, bad)
    listed = {entry["path"] for entry in entries}
    extra = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in listed)
    for item in extra[:20]:
        log.error("nicht im Manifest: %s", item)
    problems: list[str] = []
    unchecked = 0
    for entry in entries:
        if not (entry["written"] and entry["syncable"] and entry["mess"] in _WHOLE):
            continue
        path = root / entry["path"]
        if skip_scan_ocr and _is_image_only_pdf(path):
            continue
        try:
            doc = extract_document(path)
        except ConversionError as exc:
            if _no_ocr_engine(exc) and _is_image_only_pdf(path):
                unchecked += 1
                continue
            problems.append(f"{entry['path']}: {exc}")
            continue
        if entry["kind"] == "autodoc" and entry["plan_id"] and entry["fmt"] in {"txt", "docx", "msg"}:
            if "Aktenzeichen:" not in doc.text:
                problems.append(f"{entry['path']}: letterhead Aktenzeichen missing from extracted text")
    for item in problems:
        log.error("%s", item)
    if unchecked:
        log.warning("%d scanned PDFs not checked: no OCR engine on this machine. "
                    "Run verify where tesseract is installed (the Connector image).", unchecked)
    return 1 if (missing or bad or extra or problems) else 0
