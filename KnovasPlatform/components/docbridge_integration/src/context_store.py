"""Read/write per-document context sidecars for search-result previews."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SIDECAR_VERSION = 1
MAX_SENTENCE_CHARS = 2000
MAX_FIRST_PAGE_CHARS = 8000
MAX_SIDEcar_SENTENCES = 50_000
FIRST_PAGE_FALLBACK_SENTENCES = 15
DEFAULT_CONTEXT_RADIUS = 10


def _truncate(text: str, max_chars: int) -> str:
    raw = str(text or "").strip()
    if len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 1].rstrip() + "…"


def sidecar_path_for_pointer(store_dir: Path, pointer: str) -> Path:
    digest = hashlib.sha256(str(pointer or "").encode("utf-8")).hexdigest()
    return store_dir / f"{digest}.json"


def build_sentence_records_from_dicts(
    text: str,
    sentences: Sequence[Any],
) -> List[Dict[str, Any]]:
    """Build compact sentence records from knovas-extract Sentence objects."""
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
    sentences: Optional[Sequence[Any]],
) -> Dict[str, Any]:
    sentence_records = build_sentence_records_from_dicts(text, sentences or [])
    return {
        "version": SIDECAR_VERSION,
        "pointer": str(pointer or "").strip(),
        "path": str(path or "").strip(),
        "sentences": sentence_records,
        "first_page": build_first_page_payload(sentence_records),
    }


def write_context_sidecar(
    store_dir: Optional[str],
    pointer: str,
    path: str,
    text: str,
    sentences: Optional[Sequence[Any]],
) -> bool:
    if not store_dir or not str(store_dir).strip():
        return False
    pointer_s = str(pointer or "").strip()
    if not pointer_s:
        return False
    root = Path(store_dir).resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        payload = build_sidecar_payload(pointer_s, path, text, sentences)
        target = sidecar_path_for_pointer(root, pointer_s)
        fd, tmp = tempfile.mkstemp(dir=root, suffix=".tmp")
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
        return True
    except Exception as exc:
        logger.warning("Failed to write context sidecar for %s: %s", pointer_s, exc)
        return False


@lru_cache(maxsize=4096)
def _load_sidecar_file(path: str, mtime: float) -> Optional[Dict[str, Any]]:
    del mtime
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as exc:
        logger.debug("Could not load context sidecar %s: %s", path, exc)
        return None


def load_context(store_dir: Optional[str], pointer_candidates: Sequence[str]) -> Optional[Dict[str, Any]]:
    if not store_dir or not str(store_dir).strip():
        return None
    root = Path(store_dir)
    if not root.is_dir():
        return None
    seen: set[str] = set()
    for raw in pointer_candidates:
        pointer = str(raw or "").strip()
        if not pointer or pointer in seen:
            continue
        seen.add(pointer)
        path = sidecar_path_for_pointer(root, pointer)
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        data = _load_sidecar_file(str(path), mtime)
        if data:
            return data
    return None


def first_page_text(entry: Optional[Dict[str, Any]], max_chars: int = MAX_FIRST_PAGE_CHARS) -> str:
    if not entry:
        return ""
    first = entry.get("first_page")
    if isinstance(first, dict):
        text = first.get("text")
        if isinstance(text, str) and text.strip():
            return _truncate(text.strip(), max_chars)
    sentences = entry.get("sentences")
    if isinstance(sentences, list):
        return _truncate(build_first_page_payload(sentences).get("text", ""), max_chars)
    return ""


def _anchor_sentence_index(
    sentences: List[Dict[str, Any]],
    sentence_number: Optional[int],
) -> int:
    if not sentences:
        return 0
    by_i = {int(s["i"]): idx for idx, s in enumerate(sentences) if "i" in s}
    if sentence_number is not None:
        try:
            target = int(sentence_number)
        except (TypeError, ValueError):
            target = None
        if target is not None and target in by_i:
            return by_i[target]
    return 0


def context_window(
    sentences: List[Dict[str, Any]],
    sentence_number: Optional[int],
    radius: int = DEFAULT_CONTEXT_RADIUS,
) -> Optional[Dict[str, str]]:
    if not sentences or radius < 0:
        return None
    anchor_idx = _anchor_sentence_index(sentences, sentence_number)
    start = max(0, anchor_idx - radius)
    end = min(len(sentences), anchor_idx + radius + 1)
    window = sentences[start:end]
    if not window:
        return None
    before_parts: List[str] = []
    match_text = ""
    after_parts: List[str] = []
    for idx, sent in enumerate(window):
        global_idx = start + idx
        text = str(sent.get("t") or "").strip()
        if not text:
            continue
        if global_idx < anchor_idx:
            before_parts.append(text)
        elif global_idx == anchor_idx:
            match_text = text
        else:
            after_parts.append(text)
    if not match_text and anchor_idx < len(sentences):
        match_text = str(sentences[anchor_idx].get("t") or "").strip()
    return {
        "before": " ".join(before_parts).strip(),
        "match": match_text,
        "after": " ".join(after_parts).strip(),
    }


def resolve_sentence_number(result: Dict[str, Any]) -> Optional[int]:
    raw = result.get("sentence_number")
    if raw is not None and str(raw).strip() != "":
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    chunks = result.get("top_chunks")
    if isinstance(chunks, list) and chunks:
        first = chunks[0]
        if isinstance(first, dict):
            raw = first.get("sentence_number")
            if raw is not None and str(raw).strip() != "":
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    pass
    return None


def enrich_result_with_context(
    result: Dict[str, Any],
    store_dir: Optional[str],
    pointer_candidates: Sequence[str],
    *,
    context_radius: int = DEFAULT_CONTEXT_RADIUS,
) -> bool:
    """Attach first_page_preview and context_snippet when a sidecar exists."""
    entry = load_context(store_dir, pointer_candidates)
    if not entry:
        return False
    first = first_page_text(entry)
    sentences = entry.get("sentences")
    snippet = None
    if isinstance(sentences, list):
        snippet = context_window(sentences, resolve_sentence_number(result), radius=context_radius)
    if first:
        result["first_page_preview"] = first
    if snippet:
        result["context_snippet"] = snippet
    return bool(first or snippet)
