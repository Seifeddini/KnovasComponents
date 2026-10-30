"""Ansichten, KC-2: shared views from the tenant's profiles, who sees which, and the CTO's system map.

Seeding reads the profiles of the tenant's bundle: the Knovas fixture gives the CTO's six views and
the engineer's, the law-firm fixture its board. Names, descriptions and definitions come from the
bundle; no view name or definition key is written in the Platform's Python code.
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from pathlib import Path

import pytest
from flask import Flask

from graph_views import bundle, profiles, service as views, spine_views as sv
from graph_views.settings import ViewSettings
from identity.saved_views import clean_view
from views_fixture import KNOVAS_BUNDLE, KNOVAS_SOURCE, LAWFIRM_BUNDLE, FakeViewsClient, FixtureGraph

SRC = Path(__file__).resolve().parents[1] / "src"
TEMPLATES = SRC / "web_interface" / "templates"
LAWFIRM_SOURCE = f"file:{LAWFIRM_BUNDLE}"


class _NoWait:
    def acquire(self):
        return None


def _bundle_doc(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _platform_views(path: Path) -> list:
    """(profile id, view) of every Platform profile in a fixture, read straight from its JSON."""
    doc = _bundle_doc(path)
    return [(pid, v) for pid, p in sorted(doc["sections"]["profiles"].items())
            if str(p.get("delivered_through", "")).lower().startswith("platform") for v in p["views"]]


class _MemoryStore:
    """SavedViewStore's seeding surface, in memory."""

    def __init__(self):
        self.rows, self.log = {}, []

    def profile_views(self):
        return {v["source"]["profile"]: v for v in self.rows.values()}

    def create(self, user, data, audience="personal"):
        view = {**clean_view(data), "id": str(uuid.uuid4()), "audience": audience}
        view["source"] = {view.pop("source_kind"): view.pop("source_key")}
        self.rows[view["id"]] = view
        self.log.append(("created", view["source"]["profile"]))
        return view

    def update(self, view_id, user, data):
        self.rows[view_id].update(data)
        self.log.append(("updated", self.rows[view_id]["source"]["profile"]))
        return self.rows[view_id]


# -- seeding -----------------------------------------------------------------------------------------


class TestSeed:
    def test_the_knovas_fixture_gives_the_ctos_six_views_and_the_engineers(self):
        store = _MemoryStore()
        got = profiles.seed(store, object())
        expected = [profiles.source_key(pid, v["id"]) for pid, v in _platform_views(KNOVAS_BUNDLE)]
        assert got["created"] == expected and got["updated"] == got["kept"] == []
        by_profile = {}
        for key in got["created"]:
            by_profile.setdefault(key.split("/")[0], []).append(key)
        assert {p: len(k) for p, k in by_profile.items()} == {"cto": 6, "engineer": 1}
        # agent and ci are not delivered through the Platform
        assert not any(k.startswith(("agent/", "ci/")) for k in got["created"])

    def test_names_and_descriptions_come_from_the_profile_in_its_language(self):
        store = _MemoryStore()
        profiles.seed(store, object())
        rows = store.profile_views()
        for pid, v in _platform_views(KNOVAS_BUNDLE):
            row = rows[f"{pid}/{v['id']}"]
            assert row["name"] == v["label"] and row["description"] == " ".join(v["definition"]["description"].split())
            assert row["render_kind"] == v["render"] and row["settings"] == {}

    def test_seeding_is_idempotent_by_profile_and_view_and_follows_a_changed_label(self, tmp_path):
        store = _MemoryStore()
        profiles.seed(store, object())
        again = profiles.seed(store, object())
        assert again["created"] == [] and again["updated"] == [] and len(again["kept"]) == 7
        doc = _bundle_doc(KNOVAS_BUNDLE)
        doc["sections"]["profiles"]["cto"]["views"][0]["label"] = "Systemkarte"
        doc["header"]["content_hash"] = bundle.content_hash(doc["sections"])
        (tmp_path / "bundle.json").write_text(json.dumps(doc), encoding="utf-8")
        changed = bundle.load_file(tmp_path / "bundle.json")
        third = profiles.seed(store, object(), changed)
        first_key = profiles.source_key("cto", doc["sections"]["profiles"]["cto"]["views"][0]["id"])
        assert third["updated"] == [first_key] and store.profile_views()[first_key]["name"] == "Systemkarte"

    def test_the_law_firm_fixture_gives_its_board(self):
        law = bundle.load_file(LAWFIRM_BUNDLE)
        store = _MemoryStore()
        got = profiles.seed(store, object(), law)
        ((pid, view),) = _platform_views(LAWFIRM_BUNDLE)
        assert got["created"] == [f"{pid}/{view['id']}"] and view["render"] == "board"
        profile, pview = profiles.find(got["created"][0], law)
        assert profiles.open_spec(profile, pview, law) == {"kind": "board", "process": view["definition"]["process"]}
        assert store.profile_views()[got["created"][0]]["name"] == view["label"]


# -- opening a view, and the map's levels ----------------------------------------------------------


class TestOpen:
    def test_each_cto_view_opens_with_an_existing_render_kind_or_says_why_not(self):
        b = bundle.current()
        processes = {t: p["key"] for p in b.processes["processes"] for t in p["instance_types"]}
        for pid, v in _platform_views(KNOVAS_BUNDLE):
            profile, view = profiles.find(f"{pid}/{v['id']}")
            spec = profiles.open_spec(profile, view)
            d = v["definition"]
            if v["render"] == "map":
                assert spec == {"kind": "tree", "profile_view": f"{pid}/{v['id']}"}
            elif v["render"] == "board":
                assert spec == {"kind": "board", "process": d["process"]}
            elif v["render"] == "trace":
                assert spec["kind"] == "trace" and spec["process"] == processes[d["root_types"][0]] and spec["note"]
            elif v["render"] == "list":
                assert spec["kind"] == "list" and spec["type"] == d["root_types"][0]
            else:                                           # a matrix over rules: no renderer yet, and said so
                assert spec["kind"] is None and v["render"] in spec["unsupported"]

    def test_the_system_map_levels_follow_the_profile_paths(self):
        _, view = profiles.find(_map_key())
        levels, skipped = profiles.levels(view)
        paths = view["definition"]["paths"]
        assert skipped == []
        assert levels[0]["types"] == view["definition"]["root_types"]
        assert [l["types"] for l in levels[1:]] == [[paths[0]["path"][-1]["to_type"]], [paths[1]["path"][-1]["to_type"]],
                                                    [paths[2]["path"][-1]["to_type"], paths[3]["path"][-1]["to_type"]]]
        last = levels[-1]["links_to_parent"]
        assert last[paths[2]["path"][-1]["to_type"]] == {"relation": paths[2]["path"][-1]["relation"], "direction": "in"}
        assert levels[1]["links_to_parent"][paths[0]["path"][0]["to_type"]]["direction"] == "out"

    def test_a_path_that_extends_nothing_is_listed(self):
        view = {"id": "x", "definition": {"root_types": ["System"], "paths": [
            {"label": "deep", "path": [{"relation": "a", "direction": "in", "to_type": "A"},
                                       {"relation": "b", "direction": "in", "to_type": "B"}]}]}}
        levels, skipped = profiles.levels(view)
        assert len(levels) == 1 and skipped == ["deep: setzt keinen Pfad der Ebene darüber fort"]

    def test_an_unknown_profile_view_is_named(self):
        with pytest.raises(profiles.NotAProfileView, match="nope/none"):
            profiles.find("nope/none")


def _map_key() -> str:
    return next(f"{pid}/{v['id']}" for pid, v in _platform_views(KNOVAS_BUNDLE) if v["render"] == "map")


# -- who sees which ----------------------------------------------------------------------------------


class TestRoles:
    def test_roles_map_to_profiles_by_audience_or_id(self):
        m = profiles.parse_roles("admin=CTO; member=engineer, Engineer;approver=")
        assert m == {"admin": ["cto"], "member": ["engineer", "engineer"], "approver": []}
        assert profiles.profiles_for({"admin"}, m) == {"cto"}
        assert profiles.profiles_for({"member"}, m) == {"engineer"}
        assert profiles.profiles_for({"admin", "member"}, m) == {"cto", "engineer"}
        assert profiles.profiles_for({"approver", "ingestion_manager"}, m) == set()

    def test_without_a_mapping_every_profile_is_seen(self):
        assert profiles.profiles_for({"member"}, {}) is None

    def test_only_profiles_delivered_through_the_platform_count(self):
        assert profiles.profiles_for({"admin"}, profiles.parse_roles("admin=agent,ci")) == set()


# -- the system map ------------------------------------------------------------------------------------


def _map_graph():
    """System → areas → components (one documented, one not) → interfaces, with checks on invariants."""
    rows = {r["component"]: r for r in _bundle_doc(KNOVAS_BUNDLE.parent / "docs-completeness.json")["components"]}
    complete = next(k for k, r in rows.items() if not r["empty_required"] and not r["stale"])
    incomplete = next(k for k, r in rows.items() if r["empty_required"])
    g = FixtureGraph()
    system = g.node("System", "Knovas Semantix", key="SYS-KNOVAS-SEMANTIX")
    area = g.node("BoundedContext", "Retrieve", key="L1-RETRIEVE")
    g.edge(area, "part_of", system)
    good = g.node("Component", f"{complete} — documented", key=complete)
    bare = g.node("Component", f"{incomplete} — not documented", key=incomplete)
    for c in (good, bare):
        g.edge(c, "part_of", area)
    api = g.node("Interface", "POST /secured/query", key="IF-QUERY")
    g.edge(good, "exposes", api)
    g.edge(good, "calls", bare)
    kept = g.node("Invariant", "GI-GRAPH-01 — tenant wall", key="GI-GRAPH-01")
    unchecked = g.node("Invariant", "GI-GRAPH-02 — no widening", key="GI-GRAPH-02")
    g.edge(good, "keeps", kept)
    check = g.node("AlloyCheck", "no_cross_tenant_edge", key="ALLOY-kg#no_cross_tenant_edge", outcome="passed")
    g.edge(check, "verifies", kept)
    g.edge(bare, "keeps", kept)
    g.edge(bare, "keeps", unchecked)
    return g, {"system": system, "area": area, "good": good, "bare": bare, "api": api}


def _svc(graph, **settings):
    client = FakeViewsClient(graph)
    return views.ViewService(lambda: client, ViewSettings(**{"config_source": KNOVAS_SOURCE, **settings}),
                             bucket=_NoWait(), markers=lambda: set()), client


class TestSystemMap:
    def test_the_map_is_the_tree_over_the_profile_paths_coloured_by_both_signals(self):
        g, n = _map_graph()
        svc, _ = _svc(g)
        ctx = svc.context()
        key = _map_key()
        top = sv.profile_map(ctx, key)
        assert [r["id"] for r in top["nodes"]] == [n["system"]] and top["profile_view"] == key
        assert [s["id"] for s in top["signals"]] == ["docs_completeness", "check_status"] and top["unknown_signals"] == []
        areas = sv.profile_map(ctx, key, parent=n["system"], level=top["level"]["id"])
        assert [r["id"] for r in areas["nodes"]] == [n["area"]]
        comps = sv.profile_map(ctx, key, parent=n["area"], level=areas["level"]["id"])
        signals = {r["id"]: r["signals"] for r in comps["nodes"]}
        assert signals[n["good"]] == {"docs_completeness": "complete", "check_status": "passing"}
        assert signals[n["bare"]] == {"docs_completeness": "incomplete", "check_status": "partial"}
        below = sv.profile_map(ctx, key, parent=n["good"], level=comps["level"]["id"])
        assert {r["id"] for r in below["nodes"]} == {n["api"], n["bare"]}      # its interface, and what it calls

    def test_a_failing_check_colours_the_component_red(self):
        g, n = _map_graph()
        for f in g.facts[g.by_key["ALLOY-kg#no_cross_tenant_edge"]]:
            if f["label"] == "outcome":
                f["value"] = "failed"
        svc, _ = _svc(g)
        ctx = svc.context()
        key = _map_key()
        comps = sv.profile_map(ctx, key, parent=n["area"], level=profiles.levels(profiles.find(key)[1])[0][1]["id"])
        assert {r["id"]: r["signals"]["check_status"] for r in comps["nodes"]} == {n["good"]: "failing", n["bare"]: "failing"}

    def test_the_fact_budget_bounds_the_check_reads(self):
        g, n = _map_graph()
        svc, client = _svc(g, map_fact_budget=0)
        ctx = svc.context()
        key = _map_key()
        level = profiles.levels(profiles.find(key)[1])[0][1]["id"]
        comps = sv.profile_map(ctx, key, parent=n["area"], level=level)
        assert {r["signals"]["check_status"] for r in comps["nodes"]} == {"unread"}

    def test_the_route_draws_the_map_and_names_an_unknown_view(self):
        g, n = _map_graph()
        svc, _ = _svc(g)
        app = Flask(__name__, template_folder=str(TEMPLATES))
        from web_interface.views_routes import create_views_blueprint

        app.register_blueprint(create_views_blueprint(None, svc, graph_mode=lambda: True))
        c = app.test_client()
        body = c.get(f"/api/views/tree?view={_map_key()}").get_json()
        assert body["success"] and body["nodes"][0]["id"] == n["system"]
        missing = c.get("/api/views/tree?view=cto/no-such-view")
        assert missing.status_code == 404 and "steht nicht im Definitionsbündel" in missing.get_json()["error"]


# -- no view name or definition key in Python code ------------------------------------------------------------


def _strings(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def test_no_view_name_or_definition_key_is_written_in_python_code():
    names, keys = set(), set()
    for path in (KNOVAS_BUNDLE, LAWFIRM_BUNDLE):
        sections = _bundle_doc(path)["sections"]
        for pid, p in sections["profiles"].items():
            for v in p.get("views") or []:
                names.add(v.get("label") or v["id"])
                keys.add(f"{pid}/{v['id']}")
        keys |= {p["key"] for p in sections["processes"]["processes"]}
        keys |= {s["key"] for s in sections["spines"]["spines"]}
        keys |= {p["key"] for p in sections["pages"]["pages"]}
    # A key is structured (PROC-…, SPINE-…, PAGE-…, <profile>/<view>); a law firm's page key "matter" is
    # also an English word (front matter), so bare words are left to the names check.
    keys = {k for k in keys if "-" in k or "/" in k}
    # the former hard-coded list of shared views
    names |= {"Entwicklungszyklus", "Entwicklungs-Board", "Management-Sicht", "Entscheidungen & Risiken", "Software-Dokumentation"}
    code = sorted(SRC.glob("graph_views/*.py")) + [SRC / "web_interface" / "views_routes.py", SRC / "web_interface" / "app.py",
                                                   SRC / "identity" / "saved_views.py"]
    found = []
    for path in code:
        for text in _strings(path):
            for k in keys:
                if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", text):
                    found.append(f"{path.name}: {k}")
            if text.strip() in names:
                found.append(f"{path.name}: {text.strip()!r}")
    assert found == []


# -- the routes, on the database ---------------------------------------------------------------------------------

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable  # noqa: E402

needs_db = pytest.mark.skipif(not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


@needs_db
class TestSavedProfileViews:
    def _user(self, repo, email, *roles):
        u = repo.create(email=email, display_name=email.split("@")[0], password="korrektes-pferd-batterie")
        for r in roles:
            repo.grant_role(u.id, r)
        return repo.get(u.id)

    def _client(self, platform_db, source=KNOVAS_SOURCE, roles="admin=CTO; member=Engineer"):
        from identity.saved_views import SavedViewStore
        from web_interface.views_routes import create_views_blueprint

        gate = type("G", (), {"user": None, "current_user": lambda self: self.user,
                              "connection": lambda self: platform_db})()
        svc = views.ViewService(lambda: None, ViewSettings(config_source=source, profile_roles=roles))
        app = Flask(__name__, template_folder=str(TEMPLATES))
        app.register_blueprint(create_views_blueprint(gate, svc, graph_mode=lambda: True,
                                                      view_store=lambda: SavedViewStore(platform_db)))
        return app.test_client(), gate

    def test_seeding_is_admin_only_idempotent_and_audited(self, platform_db, identity_repo):
        from identity import audit

        client, gate = self._client(platform_db)
        gate.user = self._user(identity_repo, "ida@knovas.ch", "member")
        assert client.post("/api/views/saved/defaults", json={}).status_code == 403
        gate.user = self._user(identity_repo, "admin@knovas.ch", "admin")
        first = client.post("/api/views/saved/defaults", json={}).get_json()
        assert len(first["created"]) == 7 and first["kept"] == []
        again = client.post("/api/views/saved/defaults", json={}).get_json()
        assert again["created"] == [] and sorted(again["kept"]) == sorted(first["created"])
        created = audit.recent(platform_db, action="view.shared.created")
        assert len(created) == 7 and {r["actor_email"] for r in created} == {"admin@knovas.ch"}

    def test_a_user_sees_the_views_of_their_profiles(self, platform_db, identity_repo):
        client, gate = self._client(platform_db)
        admin = gate.user = self._user(identity_repo, "admin@knovas.ch", "admin")
        client.post("/api/views/saved/defaults", json={})
        mine = client.get("/api/views/saved").get_json()
        assert {v["source"]["profile"].split("/")[0] for v in mine["views"]} == {"cto"} and mine["profiles"] == ["cto"]
        assert len(client.get("/api/views/saved?all=1").get_json()["views"]) == 7
        cto_view = mine["views"][0]["id"]
        gate.user = self._user(identity_repo, "eve@knovas.ch", "member")
        theirs = client.get("/api/views/saved").get_json()["views"]
        assert [v["source"]["profile"] for v in theirs] == ["engineer/feature-trace"]
        assert theirs[0]["profile"] == {"id": "engineer", "audience": "Engineer"} and theirs[0]["open"]["kind"] == "trace"
        assert client.get(f"/api/views/saved/{cto_view}").status_code == 404            # another profile's view
        assert len(client.get("/api/views/saved?all=1").get_json()["views"]) == 1       # ?all is for admins
        gate.user = admin
        assert client.get(f"/api/views/saved/{cto_view}").status_code == 200

    def test_the_cto_and_the_engineer_may_both_have_a_feature_trace(self, platform_db, identity_repo):
        client, gate = self._client(platform_db, roles="")
        gate.user = self._user(identity_repo, "admin@knovas.ch", "admin")
        client.post("/api/views/saved/defaults", json={})
        names = [v["name"] for v in client.get("/api/views/saved").get_json()["views"]]
        assert len(names) == 7 and len(set(names)) == 6                                  # no mapping: all profiles

    def test_the_law_firm_seeds_its_board(self, platform_db, identity_repo):
        client, gate = self._client(platform_db, source=LAWFIRM_SOURCE, roles="")
        gate.user = self._user(identity_repo, "admin@kanzlei.ch", "admin")
        assert client.post("/api/views/saved/defaults", json={}).get_json()["created"] == ["partner/contract-board"]
        (v,) = client.get("/api/views/saved").get_json()["views"]
        assert v["render_kind"] == "board" and v["open"]["kind"] == "board" and v["profile"]["audience"] == "Partner"

    def test_a_profile_views_source_does_not_change(self, platform_db, identity_repo):
        client, gate = self._client(platform_db)
        gate.user = self._user(identity_repo, "admin@knovas.ch", "admin")
        client.post("/api/views/saved/defaults", json={})
        vid = client.get("/api/views/saved").get_json()["views"][0]["id"]
        r = client.patch(f"/api/views/saved/{vid}", json={"source": {"spine": "SPINE-product"}, "render_kind": "tree"})
        assert r.status_code == 400 and "Quelle einer Profilansicht" in r.get_json()["error"]
