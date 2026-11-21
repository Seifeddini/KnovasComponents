"""The dossier's facts and documents (PMVP-30a, plan B-04; L2-PLATFORM-BRAIN-VIEWS POST-02).

1. A fact whose source passage lies in one of the node's documents (as the Core listed them for the person) opens
   on click: its source document, and the dossier's own address that opens that document (its place in the list;
   test_brain_views_dossier_open.py follows it). Not the passage: no Core route reads one (FU-38, CR-2). The fact's
   source is read from provenance_pointer, provenance_chunk_id and provenance_status.
2. Every other fact carries the mark «ohne Beleg» and nothing that looks like a source: no source at all, a passage
   the Core reports missing, or a document the person was not handed (its name is not shown, and it is not opened).
3. Rejected facts are left out; the facts follow the order and the names of the type's fields.
4. The Dokumente tab: each document once, by name, with its folder and the address that opens it.
5. A name, a value, a label or a pointer holding mark-up comes out as text; no log line carries any of them.

The Core's answers are the fixture `brain` of test_brain_views_dossier_page.py.
"""
from __future__ import annotations

import html as html_lib
import logging
import re

import pytest

pytest.importorskip("flask")

from test_brain_views_dossier_page import (  # noqa: E402  (the shared fixture and the seeded node)
    FOREIGN, MEMO, MIETVERTRAG, NODE, TYPE, Brain, _fact, _page, brain)

__all__ = ["brain", "Brain"]

GATE_REFUSAL = {"success": False, "error": "Not found"}


def _segment(html: str, fact_id: str) -> str:
    """The mark-up of one fact: from its data-fact attribute to the next fact's, or to the note under the facts."""
    start = html.index(f'data-fact="{fact_id}"')
    ends = [i for i in (html.find('data-fact="', start + 1), html.find('class="bv-note', start)) if i != -1]
    return html[start:min(ends)] if ends else html[start:]


def _details(html: str, fact_id: str) -> str:
    assert f'<details class="bv-fact" data-fact="{fact_id}">' in html, f"the fact {fact_id} does not open"
    return _segment(html, fact_id)


def _plain(html: str, fact_id: str) -> str:
    assert f'<div class="bv-fact" data-fact="{fact_id}">' in html, f"the fact {fact_id} is not a plain line"
    return _segment(html, fact_id)


def _href(app, endpoint: str, pointer: str) -> str:
    """The address a file route has for a document (url_for, as the Platform builds it)."""
    with app.test_request_context():
        from flask import url_for

        return url_for(endpoint, doc_id=pointer, path=pointer)


def _opens(position: int) -> str:
    """The dossier's address that opens the document at this place of the list (1: the first, by name)."""
    return f"/dossier/{NODE}/dokument/{position}"


def _hrefs(html: str) -> list[str]:
    return [html_lib.unescape(h) for h in re.findall(r'href="([^"]*)"', html)]


# --- 1: a fact with its source -----------------------------------------------------------------------------------


def test_a_fact_with_a_source_passage_opens_its_document(brain, member_client):
    response, html = _page(member_client)
    assert response.status_code == 200
    opened = _details(html, "f-branche")
    assert "Branche" in opened and "Immobilienverwaltung" in opened
    assert "Quelle" in opened and "Mietvertrag Seefeldstrasse 48.pdf" in opened
    assert "corpus/Mandanten/Limmat" in opened                     # the folder it is in
    assert "ohne Beleg" not in opened
    # the Mietvertrag is the second of the node's documents by name (after the Memo)
    assert _hrefs(opened) == [_opens(2)]


def test_showing_the_dossier_hands_over_no_document(brain, member_client, workbench_app):
    _page(member_client)
    _page(member_client, tab="dokumente")
    for pointer in (MIETVERTRAG, MEMO):
        assert member_client.get(_href(workbench_app, "preview_document", pointer)).get_json() == GATE_REFUSAL


# --- 2: every other fact is marked -------------------------------------------------------------------------------


def test_a_fact_without_a_source_is_marked(brain, member_client):
    _, html = _page(member_client)
    line = _plain(html, "f-kontakt")
    assert "S. Vogt, CFO" in line
    assert '<span class="k-tag k-tag--warn">ohne Beleg</span>' in line
    assert "href" not in line and "Quelle" not in line


def test_a_fact_whose_source_the_person_was_not_handed_is_marked_and_its_document_stays_closed(
        brain, member_client, workbench_app):
    _, html = _page(member_client)
    line = _plain(html, "f-vertrag")
    assert '<span class="k-tag k-tag--warn">ohne Beleg</span>' in line
    assert "href" not in line
    assert "Vergleich Hardturm" not in html and "Mandanten/Wand" not in html
    for position in (1, 2, 3):
        member_client.get(_opens(position))
    href = _href(workbench_app, "preview_document", FOREIGN)
    assert member_client.get(href).get_json() == GATE_REFUSAL


@pytest.mark.parametrize("change", [
    {"provenance_status": "chunk_missing"},
    {"provenance_status": "pointer_detached"},
    {"provenance_chunk_id": None},
    {"provenance_chunk_id": "  "},
    {"provenance_pointer": None},
])
def test_a_fact_whose_passage_is_not_there_is_marked(brain, member_client, change):
    facts = brain.nodes[NODE]["facts"]
    facts[0].update(change)
    _, html = _page(member_client)
    assert '<span class="k-tag k-tag--warn">ohne Beleg</span>' in _plain(html, "f-branche")
    assert 'data-fact="f-branche"' in html and '<details class="bv-fact" data-fact="f-branche">' not in html


def test_no_fact_is_shown_with_a_source_unless_it_has_one(brain, member_client):
    _, html = _page(member_client)
    opened = re.findall(r'<details class="bv-fact" data-fact="([^"]+)"', html)
    assert opened == ["f-branche"]
    marked = re.findall(r'<div class="bv-fact" data-fact="([^"]+)"', html)
    assert marked == ["f-kontakt", "f-vertrag"]
    assert html.count("ohne Beleg") == 2 + 1      # two marks, and the note under the facts says what it means


# --- 3: which facts, in which order ------------------------------------------------------------------------------


def test_rejected_facts_are_left_out(brain, member_client):
    _, html = _page(member_client)
    assert "Verwaltung von Gewerbeflächen" not in html


def test_the_facts_follow_the_fields_of_the_type(brain, member_client):
    facts = brain.nodes[NODE]["facts"]
    facts.append(_fact("f-frei", "B", label="Notiz"))
    brain.attributes[0]["sort_order"] = 9                 # Branche now last of the fields
    _, html = _page(member_client)
    order = re.findall(r'<span class="bv-fact-label">([^<]*)</span>', html)
    assert order == ["Kontakt", "Erster Vertrag", "Branche", "Notiz"]


def test_a_reference_to_another_thing_links_to_its_dossier(brain, member_client):
    other = "6f1c2c1e-0000-4000-8000-0000000000ff"
    brain.nodes[NODE]["facts"].append(_fact("f-partei", other, "a-partei"))
    _, html = _page(member_client)
    assert f"/dossier/{other}" in _hrefs(_plain(html, "f-partei"))


@pytest.mark.parametrize("value, shown", [
    (True, "ja"), (1200, "1200"), (12.5, "12.5"), (3.0, "3"),
    ({"amount": 1200, "currency": "CHF"}, "CHF 1200"), (["a", "b"], "a, b"), ({"value": "x"}, "x"),
])
def test_a_value_is_one_line_of_text(value, shown):
    from brain_views.dossier import value_text

    assert value_text(value) == shown


# --- 4: the documents --------------------------------------------------------------------------------------------


def test_the_documents_tab_lists_each_document_once_with_its_address(brain, member_client):
    response, html = _page(member_client, tab="dokumente")
    assert response.status_code == 200
    names = re.findall(r'<span class="bv-doc-name">([^<]*)</span>', html)
    assert names == ["Memo Kuendigung.docx", "Mietvertrag Seefeldstrasse 48.pdf"]
    hrefs = _hrefs(html)
    assert _opens(1) in hrefs and _opens(2) in hrefs and _opens(3) not in hrefs
    assert '<span class="k-tab-count">2</span>' in html
    # the facts are on the other tab
    assert "Immobilienverwaltung" not in html


def test_every_address_on_the_page_is_one_the_platform_built(brain, member_client):
    for tab in ({}, {"tab": "dokumente"}):
        _, html = _page(member_client, **tab)
        for href in _hrefs(html):
            assert href.startswith("/"), href
            assert not href.startswith("//"), href


def test_a_node_without_documents_or_facts_says_so(brain, member_client):
    brain.nodes[NODE]["assignments"] = []
    brain.nodes[NODE]["facts"] = []
    _, html = _page(member_client)
    assert "Zu diesem Eintrag sind keine Angaben hinterlegt." in html
    _, html = _page(member_client, tab="dokumente")
    assert "Diesem Eintrag ist kein Dokument zugeordnet" in html


# --- 5: text stays text, and stays out of the logs ---------------------------------------------------------------

SCRIPT = "<script>alert(1)</script>"


def _poisoned(brain):
    node = brain.nodes[NODE]
    node["node"]["name"] = f"Limmat {SCRIPT}"
    node["node"]["description"] = f"Zürich {SCRIPT}"
    brain.node_type["name"] = f"Mandant {SCRIPT}"
    brain.attributes[0]["name"] = f"Branche {SCRIPT}"
    pointer = f"corpus/Mandanten/{SCRIPT}/Mietvertrag {SCRIPT}.pdf"
    node["assignments"][0]["pointer"] = node["assignments"][1]["pointer"] = pointer
    node["facts"][0].update(value=f"Immobilien {SCRIPT}", provenance_pointer=pointer)
    node["facts"][1].update(value=f"S. Vogt {SCRIPT}")
    node["facts"].append(_fact("f-frei", f"frei {SCRIPT}", label=f"Notiz {SCRIPT}"))


def test_mark_up_from_the_brain_comes_out_as_text(brain, member_client):
    _poisoned(brain)
    for tab in ({}, {"tab": "dokumente"}):
        response, html = _page(member_client, **tab)
        assert response.status_code == 200
        assert SCRIPT not in html and "<script>alert" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_no_log_line_carries_a_name_a_value_or_a_pointer(brain, member_client, caplog, monkeypatch):
    _poisoned(brain)
    caplog.set_level(logging.DEBUG)
    _page(member_client)
    _page(member_client, tab="dokumente")
    _page(member_client, "6f1c2c1e-0000-4000-8000-000000000003")
    for position in (1, 2, 3):
        member_client.get(f"/dossier/{NODE}/dokument/{position}")

    from knovas_client import GraphError

    def broken(node_id):
        raise GraphError(500, "internal", f"Limmat {SCRIPT}")

    monkeypatch.setattr(brain.fake, "graph_node", broken)
    assert _page(member_client)[0].status_code == 502
    lines = [record.getMessage() for record in caplog.records]
    assert any("Dossier" in line for line in lines)                      # the failure is logged ...
    for line in lines:                                                    # ... with nothing of the firm's text
        for word in ("Limmat", "script", "Immobilien", "Vogt", "Mandanten", "Mietvertrag", "Notiz", "Zürich"):
            assert word not in line, line
