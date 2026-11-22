"""PMVP-30b: the dossier's related things by kind, as tabs with counts and lists (plan B-01, B-03; contract
L2-PLATFORM-BRAIN-VIEWS PRE-01, POST-01, POST-03).

1. The gate is the dossier's: signed out 302, switched off 404, a hidden or missing thing 404, no accounts 404.
2. The tabs: «Überblick», one tab per kind with its count in the plural (by count, then by name; «Weitere Einträge»
   last), «Dokumente»; the starting thing never counted; an unknown ``art`` shows the «Überblick».
3. A kind's list: sorted A-Z and Z-A, and by relation; filtered; 50 a page; a page out of range shows the last one;
   each row links to that thing's dossier and is marked ``data-bv-row``; nothing is drawn beyond the thing.
4. «Dokumente» sorted, filtered and paged; position n opens the same document whatever the sort, filter and page.
5. A failed neighbours read: the dossier opens with one line; a 404 or a graph switched off: no kind tabs.
6. Every read per thing, as the person; no whole-tenant read; every address from url_for; text stays text, and no log
   line carries a name, a relation or a filter text.

The Core is the shared FakeGraphApi with the per-thing answers put on it here (``near``).
"""
from __future__ import annotations

import copy
import html as html_lib
import logging
import re

import pytest

pytest.importorskip("flask")

NODE = "6f1c2c1e-0000-4000-8000-0000000b0001"
HIDDEN = "6f1c2c1e-0000-4000-8000-0000000b0002"
MISSING = "6f1c2c1e-0000-4000-8000-0000000b0003"
TYPE = "7a2b0000-0000-4000-8000-0000000b00aa"
MANDAT, VERTRAG, PERSON, SONDER = (f"7a2b0000-0000-4000-8000-0000000b00{n:02d}" for n in range(1, 5))
KIND_NAMES = {TYPE: "Mandant", MANDAT: "Mandat", VERTRAG: "Vertrag", PERSON: "Person", SONDER: "Sonderfall"}
WHOLE_TENANT = ("graph_export", "graph_edges", "graph_nodes", "graph_node_types")
SCRIPT = "<script>alert(1)</script>"

NODE_MARK = re.compile(r"\bdata-bv-node\b")
ROW_MARK = re.compile(r"\bdata-bv-row\b")


def _id(n):
    return f"6f1c2c1e-0000-4000-8000-{n:012d}"


def _edge(a, b, relation):
    lo, hi = sorted((a, b))
    return {"id": f"e-{a[-4:]}-{b[-4:]}-{relation}", "node_lo": lo, "node_hi": hi, "relation": relation}


class Near:
    """The Core's per-thing answers for one thing and its neighbours, as graph_api.py gives them.

    ``calls`` records every read with the person signed in when it was made. ``HIDDEN`` answers as a missing node
    for everyone (node and neighbours). ``neighbours_error`` makes the neighbours read fail."""

    def __init__(self, fake, monkeypatch):
        self.fake = fake
        self.calls: list[tuple] = []
        self.neighbours_error = None
        self.documents = [f"corpus/Limmat/Akte {n:03d}.pdf" for n in range(120)]
        self.node = {"id": NODE, "name": "Limmat Immobilien AG", "node_type_id": TYPE, "description": "Mandant"}
        self.neighbours = ([{"id": NODE, "name": "Limmat Immobilien AG", "node_type_id": TYPE, "hop": 0}]
                           + [{"id": _id(100 + n), "name": f"Mandat 2026-{n:04d}", "node_type_id": MANDAT, "hop": 1}
                              for n in range(7)]
                           + [{"id": _id(200 + n), "name": f"Mietvertrag {n:03d}", "node_type_id": VERTRAG, "hop": 1}
                              for n in range(120)]
                           + [{"id": _id(3000 + n), "name": name, "node_type_id": PERSON, "hop": 1}
                              for n, name in enumerate(["Vogt", "Keller", "Huber", "Meier", "Graf", "Frei"])]
                           + [{"id": _id(400), "name": "Ein Sonderfall", "node_type_id": SONDER, "hop": 1},
                              {"id": _id(500), "name": "Ohne Art", "node_type_id": None, "hop": 1},
                              {"id": _id(600), "name": "Zwei Schritte weg", "node_type_id": MANDAT, "hop": 2}])
        self.edges = ([_edge(NODE, _id(3000), "Kontakt"), _edge(_id(3000), NODE, "Partei"),
                       _edge(NODE, _id(3001), "Anwältin"), _edge(_id(3000), _id(3001), "kennt")]
                      + [_edge(NODE, _id(200 + n), "Vermieterin" if n % 2 else "Mieterin") for n in range(120)])
        monkeypatch.setattr(fake, "graph_node", self.graph_node, raising=False)
        monkeypatch.setattr(fake, "graph_neighbors", self.graph_neighbors, raising=False)
        monkeypatch.setattr(fake, "graph_get_many", self.graph_get_many, raising=False)
        monkeypatch.setattr(fake, "document_readable", self.document_readable, raising=False)
        for name in WHOLE_TENANT:
            monkeypatch.setattr(fake, name, self._refused(name), raising=False)

    def person(self):
        user = self.fake.principal_broker.current_user()
        return None if user is None else user.email

    def graph_node(self, node_id):
        self.calls.append(("graph_node", node_id, self.person()))
        if node_id != NODE:
            return None
        return {"status": "success", "node": dict(self.node), "sections": [], "facts": [],
                "assignments": [{"id": f"as{n}", "node_id": NODE, "pointer": p, "chunk_id": None, "status": "active"}
                                for n, p in enumerate(self.documents)]}

    def graph_neighbors(self, node_id, depth=1, include_edges=False):
        self.calls.append(("graph_neighbors", node_id, depth, include_edges, self.person()))
        if self.neighbours_error is not None:
            raise self.neighbours_error
        if node_id != NODE:
            return {"neighbors": [], "edges": []}          # the client's answer to the Core's 404
        return {"neighbors": copy.deepcopy(self.neighbours),
                "edges": copy.deepcopy(self.edges) if include_edges else []}

    def graph_get_many(self, calls, *, workers=4, acquire=None, max_retries=3):
        answers = []
        for path, _params in calls:
            self.calls.append(("graph_get_many", path, self.person()))
            match = re.fullmatch(r"/node-types/([0-9a-f-]{36})(/schema)?", path)
            if match and match.group(1) in KIND_NAMES and not match.group(2):
                answers.append((200, {"status": "success", "node_type": {"id": match.group(1),
                                                                         "name": KIND_NAMES[match.group(1)]}}))
            elif match and match.group(2):
                answers.append((200, {"status": "success", "attributes": []}))
            else:
                answers.append((404, {"status": "error"}))
        return answers

    def document_readable(self, pointer):
        self.calls.append(("document_readable", pointer, self.person()))
        return True

    def _refused(self, name):
        def refuse(*args, **kwargs):
            self.calls.append((name, "whole tenant", self.person()))
            raise AssertionError(f"the dossier made a whole-tenant read: {name}")
        return refuse


@pytest.fixture
def near(fake_graph, monkeypatch):
    monkeypatch.setenv("BRAIN_VIEWS_ENABLED", "true")
    return Near(fake_graph, monkeypatch)


def _page(client, node_id=NODE, **query):
    response = client.get(f"/dossier/{node_id}", query_string=query)
    return response, response.get_data(as_text=True)


def _tabs(html):
    """(label, count or None, href) of each tab, in order."""
    nav = html[html.index('class="k-tabs"'):html.index("</nav>", html.index('class="k-tabs"'))]
    out = []
    for href, inner in re.findall(r'<a class="k-tab[^"]*" href="([^"]*)"[^>]*>(.*?)</a>', nav, re.S):
        count = re.search(r'<span class="k-tab-count">(\d+)</span>', inner)
        label = re.sub(r"<[^>]+>", "", inner.split("<span", 1)[0]).strip()
        out.append((html_lib.unescape(label), int(count.group(1)) if count else None, html_lib.unescape(href)))
    return out


def _row_names(html):
    return [html_lib.unescape(n) for n in re.findall(r'<div class="bv-thing" data-bv-row>\s*<a class="k-link k-link--dotted" '
                                                     r'href="[^"]*">([^<]*)</a>', html)]


def _hrefs(html):
    return [html_lib.unescape(h) for h in re.findall(r'href="([^"]*)"', html)]


# --- 1: the gate ---------------------------------------------------------------------------------------------------


def test_a_signed_out_caller_is_sent_to_sign_in(near, anon_client):
    response = anon_client.get(f"/dossier/{NODE}?art={VERTRAG}")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert near.calls == []


def test_switched_off_a_kinds_list_answers_404_and_the_core_is_not_asked(near, member_client, monkeypatch):
    monkeypatch.delenv("BRAIN_VIEWS_ENABLED")
    assert _page(member_client, art=VERTRAG)[0].status_code == 404
    assert near.calls == []


def test_a_hidden_or_missing_thing_answers_404_and_its_neighbours_are_not_read(near, member_client):
    hidden, hidden_html = _page(member_client, HIDDEN, art=VERTRAG)
    missing, missing_html = _page(member_client, MISSING, art=VERTRAG)
    assert hidden.status_code == missing.status_code == 404 and hidden_html == missing_html
    assert [c for c in near.calls if c[0] == "graph_neighbors"] == []


def test_without_per_user_accounts_a_kinds_list_does_not_answer():
    from flask import Flask

    from web_interface.brain_views_routes import create_brain_views_blueprint

    app = Flask(__name__)
    asked = []
    app.register_blueprint(create_brain_views_blueprint(
        None, client=lambda: asked.append("client"), settings=lambda: asked.append("settings"),
        grant=lambda rows: asked.append("grant"), split=lambda r: asked.append("split"),
        enhance=lambda r: asked.append("enhance"), pdf_inline=True, download_allowed=lambda: asked.append("dl"),
        page_context=dict))
    assert app.test_client().get(f"/dossier/{NODE}?art={VERTRAG}").status_code == 404
    assert asked == []


# --- 2: the tabs ---------------------------------------------------------------------------------------------------


def test_one_tab_per_kind_with_its_count_in_the_plural(near, member_client):
    response, html = _page(member_client)
    assert response.status_code == 200
    assert [(label, count) for label, count, _ in _tabs(html)] == [
        ("Überblick", None), ("Verträge", 120), ("Mandate", 7), ("Personen", 6), ("Sonderfall", 1),
        ("Weitere Einträge", 1), ("Dokumente", 120)]


def test_the_tabs_are_addresses_of_this_dossier(near, member_client):
    _, html = _page(member_client)
    hrefs = [href for _, _, href in _tabs(html)]
    assert hrefs == [f"/dossier/{NODE}", f"/dossier/{NODE}?art={VERTRAG}", f"/dossier/{NODE}?art={MANDAT}",
                     f"/dossier/{NODE}?art={PERSON}", f"/dossier/{NODE}?art={SONDER}", f"/dossier/{NODE}?art=ohne",
                     f"/dossier/{NODE}?tab=dokumente"]
    _, html = _page(member_client, art=MANDAT)
    assert f'k-tab k-tab--on" href="/dossier/{NODE}?art={MANDAT}"' in html


def test_the_starting_thing_and_what_is_two_hops_away_are_never_counted(near, member_client):
    _, html = _page(member_client, art=MANDAT)
    assert "Zwei Schritte weg" not in html
    _, html = _page(member_client, art="ohne")
    assert _row_names(html) == ["Ohne Art"]
    assert f'href="/dossier/{NODE}"' in html and f">Limmat Immobilien AG</a>" not in html


@pytest.mark.parametrize("art", ["7a2b0000-0000-4000-8000-0000000b0099", "unbekannt", SCRIPT, ""])
def test_an_unknown_kind_shows_the_overview_and_its_key_is_not_echoed(near, member_client, art):
    response, html = _page(member_client, art=art)
    assert response.status_code == 200
    assert 'k-tab k-tab--on" href="/dossier/' + NODE + '"' in html
    assert "Angaben" in html
    if art:
        assert art not in html and html_lib.escape(art) not in html


def test_the_overview_draws_nothing_but_the_thing(near, member_client):
    _, html = _page(member_client)
    assert len(NODE_MARK.findall(html)) == 1
    assert "Mietvertrag 000" not in html


def test_a_thing_without_related_things_has_no_kind_tabs(near, member_client):
    near.neighbours = []
    _, html = _page(member_client)
    assert [label for label, _, _ in _tabs(html)] == ["Überblick", "Dokumente"]
    assert "Verbundene Einträge" not in html


# --- 3: a kind's list ---------------------------------------------------------------------------------------------


def test_a_kinds_list_is_sorted_by_name_and_each_row_opens_that_things_dossier(near, member_client):
    _, html = _page(member_client, art=PERSON)
    assert _row_names(html) == ["Frei", "Graf", "Huber", "Keller", "Meier", "Vogt"]
    assert f'href="/dossier/{_id(3000)}"' in html
    assert len(ROW_MARK.findall(html)) == 6 and len(NODE_MARK.findall(html)) == 1
    _, html = _page(member_client, art=PERSON, sort="name-ab")
    assert _row_names(html) == ["Vogt", "Meier", "Keller", "Huber", "Graf", "Frei"]


def test_a_rows_relation_stands_under_its_name(near, member_client):
    _, html = _page(member_client, art=PERSON)
    vogt = html[html.index(">Vogt</a>"):]
    assert vogt.index('class="bv-thing-relation k-muted k-small">Kontakt, Partei</span>') < 200
    keller = html[html.index(">Keller</a>"):]
    assert keller.index(">Anwältin</span>") < 200
    huber = html[html.index(">Huber</a>"):html.index(">Huber</a>") + 200]
    assert "bv-thing-relation" not in huber


def test_sorted_by_relation_when_the_kind_has_relations(near, member_client):
    _, html = _page(member_client, art=PERSON, sort="beziehung")
    assert _row_names(html) == ["Keller", "Vogt", "Frei", "Graf", "Huber", "Meier"]
    assert '<option value="beziehung" selected>Beziehung</option>' in html
    _, html = _page(member_client, art=MANDAT)
    assert 'value="beziehung"' not in html                # no relations, no such sort
    _, html = _page(member_client, art=MANDAT, sort="beziehung")
    assert _row_names(html)[0] == "Mandat 2026-0000"


def test_the_filter_keeps_the_names_that_hold_it_and_is_shown_back(near, member_client):
    _, html = _page(member_client, art=VERTRAG, filter="  mietvertrag   01 ")
    assert _row_names(html) == [f"Mietvertrag {n:03d}" for n in range(10, 20)]
    assert 'name="filter" value="mietvertrag 01"' in html
    _, html = _page(member_client, art=VERTRAG, filter="nichts davon")
    assert _row_names(html) == [] and "Kein Eintrag passt zu diesem Filter." in html


def test_the_filter_is_cut_at_100_characters(near, member_client):
    _, html = _page(member_client, art=VERTRAG, filter="m" * 150)
    assert 'value="' + "m" * 100 + '"' in html and "m" * 101 not in html


def test_a_list_has_pages_of_50(near, member_client):
    _, html = _page(member_client, art=VERTRAG)
    assert _row_names(html) == [f"Mietvertrag {n:03d}" for n in range(50)]
    assert "1–50 von 120 · Seite 1 von 3" in html
    assert f'href="/dossier/{NODE}?art={VERTRAG}&amp;seite=2">Weitere 50</a>' in html
    _, html = _page(member_client, art=VERTRAG, seite="2")
    assert _row_names(html) == [f"Mietvertrag {n:03d}" for n in range(50, 100)]
    assert f'href="/dossier/{NODE}?art={VERTRAG}">Zurück</a>' in html
    _, html = _page(member_client, art=VERTRAG, seite="3")
    assert _row_names(html) == [f"Mietvertrag {n:03d}" for n in range(100, 120)]
    assert not re.search(r">Weitere \d+</a>", html)


@pytest.mark.parametrize("seite", ["99", "0", "-1", "zwei", ""])
def test_a_page_out_of_range_shows_the_nearest_one(near, member_client, seite):
    response, html = _page(member_client, art=VERTRAG, seite=seite)
    assert response.status_code == 200
    expected = range(100, 120) if seite == "99" else range(50)
    assert _row_names(html) == [f"Mietvertrag {n:03d}" for n in expected]


def test_the_page_links_keep_the_sort_and_the_filter(near, member_client):
    _, html = _page(member_client, art=VERTRAG, sort="name-ab", filter="Miet")
    assert (f'href="/dossier/{NODE}?art={VERTRAG}&amp;sort=name-ab&amp;filter=Miet&amp;seite=2">Weitere 50</a>'
            in html)
    _, html = _page(member_client, art=VERTRAG, sort="<b>", filter="a&b")
    assert "&lt;b&gt;" not in html and 'name="filter" value="a&amp;b"' in html


def test_the_form_works_without_a_script(near, member_client):
    _, html = _page(member_client, art=VERTRAG)
    form = html[html.index('<form class="bv-listform"'):html.index("</form>")]
    assert 'method="get"' in form and f'action="/dossier/{NODE}"' in form
    assert f'<input type="hidden" name="art" value="{VERTRAG}">' in form
    assert 'name="filter"' in form and 'name="sort"' in form and 'type="submit"' in form
    assert "<script" not in html.split("<main", 1)[1]


def test_a_name_a_relation_and_a_filter_holding_mark_up_come_out_as_text(near, member_client):
    near.neighbours[1]["name"] = f"Mandat {SCRIPT}"
    near.edges.append(_edge(NODE, near.neighbours[1]["id"], f"Rolle {SCRIPT}"))
    _, html = _page(member_client, art=MANDAT, filter=SCRIPT[:20])
    assert SCRIPT not in html and "<script>alert" not in html
    _, html = _page(member_client, art=MANDAT)
    assert SCRIPT not in html
    assert html_lib.escape(f"Mandat {SCRIPT}", quote=False) in html
    assert html_lib.escape(f"Rolle {SCRIPT}", quote=False) in html


# --- 4: «Dokumente» --------------------------------------------------------------------------------------------------


def _documents(html):
    return re.findall(r'<span class="bv-doc-name">([^<]*)</span>', html)


def _opened(html):
    """{document name: the address that opens it} on this page."""
    rows = re.findall(r'<span class="bv-doc-name">([^<]*)</span>.*?<a class="k-link" href="([^"]*)">Öffnen</a>',
                      html, re.S)
    return dict(rows)


def test_documents_are_sorted_filtered_and_paged(near, member_client):
    _, html = _page(member_client, tab="dokumente")
    assert _documents(html) == [f"Akte {n:03d}.pdf" for n in range(50)]
    assert len(ROW_MARK.findall(html)) == 50 and len(NODE_MARK.findall(html)) == 1
    _, html = _page(member_client, tab="dokumente", seite="3")
    assert _documents(html) == [f"Akte {n:03d}.pdf" for n in range(100, 120)]
    _, html = _page(member_client, tab="dokumente", sort="name-ab")
    assert _documents(html)[:2] == ["Akte 119.pdf", "Akte 118.pdf"]
    _, html = _page(member_client, tab="dokumente", filter="akte 07")
    assert _documents(html) == [f"Akte {n:03d}.pdf" for n in range(70, 80)]
    assert '<input type="hidden" name="tab" value="dokumente">' in html
    assert 'value="beziehung"' not in html


def test_a_position_opens_the_same_document_whatever_the_sort_filter_and_page(near, member_client):
    whole = {f"Akte {n:03d}.pdf": f"/dossier/{NODE}/dokument/{n + 1}" for n in range(120)}
    for query in ({}, {"seite": "2"}, {"seite": "3"}, {"sort": "name-ab"}, {"sort": "name-ab", "seite": "2"},
                  {"filter": "akte 1"}, {"filter": "11", "sort": "name-ab"}):
        _, html = _page(member_client, tab="dokumente", **query)
        opened = _opened(html)
        assert opened, query
        for name, href in opened.items():
            assert href == whole[name], (query, name)
    near.calls.clear()
    response = member_client.get(f"/dossier/{NODE}/dokument/{120}")
    assert response.status_code == 302
    assert ("document_readable", "corpus/Limmat/Akte 119.pdf", near.calls[-1][2]) == near.calls[-1]


def test_an_empty_filter_on_the_documents_says_so(near, member_client):
    _, html = _page(member_client, tab="dokumente", filter="kein solches")
    assert _documents(html) == [] and "Kein Eintrag passt zu diesem Filter." in html


# --- 5: when the neighbours cannot be read ----------------------------------------------------------------------------


def test_a_failed_neighbours_read_is_one_line_and_the_dossier_still_opens(near, member_client):
    from knovas_client import GraphError

    near.neighbours_error = GraphError(500, "internal")
    for query in ({}, {"tab": "dokumente"}, {"art": VERTRAG}):
        response, html = _page(member_client, **query)
        assert response.status_code == 200
        assert html.count("Verbundene Einträge sind gerade nicht verfügbar.") == 1
        assert [label for label, _, _ in _tabs(html)] == ["Überblick", "Dokumente"]
    _, html = _page(member_client, tab="dokumente")
    assert _documents(html)[:1] == ["Akte 000.pdf"]


def test_a_graph_switched_off_shows_no_kind_tabs_and_no_line(near, member_client):
    from knovas_client import KnowledgeGraphDisabled

    near.neighbours_error = KnowledgeGraphDisabled("aus")
    _, html = _page(member_client)
    assert [label for label, _, _ in _tabs(html)] == ["Überblick", "Dokumente"]
    assert "Verbundene Einträge" not in html


# --- 6: per thing, as the person; addresses; logs --------------------------------------------------------------------


def test_one_neighbours_read_and_one_round_of_kind_reads_per_page_as_the_person(near, member_client, member):
    for query in ({}, {"art": VERTRAG}, {"tab": "dokumente"}):
        near.calls.clear()
        assert _page(member_client, **query)[0].status_code == 200
        assert [c for c in near.calls if c[0] == "graph_neighbors"] == [
            ("graph_neighbors", NODE, 1, True, member.email)]
        kind_reads = [c[1] for c in near.calls if c[0] == "graph_get_many" and c[1] != f"/node-types/{TYPE}/schema"]
        # the dossier's own kind, then the round of the neighbours' kinds
        assert kind_reads[0] == f"/node-types/{TYPE}"
        assert sorted(kind_reads[1:]) == sorted(f"/node-types/{k}" for k in (MANDAT, VERTRAG, PERSON, SONDER))
        assert {c[-1] for c in near.calls} == {member.email}


def test_no_whole_tenant_read_on_any_tab(near, member_client):
    for query in ({}, {"art": VERTRAG}, {"art": "ohne"}, {"tab": "dokumente", "seite": "2"}):
        assert _page(member_client, **query)[0].status_code == 200
    assert [c for c in near.calls if c[1] == "whole tenant"] == []


def test_every_address_on_the_pages_is_one_the_platform_built(near, member_client):
    near.neighbours[1]["id"] = "javascript:alert(1)"            # not an id: left out
    for query in ({}, {"art": VERTRAG, "filter": "//evil.example", "sort": "javascript:x"},
                  {"tab": "dokumente", "filter": "\"><a href=x>"}):
        _, html = _page(member_client, **query)
        for href in _hrefs(html):
            assert href.startswith("/") and not href.startswith("//"), href
        assert "javascript:" not in html


def test_no_log_line_carries_a_name_a_relation_or_a_filter_text(near, member_client, caplog):
    from knovas_client import GraphError

    near.neighbours[1]["name"] = "Mandat Hardturm Geheim"
    near.edges.append(_edge(NODE, near.neighbours[1]["id"], "Vermieterin Geheim"))
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="web_interface")
    caplog.set_level(logging.DEBUG, logger="brain_views")
    _page(member_client, art=MANDAT, filter="Hardturm Filterwort")
    _page(member_client, tab="dokumente", filter="Akte Filterwort")
    near.neighbours_error = GraphError(500, "internal", "Mandat Hardturm Geheim")
    _page(member_client, art=MANDAT)
    lines = [record.getMessage() for record in caplog.records]
    assert any("neighbours read failed (GraphError)" in line for line in lines)
    assert any("related things in" in line for line in lines)
    for line in lines:
        for word in ("Hardturm", "Geheim", "Filterwort", "Vermieterin", "Limmat", "Akte", "Mietvertrag", "Vogt"):
            assert word not in line, line
