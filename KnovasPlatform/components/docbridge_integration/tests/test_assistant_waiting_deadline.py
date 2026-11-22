"""PMVP-24: one deadline per answer (FU-08) and "slow reads as busy" (decision D1), on a fake clock.

The search with its retries and the generation end within ``ANSWER_DEADLINE_SECONDS`` (150) of the route's start;
the generation is not started with less than ``MIN_GENERATE_SECONDS`` (30) left; each attempt's timeout is at most
the time left and a retry happens only if time is left for it; a timeout of the search or the generation is busy.
The search page's own search is unchanged: three tries with ``http_read_timeout``.

Nothing waits for real: the fake Core and tenacity's sleep move a fake clock, which the ``Deadline`` reads. The
Platform's real client sends every request; only its HTTP session is the fake Core. This file is ASCII-only.
"""
from __future__ import annotations

import json
import logging
import types

import pytest
import requests

from assistant import core, providers
from assistant.core import ANSWER_DEADLINE_SECONDS, MIN_GENERATE_SECONDS, Deadline, make_generate, make_search
from assistant.errors import CoreBusy, CoreFailed
from assistant.service import AssistantService
from experiments.search import SearchIntegration
from identity.broker_key import load_or_create_signer
from knovas_client import MIN_ATTEMPT_SECONDS, KnovasAPIClient

TENANT = "tenant-a"
QUESTION = "Welche Kuendigungsfrist gilt fuer den Mietvertrag Hardturm?"
PASSAGE = "Die Kuendigungsfrist betraegt drei Monate auf Ende Juni."
ANSWER = json.dumps({"statement": "Die Frist betraegt drei Monate.", "source": 1,
                     "quote": "Kuendigungsfrist betraegt drei Monate"})
READ_TIMEOUT = 120


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
    "api.http_read_timeout": READ_TIMEOUT,
    "web.doc_fields.ui": "off",
}


class _User:
    id = "44444444-4444-4444-4444-444444444444"
    roles = ("member",)


class _Broker:
    def __init__(self, signer):
        self._signer = signer

    def current_user(self):
        return _User()

    def assertion_for(self, user):
        return self._signer.mint(subject=str(user.id), tenant=TENANT, groups=["g"], roles=list(user.roles))


class Clock:
    """A monotonic clock that moves only when told: by the fake Core, or by tenacity's sleep."""

    def __init__(self):
        self.now = 1_000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _response(status, payload, url):
    resp = requests.Response()
    resp.status_code = status
    resp.reason = "test"
    resp.url = url
    resp._content = json.dumps(payload).encode("utf-8")
    return resp


class Core:
    """One fake Knovas Core on the client's session.

    ``search`` and ``generate`` say what each request does, in turn: a number of seconds it takes before it
    answers; "timeout" (it takes the whole timeout it was given and then times out); or an HTTP status.
    """

    def __init__(self, clock, *, search=(5,), generate=(20,)):
        self.clock = clock
        self.search, self.generate = list(search), list(generate)
        self.sent = []

    def _next(self, plan):
        return plan.pop(0) if len(plan) > 1 else plan[0]

    def request(self, **kw):
        url, timeout = kw["url"], kw.get("timeout")
        kind = "query" if url.endswith("/secured/query") else "generate" if url.endswith("/secured/llm/generate") \
            else "other"
        self.sent.append({"kind": kind, "timeout": timeout, "at": self.clock.now})
        if kind == "other":
            raise AssertionError(f"unexpected request to {url}")
        step = self._next(self.search if kind == "query" else self.generate)
        if step == "timeout":
            self.clock.now += timeout
            raise requests.exceptions.ReadTimeout("read timed out")
        if isinstance(step, int) and step >= 400:
            return _response(step, {"status": "error", "error_code": "busy"}, url)
        self.clock.now += step
        if kind == "query":
            hit = {"pointer": "mandate/hardturm/vertrag.pdf", "cosine_similarity": 0.8, "snippet": PASSAGE,
                   "top_chunks": [{"chunk_uuid": "c-1", "page_number": 3, "sentence_number": 7,
                                   "snippet": PASSAGE}]}
            return _response(200, {"status": "success", "results": [hit], "result_count": 1}, url)
        return _response(200, {"status": "success", "message": "ok", "content": ANSWER, "model": "m",
                               "prompt_tokens": 1, "completion_tokens": 1}, url)

    def of(self, kind):
        return [s for s in self.sent if s["kind"] == kind]


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(KnovasAPIClient._make_request.retry, "sleep", clock.sleep)
    monkeypatch.delenv("SEMANTIX_HTTP_READ_TIMEOUT", raising=False)
    return clock


@pytest.fixture
def key_dir(tmp_path):
    path = tmp_path / "broker"
    path.mkdir()
    return path


@pytest.fixture
def split():
    return SearchIntegration(settings=types.SimpleNamespace(pointer_prefix="experiments"),
                             connection=lambda: None, current_user=lambda: None, enabled=False).split


def _client(key_dir, monkeypatch, fake_core):
    client = KnovasAPIClient(config_loader=_StubConfig(SECURED))
    client.attach_principal_broker(_Broker(load_or_create_signer(key_dir)))
    monkeypatch.setattr(client._session, "request", fake_core.request)
    return client


def _service(client, split, clock, *, generate=None):
    deadline = Deadline(clock=clock)
    return deadline, AssistantService(search=make_search(client, split=split, deadline=deadline),
                                      generate=generate or make_generate(client, deadline=deadline))


class ClaudeClient:
    """Anthropic's client as the provider builds it: records its timeout; answers or raises."""

    def __init__(self, built, outcome):
        self.built, self.outcome = built, outcome
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    def create(self, **request):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return types.SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5",
                                     usage=types.SimpleNamespace(input_tokens=1, output_tokens=1),
                                     content=[types.SimpleNamespace(type="text", text=ANSWER)])

    def close(self):
        pass


def _claude(clock, deadline, outcome=None, built=None):
    built = [] if built is None else built

    def factory(**kw):
        built.append(kw)
        return ClaudeClient(built, outcome)

    generate = core.in_time(providers.make_anthropic_generate(
        api_key="sk-ant-test", model=providers.DEFAULT_ANTHROPIC_MODEL, client_factory=factory,
        time_left=deadline.left), deadline)
    return generate, built


# --- the constants -----------------------------------------------------------------------------------------------


def test_the_deadline_is_under_the_web_servers_180_seconds():
    assert ANSWER_DEADLINE_SECONDS == 150 and MIN_GENERATE_SECONDS == 30
    assert ANSWER_DEADLINE_SECONDS + 5 < 180
    clock = Clock()
    deadline = Deadline(clock=clock)
    clock.now += 40
    assert (deadline.elapsed(), deadline.left()) == (40, 110)


# --- the search with its retries -------------------------------------------------------------------------------


def test_a_search_that_always_times_out_is_busy_within_the_deadline(key_dir, monkeypatch, split, clock):
    fake = Core(clock, search=["timeout"])
    client = _client(key_dir, monkeypatch, fake)
    deadline, service = _service(client, split, clock)
    reply = service.answer(QUESTION)
    assert reply.status == "busy" and reply.sentences == [] and reply.passages == []
    attempts = fake.of("query")
    # 120 s, a wait of 2 s, then the 28 s that are left; no third attempt: nothing would be left after its wait
    assert [a["timeout"] for a in attempts] == [READ_TIMEOUT, ANSWER_DEADLINE_SECONDS - READ_TIMEOUT - 2]
    assert deadline.elapsed() <= ANSWER_DEADLINE_SECONDS
    assert fake.of("generate") == []


def test_a_retry_is_made_only_with_time_left_for_it(key_dir, monkeypatch, split, clock):
    fake = Core(clock, search=["timeout"])
    client = _client(key_dir, monkeypatch, fake)
    deadline = Deadline(seconds=130, clock=clock)
    with pytest.raises(CoreBusy) as caught:
        make_search(client, split=split, deadline=deadline)(QUESTION, 5)
    assert caught.value.reason == "search_timeout" and caught.value.__context__ is None
    # after 120 s and a wait of 2 s, 8 s would be left: enough for one more try of at most 8 s
    assert [a["timeout"] for a in fake.of("query")] == [120, 8]
    assert deadline.left() >= 0


def test_with_no_time_left_nothing_is_sent(key_dir, monkeypatch, split, clock):
    fake = Core(clock)
    client = _client(key_dir, monkeypatch, fake)
    deadline = Deadline(clock=clock)
    clock.now += ANSWER_DEADLINE_SECONDS
    with pytest.raises(CoreBusy):
        make_search(client, split=split, deadline=deadline)(QUESTION, 5)
    assert fake.sent == []


def test_a_search_of_125_seconds_leaves_no_time_to_generate(key_dir, monkeypatch, split, clock):
    fake = Core(clock, search=[125])
    deadline, service = _service(_client(key_dir, monkeypatch, fake), split, clock)
    reply = service.answer(QUESTION)
    assert reply.status == "busy"
    assert fake.of("generate") == []
    assert deadline.left() == pytest.approx(25)


def test_a_search_that_answers_in_time_is_answered(key_dir, monkeypatch, split, clock):
    fake = Core(clock, search=[10], generate=[30])
    _, service = _service(_client(key_dir, monkeypatch, fake), split, clock)
    reply = service.answer(QUESTION)
    assert reply.status == "answered" and len(reply.sentences) == 1


# --- the generation's timeout --------------------------------------------------------------------------------------


def test_after_a_search_of_10_seconds_the_core_waits_120_and_claude_60(key_dir, monkeypatch, split, clock):
    fake = Core(clock, search=[10], generate=[5])
    deadline, service = _service(_client(key_dir, monkeypatch, fake), split, clock)
    service.answer(QUESTION)
    assert [g["timeout"] for g in fake.of("generate")] == [120]

    fake2 = Core(clock, search=[10])
    client2 = _client(key_dir, monkeypatch, fake2)
    deadline2 = Deadline(clock=clock)
    generate, built = _claude(clock, deadline2)
    reply = AssistantService(search=make_search(client2, split=split, deadline=deadline2),
                             generate=generate).answer(QUESTION)
    assert reply.status == "answered"
    assert [b["timeout"] for b in built] == [60.0]


def test_the_generations_timeout_is_never_more_than_the_time_left(key_dir, monkeypatch, split, clock):
    fake = Core(clock, search=[100], generate=[5])
    _, service = _service(_client(key_dir, monkeypatch, fake), split, clock)
    service.answer(QUESTION)
    assert [g["timeout"] for g in fake.of("generate")] == [pytest.approx(50)]

    fake2 = Core(clock, search=[110])
    client2 = _client(key_dir, monkeypatch, fake2)
    deadline2 = Deadline(clock=clock)
    generate, built = _claude(clock, deadline2)
    AssistantService(search=make_search(client2, split=split, deadline=deadline2), generate=generate).answer(QUESTION)
    assert [b["timeout"] for b in built] == [pytest.approx(40)]


def test_the_generation_is_not_started_with_less_than_30_seconds_left(clock):
    deadline = Deadline(clock=clock)
    called = []
    generate = core.in_time(lambda **kw: called.append(kw) or ANSWER, deadline)
    clock.now += ANSWER_DEADLINE_SECONDS - MIN_GENERATE_SECONDS + 0.5
    with pytest.raises(CoreBusy) as caught:
        generate(system_instruction="s", messages=[], max_tokens=10)
    assert caught.value.reason == "no_time_to_generate" and called == []
    clock.now -= 1
    assert generate(system_instruction="s", messages=[], max_tokens=10) == ANSWER


def test_a_core_generation_that_times_out_is_busy(key_dir, monkeypatch, split, clock):
    fake = Core(clock, search=[10], generate=["timeout"])
    deadline, service = _service(_client(key_dir, monkeypatch, fake), split, clock)
    reply = service.answer(QUESTION)
    assert reply.status == "busy" and reply.sentences == [] and reply.passages == []
    assert len(fake.of("generate")) == 1                                        # never retried
    assert deadline.elapsed() <= ANSWER_DEADLINE_SECONDS


def test_a_claude_generation_that_times_out_is_busy(key_dir, monkeypatch, split, clock):
    import anthropic
    import httpx

    fake = Core(clock, search=[10])
    deadline = Deadline(clock=clock)
    timeout = anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.test/v1/messages"))
    generate, built = _claude(clock, deadline, outcome=timeout)
    reply = AssistantService(search=make_search(_client(key_dir, monkeypatch, fake), split=split, deadline=deadline),
                             generate=generate).answer(QUESTION)
    assert reply.status == "busy" and len(built) == 1


def test_without_a_deadline_a_timeout_is_busy_all_the_same(key_dir, monkeypatch, split, clock):
    fake = Core(clock, search=[1], generate=["timeout"])
    client = _client(key_dir, monkeypatch, fake)
    reply = AssistantService(search=make_search(client, split=split), generate=make_generate(client)).answer(QUESTION)
    assert reply.status == "busy"
    assert [g["timeout"] for g in fake.of("generate")] == [120]                # the Core's own, unbounded


# --- busy at once, failed otherwise ------------------------------------------------------------------------------


@pytest.mark.parametrize("status, wanted", [(429, "busy"), (503, "busy"), (500, "failed"), (400, "failed")])
def test_the_cores_generation_429_and_503_are_busy_at_once(key_dir, monkeypatch, split, clock, status, wanted):
    fake = Core(clock, search=[1], generate=[status])
    _, service = _service(_client(key_dir, monkeypatch, fake), split, clock)
    assert service.answer(QUESTION).status == wanted
    assert len(fake.of("generate")) == 1


@pytest.mark.parametrize("status, wanted", [(429, "busy"), (503, "busy"), (500, "failed")])
def test_the_cores_search_429_and_503_are_busy_at_once(key_dir, monkeypatch, split, clock, status, wanted):
    fake = Core(clock, search=[status])
    _, service = _service(_client(key_dir, monkeypatch, fake), split, clock)
    assert service.answer(QUESTION).status == wanted
    assert len(fake.of("query")) == 1 and fake.of("generate") == []


@pytest.mark.parametrize("status, wanted", [(429, "busy"), (529, "busy"), (500, "failed"), (401, "failed")])
def test_claudes_429_and_529_are_busy_at_once(key_dir, monkeypatch, split, clock, status, wanted):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.test/v1/messages")
    response = httpx.Response(status, request=request, json={"type": "error", "error": {"type": "x", "message": "m"}})
    error = anthropic.APIStatusError("refused", response=response, body=None)
    if status == 429:
        error = anthropic.RateLimitError("busy", response=response, body=None)
    elif status == 401:
        error = anthropic.AuthenticationError("key", response=response, body=None)
    fake = Core(clock, search=[1])
    deadline = Deadline(clock=clock)
    generate, built = _claude(clock, deadline, outcome=error)
    reply = AssistantService(search=make_search(_client(key_dir, monkeypatch, fake), split=split, deadline=deadline),
                             generate=generate).answer(QUESTION)
    assert reply.status == wanted and len(built) == 1


# --- the search page is unchanged ---------------------------------------------------------------------------------


def test_the_search_pages_own_search_still_tries_three_times_with_its_read_timeout(key_dir, monkeypatch, clock):
    import tenacity

    fake = Core(clock, search=["timeout"])
    client = _client(key_dir, monkeypatch, fake)
    with pytest.raises(tenacity.RetryError):
        client.search_documents(QUESTION, limit=5)
    assert [a["timeout"] for a in fake.of("query")] == [READ_TIMEOUT] * 3
    assert clock.slept == [2, 4]


def test_the_bound_is_per_thread_and_ends_with_its_block(key_dir, monkeypatch, clock):
    import threading

    client = _client(key_dir, monkeypatch, Core(clock))
    seen = {}
    with client.within_time(lambda: 42.0):
        seen["inside"] = client._time_left()
        worker = threading.Thread(target=lambda: seen.setdefault("other_thread", client._time_left()))
        worker.start()
        worker.join()
    seen["after"] = client._time_left()
    assert seen == {"inside": 42.0, "other_thread": None, "after": None}


def test_outside_the_bound_the_stop_never_cuts_a_retry(clock):
    from knovas_client import _out_of_time

    client = KnovasAPIClient(config_loader=_StubConfig(SECURED))
    state = types.SimpleNamespace(args=(client,), upcoming_sleep=30)
    assert _out_of_time(state) is False
    with client.within_time(lambda: MIN_ATTEMPT_SECONDS + 30 - 0.1):
        assert _out_of_time(state) is True
    with client.within_time(lambda: MIN_ATTEMPT_SECONDS + 30):
        assert _out_of_time(state) is False


# --- logs (FU-46) ---------------------------------------------------------------------------------------------------


def test_the_deadlines_log_lines_hold_durations_and_step_names_only(key_dir, monkeypatch, split, clock, caplog):
    for name in ("assistant", "web_interface", "knovas_client"):
        caplog.set_level(logging.DEBUG, logger=name)
    caplog.set_level(logging.DEBUG)
    _, search_out = _service(_client(key_dir, monkeypatch, Core(clock, search=["timeout"])), split, clock)
    search_out.answer(QUESTION)
    _, slow = _service(_client(key_dir, monkeypatch, Core(clock, search=[125])), split, clock)
    slow.answer(QUESTION)
    _, gen_out = _service(_client(key_dir, monkeypatch, Core(clock, search=[5], generate=["timeout"])), split,
                          clock)
    gen_out.answer(QUESTION)
    deadline_lines = [r.getMessage() for r in caplog.records if "deadline" in r.getMessage()]
    assert [line.split(" step=")[1].split(" ")[0:3] for line in deadline_lines] == [
        ["search", "ran", "out"], ["generation", "not", "started"], ["generation", "ran", "out"]]
    for line in deadline_lines:
        assert set(word.split("=")[0] for word in line.split() if "=" in word) <= {"step", "elapsed_ms", "left_ms",
                                                                                   "generate_ms"}
    joined = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (QUESTION, PASSAGE, "Hardturm", "hardturm", "Kuendigungsfrist", "drei Monate"):
        assert secret not in joined, secret
