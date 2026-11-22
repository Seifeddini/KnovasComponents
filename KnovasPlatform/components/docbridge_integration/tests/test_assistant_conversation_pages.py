"""PMVP-20 conversation on the pages: a reply is drawn as the assistant speaking and without a source, the nearest
passages only under a clarifying reply or a no-answer (at most five, clean titles), and the chat stores the reply and
shows it again (web_interface.assistant_routes, ask.html).

Work order docs/platform-mvp/work-orders/PMVP-20-conversation.md. A fake service stands in for the assistant; the
store is the real one on the test database. No Core and no model is reached. This file is ASCII-only.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re

import pytest

pytest.importorskip("flask")

from assistant.service import NO_ANSWER_TEXT, Passage, Reply, Sentence  # noqa: E402
from web_interface import assistant_routes  # noqa: E402

UUID_1 = "0ef33273-3627-56a4-8353-e31584baf6a7"
UUID_2 = "df1b01fd-0a1b-4c2d-8e3f-1234567890ab"
TITLES = [f"{UUID_1}_2024-055_Beilage-Vertrag", f"{UUID_2}_2019-029_Beilage-Vertrag", "2023-041_Abmahnung",
          "Mietvertrag_Seefeldstrasse_48.pdf", f"{UUID_1}_2024-055_Mietvertrag", "Nachtrag_2_2023.pdf",
          "2022-007_Vollmacht"]
SHOWN = ["2024-055 \u00b7 Beilage-Vertrag", "2019-029 \u00b7 Beilage-Vertrag", "2023-041 \u00b7 Abmahnung",
         "Mietvertrag_Seefeldstrasse_48.pdf", "2024-055 \u00b7 Mietvertrag"]
PASSAGES = [Passage(number=n, pointer=f"kanzlei/demo/{title}.pdf", title=title, page=(n if n % 2 else None),
                    text=f"Text der Stelle {n}.", sentence=n) for n, title in enumerate(TITLES, start=1)]
GREETING = "Hallo! Wie kann ich Ihnen helfen?"
CLARIFY = "Gern helfe ich Ihnen. Um welches Mandat geht es, und welche Art von Dokument suchen Sie?"
LEAD = "Dazu habe ich Folgendes gefunden:"
S1 = Sentence(text="Die Frist betr\u00e4gt drei Monate.", refs=(1,), quote="Text der Stelle 1")
UUID_PATH = re.compile(r"/fragen/([0-9a-fA-F-]{36})")


def said(text, kind):
    return Reply(status="no_answer", passages=list(PASSAGES[:5]) if kind == "clarify" else [], message=text,
                 reply_text=text, reply_kind=kind)


class FakeService:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def answer(self, question, history=(), *, search_text=None):
        self.calls.append({"question": question, "history": list(history), "search_text": search_text})
        return self.reply


@pytest.fixture
def service(workbench_app, monkeypatch):
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")
    fake = FakeService(said(GREETING, "chat"))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    return fake


def _ask(client, question="wagwan", url="/fragen"):
    return client.post(url, data={"question": question, "csrf_token": client._token})


def _chat_id(response):
    assert response.status_code == 303, response.status_code
    return UUID_PATH.search(response.headers["Location"]).group(1)


def _main(client, chat_id):
    response = client.get(f"/fragen/{chat_id}")
    assert response.status_code == 200
    return re.search(r"<main\b.*?</main>", response.get_data(as_text=True), flags=re.S).group(0)


def _text(fragment):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _replies(main):
    return [_text(m) for m in re.findall(r'<p class="ask-reply-text" dir="auto">(.*?)</p>', main, flags=re.S)]


def _sources(main):
    return [_text(m) for m in re.findall(r'<li class="ask-source">(.*?)</li>', main, flags=re.S)]


def test_a_greeting_is_drawn_as_the_assistant_speaking_without_sources(alice_client, service):
    main = _main(alice_client, _chat_id(_ask(alice_client)))
    assert _replies(main) == [GREETING]
    assert re.search(r'<div class="ask-reply">\s*<span class="ask-reply-who">Knovas Assistent</span>', main)
    for absent in (NO_ANSWER_TEXT, "Die n\u00e4chsten Stellen", "Quellen", "ask-source", "ask-statement",
                   "ask-quote", "k-chip", "ask-message"):
        assert absent not in main, absent


def test_a_clarifying_reply_lists_at_most_five_nearest_passages_with_clean_titles(alice_client, service):
    service.reply = Reply(status="no_answer", passages=list(PASSAGES), message=CLARIFY, reply_text=CLARIFY,
                          reply_kind="clarify")                  # seven: the page lists five all the same
    chat_id = _chat_id(_ask(alice_client, "document"))
    main = _main(alice_client, chat_id)
    assert _replies(main) == [CLARIFY] and NO_ANSWER_TEXT not in main
    assert "Die n\u00e4chsten Stellen" in main
    assert _sources(main) == [f"{n} {title}" + (f" , S. {n}" if n % 2 else "")
                              for n, title in enumerate(SHOWN, start=1)]
    assert UUID_1 not in main and UUID_2 not in main
    hrefs = re.findall(r'<li class="ask-source">.*?href="([^"]+)"', main, flags=re.S)
    assert hrefs == [f"/fragen/{chat_id}/quelle/1/{n}" for n in range(1, 6)]   # each opens its document


def test_the_no_answer_sentence_lists_at_most_five_too(alice_client, service):
    service.reply = Reply(status="no_answer", passages=list(PASSAGES), message=NO_ANSWER_TEXT)
    main = _main(alice_client, _chat_id(_ask(alice_client, "Bonus?")))
    assert NO_ANSWER_TEXT in main and _replies(main) == [] and len(_sources(main)) == 5


def test_a_lead_in_stands_above_the_statements_and_the_titles_are_clean(alice_client, service):
    service.reply = Reply(status="answered", sentences=[S1], passages=list(PASSAGES), reply_text=LEAD)
    main = _main(alice_client, _chat_id(_ask(alice_client, "Welche Frist?")))
    assert _replies(main) == [LEAD]
    assert main.index("ask-reply") < main.index("ask-statement")
    head = re.search(r'<span class="ask-quote-doc" dir="auto">(.*?)</span>', main).group(1)
    assert html_lib.unescape(head) == SHOWN[0]                                   # under the statement too
    assert len(_sources(main)) == 7 and _sources(main)[0] == "1 " + SHOWN[0] + " , S. 1"   # Quellen: all


def test_the_chat_stores_the_reply_and_shows_it_again(alice_client, alice, platform_db, service):
    from identity.chats import ChatStore

    service.reply = Reply(status="no_answer", passages=list(PASSAGES[:5]), message=CLARIFY, reply_text=CLARIFY,
                          reply_kind="clarify")
    chat_id = _chat_id(_ask(alice_client, "document"))
    chat = ChatStore(platform_db).get_chat(chat_id, owner=alice.id)
    stored = json.loads(chat.messages[1].text)
    assert stored["status"] == "no_answer" and stored["reply"] == CLARIFY and stored["kind"] == "clarify"
    assert len(chat.messages[1].references) == 5
    again = assistant_routes.stored_reply(chat.messages[1])
    assert (again.reply_text, again.reply_kind, again.message) == (CLARIFY, "clarify", CLARIFY)
    # the follow-up: the clarifying question is the assistant's turn of the history, the answer below it
    service.reply = Reply(status="answered", sentences=[S1], passages=list(PASSAGES[:1]))
    assert _chat_id(_ask(alice_client, "Den Mietvertrag", url=f"/fragen/{chat_id}")) == chat_id
    history = service.calls[-1]["history"]
    assert [(t.role, t.text) for t in history] == [("user", "document"), ("assistant", CLARIFY)]
    main = _main(alice_client, chat_id)
    assert _replies(main) == [CLARIFY] and "ask-statement" in main
    assert main.index(CLARIFY.split(".")[0]) < main.index("ask-statement")


def test_an_older_stored_no_answer_reads_as_before(alice_client, alice, platform_db, service):
    from identity.chats import ChatStore

    store = ChatStore(platform_db)
    chat_id = store.create_chat(owner=alice.id, title="alt")
    store.add_message(chat_id, owner=alice.id, role="user", text="alt?")
    store.add_message(chat_id, owner=alice.id, role="assistant",
                      text=json.dumps({"v": 1, "status": "no_answer", "statements": [], "removed": 0,
                                       "sentences": {}}),
                      references=[{"number": p.number, "pointer": p.pointer, "page": p.page, "title": p.title,
                                   "text": p.text} for p in PASSAGES])
    main = _main(alice_client, str(chat_id))
    assert NO_ANSWER_TEXT in main and _replies(main) == [] and len(_sources(main)) == 5
    reply = assistant_routes.stored_reply(store.get_chat(chat_id, owner=alice.id).messages[1])
    assert reply.reply_text == "" and reply.message == NO_ANSWER_TEXT
    assert [t.text for t in assistant_routes.history_of(store.get_chat(chat_id, owner=alice.id))] == [
        "alt?", NO_ANSWER_TEXT]


@pytest.mark.parametrize("bad", [{"reply": 5}, {"reply": "x", "kind": ["clarify"]}])
def test_a_stored_reply_of_the_wrong_type_is_drawn_as_plain_text(bad):
    message = type("M", (), {})()
    message.text = json.dumps({"v": 1, "status": "no_answer", "statements": [], "removed": 0, "sentences": {},
                               **bad})
    message.references = []
    with pytest.raises(TypeError):
        assistant_routes.stored_reply(message)


def test_a_reply_is_text_never_markup(alice_client, service):
    service.reply = said("<script>alert(1)</script> **fett**", "chat")
    main = _main(alice_client, _chat_id(_ask(alice_client)))
    assert "<script>alert(1)</script>" not in main and "&lt;script&gt;" in main and "**fett**" in main


def test_no_reply_text_in_the_routes_log_lines_at_debug(alice_client, service, caplog):
    for name in ("", "web_interface", "web_interface.assistant_routes", "assistant", "identity"):
        caplog.set_level(logging.DEBUG, logger=name)
    service.reply = said("Hallo GEHEIMGRUSS!", "chat")
    chat_id = _chat_id(_ask(alice_client, "wagwan GEHEIMFRAGE"))
    _main(alice_client, chat_id)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert caplog.records
    assert "GEHEIMGRUSS" not in logged and "GEHEIMFRAGE" not in logged
