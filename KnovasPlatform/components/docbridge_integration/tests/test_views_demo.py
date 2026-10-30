"""The offline Lumora demo (serve_views_demo.py): a fictional client that runs the way Knovas runs itself.

The demo is the real views blueprint over ``demo_data/lumora/graph.json`` (graph_views/offline.py)
and its definitions bundle. These tests hold the demo to what it promises: every view draws from it,
the Definition tab is clean, every process has instances on its board, and nothing in it is Knovas's.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from graph_views import bundle
from graph_views.offline import OfflineClient, OfflineGraph, OfflineGraphError

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo_data" / "lumora"
PROCESSES = ("PROC-feature-lifecycle", "PROC-decision", "PROC-risk-review", "PROC-release",
             "PROC-customer-onboarding", "PROC-sales", "PROC-experiment")


@pytest.fixture(scope="module")
def client():
    import serve_views_demo

    return serve_views_demo.create_app().test_client()


class TestOfflineGraph:
    def test_a_saved_graph_reads_back_with_the_same_ids(self, tmp_path):
        g = OfflineGraph("tenant-1", "Demo")
        g.node("Component", "Inbox", key="L2-INBOX", description="Accepts uploads.", facts={"owner": "ROLE-X"})
        g.node("FeaturePlan", "Plan", key="FEAT-2026-01-01-x", facts={"current_step": ("brief", "2026-01-02")})
        g.edge("FEAT-2026-01-01-x", "affects", "L2-INBOX")
        g.save(tmp_path / "g.json")
        again = OfflineGraph.load(tmp_path / "g.json")
        assert again.by_key == g.by_key and again.edges == g.edges
        dated = next(f for f in again.facts[again.id_of("FEAT-2026-01-01-x")] if f["label"] == "current_step")
        assert dated == {"label": "current_step", "value": "brief", "valid_from": "2026-01-02"}

    def test_the_client_answers_both_the_views_and_brain_runner(self):
        g = OfflineGraph("tenant-1")
        g.type("Component", description="[meta=false] Component")
        nid = g.node("Component", "Inbox", key="L2-INBOX")
        c = OfflineClient(g)
        assert c.customer_id == "tenant-1"
        (status, body), = c.graph_get_many([(f"/nodes/{nid}/facts", None)])
        assert status == 200 and body["facts"][0]["label"] == "key"
        types = c.get("secured/graph/node-types")["node_types"]
        assert types == [{"id": g.types["Component"]["id"], "name": "Component", "description": "[meta=false] Component"}]
        attrs = c.get(f"secured/graph/node-types/{types[0]['id']}/schema")["attributes"]
        assert [a["name"] for a in attrs] == ["key"] and all(a["id"] for a in attrs)
        with pytest.raises(RuntimeError):
            c.get("secured/graph/nodes/nope/facts")

    def test_another_file_is_refused(self):
        with pytest.raises(OfflineGraphError):
            OfflineGraph.from_json({"format": "something-else"})


class TestTheDemoData:
    def test_the_bundle_is_verified_and_labelled_lumora(self):
        b = bundle.load_file(DEMO / "bundle.json")
        assert b.label == "LUMORA" and b.header["label"]["name"] == "Lumora AG"
        assert set(b.report_paths) == {"process_completeness", "docs_completeness"}

    def test_nothing_in_the_demo_is_knovas(self):
        names = re.compile(r"Knovas|Siran|Jiannis|Seifeddini|Sarlos|af639810|Cortex|Nexus|cloudscale", re.I)
        sections = json.loads((DEMO / "bundle.json").read_text(encoding="utf-8"))["sections"]
        assert not names.search(json.dumps(sections, ensure_ascii=False))
        for name in ("graph.json", "process-completeness.json", "docs-completeness.json", "findings.json"):
            text = (DEMO / name).read_text(encoding="utf-8").replace("knovas-offline-graph", "")
            assert not names.search(text), name


class TestTheDemoServes:
    def test_the_page_loads(self, client):
        r = client.get("/views")
        assert r.status_code == 200 and b"Lumora AG (Demo)" in r.data

    def test_the_definition_tab_is_clean(self, client):
        body = client.get("/api/views/definitions").get_json()
        assert body["source"] == "tenant"
        assert body["errors"] == [] and body["gaps"] == [] and body["differences"] == []
        assert sorted(p["key"] for p in body["processes"]) == sorted(PROCESSES) and len(body["spines"]) == 4
        assert body["pinned"]["label"] == "LUMORA"

    @pytest.mark.parametrize("process", PROCESSES)
    def test_every_process_has_instances_on_its_board(self, client, process):
        body = client.get(f"/api/views/board?process={process}").get_json()
        assert body["success"] and body["total"] >= 3 and body["unplaced"] == []

    def test_the_planned_gaps_show(self, client):
        rows = json.loads((DEMO / "process-completeness.json").read_text(encoding="utf-8"))["instances"]
        intake = next(r for r in rows if r["instance"] == "FEAT-2026-10-21-email-intake")
        assert {m["type"] for m in intake["missing"]} == {"Record", "Invariant", "AlloyCheck"}
        risk = next(r for r in rows if r["instance"] == "RISK-3")
        assert {u["id"] for u in risk["unmet"]} == {"P-review"}


def test_a_bundle_label_written_as_the_company_reads_as_its_key():
    b = bundle.Bundle({"label": {"key": "ACME", "name": "Acme AG", "kind": "x"}}, {}, "file:x")
    assert b.label == "ACME"
    assert bundle.Bundle({"label": "KNOVAS"}, {}, "file:x").label == "KNOVAS"
