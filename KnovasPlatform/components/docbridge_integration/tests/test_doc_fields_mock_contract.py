"""The real KnovasAPIClient and the real routes against the mock Knovas API.

The route tests use FakeDocFieldsApi, which mirrors the client's signatures;
this file closes the remaining gap: the real client, over a real
``requests`` session (testing.WsgiSession), against the mock in each server
state of spec 2.1 -- ``off``, ``values``, ``filters`` and ``filters`` without
a relevance calibration (``listing_only``). Then the whole Platform route
stack on top of it, so a shape mismatch between the client, the routes and
the server contract fails here rather than in a deployment.

Skipped when the mock is not in the checkout. Placeholder names only.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("flask")

from conftest import _identity_app, platform_db_reachable  # noqa: E402
from knovas_client import (  # noqa: E402
    ASSERTION_FIELD,
    DocFieldsUnavailable,
    KnovasAPIClient,
    QueryRejected,
)
from test_knovas_client_hardening import make_secured_client  # noqa: E402
from test_web_search_doc_fields import signed_in  # noqa: E402

TESTING_PATH = Path(__file__).resolve().parents[3] / "mock_knovas_api" / "testing.py"


def _testing():
    if not TESTING_PATH.is_file():
        pytest.skip("mock_knovas_api is not in this checkout")
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location("knovas_mock_testing", TESTING_PATH))
    module.__spec__.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    import tenacity

    monkeypatch.setattr(KnovasAPIClient._make_request.retry, "wait", tenacity.wait_none())


class Broker:
    """Signs every call with a placeholder assertion; the mock checks that
    one is there, not what it says."""

    class User:
        id = "11111111-1111-1111-1111-111111111111"

    def current_user(self):
        return Broker.User()

    def assertion_for(self, user):
        return "signed.assertion.jws"


def mock_client(mode, **kw):
    testing = _testing()
    mock = testing.load_mock_app(doc_fields=mode, brokered=True, **kw)
    client = make_secured_client(base_url="https://knovas.test")
    client._session = testing.WsgiSession(mock)
    client.attach_principal_broker(Broker())
    return client, testing.mock_state(mock)


# ---------------------------------------------------------------------------
# The client against the mock
# ---------------------------------------------------------------------------

class TestClientAgainstTheMock:
    @pytest.mark.parametrize("mode, kw, answer", [
        ("off", {}, "off"), ("values", {}, "values"), ("filters", {}, "filters"),
        ("filters", {"calibrated": False}, "filters"),
    ])
    def test_probe(self, mode, kw, answer):
        client, state = mock_client(mode, **kw)
        assert client.doc_fields_probe() == answer
        sent = state.requests[-1]
        assert (sent["method"], sent["path"]) == ("POST", "/secured/graph/doc-values/find")
        assert sent["json"][ASSERTION_FIELD]

    def test_off_ignores_the_keys_and_hides_the_routes(self):
        client, state = mock_client("off")
        result = client.search_documents("lease", limit=5, return_fields=["title", "doc_type"])
        assert [r["doc_id"] for r in result["results"]] == ["demo-001"]
        assert "where" not in result["semantix"] and "return_fields" not in result["semantix"]
        assert "fields" not in result["results"][0]
        with pytest.raises(DocFieldsUnavailable):
            client.doc_fields()
        with pytest.raises(DocFieldsUnavailable):
            client.doc_values("demo-001")

    def test_values_refuses_return_fields_and_serves_values(self):
        client, state = mock_client("values")
        with pytest.raises(QueryRejected) as refused:
            client.search_documents("lease", return_fields=["title"])
        assert (refused.value.status, refused.value.error_code) == (400, "where_unsupported")
        plain = client.search_documents("lease", limit=5)
        assert plain["semantix"]["no_strong_matches"] is False
        view = client.doc_values("demo-001")
        assert view["fields"]["doc_type"] == "contract"
        sent = state.requests[-1]
        assert sent["method"] == "GET" and sent["query"] == {}, "the pointer is in the body"
        assert sent["json"]["pointer"] == "demo-001"
        patched = client.patch_doc_values("demo-001", view["version"],
                                          set={"title": "Mietvertrag Muster AG"},
                                          actor_ref="platform-user:1")
        # As on the server: the answer carries every effective field.
        assert patched["fields"] == view["fields"] and patched["version"] == view["version"] + 1

    def test_filters_echo_and_fields(self):
        client, state = mock_client("filters")
        result = client.search_documents("contract", limit=5, where={"doc_type": "contract"},
                                         return_fields=["title", "doc_type", "party"])
        meta = result["semantix"]
        assert meta["where"]["applied"] is True
        assert meta["where"]["resolved"][0]["field"] == "doc_type"
        assert meta["return_fields"] == {"applied": True}
        assert meta["relevance_gate_applied"] is True
        rows = result["results"]
        assert [r["doc_id"] for r in rows] == ["demo-002"]
        assert rows[0]["fields"]["doc_type"] == "contract"
        assert rows[0]["fields"]["party"] == [{"name": "Beispiel GmbH"}]
        assert rows[0]["relevance_tier"] == "strong"
        # A title that is a real title replaces the stem (title rule).
        assert rows[0]["title_from_values"] is True

    def test_listing_only_refuses_where_but_returns_fields(self):
        client, state = mock_client("filters", calibrated=False)
        with pytest.raises(QueryRejected) as refused:
            client.search_documents("contract", where={"doc_type": "contract"})
        assert (refused.value.status, refused.value.error_code) == (
            503, "where_requires_calibration")
        rows = client.search_documents("contract", return_fields=["doc_type"])["results"]
        assert rows[0]["fields"] == {"doc_type": "contract"}
        page = client.find_doc_values({"doc_type": "contract"}, limit=1)
        assert len(page["documents"]) == 1 and page["next_after"]
        assert page["complete"] is False and page["total_count"] == 2
        last = client.find_doc_values({"doc_type": "contract"}, limit=1, after=page["next_after"])
        assert last["next_after"] is None and last["complete"] is True
        assert "total_count" not in last

    def test_the_field_form_s_definitions_are_accepted(self):
        """F1: what the Dokumentfelder form builds is what the server takes
        (the mock checks definitions like registry.py)."""
        from web_interface.admin_doc_fields import definition_from_form

        client, state = mock_client("values")
        for form in (
            {"key": "aktenzeichen", "datatype": "code", "code_scheme": "bger"},
            {"key": "geschaeftsjahr", "datatype": "period", "fy_start_month": "7",
             "fy_label": "start"},
            {"key": "eingang", "datatype": "date", "date_order": "mdy"},
            {"key": "gegenseite", "datatype": "entity_ref", "link_policy": "never"},
            {"key": "kostenstelle", "datatype": "enum", "choice_code_0": "4100",
             "choice_label_de_0": "Verwaltung", "choice_label_fr_0": "Administration",
             "choice_aliases_0": "Verw", "choice_code_1": "4200"},
        ):
            assert client.create_doc_field(definition_from_form(form))["key"] == form["key"]
        fields = {f["key"]: f for f in client.doc_fields()}
        assert fields["aktenzeichen"]["code_scheme"] == "bger"
        assert (fields["geschaeftsjahr"]["fy_start_month"],
                fields["geschaeftsjahr"]["fy_label"]) == (7, "start")
        assert fields["kostenstelle"]["enum_values"][0]["aliases"] == ["Verw"]


# ---------------------------------------------------------------------------
# The Platform routes on the real client, against the mock
# ---------------------------------------------------------------------------

def _app_on_mock(platform_db, tmp_path, monkeypatch, mode, **kw):
    """The identity app whose Knovas client is the real one, talking to a
    mock in ``mode`` through an in-process session."""
    testing = _testing()
    mock = testing.load_mock_app(doc_fields=mode, brokered=True, **kw)

    class RealClient(KnovasAPIClient):
        def __init__(self, config, principal_broker=None):
            config._config.setdefault("api", {}).update({
                "base_url": "https://knovas.test", "use_secured_api": True,
                "cert_path": "/certs/client.crt", "key_path": "/certs/client.key",
                "ca_cert_path": "/certs/ca.crt", "cert_auto_renew_enabled": False,
                "rate_limit": {"requests_per_second": 0, "retry_attempts": 3,
                               "retry_backoff": 2},
            })
            super().__init__(config_loader=config)
            self._session = testing.WsgiSession(mock)

    app = _identity_app(platform_db, tmp_path, monkeypatch, client_cls=RealClient)
    return app, testing.mock_state(mock)


def _queries(state):
    return [r["json"] for r in state.requests if r["path"] == "/secured/query"]


def _doc_field_paths(state):
    return [r["path"] for r in state.requests if r["path"].startswith("/secured/graph/doc-")]


@pytest.mark.skipif(not platform_db_reachable(), reason="identity routes need a real PostgreSQL")
class TestRoutesOnTheMock:
    def test_off_today_s_search(self, platform_db, tmp_path, monkeypatch, identity_repo):
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "off")
        client = signed_in(app, identity_repo, role="member")
        body = client.post("/api/search", json={"query": "lease", "limit": 80}).get_json()
        assert [r["doc_id"] for r in body["results"]] == ["demo-001"]
        assert body["document_fields"]["capability"] == "off"
        sent = _queries(state)[-1]
        # Today's body, key for key (D8).
        assert set(sent) == {"Input", "limit", ASSERTION_FIELD}
        # "Mehr laden" past 50 used to be a 422 from Knovas.
        assert sent["limit"] == 50
        # One probe, nothing else of the feature.
        assert _doc_field_paths(state) == ["/secured/graph/doc-values/find"]
        assert client.get("/api/doc-fields").get_json() == {"capability": "off", "fields": []}

    def test_values_panel_read_and_edit(self, platform_db, tmp_path, monkeypatch,
                                        identity_repo):
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "values")
        client = signed_in(app, identity_repo, role="admin")
        body = client.post("/api/search", json={"query": "lease"}).get_json()
        assert "return_fields" not in _queries(state)[-1]
        assert body["document_fields"]["capability"] == "values"
        view = client.post("/api/document-fields/read", json={"doc_id": "demo-001"}).get_json()
        rows = {r["key"]: r for r in view["fields"]}
        assert rows["doc_type"]["text"] == "Vertrag"
        assert rows["party"]["text"] == "Muster AG"
        # The mock stores dates as sent (it has no normaliser); shown as is.
        assert rows["document_date"]["text"]
        edited = client.post("/api/document-fields/edit", json={
            "doc_id": "demo-001", "if_version": view["version"],
            "set": {"doc_type": "Rechnung", "counterparty": "Beispiel GmbH"},
        })
        # counterparty is not in core: Knovas warns, the Platform refuses
        # keys its registry does not know before sending anything.
        assert edited.status_code == 400
        edited = client.post("/api/document-fields/edit", json={
            "doc_id": "demo-001", "if_version": view["version"], "set": {"doc_type": "Rechnung"},
        })
        assert edited.status_code == 200, edited.get_json()
        rows = {r["key"]: r for r in edited.get_json()["document"]["fields"]}
        assert rows["doc_type"]["text"] == "Rechnung"
        assert rows["doc_type"]["layer_label"] == "Manuell"
        patch = [r for r in state.requests if r["method"] == "PATCH"][-1]["json"]
        assert patch["fields_strict"] is False and patch["actor_ref"].startswith("platform-user:")
        stale = client.post("/api/document-fields/edit", json={
            "doc_id": "demo-001", "if_version": view["version"], "set": {"doc_type": "memo"},
        })
        assert stale.status_code == 409 and stale.get_json()["error_code"] == "version_conflict"

    def test_a_knovas_release_before_s2_says_update(self, platform_db, tmp_path, monkeypatch,
                                                     identity_repo):
        """The shipped server before S2 reads the GET doc-values pointer from
        the query string only. The Platform never moves it there: the panel
        says "Knovas-Update noetig" and nothing can be edited."""
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "values")
        state.pointer_in_body = False
        client = signed_in(app, identity_repo, role="admin")
        assert client.post("/api/search", json={"query": "lease"}).status_code == 200
        answer = client.post("/api/document-fields/read", json={"doc_id": "demo-001"})
        assert answer.status_code == 502
        assert answer.get_json()["error_code"] == "knovas_update_needed"
        reads = [r for r in state.requests
                 if r["method"] == "GET" and r["path"] == "/secured/graph/doc-values"]
        assert len(reads) == 1 and reads[0]["query"] == {}, "never retried with a URL pointer"
        assert not [r for r in state.requests if r["method"] == "PATCH"]

    def test_filters_search_and_listing(self, platform_db, tmp_path, monkeypatch,
                                        identity_repo):
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "filters")
        client = signed_in(app, identity_repo, role="member")
        body = client.post("/api/search", json={
            "query": "contract", "where": {"doc_type": "contract"}}).get_json()
        assert body["document_fields"]["filter_state"] == "applied"
        assert body["document_fields"]["resolved"][0]["text"] == "Vertrag"
        assert [r["doc_id"] for r in body["results"]] == ["demo-002"]
        assert body["results"][0]["fields_display"][0] == {
            "key": "doc_type", "label": "Dokumentart", "text": "Vertrag"}
        listed = client.post("/api/documents/find", json={
            "where": {"doc_type": "contract"},
            "sort": {"field": "document_date", "order": "desc"}}).get_json()
        assert [d["doc_id"] for d in listed["documents"]] == ["demo-002", "demo-001"]
        assert listed["complete"] is True and listed["total_count"] == 2
        assert listed["notice"]["incomplete"] is False
        # Listed, so the panel may read it.
        read = client.post("/api/document-fields/read", json={"doc_id": "demo-001"})
        assert read.status_code == 200

    def test_listing_only(self, platform_db, tmp_path, monkeypatch, identity_repo):
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "filters",
                                  calibrated=False)
        client = signed_in(app, identity_repo, role="member")
        plain = client.post("/api/search", json={"query": "contract"}).get_json()
        assert _queries(state)[-1]["return_fields"][0] == "title"
        assert plain["results"][0]["fields_display"]
        refused = client.post("/api/search", json={"query": "contract",
                                                   "where": {"doc_type": "contract"}})
        assert refused.status_code == 409
        assert refused.get_json()["error_code"] == "filters_need_calibration"
        assert client.get("/api/doc-fields").get_json()["capability"] == "listing_only"
        listed = client.post("/api/documents/find", json={"where": {"doc_type": "contract"}})
        assert listed.status_code == 200
        assert len(listed.get_json()["documents"]) == 2

    def test_auto_scope_is_named_through_the_person_s_graph(self, platform_db, tmp_path,
                                                            monkeypatch, identity_repo):
        """F3 end to end: the mock recognises "Muster AG"; the Platform names
        it from GET /secured/graph/nodes/<id> as the person -- that one node,
        not the node list, and nothing in the query string."""
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "filters",
                                  auto_scope="applied")
        state.add_document("rc-sync/Muster AG/Vertrag_1.pdf", title="Vertrag Muster AG",
                           snippet="Mietvertrag mit der Muster AG",
                           fields={"doc_type": "contract"})
        client = signed_in(app, identity_repo, role="member")
        body = client.post("/api/search", json={"query": "Muster AG"}).get_json()
        assert [r["doc_id"] for r in body["results"]] == ["rc-sync/Muster AG/Vertrag_1.pdf"]
        assert body["notices"] == [{"kind": "auto_scope_applied", "names": ["Muster AG"],
                                    "hidden_count": 0}]
        muster = next(n["id"] for n in state.nodes.values() if n["name"] == "Muster AG")
        reads = [r for r in state.requests if r["path"].startswith("/secured/graph/nodes")]
        assert [(r["method"], r["path"]) for r in reads] == [
            ("GET", f"/secured/graph/nodes/{muster}")]
        assert reads[0]["query"] == {}, "never with the typed text"

    def test_unreadable_values_are_said(self, platform_db, tmp_path, monkeypatch,
                                        identity_repo):
        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "filters")
        state.return_fields_unreadable = True
        client = signed_in(app, identity_repo, role="member")
        body = client.post("/api/search", json={"query": "lease"}).get_json()
        assert body["results"] and body["results"][0]["fields_display"] == []
        assert [n["kind"] for n in body["notices"]] == ["return_fields_unavailable"]

    def test_no_value_in_any_log_line_end_to_end(self, platform_db, tmp_path, monkeypatch,
                                                 identity_repo, caplog):
        """D6 through the real client: refused filters and edits log keys and
        codes (the client logs Knovas's error body, which never echoes a
        value), never the values or the question."""
        import logging

        app, state = _app_on_mock(platform_db, tmp_path, monkeypatch, "filters")
        client = signed_in(app, identity_repo, role="admin")
        caplog.set_level(logging.DEBUG)
        client.post("/api/search", json={"query": "Sentinel-Frage contract",
                                         "where": {"party": {"name": "Sentinel-Partei"}}})
        client.post("/api/search", json={"query": "Sentinel-Frage",
                                         "where": {"sentinel_key": "Sentinel-Wert"}})
        client.post("/api/documents/find", json={"where": {"doc_type": "Sentinel-Art"}})
        client.post("/api/documents/find", json={"where": {"doc_type": "contract"}})
        client.post("/api/document-fields/edit", json={
            "doc_id": "demo-001", "if_version": 1, "set": {"doc_type": "Sentinel-Typ"}})
        client.post("/api/document-fields/edit", json={
            "doc_id": "demo-001", "if_version": 1, "set": {"title": "Sentinel-Titel"}})
        text = "\n".join(r.getMessage() for r in caplog.records)
        for needle in ("Sentinel-Frage", "Sentinel-Partei", "Sentinel-Wert", "Sentinel-Art",
                       "Sentinel-Typ", "Sentinel-Titel", "demo-001"):
            assert needle not in text, needle
