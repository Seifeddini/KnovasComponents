"""Knovas Link speaks connector protocol 2.0 behind LINK_PROTOCOL_V2 (draft; BET-2026-11-write-back A-5; WB-21, WB-06,
WB-25 site halves; SPEC-write-back -10, -12, -15, -21, -22, -23, -26; GI-CONN-05, GI-CONN-08).

Link runs between the fake Platform and the fake secure-api (conftest), with a v2 connector behind it: the connector
kit's example connector over its fake record source, driven through Link's local API as a real connector would be.
The kit (KnovasConnectorKit) is used only here, as the test's connector and the Brain's signer; Link imports nothing
from it. The source kind and names are made up.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import uuid
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from conftest import TENANT, deadline_in, eventually, register

KIT = Path(__file__).resolve().parents[2] / "KnovasConnectorKit" / "src"
sys.path.insert(0, str(KIT))

from knovas_connector_kit.canonical import params_hash  # noqa: E402
from knovas_connector_kit.example import ExampleRecordsConnector, FakeRecordSource  # noqa: E402
from knovas_connector_kit.gate import LinkGate  # noqa: E402
from knovas_connector_kit.permits import KeyRing as KitRing  # noqa: E402
from knovas_connector_kit.permits import Signer  # noqa: E402

from knovas_link import contracts  # noqa: E402
from knovas_link.errors import StartupError  # noqa: E402
from knovas_link.permits import KeyRing  # noqa: E402

SOURCE = "records"
CONNECTOR = "records-connector"
SERIAL_DEC = str(int("4F2A", 16))            # the serial the test CA signs Link's certificate with
NOW = int(dt.datetime.now(dt.timezone.utc).timestamp())


class Brain:
    """The Brain's side of v2 in the fake secure-api: its permit keys, the site declarations it took, its permits."""

    def __init__(self, world):
        self.world = world
        self.signer = Signer()
        self.sites: list = []
        self.keys_answer = None
        world.secure.app.router.routes[0:0] = [
            Route("/secured/connector/v2/permit-keys", self.permit_keys, methods=["GET"]),
            Route("/secured/connector/v2/site", self.site, methods=["PUT"]),
        ]

    async def permit_keys(self, request: Request):
        return JSONResponse(self.keys_answer or self.signer.keys_document())

    async def site(self, request: Request):
        body = await request.json()
        if not contracts.v2_valid("site.schema.json", body):
            return JSONResponse({"error_code": "invalid_site"}, status_code=400)
        self.sites.append(body)
        return JSONResponse({"status": "success"})

    def permit(self, step, *, site=SERIAL_DEC, tenant=TENANT, staging="direct", iat=None, exp=None, signer=None):
        iat = NOW - 10 if iat is None else iat
        payload = {"v": 1, "permit_id": str(uuid.uuid4()), "tenant": tenant, "site": site, "source": SOURCE,
                   "action_id": str(uuid.uuid4()), "change_set_id": str(uuid.uuid4()), "action": step["action"],
                   "params_hash": params_hash(step["target"], step["params"]),
                   "expected_version": step["expected_version"], "staging": staging, "iat": iat,
                   "exp": iat + 3600 if exp is None else exp}
        return (signer or self.signer).sign(payload)

    def job(self, kind, args, **extra):
        job = {"id": str(uuid.uuid4()), "kind": kind, "protocol": "2.0", "source": SOURCE, "target_serial": SERIAL_DEC,
               "deadline": deadline_in(20), "actor": {"kind": "user", "id": "person-approver"}, "args": args}
        job.update(extra)
        return job


STEP = {"action": "set_fields", "target": "items/matter-1", "params": {"owner": "person-7"}, "expected_version": "v-1"}


@pytest.fixture
def v2(world):
    world.platform.config["sources"].append(
        {"workspace": "ws-1", "key": SOURCE, "kind": "example-records", "access": "read_write", "fetch": False,
         "settings": {}, "component": CONNECTOR})
    brain = Brain(world)
    with world.running(loops=("channel",), protocol_v2=True) as (client, link):
        src = FakeRecordSource()
        src.add("items/matter-1", {"owner": "person-1", "state": "open"})
        src.add("items/matter-2", {"owner": "person-2", "state": "open"})
        connector = ExampleRecordsConnector(src)
        register(world, client, CONNECTOR, kind="records_connector", capabilities=[],
                 job_kinds=["preview", "act", "reread", "review_outcome", "unstage", "probe"],
                 declaration=connector.declaration())
        eventually(lambda: link.channel.state == "connected")
        eventually(lambda: brain.sites)
        yield client, link, brain, connector, src


def serve(world, client, connector, name=CONNECTOR):
    """The connector's loop, once: take a job from Link, answer it with the kit's connector."""
    answer = client.get("/local/v1/jobs?wait=3", headers=world.headers(name))
    assert answer.status_code == 200, answer.status_code
    job = answer.json()["job"]
    result = connector.handle(job)
    posted = client.post(f"/local/v1/jobs/{job['id']}/result", headers=world.headers(name), json=result)
    assert posted.status_code in (200, 204), posted.text
    return job


def outcome(world, jid):
    return eventually(lambda: world.secure.outcome(jid))


def nothing_waiting(world, client, name=CONNECTOR):
    return client.get("/local/v1/jobs?wait=0", headers=world.headers(name)).status_code == 204


# ------------------------------------------------------------------ the switch

def test_with_the_switch_off_a_v2_job_is_refused_and_a_declaration_is_ignored(world):
    world.platform.config["sources"].append(
        {"workspace": "ws-1", "key": SOURCE, "kind": "example-records", "access": "read_write", "fetch": False,
         "settings": {}, "component": CONNECTOR})
    brain = Brain(world)
    with world.running(loops=("channel",)) as (client, link):
        assert link.v2 is None
        register(world, client, CONNECTOR, kind="records_connector", capabilities=[], job_kinds=["preview", "act"],
                 declaration=ExampleRecordsConnector(FakeRecordSource()).declaration())
        assert link.registry.get(CONNECTOR).declaration is None
        assert link.registry.get(CONNECTOR).job_kinds == ()
        eventually(lambda: link.channel.state == "connected")
        job = world.secure.enqueue(brain.job("preview", {"steps": [dict(STEP)], "staging": "direct"}))
        assert outcome(world, job["id"]) == {"status": "unsupported", "code": "protocol_unsupported",
                                             "duration_ms": outcome(world, job["id"])["duration_ms"]}
        assert brain.sites == []


def test_link_refuses_to_start_with_v2_on_when_a_v2_contract_is_stale(world, tmp_path, monkeypatch):
    stale = tmp_path / "v2"
    stale.mkdir()
    for f in contracts.FOLDER_V2.iterdir():
        if f.is_file():
            (stale / f.name).write_bytes(f.read_bytes())
    (stale / "job.schema.json").write_text("{}")
    with pytest.raises(StartupError, match="contracts.stale"):
        contracts.verify_v2(stale)


# ------------------------------------------------------------------ the site declaration and the keys

def test_link_declares_its_site_with_each_connectors_declaration_and_its_sources_of_that_kind(world, v2):
    client, link, brain, connector, src = v2
    site = brain.sites[-1]
    assert site["site_id"] == link.credentials.component and site["protocol"] == "2.0"
    assert [c["declaration"] for c in site["connectors"]] == [connector.declaration()]
    assert site["connectors"][0]["sources"] == [SOURCE], "only the assigned sources whose kind the connector declared"


def test_link_pins_the_brains_permit_keys_on_its_volume(world, v2):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    pinned = json.loads(link.v2.keys_path.read_text())
    assert [k["kid"] for k in pinned["keys"]] == [brain.signer.kid]
    assert oct(link.v2.keys_path.stat().st_mode & 0o777) == "0o600"


# ------------------------------------------------------------------ preview and act through Link

def _act(brain, step=STEP, **permit_kw):
    return brain.job("act", {"steps": [dict(step)], "staging": "direct"}, permits=[brain.permit(step, **permit_kw)])


def test_a_preview_reaches_the_connector_that_declared_the_action_and_its_answer_goes_back(world, v2):
    client, link, brain, connector, src = v2
    job = world.secure.enqueue(brain.job("preview", {"steps": [{k: STEP[k] for k in ("action", "target", "params")}],
                                                     "staging": "direct"}))
    serve(world, client, connector)
    result = outcome(world, job["id"])
    assert result["status"] == "ok" and result["preview"]["exact"] is True
    assert result["preview"]["read_versions"] == [{"target": "items/matter-1", "version": "v-1"}]


def test_an_act_under_a_valid_permit_is_written_once_and_a_repeat_gets_the_first_result(world, v2):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    job = world.secure.enqueue(_act(brain))
    serve(world, client, connector)
    first = outcome(world, job["id"])
    assert first["status"] == "ok" and first["receipts"][0]["outcome"] == "written"
    assert src.writes == {"items/matter-1": 1}
    again = world.secure.enqueue(dict(job, id=str(uuid.uuid4())))       # the Brain delivers the same permit again
    repeat = outcome(world, again["id"])
    assert repeat["repeat"] is True and repeat["code"] == "permit_used"
    assert repeat["receipts"] == first["receipts"]
    assert nothing_waiting(world, client) and src.writes == {"items/matter-1": 1}
    outcomes = [e["outcome"] for e in link.v2.store.entries()]
    assert outcomes == ["accepted", "executed", "repeated"]
    assert oct(link.v2.store.used_path.stat().st_mode & 0o777) == "0o600"


def test_a_repeat_after_the_permit_expired_and_its_key_retired_still_gets_the_first_result(world, v2):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    iso = lambda ts: dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    # The key's window ends a minute after the permit was signed.
    link.v2.ring.keys = KeyRing.parse(brain.signer.keys_document(iso(NOW - 3600), iso(NOW + 60)))
    job = world.secure.enqueue(_act(brain))
    serve(world, client, connector)
    first = outcome(world, job["id"])
    # Hours later the Brain lists only a new key; Link keeps the old one, whose window still holds the permit's iat.
    link.v2.clock = lambda: NOW + 10 * 3600
    link.v2.ring.pin(Signer().keys_document(), dt.datetime.fromtimestamp(NOW + 10 * 3600, dt.timezone.utc))
    assert brain.signer.kid in link.v2.ring.keys
    again = world.secure.enqueue(dict(job, id=str(uuid.uuid4())))
    repeat = outcome(world, again["id"])
    assert repeat["repeat"] is True and repeat["receipts"] == first["receipts"]
    assert src.writes == {"items/matter-1": 1}


@pytest.mark.parametrize("case, code", [
    ("unknown key", "permit_bad_signature"),
    ("another site", "permit_wrong_site"),
    ("another tenant", "permit_mismatch"),
    ("parameters changed", "permit_mismatch"),
    ("expected version changed", "permit_mismatch"),
    ("staging changed", "permit_mismatch"),
    ("expired", "permit_expired"),
    ("no permits", "permit_missing"),
    ("not a token", "permit_bad_signature"),
])
def test_a_refused_permit_writes_nothing_and_is_logged(world, v2, case, code):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    job = {
        "unknown key": lambda: _act(brain, signer=Signer()),
        "another site": lambda: _act(brain, site="1"),
        "another tenant": lambda: _act(brain, tenant=str(uuid.uuid4())),
        "parameters changed": lambda: dict(_act(brain), args={"steps": [dict(STEP, params={"owner": "x"})],
                                                              "staging": "direct"}),
        "expected version changed": lambda: dict(_act(brain), args={"steps": [dict(STEP, expected_version="v-9")],
                                                                    "staging": "direct"}),
        "staging changed": lambda: dict(_act(brain, staging="native_review")),
        "expired": lambda: _act(brain, iat=NOW - 7200),
        "no permits": lambda: dict(_act(brain), permits=[]),
        "not a token": lambda: dict(_act(brain), permits=["not-a-token"]),
    }[case]()
    world.secure.enqueue(job)
    result = outcome(world, job["id"])
    assert (result["status"], result["code"]) == ("refused", code)
    assert nothing_waiting(world, client) and src.writes == {}
    if case not in ("unknown key", "no permits", "not a token"):
        assert [(e["outcome"], e["code"]) for e in link.v2.store.entries()] == [("refused", code)]


def test_links_verdicts_agree_with_the_kits_reference(world, v2):
    """The same deliveries through Link and through the kit's reference gate give the same code."""
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    tenant, serial = link.v2.identity()
    reference = LinkGate(ExampleRecordsConnector(FakeRecordSource()), KitRing.from_document(brain.signer.keys_document()),
                         tenant=tenant, serial=serial, clock=lambda: NOW)
    for job in (_act(brain, signer=Signer()), _act(brain, site="1"), _act(brain, tenant=str(uuid.uuid4())),
                _act(brain, iat=NOW - 7200), dict(_act(brain), permits=[])):
        world.secure.enqueue(job)
        assert outcome(world, job["id"])["code"] == reference.deliver(job)["code"]


@pytest.mark.parametrize("change, code, status", [
    ({"action": "archive"}, "action_not_declared", "refused"),
    ({"staging": "native_review_only"}, None, "failed"),
])
def test_an_undeclared_action_is_refused_before_any_permit_check(world, v2, change, code, status):
    client, link, brain, connector, src = v2
    step = dict(STEP, **{k: v for k, v in change.items() if k == "action"})
    job = brain.job("act", {"steps": [step], "staging": change.get("staging", "direct")}, permits=[brain.permit(step)])
    world.secure.enqueue(job)
    result = outcome(world, job["id"])
    assert result["status"] == status and (code is None or result["code"] == code)
    assert src.writes == {}


def test_a_source_that_only_reads_refuses_an_act_write_disabled(world, v2):
    client, link, brain, connector, src = v2
    for s in link.config["sources"]:
        if s["key"] == SOURCE:
            s["access"] = "read"
    job = world.secure.enqueue(_act(brain))
    assert outcome(world, job["id"])["code"] == "write_disabled"
    assert src.writes == {}


def test_a_source_of_another_kind_than_the_connector_declared_is_unsupported(world, v2):
    client, link, brain, connector, src = v2
    for s in link.config["sources"]:
        if s["key"] == SOURCE:
            s["kind"] = "other-records"
    job = world.secure.enqueue(_act(brain))
    assert outcome(world, job["id"])["code"] == "unsupported"


def test_when_the_write_log_cannot_be_written_nothing_goes_on(world, v2, monkeypatch):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    from knovas_link.write_path import WriteLogUnavailable

    def broken(*a, **k):
        raise WriteLogUnavailable()
    monkeypatch.setattr(link.v2.store, "log", broken)
    job = world.secure.enqueue(_act(brain))
    result = outcome(world, job["id"])
    assert (result["status"], result["code"]) == ("failed", "write_log_unavailable")
    assert nothing_waiting(world, client) and src.writes == {}


def test_a_v1_job_still_goes_its_1x_way_with_the_switch_on(world, v2):
    from conftest import make_job
    client, link, brain, connector, src = v2
    register(world, client)
    job = world.secure.enqueue(make_job("fetch", "handbook"))
    taken = client.get("/local/v1/jobs?wait=3", headers=world.headers("sync-component")).json()["job"]
    assert taken == job


def test_logs_carry_no_permit_token_or_value(world, v2, caplog):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    caplog.set_level("DEBUG")
    job = world.secure.enqueue(_act(brain))
    serve(world, client, connector)
    outcome(world, job["id"])
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert job["permits"][0] not in text and "person-7" not in text and "items/matter-1" not in text
