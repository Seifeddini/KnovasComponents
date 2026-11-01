"""Operating a published Brain (build plan step B10): health, conditions, history, tidiness, coverage, routes.

A small tenant graph is seeded the way brain-runner's snapshot gives it (builder_harness.seed_graph): the sample
package's client, mandates and deadlines. test_builder_screens.py runs the stage on Client Zero in a browser.
"""
import datetime as dt
import io

import pytest

from builder import editing, operate, package
from builder_harness import SAMPLE_PACKAGE, FakeEngine, MemoryWorkspaceStore, StandInUser, make_app, seed_graph

NOW = dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)
RECENT = "2026-10-30T09:00:00+00:00"
OLD = "2026-05-01T09:00:00+00:00"

SEED = {
    "nodes": [
        {"type": "Organization", "key": "ORG-muster", "name": "Muster AG", "facts": {"legal_form": "ag"}},
        {"type": "Mandate", "key": "M-1", "name": "Mandat Muster", "description": "Steuern 2026",
         "facts": {"owner": "PERS-anna", "status": "open"}},
        {"type": "Mandate", "key": "M-2", "name": "Mandat Meier", "facts": {"status": "active"}},
        {"type": "Mandate", "key": "M-3", "name": "Mandat Meyer", "facts": {"owner": "PERS-ben", "status": "closed"}},
        {"type": "Deadline", "key": "D-1", "name": "Frist Steuererklärung", "facts": {"due_on": "2026-11-30"}},
        {"type": "Deadline", "key": "D-9", "name": "Frist ohne Mandat", "facts": {"due_on": "2026-12-31"}},
    ],
    "edges": [
        {"from": "ORG-muster", "relation": "client_of", "to": "M-1"},
        {"from": "ORG-muster", "relation": "client_of", "to": "M-2"},
        {"from": "D-1", "relation": "due_in", "to": "M-1"},
    ],
}


@pytest.fixture
def model():
    return package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model


@pytest.fixture
def graph():
    return operate.Graph(seed_graph(SEED, at=RECENT, ages={"M-3": OLD}))


LINKS = {"type": lambda k: f"/o#type:{k}", "page": lambda k: f"/v#page:{k}", "spine": lambda k: f"/v#spine:{k}",
         "mapping": lambda t: "/z/dms-mandates" if t == "Mandate" else None}


class TestHealth:
    def test_three_numbers_owner_and_review(self, model, graph):
        [mandate] = operate.health(model, graph, now=NOW, reviews={}, published_at=None)
        assert mandate["type"] == "Mandate" and mandate["nodes"] == 3 and mandate["owner_role"] == "ROLE-PARTNER"
        # owner and status on M-1 and M-3; M-2 has no owner.
        assert mandate["completeness"] == 67 and mandate["detail"]["complete"] == 2
        # M-3 was last confirmed in May: older than stale_after (90 d).
        assert mandate["correctness"] == 67
        # P-owner (fact_present owner) is checked here: two of three pass.
        assert mandate["compliance"] == 67 and mandate["detail"]["checks"] == 3
        # The package's last_review starts the 30-day rhythm; a review recorded later moves it.
        assert mandate["due"].startswith("2026-10-31") and mandate["overdue"]
        [later] = operate.health(model, graph, now=NOW, reviews={"Mandate": {"at": "2026-10-30T08:00:00+00:00",
                                                                           "actor_name": "Anna"}}, published_at=None)
        assert later["due"].startswith("2026-11-29") and not later["overdue"] and later["reviewed_by"] == "Anna"

    def test_a_type_without_nodes_has_no_numbers(self, model):
        [mandate] = operate.health(model, operate.Graph({}), now=NOW, reviews={}, published_at=None)
        assert mandate["nodes"] == 0 and mandate["completeness"] is None and mandate["compliance"] is None

    def test_rules_this_screen_cannot_check_are_named(self, model, graph):
        model["health"]["types"]["Mandate"]["compliance"] = ["P-owner", "R3"]
        [mandate] = operate.health(model, graph, now=NOW, reviews={}, published_at=None)
        assert mandate["detail"]["unevaluable"] == ["R3"] and mandate["detail"]["checks"] == 3
        evaluation = {"instances": [{"key": "M-1", "rule_results": {"R3": {"passed": True}}}]}
        [mandate] = operate.health(model, graph, now=NOW, reviews={}, published_at=None, evaluation=evaluation)
        assert mandate["detail"]["checks"] == 4 and mandate["detail"]["passed"] == 3


class TestTidiness:
    def test_every_kind(self, model, graph):
        editing.apply(model, {"op": "type.create", "key": "Vorgang", "parent": "Organization"})
        editing.apply(model, {"op": "attribute.add", "key": "Vorgang", "attribute": {"name": "zeichen"}})
        lint = operate.lint(model, graph, now=NOW, links=LINKS)
        assert [i["target"] for i in lint["type_undescribed"]["items"]] == ["Vorgang"]
        assert lint["type_undescribed"]["items"][0]["url"] == "/o#type:Vorgang"
        assert "Vorgang.zeichen" in [i["target"] for i in lint["attribute_undescribed"]["items"]]
        # PAGE-mandate's description is required: M-2 and M-3 have none.
        assert sorted(i["target"] for i in lint["slot_empty"]["items"]) == ["M-2", "M-3"]
        assert lint["slot_empty"]["items"][0]["url"] == "/v#page:PAGE-mandate"
        # M-3 and D-9 hang on nothing.
        assert sorted(i["target"] for i in lint["orphan"]["items"]) == ["D-9", "M-3"]
        assert next(i for i in lint["orphan"]["items"] if i["target"] == "M-3")["where"] == "Zuordnung"
        assert [i["target"] for i in lint["duplicate"]["items"]] == ["M-2 / M-3"]
        assert [i["target"] for i in lint["stale_claim"]["items"]] == ["M-3"]
        assert lint["wrong_level"]["count"] == 0

    def test_knowledge_on_the_wrong_level(self, model):
        seeded = {"nodes": SEED["nodes"] + [{"type": "Deadline", "key": "D-2", "name": "Frist am Mandanten"}],
                  "edges": SEED["edges"] + [{"from": "D-2", "relation": "due_in", "to": "ORG-muster"}]}
        lint = operate.lint(model, operate.Graph(seed_graph(seeded, at=RECENT)), now=NOW, links=LINKS)
        [item] = lint["wrong_level"]["items"]
        assert item["target"] == "D-2" and "darüber erwartet: Mandate" in item["message"]
        assert item["url"] == "/v#spine:SPINE-client"

    @pytest.mark.parametrize("a,b,near", [("mandat meier", "mandat meyer", True), ("muster ag", "muster ag", True),
                                          ("release 1", "release 2", False), ("wait max", "wait min", False),
                                          ("mandat muster", "mandat musterx", True), ("abc", "abd", False)])
    def test_near_names(self, a, b, near):
        assert operate._near(a, b) is near


class TestCoverage:
    def test_levels_down_slots_across(self, model, graph):
        cov = operate.coverage(model, graph, None, now=NOW)
        assert cov["spine"] == "SPINE-client" and cov["columns"] == ["Beschreibung", "Fristen"]
        client, mandate, deadline = cov["rows"]
        assert client["page"] is None and client["cells"] == {}
        # Three mandates: one described (missing for two), one with a deadline.
        assert mandate["cells"]["Beschreibung"] == {"state": "missing", "filled": 1, "nodes": 3, "stale": 0, "required": True}
        assert mandate["cells"]["Fristen"]["state"] == "missing"
        assert deadline["nodes"] == 2 and deadline["page"] is None

    def test_a_filled_slot_of_old_nodes_is_stale(self, model):
        old = {"nodes": [{"type": "Mandate", "key": "M-1", "name": "Mandat", "description": "x"}], "edges": []}
        cov = operate.coverage(model, operate.Graph(seed_graph(old, at=OLD)), "SPINE-client", now=NOW)
        assert cov["rows"][1]["cells"]["Beschreibung"]["state"] == "stale"


class TestConditionsAndHistory:
    STATE = {"mappings": {"dms-mandates": {
        "conditions": {"Ready": {"status": "False"}, "Stale": {"status": "False"},
                       "Degraded": {"status": "True", "reason": "DeletionGuard", "message": "would retire 8 of 10"}},
        "observed": {"added": 1, "modified": 2, "retired": 0, "drift": 1, "nodes_in_brain": 10, "source_items": 11},
        "last_run_at": "2026-10-31T07:00:00+00:00", "last_success_at": "2026-10-30T07:00:00+00:00"}}}
    RUNS = [{"id": "r2", "kind": "probe", "status": "ok", "started_by": "anna@example.ch",
             "started_at": "2026-10-31T07:00:00+00:00", "finished_at": "2026-10-31T07:01:00+00:00",
             "counts": {"dms-mandates": {"add": 1, "change": 2, "retire": 8, "blocked": True, "guard_tripped": True}},
             "result": "Probelauf: 1 Zuordnungen geplant"},
            {"id": "r1", "kind": "apply", "status": "ok", "started_by": None, "started_at": "2026-10-30T07:00:00+00:00",
             "finished_at": "2026-10-30T07:02:00+00:00", "counts": {"dms-mandates": {"add": 3}}, "result": "angewendet"}]

    def test_condition_counts_errors_and_the_held_plan(self, model):
        status = operate.mappings_status(model, state=self.STATE, sync_runs=self.RUNS, builder_runs=[])
        [row] = status["mappings"]
        assert row["condition"] == "Degraded" and row["errors"] == ["would retire 8 of 10"]
        assert row["counts"]["modified"] == 2 and row["counts"]["in_brain"] == 10
        assert row["held"]["why"] == "Löschschutz" and row["held"]["retire"] == 8 and row["held"]["by"] == "anna@example.ch"
        assert status["counts"]["Degraded"] == 1 and len(status["held"]) == 1

    def test_without_a_sync_a_mapping_has_no_condition(self, model):
        [row] = operate.mappings_status(model, state=None, sync_runs=[], builder_runs=[])["mappings"]
        assert row["condition"] == "Unbekannt" and row["held"] is None

    def test_history_joins_publishes_syncs_and_run_records(self):
        graph = operate.Graph(seed_graph({"nodes": [{"type": "RunEvent", "key": "EVT-runner-dms-1",
                                                     "facts": {"occurred_at": "2026-10-31T08:00:00+00:00",
                                                               "condition": "Ready", "observed": "3 written"}}]}))
        builder_runs = [{"kind": "apply", "status": "ok", "actor_name": "Alex", "at": "2026-10-29T10:00:00+00:00",
                         "summary": {"published_number": 2, "written": 5, "refused": []}}]
        rows = operate.history(builder_runs, self.RUNS, graph)
        assert [r["kind"] for r in rows] == ["run_event", "sync_probe", "sync_apply", "publish"]
        assert rows[1]["who"] == "anna@example.ch" and "−8" in rows[1]["changed"]
        assert rows[3]["what"] == "Version 2 veröffentlicht" and rows[3]["who"] == "Alex"


class TestTheStage:
    @pytest.fixture
    def world(self):
        user = StandInUser({"admin"}, "Alex Beispiel")
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        engine = FakeEngine()
        engine.graph = seed_graph(SEED, at=RECENT, ages={"M-3": OLD})
        app = make_app(user, store, tenant="t-1", engine=engine, sync={"state": TestConditionsAndHistory.STATE,
                                                                       "runs": TestConditionsAndHistory.RUNS})
        client = app.test_client()
        data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
        client.post("/builder/api/import", data={"package": (io.BytesIO(data), "p.zip"), "name": "Beispiel AG"},
                    content_type="multipart/form-data")
        [workspace] = store.list()
        store.link_tenant(workspace.id, "t-1")
        draft = store.draft(workspace.id)
        store.publish(workspace.id, user.id, draft["content_hash"], {"sections": {}}, "h")
        return client, store, workspace.id

    def test_the_stage_shows_real_numbers(self, world):
        client, _, _ = world
        page = client.get("/builder/betrieb").get_data(as_text=True)
        assert "Gesundheit je Typ" in page and "6 Knoten gelesen" in page
        assert 'data-op-type="Mandate"' in page and "67" in page
        assert 'data-op-mapping="dms-mandates"' in page and "Löschschutz: 8 stilllegen" in page
        assert 'data-op-lint="orphan"' in page and "Beheben: Zuordnung" in page

    def test_a_review_moves_the_next_one(self, world):
        client, store, wid = world
        assert client.post(f"/builder/api/w/{wid}/reviews", json={"type": "Nowhere"}).status_code == 404
        assert client.post(f"/builder/api/w/{wid}/reviews", json={"type": "Mandate"}).status_code == 200
        assert store.reviews(wid)["Mandate"]["actor_name"] == "Alex Beispiel"

    def test_before_a_publish_the_stage_is_empty(self):
        user = StandInUser({"admin"})
        store = MemoryWorkspaceStore()
        client = make_app(user, store, tenant="t-1").test_client()
        data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
        client.post("/builder/api/import", data={"package": (io.BytesIO(data), "p.zip"), "name": "Beispiel AG"},
                    content_type="multipart/form-data")
        assert "Noch nichts veröffentlicht" in client.get("/builder/betrieb").get_data(as_text=True)
