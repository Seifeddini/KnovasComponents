"""The builder as a team tool (build plan step B11): roles on every route, consultants, comments, the logs.

Each role is tried against the routes it may and may not use; a consultant's grant opens one workspace until
its day; comments hold an approval back until resolved; invitations make accounts and every action is logged.
test_builder_screens.py runs the whole flow in a browser.
"""
import datetime as dt
import io

import pytest

from builder import package, team
from builder_harness import SAMPLE_PACKAGE, FakeEngine, MemoryDirectory, MemoryWorkspaceStore, StandInUser, make_app

TODAY = dt.date.today()
TENANT = "t-1"


class TestTheRules:
    def test_roles_give_permissions(self):
        assert team.permissions_of(["builder_viewer"]) == {"view"}
        assert team.permissions_of(["builder_author"]) == {"view", "edit", "comment", "resolve"}
        assert "review" in team.permissions_of(["builder_reviewer"]) and "edit" not in team.permissions_of(["builder_reviewer"])
        assert team.permissions_of(["builder_publisher"]) == {"view", "comment", "publish"}
        assert team.permissions_of(["admin"]) == set(team.PERMISSIONS)
        assert team.permissions_of(["member", "approver"]) == set()

    def test_a_grant_opens_its_workspace_until_its_day(self):
        grants = [{"workspace": "w1", "roles": ["builder_author"], "ends_on": TODAY.isoformat()}]
        found = team.access(["member"], grants, "w1", TODAY)
        assert found.via == "grant" and found.may("edit") and found.workspaces == {"w1"}
        assert not team.access(["member"], grants, "w2", TODAY).permissions
        later = team.access(["member"], grants, "w1", TODAY + dt.timedelta(days=1))
        assert not later.permissions and later.expired["ends_on"] == TODAY.isoformat()

    def test_a_platform_role_and_a_grant_add_up(self):
        grants = [{"workspace": "w1", "roles": ["builder_publisher"], "ends_on": TODAY.isoformat()}]
        found = team.access(["builder_viewer"], grants, "w1", TODAY)
        assert found.via == "role" and found.may("publish") and found.roles == ("builder_viewer", "builder_publisher")

    @pytest.mark.parametrize("roles,ends,why", [([], "2030-01-01", "Rollen"), (["admin"], "2030-01-01", "Rollen"),
                                                (["builder_author"], "gestern", "Ende"),
                                                (["builder_author"], "2000-01-01", "heute oder später")])
    def test_a_grant_is_checked(self, roles, ends, why):
        with pytest.raises(ValueError, match=why):
            team.check_grant(roles, ends, TODAY)


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin", "ada@example.org")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    directory = MemoryDirectory(store, [admin])
    people = {"admin": admin}
    for role, name in (("builder_author", "Anna Autorin"), ("builder_reviewer", "Rolf Prüfer"),
                       ("builder_publisher", "Paula Publiziert"), ("builder_viewer", "Lea Leserin")):
        people[role] = directory.add(StandInUser({role}, name, f"{role}@example.org"))
    people["member"] = directory.add(StandInUser({"member"}, "Max Mitglied", "max@example.org"))
    engine = FakeEngine()
    app = make_app(admin, store, tenant=TENANT, engine=engine, directory=directory)
    client = app.test_client()
    data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
    for name in ("Beispiel AG", "Andere AG"):
        assert client.post("/builder/api/import", data={"package": (io.BytesIO(data), "p.zip"), "name": name},
                           content_type="multipart/form-data").status_code == 201
    first, other = sorted(store.list(), key=lambda w: w.name, reverse=True)
    store.link_tenant(first.id, TENANT)
    gate = app.extensions["builder_gate"]

    def as_(who):
        gate.user = people[who] if isinstance(who, str) else who
        return client

    return {"client": client, "store": store, "as": as_, "people": people, "directory": directory, "id": first.id,
            "other": other.id, "engine": engine}


def _edit(client, wid, key="Invoice"):
    return client.post(f"/builder/api/w/{wid}/ontology", json={"op": "type.create", "key": key})


class TestEveryRoute:
    def test_a_viewer_reads_and_changes_nothing(self, world):
        c, wid = world["as"]("builder_viewer"), world["id"]
        assert c.get(f"/builder/w/{wid}").status_code == 200
        assert c.get("/builder/ontologie").status_code == 200
        assert _edit(c, wid).status_code == 403
        assert c.post(f"/builder/api/w/{wid}/dry-run").status_code == 403
        assert c.post(f"/builder/api/w/{wid}/approve", json={}).status_code == 403
        assert c.post(f"/builder/api/w/{wid}/publish", json={}).status_code == 403
        assert c.post(f"/builder/api/w/{wid}/comments", json={"target": "x", "body": "y"}).status_code == 403
        refused = _edit(c, wid).get_json()["error"]
        assert "Bearbeiten" in refused

    def test_an_author_edits_but_neither_approves_nor_publishes(self, world):
        c, wid = world["as"]("builder_author"), world["id"]
        assert _edit(c, wid).status_code == 200
        assert c.post(f"/builder/api/w/{wid}/dry-run").status_code == 200
        assert c.post(f"/builder/api/w/{wid}/approve", json={}).status_code == 403
        assert c.post(f"/builder/api/w/{wid}/publish", json={}).status_code == 403
        assert c.post(f"/builder/api/w/{wid}/tenant").status_code == 403

    def test_a_reviewer_approves_but_not_what_they_changed(self, world):
        store, wid = world["store"], world["id"]
        world["as"]("builder_author")
        _edit(world["client"], wid)
        world["client"].post(f"/builder/api/w/{wid}/dry-run")
        c = world["as"]("builder_reviewer")
        assert _edit(c, wid, "Reminder").status_code == 403
        draft = store.draft(wid)
        assert c.post(f"/builder/api/w/{wid}/approve", json={"content_hash": draft["content_hash"]}).status_code == 200
        assert c.post(f"/builder/api/w/{wid}/publish", json={"content_hash": draft["content_hash"]}).status_code == 403
        # A reviewer who also authors (both roles) cannot approve their own change.
        both = world["directory"].add(StandInUser({"builder_author", "builder_reviewer"}, "Beide", "beide@example.org"))
        c = world["as"](both)
        assert _edit(c, wid, "Reminder").status_code == 200
        c.post(f"/builder/api/w/{wid}/dry-run")
        draft = store.draft(wid)
        assert c.post(f"/builder/api/w/{wid}/approve", json={"content_hash": draft["content_hash"]}).status_code == 403

    def test_an_administrator_approves_their_own_changes_only_deliberately(self, world):
        store, wid = world["store"], world["id"]
        c = world["as"]("admin")
        c.get(f"/builder/w/{wid}")
        assert _edit(c, wid).status_code == 200
        c.post(f"/builder/api/w/{wid}/dry-run")
        draft = store.draft(wid)
        html = c.get("/builder/pruefen").get_data(as_text=True)
        assert "data-publish-self-approve" in html and "data-publish-approve" not in html
        plain = {"content_hash": draft["content_hash"]}
        assert c.post(f"/builder/api/w/{wid}/approve", json=plain).status_code == 403
        assert c.post(f"/builder/api/w/{wid}/approve", json={**plain, "self_approve": True, "reason": " "}).status_code == 400
        assert store.approvals(wid) == []
        answer = c.post(f"/builder/api/w/{wid}/approve",
                        json={**plain, "self_approve": True, "reason": "Einzige Prüferin heute."})
        assert answer.status_code == 200
        [approval] = store.approvals(wid)
        assert approval["own_changes"] is True and approval["reason"] == "Einzige Prüferin heute."
        assert "Selbstfreigabe als Administrator" in c.get("/builder/pruefen").get_data(as_text=True)
        assert "Selbstfreigabe als Administrator" in c.get(f"/builder/w/{wid}/aktivitaet").get_data(as_text=True)
        # Anyone else who changed the draft still cannot, whatever they send.
        both = world["directory"].add(StandInUser({"builder_author", "builder_reviewer"}, "Beide", "beide@example.org"))
        c = world["as"](both)
        _edit(c, wid, "Reminder")
        c.post(f"/builder/api/w/{wid}/dry-run")
        draft = store.draft(wid)
        assert c.post(f"/builder/api/w/{wid}/approve", json={"content_hash": draft["content_hash"], "self_approve": True,
                                                              "reason": "Ich darf das."}).status_code == 403

    def test_a_publisher_applies(self, world):
        store, wid = world["store"], world["id"]
        world["as"]("builder_author")
        _edit(world["client"], wid)
        world["client"].post(f"/builder/api/w/{wid}/dry-run")
        draft = store.draft(wid)
        world["as"]("builder_reviewer").post(f"/builder/api/w/{wid}/approve", json={"content_hash": draft["content_hash"]})
        c = world["as"]("builder_publisher")
        assert _edit(c, wid, "Other").status_code == 403
        answer = c.post(f"/builder/api/w/{wid}/publish", json={"content_hash": draft["content_hash"]})
        assert answer.status_code == 200 and store.published_bundle(wid)["number"] == 1

    def test_without_a_builder_role_nothing_opens(self, world):
        c = world["as"]("member")
        assert c.get("/builder/").status_code == 403
        assert c.get(f"/builder/w/{world['id']}").status_code == 403
        assert "Builder-Rolle" in c.get("/builder/arbeitsbereich").get_data(as_text=True)

    def test_an_endpoint_not_listed_needs_manage(self, world):
        from web_interface import builder_routes

        app = world["client"].application
        named = {rule.endpoint.rpartition(".")[2] for rule in app.url_map.iter_rules() if rule.endpoint.startswith("builder.")}
        assert named - {"static"} <= set(builder_routes.ENDPOINT_PERMISSIONS)
        assert world["as"]("builder_author").post("/builder/api/components", json={}).status_code == 403


class TestConsultants:
    def _invite(self, world, ends_on=None, roles=("builder_author",)):
        c = world["as"]("admin")
        answer = c.post(f"/builder/api/w/{world['id']}/access",
                        json={"email": "berater@extern.ch", "name": "Bea Berater", "roles": list(roles),
                              "ends_on": ends_on or (TODAY + dt.timedelta(days=14)).isoformat()})
        assert answer.status_code == 201, answer.get_json()
        return answer.get_json(), world["directory"].get_by_email("berater@extern.ch")

    def test_an_invitation_makes_an_account_once(self, world):
        made, consultant = self._invite(world)
        assert made["password"] and world["directory"].passwords["berater@extern.ch"] == made["password"]
        assert consultant.roles == frozenset() and consultant.display_name == "Bea Berater"
        again, _ = self._invite(world, roles=("builder_author", "builder_viewer"))
        assert again["password"] is None
        [grant] = world["store"].access_grants(world["id"])
        assert grant["roles"] == ["builder_author", "builder_viewer"]

    def test_only_an_admin_invites_and_the_input_is_checked(self, world):
        c = world["as"]("builder_author")
        assert c.post(f"/builder/api/w/{world['id']}/access", json={}).status_code == 403
        c = world["as"]("admin")
        for body, why in (({"email": "nope"}, "E-Mail"),
                          ({"email": "a@b.ch", "roles": ["admin"], "ends_on": "2030-01-01"}, "Rollen"),
                          ({"email": "a@b.ch", "roles": ["builder_author"], "ends_on": "1999-01-01"}, "Ende")):
            answer = c.post(f"/builder/api/w/{world['id']}/access", json=body)
            assert answer.status_code == 400 and why in answer.get_json()["error"]

    def test_a_consultant_sees_and_edits_one_workspace(self, world):
        _, consultant = self._invite(world)
        c = world["as"](consultant)
        page = c.get(f"/builder/w/{world['id']}").get_data(as_text=True)
        assert "Beispiel AG" in page and "Andere AG" not in page and "Autor · bis" in page
        assert c.get(f"/builder/w/{world['other']}").status_code == 403
        assert _edit(c, world["id"]).status_code == 200
        assert c.post(f"/builder/api/w/{world['id']}/publish", json={}).status_code == 403
        assert c.post("/builder/api/workspaces", data={"name": "Eigene"}).status_code == 403
        assert c.get(f"/builder/w/{world['id']}/team").status_code == 403

    def test_access_ends_by_itself(self, world):
        _, consultant = self._invite(world)
        world["store"].grant_access(world["id"], user_id=consultant.id, email=consultant.email, roles=["builder_author"],
                                    ends_on=(TODAY - dt.timedelta(days=1)).isoformat(), actor_id=world["people"]["admin"].id)
        c = world["as"](consultant)
        refused = c.get(f"/builder/w/{world['id']}")
        assert refused.status_code == 403 and "abgelaufen" in refused.get_data(as_text=True)
        assert _edit(c, world["id"]).status_code == 403
        assert [(e["action"], e["status"], e["via"]) for e in world["store"].access_log(world["id"])[:2]] == [
            ("edit_ontology", 403, "grant"), ("workspace", 403, "grant")]
        page = world["as"]("admin").get(f"/builder/w/{world['id']}/team").get_data(as_text=True)
        assert 'data-grant-state="expired"' in page

    def test_a_revoked_grant_opens_nothing(self, world):
        _, consultant = self._invite(world)
        [grant] = world["store"].access_grants(world["id"])
        assert world["as"]("admin").post(f"/builder/api/w/{world['id']}/access/{grant['id']}/revoke").status_code == 200
        assert world["as"](consultant).get(f"/builder/w/{world['id']}").status_code == 403

    def test_every_action_of_a_consultant_is_logged(self, world):
        _, consultant = self._invite(world)
        c = world["as"](consultant)
        c.get(f"/builder/w/{world['id']}")
        _edit(c, world["id"])
        c.post(f"/builder/api/w/{world['id']}/publish", json={})
        world["as"]("admin").get(f"/builder/w/{world['id']}")       # an admin's read is not logged
        log = world["store"].access_log(world["id"])
        assert [(e["method"], e["action"], e["status"], e["via"]) for e in log[:3]] == [
            ("POST", "publish", 403, "grant"), ("POST", "edit_ontology", 200, "grant"), ("GET", "workspace", 200, "grant")]
        assert all(e["email"] != "ada@example.org" or e["method"] == "POST" for e in log)


class TestComments:
    def test_a_thread_on_an_element(self, world):
        wid, store = world["id"], world["store"]
        c = world["as"]("builder_reviewer")
        made = c.post(f"/builder/api/w/{wid}/comments", json={"target": "ontology.types/Mandate", "body": "Fehlt ein Status?"})
        assert made.status_code == 201
        [comment] = made.get_json()["comments"]
        assert comment["author_name"] == "Rolf Prüfer" and not comment["changed_since"]
        # The author changes the type afterwards: the thread says so, then the comment is resolved.
        world["as"]("builder_author").post(f"/builder/api/w/{wid}/ontology",
                                           json={"op": "attribute.add", "key": "Mandate", "attribute": {"name": "phase"}})
        listed = world["as"]("builder_viewer").get(f"/builder/api/w/{wid}/comments?target=ontology.types/Mandate").get_json()
        assert listed["comments"][0]["changed_since"] and listed["may"] == {"comment": False, "resolve": False}
        assert world["as"]("builder_viewer").post(f"/builder/api/w/{wid}/comments/{comment['id']}/resolve").status_code == 403
        done = world["as"]("builder_author").post(f"/builder/api/w/{wid}/comments/{comment['id']}/resolve")
        assert done.status_code == 200 and done.get_json()["comments"][0]["resolved_by_name"] == "Anna Autorin"
        assert world["as"]("builder_author").post(f"/builder/api/w/{wid}/comments/{comment['id']}/resolve").status_code == 404

    def test_open_comments_hold_the_approval_back(self, world):
        wid, store = world["id"], world["store"]
        world["as"]("builder_author")
        _edit(world["client"], wid)
        world["client"].post(f"/builder/api/w/{wid}/dry-run")
        c = world["as"]("builder_reviewer")
        made = c.post(f"/builder/api/w/{wid}/comments", json={"target": "ontology.types/Invoice", "body": "Name?"}).get_json()
        draft = store.draft(wid)
        held = c.post(f"/builder/api/w/{wid}/approve", json={"content_hash": draft["content_hash"]})
        assert held.status_code == 409 and "offenen Kommentare" in held.get_json()["error"]
        c.get(f"/builder/w/{wid}")
        page = c.get("/builder/pruefen").get_data(as_text=True)
        assert "1 offener Kommentar" in page and 'data-comment-target="ontology.types/Invoice"' in page
        c.post(f"/builder/api/w/{wid}/comments/{made['comments'][0]['id']}/resolve")
        assert c.post(f"/builder/api/w/{wid}/approve", json={"content_hash": draft["content_hash"]}).status_code == 200

    def test_a_comment_needs_a_target_and_text(self, world):
        c = world["as"]("builder_reviewer")
        assert c.post(f"/builder/api/w/{world['id']}/comments", json={"target": "", "body": "x"}).status_code == 400
        assert c.post(f"/builder/api/w/{world['id']}/comments", json={"target": "t", "body": " "}).status_code == 400


class TestTheActivityFeed:
    def test_who_changed_what_when(self, world):
        wid = world["id"]
        world["as"]("builder_author")
        _edit(world["client"], wid)
        world["as"]("builder_reviewer").post(f"/builder/api/w/{wid}/comments", json={"target": "ontology.types/Invoice",
                                                                                  "body": "Gut"})
        world["as"]("admin").post(f"/builder/api/w/{wid}/access", json={"email": "b@extern.ch", "roles": ["builder_viewer"],
                                                                         "ends_on": TODAY.isoformat()})
        page = world["as"]("builder_viewer").get(f"/builder/w/{wid}/aktivitaet").get_data(as_text=True)
        assert "Anna Autorin" in page and "änderte Typ Invoice" in page
        assert "Rolf Prüfer" in page and "kommentierte Typ Invoice" in page
        assert "lud b@extern.ch ein (Leser" in page
