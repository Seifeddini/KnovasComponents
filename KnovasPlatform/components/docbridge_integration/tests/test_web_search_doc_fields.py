"""/api/search with document fields (spec 4.3, H1-H4, H8, D2, D6, D8).

What is pinned here is what the person sees and what leaves the Platform:

    * H1 -- ``where`` goes to Knovas only under ``filters``; ``return_fields``
      under ``filters`` and ``listing_only``; a filter the capability cannot
      carry is refused before anything is sent;
    * H2 -- results are shown as filtered only when Knovas echoed
      ``where.applied is True``; otherwise a 409 without results;
    * H3 -- a filtered search is never retried unfiltered, and the client
      score thresholds and the filename supplement stay out of it; a search
      refused only for ``return_fields`` is retried once without them;
    * F3 -- Knovas's ``auto_scope`` node ids stay on the server: they may
      name nodes the person may not see;
    * D8 -- with the feature off, Knovas gets exactly today's call and the
      browser only additive keys;
    * D6 -- no query text, filter value or pointer in a log line.

The fake Knovas is tests/doc_fields_fakes.FakeDocFieldsApi in each server
state of spec 2.1. Placeholder names only ("Muster AG", "Beispiel GmbH").
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("flask")

from conftest import (  # noqa: E402
    DummyKnovasClient,
    _identity_app,
    _person,
    _signed_in,
    platform_db_reachable,
)
from doc_fields_fakes import FakeDocFieldsApi  # noqa: E402

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason="identity routes need a real PostgreSQL",
)

INVOICE = "rc-sync/Muster AG/GJ 2024/Rechnung_17.pdf"
CONTRACT = "rc-sync/Beispiel GmbH/Vertrag_3.pdf"
MEMO = "rc-sync/Beispiel GmbH/Notiz.txt"


# ---------------------------------------------------------------------------
# Helpers (also used by test_web_documents_find.py and
# test_web_document_fields.py)
# ---------------------------------------------------------------------------

def make_app(platform_db, tmp_path, monkeypatch, mode="filters", **kw):
    """The real app with identity on and a FakeDocFieldsApi in ``mode``."""
    app = _identity_app(platform_db, tmp_path, monkeypatch,
                        client_cls=FakeDocFieldsApi.bind(mode, **kw))
    return app, FakeDocFieldsApi.current


def doc_fields_config(**values):
    """Set ``web.doc_fields.<key>`` on the running app's config. The routes
    read these settings per request, so a test can change them after the
    app was built."""
    from config_loader import get_config

    cfg = get_config()
    block = cfg._config.setdefault("web", {}).setdefault("doc_fields", {})
    block.update(values)


def web_search_config(**values):
    from config_loader import get_config

    cfg = get_config()
    cfg._config.setdefault("web", {}).setdefault("search", {}).update(values)


def signed_in(app, identity_repo, email="anwalt@kanzlei.ch", role="admin", **kw):
    _person(identity_repo, email, email.split("@")[0], role)
    return _signed_in(app, email, **kw)


def search(client, query="Kuendigungsfrist", **extra):
    body = {"query": query}
    body.update(extra)
    return client.post("/api/search", json=body)


def seed(api):
    """Three documents: an invoice of Muster AG, a contract and a memo of
    Beispiel GmbH. All are found by every query of the fake."""
    api.add_document(INVOICE, title="Rechnung Muster AG GJ 2024",
                     fields={"doc_type": "invoice", "mandant": {"name": "Muster AG"},
                             "document_date": {"lo": "2024-03-15", "hi": "2024-03-15",
                                               "precision": "day"}})
    api.add_document(CONTRACT, title="Vertrag mit Beispiel GmbH",
                     fields={"doc_type": "contract", "mandant": {"name": "Beispiel GmbH"}})
    api.add_document(MEMO, fields={"doc_type": "memo"})


@pytest.fixture
def filters_app(platform_db, tmp_path, monkeypatch):
    app, api = make_app(platform_db, tmp_path, monkeypatch, "filters")
    seed(api)
    return app, api


def _cap():
    import doc_fields_capability as dfc

    return dfc.shared_cache().peek()


# ---------------------------------------------------------------------------
# Feature off: today's call, additive keys only (D8, matrix 2.5)
# ---------------------------------------------------------------------------

class TestFeatureOffParity:
    def test_legacy_client_gets_todays_call_and_additive_keys(self, identity_app, identity_repo):
        """Not secured: capability off without a probe, nothing new sent."""
        knovas = DummyKnovasClient.last_instance
        knovas.search_results = [{"doc_id": INVOICE, "path": INVOICE, "title": "Rechnung"}]
        client = signed_in(identity_app, identity_repo, role="member")
        response = search(client)
        assert response.status_code == 200
        # 40: the page of 20 plus the margin main asks for, so experiment hits
        # taken out never leave the page short (_fetch_search_page).
        assert knovas.search_requests == [{"query": "Kuendigungsfrist", "limit": 40,
                                          "filters": {}, "where": None,
                                          "return_fields": None}]
        body = response.get_json()
        # Today's keys plus two blocks; nothing renamed, nothing removed.
        assert set(body) == {"success", "query", "results", "literal_query_matches",
                             "highlight_prefixes", "total", "has_more", "timestamp",
                             "onedrive_enrichment_loaded", "location_summary",
                             "document_fields", "honesty"}
        assert body["document_fields"] == {"capability": "off", "filter_state": "none",
                                           "fields_unavailable": False, "resolved": []}
        assert body["honesty"] == {"no_strong_matches": None, "no_results_reason": None,
                                   "relevance_gate_applied": None, "degraded_to_bm25": None}
        row = body["results"][0]
        assert row["doc_id"] == INVOICE
        assert row["fields_display"] == [] and row["title_from_values"] is False
        assert row["relevance_tier"] is None

    def test_a_client_with_todays_signature_still_works(self, platform_db, tmp_path,
                                                         monkeypatch, identity_repo):
        """The new keyword arguments are passed only when they are not None,
        so a client (or a fake) that predates them is called as before."""

        class TodaysClient(DummyKnovasClient):
            def search_documents(self, query, limit=20, filters=None):
                self.search_calls.append(str(query))
                return {"results": [{"doc_id": CONTRACT, "path": CONTRACT}], "total": 1}

        app = _identity_app(platform_db, tmp_path, monkeypatch, client_cls=TodaysClient)
        client = signed_in(app, identity_repo, role="member")
        response = search(client)
        assert response.status_code == 200
        assert [r["doc_id"] for r in response.get_json()["results"]] == [CONTRACT]

    def test_secured_but_off_at_knovas_sends_nothing_new(self, platform_db, tmp_path,
                                                          monkeypatch, identity_repo):
        app, api = make_app(platform_db, tmp_path, monkeypatch, "off")
        seed(api)
        client = signed_in(app, identity_repo, role="member")
        body = search(client).get_json()
        assert api.search_requests[-1]["where"] is None
        assert api.search_requests[-1]["return_fields"] is None
        assert body["document_fields"]["capability"] == "off"
        assert api.probe_calls == 1
        # No registry read, no other doc-fields call.
        assert [m for m, _ in api.doc_calls if m != "search_documents"] == []

    def test_the_browser_cannot_switch_it_on(self, platform_db, tmp_path, monkeypatch,
                                             identity_repo):
        """D1: a where from the browser against an off tenant is refused,
        not sent and not silently dropped."""
        app, api = make_app(platform_db, tmp_path, monkeypatch, "off")
        seed(api)
        client = signed_in(app, identity_repo, role="member")
        response = search(client, where={"doc_type": "invoice"})
        assert response.status_code == 409
        assert response.get_json()["error_code"] == "filters_unavailable"
        assert api.search_requests == []


# ---------------------------------------------------------------------------
# H1: what goes out per capability
# ---------------------------------------------------------------------------

class TestWhatGoesOut:
    def test_values_server_gets_neither_key_and_the_search_works(
            self, platform_db, tmp_path, monkeypatch, identity_repo):
        """A values-mode server answers 400 where_unsupported to either key;
        a plain search must not provoke it."""
        app, api = make_app(platform_db, tmp_path, monkeypatch, "values")
        seed(api)
        client = signed_in(app, identity_repo, role="member")
        response = search(client)
        assert response.status_code == 200
        assert len(response.get_json()["results"]) == 3
        assert api.search_requests[-1]["where"] is None
        assert api.search_requests[-1]["return_fields"] is None
        assert response.get_json()["document_fields"]["capability"] == "values"

    def test_listing_only_sends_return_fields(self, platform_db, tmp_path, monkeypatch,
                                              identity_repo):
        app, api = make_app(platform_db, tmp_path, monkeypatch, "listing_only")
        seed(api)
        client = signed_in(app, identity_repo, role="member")
        response = search(client)
        assert response.status_code == 200
        sent = api.search_requests[-1]
        assert sent["where"] is None
        # Display fields, never the special one (patient), title first.
        assert sent["return_fields"] == ["title", "doc_type", "document_date", "mandant"]

    def test_listing_only_search_carries_the_rail_notice(self, platform_db, tmp_path,
                                                         monkeypatch, identity_repo):
        """platform-search-3: under listing_only the rail never filters a
        search; the answer carries the text the page shows when the rail
        holds values. Under filters it does not."""
        import doc_fields_view as dfv

        app, api = make_app(platform_db, tmp_path, monkeypatch, "listing_only")
        seed(api)
        client = signed_in(app, identity_repo, role="member")
        # The probe says filters until a refused query teaches listing_only.
        before = search(client).get_json()["document_fields"]
        assert before["capability"] == "filters" and "rail_not_applied" not in before
        assert search(client, where={"doc_type": "invoice"}).status_code == 409
        block = search(client).get_json()["document_fields"]
        assert block["capability"] == "listing_only"
        assert block["rail_not_applied"] == dfv.RAIL_NOT_APPLIED_TO_SEARCH

    def test_listing_only_refuses_where_before_calling_knovas(
            self, platform_db, tmp_path, monkeypatch, identity_repo):
        app, api = make_app(platform_db, tmp_path, monkeypatch, "listing_only")
        seed(api)
        # The probe cannot tell listing_only from filters (find never checks
        # calibration); the first refused query teaches it.
        client = signed_in(app, identity_repo, role="member")
        first = search(client, where={"doc_type": "invoice"})
        assert first.status_code == 409
        assert first.get_json()["error_code"] == "filters_need_calibration"
        assert "results" not in first.get_json()
        assert _cap().value == "listing_only"
        before = len(api.search_requests)
        second = search(client, where={"doc_type": "invoice"})
        assert second.status_code == 409
        assert second.get_json()["error_code"] == "filters_need_calibration"
        assert len(api.search_requests) == before, "refused without asking Knovas"

    @pytest.mark.parametrize("mode", ["values", "off"])
    def test_where_without_filters_is_refused_unsent(self, mode, platform_db, tmp_path,
                                                     monkeypatch, identity_repo):
        app, api = make_app(platform_db, tmp_path, monkeypatch, mode)
        seed(api)
        client = signed_in(app, identity_repo, role="member")
        response = search(client, where={"doc_type": "invoice"})
        assert response.status_code == 409
        assert response.get_json()["error_code"] == "filters_unavailable"
        assert "results" not in response.get_json()
        assert api.search_requests == []

    def test_filters_send_where_and_show_what_was_understood(self, filters_app,
                                                              identity_repo):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        where = {"doc_type": "invoice", "mandant": {"name": "Muster AG"}}
        response = search(client, where=where)
        assert response.status_code == 200
        assert api.search_requests[-1]["where"] == where
        body = response.get_json()
        assert [r["doc_id"] for r in body["results"]] == [INVOICE]
        df = body["document_fields"]
        assert df["capability"] == "filters" and df["filter_state"] == "applied"
        assert df["resolved"] == [
            {"field": "doc_type", "label": "Dokumentart", "op": "eq", "text": "Rechnung"},
            {"field": "mandant", "label": "Mandant", "op": "eq", "text": "Muster AG",
             "linked_count": 1, "linked_text": "1 verkn\u00fcpfter Eintrag"},
        ]
        assert body["results"][0]["relevance_tier"] == "strong"

    def test_a_malformed_where_never_leaves_the_platform(self, filters_app, identity_repo):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        too_many = {f"k{i}": "x" for i in range(9)}
        for where in (too_many, {"Bad Key!": "x"}, {"doc_type": {"a": {"b": {"c": {"d": 1}}}}},
                      ["doc_type"], "doc_type", {"reference": [f"R-{i}" for i in range(51)]}):
            response = search(client, where=where)
            assert response.status_code == 400, where
            assert response.get_json()["error_code"] == "filter_invalid"
        assert api.search_requests == []

    def test_operators_reach_knovas_and_read_back_in_german(self, filters_app, identity_repo):
        """F2: the rail's operators go out as written; "Verstanden als" says
        each one in German."""
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        where = {"doc_type": ["invoice", "contract"],
                 "document_date": {"gte": "01.01.2024", "lte": "31.12.2024",
                                   "match": "possible"},
                 "status": {"exists": True}}
        response = search(client, where=where)
        assert response.status_code == 200
        assert api.search_requests[-1]["where"] == where
        texts = {c["field"]: c["text"]
                 for c in response.get_json()["document_fields"]["resolved"]}
        assert texts == {"doc_type": "eine von Rechnung; Vertrag",
                         "document_date": "zwischen 01.01.2024 und 31.12.2024, auch teilweise",
                         "status": "hat einen Wert"}


# ---------------------------------------------------------------------------
# H2: shown as filtered only when Knovas says so
# ---------------------------------------------------------------------------

class TestHonestFilterState:
    def test_a_missing_echo_withholds_the_results(self, filters_app, identity_repo):
        app, api = filters_app
        api.drop_where_echo = True
        client = signed_in(app, identity_repo, role="member")
        response = search(client, where={"doc_type": "invoice"})
        assert response.status_code == 409
        body = response.get_json()
        assert body["error_code"] == "filter_not_applied"
        assert body["results"] == []
        assert "Knovas hat nicht best\u00e4tigt" in body["error"]
        # echo_missing: the capability is unknown and the next call probes.
        probes = api.probe_calls
        search(client)
        assert api.probe_calls == probes + 1

    def test_partial_is_said(self, filters_app, identity_repo):
        app, api = filters_app
        api.may_be_partial = True
        client = signed_in(app, identity_repo, role="member")
        df = search(client, where={"doc_type": "invoice"}).get_json()["document_fields"]
        assert df["filter_state"] == "partial"
        assert "m\u00f6glicherweise unvollst\u00e4ndig" in df["partial_hint"]

    def test_without_where_the_state_is_none(self, filters_app, identity_repo):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        df = search(client).get_json()["document_fields"]
        assert df["filter_state"] == "none" and df["resolved"] == []


# ---------------------------------------------------------------------------
# Error mapping on a filtered search: never retried unfiltered (H3)
# ---------------------------------------------------------------------------

class TestRefusedFilter:
    @pytest.mark.parametrize("status, code, details, browser_status, browser_code, cap", [
        (400, "unknown_field", {"path": "where.mandnt", "suggest": ["mandant"]},
         400, "filter_invalid", "filters"),
        (400, "ambiguous_field", {"path": "where.datum",
                                  "candidates": ["document_date", "period"]},
         400, "filter_invalid", "filters"),
        (400, "type_mismatch", {"path": "where.amount"}, 400, "filter_invalid", "filters"),
        (400, "where_too_complex", {"path": "where"}, 400, "filter_invalid", "filters"),
        (400, "restricted_identifier", {"path": "where.reference"}, 400, "filter_invalid",
         "filters"),
        (400, "where_unsupported", {}, 409, "filters_unavailable", "values"),
        (503, "where_requires_calibration", {}, 409, "filters_need_calibration",
         "listing_only"),
        (503, "where_unavailable", {}, 503, "filter_temporarily_unavailable", "filters"),
    ])
    def test_mapping(self, filters_app, identity_repo, status, code, details,
                     browser_status, browser_code, cap):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        search(client)  # learn the capability first
        api.fail_call("search_documents", status, code, **details)
        before = len(api.search_requests)
        response = search(client, where={"doc_type": "invoice"})
        assert response.status_code == browser_status
        body = response.get_json()
        assert body["error_code"] == browser_code
        assert "results" not in body or body["results"] == []
        # Exactly one request, and it carried the filter: no unfiltered retry.
        assert len(api.search_requests) == before + 1
        assert api.search_requests[-1]["where"] == {"doc_type": "invoice"}
        assert _cap().value == cap

    def test_field_errors_name_the_field_by_label(self, filters_app, identity_repo):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        api.fail_call("search_documents", 400, "unknown_field",
                      path="where.mandnt", suggest=["mandant"])
        body = search(client, where={"mandnt": "Muster AG"}).get_json()
        assert body["field"] == "mandnt"
        assert body["suggest_labels"] == ["Mandant"]
        assert "Meinten Sie \u201eMandant\u201c?" in body["error"]
        assert "Muster AG" not in body["error"], "a value is never repeated"

    def test_calibration_message_says_try_again_later(self, filters_app, identity_repo):
        """F6: a temporary problem at Knovas, never a missing setup step."""
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        api.fail_call("search_documents", 503, "where_requires_calibration")
        body = search(client, where={"doc_type": "invoice"}).get_json()
        assert "vor\u00fcbergehend nicht verf\u00fcgbar" in body["error"]
        assert "Kalibrierung" not in body["error"]


# ---------------------------------------------------------------------------
# H3: refinement and supplement only without a filter
# ---------------------------------------------------------------------------

class TestRefinementUnderAFilter:
    def test_thresholds_do_not_drop_filtered_rows(self, filters_app, identity_repo):
        app, api = filters_app
        web_search_config(min_similarity_score=0.95)
        client = signed_in(app, identity_repo, role="member")
        plain = search(client).get_json()
        filtered = search(client, where={"doc_type": "invoice"}).get_json()
        assert plain["results"] == [], "without a filter the threshold still applies"
        assert [r["doc_id"] for r in filtered["results"]] == [INVOICE]

    def test_threshold_still_applies_when_knovas_gated_without_where(self, filters_app,
                                                                      identity_repo):
        """relevance_gate_applied follows RELEVANCE_GATE_ENABLED, not document
        fields; it is no reason to drop today's refinement."""
        app, api = filters_app
        api.relevance_gate = True
        web_search_config(min_similarity_score=0.95)
        client = signed_in(app, identity_repo, role="member")
        body = search(client).get_json()
        assert body["honesty"]["relevance_gate_applied"] is True
        assert body["results"] == []

    def test_the_filename_supplement_is_skipped_only_under_where(self, filters_app,
                                                                 identity_repo,
                                                                 monkeypatch):
        from web_interface import app as web_app

        calls = []
        original = web_app._supplement_results_from_enrichment_filenames

        def spy(query, results, filters, config, *, limit=20):
            calls.append(len(results))
            return original(query, results, filters, config, limit=limit)

        monkeypatch.setattr(web_app, "_supplement_results_from_enrichment_filenames", spy)
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        search(client, where={"doc_type": "invoice"})
        assert calls == []
        search(client)
        assert calls == [3]


# ---------------------------------------------------------------------------
# return_fields: one retry without them, never a failed plain search
# ---------------------------------------------------------------------------

class TestReturnFieldsFallback:
    @pytest.mark.parametrize("status, code", [
        (400, "unknown_field"), (400, "where_unsupported"), (400, "invalid_value"),
        (400, "where_too_complex"), (503, "where_unavailable"),
        (503, "doc_fields_unavailable"),
    ])
    def test_retried_exactly_once_without_return_fields(self, filters_app, identity_repo,
                                                        status, code):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        api.fail_call("search_documents", status, code, path="return_fields")
        response = search(client)
        assert response.status_code == 200
        assert len(response.get_json()["results"]) == 3
        first, second = api.search_requests[-2:]
        assert first["return_fields"] and second["return_fields"] is None
        assert first["where"] is None and second["where"] is None
        assert response.get_json()["document_fields"]["fields_unavailable"] is True
        if code == "where_unsupported":
            assert _cap().value == "values"

    def test_a_registry_that_cannot_be_read_still_searches(self, filters_app, identity_repo):
        app, api = filters_app
        api.fail_call("doc_fields", 503, "doc_fields_unavailable")
        client = signed_in(app, identity_repo, role="member")
        response = search(client)
        assert response.status_code == 200
        assert len(response.get_json()["results"]) == 3
        assert api.search_requests[-1]["return_fields"] is None
        assert response.get_json()["document_fields"]["fields_unavailable"] is True

    def test_a_second_refusal_is_an_ordinary_failure(self, filters_app, identity_repo):
        """Exactly once: a retry that fails again is reported, not retried."""
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        api.fail_call("search_documents", 400, "unknown_field")
        api.fail_call("search_documents", RuntimeError("down"))
        response = search(client)
        assert response.status_code == 500
        assert len(api.search_requests) == 2


# ---------------------------------------------------------------------------
# Rows: values on cards, titles, limit
# ---------------------------------------------------------------------------

class TestRows:
    def test_cards_get_values_title_and_tier(self, filters_app, identity_repo):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        rows = {r["doc_id"]: r for r in search(client).get_json()["results"]}
        contract = rows[CONTRACT]
        assert contract["title"] == "Vertrag mit Beispiel GmbH"
        assert contract["title_from_values"] is True
        assert contract["fields_display"] == [
            {"key": "doc_type", "label": "Dokumentart", "text": "Vertrag"},
            {"key": "mandant", "label": "Mandant", "text": "Beispiel GmbH"},
        ]
        memo = rows[MEMO]
        # A title that is only the file name is not a title: today's stem.
        assert memo["title_from_values"] is False and memo["title"] == "Notiz"

    def test_special_fields_never_reach_a_card(self, filters_app, identity_repo):
        app, api = filters_app
        api.add_document("rc-sync/Patienten/Bericht.pdf",
                         fields={"doc_type": "report", "patient": {"name": "Beispiel Patient"}})
        client = signed_in(app, identity_repo, role="member")
        sent = api.search_requests
        body = search(client).get_json()
        assert "patient" not in sent[-1]["return_fields"]
        assert all("patient" not in {d["key"] for d in r["fields_display"]}
                   for r in body["results"])

    # ``sent`` is what Knovas is asked for: the clamped page plus the
    # experiments margin (_search_fetch_size), never more than 50.
    @pytest.mark.parametrize("limit, sent", [(100, 50), (51, 50), (50, 50), (0, 2),
                                             (-5, 2), ("viele", 40), (None, 40)])
    def test_limit_is_clamped(self, filters_app, identity_repo, limit, sent):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        extra = {} if limit is None else {"limit": limit}
        assert search(client, **extra).status_code == 200
        assert api.search_requests[-1]["limit"] == sent

    def test_an_enrichment_title_does_not_overrule_a_values_title(self, monkeypatch):
        from web_interface import app as web_app

        monkeypatch.setattr(web_app, "_load_search_enrichment", lambda config=None: {"x": {}})
        monkeypatch.setattr(web_app, "_lookup_enrichment_meta",
                            lambda enrichment, result, exact_only=False: {"title": "Aus Datei"})
        monkeypatch.setattr(web_app, "_context_store_path_from_config", lambda config=None: "")

        class Handler:
            autodoc_path = "/nonexistent-autodoc-root"

        rows = [{"doc_id": "a", "path": "a", "title": "Vertrag mit Muster AG",
                 "title_from_values": True},
                {"doc_id": "b", "path": "b", "title": "b", "title_from_values": False}]
        out = web_app._enhance_search_results({"results": rows}, Handler(), None, "")
        assert out["results"][0]["title"] == "Vertrag mit Muster AG"
        assert out["results"][1]["title"] == "Aus Datei"


# ---------------------------------------------------------------------------
# Grants: a filtered search hands out documents like any search
# ---------------------------------------------------------------------------

class TestGrants:
    def test_a_filtered_result_can_be_previewed(self, filters_app, identity_repo):
        app, api = filters_app
        client = signed_in(app, identity_repo, role="member")
        search(client, where={"doc_type": "invoice"})
        ok = client.get(f"/api/document/{INVOICE}/download")
        assert (ok.get_json() or {}).get("error") != "Not found"
        # The contract was filtered out, so it was never handed over.
        refused = client.get(f"/api/document/{CONTRACT}/download")
        assert refused.status_code == 404

    def test_a_refused_filter_grants_nothing(self, filters_app, identity_repo):
        app, api = filters_app
        api.drop_where_echo = True
        client = signed_in(app, identity_repo, role="member")
        search(client, where={"doc_type": "invoice"})
        assert client.get(f"/api/document/{INVOICE}/download").status_code == 404


# ---------------------------------------------------------------------------
# F3: Knovas's auto_scope stays on the server
# ---------------------------------------------------------------------------

class TestAutoScopeStaysOnTheServer:
    def test_node_ids_never_reach_the_browser(self, filters_app, identity_repo, monkeypatch):
        """Knovas narrowed the search to the nodes it recognised in the
        question. Their ids may name nodes this person may not see, so the
        semantix block reaches the browser without auto_scope, and otherwise
        unchanged."""
        from knovas_client import _secured_query_honesty

        app, api = filters_app
        # What the real client keeps of Knovas's block.
        kept = _secured_query_honesty({"auto_scope": {
            "detections": [{"node_id": "hidden-node", "identifier_id": "i-9",
                            "channel": "lexical", "score": 0.97}],
            "node_ids": ["m1", "hidden-node"], "applied": True,
            "fallback": False}})["auto_scope"]
        assert kept["node_ids"] == ["hidden-node", "m1"]
        answer = api.search_documents

        def narrowed(*args, **kwargs):
            out = answer(*args, **kwargs)
            out["semantix"]["auto_scope"] = kept
            return out

        client = signed_in(app, identity_repo, role="member")
        plain = search(client).get_json()["semantix"]
        monkeypatch.setattr(api, "search_documents", narrowed)
        response = search(client)
        assert response.status_code == 200
        body = response.get_json()
        assert len(body["results"]) == 3
        assert body["semantix"] == plain, "only auto_scope stays behind"
        assert "hidden-node" not in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# D6: nothing a person typed or a document holds in a log line
# ---------------------------------------------------------------------------

class TestNoValuesInLogs:
    def test_queries_filters_and_pointers_stay_out_of_the_log(self, filters_app,
                                                              identity_repo, caplog):
        app, api = filters_app
        sentinel_pointer = "rc-sync/Sentinel-Mandant/Sentinel-Akte.pdf"
        api.add_document(sentinel_pointer, title="Sentinel-Titel Vertrag",
                         fields={"doc_type": "contract",
                                 "mandant": {"name": "Sentinel-Mandant-AG"}})
        client = signed_in(app, identity_repo, role="member")
        caplog.set_level(logging.DEBUG)
        search(client, query="Sentinel-Frage nach Kuendigung")
        search(client, query="Sentinel-Frage nach Kuendigung",
               where={"mandant": {"name": "Sentinel-Mandant-AG"}})
        api.drop_where_echo = True
        search(client, query="Sentinel-Frage nach Kuendigung",
               where={"mandant": {"name": "Sentinel-Mandant-AG"}})
        api.fail_call("search_documents", 400, "unknown_field", path="where.sentinel")
        api.drop_where_echo = False
        search(client, query="Sentinel-Frage nach Kuendigung", where={"sentinel": "Sentinel-Wert"})
        text = "\n".join(r.getMessage() for r in caplog.records)
        for needle in ("Sentinel-Frage", "Sentinel-Mandant", "Sentinel-Akte", "Sentinel-Titel",
                       "Sentinel-Wert"):
            assert needle not in text, needle
