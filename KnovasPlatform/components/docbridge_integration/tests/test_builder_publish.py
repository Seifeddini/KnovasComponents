"""Review and publish (build plan step B5): change set, dry run, approval, apply.

On the harness with a stand-in engine (builder_harness.FakeEngine), a tenant
held in memory. test_builder_store.py holds the PostgreSQL store to the same
behaviour; the bundle's identity with brain-runner's own is checked here when
a brain-runner with ontology support is installed.
"""
import os
from pathlib import Path

import pytest

from builder import package, publisher, review
from builder_harness import SAMPLE_PACKAGE, FakeEngine, MemoryWorkspaceStore, StandInUser, make_app

TENANT = "test-tenant-0001"


@pytest.fixture
def setup():
    author, reviewer = StandInUser({"admin"}, "Alex Beispiel"), StandInUser({"admin"}, "Robin Muster")
    store = MemoryWorkspaceStore({str(author.id): author.display_name, str(reviewer.id): reviewer.display_name})
    engine = FakeEngine(tenant_types={"Mandate": set()})
    app = make_app(author, store, tenant=TENANT, engine=engine)
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    workspace_id = store.create(name="Beispiel AG", model=model, actor_id=None, source={"kind": "zip"}, tenant=TENANT)
    client = app.test_client()
    with client.session_transaction() as session:
        session["builder_workspace"] = workspace_id
    gate = app.extensions["builder_gate"]
    return {"client": client, "store": store, "engine": engine, "id": workspace_id, "gate": gate,
            "author": author, "reviewer": reviewer}


def _edit(s, **op):
    response = s["client"].post(f"/builder/api/w/{s['id']}/ontology", json=op)
    assert response.status_code == 200, response.get_json()


def _post(s, what, **body):
    return s["client"].post(f"/builder/api/w/{s['id']}/{what}", json=body)


def _hash(s):
    return s["store"].draft(s["id"])["content_hash"]


def _as(s, who):
    s["gate"].user = s[who]


class TestTheChangeSet:
    def test_changes_fold_per_element_and_an_undone_edit_is_nothing(self, setup):
        s = setup
        _edit(s, op="type.create", key="Invoice")
        _edit(s, op="attribute.add", key="Invoice", attribute={"name": "number"})
        _edit(s, op="type.create", key="Reminder")
        s["client"].post(f"/builder/api/w/{s['id']}/undo")
        changes = review.change_set(s["store"].draft_changes(s["id"]))
        [types] = changes["sections"]
        assert [g["key"] for g in types["elements"]] == ["Invoice"]
        assert types["elements"][0]["state"] == "added" and "number" in types["elements"][0]["lines"][0]["text"]
        assert changes["origin"]["label"] == "Angelegt"

    def test_the_author_filter(self, setup):
        s = setup
        _edit(s, op="type.create", key="Invoice")
        _as(s, "reviewer")
        _edit(s, op="type.create", key="Reminder")
        rows = s["store"].draft_changes(s["id"])
        mine = review.change_set(rows, author=str(s["author"].id))
        assert [g["key"] for sec in mine["sections"] for g in sec["elements"]] == ["Invoice"]
        page = s["client"].get(f"/builder/pruefen?autor={s['author'].id}").get_data(as_text=True)
        assert "Invoice" in page and "Reminder" not in page.split("Probelauf")[0].split("Änderungen", 1)[1]

    def test_a_retirement_is_destructive(self, setup):
        s = setup
        _edit(s, op="type.create", key="Invoice")
        key = next(t["key"] for t in s["store"].model(s["id"])["ontology"]["types"] if t["level"] == "company")
        _edit(s, op="type.retire", key=key)
        assert [g["key"] for g in review.destructive(review.change_set(s["store"].draft_changes(s["id"])))] == [key]


class TestPublishing:
    def test_the_whole_way_dry_run_approval_by_a_second_person_apply(self, setup):
        s = setup
        _edit(s, op="type.create", key="Invoice")
        _edit(s, op="attribute.add", key="Invoice", attribute={"name": "status", "datatype": "enum"})
        _edit(s, op="attribute.update", key="Invoice", name="status", set={"enum_values": ["open", "paid"]})

        dry = _post(s, "dry-run").get_json()
        assert dry["success"] and dry["counts"]["create"] >= 2
        assert {"element": "Invoice", "status": "create", "detail": "Typ anlegen"} in dry["rows"]
        assert "Invoice" not in s["engine"].tenant  # a dry run writes nothing

        assert _post(s, "approve", content_hash=_hash(s)).status_code == 403  # the author cannot
        _as(s, "reviewer")
        assert _post(s, "approve", content_hash=_hash(s)).get_json()["success"]

        _as(s, "author")
        published = _post(s, "publish", content_hash=_hash(s))
        body = published.get_json()
        assert published.status_code == 200 and body["status"] == "ok", body
        assert s["engine"].tenant["Invoice"] == {"status"}
        assert s["store"].draft(s["id"]) is None
        assert s["store"].published_bundle(s["id"])["number"] == 1
        bundle = s["client"].get(f"/builder/api/w/{s['id']}/bundle").get_json()
        assert bundle["header"]["format"] == "brain-definitions-bundle" and "Invoice" in bundle["sections"]["types"]
        kinds = [(r["kind"], r["status"]) for r in s["store"].runs(s["id"])]
        assert kinds == [("apply", "ok"), ("dry_run", "ok")]

        # The next edit starts the next draft.
        _edit(s, op="type.create", key="Reminder")
        assert s["store"].draft(s["id"])["number"] == 2

    def test_an_edit_after_the_approval_voids_it(self, setup):
        s = setup
        _edit(s, op="type.create", key="Invoice")
        _post(s, "dry-run")
        _as(s, "reviewer")
        _post(s, "approve", content_hash=_hash(s))
        _as(s, "author")
        _edit(s, op="type.update", key="Invoice", set={"example": "Invoice 7."})
        response = _post(s, "publish", content_hash=_hash(s))
        assert response.status_code == 409 and "nicht freigegeben" in response.get_json()["error"]

    def test_no_approval_before_a_dry_run(self, setup):
        s = setup
        _edit(s, op="type.create", key="Invoice")
        _as(s, "reviewer")
        assert _post(s, "approve", content_hash=_hash(s)).status_code == 409

    def test_retirements_need_their_own_confirmation(self, setup):
        s = setup
        key = next(t["key"] for t in s["store"].model(s["id"])["ontology"]["types"] if t["level"] == "company")
        _edit(s, op="type.retire", key=key)
        _post(s, "dry-run")
        _as(s, "reviewer")
        _post(s, "approve", content_hash=_hash(s))
        refused = _post(s, "publish", content_hash=_hash(s))
        assert refused.status_code == 409 and refused.get_json()["details"] == [f"ontology.types/{key}"]
        assert _post(s, "publish", content_hash=_hash(s), confirm_destructive=[f"ontology.types/{key}"]).status_code == 200

    def test_refusals_show_in_the_dry_run_and_the_result(self, setup):
        s = setup
        s["engine"].refuse = {"Invoice"}
        _edit(s, op="type.create", key="Invoice")
        dry = _post(s, "dry-run").get_json()
        assert dry["status"] == "refused" and dry["counts"]["refused"] == 1
        page = s["client"].get("/builder/pruefen").get_data(as_text=True)
        assert "ablehnen" in page

    def test_only_the_platform_s_own_tenant(self, setup):
        s = setup
        s["store"].rows[s["id"]]["tenant"] = "someone-else"
        _edit(s, op="type.create", key="Invoice")
        response = _post(s, "dry-run")
        assert response.status_code == 409 and "anderen Mandanten" in response.get_json()["error"]

    def test_without_a_linked_tenant_it_offers_this_platform_s(self, setup):
        s = setup
        s["store"].rows[s["id"]]["tenant"] = None
        _edit(s, op="type.create", key="Invoice")
        assert _post(s, "dry-run").status_code == 409
        assert "Mit diesem Mandanten verbinden" in s["client"].get("/builder/pruefen").get_data(as_text=True)
        assert _post(s, "tenant").get_json()["success"]
        assert s["store"].get(s["id"]).tenant == TENANT

    def test_an_engine_that_cannot_plan_says_so(self, setup):
        s = setup
        s["engine"]._missing = "brain-runner 0.1.0.dev2 kann die Ontologie noch nicht planen."
        _edit(s, op="type.create", key="Invoice")
        assert _post(s, "dry-run").status_code == 503
        assert "noch nicht planen" in s["client"].get("/builder/pruefen").get_data(as_text=True)

    def test_without_a_builder_role_nothing(self, setup):
        s = setup
        s["gate"].user = StandInUser({"member"})
        for what in ("dry-run", "approve", "publish", "tenant"):
            assert _post(s, what, content_hash="x").status_code == 403


class TestTheBundle:
    """With a brain-runner that has the bundle and ontology modules (0.1.0 or newer)."""

    def test_the_bundle_is_brain_runner_s_own(self):
        bundle_mod = pytest.importorskip("brain_runner.bundle")
        pytest.importorskip("brain_runner.ontology")
        import subprocess
        import sys
        import tempfile

        from builder import editing

        model = package.read_package(package.blank_package("Kanzlei Muster")).model
        editing.apply(model, {"op": "pack.apply", "pack": "legal"})
        compiled = publisher.BrainRunnerEngine(lambda: None).compile(model)
        assert compiled.bundle["header"]["format"] == "brain-definitions-bundle"
        assert compiled.bundle["header"]["format_version"] == 1
        with tempfile.TemporaryDirectory() as tmp:
            for path, data in package.write_package(model).items():
                (Path(tmp) / path).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / path).write_bytes(data)
            out = Path(tmp) / publisher.BUNDLE_FILE
            subprocess.run([sys.executable, "-m", "brain_runner", "bundle", "--config",
                            str(Path(tmp) / package.PACKAGE_FILE), "--out", str(out)], check=True,
                           env={**os.environ})
            assert out.read_bytes() == compiled.bundle_bytes
        assert bundle_mod.FORMAT == "brain-definitions-bundle"

    def test_client_zero_matches_the_bundle_knowledgebase_committed(self):
        pytest.importorskip("brain_runner.ontology")
        import json

        root = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO")
        if not root:
            pytest.skip("set KNOWLEDGEBASE_CLIENT_ZERO")
        model = package.read_package(package.files_from_directory(Path(root))).model
        compiled = publisher.BrainRunnerEngine(lambda: None).compile(model)
        committed = json.loads((Path(root) / "ontology/generated/bundle.json").read_text(encoding="utf-8"))
        assert compiled.content_hash == committed["header"]["content_hash"]
        assert compiled.bundle["sections"] == committed["sections"]


def test_the_plan_table():
    table = publisher.summarise_plan({
        "items": [{"op": "create_type", "type": "Invoice"}, {"op": "update_attribute", "type": "Mandate", "attribute": "status"}],
        "not_configured": [{"type": "Invoice", "attribute": "number"}], "unresolved_parents": ["Hearing"]})
    assert table["counts"] == {"create": 1, "update": 1, "refused": 1, "not_configured": 1}
    assert table["rows"][1] == {"element": "Mandate.status", "status": "update", "detail": "Attribut ändern"}
