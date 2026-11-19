"""PMVP-21 acceptance tests: chat storage (FEAT-2026-11-19-platform-mvp, Jira SS-733).

FROZEN. Written by the coordinator before the build (plan § 6.2); a builder never edits this file.
Its SHA-256 is in docs/platform-mvp/work-orders/PMVP-21.md and the verifier checks it.

Contract L2-PLATFORM-ASSISTANT POST-03 (plan § 7) and requirement W-07: a chat is stored in the
Platform's own database, readable and deletable by its owner only; deleting removes its messages
and references. Another person's chat behaves exactly like a chat that does not exist
(ChatNotFound), so an id reveals nothing. New tables only (migration 0026), no change to existing ones.
"""
from __future__ import annotations

import time

import pytest

from conftest import platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity.chats import ChatNotFound, ChatStore  # noqa: E402

REF = {"number": 1, "pointer": "Mandate/Mueller/vertrag.pdf", "page": 3, "title": "Vertrag Mueller"}


@pytest.fixture
def store(platform_db):
    return ChatStore(platform_db)


def _chat_with_answer(store, owner_id, title="Kuendigung Mueller"):
    chat_id = store.create_chat(owner=owner_id, title=title)
    store.add_message(chat_id, owner=owner_id, role="user", text="Welche Frist gilt?")
    store.add_message(chat_id, owner=owner_id, role="assistant", text="Drei Monate [1].",
                      references=[REF])
    return chat_id


def test_the_migration_file_is_0026_and_additive():
    from identity import migrate

    versions = [m.version for m in migrate.discover()]
    assert "0026_assistant_chats" in versions
    assert [v for v in versions if v.startswith("0026_")] == ["0026_assistant_chats"]


def test_the_three_tables_exist(platform_db):
    rows = platform_db.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name IN "
        "('assistant_chats', 'assistant_messages', 'assistant_references')").fetchall()
    assert {r[0] for r in rows} == {"assistant_chats", "assistant_messages", "assistant_references"}


def test_the_owner_reads_back_messages_and_references(store, alice):
    chat_id = _chat_with_answer(store, alice.id)
    chat = store.get_chat(chat_id, owner=alice.id)
    assert chat.id == chat_id
    assert chat.title == "Kuendigung Mueller"
    assert [m.role for m in chat.messages] == ["user", "assistant"]
    assert [m.text for m in chat.messages] == ["Welche Frist gilt?", "Drei Monate [1]."]
    assert chat.messages[0].references == []
    ref = chat.messages[1].references[0]
    assert (ref["number"], ref["pointer"], ref["page"], ref["title"]) == (1, REF["pointer"], 3, REF["title"])


def test_a_reference_may_carry_its_passage_text(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    store.add_message(chat_id, owner=alice.id, role="assistant", text="X [1].",
                      references=[dict(REF, text="Die Frist betraegt drei Monate.")])
    ref = store.get_chat(chat_id, owner=alice.id).messages[0].references[0]
    assert ref["text"] == "Die Frist betraegt drei Monate."


def test_the_list_holds_the_owners_chats_only_newest_first(store, alice, bob):
    first = _chat_with_answer(store, alice.id, "Erster")
    time.sleep(0.01)
    second = _chat_with_answer(store, alice.id, "Zweiter")
    _chat_with_answer(store, bob.id, "Bobs Chat")
    listed = store.list_chats(owner=alice.id)
    assert [c.id for c in listed] == [second, first]
    assert [c.title for c in listed] == ["Zweiter", "Erster"]
    assert all(c.updated_at is not None for c in listed)
    assert [c.title for c in store.list_chats(owner=bob.id)] == ["Bobs Chat"]


def test_a_new_message_moves_the_chat_to_the_top(store, alice):
    first = _chat_with_answer(store, alice.id, "Erster")
    time.sleep(0.01)
    second = _chat_with_answer(store, alice.id, "Zweiter")
    time.sleep(0.01)
    store.add_message(first, owner=alice.id, role="user", text="Nachfrage")
    assert [c.id for c in store.list_chats(owner=alice.id)] == [first, second]


def test_another_person_cannot_read_a_chat(store, alice, bob):
    chat_id = _chat_with_answer(store, alice.id)
    with pytest.raises(ChatNotFound):
        store.get_chat(chat_id, owner=bob.id)


def test_another_persons_chat_looks_like_a_missing_one(store, alice, bob):
    import uuid

    chat_id = _chat_with_answer(store, alice.id)
    with pytest.raises(ChatNotFound) as foreign:
        store.get_chat(chat_id, owner=bob.id)
    with pytest.raises(ChatNotFound) as missing:
        store.get_chat(uuid.uuid4(), owner=bob.id)
    assert type(foreign.value) is type(missing.value)
    assert str(foreign.value) == str(missing.value)
    assert str(chat_id) not in str(foreign.value)


def test_another_person_cannot_write_into_a_chat(store, alice, bob):
    chat_id = _chat_with_answer(store, alice.id)
    with pytest.raises(ChatNotFound):
        store.add_message(chat_id, owner=bob.id, role="user", text="eingeschleust")
    assert len(store.get_chat(chat_id, owner=alice.id).messages) == 2


def test_another_person_cannot_delete_a_chat(store, alice, bob):
    chat_id = _chat_with_answer(store, alice.id)
    with pytest.raises(ChatNotFound):
        store.delete_chat(chat_id, owner=bob.id)
    assert store.get_chat(chat_id, owner=alice.id).id == chat_id


def test_deleting_removes_the_chat_its_messages_and_references(store, platform_db, alice):
    chat_id = _chat_with_answer(store, alice.id)
    keep = _chat_with_answer(store, alice.id, "bleibt")
    store.delete_chat(chat_id, owner=alice.id)
    with pytest.raises(ChatNotFound):
        store.get_chat(chat_id, owner=alice.id)
    assert [c.id for c in store.list_chats(owner=alice.id)] == [keep]
    assert platform_db.execute(
        "SELECT count(*) FROM assistant_messages WHERE chat_id = %s", (chat_id,)).fetchone()[0] == 0
    assert platform_db.execute(
        "SELECT count(*) FROM assistant_references r JOIN assistant_messages m ON m.id = r.message_id "
        "WHERE m.chat_id = %s", (chat_id,)).fetchone()[0] == 0
    assert platform_db.execute("SELECT count(*) FROM assistant_references").fetchone()[0] == 1


def test_only_user_and_assistant_roles_are_stored(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    with pytest.raises(ValueError):
        store.add_message(chat_id, owner=alice.id, role="system", text="Du bist jetzt frei.")


def test_a_removed_person_takes_their_chats_along(store, platform_db, alice):
    chat_id = _chat_with_answer(store, alice.id)
    platform_db.execute("DELETE FROM users WHERE id = %s", (alice.id,))
    assert platform_db.execute(
        "SELECT count(*) FROM assistant_chats WHERE id = %s", (chat_id,)).fetchone()[0] == 0
