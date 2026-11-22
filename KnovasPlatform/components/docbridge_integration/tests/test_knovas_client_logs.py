"""RB-FIX-1, FU-43: the Core client writes no customer text into a log line, at any level, DEBUG included.

A body the Core sends can quote a group's name, which since RB-4 is a role's name the customer chose; a path can
carry a speaking group id or a node's id; a query string a document's pointer. A log line names the route
(``/secured/access_groups/{id}``), the status, the Core's error code when it is a short identifier and the Core's
request id (``X-Request-ID``) - never body text, never the path's ids. The error ``requests`` raises names the route
too, because callers log exceptions. Fakes only: nothing reaches a Core.
"""
from __future__ import annotations

import json
import logging

import pytest
import requests
import tenacity

from knovas_client import KnovasAPIClient, _route_name
from test_knovas_client_hardening import FakeSession, make_client, make_secured_client

#: A role's name as a customer would choose it, quoted by the Core in its answer.
ROLE_NAME = "Mandat Zwiebel-Erbschaft"
#: A speaking group id (a tenant may choose one) and a node id that is a name.
GROUP_ID = "g-zwiebel-erbschaft"
NODE_ID = "Erbschaft Zwiebel"
POINTER = "Mandanten/Zwiebel/Testament 2026.pdf"
SNIPPET = "Der Erblasser vermacht das Ferienhaus in Arosa seiner Nichte."
REQUEST_ID = "3f2a9c41d0e84b7f9a1c2d3e4f5a6b7c"
SECRETS = (ROLE_NAME, GROUP_ID, "zwiebel", "Zwiebel", "Arosa", "Testament", SNIPPET)


class Answer:
    """A response double with a body, headers and a status, as ``requests`` gives them."""

    def __init__(self, status=200, body=None, *, text=None, request_id=REQUEST_ID):
        self.status_code = status
        self.text = text if text is not None else ("" if body is None else json.dumps(body))
        self.content = self.text.encode("utf-8")
        self.headers = {"X-Request-ID": request_id} if request_id is not None else {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            # What requests itself says: the whole URL, ids and query string included.
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error: x for url: https://knovas.test/secured/access_groups/"
                f"{GROUP_ID}?pointer={POINTER}", response=self)


def _refusal(status, code="NOT_FOUND"):
    return Answer(status, {"status": "error", "error_code": code,
                           "message": f"Gruppe «{ROLE_NAME}» ({GROUP_ID}) nicht gefunden"})


def _client(answer, *, secured=False):
    client = make_secured_client() if secured else make_client()
    client._session = FakeSession(lambda method, url, **kw: answer() if callable(answer) else answer)
    return client


@pytest.fixture(autouse=True)
def _debug_and_fast(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="knovas_client")    # whatever an earlier test left on it
    monkeypatch.setattr(KnovasAPIClient._make_request.retry, "wait", tenacity.wait_none())


def _assert_clean(caplog, *extra):
    text = caplog.text
    for secret in SECRETS + extra:
        assert secret not in text, f"{secret!r} reached a log line"
    return text


# -- the route's name ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint, route", [
    (f"/secured/access_groups/{GROUP_ID}", "/secured/access_groups/{id}"),
    ("/secured/access_groups", "/secured/access_groups"),
    (f"/secured/agents/{GROUP_ID}/claims", "/secured/agents/{id}/claims"),
    ("/secured/folder_rules/r-7", "/secured/folder_rules/{id}"),
    ("/secured/graph/nodes/Erbschaft%20Zwiebel/facts", "/secured/graph/nodes/{id}/facts"),
    ("/secured/graph/node-types/t1/schema/a-zwiebel", "/secured/graph/node-types/{id}/schema/{id}"),
    ("/secured/graph/doc-fields/packs", "/secured/graph/doc-fields/packs"),
    ("/secured/graph/doc-fields/settings", "/secured/graph/doc-fields/settings"),
    ("/secured/graph/doc-fields/zwiebel/deprecate", "/secured/graph/doc-fields/{id}/deprecate"),
    ("/secured/graph/doc-fields/packs/zwiebel/install", "/secured/graph/doc-fields/packs/{id}/install"),
    ("/secured/graph", "/secured/graph"),
    (f"/secured/document_readable?pointer={POINTER}", "/secured/document_readable"),
    (f"https://knovas.test/secured/access_groups/{GROUP_ID}", "/secured/access_groups/{id}"),
    # a route no template knows keeps its own words only
    (f"/secured/access_groups/{GROUP_ID}/members", "/secured/access_groups/{id}/{id}"),
    (f"/secured/{GROUP_ID}", "/secured/{id}"),
])
def test_a_route_is_named_without_its_ids(endpoint, route):
    assert _route_name(endpoint) == route


# -- _rbac_request and _make_request ---------------------------------------------------------------------------


def test_a_write_404_names_the_route_not_the_group(caplog):
    """FU-43's case: a write's 404 at the Core logged up to 600 characters of the body, which quoted the name."""
    client = _client(_refusal(404))
    assert client.rename_access_group(GROUP_ID, ROLE_NAME) is None
    assert client.delete_access_group(GROUP_ID) is False
    text = _assert_clean(caplog)
    assert "PATCH /secured/access_groups/{id} -> 404" in text
    assert "DELETE /secured/access_groups/{id} -> 404" in text
    assert "error_code=NOT_FOUND" in text and f"request_id={REQUEST_ID}" in text
    assert "nicht protokolliert" in text


def test_a_core_failure_logs_no_body_and_raises_an_error_without_the_url(caplog):
    client = _client(_refusal(500, code="INTERNAL_ERROR"))
    with pytest.raises(requests.exceptions.HTTPError) as raised:
        client.create_access_group(ROLE_NAME, parent=GROUP_ID)
    text = _assert_clean(caplog)
    assert "POST /secured/access_groups -> 500" in text
    assert "error_code=INTERNAL_ERROR" in text and f"request_id={REQUEST_ID}" in text
    message = str(raised.value)
    for secret in SECRETS + (POINTER,):
        assert secret not in message
    assert message == "Knovas API POST /secured/access_groups -> HTTP 500"
    assert raised.value.response.status_code == 500          # the caller still reads the answer
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_the_debug_line_names_the_route_not_the_path(caplog):
    client = _client(Answer(200, {"status": "success"}))
    client.rename_access_group(GROUP_ID, ROLE_NAME)
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and r.name == "knovas_client"]
    assert "Knovas API PATCH /secured/access_groups/{id}" in debug
    _assert_clean(caplog)


def test_a_body_that_is_no_json_is_counted_not_logged(caplog):
    client = _client(Answer(200, text=f"<html>{ROLE_NAME} {SNIPPET}</html>"))
    assert client.document_access(POINTER) == {}
    text = _assert_clean(caplog, POINTER)
    assert "GET /secured/document_access -> 200" in text


@pytest.mark.parametrize("code", [
    f"Gruppe {ROLE_NAME} fehlt",            # text, not an identifier
    "A" * 65,                                # too long
    {"name": ROLE_NAME},                     # not a string
])
def test_an_error_code_that_is_no_short_identifier_is_not_logged(caplog, code):
    client = _client(_refusal(404, code=code))
    client.rename_access_group(GROUP_ID, ROLE_NAME)
    text = _assert_clean(caplog)
    assert "error_code=-" in text
    if isinstance(code, str):
        assert code not in text


def test_a_request_id_that_is_no_short_identifier_is_not_logged(caplog):
    client = _client(Answer(409, {"error_code": "conflict"}, request_id=f"req {ROLE_NAME}"))
    with pytest.raises(requests.exceptions.HTTPError):
        client.create_access_group(ROLE_NAME)
    text = _assert_clean(caplog)
    assert "request_id=-" in text and "error_code=conflict" in text


def test_document_readable_logs_neither_the_pointer_nor_the_error_text(caplog):
    client = _client(_refusal(500))
    assert client.document_readable(POINTER) is False
    text = _assert_clean(caplog, POINTER)
    assert "document_readable failed (HTTPError, HTTP 500)" in text
    caplog.clear()
    broken = make_client()

    def no_connection(method, url, **kw):
        raise requests.exceptions.ConnectionError(f"cannot reach {url} for {POINTER}")

    broken._session = FakeSession(no_connection)
    assert broken.document_readable(POINTER) is False
    _assert_clean(caplog, POINTER)


# -- the graph ------------------------------------------------------------------------------------------------------


def test_a_graph_write_404_and_an_empty_write_name_the_route(caplog):
    client = _client(_refusal(404))
    assert client.graph_delete_node(NODE_ID) is None
    client = _client(Answer(200, {}))
    assert client.graph_update_node(NODE_ID, name=ROLE_NAME) == {}
    text = _assert_clean(caplog, NODE_ID)
    assert "DELETE /secured/graph/nodes/{id} -> 404, so nothing was written" in text
    assert "PATCH /secured/graph/nodes/{id} -> 200 with an empty body" in text


def test_a_graph_answer_without_json_is_counted_not_logged(caplog):
    client = _client(Answer(200, text=f"{ROLE_NAME}: {SNIPPET}"))
    assert client.graph_node(NODE_ID) == {}
    text = _assert_clean(caplog, NODE_ID)
    assert "GET /secured/graph/nodes/{id} -> 200" in text


def test_a_graph_read_404_names_the_route(caplog):
    client = _client(Answer(404, {"error_code": "NOT_FOUND"}))
    assert client.graph_node(NODE_ID) is None
    text = _assert_clean(caplog, NODE_ID)
    assert "Graph 404 (unbekannte oder fremde Id): GET /secured/graph/nodes/{id}" in text


def test_a_failed_parallel_graph_read_names_the_route(caplog):
    client = make_client()

    def no_connection(method, url, **kw):
        raise requests.exceptions.ConnectionError(url)

    client._session = FakeSession(no_connection)
    answers = client.graph_get_many([(f"/nodes/{NODE_ID}/facts", None)], max_retries=0)
    assert answers == [(599, None)]
    text = _assert_clean(caplog, NODE_ID)
    assert "Graph read /secured/graph/nodes/{id}/facts failed: ConnectionError" in text


def test_document_fields_off_names_the_route_and_a_short_code_only(caplog):
    from knovas_client import DocFieldsUnavailable

    client = _client(Answer(404, {"error_code": f"{ROLE_NAME} off", "message": ROLE_NAME}))
    with pytest.raises(DocFieldsUnavailable):
        client.update_doc_field(GROUP_ID, {"label": ROLE_NAME})
    text = _assert_clean(caplog)
    assert "Document fields off at Knovas: PATCH /secured/graph/doc-fields/{id} -> 404 -" in text


# -- the search and the upload ----------------------------------------------------------------------------------------


def test_the_search_logs_the_number_of_chunks_not_their_text(caplog):
    hit = {"pointer": POINTER, "title": ROLE_NAME, "top_chunks": [{"snippet": SNIPPET}, {"snippet": SNIPPET}]}
    client = _client(Answer(200, {"status": "success", "results": [hit], "result_count": 1}), secured=True)
    client.search_documents("Ferienhaus", limit=5)
    text = _assert_clean(caplog, POINTER)
    assert "top_chunks=2 item(s), not logged" in text


def test_an_uploaded_text_document_is_logged_without_its_identifier(caplog):
    answers = iter([Answer(200, {"transmission_key_id": "k1"}), Answer(200, {"status": "success"})])
    client = make_secured_client()
    client._session = FakeSession(lambda method, url, **kw: next(answers))
    client.upload_text_document(POINTER, title=ROLE_NAME, description="", path=POINTER,
                                parts=[{"snippet": SNIPPET}])
    text = _assert_clean(caplog, POINTER)
    assert f"Text document uploaded: 1 part(s), request_id={REQUEST_ID}" in text


# -- urllib3, the library under requests -----------------------------------------------------------------------------


def test_urllib3_is_held_at_info_so_its_request_lines_with_paths_stay_out(monkeypatch):
    """urllib3 logs each request line (path and query string: a group's id, a pointer) at DEBUG."""
    from web_interface.app import QUIET_LOGGERS, _configure_logging_for_wsgi

    names = ("",) + QUIET_LOGGERS
    saved = {name: logging.getLogger(name).level for name in names}
    try:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        class _Config:
            def get(self, key, default=None):
                return default

        _configure_logging_for_wsgi(_Config())
        assert "urllib3" in QUIET_LOGGERS
        assert logging.getLogger("urllib3.connectionpool").getEffectiveLevel() >= logging.INFO
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)
