"""„Alle Wörter müssen vorkommen" — die Verschärfung der semantischen Suche.

Die Vektorsuche findet Verwandtes, auch wenn das Wort nirgends steht. Bei einem
Begriff ist das die Stärke; bei zweien meist nicht, was gemeint war: wer
„Reaktionszeit Störung" eingibt, will Dokumente, in denen beides vorkommt.

Die Option war in config.yaml beschrieben (`strict_match_min_term_length`, der
Kommentar verweist auf „die UI-Checkbox") und serverseitig implementiert -- es
gab sie nur nirgends zu klicken, und der Heuhaufen, in dem sie suchte, bestand
aus Titel und Pfad. Bei Akten, die nach Aktenzeichen heissen, traf das nie zu.
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


class TestTheOptionIsReachable:
    def test_the_search_page_offers_it(self, logged_in):
        """Ohne Bedienelement ist eine implementierte Option keine Funktion."""
        page = logged_in.get("/").data.decode("utf-8")
        assert 'id="exactMatch"' in page

    def test_both_words_required_drops_a_hit_that_has_only_one(self, logged_in):
        from conftest import DummyKnovasClient

        DummyKnovasClient.last_instance.search_results = [
            {"doc_id": "a.pdf", "path": "a.pdf", "title": "Wartungsvertrag",
             "first_page_preview": "Reaktionszeit bei Störung der Stufe 1"},
            {"doc_id": "b.pdf", "path": "b.pdf", "title": "Mietvertrag",
             "first_page_preview": "Reaktionszeit des Vermieters"},
        ]
        response = logged_in.post(
            "/api/search",
            json={"query": "Reaktionszeit Störung", "limit": 20,
                  "filters": {"exact_match": True}},
            headers={"X-CSRF-Token": _csrf(logged_in)},
        )
        ids = [r["doc_id"] for r in response.get_json()["results"]]
        assert ids == ["a.pdf"]

    def test_without_the_option_both_hits_stay(self, logged_in):
        """Die semantische Suche bleibt der Normalfall."""
        from conftest import DummyKnovasClient

        DummyKnovasClient.last_instance.search_results = [
            {"doc_id": "a.pdf", "path": "a.pdf", "title": "Wartungsvertrag",
             "first_page_preview": "Reaktionszeit bei Störung der Stufe 1"},
            {"doc_id": "b.pdf", "path": "b.pdf", "title": "Mietvertrag",
             "first_page_preview": "Reaktionszeit des Vermieters"},
        ]
        response = logged_in.post(
            "/api/search",
            json={"query": "Reaktionszeit Störung", "limit": 20},
            headers={"X-CSRF-Token": _csrf(logged_in)},
        )
        assert len(response.get_json()["results"]) == 2


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
