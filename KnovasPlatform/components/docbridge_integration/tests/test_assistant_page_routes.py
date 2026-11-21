"""PMVP-22a: the ask page (POST /fragen) and the box on the search page, with a fake assistant service.

Contract L2-PLATFORM-ASSISTANT: N-1 (signed in, the form's token), N-2 (the switch), POST-01 (every reference
shown points to a passage of this reply), POST-05 (no answer: the sentence and the closest passages), POST-06 (busy,
failed: the plain message and nothing else), POST-04 for the route's own log line. Work order
docs/platform-mvp/work-orders/PMVP-22a.md.

No model and no Knovas is called: the app's service factory is replaced by a fake that returns a fixed reply.
This file is ASCII-only: umlauts are \\u escapes.
"""
from __future__ import annotations

import html as html_lib
import logging
import re

import pytest
from flask import url_for

import conftest
from assistant.service import (BUSY_TEXT, FAILED_TEXT, MAX_QUESTION_BYTES, NO_ANSWER_TEXT, QUESTION_TOO_LONG_TEXT,
                               Passage, Reply, Sentence)
from web_interface import assistant_routes

QUESTION = "Bis wann kann Limmat Immobilien den Mietvertrag Seefeldstrasse k\u00fcndigen?"
PDF = "mandate/2026-0142/Nachtrag_2_2023-03-14.pdf"
DOCX = "mandate/2026-0142/Mietvertrag_Seefeldstrasse_48.docx"

PASSAGES = [
    Passage(number=1, pointer=DOCX, title="Mietvertrag_Seefeldstrasse_48.docx", page=4,
            text="Die K\u00fcndigung muss eingeschrieben erfolgen, jeweils auf Ende M\u00e4rz oder Ende September.",
            sentence=12),
    Passage(number=2, pointer=PDF, title="Nachtrag_2_2023-03-14.pdf", page=1,
            text="Die feste Mindestdauer des Mietverh\u00e4ltnisses wird bis zum 30. September 2027 verl\u00e4ngert.",
            sentence=3),
]
SENTENCES = [
    Sentence(text="Limmat Immobilien AG kann fr\u00fchestens auf den 30. September 2027 k\u00fcndigen.", refs=(2,),
             quote="Die feste Mindestdauer des Mietverh\u00e4ltnisses wird bis zum 30. September 2027 verl\u00e4ngert"),
    Sentence(text="Die K\u00fcndigung muss eingeschrieben erfolgen.", refs=(1,),
             quote="Die K\u00fcndigung muss eingeschrieben erfolgen"),
]


def answered(**overrides):
    values = dict(status="answered", sentences=list(SENTENCES), passages=list(PASSAGES), removed=0)
    values.update(overrides)
    return Reply(**values)


class FakeService:
    """The assistant service as the page calls it: answer(question) -> Reply. Counts its calls."""

    def __init__(self, reply):
        self.reply = reply
        self.questions = []

    def answer(self, question, history=()):
        self.questions.append(question)
        return self.reply


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")


@pytest.fixture
def switch_off(monkeypatch):
    monkeypatch.delenv(assistant_routes.SWITCH_ENV, raising=False)


@pytest.fixture
def service(workbench_app):
    fake = FakeService(answered())
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    return fake


@pytest.fixture
def ask_url(workbench_app):
    with workbench_app.test_request_context():
        return url_for("assistant.ask")


def _token(client):
    return client._token


def _ask(client, url, question=QUESTION, **extra):
    data = {"csrf_token": _token(client), "question": question}
    data.update(extra)
    return client.post(url, data=data)


def _main(page):
    match = re.search(r"<main\b.*?</main>", page, flags=re.S)
    assert match, "the page has no <main>"
    return match.group(0)


def _text(fragment):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _flat(fragment):
    """The text of a fragment as the browser joins it: tags taken out, nothing put in their place."""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", fragment))


def _hrefs(fragment):
    return [html_lib.unescape(h) for h in re.findall(r'href="([^"]*)"', fragment)]


# --- N-1: signed in, and the form's token -------------------------------------------------------------------


def test_signed_out_is_sent_to_sign_in(workbench_app, switch_on, service, ask_url):
    response = workbench_app.test_client().post(ask_url, data={"question": QUESTION})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert service.questions == []


def test_signed_out_is_sent_to_sign_in_with_the_switch_off_too(workbench_app, switch_off, service, ask_url):
    response = workbench_app.test_client().post(ask_url, data={"question": QUESTION})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_without_the_token_the_question_is_refused(workbench_app, member, switch_on, service, ask_url):
    client = conftest._signed_in(workbench_app, member.email, with_csrf=False)
    response = client.post(ask_url, data={"question": QUESTION})
    assert response.status_code == 400
    assert assistant_routes.EXPIRED_TEXT in response.data.decode("utf-8")
    assert service.questions == []


def test_a_wrong_token_is_refused(member_client, switch_on, service, ask_url):
    response = member_client.post(ask_url, data={"question": QUESTION, "csrf_token": "falsch"})
    assert response.status_code == 400
    assert service.questions == []
    assert QUESTION not in response.data.decode("utf-8")


def test_the_header_alone_does_not_stand_in_for_the_form_token(member_client, switch_on, service, ask_url):
    # member_client sends the X-CSRF-Token header on every request; the form must carry its own field.
    response = member_client.post(ask_url, data={"question": QUESTION})
    assert response.status_code == 400
    assert service.questions == []


def test_get_is_not_a_question(member_client, switch_on, service, ask_url):
    assert member_client.get(ask_url).status_code == 405
    assert service.questions == []


# --- N-2: the switch ----------------------------------------------------------------------------------------


def test_switched_off_the_page_answers_404(member_client, switch_off, service, ask_url):
    response = _ask(member_client, ask_url)
    assert response.status_code == 404
    assert service.questions == []


class _Setting:
    """The settings' Resolver as the switch reads it: value(key)."""

    def __init__(self, value):
        self._value = value
        self.keys = []

    def value(self, key):
        self.keys.append(key)
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


@pytest.mark.parametrize("value", [None, "", "aus", "false", "0", "no", "off", "tru", 0, False])
def test_the_switch_is_off_unless_the_setting_says_on(value):
    setting = _Setting(value)
    assert assistant_routes.assistant_enabled(setting) is False
    assert setting.keys == ["assistant.enabled"]


@pytest.mark.parametrize("value", ["an", "true", "TRUE", " yes ", "ja", "1", "on", True])
def test_the_switch_reads_on(value):
    assert assistant_routes.assistant_enabled(_Setting(value)) is True


def test_a_setting_that_cannot_be_read_is_off():
    assert assistant_routes.assistant_enabled(_Setting(RuntimeError("database gone"))) is False


def test_an_app_without_settings_is_off():
    from flask import Flask

    with Flask("bare").app_context():
        assert assistant_routes.assistant_enabled() is False


def test_the_deployment_pins_the_switch(workbench_app, monkeypatch):
    with workbench_app.app_context():
        monkeypatch.delenv(assistant_routes.SWITCH_ENV, raising=False)
        assert assistant_routes.assistant_enabled() is False  # the default: off
        monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")
        assert assistant_routes.assistant_enabled() is True


def test_the_settings_page_switches_it_on(workbench_app, platform_admin, member_client, switch_off, service,
                                          ask_url):
    assert _ask(member_client, ask_url).status_code == 404
    workbench_app.extensions["platform_settings"].save(platform_admin, {"assistant.enabled": "an"},
                                                        roles=("admin", "member"))
    assert _ask(member_client, ask_url).status_code == 200
    assert len(service.questions) == 1


def test_a_service_factory_that_says_off_is_a_404(workbench_app, member_client, switch_on, ask_url):
    from assistant.core import OFF

    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: OFF
    assert _ask(member_client, ask_url).status_code == 404


def test_the_real_service_is_wired_and_fails_closed_without_the_secured_api(member_client, switch_on, ask_url,
                                                                            caplog):
    """No fake: PMVP-25's factory builds the service on the app's client. The test client is not in secured mode,
    so the person's search refuses (CoreFailed) and the page shows the plain failure; no model is called."""
    with caplog.at_level(logging.INFO):
        response = _ask(member_client, ask_url)
    assert response.status_code == 200
    assert html_lib.escape(FAILED_TEXT) in _main(response.data.decode("utf-8"))
    assert any("status=failed" in r.getMessage() for r in caplog.records)


def test_the_switch_is_read_on_every_request(member_client, monkeypatch, service, ask_url):
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")
    assert _ask(member_client, ask_url).status_code == 200
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "false")
    assert _ask(member_client, ask_url).status_code == 404
    assert len(service.questions) == 1


# --- the box on the search page -----------------------------------------------------------------------------


def _search_section(page):
    match = re.search(r'<section class="search-section">.*?</section>', page, flags=re.S)
    assert match
    return match.group(0)


def test_switched_off_the_box_only_searches(member_client, switch_off):
    page = member_client.get("/").data.decode("utf-8")
    section = _search_section(page)
    assert "<form" not in section
    assert "askSwitch" not in section and "askButton" not in section
    assert 'name="question"' not in section
    assert '<button id="searchButton" class="btn btn-primary">' in section
    assert 'placeholder="Durchsucht den Inhalt aller Dokumente"' in section
    assert "js/ask.js" not in page and "css/ask.css" not in page


def test_switched_on_the_box_asks_and_offers_the_switch(member_client, switch_on, ask_url):
    page = member_client.get("/").data.decode("utf-8")
    section = _search_section(page)
    form = re.search(r"<form\b[^>]*>", section).group(0)
    assert f'action="{ask_url}"' in form and 'method="post"' in form
    assert f'name="csrf_token" value="{_token(member_client)}"' in section
    assert 'id="searchInput"' in section and 'name="question"' in section
    # The switch: two choices, "Fragen" first and chosen; shown by ask.js.
    switch = re.search(r'<fieldset class="ask-switch" id="askSwitch" hidden>.*?</fieldset>', section, flags=re.S)
    assert switch
    assert re.findall(r'value="(\w+)"', switch.group(0)) == ["fragen", "suchen"]
    assert re.search(r'value="fragen"[^>]*checked', switch.group(0))
    # The search button stays (app.js binds it); it no longer sends the form.
    assert re.search(r'<button id="searchButton" class="btn btn-primary" type="button" hidden>', section)
    assert re.search(r'<button id="askButton" type="submit"[^>]*>Fragen</button>', section)
    assert "js/ask.js" in page and "css/ask.css" in page
    assert page.index("js/app.js") < page.index("js/ask.js")


def test_the_results_and_the_preview_of_the_search_stay(member_client, switch_on):
    page = member_client.get("/").data.decode("utf-8")
    for element_id in ("resultsSection", "resultsContainer", "previewDialog", "docFieldsRail", "loadMoreButton"):
        assert f'id="{element_id}"' in page, element_id


# --- answered -----------------------------------------------------------------------------------------------


def test_the_question_is_asked_once_as_typed(member_client, switch_on, service, ask_url):
    _ask(member_client, ask_url, question="  " + QUESTION + "  ")
    assert service.questions == [QUESTION]


def test_the_question_is_the_page_head(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    head = re.search(r'<h1 class="k-title">(.*?)</h1>', main, flags=re.S)
    assert head and html_lib.unescape(head.group(1)) == QUESTION


def test_each_statement_is_a_paragraph_with_its_reference(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    statements = re.findall(r'<p class="ask-statement">(.*?)</p>', main, flags=re.S)
    assert len(statements) == 2
    for found, sentence in zip(statements, SENTENCES):
        assert _flat(found) == sentence.text[:-1] + str(sentence.refs[0]) + "."
        chips = re.findall(r"<button\b[^>]*\bk-ref\b[^>]*>(\d+)</button>", found)
        assert chips == [str(sentence.refs[0])]


def test_the_last_word_the_reference_and_the_stop_stay_together(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    tails = re.findall(r'<span class="ask-tail">(.*?)</span>', main, flags=re.S)
    assert [_flat(t) for t in tails] == ["k\u00fcndigen2.", "erfolgen1."]


def test_each_reference_controls_its_own_quote(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    chips = re.findall(r'<button type="button" class="k-ref[^"]*"[^>]*aria-controls="([^"]+)"', main)
    assert len(chips) == 2 and len(set(chips)) == 2
    for quote_id, sentence in zip(chips, SENTENCES):
        block = re.search(rf'<div class="ask-quote" id="{re.escape(quote_id)}">(.*?)</div>\s*</div>', main,
                          flags=re.S)
        assert block, quote_id
        assert "\u00ab" + sentence.quote + "\u00bb" in html_lib.unescape(block.group(1))


def test_the_quote_stands_right_under_its_statement(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    order = [m.group(0) for m in re.finditer(r'class="ask-statement"|class="ask-quote"', main)]
    assert order == ['class="ask-statement"', 'class="ask-quote"'] * 2


def test_without_javascript_every_quote_is_open(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    assert not re.search(r'class="ask-quote"[^>]*hidden', main)
    assert re.findall(r'aria-expanded="(\w+)"', main) == ["true", "true"]


def test_the_quote_card_names_the_document_and_the_page(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    first = re.search(r'<div class="ask-quote" id="zitat-1">(.*?)Im Dokument zeigen', main, flags=re.S).group(1)
    assert "k-card" in first
    assert "Nachtrag_2_2023-03-14.pdf" in first
    assert "Seite 1" in _text(first)


def test_the_link_opens_the_pdf_as_a_search_hit_opens_it(workbench_app, member_client, switch_on, service,
                                                          ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    with workbench_app.test_request_context():
        pdf = url_for("preview_document", doc_id=PDF, path=PDF, s="3", _anchor="page=1")
        docx = url_for("download_document", doc_id=DOCX, path=DOCX)
    links = re.findall(r'<a class="k-link k-small" href="([^"]+)"[^>]*>Im Dokument zeigen', main)
    assert [html_lib.unescape(h) for h in links] == [pdf, docx]
    assert pdf.endswith("#page=1") and "s=3" in pdf


def test_a_pdf_opens_as_the_file_while_the_inline_preview_is_off(workbench_app, switch_on, service):
    with workbench_app.test_request_context():
        reply = assistant_routes.shown_reply(answered(), pdf_preview=False)
        expected = url_for("download_document", doc_id=PDF, path=PDF)
    assert reply["passages"][1].href == expected


def test_the_passages_follow_with_number_title_and_page(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    section = re.search(r'<h2 class="k-section-title">Quellen</h2>(.*?)</section>', main, flags=re.S)
    assert section
    items = re.findall(r'<li class="ask-source">(.*?)</li>', section.group(1), flags=re.S)
    assert [_text(i) for i in items] == ["1 Mietvertrag_Seefeldstrasse_48.docx , S. 4",
                                         "2 Nachtrag_2_2023-03-14.pdf , S. 1"]


def test_every_address_on_the_page_is_built_by_the_app(workbench_app, member_client, switch_on, ask_url):
    """FU-34: no href is taken from the reply. A passage whose pointer and title look like addresses still
    gets only the app's own document address."""
    evil = Passage(number=1, pointer="javascript:alert(1)//x.pdf", title="https://evil.test/", page=None,
                   text="t", sentence=None)
    fake = FakeService(answered(passages=[evil], sentences=[Sentence(text="Ein Satz.", refs=(1,), quote="t")]))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    for href in _hrefs(main):
        assert href.startswith("/") and not href.startswith("//"), href
        assert href.startswith("/api/document/"), href
    assert 'href="https://evil.test/"' not in main and 'href="javascript:' not in main


def test_no_removed_line_when_nothing_was_removed(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    assert "ask-removed" not in main and "ohne Beleg" not in main


@pytest.mark.parametrize("count, line", [(1, "1 Aussage ohne Beleg ausgelassen"),
                                         (2, "2 Aussagen ohne Beleg ausgelassen")])
def test_the_removed_line_counts_and_shows_no_text(workbench_app, member_client, switch_on, ask_url, count, line):
    fake = FakeService(answered(removed=count))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    removed = re.search(r'<p class="ask-removed[^"]*">(.*?)</p>', main, flags=re.S)
    assert removed and removed.group(1) == line


# --- no answer, busy, failed --------------------------------------------------------------------------------


def test_no_answer_says_so_and_lists_the_closest_passages(workbench_app, member_client, switch_on, ask_url):
    fake = FakeService(Reply(status="no_answer", passages=list(PASSAGES), message=NO_ANSWER_TEXT, removed=3))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    assert NO_ANSWER_TEXT == "Dazu finde ich nichts in Ihren Quellen"
    assert re.search(r'<p class="ask-message">' + re.escape(NO_ANSWER_TEXT) + "</p>", main)
    assert "ask-statement" not in main and "ask-quote" not in main and "k-ref" not in main
    items = re.findall(r'<li class="ask-source">(.*?)</li>', main, flags=re.S)
    assert len(items) == 2
    assert "3 Aussagen ohne Beleg ausgelassen" in main


def test_no_answer_without_passages_shows_the_sentence_alone(workbench_app, member_client, switch_on, ask_url):
    fake = FakeService(Reply(status="no_answer", message=NO_ANSWER_TEXT))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    assert NO_ANSWER_TEXT in main
    assert "ask-source" not in main and "k-section" not in main


@pytest.mark.parametrize("status, message", [("busy", BUSY_TEXT), ("failed", FAILED_TEXT)])
def test_busy_and_failed_show_the_plain_message_and_nothing_else(workbench_app, member_client, switch_on, ask_url,
                                                                  status, message):
    # Even a reply that wrongly carried passages, statements and a count shows only its message (POST-06).
    fake = FakeService(Reply(status=status, message=message, passages=list(PASSAGES), sentences=list(SENTENCES),
                             removed=2))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    response = _ask(member_client, ask_url)
    assert response.status_code == 200
    main = _main(response.data.decode("utf-8"))
    assert re.search(r'<p class="ask-message" role="status">' + re.escape(html_lib.escape(message)) + "</p>",
                     main)
    for absent in ("ask-statement", "ask-quote", "k-ref", "ask-source", "ask-removed", "Quellen",
                   SENTENCES[0].text, SENTENCES[0].quote, "Nachtrag_2"):
        assert absent not in html_lib.unescape(main), absent


def test_a_question_over_the_limit_is_refused_before_the_service(member_client, switch_on, service, ask_url):
    response = _ask(member_client, ask_url, question="x" * (MAX_QUESTION_BYTES + 1))
    assert response.status_code == 200
    main = _main(response.data.decode("utf-8"))
    assert html_lib.escape(QUESTION_TOO_LONG_TEXT) in main
    assert service.questions == []
    assert "x" * 100 not in main


def test_an_empty_question_goes_back_to_the_search(member_client, switch_on, service, ask_url):
    response = _ask(member_client, ask_url, question="   ")
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/")
    assert service.questions == []


def test_the_page_offers_the_box_for_a_new_question(member_client, switch_on, service, ask_url):
    main = _main(_ask(member_client, ask_url).data.decode("utf-8"))
    form = re.search(r'<form class="ask-again" method="post" action="([^"]+)">(.*?)</form>', main, flags=re.S)
    assert form and form.group(1) == ask_url
    assert f'name="csrf_token" value="{_token(member_client)}"' in form.group(2)
    assert 'name="question"' in form.group(2)


def test_the_page_is_in_the_workspace(member_client, switch_on, service, ask_url):
    page = _ask(member_client, ask_url).data.decode("utf-8")
    aside = re.search(r"<aside\b[^>]*>", page).group(0)
    assert 'data-area="work"' in aside
    assert "js/ask.js" in page and "css/ask.css" in page


# --- plain text (FU-11) -------------------------------------------------------------------------------------

STATEMENT = "<script>alert(1)</script> **fett** [x](http://evil.test) &amp; <b>B</b>."
QUOTE = "<img src=x onerror=alert(2)> __unter__ `code`"
TITLE = "<a href=\"http://evil.test\">T</a> **titel**.pdf"


def test_statement_quote_and_title_come_out_as_their_characters(workbench_app, member_client, switch_on, ask_url):
    passage = Passage(number=1, pointer="mandate/x.pdf", title=TITLE, page=2, text="t", sentence=None)
    fake = FakeService(answered(sentences=[Sentence(text=STATEMENT, refs=(1,), quote=QUOTE)], passages=[passage]))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    page = _ask(member_client, ask_url, question="<i>Frage</i> **x**?").data.decode("utf-8")
    main = _main(page)
    for raw in (STATEMENT, QUOTE, TITLE, "<i>Frage</i>"):
        # Present only escaped: the browser shows the characters.
        assert raw not in main, raw
    for shown in ("**fett**", "[x](http://evil.test)", "__unter__", "`code`", "**titel**.pdf", "**x**?"):
        assert shown in main, shown
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in main
    assert "&amp;amp;" in main
    assert "&lt;img src=x onerror=alert(2)&gt;" in main
    assert "<script>alert" not in page and "<b>B</b>" not in main and "<strong>" not in main and "<em>" not in main
    assert 'href="http://evil.test"' not in main
    assert _flat(re.search(r'<p class="ask-statement">.*?</p>', main, flags=re.S).group(0)) == \
        "<script>alert(1)</script> **fett** [x](http://evil.test) &amp; <b>B</b>1."


# --- grants and logs ----------------------------------------------------------------------------------------


def test_the_passages_shown_may_be_opened(member_client, switch_on, service, ask_url):
    def gate_answer():
        response = member_client.get(f"/api/document/{DOCX}/download", query_string={"path": DOCX})
        return response.get_json() or {}

    assert gate_answer().get("error") == "Not found"  # the wall: no grant before the question
    _ask(member_client, ask_url)
    assert gate_answer().get("error") != "Not found"  # past the wall: the file route itself answers


def test_no_customer_text_reaches_a_log_line(workbench_app, member_client, switch_on, ask_url, caplog):
    passage = Passage(number=1, pointer="mandate/Geheim_Mandant_Huber.pdf", title="Geheim_Mandant_Huber.pdf", page=1,
                      text="Vertrauliche Passage", sentence=None)
    reply = answered(passages=[passage], removed=1,
                     sentences=[Sentence(text="Huber zahlt 1'500 Franken.", refs=(1,), quote="Vertrauliche Passage")])
    fake = FakeService(reply)
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    with caplog.at_level(logging.DEBUG):
        _ask(member_client, ask_url, question="Was zahlt Mandant Huber?")
    lines = [record.getMessage() for record in caplog.records]
    assert any("status=answered" in line and "statements=1" in line and "removed=1" in line for line in lines)
    for secret in ("Huber", "Vertrauliche", "Geheim", "1'500", "Was zahlt"):
        assert not [line for line in lines if secret in line], secret


def test_a_refused_token_logs_no_text(workbench_app, member, switch_on, service, ask_url, caplog):
    client = conftest._signed_in(workbench_app, member.email, with_csrf=False)
    with caplog.at_level(logging.DEBUG):
        client.post(ask_url, data={"question": "Was zahlt Mandant Huber?"})
    assert not [r for r in caplog.records if "Huber" in r.getMessage()]
