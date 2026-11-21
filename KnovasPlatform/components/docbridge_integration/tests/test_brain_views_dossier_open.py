"""Opening a document from the dossier (PMVP-30a rework): GET /dossier/<node_id>/dokument/<n>.

1. Showing the dossier hands over nothing. A click on the n-th document reads the node's documents again as the
   person, asks the Core whether that person may read that one document, and only then hands over exactly that one,
   to that person alone, and sends the browser where a search hit opens it.
2. A document the Core says the person may not read (a stale assignment, a document deleted since), a place outside
   the list, or a failed read: the same 404 as a document that is not there, and nothing handed over.
3. Experiment documents (experiments/<domain>/<KEY>) are taken out by the function search uses: not listed, not a
   fact's source, never handed over (the verifier's case: a listed EXP pointer became a grant, and the
   document-fields read then answered 200).
4. Where it opens, as a search hit: a PDF in the browser's viewer while PDFs are shown inline, the file handed over
   when they are not, a non-PDF handed over; nothing the browser can open: a plain message, nothing handed over.

The Core's answers are the fixture `brain` of test_brain_views_dossier_page.py; the grants are read from the web app's
grant store (open.grant_store_path of the test configuration, in tmp_path).
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("flask")

import conftest  # noqa: E402
from test_brain_views_dossier_page import (  # noqa: E402  (the shared fixture and the seeded node)
    MEMO, MIETVERTRAG, NODE, Brain, _fact, _page, brain)

__all__ = ["brain", "Brain"]

EXPERIMENT = "experiments/rechtsfaelle/EXP-0007"


def _grants(tmp_path) -> set[tuple[str, str]]:
    """Every (person id, pointer) the web app's grant store holds."""
    path = tmp_path / "grants.sqlite3"
    if not path.exists():
        return set()
    with sqlite3.connect(path) as conn:
        try:
            return {(str(s), str(p)) for s, p in conn.execute("SELECT subject, pointer FROM document_grants")}
        except sqlite3.OperationalError:
            return set()


def _href(app, endpoint: str, pointer: str) -> str:
    with app.test_request_context():
        from flask import url_for

        return url_for(endpoint, doc_id=pointer, path=pointer)


def _open(client, position: int, node_id: str = NODE):
    return client.get(f"/dossier/{node_id}/dokument/{position}")


# --- 1: a click hands over exactly one document ------------------------------------------------------------------


def test_showing_the_dossier_hands_over_nothing(brain, member_client, tmp_path):
    _page(member_client)
    _page(member_client, tab="dokumente")
    assert _grants(tmp_path) == set()


def test_a_click_hands_over_exactly_that_document_to_that_person(brain, member_client, member, tmp_path,
                                                                 workbench_app):
    response = _open(member_client, 2)                                   # the Mietvertrag (second by name)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(_href(workbench_app, "preview_document", MIETVERTRAG))
    granted = _grants(tmp_path)
    assert granted and {s for s, p in granted} == {str(member.id)}
    # one document: its pointer, and its path under the mount where that differs
    assert MIETVERTRAG in {p for s, p in granted}
    assert all(p.endswith("Mietvertrag Seefeldstrasse 48.pdf") for s, p in granted)
    # the file route now lets that document through, and only that one
    assert member_client.get(_href(workbench_app, "preview_document", MIETVERTRAG)).get_json() != \
        {"success": False, "error": "Not found"}
    assert member_client.get(_href(workbench_app, "download_document", MEMO)).get_json() == \
        {"success": False, "error": "Not found"}


def test_the_click_reads_the_node_and_asks_the_core_as_the_person(brain, member_client, member):
    _open(member_client, 1)
    assert brain.calls == [("graph_node", NODE, member.email), ("document_readable", MEMO, member.email)]


def test_another_persons_click_hands_the_first_person_nothing(brain, member_client, bob_client, bob, tmp_path,
                                                              workbench_app):
    assert _open(bob_client, 2).status_code == 302
    assert {s for s, p in _grants(tmp_path)} == {str(bob.id)}
    assert member_client.get(_href(workbench_app, "preview_document", MIETVERTRAG)).get_json() == \
        {"success": False, "error": "Not found"}


# --- 2: what does not open ---------------------------------------------------------------------------------------


def test_a_stale_assignment_is_not_handed_over_and_answers_as_a_missing_document(brain, member_client, tmp_path):
    brain.unreadable = {MEMO}                                            # deleted since; the assignment is left
    stale = _open(member_client, 1)
    missing = _open(member_client, 9)
    assert stale.status_code == missing.status_code == 404
    assert stale.get_data() == missing.get_data()
    assert _grants(tmp_path) == set()


@pytest.mark.parametrize("position", [0, 3, 99])
def test_a_place_outside_the_list_is_a_404_and_asks_nothing_more(brain, member_client, tmp_path, position):
    assert _open(member_client, position).status_code == 404
    assert [c[0] for c in brain.calls if c[0] != "graph_node"] == []
    assert _grants(tmp_path) == set()


def test_a_failed_read_is_a_404_and_hands_over_nothing(brain, member_client, monkeypatch, tmp_path):
    from knovas_client import GraphError

    def broken(node_id):
        raise GraphError(500, "internal", "")

    monkeypatch.setattr(brain.fake, "graph_node", broken)
    assert _open(member_client, 1).status_code == 404
    assert _grants(tmp_path) == set()


# --- 3: experiment documents -------------------------------------------------------------------------------------


def test_an_experiment_document_is_not_listed_not_a_source_and_never_handed_over(brain, member_client, tmp_path):
    node = brain.nodes[NODE]
    node["assignments"].append({"id": "as-exp", "node_id": NODE, "pointer": EXPERIMENT, "status": "active"})
    node["facts"].append(_fact("f-exp", "aus dem Experiment", label="Befund", pointer=EXPERIMENT, chunk="c-1"))
    _, html = _page(member_client, tab="dokumente")
    assert "EXP-0007" not in html and "experiments/" not in html
    assert '<span class="k-tab-count">2</span>' in html
    _, html = _page(member_client)
    assert '<div class="bv-fact" data-fact="f-exp">' in html and "EXP-0007" not in html
    for position in range(0, 5):
        _open(member_client, position)
    assert not any("EXP-0007" in p for s, p in _grants(tmp_path))
    assert not any(c[0] == "document_readable" and c[1] == EXPERIMENT for c in brain.calls)


# --- 4: where a document opens -----------------------------------------------------------------------------------


def test_a_document_that_is_not_a_pdf_is_handed_over(brain, member_client, workbench_app):
    response = _open(member_client, 1)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(_href(workbench_app, "download_document", MEMO))


@pytest.fixture
def pdf_off_app(platform_db, tmp_path, monkeypatch):
    """The web app with open.pdf_inline_in_browser false (the rest of the open section as the shared fixture has it)."""
    monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")
    return conftest._identity_app(
        platform_db, tmp_path, monkeypatch, client_cls=conftest.FakeGraphApi,
        extra_yaml=('open:\n'
                    '  companion_enabled: false\n'
                    f'  grant_store_path: "{(tmp_path / "grants.sqlite3").as_posix()}"\n'
                    '  pdf_inline_in_browser: false\n'))


def test_without_inline_pdfs_a_pdf_is_handed_over_as_a_search_hit_offers_it(pdf_off_app, member, monkeypatch,
                                                                            tmp_path):
    monkeypatch.setenv("BRAIN_VIEWS_ENABLED", "true")
    Brain(conftest.FakeGraphApi.current, monkeypatch)
    client = conftest._signed_in(pdf_off_app, member.email)
    response = _open(client, 2)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(_href(pdf_off_app, "download_document", MIETVERTRAG))
    assert "/preview" not in response.headers["Location"]


def test_what_the_browser_cannot_open_is_a_plain_message_and_hands_over_nothing(brain, member_client, tmp_path,
                                                                               monkeypatch):
    monkeypatch.setenv("OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN", "false")    # the operator chose: no downloads
    response = _open(member_client, 1)                                   # the Memo, not a PDF
    assert response.status_code == 409
    html = response.get_data(as_text=True)
    assert "Nur auf Ihrem Rechner" in html and "Memo" not in html
    assert _grants(tmp_path) == set()
