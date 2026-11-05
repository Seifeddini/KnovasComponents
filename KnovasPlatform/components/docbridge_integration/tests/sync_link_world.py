"""A made-up company for the sync component's link-mode tests (MVP-20 … MVP-22): its handbook of policy pages, kept in
the GitHub repository ``example-org/handbook`` and on a mounted share; a configuration package with one mapping,
``policy-pages``, whose ``summary`` and ``reviewed_on`` may be written back; and the world the tests run in: an
in-process Knovas Link, an in-process GitHub, and the real component in link mode. A test helper only.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import yaml

SYNC_SRC = Path(__file__).resolve().parents[2] / "sync_component" / "src"
if str(SYNC_SRC) not in sys.path:
    sys.path.insert(0, str(SYNC_SRC))

from fake_github_rest import FakeGitHub, blob_sha  # noqa: E402
from fake_knovas_link import FakeLink, files_of, stop  # noqa: E402

from sync_component.agent import CAPABILITIES, Agent  # noqa: E402
from sync_component.engine import RunnerEngine  # noqa: E402
from sync_component.jobs import JOB_CAPABILITIES, JOB_KINDS, WRITABLE_KINDS, JobWorker  # noqa: E402
from sync_component.link_client import LinkClient  # noqa: E402

COMPONENT = "sync-component"
TOKEN = "local-token-" + "x" * 32
WORKSPACE = "ws-1"
GITHUB_KEY = "handbook"
SHARE_KEY = "handbook-share"
MAPPING = "policy-pages"
AGENT = "agent-research-assistant"
PERSON = "user-0042"

TERMS = {
    "common_attributes": [{"name": n, "datatype": t} for n, t in (("key", "text"), ("source", "text"), ("source_key", "text"),
                                                                  ("mapping", "text"), ("in_source", "text"), ("last_seen", "date"))],
    "node_types": [{"name": "Policy", "attributes": [
        {"name": "summary", "datatype": "text"}, {"name": "reviewed_on", "datatype": "date"},
        {"name": "state", "datatype": "enum", "enum_values": ["draft", "active", "retired"], "computed": True}]}],
}
POLICY_PAGES = {
    "id": MAPPING, "version": 1,
    "source": {"extractor": "markdown_front_matter", "glob": "policies/*.md", "select": "@", "as": "p"},
    "freshness": {"window": "1 d"}, "deletion_guard": {"max_retire_share": 0.9, "min_retire": 5}, "edit_policy": "source_owned",
    "emits": [{"type": "Policy", "identify": ["key"], "key": "p.front_matter.id", "name": "p.title",
               "facts": {"summary": {"value": "p.front_matter.summary", "priority": 10},
                         "reviewed_on": {"value": "p.front_matter.reviewed_on", "priority": 10},
                         "state": {"value": "p.front_matter.state", "priority": 10}}}],
    "writes": {"Policy": {"summary": {"field": "front_matter.summary"}, "reviewed_on": {"field": "front_matter.reviewed_on"}}},
}
MANIFEST = {
    "version": 1, "company": {"key": "EXAMPLE", "name": "Example Org", "kind": "company"},
    "ontology": {"terms": "ontology/terms.yaml", "relations": "ontology/relations.yaml",
                 "descriptions": "ontology/descriptions.yaml", "node_descriptions": "ontology/node-descriptions.yaml"},
    "processes": "ontology/processes.yaml", "spines": "ontology/spines.yaml", "pages": "ontology/page-definitions.yaml",
    "owners": "ontology/owners.yaml", "edit_sources": "ontology/edit-sources.yaml", "mappings": "mappings/*.yaml",
    "profiles": "profiles/*.yaml", "health": "health.yaml", "runner": {"state": ".brain-runner/state.json"},
}
TRAVEL = (b"---\r\nid: POLICY-travel\r\nsummary: Book the cheapest fare.  # checked yearly\r\nreviewed_on: 2026-01-10\r\n"
          b"state: active\r\n---\r\n# Travel\r\n\r\nsummary: the body says it again.\r\n")
REMOTE = b"---\nid: POLICY-remote\nsummary: 'Work from home on Fridays.'\nstate: draft\n---\n# Remote work\n"
POLICIES = {"policies/travel.md": TRAVEL, "policies/remote.md": REMOTE}


def write_config(folder: Path) -> Path:
    for rel, doc in {"ontology/terms.yaml": TERMS, "ontology/relations.yaml": {"relations": []},
                     f"mappings/{MAPPING}.yaml": POLICY_PAGES, "brain-config.yaml": MANIFEST}.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return folder / "brain-config.yaml"


def deadline(seconds: int = 60) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_job(external_id: str, version: str | None = None, max_bytes: int = 26214400, source: str = GITHUB_KEY) -> dict:
    args = {"external_id": external_id, "max_bytes": max_bytes}
    if version is not None:
        args["version"] = version
    return {"id": str(uuid.uuid4()), "kind": "fetch", "source": source, "target_serial": None, "deadline": deadline(),
            "actor": {"kind": "agent", "id": AGENT, "on_behalf_of": {"kind": "user", "id": PERSON}}, "args": args}


def write_job(facts: dict, expected_version: str, external_id: str = "policies/travel.md", node_key: str = "POLICY-travel",
              mapping: str = MAPPING, source: str = GITHUB_KEY) -> dict:
    return {"id": str(uuid.uuid4()), "kind": "write", "source": source, "target_serial": None, "deadline": deadline(),
            "actor": {"kind": "user", "id": PERSON, "on_behalf_of": {"kind": "agent", "id": AGENT}},
            "args": {"mapping": mapping, "node_key": node_key, "external_id": external_id,
                     "expected_version": expected_version, "facts": facts, "mode": "proposal"}}


def probe_job() -> dict:
    return {"id": str(uuid.uuid4()), "kind": "probe", "deadline": deadline(30),
            "actor": {"kind": "component", "id": "knovas-link"}, "args": {}}


def runner_env() -> dict[str, str]:
    """The runner's command line in a subprocess finds this component and brain-runner as in the image."""
    return {"PYTHONPATH": os.pathsep.join([str(SYNC_SRC), *filter(None, [os.environ.get("PYTHONPATH")])])}


def build(tmp_path: Path, monkeypatch, *, github_access: str = "read_write", fetch: bool = True,
          credential: str | None = "write-token", run=None) -> SimpleNamespace:
    """The world of one link-mode test. ``run`` replaces the runner's subprocess (default: the real runner)."""
    config = tmp_path / "package"
    write_config(config)
    share = tmp_path / "share"
    for rel, data in POLICIES.items():
        (share / rel).parent.mkdir(parents=True, exist_ok=True)
        (share / rel).write_bytes(data)
    monkeypatch.setenv("SYNC_FILES_ROOT", str(share))
    github = FakeGitHub()
    base_commit = github.commit(dict(POLICIES))
    gh_served = github.serve()
    monkeypatch.setenv("BRAIN_GITHUB_API", gh_served.url)
    sources = [
        {"workspace": WORKSPACE, "key": GITHUB_KEY, "kind": "github", "access": github_access, "fetch": fetch,
         "settings": {"repository": "example-org/handbook", "ref": "main"}, "component": COMPONENT},
        {"workspace": WORKSPACE, "key": SHARE_KEY, "kind": "knovas_connector", "access": "read", "fetch": False,
         "settings": {"folder": ""}, "component": COMPONENT},
    ]
    mappings = [{"id": MAPPING, "workspace": WORKSPACE, "source": SHARE_KEY}]
    link = FakeLink(tokens={COMPONENT: TOKEN}, sources=sources, mappings=mappings,
                    package={"workspaces": [{"workspace": WORKSPACE, "files": files_of(config)}]})
    served = link.serve()
    client = LinkClient(served.url, COMPONENT, TOKEN)
    client.register(CAPABILITIES + JOB_CAPABILITIES, JOB_KINDS)
    env = {"BRAIN_API_URL": served.url + "/local/v1/brain", "BRAIN_LOCAL_TOKEN": TOKEN, "BRAIN_COMPONENT_NAME": COMPONENT,
           **runner_env()}
    engine = RunnerEngine(tmp_path / "data" / "sources", env, keep_stream=True, run=run)
    environ = {"SYNC_CREDENTIAL_GITHUB": credential} if credential else {}
    agent = Agent(client, engine, environ=environ, writable_kinds=WRITABLE_KINDS,
                  capabilities=CAPABILITIES + JOB_CAPABILITIES)
    worker = JobWorker(client, engine, environ=environ)

    def close() -> None:
        worker.close()
        stop(served)
        stop(gh_served)

    return SimpleNamespace(link=link, url=served.url, client=client, engine=engine, agent=agent, worker=worker,
                           github=github, base_commit=base_commit, share=share, config=config, close=close,
                           travel_version=blob_sha(TRAVEL), remote_version=blob_sha(REMOTE))


def run_job(world: SimpleNamespace, job: dict) -> dict | None:
    """Queue the job at Link, let the component take it through its long-poll and answer it; Link's record of the
    result (None when Link took none)."""
    world.link.queue(COMPONENT, job)
    found = world.client.next_job(wait=5)
    assert found is not None and found["id"] == job["id"]
    world.worker.handle(found)
    return world.link.results.get(job["id"])
