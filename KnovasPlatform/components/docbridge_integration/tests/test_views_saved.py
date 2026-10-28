"""Ansichten, Step 5: saved views as Platform configuration.

Personal views are their owner's alone; shared views are read by everyone and
changed by admins only; every change to a shared view is in audit_log (who,
when, what); a view stores presentation settings and nothing else.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from flask import Flask

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = [
    pytest.mark.skipif(not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}"),
]

from identity import audit  # noqa: E402
from identity.saved_views import (  # noqa: E402
    InvalidView, SavedViewStore, ViewForbidden, ViewNameTaken, ViewNotFound, clean_settings)

PASSWORD = "korrektes-pferd-batterie"
BOARD = {"name": "Entwicklungs-Board", "description": "Alle Feature-Pläne",
         "source": {"process": "PROC-feature-lifecycle"}, "render_kind": "board",
         "settings": {"filters": {"owner": "Siran"}, "sorting": "missing", "threshold": 5}}


@pytest.fixture
def store(platform_db):
    return SavedViewStore(platform_db)


def _user(repo, name, admin=False):
    u = repo.create(email=f"{name}@kanzlei.ch", display_name=name.title(), password=PASSWORD)
    if admin:
        repo.grant_role(u.id, "admin")
    return repo.get(u.id)


@pytest.fixture
def alice(identity_repo):
    return _user(identity_repo, "alice")


@pytest.fixture
def bob(identity_repo):
    return _user(identity_repo, "bob")


@pytest.fixture
def admin(identity_repo):
    return _user(identity_repo, "admin-anna", admin=True)


def _audit(conn):
    rows = []
    for action in ("created", "updated", "deleted", "shared"):
        rows += audit.recent(conn, action=f"view.shared.{action}")
    return rows


class TestPersonal:
    def test_save_and_reopen(self, store, alice):
        v = store.create(alice, BOARD)
        again = store.get(v["id"], alice)
        assert again["name"] == "Entwicklungs-Board" and again["audience"] == "personal"
        assert again["source"] == {"process": "PROC-feature-lifecycle"} and again["render_kind"] == "board"
        assert again["settings"] == {"filters": {"owner": "Siran"}, "sorting": "missing", "threshold": 5}

    def test_nobody_else_sees_a_personal_view(self, store, alice, bob, admin):
        v = store.create(alice, BOARD)
        for other in (bob, admin):
            with pytest.raises(ViewNotFound):
                store.get(v["id"], other)
            assert v["id"] not in [x["id"] for x in store.list_for(other)]

    def test_the_owner_changes_and_deletes_it_without_an_audit_entry(self, store, alice, platform_db):
        v = store.create(alice, BOARD)
        store.update(v["id"], alice, {"name": "Mein Board"})
        store.delete(v["id"], alice)
        assert _audit(platform_db) == []

    def test_names_are_unique_per_owner(self, store, alice, bob):
        store.create(alice, BOARD)
        with pytest.raises(ViewNameTaken):
            store.create(alice, {**BOARD, "name": "entwicklungs-board"})
        store.create(bob, BOARD)            # bob's own namespace


class TestShared:
    def test_only_admins_create_shared_views(self, store, alice, admin):
        with pytest.raises(ViewForbidden):
            store.create(alice, BOARD, audience="shared")
        v = store.create(admin, BOARD, audience="shared")
        assert v["audience"] == "shared" and v["owner_user_id"] is None

    def test_everyone_reads_a_shared_view(self, store, admin, alice):
        v = store.create(admin, BOARD, audience="shared")
        assert store.get(v["id"], alice)["name"] == BOARD["name"]
        assert [x["id"] for x in store.list_for(alice)] == [v["id"]]

    def test_only_admins_change_or_delete_it(self, store, admin, alice):
        v = store.create(admin, BOARD, audience="shared")
        with pytest.raises(ViewForbidden):
            store.update(v["id"], alice, {"name": "Gekapert"})
        with pytest.raises(ViewForbidden):
            store.delete(v["id"], alice)

    def test_every_change_is_audited_with_who_and_what(self, store, admin, platform_db):
        v = store.create(admin, BOARD, audience="shared")
        store.update(v["id"], admin, {"settings": {**BOARD["settings"], "threshold": 8}})
        store.delete(v["id"], admin)
        rows = _audit(platform_db)
        assert sorted(r["action"] for r in rows) == ["view.shared.created", "view.shared.deleted",
                                                     "view.shared.updated"]
        assert {r["actor_email"] for r in rows} == {"admin-anna@kanzlei.ch"}
        assert all(r["occurred_at"] is not None and r["target_id"] == v["id"] for r in rows)
        updated = next(r for r in rows if r["action"] == "view.shared.updated")
        assert updated["detail"]["changed"] == ["settings"]
        assert updated["detail"]["before"]["settings"]["threshold"] == 5
        assert updated["detail"]["after"]["settings"]["threshold"] == 8

    def test_share_makes_a_shared_copy_and_says_so(self, store, admin, alice, platform_db):
        mine = store.create(admin, BOARD)
        shared = store.share(mine["id"], admin, name="Board für alle")
        assert shared["audience"] == "shared" and store.get(mine["id"], admin)["audience"] == "personal"
        assert store.get(shared["id"], alice)["name"] == "Board für alle"
        shared_rows = [r for r in _audit(platform_db) if r["action"] == "view.shared.shared"]
        assert shared_rows[0]["detail"]["from_personal"] == mine["id"]

    def test_a_non_admin_cannot_share(self, store, alice):
        mine = store.create(alice, BOARD)
        with pytest.raises(ViewForbidden):
            store.share(mine["id"], alice)


class TestOnlyPresentation:
    def test_unknown_settings_are_refused(self):
        with pytest.raises(InvalidView, match="nur Darstellung"):
            clean_settings({"facts": [{"label": "status", "value": "secret"}]})

    def test_settings_are_checked(self):
        for bad in ({"threshold": 0}, {"threshold": "x"}, {"colours": {"Risk": "red"}},
                    {"filters": {"query": "x"}}, {"sorting": "random"}, {"default_root": "not-an-id"},
                    {"hidden_types": "Risk"}):
            with pytest.raises(InvalidView):
                clean_settings(bad)

    def test_good_settings_pass(self):
        root = str(uuid.uuid4())
        assert clean_settings({"default_root": root, "threshold": 7, "hidden_types": ["Event", "Commit"],
                               "colours": {"Risk": "#BE123C"}, "filters": {"meta": 1, "rows": "L3"}}) == {
            "default_root": root, "threshold": 7, "hidden_types": ["Commit", "Event"],
            "colours": {"Risk": "#be123c"}, "filters": {"meta": True, "rows": "L3"}}

    def test_a_process_draws_as_trace_or_board_a_spine_as_tree_or_matrix(self, store, alice):
        with pytest.raises(InvalidView):
            store.create(alice, {**BOARD, "render_kind": "tree"})
        with pytest.raises(InvalidView):
            store.create(alice, {**BOARD, "source": {"spine": "PROC-feature-lifecycle"}})
        assert store.create(alice, {**BOARD, "name": "Baum", "source": {"spine": "SPINE-product"},
                                    "render_kind": "tree"})["render_kind"] == "tree"

    def test_the_source_is_a_definition_key(self, store, alice):
        with pytest.raises(InvalidView):
            store.create(alice, {**BOARD, "source": {"process": "DROP TABLE"}})


# -- routes ------------------------------------------------------------------------


class _Gate:
    def __init__(self, conn):
        self.conn, self.user = conn, None

    def current_user(self):
        return self.user

    def connection(self):
        return self.conn


@pytest.fixture
def app(platform_db):
    from graph_views import service as views
    from graph_views.settings import ViewSettings
    from web_interface.views_routes import create_views_blueprint

    gate = _Gate(platform_db)
    flask_app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
    flask_app.register_blueprint(create_views_blueprint(
        gate, views.ViewService(lambda: None, ViewSettings()), graph_mode=lambda: True,
        view_store=lambda: SavedViewStore(platform_db)))
    flask_app.gate = gate
    return flask_app


class TestRoutes:
    def test_save_reopen_share_and_audit(self, app, alice, admin):
        client = app.test_client()
        app.gate.user = admin
        created = client.post("/api/views/saved", json=BOARD)
        assert created.status_code == 201
        vid = created.get_json()["view"]["id"]
        assert client.get(f"/api/views/saved/{vid}").get_json()["view"]["name"] == BOARD["name"]
        shared = client.post(f"/api/views/saved/{vid}/share", json={"name": "Für alle"})
        assert shared.status_code == 201
        log = client.get("/api/views/saved/audit").get_json()["entries"]
        assert {e["action"] for e in log} == {"view.shared.created", "view.shared.shared"}
        assert all(e["occurred_at"][4] == "-" and "T" in e["occurred_at"] for e in log)   # ISO, not an HTTP date

        app.gate.user = alice
        names = [v["name"] for v in client.get("/api/views/saved").get_json()["views"]]
        assert names == ["Für alle"]
        sid = shared.get_json()["view"]["id"]
        assert client.patch(f"/api/views/saved/{sid}", json={"name": "x"}).status_code == 403
        assert client.delete(f"/api/views/saved/{sid}").status_code == 403
        assert client.get("/api/views/saved/audit").status_code == 403
        assert client.get(f"/api/views/saved/{vid}").status_code == 404

    def test_invalid_and_conflicting(self, app, alice):
        client = app.test_client()
        app.gate.user = alice
        assert client.post("/api/views/saved", json={**BOARD, "settings": {"nodes": []}}).status_code == 400
        assert client.post("/api/views/saved", json=BOARD).status_code == 201
        assert client.post("/api/views/saved", json=BOARD).status_code == 409

    def test_signed_out(self, app):
        assert app.test_client().get("/api/views/saved").status_code == 401

    def test_without_identity_there_are_no_saved_views(self):
        from graph_views import service as views
        from graph_views.settings import ViewSettings
        from web_interface.views_routes import create_views_blueprint

        flask_app = Flask(__name__)
        flask_app.register_blueprint(create_views_blueprint(
            None, views.ViewService(lambda: None, ViewSettings()), graph_mode=lambda: True))
        assert flask_app.test_client().get("/api/views/saved").status_code == 409


class TestInTheApp:
    def test_writes_need_the_csrf_token(self, identity_client, identity_repo):
        """The app's global hook guards the new JSON writes like every other."""
        from _console import csrf_from, sign_in

        _user(identity_repo, "carla")
        sign_in(identity_client, "carla@kanzlei.ch")
        assert identity_client.post("/api/views/saved", json=BOARD).status_code in (400, 403)
        page = identity_client.get("/views").data.decode("utf-8")
        marker = 'name="csrf-token" content="'
        token = page[page.index(marker) + len(marker):].split('"', 1)[0]
        ok = identity_client.post("/api/views/saved", json=BOARD, headers={"X-CSRF-Token": token})
        assert ok.status_code == 201, ok.get_json()
        assert identity_client.get("/api/views/saved").get_json()["views"][0]["name"] == BOARD["name"]
