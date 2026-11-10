"""J-2: ``set_assignee``, ``transition_issue`` and ``set_due_date`` (``staging: [direct]``, ``grouping: none``): an exact
preview from the edit metadata and the transitions, inexact for a field Jira or an automation computes; a conflict
when the change log moved past ``expected_version`` (nothing written); the permit id kept on the issue in the same
request as the write, so a repeat writes nothing and answers a receipt; the receipt is a re-read
(SPEC-write-back-06, -07, -08, -10, -31, -32; GI-CONN-05, -06, -09, -10)."""
from __future__ import annotations

import pytest

from harness import SOURCE, JiraHarness
from knovas_connector_jira.connector import MARK_PREFIX
from knovas_connector_kit import contracts
from knovas_connector_kit.conformance.suite import Brain


@pytest.fixture
def h():
    return JiraHarness()


def step(action, target, **params):
    return {"action": action, "target": target, "params": params}


def preview(h, *steps, brain=None):
    result = h.connector.handle((brain or Brain(h.source)).preview(list(steps)))
    assert contracts.valid("result.schema.json", result)
    return result


def act(h, one, expected, brain=None):
    result = h.connector.handle((brain or Brain(h.source)).act([one], [expected]))
    assert contracts.valid("result.schema.json", result)
    return result


def version(result, target):
    return next(v["version"] for v in result["preview"]["read_versions"] if v["target"] == target)


def plain(entries):
    """Changes without their step index (every one names it, D-14; the tests that are about steps check it)."""
    assert all(isinstance(e["step"], int) for e in entries)
    return [{k: v for k, v in e.items() if k != "step"} for e in entries]


# ------------------------------------------------------------------ the declaration

def test_three_direct_actions_field_edits_atomic_a_transition_alone_no_inverse(h):
    decl = h.connector.declaration()
    assert contracts.valid("declaration.schema.json", decl)
    assert [a["name"] for a in decl["actions"]] == ["set_assignee", "transition_issue", "set_due_date"]
    assert all(a["staging"] == ["direct"] and a["inverse"] is None
               and a["preview"] == "exact" and a["target_kind"] == "issue" for a in decl["actions"])
    assert {a["name"]: a["grouping"] for a in decl["actions"]} == {"set_assignee": "atomic", "set_due_date": "atomic",
                                                                   "transition_issue": "none"}
    assert decl["source_kind"] == "jira" and decl["fetch"] is False
    assert decl["streams"] == ["record", "tombstone", "generation", "state"]


# ------------------------------------------------------------------ preview

def test_an_exact_preview_names_each_field_before_and_after_and_the_version_read(h):
    h.jira.edit_by_someone("DEMO-1", summary="Renamed")
    got = preview(h, step("set_assignee", "DEMO-1", assignee="acct-7"),
                  step("transition_issue", "DEMO-2", status="Done"),
                  step("set_due_date", "DEMO-3", due_date=None))
    assert got["status"] == "ok" and got["preview"]["exact"] and got["preview"]["grouping"] == "none"
    assert plain(got["preview"]["changes"]) == [
        {"target": "DEMO-1", "field": "assignee", "before": "acct-1", "after": "acct-7"},
        {"target": "DEMO-2", "field": "status", "before": "To Do", "after": "Done"},
        {"target": "DEMO-3", "field": "duedate", "before": "2026-11-20", "after": None}]
    assert version(got, "DEMO-1") == h.jira.issues["DEMO-1"]["history"][-1]["id"]
    assert sum(h.jira.writes.values()) == 0                                  # a preview writes nothing


def test_each_change_names_its_step_so_two_actions_on_one_issue_are_told_apart(h):
    got = preview(h, step("set_assignee", "DEMO-1", assignee="acct-7"),
                  step("set_due_date", "DEMO-1", due_date="2026-12-01"))
    assert got["preview"]["read_versions"] == [{"target": "DEMO-1", "version": "0"}]
    assert [(c["field"], c["step"]) for c in got["preview"]["changes"]] == [("assignee", 0), ("duedate", 1)]
    assert got["preview"]["grouping"] == "atomic"
    mixed = preview(h, step("set_assignee", "DEMO-1", assignee="acct-7"), step("transition_issue", "DEMO-2", status="Done"))
    assert mixed["preview"]["grouping"] == "none"                     # the approver is told: written one by one


def test_two_steps_that_set_one_field_of_one_issue_are_refused(h):
    got = preview(h, step("set_assignee", "DEMO-1", assignee="acct-7"), step("set_assignee", "DEMO-1", assignee="acct-8"))
    assert (got["status"], got["code"]) == ("refused", "same_field_twice")


def test_a_field_missing_from_the_edit_metadata_makes_the_preview_inexact(h):
    got = preview(h, step("set_due_date", "DEMO-4", due_date="2026-12-01"))
    assert got["preview"]["exact"] is False and got["preview"]["inexact_code"] == "computed_field"
    assert got["preview"]["changes"] == [] and got["code"] == "preview_inexact"


@pytest.mark.parametrize("field,one", [("assignee", step("set_assignee", "DEMO-1", assignee="acct-7")),
                                       ("status", step("transition_issue", "DEMO-2", status="Done"))])
def test_a_field_an_automation_computes_makes_the_preview_inexact(field, one):
    h = JiraHarness(dict(SOURCE, settings=dict(SOURCE["settings"], computed_fields=[field])))
    got = preview(h, one)
    assert got["preview"]["exact"] is False and got["preview"]["inexact_code"] == "computed_field"


@pytest.mark.parametrize("one,code", [
    (step("set_assignee", "DEMO-1", assignee="acct-7", extra="x"), "params_invalid"),
    (step("set_assignee", "DEMO-1", assignee="no spaces allowed"), "params_invalid"),
    (step("set_due_date", "DEMO-3", due_date="2026-02-30"), "params_invalid"),
    (step("set_due_date", "DEMO-3", due_date="1 December"), "params_invalid"),
    (step("transition_issue", "DEMO-2", status=""), "params_invalid"),
    (step("set_assignee", "OTHER-1", assignee="acct-7"), "target_not_found"),
    (step("set_assignee", "DEMO-99", assignee="acct-7"), "target_not_found"),
    (step("set_assignee", "DEMO-1", assignee="acct-unknown"), "assignee_not_assignable"),
    (step("set_assignee", "DEMO-1", assignee="acct-1"), "no_change"),
    (step("transition_issue", "DEMO-2", status="Rejected"), "transition_unavailable"),
    (step("transition_issue", "DEMO-6", status="Rejected"), "transition_needs_fields"),
])
def test_a_step_that_cannot_be_carried_out_is_refused(h, one, code):
    got = preview(h, one)
    assert (got["status"], got["code"]) == ("refused", code)


def test_two_transitions_to_one_status_are_refused_as_ambiguous(h):
    h.jira.workflow.append(("61", "Finish quickly", ["To Do"], "Done", []))
    got = preview(h, step("transition_issue", "DEMO-2", status="Done"))
    assert (got["status"], got["code"]) == ("refused", "transition_ambiguous")


def test_a_preview_at_a_version_the_issue_left_is_a_conflict(h):
    brain = Brain(h.source)
    h.jira.edit_by_someone("DEMO-1", summary="Renamed")
    got = h.connector.handle(brain.preview([step("set_assignee", "DEMO-1", assignee="acct-7")], at_version="0"))
    assert (got["status"], got["code"]) == ("conflict", "version_changed")
    assert got["conflict"]["found_version"] == h.jira.issues["DEMO-1"]["history"][-1]["id"]


# ------------------------------------------------------------------ act

def test_a_direct_write_sends_the_change_and_the_permit_marker_in_one_request(h):
    one = step("set_due_date", "DEMO-3", due_date="2026-12-01")
    brain = Brain(h.source)
    got = act(h, one, version(preview(h, one, brain=brain), "DEMO-3"), brain)
    receipt = got["receipts"][0]
    writes = [c for c in h.jira.calls if c[0] in ("PUT", "POST") and c[1] != "/search/jql"]
    assert writes == [("PUT", "/issue/DEMO-3")]
    assert h.jira.issues["DEMO-3"]["properties"][MARK_PREFIX + receipt["permit_id"]]["after"] == "2026-12-01"
    assert receipt["outcome"] == "written" and receipt["differences"] == [] and receipt["side_effects"] == []
    assert plain(receipt["values"]) == [{"target": "DEMO-3", "field": "duedate", "before": "2026-11-20",
                                         "after": "2026-12-01"}]
    assert receipt["version_after"] == h.jira.issues["DEMO-3"]["history"][-1]["id"] != receipt["expected_version"]


def test_a_transition_to_done_names_what_jira_set_besides_as_side_effects(h):
    one = step("transition_issue", "DEMO-2", status="Done")
    brain = Brain(h.source)
    got = act(h, one, version(preview(h, one, brain=brain), "DEMO-2"), brain)
    receipt = got["receipts"][0]
    assert h.jira.status_of("DEMO-2") == "Done" and receipt["differences"] == []
    assert receipt["side_effects"] == [{"target": "DEMO-2", "field": "resolution"}]


def test_unassigning_writes_null(h):
    one = step("set_assignee", "DEMO-1", assignee=None)
    brain = Brain(h.source)
    got = act(h, one, version(preview(h, one, brain=brain), "DEMO-1"), brain)
    assert plain(got["receipts"][0]["values"])[0]["after"] is None and h.jira.issues["DEMO-1"]["fields"]["assignee"] is None


def test_the_receipt_is_a_re_read_so_an_automation_that_changes_the_value_is_a_difference(h):
    one = step("set_assignee", "DEMO-1", assignee="acct-7")
    brain = Brain(h.source)
    expected = version(preview(h, one, brain=brain), "DEMO-1")
    h.jira.alter_next_write = True
    receipt = act(h, one, expected, brain)["receipts"][0]
    assert plain(receipt["differences"]) == [{"target": "DEMO-1", "field": "assignee", "previewed": "acct-7",
                                              "found": "acct-9"}]
    assert receipt["version_after"] == h.jira.issues["DEMO-1"]["history"][-1]["id"]


def test_a_change_log_that_moved_past_the_expected_version_is_a_conflict_and_nothing_is_written(h):
    one = step("set_assignee", "DEMO-1", assignee="acct-7")
    brain = Brain(h.source)
    expected = version(preview(h, one, brain=brain), "DEMO-1")
    h.jira.edit_by_someone("DEMO-1", assignee=h.jira._person("acct-3"))
    got = act(h, one, expected, brain)
    assert (got["status"], got["code"]) == ("conflict", "version_changed") and h.jira.writes == {}
    conflict = got["conflict"]
    assert (conflict["target"], conflict["expected_version"]) == ("DEMO-1", expected)
    assert plain(conflict["changes"]) == [{"target": "DEMO-1", "field": "assignee", "before": "acct-1",
                                           "after": "acct-3"}]
    assert conflict["step"] == 0 and conflict["changes"][0]["step"] == 0


def test_a_repeat_writes_nothing_and_answers_the_receipt(h):
    one = step("set_assignee", "DEMO-1", assignee="acct-7")
    brain = Brain(h.source)
    job = brain.act([one], [version(preview(h, one, brain=brain), "DEMO-1")])
    first, again = h.connector.handle(job), h.connector.handle(job)
    assert h.jira.writes == {"DEMO-1": 1}
    assert again["receipts"][0]["values"] == first["receipts"][0]["values"]
    assert again["receipts"][0]["version_after"] == first["receipts"][0]["version_after"]


def test_a_write_whose_answer_was_lost_is_found_by_its_marker_and_not_written_again(h):
    one = step("transition_issue", "DEMO-2", status="In Progress")
    brain = Brain(h.source)
    job = brain.act([one], [version(preview(h, one, brain=brain), "DEMO-2")])
    h.jira.crash_after_next_write = True
    lost = h.connector.handle(job)
    assert (lost["status"], lost["code"]) == ("failed", "jira.http_500")
    again = h.connector.handle(job)
    assert again["status"] == "ok" and again["receipts"][0]["outcome"] == "written"
    assert plain(again["receipts"][0]["values"]) == [{"target": "DEMO-2", "field": "status", "before": "To Do",
                                                      "after": "In Progress"}]
    assert h.jira.writes == {"DEMO-2": 1}


def test_a_source_switched_to_read_writes_nothing():
    h = JiraHarness(dict(SOURCE, access="read"))
    one = step("set_assignee", "DEMO-1", assignee="acct-7")
    brain = Brain(h.source)
    got = act(h, one, version(preview(h, one, brain=brain), "DEMO-1"), brain)
    assert (got["status"], got["code"]) == ("refused", "write_disabled") and h.jira.writes == {}


def test_a_step_on_a_computed_field_is_never_written(h):
    got = act(h, step("set_due_date", "DEMO-4", due_date="2026-12-01"), "0")
    assert (got["status"], got["code"]) == ("refused", "computed_field") and h.jira.writes == {}


def test_two_field_edits_of_one_issue_are_one_edit_one_change_log_entry_and_one_receipt_per_step(h):
    """The coordinator's decision (2026-11-10): set_assignee and set_due_date on one issue are written together."""
    brain = Brain(h.source)
    pair = [step("set_assignee", "DEMO-1", assignee="acct-7"), step("set_due_date", "DEMO-1", due_date="2026-12-01")]
    expected = version(preview(h, *pair, brain=brain), "DEMO-1")
    job = brain.act(pair, [expected, expected])
    got = h.connector.handle(job)
    assert contracts.valid("result.schema.json", got) and got["status"] == "ok"
    assert [c for c in h.jira.calls if c[0] in ("PUT", "POST") and c[1] != "/search/jql"] == [("PUT", "/issue/DEMO-1")]
    assert len(h.jira.issues["DEMO-1"]["history"]) == 1 and h.jira.writes == {"DEMO-1": 1}
    assert [(r["values"][0]["step"], r["values"][0]["field"], r["values"][0]["after"]) for r in got["receipts"]] == [
        (0, "assignee", "acct-7"), (1, "duedate", "2026-12-01")]
    assert all(r["side_effects"] == [] and r["differences"] == [] for r in got["receipts"])
    again = h.connector.handle(job)                                   # a repeat finds both markers
    assert [r["values"] for r in again["receipts"]] == [r["values"] for r in got["receipts"]] and h.jira.writes == {"DEMO-1": 1}


def test_a_transition_goes_alone(h):
    brain = Brain(h.source)
    pair = [step("set_assignee", "DEMO-2", assignee="acct-7"), step("transition_issue", "DEMO-2", status="Done")]
    got = h.connector.handle(brain.act(pair, ["0", "0"]))
    assert (got["status"], got["code"]) == ("refused", "grouping_unsupported") and h.jira.writes == {}


def test_field_edits_of_two_issues_are_never_partly_written(h):
    brain = Brain(h.source)
    pair = [step("set_assignee", "DEMO-1", assignee="acct-7"), step("set_due_date", "DEMO-3", due_date="2026-12-01")]
    prev = preview(h, *pair, brain=brain)
    got = h.connector.handle(brain.act(pair, [version(prev, "DEMO-1"), version(prev, "DEMO-3")]))
    assert (got["status"], got["code"]) == ("refused", "grouping_across_items") and h.jira.writes == {}
    h.jira.edit_by_someone("DEMO-3", summary="Renamed")                 # a moved issue is reported first, with its step
    got = h.connector.handle(brain.act(pair, [version(prev, "DEMO-1"), version(prev, "DEMO-3")]))
    assert (got["status"], got["code"], got["conflict"]["step"]) == ("conflict", "version_changed", 1)
    assert h.jira.writes == {}


def test_a_transition_after_an_edit_in_its_own_job_finds_the_change_log_moved(h):
    """With grouping none for transition_issue, a change set that edits and moves one issue gets two act jobs against
    the one preview's version: the second is a conflict. The Brain refuses such a change set (the coordinator)."""
    brain = Brain(h.source)
    first, second = step("set_assignee", "DEMO-2", assignee="acct-7"), step("transition_issue", "DEMO-2", status="Done")
    expected = version(preview(h, first, second, brain=brain), "DEMO-2")
    assert act(h, first, expected, brain)["status"] == "ok"
    got = act(h, second, expected, brain)
    assert (got["status"], got["code"]) == ("conflict", "version_changed") and h.jira.writes == {"DEMO-2": 1}


# ------------------------------------------------------------------ reread

def test_reread_answers_values_versions_and_the_receipt_of_an_earlier_write(h):
    one = step("set_due_date", "DEMO-3", due_date="2026-12-01")
    brain = Brain(h.source)
    receipt = act(h, one, version(preview(h, one, brain=brain), "DEMO-3"), brain)["receipts"][0]
    got = h.connector.handle(brain.job("reread", {"targets": ["DEMO-3", "DEMO-99"], "fields": ["duedate"],
                                                  "permit_ids": [receipt["permit_id"]]}))
    assert contracts.valid("result.schema.json", got) and got["status"] == "ok"
    assert got["read"] == [{"target": "DEMO-3", "version": receipt["version_after"],
                            "values": {"duedate": "2026-12-01"}},
                           {"target": "DEMO-99", "version": None}]
    assert got["receipts"][0]["permit_id"] == receipt["permit_id"]
    assert got["receipts"][0]["values"] == receipt["values"] and h.jira.writes == {"DEMO-3": 1}
