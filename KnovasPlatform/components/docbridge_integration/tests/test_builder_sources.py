"""A company's sources (build plan step B7): components, sources, credentials, test and sample.

The readers run over stand-in transports (builder_harness): brain-runner reads
a checkout on disk the way it reads a GitHub repository, the connector is a
folder answered as /discover answers, the HTTP source a function. The browser
acceptance is in test_builder_screens.py.
"""
import io
import json
import logging

import pytest

from builder import editing, package, sources
from builder_harness import (SAMPLE_PACKAGE, FakeConnector, MemorySecrets, MemoryWorkspaceStore, StandInUser,
                             make_app, stand_in_readers)

TENANT = "test-tenant-0001"
TOKEN = "ghp_secret_never_shown_0123456789"
PUBLIC = lambda host: [(None, None, None, None, ("93.184.216.34", 443))]  # noqa: E731


@pytest.fixture
def share(tmp_path):
    root = tmp_path / "ablage"
    for path, size in (("Mandate/Muster AG/Vollmacht.pdf", 1200), ("Mandate/Muster AG/Honorar.docx", 800),
                       ("Mandate/Beispiel GmbH/Klage.pdf", 3000), ("Vorlagen/Brief.docx", 400)):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"x" * size)
    return root


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "docs" / "specs").mkdir(parents=True)
    (root / "docs" / "specs" / "a.yaml").write_text("key: A\nstatus: draft\nowner: ROLE-X\n", encoding="utf-8")
    (root / "docs" / "specs" / "b.yaml").write_text("key: B\nstatus: approved\n", encoding="utf-8")
    (root / "docs" / "guide.md").write_text("---\ntitle: Guide\n---\n# Guide\n\nHow to.\n", encoding="utf-8")
    (root / "README.md").write_text("# Repo\n", encoding="utf-8")
    return root


class TestTheProfile:
    def test_types_empty_share_distinct_and_examples(self):
        p = sources.profile([{"key": "A", "n": 1, "on": "2026-01-02", "tags": ["x", "y"], "meta": {"owner": "R"}},
                             {"key": "B", "n": 2, "on": None, "tags": [], "meta": {"owner": "R"}},
                             {"key": "A", "n": "drei"}])
        fields = {f["name"]: f for f in p["fields"]}
        assert fields["key"]["type"] == "Text" and fields["key"]["distinct"] == 2 and fields["key"]["examples"] == ["A", "B"]
        assert fields["n"]["type"] == "gemischt" and fields["on"]["type"] == "Datum"
        assert fields["on"]["empty_share"] == pytest.approx(0.667, abs=0.001)
        assert fields["meta.owner"]["examples"] == ["R"] and fields["tags"]["type"] == "Liste"
        assert p["count"] == 3 and p["rows"][0]["tags"] == "x, y"
        # Most filled first, at most FIELD_LIMIT of them.
        assert p["fields"][0]["name"] == "key" and p["more_fields"] == 0
        wide = sources.profile([{f"f{i}": i for i in range(60)}])
        assert len(wide["fields"]) == sources.FIELD_LIMIT and wide["more_fields"] == 20
        assert len(wide["rows"][0]) == sources.FIELD_LIMIT


class TestTheReaders:
    def test_a_repository_through_brain_runner(self, repo):
        pytest.importorskip("brain_runner.extractors")
        from brain_runner.sources.local import LocalSource

        reader = sources.GitHubReader(LocalSource(repo))
        result = reader.test()
        assert dict(result["counts"])["Dateien"] == 4
        by_id = {c["id"]: c["count"] for c in result["collections"]}
        assert by_id == {"yaml_file:docs/specs/**/*.yaml": 2, "markdown_front_matter:docs/*.md": 1,
                         "markdown_front_matter:*.md": 1}
        p = sources.profile(reader.sample("yaml_file:docs/specs/**/*.yaml"))
        assert {"path", "data.key", "data.status", "data.owner"} <= {f["name"] for f in p["fields"]}
        with pytest.raises(sources.ReaderError):
            reader.sample("python_ast:**/*.py")
        with pytest.raises(sources.ReaderError):
            reader.sample("yaml_file:../secrets/*.yaml")

    def test_a_folder_through_the_connector(self, share):
        reader = sources.KnovasConnectorReader(FakeConnector(share), "Mandate")
        result = reader.test()
        assert result["counts"] == [["Dateien", 3], ["Ordner", 2], ["Dateiarten", 2]]
        assert [(c["id"], c["count"]) for c in result["collections"]] == [
            ("*", 3), ("folder:Muster AG", 2), ("folder:Beispiel GmbH", 1)]
        items = reader.sample("folder:Muster AG")
        assert {i["name"] for i in items} == {"Vollmacht.pdf", "Honorar.docx"}
        assert set(items[0]) == {"path", "name", "folder", "extension", "size_bytes", "modified_at"}
        with pytest.raises(sources.ReaderError, match="watch roots"):
            sources.KnovasConnectorReader(FakeConnector(share), "Anderswo").test()

    def test_json_over_https(self):
        class Answer:
            def __init__(self, status, body):
                self.status_code, self.content = status, body

        sent = {}

        def get(url, headers, timeout, stream, allow_redirects):
            assert allow_redirects is False
            sent.update(headers)
            return Answer(200, json.dumps({"data": {"clients": [{"id": 1, "name": "A"}, {"id": 2}]}, "page": 1}).encode())

        reader = sources.HttpJsonReader("https://example.org/api", "tok", get=get, resolve=PUBLIC)
        assert reader.test()["collections"] == [{"id": "$.data.clients", "label": "$.data.clients", "kind": "Datensätze", "count": 2}]
        assert reader.sample("$.data.clients")[1] == {"id": 2} and sent["Authorization"] == "Bearer tok"
        assert pytest.raises(sources.ReaderError, sources.HttpJsonReader(
            "https://x", get=lambda *a, **k: Answer(200, b"<html>"), resolve=PUBLIC).test)
        with pytest.raises(sources.ReaderError, match="HTTP 401"):
            sources.HttpJsonReader("https://x", get=lambda *a, **k: Answer(401, b"{}"), resolve=PUBLIC).test()
        with pytest.raises(sources.ReaderError, match="leitet weiter"):
            sources.HttpJsonReader("https://x", get=lambda *a, **k: Answer(302, b""), resolve=PUBLIC).test()

    @pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.1.15", "::1", "fd00::1"])
    def test_json_only_from_the_public_network(self, address):
        called = []
        reader = sources.HttpJsonReader("https://inside.example/api", get=lambda *a, **k: called.append(1),
                                        resolve=lambda host: [(None, None, None, None, (address, 443))])
        with pytest.raises(sources.ReaderError, match="öffentliche Netz"):
            reader.test()
        assert called == []
        with pytest.raises(sources.ReaderError, match="https://"):
            sources.public_https("http://example.org/x", PUBLIC)


@pytest.fixture
def model():
    return package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model


class TestTheEdits:
    def test_a_source_is_an_element_of_the_model(self, model):
        changes = editing.apply(model, {"op": "source.create", "kind": "github", "name": "Wissensbasis",
                                        "component": "c1", "settings": {"repository": "acme/kb", "ref": "main"}})
        assert changes[0]["path"] == "sources/wissensbasis"
        assert model["sources"] == [{"key": "wissensbasis", "name": "Wissensbasis", "kind": "github", "component": "c1",
                                     "sync_mode": "manual", "access": "read", "settings": {"repository": "acme/kb", "ref": "main"},
                                     "credential_set": False}]
        editing.apply(model, {"op": "source.create", "kind": "github", "name": "Wissensbasis", "component": "c1",
                              "settings": {"repository": "acme/kb2"}})
        assert [s["key"] for s in model["sources"]] == ["wissensbasis", "wissensbasis-2"]
        credential = editing.apply(model, {"op": "source.credential", "key": "wissensbasis", "set": True})
        assert model["sources"][0]["credential_set"] is True
        with pytest.raises(editing.EditError, match="inzwischen"):
            editing.undo(model, changes)   # the source changed since: taken back in order only
        editing.undo(model, credential)
        editing.undo(model, changes)
        assert [s["key"] for s in model["sources"]] == ["wissensbasis-2"]

    @pytest.mark.parametrize("op", [
        {"kind": "crm", "name": "CRM", "component": "c"},
        {"kind": "github", "name": "X", "component": "c", "settings": {"repository": "not a repo"}},
        {"kind": "http_json", "name": "X", "component": "c", "settings": {"url": "http://plain"}},
        {"kind": "knovas_connector", "name": "X", "component": "c", "settings": {"folder": "../etc"}},
        {"kind": "github", "name": "X", "settings": {"repository": "a/b"}},
    ])
    def test_refused(self, model, op):
        with pytest.raises(editing.EditError):
            editing.apply(model, {"op": "source.create", **op})


@pytest.fixture
def world(share, repo):
    user = StandInUser({"admin"})
    store = MemoryWorkspaceStore({str(user.id): user.display_name})
    secrets = MemorySecrets()
    readers = stand_in_readers(github_root=repo, connector=FakeConnector(share))
    app = make_app(user, store, tenant=TENANT, secrets=secrets, readers=readers)
    client = app.test_client()
    data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
    assert client.post("/builder/api/import", data={"package": (io.BytesIO(data), "p.zip"), "name": "Beispiel AG"},
                       content_type="multipart/form-data").status_code == 201
    [workspace] = store.list()
    return {"client": client, "store": store, "secrets": secrets, "readers": readers, "app": app, "id": workspace.id}


def _register(w, kind, name, **extra):
    return w["client"].post("/builder/api/components", json={"kind": kind, "name": name, **extra})


def _edit(w, **op):
    return w["client"].post(f"/builder/api/w/{w['id']}/ontology?view=sources", json=op)


class TestTheRoutes:
    def test_components_are_registered_for_the_platform_s_tenant(self, world):
        made = _register(world, "github_runner", "GitHub-Runner", site="Plattform", owner="IT")
        assert made.status_code == 201 and made.get_json()["components"][0]["kind"] == "github_runner"
        assert _register(world, "github_runner", "GitHub-Runner").status_code == 409
        assert _register(world, "crm", "CRM").status_code == 400
        assert _register(world, "github_runner", "X", settings={"base_url": "https://x"}).status_code == 400
        assert _register(world, "knovas_connector", "Ablage", settings={"base_url": "https://rc.local:5001"}).status_code == 201
        page = world["client"].get("/builder/verbinden").get_data(as_text=True)
        assert "GitHub-Runner" in page and "https://rc.local:5001" in page and "Geplant" in page
        # The tenant is the Platform's own: another one in the request is ignored.
        assert world["store"].components("other-tenant") == []

    def test_without_a_tenant_no_component(self, share):
        app = make_app(StandInUser({"admin"}), MemoryWorkspaceStore())
        assert app.test_client().post("/builder/api/components", json={"kind": "http_json", "name": "X"}).status_code == 409

    def test_a_source_needs_a_registered_component_of_its_kind(self, world):
        connector = _register(world, "knovas_connector", "Ablage").get_json()["id"]
        answer = _edit(world, op="source.create", kind="github", name="KB", component=connector,
                       settings={"repository": "acme/kb"})
        assert answer.status_code == 400 and "GitHub-Runner" in answer.get_json()["error"]
        assert _edit(world, op="source.create", kind="github", name="KB", component="0000",
                     settings={"repository": "acme/kb"}).status_code == 404
        made = _edit(world, op="source.create", kind="knovas_connector", name="Mandate", component=connector,
                     settings={"folder": "Mandate"})
        view = made.get_json()["sources"]
        assert view["sources"][0]["component_name"] == "Ablage" and view["sources"][0]["sync_mode"] == "manual"

    def test_the_credential_is_written_only(self, world, caplog):
        caplog.set_level(logging.INFO)
        runner = _register(world, "github_runner", "GitHub-Runner").get_json()["id"]
        _edit(world, op="source.create", kind="github", name="KB", component=runner, settings={"repository": "acme/kb"})
        refused = world["client"].post(f"/builder/api/w/{world['id']}/sources/kb/test")
        assert refused.status_code == 502 and "kein Zugang" in refused.get_json()["error"]
        saved = world["client"].post(f"/builder/api/w/{world['id']}/sources/kb/credential", json={"value": TOKEN})
        assert saved.status_code == 200 and TOKEN not in saved.get_data(as_text=True)
        assert saved.get_json()["sources"]["sources"][0]["credential_set"] is True
        assert world["secrets"].values == {f"builder.sources.{world['id']}.kb.credential": TOKEN}
        assert world["client"].post(f"/builder/api/w/{world['id']}/sources/kb/test").status_code == 200
        assert world["readers"].secrets == [TOKEN]
        page = world["client"].get("/builder/quellen").get_data(as_text=True)
        bundle_view = world["client"].get(f"/builder/w/{world['id']}/export").data
        assert TOKEN not in page and TOKEN.encode() not in bundle_view
        assert TOKEN not in caplog.text
        assert "sources/kb" in [c["path"] for c in world["store"].draft_changes(world["id"])]

    def test_a_source_that_moves_loses_its_credential(self, world):
        runner = _register(world, "github_runner", "GitHub-Runner").get_json()["id"]
        _edit(world, op="source.create", kind="github", name="KB", component=runner, settings={"repository": "acme/kb"})
        world["client"].post(f"/builder/api/w/{world['id']}/sources/kb/credential", json={"value": TOKEN})
        kept = _edit(world, op="source.update", key="kb", fields={"settings": {"repository": "acme/kb", "ref": "dev"}})
        assert kept.get_json()["sources"]["sources"][0]["credential_set"] is True and world["secrets"].values
        moved = _edit(world, op="source.update", key="kb", fields={"settings": {"repository": "evil/kb"}})
        assert moved.get_json()["sources"]["sources"][0]["credential_set"] is False
        assert world["secrets"].values == {}

    def test_a_test_and_a_sample_log_counts_only(self, world, caplog):
        caplog.set_level(logging.INFO)
        connector = _register(world, "knovas_connector", "Ablage").get_json()["id"]
        _edit(world, op="source.create", kind="knovas_connector", name="Mandate", component=connector,
              settings={"folder": "Mandate"})
        result = world["client"].post(f"/builder/api/w/{world['id']}/sources/mandate/test").get_json()["result"]
        assert dict(result["counts"])["Dateien"] == 3
        sample = world["client"].post(f"/builder/api/w/{world['id']}/sources/mandate/sample",
                                      json={"collection": "folder:Muster AG"}).get_json()
        assert sample["profile"]["count"] == 2 and {r["name"] for r in sample["profile"]["rows"]} == {"Vollmacht.pdf", "Honorar.docx"}
        for secret_text in ("Vollmacht", "Muster AG", "Honorar"):
            assert secret_text not in caplog.text

    @pytest.mark.parametrize("failure,said", [(ImportError("No module named 'brain_runner'"), "brain-runner ist nicht installiert"),
                                              (OSError("read-only file system"), "nicht öffnen (OSError)")])
    def test_a_reader_that_cannot_open_is_answered_as_json(self, world, failure, said):
        def broken(component, source, secret, workspace_id):
            raise failure

        app = make_app(StandInUser({"admin"}), world["store"], tenant=TENANT, secrets=world["secrets"], readers=broken)
        client = app.test_client()
        runner = _register(world, "github_runner", "GitHub-Runner").get_json()["id"]
        _edit(world, op="source.create", kind="github", name="KB", component=runner, settings={"repository": "acme/kb"})
        world["client"].post(f"/builder/api/w/{world['id']}/sources/kb/credential", json={"value": TOKEN})
        for route, body in (("test", None), ("sample", {"collection": "file_glob:*.md"})):
            answer = client.post(f"/builder/api/w/{world['id']}/sources/kb/{route}", json=body)
            assert answer.status_code == 502 and answer.is_json and said in answer.get_json()["error"]

    def test_only_a_builder_role(self, world):
        world["app"].extensions["builder_gate"].user = StandInUser({"member"})
        client = world["client"]
        assert client.get("/builder/verbinden").status_code == 403
        assert client.get("/builder/quellen").status_code == 403
        assert client.post("/builder/api/components", json={"kind": "http_json", "name": "X"}).status_code == 403
        for action in ("test", "sample", "credential"):
            assert client.post(f"/builder/api/w/{world['id']}/sources/kb/{action}", json={}).status_code == 403
