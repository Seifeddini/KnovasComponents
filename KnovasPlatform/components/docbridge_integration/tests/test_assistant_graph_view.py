"""<<Im Wissensgraphen>>: what the assistant chooses to show, and the read nodes beside a cited one (the founder,
2026-11-28: "the graph is somewhat not easy to understand. Can you enable that the agent could show and hide edges
and nodes ... Maybe even name the edges and nodes in the visible graph?").

Claude may add one line ``{"graph": {...}}`` to an answer of <<Wissensgraph>>; ``answer_graph.curate`` keeps only
node and count passages of this answer and only edges the graph holds, cleans labels and the caption; it is no
statement and changes no check. The fallback of ``build_graph`` draws the read nodes linked to a cited node. The
fake Core and the fake Claude are those of test_assistant_brain_graph.py. This file is ASCII-only.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from assistant import answer_graph as ag
from assistant import brain_graph, check
from assistant.brain_graph import GraphReply
from assistant.service import Passage, Sentence
from test_assistant_brain_graph import (QUESTION, FakeAnthropic, FakeCore, _page_app, calls, find_then_count,  # noqa: F401
                                        lines, message, no_real_anthropic, results_of, service, session_for, text)

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"
GRAPH_JS = WEB / "static" / "js" / "ask_graph.js"
TEMPLATE = WEB / "templates" / "ask.html"


def cite(session, *numbers):
    by_number = {p.number: p for p in session.book.passages}
    return [replace(by_number[n], number=i) for i, n in enumerate(numbers, start=1)]


def by_id(graph):
    return {node["id"]: node for node in graph["nodes"]}


def edge_set(graph):
    return {(e["from"], e["relation"], e["to"]) for e in graph["edges"]}


# --- the read nodes beside a cited one ----------------------------------------------------------------------------


def test_one_cited_node_and_the_read_nodes_linked_to_it_make_a_graph():
    session = session_for(FakeCore())
    session.find_nodes("Huber Bau")                  # 1: p-2, a short passage
    session.read_node("akte-1")                      # 2: read, linked to p-2 (mandant)
    session.read_node("re-1")                        # 3: read, linked to akte-2 only
    graph = ag.build_graph(session, cite(session, 1))
    assert graph is not None
    assert set(by_id(graph)) == {"p-2", "akte-1"}                       # re-1: not linked to the cited node
    assert ("akte-1", "mandant", "p-2") in edge_set(graph)
    # the name rule holds: akte-1's name stands in no cited passage, so it keeps its type only
    assert by_id(graph)["akte-1"] == {"id": "akte-1", "name": "", "type": "Akte", "cited": False}


def test_a_read_node_whose_name_the_cited_passage_shows_keeps_it():
    session = session_for(FakeCore())
    session.read_node("akte-1")                      # 1: its connections name p-2 and p-1
    session.find_nodes("Huber Bau")                  # 2: p-2, read
    graph = ag.build_graph(session, cite(session, 1))
    assert by_id(graph)["p-2"]["name"] == "Huber Bau GmbH" and by_id(graph)["p-2"]["cited"] is False


def test_read_nodes_also_join_a_graph_that_had_one_already_and_unlinked_ones_stay_out():
    session = session_for(FakeCore())
    session.read_node("akte-2")                      # 1
    session.read_node("p-3")                         # 2
    session.read_node("re-1")                        # 3: rechnung_von akte-2
    session.read_node("re-2")                        # 4: rechnung_von akte-1, not cited
    graph = ag.build_graph(session, cite(session, 1, 2))
    assert set(by_id(graph)) == {"akte-2", "p-3", "re-1"}
    assert ("re-1", "rechnung_von", "akte-2") in edge_set(graph)


# --- the selection ------------------------------------------------------------------------------------------------


def selection_session():
    session = session_for(FakeCore())
    session.read_node("akte-1")                      # 1
    session.read_node("p-1")                         # 2
    session.read_node("p-2")                         # 3
    session.read_node("ma-2")                        # 4: no edge to any of them
    passages = cite(session, 1, 2)
    return session, passages, ag.build_graph(session, passages)


SPEC = {"focus": 1, "nodes": [1, 2, 3, 99, "x"],
        "edges": [{"from": 2, "to": 1, "label": "vertreten gegen"},
                  {"from": 1, "to": 3, "label": "Mandantin\x00\n\u202e  und mehr als dreissig Zeichen lang"},
                  {"from": 2, "to": 3, "label": "erfunden"},
                  {"from": 1, "to": 99, "label": "nirgends"}],
        "caption": "Die Akte und ihre Parteien. " * 10}


def test_a_selection_keeps_only_passages_of_this_answer_and_edges_the_graph_holds():
    session, passages, graph = selection_session()
    curated, dropped = ag.curate(session, graph, [SPEC], passages)
    assert dropped == 0
    view = curated["view"]
    assert view["focus"] == "akte-1" and view["nodes"] == ["akte-1", "p-1", "p-2"]
    # the real relation and direction, whatever way Claude named it; the invented edge is dropped
    assert [(e["from"], e["relation"], e["to"]) for e in view["edges"]] == [("akte-1", "gegenpartei", "p-1"),
                                                                            ("akte-1", "mandant", "p-2")]
    assert view["edges"][0]["label"] == "vertreten gegen"
    label = view["edges"][1]["label"]
    assert len(label) <= ag.MAX_LABEL_CHARS and "\x00" not in label and "\n" not in label and "\u202e" not in label
    assert label.startswith("Mandantin und mehr")
    assert len(view["caption"]) <= ag.MAX_CAPTION_CHARS
    # a node passage not yet drawn joins the full graph as a read node
    assert by_id(curated)["p-2"]["cited"] is False
    assert "view" not in graph                                         # the built graph itself is not changed


def test_a_malformed_or_empty_selection_is_dropped_and_counted():
    session, passages, graph = selection_session()
    for spec in ("x", None, {"nodes": "1"}, {"nodes": [1]}, {"nodes": [99, 100]}, {"nodes": [1, 1]}):
        curated, dropped = ag.curate(session, graph, [spec], passages)
        assert curated == graph and dropped == 1, spec
    curated, dropped = ag.curate(session, graph, ["x", SPEC, SPEC], passages)
    assert curated["view"]["nodes"] == ["akte-1", "p-1", "p-2"] and dropped == 2
    assert ag.curate(session, graph, [], passages) == (graph, 0)


def test_the_graph_line_is_no_statement_and_changes_no_check():
    numbered = {1: "Akte: 2024-017 Huber gegen Rueegg\nstatus: offen"}
    statement = json.dumps({"statement": "Die Akte ist offen.", "source": 1, "quote": "Huber gegen Rueegg status"})
    with_line = statement + "\n" + json.dumps({"graph": SPEC})
    assert check.check_answer(with_line, numbered) == check.check_answer(statement, numbered)
    assert check.removed_statements(with_line, numbered) == []
    assert ag.graph_specs(with_line) == [SPEC]
    assert not check.is_graph_spec({"graph": {}, "statement": "x"}) and not check.is_graph_spec({"graph": 1, "reply": "x"})


def test_a_selection_survives_storing_and_a_broken_one_is_left_out():
    session, passages, graph = selection_session()
    curated, _ = ag.curate(session, graph, [SPEC], passages)
    assert ag.clean_graph(curated)["view"] == curated["view"]
    broken = dict(curated, view={"nodes": ["akte-1", "nowhere"], "edges": []})
    assert "view" not in ag.clean_graph(broken) and ag.clean_graph(broken)["nodes"]
    older = {"nodes": curated["nodes"], "edges": curated["edges"]}
    assert "view" not in ag.clean_graph(older)


def test_stored_with_the_answer_and_drawn_with_its_caption():
    from web_interface.assistant_routes import shown_graph, stored_answer, stored_reply

    session, passages, graph = selection_session()
    curated, _ = ag.curate(session, graph, [dict(SPEC, caption="<b>Die Akte</b> und ihre Parteien.")], passages)
    reply = GraphReply(status="answered", sentences=[Sentence(text="Die Akte ist offen.", refs=(1,), quote="x y z")],
                       passages=passages, graph=curated)
    text, references = stored_answer(reply)
    back = stored_reply(SimpleNamespace(text=text, references=references))
    assert back.graph["view"] == curated["view"]
    with _page_app(False).test_request_context("/"):
        shown, as_json = shown_graph(back.graph)
    assert shown["view"]["caption"] == "<b>Die Akte</b> und ihre Parteien."
    assert json.loads(as_json)["view"]["edges"][0]["label"] == "vertreten gegen"


def test_the_template_shows_the_caption_as_text():
    template = TEMPLATE.read_text(encoding="utf-8")
    assert ('{%- if t.graph.view and t.graph.view.caption %}<p class="ask-graph-caption" dir="auto">Auswahl des '
            'Assistenten: {{ t.graph.view.caption }}</p>{% endif %}') in template
    assert "|safe" not in template


# --- the loop ------------------------------------------------------------------------------------------------------


def test_claude_chooses_the_selection_in_the_loop_and_the_log_names_nothing(caplog):
    caplog.set_level(logging.INFO, logger="assistant.brain_graph")

    def answer(counted):
        number = counted["count_passage"]["passage"]
        return lines({"statement": "Dr. Markus Baer fuehrt 2 offene Akten: 2024-017 und 2024-021.", "source": number,
                      "quote": "Anzahl der Treffer: 2 Treffer: 2024-017 Huber gegen Rueegg"},
                     {"graph": {"focus": number, "nodes": [number, 1],
                                "edges": [{"from": 1, "to": number, "label": "verantwortlich fuer"}],
                                "caption": "Die offenen Akten von Dr. Markus Baer."}})

    reply = service(FakeAnthropic(*find_then_count(answer))).answer(QUESTION)
    assert reply.status == "answered" and reply.removed == 0 and len(reply.sentences) == 1
    view = reply.graph["view"]
    assert view["nodes"] == ["abfrage:1", "ma-1"] and view["focus"] == "abfrage:1"
    assert view["edges"] == [{"from": "ma-1", "to": "abfrage:1", "relation": "verantwortlich",
                              "label": "verantwortlich fuer"}]
    assert view["caption"] == "Die offenen Akten von Dr. Markus Baer."
    line = [r.getMessage() for r in caplog.records if "brain graph status=" in r.getMessage()][-1]
    assert "graph_view=2 graph_view_default=0 graph_spec_dropped=0" in line
    for secret in ("verantwortlich fuer", "offenen Akten", "Markus"):
        assert secret not in line


def test_a_broken_graph_line_in_the_loop_is_counted_and_the_answer_stands(caplog):
    caplog.set_level(logging.INFO, logger="assistant.brain_graph")

    def answer(counted):
        number = counted["count_passage"]["passage"]
        return lines({"statement": "Dr. Markus Baer fuehrt 2 offene Akten: 2024-017 und 2024-021.", "source": number,
                      "quote": "Anzahl der Treffer: 2 Treffer: 2024-017 Huber gegen Rueegg"},
                     {"graph": {"nodes": "alle"}})

    reply = service(FakeAnthropic(*find_then_count(answer))).answer(QUESTION)
    assert reply.status == "answered" and reply.removed == 0 and "view" not in reply.graph
    line = [r.getMessage() for r in caplog.records if "brain graph status=" in r.getMessage()][-1]
    assert "graph_view=0 graph_view_default=0 graph_spec_dropped=1" in line


def test_the_instruction_teaches_the_graph_line():
    instruction = brain_graph.GRAPH_SYSTEM_INSTRUCTION
    assert '{"graph": {"focus": 3, "nodes": [3, 5, 8], "edges": [{"from": 3, "to": 5, "label": "..."}], ' in instruction
    assert "usually 3 to 7, at most 8, never all nodes of a larger graph" in instruction
    assert "the one in the middle" in instruction and "label every one" in instruction
    assert "This line is no statement" in instruction and "vertreten gegen" in instruction


# --- the script -----------------------------------------------------------------------------------------------------


def test_the_script_switches_between_the_selection_and_the_whole_graph_and_toggles_the_keys():
    source = GRAPH_JS.read_text(encoding="ascii")
    for words in ("'Alles zeigen'", "'Auswahl zeigen'", "aria-pressed", "'ask-graph-key'", "'Verbindungen:'",
                  "graph.view", "(Relation: ", "scene.reset()", "hide: function (types, relations)"):
        assert words in source, words
    # a toggle is a button: keyboard operable without a handler of its own
    assert re.search(r"var button = document\.createElement\('button'\);\s*button\.type = 'button';", source)


# --- a selection is a selection (the founder after the live deploy, 2026-11-28: all 11 nodes were chosen) ----------


ALL = ["ma-1", "ma-2", "akte-1", "akte-2", "akte-3", "akte-4", "p-1", "p-2", "p-3", "dok-1", "dok-2", "re-1", "re-2"]


def every_node_session():
    session = session_for(FakeCore())
    for node_id in ALL:                               # 1..13 in this order
        session.read_node(node_id)
    return session


def test_a_selection_of_every_node_is_cut_to_eight_focus_first_then_the_cited_in_citation_order():
    session = every_node_session()
    passages = cite(session, 4, 3, 7)                 # akte-2, akte-1, p-1 cited (reply numbers 1, 2, 3)
    graph = ag.build_graph(session, passages)
    spec = {"focus": 3, "nodes": list(range(1, 14)), "edges": [{"from": 3, "to": 7, "label": "gegen"}]}
    curated, dropped = ag.curate(session, graph, [spec], passages)
    view = curated["view"]
    assert dropped == 0 and len(view["nodes"]) == ag.MAX_VIEW_NODES == 8
    assert view["nodes"][:3] == ["akte-1", "akte-2", "p-1"]            # focus, then cited by passage number
    assert view["nodes"][3:] == ["ma-1", "ma-2", "akte-3", "akte-4", "p-2"]   # then Claude's list in its order
    assert view["focus"] == "akte-1" and view["by"] == ag.BY_ASSISTANT
    assert len(view["nodes"]) < len(curated["nodes"])


def test_without_a_line_a_larger_graph_gets_a_default_view_smaller_than_the_whole():
    nodes = [{"id": f"n{i}", "name": f"N{i}", "type": "T", "cited": i < 3, **({"passage": i + 1} if i < 3 else {})}
             for i in range(12)]
    edges = ([{"from": "n0", "to": f"n{i}", "relation": "r"} for i in range(1, 12)]
             + [{"from": "n5", "to": "n1", "relation": "s"}, {"from": "n5", "to": "n2", "relation": "s"}])
    graph = {"nodes": nodes, "edges": edges}
    view = ag.default_view(graph)
    assert view["by"] == ag.BY_DEFAULT and view["focus"] == "n0"
    assert view["nodes"][:3] == ["n0", "n1", "n2"] and "n5" in view["nodes"]   # a connector between cited ones
    assert 2 <= len(view["nodes"]) <= ag.MAX_VIEW_NODES and len(view["nodes"]) < len(nodes)
    assert all(e["label"] == "" and e["from"] in view["nodes"] and e["to"] in view["nodes"] for e in view["edges"])
    session = session_for(FakeCore())
    curated, dropped = ag.curate(session, graph, [], [])
    assert curated["view"] == view and dropped == 0
    curated, dropped = ag.curate(session, graph, ["kaputt"], [])
    assert curated["view"] == view and dropped == 1


def test_one_cited_node_gets_its_neighbours_in_the_default_view():
    nodes = [{"id": "c", "name": "C", "type": "T", "cited": True, "passage": 1}] + [
        {"id": f"r{i}", "name": "", "type": "T", "cited": False} for i in range(10)]
    edges = [{"from": "c", "to": f"r{i}", "relation": "r"} for i in range(10)]
    view = ag.default_view({"nodes": nodes, "edges": edges})
    assert view["nodes"][0] == "c" and len(view["nodes"]) == ag.DEFAULT_VIEW_MIN


def test_a_small_graph_gets_no_default_view():
    session = session_for(FakeCore())
    graph = {"nodes": [{"id": f"n{i}", "name": "", "type": "T", "cited": True, "passage": i + 1} for i in range(8)],
             "edges": []}
    assert ag.curate(session, graph, [], []) == (graph, 0)
    assert ag.default_view({"nodes": [], "edges": []}) is None


def test_the_default_view_is_stored_and_logged_as_a_count():
    graph = {"nodes": [{"id": f"n{i}", "name": "", "type": "T", "cited": i == 0, **({"passage": 1} if i == 0 else {})}
                       for i in range(10)],
             "edges": [{"from": "n0", "to": f"n{i}", "relation": "r"} for i in range(1, 10)]}
    view = ag.default_view(graph)
    assert ag.clean_graph(dict(graph, view=view))["view"] == view
    assert ag.clean_view(dict(view, by="elsewhere"), {n["id"]: n for n in graph["nodes"]})["by"] == ag.BY_ASSISTANT
