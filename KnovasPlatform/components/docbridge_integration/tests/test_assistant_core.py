"""PMVP-20: the assistant's callables built from KnovasAPIClient (assistant.core).

search: /api/search's path (search_plan / run_search -> search_documents ->
POST /secured/query) as the signed-in person, passage text kept, experiment
documents taken out, legacy mode refused. generate: llm_generate, the text
only. And the whole service over one fake Core: both calls carry the same
person's assertion, and the prompt holds what that search returned.
"""
from __future__ import annotations

import json
import types

import pytest
import requests

from assistant.core import DEFAULT_TEMPERATURE, make_generate, make_search
from assistant.errors import CoreBusy, CoreFailed
from assistant.service import AssistantService
from experiments.search import SearchIntegration
from identity.assertion import AssertionVerifier
from identity.broker_key import load_or_create_signer, public_pem
from knovas_client import ASSERTION_FIELD, KnovasAPIClient

TENANT = "tenant-a"
USER_ID = "33333333-3333-3333-3333-333333333333"


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
    def __init__(self, signer):
        self._signer = signer

    def current_user(self):
        return _User()

    def assertion_for(self, user):
        return self._signer.mint(subject=str(user.id), tenant=TENANT, groups=["litigation"],
                                 roles=list(user.roles))


def _response(status, payload, url):
    resp = requests.Response()
    resp.status_code = status
    resp.reason = "test"
    resp.url = url
    resp._content = json.dumps(payload).encode("utf-8")
    return resp


def _hit(pointer, snippet=None, score=0.8):
    hit = {"pointer": pointer, "cosine_similarity": score,
           "top_chunks": [{"chunk_uuid": f"c-{pointer}", "page_number": 4, "sentence_number": 9}]}
    if snippet is not None:
        hit["snippet"] = snippet
        hit["top_chunks"][0]["snippet"] = snippet
    return hit


HITS = [
    _hit("Mandate/Mueller/vertrag.pdf", "Die Kuendigungsfrist betraegt drei Monate."),
    _hit("experiments/marketing/MKT-58", "Hypothese: der Rabatt wirkt."),
    _hit("Mandate/Mueller/brief.pdf", "Die Frist beginnt mit dem Zugang."),
    _hit("Mandate/Mueller/ohne-text.pdf"),
]


class _Core:
    """One fake Knovas: /secured/query and /secured/llm/generate."""

    def __init__(self, hits=HITS, query_status=200, answer="Drei Monate [1]. Ab Zugang [2]."):
        self.hits, self.query_status, self.answer = hits, query_status, answer
        self.sent = []

    def request(self, **kw):
        self.sent.append(kw)
        url = kw["url"]
        if url.endswith("/secured/query"):
            if self.query_status != 200:
                return _response(self.query_status, {"status": "error"}, url)
            return _response(200, {"status": "success", "results": self.hits,
                                   "result_count": len(self.hits)}, url)
        if url.endswith("/secured/llm/generate"):
            return _response(200, {"status": "success", "message": "Generation complete",
                                   "content": self.answer, "model": "m", "prompt_tokens": 1,
                                   "completion_tokens": 1}, url)
        raise AssertionError(f"unexpected request to {url}")

    def bodies(self, endpoint):
        return [s["json"] for s in self.sent if s["url"].endswith(endpoint)]


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


def _client(key_dir, monkeypatch, core, config=SECURED):
    client = KnovasAPIClient(config_loader=_StubConfig(config))
    client.attach_principal_broker(_Broker(load_or_create_signer(key_dir)))
    monkeypatch.setattr(client._session, "request", core.request)
    return client


@pytest.fixture
def split():
    return SearchIntegration(settings=types.SimpleNamespace(pointer_prefix="experiments"),
                             connection=lambda: None, current_user=lambda: None,
                             enabled=False).split


# --- search ---------------------------------------------------------------------------------------


def test_search_is_the_persons_secured_query_with_passage_text(key_dir, monkeypatch, split,
                                                               verifier):
    core = _Core()
    rows = make_search(_client(key_dir, monkeypatch, core), split=split)("Welche Frist?", 8)
    (body,) = core.bodies("/secured/query")
    assert {k: v for k, v in body.items() if k != ASSERTION_FIELD} == {
        "Input": "Welche Frist?", "limit": 8}
    assert verifier.verify(body[ASSERTION_FIELD], tenant=TENANT).subject == USER_ID
    assert [r["doc_id"] for r in rows] == [
        "Mandate/Mueller/vertrag.pdf", "Mandate/Mueller/brief.pdf", "Mandate/Mueller/ohne-text.pdf"]
    assert [r.get("snippet") for r in rows] == [
        "Die Kuendigungsfrist betraegt drei Monate.", "Die Frist beginnt mit dem Zugang.", None]


def test_experiment_documents_are_taken_out_as_on_the_search_page(key_dir, monkeypatch, split):
    rows = make_search(_client(key_dir, monkeypatch, _Core()), split=split)("Rabatt", 8)
    assert all(not r["doc_id"].startswith("experiments/") for r in rows)
    assert all("Hypothese" not in (r.get("snippet") or "") for r in rows)


def test_the_split_cannot_be_left_out(key_dir, monkeypatch):
    client = _client(key_dir, monkeypatch, _Core())
    with pytest.raises(TypeError):
        make_search(client)  # noqa - the keyword is required
    with pytest.raises(TypeError):
        make_search(client, split=None)


def test_without_the_secured_api_nothing_is_searched(key_dir, monkeypatch, split):
    core = _Core()
    legacy = {k: v for k, v in SECURED.items() if not k.endswith(("cert_path", "key_path"))}
    legacy["api.base_url"] = "http://core.test"
    search = make_search(_client(key_dir, monkeypatch, core, legacy), split=split)
    with pytest.raises(CoreFailed):
        search("Frist?", 8)
    assert core.sent == []


@pytest.mark.parametrize("status, error", [(429, CoreBusy), (503, CoreBusy), (500, CoreFailed),
                                           (403, CoreFailed)])
def test_a_refused_search_is_busy_or_failed(key_dir, monkeypatch, split, status, error):
    search = make_search(_client(key_dir, monkeypatch, _Core(query_status=status)), split=split)
    with pytest.raises(error) as caught:
        search("Frist?", 8)
    assert caught.value.status == status
    if error is CoreFailed:
        assert not isinstance(caught.value, CoreBusy)


def test_the_user_key_is_asked_for_per_search(key_dir, monkeypatch, split):
    asked = []

    def user_key():
        asked.append(1)
        return USER_ID

    search = make_search(_client(key_dir, monkeypatch, _Core()), split=split, user_key=user_key)
    search("eins", 3)
    search("zwei", 3)
    assert len(asked) == 2


# --- generate -------------------------------------------------------------------------------------


def test_generate_returns_the_text_only(key_dir, monkeypatch):
    core = _Core(answer="Antwort [1].")
    generate = make_generate(_client(key_dir, monkeypatch, core))
    text = generate(system_instruction="Regeln", messages=[{"role": "user", "content": "F?"}],
                    max_tokens=900)
    assert text == "Antwort [1]."
    (body,) = core.bodies("/secured/llm/generate")
    assert body["max_tokens"] == 900
    assert body["temperature"] == DEFAULT_TEMPERATURE
    assert body["system_instruction"] == "Regeln"


# --- the whole service over one Core --------------------------------------------------------------


def test_the_service_answers_from_the_persons_own_search(key_dir, monkeypatch, split, verifier):
    core = _Core()
    client = _client(key_dir, monkeypatch, core)
    service = AssistantService(search=make_search(client, split=split),
                               generate=make_generate(client))
    reply = service.answer("Welche Frist gilt?")

    assert reply.status == "answered"
    assert [(s.text, s.refs) for s in reply.sentences] == [("Drei Monate.", (1,)),
                                                          ("Ab Zugang.", (2,))]
    assert [(p.number, p.pointer, p.page) for p in reply.passages] == [
        (1, "Mandate/Mueller/vertrag.pdf", 4), (2, "Mandate/Mueller/brief.pdf", 4)]

    (query,) = core.bodies("/secured/query")
    (generation,) = core.bodies("/secured/llm/generate")
    prompt = generation["messages"][-1]["content"]
    assert "[1] vertrag, page 4\nDie Kuendigungsfrist betraegt drei Monate." in prompt
    assert "[2] brief, page 4\nDie Frist beginnt mit dem Zugang." in prompt
    assert "[3]" not in prompt and "Hypothese" not in prompt
    subjects = {verifier.verify(b[ASSERTION_FIELD], tenant=TENANT).subject
                for b in (query, generation)}
    assert subjects == {USER_ID}


def test_the_service_says_busy_when_the_core_is(key_dir, monkeypatch, split):
    core = _Core(query_status=429)
    client = _client(key_dir, monkeypatch, core)
    reply = AssistantService(search=make_search(client, split=split),
                             generate=make_generate(client)).answer("Frist?")
    assert reply.status == "busy"
    assert core.bodies("/secured/llm/generate") == []


def test_a_reference_opens_the_page_its_text_is_on(key_dir, monkeypatch, split):
    """Rework 1, rule 5: the first chunk has no snippet (an auto summary ranked first)."""
    hit = {"pointer": "Mandate/Mueller/vertrag.pdf", "cosine_similarity": 0.9,
           "snippet": "Die Frist beginnt mit dem Zugang.",
           "top_chunks": [{"chunk_uuid": "c-0", "page_number": 1, "sentence_number": 1},
                          {"chunk_uuid": "c-1", "page_number": 7, "sentence_number": 31,
                           "snippet": "Die Frist beginnt mit dem Zugang."}]}
    core = _Core(hits=[hit], answer="Ab Zugang [1].")
    client = _client(key_dir, monkeypatch, core)
    reply = AssistantService(search=make_search(client, split=split),
                             generate=make_generate(client)).answer("Wann beginnt die Frist?")
    assert reply.status == "answered"
    assert [(p.page, p.sentence) for p in reply.passages] == [(7, 31)]
    (generation,) = core.bodies("/secured/llm/generate")
    assert "[1] vertrag, page 7\nDie Frist beginnt mit dem Zugang." in generation["messages"][-1]["content"]
