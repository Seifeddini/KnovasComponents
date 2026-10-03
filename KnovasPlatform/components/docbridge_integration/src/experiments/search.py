"""Experiments in the normal Knovas search.

Every experiment is one Knovas document under ``<prefix>/<domain>/<KEY>``.
The search route passes each result set through ``SearchIntegration``:

* ``split`` removes experiment hits -- and their pointers in the ``semantix``
  block -- from every response, always, even with the module switched off.
  Only people with a viewing role may learn that an experiment exists, and
  Knovas cannot know who that is.
* ``rows`` turns the removed hits back into result rows for those people,
  rendered from the Platform database (title, status, domain), so a card
  never shows what the Knovas copy said at its last upload. The rows link to
  the experiment page and never to a file.

Flask-free: the app hands in callables for the request's connection and user.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from experiments import permissions

logger = logging.getLogger(__name__)

#: Experiment keys: <id_prefix>-<n>, e.g. MKT-58 (exp_domains.id_prefix).
KEY_PATTERN = r"[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}"
#: exp_domains.key.
DOMAIN_KEY_PATTERN = r"[a-z][a-z0-9-]{1,31}"
_KEY_RE = re.compile(KEY_PATTERN)
_DOMAIN_RE = re.compile(DOMAIN_KEY_PATTERN)

#: Where a result row or a semantix pointer entry may carry the pointer.
ROW_POINTER_FIELDS = ("doc_id", "pointer", "identifier", "path")
SEMANTIX_POINTER_FIELDS = ("pointer", "identifier", "doc_id")
SNIPPET_MAX_CHARS = 300
SHOW_IN_SEARCH_KEY = "experiments.show_in_search"


def parse_pointer(prefix: str, pointer: str) -> Optional[str]:
    """``<prefix>/<domain>/<KEY>`` -> KEY; anything else -> None."""
    if not isinstance(pointer, str) or not prefix:
        return None
    text = pointer.strip()
    if text.startswith("/"):
        text = text[1:]
    parts = text.split("/")
    if len(parts) != 3:
        return None
    head, domain, key = parts
    if head != prefix or not _DOMAIN_RE.fullmatch(domain) or not _KEY_RE.fullmatch(key):
        return None
    return key


def _store():
    from experiments import store

    return store


def _first_text(chunks: Any) -> str:
    if not isinstance(chunks, list):
        return ""
    for chunk in chunks:
        if isinstance(chunk, str) and chunk.strip():
            return chunk
        if isinstance(chunk, dict):
            for field in ("text", "snippet", "content"):
                value = chunk.get(field)
                if isinstance(value, str) and value.strip():
                    return value
    return ""


def _snippet(text: str) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= SNIPPET_MAX_CHARS:
        return flat
    return flat[: SNIPPET_MAX_CHARS - 1].rstrip() + "\u2026"


class SearchIntegration:
    def __init__(self, *, settings: Any, connection: Callable[[], Any],
                 current_user: Callable[[], Any], enabled: bool) -> None:
        self.settings = settings
        self._connection = connection
        self._current_user = current_user
        self.enabled = bool(enabled)

    @property
    def prefix(self) -> str:
        return str(getattr(self.settings, "pointer_prefix", "") or "experiments")

    def key_of(self, row: Any) -> Optional[str]:
        """The experiment KEY a result row points at, or None."""
        if not isinstance(row, dict):
            return None
        for field in ROW_POINTER_FIELDS:
            key = parse_pointer(self.prefix, row.get(field))
            if key is not None:
                return key
        return None

    def _is_experiment_pointer(self, item: Any) -> bool:
        if isinstance(item, str):
            return parse_pointer(self.prefix, item) is not None
        if isinstance(item, dict):
            return any(parse_pointer(self.prefix, item.get(f)) is not None
                       for f in SEMANTIX_POINTER_FIELDS)
        return False

    # -- stripping ---------------------------------------------------------

    def split(self, results: Any) -> Tuple[Any, List[Dict[str, Any]]]:
        """(results without experiment hits, the experiment hits).

        Returns a copy; the input is not changed. Runs whether or not the
        module is enabled.
        """
        if not isinstance(results, dict):
            return results, []
        out = dict(results)
        kept: List[Any] = []
        hits: List[Dict[str, Any]] = []
        for row in results.get("results") or []:
            if self.key_of(row) is not None:
                hits.append(row)
            else:
                kept.append(row)
        if "results" in results or hits:
            out["results"] = kept
        semantix = results.get("semantix")
        if isinstance(semantix, dict):
            meta = dict(semantix)
            removed = len(hits)
            pointers = semantix.get("pointers")
            if isinstance(pointers, list):
                remaining = [p for p in pointers if not self._is_experiment_pointer(p)]
                removed = len(pointers) - len(remaining)
                meta["pointers"] = remaining
            count = semantix.get("result_count")
            if isinstance(count, int) and not isinstance(count, bool):
                meta["result_count"] = max(0, count - removed)
            out["semantix"] = meta
        if "total" in results or hits:
            out["total"] = len(kept)
        return out, hits

    # -- rows for viewers --------------------------------------------------

    def rows(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Result rows for the experiment hits, for people allowed to see them.

        Never raises: a failure here must not take the normal search down.
        """
        if not self.enabled or not hits:
            return []
        try:
            user = self._current_user()
            if user is None or not permissions.can_view(user):
                return []
            conn = self._connection()
            store = _store()
            if not store.get_runtime_setting(conn, SHOW_IN_SEARCH_KEY):
                return []
            if not store.get_user_show_in_search(conn, user.id):
                return []
            keyed: List[Tuple[str, Dict[str, Any]]] = []
            seen = set()
            for hit in hits:
                key = self.key_of(hit)
                if key is None or key in seen:
                    continue
                seen.add(key)
                keyed.append((key, hit))
            if not keyed:
                return []
            found = store.lookup_by_keys(conn, [key for key, _ in keyed]) or {}
            return [self._row(key, hit, found[key]) for key, hit in keyed if key in found]
        except Exception:  # noqa: BLE001
            logger.exception("Experiment hits could not be prepared for the search result.")
            return []

    def _row(self, key: str, hit: Dict[str, Any], info: Dict[str, Any]) -> Dict[str, Any]:
        row: Dict[str, Any] = {}
        for field in ("doc_id", "path", "score", "final_score"):
            if field in hit:
                row[field] = hit[field]
        for field, value in hit.items():
            if isinstance(field, str) and field.startswith("cosine_"):
                row[field] = value
        if not row.get("doc_id"):
            for field in ROW_POINTER_FIELDS:
                if parse_pointer(self.prefix, hit.get(field)) is not None:
                    row["doc_id"] = hit[field]
                    break
        text = _first_text(hit.get("top_chunks")) or str(info.get("hypothesis") or "")
        snippet = _snippet(text)
        row.update({
            "result_kind": "experiment",
            "title": f"{key} \u00b7 {info.get('title') or ''}",
            "app_url": f"/experiments/{key}",
            "experiment": {
                "key": key,
                "domain_key": info.get("domain_key"),
                "domain_name": info.get("domain_name"),
                "domain_color": info.get("domain_color"),
                "status_label": info.get("status_label"),
                "type_name": info.get("type_name"),
            },
            "context_snippet": snippet,
            "snippet": snippet,
            "file_exists": False,
            "can_open": False,
        })
        return row
