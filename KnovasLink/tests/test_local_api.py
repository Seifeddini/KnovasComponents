"""Knovas Link's local API (MVP-16, KC-7a): every endpoint of contracts/local-api.md, its authentication, and the
control plane through Link (heartbeat, commands, configuration and package per component)."""
from __future__ import annotations

import pytest

from conftest import LINK_ID, eventually, register
from knovas_link import VERSION

ENDPOINTS = [
    ("POST", "/local/v1/register"), ("POST", "/local/v1/heartbeat"), ("POST", "/local/v1/commands/c-1/done"),
    ("GET", "/local/v1/config"), ("GET", "/local/v1/package"), ("GET", "/local/v1/ping"),
    ("GET", "/local/v1/jobs?wait=0"), ("POST", "/local/v1/jobs/2b1f7d38-6a43-4e8e-9d55-0c9a1c4f1e10/part?n=0"),
    ("POST", "/local/v1/jobs/2b1f7d38-6a43-4e8e-9d55-0c9a1c4f1e10/result"), ("GET", "/local/v1/brain/secured/graph/nodes"),
    ("POST", "/local/v1/report/runs"), ("POST", "/local/v1/report/events"), ("POST", "/local/v1/report/checks"),
]


# ------------------------------------------------------------------ authentication

@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_every_endpoint_needs_the_token_and_the_name(world, method, path):
    with world.running(loops=()) as (client, _):
        own = world.headers("sync-component")
        other = world.headers("knovas-connector")
        cases = [
            ({}, 401, "unknown_component"),
            ({"Authorization": own["Authorization"]}, 401, "unknown_component"),
            ({"X-Component": "sync-component"}, 401, "bad_token"),
            ({"X-Component": "sync-component", "Authorization": "Bearer wrong"}, 401, "bad_token"),
            ({"X-Component": "sync-component", "Authorization": other["Authorization"]}, 401, "bad_token"),
            ({"X-Component": "unknown-reader", "Authorization": own["Authorization"]}, 401, "unknown_component"),
        ]
        for headers, status, code in cases:
            answer = client.request(method, path, headers=headers, json={})
            assert answer.status_code == status, (headers, answer.text)
            assert answer.json()["error"] == code
            assert set(answer.json()) == {"error", "message"}


def test_an_unknown_path_is_a_json_404(world):
    with world.running(loops=()) as (client, _):
        answer = client.get("/local/v1/nothing", headers=world.headers())
    assert answer.status_code == 404 and answer.json()["error"] == "not_found"


# ------------------------------------------------------------------ register

def test_register_answers_the_sources_assigned_to_the_name_without_credentials(world):
    with world.running(loops=()) as (client, link):
        answer = register(world, client)
        assert [s["key"] for s in answer["sources"]] == ["handbook", "archive"]
        for s in answer["sources"]:
            assert set(s) == {"workspace", "key", "kind", "access", "settings", "fetch", "auto_apply"}
        assert answer["sources"][0] == {"workspace": "ws-1", "key": "handbook", "kind": "github", "access": "read_write",
                                        "settings": {"repository": "example-org/handbook", "ref": "main"}, "fetch": True,
                                        "auto_apply": False}
        assert answer["sources"][1]["fetch"] is False and answer["sources"][1]["access"] == "read"
        crm = register(world, client, "records-reader", kind="sync_component", job_kinds=["fetch"])
        assert [s["key"] for s in crm["sources"]] == ["crm"]
        c = link.registry.get("sync-component")
        assert c.registered and c.job_kinds == ("fetch", "write", "probe") and c.version == "0.2.0"


def test_a_later_registration_replaces_the_earlier_and_unknown_job_kinds_are_ignored(world):
    with world.running(loops=()) as (client, link):
        register(world, client, job_kinds=["fetch", "write"])
        register(world, client, job_kinds=["fetch", "inventory"])
        assert link.registry.get("sync-component").job_kinds == ("fetch",)


@pytest.mark.parametrize("change", [{"component": "knovas-connector"}, {"kind": "Sync Komponente"}, {"version": ""},
                                    {"capabilities": "dry_run"}, {"capabilities": ["has space"]}, {"job_kinds": [3]}])
def test_register_refuses_a_bad_field(world, change):
    body = {"component": "sync-component", "kind": "sync_component", "version": "0.2.0", "capabilities": [],
            "job_kinds": ["fetch"], **change}
    with world.running(loops=()) as (client, _):
        answer = client.post("/local/v1/register", headers=world.headers(), json=body)
    assert answer.status_code == 400 and answer.json()["error"] == "bad_field"


def test_register_without_any_configuration_is_unreachable(world):
    with world.running(loops=()):
        pass
    (world.data_dir / "state" / "config.json").unlink()
    world.platform.transport.down = True
    with world.running(loops=()) as (client, _):
        answer = client.post("/local/v1/register", headers=world.headers(),
                             json={"component": "sync-component", "kind": "sync_component", "version": "0.2.0",
                                   "capabilities": [], "job_kinds": []})
    assert answer.status_code == 503 and answer.json()["error"] == "unreachable"


# ------------------------------------------------------------------ heartbeat and commands

def _beat(world, client, name="sync-component", **body):
    answer = client.post("/local/v1/heartbeat", headers=world.headers(name),
                         json={"state": "online", "version": "0.2.0", "lag_seconds": 12, "open_errors": 0,
                               "capabilities": ["dry_run"], **body})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_commands_fan_out_to_their_component_once_and_link_keeps_its_own(world):
    world.platform.paused = True
    world.platform.commands = [
        {"id": "cmd-1", "command": "run", "args": {"kind": "dry_run"}, "component": "sync-component"},
        {"id": "cmd-2", "command": "check", "args": {}, "component": "knovas-connector"},
        {"id": "cmd-3", "command": "reload_config", "args": {}},
    ]
    with world.running(loops=()) as (client, link):
        assert client.portal.call(link.beat) is True
        first = _beat(world, client)
        assert first == {"paused": True, "schedule": {"mode": "interval", "every_minutes": 60},
                         "commands": [{"id": "cmd-1", "command": "run", "args": {"kind": "dry_run"}}]}
        assert _beat(world, client)["commands"] == [], "delivered once"
        assert [c["id"] for c in _beat(world, client, "knovas-connector")["commands"]] == ["cmd-2"]
        eventually(lambda: any(d[0] == "cmd-3" for d in world.platform.done))
    (own,) = [d for d in world.platform.done if d[0] == "cmd-3"]
    assert own == ("cmd-3", {"result": "ok"}, None), "Link's own command, without a sub-component"


def test_a_component_reports_its_command_done_through_link(world):
    world.platform.commands = [{"id": "cmd-9", "command": "check", "args": {}, "component": "sync-component"}]
    with world.running(loops=()) as (client, link):
        client.portal.call(link.beat)
        _beat(world, client)
        other = client.post("/local/v1/commands/cmd-9/done", headers=world.headers("knovas-connector"), json={"result": "ok"})
        assert other.status_code == 404 and other.json()["error"] == "unknown_command"
        bad = client.post("/local/v1/commands/cmd-9/done", headers=world.headers(), json={"result": "fine"})
        assert bad.status_code == 400 and bad.json()["error"] == "bad_field"
        done = client.post("/local/v1/commands/cmd-9/done", headers=world.headers(), json={"result": "ok"})
        assert done.status_code == 200 and done.json() == {"ok": True}, "the Platform's answer"
        again = client.post("/local/v1/commands/cmd-9/done", headers=world.headers(), json={"result": "ok"})
        assert again.status_code == 404 and again.json()["error"] == "unknown_command"
    assert world.platform.done == [("cmd-9", {"result": "ok", "component": "sync-component"}, "sync-component")]


def test_command_done_while_the_platform_is_away_is_unreachable_and_stays_open(world):
    world.platform.commands = [{"id": "cmd-5", "command": "pause", "args": {}, "component": "sync-component"}]
    with world.running(loops=()) as (client, link):
        client.portal.call(link.beat)
        _beat(world, client)
        world.platform.transport.down = True
        away = client.post("/local/v1/commands/cmd-5/done", headers=world.headers(), json={"result": "ok"})
        assert away.status_code == 503 and away.json()["error"] == "unreachable"
        world.platform.transport.down = False
        assert client.post("/local/v1/commands/cmd-5/done", headers=world.headers(), json={"result": "ok"}).status_code == 200


@pytest.mark.parametrize("change", [{"state": "busy"}, {"lag_seconds": -1}, {"open_errors": "3"}, {"capabilities": "x"}])
def test_heartbeat_refuses_a_bad_field(world, change):
    with world.running(loops=()) as (client, _):
        answer = client.post("/local/v1/heartbeat", headers=world.headers(), json={"state": "online", **change})
    assert answer.status_code == 400 and answer.json()["error"] == "bad_field"


# ------------------------------------------------------------------ configuration and package

def test_config_is_limited_to_the_components_sources_and_points_at_links_relay(world):
    with world.running(loops=()) as (client, _):
        answer = client.get("/local/v1/config", headers=world.headers())
    assert answer.status_code == 200
    config = answer.json()
    assert config["component"] == "sync-component" and config["component"] != LINK_ID
    assert [s["key"] for s in config["sources"]] == ["handbook", "archive"]
    assert [m["id"] for m in config["mappings"]] == ["handbook-pages", "archive-notes"]
    assert config["brain_url"] == "http://knovas-link:8460/local/v1/brain"
    assert set(config) == {"component", "paused", "schedule", "brain_url", "sources", "mappings"}


def test_package_is_limited_to_the_components_workspaces_and_cached(world):
    with world.running(loops=()) as (client, _):
        mine = client.get("/local/v1/package", headers=world.headers())
        assert [w["workspace"] for w in mine.json()["workspaces"]] == ["ws-1"]
        world.platform.transport.down = True
        cached = client.get("/local/v1/package", headers=world.headers("knovas-connector"))
        assert cached.status_code == 200 and [w["workspace"] for w in cached.json()["workspaces"]] == ["ws-2"]
        config = client.get("/local/v1/config", headers=world.headers())
        assert config.status_code == 200 and [s["key"] for s in config.json()["sources"]] == ["handbook", "archive"]


def test_package_never_had_and_platform_away_is_unreachable(world):
    with world.running(loops=()) as (client, _):
        world.platform.transport.down = True
        answer = client.get("/local/v1/package", headers=world.headers())
    assert answer.status_code == 503 and answer.json()["error"] == "unreachable"


# ------------------------------------------------------------------ ping and the jobs long-poll

def test_ping_never_calls_knovas(world):
    with world.running(loops=()) as (client, link):
        world.platform.transport.down = True
        world.secure.transport.down = True
        answer = client.get("/local/v1/ping", headers=world.headers())
    assert answer.status_code == 200
    assert answer.json() == {"version": VERSION, "certificate": "present", "work_channel": "down"}


@pytest.mark.parametrize("query,wait", [("", 25), ("?wait=0", 0), ("?wait=7", 7), ("?wait=99", 25)])
def test_jobs_wait_is_whole_seconds_up_to_25(world, query, wait, monkeypatch):
    seen = []
    with world.running(loops=()) as (client, link):
        async def next_job(name, w):
            seen.append((name, w))
            return None
        monkeypatch.setattr(link.channel, "next_job", next_job)
        assert client.get("/local/v1/jobs" + query, headers=world.headers()).status_code == 204
    assert seen == [("sync-component", wait)]


@pytest.mark.parametrize("query", ["?wait=-1", "?wait=2.5", "?wait=soon"])
def test_jobs_refuses_a_wait_that_is_not_whole_seconds(world, query):
    with world.running(loops=()) as (client, _):
        answer = client.get("/local/v1/jobs" + query, headers=world.headers())
    assert answer.status_code == 400 and answer.json()["error"] == "bad_field"


def test_a_component_that_is_not_registered_gets_no_job(world):
    with world.running(loops=()) as (client, _):
        assert client.get("/local/v1/jobs?wait=0", headers=world.headers()).status_code == 204
