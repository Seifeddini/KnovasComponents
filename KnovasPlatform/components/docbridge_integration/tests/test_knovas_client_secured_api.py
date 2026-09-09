"""Tests for secured Knovas API client behaviour: object deletion, multi-input
query bodies, certificate renewal, and structured tables in transmit payloads.

These previously lived in test_engagement.py, whose name did not match most of
its contents; they were rescued when the engagement feature was removed.
"""

import pytest

from knovas_client import (
    GraphError,
    _validate_and_normalize_tables,
    _secured_transmit_part_payload,
)
from test_knovas_client_hardening import FakeResponse, FakeSession, make_client, make_secured_client


def test_delete_information_object():
    client = make_secured_client()

    def responder(method, url, **kw):
        assert method == "DELETE"
        return FakeResponse(200, {"status": "success", "deleted_sentences": 3})

    client._session = FakeSession(responder)
    out = client.delete_information_object("doc-pointer")
    assert out["deleted_sentences"] == 3


def test_secured_query_multi_input_body():
    client = make_secured_client()
    body = client._secured_query_request_body(["Q3 revenue", "third quarter"])
    assert body["Input"] == ["Q3 revenue", "third quarter"]


def test_csr_renewal_installs_certificate(tmp_path, monkeypatch):
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    cert.write_text("ORIGINAL CERT\n", encoding="utf-8")
    key.write_text("ORIGINAL KEY\n", encoding="utf-8")
    ca.write_text("CA\n", encoding="utf-8")

    client = make_client(
        base_url="https://knovas.test",
        cert_path=str(cert),
        key_path=str(key),
        ca_cert_path=str(ca),
        cert_renew_method="csr",
    )
    original_session = FakeSession(lambda *a, **k: FakeResponse(200))
    client._session = original_session

    monkeypatch.setattr(
        client,
        "_generate_csr_key_pair",
        lambda: ("-----BEGIN CERTIFICATE REQUEST-----\nMOCK\n-----END CERTIFICATE REQUEST-----\n", "NEW KEY"),
    )
    monkeypatch.setattr(
        client,
        "sign_certificate",
        lambda csr, **kw: {"certificate": "NEW CERT"},
    )
    monkeypatch.setattr(client, "_validate_renewed_certificate", lambda c, k: True)

    assert client._attempt_certificate_renewal() is True
    assert cert.read_text(encoding="utf-8").strip() == "NEW CERT"
    assert key.read_text(encoding="utf-8").strip() == "NEW KEY"
    assert client._session is not original_session

    tables = _validate_and_normalize_tables(
        [
            {
                "client_table_hint": "revenue",
                "headers": ["Region", "Amt"],
                "rows": [["EMEA", "12M"]],
            }
        ]
    )
    assert tables[0]["client_table_hint"] == "revenue"


def test_transmit_payload_includes_tables():
    payload = _secured_transmit_part_payload(
        "key-1",
        0,
        {
            "snippet": "see table",
            "tables": [
                {
                    "client_table_hint": "t1",
                    "headers": ["A"],
                    "rows": [["1"]],
                }
            ],
        },
    )
    assert payload["tables"][0]["headers"] == ["A"]


class TestGraphError:
    """A non-404 graph failure must reach the caller with its error_code intact."""

    @staticmethod
    def _client_answering(status, body):
        client = make_secured_client()
        client._session = FakeSession(lambda method, url, **kw: FakeResponse(status, body))
        return client

    def test_404_still_returns_none(self):
        client = self._client_answering(404, {"message": "Node not found"})
        assert client.graph_node("missing") is None

    def test_a_422_raises_with_its_error_code(self):
        client = self._client_answering(
            422, {"error_code": "identifier_limit_exceeded", "message": "Max 16"}
        )
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 422
        assert caught.value.error_code == "identifier_limit_exceeded"
        assert caught.value.message == "Max 16"

    def test_a_503_carries_its_code_so_a_route_can_say_retry(self):
        client = self._client_answering(503, {"error_code": "relevance_calibration_missing"})
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 503
        assert caught.value.error_code == "relevance_calibration_missing"

    def test_a_body_without_an_error_code_still_raises(self):
        client = self._client_answering(500, {})
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 500 and caught.value.error_code is None


# ---------------------------------------------------------------------------
# Graph client: schema reads, type and node updates, server-side filters
# ---------------------------------------------------------------------------


class _GraphCall:
    """One recorded request, in the terms the graph client speaks."""

    def __init__(self, method, url, params, data):
        self.method = method
        self.url = url
        self.params = params or {}
        self.data = data or {}


class _GraphCapture:
    """Records every graph request and answers with a settable body."""

    def __init__(self):
        self.calls = []
        self.status = 200
        self.body = {"status": "success"}

    @property
    def last(self):
        return self.calls[-1]

    def __call__(self, method, url, **kwargs):
        self.calls.append(
            _GraphCall(method, url, kwargs.get("params"), kwargs.get("json"))
        )
        return FakeResponse(self.status, self.body)


@pytest.fixture
def capture():
    return _GraphCapture()


@pytest.fixture
def client(capture):
    client = make_secured_client()
    client._session = FakeSession(capture)
    return client


@pytest.fixture
def requests_mock(capture):
    """Set the body the graph calls of this test are answered with."""

    def respond(json=None, status=200):
        capture.body = {} if json is None else json
        capture.status = status

    return respond


class TestSchemaAndFilters:
    def test_graph_nodes_sends_the_server_side_filters(self, client, capture):
        client.graph_nodes(node_type_id="t1", q="Müller")
        assert capture.last.params == {"node_type_id": "t1", "q": "Müller"}

    def test_graph_nodes_omits_absent_filters(self, client, capture):
        client.graph_nodes()
        assert capture.last.params == {}

    def test_graph_schema_reads_the_attributes(self, client, requests_mock):
        requests_mock(json={"attributes": [{"id": "a1", "name": "Frist",
                                            "datatype": "date"}]})
        assert client.graph_schema("t1")[0]["name"] == "Frist"

    def test_graph_schema_can_include_deprecated(self, client, capture):
        client.graph_schema("t1", include_deprecated=True)
        assert capture.last.params == {"include_deprecated": "true"}

    def test_create_attribute_sends_the_target_type(self, client, capture):
        client.graph_create_schema_attribute(
            "t1", "Zustaendig", datatype="entity_ref", target_node_type_id="t2")
        assert capture.last.data["target_node_type_id"] == "t2"

    def test_create_attribute_omits_a_null_target(self, client, capture):
        client.graph_create_schema_attribute("t1", "Notiz", datatype="text")
        assert "target_node_type_id" not in capture.last.data

    def test_deprecate_is_the_name_and_delete_is_gone(self, client):
        assert hasattr(client, "graph_deprecate_schema_attribute")
        assert not hasattr(client, "graph_delete_schema_attribute")

    def test_update_node_sends_only_the_given_fields(self, client, capture):
        client.graph_update_node("n1", name="Neu")
        assert capture.last.data == {"name": "Neu"}

    # -- what the wrapper is actually for: the verb and the endpoint --------
    #
    # Every assertion above reads the body or the params, which a method
    # POSTing to the wrong path would still satisfy. These read the two things
    # only the wrapper decides.

    def test_schema_read_is_a_get_on_the_type_s_schema(self, client, capture):
        client.graph_schema("t1")
        assert capture.last.method == "GET"
        assert capture.last.url.endswith("/secured/graph/node-types/t1/schema")

    def test_creating_an_attribute_posts_to_the_same_path(self, client, capture):
        client.graph_create_schema_attribute("t1", "Notiz", datatype="text")
        assert capture.last.method == "POST"
        assert capture.last.url.endswith("/secured/graph/node-types/t1/schema")

    def test_updating_an_attribute_patches_the_attribute(self, client, capture):
        client.graph_update_schema_attribute("t1", "a1", name="Frist", required=True)
        assert capture.last.method == "PATCH"
        assert capture.last.url.endswith("/secured/graph/node-types/t1/schema/a1")
        assert capture.last.data == {"name": "Frist", "required": True}

    def test_deprecating_an_attribute_deletes_the_attribute(self, client, capture):
        """The server only marks it deprecated; the verb it wants is still
        DELETE, and nothing but this test says so."""
        client.graph_deprecate_schema_attribute("t1", "a1")
        assert capture.last.method == "DELETE"
        assert capture.last.url.endswith("/secured/graph/node-types/t1/schema/a1")

    def test_updating_a_node_type_patches_the_type_not_its_schema(self, client, capture):
        client.graph_update_node_type("t1", name="Mandat")
        assert capture.last.method == "PATCH"
        assert capture.last.url.endswith("/secured/graph/node-types/t1")
        assert capture.last.data == {"name": "Mandat"}

    def test_updating_a_node_patches_the_node(self, client, capture):
        client.graph_update_node("n1", name="Neu")
        assert capture.last.method == "PATCH"
        assert capture.last.url.endswith("/secured/graph/nodes/n1")

    def test_an_id_with_a_slash_cannot_escape_its_path_segment(self, client, capture):
        """quote(safe="") is load-bearing: an unquoted id would address a
        different endpoint entirely."""
        client.graph_schema("t1/../nodes")
        assert capture.last.url.endswith(
            "/secured/graph/node-types/t1%2F..%2Fnodes/schema")

    # -- 404 is not "empty" ------------------------------------------------

    def test_an_unknown_type_reads_as_none_not_as_a_type_without_fields(
            self, client, requests_mock):
        """The whole UI is generated from this answer. [] would draw an empty
        form and tell the user their type has no fields."""
        requests_mock(json={"message": "Node type not found"}, status=404)
        assert client.graph_schema("weg") is None

    def test_a_known_type_without_fields_still_reads_as_an_empty_list(
            self, client, requests_mock):
        requests_mock(json={"attributes": []})
        assert client.graph_schema("t1") == []

    # -- a write with nothing to write is not a read -----------------------

    def test_updating_a_node_with_no_fields_is_refused(self, client, capture):
        """It used to issue a GET and hand back the node envelope, which every
        caller reads as "gespeichert"."""
        with pytest.raises(ValueError):
            client.graph_update_node("n1")
        assert capture.calls == []


# ---------------------------------------------------------------------------
# Graph client: facts CRUD and neighbours with induced edges
# ---------------------------------------------------------------------------


class TestFactsAndNeighbours:
    def test_create_fact_requires_an_attribute_or_a_label(self, client):
        with pytest.raises(ValueError):
            client.graph_create_fact("n1", "Wert")

    def test_create_fact_with_an_attribute_id(self, client, capture):
        client.graph_create_fact("n1", {"value": "2026-03-04", "precision": "day"},
                                 attribute_id="a1")
        assert capture.last.data == {
            "attribute_id": "a1",
            "value": {"value": "2026-03-04", "precision": "day"}}

    def test_create_fact_with_a_free_form_label(self, client, capture):
        client.graph_create_fact("n1", "Wert", label="Notiz")
        assert capture.last.data == {"label": "Notiz", "value": "Wert"}

    def test_facts_reads_the_list(self, client, requests_mock):
        requests_mock(json={"facts": [{"id": "f1", "value": "Wert"}]})
        assert client.graph_facts("n1")[0]["id"] == "f1"

    def test_neighbours_returns_a_mapping_with_both_keys(self, client, requests_mock):
        requests_mock(json={"neighbors": [{"id": "n2"}], "edges": [{"id": "e1"}]})
        result = client.graph_neighbors("n1", depth=1, include_edges=True)
        assert result["neighbors"][0]["id"] == "n2"
        assert result["edges"][0]["id"] == "e1"

    def test_neighbours_sends_include_edges_only_when_asked(self, client, capture):
        client.graph_neighbors("n1", depth=1)
        assert capture.last.params == {"depth": 1}
        client.graph_neighbors("n1", depth=1, include_edges=True)
        assert capture.last.params == {"depth": 1, "include_edges": "true"}

    def test_neighbours_edges_default_to_empty_not_missing(self, client, requests_mock):
        requests_mock(json={"neighbors": []})
        assert client.graph_neighbors("n1")["edges"] == []

    def test_neighbours_never_reports_the_nodes_as_edges(self, client, requests_mock):
        """A server without include_edges (backend Task A2) answers with the
        neighbours alone. Reporting that list under "edges" would invent
        relations the graph does not have, so the key must stay empty."""
        requests_mock(json={"neighbors": [{"id": "n2"}, {"id": "n3"}]})
        assert client.graph_neighbors("n1", include_edges=True)["edges"] == []

    def test_neighbours_depth_is_clamped_to_the_api_cap(self, client, capture):
        client.graph_neighbors("n1", depth=9)
        assert capture.last.params["depth"] == 3

    def test_neighbours_never_reports_the_edges_as_the_nodes(self, client, requests_mock):
        """The mirror of the test above. _graph_payload_list falls back to "the
        first list in the object", so an edges-only answer used to come back as
        two invented neighbours."""
        requests_mock(json={"edges": [{"id": "e1"}, {"id": "e2"}]})
        result = client.graph_neighbors("n1", include_edges=True)
        assert result["neighbors"] == []
        assert [e["id"] for e in result["edges"]] == ["e1", "e2"]

    # -- verb and endpoint, for the fact methods too ------------------------

    def test_facts_is_a_get_on_the_node_s_facts(self, client, capture):
        client.graph_facts("n1")
        assert capture.last.method == "GET"
        assert capture.last.url.endswith("/secured/graph/nodes/n1/facts")

    def test_creating_a_fact_posts_to_the_same_path(self, client, capture):
        client.graph_create_fact("n1", "Wert", label="Notiz")
        assert capture.last.method == "POST"
        assert capture.last.url.endswith("/secured/graph/nodes/n1/facts")

    def test_updating_a_fact_patches_the_fact_not_the_node(self, client, capture):
        client.graph_update_fact("f1", value="Neu")
        assert capture.last.method == "PATCH"
        assert capture.last.url.endswith("/secured/graph/facts/f1")
        assert capture.last.data == {"value": "Neu"}

    def test_deleting_a_fact_deletes_the_fact(self, client, capture):
        client.graph_delete_fact("f1")
        assert capture.last.method == "DELETE"
        assert capture.last.url.endswith("/secured/graph/facts/f1")

    def test_neighbours_is_a_get_on_the_node_s_neighbours(self, client, capture):
        client.graph_neighbors("n1")
        assert capture.last.method == "GET"
        assert capture.last.url.endswith("/secured/graph/nodes/n1/neighbors")

    # -- 404 and 204 are not "empty" ---------------------------------------

    def test_an_unknown_node_reads_as_none_not_as_a_node_without_facts(
            self, client, requests_mock):
        requests_mock(json={"message": "Node not found"}, status=404)
        assert client.graph_facts("weg") is None

    def test_a_known_node_without_facts_still_reads_as_an_empty_list(
            self, client, requests_mock):
        requests_mock(json={"facts": []})
        assert client.graph_facts("n1") == []

    def test_a_204_delete_is_a_success_not_a_failed_write(self, client, requests_mock):
        """204 No Content has no body to parse. It used to come back as {},
        which every caller reads as "the write did not happen"."""
        requests_mock(json=None, status=204)
        assert client.graph_delete_fact("f1")
