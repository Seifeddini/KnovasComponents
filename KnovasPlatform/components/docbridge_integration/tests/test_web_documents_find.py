"""POST /api/documents/find: the listing by document fields (spec 4.4, H2, H5, H9).

The listing is the other way a person is handed documents, so it is held to
the same rules as search: shown only when Knovas echoed ``where.applied``,
granted like search rows (so a listed document previews), and the
"Liste unvollstaendig" notice appears only where it is true -- on the last
page, when Knovas says the walk was not complete. Never on a page that
merely has a successor.

Placeholder names only ("Muster AG", "Beispiel GmbH").
"""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("flask")

from conftest import platform_db_reachable  # noqa: E402
from doc_fields_fakes import field_def  # noqa: E402
from test_web_search_doc_fields import (  # noqa: E402
    CONTRACT,
    INVOICE,
    MEMO,
    doc_fields_config,
    make_app,
    seed,
    signed_in,
)

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason="identity routes need a real PostgreSQL",
)

import doc_fields_view as dfv  # noqa: E402

#: An experiment document as the Experimente module writes it to Knovas.
EXP_POINTER = "experiments/marketing/MKT-1"


def find(client, where, **extra):
    body = {"where": where}
    body.update(extra)
    return client.post("/api/documents/find", json=body)


@pytest.fixture
def listing(platform_db, tmp_path, monkeypatch):
    app, api = make_app(platform_db, tmp_path, monkeypatch, "listing_only")
    seed(api)
    return app, api


def _finds(api):
    return [args for method, args in api.doc_calls if method == "find_doc_values"]


class TestGating:
    @pytest.mark.parametrize("mode, code", [("off", "doc_fields_off"),
                                            ("values", "listing_unavailable")])
    def test_needs_listing_or_filters(self, mode, code, platform_db, tmp_path, monkeypatch,
                                      identity_repo):
        app, api = make_app(platform_db, tmp_path, monkeypatch, mode)
        seed(api)
        client = signed_in(app, identity_repo, role="member")
        response = find(client, {"doc_type": "invoice"})
        assert response.status_code == 409
        assert response.get_json()["error_code"] == code
        assert _finds(api) == []

    def test_csrf_is_required(self, listing, identity_repo):
        app, api = listing
        raw = signed_in(app, identity_repo, role="member", with_csrf=False)
        response = raw.post("/api/documents/find", json={"where": {"doc_type": "invoice"}})
        assert response.status_code == 403
        assert _finds(api) == []

    def test_signed_out_is_refused(self, listing):
        app, api = listing
        response = app.test_client().post("/api/documents/find",
                                          json={"where": {"doc_type": "invoice"}})
        assert response.status_code == 401
        assert _finds(api) == []

    @pytest.mark.parametrize("body", [
        {"where": {}},
        {"where": "doc_type"},
        {"where": {f"k{i}": 1 for i in range(9)}},
        {"where": {"doc_type": ["invoice"] * 51}},
        {"where": {"doc_type": "invoice"}, "sort": {"field": "document_date", "x": 1}},
        {"where": {"doc_type": "invoice"}, "sort": {"field": "Bad Field", "order": "asc"}},
        {"where": {"doc_type": "invoice"}, "sort": {"field": "document_date", "order": "up"}},
        {"where": {"doc_type": "invoice"}, "after": ""},
        {"where": {"doc_type": "invoice"}, "after": "x" * 5000},
        {"where": {"doc_type": "invoice"}, "after": 7},
    ])
    def test_bounds_are_checked_before_knovas(self, listing, identity_repo, body):
        app, api = listing
        client = signed_in(app, identity_repo, role="member")
        response = client.post("/api/documents/find", json=body)
        assert response.status_code == 400
        assert _finds(api) == []


class TestThePage:
    def test_rows_in_search_shape(self, listing, identity_repo):
        app, api = listing
        client = signed_in(app, identity_repo, role="member")
        response = find(client, {"mandant": {"name": "Beispiel GmbH"}})
        assert response.status_code == 200
        body = response.get_json()
        assert [d["doc_id"] for d in body["documents"]] == [CONTRACT]
        row = body["documents"][0]
        assert row["path"] == CONTRACT
        assert row["title"] == "Vertrag mit Beispiel GmbH" and row["title_from_values"] is True
        assert row["fields_display"] == [
            {"key": "doc_type", "label": "Dokumentart", "text": "Vertrag"},
            {"key": "mandant", "label": "Mandant", "text": "Beispiel GmbH"},
        ]
        # Enriched like a search row (disk metadata from the search path).
        assert "can_open" in row and "autodoc_rel_path" in row
        assert body["complete"] is True and body["next_after"] is None
        assert body["total_count"] == 1
        assert body["notice"] == {"incomplete": False, "text": None, "total_count": 1}
        assert body["document_fields"]["filter_state"] == "applied"
        assert body["document_fields"]["resolved"][0]["text"] == "Beispiel GmbH"

    def test_what_is_sent(self, listing, identity_repo):
        app, api = listing
        client = signed_in(app, identity_repo, role="member")
        find(client, {"doc_type": "invoice"}, sort={"field": "document_date", "order": "desc"})
        sent = _finds(api)[-1]
        assert sent["where"] == {"doc_type": "invoice"}
        assert sent["sort"] == {"field": "document_date", "order": "desc"}
        assert sent["limit"] == 50          # web.doc_fields.find_page_size
        assert sent["after"] is None
        assert sent["return_fields"] == ["title", "doc_type", "document_date", "mandant"]

    def test_a_missing_echo_withholds_the_page(self, listing, identity_repo):
        app, api = listing
        api.drop_where_echo = True
        client = signed_in(app, identity_repo, role="member")
        response = find(client, {"doc_type": "invoice"})
        assert response.status_code == 409
        body = response.get_json()
        assert body["error_code"] == "filter_not_applied"
        assert body["results"] == [] and "documents" not in body
        import doc_fields_capability as dfc

        assert dfc.shared_cache().peek() is None, "the next call asks Knovas again"

    def test_a_registry_failure_lists_without_values(self, listing, identity_repo):
        app, api = listing
        api.fail_call("doc_fields", 503, "doc_fields_unavailable")
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "invoice"}).get_json()
        assert [d["doc_id"] for d in body["documents"]] == [INVOICE]
        assert _finds(api)[-1]["return_fields"] is None
        assert body["document_fields"]["fields_unavailable"] is True


class TestPointerSort:
    def test_the_path_descending(self, listing, identity_repo):
        """F3: "Dokumentpfad absteigend" goes to Knovas as written."""
        app, api = listing
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": ["invoice", "contract", "memo"]},
                    sort={"field": "pointer", "order": "desc"}).get_json()
        assert _finds(api)[-1]["sort"] == {"field": "pointer", "order": "desc"}
        assert [d["doc_id"] for d in body["documents"]] == sorted(
            [INVOICE, CONTRACT, MEMO], reverse=True)
        assert "deadline_banner" not in body


class TestPagingAndTheNotice:
    """H5: the notice only when next_after is None and complete is False."""

    def _walk(self, client, where):
        pages, after = [], None
        while True:
            body = find(client, where, **({"after": after} if after else {})).get_json()
            pages.append(body)
            after = body["next_after"]
            if after is None:
                return pages

    def test_no_notice_on_a_page_with_a_successor(self, listing, identity_repo):
        app, api = listing
        doc_fields_config(find_page_size=1)
        client = signed_in(app, identity_repo, role="member")
        pages = self._walk(client, {"doc_type": ["invoice", "contract", "memo"]})
        assert len(pages) == 3
        assert [p["complete"] for p in pages] == [False, False, True]
        assert all(p["notice"]["incomplete"] is False for p in pages)
        # total_count: first page only.
        assert pages[0]["total_count"] == 3
        assert all("total_count" not in p for p in pages[1:])

    def test_the_notice_on_an_exhausted_last_page(self, listing, identity_repo):
        app, api = listing
        api.find_budget_exhausted = True
        doc_fields_config(find_page_size=2)
        client = signed_in(app, identity_repo, role="member")
        pages = self._walk(client, {"doc_type": ["invoice", "contract", "memo"]})
        assert [p["notice"]["incomplete"] for p in pages] == [False, True]
        assert pages[-1]["notice"]["text"] == dfv.INCOMPLETE_LISTING

    def test_an_overflowing_total_is_not_shown(self, listing, identity_repo):
        app, api = listing
        api.total_count_overflow = True
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "invoice"}).get_json()
        assert "total_count" not in body and body["notice"]["total_count"] is None

    def test_an_empty_page_with_a_cursor_is_passed_on(self, listing, identity_repo):
        """Pages may be short or empty while next_after is set (contract 4.3):
        the browser keeps paging; the route reports exactly what came."""
        app, api = listing
        api.scripted_pages = [
            {"documents": [], "next_after": "c1", "complete": False, "total_count": 2,
             "where": {"applied": True, "clauses": 1, "resolved": []}},
        ]
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "invoice"}).get_json()
        assert body["documents"] == [] and body["next_after"] == "c1"
        assert body["notice"]["incomplete"] is False

    def test_the_deadline_banner(self, listing, identity_repo):
        """H9: a listing by a due date is never a deadline control."""
        app, api = listing
        api.registry.append(field_def("deadline", "date", "Frist", display=True,
                                      date_role="due", pack="legal_ch"))
        client = signed_in(app, identity_repo, role="member")
        by_deadline = find(client, {"doc_type": "invoice"},
                           sort={"field": "deadline", "order": "asc"}).get_json()
        assert by_deadline["deadline_banner"] == dfv.DEADLINE_BANNER
        filtered = find(client, {"deadline": {"gte": "01.10.2026"}}).get_json()
        assert filtered["deadline_banner"] == dfv.DEADLINE_BANNER
        plain = find(client, {"doc_type": "invoice"}).get_json()
        assert "deadline_banner" not in plain


class TestEmptyListingHonesty:
    """platform-search-1: an empty listing says "no document" only when
    Knovas called the walk complete; one that ran out of scan budget says
    only the checked documents had no match (H8, H9)."""

    def test_an_exhausted_walk_with_no_rows_never_says_no_document(self, listing,
                                                                    identity_repo):
        app, api = listing
        api.find_budget_exhausted = True
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "nonexistent_code_zz"}).get_json()
        assert body["documents"] == [] and body["next_after"] is None
        assert body["complete"] is False and body["notice"]["incomplete"] is True
        assert body["empty_text"] == dfv.EMPTY_INCOMPLETE_LISTING
        assert body["empty_text"] != dfv._NO_RESULTS["empty_where"]

    def test_a_complete_walk_with_no_rows_says_no_document(self, listing, identity_repo):
        app, api = listing
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "nonexistent_code_zz"}).get_json()
        assert body["complete"] is True
        assert body["empty_text"] == dfv._NO_RESULTS["empty_where"]

    def test_rows_or_a_successor_carry_no_empty_text(self, listing, identity_repo):
        app, api = listing
        client = signed_in(app, identity_repo, role="member")
        assert "empty_text" not in find(client, {"doc_type": "invoice"}).get_json()
        api.scripted_pages = [
            {"documents": [], "next_after": "c1", "complete": False, "total_count": 2,
             "where": {"applied": True, "clauses": 1, "resolved": []}},
        ]
        assert "empty_text" not in find(client, {"doc_type": "invoice"}).get_json()

    @pytest.mark.parametrize("page, expected", [
        ({"documents": [], "next_after": None, "complete": True}, "empty_where"),
        ({"documents": [], "next_after": None, "complete": False}, "incomplete"),
        ({"documents": [], "next_after": None}, "incomplete"),
        ({"documents": [], "next_after": "c"}, None),
        ({"documents": [{"pointer": "p"}], "next_after": None, "complete": True}, None),
    ])
    def test_the_truth_table(self, page, expected):
        texts = {"empty_where": dfv._NO_RESULTS["empty_where"],
                 "incomplete": dfv.EMPTY_INCOMPLETE_LISTING, None: None}
        assert dfv.listing_empty_text(page) == texts[expected]


class TestDeadlineBannerWithoutRegistry:
    """platform-search-4: a failed registry read never drops the H9 banner."""

    @pytest.fixture
    def with_deadline(self, listing):
        app, api = listing
        api.registry.append(field_def("deadline", "date", "Frist", display=True,
                                      date_role="due", pack="legal_ch"))
        return app, api

    @pytest.mark.parametrize("status", [429, 503, 500, 403])
    def test_the_last_registry_decides(self, with_deadline, identity_repo, status,
                                       monkeypatch):
        import time

        import doc_fields_capability as dfc

        app, api = with_deadline
        client = signed_in(app, identity_repo, role="member")
        assert find(client, {"doc_type": "invoice"},
                    sort={"field": "deadline", "order": "asc"}).get_json()["deadline_banner"]
        later = time.monotonic() + 10_000
        monkeypatch.setattr(dfc, "_now", lambda: later)   # the cached registry expired
        api.fail_call("doc_fields", status)
        body = find(client, {"doc_type": "invoice"},
                    sort={"field": "deadline", "order": "asc"}).get_json()
        # 429 and 5xx: Knovas did not answer, so the last registry is used
        # (and not asked for again within 30 s: the later failures are never
        # read). 403: a refusal, so the listing goes without the fields and
        # the last registry still decides the banner.
        assert body["document_fields"]["fields_unavailable"] is (status == 403)
        assert body["deadline_banner"] == dfv.DEADLINE_BANNER
        api.fail_call("doc_fields", status)
        filtered = find(client, {"deadline": {"gte": "01.10.2026"}}).get_json()
        assert filtered["deadline_banner"] == dfv.DEADLINE_BANNER
        api.fail_call("doc_fields", status)
        plain = find(client, {"doc_type": "invoice"}).get_json()
        assert "deadline_banner" not in plain, "the last registry knows doc_type"

    def test_without_any_registry_every_field_listing_carries_it(self, with_deadline,
                                                                  identity_repo):
        import doc_fields_capability as dfc

        app, api = with_deadline
        client = signed_in(app, identity_repo, role="member")
        dfc.invalidate()
        api.fail_call("doc_fields", 503)
        body = find(client, {"doc_type": "invoice"}).get_json()
        assert body["document_fields"]["fields_unavailable"] is True
        assert body["deadline_banner"] == dfv.DEADLINE_BANNER


class TestRefusals:
    @pytest.mark.parametrize("status, code, details, browser_status, browser_code", [
        (400, "unknown_field", {"path": "where.mandnt", "suggest": ["mandant"]},
         400, "filter_invalid"),
        (400, "invalid_value", {"path": "sort.field"}, 400, "filter_invalid"),
        (400, "invalid_cursor", {"path": "after"}, 400, "invalid_cursor"),
        (400, "where_unsupported", {}, 409, "filters_unavailable"),
        (503, "where_unavailable", {}, 503, "filter_temporarily_unavailable"),
        (403, "assertion_rejected", {}, 403, "filter_not_authorized"),
    ])
    def test_mapping(self, listing, identity_repo, status, code, details, browser_status,
                     browser_code):
        app, api = listing
        api.fail_call("find_doc_values", status, code, **details)
        client = signed_in(app, identity_repo, role="member")
        response = find(client, {"doc_type": "invoice"})
        assert response.status_code == browser_status
        assert response.get_json()["error_code"] == browser_code
        assert len(_finds(api)) == 1

    def test_where_unsupported_lowers_the_capability(self, listing, identity_repo):
        import doc_fields_capability as dfc

        app, api = listing
        api.fail_call("find_doc_values", 400, "where_unsupported")
        client = signed_in(app, identity_repo, role="member")
        find(client, {"doc_type": "invoice"})
        assert dfc.shared_cache().peek().value == "values"

    def test_feature_off_turns_the_capability_off(self, listing, identity_repo):
        import doc_fields_capability as dfc
        from knovas_client import DocFieldsUnavailable

        app, api = listing
        api.fail_call("find_doc_values", DocFieldsUnavailable())
        client = signed_in(app, identity_repo, role="member")
        response = find(client, {"doc_type": "invoice"})
        assert response.status_code == 409
        assert dfc.shared_cache().peek().value == "off"


class TestGrants:
    def test_a_listed_document_can_be_previewed(self, listing, identity_repo):
        """The pattern of test_web_content_wall: the gate stands aside for a
        document the person's own listing returned, and only for that."""
        app, api = listing
        client = signed_in(app, identity_repo, role="member")
        find(client, {"doc_type": "invoice"})
        ok = client.get(f"/api/document/{INVOICE}/download")
        assert (ok.get_json() or {}).get("error") != "Not found"
        assert client.get(f"/api/document/{MEMO}/download").status_code == 404

    def test_a_withheld_page_grants_nothing(self, listing, identity_repo):
        app, api = listing
        api.drop_where_echo = True
        client = signed_in(app, identity_repo, role="member")
        find(client, {"doc_type": "invoice"})
        assert client.get(f"/api/document/{INVOICE}/download").status_code == 404

    def test_a_grant_belongs_to_one_person(self, listing, identity_repo):
        app, api = listing
        finder = signed_in(app, identity_repo, "erste@kanzlei.ch", role="member")
        find(finder, {"doc_type": "invoice"})
        other = signed_in(app, identity_repo, "zweite@kanzlei.ch", role="member")
        assert other.get(f"/api/document/{INVOICE}/download").status_code == 404


class TestExperimentDocuments:
    """Experiment documents (experiments/<domain>/<KEY>) reach nobody through
    the listing, as they reach nobody through search (SearchIntegration.split):
    Knovas cannot know who holds an Experimente role, and an experiment
    pointer is never a file grant. Today they carry no fields, but a folder
    default could give them values."""

    def test_the_listing_drops_them_and_grants_none(self, listing, identity_repo, tmp_path):
        from document_grants import DocumentGrantStore

        app, api = listing
        api.add_document(EXP_POINTER, fields={"doc_type": "invoice"}, in_search=False)
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "invoice"}).get_json()
        assert [d["doc_id"] for d in body["documents"]] == [INVOICE]
        assert "experiments/" not in json.dumps(body["documents"])
        member = identity_repo.get_by_email("anwalt@kanzlei.ch")
        grants = DocumentGrantStore(str(tmp_path / "grants.sqlite3"))
        assert grants.granted(str(member.id), INVOICE)
        assert not grants.granted(str(member.id), EXP_POINTER)
        assert not grants.granted(str(member.id), "/" + EXP_POINTER)

    def test_a_page_of_experiments_only_is_passed_on_empty(self, listing, identity_repo):
        """Dropped before the empty-state rules: a page with a successor
        still says nothing about the documents after it (H5, H8)."""
        app, api = listing
        api.scripted_pages = [
            {"documents": [{"pointer": EXP_POINTER, "title": "MKT-1"}],
             "next_after": "c1", "complete": False, "total_count": 2,
             "where": {"applied": True, "clauses": 1, "resolved": []}},
        ]
        client = signed_in(app, identity_repo, role="member")
        body = find(client, {"doc_type": "invoice"}).get_json()
        assert body["documents"] == [] and body["next_after"] == "c1"
        assert "empty_text" not in body


class TestNoValuesInLogs:
    def test_pointers_names_and_filters_stay_out_of_the_log(self, listing, identity_repo,
                                                            caplog):
        app, api = listing
        api.add_document("rc-sync/Sentinel-Mandant/Sentinel-Akte.pdf",
                         title="Sentinel-Titel",
                         fields={"doc_type": "contract", "mandant": {"name": "Sentinel-AG"}})
        client = signed_in(app, identity_repo, role="member")
        caplog.set_level(logging.DEBUG)
        find(client, {"mandant": {"name": "Sentinel-AG"}})
        api.fail_call("find_doc_values", 400, "unknown_field", path="where.sentinel")
        find(client, {"sentinel": "Sentinel-Wert"})
        text = "\n".join(r.getMessage() for r in caplog.records)
        for needle in ("Sentinel-Mandant", "Sentinel-Akte", "Sentinel-Titel", "Sentinel-AG",
                       "Sentinel-Wert"):
            assert needle not in text, needle
