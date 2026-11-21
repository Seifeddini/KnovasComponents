"""RB-7: the delivery of external agents' claims to the Core's MCP gateway, beyond the frozen acceptance file.

identity/agent_delivery.py: the switch read on every call; a Core that refuses (409 ``not_brokered``, 404 without
CR-R5, no connection) logged by id, status and class, never raised and never with a claim; one agent's pushes one
at a time (a push that waits for the agent's lock reads the agent after it); the job's lock (two workers, one
pusher), its 60-second cadence and no push at start. The client's two calls carry exactly ``{"claims": [...]}`` and
no person's assertion. Work order docs/platform-mvp/work-orders/RB-6-7.md § 4. Nothing reaches a tenant: the Core
is a recording fake.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
import requests

import conftest
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity import agent_delivery  # noqa: E402
from identity.agent_delivery import (  # noqa: E402
    CLAIM_LIFETIME_SECONDS,
    POOL_SIZE,
    SYNC_INTERVAL_SECONDS,
    AgentDelivery,
    AgentSyncJob,
    agent_lock,
    install_agent_delivery,
)
from identity.agents import AgentStore  # noqa: E402
from identity.assertion import AssertionVerifier  # noqa: E402
from identity.broker_key import load_or_create_signer, public_pem  # noqa: E402
from identity.roles import RoleStore  # noqa: E402
from knovas_client import ASSERTION_FIELD, KnovasAPIClient  # noqa: E402

TENANT = "tenant-a"


def _connect(conn):
    import psycopg

    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    other = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    other.execute(f'SET search_path TO "{schema}"')
    return other


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    response._content = b'{"status": "error", "error_code": "not_brokered"}'
    return requests.exceptions.HTTPError(f"{status} for url", response=response)


class FakeCore:
    """put_agent_claims / delete_agent_claims as the delivery calls them, recorded; ``fail`` raises instead."""

    def __init__(self):
        self.calls = []
        self.fail = None

    def put_agent_claims(self, agent_id, claims, *, timeout=None):
        self.calls.append(("PUT", str(agent_id), list(claims)))
        if self.fail is not None:
            raise self.fail

    def delete_agent_claims(self, agent_id, *, timeout=None):
        self.calls.append(("DELETE", str(agent_id), None))
        if self.fail is not None:
            raise self.fail


@pytest.fixture
def key_dir(tmp_path):
    path = tmp_path / "broker"
    path.mkdir()
    load_or_create_signer(path)
    return path


@pytest.fixture
def verifier(key_dir):
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    return AssertionVerifier({kid: public_pem(key_dir)})


@pytest.fixture
def switch():
    return {"value": "an"}


@pytest.fixture
def opened(platform_db):
    """The connections the delivery opened, closed after the test."""
    made = []
    yield made
    for conn in made:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def core():
    return FakeCore()


@pytest.fixture
def delivery(platform_db, key_dir, switch, core, opened):
    def connect():
        conn = _connect(platform_db)
        opened.append(conn)
        return conn

    return AgentDelivery(client=core, signer=load_or_create_signer(key_dir), tenant_id=TENANT,
                         setting=lambda: switch["value"], connect=connect)


@pytest.fixture
def agents(platform_db):
    return AgentStore(platform_db)


# --- the switch, read on every call ----------------------------------------------------------------------------------


def test_the_switch_is_read_on_every_call(delivery, agents, switch, core, opened):
    agent = agents.create_agent(name="Claude Code")
    switch["value"] = "aus"
    assert delivery.sync() is None and delivery.deliver(agent.id) is None
    assert core.calls == [] and opened == []                            # off: no connection, no Core
    switch["value"] = "an"
    assert delivery.sync() == {"sent": 1, "dropped": 0, "failed": 0}
    switch["value"] = "aus"
    assert delivery.sync() is None
    assert [c[0] for c in core.calls] == ["PUT"]


def test_a_switch_that_cannot_be_read_is_off(platform_db, key_dir, core, caplog):
    def broken():
        raise RuntimeError("no settings")

    delivery = AgentDelivery(client=core, signer=load_or_create_signer(key_dir), tenant_id=TENANT,
                             setting=broken, connect=lambda: pytest.fail("no connection while off"))
    assert delivery.sync() is None and core.calls == []
    assert "RuntimeError" in caplog.text


def test_the_pool_is_twenty_signed_claims_of_300_seconds(delivery, agents, core, verifier, platform_db):
    role = RoleStore(platform_db).create_role(name="Prozess", functions={"approver"}, core_group_id="g-1")
    agent = agents.create_agent(name="Claude Code")
    agents.add_to_role(role.id, agent.id)
    assert delivery.deliver(agent.id) == "sent"
    ((method, agent_id, claims),) = core.calls
    assert (method, agent_id, len(claims)) == ("PUT", str(agent.id), POOL_SIZE)
    verified = [verifier.verify(c, tenant=TENANT) for c in claims]
    assert {(c.subject, c.groups, c.roles) for c in verified} == {(str(agent.id), ("g-1",), ("approver",))}
    assert len({c.jti for c in verified}) == POOL_SIZE
    assert {c.expires_at - c.issued_at for c in verified} == {CLAIM_LIFETIME_SECONDS}


def test_the_assistant_and_an_unknown_agent_are_never_delivered(delivery, agents, core):
    assert delivery.deliver(agents.assistant().id) == "skipped"
    assert delivery.deliver(uuid.uuid4()) == "skipped"
    assert delivery.deliver("kein-uuid") == "skipped"
    assert delivery.sync() == {"sent": 0, "dropped": 0, "failed": 0}
    assert core.calls == []


# --- a Core that refuses -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("failure, status", [(_http_error(409), "409"), (_http_error(404), "404"),
                                             (_http_error(503), "503"),
                                             (requests.exceptions.ConnectionError("down"), "-")])
def test_a_refusing_core_is_logged_by_id_status_and_class_and_never_raised(delivery, agents, core, caplog,
                                                                         failure, status):
    caplog.set_level(logging.DEBUG)
    live = agents.create_agent(name="Agent Zwiebel")
    gone = agents.create_agent(name="Weg Zwiebel")
    agents.revoke_agent(gone.id)
    core.fail = failure
    assert delivery.sync() == {"sent": 0, "dropped": 0, "failed": 2}
    assert delivery.deliver(live.id) == "failed"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3
    for line in warnings:
        assert f"HTTP {status}" in line and type(failure).__name__ in line
    assert any(str(live.id) in line for line in warnings) and any(str(gone.id) in line for line in warnings)
    text = "\n".join(r.getMessage() for r in caplog.records)
    claims = [t for method, _, pool in core.calls if pool for t in pool]
    assert claims and not [t for t in claims if t.split(".")[2] in text]
    assert "zwiebel" not in text.lower() and "not_brokered" not in text


def test_a_database_failure_stops_the_sync_without_raising(platform_db, key_dir, core, switch, caplog):
    def connect():
        raise OSError("no database")

    delivery = AgentDelivery(client=core, signer=load_or_create_signer(key_dir), tenant_id=TENANT,
                             setting=lambda: switch["value"], connect=connect)
    assert delivery.sync() is None and delivery.deliver(uuid.uuid4()) == "failed"
    assert "OSError" in caplog.text and core.calls == []


# --- one agent at a time ---------------------------------------------------------------------------------------------


def test_a_push_waits_for_the_agents_lock_and_reads_the_agent_after_it(delivery, agents, core, platform_db):
    agent = agents.create_agent(name="Claude Code")
    holder = _connect(platform_db)
    outcome = {}
    try:
        with agent_lock(holder, agent.id):                               # a push of the minute, still running
            racer = threading.Thread(target=lambda: outcome.update(result=delivery.deliver(agent.id)))
            racer.start()
            time.sleep(0.5)
            assert racer.is_alive() and core.calls == [], "the push did not wait for the agent's lock"
            agents.revoke_agent(agent.id)                                # committed while the push waits
        racer.join(10)
    finally:
        holder.close()
    assert outcome == {"result": "dropped"}
    assert core.calls == [("DELETE", str(agent.id), None)]


# --- the job -------------------------------------------------------------------------------------------------------


def _job(platform_db, opened, pushed, name, lock_name, **kwargs):
    def connect():
        conn = _connect(platform_db)
        opened.append(conn)
        return conn

    return AgentSyncJob(sync=lambda conn: pushed.append(name), connect=connect, enabled=lambda: True,
                        lock_name=lock_name, **kwargs)


def test_two_workers_one_pushes_and_the_other_takes_over_when_it_ends(platform_db, opened):
    lock_name = f"test.agents.{uuid.uuid4().hex}"
    pushed = []
    first, second = (_job(platform_db, opened, pushed, n, lock_name) for n in ("first", "second"))
    for _ in range(3):
        outcomes = [first.run_once(), second.run_once()]
        assert outcomes == [True, False]
    assert pushed == ["first"] * 3 and first.leader and not second.leader
    first._drop()                                                        # the worker ends: its lock with it
    assert second.run_once() is True and first.run_once() is False
    assert pushed == ["first"] * 3 + ["second"]


def test_the_job_ticks_every_60_seconds_and_never_at_the_start(platform_db, opened):
    waits, pushed = [], []
    answers = iter([False, False, True])                                  # two ticks, then stopped

    def wait(seconds):
        waits.append((seconds, len(pushed)))
        return next(answers)

    job = _job(platform_db, opened, pushed, "job", f"test.agents.{uuid.uuid4().hex}", wait=wait)
    job.run()
    assert SYNC_INTERVAL_SECONDS == 60
    assert waits == [(60.0, 0), (60.0, 1), (60.0, 2)]                     # the first wait comes before any push
    assert pushed == ["job", "job"]
    assert job._conn is None and not job.leader                          # the thread's end closes its connection


def test_switched_off_the_job_holds_no_connection_and_no_lock(platform_db, opened):
    on = {"value": True}
    pushed = []
    job = _job(platform_db, opened, pushed, "job", f"test.agents.{uuid.uuid4().hex}")
    job._enabled = lambda: on["value"]
    assert job.run_once() is True and job._conn is not None
    on["value"] = False
    assert job.run_once() is False and job._conn is None and not job.leader
    assert pushed == ["job"]


def test_a_test_app_ends_the_job_at_its_first_tick(platform_db, opened):
    pushed = []
    job = _job(platform_db, opened, pushed, "job", f"test.agents.{uuid.uuid4().hex}",
               keep_running=lambda: False, wait=lambda seconds: False)
    job.run()
    assert pushed == [] and opened == []


def test_a_failing_tick_is_logged_and_the_next_one_reconnects(platform_db, opened, caplog):
    calls = []

    def sync(conn):
        calls.append(conn)
        if len(calls) == 1:
            raise RuntimeError("boom")

    def connect():
        conn = _connect(platform_db)
        opened.append(conn)
        return conn

    job = AgentSyncJob(sync=sync, connect=connect, enabled=lambda: True, lock_name=f"test.agents.{uuid.uuid4().hex}")
    assert job.run_once() is False and "RuntimeError" in caplog.text and "boom" not in caplog.text
    assert job.run_once() is True and len(opened) == 2


def test_install_sets_the_seam_and_starts_the_job(platform_db):
    app = SimpleNamespace(extensions={}, testing=False)
    gate = SimpleNamespace(open_connection=lambda: _connect(platform_db))
    settings = SimpleNamespace(value=lambda key: "aus")
    install_agent_delivery(app, gate=gate, client=FakeCore(), signer=None, tenant_id=TENANT, settings=settings)
    seam = app.extensions["agents"]
    try:
        assert {"sync", "deliver", "on", "job"} <= set(seam)
        assert seam["on"]() is False and seam["sync"]() is None
        job = seam["job"]
        assert isinstance(job, AgentSyncJob) and job._thread.daemon and job._thread.is_alive()
        assert job.interval == 60 and job.lock_name == agent_delivery.LEADER_LOCK
    finally:
        seam["job"].stop()
    seam["job"]._thread.join(5)
    assert not seam["job"]._thread.is_alive()


# --- what the client sends -----------------------------------------------------------------------------------------


class _Wire:
    def __init__(self):
        self.sent = []

    def request(self, method=None, url=None, json=None, params=None, **kwargs):
        self.sent.append({"method": method, "url": url, "json": json, "params": params})
        response = requests.Response()
        response.status_code = 204
        response._content = b""
        return response


def test_the_client_sends_exactly_the_claims_and_no_persons_assertion(key_dir):
    class Config:
        def __init__(self, values):
            self._v = values

        def get(self, key, default=None):
            return self._v.get(key, default)

        def get_bool(self, key, default=False):
            return bool(self._v.get(key, default))

        def get_int(self, key, default=0):
            return int(self._v.get(key, default))

    class Broker:                                                         # a person is signed in
        def current_user(self):
            return SimpleNamespace(id="p-1", roles=("admin",))

        def assertion_for(self, user):
            raise AssertionError("the push must not sign the person")

    client = KnovasAPIClient(config_loader=Config({"api.base_url": "http://core.test", "api.customer_id": TENANT}))
    client._request_interval = 0
    client._session = _Wire()
    client.attach_principal_broker(Broker())
    client.put_agent_claims("a-1", ["x.y.z", "u.v.w"])
    client.delete_agent_claims("a-1")
    put, delete = client._session.sent
    assert (put["method"], put["url"], put["json"]) == ("PUT", "http://core.test/secured/agents/a-1/claims",
                                                        {"claims": ["x.y.z", "u.v.w"]})
    assert (delete["method"], delete["url"], delete["json"]) == ("DELETE",
                                                                 "http://core.test/secured/agents/a-1/claims", None)
    assert not put["params"] and not delete["params"] and ASSERTION_FIELD not in put["json"]


def test_a_push_gives_up_on_a_held_lock_after_its_wait(platform_db, agents):
    agent = agents.create_agent(name="Claude Code")
    holder, other = _connect(platform_db), _connect(platform_db)
    try:
        with agent_lock(holder, agent.id):
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                with agent_lock(other, agent.id, wait=0.3):
                    pass
            assert time.monotonic() - started < 5
        with agent_lock(other, agent.id, wait=0.3):                      # released: taken at once
            pass
    finally:
        holder.close()
        other.close()


def test_a_claim_that_cannot_be_signed_drops_the_pool_and_the_others_still_go(platform_db, key_dir, core, switch,
                                                                              agents, caplog, monkeypatch):
    first, second = agents.create_agent(name="Erster"), agents.create_agent(name="Zweiter")
    real = load_or_create_signer(key_dir)

    class Signer:
        def mint(self, **kwargs):
            if kwargs["subject"] == str(first.id):
                raise RuntimeError("no key")
            return real.mint(**kwargs)

    delivery = AgentDelivery(client=core, signer=Signer(), tenant_id=TENANT, setting=lambda: switch["value"],
                             connect=lambda: _connect(platform_db))
    assert delivery.sync() == {"sent": 1, "dropped": 1, "failed": 0}
    assert {(m, a) for m, a, _ in core.calls} == {("DELETE", str(first.id)), ("PUT", str(second.id))}
    assert "RuntimeError" in caplog.text and "no key" not in caplog.text
