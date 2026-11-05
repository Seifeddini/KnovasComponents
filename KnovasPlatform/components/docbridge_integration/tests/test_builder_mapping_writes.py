"""The mapping panels «Zugriff» and «Zurückschreiben» (connection layer MVP-26, KC-7b).

``access``: {fixed: all} | {fixed: [group keys]} | {inherit: folder_rule}; none is preselected, «aus der Quelle» waits
for the permissions from the source. ``writes``: {<Type>: {<fact>: {field: <plain path>, allowed?: [...]}}}, only for
a fact whose expression is a plain path into the item (brain_runner.inverse.invertible when installed, else the same
rule here), never for a computed attribute, and only while the source's write switch is on. An edit that makes a
written fact no longer invertible takes it out of ``writes`` in the same change and says so.
"""
import datetime as dt

import pytest

from builder import editing, mappings as M, package
from builder_harness import (SAMPLE_PACKAGE, FakeEngine, MemorySecrets, MemoryWorkspaceStore, ProtocolClient, StandInUser,
                             make_app)

TENANT = "t-writes"
MAPPING = "merkblaetter"

FACT_SHEET = {"key": "Merkblatt", "level": "company", "label": {"de": "Merkblatt"}, "attributes": [
    {"name": "satz", "datatype": "text", "level": "company"},
    {"name": "pruefdatum", "datatype": "date", "level": "company"},
    {"name": "titel", "datatype": "text", "level": "company"},
    {"name": "ablage", "datatype": "text", "level": "company"},
    {"name": "status", "datatype": "enum", "enum_values": ["belegt", "veraltet"], "level": "company",
     "note": "veraltet, wenn die Belege älter sind als die letzte Änderung.", "extra": {"computed": True}},
    {"name": "stufe", "datatype": "text", "level": "company", "extra": {"computed": True}},
    # Only the explicit mark counts: a description that says «Computed» marks nothing.
    {"name": "hinweis", "datatype": "text", "level": "company", "note": "Computed by hand once a year."},
]}


def _model(source_component="c1"):
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    model["ontology"]["types"].append(dict(FACT_SHEET))
    model["access"] = {"groups": [{"key": "partner", "name": "Partner"}, {"key": "sekretariat", "name": "Sekretariat"}],
                       "rules": []}
    ops = [{"op": "source.create", "kind": "github", "name": "Akten", "component": source_component,
            "settings": {"repository": "muster-partner/akten", "ref": "main"}},
           {"op": "mapping.create", "id": MAPPING, "source": "akten", "collection": "markdown_front_matter:merkblaetter/*.md",
            "type": "Merkblatt"}]
    for target, field, steps in (("key", "front_matter.id", []), ("name", "title", []),
                                 ("fact:satz", "front_matter.summary", []), ("fact:pruefdatum", "front_matter.geprueft_am", []),
                                 ("fact:titel", "title", [{"step": "trim"}]), ("fact:ablage", "doc.path", []),
                                 ("fact:status", "front_matter.status", []), ("fact:stufe", "front_matter.stufe", []),
                                 ("fact:hinweis", "front_matter.hinweis", [])):
        ops.append({"op": "mapping.bind", "id": MAPPING, "emit": 0, "target": target, "field": field, "steps": steps})
    for op in ops:
        editing.apply(model, op)
    return model


def _definition(model):
    return next(m for m in model["mappings"] if m["definition"]["id"] == MAPPING)["definition"]


class TestInvertible:
    def test_a_plain_path_into_the_item_is_writable_and_nothing_else(self):
        found = M.invertible(_definition(_model()), _model())
        facts = found["Merkblatt"]
        assert facts["satz"] == (True, None) and facts["pruefdatum"] == (True, None)
        assert facts["titel"] == (False, "not_invertible")        # built with a step
        assert facts["ablage"] == (False, "not_invertible")       # the document's path, not a field of the item
        # Computed attributes (computed: true) are never listed, plain path or not; a description marks nothing.
        assert "status" not in facts and "stufe" not in facts
        assert facts["hinweis"] == (True, None)

    def test_only_the_explicit_mark_makes_an_attribute_computed(self):
        assert M.is_computed({"name": "a", "extra": {"computed": True}})
        assert not M.is_computed({"name": "a", "note": "Computed: from the evidence."})
        assert not M.is_computed({"name": "a", "description": {"de": "Berechnet aus den Belegen."}})
        assert not M.is_computed({"name": "a", "extra": {"computed": "yes"}})

    def test_the_package_s_computed_true_reaches_the_model(self):
        files = package.files_from_directory(SAMPLE_PACKAGE)
        model = package.read_package(files).model
        typed = next(t for t in model["ontology"]["types"] if t.get("attributes"))
        typed["attributes"][0]["extra"] = {"computed": True}
        again = package.read_package(package.write_package(model)).model
        same = next(t for t in again["ontology"]["types"] if t["key"] == typed["key"])
        assert M.is_computed(same["attributes"][0])

    def test_the_field_is_the_plain_path_without_the_item(self):
        assert M.write_field(_definition(_model()), "Merkblatt", "satz") == "front_matter.summary"
        assert M.write_field(_definition(_model()), "Merkblatt", "titel") is None

    def test_brain_runner_s_rule_is_used_when_installed(self, monkeypatch):
        import sys
        import types

        fake = types.ModuleType("brain_runner.inverse")
        fake.invertible = lambda mapping: {"Merkblatt": {"satz": (False, "not_invertible"), "status": (True, None)}}
        monkeypatch.setitem(sys.modules, "brain_runner.inverse", fake)
        found = M.invertible(_definition(_model()), _model())
        assert found == {"Merkblatt": {"satz": (False, "not_invertible")}}   # still: computed never listed


@pytest.fixture
def world():
    admin = StandInUser({"admin"}, "Ada Admin")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), component_secrets=MemorySecrets(),
                   secrets=MemorySecrets())
    http = app.test_client()
    import io
    import zipfile

    made = http.post("/builder/api/sites", json={"name": "Kanzlei Chur", "options": ["github"]}).get_json()
    env = zipfile.ZipFile(io.BytesIO(http.post(f"/builder/api/sites/{made['id']}/package", json={}).data)) \
        .read("knovas-link.env").decode()
    link = ProtocolClient(http)
    link.enrol(next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("LINK_ENROL_TOKEN=")))
    link.heartbeat(site={"certificate": "present", "probe": {"last_at": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": True},
                         "components": [{"name": "sync-component", "kind": "sync_component", "version": "0.2.0",
                                         "state": "online", "capabilities": ["write:github", "fetch:github"]}]})
    [child] = store.children(link.component)
    workspace = store.create(name="Muster & Partner", model=_model(child["id"]), actor_id=admin.id,
                             source={"kind": "blank"}, tenant=TENANT)
    http.get(f"/builder/w/{workspace}")
    return {"http": http, "store": store, "workspace": workspace, "gate": app.extensions["builder_gate"]}


def _op(world, **op):
    return world["http"].post(f"/builder/api/w/{world['workspace']}/ontology?view=mapping&mapping={MAPPING}", json=op)


def _stored(world):
    return _definition(world["store"].model(world["workspace"]))


class TestAccess:
    def test_no_choice_is_preselected(self, world):
        assert "access" not in _stored(world)
        page = world["http"].get(f"/builder/zuordnung/{MAPPING}").get_data(as_text=True)
        panel = page.split("data-map-access", 1)[1].split("</fieldset>", 1)[0]
        assert "checked" not in panel
        assert "Wie die Ordnerregel" in panel and "Alle im Mandanten" in panel
        assert "überschreibt die Ordnerregeln" in panel and "Probelauf hält an, wo eine Ordnerregel gilt" in panel
        assert "aus der Quelle" in panel and "kommt mit der Berechtigungsübernahme" in panel
        assert 'value="from_source" disabled' in panel
        assert "Partner" in panel and "Sekretariat" in panel

    @pytest.mark.parametrize("access", [{"fixed": "all"}, {"inherit": "folder_rule"}, {"fixed": ["partner"]}])
    def test_a_choice_is_kept_in_the_definition(self, world, access):
        answer = _op(world, op="mapping.access", id=MAPPING, access=access)
        assert answer.status_code == 200, answer.get_json()
        assert _stored(world)["access"] == access
        page = world["http"].get(f"/builder/zuordnung/{MAPPING}").get_data(as_text=True)
        assert "checked" in page.split("data-map-access", 1)[1].split("</fieldset>", 1)[0]

    @pytest.mark.parametrize("access", [{"from_source": True}, {"fixed": []}, {"fixed": ["unbekannt"]}, {"fixed": "alle"},
                                        {"inherit": "ordner"}, {"fixed": "all", "inherit": "folder_rule"}, "all"])
    def test_what_is_not_offered_is_refused(self, world, access):
        assert _op(world, op="mapping.access", id=MAPPING, access=access).status_code == 400
        assert "access" not in _stored(world)

    def test_the_choice_can_be_taken_back(self, world):
        _op(world, op="mapping.access", id=MAPPING, access={"fixed": "all"})
        assert _op(world, op="mapping.access", id=MAPPING, access=None).status_code == 200
        assert "access" not in _stored(world)


class TestWrites:
    def _write_on(self, world):
        answer = world["http"].post(f"/builder/api/w/{world['workspace']}/ontology",
                                    json={"op": "source.switches", "key": "akten", "access": "read_write"})
        assert answer.status_code == 200, answer.get_json()

    def test_refused_while_the_source_s_write_switch_is_off(self, world):
        refused = _op(world, op="mapping.writes", id=MAPPING, type="Merkblatt", fact="satz", on=True)
        assert refused.status_code == 409 and "zurückgeschrieben" in refused.get_json()["error"]
        page = world["http"].get(f"/builder/zuordnung/{MAPPING}").get_data(as_text=True)
        assert "data-map-writes" in page and 'data-map-writes disabled' in page

    def test_a_plain_field_is_written_with_its_path(self, world):
        self._write_on(world)
        assert _op(world, op="mapping.writes", id=MAPPING, type="Merkblatt", fact="satz", on=True).status_code == 200
        assert _op(world, op="mapping.writes", id=MAPPING, type="Merkblatt", fact="pruefdatum", on=True,
                   allowed=["2026-11-05"]).status_code == 200
        assert _stored(world)["writes"] == {"Merkblatt": {"satz": {"field": "front_matter.summary"},
                                                          "pruefdatum": {"field": "front_matter.geprueft_am",
                                                                         "allowed": ["2026-11-05"]}}}
        assert _op(world, op="mapping.writes", id=MAPPING, type="Merkblatt", fact="pruefdatum", on=False).status_code == 200
        assert _stored(world)["writes"] == {"Merkblatt": {"satz": {"field": "front_matter.summary"}}}

    @pytest.mark.parametrize("fact,code", [("titel", "Nicht umkehrbar"), ("ablage", "Nicht umkehrbar"),
                                           ("status", "Berechnetes Attribut"), ("stufe", "Berechnetes Attribut"),
                                           ("gibt_es_nicht", "Nicht beschreibbar")])
    def test_what_cannot_be_inverted_is_refused_with_the_reason(self, world, fact, code):
        self._write_on(world)
        refused = _op(world, op="mapping.writes", id=MAPPING, type="Merkblatt", fact=fact, on=True)
        assert refused.status_code == 400 and code in refused.get_json()["error"]
        assert "writes" not in _stored(world)

    def test_an_edit_that_breaks_invertibility_removes_the_fact_and_says_so(self, world):
        self._write_on(world)
        _op(world, op="mapping.writes", id=MAPPING, type="Merkblatt", fact="satz", on=True)
        answer = _op(world, op="mapping.bind", id=MAPPING, emit=0, target="fact:satz", field="front_matter.summary",
                     steps=[{"step": "trim"}])
        assert answer.status_code == 200
        assert "writes" not in _stored(world)
        [notice] = answer.get_json()["notices"]
        assert "satz" in notice and "Zurückschreiben" in notice
        # One change: the binding and the removal together, so undo takes both back.
        world["http"].post(f"/builder/api/w/{world['workspace']}/undo")
        assert _stored(world)["writes"] == {"Merkblatt": {"satz": {"field": "front_matter.summary"}}}

    def test_a_new_plain_path_moves_the_field_with_it(self, world):
        self._write_on(world)
        _op(world, op="mapping.writes", id=MAPPING, type="Merkblatt", fact="satz", on=True)
        answer = _op(world, op="mapping.bind", id=MAPPING, emit=0, target="fact:satz", field="front_matter.kurzfassung")
        assert _stored(world)["writes"]["Merkblatt"]["satz"] == {"field": "front_matter.kurzfassung"}
        assert answer.get_json()["notices"]

    def test_the_panel_lists_what_may_be_written(self, world):
        self._write_on(world)
        page = world["http"].get(f"/builder/zuordnung/{MAPPING}").get_data(as_text=True)
        panel = page.split("data-map-writes", 1)[1].split("</fieldset>", 1)[0]
        assert 'data-write-fact="satz"' in panel and 'data-write-fact="titel"' in panel
        assert "Nicht umkehrbar" in panel
        assert 'data-write-fact="status"' not in panel and 'data-write-fact="stufe"' not in panel

    def test_editing_needs_edit(self, world):
        world["gate"].user = StandInUser({"builder_viewer"})
        assert _op(world, op="mapping.access", id=MAPPING, access={"fixed": "all"}).status_code == 403


def test_what_this_mapping_does_in_one_sentence(world):
    _op(world, op="mapping.access", id=MAPPING, access={"fixed": "all"})
    text = M.describe(world["store"].model(world["workspace"]), next(
        m for m in world["store"].model(world["workspace"])["mappings"] if m["definition"]["id"] == MAPPING))
    assert text.startswith("Liest merkblaetter/*.md aus der Quelle «Akten»")
    assert "Merkblatt" in text and "alle im Mandanten" in text and "Zurückgeschrieben wird nichts" in text
    page = world["http"].get(f"/builder/zuordnung/{MAPPING}").get_data(as_text=True)
    assert "Was diese Zuordnung tut" in page and text in page.replace("&#34;", '"')
