"""Workspace screens and the import API (build plan step B1), on the harness.

The routes are exercised against the in-memory store; test_builder_store.py
holds it to the PostgreSQL store's behaviour, and test_builder_in_the_app.py
runs the import through the real app with its CSRF gate.
"""
import io
import re
import zipfile

import pytest

from builder import package
from builder_harness import SAMPLE_PACKAGE, MemoryWorkspaceStore, StandInUser, make_app


@pytest.fixture(scope="module")
def sample_zip():
    return package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))


def _app(roles=("admin",), store=None):
    user = StandInUser(set(roles))
    store = store if store is not None else MemoryWorkspaceStore({str(user.id): user.display_name})
    return make_app(user, store).test_client(), store


def _upload(data, **fields):
    return {"package": (io.BytesIO(data), "paket.zip"), **fields}


def _import(client, data, name="Beispiel AG"):
    return client.post("/builder/api/import", data=_upload(data, name=name), content_type="multipart/form-data")


class TestTheImportApi:
    def test_preview_counts_what_the_package_holds(self, sample_zip):
        client, store = _app()
        response = client.post("/builder/api/import/preview", data=_upload(sample_zip), content_type="multipart/form-data")
        body = response.get_json()
        assert response.status_code == 200 and body["success"]
        assert body["summary"]["types"] == 4
        assert body["summary"]["levels"] == {"core": 2, "pack": 1, "company": 1}
        assert body["suggested_name"] == "Beispiel AG"
        assert store.list() == []  # a preview stores nothing

    def test_preview_explains_a_broken_package(self):
        client, _ = _app()
        response = client.post("/builder/api/import/preview", data=_upload(b"no zip"), content_type="multipart/form-data")
        assert response.status_code == 422
        assert response.get_json()["error"] == "Die Datei ist kein ZIP-Archiv."

    def test_preview_without_a_file(self):
        client, _ = _app()
        response = client.post("/builder/api/import/preview", data={}, content_type="multipart/form-data")
        assert response.status_code == 400

    def test_import_creates_the_workspace_and_chooses_it(self, sample_zip):
        client, store = _app()
        response = _import(client, sample_zip)
        assert response.status_code == 201
        [workspace] = store.list()
        assert response.get_json()["url"] == f"/builder/w/{workspace.id}"
        with client.session_transaction() as session:
            assert session["builder_workspace"] == workspace.id

    @pytest.mark.parametrize("name", ["", "   ", "x" * 121])
    def test_import_needs_a_name(self, sample_zip, name):
        client, store = _app()
        assert _import(client, sample_zip, name=name).status_code == 400
        assert store.list() == []

    def test_without_a_store_there_is_no_import(self, sample_zip):
        client = make_app(StandInUser({"admin"})).test_client()
        assert _import(client, sample_zip).status_code == 503

    @pytest.mark.parametrize("path", ["/builder/api/import", "/builder/api/import/preview"])
    def test_the_api_refuses_without_a_builder_role(self, sample_zip, path):
        client, store = _app(roles=("member",))
        response = client.post(path, data=_upload(sample_zip, name="x"), content_type="multipart/form-data")
        assert response.status_code == 403
        assert store.list() == []

    def test_the_api_answers_an_anonymous_caller_in_json(self, sample_zip):
        client = make_app(None, MemoryWorkspaceStore()).test_client()
        response = client.post("/builder/api/import/preview", data=_upload(sample_zip), content_type="multipart/form-data")
        assert response.status_code == 401
        assert response.get_json()["success"] is False


def _create(client, name="Muster Treuhand AG"):
    return client.post("/builder/api/workspaces", data={"name": name}, content_type="multipart/form-data")


class TestCreatingAWorkspace:
    def test_it_creates_an_empty_workspace_and_chooses_it(self):
        client, store = _app()
        response = _create(client, " Muster Treuhand AG ")
        assert response.status_code == 201
        [workspace] = store.list()
        assert workspace.name == "Muster Treuhand AG"
        assert response.get_json()["url"] == f"/builder/w/{workspace.id}"
        assert workspace.summary["types"] == 0 and workspace.summary["company"] == "Muster Treuhand AG"
        assert workspace.last_change["path"] == "create"
        with client.session_transaction() as session:
            assert session["builder_workspace"] == workspace.id

    @pytest.mark.parametrize("name", ["", "   ", "x" * 121])
    def test_it_needs_a_name(self, name):
        client, store = _app()
        assert _create(client, name).status_code == 400
        assert store.list() == []

    def test_it_refuses_without_a_builder_role(self):
        client, store = _app(roles=("member",))
        assert _create(client).status_code == 403
        assert store.list() == []

    def test_without_a_store_there_is_nothing_to_create(self):
        client = make_app(StandInUser({"admin"})).test_client()
        assert _create(client).status_code == 503

    def test_the_new_workspace_shows_as_created_and_exports_a_package(self):
        client, store = _app()
        url = _create(client).get_json()["url"]
        assert "Angelegt" in client.get("/builder/arbeitsbereich").get_data(as_text=True)
        exported = client.get(url + "/export")
        with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        assert package.read_package(files).model == store.model(store.list()[0].id)

    def test_its_ontology_says_there_are_no_types_yet(self):
        client, _ = _app()
        _create(client)
        html = client.get("/builder/ontologie").get_data(as_text=True)
        assert "Noch keine eigenen Typen" in html
        assert '"name": "Entity"' in html or '"name":"Entity"' in html  # the core is there

    def test_the_list_offers_both_ways_to_start(self):
        client, _ = _app()
        html = client.get("/builder/arbeitsbereich").get_data(as_text=True)
        assert 'id="b-create"' in html and 'id="b-import"' in html
        assert "Neuer Arbeitsbereich" in html


def _old_workspace(store, actor_id, name="Alt AG"):
    """A workspace as the first version of "Neuer Arbeitsbereich" made it: no processes, spines, pages,
    profiles or mappings in its package."""
    model = package.read_package(package.blank_package(name)).model
    for part in package.PART_PATHS:
        model["package"]["manifest"].pop(part, None)
        if part in package.EMPTY_PARTS:
            model[part] = None
    model["ontology"]["types"].append({"key": "Matter", "level": "company", "attributes": []})
    return store.create(name=name, model=model, actor_id=actor_id, source={"kind": "blank"})


class TestAWorkspaceFromBeforeTheParts:
    def test_the_first_process_brings_the_processes_file(self):
        client, store = _app()
        wid = _old_workspace(store, None)
        client.get(f"/builder/w/{wid}")
        assert "data-proc-new disabled" not in client.get("/builder/prozesse").get_data(as_text=True)
        response = client.post(f"/builder/api/w/{wid}/ontology?view=processes",
                               json={"op": "process.create", "key": "PROC-intake", "name": "Aufnahme",
                                     "instance_types": ["Matter"]})
        assert response.status_code == 200, response.get_json()
        files = package.write_package(store.model(wid))
        assert b"PROC-intake" in files["ontology/processes.yaml"]
        assert package.read_package(files).model["processes"]["processes"][0]["key"] == "PROC-intake"

    def test_pages_profiles_and_mappings_too(self):
        from builder import editing

        client, store = _app()
        wid = _old_workspace(store, None)
        client.get(f"/builder/w/{wid}")
        for op in ({"op": "page.create", "type": "Matter"}, {"op": "profile.create", "id": "partner", "audience": "Partner"}):
            response = client.post(f"/builder/api/w/{wid}/ontology?view=views", json=op)
            assert response.status_code == 200, (op, response.get_json())
        model = store.model(wid)
        manifest = model["package"]["manifest"]
        assert manifest["pages"] == "ontology/page-definitions.yaml" and manifest["profiles"] == "profiles/*.yaml"
        editing.ensure_part(model, "mappings")
        assert manifest["mappings"] == "mappings/*.yaml"
        files = package.write_package(model)
        assert "profiles/partner.yaml" in files and "ontology/page-definitions.yaml" in files

    def test_a_part_whose_place_is_taken_is_refused(self):
        from builder import editing

        model = package.read_package(package.blank_package("Alt AG")).model
        model["package"]["manifest"].pop("processes")
        model["package"]["manifest"]["notes"] = "ontology/processes.yaml"
        assert not package.part_available(model, "processes")
        with pytest.raises(editing.EditError):
            editing.apply(model, {"op": "process.create", "key": "PROC-intake", "instance_types": []})


def _delete(client, wid, name):
    return client.post(f"/builder/api/w/{wid}/delete", json={"name": name})


class TestDeletingAWorkspace:
    def test_the_name_confirms_and_the_workspace_leaves_every_list(self):
        client, store = _app()
        wid = _create(client, "Weg AG").get_json()["url"].rsplit("/", 1)[1]
        html = client.get(f"/builder/w/{wid}").get_data(as_text=True)
        assert "data-ws-delete-open" in html and 'id="b-ws-delete"' in html
        assert _delete(client, wid, "Weg").status_code == 400
        assert store.get(wid) is not None
        response = _delete(client, wid, "Weg AG")
        assert response.status_code == 200 and response.get_json()["url"] == "/builder/arbeitsbereich"
        assert store.list() == [] and store.get(wid) is None
        assert client.get(f"/builder/w/{wid}").status_code == 404
        assert _delete(client, wid, "Weg AG").status_code == 404
        with client.session_transaction() as session:
            assert "builder_workspace" not in session
        assert store.rows[wid]["changes"]  # the history stays

    @pytest.mark.parametrize("role", ["builder_author", "builder_reviewer", "builder_publisher", "builder_viewer"])
    def test_only_who_manages_the_builder_deletes(self, role):
        admin_client, store = _app()
        wid = _create(admin_client, "Bleibt AG").get_json()["url"].rsplit("/", 1)[1]
        client, _ = _app(roles=(role,), store=store)
        assert "data-ws-delete-open" not in client.get(f"/builder/w/{wid}").get_data(as_text=True)
        assert _delete(client, wid, "Bleibt AG").status_code == 403
        assert store.get(wid) is not None


class TestTheScreens:
    def test_the_list_shows_each_workspace_with_its_counts(self, sample_zip):
        client, _ = _app()
        _import(client, sample_zip)
        html = client.get("/builder/arbeitsbereich").get_data(as_text=True)
        assert "Beispiel AG" in html
        assert re.search(r'<td class="is-num">4</td>\s*<td class="is-num">3</td>', html)
        assert "Paket importiert" in html
        assert "Entwurf 1" in html

    def test_an_empty_list_offers_the_import(self):
        client, _ = _app()
        html = client.get("/builder/arbeitsbereich").get_data(as_text=True)
        assert "Noch kein Arbeitsbereich" in html
        assert 'id="b-import"' in html

    def test_the_overview_counts_every_section_and_shows_the_last_change(self, sample_zip):
        client, store = _app()
        _import(client, sample_zip)
        [workspace] = store.list()
        html = client.get(f"/builder/w/{workspace.id}").get_data(as_text=True)
        for label, value in (("Typen", 4), ("Beziehungen", 3), ("Prozesse", 1), ("Zuordnungen", 1), ("Profile", 1)):
            assert re.search(rf'b-stat-label">{label}</span>\s*<span class="b-stat-value">{value}<', html), label
        assert "2 Kern · 1 Paket · 1 Firma" in html
        assert "Paket importiert" in html and "Alex Beispiel" in html

    def test_the_switcher_names_the_chosen_workspace(self, sample_zip):
        client, store = _app()
        _import(client, sample_zip)
        html = client.get("/builder/ontologie").get_data(as_text=True)
        assert re.search(r'b-switcher-name">Beispiel AG<', html)
        assert "Dafür braucht es einen Arbeitsbereich" not in html

    def test_an_unknown_workspace_is_404(self):
        client, _ = _app()
        assert client.get("/builder/w/00000000-0000-0000-0000-000000000000").status_code == 404
        assert client.get("/builder/w/00000000-0000-0000-0000-000000000000/export").status_code == 404


class TestTheOntologyStage:
    def test_without_a_workspace_it_asks_for_one(self):
        client, _ = _app()
        html = client.get("/builder/ontologie").get_data(as_text=True)
        assert "Dafür braucht es einen Arbeitsbereich" in html
        assert 'id="b-onto-data"' not in html

    def test_with_a_workspace_it_carries_the_tree_as_json(self, sample_zip):
        import json

        client, _ = _app()
        _import(client, sample_zip)
        html = client.get("/builder/ontologie").get_data(as_text=True)
        raw = html.split('<script type="application/json" id="b-onto-data">', 1)[1].split("</script>", 1)[0]
        data = json.loads(raw)
        assert data["counts"]["types"] == 4
        assert "js/vendor/cytoscape.min.js" in html
        assert "4 Typen" in html and "2 Probleme" in html

    def test_text_from_the_package_cannot_close_the_script(self, sample_zip):
        """A description is customer text; tojson must keep it inside the JSON."""
        files = package.files_from_directory(SAMPLE_PACKAGE)
        files["ontology/descriptions.yaml"] = files["ontology/descriptions.yaml"].replace(
            b"A dated duty inside a mandate.", b"</script><script>alert(1)</script>")
        client, _ = _app()
        assert _import(client, package.zip_files(files)).status_code == 201
        html = client.get("/builder/ontologie").get_data(as_text=True)
        assert "<script>alert(1)</script>" not in html


class TestTheExport:
    def test_it_downloads_the_package_with_the_same_data(self, sample_zip):
        client, store = _app()
        _import(client, sample_zip)
        [workspace] = store.list()
        response = client.get(f"/builder/w/{workspace.id}/export")
        assert response.status_code == 200
        assert response.mimetype == "application/zip"
        assert response.headers["Content-Disposition"] == 'attachment; filename="beispiel-ag-konfiguration.zip"'
        original = package.files_from_directory(SAMPLE_PACKAGE)
        exported = package.files_from_zip(response.data)
        for path, data in exported.items():
            assert package.load_yaml(data) == package.load_yaml(original[path]), path

    def test_two_exports_are_the_same_bytes(self, sample_zip):
        client, store = _app()
        _import(client, sample_zip)
        [workspace] = store.list()
        first = client.get(f"/builder/w/{workspace.id}/export").data
        assert client.get(f"/builder/w/{workspace.id}/export").data == first
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            assert "brain-config.yaml" in archive.namelist()


class TestStartingFromPacks:
    def _client(self, tenant="af000000"):
        user = StandInUser({"admin"})
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        return make_app(user, store, tenant=tenant).test_client(), store

    def test_the_wizard_offers_the_packs_and_the_platform_tenant(self):
        client, _ = self._client()
        html = client.get("/builder/arbeitsbereich").get_data(as_text=True)
        assert 'value="legal"' in html and 'value="fiduciary"' in html and 'value="software"' in html
        assert "af000000" in html

    def test_a_workspace_from_two_packs_linked_to_the_tenant(self):
        client, store = self._client()
        response = client.post("/builder/api/workspaces",
                               data={"name": "Kanzlei Muster", "packs": ["legal", "fiduciary"], "tenant": "link"})
        assert response.status_code == 201
        [workspace] = store.list()
        assert workspace.tenant == "af000000"
        assert [p["key"] for p in store.model(workspace.id)["packs"]] == ["legal", "fiduciary"]
        assert workspace.summary["levels"]["pack"] == 16

    def test_the_tenant_never_comes_from_the_request(self):
        client, store = self._client()
        client.post("/builder/api/workspaces", data={"name": "Muster", "tenant": "someone-else"})
        assert store.list()[0].tenant is None

    def test_without_a_known_tenant_it_is_linked_later(self):
        client, store = self._client(tenant=None)
        assert "af000000" not in client.get("/builder/arbeitsbereich").get_data(as_text=True)
        client.post("/builder/api/workspaces", data={"name": "Muster", "tenant": "link"})
        assert store.list()[0].tenant is None

    def test_an_unknown_pack_is_refused(self):
        client, store = self._client()
        response = client.post("/builder/api/workspaces", data={"name": "Muster", "packs": ["nowhere"]})
        assert response.status_code == 404 and store.list() == []

    def test_the_overview_offers_an_update_and_the_diff_shows_it(self):
        client, store = self._client()
        client.post("/builder/api/workspaces", data={"name": "Kanzlei Muster", "packs": ["legal"]})
        [workspace] = store.list()
        assert "Aktuell" in client.get(f"/builder/w/{workspace.id}").get_data(as_text=True)
        row = store.rows[workspace.id]
        row["model"]["packs"] = [{"key": "legal", "version": "0.9"}]
        row["model"]["ontology"]["types"] = [t for t in row["model"]["ontology"]["types"] if t["key"] != "Claim"]
        overview = client.get(f"/builder/w/{workspace.id}").get_data(as_text=True)
        assert "Version 1.0 verfügbar" in overview
        diff = client.get(f"/builder/w/{workspace.id}/pakete/legal").get_data(as_text=True)
        assert "Claim" in diff and "Version 1.0 übernehmen" in diff
        assert store.model(workspace.id)["packs"][0]["version"] == "0.9"
        assert client.get(f"/builder/w/{workspace.id}/pakete/nowhere").status_code == 404
