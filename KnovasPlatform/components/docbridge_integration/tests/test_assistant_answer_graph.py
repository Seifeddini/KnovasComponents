"""PMVP-20 brain-graph: the block "Im Wissensgraphen" under an answer of <<Wissensgraph>> (assistant.answer_graph,
assistant_routes.shown_graph, ask.html, static/js/ask_graph.js).

Building the graph from what the answer read (cited nodes, the node a count was asked about, a small count's
matches, the single hops between them, the edges among them; a count as one summary node; the caps; only names the
person was shown), storing it with the answer and reading it back, and the page: the block under a stored answer of
<<Wissensgraph>> and nowhere else. The fake Core and the fake Claude are those of test_assistant_brain_graph.py; the
chats use the real store on the test database. No Core and no model is reached. This file is ASCII-only.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant import answer_graph as ag
from assistant import brain_graph, graph_reads as gr
from assistant.brain_graph import GraphReply
from assistant.brain_tools import ToolsReply
from assistant.service import Passage, Reply, Sentence
from test_assistant_brain_graph import (QUESTION, FakeAnthropic, FakeCore, _page_app, count_statement,  # noqa: F401
                                        find_then_count, no_real_anthropic, service, session_for)

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"
GRAPH_JS = WEB / "static" / "js" / "ask_graph.js"


def cite(session, *numbers):
    """The reply's passages for the book's passages ``numbers``: only those, renumbered 1..k (brain_tools.cited)."""
    by_number = {p.number: p for p in session.book.passages}
    return [replace(by_number[n], number=i) for i, n in enumerate(numbers, start=1)]


def by_id(graph):
    return {node["id"]: node for node in graph["nodes"]}


def edge_set(graph):
    return {(e["from"], e["relation"], e["to"]) for e in graph["edges"]}


# --- building the graph ------------------------------------------------------------------------------------------


def test_an_answered_graph_reply_carries_its_count_the_node_asked_about_and_the_small_counts_matches():
    reply = service(FakeAnthropic(*find_then_count(count_statement))).answer(QUESTION)
    assert reply.status == "answered" and reply.graph is not None
    nodes = by_id(reply.graph)
    count = nodes["abfrage:1"]
    assert count == {"id": "abfrage:1", "name": "2 \u00d7 Akte", "type": "Akte", "cited": True, "passage": 1,
                     "kind": "count", "query": count["query"]}
    assert count["query"].startswith("verbunden mit Mitarbeiter")
    assert nodes["ma-1"] == {"id": "ma-1", "name": "Dr. Markus Baer", "type": "Mitarbeiter", "cited": False}
    assert nodes["akte-1"]["name"] == "2024-017 Huber gegen Rueegg" and nodes["akte-1"]["cited"] is False
    assert set(nodes) == {"abfrage:1", "ma-1", "akte-1", "akte-2"}             # not the closed matter, no party
    assert edge_set(reply.graph) == {("ma-1", "verantwortlich", "abfrage:1"),
                                     ("akte-1", "verantwortlich", "ma-1"), ("akte-2", "verantwortlich", "ma-1")}


def test_cited_nodes_come_first_with_their_passage_numbers_and_the_edges_between_them():
    session = session_for(FakeCore())
    session.read_node("akte-1")                                      # 1
    session.read_node("ma-1")                                        # 2
    graph = ag.build_graph(session, cite(session, 2, 1))
    assert [(n["id"], n["cited"], n["passage"]) for n in graph["nodes"][:2]] == [("ma-1", True, 1),
                                                                                  ("akte-1", True, 2)]
    assert ("akte-1", "verantwortlich", "ma-1") in edge_set(graph)


def test_a_single_hop_between_cited_nodes_is_drawn_and_named_only_when_a_cited_passage_shows_its_name():
    session = session_for(FakeCore())
    session.find_nodes("Huber Bau")                                  # 1: p-2, a short passage
    session.find_nodes("Klage", type="Dokument")                     # 2: dok-1, a short passage
    graph = ag.build_graph(session, cite(session, 1, 2))
    nodes = by_id(graph)
    assert nodes["akte-1"] == {"id": "akte-1", "name": "", "type": "Akte", "cited": False}   # never shown
    assert edge_set(graph) == {("akte-1", "mandant", "p-2"), ("dok-1", "dokument_von", "akte-1")}

    session.read_node("p-2")                                         # 3: with its connections, the matter's name
    graph = ag.build_graph(session, cite(session, 3, 2))
    assert by_id(graph)["akte-1"]["name"] == "2024-017 Huber gegen Rueegg"


def test_a_node_joined_to_one_cited_node_only_is_left_out():
    session = session_for(FakeCore())
    session.read_node("akte-2")                                      # 1
    session.read_node("p-3")                                         # 2
    graph = ag.build_graph(session, cite(session, 1, 2))
    assert set(by_id(graph)) == {"akte-2", "p-3"}                    # ma-1, re-1: one cited neighbour each


class StarCore(FakeCore):
    """A hub with 97 leaves, and 40 nodes of a second type all joined to one another."""

    LEAVES = [f"leaf-{i:02d}" for i in range(97)]
    DENSE = [f"d-{i:02d}" for i in range(40)]

    def graph_read(self, path):
        if path == "/node-types":
            return 200, {"node_types": [{"id": "t-hub", "name": "Knoten"}, {"id": "t-leaf", "name": "Blatt"},
                                        {"id": "t-dense", "name": "Dicht"}]}
        if path == "/nodes":
            rows = [{"id": "hub", "name": "Mitte", "node_type_id": "t-hub"}]
            rows += [{"id": n, "name": f"Blatt {n}", "node_type_id": "t-leaf"} for n in self.LEAVES]
            rows += [{"id": n, "name": f"Dicht {n}", "node_type_id": "t-dense"} for n in self.DENSE]
            return 200, {"nodes": rows}
        if path == "/edges":
            edges = [{"node_lo": n, "node_hi": "hub", "relation": "haengt_an"} for n in self.LEAVES]
            edges += [{"node_lo": a, "node_hi": b, "relation": "kennt"}
                      for i, a in enumerate(self.DENSE) for b in self.DENSE[i + 1:]]
            return 200, {"edges": edges}
        if path.startswith("/nodes/") and path.endswith("/facts"):
            return 200, {"facts": []}
        return 404, {}

    def graph_facts_batch(self, ids):
        return 200, {"facts_by_node": {i: [] for i in ids}}


def test_a_large_count_is_one_summary_node_joined_to_the_node_it_was_asked_about():
    session = session_for(StarCore())
    counted = session.neighbours("hub", relation="haengt_an", direction="in")
    assert counted["count"] == 97
    graph = ag.build_graph(session, cite(session, counted["count_passage"]["passage"]))
    assert [(n["id"], n["name"], n["cited"]) for n in graph["nodes"]] == [("abfrage:1", "97 \u00d7 Blatt", True),
                                                                            ("hub", "Mitte", False)]
    assert graph["edges"] == [{"from": "hub", "to": "abfrage:1", "relation": "haengt_an"}]


def test_a_count_over_a_type_alone_is_no_graph_and_its_label_without_one_type_says_treffer():
    session = session_for(StarCore())
    counted = session.list_nodes("Blatt")
    assert ag.build_graph(session, cite(session, counted["count_passage"]["passage"])) is None   # one node
    assert ag.count_label(3, "") == "3 Treffer" and ag.count_label(0, "Akte") == "0 \u00d7 Akte"


def test_a_small_count_over_a_type_draws_its_matches_joined_to_it():
    session = session_for(FakeCore())
    counted = session.list_nodes("Rechnung")
    graph = ag.build_graph(session, cite(session, counted["count_passage"]["passage"]))
    assert set(by_id(graph)) == {"abfrage:1", "re-1", "re-2"}
    assert edge_set(graph) == {("abfrage:1", ag.MATCH_RELATION, "re-1"), ("abfrage:1", ag.MATCH_RELATION, "re-2")}


def test_at_most_thirty_nodes_and_sixty_edges_the_cited_first():
    session = session_for(StarCore())
    for node_id in StarCore.DENSE:
        session.read_node(node_id)                                   # 40 passages, 780 edges among them
    graph = ag.build_graph(session, cite(session, *range(1, 41)))
    assert len(graph["nodes"]) == ag.MAX_NODES == 30 and len(graph["edges"]) == ag.MAX_EDGES == 60
    assert all(n["cited"] for n in graph["nodes"]) and [n["passage"] for n in graph["nodes"]] == list(range(1, 31))
    ids = set(by_id(graph))
    assert all(e["from"] in ids and e["to"] in ids for e in graph["edges"])


def test_fewer_than_two_nodes_or_no_topology_is_no_graph():
    session = session_for(FakeCore())
    assert ag.build_graph(session, []) is None                       # nothing read
    session.read_node("akte-3")
    assert ag.build_graph(session, cite(session, 1)) is None         # one node, no count
    document = Passage(number=1, pointer="Akten/x.docx", title="x.docx", page=None, text="Text")
    assert ag.build_graph(session, [document]) is None


def test_no_graph_for_a_reply_that_is_not_answered_or_of_another_mode():
    no_answer = service(FakeAnthropic(*find_then_count(
        lambda c: count_statement(c, quote="Baer leitet zwei offene Mandate")))).answer(QUESTION)
    assert no_answer.status == "no_answer" and no_answer.graph is None
    for other in (Reply(status="answered"), ToolsReply(status="answered")):
        assert other.graph is None


def test_the_log_line_counts_the_graph_and_names_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    reply = service(FakeAnthropic(*find_then_count(count_statement))).answer(QUESTION)
    assert reply.graph is not None
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "graph_nodes=4 graph_edges=3" in logged
    for secret in ("Markus", "Baer", "2024-017", "Huber", "verantwortlich", "Akte"):
        assert secret not in logged, secret


def test_a_failure_while_drawing_leaves_the_answer_without_its_graph(monkeypatch):
    def fails(session, passages):
        raise RuntimeError("Dr. Markus Baer")

    monkeypatch.setattr(ag, "build_graph", fails)
    reply = service(FakeAnthropic(*find_then_count(count_statement))).answer(QUESTION)
    assert reply.status == "answered" and reply.graph is None


def test_the_code_names_no_type_or_relation_of_a_tenant():
    source = (Path(ag.__file__)).read_text(encoding="ascii")
    for word in ("Akte", "Mandant", "Partei", "Rechnung", "verantwortlich", "mandant", "Mitarbeiter"):
        assert word not in source, word


# --- stored and read back ----------------------------------------------------------------------------------------


GRAPH = {"nodes": [{"id": "akte-1", "name": "2024-017 Huber gegen Rueegg", "type": "Akte", "cited": True,
                    "passage": 1},
                   {"id": "ma-1", "name": "Dr. Markus Baer", "type": "Mitarbeiter", "cited": False},
                   {"id": "abfrage:2", "name": "2 \u00d7 Akte", "type": "Akte", "cited": True, "passage": 2,
                    "kind": "count", "query": "verbunden mit Mitarbeiter"}],
         "edges": [{"from": "akte-1", "to": "ma-1", "relation": "verantwortlich"},
                   {"from": "ma-1", "to": "abfrage:2", "relation": "verantwortlich"}]}
PASSAGES = [Passage(number=1, pointer="brain:akte-1", title="Akte: 2024-017 Huber gegen Rueegg", page=None,
                    text="Akte: 2024-017 Huber gegen Rueegg\nstatus: offen"),
            Passage(number=2, pointer="brain:ma-1", title="Abfrage: verbunden mit Mitarbeiter", page=None,
                    text="Abfrage im Wissensgraphen: verbunden mit Mitarbeiter\nAnzahl der Treffer: 2")]
SENTENCE = Sentence(text="Die Akte ist offen.", refs=(1,), quote="Huber gegen Rueegg status: offen")


def graph_reply(graph=GRAPH):
    return GraphReply(status="answered", sentences=[SENTENCE], passages=list(PASSAGES), graph=graph)


def stored(reply):
    from web_interface.assistant_routes import stored_answer

    text, references = stored_answer(reply)
    return text, SimpleNamespace(text=text, references=references)


def test_the_graph_is_stored_with_the_answer_and_read_back_as_it_was():
    from web_interface.assistant_routes import stored_reply

    text, message = stored(graph_reply())
    assert json.loads(text)["graph"] == GRAPH
    assert stored_reply(message).graph == GRAPH


def test_an_answer_without_a_graph_stores_none_and_an_older_answer_reads_back_without_one():
    from web_interface.assistant_routes import stored_reply

    for reply in (graph_reply(graph=None), Reply(status="answered", sentences=[SENTENCE], passages=list(PASSAGES))):
        text, message = stored(reply)
        assert "graph" not in json.loads(text)
        assert stored_reply(message).graph is None
    no_answer = GraphReply(status="no_answer", message="x", graph=GRAPH)
    assert "graph" not in json.loads(stored(no_answer)[0])


def test_a_stored_graph_not_in_the_stored_shape_draws_nothing_and_the_answer_stands():
    from web_interface.assistant_routes import stored_reply

    text, message = stored(graph_reply())
    data = json.loads(text)
    for broken in ("x", {"nodes": "x"}, {"nodes": [{"id": "a b", "name": "x"}, {"id": "c"}]},
                   {"nodes": [{"id": "a", "name": 3}, {"id": "b"}]}):
        data["graph"] = broken
        message.text = json.dumps(data)
        reply = stored_reply(message)
        assert reply.status == "answered" and reply.sentences and reply.graph is None, broken


def test_clean_graph_keeps_the_caps_and_drops_edges_to_unknown_nodes():
    many = {"nodes": [{"id": f"n{i}", "name": f"N\x00{i}", "type": "T", "cited": True, "passage": i + 1}
                      for i in range(40)],
            "edges": [{"from": f"n{i}", "to": f"n{j}", "relation": "r"} for i in range(12) for j in range(12)
                      if i != j] + [{"from": "n0", "to": "zz", "relation": "r"}]}
    cleaned = ag.clean_graph(many)
    assert len(cleaned["nodes"]) == ag.MAX_NODES and len(cleaned["edges"]) == ag.MAX_EDGES
    assert cleaned["nodes"][0]["name"] == "N0"                          # NUL removed
    assert all(e["to"] != "zz" for e in cleaned["edges"])
    assert ag.clean_graph({"nodes": [{"id": "a", "name": "A"}], "edges": []}) is None
    assert ag.clean_graph(None) is None


def test_shown_graph_links_a_node_to_its_dossier_while_the_brain_views_are_on():
    from web_interface.assistant_routes import shown_graph

    for views_on, first in ((True, "/dossier/akte-1"), (False, "")):
        with _page_app(views_on).test_request_context("/"):
            shown, as_json = shown_graph(GRAPH)
        assert [n["href"] for n in shown["nodes"]] == [first, first.replace("akte-1", "ma-1"), ""]
        assert [n["label"] for n in shown["nodes"]] == ["2024-017 Huber gegen Rueegg", "Dr. Markus Baer",
                                                        "2 \u00d7 Akte"]
        assert json.loads(as_json) == shown
    unnamed = {"nodes": [dict(GRAPH["nodes"][0]), {"id": "x-1", "name": "", "type": "Akte", "cited": False}],
               "edges": []}
    with _page_app(False).test_request_context("/"):
        assert shown_graph(unnamed)[0]["nodes"][1]["label"] == "Akte"


# --- the page ----------------------------------------------------------------------------------------------------


@pytest.fixture
def ask_service(workbench_app, monkeypatch):
    from test_assistant_chats_routes import FakeService
    from web_interface import assistant_routes

    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")
    fake = FakeService(graph_reply())
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    return fake


def _chat_page(client):
    from test_assistant_chats_routes import _ask, _chat_id, _main, _page

    chat_id = _chat_id(_ask(client))
    return _main(_page(client, f"/fragen/{chat_id}"))


def test_a_stored_answer_of_wissensgraph_draws_the_block_again_when_the_chat_is_opened(
        alice_client, platform_db, ask_service):
    main = _chat_page(alice_client)
    assert main.count('class="ask-graph"') == 1 and "Im Wissensgraphen" in main
    data = re.search(r'class="ask-graph-canvas" data-graph="([^"]*)"', main).group(1)
    drawn = json.loads(html_lib.unescape(data))
    assert [n["label"] for n in drawn["nodes"]] == ["2024-017 Huber gegen Rueegg", "Dr. Markus Baer", "2 \u00d7 Akte"]
    assert drawn["edges"] == GRAPH["edges"]
    listed = re.search(r'<ul class="ask-graph-list">(.*?)</ul>', main, flags=re.S).group(1)
    assert "Dr. Markus Baer" in listed and "2 \u00d7 Akte" in listed
    # the block stands under the statements, before the sources
    assert main.index("ask-statement") < main.index('class="ask-graph"') < main.index("Quellen")


def test_no_block_for_an_answer_without_a_graph_or_of_another_mode(alice_client, platform_db, ask_service):
    for reply in (graph_reply(graph=None), Reply(status="answered", sentences=[SENTENCE], passages=list(PASSAGES)),
                  replace(graph_reply(), graph={"nodes": GRAPH["nodes"][:1], "edges": []})):
        ask_service.reply = reply
        main = _chat_page(alice_client)
        assert "ask-graph" not in main and "Im Wissensgraphen" not in main


def test_the_page_loads_the_drawing_script(alice_client, platform_db, ask_service):
    from test_assistant_chats_routes import _ask, _chat_id, _page

    page = _page(alice_client, f"/fragen/{_chat_id(_ask(alice_client))}")
    assert re.search(r'<script src="/static/js/ask_graph\.js\?v=[^"]*"></script>', page)


# --- the script ----------------------------------------------------------------------------------------------------


def test_the_drawing_script_writes_no_markup_and_opens_only_the_apps_own_addresses():
    source = GRAPH_JS.read_text(encoding="ascii")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                 "localStorage", "sessionStorage", "fetch(", "XMLHttpRequest", "http://", "https://"):
        assert sink not in source.replace("http://www.w3.org/2000/svg", ""), sink
    # the one address it sets is the node's own, and only after ownAddress said yes
    assert source.count("setAttribute('href'") == 1
    assert re.search(r"if \(ownAddress\(node\.href\)\) \{\s*item = make\('a'[^;]*;\s*item\.setAttribute\('href', "
                     r"node\.href\);", source)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_drawing_script_parses():
    result = subprocess.run(["node", "--check", str(GRAPH_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

