"""Persist per-document sentence index for search-result context previews."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from knovas_extract.result import Sentence

logger = logging.getLogger(__name__)

SIDECAR_VERSION = 1
MAX_SENTENCE_CHARS = 2000
MAX_FIRST_PAGE_CHARS = 8000
MAX_SIDEcar_SENTENCES = 50_000
FIRST_PAGE_FALLBACK_SENTENCES = 15


def context_store_dir_from_env() -> Optional[Path]:
    raw = (os.environ.get("SEARCH_CONTEXT_STORE_PATH") or "").strip()
    if not raw:
        return None
    return Path(raw).resolve()


def sidecar_path_for_pointer(store_dir: Path, pointer: str) -> Path:
    digest = hashlib.sha256(str(pointer or "").encode("utf-8")).hexdigest()
    return store_dir / f"{digest}.json"


def _truncate(text: str, max_chars: int) -> str:
    raw = str(text or "").strip()
    if len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 1].rstrip() + "…"


def build_sentence_records(
    text: str,
    sentences: Optional[Sequence[Sentence]],
) -> List[Dict[str, Any]]:
    if not text or not sentences:
        return []
    ordered = sorted(sentences, key=lambda s: s.char_start)
    records: List[Dict[str, Any]] = []
    length = len(text)
    for idx, sent in enumerate(ordered):
        if len(records) >= MAX_SIDEcar_SENTENCES:
            break
        start = max(0, min(sent.char_start, length))
        end = ordered[idx + 1].char_start if idx + 1 < len(ordered) else length
        end = max(start, min(end, length))
        chunk = text[start:end].strip()
        if not chunk:
            continue
        entry: Dict[str, Any] = {
            "i": int(sent.index) + 1,
            "t": _truncate(chunk, MAX_SENTENCE_CHARS),
        }
        page = getattr(sent, "page_number", None)
        if page is not None and int(page) >= 1:
            entry["p"] = int(page)
        records.append(entry)
    return records


def build_first_page_payload(sentences: List[Dict[str, Any]]) -> Dict[str, Any]:
    page_one = [s["t"] for s in sentences if s.get("p") == 1]
    if page_one:
        body = " ".join(page_one)
    elif sentences:
        body = " ".join(s["t"] for s in sentences[:FIRST_PAGE_FALLBACK_SENTENCES])
    else:
        body = ""
    return {"page": 1, "text": _truncate(body, MAX_FIRST_PAGE_CHARS)}


def build_sidecar_payload(
    pointer: str,
    path: str,
    text: str,
    sentences: Optional[Sequence[Sentence]],
) -> Dict[str, Any]:
    sentence_records = build_sentence_records(text, sentences)
    return {
        "version": SIDECAR_VERSION,
        "pointer": str(pointer or "").strip(),
        "path": str(path or "").strip(),
        "sentences": sentence_records,
        "first_page": build_first_page_payload(sentence_records),
    }


def write_context_sidecar(
    store_dir: Optional[Path],
    pointer: str,
    path: str,
    text: str,
    sentences: Optional[Sequence[Sentence]],
) -> bool:
    """Write sidecar JSON atomically. No-op when store_dir is unset."""
    if store_dir is None:
        return False
    pointer_s = str(pointer or "").strip()
    if not pointer_s:
        return False
    try:
        store_dir.mkdir(parents=True, exist_ok=True)
        payload = build_sidecar_payload(pointer_s, path, text, sentences)
        target = sidecar_path_for_pointer(store_dir, pointer_s)
        fd, tmp = tempfile.mkstemp(dir=store_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        logger.debug("Wrote context sidecar for %s", pointer_s)
        return True
    except Exception as exc:
        logger.warning("Failed to write context sidecar for %s: %s", pointer_s, exc)
        return False
