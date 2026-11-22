"""Acceptance tests of PMVP-34 (frozen): no Brain view draws more than 25 things.

Plan FEAT-2026-11-19-platform-mvp B-01 ("No view draws more than 25 nodes. Everything beyond is a count that opens a
list"), section 11 ("Nodes drawn in any view: never more than 25 -- automatic test on PMVP-34"); spec item
SPEC-platform-mvp-09; contract L2-PLATFORM-BRAIN-VIEWS POST-01, POST-03. The coordinator's decisions of 2026-11-22 in
docs/platform-mvp/work-orders/PMVP-34.md: what a view draws is marked ``data-bv-node``; the rows of a list the count
opens are marked ``data-bv-row`` and are not drawn (lists are paged at 50); the link that opens the rest carries
``data-bv-more="<n>"``.

What is pinned:

- ``brain_views.limit``: ``MAX_DRAWN == 25``; ``bounded(items, *, limit=MAX_DRAWN, reserved=0)`` -> ``shown`` (a
  tuple, in the order given), ``rest``, ``total``, and ``ValueError`` for a limit above 25 or a negative limit or
  ``reserved``; ``more_text(rest)`` -> ``""``, "1 weiterer Eintrag", "<n> weitere"; ``paged(items, page, *,
  size=50)`` -> ``items``, ``page``, ``pages``, ``total``, a page out of range giving the nearest page.
- **Every view page.** Every GET route of the ``brain_views`` blueprint that takes a ``<node_id>`` is opened for one
  thing with a very large neighbourhood, and every link on a page that leads to another ``brain_views`` GET route is
  followed (one page per route and set of query keys, two links deep). On every page that answers 200 with HTML: at
  most 25 ``data-bv-node``; each ``data-bv-more`` holds a positive count and its link answers 200. Routes are found
  in the app, so a view added later is checked without editing this file.
- The Core is asked per thing only: a whole-tenant read fails the test (POST-03).
"""
from __future__ import annotations

import copy
import re
from urllib.parse import parse_qsl, urlsplit

import pytest

pytest.importorskip("flask")

NODE = "6f1c2c1e-0000-4000-8000-000000000034"
TYPE = "7a2b0000-0000-4000-8000-000000000034"
KINDS = [f"7a2b0000-0000-4000-8000-0000000001{k:02d}" for k in range(6)]
WHOLE_TENANT = ("graph_export", "graph_edges", "graph_nodes", "graph_node_types")
MANY = 140

NODE_MARK = re.compile(r"\bdata-bv-node\b")
ROW_MARK = re.compile(r"\bdata-bv-row\b")
MORE_TAG = re.compile(r"<a\b[^>]*\bdata-bv-more=\"([^\"]*)\"[^>]*>")
HREF = re.compile(r"\bhref=\"([^\"]+)\"")


def _uuid(n):
    return f"6f1c2c1e-0000-4000-8000-{n:012d}"


class Neighbourhood:
    """One thing with 140 facts (40 of them references to other things), 140 documents and 140 related things of six
    kinds, as the Core's per-thing routes give them; every whole-tenant read raises."""

    def __init__(self, fake, monkeypatch):
        self.fake = fake
        attributes = [{"id": "a-text", "name": "Angabe", "datatype": "text", "sort_order": 1},
                      {"id": "a-ref", "name": "Partei", "datatype": "entity_ref", "sort_order": 2}]
        self.attributes = attributes
        facts = []
        for n in range(MANY):
            ref = n < 40
            facts.append({"id": f"f-{n}", "node_id": NODE, "attribute_id": "a-ref" if ref else "a-text",
                          "label": None, "value": _uuid(1000 + n) if ref else f"Wert {n}",
                          "provenance_pointer": f"corpus/akte/dokument-{n:03d}.pdf", "provenance_chunk_id": f"c-{n}",
                          "provenance_status": "ok", "curation_status": "extracted", "confidence": None})
        self.node = {
            "node": {"id": NODE, "name": "Limmat Immobilien AG", "node_type_id": TYPE, "description": "Mandant"},
            "assignments": [{"id": f"as-{n}", "node_id": NODE, "pointer": f"corpus/akte/dokument-{n:03d}.pdf",
                             "chunk_id": None, "status": "active", "rank": 0.5} for n in range(MANY)],
            "sections": [],
            "facts": facts,
        }
        self.neighbours = [{"id": _uuid(2000 + n), "name": f"Verbunden {n:03d}", "node_type_id": KINDS[n % 6],
                            "hop": 1} for n in range(MANY)]
        self.edges = [{"id": f"e-{n}", "node_lo": NODE, "node_hi": _uuid(2000 + n), "relation": "betrifft"}
                      for n in range(MANY)]
        self.whole_tenant_reads = []
        monkeypatch.setattr(fake, "graph_node", self.graph_node, raising=False)
        monkeypatch.setattr(fake, "graph_get_many", self.graph_get_many, raising=False)
        monkeypatch.setattr(fake, "graph_neighbors", self.graph_neighbors, raising=False)
        monkeypatch.setattr(fake, "graph_facts_batch", self.graph_facts_batch, raising=False)
        monkeypatch.setattr(fake, "document_readable", lambda pointer: True, raising=False)
        for name in WHOLE_TENANT:
            monkeypatch.setattr(fake, name, self._refused(name), raising=False)

    def graph_node(self, node_id):
        if node_id == NODE:
            return {"status": "success", **copy.deepcopy(self.node)}
        if node_id.startswith("6f1c2c1e-"):
            return {"status": "success", "node": {"id": node_id, "name": f"Eintrag {node_id[-4:]}",
                                                  "node_type_id": KINDS[0]},
                    "assignments": [], "sections": [], "facts": []}
        return None

    def graph_get_many(self, calls, *, workers=4, acquire=None, max_retries=3):
        answers = []
        for path, _params in calls:
            match = re.fullmatch(r"/node-types/([0-9a-f-]{36})(/schema)?", path)
            if match and match.group(2):
                answers.append((200, {"status": "success", "attributes": copy.deepcopy(self.attributes)}))
            elif match:
                answers.append((200, {"status": "success", "node_type": {"id": match.group(1),
                                                                         "name": f"Art {match.group(1)[-2:]}"}}))
            else:
                answers.append((404, {"status": "error"}))
        return answers

    def graph_neighbors(self, node_id, depth=1, include_edges=False):
        if node_id != NODE:
            return {"neighbors": [], "edges": []}
        return {"neighbors": copy.deepcopy(self.neighbours),
                "edges": copy.deepcopy(self.edges) if include_edges else []}

    def graph_facts_batch(self, node_ids):
        return 200, {"status": "success", "facts": []}

    def _refused(self, name):
        def refuse(*args, **kwargs):
            self.whole_tenant_reads.append(name)
            raise AssertionError(f"a Brain view made a whole-tenant read: {name}")
        return refuse


@pytest.fixture
def neighbourhood(fake_graph, monkeypatch):
    monkeypatch.setenv("BRAIN_VIEWS_ENABLED", "true")
    return Neighbourhood(fake_graph, monkeypatch)


# --- the shared function ------------------------------------------------------------------------------------------


def test_the_limit_is_25():
    from brain_views import limit

    assert limit.MAX_DRAWN == 25


@pytest.mark.parametrize("count, shown, rest", [(0, 0, 0), (1, 1, 0), (24, 24, 0), (25, 25, 0), (26, 25, 1),
                                                (140, 25, 115)])
def test_bounded_shows_at_most_25_in_the_order_given(count, shown, rest):
    from brain_views.limit import bounded

    items = [f"x{n}" for n in range(count)]
    result = bounded(items)
    assert isinstance(result.shown, tuple)
    assert list(result.shown) == items[:shown]
    assert (result.rest, result.total) == (rest, count)


def test_bounded_counts_what_the_view_draws_anyway():
    from brain_views.limit import bounded

    result = bounded(range(140), reserved=1)
    assert len(result.shown) == 24 and result.rest == 116 and result.total == 140
    smaller = bounded(range(140), limit=12)
    assert len(smaller.shown) == 12 and smaller.rest == 128


@pytest.mark.parametrize("kwargs", [{"limit": 26}, {"limit": -1}, {"reserved": -1}, {"limit": 100}])
def test_bounded_refuses_more_than_25_and_negative_numbers(kwargs):
    from brain_views.limit import bounded

    with pytest.raises(ValueError):
        bounded(range(10), **kwargs)


def test_more_text_says_how_many_more():
    from brain_views.limit import more_text

    assert more_text(0) == ""
    assert more_text(1) == "1 weiterer Eintrag"
    assert more_text(115) == "115 weitere"


def test_paged_gives_pages_of_50_and_the_nearest_page_when_out_of_range():
    from brain_views.limit import paged

    items = list(range(140))
    first = paged(items, 1)
    assert list(first.items) == items[:50] and (first.page, first.pages, first.total) == (1, 3, 140)
    assert list(paged(items, 3).items) == items[100:]
    assert paged(items, 9).page == 3 and list(paged(items, 9).items) == items[100:]
    assert paged(items, 0).page == 1
    assert list(paged(items, 2, size=25).items) == items[25:50]
    empty = paged([], 1)
    assert list(empty.items) == [] and empty.pages == 1 and empty.total == 0


# --- every view page --------------------------------------------------------------------------------------------


def _view_rules(app):
    return [rule for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith("brain_views.") and "GET" in rule.methods and "node_id" in rule.arguments]


def _start(rule):
    path = rule.rule.replace("<node_id>", NODE)
    return re.sub(r"<(?:int:)?[^>]+>", "1", path)


def _route_of(app, url):
    """(endpoint, query keys) when ``url`` leads to a GET route of the views, else None."""
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path.startswith("/"):
        return None
    try:
        endpoint, _ = app.url_map.bind("localhost").match(parts.path, method="GET")
    except Exception:  # noqa: BLE001 - not a route of this app, or not GET
        return None
    if not endpoint.startswith("brain_views."):
        return None
    return endpoint, tuple(sorted(key for key, _ in parse_qsl(parts.query)))


def _crawl(app, client):
    """Every view page reachable from the views' routes, one page per route and set of query keys."""
    seen, pages, frontier = set(), {}, [_start(rule) for rule in _view_rules(app)]
    for _depth in range(3):
        following = []
        for url in frontier:
            shape = _route_of(app, url)
            if shape is None or shape in seen:
                continue
            seen.add(shape)
            response = client.get(url)
            if response.status_code != 200 or "html" not in (response.content_type or ""):
                continue
            body = response.get_data(as_text=True)
            pages[url] = body
            following.extend(h.replace("&amp;", "&") for h in HREF.findall(body))
        frontier = following
    return pages


def test_every_view_page_draws_at_most_25(workbench_app, member_client, neighbourhood):
    pages = _crawl(workbench_app, member_client)
    assert any(urlsplit(url).path == f"/dossier/{NODE}" for url in pages), "the dossier was not among the pages"
    for url, body in pages.items():
        drawn = len(NODE_MARK.findall(body))
        assert drawn <= 25, f"{urlsplit(url).path} draws {drawn} things"
    assert neighbourhood.whole_tenant_reads == []


def test_the_rest_is_a_count_that_opens_a_list(workbench_app, member_client, neighbourhood):
    pages = _crawl(workbench_app, member_client)
    for url, body in pages.items():
        for tag in MORE_TAG.finditer(body):
            assert tag.group(1).isdigit() and int(tag.group(1)) > 0, f"{urlsplit(url).path}: a count that is no count"
            href = HREF.search(tag.group(0))
            assert href, f"{urlsplit(url).path}: the count opens nothing"
            opened = member_client.get(href.group(1).replace("&amp;", "&"))
            assert opened.status_code == 200, f"{urlsplit(url).path}: the count's list answers {opened.status_code}"


def test_the_dossier_draws_its_thing_and_lists_its_facts(member_client, neighbourhood):
    body = member_client.get(f"/dossier/{NODE}").get_data(as_text=True)
    assert 1 <= len(NODE_MARK.findall(body)) <= 25
    assert len(ROW_MARK.findall(body)) >= MANY, "the facts are rows of a list, each marked data-bv-row"
