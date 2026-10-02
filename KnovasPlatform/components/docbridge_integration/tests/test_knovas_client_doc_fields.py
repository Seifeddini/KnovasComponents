"""KnovasAPIClient: document fields on the wire (spec 4.1, D6-D8, D10, D13).

Everything is asserted on what leaves the process -- the FakeSession records
each request -- because the failures this guards against are wire failures:
a filter that silently did not go out, a pointer in a URL the gateway logs,
a PATCH replayed by tenacity, a probe that logs an ERROR per worker.
"""

from __future__ import annotations

import inspect
import logging

import pytest
import requests

from knovas_client import (
    ASSERTION_FIELD,
    DocFieldsError,
    DocFieldsUnavailable,
    GraphError,
    KnovasAPIClient,
    QueryRejected,
    _secured_query_hit_to_row,
    _unwrap_secured_query_response,
)
from test_knovas_client_hardening import FakeSession, make_client, make_secured_client

ASSERTION = "signed.assertion.jws"
SENTINEL_NAME = "Muster-Sentinel-AG"
SENTINEL_POINTER = "rc-sync/Sentinel-Mandant/Geheim-Sentinel.pdf"
SENTINEL_QUERY = "Sentinel-Frage nach Kuendigung"


class Resp:
    """A response double with a real JSON / non-JSON distinction."""

    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else ("" if body is None else str(body))
        self.content = b"x" if (body is not None or text) else b""
        self.reason = "Reason"

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"{self.status_code} Error")
            err.response = self
            raise err


class Broker:
    class User:
        id = "11111111-1111-1111-1111-111111111111"

    def __init__(self, user=True):
        self.user = Broker.User() if user else None

    def current_user(self):
        return self.user

    def assertion_for(self, user):
        return ASSERTION


def secured(*answers, broker=True):
    """A secured client whose session answers from ``answers`` in turn (the
    last one repeats). Each answer is a Resp, an exception, or a callable."""
    client = make_secured_client()
    queue = list(answers)

    def responder(method, url, **kw):
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(answer) and not isinstance(answer, Resp):
            answer = answer(method, url, **kw)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    client._session = FakeSession(responder)
    if broker:
        client.attach_principal_broker(Broker())
    return client


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    import tenacity

    monkeypatch.setattr(KnovasAPIClient._make_request.retry, "wait", tenacity.wait_none())


def _calls(client):
    return client._session.calls


# ---------------------------------------------------------------------------
# Search: where / return_fields / limit
# ---------------------------------------------------------------------------

class TestSearchBody:
    def test_without_the_new_keys_the_body_is_todays(self):
        client = secured(Resp(200, {"results": []}), broker=False)
        client.search_documents("Mietvertrag", limit=5, filters={"akten_id": "A-42"})
        assert _calls(client)[0]["json"] == {
            "Input": "Mietvertrag", "limit": 5, "top_k": 5, "filters": {"akten_id": "A-42"}}

    def test_where_and_return_fields_go_out_only_when_given(self):
        client = secured(Resp(200, {"results": []}))
        client.search_documents("q", limit=5, where={"doc_type": "invoice"})
        client.search_documents("q", limit=5, return_fields=["title", "doc_type"])
        client.search_documents("q", limit=5, where=None, return_fields=None)
        first, second, third = (c["json"] for c in _calls(client))
        assert first["where"] == {"doc_type": "invoice"} and "return_fields" not in first
        assert second["return_fields"] == ["title", "doc_type"] and "where" not in second
        assert "where" not in third and "return_fields" not in third
        assert all(c["json"][ASSERTION_FIELD] == ASSERTION for c in _calls(client))

    @pytest.mark.parametrize("limit, sent", [(120, 50), (51, 50), (50, 50), (20, 20), (1, 1)])
    def test_limit_is_clamped_to_50(self, limit, sent):
        client = secured(Resp(200, {"results": []}))
        client.search_documents("q", limit=limit)
        body = _calls(client)[0]["json"]
        assert body["limit"] == sent and body["top_k"] == sent

    def test_a_non_positive_limit_still_sends_none(self):
        client = secured(Resp(200, {"results": []}))
        client.search_documents("q", limit=0)
        assert "limit" not in _calls(client)[0]["json"]

    @pytest.mark.parametrize("kw", [{"where": {"doc_type": "invoice"}},
                                    {"return_fields": ["title"]},
                                    {"return_fields": False}])
    def test_legacy_mode_refuses_the_new_keys_and_sends_nothing(self, kw):
        for client in (make_client(use_secured_api=False, allow_legacy_api_fallback=True),
                       make_client(use_secured_api=True)):
            client._session = FakeSession(lambda *a, **k: pytest.fail("sent"))
            with pytest.raises(ValueError):
                client.search_documents("q", limit=5, **kw)


class TestQueryRejected:
    @pytest.mark.parametrize("kw, status, body", [
        ({"where": {"doc_type": "x"}}, 400, {"error_code": "where_unsupported"}),
        ({"return_fields": ["title"]}, 400, {"error_code": "where_unsupported"}),
        ({"where": {"mandat": "x"}}, 400, {"error_code": "unknown_field", "path": "where.mandat",
                                           "suggest": ["mandant"]}),
        ({"where": {"doc_type": "x"}}, 503, {"error_code": "where_requires_calibration"}),
        ({"where": {"doc_type": "x"}}, 503, {"error_code": "where_unavailable"}),
        ({"return_fields": ["x"] * 65}, 400, {"error_code": "where_too_complex"}),
        ({"where": {"doc_type": "x"}}, 503, {"error_code": "doc_fields_something_new"}),
        ({"where": {"doc_type": "x"}}, 503, {"error_code": "relevance_mode_calibration_missing"}),
    ])
    def test_doc_fields_codes_on_a_doc_fields_query(self, kw, status, body):
        client = secured(Resp(status, dict(body, status="error", error="fixed")))
        with pytest.raises(QueryRejected) as caught:
            client.search_documents("q", limit=5, **kw)
        assert caught.value.status == status
        assert caught.value.error_code == body["error_code"]
        assert caught.value.details.get("suggest") == body.get("suggest")
        assert len(_calls(client)) == 1  # never retried, never without where

    @pytest.mark.parametrize("kw, status, body", [
        # Unfiltered queries keep today's error, whatever the body says.
        ({}, 400, {"error_code": "where_unsupported"}),
        ({}, 503, {"error_code": "where_requires_calibration"}),
        # Not a doc-fields code.
        ({"where": {"doc_type": "x"}}, 500, {"error": "boom"}),
        ({"where": {"doc_type": "x"}}, 502, None),
        # Only a query WITH where is relevance-gated by it.
        ({"return_fields": ["title"]}, 503, {"error_code": "relevance_mode_calibration_missing"}),
    ])
    def test_everything_else_is_the_old_http_error(self, kw, status, body):
        client = secured(Resp(status, body))
        with pytest.raises(requests.exceptions.HTTPError):
            client.search_documents("q", limit=5, **kw)


class TestSearchResponse:
    HIT = {"pointer": "rc-sync/Muster AG/GJ 2024/Rechnung_17.pdf", "cosine_similarity": 0.8}

    def test_honesty_keys_survive_into_semantix(self):
        client = secured(Resp(200, {
            "status": "success", "results": [dict(self.HIT)],
            "no_strong_matches": False, "relevance_gate_applied": True,
            "meta": {"degraded_to_bm25": True},
            "where": {"applied": True, "clauses": 2, "may_be_partial": True,
                      "resolved": [{"field": "doc_type", "op": "eq", "value": "invoice"}],
                      "strategy": "allowlist", "exhaustive": True},
            "return_fields": {"applied": False},
        }))
        meta = client.search_documents("q", limit=5, where={"doc_type": "invoice"})["semantix"]
        assert meta["no_strong_matches"] is False
        assert meta["no_results_reason"] is None
        assert meta["relevance_gate_applied"] is True
        assert meta["degraded_to_bm25"] is True
        assert meta["where"] == {"applied": True, "clauses": 2, "may_be_partial": True,
                                 "resolved": [{"field": "doc_type", "op": "eq",
                                               "value": "invoice"}]}
        assert meta["return_fields"] == {"applied": False}

    def test_an_old_server_gives_nulls_and_no_echo(self):
        client = secured(Resp(200, {"status": "success", "results": []}))
        meta = client.search_documents("q", limit=5)["semantix"]
        assert {k: meta[k] for k in ("no_strong_matches", "no_results_reason",
                                     "relevance_gate_applied", "degraded_to_bm25")} == dict.fromkeys(
            ("no_strong_matches", "no_results_reason", "relevance_gate_applied",
             "degraded_to_bm25"))
        assert "where" not in meta and "return_fields" not in meta

    def test_a_nested_response_keeps_its_echo(self):
        out = _unwrap_secured_query_response({"status": "success", "data": {
            "results": [], "where": {"applied": True}, "no_results_reason": "empty_where",
            "meta": {"degraded_to_bm25": False}}})
        assert out["where"] == {"applied": True}
        assert out["no_results_reason"] == "empty_where"

    def test_rows_carry_fields_tier_and_the_title_rule(self):
        hit = dict(self.HIT, relevance_tier="borderline", score_mode="vector",
                   fields={"title": "Vertrag mit Muster AG", "doc_type": "invoice"})
        row = _secured_query_hit_to_row(hit)
        assert row["fields"] == {"title": "Vertrag mit Muster AG", "doc_type": "invoice"}
        assert row["title"] == "Vertrag mit Muster AG" and row["title_from_values"] is True
        assert row["relevance_tier"] == "borderline" and row["score_mode"] == "vector"

    @pytest.mark.parametrize("title", ["Rechnung_17.pdf", "rechnung_17", "x" * 120])
    def test_a_file_name_or_run_on_values_title_gives_the_stem(self, title):
        row = _secured_query_hit_to_row(dict(self.HIT, fields={"title": title}))
        assert row["title"] == "Rechnung_17" and row["title_from_values"] is False

    def test_without_fields_the_row_is_todays(self):
        row = _secured_query_hit_to_row(dict(self.HIT))
        assert "fields" not in row and "title_from_values" not in row
        assert "relevance_tier" not in row
        assert row["title"] == "Rechnung_17"


# ---------------------------------------------------------------------------
# Doc-fields routes
# ---------------------------------------------------------------------------

def _all_methods(client):
    """Call every doc-fields method once."""
    client.doc_fields()
    client.create_doc_field({"key": "mandant", "datatype": "entity_ref"})
    client.update_doc_field("f-1", {"facet": True})
    client.deprecate_doc_field("f-1")
    client.doc_field_packs()
    client.install_doc_field_pack("legal_ch")
    client.doc_field_settings()
    client.set_doc_field_settings(unknown_keys="ignore")
    client.doc_field_rules()
    client.put_doc_field_rule("rc-sync/Muster AG/", {"legal_area": ["corporate"]})
    client.retire_doc_field_rule("rc-sync/Muster AG/")
    client.doc_values(SENTINEL_POINTER)
    client.patch_doc_values(SENTINEL_POINTER, 3, set={"mandant": SENTINEL_NAME},
                            actor_ref="platform-user:11")
    client.find_doc_values({"mandant": {"name": SENTINEL_NAME}})


OK_BODY = {"status": "success", "message": "ok", "fields": [], "packs": [], "rules": [],
           "field": {"id": "f-1"}, "documents": [], "next_after": None, "complete": True}


class TestDocFieldsWire:
    def test_every_call_carries_the_assertion_gets_included(self):
        client = secured(Resp(200, dict(OK_BODY)))
        _all_methods(client)
        calls = _calls(client)
        assert len(calls) == 14
        assert all(c["json"][ASSERTION_FIELD] == ASSERTION for c in calls)
        assert {c["method"] for c in calls} == {"GET", "POST", "PATCH", "PUT", "DELETE"}

    def test_no_pointer_name_or_q_in_any_url(self):
        client = secured(Resp(200, dict(OK_BODY)))
        _all_methods(client)
        for call in _calls(client):
            assert call.get("params") is None
            assert "?" not in call["url"]
            for needle in ("pointer=", "q=", "Sentinel", "Muster"):
                assert needle not in call["url"]

    def test_routes_and_bodies(self):
        client = secured(Resp(200, dict(OK_BODY)), broker=False)
        _all_methods(client)
        seen = [(c["method"], c["url"].replace("https://knovas.test", ""), c["json"])
                for c in _calls(client)]
        assert seen == [
            ("GET", "/secured/graph/doc-fields", None),
            ("POST", "/secured/graph/doc-fields", {"key": "mandant", "datatype": "entity_ref"}),
            ("PATCH", "/secured/graph/doc-fields/f-1", {"facet": True}),
            ("POST", "/secured/graph/doc-fields/f-1/deprecate", None),
            ("GET", "/secured/graph/doc-fields/packs", None),
            ("POST", "/secured/graph/doc-fields/packs/legal_ch/install", None),
            ("GET", "/secured/graph/doc-fields/settings", None),
            ("PUT", "/secured/graph/doc-fields/settings", {"unknown_keys": "ignore"}),
            ("GET", "/secured/graph/doc-field-rules", None),
            ("PUT", "/secured/graph/doc-field-rules",
             {"pointer_prefix": "rc-sync/Muster AG/", "set": {"legal_area": ["corporate"]}}),
            ("DELETE", "/secured/graph/doc-field-rules", {"pointer_prefix": "rc-sync/Muster AG/"}),
            ("GET", "/secured/graph/doc-values", {"pointer": SENTINEL_POINTER}),
            ("PATCH", "/secured/graph/doc-values",
             {"pointer": SENTINEL_POINTER, "if_version": 3, "set": {"mandant": SENTINEL_NAME},
              "fields_strict": False, "actor_ref": "platform-user:11"}),
            ("POST", "/secured/graph/doc-values/find",
             {"where": {"mandant": {"name": SENTINEL_NAME}}, "limit": 50}),
        ]

    def test_path_segments_are_quoted(self):
        client = secured(Resp(200, dict(OK_BODY)))
        client.install_doc_field_pack("a/../b c")
        client.update_doc_field("x/y", {"facet": False})
        urls = [c["url"] for c in _calls(client)]
        assert urls[0].endswith("/doc-fields/packs/a%2F..%2Fb%20c/install")
        assert urls[1].endswith("/doc-fields/x%2Fy")

    def test_find_options_only_when_given_and_limit_capped(self):
        client = secured(Resp(200, dict(OK_BODY)))
        client.find_doc_values({"doc_type": "invoice"}, sort={"field": "document_date",
                                                              "order": "desc"},
                               limit=5000, after="cursor-2", return_fields=["title"])
        body = _calls(client)[0]["json"]
        assert body["limit"] == 200 and body["after"] == "cursor-2"
        assert body["sort"] == {"field": "document_date", "order": "desc"}
        assert body["return_fields"] == ["title"]

    def test_patch_sends_only_the_sections_given_and_strict_false(self):
        client = secured(Resp(200, {"status": "success", "version": 4, "fields": {},
                                    "warnings": [{"key": "party", "code": "unresolved_entity"}]}))
        out = client.patch_doc_values("a.pdf", 0, set={"title": "Neu"}, unset=[], add=None)
        body = _calls(client)[0]["json"]
        assert set(body) == {"pointer", "if_version", "set", "fields_strict", ASSERTION_FIELD}
        assert body["fields_strict"] is False
        assert out == {"version": 4, "fields": {},
                       "warnings": [{"key": "party", "code": "unresolved_entity"}]}

    @pytest.mark.parametrize("call", [
        lambda c: c.patch_doc_values("a.pdf", 1),
        lambda c: c.patch_doc_values("a.pdf", -1, set={"title": "x"}),
        lambda c: c.patch_doc_values("a.pdf", True, set={"title": "x"}),
        lambda c: c.patch_doc_values("a.pdf", "1", set={"title": "x"}),
        lambda c: c.patch_doc_values(" ", 1, set={"title": "x"}),
        lambda c: c.patch_doc_values("a.pdf", 1, set={"title": "x"}, actor_ref="a@b.ch"),
        lambda c: c.doc_values(""),
        lambda c: c.find_doc_values({}),
        lambda c: c.find_doc_values(None),
        lambda c: c.put_doc_field_rule("rc-sync/Muster AG", {"x": 1}),
        lambda c: c.put_doc_field_rule("rc-sync/", {}),
        lambda c: c.retire_doc_field_rule(""),
        lambda c: c.update_doc_field("f", {}),
        lambda c: c.set_doc_field_settings(),
        lambda c: c.create_doc_field({}),
    ])
    def test_writes_that_would_not_happen_are_refused_locally(self, call):
        client = secured(Resp(200, dict(OK_BODY)))
        with pytest.raises(ValueError):
            call(client)
        assert _calls(client) == []


class TestDocFieldsAnswers:
    def test_return_shapes_without_the_envelope(self):
        client = secured(Resp(200, {"status": "success", "message": "m",
                                    "fields": [{"key": "doc_type"}, "junk"]}))
        assert client.doc_fields() == [{"key": "doc_type"}]
        client = secured(Resp(200, {"status": "success", "message": "m", "pointer": "a.pdf",
                                    "version": 2, "fields": {}, "layers": {}}))
        assert client.doc_values("a.pdf") == {"pointer": "a.pdf", "version": 2,
                                              "fields": {}, "layers": {}}

    def test_404_not_found_is_none(self):
        body = {"status": "error", "error_code": "NOT_FOUND", "error": "Document not found"}
        client = secured(Resp(404, body))
        assert client.doc_values("a.pdf") is None
        assert client.patch_doc_values("a.pdf", 1, set={"title": "x"}) is None
        assert client.update_doc_field("f", {"facet": True}) is None
        assert client.deprecate_doc_field("f") is None
        assert client.retire_doc_field_rule("rc-sync/x/") is None

    def test_404_not_found_on_a_collection_is_not_an_empty_list(self):
        client = secured(Resp(404, {"error_code": "NOT_FOUND"}))
        for call in (client.doc_fields, client.doc_field_packs, client.doc_field_rules,
                     client.doc_field_settings):
            with pytest.raises(DocFieldsUnavailable):
                call()

    def test_404_pack_not_found_is_an_error(self):
        client = secured(Resp(404, {"error_code": "pack_not_found", "error": "Field pack not found"}))
        with pytest.raises(DocFieldsError) as caught:
            client.install_doc_field_pack("medical")
        assert caught.value.error_code == "pack_not_found" and caught.value.status == 404

    @pytest.mark.parametrize("resp", [
        Resp(404, {"status": "error", "error": "Not Found", "error_code": "HTTP_404"}),
        Resp(404, {"error_code": "knowledge_graph_disabled"}),
        Resp(404, None, text="<html>Not Found</html>"),
        Resp(404, ["not", "an", "object"]),
    ])
    def test_any_other_404_means_the_feature_is_off(self, resp):
        client = secured(resp)
        with pytest.raises(DocFieldsUnavailable):
            client.doc_values("a.pdf")
        with pytest.raises(DocFieldsUnavailable):
            client.patch_doc_values("a.pdf", 1, set={"title": "x"})

    def test_errors_carry_code_and_whitelisted_details(self):
        client = secured(Resp(409, {"status": "error", "error_code": "version_conflict",
                                    "error": "The document values changed since they were read",
                                    "current_version": 7, "value": SENTINEL_NAME,
                                    "pointer": SENTINEL_POINTER}))
        with pytest.raises(DocFieldsError) as caught:
            client.patch_doc_values("a.pdf", 6, set={"title": "x"})
        err = caught.value
        assert (err.status, err.error_code) == (409, "version_conflict")
        assert err.message == "The document values changed since they were read"
        assert err.details == {"current_version": 7}
        assert isinstance(err, GraphError)

    def test_details_whitelist(self):
        body = {"error_code": "invalid_fields", "path": "fields.x", "suggest": ["a", 1],
                "candidates": ["b"], "errors": [{"path": "fields.x", "code": "c",
                                                 "value": SENTINEL_NAME}, "junk"],
                "current_version": True, "raw": SENTINEL_NAME}
        client = secured(Resp(422, body))
        with pytest.raises(DocFieldsError) as caught:
            client.create_doc_field({"key": "x"})
        assert caught.value.details == {"path": "fields.x", "suggest": ["a"],
                                        "candidates": ["b"],
                                        "errors": [{"path": "fields.x", "code": "c"}]}

    def test_rate_limit_and_server_errors(self):
        client = secured(Resp(429, {"error": "too_many_requests", "message": "slow down",
                                    "status": 429}))
        with pytest.raises(DocFieldsError) as caught:
            client.doc_fields()
        assert caught.value.error_code == "too_many_requests"
        client = secured(Resp(403, {"error_code": "registry_write_requires_full_clearance"}))
        with pytest.raises(DocFieldsError) as caught:
            client.doc_field_rules()
        assert caught.value.status == 403

    def test_a_2xx_without_json_is_not_success(self):
        client = secured(Resp(200, None, text="<html>"))
        with pytest.raises(DocFieldsError) as caught:
            client.patch_doc_values("a.pdf", 1, set={"title": "x"})
        assert caught.value.error_code == "invalid_response"

    def test_writes_are_sent_exactly_once(self):
        client = secured(requests.exceptions.ConnectionError("reset"))
        with pytest.raises(DocFieldsError) as caught:
            client.patch_doc_values("a.pdf", 1, set={"title": "x"})
        assert caught.value.error_code == "transport_error"
        assert len(_calls(client)) == 1
        for write in (lambda: client.create_doc_field({"key": "k"}),
                      lambda: client.put_doc_field_rule("p/", {"k": 1}),
                      lambda: client.retire_doc_field_rule("p/"),
                      lambda: client.install_doc_field_pack("core"),
                      lambda: client.set_doc_field_settings(date_order="dmy"),
                      lambda: client.deprecate_doc_field("f"),
                      lambda: client.update_doc_field("f", {"facet": True})):
            before = len(_calls(client))
            with pytest.raises(DocFieldsError):
                write()
            assert len(_calls(client)) == before + 1

    def test_a_write_answered_with_an_error_is_not_repeated(self):
        client = secured(Resp(503, {"error_code": "doc_fields_unavailable"}))
        with pytest.raises(DocFieldsError):
            client.patch_doc_values("a.pdf", 1, set={"title": "x"})
        assert len(_calls(client)) == 1

    def test_reads_keep_the_transport_retry(self):
        client = secured(requests.exceptions.ConnectionError("reset"))
        with pytest.raises(DocFieldsError) as caught:
            client.doc_values("a.pdf")
        assert caught.value.error_code == "transport_error"
        assert len(_calls(client)) == 3

    def test_nobody_signed_in_sends_nothing(self):
        client = make_secured_client()
        client._session = FakeSession(lambda *a, **k: pytest.fail("sent"))
        client.attach_principal_broker(Broker(user=False))
        with pytest.raises(PermissionError):
            client.doc_values("a.pdf")
        with pytest.raises(PermissionError):
            client.doc_fields_probe()

    def test_a_refused_probe_costs_no_rate_limit_slot(self, monkeypatch):
        client = make_secured_client()
        client.attach_principal_broker(Broker(user=False))
        monkeypatch.setattr(client, "_rate_limit", lambda: pytest.fail("rate limited"))
        with pytest.raises(PermissionError):
            client.doc_fields_probe()


class TestGraphErrorReadsError:
    def test_message_falls_back_to_error_and_keeps_details(self):
        client = secured(Resp(422, {"error_code": "key_looks_personal",
                                    "error": "The field key looks like personal data",
                                    "path": "aliases[0]"}))
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.message == "The field key looks like personal data"
        assert caught.value.details == {"path": "aliases[0]"}

    def test_the_constructor_stays_compatible(self):
        err = GraphError(500, None, "boom")
        assert (err.status, err.error_code, err.message, err.details) == (500, None, "boom", {})


def test_entity_suggestions_never_send_the_typed_text():
    """D10: the node list is fetched by type, without q; what the person
    types is matched inside the Platform and never reaches a URL."""
    import doc_fields_capability as cap
    import doc_fields_view as view
    from doc_fields_fakes import field_def

    registry = {"status": "success", "fields": [
        field_def("mandant", "entity_ref", "Mandant", target="t-mandant")]}
    nodes = {"status": "success", "nodes": [
        {"id": "m1", "name": "Muster AG", "node_type_id": "t-mandant"},
        {"id": "m2", "name": "Beispiel GmbH", "node_type_id": "t-mandant"}]}
    client = secured(lambda method, url, **kw: Resp(
        200, registry if url.endswith("/doc-fields") else nodes))
    names = cap.entity_names_for(client, "alice", "mandant")
    assert view.match_names(names, "Muster-Sentinel") == []
    assert view.match_names(names, "must") == ["Muster AG"]
    graph_call = _calls(client)[-1]
    assert graph_call["url"].endswith("/secured/graph/nodes")
    assert graph_call["params"] == {"node_type_id": "t-mandant"}
    for call in _calls(client):
        assert "q=" not in call["url"] and "q" not in (call.get("params") or {})
        assert "must" not in str(call).lower().replace("muster ag", "")
    assert len(_calls(client)) == 2


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------

class TestProbe:
    @pytest.mark.parametrize("resp, answer", [
        (Resp(404, {"status": "error", "error": "Not Found", "error_code": "HTTP_404"}), "off"),
        (Resp(404, {"error_code": "knowledge_graph_disabled"}), "off"),
        (Resp(404, None, text="<html>nginx</html>"), "off"),
        (Resp(404, {"error_code": "NOT_FOUND"}), "unknown"),
        (Resp(404, {"error_code": "pack_not_found"}), "unknown"),
        (Resp(400, {"error_code": "where_unsupported"}), "values"),
        (Resp(400, {"error_code": "invalid_value", "path": "where"}), "filters"),
        (Resp(400, {"error_code": "invalid_value", "path": "sort"}), "unknown"),
        (Resp(400, None, text="bad"), "unknown"),
        (Resp(401, {"error_code": "assertion_rejected"}), "unknown"),
        (Resp(403, {"error_code": "forbidden"}), "unknown"),
        (Resp(429, {"error": "too_many_requests"}), "unknown"),
        (Resp(500, None, text="boom"), "unknown"),
        (Resp(503, {"error_code": "where_unavailable"}), "unknown"),
        (Resp(200, {"documents": []}), "unknown"),
    ])
    def test_classification(self, resp, answer):
        client = secured(resp)
        assert client.doc_fields_probe() == answer

    def test_one_quiet_request_with_an_empty_body_and_the_assertion(self, caplog):
        client = secured(Resp(404, {"error_code": "HTTP_404", "error": "Not Found"}))
        with caplog.at_level(logging.DEBUG):
            assert client.doc_fields_probe() == "off"
        (call,) = _calls(client)
        assert call["method"] == "POST"
        assert call["url"] == "https://knovas.test/secured/graph/doc-values/find"
        assert call["json"] == {ASSERTION_FIELD: ASSERTION}
        assert call["allow_redirects"] is False
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_without_a_broker_the_body_is_empty(self):
        client = secured(Resp(400, {"error_code": "where_unsupported"}), broker=False)
        client.doc_fields_probe()
        assert _calls(client)[0]["json"] == {}

    def test_a_network_error_is_unknown_after_one_attempt(self):
        client = secured(requests.exceptions.ConnectionError("down"))
        assert client.doc_fields_probe() == "unknown"
        assert len(_calls(client)) == 1

    def test_secured_mode(self):
        assert make_secured_client().secured_mode() is True
        assert make_client().secured_mode() is False


# ---------------------------------------------------------------------------
# Values never reach a log line
# ---------------------------------------------------------------------------

def test_no_value_in_any_log_line(caplog):
    """D6: a field value, a pointer, a name or the query text in the log is
    a leak. Every path through the new code, success and failure."""
    answers = [
        Resp(200, dict(OK_BODY)),
        Resp(400, {"error_code": "invalid_value", "path": "set.mandant[0]",
                   "error": "A field value is invalid"}),
        Resp(404, {"error_code": "NOT_FOUND", "error": "Document not found"}),
        Resp(404, {"error_code": "HTTP_404", "error": "Not Found"}),
        Resp(409, {"error_code": "version_conflict", "current_version": 2}),
        Resp(503, {"error_code": "where_requires_calibration"}),
        requests.exceptions.ConnectionError("down"),
    ]
    with caplog.at_level(logging.DEBUG):
        for answer in answers:
            client = secured(answer)
            for call in (
                client.doc_fields_probe,
                lambda: client.doc_values(SENTINEL_POINTER),
                lambda: client.patch_doc_values(SENTINEL_POINTER, 1,
                                                set={"mandant": SENTINEL_NAME}),
                lambda: client.find_doc_values({"mandant": {"name": SENTINEL_NAME}}),
                lambda: client.put_doc_field_rule("rc-sync/Sentinel-Mandant/",
                                                  {"mandant": SENTINEL_NAME}),
                lambda: client.search_documents(SENTINEL_QUERY, limit=5,
                                                where={"mandant": {"name": SENTINEL_NAME}},
                                                return_fields=["title"]),
            ):
                try:
                    call()
                except (DocFieldsError, DocFieldsUnavailable, QueryRejected,
                        requests.exceptions.RequestException):
                    pass
                except Exception:  # noqa: BLE001 - tenacity RetryError on reads
                    pass
    text = caplog.text
    for sentinel in ("Sentinel", "Muster", "Geheim", "Kuendigung"):
        assert sentinel not in text


# ---------------------------------------------------------------------------
# The fakes wave 2 tests against
# ---------------------------------------------------------------------------

DOC_FIELDS_METHODS = (
    "doc_fields_probe", "doc_fields", "create_doc_field", "update_doc_field",
    "deprecate_doc_field", "doc_field_packs", "install_doc_field_pack", "doc_field_settings",
    "set_doc_field_settings", "doc_field_rules", "put_doc_field_rule", "retire_doc_field_rule",
    "doc_values", "patch_doc_values", "find_doc_values", "search_documents", "secured_mode",
)


@pytest.mark.parametrize("name", DOC_FIELDS_METHODS)
def test_the_fake_takes_exactly_what_the_real_client_takes(name):
    """A route tested against the fake must call the real client correctly:
    same parameter names, same keyword-only split, same defaults."""
    from doc_fields_fakes import FakeDocFieldsApi

    def shape(fn):
        params = list(inspect.signature(fn).parameters.values())[1:]
        return [(p.name, p.kind, p.default) for p in params]

    assert shape(getattr(FakeDocFieldsApi, name)) == shape(getattr(KnovasAPIClient, name))


@pytest.mark.parametrize("name", DOC_FIELDS_METHODS)
def test_the_dummy_client_answers_every_doc_fields_call(name):
    from conftest import DummyKnovasClient

    assert callable(getattr(DummyKnovasClient, name))


class TestDummyClientIsOff:
    def test_doc_fields_calls_raise_what_an_off_server_gives(self):
        from conftest import DummyKnovasClient

        dummy = DummyKnovasClient(config=None)
        assert dummy.secured_mode() is False and dummy.doc_fields_probe() == "off"
        with pytest.raises(DocFieldsUnavailable):
            dummy.doc_values("a.pdf")
        with pytest.raises(ValueError):
            dummy.search_documents("q", where={"doc_type": "invoice"})
        dummy.search_documents("q", limit=3)
        assert dummy.search_requests == [{"query": "q", "limit": 3, "filters": None,
                                          "where": None, "return_fields": None}]


class TestFakeDocFieldsApi:
    def test_off(self):
        from doc_fields_fakes import FakeDocFieldsApi

        api = FakeDocFieldsApi("off")
        api.add_document("a.pdf", fields={"doc_type": "invoice"})
        assert api.doc_fields_probe() == "off"
        with pytest.raises(DocFieldsUnavailable):
            api.doc_fields()
        out = api.search_documents("q", where={"doc_type": "invoice"}, return_fields=["title"])
        assert "where" not in out["semantix"] and "fields" not in out["results"][0]

    def test_values(self):
        from doc_fields_fakes import FakeDocFieldsApi

        api = FakeDocFieldsApi("values")
        assert api.doc_fields_probe() == "values"
        with pytest.raises(DocFieldsError) as caught:
            api.find_doc_values({"doc_type": "invoice"})
        assert caught.value.error_code == "where_unsupported"
        with pytest.raises(QueryRejected):
            api.search_documents("q", return_fields=["title"])
        assert api.search_documents("q")["results"] == []

    def test_listing_only(self):
        from doc_fields_fakes import FakeDocFieldsApi

        api = FakeDocFieldsApi("listing_only")
        api.add_document("a.pdf", title="Vertrag mit Muster AG",
                         fields={"doc_type": "invoice"})
        assert api.doc_fields_probe() == "filters"  # the probe cannot tell
        with pytest.raises(QueryRejected) as caught:
            api.search_documents("q", where={"doc_type": "invoice"})
        assert (caught.value.status, caught.value.error_code) == (503, "where_requires_calibration")
        rows = api.search_documents("q", return_fields=["title", "doc_type"])["results"]
        assert rows[0]["fields"] == {"doc_type": "invoice", "title": "Vertrag mit Muster AG"}
        assert rows[0]["title_from_values"] is True
        assert api.find_doc_values({"doc_type": "invoice"})["where"]["applied"] is True

    def test_filters_search_and_find_paging(self):
        from doc_fields_fakes import FakeDocFieldsApi

        api = FakeDocFieldsApi("filters")
        for i in range(5):
            api.add_document(f"rc-sync/{i}.pdf", fields={
                "doc_type": "invoice" if i % 2 == 0 else "contract",
                "mandant": {"name": "Muster AG"}})
        out = api.search_documents("q", where={"doc_type": "invoice",
                                               "mandant": {"name": "muster ag"}})
        assert [r["doc_id"] for r in out["results"]] == ["rc-sync/0.pdf", "rc-sync/2.pdf",
                                                         "rc-sync/4.pdf"]
        assert out["semantix"]["where"]["applied"] is True
        first = api.find_doc_values({"doc_type": "invoice"}, limit=2)
        assert (len(first["documents"]), first["complete"], first["total_count"]) == (2, False, 3)
        last = api.find_doc_values({"doc_type": "invoice"}, limit=2, after=first["next_after"])
        assert (len(last["documents"]), last["next_after"], last["complete"]) == (1, None, True)
        assert "total_count" not in last
        api.drop_where_echo = True
        assert "where" not in api.search_documents("q", where={"doc_type": "invoice"})["semantix"]

    def test_values_version_conflict_and_injected_errors(self):
        from doc_fields_fakes import FakeDocFieldsApi

        api = FakeDocFieldsApi("values")
        api.add_document("a.pdf", fields={"doc_type": "invoice"})
        view = api.doc_values("a.pdf")
        out = api.patch_doc_values("a.pdf", view["version"],
                                   set={"party": "Beispiel GmbH", "mandant": "Muster AG"})
        assert out["version"] == view["version"] + 1
        assert out["warnings"] == [{"key": "party", "code": "unresolved_entity"}]
        assert out["fields"]["mandant"] == {"node_id": "m1", "name": "Muster AG"}
        assert api.doc_values("a.pdf")["layers"]["party"][0]["layer"] == "manual"
        with pytest.raises(DocFieldsError) as caught:
            api.patch_doc_values("a.pdf", view["version"], set={"title": "x"})
        assert caught.value.error_code == "version_conflict"
        assert caught.value.details == {"current_version": view["version"] + 1}
        assert api.patch_doc_values("a.pdf", out["version"], set={"title": "Neu"})["fields"] == {}
        for status, code in ((403, "change_not_authorized"), (409, "anchor_quarantined"),
                             (422, "unresolved_entity")):
            api.fail_call("patch_doc_values", status, code)
            with pytest.raises(DocFieldsError) as caught:
                api.patch_doc_values("a.pdf", 0, set={"title": "x"})
            assert caught.value.status == status
        assert api.doc_values("missing.pdf") is None
