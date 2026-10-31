"""Mapping a source to the ontology (build plan step B8): steps, checks, edits, preview, dry run, routes.

The mappings run through brain-runner (its run and its plan); a source's
items come from the stand-in transports of builder_harness. Client Zero's
mappings are checked when $KNOWLEDGEBASE_CLIENT_ZERO names its folder; the
browser acceptance is in test_builder_screens.py.
"""
import io
import os
from pathlib import Path

import pytest

from builder import editing, mappings as M, package, publisher, review
from builder_harness import (SAMPLE_PACKAGE, FakeConnector, FakeEngine, MemorySecrets, MemoryWorkspaceStore,
                             StandInUser, make_app, stand_in_readers)

pytest.importorskip("brain_runner.mapping")
TENANT = "test-tenant-0001"


@pytest.fixture
def share(tmp_path):
    root = tmp_path / "ablage"
    for path in ("Mandate/Muster AG/Vollmacht.pdf", "Mandate/Muster AG/Honorar.docx", "Mandate/Nord AG/Klage.pdf"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"x" * 100)
    return root


@pytest.fixture
def model():
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    for op in ({"op": "source.create", "kind": "knovas_connector", "name": "Ablage", "component": "c1",
                "settings": {"folder": "Mandate"}},
               {"op": "mapping.create", "id": "ablage-mandate", "source": "ablage", "collection": "*", "type": "Mandate"}):
        editing.apply(model, op)
    return model


def _entry(model, mapping_id="ablage-mandate"):
    return next(m for m in model["mappings"] if m["definition"]["id"] == mapping_id)


def _bind(model, target, field, steps=(), emit=0):
    return editing.apply(model, {"op": "mapping.bind", "id": "ablage-mandate", "emit": emit, "target": target,
                                 "field": field, "steps": list(steps)})


class TestTheSteps:
    @pytest.mark.parametrize("field,steps,expected", [
        ("name", [], "f.name"),
        ("f.data.my-key", [], 'f.data."my-key"'),
        ("doc.path", [{"step": "trim"}], "trim(doc.path)"),
        ("folder", [{"step": "split", "separator": "/", "part": 1}], "split(f.folder, '/')[1]"),
        ("extension", [{"step": "replace", "old": ".", "new": ""}], "replace(f.extension, '.', '')"),
        ("modified_at", [{"step": "date"}], "left(str(f.modified_at), `10`)"),
        ("status", [{"step": "map", "values": {"open": "offen"}}], "((f.status == 'open' && 'offen') || f.status)"),
        ("key", [{"step": "join", "fields": ["name"], "separator": " — "}], "join(' — ', [str(f.key), str(f.name)])"),
    ])
    def test_steps_become_brain_runner_s_expressions(self, field, steps, expected):
        expr = M.compile_steps(field, steps)
        assert expr == expected and M.expression_problem(expr) is None

    def test_what_a_step_cannot_take(self):
        for steps in ([{"step": "split"}], [{"step": "map", "values": {}}], [{"step": "join"}], [{"step": "nope"}]):
            with pytest.raises(M.MappingError):
                M.compile_steps("x", steps)
        assert M.expression_problem("f.(") and M.expression_problem("")

    def test_a_plain_expression_shows_as_its_field(self):
        assert M.simple_field("c.value.name", "c") == "c.value.name"
        assert M.simple_field('c.value."x-y"', "c") == "c.value.x-y"
        assert M.simple_field("doc.path", "c") == "doc.path"
        assert M.simple_field("join(' ', [c.a])", "c") is None


class TestTheEdits:
    def test_a_new_mapping_reads_its_source_through_the_component(self, model):
        entry = _entry(model)
        assert entry["file"] == "mappings/ablage-mandate.yaml"
        assert entry["definition"]["source"] == {"extractor": M.CONNECTOR_EXTRACTOR, "glob": "Mandate/**/*",
                                                 "select": "@", "as": "f"}
        assert entry["builder"]["source"] == "ablage"
        problems = {p["target"] for p in M.problems(model, entry)}
        assert {"0:key", "0:name"} <= problems   # brain-runner's schema: key and name are not empty

    def test_binding_with_steps_undoes(self, model):
        changes = _bind(model, "key", "f.path")
        _bind(model, "name", "f.name")
        _bind(model, "fact:status", "f.suffix", [{"step": "map", "values": {".pdf": "open"}, "keep": False}])
        em = _entry(model)["definition"]["emits"][0]
        assert em["key"] == "f.path" and em["facts"]["status"]["priority"] == 10
        assert _entry(model)["builder"]["steps"]["0:fact:status"]["steps"][0]["step"] == "map"
        assert M.problems(model, _entry(model)) == []
        with pytest.raises(editing.EditError):
            editing.undo(model, changes)   # taken back in order only
        assert changes[0]["path"] == "mappings/ablage-mandate"

    def test_a_fact_that_is_no_attribute_and_a_relation_that_does_not_fit(self, model):
        _bind(model, "key", "f.path")
        _bind(model, "name", "f.name")
        _bind(model, "fact:colour", "f.name")
        editing.apply(model, {"op": "mapping.relation", "id": "ablage-mandate", "emit": 0, "relation": "client_of",
                              "to_type": "Organization", "field": "f.path"})
        found = {p["target"]: p["message"] for p in M.problems(model, _entry(model))}
        assert "kein Attribut" in found["0:fact:colour"]
        assert "passt nicht" in found["0:relation:0"]
        editing.apply(model, {"op": "mapping.relation", "id": "ablage-mandate", "emit": 0, "index": 0, "remove": True})
        editing.apply(model, {"op": "mapping.relation", "id": "ablage-mandate", "emit": 0, "relation": "client_of",
                              "to_type": "Organization", "direction": "in", "field": "f.path",
                              "steps": [{"step": "split", "separator": "/", "part": 1}]})
        assert "0:relation:0" not in {p["target"] for p in M.problems(model, _entry(model))}

    def test_settings_and_the_expert_view(self, model):
        editing.apply(model, {"op": "mapping.settings", "id": "ablage-mandate",
                              "fields": {"priority": 30, "edit_policy": "brain_owned", "max_retire_share": 0.1,
                                         "min_retire": 5, "freshness": "1 d"}})
        defn = _entry(model)["definition"]
        assert defn["edit_policy"] == "brain_owned" and defn["deletion_guard"] == {"max_retire_share": 0.1, "min_retire": 5}
        _bind(model, "key", "f.path", [{"step": "trim"}])
        editing.apply(model, {"op": "mapping.expression", "id": "ablage-mandate", "emit": 0, "target": "key",
                              "expression": "join('/', [f.path, f.name])"})
        assert "0:key" not in _entry(model)["builder"]["steps"]
        with pytest.raises(editing.EditError, match="nicht lesbar"):
            editing.apply(model, {"op": "mapping.expression", "id": "ablage-mandate", "emit": 0, "target": "key",
                                  "expression": "f.("})
        for bad in ({"edit_policy": "editable"}, {"max_retire_share": 2}, {"priority": 101}):
            with pytest.raises(editing.EditError):
                editing.apply(model, {"op": "mapping.settings", "id": "ablage-mandate", "fields": bad})

    def test_a_second_type_and_the_identify_rule(self, model):
        editing.apply(model, {"op": "mapping.emit.add", "id": "ablage-mandate", "type": "Deadline"})
        editing.apply(model, {"op": "mapping.identify", "id": "ablage-mandate", "emit": 1, "attributes": ["key", "due_on"]})
        assert _entry(model)["definition"]["emits"][1]["identify"] == ["key", "due_on"]
        editing.apply(model, {"op": "mapping.emit.remove", "id": "ablage-mandate", "emit": 1})
        with pytest.raises(editing.EditError, match="mindestens einen"):
            editing.apply(model, {"op": "mapping.emit.remove", "id": "ablage-mandate", "emit": 0})

    @pytest.mark.parametrize("op", [
        {"id": "x", "source": "ablage", "collection": "*", "type": "Mandate"},
        {"id": "ablage-mandate", "source": "ablage", "collection": "*", "type": "Mandate"},
        {"id": "neu", "source": "nirgends", "collection": "*", "type": "Mandate"},
        {"id": "neu", "source": "ablage", "collection": "*", "type": "Unbekannt"},
    ])
    def test_refused(self, model, op):
        with pytest.raises(editing.EditError):
            editing.apply(model, {"op": "mapping.create", **op})

    def test_the_package_writes_brain_runner_s_mapping_without_the_builder_s_notes(self, model):
        _bind(model, "key", "f.path")
        files = package.write_package(model)
        written = package.load_yaml(files["mappings/ablage-mandate.yaml"])
        assert written["emits"][0]["key"] == "f.path" and "builder" not in written
        again = package.read_package(files).model
        assert next(m for m in again["mappings"] if m["definition"]["id"] == "ablage-mandate")["definition"] == written

    def test_the_review_names_the_mapping(self, model):
        before = _entry(model)
        _bind(model, "key", "f.path")
        group = review.element_diff("mappings/ablage-mandate", before, _entry(model))
        assert group["kind"] == "Zuordnung" and group["state"] == "changed"


class TestTheRun:
    def test_a_connector_s_files_through_brain_runner_s_emit(self, model, share):
        from builder import sources

        _bind(model, "key", "f.path")
        _bind(model, "name", "f.name")
        _bind(model, "fact:status", "f.suffix", [{"step": "replace", "old": ".", "new": ""}])
        reader = sources.KnovasConnectorReader(FakeConnector(share), "Mandate")
        defn = _entry(model)["definition"]
        assert [i["f"]["name"] for i in M.items(defn, reader)] == ["Honorar.docx", "Vollmacht.pdf", "Klage.pdf"]
        p = M.preview(M.run(defn, reader))
        assert p["items"] == 3 and p["nodes"][0] == {"type": "Mandate", "key": "Mandate/Muster AG/Honorar.docx",
                                                     "name": "Honorar.docx", "facts": {"status": "docx"}}

    def test_brain_runner_s_plan_against_the_tenant(self, model, share):
        from builder import sources

        _bind(model, "key", "f.path")
        _bind(model, "name", "f.name")
        run = M.run(_entry(model)["definition"], sources.KnovasConnectorReader(FakeConnector(share), "Mandate"))

        class Api:
            def get(self, path):
                if path == "secured/graph/node-types":
                    return {"node_types": [{"name": "Mandate", "id": "t-m"}]}
                if path.endswith("/schema"):
                    return {"attributes": [{"name": "key", "id": "a-key"}, {"name": "status", "id": "a-status"}]}
                if path == "secured/graph/nodes":
                    return {"nodes": [{"id": "n1", "node_type_id": "t-m", "name": "Alt"}]}
                if path.endswith("/facts"):
                    return {"facts": [{"attribute_id": "a-key", "value": "Mandate/Alt/Brief.pdf", "valid_from": "2026-01-01"}]}
                return {"edges": []}

        engine = publisher.BrainRunnerEngine(lambda: Api())
        summary = M.plan_summary(engine.mapping_plan(model, "ablage-mandate", run), "ablage-mandate")
        assert summary["added"] == 3 and summary["items"] == 3
        assert summary["guard"]["tripped"] is False and summary["guard"]["max_share"] == 0.2
        # With the mapping, the workspace is a configuration brain-runner loads, as B5's compile does first.
        # (The sample package's own mapping predates brain-runner's schema; it is left out here.)
        import tempfile

        from brain_runner import config

        with tempfile.TemporaryDirectory() as tmp:
            _, manifest = engine._write({**model, "mappings": [_entry(model)]}, Path(tmp))
            assert "ablage-mandate" in config.load(manifest).mappings


@pytest.fixture
def world(share):
    author, reviewer = StandInUser({"admin"}, "Alex"), StandInUser({"admin"}, "Robin")
    store = MemoryWorkspaceStore({str(author.id): "Alex", str(reviewer.id): "Robin"})
    engine = FakeEngine()
    app = make_app(author, store, tenant=TENANT, engine=engine, secrets=MemorySecrets(),
                   readers=stand_in_readers(connector=FakeConnector(share)))
    client = app.test_client()
    data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
    client.post("/builder/api/import", data={"package": (io.BytesIO(data), "p.zip"), "name": "Beispiel AG"},
                content_type="multipart/form-data")
    [workspace] = store.list()
    component = store.register_component(TENANT, kind="knovas_connector", name="Ablage", site="", owner="",
                                         settings={}, actor_id=author.id)
    edit = f"/builder/api/w/{workspace.id}/ontology?view=sources"
    client.post(edit, json={"op": "source.create", "kind": "knovas_connector", "name": "Ablage", "component": component,
                            "settings": {"folder": "Mandate"}})
    client.post(edit, json={"op": "mapping.create", "id": "ablage-mandate", "source": "ablage", "collection": "*",
                            "type": "Mandate"})
    return {"client": client, "store": store, "engine": engine, "id": workspace.id, "app": app}


class TestTheRoutes:
    def _bind(self, w, target, field):
        return w["client"].post(f"/builder/api/w/{w['id']}/ontology?view=mapping&mapping=ablage-mandate",
                                json={"op": "mapping.bind", "id": "ablage-mandate", "emit": 0, "target": target,
                                      "field": field})

    def test_the_page_the_edits_and_the_preview(self, world):
        page = world["client"].get("/builder/zuordnung/ablage-mandate")
        assert page.status_code == 200 and b"b-map-data" in page.data
        assert world["client"].get("/builder/zuordnung/gibt-es-nicht").status_code == 404
        view = self._bind(world, "key", "f.path").get_json()["mapping"]
        assert view["emits"][0]["targets"][0] == {**view["emits"][0]["targets"][0], "field": "f.path", "expression": "f.path"}
        first = world["client"].post(f"/builder/api/w/{world['id']}/mappings/ablage-mandate/preview").get_json()
        assert first["preview"] is None and "f.name" in [f["name"] for f in first["fields"]["fields"]]
        self._bind(world, "name", "f.name")
        done = world["client"].post(f"/builder/api/w/{world['id']}/mappings/ablage-mandate/preview").get_json()
        assert done["preview"]["candidates"] == 3

    def test_the_dry_run_needs_the_tenant_and_a_clean_mapping(self, world):
        url = f"/builder/api/w/{world['id']}/mappings/ablage-mandate/dry-run"
        assert world["client"].post(url).status_code == 409          # not linked to the tenant yet
        world["store"].link_tenant(world["id"], TENANT)
        assert world["client"].post(url).status_code == 409          # key and name still empty
        self._bind(world, "key", "f.path")
        self._bind(world, "name", "f.name")
        world["engine"].nodes = {"Mandate": {f"gone-{n}": {} for n in range(5)}}
        answer = world["client"].post(url).get_json()
        assert answer["summary"]["added"] == 3 and answer["summary"]["retired"] == 5
        assert answer["summary"]["guard"]["tripped"] is True and answer["status"] == "refused"
        run = world["store"].runs(world["id"])[0]
        assert run["summary"]["mapping"] == "ablage-mandate"
        # A mapping's dry run is not the draft's: the review still asks for its own.
        assert "Probelauf starten" in world["client"].get("/builder/pruefen").get_data(as_text=True)

    def test_the_sources_page_lists_the_mappings(self, world):
        page = world["client"].get("/builder/quellen").get_data(as_text=True)
        assert "ablage-mandate" in page and "/builder/zuordnung/ablage-mandate" in page

    def test_only_a_builder_role(self, world):
        world["app"].extensions["builder_gate"].user = StandInUser({"member"})
        assert world["client"].get("/builder/zuordnung/ablage-mandate").status_code == 403
        for action in ("preview", "dry-run"):
            assert world["client"].post(f"/builder/api/w/{world['id']}/mappings/ablage-mandate/{action}").status_code == 403


CLIENT_ZERO = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO")


@pytest.mark.skipif(not CLIENT_ZERO, reason="needs $KNOWLEDGEBASE_CLIENT_ZERO (KnowledgeBase's client-zero folder)")
def test_client_zero_s_23_mappings_pass_the_checks():
    cz = package.read_package(package.files_from_directory(Path(CLIENT_ZERO))).model
    assert len(cz["mappings"]) == 23
    assert {m["definition"]["id"]: M.problems(cz, m) for m in cz["mappings"] if M.problems(cz, m)} == {}
