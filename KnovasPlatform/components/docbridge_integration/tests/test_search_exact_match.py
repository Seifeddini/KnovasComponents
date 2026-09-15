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
