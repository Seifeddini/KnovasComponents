"""Acceptance tests of PMVP-23 (frozen): follow-up questions and past chats.

Plan FEAT-2026-11-19-platform-mvp W-06, W-07; spec items SPEC-platform-mvp-05 ('"Und wer hat unterschrieben?" after a
question on a contract -> an answer about the same contract') and -06 ("person B requests person A's chat id -> 404;
no content of A's chat in the reply or the log"); contract L2-PLATFORM-ASSISTANT POST-02, POST-03, POST-04. The
decisions of 2026-11-22 in docs/platform-mvp/work-orders/PMVP-23.md: D1 (a follow-up searches with the chat's last
question and the follow-up), D2 (the passage text is kept with the chat, owner only, deleted with it), D3 (a chat is
deleted 90 days after its last message, by a daily job that runs once however many workers there are, with an audit
row holding the count only), D4 (the sidebar lists the newest 20 and "Alle Gespraeche").

What these tests pin, and nothing else (the work order describes the rest):

- the addresses: ``POST /fragen`` (a question; ``answered`` or ``no_answer`` -> 303 to ``/fragen/<chat id>``),
  ``GET /fragen/<chat id>``, ``POST /fragen/<chat id>`` (a follow-up), ``POST /fragen/<chat id>/loeschen``,
  ``GET /fragen/<chat id>/quelle/<turn>/<number>`` (turn 1 is the first answer), ``GET /fragen/verlauf``; every
  form post carries the hidden field ``csrf_token``;
- the service as the page calls it: ``answer(question, history, search_text=...)`` on a follow-up, ``history`` a
  sequence of objects with ``role`` and ``text``, oldest first;
- ``identity.chats``: ``RETENTION_DAYS == 90`` and ``purge_expired_chats(conn)``, which returns the number of chats it
  deleted, or None when another worker holds the job or it has run today, and writes one ``audit_log`` row with the
  action ``assistant.chats.retention`` and the detail ``{"deleted": n}``.

The fake service stands in for the assistant (it records what it was given); the Knovas client is the shared
``FakeGraphApi``, whose ``document_readable`` is replaced here. Nothing here reaches a Core or a model.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import threading
import uuid

import pytest

pytest.importorskip("flask")

import conftest  # noqa: E402
from assistant.service import Passage, Reply, Sentence  # noqa: E402
from web_interface import assistant_routes  # noqa: E402

# --- one chat's text: every piece of it is the firm's, and must never reach a log line or another person ------------

Q1 = "Welche K\u00fcndigungsfrist gilt im Mietvertrag Seefeldstrasse?"
Q2 = "Und wer hat unterschrieben?"
VERTRAG = "corpus/Mandanten/Limmat/Mietvertrag_Seefeldstrasse_48.pdf"
NACHTRAG = "corpus/Mandanten/Limmat/Nachtrag_2_2023.pdf"
P1_TEXT = "Die K\u00fcndigungsfrist betr\u00e4gt sechs Monate auf Ende M\u00e4rz oder Ende September."
P2_TEXT = "Unterzeichnet am 3. Mai 2021 in Z\u00fcrich von Sabine Vogt und Reto Imhof."
PASSAGES = [
    Passage(number=1, pointer=VERTRAG, title="Mietvertrag_Seefeldstrasse_48.pdf", page=4, text=P1_TEXT, sentence=12),
    Passage(number=2, pointer=NACHTRAG, title="Nachtrag_2_2023.pdf", page=1, text=P2_TEXT, sentence=3),
]
S1 = "Die Frist betr\u00e4gt ein halbes Jahr."
QUOTE1 = "betr\u00e4gt sechs Monate auf Ende M\u00e4rz"
S2 = "Sabine Vogt hat den Vertrag unterzeichnet."
QUOTE2 = "von Sabine Vogt und Reto Imhof"
SECRETS = (Q1, Q2, "Seefeldstrasse", "Mietvertrag_Seefeldstrasse", "Nachtrag_2_2023", P1_TEXT, P2_TEXT, S1, S2, QUOTE1,
           QUOTE2, "Sabine Vogt", "Reto Imhof", "halbes Jahr")

UUID_PATH = re.compile(r"/fragen/([0-9a-fA-F-]{36})")


def answered(*sentences):
    sentences = sentences or (Sentence(text=S1, refs=(1,), quote=QUOTE1),)
    return Reply(status="answered", sentences=list(sentences), passages=list(PASSAGES), removed=0)


class FakeService:
    """The assistant as the chat pages call it. Records (question, history, search_text) per call."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def answer(self, question, history=(), *, search_text=None):
        self.calls.append((question, tuple(history), search_text))
        return self.reply


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")


@pytest.fixture
def service(workbench_app, switch_on):
    fake = FakeService(answered())
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    return fake


class Readable:
    """``document_readable`` on the app's one client: who asked, for which pointer."""

    def __init__(self, fake, monkeypatch):
        self.fake = fake
        self.asked = []
        self.unreadable = set()
        monkeypatch.setattr(fake, "document_readable", self, raising=False)

    def __call__(self, pointer):
        user = self.fake.principal_broker.current_user()
        self.asked.append((pointer, None if user is None else user.email))
        return pointer not in self.unreadable


@pytest.fixture
def readable(fake_graph, monkeypatch):
    return Readable(fake_graph, monkeypatch)


# --- helpers -----------------------------------------------------------------------------------------------------


def _ask(client, question=Q1, url="/fragen", token=True):
    data = {"question": question}
    if token:
        data["csrf_token"] = client._token
    return client.post(url, data=data)


def _chat_id(response):
    assert response.status_code == 303, f"a stored answer is a 303, got {response.status_code}"
    found = UUID_PATH.search(response.headers["Location"])
    assert found, "the 303 goes to /fragen/<chat id>"
    return found.group(1)


def _path(response):
    return response.headers["Location"].split("#", 1)[0]


def _text(page):
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", page))


def _head(statement):
    """A statement as the page shows it before its reference: the reference stands before the full stop."""
    return statement.rstrip(".!?")


def _count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def _messages(conn, chat_id):
    return _count(conn, "SELECT count(*) FROM assistant_messages WHERE chat_id = %s", chat_id)


def _references(conn, chat_id):
    return _count(conn, "SELECT count(*) FROM assistant_references r JOIN assistant_messages m ON m.id = r.message_id "
                        "WHERE m.chat_id = %s", chat_id)


def _chats_of(conn, person):
    return _count(conn, "SELECT count(*) FROM assistant_chats WHERE owner_user_id = %s", person.id)


def _no_secret(text, what):
    for secret in SECRETS:
        assert secret not in text, f"{what} holds the firm's text ({secret[:12]}...)"


def _gate_open(client, pointer):
    """Whether the file route lets this person past the wall for one pointer (the grant of PMVP-22a)."""
    answer = client.get(f"/api/document/{pointer}/download", query_string={"path": pointer}).get_json() or {}
    return answer.get("error") != "Not found"


# --- a question becomes its owner's chat -------------------------------------------------------------------------


def test_a_question_becomes_a_chat_of_its_owner(alice_client, alice, platform_db, service):
    response = _ask(alice_client)
    chat_id = _chat_id(response)
    from identity.chats import ChatStore

    chat = ChatStore(platform_db).get_chat(chat_id, owner=alice.id)
    assert [m.role for m in chat.messages] == ["user", "assistant"]
    assert chat.messages[0].text == Q1
    assert chat.title == Q1
    page = alice_client.get(_path(response))
    assert page.status_code == 200
    shown = _text(page.get_data(as_text=True))
    assert Q1 in shown and _head(S1) in shown and QUOTE1 in shown


def test_no_answer_is_a_chat_too(alice_client, alice, platform_db, service):
    service.reply = Reply(status="no_answer", passages=list(PASSAGES), message="Dazu finde ich nichts in Ihren Quellen")
    _chat_id(_ask(alice_client))
    assert _chats_of(platform_db, alice) == 1


@pytest.mark.parametrize("status", ["busy", "failed"])
def test_busy_and_failed_store_nothing(alice_client, alice, platform_db, service, status):
    service.reply = Reply(status=status, message="Der Knovas Assistent ist gerade ausgelastet.")
    response = _ask(alice_client)
    assert response.status_code == 200
    assert _chats_of(platform_db, alice) == 0


# --- SPEC-05 and D1: a follow-up keeps the chat's context ---------------------------------------------------------


def test_a_follow_up_is_asked_with_the_chat_so_far(alice_client, alice, platform_db, service):
    chat_id = _chat_id(_ask(alice_client))
    service.reply = answered(Sentence(text=S2, refs=(2,), quote=QUOTE2))
    response = _ask(alice_client, Q2, url=f"/fragen/{chat_id}")
    assert _chat_id(response) == chat_id
    question, history, search_text = service.calls[-1]
    assert question == Q2
    assert search_text and Q1 in search_text and Q2 in search_text           # D1
    assert [turn.role for turn in history] == ["user", "assistant"]
    assert history[0].text == Q1
    assert S1 in history[1].text
    for turn in history:                                                     # POST-02, D2: never sent again
        for kept in (P1_TEXT, P2_TEXT, QUOTE1):
            assert kept not in turn.text
    assert _messages(platform_db, chat_id) == 4
    shown = _text(alice_client.get(f"/fragen/{chat_id}").get_data(as_text=True))
    assert Q1 in shown and Q2 in shown and _head(S1) in shown and _head(S2) in shown


def test_the_turns_reach_the_service_in_order(alice_client, service):
    chat_id = _chat_id(_ask(alice_client))
    _ask(alice_client, Q2, url=f"/fragen/{chat_id}")
    _ask(alice_client, "Und ab wann gilt das?", url=f"/fragen/{chat_id}")
    _, history, search_text = service.calls[-1]
    assert [turn.role for turn in history] == ["user", "assistant", "user", "assistant"]
    assert [history[0].text, history[2].text] == [Q1, Q2]
    assert Q2 in search_text and "Und ab wann gilt das?" in search_text


@pytest.mark.parametrize("status", ["busy", "failed"])
def test_a_follow_up_that_fails_stores_nothing(alice_client, platform_db, service, status):
    chat_id = _chat_id(_ask(alice_client))
    service.reply = Reply(status=status, message="Der Knovas Assistent ist gerade ausgelastet.")
    response = _ask(alice_client, Q2, url=f"/fragen/{chat_id}")
    assert response.status_code == 200
    assert _messages(platform_db, chat_id) == 2


# --- SPEC-06: another person's chat is a missing one ----------------------------------------------------------------


def _requests(chat_id):
    return {
        "open": ("GET", f"/fragen/{chat_id}", None),
        "follow-up": ("POST", f"/fragen/{chat_id}", {"question": Q2}),
        "delete": ("POST", f"/fragen/{chat_id}/loeschen", {}),
        "source": ("GET", f"/fragen/{chat_id}/quelle/1/1", None),
    }


def _send(client, method, url, data):
    if method == "GET":
        return client.get(url)
    return client.post(url, data={**data, "csrf_token": client._token})


@pytest.mark.parametrize("what", ["open", "follow-up", "delete", "source"])
def test_another_persons_chat_answers_like_a_missing_one(alice_client, bob_client, platform_db, service, readable,
                                                         caplog, what):
    chat_id = _chat_id(_ask(alice_client))
    calls_before, references_before = len(service.calls), _references(platform_db, chat_id)
    readable.asked.clear()
    for name in ("web_interface", "assistant", "identity"):
        caplog.set_level(logging.DEBUG, logger=name)
    caplog.set_level(logging.DEBUG)
    method, url, data = _requests(chat_id)[what]
    foreign = _send(bob_client, method, url, data)
    missing = _send(bob_client, method, url.replace(chat_id, str(uuid.uuid4())), data)
    malformed = _send(bob_client, method, url.replace(chat_id, "kein-chat"), data)
    assert foreign.status_code == missing.status_code == malformed.status_code == 404
    _no_secret(foreign.get_data(as_text=True), "the answer to another person")
    _no_secret("\n".join(r.getMessage() for r in caplog.records), "a log line")
    assert _messages(platform_db, chat_id) == 2 and _references(platform_db, chat_id) == references_before
    assert len(service.calls) == calls_before
    assert readable.asked == []
    assert not _gate_open(bob_client, VERTRAG)


@pytest.mark.parametrize("what", ["open", "follow-up", "delete", "source"])
def test_signed_out_is_sent_to_sign_in(anon_client, alice_client, service, what):
    chat_id = _chat_id(_ask(alice_client))
    method, url, data = _requests(chat_id)[what]
    response = anon_client.get(url) if method == "GET" else anon_client.post(url, data=data)
    assert response.status_code == 302 and "/login" in response.headers["Location"]


@pytest.mark.parametrize("what", ["follow-up", "delete"])
def test_a_form_without_its_token_changes_nothing(workbench_app, alice, alice_client, platform_db, service, what):
    chat_id = _chat_id(_ask(alice_client))
    calls_before = len(service.calls)
    no_token = conftest._signed_in(workbench_app, alice.email, with_csrf=False)
    _, url, data = _requests(chat_id)[what]
    assert no_token.post(url, data=data).status_code == 400
    assert alice_client.post(url, data={**data, "csrf_token": "falsch"}).status_code == 400
    assert _messages(platform_db, chat_id) == 2 and len(service.calls) == calls_before


def test_switched_off_the_chat_addresses_answer_404(alice_client, service, monkeypatch):
    chat_id = _chat_id(_ask(alice_client))
    monkeypatch.delenv(assistant_routes.SWITCH_ENV)
    for what, (method, url, data) in _requests(chat_id).items():
        assert _send(alice_client, method, url, data).status_code == 404, what
    assert alice_client.get("/fragen/verlauf").status_code == 404


# --- the list: her own chats only; the newest 20 in the sidebar (D4) ----------------------------------------------


def _store_chats(conn, person, count, prefix):
    from identity.chats import ChatStore

    store, ids = ChatStore(conn), []
    for index in range(count):
        chat_id = store.create_chat(owner=person.id, title=f"{prefix} {index:02d}")
        store.add_message(chat_id, owner=person.id, role="user", text=f"{prefix} {index:02d}?")
        conn.execute("UPDATE assistant_chats SET updated_at = now() - make_interval(mins => %s) WHERE id = %s",
                     (count - index, chat_id))
        ids.append(str(chat_id))
    return ids  # oldest first


def test_the_list_holds_her_own_chats_only(alice, bob, alice_client, bob_client, platform_db, service):
    _store_chats(platform_db, alice, 3, "Alicefrage")
    _store_chats(platform_db, bob, 2, "Bobfrage")
    alice_list = _text(alice_client.get("/fragen/verlauf").get_data(as_text=True))
    bob_list = _text(bob_client.get("/fragen/verlauf").get_data(as_text=True))
    assert "Alicefrage 02" in alice_list and "Bobfrage" not in alice_list
    assert "Bobfrage 01" in bob_list and "Alicefrage" not in bob_list
    assert "Alicefrage" not in _text(bob_client.get("/").get_data(as_text=True))


def test_the_sidebar_lists_the_newest_20_and_offers_all(alice, alice_client, platform_db, service):
    ids = _store_chats(platform_db, alice, 25, "Frage")
    page = alice_client.get("/").get_data(as_text=True)
    linked = set(UUID_PATH.findall(page))
    assert set(ids[5:]) <= linked, "the newest 20 chats are listed"
    assert not set(ids[:5]) & linked, "the 5 oldest are not"
    assert 'href="/fragen/verlauf"' in page


def test_a_title_is_shown_as_text(alice, alice_client, platform_db, service):
    from identity.chats import ChatStore

    store = ChatStore(platform_db)
    store.create_chat(owner=alice.id, title="<script>alert(1)</script>")
    for page in (alice_client.get("/fragen/verlauf"), alice_client.get("/")):
        body = page.get_data(as_text=True)
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_list_chats_takes_a_limit_and_an_offset(alice, platform_db):
    from identity.chats import ChatStore

    ids = _store_chats(platform_db, alice, 7, "Frage")
    store = ChatStore(platform_db)
    assert [str(c.id) for c in store.list_chats(owner=alice.id)] == ids[::-1]
    assert [str(c.id) for c in store.list_chats(owner=alice.id, limit=3)] == ids[::-1][:3]
    assert [str(c.id) for c in store.list_chats(owner=alice.id, limit=3, offset=3)] == ids[::-1][3:6]


# --- delete ---------------------------------------------------------------------------------------------------------


def test_delete_removes_the_chat_its_messages_and_references(alice_client, platform_db, service):
    chat_id = _chat_id(_ask(alice_client))
    assert _references(platform_db, chat_id) == 2
    response = _send(alice_client, "POST", f"/fragen/{chat_id}/loeschen", {})
    assert response.status_code == 303
    assert _count(platform_db, "SELECT count(*) FROM assistant_chats WHERE id = %s", chat_id) == 0
    assert _messages(platform_db, chat_id) == 0 and _references(platform_db, chat_id) == 0
    assert _count(platform_db, "SELECT count(*) FROM assistant_references WHERE text = %s", P1_TEXT) == 0
    assert _send(alice_client, "POST", f"/fragen/{chat_id}/loeschen", {}).status_code == 404


# --- D2: the passage is kept with the chat -------------------------------------------------------------------------


def test_the_passages_are_kept_with_the_answer(alice_client, platform_db, service):
    chat_id = _chat_id(_ask(alice_client))
    rows = platform_db.execute(
        "SELECT r.number, r.pointer, r.page, r.text FROM assistant_references r "
        "JOIN assistant_messages m ON m.id = r.message_id WHERE m.chat_id = %s AND m.role = 'assistant' "
        "ORDER BY r.number", (chat_id,)).fetchall()
    assert [tuple(row) for row in rows] == [(1, VERTRAG, 4, P1_TEXT), (2, NACHTRAG, 1, P2_TEXT)]


# --- opening a document from a stored answer ------------------------------------------------------------------------


def test_a_stored_reference_opens_after_the_core_says_yes(alice_client, readable, service):
    chat_id = _chat_id(_ask(alice_client))
    response = alice_client.get(f"/fragen/{chat_id}/quelle/1/2")
    assert response.status_code in (302, 303)
    assert readable.asked == [(NACHTRAG, "alice@kanzlei.ch")]


def test_a_stored_reference_the_core_refuses_does_not_open(alice_client, readable, service):
    chat_id = _chat_id(_ask(alice_client))
    readable.unreadable.add(NACHTRAG)
    assert alice_client.get(f"/fragen/{chat_id}/quelle/1/2").status_code == 404
    for turn, number in ((1, 3), (2, 1), (0, 1)):
        assert alice_client.get(f"/fragen/{chat_id}/quelle/{turn}/{number}").status_code == 404


# --- D3: 90 days after the last message, once a day, however many workers ------------------------------------------


def _age(conn, chat_id, days):
    conn.execute("UPDATE assistant_messages SET created_at = now() - make_interval(days => %s) WHERE chat_id = %s",
                 (days, chat_id))
    conn.execute("UPDATE assistant_chats SET updated_at = now() - make_interval(days => %s) WHERE id = %s",
                 (days, chat_id))


def _chat_with_answer(conn, person, title):
    from identity.chats import ChatStore

    store = ChatStore(conn)
    chat_id = store.create_chat(owner=person.id, title=title)
    store.add_message(chat_id, owner=person.id, role="user", text=f"{title}?")
    store.add_message(chat_id, owner=person.id, role="assistant", text="{}",
                      references=[{"number": 1, "pointer": VERTRAG, "page": 4, "title": "T", "text": P1_TEXT}])
    return str(chat_id)


def _retention_rows(conn):
    return conn.execute("SELECT detail FROM audit_log WHERE action = 'assistant.chats.retention' "
                        "ORDER BY occurred_at").fetchall()


def test_retention_is_90_days():
    from identity import chats

    assert chats.RETENTION_DAYS == 90


def test_a_chat_91_days_after_its_last_message_is_deleted_one_at_89_is_not(alice, bob, platform_db):
    from identity.chats import purge_expired_chats

    old = _chat_with_answer(platform_db, alice, "Alt")
    young = _chat_with_answer(platform_db, bob, "Jung")
    long_running = _chat_with_answer(platform_db, alice, "Lang")
    _age(platform_db, old, 91)
    _age(platform_db, young, 89)
    platform_db.execute("UPDATE assistant_chats SET created_at = now() - make_interval(days => 200) WHERE id = %s",
                        (long_running,))
    assert purge_expired_chats(platform_db) == 1
    left = {str(r[0]) for r in platform_db.execute("SELECT id FROM assistant_chats").fetchall()}
    assert left == {young, long_running}
    assert _messages(platform_db, old) == 0 and _references(platform_db, old) == 0
    rows = _retention_rows(platform_db)
    assert len(rows) == 1
    detail = rows[0][0] if isinstance(rows[0][0], dict) else json.loads(rows[0][0])
    assert detail == {"deleted": 1}


def test_the_job_runs_once_a_day(alice, platform_db):
    from identity.chats import purge_expired_chats

    _age(platform_db, _chat_with_answer(platform_db, alice, "Alt"), 95)
    assert purge_expired_chats(platform_db) == 1
    _age(platform_db, _chat_with_answer(platform_db, alice, "Noch einer"), 95)
    assert purge_expired_chats(platform_db) is None
    assert len(_retention_rows(platform_db)) == 1


def test_two_workers_run_the_job_once(alice, platform_db):
    import psycopg

    from identity.chats import purge_expired_chats

    for index in range(3):
        _age(platform_db, _chat_with_answer(platform_db, alice, f"Alt {index}"), 120)
    schema = platform_db.execute("SELECT current_schema()").fetchone()[0]
    start = threading.Barrier(2)
    results, errors = [], []

    def worker():
        try:
            with psycopg.connect(conftest.PLATFORM_DB_TEST_DSN, autocommit=True) as conn:
                conn.execute(f'SET search_path TO "{schema}"')
                start.wait(timeout=10)
                results.append(purge_expired_chats(conn))
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert sorted(results, key=lambda r: (r is None, r)) == [3, None]
    assert len(_retention_rows(platform_db)) == 1
    assert _chats_of(platform_db, alice) == 0


def test_the_retention_audit_row_holds_the_count_only(alice, platform_db):
    from identity.chats import purge_expired_chats

    chat_id = _chat_with_answer(platform_db, alice, "Geheimer Titel")
    _age(platform_db, chat_id, 100)
    purge_expired_chats(platform_db)
    row = platform_db.execute("SELECT detail::text, coalesce(target_id, '') FROM audit_log "
                              "WHERE action = 'assistant.chats.retention'").fetchone()
    assert "Geheimer" not in row[0] and chat_id not in row[0] and chat_id not in row[1]
    assert str(alice.id) not in row[0]


# --- POST-04: no customer text in a log line, DEBUG included (FU-46) ------------------------------------------------


def test_no_customer_text_reaches_a_log_line(alice, alice_client, platform_db, service, readable, caplog):
    for name in ("web_interface", "assistant", "identity", "knovas_client", "document_grants"):
        caplog.set_level(logging.DEBUG, logger=name)
    caplog.set_level(logging.DEBUG)
    chat_id = _chat_id(_ask(alice_client))
    service.reply = answered(Sentence(text=S2, refs=(2,), quote=QUOTE2))
    _ask(alice_client, Q2, url=f"/fragen/{chat_id}")
    alice_client.get(f"/fragen/{chat_id}")
    alice_client.get("/fragen/verlauf")
    alice_client.get("/")
    alice_client.get(f"/fragen/{chat_id}/quelle/1/1")
    _send(alice_client, "POST", f"/fragen/{chat_id}/loeschen", {})
    from identity.chats import purge_expired_chats

    _age(platform_db, _chat_with_answer(platform_db, alice, Q1), 100)
    purge_expired_chats(platform_db)
    lines = [record.getMessage() for record in caplog.records]
    lines += [record.exc_text or "" for record in caplog.records]
    assert lines, "the run logged nothing at all"
    _no_secret("\n".join(lines), "a log line")
