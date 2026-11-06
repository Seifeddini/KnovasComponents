"""An import is reviewed whole, and the approval binds to it (BB-07, finding B1; FEAT-2026-11-05-knovas-brain-v1, S-5).

The pre-review reproduced it: one person with the author, reviewer and publisher roles (not an administrator)
imported a package whose ``brain-builder.yaml`` carried an access design, linked the tenant, ran the dry run (the
ontology only), approved with the content hash alone and published. The review page had shown "Paket importiert" and
zero changes; publish wrote an access group and a folder rule onto the tenant that nobody had been shown.

The founder decided on 2026-11-06: self-approval stays allowed (he works alone), but the review page and the dry run
show everything the import and the publish change -- ontology, processes, mappings, access groups, folder rules, role
mapping -- as a diff against the published state, the approval binds to exactly that, and the audit records who
imported and who approved; an approval by the importer says so plainly.
"""
import io

import pytest

from builder import package, review
from builder_harness import (SAMPLE_PACKAGE, FakeAccess, FakeEngine, MemoryDirectory, MemoryWorkspaceStore, StandInUser,
                             make_app)

TENANT = "t-import-review"
ACCESS = {"groups": [{"key": "few", "name": "Nur wenige"}], "rules": [{"key": "docs", "prefix": "docs/", "groups": ["few"]}]}
ROLES = [{"key": "member", "profiles": ["leser"]}]


def _package_zip(*, access=ACCESS, roles=None) -> bytes:
    """The sample package, with the Builder's own file carrying an access design (and a role mapping)."""
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    model["access"] = access
    if roles is not None:
        model["profile_roles"] = roles
    return package.zip_files(package.write_package(model))


@pytest.fixture
def world():
    # One person, every builder role but administrator: the pre-review's importer.
    one = StandInUser({"builder_author", "builder_reviewer", "builder_publisher"}, "Ira Import", "ira@example.org")
    second = StandInUser({"builder_reviewer"}, "Rita Prüft", "rita@example.org")
    store = MemoryWorkspaceStore({str(one.id): one.display_name, str(second.id): second.display_name})
    engine = FakeEngine()
    access = FakeAccess(MemoryDirectory(store))
    written_roles = []
    current_roles = {"value": None}
    app = make_app(one, store, tenant=TENANT, engine=engine, access=access,
                   roles_sink=lambda mapping, user: written_roles.append(mapping),
                   roles_source=lambda: current_roles["value"])
    return {"http": app.test_client(), "store": store, "engine": engine, "access": access, "one": one,
            "second": second, "gate": app.extensions["builder_gate"], "roles": written_roles,
            "current_roles": current_roles}


def _import(world, data=None):
    answer = world["http"].post("/builder/api/import", content_type="multipart/form-data",
                                data={"name": "Muster AG", "package": (io.BytesIO(data or _package_zip()), "paket.zip")})
    assert answer.status_code == 201, answer.get_json()
    [workspace] = world["store"].list()
    assert world["http"].post(f"/builder/api/w/{workspace.id}/tenant").status_code == 200
    return workspace.id


def _post(world, workspace, what, **body):
    return world["http"].post(f"/builder/api/w/{workspace}/{what}", json=body)


def _hash(world, workspace):
    return world["store"].draft(workspace)["content_hash"]


class TestTheReproduction:
    def test_the_review_shows_everything_the_import_brought(self, world):
        workspace = _import(world)
        rows = world["store"].draft_changes(workspace)
        assert review.change_set(rows)["total"] == 0, "the change log still has only the origin"
        diff = review.model_diff(None, world["store"].model(workspace), rows)
        found = {s["section"]: [g["key"] for g in s["elements"]] for s in diff["sections"]}
        assert found["access.groups"] == ["few"] and found["access.rules"] == ["docs"]
        assert found["ontology.types"] and found["mappings"] and found["processes"]
        assert all(g["authors"] == ["Ira Import"] and g["from_origin"] for s in diff["sections"] for g in s["elements"])
        page = world["http"].get("/builder/pruefen").get_data(as_text=True)
        changes = page.split('id="b-rv-changes"', 1)[1].split('id="b-rv-dry"', 1)[0]
        assert "Zugriffsgruppen" in changes and "Nur wenige" in changes
        assert "Ordnerregeln" in changes and "docs/ → few" in changes
        assert "Paket importiert von Ira Import" in changes
        assert "keine Änderung" not in changes

    def test_the_dry_run_shows_what_publish_writes_beside_the_ontology(self, world):
        workspace = _import(world, _package_zip(roles=ROLES))
        dry = _post(world, workspace, "dry-run").get_json()
        assert dry["success"], dry
        effects = {(r["part"], r["element"], r["status"]) for r in dry["effects"]["rows"]}
        assert ("access", "Zugriffsgruppe Nur wenige", "create") in effects
        assert ("access", "Ordnerregel docs/", "create") in effects
        assert ("roles", "Rolle member", "create") in effects
        assert dry["diff_total"] > 0 and {"access.groups", "access.rules"} <= {s["section"] for s in dry["diff"]}
        assert len(dry["review_hash"]) == 64
        page = world["http"].get("/builder/pruefen").get_data(as_text=True)
        assert "Was das Veröffentlichen außerdem schreibt" in page and "Ordnerregel docs/" in page

    def test_the_importer_may_approve_and_publish_and_it_is_recorded_as_such(self, world):
        workspace = _import(world)
        dry = _post(world, workspace, "dry-run").get_json()
        approved = _post(world, workspace, "approve", content_hash=_hash(world, workspace), review_hash=dry["review_hash"])
        assert approved.status_code == 200 and approved.get_json()["own_import"] is True
        [approval] = world["store"].approvals(workspace)
        assert approval["own_import"] is True and approval["review_hash"] == dry["review_hash"]
        page = world["http"].get("/builder/pruefen").get_data(as_text=True)
        assert "data-own-import" in page and "Vom Importeur selbst freigegeben" in page
        published = _post(world, workspace, "publish", content_hash=_hash(world, workspace))
        body = published.get_json()
        assert published.status_code == 200, body
        audit = body["result"]["audit"]
        assert audit["origin"] == "import" and audit["imported_by"] == str(world["one"].id)
        assert audit["approved_by_importer"] is True and audit["review_hash"] == dry["review_hash"]
        assert [a["id"] for a in audit["approved_by"]] == [str(world["one"].id)]
        # What was shown is what was written.
        assert body["result"]["access"] == {"written": True, "groups": 1, "rules": 1}
        assert world["access"].rule_rows[0]["pointer_prefix"] == "docs/"
        activity = world["http"].get(f"/builder/w/{workspace}/aktivitaet").get_data(as_text=True)
        assert "den eigenen Import" in activity

    def test_a_second_person_s_approval_is_not_marked(self, world):
        workspace = _import(world)
        dry = _post(world, workspace, "dry-run").get_json()
        world["gate"].user = world["second"]
        assert _post(world, workspace, "approve", content_hash=_hash(world, workspace),
                     review_hash=dry["review_hash"]).get_json()["own_import"] is False
        world["gate"].user = world["one"]
        body = _post(world, workspace, "publish", content_hash=_hash(world, workspace)).get_json()
        assert body["result"]["audit"]["approved_by_importer"] is False


class TestTheApprovalBindsToTheDiff:
    def _approved(self, world, **kw):
        workspace = _import(world, **kw)
        dry = _post(world, workspace, "dry-run").get_json()
        assert _post(world, workspace, "approve", content_hash=_hash(world, workspace),
                     review_hash=dry["review_hash"]).status_code == 200
        return workspace

    def test_a_folder_rule_made_live_since_voids_it(self, world):
        workspace = self._approved(world)
        world["access"].create_rule("docs/", [], ())      # the tenant changed: publish would now write no rule
        refused = _post(world, workspace, "publish", content_hash=_hash(world, workspace))
        assert refused.status_code == 409 and "nicht mehr das, was freigegeben" in refused.get_json()["error"]
        assert world["engine"].applied == [] and "g-nur-wenige" not in world["access"].groups, "nothing written"

    def test_a_group_made_live_since_voids_it(self, world):
        workspace = self._approved(world)
        world["access"].create("Nur wenige", "g-admin", ())
        assert _post(world, workspace, "publish", content_hash=_hash(world, workspace)).status_code == 409

    def test_a_changed_role_mapping_on_the_platform_voids_it(self, world):
        workspace = self._approved(world, data=_package_zip(roles=ROLES))
        world["current_roles"]["value"] = {"member": ["alles"]}
        assert _post(world, workspace, "publish", content_hash=_hash(world, workspace)).status_code == 409
        assert world["roles"] == []

    def test_a_changed_tenant_ontology_voids_it(self, world):
        workspace = self._approved(world)
        world["engine"].tenant["Mandate"] = set()          # another writer made a type since
        assert _post(world, workspace, "publish", content_hash=_hash(world, workspace)).status_code == 409

    def test_a_new_dry_run_and_approval_publish_it(self, world):
        workspace = self._approved(world)
        world["access"].create_rule("docs/", [], ())
        dry = _post(world, workspace, "dry-run").get_json()
        assert not any(r["element"] == "Ordnerregel docs/" for r in dry["effects"]["rows"]), "the rule is there now"
        page = world["http"].get("/builder/pruefen").get_data(as_text=True)
        assert "data-stale-approvals" in page, "the earlier approval is shown as no longer valid"
        assert _post(world, workspace, "approve", content_hash=_hash(world, workspace),
                     review_hash=dry["review_hash"]).status_code == 200
        published = _post(world, workspace, "publish", content_hash=_hash(world, workspace))
        assert published.status_code == 200, published.get_json()
        assert published.get_json()["result"]["access"] == {"written": True, "groups": 1, "rules": 0}

    def test_an_approval_of_a_digest_the_newest_dry_run_does_not_have_is_refused(self, world):
        workspace = _import(world)
        first = _post(world, workspace, "dry-run").get_json()
        world["access"].create_rule("docs/", [], ())
        second = _post(world, workspace, "dry-run").get_json()
        assert first["review_hash"] != second["review_hash"]
        stale = _post(world, workspace, "approve", content_hash=_hash(world, workspace), review_hash=first["review_hash"])
        assert stale.status_code == 409 and world["store"].approvals(workspace) == []

    def test_an_approval_without_a_digest_does_not_publish(self, world):
        """An approval held before this change (the content hash only) no longer lets a draft be published."""
        workspace = _import(world)
        _post(world, workspace, "dry-run")
        assert world["store"].approve(workspace, world["second"].id, _hash(world, workspace))
        assert _post(world, workspace, "publish", content_hash=_hash(world, workspace)).status_code == 409

    def test_a_content_change_after_the_approval_voids_it(self, world):
        workspace = self._approved(world)
        assert _post(world, workspace, "ontology", op="type.create", key="Erinnerung").status_code == 200
        refused = _post(world, workspace, "publish", content_hash=_hash(world, workspace))
        assert refused.status_code == 409 and "nicht freigegeben" in refused.get_json()["error"]


class TestAgainstThePublishedState:
    def test_a_later_draft_shows_only_what_changed_since_the_publish(self, world):
        workspace = _import(world)
        dry = _post(world, workspace, "dry-run").get_json()
        _post(world, workspace, "approve", content_hash=_hash(world, workspace), review_hash=dry["review_hash"])
        assert _post(world, workspace, "publish", content_hash=_hash(world, workspace)).status_code == 200
        assert _post(world, workspace, "ontology", op="type.create", key="Erinnerung").status_code == 200
        rows = world["store"].draft_changes(workspace)
        diff = review.model_diff(world["store"].published_model(workspace), world["store"].model(workspace), rows)
        assert [(s["section"], [g["key"] for g in s["elements"]]) for s in diff["sections"]] == \
            [("ontology.types", ["Erinnerung"])]
        assert diff["sections"][0]["elements"][0]["authors"] == ["Ira Import"]
        assert not diff["sections"][0]["elements"][0]["from_origin"]

    def test_a_source_s_new_address_is_shown_with_its_value(self):
        group = review.element_diff("sources/kunden", {"key": "kunden", "settings": {"url": "https://a.example/"}},
                                    {"key": "kunden", "settings": {"url": "https://b.example/"}})
        assert any("https://b.example/" in line["text"] for line in group["lines"])

    def test_the_parts_outside_the_sections_are_in_the_diff(self):
        before = {"owners": {"version": 1, "aliases": {}}, "company": {"name": "A"}}
        after = {"owners": {"version": 1, "aliases": {"x": "y"}}, "company": {"name": "A"}}
        diff = review.model_diff(before, after)
        [parts] = diff["sections"]
        assert parts["section"] == "parts" and [g["key"] for g in parts["elements"]] == ["owners"]
