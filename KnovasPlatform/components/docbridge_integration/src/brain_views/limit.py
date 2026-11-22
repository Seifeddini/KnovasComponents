"""The 25-node limit of the members' Brain views (PMVP-34, plan FEAT-2026-11-19-platform-mvp B-01, section 11).

"No view draws more than 25 nodes. Everything beyond is a count that opens a list." Every view takes what it draws
through ``bounded`` and the lists its counts open through ``paged``; the frozen test
``tests/test_pmvp34_view_limit_acceptance.py`` opens every view page and counts what it draws.

What a view draws and what it lists is marked in its mark-up (the coordinator's decision D1 of 2026-11-22):

- ``data-bv-node`` on each thing a view **draws**: its starting point, a step of a process line, a circle, a card
  that stands for a thing. At most ``MAX_DRAWN`` on a page.
- ``data-bv-row`` on each **row of a list**. A list is where the rest goes: paged (``PAGE_SIZE`` rows a page), sorted
  and filtered (B-03). Its rows are not drawn nodes.
- ``data-bv-more="<n>"`` on the link that opens the rest, next to its count (``more_text``).

Pure functions: nothing of Flask, the app or the Knovas client. ASCII only; German words are written as escapes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Tuple

MAX_DRAWN = 25
PAGE_SIZE = 50

__all__ = ["MAX_DRAWN", "PAGE_SIZE", "Bounded", "Page", "bounded", "more_text", "page_number", "paged"]


@dataclass(frozen=True)
class Bounded:
    """What a view draws (``shown``, in the order given), how many it leaves out (``rest``) and how many there were."""

    shown: Tuple[Any, ...]
    rest: int
    total: int


@dataclass(frozen=True)
class Page:
    """One page of a list: its rows, its number (1 is the first), how many pages and rows the list has."""

    items: Tuple[Any, ...]
    page: int
    pages: int
    total: int
    size: int = PAGE_SIZE

    @property
    def previous(self) -> Optional[int]:
        """The page before this one, or None on the first."""
        return self.page - 1 if self.page > 1 else None

    @property
    def next(self) -> Optional[int]:
        """The page after this one, or None on the last."""
        return self.page + 1 if self.page < self.pages else None

    @property
    def first(self) -> int:
        """The place of this page's first row in the whole list (1 is the first; 0 for an empty list)."""
        return (self.page - 1) * self.size + 1 if self.items else 0

    @property
    def last(self) -> int:
        """The place of this page's last row in the whole list (0 for an empty list)."""
        return (self.page - 1) * self.size + len(self.items)


def _count(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be a whole number")
    if value < 0:
        raise ValueError(f"{what} must not be negative")
    return value


def bounded(items: Iterable[Any], *, limit: int = MAX_DRAWN, reserved: int = 0) -> Bounded:
    """At most ``limit - reserved`` of ``items``, in the order given, and how many are left out.

    ``reserved`` counts what the view draws anyway (its starting point, for example). A view may draw fewer than
    ``MAX_DRAWN``, never more: a ``limit`` above it, a negative ``limit`` or ``reserved``, or a ``reserved`` above the
    ``limit`` raises ValueError.
    """
    limit = _count(limit, "limit")
    reserved = _count(reserved, "reserved")
    if limit > MAX_DRAWN:
        raise ValueError(f"a view draws at most {MAX_DRAWN} things")
    if reserved > limit:
        raise ValueError("what a view draws anyway is more than its limit")
    every = tuple(items)
    shown = every[:limit - reserved]
    return Bounded(shown=shown, rest=len(every) - len(shown), total=len(every))


def more_text(rest: int) -> str:
    """The words beside the count that opens the rest: "" for none, "1 weiterer Eintrag", "115 weitere"."""
    rest = _count(rest, "rest")
    if rest == 0:
        return ""
    if rest == 1:
        return "1 weiterer Eintrag"
    return f"{rest} weitere"


def page_number(value: Any) -> int:
    """A page number as an address gives it (``?seite=2``): a whole number from 1, anything else is page 1.
    ``paged`` turns a number past the last page into the last page."""
    if isinstance(value, bool):
        return 1
    if isinstance(value, int):
        return max(value, 1)
    text = str(value).strip() if value is not None else ""
    if not text.isascii() or not text.isdigit() or len(text) > 9:
        return 1
    return max(int(text), 1)


def paged(items: Iterable[Any], page: int, *, size: int = PAGE_SIZE) -> Page:
    """Page ``page`` of ``items`` (1 is the first), ``size`` rows a page. A page out of range gives the nearest
    page (the first or the last), never an error; an empty list has one empty page."""
    size = _count(size, "size")
    if size == 0:
        raise ValueError("a page holds at least one row")
    every = tuple(items)
    pages = max(1, math.ceil(len(every) / size))
    number = page if isinstance(page, int) and not isinstance(page, bool) else page_number(page)
    number = min(max(number, 1), pages)
    start = (number - 1) * size
    return Page(items=every[start:start + size], page=number, pages=pages, total=len(every), size=size)
