"""A company's processes (build plan step B6): the checks, the edits, the review and the routes.

The sample package's process predates the schema; each test here replaces it
with one built through the operations, so what is checked is what the canvas
makes. Client Zero's seven processes are checked when $KNOWLEDGEBASE_CLIENT_ZERO
names its folder; test_builder_screens.py runs the canvas in a browser.
"""
import io
import os
from pathlib import Path

import pytest

from builder import editing, package, processes, review
from builder_harness import SAMPLE_PACKAGE, MemoryWorkspaceStore, StandInUser, make_app

KEY = "PROC-mandat"


def _apply(model, **op):
    return editing.apply(model, op)


@pytest.fixture
def model():
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    _apply(model, op="process.remove", key="PROC-mandate")
    _apply(model, op="process.create", key=KEY, name="Mandat", instance_types=["Mandate"], owner_role="ROLE-PARTNER")
    for step in ("open", "active", "closed"):
        _apply(model, op="step.create", key=KEY, id=step)
    _apply(model, op="transition.add", key=KEY, **{"from": "open", "to": "active"})
    _apply(model, op="transition.add", key=KEY, **{"from": "active", "to": "closed"})
    _apply(model, op="step.update", key=KEY, step="closed", fields={"resting": True})
    for value in ("open", "active", "closed"):
        _apply(model, op="status.set", key=KEY, type="Mandate", value=value, step=value)
    return model


def _process(model, key=KEY):
    return next(p for p in model["processes"]["processes"] if p["key"] == key)


def _kinds(model):
    return sorted(f["kind"] for f in processes.validate(model))


class TestTheChecks:
    def test_a_process_built_on_the_canvas_is_clean(self, model):
        assert processes.validate(model) == []
        p = _process(model)
        assert p["transitions"] == [["open", "active"], ["active", "closed"]]
        assert p["final"] == ["closed"] and p["status_map"] == {"Mandate": {"open": "open", "active": "active", "closed": "closed"}}
        assert [s["order"] for s in p["steps"]] == [1, 2, 3]

    def test_a_step_no_transition_reaches(self, model):
        _apply(model, op="step.create", key=KEY, id="on-hold", after="active")
        found = processes.validate(model)
        assert {(f["kind"], f["step"]) for f in found} == {("step_unreachable", "on-hold"), ("step_dead_end", "on-hold")}
        assert next(f for f in found if f["kind"] == "step_unreachable")["severity"] == "critical"

    def test_a_status_value_two_processes_map(self, model):
        _apply(model, op="process.create", key="PROC-abschluss", name="Abschluss", instance_types=["Mandate"], owner_role="ROLE-PARTNER")
        for step in ("pruefen", "fertig"):
            _apply(model, op="step.create", key="PROC-abschluss", id=step)
        _apply(model, op="transition.add", key="PROC-abschluss", **{"from": "pruefen", "to": "fertig"})
        _apply(model, op="step.update", key="PROC-abschluss", step="fertig", fields={"resting": True})
        _apply(model, op="status.set", key="PROC-abschluss", type="Mandate", value="closed", step="fertig")
        twice = [f for f in processes.validate(model) if f["kind"] == "status_mapped_twice"]
        assert {f["process"] for f in twice} == {KEY, "PROC-abschluss"}
        assert "Mandate.status = closed" in twice[0]["message"]

    def test_a_path_through_a_relation_that_does_not_exist(self, model):
        _apply(model, op="artefact.set", key=KEY, step="active", kind="produces",
               artefact={"path": [{"relation": "billed_to", "direction": "out", "to_type": "Organization"}]})
        [found] = processes.validate(model)
        assert found["kind"] == "path_invalid" and found["step"] == "active"
        assert "die Beziehung billed_to gibt es nicht" in found["message"]

    def test_a_path_whose_direction_does_not_fit(self, model):
        _apply(model, op="artefact.set", key=KEY, step="active", kind="requires",
               artefact={"path": [{"relation": "client_of", "direction": "out", "to_type": "Organization"}]})
        assert _kinds(model) == ["path_invalid"]
        _apply(model, op="artefact.set", key=KEY, step="active", kind="requires", index=0,
               artefact={"path": [{"relation": "client_of", "direction": "in", "to_type": "Organization"}]})
        assert processes.validate(model) == []

    def test_rework_goes_back_and_forward_transitions_form_no_cycle(self, model):
        _apply(model, op="transition.add", key=KEY, **{"from": "closed", "to": "active"})
        assert _process(model)["transitions"][-1] == ["closed", "active", "rework"]
        assert processes.validate(model) == []
        _apply(model, op="transition.update", key=KEY, rework=False, **{"from": "closed", "to": "active"})
        assert set(_kinds(model)) == {"forward_cycle", "transition_both_ways"}
        _apply(model, op="transition.add", key=KEY, rework=True, **{"from": "open", "to": "closed"})
        assert "rework_forward" in _kinds(model)

    def test_the_status_map_covers_the_values_of_the_governing_fact(self, model):
        _apply(model, op="status.set", key=KEY, type="Mandate", value="active", unmapped=True)
        _apply(model, op="status.set", key=KEY, type="Mandate", value="archived", step="closed")
        assert _kinds(model) == ["status_unknown_value", "status_unmapped"]
        _apply(model, op="process.update", key=KEY, fields={"governing_fact": "fee_note"})
        assert "governing_fact_not_enum" in _kinds(model)

    def test_an_exit_criterion_names_a_rule(self, model):
        _apply(model, op="step.update", key=KEY, step="open", fields={"exit_criteria": ["P-unknown"]})
        assert _kinds(model) == ["criterion_unknown"]
        _apply(model, op="step.update", key=KEY, step="open", fields={"exit_criteria": ["R1", "P-owner"]})
        assert processes.validate(model) == []

    def test_the_schema_comes_first(self, model):
        _apply(model, op="step.remove", key=KEY, step="active")
        _apply(model, op="step.remove", key=KEY, step="closed")
        found = processes.validate(model)
        assert [f["kind"] for f in found] == ["schema"] and "steps" in found[0]["message"]

    def test_an_instance_type_that_is_gone(self, model):
        next(t for t in model["ontology"]["types"] if t["key"] == "Mandate")["retired"] = True
        assert "instance_type_unknown" in _kinds(model)


class TestTheEdits:
    def test_every_edit_is_one_change_of_the_process_and_undoes(self, model):
        before = _process(model)
        changes = _apply(model, op="step.create", key=KEY, id="review", name="Prüfung", after="open")
        assert [c["path"] for c in changes] == [f"processes/{KEY}"] and changes[0]["before"] == before
        assert [s["id"] for s in _process(model)["steps"]] == ["open", "review", "active", "closed"]
        editing.undo(model, changes)
        assert _process(model) == before
        editing.redo(model, changes)
        assert _process(model)["steps"][1] == {"id": "review", "name": "Prüfung", "order": 2}

    def test_a_step_moves_and_the_order_follows(self, model):
        _apply(model, op="step.move", key=KEY, step="closed", to=0)
        assert [(s["id"], s["order"]) for s in _process(model)["steps"]] == [("closed", 1), ("open", 2), ("active", 3)]
        with pytest.raises(editing.EditError):
            _apply(model, op="step.move", key=KEY, step="open", to=9)

    def test_a_rename_carries_transitions_final_and_status(self, model):
        _apply(model, op="step.rename", key=KEY, step="closed", to="abgeschlossen")
        p = _process(model)
        assert p["transitions"][-1] == ["active", "abgeschlossen"] and p["final"] == ["abgeschlossen"]
        assert p["status_map"]["Mandate"]["closed"] == "abgeschlossen"
        assert processes.validate(model) == []
        with pytest.raises(editing.EditError, match="schon"):
            _apply(model, op="step.rename", key=KEY, step="open", to="active")

    def test_removing_a_step_takes_its_transitions_and_its_values(self, model):
        _apply(model, op="step.remove", key=KEY, step="active")
        p = _process(model)
        assert p["transitions"] == [] and "active" not in p["status_map"]["Mandate"]

    def test_a_transition_drawn_back_is_rework(self, model):
        _apply(model, op="transition.add", key=KEY, **{"from": "active", "to": "open"})
        assert _process(model)["transitions"][-1] == ["active", "open", "rework"]
        with pytest.raises(editing.EditError, match="gibt es schon"):
            _apply(model, op="transition.add", key=KEY, **{"from": "active", "to": "open"})
        with pytest.raises(editing.EditError):
            _apply(model, op="transition.add", key=KEY, **{"from": "open", "to": "open"})
        _apply(model, op="transition.remove", key=KEY, **{"from": "active", "to": "open"})
        assert len(_process(model)["transitions"]) == 2

    def test_a_criterion_written_as_text_is_a_new_process_rule(self, model):
        changes = _apply(model, op="criterion.add", key=KEY, step="active",
                         rule={"name": "Honorar vereinbart", "check": "fact_present", "fact": "fee_note"})
        assert [c["path"] for c in changes] == ["processes.rules/P-honorar-vereinbart", f"processes/{KEY}"]
        assert next(s for s in _process(model)["steps"] if s["id"] == "active")["exit_criteria"] == ["P-honorar-vereinbart"]
        assert {"id": "P-honorar-vereinbart", "name": "Honorar vereinbart", "check": "fact_present",
                "fact": "fee_note"} in model["processes"]["rules"]
        assert processes.validate(model) == []
        editing.undo(model, changes)
        assert [r["id"] for r in model["processes"]["rules"]] == ["P-owner"]
        with pytest.raises(editing.EditError, match="gibt es nicht"):
            _apply(model, op="criterion.add", key=KEY, step="active", rule="P-none")

    def test_an_artefact_takes_its_type_from_the_path(self, model):
        _apply(model, op="artefact.set", key=KEY, step="open", kind="requires",
               artefact={"path": [{"relation": "due_in", "direction": "in", "to_type": "Deadline"}],
                         "required": True, "min_count": 2, "verified": False,
                         "where": {"fact": "due_on", "equals": "2026-12-31"}})
        [artefact] = next(s for s in _process(model)["steps"] if s["id"] == "open")["requires"]
        assert artefact == {"type": "Deadline", "required": True, "min_count": 2,
                            "path": [{"relation": "due_in", "direction": "in", "to_type": "Deadline"}],
                            "verified": False, "where": {"fact": "due_on", "equals": "2026-12-31"}}
        assert processes.validate(model) == []
        _apply(model, op="artefact.set", key=KEY, step="open", kind="requires", index=0, artefact=None)
        assert "requires" not in next(s for s in _process(model)["steps"] if s["id"] == "open")

    def test_a_status_value_on_another_step_moves(self, model):
        _apply(model, op="status.set", key=KEY, type="Mandate", value="closed", step="active")
        assert _process(model)["status_map"]["Mandate"]["closed"] == "active"
        _apply(model, op="status.set", key=KEY, type="Mandate", value="closed", step=None)
        assert _process(model)["status_map"]["Mandate"]["closed"] is None
        with pytest.raises(editing.EditError):
            _apply(model, op="status.set", key=KEY, type="Organization", value="ag", step="open")

    @pytest.mark.parametrize("key", ["mandat", "PROC-Mandat", "PROC-mandat-2", KEY])
    def test_a_process_key_must_be_well_formed_and_free(self, model, key):
        with pytest.raises(editing.EditError):
            _apply(model, op="process.create", key=key)

    @pytest.mark.parametrize("start", ["blank", "without a version"])
    def test_a_process_file_the_builder_made_is_of_the_current_format(self, start):
        model = package.read_package(package.blank_package("Muster")).model
        if start == "without a version":  # as the builder wrote it before: {"processes": []}
            model["processes"] = {"processes": []}
        model["ontology"]["types"].append({"key": "Matter", "level": "company", "attributes": []})
        _apply(model, op="process.create", key="PROC-neu", instance_types=["Matter"])
        assert model["processes"]["version"] == processes.FORMAT_VERSION
        assert not [p for p in processes.validate(model) if p["kind"] == "schema" and "version" in p["message"]]

    def test_without_a_processes_file_the_first_process_brings_one(self, model):
        del model["package"]["manifest"]["processes"]
        model["processes"] = None
        _apply(model, op="process.create", key="PROC-neu")
        assert model["package"]["manifest"]["processes"] == package.PART_PATHS["processes"]
        assert b"PROC-neu" in package.write_package(model)[package.PART_PATHS["processes"]]

    def test_the_package_writes_the_processes_in_its_format(self, model):
        _apply(model, op="transition.add", key=KEY, **{"from": "closed", "to": "active"})
        files = package.write_package(model)
        again = package.read_package(files).model
        assert again["processes"] == model["processes"]
        assert b"- - closed\n    - active\n    - rework" in files["ontology/processes.yaml"]


    def test_a_generated_machine_form_follows_the_edits(self, model):
        """brain-runner bundles ``generated.processes`` when the manifest names it: edits must reach that file."""
        import json

        model["package"]["manifest"]["generated"] = {"processes": "ontology/generated/process-definitions.json"}
        made = dict(processes.normalise(model["processes"]), generated_at="2026-10-01", source="ontology/processes.yaml")
        kept = json.dumps(made, indent=1, ensure_ascii=False) + "\n"
        model["package"]["files"] = {"ontology/generated/process-definitions.json": kept}
        assert package.write_package(model)["ontology/generated/process-definitions.json"] == kept.encode("utf-8")
        _apply(model, op="step.create", key=KEY, id="review", after="open")
        written = json.loads(package.write_package(model)["ontology/generated/process-definitions.json"])
        assert [s["id"] for s in written["processes"][0]["steps"]] == ["open", "review", "active", "closed"]
        assert written["source"] == "ontology/processes.yaml" and written["generated_at"] != "2026-10-01"

class TestTheReview:
    def test_a_process_change_reads_as_steps_transitions_and_values(self, model):
        before = _process(model)
        _apply(model, op="step.create", key=KEY, id="review", after="open")
        _apply(model, op="transition.add", key=KEY, **{"from": "active", "to": "open"})
        _apply(model, op="status.set", key=KEY, type="Mandate", value="active", step="review")
        group = review.element_diff(f"processes/{KEY}", before, _process(model))
        texts = [line["text"] for line in group["lines"]]
        assert group["kind"] == "Prozess"
        assert "Schritt review" in texts and "Nacharbeit active → open" in texts
        assert "Status Mandate = active → review" in texts

    def test_a_new_process_and_rule_describe_themselves(self, model):
        assert review.describe(_process(model)) == "Mandat · 3 Schritte: open → active → closed"
        assert review.describe({"id": "P-x", "name": "X", "check": "fact_present"}) == "X (P-x)"


class TestTheRoutes:
    def _client(self):
        user = StandInUser({"admin"})
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        app = make_app(user, store)
        client = app.test_client()
        response = client.post("/builder/api/import", data={
            "package": (io.BytesIO(package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))), "p.zip"),
            "name": "Beispiel AG"},
            content_type="multipart/form-data")
        assert response.status_code == 201
        [workspace] = store.list()
        return client, workspace, app

    def test_the_stage_and_an_edit_that_answers_with_the_processes(self):
        client, workspace, _ = self._client()
        page = client.get("/builder/prozesse")
        assert page.status_code == 200 and b"b-proc-data" in page.data and b"PROC-mandate" in page.data
        url = f"/builder/api/w/{workspace.id}/ontology?view=processes"
        answer = client.post(url, json={"op": "process.create", "key": "PROC-neu", "name": "Neu", "instance_types": ["Mandate"]})
        body = answer.get_json()
        assert answer.status_code == 200 and "processes" in body and "ontology" not in body
        assert any(p["key"] == "PROC-neu" for p in body["processes"]["processes"])
        undone = client.post(f"/builder/api/w/{workspace.id}/undo?view=processes").get_json()
        assert not any(p["key"] == "PROC-neu" for p in undone["processes"]["processes"])

    def test_only_a_builder_role_edits(self):
        client, workspace, app = self._client()
        app.extensions["builder_gate"].user = StandInUser({"member"})
        answer = client.post(f"/builder/api/w/{workspace.id}/ontology?view=processes",
                             json={"op": "process.create", "key": "PROC-neu"})
        assert answer.status_code == 403
        assert client.get("/builder/prozesse").status_code == 403


CLIENT_ZERO = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO")


@pytest.mark.skipif(not CLIENT_ZERO, reason="needs $KNOWLEDGEBASE_CLIENT_ZERO (KnowledgeBase's client-zero folder)")
class TestClientZero:
    @pytest.fixture
    def cz(self):
        return package.read_package(package.files_from_directory(Path(CLIENT_ZERO))).model

    def test_its_processes_pass_the_checks(self, cz):
        view = processes.build(cz)
        assert view["counts"]["processes"] == 7 and processes.validate(cz) == []

    def test_a_step_and_a_rework_loop_reach_brain_runner_s_bundle(self, cz):
        pytest.importorskip("brain_runner.ontology")
        from builder import publisher

        key = "PROC-feature-lifecycle"
        _apply(cz, op="step.create", key=key, id="security-review", after="verified")
        _apply(cz, op="criterion.add", key=key, step="security-review",
               rule={"name": "Sicherheitsprüfung abgeschlossen", "check": "fact_present", "fact": "owner"})
        for a, b in (("verified", "security-review"), ("security-review", "implemented"), ("security-review", "implementing")):
            _apply(cz, op="transition.add", key=key, **{"from": a, "to": b})
        assert processes.validate(cz) == []
        bundle = publisher.BrainRunnerEngine(lambda: None).compile(cz).bundle
        lifecycle = next(p for p in bundle["sections"]["processes"]["processes"] if p["key"] == key)
        # The bundle holds the processes as the package lists them: the YAML, or its generated machine form.
        assert ("security-review", "implementing", True) in processes.transitions_of(lifecycle)
        assert next(s for s in lifecycle["steps"] if s["id"] == "security-review")["exit_criteria"] == [
            "P-sicherheitspruefung-abgeschlossen"]
