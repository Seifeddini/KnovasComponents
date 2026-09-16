"""Was die Suche über einen Treffer weiss, und was sie darüber sagen darf.

Es gab hier kurz eine Checkbox „Alle Wörter müssen vorkommen". Sie ist wieder
entfernt: sie filterte nur die Treffer, die Knovas ohnehin zurückgab, und zwar
gegen den *indexierten Ausschnitt* eines Dokuments -- erste Seite und
Trefferstellen -- nicht gegen seinen ganzen Text. Sie konnte also Dokumente
verwerfen, die das Wort sehr wohl enthalten, und keines finden, das die Suche
nicht schon gebracht hatte. Das ist keine exakte Stichwortsuche, und ein Schalter
mit diesem Namen verspricht genau die.

Der Heuhaufen bleibt: `literal_query_matches` in der Antwort liest ihn, um zu
sagen, ob die gesuchten Wörter überhaupt vorkommen. Das ist eine Aussage über
das, was da ist -- kein Versprechen darüber, was gefunden wird.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

from web_interface.app import _search_result_haystack  # noqa: E402


class TestTheHaystackIsTheDocument:
    def test_the_matched_sentences_are_searched(self):
        """Der Sidecar-Text liegt zu diesem Zeitpunkt ohnehin vor."""
        hay = _search_result_haystack({
            "title": "2024-017.pdf",
            "match_locations": [
                {"before": "", "match": "Die Reaktionszeit beträgt vier Stunden.", "after": ""},
            ],
        })
        assert "reaktionszeit" in hay

    def test_the_first_page_is_searched(self):
        hay = _search_result_haystack({
            "title": "2024-017.pdf",
            "first_page_preview": "Wartungsvertrag über Störungsbeseitigung",
        })
        assert "störungsbeseitigung" in hay

    def test_the_card_snippet_is_searched(self):
        hay = _search_result_haystack({
            "title": "x.pdf",
            "context_snippet": {"before": "Anlage 3", "match": "Servicelevel", "after": "und Fristen"},
        })
        assert "servicelevel" in hay and "fristen" in hay

    def test_a_result_without_any_text_still_works(self):
        assert _search_result_haystack({"title": "x.pdf"}) == "x.pdf"

    def test_malformed_sidecar_shapes_do_not_raise(self):
        """Ein Sidecar aus einer anderen Version darf die Suche nicht kippen."""
        hay = _search_result_haystack({
            "title": "x.pdf",
            "context_snippet": "kein dict",
            "match_locations": ["kein dict", None, {"match": "treffer"}],
        })
        assert "treffer" in hay


@pytest.fixture
def logged_in(docbridge_app):
    client = docbridge_app.test_client()
    with client.session_transaction() as session:
        session["company_login_ok"] = True
    return client


def _csrf(client):
    page = client.get("/").data.decode("utf-8")
    marker = 'csrfToken: "'
    start = page.index(marker) + len(marker)
    return page[start:page.index('"', start)]


# --- „Sophie Keller" ---------------------------------------------------------
#
# Eine Vektorsuche antwortet auf „ähnlich zu", nicht auf „enthält". Bei einem
# Personennamen ist das oft gar nichts Verwandtes: das Dokument, das zurückkam,
# enthielt den Namen nirgends. Die Liste sah dann aus wie ein kaputtes Produkt.
# Die Platform kann das nicht besser finden -- sie kann aber sagen, was sie
# gerade zeigt, statt es „Fundstellen" zu nennen.

class TestSayingWhenTheWordsDoNotOccur:
    def test_the_payload_counts_literal_matches(self, logged_in):
        from conftest import DummyKnovasClient

        DummyKnovasClient.last_instance.search_results = [
            {"doc_id": "a.pdf", "path": "a.pdf", "title": "Klage",
             "first_page_preview": "Tannenfels Holzbau GmbH gegen Gerüstbau Suter"},
        ]
        body = logged_in.post(
            "/api/search",
            json={"query": "Sophie Keller", "limit": 20},
            headers={"X-CSRF-Token": _csrf(logged_in)},
        ).get_json()
        assert body["literal_query_matches"] == 0

    def test_a_document_that_does_contain_them_is_counted(self, logged_in):
        from conftest import DummyKnovasClient

        DummyKnovasClient.last_instance.search_results = [
            {"doc_id": "a.pdf", "path": "a.pdf", "title": "Vollmacht",
             "first_page_preview": "Vollmacht erteilt durch Sophie Keller"},
        ]
        body = logged_in.post(
            "/api/search",
            json={"query": "Sophie Keller", "limit": 20},
            headers={"X-CSRF-Token": _csrf(logged_in)},
        ).get_json()
        assert body["literal_query_matches"] == 1

    def test_the_page_has_somewhere_to_say_it(self, logged_in):
        assert 'id="resultsNotice"' in logged_in.get("/").data.decode("utf-8")


class TestLocalOptionsAreNotSentUpstream:
    def test_exact_match_never_reaches_the_query_endpoint(self, logged_in):
        """/secured/query reads Input, query_prompt, scope and limit. An extra
        key is noise in the body and a warning in the log on every search."""
        from conftest import DummyKnovasClient

        client = DummyKnovasClient.last_instance
        client.search_results = []
        seen = {}
        original = client.search_documents

        def spy(query, limit=20, filters=None):
            seen["filters"] = filters
            return original(query, limit=limit, filters=filters)

        client.search_documents = spy
        logged_in.post(
            "/api/search",
            json={"query": "Reaktionszeit", "limit": 20,
                  "filters": {"exact_match": True}},
            headers={"X-CSRF-Token": _csrf(logged_in)},
        )
        assert "exact_match" not in (seen["filters"] or {})
