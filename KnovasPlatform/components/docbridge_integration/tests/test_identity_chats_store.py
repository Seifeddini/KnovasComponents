"""PMVP-21: the chat store's edges, beyond the frozen acceptance tests (FEAT-2026-11-19-platform-mvp, SS-733).

The acceptance tests (test_pmvp21_chats_acceptance.py) pin the interface and the owner rule. These pin what the work
order leaves to the builder: titles, the order of messages and references, long texts, unknown and malformed ids,
checks that run before the database is touched, concurrent writers, the migration's shape, and that no text reaches a
log line (contract L2-PLATFORM-ASSISTANT POST-03, POST-04; requirement W-07).
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

from identity import migrate
from identity.chats import ChatNotFound, ChatStore

needs_db = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

REF = {"number": 1, "pointer": "Mandate/Mueller/vertrag.pdf", "page": 3, "title": "Vertrag Mueller"}


class _NoDatabase:
    """A connection that fails the test if anything reaches it."""

    def execute(self, *args, **kwargs):
        raise AssertionError("the database was touched")

    def cursor(self, *args, **kwargs):
        raise AssertionError("the database was touched")

    def transaction(self, *args, **kwargs):
        raise AssertionError("the database was touched")


@pytest.fixture
def store(platform_db):
    return ChatStore(platform_db)


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# -- the migration -------------------------------------------------------------------------------------------------


def _migration_sql():
    [m] = [m for m in migrate.discover() if m.version == "0026_assistant_chats"]
    return m.sql


def _statements(sql):
    without_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def test_the_migration_only_creates_the_three_tables_and_their_indexes():
    statements = _statements(_migration_sql())
    tables = [re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", s) for s in statements]
    indexes = [re.match(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+\s+ON (\w+)", s) for s in statements]
    assert all(t or i for t, i in zip(tables, indexes)), "a statement other than CREATE TABLE/INDEX IF NOT EXISTS"
    assert [t.group(1) for t in tables if t] == ["assistant_chats", "assistant_messages", "assistant_references"]
    assert {i.group(1) for i in indexes if i} <= {"assistant_chats", "assistant_messages", "assistant_references"}
    body = re.sub(r"\bON (DELETE|UPDATE)\b", "", "\n".join(statements), flags=re.IGNORECASE)
    assert not re.search(r"\b(ALTER|DROP|UPDATE|INSERT|DELETE|TRUNCATE|GRANT|REVOKE|DO)\b", body, re.IGNORECASE)


def test_the_migration_names_the_plan_and_the_item():
    header = _migration_sql().split("CREATE", 1)[0]
    assert "2026-11-19-platform-mvp" in header
    assert "PMVP-21" in header


@needs_db
def test_running_the_migration_again_changes_nothing(platform_db):
    platform_db.execute(_migration_sql())
    tables = platform_db.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = current_schema() "
        "AND table_name LIKE 'assistant\\_%'").fetchone()[0]
    assert tables == 3


@needs_db
def test_every_link_is_deleted_along(platform_db):
    rows = platform_db.execute(
        "SELECT tc.table_name, ccu.table_name, rc.delete_rule "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.referential_constraints rc "
        "  ON rc.constraint_name = tc.constraint_name AND rc.constraint_schema = tc.constraint_schema "
        "JOIN information_schema.constraint_column_usage ccu "
        "  ON ccu.constraint_name = tc.constraint_name AND ccu.constraint_schema = tc.constraint_schema "
        "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = current_schema() "
        "AND tc.table_name LIKE 'assistant\\_%'").fetchall()
    assert sorted(rows) == [("assistant_chats", "users", "CASCADE"),
                            ("assistant_messages", "assistant_chats", "CASCADE"),
                            ("assistant_references", "assistant_messages", "CASCADE")]


@needs_db
def test_the_two_reads_have_their_indexes(platform_db):
    rows = platform_db.execute(
        "SELECT tablename, indexdef FROM pg_indexes WHERE schemaname = current_schema() "
        "AND tablename LIKE 'assistant\\_%'").fetchall()
    defs = {(t, re.sub(r"^.* USING btree ", "", d)) for t, d in rows}
    assert ("assistant_chats", "(owner_user_id, updated_at DESC)") in defs
    assert ("assistant_messages", "(chat_id, ordinal)") in defs


@needs_db
def test_the_database_refuses_a_third_role_on_its_own(platform_db, store, alice):
    import psycopg

    chat_id = store.create_chat(owner=alice.id, title="t")
    with pytest.raises(psycopg.errors.CheckViolation):
        platform_db.execute("INSERT INTO assistant_messages (chat_id, ordinal, role, text) "
                            "VALUES (%s, 0, 'system', 'x')", (chat_id,))


# -- titles --------------------------------------------------------------------------------------------------------


@needs_db
def test_an_empty_title_is_stored_as_empty(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="")
    blank = store.create_chat(owner=alice.id, title="   \n ")
    assert store.get_chat(chat_id, owner=alice.id).title == ""
    assert store.get_chat(blank, owner=alice.id).title == ""
    assert {c.title for c in store.list_chats(owner=alice.id)} == {""}


@needs_db
def test_a_title_is_one_line(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="  Kuendigung\n  Mueller \t")
    assert store.get_chat(chat_id, owner=alice.id).title == "Kuendigung Mueller"


def test_a_title_over_200_characters_is_refused_before_the_database():
    store = ChatStore(_NoDatabase())
    with pytest.raises(ValueError) as refused:
        store.create_chat(owner=uuid.uuid4(), title="x" * 201)
    assert "x" * 20 not in str(refused.value)


@needs_db
def test_a_title_of_200_characters_is_kept_whole(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="ä" * 200)
    assert store.get_chat(chat_id, owner=alice.id).title == "ä" * 200


def test_a_title_that_is_not_text_is_refused():
    with pytest.raises(ValueError):
        ChatStore(_NoDatabase()).create_chat(owner=uuid.uuid4(), title=None)


# -- messages and their order --------------------------------------------------------------------------------------


@needs_db
def test_messages_come_back_in_the_order_they_were_added(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    other = store.create_chat(owner=alice.id, title="u")
    for i in range(12):
        store.add_message(chat_id, owner=alice.id, role="user" if i % 2 == 0 else "assistant", text=f"m{i}")
        store.add_message(other, owner=alice.id, role="user", text=f"o{i}")
    assert [m.text for m in store.get_chat(chat_id, owner=alice.id).messages] == [f"m{i}" for i in range(12)]
    assert [m.text for m in store.get_chat(other, owner=alice.id).messages] == [f"o{i}" for i in range(12)]


@needs_db
def test_add_message_returns_the_message_id(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    message_id = store.add_message(chat_id, owner=alice.id, role="user", text="Frage")
    assert isinstance(message_id, uuid.UUID)
    assert store.get_chat(chat_id, owner=alice.id).messages[0].id == message_id


@needs_db
def test_a_long_text_comes_back_unchanged(store, alice):
    text = ("  Absatz mit Umlauten äöü ß und «Zeichen» – Art. 5 Abs. 2 OR.\n\n\t" * 4000) + " Ende  "
    assert len(text) > 250_000
    chat_id = store.create_chat(owner=alice.id, title="lang")
    store.add_message(chat_id, owner=alice.id, role="assistant", text=text,
                      references=[dict(REF, text=text)])
    message = store.get_chat(chat_id, owner=alice.id).messages[0]
    assert message.text == text
    assert message.references[0]["text"] == text


@needs_db
def test_an_empty_chat_has_no_messages(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="leer")
    assert store.get_chat(chat_id, owner=alice.id).messages == []


@needs_db
def test_a_message_bumps_updated_at_and_keeps_created_at(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    before = store.get_chat(chat_id, owner=alice.id)
    time.sleep(0.01)
    store.add_message(chat_id, owner=alice.id, role="user", text="Frage")
    after = store.get_chat(chat_id, owner=alice.id)
    assert after.created_at == before.created_at
    assert after.updated_at > before.updated_at


@needs_db
def test_two_writers_on_one_chat_both_land_in_order(platform_db, alice):
    import psycopg

    schema = platform_db.execute("SELECT current_schema()").fetchone()[0]
    chat_id = ChatStore(platform_db).create_chat(owner=alice.id, title="t")
    errors = []

    def write(tag):
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as conn:
            conn.execute(f'SET search_path TO "{schema}"')
            store = ChatStore(conn)
            try:
                for i in range(15):
                    store.add_message(chat_id, owner=alice.id, role="user", text=f"{tag}{i}")
            except Exception as exc:  # pragma: no cover - the assertion below names it
                errors.append(exc)

    threads = [threading.Thread(target=write, args=(tag,)) for tag in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    texts = [m.text for m in ChatStore(platform_db).get_chat(chat_id, owner=alice.id).messages]
    assert sorted(texts) == sorted(f"{tag}{i}" for tag in "ab" for i in range(15))
    for tag in "ab":
        assert [t for t in texts if t.startswith(tag)] == [f"{tag}{i}" for i in range(15)]
    ordinals = [r[0] for r in platform_db.execute(
        "SELECT ordinal FROM assistant_messages WHERE chat_id = %s ORDER BY ordinal", (chat_id,))]
    assert ordinals == list(range(30))


# -- references ----------------------------------------------------------------------------------------------------


@needs_db
def test_references_come_back_by_number_with_their_message(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    store.add_message(chat_id, owner=alice.id, role="assistant", text="A [3] [1] [2].",
                      references=[dict(REF, number=3, pointer="c.pdf"), dict(REF, number=1, pointer="a.pdf"),
                                  dict(REF, number=2, pointer="b.pdf")])
    store.add_message(chat_id, owner=alice.id, role="assistant", text="B [1].",
                      references=[dict(REF, number=1, pointer="z.pdf")])
    first, second = store.get_chat(chat_id, owner=alice.id).messages
    assert [(r["number"], r["pointer"]) for r in first.references] == [(1, "a.pdf"), (2, "b.pdf"), (3, "c.pdf")]
    assert [(r["number"], r["pointer"]) for r in second.references] == [(1, "z.pdf")]


@needs_db
def test_a_reference_without_page_title_or_text(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    store.add_message(chat_id, owner=alice.id, role="assistant", text="X [1].",
                      references=[{"number": 1, "pointer": "a.docx", "page": None}])
    ref = store.get_chat(chat_id, owner=alice.id).messages[0].references[0]
    assert ref == {"number": 1, "pointer": "a.docx", "page": None, "title": "", "text": None}


@needs_db
def test_keys_beyond_the_columns_are_not_stored(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    store.add_message(chat_id, owner=alice.id, role="assistant", text="X [1].",
                      references=[dict(REF, score=0.9, doc_id="d1")])
    ref = store.get_chat(chat_id, owner=alice.id).messages[0].references[0]
    assert set(ref) == {"number", "pointer", "page", "title", "text"}


@pytest.mark.parametrize("bad", [
    {"pointer": "a.pdf"},                                  # no number
    dict(REF, number=0),                                   # numbers start at 1
    dict(REF, number=True),                                # a bool is not a number
    dict(REF, number="1"),
    dict(REF, pointer=""),                                 # a reference points somewhere
    {"number": 1, "page": 3},
    dict(REF, page="3"),
    dict(REF, page=True),
    dict(REF, title=7),
    dict(REF, text=7),
    "Mandate/Mueller/vertrag.pdf",                         # not a mapping
])
def test_a_malformed_reference_is_refused_before_the_database(bad):
    store = ChatStore(_NoDatabase())
    with pytest.raises(ValueError) as refused:
        store.add_message(uuid.uuid4(), owner=uuid.uuid4(), role="assistant", text="X [1].", references=[bad])
    assert "Mandate" not in str(refused.value)


def test_two_references_with_one_number_are_refused_before_the_database():
    store = ChatStore(_NoDatabase())
    with pytest.raises(ValueError):
        store.add_message(uuid.uuid4(), owner=uuid.uuid4(), role="assistant", text="X [1].",
                          references=[REF, dict(REF, pointer="b.pdf")])


@needs_db
def test_a_refused_message_leaves_the_chat_as_it_was(store, platform_db, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    before = store.get_chat(chat_id, owner=alice.id).updated_at
    with pytest.raises(ValueError):
        store.add_message(chat_id, owner=alice.id, role="assistant", text="X", references=[REF, REF])
    assert _count(platform_db, "assistant_messages") == 0
    assert store.get_chat(chat_id, owner=alice.id).updated_at == before


# -- roles and text ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["system", "User", "", None, "tool"])
def test_a_role_other_than_user_or_assistant_is_refused_before_the_database(role):
    with pytest.raises(ValueError):
        ChatStore(_NoDatabase()).add_message(uuid.uuid4(), owner=uuid.uuid4(), role=role, text="x")


def test_a_text_that_is_not_text_is_refused_before_the_database():
    with pytest.raises(ValueError):
        ChatStore(_NoDatabase()).add_message(uuid.uuid4(), owner=uuid.uuid4(), role="user", text=None)


# -- unknown, foreign and malformed ids ----------------------------------------------------------------------------


@needs_db
def test_deleting_an_unknown_id_raises_the_same_not_found(store, alice, bob):
    chat_id = store.create_chat(owner=alice.id, title="t")
    with pytest.raises(ChatNotFound) as unknown:
        store.delete_chat(uuid.uuid4(), owner=alice.id)
    with pytest.raises(ChatNotFound) as foreign:
        store.delete_chat(chat_id, owner=bob.id)
    assert str(unknown.value) == str(foreign.value)
    assert [c.id for c in store.list_chats(owner=alice.id)] == [chat_id]


@needs_db
def test_deleting_twice_raises_the_second_time(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    store.delete_chat(chat_id, owner=alice.id)
    with pytest.raises(ChatNotFound):
        store.delete_chat(chat_id, owner=alice.id)


@needs_db
@pytest.mark.parametrize("bad_id", ["nicht-eine-id", "", None, 42, "00000000-0000-0000-0000-00000000000g"])
def test_a_malformed_id_is_a_missing_chat(store, alice, bad_id):
    expected = str(ChatNotFound())
    for call in (lambda: store.get_chat(bad_id, owner=alice.id),
                 lambda: store.delete_chat(bad_id, owner=alice.id),
                 lambda: store.add_message(bad_id, owner=alice.id, role="user", text="x")):
        with pytest.raises(ChatNotFound) as raised:
            call()
        assert str(raised.value) == expected


@needs_db
def test_an_id_given_as_text_works_like_a_uuid(store, alice):
    chat_id = store.create_chat(owner=alice.id, title="t")
    store.add_message(str(chat_id), owner=str(alice.id), role="user", text="Frage")
    assert store.get_chat(str(chat_id), owner=str(alice.id)).id == chat_id
    assert [c.id for c in store.list_chats(owner=str(alice.id))] == [chat_id]
    store.delete_chat(str(chat_id), owner=str(alice.id))
    assert store.list_chats(owner=alice.id) == []


@needs_db
def test_a_foreign_write_does_not_move_the_chat(store, alice, bob):
    chat_id = store.create_chat(owner=alice.id, title="t")
    before = store.get_chat(chat_id, owner=alice.id).updated_at
    time.sleep(0.01)
    with pytest.raises(ChatNotFound):
        store.add_message(chat_id, owner=bob.id, role="user", text="eingeschleust",
                          references=[REF])
    assert store.get_chat(chat_id, owner=alice.id).updated_at == before


def test_an_owner_that_is_not_an_id_is_a_programming_error():
    store = ChatStore(_NoDatabase())
    with pytest.raises(ValueError):
        store.create_chat(owner="alice", title="t")
    with pytest.raises(ValueError):
        store.list_chats(owner=None)


def test_not_found_names_no_id():
    assert str(ChatNotFound()) == "Chat nicht gefunden."


@needs_db
def test_a_person_without_chats_gets_an_empty_list(store, alice):
    assert store.list_chats(owner=alice.id) == []


# -- logs ----------------------------------------------------------------------------------------------------------


@needs_db
def test_no_text_reaches_a_log_line(store, alice, bob, caplog):
    secrets = ["Geheime Frage zur Kuendigung", "Geheime Antwort", "Geheimer Titel", "Geheime/Akte.pdf",
               "Geheimer Aktentitel", "Geheime Passage"]
    caplog.set_level(logging.DEBUG)
    chat_id = store.create_chat(owner=alice.id, title=secrets[2])
    store.add_message(chat_id, owner=alice.id, role="user", text=secrets[0])
    store.add_message(chat_id, owner=alice.id, role="assistant", text=secrets[1],
                      references=[{"number": 1, "pointer": secrets[3], "page": 1, "title": secrets[4],
                                   "text": secrets[5]}])
    store.list_chats(owner=alice.id)
    store.get_chat(chat_id, owner=alice.id)
    with pytest.raises(ChatNotFound):
        store.add_message(chat_id, owner=bob.id, role="user", text=secrets[0])
    store.delete_chat(chat_id, owner=alice.id)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for secret in secrets:
        assert secret not in logged
    assert str(chat_id) in logged  # the deletion is on record, by id
