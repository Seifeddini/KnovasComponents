"""The ontology as one tree, with its problems (build plan step B2).

On the fictitious sample package, whose awkward cases are on purpose, and on
Client Zero when KNOWLEDGEBASE_CLIENT_ZERO is set: every one of its types in
the tree under the Brain's core, every relation between known types.
"""
import os
from pathlib import Path

import pytest

from builder import ontology_view, package, packs
from builder_harness import SAMPLE_PACKAGE

CLIENT_ZERO = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO", "")


@pytest.fixture(scope="module")
def view():
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    return ontology_view.build(model)


def _node(view, name):
    return next(n for n in view["nodes"] if n["name"] == name and n["source"] == "model")


def _chain(view, node):
    by_id = {n["id"]: n for n in view["nodes"]}
    names = []
    while node:
        names.append(node["name"])
        node = by_id.get(node["parent"])
    return names


class TestTheTree:
    def test_every_type_sits_under_the_core(self, view):
        roots = [n for n in view["nodes"] if n["depth"] == 0]
        assert [r["id"] for r in roots] == ["euo:Entity"]
        assert _chain(view, _node(view, "Deadline")) == ["Deadline", "Mandate", "Agreement", "DirectiveContent",
                                                         "InformationContentEntity", "Entity"]

    def test_a_core_type_in_the_model_is_that_core_class(self, view):
        organization = _node(view, "Organization")
        assert organization["id"] == "euo:Organization"
        assert organization["readonly"] is True
        assert _chain(view, organization)[:3] == ["Organization", "Party", "Agent"]
        assert sum(1 for n in view["nodes"] if n["id"] == "euo:Organization") == 1

    def test_classes_above_the_types_are_read_only_references(self, view):
        party = next(n for n in view["nodes"] if n["id"] == "euo:Party")
        assert party["source"] == "core" and party["readonly"] and party["label"]["de"]

    def test_the_tree_order_is_depth_first(self, view):
        depths = [n["depth"] for n in view["nodes"]]
        assert depths[0] == 0
        assert all(b <= a + 1 for a, b in zip(depths, depths[1:]))

    def test_inherited_attributes_come_from_the_types_above_and_the_common_ones(self, view):
        deadline = _node(view, "Deadline")
        inherited = {(a["name"], a["from"]) for a in deadline["inherited"]}
        assert ("status", "Mandate") in inherited
        assert ("key", "Alle Typen") in inherited
        assert [a["name"] for a in deadline["attributes"]] == ["due_on"]

    def test_a_parent_can_come_from_the_description_header(self):
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        deadline = next(t for t in model["ontology"]["types"] if t["key"] == "Deadline")
        del deadline["parent"]
        deadline["description"] = {"en": "[level=beispiel; parent=Organization] A dated duty."}
        view = ontology_view.build(model)
        assert _chain(view, _node(view, "Deadline"))[:2] == ["Deadline", "Organization"]

    def test_a_parent_nobody_knows_is_a_problem_not_a_crash(self):
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        next(t for t in model["ontology"]["types"] if t["key"] == "Deadline")["parent"] = "xyz:Nowhere"
        view = ontology_view.build(model)
        assert any(p["kind"] == "parent_unknown" and "xyz:Nowhere" in p["message"] for p in view["problems"])
        assert _node(view, "Deadline")["depth"] == 0

    def test_a_loop_of_parents_is_cut(self):
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        types = {t["key"]: t for t in model["ontology"]["types"]}
        types["Mandate"]["parent"] = "Deadline"
        view = ontology_view.build(model)
        assert any(p["kind"] == "parent_cycle" for p in view["problems"])
        assert len([n for n in view["nodes"] if n["source"] == "model"]) == 4


class TestRelationsAndProblems:
    def test_relations_run_from_domain_to_range(self, view):
        relations = {r["key"]: r for r in view["relations"]}
        assert relations["client_of"]["domain"] == ["euo:Organization"]
        assert relations["client_of"]["range"] == ["type:Mandate"]
        mandate = _node(view, "Mandate")
        assert "relation:client_of" in mandate["incoming"]

    def test_the_problems_name_what_to_fix(self, view):
        kinds = {(p["kind"], p["target"]) for p in view["problems"]}
        assert ("relation_no_bounds", "relation:relates_to") in kinds
        assert ("type_no_description", "type:Process") not in kinds  # it has one
        not_held = {p.get("attribute") for p in view["problems"] if p["kind"] == "attribute_not_held"}
        assert {"opened_on", "fee_note", "due_on"} <= not_held
        assert "status" not in not_held  # enum: the graph store holds it

    def test_a_type_without_description_is_flagged(self):
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        del next(t for t in model["ontology"]["types"] if t["key"] == "Deadline")["description"]
        view = ontology_view.build(model)
        assert any(p["kind"] == "type_no_description" and p["target"] == "type:Deadline" for p in view["problems"])

    def test_a_wildcard_is_any_type_not_an_unknown_one(self):
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        relation = next(r for r in model["ontology"]["relations"] if r["key"] == "relates_to")
        relation["domain"], relation["range"] = ["*"], ["*"]
        view = ontology_view.build(model)
        assert not [p for p in view["problems"] if p["target"] == "relation:relates_to"]

    def test_the_counts(self, view):
        assert view["counts"]["types"] == 4
        assert view["counts"]["relations"] == 3
        assert view["counts"]["problems"]["critical"] == 2  # relates_to: no domain, no range


class TestThePacks:
    def test_the_core_has_one_root_and_known_parents(self):
        core = next(p for p in packs.load() if p["pack"] == "core")
        ids = {t["id"] for t in core["types"]}
        assert [t["id"] for t in core["types"] if not t["parents"]] == ["euo:Entity"]
        assert all(set(t["parents"]) <= ids for t in core["types"])

    def test_every_pack_class_has_german_and_english_labels(self):
        for pack in packs.load():
            for item in pack["types"]:
                assert set(item["label"]) == {"de", "en"}, item["id"]


@pytest.mark.skipif(not CLIENT_ZERO, reason="KNOWLEDGEBASE_CLIENT_ZERO is not set")
def test_client_zero_puts_all_71_types_in_one_tree():
    model = package.read_package(package.files_from_directory(Path(CLIENT_ZERO))).model
    view = ontology_view.build(model)
    assert view["counts"]["types"] == 71
    assert [n["id"] for n in view["nodes"] if n["depth"] == 0] == ["euo:Entity"]
    assert [p for p in view["problems"] if p["severity"] == "critical"] == []
    drawable = [r for r in view["relations"] if r["domain"] and r["range"]]
    assert len(drawable) + sum(1 for r in view["relations"] if r.get("domain_any") or r.get("range_any")) == 68
