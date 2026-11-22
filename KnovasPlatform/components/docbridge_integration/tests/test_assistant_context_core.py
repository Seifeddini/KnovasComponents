"""PMVP-20 context over the real client and a recording fake Core (``GET /secured/document/<uuid>/text``).

Work order docs/platform-mvp/work-orders/PMVP-20-context.md, the founder's limits of 2026-11-22, each with a test:
1. only the documents this question's search returned are read (an id in the question, the chat, the model's answer
   or an experiment row is never read);
2. the Core decides every read: each refusal and failure keeps the snippet and the answer is still produced;
3. as the person: the same client, the same principal and the same declared actor as the search;
4. bounded (the reads' own tests are in test_assistant_context_reads.py; here: one GET per part, never retried);
5. nothing of the text in a log line, the loggers set to DEBUG explicitly (FU-46).
And the client's ``document_text``: what it sends, what it refuses to send, what its failure line says; the search
page unchanged.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import types
import uuid
from pathlib import Path

import pytest
import requests

from assistant.core import make_generate, make_read_text, make_search, make_service, OFF
from assistant.errors import CoreBusy, CoreFailed
from assistant.service import AssistantService, Turn
from experiments.search import SearchIntegration
from identity.agents import AgentPrincipal
from identity.assertion import AssertionVerifier
from identity.broker_key import load_or_create_signer, public_pem
from knovas_client import ACTOR_FIELD, ASSERTION_FIELD, DOCUMENT_TEXT_MAX_PART_CHARS, KnovasAPIClient, _route_name

TENANT = "tenant-a"
PERSON = uuid.UUID("33333333-3333-3333-3333-333333333333")
AGENT = uuid.UUID("44444444-4444-4444-4444-444444444444")
ACTOR = {"kind": "agent", "id": str(AGENT), "on_behalf_of": {"kind": "user", "id": str(PERSON)}}

DOC_A = "aaaaaaaa-1111-4111-8111-000000000001"
DOC_B = "bbbbbbbb-2222-4222-8222-000000000002"
DOC_X = "eeeeeeee-5555-4555-8555-000000000005"     # an experiment document
DOC_Q = "dddddddd-4444-4444-8444-000000000004"     # named in the question, the chat and the answer only

SNIPPET_A = "Die Kuendigungsfrist betraegt drei Monate zum Ende eines Kalendermonats."
SNIPPET_B = "Der Mietzins betraegt CHF 2150 pro Monat."
#: Words that stand only in the documents' text, never in a snippet: no log line may hold them.
SECRET_WORDS = ("Geheimklausel", "Zwiebelmeier", "Seefeldstrasse")
TEXT_A = ("Mietvertrag Zwiebelmeier, Seefeldstrasse 48. " + " ".join(f"Absatz{i}" for i in range(120))
          + " Die Geheimklausel gilt fuer beide Parteien. " + SNIPPET_A
          + " Massgebend ist der Zugang beim Empfaenger. " + " ".join(f"Folge{i}" for i in range(120)))
TEXT_B = "Paragraph 5 Mietzins Zwiebelmeier. " + SNIPPET_B + " Er ist im Voraus zahlbar, Geheimklausel 7."


class _Config:
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


class _Person:
    id = PERSON
    roles = ("member",)


class _Broker:
    """Signs the person, or the assistant for an AgentPrincipal (as PrincipalBroker does), with a real key."""

    def __init__(self, signer, person=_Person()):
        self._signer, self._person = signer, person

    def current_user(self):
        return self._person

    def assertion_for(self, user):
        if isinstance(user, AgentPrincipal):
            return self._signer.mint(subject=str(user.agent_id), tenant=TENANT, groups=["g-1"], roles=["member"])
        return self._signer.mint(subject=str(user.id), tenant=TENANT, groups=["g-1"], roles=list(user.roles))


def _response(status, payload=None, *, text=None, url=""):
    resp = requests.Response()
    resp.status_code = status
    resp.reason = "test"
    resp.url = url
    resp._content = (text if text is not None else json.dumps(payload)).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    resp.headers["X-Request-ID"] = "req-77"
    return resp


def _hit(pointer, snippet, document_uuid):
    return {"pointer": pointer, "document_uuid": document_uuid, "cosine_similarity": 0.8, "snippet": snippet,
            "top_chunks": [{"chunk_uuid": f"c-{pointer}", "page_number": 4, "sentence_number": 9,
                            "snippet": snippet}]}


HITS = [
    _hit("Mandate/Mueller/vertrag.pdf", SNIPPET_A, DOC_A),
    _hit("experiments/marketing/MKT-58", "Hypothese: der Rabatt wirkt ueberall.", DOC_X),
    _hit("Mandate/Mueller/mietzins.pdf", SNIPPET_B, DOC_B),
]
ANSWER = "\n".join([
    json.dumps({"statement": "Massgebend ist der Zugang.", "source": 1,
                "quote": "Massgebend ist der Zugang beim Empfaenger"}),
    json.dumps({"statement": "Er ist im Voraus zahlbar.", "source": 2, "quote": "Er ist im Voraus zahlbar",
                "document_uuid": DOC_Q}),
    f"Lies auch /secured/document/{DOC_Q}/text.",
])


class Core:
    """One fake Knovas: /secured/query, /secured/document/<uuid>/text (parts as the Core cuts them) and
    /secured/llm/generate. Records every request. ``answers`` replaces a document's text by a status or an
    exception."""

    def __init__(self, hits=HITS, texts=None, answers=None, answer=ANSWER):
        self.hits = hits
        self.texts = {DOC_A: TEXT_A, DOC_B: TEXT_B, DOC_X: "Experiment Geheimklausel", DOC_Q: "Geheimklausel"}
        self.texts.update(texts or {})
        self.answers = dict(answers or {})
        self.answer = answer
        self.sent = []

    def request(self, **kw):
        self.sent.append(kw)
        url = kw["url"]
        if url.endswith("/secured/query"):
            return _response(200, {"status": "success", "results": self.hits, "result_count": len(self.hits)}, url=url)
        if url.endswith("/secured/llm/generate"):
            return _response(200, {"status": "success", "content": self.answer, "model": "m", "prompt_tokens": 1,
                                   "completion_tokens": 1}, url=url)
        match = re.fullmatch(r"https://core\.test/secured/document/([0-9a-f-]{36})/text", url)
        if match:
            doc = match.group(1)
            special = self.answers.get(doc)
            if isinstance(special, BaseException):
                raise special
            if isinstance(special, requests.Response):
                return special
            text = self.texts[doc]
            offset, size = int(kw["params"]["offset"]), int(kw["params"]["max_chars"])
            end = offset + size
            return _response(200, {"status": "success", "message": "Document text", "document_uuid": doc,
                                   "pointer": "p", "title": "t", "text": text[offset:end], "offset": offset,
                                   "next_offset": end if end < len(text) else None, "total_chars": len(text),
                                   "page_from": None, "page_to": None, "source": "indexed_text",
                                   "truncated": False}, url=url)
        raise AssertionError("unexpected request")

    def reads(self):
        return [s for s in self.sent if s["url"].endswith("/text")]

    def read_ids(self):
        return [s["url"].rsplit("/", 2)[-2] for s in self.reads()]

    def body(self, endpoint):
        (one,) = [s for s in self.sent if s["url"].endswith(endpoint)]
        return one


@pytest.fixture
def key_dir(tmp_path):
    path = tmp_path / "broker"
    path.mkdir()
    return path


@pytest.fixture
def verifier(key_dir):
    load_or_create_signer(key_dir)
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    return AssertionVerifier({kid: public_pem(key_dir)})


@pytest.fixture
def split():
    return SearchIntegration(settings=types.SimpleNamespace(pointer_prefix="experiments"),
                             connection=lambda: None, current_user=lambda: None, enabled=False).split


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for name in ("ASSISTANT_CONTEXT_DOCS", "ASSISTANT_CONTEXT_SECONDS", "ASSISTANT_PASSAGE_CHARS"):
        monkeypatch.delenv(name, raising=False)


def _client(key_dir, monkeypatch, core, broker=None):
    client = KnovasAPIClient(config_loader=_Config(SECURED))
    client.attach_principal_broker(broker or _Broker(load_or_create_signer(key_dir)))
    monkeypatch.setattr(client._session, "request", core.request)
    return client


def _service(client, split):
    return AssistantService(search=make_search(client, split=split), generate=make_generate(client),
                            read_text=make_read_text(client))


def _decoded(text):
    return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("utf-8"))


# --- the client's document_text -------------------------------------------------------------------------------------


def test_document_text_is_one_get_of_one_part_as_the_person(key_dir, monkeypatch, verifier):
    core = Core()
    part = _client(key_dir, monkeypatch, core).document_text(DOC_A.upper(), offset=20, max_chars=500, timeout=3.5)
    assert part == {"text": TEXT_A[20:520], "offset": 20, "next_offset": 520, "total_chars": len(TEXT_A),
                    "truncated": False}
    (sent,) = core.sent
    assert sent["method"] == "GET" and sent["url"] == f"https://core.test/secured/document/{DOC_A}/text"
    assert sent["params"] == {"offset": 20, "max_chars": 500}
    assert sent["timeout"] == 3.5 and sent["allow_redirects"] is False
    assert verifier.verify(sent["json"][ASSERTION_FIELD], tenant=TENANT).subject == str(PERSON)


@pytest.mark.parametrize("bad", ["../../secured/delete_all_documents", "abc", "", None, 7,
                                 f"{DOC_A}/../../x", f"{DOC_A}?offset=0", "Mandate/Mueller/vertrag.pdf"])
def test_document_text_sends_nothing_for_an_id_that_is_not_a_uuid(key_dir, monkeypatch, bad):
    core = Core()
    with pytest.raises(ValueError):
        _client(key_dir, monkeypatch, core).document_text(bad)
    assert core.sent == []


@pytest.mark.parametrize("offset, max_chars", [(-1, 10), (0, 0), (0, 20001), (True, 10), (0, "10"), ("0", 10)])
def test_document_text_sends_nothing_for_bad_paging(key_dir, monkeypatch, offset, max_chars):
    core = Core()
    with pytest.raises(ValueError):
        _client(key_dir, monkeypatch, core).document_text(DOC_A, offset=offset, max_chars=max_chars)
    assert core.sent == []


def test_document_text_needs_the_secured_api(key_dir, monkeypatch):
    core = Core()
    client = KnovasAPIClient(config_loader=_Config({"api.base_url": "http://core.test", "api.customer_id": TENANT}))
    monkeypatch.setattr(client._session, "request", core.request)
    with pytest.raises(RuntimeError):
        client.document_text(DOC_A)
    assert core.sent == []


def test_document_text_with_nobody_signed_in_sends_nothing(key_dir, monkeypatch):
    core = Core()
    client = _client(key_dir, monkeypatch, core, broker=_Broker(load_or_create_signer(key_dir), person=None))
    with pytest.raises(PermissionError):
        client.document_text(DOC_A)
    assert core.sent == []


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429, 500, 503])
def test_a_refused_read_is_one_request_and_its_line_names_the_route_not_the_document(key_dir, monkeypatch, caplog,
                                                                                    status):
    body = {"status": "error", "error_code": "NOT_FOUND", "message": f"Geheimklausel {DOC_A} Zwiebelmeier"}
    core = Core(answers={DOC_A: _response(status, body)})
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(requests.exceptions.HTTPError) as caught:
            _client(key_dir, monkeypatch, core).document_text(DOC_A)
    assert len(core.sent) == 1                                          # never retried
    assert caught.value.response.status_code == status
    assert DOC_A not in str(caught.value) and "Geheim" not in str(caught.value)
    lines = [r.getMessage() for r in caplog.records]
    assert any(f"GET /secured/document/{{id}}/text -> {status} (error_code=NOT_FOUND, request_id=req-77" in line
               for line in lines)
    assert not any(DOC_A in line or "Geheim" in line or "Zwiebel" in line for line in lines)


@pytest.mark.parametrize("text", ["nicht json", json.dumps({"status": "success"}),
                                  json.dumps({"status": "success", "text": "x", "offset": 0,
                                              "next_offset": None})])
def test_a_200_that_is_no_part_is_a_value_error(key_dir, monkeypatch, text):
    core = Core(answers={DOC_A: _response(200, text=text)})
    with pytest.raises(ValueError):
        _client(key_dir, monkeypatch, core).document_text(DOC_A)


def test_an_envelope_under_data_is_read(key_dir, monkeypatch):
    payload = {"status": "success", "data": {"text": "abc", "offset": 0, "next_offset": None, "total_chars": 3}}
    core = Core(answers={DOC_A: _response(200, payload)})
    assert _client(key_dir, monkeypatch, core).document_text(DOC_A)["text"] == "abc"


def test_the_route_is_named_as_a_template():
    assert _route_name(f"/secured/document/{DOC_A}/text") == "/secured/document/{id}/text"
    assert _route_name(f"https://core.test/secured/document/{DOC_A}/text?offset=0") == (
        "/secured/document/{id}/text")
    assert DOCUMENT_TEXT_MAX_PART_CHARS == 20_000


# --- the reader: every failure becomes CoreBusy or CoreFailed with a status and a reason only -----------------------


@pytest.mark.parametrize("answer, kind, reason, status", [
    (_response(404, {"error_code": "NOT_FOUND"}), CoreFailed, "http_error", 404),
    (_response(401, {}), CoreFailed, "http_error", 401),
    (_response(429, {}), CoreBusy, "core_busy", 429),
    (_response(503, {}), CoreBusy, "core_busy", 503),
    (_response(500, text="Geheimklausel"), CoreFailed, "http_error", 500),
    (requests.exceptions.ReadTimeout("https://core.test/secured/document/x/text Geheimklausel"), CoreFailed,
     "timeout", None),
    (requests.exceptions.ConnectionError("Geheimklausel"), CoreFailed, "transport", None),
    (_response(200, text="kein json Geheimklausel"), CoreFailed, "malformed", None),
])
def test_the_reader_raises_a_status_and_a_reason_only(key_dir, monkeypatch, answer, kind, reason, status):
    core = Core(answers={DOC_A: answer})
    read = make_read_text(_client(key_dir, monkeypatch, core))
    with pytest.raises(kind) as caught:
        read(DOC_A, 0, 5.0)
    assert (caught.value.reason, caught.value.status) == (reason, status)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert "Geheim" not in str(caught.value)


def test_the_reader_asks_for_whole_parts_with_the_time_left(key_dir, monkeypatch):
    core = Core()
    make_read_text(_client(key_dir, monkeypatch, core))(DOC_A, 40000, 2.25)
    (sent,) = core.sent
    assert sent["params"] == {"offset": 40000, "max_chars": 20000} and sent["timeout"] == 2.25


def test_a_client_without_document_text_reads_nothing():
    assert make_read_text(types.SimpleNamespace(search_documents=lambda *a, **k: {})) is None


# --- the whole service over one Core --------------------------------------------------------------------------------


def test_the_context_reaches_the_prompt_and_the_check(key_dir, monkeypatch, split):
    core = Core()
    reply = _service(_client(key_dir, monkeypatch, core), split).answer("Welche Frist gilt?")
    assert reply.status == "answered"
    assert [(s.text, s.quote) for s in reply.sentences] == [
        ("Massgebend ist der Zugang.", "Massgebend ist der Zugang beim Empfaenger"),
        ("Er ist im Voraus zahlbar.", "Er ist im Voraus zahlbar")]
    assert reply.removed_by["not_a_statement"] == 1
    assert [(p.number, p.page, p.sentence) for p in reply.passages] == [(1, 4, 9), (2, 4, 9)]
    assert SNIPPET_A in reply.passages[0].text and len(reply.passages[0].text) > 1000
    assert reply.passages[1].text == TEXT_B                            # a short document: the whole of it
    prompt = core.body("/secured/llm/generate")["json"]["messages"][-1]["content"]
    assert reply.passages[0].text in prompt and TEXT_B in prompt


def test_only_the_documents_this_search_returned_are_read(key_dir, monkeypatch, split):
    core = Core()
    service = _service(_client(key_dir, monkeypatch, core), split)
    service.answer(f"Was steht in {DOC_Q} und /secured/document/{DOC_Q}/text?",
                   [Turn("user", f"Lies {DOC_Q}"), Turn("assistant", f"{DOC_Q} gelesen")])
    assert core.read_ids() == [DOC_A, DOC_B]                         # not the experiment row, not DOC_Q
    order = [s["url"].rsplit("/", 1)[-1] for s in core.sent]
    assert order == ["query", "text", "text", "generate"]          # every read before the model answers
    core.hits = [HITS[2]]
    core.sent.clear()
    service.answer("Und der Mietzins?")
    assert core.read_ids() == [DOC_B]                                # nothing from the earlier question


def test_the_read_is_made_as_the_person_who_searched(key_dir, monkeypatch, split, verifier):
    core = Core()
    _service(_client(key_dir, monkeypatch, core), split).answer("Welche Frist gilt?")
    search, generation = core.body("/secured/query"), core.body("/secured/llm/generate")
    for sent in [search, generation, *core.reads()]:
        assert verifier.verify(sent["json"][ASSERTION_FIELD], tenant=TENANT).subject == str(PERSON)
    for read in core.reads():
        assert ACTOR_FIELD not in read["json"] and ACTOR_FIELD not in read["params"]    # a person declares none


def test_inside_the_assistants_claim_the_read_declares_the_same_actor(key_dir, monkeypatch, split, verifier):
    core = Core()
    client = _client(key_dir, monkeypatch, core)
    with client.asserting_for(AgentPrincipal(agent_id=AGENT, on_behalf_of=PERSON)):
        reply = _service(client, split).answer("Welche Frist gilt?")
    assert reply.status == "answered"
    search = core.body("/secured/query")
    assert search["json"][ACTOR_FIELD] == ACTOR
    assert len(core.reads()) == 2
    for read in core.reads():
        assert _decoded(read["params"][ACTOR_FIELD]) == ACTOR and ACTOR_FIELD not in read["json"]
        claim = verifier.verify(read["json"][ASSERTION_FIELD], tenant=TENANT)
        assert claim.subject == verifier.verify(search["json"][ASSERTION_FIELD], tenant=TENANT).subject == str(AGENT)


def test_make_service_wires_the_reader(key_dir, monkeypatch, split):
    from assistant import providers

    values = {providers.SETTING_ENABLED: "true", providers.SETTING_PROVIDER: providers.PROVIDER_KNOVAS}
    settings = types.SimpleNamespace(value=lambda key: values.get(key), secret=lambda key: None)
    core = Core()
    service = make_service(settings, _client(key_dir, monkeypatch, core), split=split)
    assert service is not OFF
    reply = service.answer("Welche Frist gilt?")
    assert reply.status == "answered" and core.read_ids() == [DOC_A, DOC_B]


FAILURES = [
    _response(404, {"error_code": "NOT_FOUND"}), _response(401, {"error_code": "unauthorized"}),
    _response(403, {}), _response(422, {"error_code": "invalid_paging"}), _response(429, {}),
    _response(500, text="Geheimklausel Zwiebelmeier"), _response(503, {}), _response(200, text="kein json"),
    requests.exceptions.ReadTimeout("Geheimklausel"), requests.exceptions.ConnectTimeout("x"),
    requests.exceptions.ConnectionError("Seefeldstrasse"), requests.exceptions.SSLError("x"),
]


@pytest.mark.parametrize("failure", FAILURES, ids=lambda f: str(getattr(f, "status_code", type(f).__name__)))
def test_each_refusal_and_failure_keeps_the_snippet_and_the_answer(key_dir, monkeypatch, split, failure):
    core = Core(answers={DOC_A: failure}, answer=json.dumps(
        {"statement": "Die Frist betraegt drei Monate.", "source": 1, "quote": "Kuendigungsfrist betraegt drei Monate"}))
    reply = _service(_client(key_dir, monkeypatch, core), split).answer("Welche Frist gilt?")
    assert reply.status == "answered"
    assert reply.passages[0].text == SNIPPET_A
    assert [s.quote for s in reply.sentences] == ["Kuendigungsfrist betraegt drei Monate"]
    assert core.read_ids()[0] == DOC_A and core.read_ids().count(DOC_A) == 1


# --- nothing of the text in a log line, at DEBUG (FU-46) ------------------------------------------------------------


LOGGERS = ("", "knovas_client", "assistant", "assistant.context", "assistant.service", "assistant.core",
           "assistant.check", "web_interface", "urllib3", "requests")


@pytest.fixture
def debug_everywhere():
    """Every logger that could see the text, set to DEBUG explicitly (FU-46: the app holds some at INFO)."""
    saved = [(logging.getLogger(name), logging.getLogger(name).level, logging.getLogger(name).disabled)
             for name in LOGGERS]
    for logger, _level, _disabled in saved:
        logger.setLevel(logging.DEBUG)
        logger.disabled = False
    yield
    for logger, level, disabled in saved:
        logger.setLevel(level)
        logger.disabled = disabled


def test_nothing_of_the_text_reaches_a_log_line_at_debug(key_dir, monkeypatch, split, caplog, debug_everywhere):
    long_b = TEXT_B + " " + " ".join(f"Seite{i}" for i in range(5000)) + " Geheimklausel am Ende."
    core = Core(texts={DOC_B: long_b},
                answers={DOC_X: _response(500, text="Geheimklausel")})
    hits = HITS + [_hit("Mandate/Mueller/fehlt.pdf", "Ein Satz der fehlt und Zwiebelmeier nicht nennt.",
                        "ffffffff-6666-4666-8666-000000000006")]
    core.hits = hits
    core.answers["ffffffff-6666-4666-8666-000000000006"] = _response(
        404, {"error_code": "NOT_FOUND", "message": "Geheimklausel Seefeldstrasse"})
    with caplog.at_level(logging.DEBUG):
        reply = _service(_client(key_dir, monkeypatch, core), split).answer("Welche Frist gilt im Mietvertrag?")
    assert reply.status == "answered"
    records = caplog.records
    assert any(r.name == "assistant.context" for r in records)
    everything = "\n".join(r.getMessage() + " " + (r.exc_text or "") + " " + str(r.__dict__) for r in records)
    for word in SECRET_WORDS + ("Kuendigungsfrist", "Massgebend", "Mietvertrag", "Mietzins", "Absatz1",
                                "Seite1", "Empfaenger"):
        assert word not in everything, word
    (line,) = [r.getMessage() for r in records if r.name == "assistant.context"]
    assert re.fullmatch(r"Knovas Assistent context: passages=3 documents=2 reads=\d+ parts=\d+ widened=2"
                        r"( [a-z_]+=\d+)+ ms=\d+", line)
    assert "not_visible=1" in line.split()


# --- the search page unchanged ----------------------------------------------------------------------------------------


def test_the_search_page_reads_no_document_text(key_dir, monkeypatch):
    core = Core()
    answer = _client(key_dir, monkeypatch, core).search_documents("Kuendigungsfrist", limit=10)
    assert [s["url"] for s in core.sent] == ["https://core.test/secured/query"]
    assert all("snippet" not in row for row in answer["results"])
    assert [row.get("document_uuid") for row in answer["results"]] == [DOC_A, DOC_X, DOC_B]


def test_only_the_assistants_reader_calls_document_text():
    src = Path(__file__).resolve().parents[1] / "src"
    callers = sorted(str(path.relative_to(src)).replace("\\", "/") for path in src.rglob("*.py")
                     if re.search(r"\.document_text\s*\(|[\"']document_text[\"']",
                                  path.read_text(encoding="utf-8")))
    assert callers == ["assistant/core.py"]
