"""The workspace store on PostgreSQL (build plan steps B1-B5, migrations 0011-0013)."""
import psycopg
import pytest

from builder import package
from builder.store import WorkspaceStore
from builder_harness import SAMPLE_PACKAGE, MemoryWorkspaceStore
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)


@pytest.fixture(scope="module")
def model():
    return package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model


@pytest.fixture
def consultant(identity_repo):
    return identity_repo.create(email="beraterin@example.org", display_name="Beraterin",
                                password="korrektes-pferd-batterie")


@pytest.fixture(params=["postgres", "memory"])
def store(request, platform_db, consultant):
    """Both stores, held to the same behaviour: the click-dummy may not lie."""
    if request.param == "memory":
        return MemoryWorkspaceStore({str(consultant.id): consultant.display_name})
    return WorkspaceStore(platform_db)


def _source():
    return {"kind": "zip", "bytes": 10, "sha256": "0" * 64, "types": 4, "relations": 3}


def test_an_import_becomes_a_workspace_with_one_draft(store, model, consultant):
    workspace_id = store.create_from_import(name=" Beispiel AG ", model=model, actor_id=consultant.id, source=_source())
    workspace = store.get(workspace_id)
    assert workspace.name == "Beispiel AG"
    assert workspace.draft_number == 1
    assert workspace.published_number is None
    assert workspace.tenant is None
    assert workspace.summary["types"] == 4
    assert store.model(workspace_id) == model


def test_the_import_is_the_first_change(store, model, consultant):
    workspace_id = store.create_from_import(name="Beispiel AG", model=model, actor_id=consultant.id, source=_source())
    workspace = store.get(workspace_id)
    assert workspace.last_change["path"] == "import"
    assert workspace.last_change["actor_name"] == "Beraterin"
    [change] = store.changes(workspace_id)
    assert change["after"]["content_hash"] == package.content_hash(model)
    assert change["after"]["types"] == 4


def test_a_created_workspace_records_its_creation(store, consultant):
    blank = package.read_package(package.blank_package("Muster AG")).model
    workspace_id = store.create(name="Muster AG", model=blank, actor_id=consultant.id, source={"kind": "blank"})
    workspace = store.get(workspace_id)
    assert workspace.draft_number == 1 and workspace.summary["types"] == 0
    assert workspace.last_change["path"] == "create"
    [change] = store.changes(workspace_id)
    assert change["after"] == {"kind": "blank", "content_hash": package.content_hash(blank)}
    assert store.model(workspace_id) == blank


def test_a_deleted_workspace_leaves_the_list_and_keeps_its_history(store, consultant):
    blank = package.read_package(package.blank_package("Weg AG")).model
    workspace_id = store.create(name="Weg AG", model=blank, actor_id=consultant.id, source={"kind": "blank"})
    assert store.delete(workspace_id, consultant.id) is True
    assert store.get(workspace_id) is None and store.list() == []
    assert store.delete(workspace_id, consultant.id) is False
    assert [c["path"] for c in store.changes(workspace_id)] == ["create"]


def test_the_list_is_sorted_by_name(store, model, consultant):
    for name in ("zeta", "Alpha", "beta"):
        store.create_from_import(name=name, model=model, actor_id=consultant.id, source=_source())
    assert [w.name for w in store.list()] == ["Alpha", "beta", "zeta"]


def test_an_unknown_or_malformed_id_is_none(store):
    assert store.get("00000000-0000-0000-0000-000000000000") is None
    assert store.get("not-a-uuid") is None
    assert store.model("00000000-0000-0000-0000-000000000000") is None


def test_the_database_allows_one_draft_per_workspace(platform_db, model, consultant):
    store = WorkspaceStore(platform_db)
    workspace_id = store.create_from_import(name="Beispiel AG", model=model, actor_id=consultant.id, source=_source())
    with pytest.raises(psycopg.errors.UniqueViolation):
        platform_db.execute(
            """INSERT INTO builder_version (workspace, number, state, model, content_hash)
               VALUES (%s, 2, 'draft', '{}'::jsonb, %s)""",
            (workspace_id, "0" * 64),
        )


def test_a_published_version_must_say_when(platform_db, model, consultant):
    store = WorkspaceStore(platform_db)
    workspace_id = store.create_from_import(name="Beispiel AG", model=model, actor_id=consultant.id, source=_source())
    with pytest.raises(psycopg.errors.CheckViolation):
        platform_db.execute(
            """INSERT INTO builder_version (workspace, number, state, model, content_hash)
               VALUES (%s, 2, 'published', '{}'::jsonb, %s)""",
            (workspace_id, "0" * 64),
        )


def test_an_edit_changes_the_draft_and_logs_each_element_in_one_batch(store, consultant):
    from builder import editing

    blank = package.read_package(package.blank_package("Muster AG")).model
    workspace_id = store.create(name="Muster AG", model=blank, actor_id=consultant.id, source={"kind": "blank"})
    model, changes, batch = store.edit(workspace_id, consultant.id,
                                       lambda m: editing.apply(m, {"op": "type.create", "key": "Invoice"}))
    assert [t["key"] for t in store.model(workspace_id)["ontology"]["types"]] == ["Invoice"]
    assert store.batch(workspace_id, batch) == changes
    assert store.get(workspace_id).last_change["path"] == "ontology.types/Invoice"

    def refuse(m):
        editing.apply(m, {"op": "type.create", "key": "Bill"})
        raise editing.EditError("no")

    with pytest.raises(editing.EditError):
        store.edit(workspace_id, consultant.id, refuse)
    assert [t["key"] for t in store.model(workspace_id)["ontology"]["types"]] == ["Invoice"]
    assert store.batch(workspace_id, "not-a-uuid") == []


def test_review_and_publish(store, consultant):
    from builder import editing

    blank = package.read_package(package.blank_package("Muster AG")).model
    workspace_id = store.create(name="Muster AG", model=blank, actor_id=consultant.id, source={"kind": "blank"},
                                tenant="test-tenant")
    assert store.get(workspace_id).tenant == "test-tenant"
    store.edit(workspace_id, consultant.id, lambda m: editing.apply(m, {"op": "type.create", "key": "Invoice"}))
    draft = store.draft(workspace_id)
    assert draft["number"] == 1 and len(draft["content_hash"]) == 64
    assert [c["path"] for c in store.draft_changes(workspace_id)] == ["create", "ontology.types/Invoice"]

    assert not store.approve(workspace_id, consultant.id, "0" * 64)  # not the draft as it is
    assert store.approve(workspace_id, consultant.id, draft["content_hash"])
    assert [a["content_hash"] for a in store.approvals(workspace_id)] == [draft["content_hash"]]

    store.record_run(workspace_id, kind="dry_run", actor_id=consultant.id, tenant="test-tenant",
                     content_hash=draft["content_hash"], bundle_hash="a" * 64, status="ok", summary={"counts": {}})
    bundle = {"header": {"format": "brain-definitions-bundle"}, "sections": {}}
    assert store.publish(workspace_id, consultant.id, "0" * 64, bundle, "a" * 64) is None
    assert store.publish(workspace_id, consultant.id, draft["content_hash"], bundle, "a" * 64) == 1
    assert store.draft(workspace_id) is None and store.approvals(workspace_id) == []
    assert [a["content_hash"] for a in store.approval_history(workspace_id)] == [draft["content_hash"]]
    published = store.published_bundle(workspace_id)
    assert published["number"] == 1 and published["bundle"] == bundle
    assert store.get(workspace_id).published_number == 1
    assert [r["kind"] for r in store.runs(workspace_id)] == ["dry_run"]

    # The next edit opens draft 2 on the published model.
    store.edit(workspace_id, consultant.id, lambda m: editing.apply(m, {"op": "type.create", "key": "Reminder"}))
    assert store.draft(workspace_id)["number"] == 2
    assert [c["path"] for c in store.draft_changes(workspace_id)] == ["ontology.types/Reminder"]
    assert [t["key"] for t in store.model(workspace_id)["ontology"]["types"]] == ["Invoice", "Reminder"]
    store.link_tenant(workspace_id, "other")
    assert store.get(workspace_id).tenant == "other"


def test_health_reviews_keep_the_latest_per_type(store, consultant):
    blank = package.read_package(package.blank_package("Muster AG")).model
    workspace_id = store.create(name="Muster AG", model=blank, actor_id=consultant.id, source={"kind": "blank"})
    assert store.reviews(workspace_id) == {}
    store.record_review(workspace_id, "Invoice", consultant.id)
    store.record_review(workspace_id, "Invoice", consultant.id)
    store.record_review(workspace_id, "Mandate", consultant.id)
    reviews = store.reviews(workspace_id)
    assert set(reviews) == {"Invoice", "Mandate"} and reviews["Invoice"]["actor_name"] == "Beraterin"
    assert reviews["Invoice"]["at"]


def test_grants_comments_and_the_access_log(store, consultant, identity_repo):
    """Step B11 (migration 0016): one grant per person and workspace, comments until resolved, the access log."""
    blank = package.read_package(package.blank_package("Muster AG")).model
    workspace_id = store.create(name="Muster AG", model=blank, actor_id=consultant.id, source={"kind": "blank"})
    guest = identity_repo.create(email="gast@extern.example", display_name="Gast", password="korrektes-pferd-batterie")
    if isinstance(store, MemoryWorkspaceStore):
        store.user_names[str(guest.id)] = "Gast"
    first = store.grant_access(workspace_id, user_id=guest.id, email=guest.email, roles=["builder_author"],
                               ends_on="2030-01-31", actor_id=consultant.id)
    again = store.grant_access(workspace_id, user_id=guest.id, email=guest.email,
                               roles=["builder_author", "builder_viewer"], ends_on="2030-02-28", actor_id=consultant.id)
    assert first == again
    [grant] = store.access_grants(workspace_id)
    assert grant["roles"] == ["builder_author", "builder_viewer"] and grant["ends_on"] == "2030-02-28"
    assert grant["invited_by_name"] == "Beraterin" and grant["revoked_at"] is None
    assert store.grants_of(guest.id) == [{"workspace": workspace_id, "roles": ["builder_author", "builder_viewer"],
                                          "ends_on": "2030-02-28"}]
    assert store.revoke_access(workspace_id, grant["id"], consultant.id)
    assert not store.revoke_access(workspace_id, grant["id"], consultant.id)
    assert store.grants_of(guest.id) == []

    comment = store.add_comment(workspace_id, "ontology.types/Invoice", "Name?", guest.id)
    store.add_comment(workspace_id, "ontology.types/Other", "Und das?", consultant.id)
    [mine] = store.comments(workspace_id, "ontology.types/Invoice")
    assert mine["author_name"] == "Gast" and mine["body"] == "Name?" and mine["resolved_at"] is None
    assert len(store.comments(workspace_id)) == 2
    assert store.resolve_comment(workspace_id, comment, consultant.id)
    assert not store.resolve_comment(workspace_id, comment, consultant.id)
    assert store.comments(workspace_id, "ontology.types/Invoice")[0]["resolved_by_name"] == "Beraterin"

    store.log_access(workspace_id, user_id=guest.id, email=guest.email, method="GET", action="workspace", status=200,
                     via="grant")
    store.log_access(workspace_id, user_id=guest.id, email=guest.email, method="POST", action="publish", status=403,
                     via="grant")
    log = store.access_log(workspace_id)
    assert [(e["method"], e["action"], e["status"]) for e in log] == [("POST", "publish", 403), ("GET", "workspace", 200)]
    assert log[0]["actor_name"] == "Gast" and log[0]["via"] == "grant"


def test_components_enrol_run_hold_and_take_commands(store, consultant):
    """Step B13 (migration 0017): a v1 component from pending to enrolled, its runs, held plans, events, commands."""
    tenant = "tenant-b13"
    cid = store.create_component(tenant, kind="knovas_connector", name="Ablage", site="Zürich", owner="IT",
                                 actor_id=consultant.id, enrol_expires_at="2030-01-01T00:00:00+00:00")
    assert store.create_component(tenant, kind="github_runner", name="Ablage", site="", owner="", actor_id=consultant.id,
                                  enrol_expires_at="2030-01-01T00:00:00+00:00") is None
    assert store.component(tenant, cid) is None                       # pending: no source reads through it yet
    [pending] = store.pending_components(tenant)
    assert pending["transport"] == "v1" and pending["schedule"] == {"mode": "manual"}
    store.update_component(cid, state="enrolled", version="2.4.0", capabilities=["dry_run"], certificate_fingerprint="ab",
                           last_heartbeat_at="2026-11-01T10:00:00+00:00", reported_state="online",
                           schedule={"mode": "interval", "every_minutes": 60}, paused=True)
    c = store.component(tenant, cid)
    assert c["state"] == "enrolled" and c["capabilities"] == ["dry_run"] and c["paused"] is True
    assert c["schedule"] == {"mode": "interval", "every_minutes": 60}
    with pytest.raises(ValueError):
        store.update_component(cid, tenant="other")

    store.add_run(cid, kind="dry_run", status="held", started_at="2026-11-01T10:00:00+00:00",
                  finished_at="2026-11-01T10:01:00+00:00", duration_ms=60000, added=1, changed=2, retired=40, errors=0)
    [run] = store.component_runs(cid)
    assert (run["kind"], run["status"], run["retired"]) == ("dry_run", "held", 40)
    plan = store.add_held(cid, mapping="ablage-akten", retire={"Akte": 40}, share=0.31)
    assert store.decide_held(cid, plan, approve=True, actor_id=consultant.id, reason="geprüft")
    assert not store.decide_held(cid, plan, approve=False, actor_id=consultant.id, reason="")
    [held] = store.held_plans(cid)
    assert held["state"] == "approved" and held["decided_by_name"] == "Beraterin" and held["retire"] == {"Akte": 40}

    store.add_event(cid, "plan.held", {"retire": 40})
    assert store.events(cid)[0]["code"] == "plan.held" and store.events(cid)[0]["counts"] == {"retire": 40}
    first = store.add_command(cid, "run", {"kind": "dry_run"}, consultant.id)
    store.add_command(cid, "pause", {}, consultant.id)
    assert [c["command"] for c in store.deliver_commands(cid, "2026-11-01T10:02:00+00:00")] == ["run", "pause"]
    assert store.deliver_commands(cid, "2026-11-01T10:03:00+00:00") == []
    assert store.finish_command(cid, first, "ok", "2026-11-01T10:04:00+00:00")
    assert not store.finish_command(cid, first, "ok", "2026-11-01T10:05:00+00:00")
    assert {c["command"]: c["result"] for c in store.commands(cid)} == {"run": "ok", "pause": None}
    store.update_component(cid, state="revoked")
    assert store.component(tenant, cid) is None and store.component(tenant, cid, any_state=True)["state"] == "revoked"


def test_group_grants_end_on_their_day(identity_repo, consultant):
    """Step B14 (migration 0018): a group grant with an end date stops counting after it, by itself."""
    import datetime as _dt

    today = _dt.date.today()
    identity_repo.grant_access_group(consultant.id, "g-partner", by=consultant.id)
    identity_repo.grant_access_group(consultant.id, "g-meier", ends_on=(today + _dt.timedelta(days=3)).isoformat())
    identity_repo.grant_access_group(consultant.id, "g-alt", ends_on=(today - _dt.timedelta(days=1)).isoformat())
    assert identity_repo.access_groups_of(consultant.id) == ("g-meier", "g-partner")
    assert [g["group_id"] for g in identity_repo.access_grants_of(consultant.id)] == ["g-alt", "g-meier", "g-partner"]
    assert identity_repo.members_of_groups() == {"g-meier": [str(consultant.id)], "g-partner": [str(consultant.id)]}
    assert identity_repo.revoke_access_group(consultant.id, "g-meier")
    assert not identity_repo.revoke_access_group(consultant.id, "g-meier")


def test_an_enforcement_switch_takes_a_second_person(store, consultant, identity_repo):
    other = identity_repo.create(email="zweite@example.org", display_name="Zweite", password="korrektes-pferd-batterie")
    if isinstance(store, MemoryWorkspaceStore):
        store.user_names[str(other.id)] = "Zweite"
    rid = store.request_posture("tenant-b14", "enforcing", consultant.id)
    assert not store.approve_posture("tenant-b14", rid, consultant.id, "ok")
    assert store.approve_posture("tenant-b14", rid, other.id, "freigegeben")
    [req] = store.posture_requests("tenant-b14")
    assert req["approved_by_name"] == "Zweite" and req["requested_by_name"] == "Beraterin" and req["outcome"] == "freigegeben"
