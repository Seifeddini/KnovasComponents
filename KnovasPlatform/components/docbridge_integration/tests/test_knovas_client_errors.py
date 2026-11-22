"""RB-FIX-2: the errors the Core client raises carry no URL, query, path id or Core text, and urllib3's warnings no
address.

RB-FIX-1's verification found what was left: an error for no answer (no connection, a timeout) still quoted the
whole URL - ``graph_nodes(q="Anna Zwiebelmeier")`` against a closed port showed ``q=Anna+Zwiebelmeier`` in the
traceback, and app.py logs such tracebacks; ``GraphError`` and ``DocFieldsError`` kept the Core's text; urllib3's
WARNING on an answer whose headers do not parse named the whole URL. Now:

1. every request path of the client raises the same kind of error, naming the route's template only, with nothing of
   the original chained;
2. the client's own errors carry the status, a short error code and the Core's request id, never the Core's text;
   pages say it in their own words;
3. urllib3's loggers keep their warnings with every address taken out;
4. the request id is kept wherever a failure is told, so an operator finds the Core's full message in its own logs.

Fakes and one closed local port only: nothing reaches a Core.
"""
from __future__ import annotations

import json
import logging
import socket
import traceback
from urllib.parse import urlencode

import pytest
import requests
import tenacity

from knovas_client import DocFieldsError, DocFieldsUnavailable, GraphError, KnovasAPIClient, QueryRejected
from test_knovas_client_hardening import FakeSession, make_client, make_secured_client

NAME = "Anna Zwiebelmeier"
NODE_ID = "Erbschaft Zwiebel"
POINTER = "Mandanten/Zwiebel/Testament 2026.pdf"
REQUEST_ID = "7c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f"
#: What must not show. A traceback's source lines may show code (``graph_nodes(q=NAME)``), never a value.
SECRETS = ("Anna", "Zwiebel", "Testament", "q=Anna", "pointer=Mandanten", "example.test", "knovas.test")


def _chain(exc: BaseException) -> str:
    """Everything a logged traceback of ``exc`` would show: every exception of the chain with its message."""
    return "".join(traceback.format_exception(exc))


def _clean(text: str, *extra: str) -> None:
    for secret in SECRETS + extra:
        assert secret not in text, f"{secret!r} in {text[:300]!r}"


def _as_requests_says(method, url, params=None):
    """The error ``requests`` raises for no connection: its text quotes the path and the query string."""
    path = url.split("://", 1)[-1].split("/", 1)[-1]
    query = f"?{urlencode(params)}" if params else ""
    return requests.exceptions.ConnectionError(
        f"HTTPSConnectionPool(host='knovas.test', port=443): Max retries exceeded with url: /{path}{query} "
        f"(Caused by NewConnectionError('Failed to establish a new connection: [Errno 111] refused'))")


class Answer:
    def __init__(self, status, body, *, request_id=REQUEST_ID):
        self.status_code = status
        self.text = json.dumps(body)
        self.content = self.text.encode()
        self.headers = {"X-Request-ID": request_id}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error for url: https://knovas.test/secured/graph/nodes?q={NAME}",
                response=self)


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch, caplog):
    monkeypatch.setattr(KnovasAPIClient._make_request.retry, "wait", tenacity.wait_none())
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="knovas_client")


def _failing(error=None):
    client = make_client()
    calls = []

    def respond(method, url, **kw):
        calls.append((method, url, kw.get("params")))
        raise error if error is not None else _as_requests_says(method, url, kw.get("params"))

    client._session = FakeSession(respond)
    return client, calls


# === 1. no answer: the error names the route only ====================================================================


def test_the_verifiers_repro_a_closed_port_shows_no_query_in_the_traceback(caplog):
    """``graph_nodes(q=NAME)`` against a closed local port, through the real ``requests`` session."""
    with socket.socket() as probe:              # a port that was free a moment ago: nothing listens there
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = make_client(base_url=f"http://127.0.0.1:{port}")
    with pytest.raises(Exception) as raised:
        client.graph_nodes(q=NAME)
    text = _chain(raised.value)
    _clean(text, "Zwiebelmeier", "127.0.0.1")
    assert "/secured/graph/nodes" in text and "no answer (ConnectionError)" in text
    _clean(caplog.text, "Zwiebelmeier")       # urllib3's DEBUG line names the host (the Core's), not the path


@pytest.mark.parametrize("call, route", [
    (lambda c: c.graph_nodes(q=NAME), "GET /secured/graph/nodes"),
    (lambda c: c.graph_node(NODE_ID), "GET /secured/graph/nodes/{id}"),
    (lambda c: c.document_access(POINTER), "GET /secured/document_access"),
    (lambda c: c.rename_access_group("g-zwiebel", NAME), "PATCH /secured/access_groups/{id}"),
])
def test_through_make_request_the_error_and_its_chain_name_the_route_only(call, route):
    client, calls = _failing()
    with pytest.raises(Exception) as raised:
        call(client)
    assert len(calls) == 3                       # retried as before: the error is still a ConnectionError
    text = _chain(raised.value)
    _clean(text)
    assert f"Knovas API {route}: no answer (ConnectionError)" in text


def test_through_request_no_retry_the_error_keeps_its_class_and_names_the_route():
    client, _ = _failing()
    with pytest.raises(requests.exceptions.ConnectionError) as raised:
        client.put_agent_claims(NAME, ["claim"])
    assert str(raised.value) == "Knovas API PUT /secured/agents/{id}/claims: no answer (ConnectionError)"
    assert raised.value.__context__ is None and raised.value.__cause__ is None
    assert raised.value.request is None and raised.value.response is None


@pytest.mark.parametrize("error, kind", [
    (requests.exceptions.ReadTimeout(f"read timed out: /secured/graph/nodes?q={NAME}"), requests.exceptions.ReadTimeout),
    (requests.exceptions.ConnectTimeout(f"connect timed out: /x?q={NAME}"), requests.exceptions.ConnectTimeout),
    (requests.exceptions.SSLError(f"bad handshake for /x?q={NAME}"), requests.exceptions.SSLError),
    (requests.exceptions.InvalidURL(f"invalid: /x?q={NAME}"), requests.exceptions.InvalidURL),
])
def test_a_timeout_stays_a_timeout_and_every_kind_loses_its_text(error, kind):
    client, _ = _failing(error)
    with pytest.raises(kind) as raised:
        client._request_no_retry("GET", "/secured/document_readable", params={"pointer": POINTER})
    assert type(raised.value) is kind
    _clean(_chain(raised.value))


def test_through_request_quiet_and_the_probes_the_error_names_the_route():
    client, _ = _failing()
    with pytest.raises(requests.exceptions.ConnectionError) as raised:
        client.graph_node_name(NODE_ID, timeout=1)
    _clean(_chain(raised.value))
    assert "GET /secured/graph/nodes/{id}" in str(raised.value)
    assert client.doc_fields_probe() == "unknown"


def test_the_parallel_reads_and_the_batch_lose_the_text_of_an_unretried_error():
    client, _ = _failing(requests.exceptions.InvalidHeader(f"bad header for /secured/graph/nodes/{NODE_ID}"))
    with pytest.raises(requests.exceptions.InvalidHeader) as raised:
        client.graph_get_many([(f"/nodes/{NODE_ID}/facts", None)], max_retries=0)
    _clean(_chain(raised.value), NODE_ID)
    assert "GET /secured/graph/nodes/{id}/facts" in str(raised.value)
    with pytest.raises(requests.exceptions.InvalidHeader) as raised:
        client.graph_facts_batch([NODE_ID])
    _clean(_chain(raised.value), NODE_ID)


def test_a_document_fields_read_without_an_answer_chains_no_url():
    client, _ = _failing()
    with pytest.raises(DocFieldsError) as raised:
        client.doc_fields()
    assert raised.value.error_code == "transport_error" and raised.value.status == 503
    _clean(_chain(raised.value))


# === 2. the client's own errors: status, code, request id; never the Core's text ======================================


def _answering(status, body, *, secured=False):
    client = make_secured_client() if secured else make_client()
    client._session = FakeSession(lambda method, url, **kw: Answer(status, body))
    return client


def _no_text(exc: BaseException, *extra: str) -> None:
    _clean(_chain(exc) + repr(vars(exc)), *extra)


def test_a_graph_error_carries_status_code_and_request_id_not_the_cores_text():
    client = _answering(422, {"error_code": "invalid_node", "message": f"Knoten «{NAME}» ist ungültig",
                              "error": f"{NAME} exists"})
    with pytest.raises(GraphError) as raised:
        client.graph_update_node(NODE_ID, name=NAME)
    error = raised.value
    assert (error.status, error.error_code, error.request_id, error.message) == (422, "invalid_node", REQUEST_ID, "")
    assert str(error) == f"Knovas graph call failed: HTTP 422, error_code invalid_node, request_id {REQUEST_ID}"
    _no_text(error, NODE_ID)


def test_an_error_code_that_is_text_is_dropped():
    client = _answering(500, {"error_code": f"Knoten {NAME} fehlt", "message": NAME})
    with pytest.raises(GraphError) as raised:
        client.graph_delete_node(NODE_ID)
    assert raised.value.error_code is None
    _no_text(raised.value, NODE_ID)


def test_a_document_fields_error_carries_no_core_text_and_the_page_its_own_words():
    from doc_fields_view import error_message
    from web_interface.admin_doc_fields import knovas_message
    from web_interface.doc_fields_routes import _edit_refusal, filter_refusal

    client = _answering(400, {"error_code": "invalid_value", "message": f"«{NAME}» ist kein Datum",
                              "path": "set.vertragsdatum"})
    with pytest.raises(DocFieldsError) as raised:
        client.update_doc_field("vertragsdatum", {"label": NAME})
    error = raised.value
    assert (error.status, error.error_code, error.request_id) == (400, "invalid_value", REQUEST_ID)
    assert error.details == {"path": "set.vertragsdatum"}
    _no_text(error)
    for shown in (knovas_message(error)[0], json.dumps(filter_refusal(error, None)[0]),
                  json.dumps(_edit_refusal(error, None)[0])):
        assert NAME not in shown and "Datum" not in shown
    assert knovas_message(error)[0] == error_message("invalid_value", error.details, None)


def test_a_feature_off_and_a_refused_query_carry_a_short_code_only():
    assert DocFieldsUnavailable(status=404, error_code=f"{NAME} off").error_code is None
    assert DocFieldsUnavailable(status=404, error_code="HTTP_404").error_code == "HTTP_404"
    rejected = QueryRejected(400, "unknown_field", {"path": "where.x", "message": NAME}, request_id=REQUEST_ID)
    assert (rejected.error_code, rejected.request_id) == ("unknown_field", REQUEST_ID)
    _no_text(rejected)


def test_a_refused_filtered_search_names_the_request_id(caplog):
    client = _answering(400, {"error_code": "unknown_field", "message": NAME}, secured=True)
    with pytest.raises(QueryRejected) as raised:
        client.search_documents("Ferienhaus", limit=5, where={"x": {"eq": NAME}})
    assert raised.value.request_id == REQUEST_ID
    _no_text(raised.value)


# === 4. the request id stays where a failure is told =================================================================


def test_a_write_without_retry_logs_its_failure_with_code_and_request_id(caplog):
    body = {"error_code": "not_brokered", "message": f"Agent {NAME}"}
    client = _answering(409, body)
    with pytest.raises(requests.exceptions.HTTPError) as raised:
        client.put_agent_claims("agent-1", ["claim"])
    assert f"(request_id={REQUEST_ID})" in str(raised.value)
    line = [r.getMessage() for r in caplog.records if r.name == "knovas_client" and "-> 409" in r.getMessage()]
    assert line == ["Knovas API PUT /secured/agents/{id}/claims -> 409 (error_code=not_brokered, "
                    f"request_id={REQUEST_ID}; body {len(json.dumps(body))} chars, not logged)"]
    _clean(caplog.text)


# === 3. urllib3's warnings: kept, without an address ================================================================


@pytest.fixture
def held(monkeypatch):
    """The app's hold on the libraries' loggers, as create_app applies it; the loggers as they were afterwards."""
    from web_interface.app import QUIET_LOGGERS, URLLIB3_LOGGERS, _hold_quiet_loggers

    names = QUIET_LOGGERS + URLLIB3_LOGGERS
    saved = {n: (logging.getLogger(n).level, list(logging.getLogger(n).filters)) for n in names}
    _hold_quiet_loggers(logging.DEBUG)
    yield
    for n, (level, filters) in saved.items():
        logging.getLogger(n).setLevel(level)
        logging.getLogger(n).filters[:] = filters


def test_urllib3s_header_warning_keeps_its_news_without_the_url(held, caplog):
    from urllib3.exceptions import HeaderParsingError

    try:
        raise HeaderParsingError(defects=["bad"], unparsed_data=f"X-Name: {NAME}")
    except HeaderParsingError as hpe:
        logging.getLogger("urllib3.connection").warning(
            "Failed to parse headers (url=%s): %s",
            f"https://knovas.test:443/secured/graph/nodes?q={NAME.replace(' ', '+')}", hpe, exc_info=True)
    record = [r for r in caplog.records if r.name == "urllib3.connection"][-1]
    assert record.levelno == logging.WARNING
    assert record.getMessage().startswith("Failed to parse headers (url=<url>)")
    assert record.getMessage() == "Failed to parse headers (url=<url>): <HeaderParsingError, not logged>"
    assert record.exc_info is None
    _clean(caplog.text, "Zwiebelmeier")


def test_urllib3s_retry_warning_loses_the_path_and_two_installs_add_one_filter(held, caplog):
    from web_interface.app import RedactUrls, _hold_quiet_loggers

    _hold_quiet_loggers(logging.INFO)
    pool = logging.getLogger("urllib3.connectionpool")
    assert sum(isinstance(f, RedactUrls) for f in pool.filters) == 1
    pool.warning("Retrying (%r) after connection broken by '%r': %s", "Retry(total=0)", "ProtocolError()",
                 f"/secured/graph/nodes/{NODE_ID.replace(' ', '%20')}?q={NAME.replace(' ', '+')}")
    assert "<url>" in caplog.text
    _clean(caplog.text, "Erbschaft")
