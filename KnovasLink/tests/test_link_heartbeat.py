"""Knovas Link's heartbeat with the MVP telemetry (MVP-19, KC-7a): protocol v1's heartbeat plus the site object,
codes and counts only, never a name of a file or a document."""
from __future__ import annotations

import json

from conftest import SITE_TOKEN, CONTROL, eventually, make_job, register
from knovas_link import CAPABILITIES, VERSION
from knovas_link.registry import STALE_SECONDS

SITE_KEYS = {"certificate", "work_channel", "probe", "components", "sources", "jobs"}
COMPONENT_KEYS = {"name", "kind", "version", "state", "capabilities", "local_ping_ms"}
SOURCE_KEYS = {"workspace", "key", "last_read_ok_at", "last_run_status"}


def _beat(client, link, world):
    assert client.portal.call(link.beat) is True
    return world.platform.heartbeats[-1]


def test_the_heartbeat_is_protocol_v1_plus_the_site_object(world):
    with world.running(loops=()) as (client, link):
        beat = _beat(client, link, world)
    assert set(beat) == {"state", "version", "lag_seconds", "open_errors", "capabilities", "site"}
    assert (beat["version"], beat["lag_seconds"], beat["open_errors"]) == (VERSION, None, 0)
    assert beat["capabilities"] == CAPABILITIES
    site = beat["site"]
    assert set(site) == SITE_KEYS
    assert site["certificate"] == "present" and site["work_channel"] == "down"
    assert site["probe"] == {"last_at": None, "ok": None, "round_trip_ms": None}, "never probed: unknown"
    assert site["components"] == [] and site["jobs"] == {}
    assert [s["key"] for s in site["sources"]] == ["handbook", "archive", "shares", "crm"]
    for s in site["sources"]:
        assert set(s) == SOURCE_KEYS and s["last_read_ok_at"] is None and s["last_run_status"] is None
    assert beat["state"] == "degraded", "no work channel: not online"


def test_a_working_site_reports_online_with_its_probe_components_and_job_counts(world):
    with world.running(loops=("channel",)) as (client, link):
        register(world, client)
        register(world, client, "knovas-connector", kind="knovas_connector", job_kinds=["fetch"], capabilities=["documents"])
        client.post("/local/v1/heartbeat", headers=world.headers(), json={"state": "online", "version": "0.2.0"})
        client.post("/local/v1/heartbeat", headers=world.headers("knovas-connector"), json={"state": "degraded"})
        eventually(lambda: link.channel.state == "connected")
        assert client.portal.call(link.channel.probe_now) is True
        local = client.get("/local/v1/jobs?wait=2", headers=world.headers()).json()["job"]
        client.post(f"/local/v1/jobs/{local['id']}/result", headers=world.headers(), json={"status": "ok", "duration_ms": 0})
        for source in ("archive", "crm"):
            world.secure.enqueue(make_job("fetch", source))
        eventually(lambda: sum(sum(v.values()) for v in link.telemetry.jobs.values()) == 3)
        beat = _beat(client, link, world)
    site = beat["site"]
    assert beat["state"] == "online" and site["work_channel"] == "connected"
    assert site["probe"]["ok"] is True and isinstance(site["probe"]["round_trip_ms"], int)
    assert site["jobs"] == {"probe": {"ok": 1}, "fetch": {"refused": 1, "unsupported": 1}}
    by_name = {c["name"]: c for c in site["components"]}
    assert set(by_name) == {"sync-component", "knovas-connector"}
    for c in site["components"]:
        assert set(c) == COMPONENT_KEYS
    assert by_name["sync-component"]["state"] == "online" and by_name["sync-component"]["kind"] == "sync_component"
    assert by_name["sync-component"]["capabilities"] == ["dry_run", "apply", "write:github"]
    assert isinstance(by_name["sync-component"]["local_ping_ms"], int)
    assert by_name["knovas-connector"]["state"] == "degraded"
    assert by_name["knovas-connector"]["local_ping_ms"] is None, "it answers no probe: unknown, never fine"


def test_job_counts_are_those_since_the_last_beat_that_was_taken(world):
    with world.running(loops=()) as (client, link):
        link.telemetry.job("fetch", "ok")
        link.telemetry.job("fetch", "ok")
        world.platform.transport.down = True
        assert client.portal.call(link.beat) is False
        link.telemetry.job("write", "conflict")
        world.platform.transport.down = False
        first = _beat(client, link, world)["site"]["jobs"]
        assert first == {"fetch": {"ok": 2}, "write": {"conflict": 1}}, "a beat that was not taken loses nothing"
        assert _beat(client, link, world)["site"]["jobs"] == {}
        link.telemetry.job("probe", "ok")
        assert _beat(client, link, world)["site"]["jobs"] == {"probe": {"ok": 1}}


def test_sources_show_their_last_good_read_and_last_run(world):
    world.platform.commands = [{"id": "cmd-1", "command": "approve_plan", "component": "sync-component",
                                "args": {"plan": "p-1", "mapping": "archive-notes", "reason": "checked"}}]
    with world.running(loops=()) as (client, link):
        client.portal.call(link.beat)
        client.post("/local/v1/heartbeat", headers=world.headers(), json={"state": "online"})
        h = world.headers()
        client.post("/local/v1/report/checks", headers=h, json={"checks": [
            {"workspace": "ws-1", "source": "handbook", "code": "source.ok", "counts": {}},
            {"workspace": "ws-1", "source": "archive", "code": "fetch.http_401", "counts": {}},
            {"workspace": "ws-2", "source": "shares", "code": "source.ok", "counts": {}}]})   # not this component's
        run = {"kind": "apply", "status": "failed", "duration_ms": 10, "added": 0, "changed": 0, "retired": 0,
               "errors": 1, "command": "cmd-1"}
        assert client.post("/local/v1/report/runs", headers=h, json=run).status_code == 201
        sources = {s["key"]: s for s in _beat(client, link, world)["site"]["sources"]}
        assert sources["handbook"]["last_read_ok_at"] and sources["handbook"]["last_run_status"] is None
        assert sources["archive"]["last_read_ok_at"] is None and sources["archive"]["last_run_status"] == "failed", \
            "a run a command named with a mapping counts for that mapping's source only"
        assert sources["shares"]["last_read_ok_at"] is None
        client.post("/local/v1/report/runs", headers=h, json={**run, "status": "ok", "errors": 0, "command": None})
        sources = {s["key"]: s for s in _beat(client, link, world)["site"]["sources"]}
        assert sources["handbook"]["last_run_status"] == "ok" and sources["archive"]["last_run_status"] == "ok"
        assert sources["archive"]["last_read_ok_at"]


def test_a_component_that_fell_silent_is_offline_and_one_that_never_beat_is_unknown(world):
    with world.running(loops=()) as (client, link):
        register(world, client)
        register(world, client, "knovas-connector", kind="knovas_connector", job_kinds=["fetch"])
        client.post("/local/v1/heartbeat", headers=world.headers(), json={"state": "online"})
        now = link.registry.clock()
        link.registry.clock = lambda: now + STALE_SECONDS + 1
        states = {c["name"]: c["state"] for c in _beat(client, link, world)["site"]["components"]}
    assert states == {"sync-component": "offline", "knovas-connector": "unknown"}


def test_while_waiting_for_the_certificate_the_heartbeat_says_so(world):
    world.platform.issue_certificate = False
    with world.running(loops=()) as (client, link):
        beat = _beat(client, link, world)
    assert beat["site"]["certificate"] == "waiting" and beat["state"] == "degraded" and "warnings" not in beat


def test_the_platforms_pause_and_schedule_reach_the_components(world):
    world.platform.paused = True
    with world.running(loops=()) as (client, link):
        _beat(client, link, world)
        answer = client.post("/local/v1/heartbeat", headers=world.headers(), json={"state": "online"}).json()
    assert answer["paused"] is True and answer["schedule"] == {"mode": "interval", "every_minutes": 60}


def test_the_heartbeat_carries_codes_and_counts_never_a_name(world):
    with world.running(loops=("channel",)) as (client, link):
        register(world, client)
        eventually(lambda: link.channel.state == "connected")
        world.secure.enqueue(make_job("fetch", "handbook", external_id="Verträge/Muster Treuhand AG.pdf"))
        job = client.get("/local/v1/jobs?wait=3", headers=world.headers()).json()["job"]
        client.post(f"/local/v1/jobs/{job['id']}/result", headers=world.headers(),
                    json={"status": "refused", "code": "not_found", "duration_ms": 4})
        client.post("/local/v1/report/events", headers=world.headers(), json={"events": [{"code": "fetch.http_404",
                                                                                         "counts": {"files": 2}}]})
        beat = _beat(client, link, world)
    text = json.dumps(beat)
    for name in ("Muster", "Verträge", ".pdf", "example-org", "repository", "docs/", SITE_TOKEN, CONTROL,
                 world.token("sync-component"), "BEGIN"):
        assert name not in text, name
    assert beat["site"]["jobs"] == {"fetch": {"refused": 1}}
