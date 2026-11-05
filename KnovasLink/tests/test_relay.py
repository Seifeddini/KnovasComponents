"""Knovas Link's relays (MVP-17, KC-7a): Brain calls to secure-api on an allow-list with the site's certificate, and
reports to the Platform as a sub-component, checked first that they hold only codes and counts."""
from __future__ import annotations

import json
import logging
import sys

import pytest

from conftest import LINK_ROOT
from knovas_link import relay

PLATFORM_SRC = LINK_ROOT.parent / "KnovasPlatform" / "components" / "docbridge_integration" / "src"


# ------------------------------------------------------------------ the allow-list

@pytest.mark.parametrize("path", [
    "secured/graph/nodes", "secured/graph/nodes/0b7c2f9e-1d2a-4c3b-8e4f-5a6b7c8d9e0f/facts", "secured/graph/node-types",
    "secured/graph/edges", "secured/init_document_transmission", "secured/transmit_document_part",
    "secured/delete_information_object", "secured/documents", "secured/documents/source_ref", "secured/folder_rules",
])
def test_allowed_paths(path):
    assert relay.relayed_path(path) == path


@pytest.mark.parametrize("path", [
    "", "secured/graph", "secured/graph/", "secured/documents/original", "secured/connector/jobs",
    "secured/connector/probe", "secured/query", "secured/documents/", "secured/folder_rules/7",
    "secured/graph/../connector/jobs", "secured/graph/./nodes", "secured/graph/%2e%2e/connector/jobs",
    "secured/graph%2Fnodes", "secured/graph/nodes%2F..", "secured/graph/a%5Cb", "secured/graph//nodes",
    "/secured/graph/nodes", "secured/graph/nodes x", "secured/graph/nodes\\..", "secured/sign_certificate",
])
def test_everything_else_is_not_relayed(path):
    assert relay.relayed_path(path) is None


# ------------------------------------------------------------------ the Brain relay

def test_a_relayed_call_reaches_secure_api_unchanged_and_its_answer_comes_back_unchanged(world):
    with world.running(loops=()) as (client, _):
        answer = client.post("/local/v1/brain/secured/graph/nodes?include=core&actor=eyJ4IjoxfQ",
                             headers={**world.headers(), "Content-Type": "application/json", "Idempotency-Key": "k-1"},
                             content=b'{"name":"Page","node_type_id":"t-1"}')
    assert answer.status_code == 200
    assert answer.headers["x-brain-answer"] == "yes"
    assert answer.json() == {"echo": {"method": "POST", "path": "/secured/graph/nodes",
                                      "query": "include=core&actor=eyJ4IjoxfQ", "body": '{"name":"Page","node_type_id":"t-1"}'}}
    (seen,) = world.secure.relayed
    assert seen["headers"]["idempotency-key"] == "k-1" and seen["headers"]["content-type"] == "application/json"
    assert "authorization" not in seen["headers"] and "x-component" not in seen["headers"], "the local token stays local"


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE", "PUT"])
def test_every_method_is_relayed(world, method):
    with world.running(loops=()) as (client, _):
        answer = client.request(method, "/local/v1/brain/secured/graph/facts/f-1", headers=world.headers(),
                                content=b"{}" if method != "GET" else None)
    assert answer.status_code == 200 and answer.json()["echo"]["method"] == method


def test_secure_apis_error_comes_back_unchanged(world):
    with world.running(loops=()) as (client, _):
        answer = client.get("/local/v1/brain/secured/graph/missing", headers=world.headers())
    assert answer.status_code == 404 and answer.json()["error_code"] == "not_found"


def test_a_relayed_path_or_query_never_reaches_a_log_line(world, caplog):
    caplog.set_level(logging.DEBUG)
    with world.running(loops=()) as (client, _):
        client.get("/local/v1/brain/secured/documents?pointer=Vertr%C3%A4ge%2FMuster%20Treuhand%20AG.pdf",
                   headers=world.headers())
        client.delete("/local/v1/brain/secured/graph/nodes/Muster-Treuhand", headers=world.headers())
    assert len(world.secure.relayed) == 2
    for name in ("Muster", "Vertr", "pointer", "secured/documents", "secured/graph"):
        assert name not in caplog.text, name


def test_a_relayed_call_is_never_retried(world):
    with world.running(loops=()) as (client, _):
        answer = client.post("/local/v1/brain/secured/graph/flaky", headers=world.headers(), json={})
    assert answer.status_code == 503 and answer.headers["retry-after"] == "2"
    assert answer.json()["error_code"] == "admission_rejected", "secure-api's own answer"
    assert len(world.secure.relayed) == 1


def test_an_encoded_path_reaches_secure_api_as_it_was_sent(world):
    with world.running(loops=()) as (client, _):
        answer = client.get("/local/v1/brain/secured/graph/nodes/a%20b/facts", headers=world.headers())
    assert answer.status_code == 200
    assert world.secure.relayed[0]["raw_path"] == "/secured/graph/nodes/a%20b/facts"


@pytest.mark.parametrize("path", ["secured/documents/original?pointer=p", "secured/connector/jobs?wait=25",
                                  "secured/query", "secured/graph", "secured/graph/%2e%2e/connector/jobs"])
def test_a_route_off_the_list_is_403_and_never_reaches_secure_api(world, path):
    with world.running(loops=()) as (client, _):
        answer = client.post("/local/v1/brain/" + path, headers=world.headers(), json={})
    assert answer.status_code == 403 and answer.json()["error"] == "not_relayed"
    assert world.secure.relayed == []


def test_a_doubled_slash_after_the_prefix_is_not_relayed(world):
    with world.running(loops=()) as (client, _):
        answer = client.get("/local/v1/brain//secured/graph/nodes", headers=world.headers())
    assert answer.status_code == 403 and answer.json()["error"] == "not_relayed"
    assert world.secure.relayed == []


def test_while_waiting_for_the_certificate_nothing_is_relayed(world):
    world.platform.issue_certificate = False
    with world.running(loops=()) as (client, _):
        answer = client.get("/local/v1/brain/secured/graph/nodes", headers=world.headers())
        refused = client.get("/local/v1/brain/secured/query", headers=world.headers())
    assert answer.status_code == 503 and answer.json()["error"] == "waiting_for_certificate"
    assert refused.status_code == 403 and refused.json()["error"] == "not_relayed"
    assert world.secure.relayed == []


def test_secure_api_away_is_unreachable_and_not_retried(world):
    with world.running(loops=()) as (client, _):
        world.secure.transport.down = True
        answer = client.post("/local/v1/brain/secured/documents", headers=world.headers(), json={})
    assert answer.status_code == 503 and answer.json()["error"] == "unreachable"
    assert world.secure.relayed == []


# ------------------------------------------------------------------ reports

RUN = {"kind": "dry_run", "status": "ok", "started_at": "2026-11-05T10:00:00+00:00", "finished_at": "2026-11-05T10:00:03Z",
       "duration_ms": 3000, "added": 2, "changed": 1, "retired": 0, "errors": 0,
       "held": [{"mapping": "handbook-pages", "retire": {"Page": 7}, "share": 0.5}], "command": None}


@pytest.mark.parametrize("kind,body", [
    ("runs", RUN),
    ("events", {"events": [{"code": "fetch.http_404", "counts": {"files": 3, "m.added": 2}}]}),
    ("checks", {"checks": [{"workspace": "ws-1", "source": "handbook", "code": "source.ok", "counts": {"files": 12}}]}),
])
def test_a_report_is_passed_on_unchanged_as_a_sub_component(world, kind, body):
    raw = json.dumps(body, indent=1).encode()
    with world.running(loops=()) as (client, _):
        answer = client.post(f"/local/v1/report/{kind}", headers={**world.headers(), "Content-Type": "application/json"},
                             content=raw)
    assert answer.status_code == 201, answer.text
    assert answer.json() == ({"id": "run-1"} if kind == "runs" else {"stored": 1}), "the Platform's answer"
    assert world.platform.reports == [(kind, raw, "sync-component")]


@pytest.mark.parametrize("kind,body", [
    ("events", {"events": [{"code": "Bericht Muster AG.pdf", "counts": {}}]}),
    ("events", {"events": [{"code": "fetch.failed", "counts": {"docs/vertrag.docx": 1}}]}),
    ("events", {"events": [{"code": "fetch.failed", "counts": {"Vertrag 2024": 1}}]}),
    ("events", {"events": [{"code": "fetch.failed", "counts": {"vertrag.pdf": 1}}]}),
    ("events", {"events": [{"code": "export.json", "counts": {}}]}),
    ("events", {"events": [{"code": "fetch.failed", "counts": {"files": "3"}}]}),
    ("events", {"events": [{"code": "fetch.failed", "message": "could not read docs/intro.md"}]}),
    ("events", {"events": [{"code": "x" * 70}]}),
    ("events", {"events": [{"code": "ok"}] * 101}),
    ("events", {"events": [], "note": "free text"}),
    ("runs", {**RUN, "kind": "sync"}),
    ("runs", {**RUN, "status": "done"}),
    ("runs", {**RUN, "added": -1}),
    ("runs", {**RUN, "added": True}),
    ("runs", {**RUN, "started_at": "yesterday"}),
    ("runs", {**RUN, "held": [{"mapping": "docs/handbook", "retire": {}}]}),
    ("runs", {**RUN, "held": [{"mapping": "handbook-pages", "retire": {"Page 1.md": 1}}]}),
    ("runs", {**RUN, "held": [{"mapping": "handbook-pages", "path": "docs/intro.md"}]}),
    ("runs", {**RUN, "command": "run docs/intro.md"}),
    ("runs", {**RUN, "error_text": "intro.md is broken"}),
    ("checks", {"checks": [{"workspace": "ws-1", "source": "handbook", "code": "source.ok", "counts": {"Ordner/Akten": 2}}]}),
    ("checks", {"checks": [{"workspace": "ws-1", "source": "Muster Treuhand AG", "code": "source.ok"}]}),
    ("checks", {"checks": [{"workspace": "ws-1", "source": "handbook", "code": "source.ok", "detail": "x"}]}),
    ("checks", {"checks": "source.ok"}),
])
def test_a_report_with_anything_but_codes_and_counts_is_refused(world, kind, body):
    with world.running(loops=()) as (client, _):
        answer = client.post(f"/local/v1/report/{kind}", headers=world.headers(), json=body)
    assert answer.status_code == 400 and answer.json()["error"] == "bad_field"
    assert "docs/" not in answer.json()["message"] and "Muster" not in answer.json()["message"]
    assert world.platform.reports == []


def test_a_report_while_the_platform_is_away_is_unreachable(world):
    with world.running(loops=()) as (client, _):
        world.platform.transport.down = True
        answer = client.post("/local/v1/report/events", headers=world.headers(), json={"events": []})
    assert answer.status_code == 503 and answer.json()["error"] == "unreachable"


def test_an_unknown_report_is_404(world):
    with world.running(loops=()) as (client, _):
        answer = client.post("/local/v1/report/plans", headers=world.headers(), json={})
    assert answer.status_code == 404


def test_a_run_may_name_only_a_command_delivered_to_its_component(world):
    world.platform.commands = [{"id": "cmd-7", "command": "run", "args": {"kind": "dry_run"}, "component": "knovas-connector"}]
    with world.running(loops=()) as (client, link):
        client.portal.call(link.beat)
        client.post("/local/v1/heartbeat", headers=world.headers("knovas-connector"), json={"state": "online"})
        answer = client.post("/local/v1/report/runs", headers=world.headers(), json={**RUN, "command": "cmd-7"})
        assert answer.status_code == 400 and answer.json()["error"] == "bad_field"
        own = client.post("/local/v1/report/runs", headers=world.headers("knovas-connector"), json={**RUN, "command": "cmd-7"})
        assert own.status_code == 201
        assert link.commands.owner("cmd-7") is None, "the run report finishes a run command"


def test_links_report_rules_are_the_platforms():
    sys.path.insert(0, str(PLATFORM_SRC))
    try:
        from builder import components
    finally:
        sys.path.remove(str(PLATFORM_SRC))
    assert relay.EVENT_CODE.pattern == components.EVENT_CODE.pattern
    assert relay.COUNT_KEY.pattern == components.COUNT_KEY.pattern
