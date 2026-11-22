"""The lists of the Brain views (B-03): sorted, filtered and paged over what was read, as the dossier's kinds and
documents (PMVP-30b) and the process line's instances (PMVP-32b) show them. Moved from brain_views/related.py and
web_interface/brain_views_routes.py by PMVP-32a without a change of behaviour, when a second view needed them.

By name A to Z (the default) or Z to A, or by relation; one filter text over the names, case and spacing ignored, at
most ``FILTER_MAX`` characters; ``brain_views.limit.paged`` cuts the pages of 50. ``listing`` gives a page what it
draws; the route hands in ``href``, which builds the address of a page of this list (url_for), so nothing here
imports Flask. Nothing here logs: a name and a filter text are the firm's text.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Callable, Dict, List, Optional, Sequence

from brain_views.limit import page_number, paged

FILTER_MAX = 100
SORT_NAME = "name"
SORT_NAME_DESC = "name-ab"
SORT_RELATION = "beziehung"
SORT_LABELS = {SORT_NAME: "Name A–Z", SORT_NAME_DESC: "Name Z–A", SORT_RELATION: "Beziehung"}


def _folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def sort_key(text: str) -> str:
    """A name as the lists sort it: case, accents and spacing ignored («Ärzte» beside «Arzt», not after «Zürich»)."""
    decomposed = unicodedata.normalize("NFKD", text.replace("ß", "ss"))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split()).casefold()


def filter_text(raw: Any) -> str:
    """The filter as the page keeps it: spacing collapsed, at most FILTER_MAX characters, no control characters."""
    if not isinstance(raw, str):
        return ""
    text = "".join(c for c in raw if unicodedata.category(c)[0] != "C")
    return " ".join(text.split())[:FILTER_MAX].strip()


def matches(name: str, needle: str) -> bool:
    """Whether a name holds the filter text, case and spacing ignored."""
    return not needle or _folded(needle) in _folded(name)


def sort_choice(raw: Any, *, relations: bool) -> str:
    """The list's sort from the address: one of the known keys, else by name A to Z."""
    allowed = (SORT_NAME, SORT_NAME_DESC, SORT_RELATION) if relations else (SORT_NAME, SORT_NAME_DESC)
    return raw if raw in allowed else SORT_NAME


def arrange(items: Sequence[Any], *, name: Callable[[Any], str], needle: str, sort: str,
            relation: Optional[Callable[[Any], Optional[str]]] = None) -> List[Any]:
    """The items the filter keeps, in the chosen order (by relation: things without one last, then by name)."""
    kept = [item for item in items if matches(name(item), needle)]
    by_name = sorted(kept, key=lambda item: sort_key(name(item)))
    if sort == SORT_NAME_DESC:
        return list(reversed(by_name))
    if sort == SORT_RELATION and relation is not None:
        return sorted(by_name, key=lambda item: (relation(item) is None, sort_key(relation(item) or "")))
    return by_name


def listing(items: Any, args: Any, *, href: Callable[..., str], keep: Dict[str, str],
            name: Callable[[Any], str], relation: Optional[Callable[[Any], Optional[str]]] = None) -> Dict[str, Any]:
    """One list: filtered and sorted over what was read, 50 a page; the form's fields and the page links.

    ``args`` is the address's query; ``href(**query)`` builds the address of this list with that query. Only a known
    sort key and the cleaned filter go into an address; ``keep`` holds the list's own key (a tab, a kind's key as the
    Core gave it), which the form sends again."""
    items = list(items)
    has_relations = relation is not None and any(relation(item) for item in items)
    needle = filter_text(args.get("filter"))
    sort = sort_choice(args.get("sort"), relations=has_relations)
    arranged = arrange(items, name=name, needle=needle, sort=sort, relation=relation if has_relations else None)
    shown = paged(arranged, page_number(args.get("seite")))
    query = dict(keep)
    if sort != SORT_NAME:
        query["sort"] = sort
    if needle:
        query["filter"] = needle

    def at(number: Optional[int]) -> Optional[str]:
        if number is None:
            return None
        return href(**query, **({"seite": number} if number > 1 else {}))

    choices = (SORT_NAME, SORT_NAME_DESC) + ((SORT_RELATION,) if has_relations else ())
    return {
        "page": shown,
        "total": len(items),
        "filter": needle,
        "sort": sort,
        "sorts": [{"key": key, "label": SORT_LABELS[key]} for key in choices],
        "action": href(),
        "hidden": keep,
        "previous_href": at(shown.previous),
        "next_href": at(shown.next),
        "next_count": min(shown.size, shown.total - shown.last) if shown.next else 0,
        "rows": [],
    }
