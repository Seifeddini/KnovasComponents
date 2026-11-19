"""PMVP-20: what KnovasAPIClient sends and keeps for the Knovas Assistent.

``llm_generate`` (POST /secured/llm/generate): the body, the person's
assertion, one request and no retry, 429/503 -> CoreBusy, everything else ->
CoreFailed, and no response body in a log line. And the passage text of a
/secured/query hit, kept in the row as ``snippet`` only when asked for, so
the search page's rows are what they were.

Asserted on what leaves the process (the fake session), as in
test_knovas_client_assertion.py.
"""
from __future__ import annotations

import json
import logging

import pytest
import requests

from assistant.errors import CoreBusy, CoreFailed
from identity.assertion import AssertionVerifier
from identity.broker_key import load_or_create_signer, public_pem
from knovas_client import ASSERTION_FIELD, KnovasAPIClient, _snippet_from_hit

TENANT = "tenant-a"
USER_ID = "22222222-2222-2222-2222-222222222222"
SECRET = "GENERIERTER-TEXT-IM-FEHLERKOERPER-31337"


class _StubConfig:
    def __init__(self, values):
        self._v = dict(values)

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        return bool(self._v.get(key, default))

    def get_int(self, key, default=0):
        return int(self._v.get(key, default))


SECURED = {
    "api.base_url": "https://core.test",
    "api.customer_id": TENANT,
    "api.cert_path": "client.crt",
    "api.key_path": "client.key",
    "api.ca_cert_path": "ca.crt",
    "api.cert_auto_renew_enabled": False,
    "api.rate_limit.requests_per_second": 0,
    "web.doc_fields.ui": "off",
}


class _User:
    id = USER_ID
    roles = ("member",)


class _Broker:
    def __init__(self, signer, user):
        self._signer, self._user = signer, user

    def current_user(self):
        return self._user

    def assertion_for(self, user):
        return self._signer.mint(subject=str(user.id), tenant=TENANT, groups=["litigation"],
                                 roles=list(user.roles))


def _response(status, payload=None, *, text=None, url="https://core.test/secured/llm/generate"):
    resp = requests.Response()
    resp.status_code = status
    resp.reason = "test"
    resp.url = url
    body = text if text is not None else json.dumps(payload)
    resp._content = body.encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


class _Session:
    """Records every request; answers with ``answers`` in turn (a Response or an exception)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []

    def request(self, **kw):
        self.sent.append(kw)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def key_dir(tmp_path):
    path = tmp_path / "broker"
    path.mkdir()
    return path


@pytest.fixture
def signer(key_dir):
    return load_or_create_signer(key_dir)


@pytest.fixture
def verifier(key_dir, signer):
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    return AssertionVerifier({kid: public_pem(key_dir)})


@pytest.fixture
def client(signer):
    c = KnovasAPIClient(config_loader=_StubConfig(SECURED))
    c.attach_principal_broker(_Broker(signer, _User()))
    return c


def _wire(client, monkeypatch, *answers):
    session = _Session(*answers)
    monkeypatch.setattr(client._session, "request", session.request)
    return session


OK = {"status": "success", "message": "Generation complete", "content": "Drei Monate [1].",
      "model": "some-model", "prompt_tokens": 120, "completion_tokens": 9}
MESSAGES = [{"role": "user", "content": "Frage?"}, {"role": "assistant", "content": "Antwort."},
            {"role": "user", "content": "Quellen ... Frage: Und?"}]


# --- llm_generate: what goes out ------------------------------------------------------------------


def test_the_body_and_the_assertion_go_out_once(client, monkeypatch, verifier):
    session = _wire(client, monkeypatch, _response(200, OK))
    result = client.llm_generate(MESSAGES, system_instruction="Regeln", max_tokens=700,
                                 temperature=0.2)
    assert len(session.sent) == 1
    sent = session.sent[0]
    assert sent["method"] == "POST"
    assert sent["url"] == "https://core.test/secured/llm/generate"
    body = sent["json"]
    assert body["messages"] == MESSAGES
    assert body["system_instruction"] == "Regeln"
    assert body["max_tokens"] == 700
    assert body["temperature"] == 0.2
    assert set(body) == {"messages", "system_instruction", "max_tokens", "temperature",
                         ASSERTION_FIELD}
    assert verifier.verify(body[ASSERTION_FIELD], tenant=TENANT).subject == USER_ID
    assert sent["allow_redirects"] is False
    assert result == {"content": "Drei Monate [1].", "model": "some-model",
                      "prompt_tokens": 120, "completion_tokens": 9}


def test_optional_fields_are_left_out(client, monkeypatch):
    session = _wire(client, monkeypatch, _response(200, OK))
    client.llm_generate([{"role": "user", "content": "x", "extra": "nicht senden"}])
    body = session.sent[0]["json"]
    assert set(body) == {"messages", "max_tokens", ASSERTION_FIELD}
    assert body["messages"] == [{"role": "user", "content": "x"}]
    assert body["max_tokens"] == 512


def test_nobody_signed_in_sends_nothing(signer, monkeypatch):
    c = KnovasAPIClient(config_loader=_StubConfig(SECURED))
    c.attach_principal_broker(_Broker(signer, None))
    session = _wire(c, monkeypatch, _response(200, OK))
    with pytest.raises(PermissionError):
        c.llm_generate(MESSAGES)
    assert session.sent == []


def test_a_generation_waits_longer_than_a_search(client, monkeypatch):
    session = _wire(client, monkeypatch, _response(200, OK))
    client.llm_generate(MESSAGES)
    assert session.sent[0]["timeout"] == 120
    client.llm_generate(MESSAGES, timeout=7)
    assert session.sent[1]["timeout"] == 7


def test_the_timeout_and_the_endpoint_follow_the_config(signer, monkeypatch):
    c = KnovasAPIClient(config_loader=_StubConfig({
        **SECURED, "api.llm_generate_timeout": 45,
        "api.endpoints.llm_generate": "/v2/secured/llm/generate"}))
    c.attach_principal_broker(_Broker(signer, _User()))
    session = _wire(c, monkeypatch, _response(200, OK))
    c.llm_generate(MESSAGES)
    assert session.sent[0]["timeout"] == 45
    assert session.sent[0]["url"] == "https://core.test/v2/secured/llm/generate"


# --- llm_generate: what comes back ----------------------------------------------------------------


def test_an_envelope_nesting_the_result_under_data_is_read(client, monkeypatch):
    nested = {"status": "success", "message": "ok",
              "data": {"content": "Text [2].", "model": "m", "prompt_tokens": 1,
                       "completion_tokens": 2}}
    _wire(client, monkeypatch, _response(200, nested))
    assert client.llm_generate(MESSAGES)["content"] == "Text [2]."


def test_missing_token_counts_are_none(client, monkeypatch):
    _wire(client, monkeypatch, _response(200, {"status": "success", "content": "x",
                                                "prompt_tokens": "viele"}))
    assert client.llm_generate(MESSAGES) == {"content": "x", "model": None,
                                             "prompt_tokens": None, "completion_tokens": None}


@pytest.mark.parametrize("status", [429, 503])
def test_busy_is_core_busy_and_not_retried(client, monkeypatch, status):
    session = _wire(client, monkeypatch, _response(status, {"status": "error", "message": SECRET}))
    with pytest.raises(CoreBusy) as caught:
        client.llm_generate(MESSAGES)
    assert caught.value.status == status
    assert len(session.sent) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 500, 502, 504])
def test_any_other_http_error_is_core_failed(client, monkeypatch, status):
    session = _wire(client, monkeypatch, _response(status, {"status": "error", "message": SECRET}))
    with pytest.raises(CoreFailed) as caught:
        client.llm_generate(MESSAGES)
    assert not isinstance(caught.value, CoreBusy)
    assert caught.value.status == status
    assert len(session.sent) == 1


@pytest.mark.parametrize("exc", [requests.exceptions.ReadTimeout("read timed out"),
                                 requests.exceptions.ConnectTimeout("connect timed out"),
                                 requests.exceptions.ConnectionError("reset"),
                                 requests.exceptions.SSLError("bad cert")])
def test_transport_failures_are_core_failed_and_not_retried(client, monkeypatch, exc):
    session = _wire(client, monkeypatch, exc)
    with pytest.raises(CoreFailed):
        client.llm_generate(MESSAGES)
    assert len(session.sent) == 1


@pytest.mark.parametrize("answer", [
    _response(200, text="<html>kein JSON</html>"),
    _response(200, ["eine", "Liste"]),
    _response(200, {"status": "error", "content": "x"}),
    _response(200, {"content": "ohne Status"}),
    _response(200, {"status": "success", "message": "ohne Inhalt"}),
    _response(200, {"status": "success", "content": None}),
    _response(200, {"status": "success", "content": {"text": "kein String"}}),
])
def test_a_malformed_envelope_is_core_failed(client, monkeypatch, answer):
    _wire(client, monkeypatch, answer)
    with pytest.raises(CoreFailed):
        client.llm_generate(MESSAGES)


def test_no_body_question_or_answer_reaches_a_log_line(client, monkeypatch, caplog):
    question = [{"role": "user", "content": "FRAGE-IM-PROMPT-4711"}]
    answers = [
        _response(200, {**OK, "content": "ANTWORT-TEXT-0815", "model": "MODELLNAME-X"}),
        _response(429, {"status": "error", "message": SECRET}),
        _response(503, text=SECRET),
        _response(500, {"status": "error", "message": SECRET}),
        _response(400, {"status": "error", "message": SECRET, "field": "messages"}),
        _response(200, text=SECRET),
        requests.exceptions.ReadTimeout(SECRET),
    ]
    with caplog.at_level(logging.DEBUG):
        for answer in answers:
            _wire(client, monkeypatch, answer)
            try:
                client.llm_generate(question, system_instruction="REGELN-IM-SYSTEM-99")
            except (CoreBusy, CoreFailed):
                pass
    logged = "\n".join(
        r.getMessage() + " " + (r.exc_text or "") + " " + str(r.__dict__) for r in caplog.records)
    for token in (SECRET, "FRAGE-IM-PROMPT", "ANTWORT-TEXT", "REGELN-IM-SYSTEM", "MODELLNAME-X"):
        assert token not in logged, token
    assert "HTTP 429" in logged and "HTTP 500" in logged


def test_the_raised_error_does_not_carry_the_body(client, monkeypatch):
    _wire(client, monkeypatch, _response(500, {"status": "error", "message": SECRET}))
    with pytest.raises(CoreFailed) as caught:
        client.llm_generate(MESSAGES)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


# --- the passage text of a /secured/query hit ---------------------------------------------------------


HIT = {
    "pointer": "Mandate/Mueller/vertrag.pdf",
    "title": "vertrag",
    "cosine_similarity": 0.81,
    "snippet": "Die Kuendigungsfrist betraegt drei Monate zum Monatsende.",
    "text": "Die Kuendigungsfrist betraegt drei Monate zum Monatsende.",
    "top_chunks": [
        {"chunk_uuid": "c-1", "page_number": 2, "sentence_number": 14, "cosine_similarity": 0.81,
         "snippet": "Die Kuendigungsfrist betraegt drei Monate zum Monatsende."},
        {"chunk_uuid": "c-2", "page_number": 3, "sentence_number": 20, "cosine_similarity": 0.7,
         "snippet": "Die Frist beginnt mit dem Zugang."},
    ],
}


def test_the_snippet_is_read_where_the_core_puts_it():
    assert _snippet_from_hit(HIT) == HIT["snippet"]
    # Only on the chunks (a hit whose row-level copy is missing): the first chunk's.
    only_chunks = {k: v for k, v in HIT.items() if k not in ("snippet", "text")}
    assert _snippet_from_hit(only_chunks) == HIT["top_chunks"][0]["snippet"]
    camel = {"pointer": "p", "topChunks": [{"snippet": "  aus topChunks  "}]}
    assert _snippet_from_hit(camel) == "aus topChunks"


def test_only_the_snippet_key_is_passage_text():
    """Not ``text``/``content``: GI-QUERY-04 serves original_text only as ``snippet``."""
    assert _snippet_from_hit({"pointer": "p", "text": "BM25 ohne Ziffern", "content": "x",
                              "top_chunks": [{"page_number": 1, "text": "y"}]}) is None
    assert _snippet_from_hit({"pointer": "p", "snippet": "   ", "top_chunks": "kaputt"}) is None


def _query_answer(*hits, nested=False):
    if nested:
        return {"status": "success", "data": {"results": list(hits), "result_count": len(hits)}}
    return {"status": "success", "results": list(hits), "result_count": len(hits)}


def test_search_keeps_the_snippet_only_when_asked(client, monkeypatch):
    plain_hit = {"pointer": "Akten/ohne.pdf", "cosine_similarity": 0.5}
    session = _wire(client, monkeypatch,
                    _response(200, _query_answer(HIT, plain_hit), url="https://core.test/secured/query"))
    with client.keeping_snippets():
        with_text = client.search_documents("Kuendigungsfrist", limit=5)["results"]
    page_rows = client.search_documents("Kuendigungsfrist", limit=5)["results"]

    assert with_text[0]["snippet"] == HIT["snippet"]
    assert "snippet" not in with_text[1]
    # The search page's rows are what they were: no snippet key at all.
    assert all("snippet" not in row for row in page_rows)
    assert [{k: v for k, v in r.items() if k != "snippet"} for r in with_text] == page_rows
    # And the question Knovas receives is the same.
    first, second = (dict(s["json"]) for s in session.sent)
    first.pop(ASSERTION_FIELD), second.pop(ASSERTION_FIELD)
    assert first == second == {"Input": "Kuendigungsfrist", "limit": 5}
    assert session.sent[0]["url"] == "https://core.test/secured/query"


def test_a_nested_query_answer_keeps_the_snippet(client, monkeypatch):
    _wire(client, monkeypatch,
          _response(200, _query_answer(HIT, nested=True), url="https://core.test/secured/query"))
    with client.keeping_snippets():
        rows = client.search_documents("Frist", limit=5)["results"]
    assert rows[0]["snippet"] == HIT["snippet"]
    assert rows[0]["doc_id"] == HIT["pointer"]


def test_keeping_snippets_is_per_thread_and_ends_with_the_block(client, monkeypatch):
    import threading

    _wire(client, monkeypatch,
          _response(200, _query_answer(HIT), url="https://core.test/secured/query"))
    other = {}

    def search_page_request():
        other["rows"] = client.search_documents("Frist", limit=5)["results"]

    with client.keeping_snippets():
        with client.keeping_snippets():
            pass
        inside = client.search_documents("Frist", limit=5)["results"]
        thread = threading.Thread(target=search_page_request)
        thread.start()
        thread.join()
    after = client.search_documents("Frist", limit=5)["results"]
    assert "snippet" in inside[0]
    assert "snippet" not in other["rows"][0]
    assert "snippet" not in after[0]


def test_an_error_inside_the_block_still_ends_it(client, monkeypatch):
    _wire(client, monkeypatch,
          _response(200, _query_answer(HIT), url="https://core.test/secured/query"))
    with pytest.raises(RuntimeError):
        with client.keeping_snippets():
            raise RuntimeError("x")
    assert "snippet" not in client.search_documents("Frist", limit=5)["results"][0]


def test_search_documents_takes_what_it_took():
    """The fakes mirror its signature (test_knovas_client_doc_fields); keeping
    the passage text added no parameter."""
    import inspect

    params = list(inspect.signature(KnovasAPIClient.search_documents).parameters)
    assert params == ["self", "query", "limit", "filters", "where", "return_fields"]
