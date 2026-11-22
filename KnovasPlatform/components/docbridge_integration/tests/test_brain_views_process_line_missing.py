"""PMVP-32b: what the current step still needs («fehlt noch»), and the others in the same process («Alle N … in diesem
Ablauf», ``GET /ablauf/<node_id>/alle``).

1. The current step's ``requires`` entries over one hop: the thing's neighbours over that relation whose kind is the
   entry's ``to_type``; present ones listed (links to their dossiers, rows), a required entry with fewer than
   ``min_count`` marked «fehlt noch» with its type, a missing ``required: false`` entry not marked; longer paths left
   out with one line; the direction of a hop is not checked, and the card says so.
2. The count of the process's ``instance_of`` edges, from the edges route or the fallback; the link carries
   ``data-bv-more`` (the others) and opens the list: the others by name, filtered, 50 a page, each opening its own
   line; mark-up as text; no read per instance; the gate and the 404 as the line's.

The Core is ``ProcessCore`` of test_brain_views_process_line_read.py.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re

import pytest

from brain_views.process_line import read_instances, read_line
from test_brain_views_process_line_read import (INSTANCE, PROCESS, T_PERSON, T_PLAN, T_ROLE, ProcessCore, edge,
                                                step_id)

ROW_MARK = re.compile(r"\bdata-bv-row\b")
NODE_MARK = re.compile(r"\bdata-bv-node\b")
SCRIPT = "<script>alert(1)</script>"
PERSON = "6f1c2c1e-0000-4000-8000-000000327001"


def _requires(core, entries, step=6):
    core.facts[step_id(step)].append(core.fact(step_id(step), "requires", json.dumps(entries)))
    return core


def _hop(relation, to_type, *, type=None, required=True, min_count=1):
    return {"type": type or to_type, "required": required, "min_count": min_count,
            "path": [{"relation": relation, "direction": "out", "to_type": to_type}]}


def _two_hops():
    return {"type": "Person", "required": True, "min_count": 1,
            "path": [{"relation": "part_of", "direction": "out", "to_type": "Vorhaben"},
                     {"relation": "owned_by", "direction": "out", "to_type": "Person"}]}


def _add(core, name, type_id, relation, n):
    """Another neighbour of the instance, of this kind, over this relation."""
    other = f"6f1c2c1e-0000-4000-8000-00000033{n:04d}"
    core.nodes[other] = {"id": other, "name": name, "node_type_id": type_id}
    core.facts[other] = []
    core.edges.append(edge(INSTANCE, other, relation))
    return other


# --- 1: what is still missing (the read) -------------------------------------------------------------------------------


def test_one_kind_present_and_one_missing():
    core = _requires(ProcessCore(), [_hop("owned_by", "Person"), _hop("has_role", "Role", type="Rolle")])
    line = read_line(core, INSTANCE)
    person, role = line.requirements
    assert (person.type, person.present, person.missing) == ("Person", ((PERSON, "Erfundene Person"),), False)
    assert (role.type, role.present, role.missing, role.required) == ("Rolle", (), True, True)
    assert line.more_requirements is False


def test_a_neighbour_of_another_kind_or_over_another_relation_does_not_count():
    core = _requires(ProcessCore(), [_hop("owned_by", "Role")])
    _add(core, "Erfundene Rolle", T_ROLE, "has_role", 1)                  # the right kind, another relation
    assert read_line(core, INSTANCE).requirements[0].missing             # the person is owned_by, but no Role


def test_min_count_two_with_one_present_is_missing():
    core = _requires(ProcessCore(), [_hop("owned_by", "Person", min_count=2)])
    need = read_line(core, INSTANCE).requirements[0]
    assert need.missing and len(need.present) == 1 and need.min_count == 2
    _add(core, "Zweite Person", T_PERSON, "owned_by", 2)
    need = read_line(core, INSTANCE).requirements[0]
    assert not need.missing and [name for _, name in need.present] == ["Erfundene Person", "Zweite Person"]


def test_a_missing_optional_entry_is_not_required():
    need = read_line(_requires(ProcessCore(), [_hop("has_role", "Role", required=False)]), INSTANCE).requirements[0]
    assert need.missing and need.required is False


def test_a_longer_path_is_left_out_and_said_so():
    line = read_line(_requires(ProcessCore(), [_two_hops(), _hop("owned_by", "Person")]), INSTANCE)
    assert [r.type for r in line.requirements] == ["Person"] and line.more_requirements is True
    only = read_line(_requires(ProcessCore(), [_two_hops()]), INSTANCE)
    assert only.requirements == () and only.more_requirements is True


@pytest.mark.parametrize("value", ["kein JSON", json.dumps({"type": "x"}), json.dumps([1, "x", None]), ""])
def test_unreadable_requirements_are_none(value):
    core = ProcessCore()
    core.facts[step_id(6)].append(core.fact(step_id(6), "requires", value))
    line = read_line(core, INSTANCE)
    assert line.complete and line.requirements == ()


def test_only_the_current_steps_requirements_count():
    core = _requires(ProcessCore(), [_hop("has_role", "Role")], step=7)
    assert read_line(core, INSTANCE).requirements == ()


def test_the_direction_of_the_hop_is_not_checked():
    core = _requires(ProcessCore(), [dict(_hop("owned_by", "Person"), path=[{"relation": "owned_by",
                                                                             "direction": "in", "to_type": "Person"}])])
    assert not read_line(core, INSTANCE).requirements[0].missing


# --- 2: the others in the process (the read) -----------------------------------------------------------------------


def test_the_instances_are_counted_from_the_edges_route_and_from_the_fallback():
    core = ProcessCore()
    assert read_line(core, INSTANCE).instances == 4                      # this one and three others
    assert ("graph_get_many", "/edges", (("node_id", PROCESS), ("type", "instance_of"))) in core.calls
    for route in ("404", "unfiltered"):
        assert read_line(ProcessCore(edges_route=route), INSTANCE).instances == 4


def test_read_instances_lists_the_others_by_name_without_a_read_per_instance():
    core = ProcessCore()
    found = read_instances(core, INSTANCE)
    assert found.in_process and found.process_name == "Vorhaben-Ablauf"
    assert [name for _, name in found.others] == ["Vorhaben 0", "Vorhaben 1", "Vorhaben 2"]
    assert INSTANCE not in [other for other, _ in found.others]
    assert [c[1] for c in core.calls if c[0] == "graph_neighbors"] == [INSTANCE, PROCESS]
    assert [c for c in core.calls if c[0] == "graph_node"] == [("graph_node", INSTANCE)]
    assert not any(c[0] == "graph_facts_batch" for c in core.calls)


def test_a_thing_in_no_process_has_no_others():
    assert not read_instances(ProcessCore(), PERSON).in_process


# --- the pages ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def install(fake_graph, monkeypatch):
    monkeypatch.setenv("BRAIN_VIEWS_ENABLED", "true")
    refused = []

    def put(core):
        for name in ("graph_node", "graph_neighbors", "graph_get_many", "graph_facts_batch"):
            monkeypatch.setattr(fake_graph, name, getattr(core, name), raising=False)
        for name in ("graph_export", "graph_edges", "graph_nodes", "graph_node_types"):
            def refuse(*args, _name=name, **kwargs):
                refused.append(_name)
                raise AssertionError(f"whole-tenant read: {_name}")
            monkeypatch.setattr(fake_graph, name, refuse, raising=False)
        core.refused = refused
        return core

    return put


def _checks(html):
    """(mark, text, end) of each row of the checklist in «Jetzt»."""
    block = html[html.index('<div class="bv-checks'):html.index('class="bv-checks-note')]
    out = []
    for row in re.findall(r'<div class="k-rowline">(.*?)</div>', block, re.S):
        mark = "ok" if "bv-check-mark--ok" in row else "missing"
        text = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", row.split("</span>\n", 1)[0]))).strip()
        end = "fehlt noch" if "k-tag--warn" in row else ("wahlweise" if "wahlweise" in row else "")
        out.append((mark, text, end))
    return out


def test_the_card_lists_what_is_there_and_marks_what_is_missing(install, member_client):
    install(_requires(ProcessCore(), [_hop("owned_by", "Person"), _hop("has_role", "Role", type="Rolle"),
                                      _hop("has_role", "Role", type="Vertretung", required=False)]))
    html = member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)
    assert _checks(html) == [("ok", "✓ Erfundene Person", ""), ("missing", "Rolle", "fehlt noch"),
                             ("missing", "Vertretung", "wahlweise")]
    assert f'href="/dossier/{PERSON}">Erfundene Person</a>' in html
    assert html.count(">fehlt noch<") == 1
    assert "in welche Richtung die Verbindung zeigt, wird nicht geprüft" in html
    assert "Weitere Voraussetzungen werden hier nicht geprüft." not in html


def test_min_count_shows_how_many_of_how_many(install, member_client):
    install(_requires(ProcessCore(), [_hop("owned_by", "Person", min_count=2)]))
    html = member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)
    assert ("missing", "Person (1 von 2)", "fehlt noch") in _checks(html)


def test_a_longer_path_is_one_line(install, member_client):
    install(_requires(ProcessCore(), [_two_hops()]))
    html = member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)
    assert "Weitere Voraussetzungen werden hier nicht geprüft." in html and "bv-checks bv-list" not in html
    install(_requires(ProcessCore(), [_two_hops(), _hop("owned_by", "Person")]))
    html = member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)
    assert html.count("Weitere Voraussetzungen werden hier nicht geprüft.") == 1 and _checks(html)


def test_without_requirements_there_is_no_checklist(install, member_client):
    install(ProcessCore())
    html = member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)
    assert "bv-checks" not in html and "fehlt noch" not in html


def test_the_line_counts_all_and_its_link_carries_the_others(install, member_client):
    install(ProcessCore())
    html = member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)
    link = re.search(r'<a class="k-link" data-bv-more="(\d+)" href="([^"]+)">([^<]*)</a>', html)
    assert link and link.group(1) == "3" and link.group(3) == "Alle 4 Einträge in diesem Ablauf →"
    assert html_lib.unescape(link.group(2)) == f"/ablauf/{INSTANCE}/alle"


def test_alone_in_its_process_there_is_no_link(install, member_client):
    core = ProcessCore()
    core.edges = [e for e in core.edges if not (e["relation"] == "instance_of" and INSTANCE not in
                                               (e["node_lo"], e["node_hi"]))]
    install(core)
    html = member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)
    assert "data-bv-more" not in html and "Alle " not in html


def test_the_list_of_the_others(install, member_client):
    core = install(ProcessCore())
    core.calls.clear()
    response = member_client.get(f"/ablauf/{INSTANCE}/alle")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    rows = re.findall(r'<div class="bv-thing" data-bv-row><a class="k-link k-link--dotted" href="([^"]+)">([^<]*)</a>',
                      html)
    assert [name for _, name in rows] == ["Vorhaben 0", "Vorhaben 1", "Vorhaben 2"]
    assert rows[0][0] == "/ablauf/6f1c2c1e-0000-4000-8000-000000328000"
    assert len(NODE_MARK.findall(html)) == 1 and len(ROW_MARK.findall(html)) == 3
    assert "3 weitere neben «Vorhaben Archiv 2026»" in html and 'Ablauf «Vorhaben-Ablauf»' in html
    assert f'href="/ablauf/{INSTANCE}">Zurück zum Ablauf</a>' in html
    assert not any(c[0] == "graph_node" and c[1] != INSTANCE for c in core.calls)


def _many(n):
    core = ProcessCore()
    for i in range(n):
        other = f"6f1c2c1e-0000-4000-8000-00000034{i:04d}"
        core.nodes[other] = {"id": other, "name": f"Plan {i:03d}", "node_type_id": T_PLAN}
        core.facts[other] = []
        core.edges.append(edge(other, PROCESS, "instance_of"))
    return core


def test_the_list_pages_and_filters(install, member_client):
    install(_many(120))
    html = member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)
    assert 'data-bv-more="123"' in html and "Alle 124 Einträge in diesem Ablauf" in html
    html = member_client.get(f"/ablauf/{INSTANCE}/alle").get_data(as_text=True)
    assert len(ROW_MARK.findall(html)) == 50 and "1–50 von 123 · Seite 1 von 3" in html
    assert f'href="/ablauf/{INSTANCE}/alle?seite=2">Weitere 50</a>' in html
    html = member_client.get(f"/ablauf/{INSTANCE}/alle?seite=9").get_data(as_text=True)
    assert len(ROW_MARK.findall(html)) == 23
    html = member_client.get(f"/ablauf/{INSTANCE}/alle?filter=plan+01").get_data(as_text=True)
    names = re.findall(r'k-link--dotted" href="[^"]+">([^<]*)</a>', html)
    assert names == [f"Plan {n:03d}" for n in range(10, 20)]
    html = member_client.get(f"/ablauf/{INSTANCE}/alle?filter=nichts&sort=name-ab").get_data(as_text=True)
    assert "Kein Eintrag passt zu diesem Filter." in html
    html = member_client.get(f"/ablauf/{INSTANCE}/alle?sort=name-ab").get_data(as_text=True)
    assert re.findall(r'k-link--dotted" href="[^"]+">([^<]*)</a>', html)[0] == "Vorhaben 2"


def test_the_list_keeps_mark_up_as_text(install, member_client):
    core = ProcessCore()
    other = "6f1c2c1e-0000-4000-8000-000000328000"
    core.nodes[other]["name"] = f"Vorhaben {SCRIPT}"
    core.nodes[PROCESS]["name"] = f"Ablauf {SCRIPT}"
    install(core)
    html = member_client.get(f"/ablauf/{INSTANCE}/alle?filter=%3Cscript%3E").get_data(as_text=True)
    assert SCRIPT not in html and "&lt;script&gt;" in html
    for href in re.findall(r'href="([^"]*)"', html):
        assert html_lib.unescape(href).startswith("/") and not href.startswith("//"), href


def test_the_list_has_the_lines_gate_and_404(install, member_client, anon_client, monkeypatch):
    core = install(ProcessCore())
    assert anon_client.get(f"/ablauf/{INSTANCE}/alle").status_code == 302
    missing = "6f1c2c1e-0000-4000-8000-000000000404"
    assert member_client.get(f"/ablauf/{missing}/alle").status_code == 404
    html = member_client.get(f"/ablauf/{PERSON}/alle").get_data(as_text=True)
    assert "Für diesen Eintrag ist kein Ablauf hinterlegt." in html
    monkeypatch.delenv("BRAIN_VIEWS_ENABLED")
    core.calls.clear()
    assert member_client.get(f"/ablauf/{INSTANCE}/alle").status_code == 404 and core.calls == []
    assert core.refused == []


def test_no_log_line_carries_a_name(install, member_client, caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="web_interface")
    caplog.set_level(logging.DEBUG, logger="brain_views")
    install(_requires(ProcessCore(), [_hop("owned_by", "Person"), _hop("has_role", "Role")]))
    member_client.get(f"/ablauf/{INSTANCE}")
    member_client.get(f"/ablauf/{INSTANCE}/alle?filter=Filterwort")
    for line in [record.getMessage() for record in caplog.records]:
        for word in ("Vorhaben", "Erfunden", "Filterwort", "Role"):
            assert word not in line, line
