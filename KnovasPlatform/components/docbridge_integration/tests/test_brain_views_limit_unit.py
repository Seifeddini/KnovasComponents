"""PMVP-34: the builder's own tests of ``brain_views.limit``, beyond the frozen acceptance test.

The frozen ``test_pmvp34_view_limit_acceptance.py`` pins ``MAX_DRAWN``, ``bounded``, ``more_text`` and ``paged`` and
counts what every view page draws. These pin what it leaves open: a ``reserved`` above the limit, the types the
functions refuse, a page number as an address gives it, a page's neighbours and places, and the module's own rules
(no Flask, the app or the client; ASCII only).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from brain_views import limit
from brain_views.limit import MAX_DRAWN, PAGE_SIZE, bounded, more_text, page_number, paged

SOURCE = Path(limit.__file__)


def test_a_view_that_draws_more_than_its_limit_anyway_is_refused():
    with pytest.raises(ValueError):
        bounded(range(10), limit=3, reserved=4)
    assert bounded(range(10), limit=3, reserved=3).shown == ()
    assert bounded(range(10), limit=3, reserved=3).rest == 10


@pytest.mark.parametrize("kwargs", [{"limit": 2.5}, {"limit": "25"}, {"limit": True}, {"reserved": None},
                                    {"reserved": 1.0}])
def test_only_whole_numbers_are_limits(kwargs):
    with pytest.raises(ValueError):
        bounded(range(10), **kwargs)


def test_bounded_takes_any_iterable_once():
    generated = (n for n in range(30))
    result = bounded(generated, reserved=1)
    assert result.shown == tuple(range(24)) and result.rest == 6 and result.total == 30


def test_the_result_is_frozen():
    result = bounded(range(3))
    with pytest.raises(AttributeError):
        result.rest = 0  # type: ignore[misc]


def test_more_text_refuses_what_is_no_count():
    for wrong in (-1, 1.5, "3", None, True):
        with pytest.raises(ValueError):
            more_text(wrong)  # type: ignore[arg-type]
    assert more_text(2) == "2 weitere"


@pytest.mark.parametrize("given, number", [("2", 2), ("  3 ", 3), ("0", 1), ("-2", 1), ("zwei", 1), ("", 1),
                                           (None, 1), ("2.5", 1), ("٣", 1), ("1" * 12, 10 ** 9), (7, 7), (-4, 1),
                                           (True, 1)])
def test_a_page_number_as_an_address_gives_it(given, number):
    assert page_number(given) == number


def test_paged_takes_the_page_as_an_address_gives_it():
    items = list(range(140))
    assert paged(items, "2").page == 2
    assert paged(items, "x").page == 1
    assert paged(items, "999999").page == 3
    assert paged(items, "99999999999").page == 3 and paged(items, "9" * 5000).page == 3   # FU-49: the last page


def test_a_page_knows_its_neighbours_and_its_places():
    items = list(range(140))
    first, middle, last = paged(items, 1), paged(items, 2), paged(items, 3)
    assert (first.previous, first.next) == (None, 2)
    assert (middle.previous, middle.next) == (1, 3)
    assert (last.previous, last.next) == (2, None)
    assert (first.first, first.last) == (1, 50)
    assert (last.first, last.last) == (101, 140)
    empty = paged([], 4)
    assert (empty.page, empty.previous, empty.next, empty.first, empty.last) == (1, None, None, 0, 0)


def test_a_page_holds_at_least_one_row():
    with pytest.raises(ValueError):
        paged(range(3), 1, size=0)
    with pytest.raises(ValueError):
        paged(range(3), 1, size=-5)


def test_the_numbers_of_the_plan():
    assert MAX_DRAWN == 25 and PAGE_SIZE == 50


def test_the_module_imports_nothing_of_flask_the_app_or_the_client():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "math", "dataclasses", "typing"}, imported


def test_the_module_is_ascii_only():
    SOURCE.read_bytes().decode("ascii")
