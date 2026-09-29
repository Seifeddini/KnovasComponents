"""Values added to selection fields: a new segment ("Notariat") for the
sales types, without a new type version.

Reported as "I can't add new segments": the Segment field of the sales pack
is a fixed list in the versioned type definition, so a new value reached
neither existing experiments nor anyone without the manager role.
"""

from __future__ import annotations

import pytest

from conftest import platform_db_reachable
from experiments import packs, schema, store
from experiments.errors import Conflict, Forbidden, NotFound, ValidationError
from test_experiments_frontend import _run_page, needs_node

db = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")


# -- schema ------------------------------------------------------------------------------


def _sales_definition(**segment):
    definition = dict(packs.load_pack("sales")["types"][0]["definition"])
    definition["fields"] = [dict(f, **segment) if f["key"] == "segment" else dict(f)
                            for f in definition["fields"]]
    return schema.validate_type_definition(definition)


def test_selection_fields_take_added_values_unless_locked():
    open_ = _sales_definition()
    assert schema.is_extensible(next(f for f in open_["fields"] if f["key"] == "segment"))
    values = schema.validate_field_values(open_, {"segment": "notariat"},
                                          extra_options={"segment": ["Notariat"]})
    assert values == {"segment": "Notariat"}
    with pytest.raises(ValidationError):
        schema.validate_field_values(open_, {"segment": "Notariat"})
    locked = _sales_definition(extensible=False)
    with pytest.raises(ValidationError):
        schema.validate_field_values(locked, {"segment": "Notariat"},
                                     extra_options={"segment": ["Notariat"]})


def test_extensible_is_only_for_selection_fields():
    definition = dict(packs.load_pack("sales")["types"][0]["definition"])
    definition["fields"] = [dict(f, extensible=True) if f["key"] == "sequence" else dict(f)
                            for f in definition["fields"]]
    with pytest.raises(ValidationError) as caught:
        schema.validate_type_definition(definition)
    assert any("extensible" in k for k in caught.value.fields)


def test_effective_options_keep_one_spelling():
    field = {"type": "enum", "options": ["Kanzlei klein", "Kanzlei mittel"]}
    assert schema.effective_options(field, ["kanzlei MITTEL", "Notariat", "notariat"]) == [
        "Kanzlei klein", "Kanzlei mittel", "Notariat"]
    assert schema.effective_options(dict(field, extensible=False), ["Notariat"]) == [
        "Kanzlei klein", "Kanzlei mittel"]


# -- service ---------------------------------------------------------------------------------


@pytest.fixture
def w(platform_db, identity_repo):
    from test_experiments_service import World

    world = World(platform_db, identity_repo)
    world.manager.install_pack("sales")
    return world


def _sales_experiment(w, **fields):
    return w.experimenter.create_experiment({
        "domain": "sales", "type": "playbook", "title": "Video-Demo",
        "hypothesis": "Video-Demos bringen mehr Termine.", "fields": fields})


@db
def test_an_experimenter_adds_a_segment_that_every_experiment_offers(w):
    before = _sales_experiment(w)                      # created before the value exists
    added = w.experimenter.add_field_option("sales", {"field": "segment", "value": "  Notariat "})
    assert added == {"field": "segment", "value": "Notariat", "created": True}
    # The existing experiment, on its old type version, takes it ...
    updated = w.experimenter.update_experiment(
        before["key"], {"row_version": before["row_version"], "fields": {"segment": "notariat"}})
    assert updated["field_values"]["segment"] == "Notariat"
    # ... a new one too, and the snapshot names it for the form.
    fresh = _sales_experiment(w, segment="Notariat")
    assert fresh["field_values"]["segment"] == "Notariat"
    assert w.experimenter.get_experiment(fresh["key"])["field_options"] == {"segment": ["Notariat"]}
    assert w.audit("experiments.field_option.create")[0]["detail"] == {"field": "segment",
                                                                       "value": "Notariat"}


@db
def test_a_second_spelling_is_the_same_value(w):
    own = w.experimenter.add_field_option("sales", {"field": "segment", "value": "kanzlei MITTEL"})
    assert own == {"field": "segment", "value": "Kanzlei mittel", "created": False}
    w.experimenter.add_field_option("sales", {"field": "segment", "value": "Notariat"})
    again = w.experimenter.add_field_option("sales", {"field": "segment", "value": "NOTARIAT"})
    assert again == {"field": "segment", "value": "Notariat", "created": False}
    assert w.experimenter.list_field_options("sales")["by_field"] == {"segment": ["Notariat"]}


@db
def test_refusals(w):
    with pytest.raises(ValidationError) as caught:
        w.experimenter.add_field_option("sales", {"field": "sequence", "value": "x"})
    assert "field" in caught.value.fields                  # a text field, not a selection
    with pytest.raises(ValidationError):
        w.experimenter.add_field_option("sales", {"field": "segment", "value": "   "})
    with pytest.raises(ValidationError):
        w.experimenter.add_field_option("sales", {"field": "segment", "value": "x" * 81})
    with pytest.raises(NotFound):
        w.svc(w.mia).add_field_option("sales", {"field": "segment", "value": "Notariat"})
    with pytest.raises(NotFound):
        w.experimenter.add_field_option("gibtsnicht", {"field": "segment", "value": "Notariat"})


@db
def test_a_locked_field_takes_no_values(w):
    definition = _sales_definition(extensible=False)
    w.manager.create_type({"domain": "sales", "key": "fixed", "name": "Fest",
                           "definition": definition})
    # The playbook type still lets people add segments: one extensible type is enough.
    assert w.experimenter.add_field_option("sales", {"field": "segment", "value": "Notariat"})["created"]
    playbook = store.find_type(w.conn, store.get_domain(w.conn, "sales")["id"], "playbook")
    store.add_type_version(w.conn, playbook["id"], _sales_definition(extensible=False), None)
    pricing = store.find_type(w.conn, store.get_domain(w.conn, "sales")["id"], "pricing")
    locked_pricing = dict(pricing["definition"])
    locked_pricing["fields"] = [dict(f, extensible=False) if f["key"] == "segment" else f
                                for f in locked_pricing["fields"]]
    store.add_type_version(w.conn, pricing["id"], schema.validate_type_definition(locked_pricing), None)
    with pytest.raises(ValidationError) as caught:
        w.experimenter.add_field_option("sales", {"field": "segment", "value": "Beh\u00f6rde"})
    assert "fest vorgegeben" in caught.value.message


@db
def test_only_managers_remove_and_only_unused_values(w):
    w.experimenter.add_field_option("sales", {"field": "segment", "value": "Notariat"})
    w.experimenter.add_field_option("sales", {"field": "segment", "value": "Notarait"})   # a typo
    _sales_experiment(w, segment="Notariat")
    listed = {o["value"]: o for o in w.manager.list_field_options("sales")["options"]}
    assert listed["Notariat"]["used"] == 1 and listed["Notarait"]["used"] == 0
    assert listed["Notariat"]["field_label"] == "Segment"
    with pytest.raises(Forbidden):
        w.experimenter.delete_field_option("sales", listed["Notarait"]["id"])
    with pytest.raises(Conflict) as caught:
        w.manager.delete_field_option("sales", listed["Notariat"]["id"])
    assert "1 Experiment verwendet" in caught.value.message
    assert w.manager.delete_field_option("sales", listed["Notarait"]["id"]) == {"deleted": True}
    assert w.manager.list_field_options("sales")["by_field"] == {"segment": ["Notariat"]}
    with pytest.raises(NotFound):
        w.manager.delete_field_option("marketing", listed["Notariat"]["id"])   # other domain


@db
def test_multi_select_values_count_as_used(w):
    definition = _sales_definition()
    definition["fields"].append({"key": "channels", "label": "Kan\u00e4le", "type": "multi_enum",
                                 "options": ["Telefon", "E-Mail"], "required": False, "help": ""})
    w.manager.create_type({"domain": "sales", "key": "multi", "name": "Mehrfach",
                           "definition": definition})
    w.experimenter.add_field_option("sales", {"field": "channels", "value": "LinkedIn"})
    created = w.experimenter.create_experiment({
        "domain": "sales", "type": "multi", "title": "Kan\u00e4le",
        "fields": {"channels": ["linkedin", "Telefon"]}})
    assert created["field_values"]["channels"] == ["Telefon", "LinkedIn"]
    used = {o["value"]: o["used"] for o in w.manager.list_field_options("sales")["options"]}
    assert used == {"LinkedIn": 1}


@db
def test_export_and_import_carry_the_added_values(w, monkeypatch):
    w.experimenter.add_field_option("sales", {"field": "segment", "value": "Notariat"})
    text = w.manager.export_domain("sales")
    assert "field_options" in text and "Notariat" in text
    copy = text.replace("key: sales", "key: sales-ch").replace("id_prefix: SAL", "id_prefix: SCH")
    copy = copy.replace("pack: sales", "pack: sales-ch")
    w.manager.import_pack({"text": copy})
    assert w.manager.list_field_options("sales-ch")["by_field"] == {"segment": ["Notariat"]}


@db
def test_the_number_of_added_values_is_bounded(w, monkeypatch):
    monkeypatch.setattr(store, "MAX_FIELD_OPTIONS", 2)
    for value in ("Notariat", "Beh\u00f6rde"):
        w.experimenter.add_field_option("sales", {"field": "segment", "value": value})
    with pytest.raises(Conflict):
        w.experimenter.add_field_option("sales", {"field": "segment", "value": "Verband"})


# -- routes -------------------------------------------------------------------------------------


@db
def test_routes(exp_manager_client, experimenter_client, exp_member_client):
    assert exp_manager_client.post("/api/experiments/packs/sales/install").status_code == 200
    url = "/api/experiments/domains/sales/field-options"
    added = experimenter_client.post(url, json={"field": "segment", "value": "Notariat"})
    assert added.status_code == 200 and added.get_json()["result"]["created"] is True
    listed = experimenter_client.get(url).get_json()["result"]
    assert listed["by_field"] == {"segment": ["Notariat"]}
    assert exp_member_client.get(url).status_code == 404
    option_id = listed["options"][0]["id"]
    assert experimenter_client.delete(f"{url}/{option_id}").status_code == 403
    assert exp_manager_client.delete(f"{url}/{option_id}").status_code == 200


# -- the form -----------------------------------------------------------------------------------


def _form(body):
    return _run_page(None, r"""
    const def = { key: 'segment', label: 'Segment', type: 'enum', options: ['Kanzlei klein', 'Kanzlei mittel'] };
    const labels = (row) => row.querySelectorAll('option').map((o) => o.textContent);
    """ + body)


@needs_node
class TestForm:
    def test_added_values_and_the_add_entry_are_offered(self):
        result = _form(r"""
        const row = KX.renderFieldInput(def, 'Notariat', { extra: ['Notariat', 'kanzlei MITTEL'], onAdd: async () => null });
        const locked = KX.renderFieldInput(Object.assign({}, def, { extensible: false }), null, { extra: ['Notariat'], onAdd: async () => null });
        const without = KX.renderFieldInput(def, null, { extra: ['Notariat'] });
        out({ open: labels(row), value: KX.readFieldInput(def, row), locked: labels(locked), without: labels(without) });
        """)
        assert result["open"][1:] == ["Kanzlei klein", "Kanzlei mittel", "Notariat", "+ Neuer Wert \u2026"]
        assert result["value"] == "Notariat"
        assert result["locked"][1:] == ["Kanzlei klein", "Kanzlei mittel"]
        assert result["without"][1:] == ["Kanzlei klein", "Kanzlei mittel", "Notariat"]

    def test_choosing_the_add_entry_adds_and_selects_the_value(self):
        result = _form(r"""
        let asked = 0;
        const row = KX.renderFieldInput(def, 'Kanzlei klein', { onAdd: async () => { asked += 1; return asked === 1 ? 'Notariat' : null; } });
        const select = row.querySelector('select');
        const pick = async (value) => { select.value = value; await select.dispatch('change'); await tick(); };
        await pick('__kx_new_option__');
        const first = { value: KX.readFieldInput(def, row), labels: labels(row) };
        await pick('__kx_new_option__');          // cancelled: the choice stays
        out({ first, second: KX.readFieldInput(def, row), asked });
        """)
        assert result["first"]["value"] == "Notariat"
        assert result["first"]["labels"][-2:] == ["Notariat", "+ Neuer Wert \u2026"]
        assert result["second"] == "Notariat" and result["asked"] == 2
