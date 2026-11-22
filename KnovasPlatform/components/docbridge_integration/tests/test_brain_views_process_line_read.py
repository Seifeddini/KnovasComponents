"""PMVP-32a: the read of one thing's process line (``brain_views.process_line``), without the web app.

The fake Core below is built from the shapes of the Client Zero convention as the tenant holds them (decision
PMVP-32, option A), with invented names: a ``Process`` node, ``ProcessStep`` nodes joined to it by ``has_step``, an
``order`` fact on each step, ``transitions_to`` and ``reworks_to`` between steps, edges stored with their ends in id
order (``node_lo`` < ``node_hi``) and not in their direction; an instance joined by ``instance_of`` and ``in_state``,
with ``current_step``, ``step_history`` and ``owner`` facts. Its ``GET /edges?node_id=&type=`` honours both filters
(the Core's contract on master), or answers 404, or ignores the filters (``edges_route``).

Pinned: the main path and the variants; the direction from ``order``; "not completely described"; «seit» and who;
the edges route used, its 404 and an unfiltered answer falling back to the process's neighbours; not in a process;
the reads (per thing, as asked, never the whole tenant).
"""
from __future__ import annotations

import copy
import json
import logging
import re
from datetime import date

import pytest

from brain_views import process_line
from brain_views.process_line import MAX_STEP_READS, NotFound, Unavailable, main_path, read_line, step_name

T_PROCESS, T_STEP, T_PLAN, T_PERSON, T_ROLE = (f"7a2b0000-0000-4000-8000-0000000032{n:02d}" for n in range(5))
KIND_NAMES = {T_PROCESS: "Process", T_STEP: "ProcessStep", T_PLAN: "Vorhaben", T_PERSON: "Person", T_ROLE: "Role"}
PROCESS = "6f1c2c1e-0000-4000-8000-000000320000"
INSTANCE = "6f1c2c1e-0000-4000-8000-000000329999"
WHOLE_TENANT = ("graph_export", "graph_edges", "graph_nodes", "graph_node_types")
STEP_NAMES = ["Idee", "Skizze", "Entwurf", "Modell", "Vorprüfung", "Freigabe", "In Arbeit", "Geprüft", "Fertig",
              "Ausgeliefert", "Verworfen"]


def step_id(n):
    """Step n's id. Odd and even steps are numbered from opposite ends, so ids do not follow the order."""
    return f"6f1c2c1e-0000-4000-8000-{(320000 + (n if n % 2 else 900 - n)):012d}"


def edge(a, b, relation, *, swap=False):
    lo, hi = sorted((a, b))
    if swap:
        lo, hi = hi, lo
    return {"id": f"e-{a[-4:]}-{b[-4:]}-{relation}", "node_lo": lo, "node_hi": hi, "relation": relation}


class ProcessCore:
    """One process and one instance in the Client Zero convention, as the Core's per-thing routes give them."""

    def __init__(self, *, steps=11, transitions=None, reworks=None, current=6, edges_route="honoured"):
        self.calls: list[tuple] = []
        self.edges_route = edges_route
        self.process_name = "Vorhaben-Ablauf"
        self.nodes = {PROCESS: {"id": PROCESS, "name": self.process_name, "node_type_id": T_PROCESS},
                      INSTANCE: {"id": INSTANCE, "name": "Vorhaben Archiv 2026", "node_type_id": T_PLAN}}
        self.facts = {PROCESS: [], INSTANCE: []}
        self.steps = []
        for n in range(1, steps + 1):
            name = STEP_NAMES[n - 1] if n <= len(STEP_NAMES) else f"Schritt {n}"
            sid = step_id(n)
            self.steps.append(sid)
            self.nodes[sid] = {"id": sid, "name": f"{self.process_name}: {name}", "node_type_id": T_STEP}
            self.facts[sid] = [self.fact(sid, "order", str(n)), self.fact(sid, "owner_role", "ROLE-X")]
        if transitions is None:
            transitions = [(1, 2), (2, 3), (2, 6), (3, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 9), (9, 10), (6, 11)]
        if reworks is None:
            reworks = [(8, 7), (6, 2)]
        self.edges = [edge(PROCESS, s, "has_step") for s in self.steps]
        self.edges += [edge(step_id(a), step_id(b), "transitions_to") for a, b in transitions]
        self.edges += [edge(step_id(a), step_id(b), "reworks_to") for a, b in reworks]
        self.edges += [edge(INSTANCE, PROCESS, "instance_of"), edge(INSTANCE, step_id(current), "in_state")]
        self.facts[INSTANCE] = [
            self.fact(INSTANCE, "current_step", "freigabe", valid_from="2026-11-09"),
            self.fact(INSTANCE, "step_history", json.dumps([["vorpruefung", "2026-11-01", "2026-11-09"],
                                                            ["freigabe", "2026-11-09", None]])),
            self.fact(INSTANCE, "owner", "PERS-erfunden"),
        ]
        # other instances of the same process, and other neighbours of the instance
        for n in range(3):
            other = f"6f1c2c1e-0000-4000-8000-0000003280{n:02d}"
            self.nodes[other] = {"id": other, "name": f"Vorhaben {n}", "node_type_id": T_PLAN}
            self.facts[other] = []
            self.edges += [edge(other, PROCESS, "instance_of"), edge(other, step_id(2), "in_state")]
        person = "6f1c2c1e-0000-4000-8000-000000327001"
        self.nodes[person] = {"id": person, "name": "Erfundene Person", "node_type_id": T_PERSON}
        self.facts[person] = []
        self.edges.append(edge(INSTANCE, person, "owned_by"))

    @staticmethod
    def fact(node, label, value, valid_from=None):
        return {"id": f"f-{node[-4:]}-{label}", "node_id": node, "attribute_id": None, "label": label,
                "value": value, "valid_from": valid_from, "curation_status": "manual"}

    # -- the Core's per-thing routes
    def graph_node(self, node_id):
        self.calls.append(("graph_node", node_id))
        if node_id not in self.nodes:
            return None
        return {"status": "success", "node": dict(self.nodes[node_id]), "assignments": [], "sections": [],
                "facts": copy.deepcopy(self.facts[node_id])}

    def graph_neighbors(self, node_id, depth=1, include_edges=False):
        self.calls.append(("graph_neighbors", node_id, depth, include_edges))
        if node_id not in self.nodes:
            return {"neighbors": [], "edges": []}
        near = {e["node_hi"] if e["node_lo"] == node_id else e["node_lo"]
                for e in self.edges if node_id in (e["node_lo"], e["node_hi"])}
        rows = [dict(self.nodes[n], hop=1) for n in sorted(near) if n in self.nodes]
        rows.append(dict(self.nodes[node_id], hop=0))
        ids = near | {node_id}
        induced = [dict(e) for e in self.edges if e["node_lo"] in ids and e["node_hi"] in ids]
        return {"neighbors": rows, "edges": induced if include_edges else []}

    def graph_get_many(self, calls, *, workers=4, acquire=None, max_retries=3):
        answers = []
        for path, params in calls:
            self.calls.append(("graph_get_many", path, tuple(sorted((params or {}).items()))))
            match = re.fullmatch(r"/node-types/([0-9a-f-]{36})", path)
            if match:
                name = KIND_NAMES.get(match.group(1))
                answers.append((200, {"status": "success", "node_type": {"id": match.group(1), "name": name}})
                               if name else (404, {"status": "error"}))
            elif path == "/edges":
                answers.append(self._edges(params or {}))
            elif re.fullmatch(r"/nodes/[0-9a-f-]{36}", path):
                found = self.graph_node(path.rsplit("/", 1)[-1])
                self.calls.pop()
                answers.append((200, found) if found else (404, {"status": "error"}))
            else:
                answers.append((404, {"status": "error"}))
        return answers

    def _edges(self, params):
        if self.edges_route == "404":
            return 404, {"status": "error"}
        if self.edges_route == "unfiltered":
            return 200, {"status": "success", "edges": [dict(e) for e in self.edges]}
        node, relation = params.get("node_id"), params.get("type")
        return 200, {"status": "success", "edges": [dict(e) for e in self.edges
                                                    if node in (e["node_lo"], e["node_hi"])
                                                    and (relation is None or e["relation"] == relation)]}

    def graph_facts_batch(self, node_ids):
        self.calls.append(("graph_facts_batch", tuple(node_ids)))
        return 200, {"status": "success",
                     "facts_by_node": {n: copy.deepcopy(self.facts[n]) for n in node_ids if n in self.facts}}

    def __getattr__(self, name):
        if name in WHOLE_TENANT:
            raise AssertionError(f"the process line made a whole-tenant read: {name}")
        raise AttributeError(name)


def _names(steps):
    return [s.name for s in steps]


# --- the main path and the variants ---------------------------------------------------------------------------------


def test_the_main_path_follows_the_smallest_next_order_and_the_rest_are_variants():
    line = read_line(ProcessCore(), INSTANCE)
    assert line.in_process and line.complete
    assert _names(line.main) == STEP_NAMES[:10]
    assert (line.position, len(line.main)) == (6, 10)
    assert line.current.name == "Freigabe" and line.next_step.name == "In Arbeit"
    assert [(w.step.name, w.relation) for w in line.ways] == [("Verworfen", "transitions_to"), ("Skizze", "reworks_to")]
    assert line.process_name == "Vorhaben-Ablauf" and line.kind == "Vorhaben" and line.name == "Vorhaben Archiv 2026"


def test_the_direction_comes_from_the_order_not_from_how_an_edge_is_stored():
    core = ProcessCore()
    swapped = ProcessCore()
    swapped.edges = [dict(e, node_lo=e["node_hi"], node_hi=e["node_lo"]) for e in swapped.edges]
    first, second = read_line(core, INSTANCE), read_line(swapped, INSTANCE)
    assert _names(first.main) == _names(second.main)
    assert (first.position, first.next_step, first.ways) == (second.position, second.next_step, second.ways)


def test_the_main_path_function():
    orders = {"a": 1, "b": 2, "c": 3, "d": 4}
    assert main_path(orders, {"a": {"c", "b"}, "b": {"d"}, "c": {"d"}}) == ["a", "b", "d"]
    assert main_path(orders, {}) == ["a"]
    assert main_path({}, {}) == []


def test_a_step_off_the_main_path_is_where_the_thing_left_it():
    line = read_line(ProcessCore(current=11), INSTANCE)
    assert line.complete and line.position is None and line.current.name == "Verworfen"
    assert line.next_step is None and _names(line.main) == STEP_NAMES[:10]


def test_at_the_end_of_the_main_path_nothing_comes_after():
    line = read_line(ProcessCore(current=10), INSTANCE)
    assert line.position == 10 and line.next_step is None


def test_a_steps_name_loses_the_process_prefix():
    assert step_name("Vorhaben-Ablauf: Freigabe", "Vorhaben-Ablauf") == "Freigabe"
    assert step_name("Freigabe", "Vorhaben-Ablauf") == "Freigabe"
    assert step_name("Vorhaben-Ablauf: ", "Vorhaben-Ablauf") == "Vorhaben-Ablauf:"


# --- not completely described ----------------------------------------------------------------------------------------


def test_a_step_without_an_order_leaves_the_line_undrawn(caplog):
    core = ProcessCore()
    core.facts[step_id(4)] = [f for f in core.facts[step_id(4)] if f["label"] != "order"]
    caplog.set_level(logging.DEBUG, logger="brain_views")
    line = read_line(core, INSTANCE)
    assert line.in_process and not line.complete and line.main == ()
    assert any(PROCESS in r.getMessage() and "order_missing" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("value", ["vier", "", "4.5", None, True])
def test_an_order_that_is_no_whole_number_leaves_the_line_undrawn(value):
    core = ProcessCore()
    core.facts[step_id(4)][0]["value"] = value
    assert not read_line(core, INSTANCE).complete


def test_an_edge_that_cannot_be_oriented_leaves_the_line_undrawn(caplog):
    core = ProcessCore()
    core.facts[step_id(9)][0]["value"] = "8"                # two steps of order 8, joined by an edge
    caplog.set_level(logging.DEBUG, logger="brain_views")
    assert not read_line(core, INSTANCE).complete
    assert any("order_tie" in r.getMessage() or "edge_against_order" in r.getMessage() for r in caplog.records)
    looped = ProcessCore()
    looped.edges.append({"id": "loop", "node_lo": step_id(3), "node_hi": step_id(3), "relation": "transitions_to"})
    assert not read_line(looped, INSTANCE).complete


def test_a_current_step_outside_the_process_leaves_the_line_undrawn():
    core = ProcessCore()
    core.edges = [e for e in core.edges if not (e["relation"] == "has_step" and step_id(6) in (e["node_lo"],
                                                                                                e["node_hi"]))]
    assert not read_line(core, INSTANCE).complete


# --- since when, and who ----------------------------------------------------------------------------------------------


def test_since_comes_from_the_last_open_entry_of_the_step_history():
    assert read_line(ProcessCore(), INSTANCE).since == date(2026, 11, 9)
    core = ProcessCore()
    core.facts[INSTANCE][1]["value"] = json.dumps([["a", "2026-10-01", "2026-10-05"], ["b", "2026-10-05", None],
                                                   ["c", "2026-11-16T08:00:00Z", None]])
    assert read_line(core, INSTANCE).since == date(2026, 11, 16)


def test_without_an_open_history_entry_since_is_the_current_steps_valid_from():
    core = ProcessCore()
    core.facts[INSTANCE][1]["value"] = json.dumps([["a", "2026-10-01", "2026-10-05"]])
    core.facts[INSTANCE][0]["valid_from"] = "2026-11-02"
    assert read_line(core, INSTANCE).since == date(2026, 11, 2)
    core.facts[INSTANCE] = [f for f in core.facts[INSTANCE] if f["label"] != "step_history"]
    assert read_line(core, INSTANCE).since == date(2026, 11, 2)
    core.facts[INSTANCE] = []
    assert read_line(core, INSTANCE).since is None


def test_an_owner_key_is_not_shown_and_a_name_is():
    assert read_line(ProcessCore(), INSTANCE).who is None
    core = ProcessCore()
    core.facts[INSTANCE][2]["value"] = "Erfundene Person"
    assert read_line(core, INSTANCE).who == "Erfundene Person"


# --- the edges route and its fallback ------------------------------------------------------------------------------


def _paths(core):
    return [c for c in core.calls if c[0] == "graph_get_many" and c[1] == "/edges"]


def test_the_edges_route_is_used_when_it_honours_its_filters():
    core = ProcessCore()
    line = read_line(core, INSTANCE)
    assert line.complete and len(line.main) == 10
    assert ("graph_get_many", "/edges", (("node_id", PROCESS), ("type", "has_step"))) in core.calls
    asked = {params for _, _, params in _paths(core)}
    assert (("node_id", step_id(6)), ("type", "transitions_to")) in asked
    assert (("node_id", step_id(6)), ("type", "reworks_to")) in asked
    assert [c for c in core.calls if c[0] == "graph_neighbors"] == [("graph_neighbors", INSTANCE, 1, True)]
    assert [c[0] for c in core.calls].count("graph_facts_batch") == 1


@pytest.mark.parametrize("route", ["404", "unfiltered"])
def test_a_404_or_an_unfiltered_answer_falls_back_to_the_process_neighbours(route, caplog):
    core = ProcessCore(edges_route=route)
    caplog.set_level(logging.DEBUG, logger="brain_views")
    line = read_line(core, INSTANCE)
    assert line.complete and _names(line.main) == STEP_NAMES[:10] and line.position == 6
    assert ("graph_neighbors", PROCESS, 1, True) in core.calls
    assert len(_paths(core)) == 1                              # the first answer was enough to throw it away
    reason = "edges_404" if route == "404" else "edges_unfiltered"
    assert any(reason in r.getMessage() for r in caplog.records)


def test_an_answer_with_another_relation_is_thrown_away_too():
    core = ProcessCore()
    honest = core._edges

    def sloppy(params):
        status, payload = honest(params)
        if params.get("type") == "reworks_to":       # a Core that honours the node but not the relation
            payload["edges"] = [dict(e) for e in core.edges if params["node_id"] in (e["node_lo"], e["node_hi"])]
        return status, payload

    core._edges = sloppy
    line = read_line(core, INSTANCE)
    assert line.complete and ("graph_neighbors", PROCESS, 1, True) in core.calls


def test_more_steps_than_the_round_reads_fall_back_to_the_process_neighbours():
    many = MAX_STEP_READS + 5
    core = ProcessCore(steps=many, transitions=[(n, n + 1) for n in range(1, many)], reworks=[], current=28)
    line = read_line(core, INSTANCE)
    assert line.complete and len(line.main) == many and line.position == 28
    assert ("graph_neighbors", PROCESS, 1, True) in core.calls
    assert len(_paths(core)) == 1


def test_an_edges_route_that_fails_otherwise_is_unavailable():
    core = ProcessCore()
    core._edges = lambda params: (500, {"status": "error"})
    with pytest.raises(Unavailable):
        read_line(core, INSTANCE)


# --- not in a process -----------------------------------------------------------------------------------------------


def test_a_thing_without_in_state_or_instance_of_is_in_no_process():
    core = ProcessCore()
    core.edges = [e for e in core.edges if not (INSTANCE in (e["node_lo"], e["node_hi"]) and e["relation"] == "in_state")]
    line = read_line(core, INSTANCE)
    assert not line.in_process and line.name == "Vorhaben Archiv 2026"
    assert [c[0] for c in core.calls if c[0] in ("graph_facts_batch",)] == []
    core = ProcessCore()
    core.edges = [e for e in core.edges if not (INSTANCE in (e["node_lo"], e["node_hi"])
                                                and e["relation"] == "instance_of")]
    assert not read_line(core, INSTANCE).in_process


def test_two_current_steps_or_two_processes_are_no_process():
    core = ProcessCore()
    core.edges.append(edge(INSTANCE, step_id(7), "in_state"))
    assert not read_line(core, INSTANCE).in_process


def test_a_process_or_step_of_another_kind_is_no_process():
    core = ProcessCore()
    core.nodes[PROCESS]["node_type_id"] = T_PLAN
    assert not read_line(core, INSTANCE).in_process


def test_a_hidden_or_missing_thing_is_not_found():
    with pytest.raises(NotFound):
        read_line(ProcessCore(), "6f1c2c1e-0000-4000-8000-000000000404")
    with pytest.raises(NotFound):
        read_line(ProcessCore(), "../x")


# --- the reads -------------------------------------------------------------------------------------------------------


def test_every_read_is_a_per_thing_read():
    core = ProcessCore()
    read_line(core, INSTANCE)
    kinds = {c[0] for c in core.calls}
    assert kinds <= {"graph_node", "graph_neighbors", "graph_get_many", "graph_facts_batch"}
    for call in core.calls:
        if call[0] == "graph_get_many":
            assert call[1] == "/edges" and dict(call[2]).get("node_id") or re.fullmatch(
                r"/(node-types|nodes)/[0-9a-f-]{36}", call[1]), call
    # one round of kind reads, at most 21 (the thing's own and twenty)
    assert sum(1 for c in core.calls if c[0] == "graph_get_many" and c[1].startswith("/node-types/")) <= 21


def test_the_module_logs_no_name_or_fact(caplog):
    caplog.set_level(logging.DEBUG, logger="brain_views")
    for core in (ProcessCore(), ProcessCore(edges_route="404"), ProcessCore(current=11)):
        read_line(core, INSTANCE)
    broken = ProcessCore()
    broken.facts[step_id(3)] = []
    read_line(broken, INSTANCE)
    for record in caplog.records:
        line = record.getMessage()
        for word in ("Vorhaben", "Freigabe", "Erfunden", "Archiv", "2026-11"):
            assert word not in line, line

