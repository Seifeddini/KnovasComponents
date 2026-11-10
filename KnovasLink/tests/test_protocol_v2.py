"""Knovas Link speaks connector protocol 2.0 behind LINK_PROTOCOL_V2 (draft; BET-2026-11-write-back A-5; WB-21, WB-06,
WB-25 site halves; SPEC-write-back -10, -12, -15, -21, -22, -23, -26; GI-CONN-05, GI-CONN-08), with the fixes of the
independent verifier's findings on Phase A (M1: a repeat is answered before anything else; M2: a permit is reserved
before its connector runs; L1, L2, L5).

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
from knovas_link.permits import KeyRing, PermitRefused, permit_id_unverified, verify  # noqa: E402
from knovas_link.write_path import PermitStore  # noqa: E402

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
    # The first declaration may leave before the connector registered; the one after its registration names it.
    site = eventually(lambda: brain.sites[-1] if brain.sites[-1]["connectors"] else None)
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
    # Every refusal is a line of the site's write log (L2): a permit that did not verify under the id it names,
    # unverified; a delivery that carried no readable permit as one line without an id.
    named = permit_id_unverified(job["permits"][0]) if job["permits"] else None
    assert [(e["permit_id"], e["outcome"], e["code"], e["job_id"]) for e in link.v2.store.entries()] == [
        (named, "refused", code, job["id"])]
    assert (named is None) == (case in ("no permits", "not a token"))


@pytest.mark.parametrize("lifetime", [10800, 10801])
def test_a_permit_may_live_three_hours_and_not_a_second_longer(world, v2, lifetime):
    """D-15 (v2.0 as amended 2026-11-10): exp - iat up to 10800 s passes step 2; one second more is
    permit_bad_signature, and nothing is written."""
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    job = world.secure.enqueue(_act(brain, iat=NOW - 10, exp=NOW - 10 + lifetime))
    if lifetime == 10800:
        serve(world, client, connector)
        result = outcome(world, job["id"])
        assert result["status"] == "ok" and src.writes == {"items/matter-1": 1}
    else:
        result = outcome(world, job["id"])
        assert (result["status"], result["code"]) == ("refused", "permit_bad_signature")
        assert nothing_waiting(world, client) and src.writes == {}


def test_a_v2_result_whose_changes_name_no_step_is_not_taken(world, v2):
    """D-14: Link checks a v2 result against v2's result schema, which requires every change's step."""
    client, link, brain, connector, src = v2
    world.secure.enqueue(brain.job("preview", {"steps": [{k: STEP[k] for k in ("action", "target", "params")}],
                                               "staging": "direct"}))
    answer = client.get("/local/v1/jobs?wait=3", headers=world.headers(CONNECTOR))
    job = answer.json()["job"]
    result = connector.handle(job)
    assert [c["step"] for c in result["preview"]["changes"]] == [0]
    for change in result["preview"]["changes"]:
        del change["step"]
    posted = client.post(f"/local/v1/jobs/{job['id']}/result", headers=world.headers(CONNECTOR), json=result)
    assert posted.status_code == 400


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


@pytest.mark.parametrize("signer, code", [("the Brain", "action_not_declared"), ("an unknown key", "permit_bad_signature")])
def test_an_undeclared_action_is_refused_after_the_permit_checks(world, v2, signer, code):
    """Step 7 (the declaration) comes after the permit checks (steps 1 to 6), as the protocol and the Alloy model
    order them (verifier, Phase A, M1); either refusal is a line of the write log (L2)."""
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    step = dict(STEP, action="archive")
    job = brain.job("act", {"steps": [step], "staging": "direct"},
                    permits=[brain.permit(step, signer=None if signer == "the Brain" else Signer())])
    world.secure.enqueue(job)
    result = outcome(world, job["id"])
    assert (result["status"], result["code"]) == ("refused", code) and src.writes == {}
    assert [(e["outcome"], e["code"]) for e in link.v2.store.entries()] == [("refused", code)]


def test_an_act_that_breaks_the_v2_job_schema_is_invalid_job_and_logged(world, v2):
    client, link, brain, connector, src = v2
    job = brain.job("act", {"steps": [dict(STEP)], "staging": "native_review_only"}, permits=[brain.permit(STEP)])
    world.secure.enqueue(job)
    result = outcome(world, job["id"])
    assert (result["status"], result["code"]) == ("failed", "invalid_job") and src.writes == {}
    assert [(e["permit_id"], e["code"]) for e in link.v2.store.entries()] == [
        (permit_id_unverified(job["permits"][0]), "invalid_job")]


def test_a_source_that_only_reads_refuses_an_act_write_disabled(world, v2):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    for s in link.config["sources"]:
        if s["key"] == SOURCE:
            s["access"] = "read"
    job = world.secure.enqueue(_act(brain))
    assert outcome(world, job["id"])["code"] == "write_disabled"
    assert src.writes == {}
    assert [(e["outcome"], e["code"]) for e in link.v2.store.entries()] == [("refused", "write_disabled")]


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


# ------------------------------------------------------------------ grouping item (D-24)

@pytest.mark.parametrize("second, code", [("items/matter-1", None), ("items/matter-2", "grouping_unsupported")])
def test_grouping_item_lets_several_steps_of_one_item_through_and_refuses_steps_on_several(world, v2, second, code):
    """v2.0 as amended 2026-11-10 (D-24): an action declared grouping item takes several steps in one act only on one
    item (all or none there); an act spanning items is the Brain's error, refused grouping_unsupported and logged."""
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    declaration = connector.declaration()
    for action in declaration["actions"]:
        if action["name"] == STEP["action"]:
            action["grouping"] = "item"
    register(world, client, CONNECTOR, kind="records_connector", capabilities=[],
             job_kinds=["preview", "act", "reread", "review_outcome", "unstage", "probe"], declaration=declaration)
    steps = [dict(STEP, params={"owner": "person-7"}), dict(STEP, target=second, params={"due_date": "2026-12-01"})]
    job = world.secure.enqueue(brain.job("act", {"steps": steps, "staging": "direct"},
                                         permits=[brain.permit(s) for s in steps]))
    if code is None:
        serve(world, client, connector)
        result = outcome(world, job["id"])
        assert result["status"] == "ok" and [r["outcome"] for r in result["receipts"]] == ["written", "written"]
        assert src.writes == {"items/matter-1": 2}
    else:
        result = outcome(world, job["id"])
        assert (result["status"], result["code"]) == ("refused", code)
        assert nothing_waiting(world, client) and src.writes == {}
        assert [(e["outcome"], e["code"]) for e in link.v2.store.entries()] == [("refused", code)] * 2


# ------------------------------------------------------------------ M1: a repeat is answered before anything else

def _site_changes(world, client, link, connector, change):
    if change == "its deadline passed":
        return {"deadline": deadline_in(-60)}
    if change == "the source reads only now":
        for s in link.config["sources"]:
            if s["key"] == SOURCE:
                s["access"] = "read"
    elif change == "the connector registered again without the action":
        declaration = connector.declaration()
        declaration["actions"] = [a for a in declaration["actions"] if a["name"] != STEP["action"]]
        register(world, client, CONNECTOR, kind="records_connector", capabilities=[],
                 job_kinds=["preview", "act", "reread", "review_outcome", "unstage", "probe"], declaration=declaration)
        assert STEP["action"] not in {a["name"] for a in link.registry.get(CONNECTOR).declaration["actions"]}
    return {}


@pytest.mark.parametrize("change", ["its deadline passed", "the source reads only now",
                                    "the connector registered again without the action"])
def test_a_repeat_of_a_written_permit_gets_its_first_result_whatever_changed_since(world, v2, change):
    """SPEC-write-back-10: the used-permit store answers a repeat (step 5) before Link looks at the job's deadline,
    the source's switch or the connector's declaration (step 7); the first receipt reaches the Brain (M1)."""
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    job = world.secure.enqueue(_act(brain))
    serve(world, client, connector)
    first = outcome(world, job["id"])
    assert first["status"] == "ok"
    again = world.secure.enqueue(dict(job, id=str(uuid.uuid4()), **_site_changes(world, client, link, connector, change)))
    repeat = outcome(world, again["id"])
    assert (repeat["status"], repeat["repeat"], repeat["code"]) == ("ok", True, "permit_used")
    assert repeat["receipts"] == first["receipts"]
    assert nothing_waiting(world, client) and src.writes == {"items/matter-1": 1}
    assert [e["outcome"] for e in link.v2.store.entries()] == ["accepted", "executed", "repeated"]


def test_a_first_delivery_after_the_expiry_is_permit_expired_even_past_its_deadline(world, v2):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    job = world.secure.enqueue(dict(_act(brain, iat=NOW - 7200), deadline=deadline_in(-60)))
    assert (outcome(world, job["id"])["status"], outcome(world, job["id"])["code"]) == ("refused", "permit_expired")
    assert src.writes == {}


# ------------------------------------------------------------------ M2: a permit runs once, also while it runs

def _connector_takes(world, client):
    answer = client.get("/local/v1/jobs?wait=3", headers=world.headers(CONNECTOR))
    assert answer.status_code == 200, answer.status_code
    return answer.json()["job"]


def _connector_answers(world, client, connector, job):
    posted = client.post(f"/local/v1/jobs/{job['id']}/result", headers=world.headers(CONNECTOR),
                         json=connector.handle(job))
    assert posted.status_code in (200, 204), posted.text


def test_a_second_delivery_while_the_first_is_with_the_connector_waits_and_never_runs(world, v2, caplog):
    """The permit is reserved before the connector sees the first delivery; the second (a new job id, the same
    permit) is never handed on: it waits and is answered with the first result, which nothing replaces (M2)."""
    client, link, brain, connector, src = v2
    caplog.set_level("INFO", logger="knovas_link.jobs")
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    first = world.secure.enqueue(_act(brain))
    taken = _connector_takes(world, client)
    pid = permit_id_unverified(first["permits"][0])
    assert taken["id"] == first["id"] and link.v2.store.state([pid], link.v2.store.used[pid]["content"])[0] == "reserved"
    second = world.secure.enqueue(dict(first, id=str(uuid.uuid4())))
    eventually(lambda: any("waits_for_its_permits_first_delivery" in r.getMessage() for r in caplog.records))
    assert nothing_waiting(world, client), "the second delivery never reaches the connector"
    assert world.secure.outcome(second["id"]) is None, "it waits for the first result"
    _connector_answers(world, client, connector, taken)
    done = outcome(world, first["id"])
    repeat = outcome(world, second["id"])
    assert done["status"] == "ok" and repeat["repeat"] is True and repeat["receipts"] == done["receipts"]
    assert nothing_waiting(world, client) and src.writes == {"items/matter-1": 1}
    assert [(e["job_id"], e["outcome"]) for e in link.v2.store.entries()] == [
        (first["id"], "accepted"), (first["id"], "executed"), (second["id"], "repeated")]


def test_an_act_claimed_again_while_its_connector_writes_runs_once_under_the_new_lease(world, v2):
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    job = world.secure.enqueue(_act(brain))
    taken = _connector_takes(world, client)
    world.secure.lease_runs_out(job["id"])                      # secure-api offers it again; Link claims it again
    eventually(lambda: link.channel.held[job["id"]].lease == world.secure.jobs[job["id"]]["lease"]
               and len(world.secure.jobs[job["id"]]["leases"]) == 2)
    assert nothing_waiting(world, client), "the job is not handed out a second time"
    _connector_answers(world, client, connector, taken)
    assert outcome(world, job["id"])["status"] == "ok" and src.writes == {"items/matter-1": 1}


def test_a_permit_whose_delivery_ended_without_a_result_goes_on_again_only_before_its_expiry(world, v2):
    """The connector wrote, but its result never reached Link (it stayed silent past the deadline). Before the expiry
    the next delivery is handed on and the connector's marker answers the earlier write's receipt; after it, nothing
    goes on (permit_expired; the Brain recovers the outcome with reread)."""
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    job = world.secure.enqueue(dict(_act(brain), deadline=deadline_in(1)))
    taken = _connector_takes(world, client)
    connector.handle(taken)                                      # written; the answer is lost
    assert outcome(world, job["id"])["status"] == "unreachable" and src.writes == {"items/matter-1": 1}
    again = world.secure.enqueue(dict(job, id=str(uuid.uuid4()), deadline=deadline_in(20)))
    serve(world, client, connector)
    result = outcome(world, again["id"])
    assert result["status"] == "ok" and result["receipts"][0]["outcome"] == "written"
    assert src.writes == {"items/matter-1": 1}, "the connector found its earlier write"
    pid = permit_id_unverified(job["permits"][0])
    later_result = outcome(world, world.secure.enqueue(dict(job, id=str(uuid.uuid4())))["id"])
    assert later_result["repeat"] is True and later_result["receipts"] == result["receipts"]
    assert link.v2.store.used[pid]["job_id"] == again["id"]
    # A permit that ended without a result and is past its expiry: nothing goes on.
    lost = world.secure.enqueue(dict(_act(brain), deadline=deadline_in(1)))
    connector.handle(_connector_takes(world, client))
    assert outcome(world, lost["id"])["status"] == "unreachable"
    link.v2.clock = lambda: NOW + 2 * 3600
    late = world.secure.enqueue(dict(lost, id=str(uuid.uuid4()), deadline=deadline_in(20)))
    assert (outcome(world, late["id"])["status"], outcome(world, late["id"])["code"]) == ("refused", "permit_expired")
    assert nothing_waiting(world, client)


def test_the_first_result_is_kept_and_never_replaced(tmp_path):
    store = PermitStore(tmp_path, clock=lambda: NOW)
    assert store.begin(["p-1"], "job-a", "digest", NOW + 60)
    assert store.state(["p-1"], "digest") == ("reserved", None, "job-a")
    assert store.complete(["p-1"], "job-a", "digest", b'{"first":1}', NOW + 60) == b'{"first":1}'
    assert store.complete(["p-1"], "job-b", "digest", b'{"second":1}', NOW + 60) == b'{"first":1}'
    assert not store.begin(["p-1"], "job-c", "digest", NOW + 60)
    again = PermitStore(tmp_path, clock=lambda: NOW)                       # Link restarted
    assert again.state(["p-1"], "digest") == ("used", b'{"first":1}', "job-a")
    assert oct(again.used_path.stat().st_mode & 0o777) == "0o600"


# ------------------------------------------------------------------ untrusted tokens (L1, L5)

def _b64(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unsigned(header, payload) -> str:
    def part(value):
        return value if isinstance(value, str) else _b64(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())
    return part(header) + "." + part(payload) + "." + "A" * 86


def _payload(**changes):
    payload = {"v": 1, "permit_id": str(uuid.uuid4()), "tenant": TENANT, "site": SERIAL_DEC, "source": SOURCE,
               "action_id": str(uuid.uuid4()), "change_set_id": str(uuid.uuid4()), "action": STEP["action"],
               "params_hash": params_hash(STEP["target"], STEP["params"]), "expected_version": "v-1",
               "staging": "direct", "iat": NOW - 10, "exp": NOW + 3600}
    payload.update(changes)
    return payload


UNTRUSTED = {
    "an iat past the year 9999": lambda kid: _unsigned({"alg": "EdDSA", "kid": kid, "typ": "knovas-permit+jws"},
                                                       _payload(iat=2 ** 53 - 100, exp=2 ** 53 - 50)),
    "a deeply nested header": lambda kid: _unsigned(_b64(b"[" * 2000 + b"]" * 2000), _payload()),
    "a deeply nested payload": lambda kid: _unsigned({"alg": "EdDSA", "kid": kid, "typ": "knovas-permit+jws"},
                                                     _b64(b"[" * 1500 + b"]" * 1500)),
    "longer than any token": lambda kid: "A" * 9000 + ".B.C",
}


@pytest.mark.parametrize("case", sorted(UNTRUSTED))
def test_an_untrusted_token_is_refused_and_never_raises(case):
    signer = Signer()
    ring = KeyRing(KeyRing.parse(signer.keys_document()))
    token = UNTRUSTED[case](signer.kid)
    with pytest.raises(PermitRefused) as refused:
        verify(token, ring)
    assert refused.value.code == "permit_bad_signature"
    permit_id_unverified(token)                                           # never raises either


@pytest.mark.parametrize("case", ["an iat past the year 9999", "a deeply nested header"])
def test_an_untrusted_token_gets_an_answer_through_link(world, v2, case):
    """Before L1's fix such a job got no answer at all, and the Brain delivered it again."""
    client, link, brain, connector, src = v2
    eventually(lambda: brain.signer.kid in link.v2.ring.keys)
    job = world.secure.enqueue(dict(_act(brain), permits=[UNTRUSTED[case](brain.signer.kid)]))
    assert (outcome(world, job["id"])["status"], outcome(world, job["id"])["code"]) == ("refused", "permit_bad_signature")
    assert src.writes == {}


@pytest.mark.parametrize("part", [0, 1, 2])
def test_a_permit_has_exactly_one_encoding(part):
    """L5: base64url without padding has unused bits in the last character of a part whose length is not a multiple
    of 4; a token that sets them spells the same bytes another way and is refused (before the fix, the signature's
    last character could be changed and the token still verified). A part of a length base64url cannot have is
    refused too."""
    import base64
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signer = Signer()
    ring = KeyRing(KeyRing.parse(signer.keys_document()))
    tokens = [signer.sign(_payload(source=SOURCE + "x" * k)) for k in range(3)]
    token = next(t for t in tokens if len(t.split(".")[part]) % 4)       # a part that has unused bits
    assert verify(token, ring).payload["action"] == STEP["action"]
    parts = token.split(".")
    other = parts[part][:-1] + alphabet[alphabet.index(parts[part][-1]) ^ 1]
    pad = lambda t: t + "=" * (-len(t) % 4)  # noqa: E731
    assert other != parts[part] and base64.urlsafe_b64decode(pad(other)) == base64.urlsafe_b64decode(pad(parts[part]))
    for bad in (other, parts[part][:len(parts[part]) - len(parts[part]) % 4] + "A"):
        with pytest.raises(PermitRefused) as refused:
            verify(".".join(parts[:part] + [bad] + parts[part + 1:]), ring)
        assert refused.value.code == "permit_bad_signature"


def test_the_heartbeat_carries_each_connectors_declaration_only_with_the_switch_on(world, v2):
    client, link, brain, connector, src = v2
    components = {c["name"]: c for c in link.site()["components"]}
    assert components[CONNECTOR]["declaration"] == connector.declaration()
    link.v2, saved = None, link.v2
    try:
        assert "declaration" not in {c["name"]: c for c in link.site()["components"]}[CONNECTOR]
    finally:
        link.v2 = saved
