"""PMVP-23: the assistant service on a follow-up (work order item 5): the search text, and the history fitted as whole
pairs (FU-07) and weighed with every digit as a token (FU-06).

No database, no Core, no model: fake search and generate functions record what they were given. Long chats are
built as the route builds them (``history_of``: a question as typed, an answer as its statements, one per line).
This file is ASCII-only.
"""
from __future__ import annotations

import pytest

from assistant import service as svc
from assistant.service import (AssistantService, Passage, Reply, Turn, compose, cut_to_weight, history_weight,
                               passages_from_rows)

QUESTION = "Und wer hat den Vertrag unterschrieben?"
MODEL_TOKENS = 8_192                                         # the model's context, prompt and answer together


def _row(n, text="Die K\u00fcndigungsfrist betr\u00e4gt drei Monate.", **extra):
    return {"doc_id": f"mandate/dok_{n}.pdf", "title": f"Dokument {n}", "snippet": text, "page_number": n, **extra}


class FakeSearch:
    def __init__(self, rows=None):
        self.rows = [_row(1)] if rows is None else rows
        self.queries = []

    def __call__(self, query, limit):
        self.queries.append(query)
        return list(self.rows)


class FakeGenerate:
    def __init__(self):
        self.calls = []

    def __call__(self, *, system_instruction, messages, max_tokens):
        self.calls.append({"system_instruction": system_instruction, "messages": messages, "max_tokens": max_tokens})
        return '{"no_answer": true}'


def _service(search=None, generate=None):
    return AssistantService(search=search or FakeSearch(), generate=generate or FakeGenerate())


def _size(text):
    return len(text.encode("utf-8"))


def _chat(pairs, question=lambda i: f"Frage {i}?", answer=lambda i: f"Antwort {i}."):
    history = []
    for i in range(pairs):
        history += [Turn("user", question(i)), Turn("assistant", answer(i))]
    return history


def _prompt(call):
    return [call["system_instruction"]] + [m["content"] for m in call["messages"]]


def _tokens(texts):
    """The prompt's tokens at one token per digit and three bytes a token for everything else (FU-06)."""
    digits = sum(sum(1 for ch in t if "0" <= ch <= "9") for t in texts)
    return digits + (sum(_size(t) for t in texts) - digits) / 3


# --- the search text (D1) --------------------------------------------------------------------------------------------


def test_the_search_uses_the_search_text_and_the_prompt_the_question():
    search, generate = FakeSearch(), FakeGenerate()
    _service(search, generate).answer(QUESTION, _chat(1), search_text="Welche Frist gilt?\n" + QUESTION)
    assert search.queries == ["Welche Frist gilt?\n" + QUESTION]
    final = generate.calls[0]["messages"][-1]["content"]
    assert "Question: " + QUESTION in final and "Welche Frist gilt?" not in final


def test_without_a_search_text_the_question_is_searched_as_before():
    search = FakeSearch()
    _service(search).answer(QUESTION)
    _service(search).answer(QUESTION, search_text="   ")
    assert search.queries == [QUESTION, QUESTION]


def test_a_search_text_over_the_limit_is_cut_and_a_wrong_type_fails():
    search = FakeSearch()
    _service(search).answer(QUESTION, search_text="Frist " * 1_000)
    assert _size(search.queries[0]) <= svc.MAX_QUESTION_BYTES
    reply = _service(search).answer(QUESTION, search_text=42)
    assert reply.status == "failed" and len(search.queries) == 1


# --- long chats: the Core's limits and the budget ----------------------------------------------------------------------


def test_forty_turns_stay_within_the_core_and_the_budget():
    generate = FakeGenerate()
    history = _chat(40, question=lambda i: f"Frage {i}: " + "Wort " * 300, answer=lambda i: f"Antwort {i}. " * 200)
    _service(generate=generate).answer(QUESTION, history)
    messages = generate.calls[0]["messages"]
    assert len(messages) <= svc.CORE_MAX_MESSAGES
    assert all(_size(m["content"]) <= svc.CORE_MAX_MESSAGE_BYTES for m in messages)
    assert sum(_size(t) for t in _prompt(generate.calls[0])) <= svc.DEFAULT_PROMPT_BUDGET_BYTES
    roles = [m["role"] for m in messages]
    assert roles[0] == "user" and roles[-1] == "user" and all(a != b for a, b in zip(roles, roles[1:]))
    assert messages[-2]["content"].startswith("Antwort 39.")                     # the newest answer is there


def test_many_short_turns_keep_at_most_32_messages_in_whole_pairs():
    generate = FakeGenerate()
    _service(generate=generate).answer(QUESTION, _chat(40))
    messages = generate.calls[0]["messages"]
    assert len(messages) == svc.CORE_MAX_MESSAGES - 1                            # 15 pairs and the question
    assert [m["content"] for m in messages[:2]] == ["Frage 25?", "Antwort 25."]
    assert messages[-2]["content"] == "Antwort 39."


def test_a_history_of_numbers_stays_within_the_models_tokens():
    """FU-06: weighed by its bytes alone, a history of digits filled the budget and overflowed 8,192 tokens."""
    generate = FakeGenerate()
    digits = "4711 0815 2026 " * 300                                               # 4,500 bytes, 3,600 digits
    history = _chat(40, question=lambda i: f"{i} " + digits, answer=lambda i: f"{i} " + digits)
    _service(generate=generate).answer(QUESTION, history)
    call = generate.calls[0]
    assert len(call["messages"]) >= 3                                              # the newest pair is sent
    assert _tokens(_prompt(call)) + call["max_tokens"] <= MODEL_TOKENS
    # weighed by bytes alone it would not fit
    sent = call["messages"][:-1]
    assert sum(history_weight(m["content"]) for m in sent) > sum(_size(m["content"]) for m in sent)


# --- FU-07: the newest pair is cut, never dropped ----------------------------------------------------------------------


def _long_passages():
    return passages_from_rows([_row(n, "Wort " * 2_000) for n in range(1, 6)])


def test_a_long_last_answer_is_sent_cut_not_dropped():
    question = "Welche Frist gilt? " * 50                                             # 950 bytes
    answer = "Die Frist betr\u00e4gt drei Monate. " * 125                            # about 4,000 bytes
    history = [Turn("user", question.strip()), Turn("assistant", answer.strip())]
    system, messages = compose(QUESTION, _long_passages(), history)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == question.strip()                                 # the question whole
    sent = messages[1]["content"]
    assert sent.endswith("\u2026") and answer.startswith(sent[:-1])                   # the answer cut, its start kept
    assert _size(system) + sum(_size(m["content"]) for m in messages) <= svc.DEFAULT_PROMPT_BUDGET_BYTES


def test_when_the_question_alone_is_too_long_both_are_cut():
    history = [Turn("user", "Frage " * 800), Turn("assistant", "Antwort " * 600)]
    system, messages = compose("F" * svc.MAX_QUESTION_BYTES, _long_passages(), history)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert all(m["content"].endswith("\u2026") for m in messages[:2])
    assert history_weight(messages[1]["content"]) <= svc.MIN_HISTORY_PIECE_BYTES
    assert _size(system) + sum(_size(m["content"]) for m in messages) <= svc.DEFAULT_PROMPT_BUDGET_BYTES


def test_older_pairs_follow_whole_or_not_at_all():
    history = _chat(6, answer=lambda i: f"Antwort {i}. " + "x" * 1_500)
    _, messages = compose(QUESTION, _long_passages(), history)
    sent = messages[:-1]
    assert len(sent) % 2 == 0 and sent[0]["role"] == "user"
    assert sent[-1]["content"].startswith("Antwort 5.")
    for message in sent[:-2]:
        assert not message["content"].endswith("\u2026")                           # only the newest pair is cut


def test_no_history_fits_a_budget_smaller_than_two_pieces():
    assert svc._fit_history([("user", "F?"), ("assistant", "A.")], 2 * svc.MIN_HISTORY_PIECE_BYTES - 1, 31) == []
    assert svc._fit_history([("user", "F?"), ("assistant", "A.")], 2 * svc.MIN_HISTORY_PIECE_BYTES, 31) == [
        ("user", "F?"), ("assistant", "A.")]
    assert svc._fit_history([("assistant", "Hallo."), ("user", "F?"), ("assistant", "A.")], 1_000, 1) == []


@pytest.mark.parametrize("text, weight", [("abc", 3), ("2026", 12), ("\u00e4 1", 6), ("", 0)])
def test_a_digit_weighs_three_bytes(text, weight):
    assert history_weight(text) == weight


@pytest.mark.parametrize("text", ["1234567890" * 100, "Wort " * 300, "\u00e4" * 500 + "9" * 500])
@pytest.mark.parametrize("limit", [0, 3, 50, 777, 5_000])
def test_cutting_to_a_weight_keeps_it_and_the_start(text, limit):
    cut = cut_to_weight(text, limit)
    assert history_weight(cut) <= limit
    assert cut == text or text.startswith(cut.rstrip("\u2026").rstrip())


# --- AsTheAssistant passes the search text on ------------------------------------------------------------------------


class _Client:
    def asserting_for(self, principal):
        import contextlib

        return contextlib.nullcontext()


class _Gate:
    class _Person:
        id = "00000000-0000-0000-0000-000000000001"

    def current_user(self):
        return self._Person()

    def connection(self):
        return None


class _TwoArguments:
    """A service of before PMVP-23: answer(question, history)."""

    def __init__(self):
        self.calls = []

    def answer(self, question, history=()):
        self.calls.append((question, tuple(history)))
        return Reply(status="no_answer")


def test_the_assistants_wrapper_passes_the_search_text_on(monkeypatch):
    from identity import agents
    from web_interface.assistant_routes import AsTheAssistant

    class _Store:
        def __init__(self, conn):
            pass

        def assistant(self):
            return type("A", (), {"id": "00000000-0000-0000-0000-0000000000aa"})()

    monkeypatch.setattr(agents, "AgentStore", _Store)
    old = _TwoArguments()
    AsTheAssistant(old, client=_Client(), identity_gate=_Gate()).answer(QUESTION, [Turn("user", "F?")])
    assert old.calls == [(QUESTION, (Turn("user", "F?"),))]

    class _New(_TwoArguments):
        def answer(self, question, history=(), *, search_text=None):
            self.calls.append((question, search_text))
            return Reply(status="no_answer")

    new = _New()
    AsTheAssistant(new, client=_Client(), identity_gate=_Gate()).answer(QUESTION, (), search_text="F?\n" + QUESTION)
    assert new.calls == [(QUESTION, "F?\n" + QUESTION)]


def test_a_passage_never_reaches_the_history():
    """POST-02: the route sends statements only; the service adds nothing of earlier passages to the prompt."""
    generate = FakeGenerate()
    earlier = Passage(number=1, pointer="mandate/alt.pdf", title="alt.pdf", page=1, text="FRUEHERE-STELLE")
    history = [Turn("user", "Erste Frage?"), Turn("assistant", "Erste Aussage.")]
    _service(generate=generate).answer(QUESTION, history)
    assert earlier.text not in "\n".join(_prompt(generate.calls[0]))
