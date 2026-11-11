"""Automatic GitHub → Brain syncing as the Builder manages it (FEAT-2026-11-05-knovas-brain-v1, KG-2; protocol 1.2).

- The schedule «bei neuen Commits» (poll every 15 to 3600 seconds) beside manual, interval (now from 1 minute) and
  window; it reaches a component the way schedules do: the heartbeat's answer and /config, and for a component behind
  Knovas Link its own entry in Link's ``components``.
- «Automatisch anwenden, wenn nichts zur Freigabe ansteht», per source: only whoever manages sites switches it, every
  change is an event on the component and a line in the access log; it reaches the component as ``auto_apply``.
- The last synced commit and run per source on «Verbinden», the site page and the component page, from what the
  component reports in its heartbeat (directly, or through Link's ``site.sources``): commits, times, codes, counts.

The companies, repositories and people are made up.
"""
import datetime as dt
import html

import pytest

from builder import components as C
from builder import editing, package, protocol
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient,
                             StandInUser, make_app, publish_as_reviewed)
from test_builder_sites import _online_link, _telemetry

TENANT = "t-site"
SYNCED, NEWER = "1" * 40, "2" * 40
CAPS = ["dry_run", "apply", "fetch:github", "write:github", "poll_commits", "auto_apply", "sync_status"]


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    secrets = MemorySecrets()
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=secrets)
    return {"http": app.test_client(), "store": store, "secrets": secrets, "gate": app.extensions["builder_gate"],
            "admin": admin}


def _site_with_source(world, capabilities=CAPS):
    """A site whose sync component reads one published GitHub source."""
    made, link = _online_link(world)
    component = {**_telemetry()["components"][0], "version": "0.3.0", "capabilities": list(capabilities)}
    assert link.heartbeat(site=_telemetry(components=[component])).status_code == 200
    [child] = world["store"].children(link.component)
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    editing.apply(model, {"op": "source.create", "kind": "github", "name": "Handbuch", "component": child["id"],
                          "settings": {"repository": "beispiel-firma/handbuch", "ref": "main"}})
    workspace = world["store"].create(name="Beispiel AG", model=model, actor_id=world["admin"].id,
                                      source={"kind": "blank"}, tenant=TENANT)
    publish_as_reviewed(world["store"], workspace, world["admin"].id)
    key = [s for s in world["store"].published_model(workspace)["sources"]][0]["key"]
    return made, link, child, workspace, key


def _status(workspace, key, **changes):
    entry = {"workspace": workspace, "key": key, "synced_commit": SYNCED, "synced_at": "2026-11-10T09:00:00+00:00",
             "checked_at": "2026-11-10T09:05:00+00:00", "next_check_at": "2026-11-10T09:05:30+00:00",
             "planned_commit": None, "held_back": [],
             "last_apply": {"at": "2026-11-10T09:00:00+00:00", "status": "ok", "added": 3, "changed": 2, "retired": 1,
                            "errors": 0, "auto": True}}
    entry.update(changes)
    return entry


def _source_telemetry(workspace, key, **changes):
    return {"workspace": workspace, "key": key, "last_read_ok_at": None, "last_run_status": "ok",
            **_status(workspace, key, **changes)}


# ------------------------------------------------------------------ the schedule

class TestTheSchedule:
    def test_the_texts(self):
        assert C.schedule_text({"mode": "commits", "poll_seconds": 30}) == "bei neuen Commits (alle 30 Sekunden geprüft)"
        assert C.schedule_text({"mode": "interval", "every_minutes": 1}) == "jede Minute"
        assert C.schedule_text({"mode": "interval", "every_minutes": 5}) == "alle 5 Minuten"

    def test_a_component_behind_link_gets_its_own_schedule_and_pause_through_link(self, world):
        _, link, child, _, _ = _site_with_source(world)
        answer = world["http"].post(f"/builder/api/components/{child['id']}/schedule",
                                    json={"schedule": {"mode": "commits", "poll_seconds": 45}})
        assert answer.status_code == 200 and answer.get_json()["text"].startswith("bei neuen Commits")
        beat = link.heartbeat().get_json()
        assert beat["components"] == [{"component": "sync-component", "paused": False,
                                       "schedule": {"mode": "commits", "poll_seconds": 45}}]
        assert link.config().get_json()["components"] == beat["components"]
        assert world["http"].post(f"/builder/api/components/{child['id']}/pause").status_code == 200
        [entry] = link.heartbeat().get_json()["components"]
        assert entry["paused"] is True, "pausing a component on its page now reaches it"

    def test_link_runs_nothing_so_its_own_schedule_is_not_set(self, world):
        _, link, _, _, _ = _site_with_source(world)
        answer = world["http"].post(f"/builder/api/components/{link.component}/schedule",
                                    json={"schedule": {"mode": "interval", "every_minutes": 5}})
        assert answer.status_code == 409
        page = world["http"].get(f"/builder/komponenten/{link.component}").get_data(as_text=True)
        assert "data-cp-schedule-link" in page and "data-cp-schedule>" not in page

    def test_a_component_that_cannot_poll_is_not_given_the_schedule(self, world):
        _, _, child, _, _ = _site_with_source(world, capabilities=["dry_run", "apply"])
        answer = world["http"].post(f"/builder/api/components/{child['id']}/schedule",
                                    json={"schedule": {"mode": "commits", "poll_seconds": 30}})
        assert answer.status_code == 409 and "Abbild" in answer.get_json()["error"]
        assert world["http"].post(f"/builder/api/components/{child['id']}/schedule",
                                  json={"schedule": {"mode": "interval", "every_minutes": 1}}).status_code == 200

    def test_a_direct_component_gets_the_schedule_in_its_heartbeat_and_config(self, world):
        made = world["http"].post("/builder/api/components/enrol", json={"kind": "sync_component", "name": "S"}).get_json()
        direct = ProtocolClient(world["http"])
        direct.enrol(made["token"], capabilities=CAPS)
        assert world["http"].post(f"/builder/api/components/{made['id']}/schedule",
                                  json={"schedule": {"mode": "commits"}}).status_code == 200
        assert direct.heartbeat().get_json()["schedule"] == {"mode": "commits", "poll_seconds": 30}
        assert direct.config().get_json()["schedule"] == {"mode": "commits", "poll_seconds": 30}
        assert "components" not in direct.heartbeat().get_json(), "only Link's answer names components"

    def test_the_page_offers_bei_neuen_commits(self, world):
        _, _, child, _, _ = _site_with_source(world)
        page = world["http"].get(f"/builder/komponenten/{child['id']}").get_data(as_text=True)
        assert 'value="commits"' in page and "Bei neuen Commits" in page and 'name="poll_seconds"' in page


# ------------------------------------------------------------------ automatic apply

class TestAutomaticApply:
    def _switch(self, world, child, workspace, key, on):
        return world["http"].post(f"/builder/api/components/{child['id']}/auto_apply",
                                  json={"workspace": workspace, "source": key, "on": on})

    def test_off_by_default_and_on_only_where_switched(self, world):
        _, link, child, workspace, key = _site_with_source(world)
        [source] = link.config().get_json()["sources"]
        assert source["auto_apply"] is False
        assert self._switch(world, child, workspace, key, True).status_code == 200
        [source] = link.config().get_json()["sources"]
        assert source["auto_apply"] is True and source["component"] == "sync-component"
        assert self._switch(world, child, workspace, key, False).status_code == 200
        assert link.config().get_json()["sources"][0]["auto_apply"] is False

    def test_every_change_is_an_event_and_tells_the_component_to_read_its_configuration(self, world):
        _, link, child, workspace, key = _site_with_source(world)
        self._switch(world, child, workspace, key, True)
        self._switch(world, child, workspace, key, True)                  # no change: nothing more
        self._switch(world, child, workspace, key, False)
        codes = [e["code"] for e in world["store"].events(child["id"])]
        assert codes.count("auto_apply.on") == 1 and codes.count("auto_apply.off") == 1
        commands = [c["command"] for c in link.heartbeat().get_json()["commands"]]
        assert commands.count("reload_config") == 2
        logged = [r for r in getattr(world["store"], "access_rows", []) if r["action"] == "component_auto_apply"]
        assert len(logged) == 3 and all(r["status"] == 200 and r["user_id"] == str(world["admin"].id) for r in logged), \
            "who switched it, and when, is in the access log"

    @pytest.mark.parametrize("roles", [{"builder_author"}, {"builder_publisher"}, {"builder_reviewer"},
                                       {"builder_viewer"}])
    def test_only_whoever_manages_sites_switches_it(self, world, roles):
        _, link, child, workspace, key = _site_with_source(world)
        world["gate"].user = StandInUser(roles)
        assert self._switch(world, child, workspace, key, True).status_code == 403
        assert link.config().get_json()["sources"][0]["auto_apply"] is False

    def test_a_source_the_component_does_not_serve_or_a_component_that_cannot_is_refused(self, world):
        _, _, child, workspace, key = _site_with_source(world, capabilities=["dry_run", "apply"])
        assert self._switch(world, child, workspace, "andere-quelle", True).status_code == 404
        refused = self._switch(world, child, workspace, key, True)
        assert refused.status_code == 409 and "Abbild" in refused.get_json()["error"]
        assert self._switch(world, child, workspace, key, "ja").status_code == 400
        assert self._switch(world, child, workspace, key, False).status_code == 200, "switching off always works"

    def test_a_direct_component_gets_it_in_its_config(self, world):
        made = world["http"].post("/builder/api/components/enrol", json={"kind": "sync_component", "name": "S"}).get_json()
        direct = ProtocolClient(world["http"])
        direct.enrol(made["token"], capabilities=CAPS)
        direct.heartbeat(capabilities=CAPS)
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        editing.apply(model, {"op": "source.create", "kind": "github", "name": "Handbuch", "component": made["id"],
                              "settings": {"repository": "beispiel-firma/handbuch", "ref": "main"}})
        workspace = world["store"].create(name="Beispiel AG", model=model, actor_id=world["admin"].id,
                                          source={"kind": "blank"}, tenant=TENANT)
        publish_as_reviewed(world["store"], workspace, world["admin"].id)
        component = world["store"].component(TENANT, made["id"])
        assert self._switch(world, component, workspace, "handbuch", True).status_code == 200
        assert direct.config().get_json()["sources"][0]["auto_apply"] is True


# ------------------------------------------------------------------ the last synced commit and run

class TestTheSyncStatus:
    def test_link_s_heartbeat_carries_it_and_the_site_page_shows_it(self, world):
        made, link, child, workspace, key = _site_with_source(world)
        world["http"].post(f"/builder/api/components/{child['id']}/schedule",
                           json={"schedule": {"mode": "commits", "poll_seconds": 30}})
        assert link.heartbeat(site=_telemetry(sources=[_source_telemetry(workspace, key, planned_commit=NEWER,
                                                                           held_back=["plan.drift"])])).status_code == 200
        kept = world["store"].component(TENANT, link.component)["telemetry"]["sources"][0]
        assert kept["synced_commit"] == SYNCED and kept["last_apply"]["auto"] is True
        page = html.unescape(world["http"].get(made["url"]).get_data(as_text=True))
        assert "Quellen und Abgleich" in page
        assert f'href="https://github.com/beispiel-firma/handbuch/commit/{SYNCED}"' in page and SYNCED[:7] in page
        assert "+3 / ~2 / −1" in page and "automatisch" in page
        assert "bei neuen Commits (alle 30 Sekunden geprüft)" in page
        assert "Neuer Commit" in page and NEWER[:7] in page and "Abweichung" in page
        assert 'data-sync-auto' in page and f'data-source="{key}"' in page
        listing = html.unescape(world["http"].get("/builder/verbinden").get_data(as_text=True))
        assert "data-site-sync-summary" in listing and SYNCED[:7] in listing and "automatisch anwenden aus" in listing

    def test_the_component_page_shows_it_too(self, world):
        _, link, child, workspace, key = _site_with_source(world)
        link.heartbeat(site=_telemetry(sources=[_source_telemetry(workspace, key)]))
        page = world["http"].get(f"/builder/komponenten/{child['id']}").get_data(as_text=True)
        assert "data-cp-sync" in page and SYNCED[:7] in page and "data-sync-auto" in page

    @pytest.mark.parametrize("bad", [{"synced_commit": "main"}, {"synced_commit": "Handbuch der Beispiel AG"},
                                     {"held_back": ["Datei fehlt in /Mandate"]}, {"last_apply": {"status": "fertig"}},
                                     {"last_apply": {"status": "ok", "added": "drei"}}, {"next_check_at": "bald"}])
    def test_names_and_free_text_are_refused(self, world, bad):
        _, link, _, workspace, key = _site_with_source(world)
        answer = link.heartbeat(site=_telemetry(sources=[_source_telemetry(workspace, key, **bad)]))
        assert answer.status_code == 400 and answer.get_json()["error"] == "bad_field"

    def test_a_source_without_a_sync_status_keeps_the_1_1_shape(self, world):
        _, link, _, workspace, key = _site_with_source(world)
        plain = {"workspace": workspace, "key": key, "last_read_ok_at": None, "last_run_status": "ok"}
        assert link.heartbeat(site=_telemetry(sources=[plain])).status_code == 200
        [kept] = world["store"].component(TENANT, link.component)["telemetry"]["sources"]
        assert set(kept) == {"workspace", "key", "last_read_ok_at", "last_run_status"}

    def test_a_direct_component_reports_it_in_its_own_heartbeat(self, world):
        made = world["http"].post("/builder/api/components/enrol", json={"kind": "sync_component", "name": "S"}).get_json()
        direct = ProtocolClient(world["http"])
        direct.enrol(made["token"], capabilities=CAPS)
        workspace = "0b8f8f8e-1111-4222-8333-944444444444"
        assert direct.heartbeat(sources=[_status(workspace, "handbuch", note="Handbuch")]).status_code == 200
        [kept] = world["store"].component(TENANT, made["id"])["telemetry"]["sources"]
        assert kept["synced_commit"] == SYNCED and "note" not in kept
        assert direct.heartbeat(sources=[_status(workspace, "handbuch", synced_commit="HEAD")]).status_code == 400
        assert direct.heartbeat().status_code == 200, "a heartbeat without it is answered as before"

    def test_rows_link_only_github_commits(self):
        rows = C.sync_rows([{"workspace": "w", "key": "k", "kind": "http_json", "settings": {"url": "https://x.test"},
                             "component_id": "c"}], [{"workspace": "w", "key": "k", "synced_commit": SYNCED}], {}, {})
        assert rows[0]["synced_url"] is None and rows[0]["synced_short"] == SYNCED[:7]
        assert C.commit_url({"kind": "github", "settings": {"repository": "a/b"}}, SYNCED) == \
            f"https://github.com/a/b/commit/{SYNCED}"
        assert C.commit_url({"kind": "github", "settings": {"repository": "a/b?x=1"}}, SYNCED) is None
