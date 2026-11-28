"""The Knovas Assistent says what it is (the founder, 2026-11-28: asked whether it works with Knovas, it said it did
not know).

In all three modes the instruction says plainly what the assistant is (the Knovas Assistent of the company's Knovas
Platform, answering only from the company's data in Knovas), lets a reply about itself say yes or no, and keeps only
the AI model and its vendor unnamed. The reply rules let such a reply through, also where the passages name Knovas.
No Core and no model is reached. This file is ASCII-only.
"""
from __future__ import annotations

import json

import pytest

from assistant import brain_graph, brain_tools
from assistant.conversation import OWN_NAMES, check_replies, names_a_passage, passage_names
from assistant.service import SELF_DESCRIPTION, SYSTEM_INSTRUCTION, AssistantService, Passage

INSTRUCTIONS = {"passages": SYSTEM_INSTRUCTION, "brain_mcp": brain_tools.BRAIN_SYSTEM_INSTRUCTION,
                "brain_graph": brain_graph.GRAPH_SYSTEM_INSTRUCTION}
YES_KNOVAS = ("Ja, ich arbeite mit Knovas: Ich bin der Knovas Assistent Ihres Unternehmens und antworte nur aus "
              "seinen Daten im Knovas Brain.")
KNOVAS_PASSAGE = Passage(number=1, pointer="Akten/Knovas Plattform Vertrag.pdf", title="Knovas Brain Vertrag",
                         page=1, text="Die Knovas GmbH betreibt die Knovas Platform und das Knovas Brain fuer Huber.")


def reply_line(text, kind="chat"):
    return json.dumps({"reply": text, "kind": kind}, ensure_ascii=False)


@pytest.mark.parametrize("mode", sorted(INSTRUCTIONS))
def test_each_mode_says_what_the_assistant_is_and_where_its_answers_come_from(mode):
    instruction = INSTRUCTIONS[mode]
    owner = "company" if mode == "brain_graph" else "firm"
    assert f"You are the Knovas Assistent of the {owner}'s Knovas Platform" in instruction
    assert "answer only from its data in Knovas" in instruction
    assert "whether you use Knovas" in instruction and "a chat reply may say yes or no" in instruction
    if mode == "brain_graph":
        assert "the Knovas Brain, its knowledge graph, and its documents searched through Knovas" in instruction
    elif mode == "brain_mcp":
        assert "(the Knovas Brain)" in instruction


@pytest.mark.parametrize("mode", sorted(INSTRUCTIONS))
def test_only_the_model_and_its_vendor_stay_unnamed(mode):
    instruction = INSTRUCTIONS[mode]
    assert "Never say which language model, company or technology" not in instruction
    assert "Never name the AI model or vendor; if asked, say you do not disclose it, not that you do not know." \
        in instruction


def test_the_yes_no_ban_stays_for_the_documents_and_the_companys_things():
    for instruction in (SYSTEM_INSTRUCTION, brain_tools.BRAIN_SYSTEM_INSTRUCTION):
        assert "never answers a yes/no question about the documents, never states anything about them" in instruction
    assert "never answers a yes/no question about the company's things" in brain_graph.GRAPH_SYSTEM_INSTRUCTION
    assert "{owner}" in SELF_DESCRIPTION and "{where}" in SELF_DESCRIPTION


def test_the_instructions_still_fit_the_cores_limit():
    assert len(SYSTEM_INSTRUCTION.encode("utf-8")) <= 4_000
    assert len(brain_tools.BRAIN_SYSTEM_INSTRUCTION.encode("utf-8")) <= 4_000


@pytest.mark.parametrize("text", [
    YES_KNOVAS,
    "Ja, ich bin Teil der Knovas Platform Ihres Unternehmens.",
    "Ich bin der Knovas Assistent und lese den Wissensgraphen Ihres Unternehmens in Knovas.",
    "Welches Sprachmodell ich nutze, gebe ich nicht bekannt.",
])
def test_a_reply_about_itself_passes_with_and_without_passages_that_name_knovas(text):
    for passages in ([], [KNOVAS_PASSAGE]):
        said, dropped = check_replies(reply_line(text), passages, statements_shown=0)
        assert said is not None and dropped == 0, (text, passages)
        assert said.text == text and said.kind == "chat"


def test_knovas_is_no_name_of_a_passage_but_the_passages_other_names_still_are():
    names = passage_names([KNOVAS_PASSAGE])
    assert not names_a_passage("Knovas Brain Platform Plattform Assistent", names)
    assert names_a_passage("Ja, ich arbeite mit Knovas und Huber.", names)               # Huber: the passage's
    said, dropped = check_replies(reply_line("Ja, ich arbeite mit Knovas und Huber."), [KNOVAS_PASSAGE],
                                  statements_shown=0)
    assert said is None and dropped == 1
    assert OWN_NAMES == frozenset({"knovas", "brain", "platform", "plattform", "assistent", "assistant"})


def test_the_passages_mode_shows_the_reply_about_itself():
    rows = [{"doc_id": "Akten/k.pdf", "path": "Akten/k.pdf", "title": KNOVAS_PASSAGE.title, "page_number": 1,
             "snippet": KNOVAS_PASSAGE.text}]
    service = AssistantService(search=lambda q, limit: [dict(r) for r in rows],
                               generate=lambda **kw: reply_line(YES_KNOVAS))
    reply = service.answer("Arbeitest du mit Knovas?")
    assert reply.status == "no_answer" and reply.reply_text == YES_KNOVAS and reply.reply_kind == "chat"
    assert reply.removed_by["reply_dropped"] == 0


def test_the_graph_mode_shows_the_reply_about_itself_without_a_tool():
    from test_assistant_brain_graph import FakeAnthropic, lines, message, service, text

    fake = FakeAnthropic(message(text(lines({"reply": YES_KNOVAS, "kind": "chat"}))))
    reply = service(fake).answer("Arbeitest du mit Knovas?")
    assert reply.status == "no_answer" and reply.reply_text == YES_KNOVAS and reply.reply_kind == "chat"


def test_the_brain_tools_mode_shows_the_reply_about_itself_without_a_tool():
    from test_assistant_brain_tools import FakeAnthropic, _service, message, text

    reply = _service(FakeAnthropic(message(text(reply_line(YES_KNOVAS))))).answer("Arbeitest du mit Knovas?")
    assert reply.status == "no_answer" and reply.reply_text == YES_KNOVAS and reply.reply_kind == "chat"
