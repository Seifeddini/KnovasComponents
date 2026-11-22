"""PMVP-23: the chat pages of the Knovas Assistent, beyond what the frozen acceptance file pins.

Work order docs/platform-mvp/work-orders/PMVP-23.md ("Your tests"): who is refused (FU-26), the wrong owner on every
address, a question becoming a chat (the title, FU-02; the 303's anchor), a follow-up (D1, POST-02), busy and
failed, delete, the click route, bad input (FU-01), a stored answer that does not parse, the follow-up under the
assistant's claim (RB-6), and the logs at DEBUG (FU-46). The list and the sidebar are in
test_assistant_chats_list.py, the service's history in test_assistant_chats_service.py, the 90 days in
test_assistant_chats_retention.py.

A fake service stands in for the assistant (it records the question, the history and the search text); the store is
the real one on the test database. No Core and no model is reached. This file is ASCII-only.
"""
from __future__ import annotations

import contextlib
import html as html_lib
import logging
import re
import uuid

import pytest

pytest.importorskip("flask")

import conftest  # noqa: E402
import pmvp_pages  # noqa: E402
from assistant.service import BUSY_TEXT, FAILED_TEXT, NO_ANSWER_TEXT, Passage, Reply, Sentence  # noqa: E402
from web_interface import assistant_routes  # noqa: E402

QUESTION = "Welche Frist gilt f\u00fcr den Mietvertrag Hardturm?"
FOLLOW = "Und wer hat unterschrieben?"
PDF = "mandate/Hardturm/Mietvertrag_Hardturm.pdf"
DOCX = "mandate/Hardturm/Nachtrag_Hardturm.docx"
P1 = "Die Frist betr\u00e4gt drei Monate auf Ende Juni."
P2 = "Unterschrieben von Anna Meier und Beat Keller."
PASSAGES = [
    Passage(number=1, pointer=PDF, title="Mietvertrag_Hardturm.pdf", page=7, text=P1, sentence=21),
    Passage(number=2, pointer=DOCX, title="Nachtrag_Hardturm.docx", page=2, text=P2, sentence=4),
]
S1 = Sentence(text="Gek\u00fcndigt wird mit einem Vierteljahr Frist.", refs=(1,), quote="Die Frist betr\u00e4gt drei Monate")
S2 = Sentence(text="Anna Meier hat unterschrieben.", refs=(2,), quote="Unterschrieben von Anna Meier")
SECRETS = (QUESTION, FOLLOW, "Hardturm", P1, P2, S1.text, S2.text, S1.quote, S2.quote, "Anna Meier", "Beat Keller")
UUID_PATH = re.compile(r"/fragen/([0-9a-fA-F-]{36})")


def answered(*sentences, passages=None):
    return Reply(status="answered", sentences=list(sentences or (S1,)),
                 passages=list(PASSAGES if passages is None else passages), removed=0)


class FakeService:
    """The assistant as the pages call it; records every call."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def answer(self, question, history=(), *, search_text=None):
        self.calls.append({"question": question, "history": list(history), "search_text": search_text})
        return self.reply


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")


@pytest.fixture
def service(workbench_app, switch_on):
    fake = FakeService(answered())
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    return fake


# --- helpers -------------------------------------------------------------------------------------------------------


def _ask(client, question=QUESTION, url="/fragen", **extra):
    return client.post(url, data={"question": question, "csrf_token": client._token, **extra})


def _chat_id(response):
    assert response.status_code == 303, response.status_code
    return UUID_PATH.search(response.headers["Location"]).group(1)


def _new_chat(client, question=QUESTION):
    return _chat_id(_ask(client, question))


def _page(client, url):
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    return response.get_data(as_text=True)


def _main(page):
    return re.search(r"<main\b.*?</main>", page, flags=re.S).group(0)


def _text(fragment):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _flat(fragment):
    """The text as the browser joins it: tags taken out, nothing put in their place."""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", fragment))


def _count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def _rows(conn):
    """(chats, messages, references) in the whole schema."""
    return tuple(_count(conn, f"SELECT count(*) FROM {table}")
                 for table in ("assistant_chats", "assistant_messages", "assistant_references"))


def _messages(conn, chat_id):
    return _count(conn, "SELECT count(*) FROM assistant_messages WHERE chat_id = %s", chat_id)


def _box(main):
    found = re.search(r'<input class="ask-again-input"[^>]*>', main, flags=re.S)
    assert found, "the page has no box"
    value = re.search(r'value="([^"]*)"', found.group(0))
    return html_lib.unescape(value.group(1)) if value else ""


def _gate_open(client, pointer):
    answer = client.get(f"/api/document/{pointer}/download", query_string={"path": pointer}).get_json() or {}
    return answer.get("error") != "Not found"


def _stored_chat(conn, person, *, reply=None, question=QUESTION, assistant_text=None):
    """A chat written straight into the store (no route, so nothing is granted)."""
    from identity.chats import ChatStore

    store = ChatStore(conn)
    chat_id = store.create_chat(owner=person.id, title=assistant_routes.chat_title(question))
    store.add_message(chat_id, owner=person.id, role="user", text=question)
    text, references = assistant_routes.stored_answer(reply or answered())
    store.add_message(chat_id, owner=person.id, role="assistant", text=assistant_text or text,
                      references=references)
    return str(chat_id)


def _addresses(chat_id):
    return [("GET", f"/fragen/{chat_id}", None), ("POST", f"/fragen/{chat_id}", {"question": FOLLOW}),
            ("POST", f"/fragen/{chat_id}/loeschen", {}), ("GET", f"/fragen/{chat_id}/quelle/1/1", None)]


# --- refused (FU-26) -----------------------------------------------------------------------------------------------


def test_signed_out_every_new_address_goes_to_sign_in_and_back(anon_client, alice_client, service):
    chat_id = _new_chat(alice_client)
    for method, url, data in _addresses(chat_id) + [("GET", "/fragen/verlauf", None)]:
        pmvp_pages.assert_signed_out_refused(anon_client, url, method=method, data=data)


def test_switched_off_no_address_answers_and_no_list_is_drawn(alice_client, service, monkeypatch):
    chat_id = _new_chat(alice_client)
    assert UUID_PATH.search(_page(alice_client, "/"))                        # on: the sidebar lists it
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "false")
    for method, url, data in _addresses(chat_id) + [("GET", "/fragen/verlauf", None)]:
        sent = alice_client.get(url) if method == "GET" else alice_client.post(
            url, data={**data, "csrf_token": alice_client._token})
        assert sent.status_code == 404, url
    page = _page(alice_client, "/")
    assert "data-chats" not in page and not UUID_PATH.search(page) and "Neue Frage" not in page


def test_without_personal_accounts_the_addresses_do_not_exist(docbridge_app, monkeypatch):
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")
    client = docbridge_app.test_client()
    with client.session_transaction() as session:
        session["company_login_ok"] = True
    some = str(uuid.uuid4())
    for method, url, _ in _addresses(some) + [("GET", "/fragen/verlauf", None)]:
        assert client.open(url, method=method).status_code == 404, url
    assert "data-chats" not in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("what", ["follow-up", "delete"])
def test_a_form_without_its_field_is_refused_before_anything_is_read(alice_client, platform_db, service,
                                                                    monkeypatch, what):
    from identity.chats import ChatStore

    chat_id = _new_chat(alice_client)
    url = f"/fragen/{chat_id}" if what == "follow-up" else f"/fragen/{chat_id}/loeschen"
    data = {"question": FOLLOW} if what == "follow-up" else {}
    reads = []
    for name in ("get_chat", "delete_chat"):
        real = getattr(ChatStore, name)
        monkeypatch.setattr(ChatStore, name, lambda self, *a, _real=real, _name=name, **k: (
            reads.append(_name), _real(self, *a, **k))[1])
    calls = len(service.calls)
    # without the field and without the header; with a wrong field; with the header only
    assert alice_client._client.post(url, data=data).status_code == 400
    assert alice_client.post(url, data={**data, "csrf_token": "falsch"}).status_code == 400
    assert alice_client.post(url, data=data).status_code == 400
    assert reads == [] and len(service.calls) == calls
    assert _messages(platform_db, chat_id) == 2
    # the control: with its field the form is taken
    pmvp_pages.assert_missing_token_refused(alice_client, url, role="member", status=400, data=data,
                                            unchanged=lambda: len(service.calls))


def test_the_refused_form_says_it_has_expired_and_repeats_nothing(alice_client, service):
    chat_id = _new_chat(alice_client)
    response = alice_client.post(f"/fragen/{chat_id}", data={"question": FOLLOW, "csrf_token": "falsch"})
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert assistant_routes.EXPIRED_TEXT in body
    assert FOLLOW not in body and html_lib.escape(QUESTION) not in _main(body)


# --- the wrong owner -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("index", [0, 1, 2, 3], ids=["open", "follow-up", "delete", "source"])
def test_another_persons_chat_is_answered_exactly_like_a_missing_one(alice_client, bob_client, platform_db, service,
                                                                     index):
    chat_id = _new_chat(alice_client)
    before, calls = _rows(platform_db), len(service.calls)
    method, url, data = _addresses(chat_id)[index]
    request = {} if data is None else {"data": {**data, "csrf_token": bob_client._token}}
    for missing in (str(uuid.uuid4()), "not-a-uuid"):
        pmvp_pages.assert_wrong_owner_looks_missing(bob_client, url, url.replace(chat_id, missing), role="member",
                                                    method=method, ids=[chat_id, missing], **request)
    assert _rows(platform_db) == before and len(service.calls) == calls


# --- a question becomes a chat --------------------------------------------------------------------------------------


def test_a_question_is_one_chat_of_two_messages_and_a_303_to_its_turn(alice_client, alice, platform_db, service):
    from identity.chats import ChatStore

    response = _ask(alice_client)
    chat_id = _chat_id(response)
    assert response.headers["Location"].endswith(f"/fragen/{chat_id}#runde-1")
    chat = ChatStore(platform_db).get_chat(chat_id, owner=alice.id)
    assert [(m.role, len(m.references)) for m in chat.messages] == [("user", 0), ("assistant", 2)]
    assert service.calls == [{"question": QUESTION, "history": [], "search_text": None}]
    main = _main(_page(alice_client, f"/fragen/{chat_id}"))
    assert re.search(r'<h1 class="k-title">' + re.escape(html_lib.escape(QUESTION)), main)
    assert 'id="runde-1"' in main
    statement = re.search(r'<p class="ask-statement">(.*?)</p>', main, flags=re.S).group(1)
    assert _flat(statement) == "Gek\u00fcndigt wird mit einem Vierteljahr Frist1."
    assert re.findall(r'aria-controls="([^"]+)"', main) == ["zitat-1-1"]
    assert "\u00ab" + S1.quote + "\u00bb" in _flat(main)
    assert "Quellen" in main


def test_no_answer_is_stored_and_drawn_with_the_closest_passages(alice_client, alice, platform_db, service):
    service.reply = Reply(status="no_answer", passages=list(PASSAGES), message=NO_ANSWER_TEXT, removed=2)
    chat_id = _new_chat(alice_client)
    main = _main(_page(alice_client, f"/fragen/{chat_id}"))
    assert NO_ANSWER_TEXT in main and "Die n\u00e4chsten Stellen" in main
    assert "2 Aussagen ohne Beleg ausgelassen" in main and "ask-statement" not in main


def test_a_long_question_titles_the_chat_cut_at_a_word(alice_client, alice, platform_db, service):
    from identity.chats import ChatStore

    question = " ".join(["Mietvertrag"] * 30) + "?"
    chat = ChatStore(platform_db).get_chat(_new_chat(alice_client, question), owner=alice.id)
    assert len(chat.title) <= 200 and chat.title.endswith("\u2026")
    assert question.startswith(chat.title[:-1]) and chat.title[:-1].endswith("Mietvertrag")
    assert chat.messages[0].text == question                                    # the question itself whole


@pytest.mark.parametrize("question, title", [
    ("Kurz?", "Kurz?"),
    ("  Zwei\n  Zeilen\tund   Raum  ", "Zwei Zeilen und Raum"),
    ("x" * 200, "x" * 200),
    ("x" * 201, "x" * 199 + "\u2026"),
    ("a " * 99 + "bb" + " c", "a " * 99 + "\u2026"),
])
def test_the_title_is_one_line_of_at_most_200_characters(question, title):
    shown = assistant_routes.chat_title(question)
    assert len(shown) <= 200
    assert shown.replace(" \u2026", "\u2026") == title.replace(" \u2026", "\u2026")


# --- a follow-up (SPEC-05, D1, POST-02) ----------------------------------------------------------------------------


def test_a_follow_up_searches_the_last_question_with_it_and_sends_statements_only(alice_client, platform_db,
                                                                                 service):
    chat_id = _new_chat(alice_client)
    service.reply = answered(S2)
    response = _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    assert response.headers["Location"].endswith(f"/fragen/{chat_id}#runde-2")
    call = service.calls[-1]
    assert call["question"] == FOLLOW
    assert call["search_text"] == QUESTION + "\n" + FOLLOW
    assert [(t.role, t.text) for t in call["history"]] == [("user", QUESTION), ("assistant", S1.text)]
    for turn in call["history"]:
        for never in (P1, P2, S1.quote, "{", "statements"):
            assert never not in turn.text
    assert _messages(platform_db, chat_id) == 4
    main = _main(_page(alice_client, f"/fragen/{chat_id}"))
    assert re.search(r'<h2 class="ask-turn-question" dir="auto">' + re.escape(FOLLOW) + "</h2>", main)
    assert re.findall(r'aria-controls="([^"]+)"', main) == ["zitat-1-1", "zitat-2-1"]
    assert main.index('id="runde-1"') < main.index('id="runde-2"')


def test_a_no_answer_turn_reaches_the_history_as_the_no_answer_sentence(alice_client, service):
    service.reply = Reply(status="no_answer", passages=list(PASSAGES), message=NO_ANSWER_TEXT)
    chat_id = _new_chat(alice_client)
    _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    assert [(t.role, t.text) for t in service.calls[-1]["history"]] == [("user", QUESTION),
                                                                        ("assistant", NO_ANSWER_TEXT)]


def test_the_follow_up_is_never_cut_from_the_search(alice_client, service):
    long_question = "Frist " * 700                                      # 4,200 bytes stored, over the limit together
    chat_id = _new_chat(alice_client, long_question.strip()[:3_990])
    _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    search_text = service.calls[-1]["search_text"]
    assert search_text.endswith("\n" + FOLLOW)
    assert len(search_text.encode("utf-8")) <= assistant_routes.MAX_QUESTION_BYTES


def test_an_empty_follow_up_goes_back_to_the_chat(alice_client, service):
    chat_id = _new_chat(alice_client)
    response = _ask(alice_client, "   ", url=f"/fragen/{chat_id}")
    assert response.status_code == 303 and response.headers["Location"].endswith(f"/fragen/{chat_id}")
    assert len(service.calls) == 1


# --- busy and failed store nothing ---------------------------------------------------------------------------------


@pytest.mark.parametrize("status, message", [("busy", BUSY_TEXT), ("failed", FAILED_TEXT)])
def test_busy_and_failed_on_a_new_question(alice_client, platform_db, service, status, message):
    service.reply = Reply(status=status, message=message)
    response = _ask(alice_client)
    assert response.status_code == 200 and _rows(platform_db) == (0, 0, 0)
    main = _main(response.get_data(as_text=True))
    assert html_lib.escape(message) in main and _box(main) == QUESTION
    assert "Gespr\u00e4ch l\u00f6schen" not in main


@pytest.mark.parametrize("status, message", [("busy", BUSY_TEXT), ("failed", FAILED_TEXT)])
def test_busy_and_failed_on_a_follow_up(alice_client, platform_db, service, status, message):
    chat_id = _new_chat(alice_client)
    before = _rows(platform_db)
    service.reply = Reply(status=status, message=message)
    response = _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    assert response.status_code == 200 and _rows(platform_db) == before
    main = _main(response.get_data(as_text=True))
    # the chat as it was, the message under its last turn, the question back in the box
    assert main.index(html_lib.escape(QUESTION)) < main.index("ask-statement") < main.index(html_lib.escape(message))
    assert _box(main) == FOLLOW
    assert f'action="/fragen/{chat_id}"' in main


# --- delete -------------------------------------------------------------------------------------------------------


def test_delete_takes_the_chat_with_its_messages_and_references(alice_client, platform_db, service):
    chat_id = _new_chat(alice_client)
    other = _new_chat(alice_client, FOLLOW)
    response = alice_client.post(f"/fragen/{chat_id}/loeschen", data={"csrf_token": alice_client._token})
    assert response.status_code == 303 and response.headers["Location"].endswith("/")
    assert _rows(platform_db) == (1, 2, 2) and _messages(platform_db, other) == 2
    again = alice_client.post(f"/fragen/{chat_id}/loeschen", data={"csrf_token": alice_client._token})
    assert again.status_code == 404


def test_the_chat_page_offers_delete_and_asks_first_with_javascript(alice_client, service):
    chat_id = _new_chat(alice_client)
    main = _main(_page(alice_client, f"/fragen/{chat_id}"))
    form = re.search(r'<form class="ask-delete" method="post" action="([^"]+)"\s+data-confirm="([^"]+)">(.*?)</form>',
                     main, flags=re.S)
    assert form and form.group(1) == f"/fragen/{chat_id}/loeschen"
    assert html_lib.unescape(form.group(2)) == ("Dieses Gespr\u00e4ch l\u00f6schen? "
                                                "Das l\u00e4sst sich nicht r\u00fcckg\u00e4ngig machen.")
    assert f'name="csrf_token" value="{alice_client._token}"' in form.group(3)
    assert "Gespr\u00e4ch l\u00f6schen</button>" in form.group(3)


# --- the click route -----------------------------------------------------------------------------------------------


def test_a_readable_reference_grants_that_one_document_and_opens_it_at_its_page(workbench_app, alice, alice_client,
                                                                                 platform_db, service):
    from flask import url_for

    chat_id = _stored_chat(platform_db, alice)
    assert not _gate_open(alice_client, PDF) and not _gate_open(alice_client, DOCX)
    response = alice_client.get(f"/fragen/{chat_id}/quelle/1/1")
    with workbench_app.test_request_context():
        wanted = url_for("preview_document", doc_id=PDF, path=PDF, s="21", _anchor="page=7")
    assert response.status_code == 302 and response.headers["Location"] == wanted
    assert _gate_open(alice_client, PDF) and not _gate_open(alice_client, DOCX)


def test_a_reference_the_core_refuses_or_out_of_range_opens_nothing(workbench_app, alice, alice_client, platform_db,
                                                                    service, fake_graph):
    chat_id = _stored_chat(platform_db, alice)
    fake_graph.denied_pointers.add(PDF)
    for turn, number in ((1, 1), (1, 3), (2, 1), (0, 1), (1, 0)):
        assert alice_client.get(f"/fragen/{chat_id}/quelle/{turn}/{number}").status_code == 404, (turn, number)
    assert fake_graph.readable_calls.count(PDF) == 1                           # asked once, for (1, 1) only
    assert not _gate_open(alice_client, PDF) and not _gate_open(alice_client, DOCX)


def test_every_link_of_a_stored_answer_goes_through_the_click_route(alice_client, service):
    chat_id = _new_chat(alice_client)
    main = _main(_page(alice_client, f"/fragen/{chat_id}"))
    hrefs = [html_lib.unescape(h) for h in re.findall(r'href="([^"]+)"', main)]
    assert sorted(set(hrefs)) == [f"/fragen/{chat_id}/quelle/1/1", f"/fragen/{chat_id}/quelle/1/2"]


# --- bad input (FU-01) ---------------------------------------------------------------------------------------------


def test_nul_characters_and_a_huge_page_are_stored_without_error(alice_client, alice, platform_db, service):
    from identity.chats import ChatStore

    service.reply = answered(
        Sentence(text="Die\x00 Frist.", refs=(1,), quote="Die\x00 Frist"),
        passages=[Passage(number=1, pointer=PDF + "\x00", title="Ver\x00trag", page=2 ** 40, text="Die\x00 Frist",
                          sentence=2 ** 40),
                  Passage(number=2, pointer="\x00", title="leer", page=1, text="t")])
    chat_id = _new_chat(alice_client, "Welche\x00 Frist?")
    chat = ChatStore(platform_db).get_chat(chat_id, owner=alice.id)
    assert chat.title == "Welche Frist?" and chat.messages[0].text == "Welche Frist?"
    assert "\x00" not in chat.messages[1].text
    assert chat.messages[1].references == [{"number": 1, "pointer": PDF, "page": None, "title": "Vertrag",
                                            "text": "Die Frist"}]
    assert _page(alice_client, f"/fragen/{chat_id}")


@pytest.mark.parametrize("follow_up", [False, True], ids=["question", "follow-up"])
def test_a_write_that_fails_leaves_nothing_and_shows_the_reply_once(alice_client, platform_db, service, monkeypatch,
                                                                    caplog, follow_up):
    from identity.chats import ChatStore

    chat_id = _new_chat(alice_client) if follow_up else None
    before = _rows(platform_db)
    real, seen = ChatStore.add_message, []

    def second_fails(self, *args, **kwargs):
        seen.append(kwargs.get("role"))
        if len(seen) == 2:
            raise RuntimeError("database gone")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ChatStore, "add_message", second_fails)
    service.reply = answered(S2)
    with caplog.at_level(logging.WARNING, logger="web_interface.assistant_routes"):
        response = _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}" if follow_up else "/fragen")
    assert response.status_code == 200
    assert _rows(platform_db) == before
    main = _main(response.get_data(as_text=True))
    assert html_lib.escape(assistant_routes.NOT_SAVED_TEXT) in main and "ask-statement" in main
    assert "Anna Meier hat unterschrieben" in main
    assert any("not stored (RuntimeError)" in r.getMessage() for r in caplog.records)


def test_a_stored_answer_that_does_not_parse_is_one_plain_paragraph(alice, alice_client, platform_db, service,
                                                                    caplog):
    chat_id = _stored_chat(platform_db, alice, assistant_text="Kein JSON <b>hier</b>")
    with caplog.at_level(logging.WARNING, logger="web_interface.assistant_routes"):
        main = _main(_page(alice_client, f"/fragen/{chat_id}"))
    assert '<p class="ask-plain" dir="auto">Kein JSON &lt;b&gt;hier&lt;/b&gt;</p>' in main
    assert "k-ref" not in main and "ask-quote" not in main
    assert any("not in the stored shape (JSONDecodeError)" in r.getMessage() for r in caplog.records)
    # and it is not sent to the model as history
    _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    assert [t.role for t in service.calls[-1]["history"]] == ["user"]


@pytest.mark.parametrize("text", ['{"v": 2, "status": "answered"}', '{"v": 1, "status": "busy"}', "[1, 2]",
                                  '{"v": 1, "status": "answered", "statements": [{"text": 1}]}',
                                  '{"v": 1, "status": "answered", "statements": [], "removed": true}'])
def test_only_the_stored_shape_is_read_as_an_answer(text):
    from types import SimpleNamespace

    with pytest.raises((ValueError, TypeError, KeyError)):
        assistant_routes.stored_reply(SimpleNamespace(text=text, references=[]))


def test_a_stored_answer_reads_back_as_the_reply_it_was(alice, platform_db):
    from identity.chats import ChatStore

    reply = answered(S1, S2)
    chat_id = _stored_chat(platform_db, alice, reply=Reply(status="answered", sentences=[S1, S2],
                                                          passages=list(PASSAGES), removed=3))
    message = ChatStore(platform_db).get_chat(chat_id, owner=alice.id).messages[1]
    again = assistant_routes.stored_reply(message)
    assert (again.status, again.sentences, again.removed) == ("answered", reply.sentences, 3)
    assert again.passages == PASSAGES


# --- the follow-up answers with the assistant's claim (RB-6) --------------------------------------------------------


class PrincipalClient(conftest.DummyKnovasClient):
    """The dummy client with ``asserting_for``: it records the principal in force."""

    current = None

    @contextlib.contextmanager
    def asserting_for(self, principal):
        previous, PrincipalClient.current = PrincipalClient.current, principal
        try:
            yield
        finally:
            PrincipalClient.current = previous


class Seen:
    """A service that records the principal in force and what it was asked."""

    def __init__(self):
        self.calls = []

    def answer(self, question, history=(), *, search_text=None):
        self.calls.append((PrincipalClient.current, question, search_text))
        return answered()


def test_a_follow_up_is_answered_with_the_assistants_claim_for_the_person(platform_db, tmp_path, monkeypatch,
                                                                          identity_repo):
    import assistant.core as core
    from identity.agents import AgentPrincipal, AgentStore

    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")
    seen = Seen()
    monkeypatch.setattr(core, "make_service", lambda *a, **k: seen)
    app = conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=PrincipalClient)
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    person = conftest._signed_in(app, anna.email)
    chat_id = _new_chat(person)
    assert _chat_id(_ask(person, FOLLOW, url=f"/fragen/{chat_id}")) == chat_id
    claim = AgentPrincipal(agent_id=AgentStore(platform_db).assistant().id, on_behalf_of=anna.id)
    assert seen.calls == [(claim, QUESTION, None), (claim, FOLLOW, QUESTION + "\n" + FOLLOW)]
    assert PrincipalClient.current is None


# --- logs (POST-04, FU-46) -----------------------------------------------------------------------------------------


def test_no_customer_text_in_any_log_line_at_debug(alice, alice_client, platform_db, service, caplog):
    for name in ("web_interface", "assistant", "identity", "knovas_client", "document_grants"):
        caplog.set_level(logging.DEBUG, logger=name)
    caplog.set_level(logging.DEBUG)
    chat_id = _new_chat(alice_client)
    service.reply = answered(S2)
    _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    service.reply = Reply(status="busy", message=BUSY_TEXT)
    _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    _ask(alice_client, FOLLOW)
    alice_client.get(f"/fragen/{chat_id}")
    alice_client.get(f"/fragen/{chat_id}/quelle/1/1")
    alice_client.get(f"/fragen/{chat_id}/quelle/2/1")
    alice_client.get("/fragen/verlauf")
    alice_client.get("/")
    alice_client.post(f"/fragen/{chat_id}", data={"question": FOLLOW, "csrf_token": "falsch"})
    alice_client.post(f"/fragen/{chat_id}/loeschen", data={"csrf_token": alice_client._token})
    lines = [r.getMessage() for r in caplog.records] + [r.exc_text or "" for r in caplog.records]
    joined = "\n".join(lines)
    assert "Fragen: status=answered stored=yes" in joined and "follow-up status=answered stored=yes" in joined
    assert "follow-up status=busy stored=no" in joined
    for secret in SECRETS + (PDF, DOCX, "Mietvertrag_Hardturm", "Nachtrag_Hardturm"):
        assert secret not in joined, secret
