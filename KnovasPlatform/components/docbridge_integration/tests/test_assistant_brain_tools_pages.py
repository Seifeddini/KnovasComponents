"""PMVP-20 brain-mcp: the ask page and the chats with a reply of the Brain's tools.

The page and the chats stay as they are: a question becomes a chat with its cited passages stored (PMVP-23), and
every document link of the stored chat goes through the click route, which asks the Core as the person before it
grants and opens. What differs: a reply of the Brain's tools is never granted by the page (the company's key read
its documents, not the person's search), and where such a reply is shown unsaved its passages show their title
without a link. The passages mode is unchanged (a control). No Core and no model is reached; the store is the real
one on the test database. This file is ASCII-only.
"""
from __future__ import annotations

import html as html_lib
import re

import pytest

pytest.importorskip("flask")

from assistant.brain_tools import ToolsReply  # noqa: E402
from assistant.service import Reply  # noqa: E402
from test_assistant_chats_routes import (DOCX, PASSAGES, PDF, QUESTION, S1, S2, FakeService, _ask,  # noqa: E402
                                         _chat_id, _gate_open, _main, _page, _rows)
from web_interface import assistant_routes  # noqa: E402


def tools_reply(*sentences):
    return ToolsReply(status="answered", sentences=list(sentences or (S1, S2)), passages=list(PASSAGES), removed=0)


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")


@pytest.fixture
def service(workbench_app, switch_on):
    fake = FakeService(tools_reply())
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    return fake


def test_a_reply_of_the_brains_tools_becomes_a_chat_with_its_cited_passages_and_nothing_is_granted(
        alice_client, platform_db, service):
    chat_id = _chat_id(_ask(alice_client))
    assert _rows(platform_db) == (1, 2, 2)                                      # one chat, two messages, two refs
    stored = platform_db.execute(
        "SELECT pointer, text FROM assistant_references ORDER BY number").fetchall()
    assert [tuple(row) for row in stored] == [(PDF, PASSAGES[0].text), (DOCX, PASSAGES[1].text)]
    assert not _gate_open(alice_client, PDF) and not _gate_open(alice_client, DOCX)
    main = _main(_page(alice_client, f"/fragen/{chat_id}"))
    hrefs = sorted({html_lib.unescape(h) for h in re.findall(r'href="([^"]+)"', main)})
    assert hrefs == [f"/fragen/{chat_id}/quelle/1/1", f"/fragen/{chat_id}/quelle/1/2"]
    assert "Mietvertrag_Hardturm.pdf" in main and "Im Dokument zeigen" in main


def test_its_document_opens_through_the_click_route_after_the_core_said_yes(alice_client, platform_db, service,
                                                                          fake_graph):
    chat_id = _chat_id(_ask(alice_client))
    fake_graph.denied_pointers.add(DOCX)
    assert alice_client.get(f"/fragen/{chat_id}/quelle/1/2").status_code == 404
    assert not _gate_open(alice_client, DOCX)
    opened = alice_client.get(f"/fragen/{chat_id}/quelle/1/1")
    assert opened.status_code == 302 and PDF in html_lib.unescape(opened.headers["Location"])
    assert _gate_open(alice_client, PDF) and not _gate_open(alice_client, DOCX)
    assert fake_graph.readable_calls.count(PDF) == 1 and fake_graph.readable_calls.count(DOCX) == 1


def test_shown_unsaved_its_passages_show_the_title_without_a_link(alice_client, platform_db, service, monkeypatch):
    from identity.chats import ChatStore

    def fails(self, *args, **kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(ChatStore, "add_message", fails)
    response = _ask(alice_client)
    assert response.status_code == 200
    main = _main(response.get_data(as_text=True))
    assert html_lib.escape(assistant_routes.NOT_SAVED_TEXT) in main
    assert "Mietvertrag_Hardturm.pdf" in main and "Nachtrag_Hardturm.docx" in main
    assert "/api/document/" not in main and "Im Dokument zeigen" not in main
    assert not _gate_open(alice_client, PDF) and not _gate_open(alice_client, DOCX)


def test_control_a_passages_reply_is_granted_and_linked_as_before(alice_client, platform_db, service, monkeypatch):
    from identity.chats import ChatStore

    service.reply = Reply(status="answered", sentences=[S1], passages=list(PASSAGES), removed=0)
    _chat_id(_ask(alice_client))
    assert _gate_open(alice_client, PDF) and _gate_open(alice_client, DOCX)

    def fails(self, *args, **kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(ChatStore, "add_message", fails)
    main = _main(_ask(alice_client).get_data(as_text=True))
    assert "/api/document/" in main and "Im Dokument zeigen" in main


def test_shown_reply_gives_no_address_only_to_a_reply_that_may_not_open_directly(workbench_app):
    with workbench_app.test_request_context():
        tools = assistant_routes.shown_reply(tools_reply(), pdf_preview=True)
        plain = assistant_routes.shown_reply(Reply(status="answered", sentences=[S1], passages=list(PASSAGES)),
                                             pdf_preview=True)
        stored = assistant_routes.shown_reply(tools_reply(), pdf_preview=True, href=lambda p: f"/x/{p.number}")
    assert [p.href for p in tools["passages"]] == ["", ""]
    assert all(p.href.startswith("/api/document/") for p in plain["passages"])
    assert [p.href for p in stored["passages"]] == ["/x/1", "/x/2"]
