"""Alloy pins — node grants (SS-315, plan C0).

The pytest half of the must-agree contract, mirroring KnowledgeBase's
tests/alloy_invariants/test_kg_v1_alloy_pins.py: every command in the models is
registered in models/alloy/ci/expected_results.json with the right outcome, so a
silently dropped check or a mutant that stopped refuting fails pytest as well as
the Alloy CI step.
"""
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.alloy

MODELS = Path(__file__).resolve().parents[1] / "models" / "alloy"

# file (relative to models/alloy) -> {command: kind}, kind in {check, run}
COMMANDS = {
    "node_grants.als": {
        "an_editor_cannot_delegate": "check",
        "who_may_delegate_is_unambiguous": "check",
        "grants_never_narrow_reads": "check",
        "write_needs_a_grant_or_admin": "check",
        "an_admin_always_writes": "check",
        "witness_mechanism_live": "run",
        "witness_breach_expressible": "run",
    },
    "node_grants_lifecycle.als": {
        "the_owner_survives_a_revoke": "check",
        "a_revoke_removes_only_the_named_editor": "check",
        "the_creator_owns_the_new_node": "check",
        "the_table_shape_is_preserved": "check",
        "witness_revoke_of_an_editor": "run",
        "witness_create_makes_an_owner": "run",
        "witness_breach_expressible": "run",
    },
}
MUTANTS = {
    "mutants/node_grants__editor_delegates.als": "editor_delegates_when_grant_gate_is_may_write",
    "mutants/node_grants__two_owners.als": "two_owners_without_the_partial_index",
    "mutants/node_grants__reads_narrowed.als": "reader_without_a_grant_is_withheld",
    "mutants/node_grants__revoke_ignores_role.als": "owner_lost_when_revoke_ignores_role",
}
_CMD = re.compile(r"^\s*(run|check)\s+(\w+)\b", re.MULTILINE)


def _expected():
    path = MODELS / "ci" / "expected_results.json"
    assert path.is_file(), f"missing {path}"
    return json.loads(path.read_text(encoding="utf-8"))


class TestCommandsRegistered:
    def test_every_command_is_pinned_with_its_outcome(self):
        data = _expected()
        checks = {(c["file"], c["command"]): c["outcome"] for c in data["checks"]}
        runs = {(r["file"], r["command"]): r["outcome"] for r in data["runs"]}
        problems = []
        for fname, commands in COMMANDS.items():
            for cmd, kind in commands.items():
                table, want = (checks, "no_counterexample") if kind == "check" else (runs, "satisfiable")
                got = table.get((f"models/alloy/{fname}", cmd))
                if got != want:
                    problems.append(f"{fname}::{cmd} = {got!r} (want {want!r})")
        for fname, cmd in MUTANTS.items():
            got = checks.get((f"models/alloy/{fname}", cmd))
            if got != "counterexample":
                problems.append(f"{fname}::{cmd} = {got!r} (want 'counterexample': the mutant must refute)")
        assert not problems, problems

    def test_no_command_on_disk_is_unpinned(self):
        for fname, commands in COMMANDS.items():
            text = (MODELS / fname).read_text(encoding="utf-8")
            on_disk = {m.group(2) for m in _CMD.finditer(text)}
            assert on_disk == set(commands), f"{fname}: disk {sorted(on_disk)} != pinned {sorted(commands)}"

    def test_every_mutant_exists(self):
        gone = [f for f in MUTANTS if not (MODELS / f).is_file()]
        assert not gone, gone


class TestHeadersTrace:
    def test_models_name_the_plan_and_the_code(self):
        for fname in COMMANDS:
            text = (MODELS / fname).read_text(encoding="utf-8")
            assert "2026-09-02-typed-node-workbench-components.md" in text, fname
            assert "@code_under_check" in text and "node_grants.py" in text, fname
