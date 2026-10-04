"""The mock Knovas API plays every document-fields server state (spec 7, WP-C).

The RemoteController and the Platform test their doc-fields code against this
mock, so the mock must answer as the server does in each state: `off` (an
old server), `values`, `filters`, and `filters` without calibration. `off`
must answer /secured/query and init exactly as the mock did before document
fields existed.

Goldens (`goldens/*.json`) are request/response pairs the mock must satisfy:

    {"name", "description",
     "server_state": "off" | "values" | "filters" | "filters_uncalibrated",
     "setup": [request, ...],            # sent first, answers must be 2xx
     "request": {"method", "path", "body"},
     "response": {"status", "body"},     # contained in the actual body
     "volatile": ["a.b", "list.*.key"],  # present and non-null, value free
     "absent": ["key"]}                  # must not be in the actual body

"Contained" means every key of the golden body is in the actual body with an
equal value (recursively; lists element by element, same length). The
goldens are the server's own, copied from the KnowledgeBase Developer Kit
(docs/Knovas_Developer_Kit/api/examples/doc_fields/), where the server's
test_doc_fields_wire_goldens pins them against the real routes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MOCK_DIR = Path(__file__).resolve().parents[1]
TESTING = MOCK_DIR / "testing.py"
GOLDENS = sorted((MOCK_DIR / "goldens").glob("*.json"))

if not TESTING.is_file():  # pragma: no cover - a checkout without the mock
    pytest.skip("mock_knovas_api/testing.py is not in this checkout", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("knovas_mock_testing", TESTING)
testing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(testing)

HTTP404 = ("The requested URL was not found on the server. If you entered the URL "
           "manually please check your spelling and try again.")
POINTER = "rc-sync/Muster AG/GJ 2024/Rechnung_17.pdf"
OWNED_ROUTES = [
    ("GET", "/secured/graph/doc-values"), ("PATCH", "/secured/graph/doc-values"),
    ("POST", "/secured/graph/doc-values"), ("DELETE", "/secured/graph/doc-values"),
    ("POST", "/secured/graph/doc-values/find"), ("GET", "/secured/graph/doc-values/find"),
    ("GET", "/secured/graph/doc-fields"), ("POST", "/secured/graph/doc-fields"),
    ("PATCH", "/secured/graph/doc-fields/00000000-0000-0000-0000-000000000001"),
    ("POST", "/secured/graph/doc-fields/00000000-0000-0000-0000-000000000001/deprecate"),
    ("GET", "/secured/graph/doc-fields/packs"),
    ("POST", "/secured/graph/doc-fields/packs/legal_ch/install"),
    ("GET", "/secured/graph/doc-fields/settings"), ("PUT", "/secured/graph/doc-fields/settings"),
    ("GET", "/secured/graph/doc-field-rules"), ("PUT", "/secured/graph/doc-field-rules"),
    ("DELETE", "/secured/graph/doc-field-rules"),
]


# -- helpers -----------------------------------------------------------------

class Mock:
    """One mock app, its test client and its state."""

    def __init__(self, **kw):
        self.app = testing.load_mock_app(**kw)
        self.client = self.app.test_client()
        self.state = testing.mock_state(self.app)

    def call(self, method, path, body=None, **kw):
        response = self.client.open(path, method=method, json=body, **kw)
        return response.status_code, response.get_json()

    def init(self, pointer=POINTER, fields=None, **extra):
        rel = pointer.split("/", 1)[1]
        body = {"identifier": pointer, "part_count": 1, "title": rel.rsplit("/", 1)[-1], "path": rel}
        if fields is not None:
            body["fields"] = fields
        body.update(extra)
        return self.call("POST", "/secured/init_document_transmission", body)

    def query(self, **body):
        body.setdefault("Input", "")
        return self.call("POST", "/secured/query", body)

    def find(self, **body):
        return self.call("POST", "/secured/graph/doc-values/find", body)

    def values(self, pointer=POINTER):
        return self.call("GET", "/secured/graph/doc-values", {"pointer": pointer})

    def patch(self, **body):
        body.setdefault("pointer", POINTER)
        return self.call("PATCH", "/secured/graph/doc-values", body)


def _contained(expected, actual, path="$"):
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: {actual!r} is not an object"
        for key, value in expected.items():
            assert key in actual, f"{path}.{key} missing"
            _contained(value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{path}: {actual!r}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            _contained(e, a, f"{path}[{i}]")
    else:
        assert expected == actual and type(expected) is type(actual), f"{path}: {actual!r} != {expected!r}"


def _resolve(body, dotted):
    """Every value at a dotted path; `*` walks every element of a list."""
    nodes = [body]
    for part in dotted.split("."):
        nxt = []
        for node in nodes:
            if part == "*":
                assert isinstance(node, list), dotted
                nxt.extend(node)
            else:
                assert isinstance(node, dict) and part in node, f"{dotted} missing"
                nxt.append(node[part])
        nodes = nxt
    return nodes


def _mock_for(server_state):
    if server_state == "filters_uncalibrated":
        return Mock(doc_fields="filters", calibrated=False)
    return Mock(doc_fields=server_state)


# -- construction and isolation ----------------------------------------------

class TestConstruction:
    def test_default_mode_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("MOCK_DOC_FIELDS", "filters")
        assert Mock().state.mode == "filters"
        monkeypatch.delenv("MOCK_DOC_FIELDS")
        assert Mock().state.mode == "off"

    def test_module_keeps_an_app(self):
        module = testing.mock_module()
        assert module.app.extensions["knovas_mock"].mode in ("off", "values", "filters")

    @pytest.mark.parametrize("kw", [{"doc_fields": "on"}, {"refuse_init_fields": "unknown_field"},
                                    {"refuse_init_fields": "abc:unknown_field"}])
    def test_bad_arguments_fail_loudly(self, kw):
        with pytest.raises(ValueError):
            testing.load_mock_app(**kw)

    def test_two_apps_share_nothing(self):
        one, two = Mock(doc_fields="filters"), Mock(doc_fields="filters")
        assert one.init(fields={"doc_type": "invoice"})[0] == 201
        one.state.settings["unknown_keys"] = "reject"
        assert one.call("POST", "/secured/graph/doc-fields", {"key": "mandant", "datatype": "text"})[0] == 201
        assert two.values()[0] == 404
        assert two.find(where={"doc_type": "invoice"})[1]["documents"] == []
        assert two.state.settings["unknown_keys"] == "ignore"
        assert two.state.field_by_key("mandant") is None
        assert two.state.documents is not one.state.documents
        assert POINTER not in two.state.stored_pointers
        assert all(d["doc_id"] != POINTER for d in testing.mock_module().DOCUMENTS)

    def test_engagement_and_delete_state_is_per_app(self):
        one, two = Mock(), Mock()
        one.init()
        assert one.call("DELETE", "/secured/delete_information_object", {"pointer": POINTER})[0] == 200
        assert two.call("DELETE", "/secured/delete_information_object", {"pointer": POINTER})[0] == 404
        one.call("POST", "/secured/analytics/engagement", {"query_session_id": "s", "events": [{}, {}]})
        assert (one.state.engagement_count, two.state.engagement_count) == (2, 0)

    def test_requests_are_recorded(self):
        mock = Mock()
        mock.init(fields={"doc_type": "invoice"})
        last = mock.state.requests[-1]
        assert last["method"] == "POST" and last["path"] == "/secured/init_document_transmission"
        assert last["json"]["fields"] == {"doc_type": "invoice"}
        assert json.loads(last["body"]) == last["json"]


# -- errors and limits in every mode -----------------------------------------

@pytest.mark.parametrize("mode", ["off", "values", "filters"])
class TestEveryMode:
    def test_unknown_route_is_json_404(self, mode):
        status, body = Mock(doc_fields=mode).call("GET", "/secured/no-such-route")
        assert status == 404
        assert body == {"status": "error", "error": HTTP404, "error_code": "HTTP_404"}

    def test_wrong_method_is_json(self, mode):
        status, body = Mock(doc_fields=mode).call("GET", "/secured/query")
        assert status == 405 and body["error_code"] == "HTTP_405"

    @pytest.mark.parametrize("limit", [51, 0, -1, "10", True, 2.5])
    def test_query_limit_outside_1_to_50_is_422(self, mode, limit):
        status, body = Mock(doc_fields=mode).query(limit=limit)
        assert status == 422
        assert body == {"status": "error", "error": "limit must be an integer between 1 and 50",
                        "type": "validation_error", "field": "limit"}

    def test_query_limit_50_is_fine(self, mode):
        assert Mock(doc_fields=mode).query(limit=50)[0] == 200

    def test_node_types_nodes_and_access_groups(self, mode):
        mock = Mock(doc_fields=mode)
        status, body = mock.call("GET", "/secured/graph/node-types")
        assert status == 200
        types = {t["name"]: t["id"] for t in body["node_types"]}
        assert set(types) == {"Mandant", "Gericht"}
        status, body = mock.call("GET", f"/secured/graph/nodes?node_type_id={types['Mandant']}")
        assert status == 200
        assert [n["name"] for n in body["nodes"]] == ["Beispiel GmbH", "Muster AG"]
        assert {n["node_type_id"] for n in body["nodes"]} == {types["Mandant"]}
        assert len(mock.call("GET", "/secured/graph/nodes")[1]["nodes"]) == 3
        node = testing.mock_module().stable_id("node", "Muster AG")
        status, body = mock.call("GET", f"/secured/graph/nodes/{node}")
        assert status == 200
        assert body["node"] == {"id": node, "name": "Muster AG", "description": None,
                                "node_type_id": types["Mandant"]}
        assert (body["assignments"], body["sections"], body["facts"]) == ([], [], [])
        status, body = mock.call("GET", "/secured/graph/nodes/00000000-0000-4000-8000-000000000000")
        assert (status, body["error_code"]) == (404, "NOT_FOUND")
        status, body = mock.call("GET", "/secured/access_groups")
        assert status == 200
        tree = body["groups"][0]
        assert tree["is_admin"] is False
        assert [c["is_admin"] for c in tree["children"]] == [True, False]


# -- off ----------------------------------------------------------------------

class TestOff:
    @pytest.mark.parametrize("method,path", OWNED_ROUTES)
    def test_every_doc_fields_route_is_the_unknown_route_404(self, method, path):
        status, body = Mock(doc_fields="off").call(method, path, {"pointer": POINTER, "where": {}})
        assert status == 404
        assert body == {"status": "error", "error": HTTP404, "error_code": "HTTP_404"}

    def test_query_answers_as_before(self):
        status, body = Mock(doc_fields="off").query(
            Input="lease", where={"doc_type": "contract"}, return_fields=True)
        assert status == 200
        assert set(body) == {"status", "message", "query_session_id", "pointers", "result_count",
                             "results", "meta", "mock"}
        assert body["pointers"] == ["demo-001"] and body["result_count"] == 1
        assert body["meta"] == {"embed_latency_ms": 1, "stage1_latency_ms": 1, "stage2_latency_ms": 1}
        result = body["results"][0]
        assert set(result) == {"pointer", "document_uuid", "final_score", "cosine_similarity",
                               "cosine_distance", "ingested_summary", "page_number",
                               "sentence_number", "top_chunks"}
        assert result["ingested_summary"] == {
            "present": True,
            "text": "This lease agreement starts on 2026-01-01 and includes renewal options."}

    def test_query_without_input_lists_the_demo_documents(self):
        body = Mock(doc_fields="off").query()[1]
        assert body["pointers"] == ["demo-001", "demo-002", "demo-003"]

    def test_init_ignores_fields_without_an_echo(self):
        mock = Mock(doc_fields="off")
        status, body = mock.init(fields={"doc_type": "invoice"}, fields_strict=True)
        assert status == 201
        assert set(body) == {"status", "message", "transmission_key_id", "mock"}
        assert body["message"] == "Transmission initialized"
        assert mock.state.anchors.get(POINTER) is None
        assert all(d["doc_id"] != POINTER for d in mock.state.documents)

    def test_forced_refusal_needs_a_server_that_reads_fields(self):
        mock = Mock(doc_fields="off", refuse_init_fields="503:doc_fields_unavailable")
        assert mock.init(fields={"doc_type": "invoice"})[0] == 201


# -- values --------------------------------------------------------------------

class TestValuesInit:
    def test_echo_shape(self):
        status, body = Mock(doc_fields="values").init(fields={"doc_type": "Rechnung", "keywords": ["a", "b"]})
        assert status == 201
        # mapped_keys lists only keys that differ from the field key.
        assert body["fields"] == {"staged": 2, "mapped_keys": {}, "unknown_keys": [], "warnings": []}
        assert body["transmission_key_id"]

    def test_unknown_key_with_suggestion_and_label_keys(self):
        status, body = Mock(doc_fields="values").init(
            fields={"doc_typ": "invoice", "Dokumentdatum": "2024-03-15"})
        assert status == 201
        echo = body["fields"]
        assert echo["unknown_keys"] == ["doc_typ"]
        assert echo["suggest"] == {"doc_typ": ["doc_type"]}
        assert echo["mapped_keys"] == {"Dokumentdatum": "document_date"}

    def test_no_suggest_key_when_nothing_is_close(self):
        body = Mock(doc_fields="values").init(fields={"zzzz": "x"})[1]
        assert body["fields"]["unknown_keys"] == ["zzzz"] and "suggest" not in body["fields"]

    def test_bad_values_are_dropped_with_a_warning(self):
        body = Mock(doc_fields="values").init(fields={"doc_type": "Kreditorenrechnung", "amount": 1234,
                                                     "pointer": "x"})[1]
        assert body["fields"]["staged"] == 0
        codes = {(w["key"], w["path"], w["code"]) for w in body["fields"]["warnings"]}
        assert codes == {("doc_type", "fields.doc_type", "invalid_value"),
                         ("amount", "fields.amount", "type_mismatch"),
                         ("pointer", "fields.pointer", "invalid_value")}

    def test_strict_turns_warnings_into_422(self):
        mock = Mock(doc_fields="values")
        status, body = mock.init(fields={"party": ["Muster AG"]}, fields_strict=True)
        assert status == 422
        assert body["error_code"] == "invalid_fields"
        assert body["errors"] == [{"path": "fields.party[0]", "code": "unresolved_entity"}]
        assert POINTER not in mock.state.stored_pointers

    @pytest.mark.parametrize("extra,code,path", [
        ({"fields": ["doc_type"]}, "invalid_fields", "fields"),
        ({"fields": {"doc_type": "invoice"}, "fields_strict": "yes"}, "invalid_fields", "fields_strict"),
        ({"fields": {"doc_type": "invoice"}, "fields_mode": "append"}, "invalid_fields", "fields_mode"),
        ({"fields": {f"k{i}": "x" for i in range(65)}}, "fields_too_large", "fields"),
        ({"fields": {key: ["x" * 250] * 32 for key in ("keywords", "author", "reference")}},
         "fields_too_large", "fields"),                 # over 16384 bytes
        ({"fields": {"keywords": ["x"] * 33}}, "fields_too_large", "fields.keywords"),
        ({"fields": {" ": "x"}}, "invalid_fields", "fields. "),
    ])
    def test_shape_and_bounds(self, extra, code, path):
        status, body = Mock(doc_fields="values").call(
            "POST", "/secured/init_document_transmission", {"identifier": POINTER, **extra})
        assert status == 400
        assert body == {"status": "error", "error_code": code,
                        "error": testing.mock_module().FIXED_MESSAGES[code], "path": path}

    def test_reject_and_register_settings(self):
        mock = Mock(doc_fields="values")
        mock.state.settings["unknown_keys"] = "reject"
        status, body = mock.init(fields={"mandat": "x"})
        assert (status, body["error_code"], body["path"]) == (422, "unknown_field", "fields.mandat")
        mock.state.settings["unknown_keys"] = "register"
        body = mock.init(fields={"aktenzeichen_intern": "A-1", "konto_12345": "x"})[1]
        assert body["fields"]["mapped_keys"] == {}
        assert body["fields"]["staged"] == 1
        assert body["fields"]["unknown_keys"] == ["konto_12345"]
        assert mock.state.field_by_key("aktenzeichen_intern")["status"] == "provisional"

    def test_replace_merge_and_no_fields(self):
        mock = Mock(doc_fields="values")
        mock.init(fields={"doc_type": "invoice", "period": "GJ 2024"})
        mock.init(fields={"status": "draft", "period": None}, fields_mode="merge")
        assert mock.values()[1]["fields"] == {"doc_type": "invoice", "status": "draft"}
        mock.init()                                      # no fields: layer kept
        assert mock.values()[1]["fields"] == {"doc_type": "invoice", "status": "draft"}
        mock.init(fields={"doc_type": "Kreditorenrechnung"}, fields_mode="merge")
        assert mock.values()[1]["fields"]["doc_type"] == "invoice"   # all invalid: not cleared
        mock.init(fields={})                             # replace with nothing clears
        assert mock.values()[1]["fields"] == {}

    @pytest.mark.parametrize("spec,status,code,path", [
        ("503:doc_fields_unavailable", 503, "doc_fields_unavailable", None),
        ("503:doc_fields_ingest_unavailable", 503, "doc_fields_ingest_unavailable", None),
        ("422:unknown_field", 422, "unknown_field", "fields"),
        ("400:invalid_fields", 400, "invalid_fields", "fields"),
        ("401:assertion_rejected", 401, "assertion_rejected", None),
    ])
    def test_forced_refusal_only_with_fields(self, spec, status, code, path):
        mock = Mock(doc_fields="values", refuse_init_fields=spec)
        got_status, body = mock.init(fields={"doc_type": "invoice"})
        assert (got_status, body["error_code"], body.get("path")) == (status, code, path)
        assert POINTER not in mock.state.stored_pointers
        assert mock.init()[0] == 201                     # the retry without fields

    def test_file_property_values(self):
        """What the Knovas Connector's file-property opt-ins send (L1): Word's
        content status as written is matched against the status choices'
        labels; one Knovas does not know is dropped with an invalid_value
        warning naming the key, never the value; keywords are text values."""
        mock = Mock(doc_fields="values")
        status, body = mock.init(fields={"status": "Final", "keywords": ["Vertrag", "Miete"]})
        assert status == 201
        assert body["fields"]["staged"] == 2 and body["fields"]["warnings"] == []
        assert mock.values()[1]["fields"] == {"keywords": ["Vertrag", "Miete"], "status": "final"}
        other = "rc-sync/Vertraege/review.docx"
        status, body = mock.init(pointer=other, fields={"status": "In Review"})
        assert status == 201 and body["fields"]["staged"] == 0
        assert body["fields"]["warnings"] == [
            {"key": "status", "path": "fields.status", "code": "invalid_value"}]
        assert "status" not in mock.values(other)[1]["fields"]


class TestValuesRead:
    def test_get_with_pointer_in_body(self):
        mock = Mock(doc_fields="values")
        mock.init(fields={"doc_type": "invoice", "party": "Muster AG"})
        status, body = mock.values()
        assert status == 200
        assert body["fields"] == {"doc_type": "invoice", "party": [{"name": "Muster AG"}]}
        # A first stored upload leaves the (opaque) version at 4, as on the server.
        assert body["title_source"] == "upload" and body["version"] == 4
        assert body["layers"]["doc_type"][0]["layer"] == "upload"

    def test_pointer_only_in_query_string_is_refused(self):
        mock = Mock(doc_fields="values")
        mock.init()
        status, body = mock.call("GET", "/secured/graph/doc-values?pointer=" + POINTER.replace(" ", "%20"))
        assert status == 400
        assert (body["error_code"], body["path"]) == ("invalid_value", "pointer")

    def test_unknown_pointer_is_not_found(self):
        status, body = Mock(doc_fields="values").values("rc-sync/unbekannt.pdf")
        assert status == 404
        assert body == {"status": "error", "error": "Document not found", "error_code": "NOT_FOUND"}

    def test_pointer_without_anchor_gets_the_synthetic_view(self):
        mock = Mock(doc_fields="values")
        mock.state.stored_pointers.add(POINTER)
        body = mock.values()[1]
        assert (body["version"], body["title"], body["title_source"]) == (0, "Rechnung_17.pdf", "path")
        assert body["fields"] == {} and body["layers"] == {}

    def test_quarantined_view_and_patch(self):
        mock = Mock(doc_fields="values")
        mock.init()
        mock.state.quarantined.add(POINTER)
        body = mock.values()[1]
        assert {k: body[k] for k in ("pointer", "acl_mode", "version")} == {
            "pointer": POINTER, "acl_mode": "quarantined", "version": 4}
        assert "fields" not in body
        assert mock.patch(if_version=4, set={"title": "x"})[1]["error_code"] == "anchor_quarantined"

    def test_linked_entity_shows_node_id(self):
        mock = Mock(doc_fields="values")
        mock.call("POST", "/secured/graph/doc-fields/packs/legal_ch/install")
        body = mock.init(fields={"client": "muster ag", "counterparty": "Beispiel GmbH"})[1]
        assert [w["code"] for w in body["fields"]["warnings"]] == ["unresolved_entity"]
        fields = mock.values()[1]["fields"]
        assert fields["client"] == {"node_id": testing.mock_module().stable_id("node", "Muster AG"),
                                    "name": "Muster AG"}
        assert fields["counterparty"] == {"name": "Beispiel GmbH"}


class TestValuesPatch:
    def _mock(self):
        mock = Mock(doc_fields="values")
        mock.init(fields={"doc_type": "invoice", "period": "GJ 2024", "keywords": ["a", "b"]})
        return mock

    def test_if_version_rules(self):
        mock = self._mock()
        assert mock.patch(set={"title": "x"})[1]["error_code"] == "if_version_required"
        for bad in (-1, True, "1"):
            body = mock.patch(if_version=bad, set={"title": "x"})[1]
            assert (body["error_code"], body["path"]) == ("invalid_value", "if_version")
        status, body = mock.patch(if_version=0, set={"title": "x"})
        assert status == 409
        assert (body["error_code"], body["current_version"]) == ("version_conflict", 4)

    def test_title_only_answers_every_effective_field(self):
        status, body = self._mock().patch(if_version=4, set={"title": "Rechnung Muster AG"})
        assert status == 200
        assert body["fields"] == {"doc_type": "invoice", "keywords": ["a", "b"],
                                  "period": {"lo": "2024-01-01", "hi": "2024-12-31", "label": "2024"}}
        assert body["title_source"] == "manual" and body["version"] == 5

    def test_manual_beats_upload_and_null_reverts(self):
        mock = self._mock()
        body = mock.patch(if_version=4, set={"period": "GJ 2023"})[1]
        assert body["fields"]["period"] == {"lo": "2023-01-01", "hi": "2023-12-31", "label": "2023"}
        layers = mock.values()[1]["layers"]["period"]
        assert [(row["layer"], row["effective"]) for row in layers] == [("manual", True), ("upload", False)]
        mock.patch(if_version=5, set={"period": None})
        assert mock.values()[1]["fields"]["period"] == {"lo": "2024-01-01", "hi": "2024-12-31", "label": "2024"}

    def test_unset_add_and_remove(self):
        mock = self._mock()
        mock.patch(if_version=4, unset=["period"], add={"keywords": ["c"]}, remove={"keywords": ["a"]})
        fields = mock.values()[1]["fields"]
        assert "period" not in fields and fields["keywords"] == ["b", "c"]

    def test_non_strict_keeps_names_with_warnings(self):
        mock = self._mock()
        status, body = mock.patch(if_version=4, set={"party": "Beispiel GmbH", "mandat": "x"})
        assert status == 200
        assert {(w["key"], w["code"]) for w in body["warnings"]} == {
            ("party", "unresolved_entity"), ("mandat", "unknown_field")}
        assert body["fields"]["party"] == [{"name": "Beispiel GmbH"}]

    def test_strict_refuses_with_the_specific_code(self):
        status, body = self._mock().patch(if_version=4, set={"party": "Beispiel GmbH"}, fields_strict=True)
        assert (status, body["error_code"], body["path"]) == (422, "unresolved_entity", "set.party")

    @pytest.mark.parametrize("body,code,path", [
        ({"set": {"path": "x"}}, "invalid_value", "set.path"),
        ({"set": {"title": ""}}, "invalid_value", "set.title"),
        ({"set": {"doc_type": "Kreditorenrechnung"}}, "invalid_value", "set.doc_type"),
        ({"set": {"keywords": ["ok", ""]}}, "invalid_value", "set.keywords[1]"),
        ({"add": {"doc_type": ["invoice"]}}, "invalid_value", "add.doc_type"),
        ({"fields_strict": 1}, "invalid_value", "fields_strict"),
    ])
    def test_invalid_edits(self, body, code, path):
        status, answer = self._mock().patch(if_version=4, **body)
        assert (status, answer["error_code"], answer["path"]) == (400, code, path)

    def test_forbidden_change(self):
        mock = self._mock()
        mock.state.change_forbidden.add(POINTER)
        status, body = mock.patch(if_version=1, set={"title": "x"})
        assert (status, body["error_code"]) == (403, "change_not_authorized")


class TestValuesRegistry:
    def test_core_is_seeded(self):
        status, body = Mock(doc_fields="values").call("GET", "/secured/graph/doc-fields")
        assert status == 200
        keys = [f["key"] for f in body["fields"]]
        assert keys == sorted(["doc_type", "document_date", "period", "language", "author", "party",
                               "reference", "amount", "status", "keywords"])
        doc_type = body["fields"][keys.index("doc_type")]
        assert set(doc_type) == set(testing.mock_module().PUBLIC_FIELD_KEYS) | {"warnings"}
        assert doc_type["origin"] == "system" and doc_type["pack_key"] == "core"
        assert {"code": "invoice", "labels": {"de": "Rechnung", "en": "Invoice"}, "aliases": []} \
            in doc_type["enum_values"]

    def test_create_update_deprecate(self):
        mock = Mock(doc_fields="values")
        type_id = testing.mock_module().stable_id("node_type", "Mandant")
        status, body = mock.call("POST", "/secured/graph/doc-fields", {
            "key": "mandant", "datatype": "entity_ref", "target_node_type_id": type_id,
            "labels": {"de": "Mandant"}, "display": True, "facet": True, "access_groups": ["g"]})
        assert status == 201 and body["field"]["key"] == "mandant"
        field_id = body["field"]["id"]
        assert mock.call("POST", "/secured/graph/doc-fields", {"key": "mandant"})[1]["error_code"] \
            == "field_key_exists"
        status, body = mock.call("PATCH", f"/secured/graph/doc-fields/{field_id}",
                                 {"labels": {"de": "Klient"}})
        assert status == 200 and body["field"]["labels"] == {"de": "Klient"}
        status, body = mock.call("PATCH", f"/secured/graph/doc-fields/{field_id}", {"datatype": "text"})
        assert (status, body["error_code"]) == (409, "field_type_locked")
        status, body = mock.call("POST", f"/secured/graph/doc-fields/{field_id}/deprecate")
        assert status == 200 and body["field"]["status"] == "deprecated"

    @pytest.mark.parametrize("body,status,code,path", [
        ({"key": "Mandant"}, 400, "invalid_field_definition", "key"),
        ({"key": "konto_12345"}, 422, "key_looks_personal", "key"),
        ({"key": "title"}, 409, "field_key_exists", "key"),
        ({"key": "mandant", "acting_as": ["g"]}, 400, "invalid_field_definition", "acting_as"),
        ({"key": "mandant", "datatype": "entity_ref",
          "target_node_type_id": "00000000-0000-0000-0000-000000000009"},
         400, "invalid_field_definition", "target_node_type_id"),
        ({"key": "art", "datatype": "enum"}, 400, "invalid_field_definition", "enum_values"),
        ({"key": "mandant", "aliases": ["Hr. Muster"]}, 422, "key_looks_personal", "aliases[0]"),
    ])
    def test_create_refusals(self, body, status, code, path):
        got, answer = Mock(doc_fields="values").call("POST", "/secured/graph/doc-fields", body)
        assert (got, answer["error_code"], answer.get("path")) == (status, code, path)

    def test_unknown_field_id(self):
        mock = Mock(doc_fields="values")
        for field_id in ("not-a-uuid", "00000000-0000-0000-0000-000000000001"):
            status, body = mock.call("PATCH", f"/secured/graph/doc-fields/{field_id}", {"labels": {}})
            assert status == 404
            assert body == {"status": "error", "error": "Field not found", "error_code": "NOT_FOUND"}

    def test_registry_writes_need_clearance(self):
        mock = Mock(doc_fields="values")
        mock.state.registry_write_allowed = False
        for method, path, body in (("POST", "/secured/graph/doc-fields", {"key": "mandant"}),
                                   ("POST", "/secured/graph/doc-fields/packs/legal_ch/install", None),
                                   ("PUT", "/secured/graph/doc-fields/settings", {"unknown_keys": "reject"}),
                                   ("GET", "/secured/graph/doc-field-rules", None)):
            status, answer = mock.call(method, path, body)
            assert (status, answer["error_code"]) == (403, "registry_write_requires_full_clearance")
        assert mock.call("GET", "/secured/graph/doc-fields")[0] == 200

    def test_packs(self):
        mock = Mock(doc_fields="values")
        body = mock.call("GET", "/secured/graph/doc-fields/packs")[1]
        assert body["packs"] == [
            {"key": "core", "version": 1, "installed": True, "installed_version": 1},
            {"key": "legal_ch", "version": 1, "installed": False, "installed_version": None}]
        status, body = mock.call("POST", "/secured/graph/doc-fields/packs/legal_ch/install")
        assert status == 200
        assert body["installed"] == 11 and body["skipped"] == 0
        assert body["warnings"] == ["target_type_missing:matter"]
        body = mock.call("POST", "/secured/graph/doc-fields/packs/legal_ch/install")[1]
        assert (body["installed"], body["skipped"]) == (0, 11)
        status, body = mock.call("POST", "/secured/graph/doc-fields/packs/medical/install")
        assert (status, body["error_code"]) == (404, "pack_not_found")

    def test_settings(self):
        mock = Mock(doc_fields="values")
        body = mock.call("GET", "/secured/graph/doc-fields/settings")[1]
        assert (body["unknown_keys"], body["date_order"]) == ("ignore", "dmy")
        body = mock.call("PUT", "/secured/graph/doc-fields/settings", {"unknown_keys": "reject"})[1]
        assert body["unknown_keys"] == "reject"
        for bad, path in (({"acting_as": ["g"]}, "acting_as"), ({"date_order": "dym"}, "date_order"),
                          ({}, "settings")):
            status, body = mock.call("PUT", "/secured/graph/doc-fields/settings", bad)
            assert (status, body["error_code"], body["path"]) == (400, "invalid_value", path)


class TestFieldReadingSettings:
    """Knovas 1.5.0 field options as registry.py checks them
    (_clean_columns, _check_merged, _clean_enum_values), so the Platform's
    field forms are tested against the server's rules."""

    def _create(self, mock, **body):
        return mock.call("POST", "/secured/graph/doc-fields", body)

    def test_options_are_stored(self):
        mock = Mock(doc_fields="values")
        status, body = self._create(mock, key="aktenzeichen", datatype="code",
                                    code_scheme="bger")
        assert status == 201 and body["field"]["code_scheme"] == "bger"
        assert self._create(mock, key="belegnummer", datatype="code")[1]["field"][
            "code_scheme"] == "generic"
        status, body = self._create(mock, key="geschaeftsjahr", datatype="period",
                                    fy_start_month=7, fy_label="start")
        assert status == 201
        assert (body["field"]["fy_start_month"], body["field"]["fy_label"]) == (7, "start")
        status, body = self._create(mock, key="eingang", datatype="date", date_order="mdy")
        assert status == 201 and body["field"]["date_order"] == "mdy"
        status, body = self._create(mock, key="gegenseite", datatype="entity_ref",
                                    link_policy="never")
        assert status == 201 and body["field"]["link_policy"] == "never"
        status, body = self._create(mock, key="kostenstelle", datatype="enum", enum_values=[
            {"code": "4100", "labels": {"de": "Verwaltung", "fr": "Administration"},
             "aliases": ["Verw"]}, "4200"])
        assert status == 201 and body["field"]["enum_values"][0]["aliases"] == ["Verw"]

    @pytest.mark.parametrize("body, path", [
        ({"key": "jahr_x", "datatype": "period", "fy_start_month": 7}, "fy_label"),
        ({"key": "jahr_x", "datatype": "period", "fy_start_month": 13, "fy_label": "start"},
         "fy_start_month"),
        ({"key": "jahr_x", "datatype": "date", "fy_start_month": 7, "fy_label": "start"},
         "fy_start_month"),
        ({"key": "jahr_x", "datatype": "period", "fy_label": "middle"}, "fy_label"),
        ({"key": "kennung_x", "datatype": "text", "code_scheme": "iban"}, "code_scheme"),
        ({"key": "kennung_x", "datatype": "code", "code_scheme": "IBAN!"}, "code_scheme"),
        ({"key": "datum_x", "datatype": "date", "date_order": "dym"}, "date_order"),
        ({"key": "art_x", "datatype": "enum", "enum_values": ["a", "a"]}, "enum_values[1]"),
        ({"key": "art_x", "datatype": "enum", "enum_values": [{"code": "a", "x": 1}]},
         "enum_values[0]"),
        ({"key": "art_x", "datatype": "enum",
          "enum_values": [{"code": "a", "aliases": ["n"] * 33}]}, "enum_values[0].aliases"),
    ])
    def test_refused_like_the_server(self, body, path):
        status, answer = self._create(Mock(doc_fields="values"), **body)
        assert (status, answer["error_code"], answer.get("path")) == (
            400, "invalid_field_definition", path)

    def test_the_code_scheme_locks_and_the_link_policy_does_not(self):
        mock = Mock(doc_fields="values")
        field_id = self._create(mock, key="aktenzeichen", datatype="code")[1]["field"]["id"]
        status, body = mock.call("PATCH", f"/secured/graph/doc-fields/{field_id}",
                                 {"code_scheme": "bger"})
        assert (status, body["error_code"]) == (409, "field_type_locked")
        entity_id = self._create(mock, key="gegenseite", datatype="entity_ref")[1]["field"]["id"]
        status, body = mock.call("PATCH", f"/secured/graph/doc-fields/{entity_id}",
                                 {"link_policy": "never"})
        assert status == 200 and body["field"]["link_policy"] == "never"


class TestValuesRules:
    def test_put_get_apply_and_retire(self):
        mock = Mock(doc_fields="values")
        mock.init(fields={"doc_type": "invoice"})
        status, body = mock.call("PUT", "/secured/graph/doc-field-rules",
                                 {"pointer_prefix": "rc-sync/Muster AG/", "set": {"status": "final"}})
        assert status == 200 and body["rule"]["version"] == 1 and body["reapply_job_id"]
        rule_id = body["rule"]["id"]
        view = mock.values()[1]
        assert view["fields"]["status"] == "final"
        assert view["layers"]["status"][0]["source_ref"] == f"rule:{rule_id}"
        body = mock.call("PUT", "/secured/graph/doc-field-rules",
                         {"pointer_prefix": "rc-sync/Muster AG/", "set": {"status": "draft"}})[1]
        assert (body["rule"]["id"], body["rule"]["version"]) == (rule_id, 2)
        # A longer prefix with null switches the shorter default off.
        mock.call("PUT", "/secured/graph/doc-field-rules",
                  {"pointer_prefix": "rc-sync/Muster AG/GJ 2024/", "set": {"status": None}})
        assert "status" not in mock.values()[1]["fields"]
        rules = mock.call("GET", "/secured/graph/doc-field-rules")[1]["rules"]
        assert [r["pointer_prefix"] for r in rules] == ["rc-sync/Muster AG/", "rc-sync/Muster AG/GJ 2024/"]
        assert rules[1]["set"] == {"status": None}
        status, body = mock.call("DELETE", "/secured/graph/doc-field-rules",
                                 {"pointer_prefix": "rc-sync/Muster AG/GJ 2024/"})
        assert status == 200 and body["retired"] is True
        assert mock.values()[1]["fields"]["status"] == "draft"
        status, body = mock.call("DELETE", "/secured/graph/doc-field-rules", {"pointer_prefix": "nope/"})
        assert status == 404 and body["error"] == "Rule not found"

    def test_upload_and_manual_beat_the_rule(self):
        mock = Mock(doc_fields="values")
        mock.init(fields={"doc_type": "invoice"})
        mock.call("PUT", "/secured/graph/doc-field-rules",
                  {"pointer_prefix": "rc-sync/", "set": {"doc_type": "contract"}})
        assert mock.values()[1]["fields"]["doc_type"] == "invoice"

    def test_unknown_key(self):
        status, body = Mock(doc_fields="values").call(
            "PUT", "/secured/graph/doc-field-rules", {"pointer_prefix": "rc-sync/", "set": {"doc_typ": "x"}})
        assert (status, body["error_code"], body["path"], body["suggest"]) == (
            400, "unknown_field", "set.doc_typ", ["doc_type"])


class TestValuesQuery:
    @pytest.mark.parametrize("extra", [{"where": {"doc_type": "invoice"}}, {"return_fields": True},
                                       {"return_fields": False}, {"where": {}}])
    def test_where_or_return_fields_is_unsupported(self, extra):
        status, body = Mock(doc_fields="values").query(Input="lease", **extra)
        assert status == 400
        assert body == {"status": "error", "error_code": "where_unsupported",
                        "error": "Field filters are not enabled for this tenant"}

    def test_find_is_unsupported(self):
        status, body = Mock(doc_fields="values").find(where={"doc_type": "invoice"})
        assert (status, body["error_code"]) == (400, "where_unsupported")

    def test_plain_query_only_adds_keys(self):
        body = Mock(doc_fields="values").query(Input="lease")[1]
        assert body["pointers"] == ["demo-001"]
        assert body["no_strong_matches"] is False and body["relevance_gate_applied"] is False
        assert "where" not in body and "return_fields" not in body
        assert "fields" not in body["results"][0] and "relevance_tier" not in body["results"][0]

    def test_empty_answer_says_why(self):
        body = Mock(doc_fields="values").query(Input="nichts dergleichen")[1]
        assert body["no_strong_matches"] is True and body["no_results_reason"] == "no_candidates"


# -- filters -----------------------------------------------------------------

class TestFiltersQuery:
    def test_where_filters_and_echoes(self):
        mock = Mock(doc_fields="filters")
        status, body = mock.query(where={"doc_type": "contract"})
        assert status == 200
        assert body["pointers"] == ["demo-001", "demo-002"]
        assert body["where"] == {"applied": True, "clauses": 1,
                                 "resolved": [{"field": "doc_type", "op": "eq", "value": "contract"}],
                                 "relevance_gate_applied": True, "no_strong_matches": False,
                                 "may_be_partial": False, "strategy": "allowlist", "exhaustive": True}
        assert body["relevance_gate_applied"] is True
        assert {r["relevance_tier"] for r in body["results"]} == {"strong"}
        assert all("fields" not in r for r in body["results"])

    def test_entity_names_match_casefolded(self):
        body = Mock(doc_fields="filters").query(where={"party": {"name": "muster ag"}})[1]
        assert body["pointers"] == ["demo-001"]
        assert body["where"]["resolved"] == [{"field": "party", "op": "eq", "resolved_nodes": 1}]

    def test_codes_match_exactly_and_labels_resolve(self):
        mock = Mock(doc_fields="filters")
        assert mock.query(where={"Dokumentart": "Vertrag"})[1]["pointers"] == ["demo-001", "demo-002"]
        assert mock.query(where={"doc_type": ["memo", "invoice"]})[1]["pointers"] == ["demo-003"]
        # Values are parsed as on the server: dates are intervals.
        for operand in ("2026-01-01", "01.01.2026", "Januar 2026"):
            assert mock.query(where={"document_date": operand})[1]["pointers"] == ["demo-001"], operand
        assert mock.query(where={"document_date": "2026"})[1]["pointers"] == [
            "demo-001", "demo-002", "demo-003"]
        assert mock.query(where={"document_date": {"gte": "Februar 2026"}})[1]["pointers"] == [
            "demo-002", "demo-003"]
        body = mock.query(where={"document_date": {"between": ["15.01.2026", "Q1 2026"]}})[1]
        assert body["pointers"] == ["demo-002", "demo-003"]
        assert body["where"]["resolved"] == [{"field": "document_date", "op": "between",
                                              "interval": ["2026-01-15", "2026-03-31"]}]
        assert mock.query(where={"status": {"exists": True}})[1]["pointers"] == ["demo-002"]

    def test_money_never_crosses_currencies(self):
        mock = Mock(doc_fields="filters")
        mock.init(fields={"amount": "CHF 1'234.50"})
        mock.init(pointer="rc-sync/Beispiel GmbH/Rechnung_2.pdf", fields={"amount": "1234.50 EUR"})
        assert mock.values()[1]["fields"]["amount"] == {"amount": "1234.50", "currency": "CHF"}
        assert mock.query(where={"amount": "Fr. 1234.50"})[1]["pointers"] == [POINTER]
        body = mock.query(where={"amount": {"gte": "CHF 1000"}})[1]
        assert body["pointers"] == [POINTER]
        assert body["where"]["resolved"] == [{"field": "amount", "op": "gte",
                                              "value": {"amount": "1000", "currency": "CHF"}}]

    def test_return_fields_names_only(self):
        mock = Mock(doc_fields="filters")
        mock.call("POST", "/secured/graph/doc-fields/packs/legal_ch/install")
        mock.init(fields={"client": "Muster AG", "doc_type": "invoice"})
        body = mock.query(Input="Rechnung", return_fields=True)[1]
        assert body["return_fields"] == {"applied": True} and "where" not in body
        fields = body["results"][0]["fields"]
        assert fields["client"] == {"name": "Muster AG"}           # never a node id here
        assert fields["title"] == "Rechnung_17.pdf"
        body = mock.query(Input="lease", return_fields=["doc_type"])[1]
        assert body["results"][0]["fields"] == {"doc_type": "contract"}

    def test_every_result_gets_fields_even_empty(self):
        mock = Mock(doc_fields="filters")
        mock.state.add_document("rc-sync/leer.pdf", title="Leer", snippet="lease")
        body = mock.query(Input="lease", return_fields=["period"])[1]
        assert [r["fields"] for r in body["results"]] == [{}, {}]

    def test_invisible_node_id_short_circuits(self):
        body = Mock(doc_fields="filters").query(
            where={"party": {"node_id": "00000000-0000-0000-0000-000000000009"}}, return_fields=True)[1]
        assert body["results"] == [] and body["no_results_reason"] == "empty_where"
        assert body["where"]["may_be_partial"] is False
        assert body["where"]["relevance_gate_applied"] is False
        assert "strategy" not in body["where"] and body["return_fields"] == {"applied": True}

    @pytest.mark.parametrize("where,code,path", [
        ({}, "invalid_value", "where"),
        (["doc_type"], "invalid_value", "where"),
        ({f"k{i}": "x" for i in range(9)}, "where_too_complex", "where"),
        ({"mandat": "Muster AG"}, "unknown_field", "where.mandat"),
        ({"doc_type": "Kreditorenrechnung"}, "invalid_value", "where.doc_type"),
        ({"doc_type": {"in": []}}, "where_too_complex", "where.doc_type.in"),
        ({"doc_type": ["contract"] * 51}, "where_too_complex", "where.doc_type"),
        ({"party": {"name": "Muster AG", "node_id": "x"}}, "type_mismatch", "where.party"),
        ({"party": {"gte": "Muster AG"}}, "invalid_value", "where.party.gte"),
        ({"amount": 1234}, "type_mismatch", "where.amount"),
        ({"doc_type": {"exists": False}}, "invalid_value", "where.doc_type.exists"),
    ])
    def test_where_refusals(self, where, code, path):
        status, body = Mock(doc_fields="filters").query(where=where)
        assert (status, body["error_code"], body["path"]) == (400, code, path)

    def test_unknown_key_suggests(self):
        body = Mock(doc_fields="filters").query(where={"doc_typ": "invoice"})[1]
        assert body["suggest"] == ["doc_type"]

    def test_deprecated_field_still_matches_by_exact_key(self):
        mock = Mock(doc_fields="filters")
        field_id = mock.state.field_by_key("doc_type")["id"]
        mock.call("POST", f"/secured/graph/doc-fields/{field_id}/deprecate")
        assert mock.query(where={"doc_type": "memo"})[1]["pointers"] == ["demo-003"]
        assert mock.query(where={"Dokumentart": "memo"})[0] == 400

    def test_relevance_gate_switch_without_where(self):
        mock = Mock(doc_fields="filters")
        mock.state.relevance_gate_enabled = True
        body = mock.query(Input="lease")[1]
        assert body["relevance_gate_applied"] is True and "where" not in body


class TestFiltersUncalibrated:
    def test_where_needs_calibration(self):
        status, body = Mock(doc_fields="filters", calibrated=False).query(where={"doc_type": "invoice"})
        assert status == 503
        assert body == {"status": "error", "error_code": "where_requires_calibration",
                        "error": "Field filters need a relevance calibration for this tenant"}

    def test_return_fields_and_find_still_work(self):
        mock = Mock(doc_fields="filters", calibrated=False)
        status, body = mock.query(Input="lease", return_fields=["doc_type"])
        assert status == 200 and body["results"][0]["fields"] == {"doc_type": "contract"}
        status, body = mock.find(where={"doc_type": "contract"})
        assert status == 200 and len(body["documents"]) == 2

    def test_probe_cannot_tell(self):
        status, body = Mock(doc_fields="filters", calibrated=False).find()
        assert (status, body["error_code"], body["path"]) == (400, "invalid_value", "where")


class TestAutoScope:
    """QUERY_AUTO_SCOPE_ENABLED: Knovas narrows a search to the nodes it
    recognises in the question (shape of query_pipeline.py, KB develop)."""

    def test_off_by_default_and_from_the_environment(self, monkeypatch):
        monkeypatch.delenv("MOCK_AUTO_SCOPE", raising=False)
        assert "auto_scope" not in Mock(doc_fields="filters").query(Input="Muster AG")[1]
        monkeypatch.setenv("MOCK_AUTO_SCOPE", "applied")
        assert Mock(doc_fields="filters").state.auto_scope == "applied"

    @pytest.mark.parametrize("mode", ["off", "values", "filters"])
    def test_applied_names_the_detected_node(self, mode):
        status, body = Mock(doc_fields=mode, auto_scope="applied").query(
            Input="Was schuldet die muster ag?")
        node = testing.mock_module().stable_id("node", "Muster AG")
        assert status == 200
        assert body["auto_scope"] == {
            "detections": [{"node_id": node, "identifier_id": None, "channel": "lexical",
                            "score": 1.0}],
            "node_ids": [node], "applied": True, "fallback": False,
            "canonicalized": False, "residualized": False}

    def test_fallback(self):
        block = Mock(doc_fields="filters", auto_scope="fallback").query(
            Input="Beispiel GmbH")[1]["auto_scope"]
        assert (block["applied"], block["fallback"]) == (False, True)

    def test_no_name_no_block(self):
        assert "auto_scope" not in Mock(doc_fields="filters", auto_scope="applied").query(
            Input="lease")[1]

    def test_a_bad_mode_fails_loudly(self):
        with pytest.raises(ValueError):
            testing.load_mock_app(auto_scope="sometimes")


class TestReturnFieldsUnreadable:
    def test_values_cannot_be_read(self):
        mock = Mock(doc_fields="filters")
        mock.state.return_fields_unreadable = True
        body = mock.query(Input="lease", return_fields=["doc_type"])[1]
        assert body["return_fields"] == {"applied": False}
        assert body["results"] and all("fields" not in r for r in body["results"])
        body = mock.query(Input="lease", where={"doc_type": "contract"}, return_fields=True)[1]
        assert body["where"]["applied"] is True and body["return_fields"] == {"applied": False}


class TestFind:
    def _mock(self, n=5):
        mock = Mock(doc_fields="filters")
        for i in range(n):
            date = f"2024-0{i + 1}-15" if i != 2 else None
            fields = {"doc_type": "invoice"}
            if date:
                fields["document_date"] = date
            mock.init(f"rc-sync/Muster AG/Rechnung_{i}.pdf", fields=fields)
        return mock

    def _walk(self, mock, **body):
        pages, after = [], None
        while True:
            status, page = mock.find(**body, **({"after": after} if after else {}))
            assert status == 200
            pages.append(page)
            after = page["next_after"]
            if after is None:
                return pages

    def test_paging(self):
        pages = self._walk(self._mock(), where={"doc_type": "invoice"}, limit=2)
        assert [len(p["documents"]) for p in pages] == [2, 2, 1]
        assert [p["complete"] for p in pages] == [False, False, True]
        assert pages[0]["total_count"] == 5
        assert all("total_count" not in p for p in pages[1:])
        pointers = [d["pointer"] for p in pages for d in p["documents"]]
        assert pointers == sorted(pointers) and len(set(pointers)) == 5
        assert pages[0]["where"] == {"applied": True, "clauses": 1,
                                     "resolved": [{"field": "doc_type", "op": "eq", "value": "invoice"}]}
        assert "return_fields" not in pages[0]

    def test_sort_by_date_desc_nulls_last(self):
        pages = self._walk(self._mock(), where={"doc_type": "invoice"}, limit=2,
                           sort={"field": "document_date", "order": "desc"})
        pointers = [d["pointer"].rsplit("_", 1)[1] for p in pages for d in p["documents"]]
        assert pointers == ["4.pdf", "3.pdf", "1.pdf", "0.pdf", "2.pdf"]

    def test_scan_budget_ends_incomplete(self):
        mock = self._mock()
        mock.state.find_scan_budget = 3
        pages = self._walk(mock, where={"doc_type": "invoice"}, limit=2)
        assert pages[0]["total_count"] is None
        assert pages[-1]["next_after"] is None and pages[-1]["complete"] is False

    @pytest.mark.parametrize("sort", [None, {"field": "pointer", "order": "desc"},
                                      {"field": "document_date", "order": "asc"},
                                      {"field": "document_date", "order": "desc"}])
    def test_an_edit_between_pages_never_ends_the_walk(self, sort):
        """Keyset paging, as the server does: the next page starts after the
        cursor's (key, pointer), whether or not that document still matches."""
        mock = self._mock()
        extra = {"sort": sort} if sort else {}
        everything = [d["pointer"] for p in self._walk(mock, where={"doc_type": "invoice"},
                                                       limit=5, **extra) for d in p["documents"]]
        status, first = mock.find(where={"doc_type": "invoice"}, limit=2, **extra)
        assert status == 200
        edited = first["documents"][-1]["pointer"]
        version = mock.values(edited)[1]["version"]
        assert mock.patch(pointer=edited, if_version=version, set={"doc_type": "memo"})[0] == 200
        rest, after = [], first["next_after"]
        while after:
            status, page = mock.find(where={"doc_type": "invoice"}, limit=2, after=after, **extra)
            assert status == 200
            rest += [d["pointer"] for d in page["documents"]]
            after = page["next_after"]
        assert page["complete"] is True
        assert rest == everything[2:], "the remaining matches, in order"

    def test_a_deleted_cursor_document_never_ends_the_walk(self):
        mock = self._mock()
        status, first = mock.find(where={"doc_type": "invoice"}, limit=2)
        mock.state.anchors.pop(first["documents"][-1]["pointer"])
        status, page = mock.find(where={"doc_type": "invoice"}, limit=5, after=first["next_after"])
        assert [d["pointer"].rsplit("_", 1)[1] for d in page["documents"]] == ["2.pdf", "3.pdf", "4.pdf"]
        assert page["complete"] is True

    @pytest.mark.parametrize("body,code,path", [
        ({"after": "bm90LWEtY3Vyc29y"}, "invalid_cursor", "after"),
        ({"after": 7}, "invalid_cursor", "after"),
        ({"limit": 0}, "invalid_value", "limit"),
        ({"limit": True}, "invalid_value", "limit"),
        ({"sort": {"field": "doc_type"}}, "invalid_value", "sort.field"),
        ({"sort": {"field": "pointer", "order": "up"}}, "invalid_value", "sort.order"),
        ({"sort": {"by": "pointer"}}, "invalid_value", "sort"),
    ])
    def test_refusals(self, body, code, path):
        status, answer = self._mock(1).find(where={"doc_type": "invoice"}, **body)
        assert (status, answer["error_code"], answer["path"]) == (400, code, path)

    def test_cursor_of_another_sort_is_refused(self):
        mock = self._mock()
        after = mock.find(where={"doc_type": "invoice"}, limit=1)[1]["next_after"]
        status, body = mock.find(where={"doc_type": "invoice"}, after=after,
                                 sort={"field": "document_date"})
        assert (status, body["error_code"]) == (400, "invalid_cursor")

    def test_return_fields_show_node_ids(self):
        mock = Mock(doc_fields="filters")
        mock.call("POST", "/secured/graph/doc-fields/packs/legal_ch/install")
        mock.init(fields={"client": "Muster AG"})
        body = mock.find(where={"client": "Muster AG"}, return_fields=["client"])[1]
        assert body["return_fields"] == {"applied": True}
        assert body["documents"][0]["fields"]["client"]["node_id"] == \
            testing.mock_module().stable_id("node", "Muster AG")

    def test_quarantined_never_match(self):
        mock = self._mock(1)
        mock.state.quarantined.add("rc-sync/Muster AG/Rechnung_0.pdf")
        assert mock.find(where={"doc_type": "invoice"})[1]["documents"] == []


# -- brokered ------------------------------------------------------------------

class TestBrokered:
    def test_access_groups_need_an_assertion_in_every_mode(self):
        for mode in ("off", "values", "filters"):
            mock = Mock(doc_fields=mode, brokered=True)
            status, body = mock.init(access_groups=["g"])
            assert status == 401
            assert body == {"status": "error", "error": "principal assertion rejected",
                            "error_code": "assertion_rejected"}
            assert mock.init(access_groups=["g"], principal_assertion="jws")[0] == 201

    def test_one_node_is_read_with_an_assertion_only(self):
        mock = Mock(doc_fields="filters", brokered=True)
        path = f"/secured/graph/nodes/{testing.mock_module().stable_id('node', 'Muster AG')}"
        status, body = mock.call("GET", path)
        assert (status, body["error_code"]) == (401, "assertion_rejected")
        assert mock.call("GET", path, {"principal_assertion": "jws"})[0] == 200

    def test_entity_fields_without_an_assertion_stay_unlinked(self):
        """S1: a BROKERED upload without an assertion (RemoteController) is
        accepted; no node is read, names stay unlinked, node ids are dropped
        and `register` counts as `ignore`."""
        mock = Mock(doc_fields="values", brokered=True)
        mock.call("POST", "/secured/graph/doc-fields/packs/legal_ch/install",
                  {"principal_assertion": "jws"})
        mock.state.settings["unknown_keys"] = "register"
        node = testing.mock_module().stable_id("node", "Muster AG")
        status, body = mock.init(fields={"client": "Muster AG", "party": {"node_id": node},
                                         "aktenzeichen_intern": "A-1"})
        assert status == 201
        assert {(w["key"], w["code"]) for w in body["fields"]["warnings"]} == {
            ("client", "unresolved_entity"), ("party", "invalid_value")}
        assert body["fields"]["unknown_keys"] == ["aktenzeichen_intern"]
        assert mock.state.field_by_key("aktenzeichen_intern") is None
        fields = mock.call("GET", "/secured/graph/doc-values",
                           {"pointer": POINTER, "principal_assertion": "jws"})[1]["fields"]
        assert fields["client"] == {"name": "Muster AG"} and "party" not in fields
        # With the assertion the same name links.
        body = mock.init(fields={"client": "Muster AG"}, principal_assertion="jws")[1]
        assert body["fields"]["warnings"] == []
        # Explicit access_groups still need the assertion.
        assert mock.init(access_groups=["g1"])[0] == 401

    def test_before_s1_entity_values_need_an_assertion(self):
        mock = Mock(doc_fields="values", brokered=True)
        mock.state.s1 = False
        status, body = mock.init(fields={"party": "Muster AG"})
        assert (status, body["error_code"]) == (401, "assertion_rejected")
        assert POINTER not in mock.state.stored_pointers
        assert mock.init(fields={"doc_type": "invoice"})[0] == 201
        assert mock.init(fields={"party": "Muster AG"}, principal_assertion="jws")[0] == 201

    def test_graph_and_query_need_an_assertion(self):
        mock = Mock(doc_fields="filters", brokered=True)
        mock.state.stored_pointers.add(POINTER)
        assert mock.values()[0] == 401
        assert mock.call("GET", "/secured/graph/doc-values",
                         {"pointer": POINTER, "principal_assertion": "jws"})[0] == 200
        assert mock.query(Input="lease")[0] == 401
        assert mock.query(Input="lease", principal_assertion="jws")[0] == 200
        assert mock.call("GET", "/secured/graph/doc-fields")[0] == 401

    def test_a_server_before_s2_reads_the_pointer_from_the_query_string_only(self):
        mock = Mock(doc_fields="values")
        mock.state.stored_pointers.add(POINTER)
        mock.state.pointer_in_body = False
        status, body = mock.values()
        assert (status, body["error_code"], body["path"]) == (400, "invalid_value", "pointer")
        status, _ = mock.call("GET", "/secured/graph/doc-values", query_string={"pointer": POINTER})
        assert status == 200
        mock.state.pointer_in_body = True
        assert mock.values()[0] == 200

    def test_probe_order(self):
        # find checks the where gate before the principal: an unasserted
        # probe still tells values from filters (doctor.sh relies on it).
        assert Mock(doc_fields="values", brokered=True).find()[1]["error_code"] == "where_unsupported"
        assert Mock(doc_fields="filters", brokered=True).find()[1]["error_code"] == "assertion_rejected"
        assert Mock(doc_fields="off", brokered=True).find()[1]["error_code"] == "HTTP_404"


# -- the requests helpers ------------------------------------------------------

class TestRequestsHelpers:
    def test_session_keeps_https_and_carries_get_bodies(self):
        mock = Mock(doc_fields="values")
        mock.init()
        session = testing.WsgiSession(mock.app)
        session.cert = ("/certs/client.pem", "/certs/client.key")
        session.verify = "/certs/ca.pem"
        response = session.request("GET", "https://knovas:8443/secured/graph/doc-values",
                                    json={"pointer": POINTER, "principal_assertion": "jws"}, timeout=5)
        assert response.status_code == 200 and response.ok
        assert response.json()["pointer"] == POINTER
        assert response.headers["Content-Type"].startswith("application/json")
        assert response.url.startswith("https://knovas:8443/")
        assert mock.state.requests[-1]["json"]["principal_assertion"] == "jws"

    def test_query_string_and_errors(self):
        mock = Mock(doc_fields="off")
        session = testing.WsgiSession(mock.app)
        response = session.get("https://knovas:8443/api/search", params={"query": "lease", "limit": 1})
        assert response.json()["results"][0]["doc_id"] == "demo-001"
        response = session.post("https://knovas:8443/secured/graph/doc-values/find", json={})
        assert response.status_code == 404 and response.reason == "Not Found"
        with pytest.raises(Exception) as exc:
            response.raise_for_status()
        assert exc.value.response.json()["error_code"] == "HTTP_404"

    def test_wsgi_request_is_a_drop_in_for_requests_request(self):
        mock = Mock(doc_fields="values")
        request = testing.wsgi_request(mock.app)
        response = request("POST", "https://knovas:8443/secured/init_document_transmission",
                           json={"identifier": POINTER, "part_count": 1, "fields": {"doc_type": "invoice"}},
                           cert=("/certs/client.pem", "/certs/client.key"), verify="/certs/ca.pem",
                           timeout=120)
        assert response.status_code == 201
        assert response.json()["fields"]["staged"] == 1
        assert response.content and response.text


# -- goldens -------------------------------------------------------------------

@pytest.mark.parametrize("path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_golden(path):
    golden = json.loads(path.read_text(encoding="utf-8"))
    assert golden["name"] == path.stem
    mock = _mock_for(golden["server_state"])
    for step in golden.get("setup") or ():
        status, _ = mock.call(step["method"], step["path"], step.get("body"))
        assert 200 <= status < 300, step
    request = golden["request"]
    status, body = mock.call(request["method"], request["path"], request.get("body"))
    assert status == golden["response"]["status"], body
    _contained(golden["response"]["body"], body)
    for dotted in golden.get("volatile") or ():
        assert all(value is not None for value in _resolve(body, dotted)), dotted
    for key in golden.get("absent") or ():
        assert key not in body, key


def test_goldens_cover_the_server_states():
    states = {json.loads(p.read_text(encoding="utf-8"))["server_state"] for p in GOLDENS}
    assert states == {"off", "values", "filters", "filters_uncalibrated"}
    names = {p.stem for p in GOLDENS}
    assert {"probe_off", "probe_values", "probe_filters", "init_echo", "query_where_echo",
            "where_unsupported_query", "where_requires_calibration", "version_conflict",
            "http_404_feature_off", "find_first_page"} <= names


def test_new_python_files_are_ascii():
    for name in ("app.py", "testing.py", "tests/test_mock_doc_fields.py"):
        (MOCK_DIR / name).read_bytes().decode("ascii")


@pytest.mark.parametrize("datatype,raw,stored", [
    ("period", "GJ 2024", {"lo": "2024-01-01", "hi": "2024-12-31", "label": "2024"}),
    ("period", "Q3 2024", {"lo": "2024-07-01", "hi": "2024-09-30", "label": "2024-Q3"}),
    ("period", "01.07.2023\u201330.06.2024",
     {"lo": "2023-07-01", "hi": "2024-06-30", "label": "2023-07-01/2024-06-30"}),
    ("date", "15.03.2024", {"lo": "2024-03-15", "hi": "2024-03-15", "precision": "day"}),
    ("date", "M\u00e4rz 2024", {"lo": "2024-03-01", "hi": "2024-03-31", "precision": "month"}),
    ("money", "CHF 1'234.50", {"amount": "1234.50", "currency": "CHF"}),
    ("money", "Fr. 12.-", {"amount": "12.00", "currency": "CHF"}),
    ("money", "1.500 \u20ac", {"amount": "1500.00", "currency": "EUR"}),
    ("number", "1'234.50", "1234.5"),
    ("number", "12,5", "12.5"),
])
def test_typed_values_have_the_server_shapes(datatype, raw, stored):
    assert testing.mock_module().typed_value(datatype, raw) == stored


@pytest.mark.parametrize("datatype,raw,code", [
    ("money", "1234.50", "invalid_value"),          # no currency
    ("date", "H1 2024", "invalid_value"),           # a date is no half-year
    ("date", "irgendwann", "invalid_value"),
    ("period", {"lo": "2024-12-31", "hi": "2024-01-01"}, "invalid_value"),
    ("number", True, "type_mismatch"),
])
def test_typed_values_refused_as_on_the_server(datatype, raw, code):
    module = testing.mock_module()
    with pytest.raises(module._Refused) as refused:
        module.typed_value(datatype, raw)
    assert refused.value.code == code
