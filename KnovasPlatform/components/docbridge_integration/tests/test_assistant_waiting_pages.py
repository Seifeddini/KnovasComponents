"""PMVP-24: the waiting and failure states on the pages (the search page's box and PMVP-23's chat page).

- A generation that runs out of time is busy: nothing stored, for a new question and for a follow-up, and the page
  shows only the plain message.
- A chat whose read fails (the database is down): a plain page with status 503, no traceback text, nothing asked.
- The route starts the answer's clock: the service is built with a 150-second deadline that began with the request.
- The waiting line: static checks of ask.js and the templates, and the real page in headless Chrome over the
  DevTools protocol with a fake service that takes 2 seconds and the line's times shortened to 1 and 3 seconds
  (skipped where no Chrome or no ``websockets`` is installed).
- Logs at DEBUG (FU-46): no customer text.

No Core and no model: the service's search is a fake and its generation a function that times out. This file is
ASCII-only.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

pytest.importorskip("flask")

import conftest  # noqa: E402
from assistant import core  # noqa: E402
from assistant.errors import CoreFailed  # noqa: E402
from assistant.service import BUSY_TEXT, AssistantService, Passage, Reply, Sentence  # noqa: E402
from web_interface import assistant_routes  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"
QUESTION = "Welche Frist gilt f\u00fcr den Mietvertrag Hardturm?"
FOLLOW = "Und wer hat unterschrieben?"
PASSAGE = "Die Frist betr\u00e4gt drei Monate auf Ende Juni."
QUOTE = "Die Frist betr\u00e4gt drei Monate"
STATEMENT = "Gek\u00fcndigt wird mit drei Monaten Frist."
WAIT_TEXTS = ("Der Knovas Assistent sucht in Ihren Quellen und pr\u00fcft die Antwort \u2026",
              "Das dauert l\u00e4nger als sonst. Die Antwort kommt, sobald sie gepr\u00fcft ist.",
              "Keine Antwort erhalten. Bitte versuchen Sie es noch einmal.")
UUID_PATH = re.compile(r"/fragen/([0-9a-fA-F-]{36})")
ROWS = [{"doc_id": "mandate/hardturm/vertrag.pdf", "title": "vertrag.pdf", "snippet": PASSAGE, "page_number": 3}]
ANSWERED = Reply(status="answered", sentences=[Sentence(text=STATEMENT, refs=(1,), quote=QUOTE)],
                 passages=[Passage(number=1, pointer="mandate/hardturm/vertrag.pdf", title="vertrag.pdf", page=3,
                                   text=PASSAGE, sentence=4)])


def _timed_out_generation(**kw):
    raise CoreFailed("timeout")


class Switch:
    """The app's service: answered first, then a generation that times out (the real service, a fake Core)."""

    def __init__(self):
        self.timeout = False
        self.calls = 0

    def answer(self, question, history=(), *, search_text=None):
        self.calls += 1
        if not self.timeout:
            return ANSWERED
        return AssistantService(search=lambda q, limit: list(ROWS),
                                generate=core.in_time(_timed_out_generation, None)).answer(
            question, history, search_text=search_text)


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv(assistant_routes.SWITCH_ENV, "true")


@pytest.fixture
def service(workbench_app, switch_on):
    fake = Switch()
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    return fake


def _ask(client, question=QUESTION, url="/fragen"):
    return client.post(url, data={"question": question, "csrf_token": client._token})


def _main(page):
    return re.search(r"<main\b.*?</main>", page, flags=re.S).group(0)


def _rows(conn):
    return tuple(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                 for t in ("assistant_chats", "assistant_messages", "assistant_references"))


# --- a generation that runs out of time: busy, nothing stored, only the message -----------------------------------


def test_a_new_question_whose_generation_times_out_is_busy_and_stores_nothing(alice_client, platform_db, service):
    service.timeout = True
    response = _ask(alice_client)
    assert response.status_code == 200 and _rows(platform_db) == (0, 0, 0)
    main = _main(response.get_data(as_text=True))
    assert re.search(r'<p class="ask-message" role="status">' + re.escape(html_lib.escape(BUSY_TEXT)) + "</p>", main)
    for absent in ("ask-statement", "ask-quote", "k-ref", "ask-source", "Quellen", STATEMENT, QUOTE, "vertrag.pdf"):
        assert absent not in html_lib.unescape(main), absent
    box = re.search(r'<input class="ask-again-input"[^>]*value="([^"]*)"', main, flags=re.S)
    assert box and html_lib.unescape(box.group(1)) == QUESTION


def test_a_follow_up_whose_generation_times_out_is_busy_and_stores_nothing(alice_client, platform_db, service):
    chat_id = UUID_PATH.search(_ask(alice_client).headers["Location"]).group(1)
    before = _rows(platform_db)
    service.timeout = True
    response = _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    assert response.status_code == 200 and _rows(platform_db) == before
    main = _main(response.get_data(as_text=True))
    # the chat as it was (one answer, one quote), then the message under it, the follow-up back in the box
    assert main.count('class="ask-statement"') == 1 and main.count('class="ask-quote"') == 1
    assert main.index("ask-statement") < main.index(html_lib.escape(BUSY_TEXT))
    box = re.search(r'<input class="ask-again-input"[^>]*value="([^"]*)"', main, flags=re.S)
    assert box and html_lib.unescape(box.group(1)) == FOLLOW


# --- a chat that cannot be read: a plain 503 ------------------------------------------------------------------------


@pytest.mark.parametrize("method, path", [("GET", ""), ("POST", ""), ("POST", "/loeschen"), ("GET", "/quelle/1/1")],
                         ids=["open", "follow-up", "delete", "source"])
def test_a_chat_that_cannot_be_read_is_a_plain_503(alice_client, platform_db, service, monkeypatch, caplog,
                                                    method, path):
    from identity.chats import ChatStore

    chat_id = UUID_PATH.search(_ask(alice_client).headers["Location"]).group(1)
    calls = service.calls

    def database_down(self, *args, **kwargs):
        raise RuntimeError(f"connection to the database lost while reading {QUESTION}")

    monkeypatch.setattr(ChatStore, "get_chat", database_down)
    monkeypatch.setattr(ChatStore, "delete_chat", database_down)
    url = f"/fragen/{chat_id}{path}"
    with caplog.at_level(logging.DEBUG):
        response = (alice_client.get(url) if method == "GET"
                    else alice_client.post(url, data={"question": FOLLOW, "csrf_token": alice_client._token}))
    assert response.status_code == 503
    body = response.get_data(as_text=True)
    assert html_lib.escape(assistant_routes.CHAT_UNAVAILABLE_TEXT) in body
    assert assistant_routes.CHAT_UNAVAILABLE_TEXT == ("Das Gespr\u00e4ch kann gerade nicht geladen werden. "
                                                      "Bitte versuchen Sie es in einem Moment noch einmal.")
    for absent in ("Traceback", "RuntimeError", "database lost"):
        assert absent not in body, absent
    for absent in (html_lib.escape(QUESTION), "ask-statement"):                # her chat's title stays in her list
        assert absent not in _main(body), absent
    assert service.calls == calls and _rows(platform_db)[0] == 1
    lines = "\n".join(r.getMessage() for r in caplog.records)
    assert "the chat is not readable (RuntimeError)" in lines and "database lost" not in lines


def test_the_expired_form_stays_a_400(alice_client, service):
    chat_id = UUID_PATH.search(_ask(alice_client).headers["Location"]).group(1)
    for url in ("/fragen", f"/fragen/{chat_id}"):
        assert alice_client.post(url, data={"question": FOLLOW, "csrf_token": "falsch"}).status_code == 400


# --- the route starts the clock ---------------------------------------------------------------------------------------


def test_the_service_is_built_with_a_deadline_that_starts_with_the_request(alice_client, switch_on, monkeypatch):
    built = []

    def make_service(settings, client, **kw):
        built.append((kw.get("deadline"), time.monotonic()))
        return Switch()

    monkeypatch.setattr(core, "make_service", make_service)
    # the dummy client cannot assert for the assistant (RB-6 has its own tests); here only the clock matters
    monkeypatch.setattr(assistant_routes, "AsTheAssistant", lambda service, **kw: service)
    chat_id = UUID_PATH.search(_ask(alice_client).headers["Location"]).group(1)
    _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    assert len(built) == 2
    for deadline, at in built:
        assert isinstance(deadline, core.Deadline) and deadline.seconds == core.ANSWER_DEADLINE_SECONDS
        assert 0 <= at - deadline.started < 10                              # started by the request, before it
    assert built[0][0] is not built[1][0]                                   # one clock per question


# --- logs (FU-46) ----------------------------------------------------------------------------------------------------


def test_no_customer_text_in_the_logs_of_a_busy_answer(alice_client, platform_db, service, caplog):
    for name in ("assistant", "web_interface", "knovas_client"):
        caplog.set_level(logging.DEBUG, logger=name)
    caplog.set_level(logging.DEBUG)
    chat_id = UUID_PATH.search(_ask(alice_client).headers["Location"]).group(1)
    service.timeout = True
    _ask(alice_client)
    _ask(alice_client, FOLLOW, url=f"/fragen/{chat_id}")
    lines = "\n".join(r.getMessage() for r in caplog.records)
    assert "step=generation ran out of time" in lines and "status=busy" in lines
    for secret in (QUESTION, FOLLOW, PASSAGE, STATEMENT, QUOTE, "Hardturm", "hardturm"):
        assert secret not in lines, secret


# --- the waiting line: static checks ------------------------------------------------------------------------------


def _read(*parts):
    return (WEB.joinpath(*parts)).read_text(encoding="utf-8")


def _js_texts():
    """The string literals of ask.js with their \\u escapes read as JavaScript reads them."""
    texts = []
    for literal in re.findall(r"'((?:[^'\\\n]|\\.)*)'", _read("static", "js", "ask.js")):
        try:
            texts.append(json.loads('"' + literal.replace('"', '\\"') + '"'))
        except ValueError:
            continue                                     # a quote inside a comment, not a string
    return texts


def test_the_script_holds_the_three_texts():
    texts = _js_texts()
    for text in WAIT_TEXTS:
        assert text in texts, text


LINE = ('<p class="ask-wait" role="status" aria-live="polite" data-wait-slow-ms="20000" '
        'data-wait-giveup-ms="170000"></p>')


def test_both_boxes_have_the_status_line_with_its_two_times():
    ask, index = _read("templates", "ask.html"), _read("templates", "index.html")
    assert ask.count(LINE) == 1 and index.count(LINE) == 1
    # on the ask page right under the box; on the search page inside the box's form, and only with the assistant on
    assert re.search(r"<button type=\"submit\" class=\"btn btn-primary\">Fragen</button>\s*</form>\s*"
                     + re.escape(LINE), ask)
    form = index[index.index('<form class="ask-box" id="askForm"'):]
    assert form.index(LINE) < form.index("</form>")
    assert index.rindex("{% if ask_on %}", 0, index.index(LINE)) > index.rindex("{% endif %}", 0, index.index(LINE))
    for text in WAIT_TEXTS:
        assert text not in ask and text not in index                 # the page holds no waiting text until it waits


def test_the_script_disables_the_button_resets_on_pageshow_and_reads_the_two_times():
    js = _read("static", "js", "ask.js")
    assert "button.disabled = true" in js and "button.disabled = false" in js
    assert "window.addEventListener('pageshow', reset)" in js
    assert "after('data-wait-slow-ms', SLOW_MS)" in js and "after('data-wait-giveup-ms', GIVE_UP_MS)" in js
    assert "var SLOW_MS = 20000;" in js and "var GIVE_UP_MS = 170000;" in js
    assert "line.textContent = WAIT_TEXTS[step]" in js
    # a second Enter or click while waiting sends nothing; the box keeps the question (nothing clears it)
    assert js.count(".sending()") >= 2 and "event.preventDefault()" in js
    assert ".value = ''" not in js and '.value = ""' not in js


def test_the_script_still_writes_no_markup_and_parses():
    import shutil

    js = _read("static", "js", "ask.js")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in js, sink
    if shutil.which("node"):
        assert subprocess.run(["node", "--check", str(WEB / "static" / "js" / "ask.js")]).returncode == 0


def test_a_quiet_pulse_stops_with_reduced_motion():
    css = re.sub(r"/\*.*?\*/", "", _read("static", "css", "ask.css"), flags=re.S)
    assert re.search(r"animation: ask-wait-pulse", css)
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert re.search(r"\{\s*animation: none;\s*\}", reduced)


# --- the waiting line in a real browser ------------------------------------------------------------------------------


class SlowService:
    """Takes 2 seconds, then answers. Counts the questions that reach it."""

    def __init__(self):
        self.calls = 0

    def answer(self, question, history=(), *, search_text=None):
        self.calls += 1
        time.sleep(2)
        return ANSWERED


class Tab:
    """One page over the DevTools protocol."""

    def __init__(self, ws):
        self.ws, self.n, self.events = ws, 0, []

    async def call(self, method, **params):
        self.n += 1
        mine = self.n
        await self.ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == mine:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self.events.append(message)

    async def js(self, expression):
        result = await self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise RuntimeError("the page's script failed: " + str(details.get("text")) + " "
                               + str(details.get("exception", {}).get("description", ""))[:300])
        return result.get("result", {}).get("value")

    async def until(self, expression, timeout=15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if await self.js(expression):
                    return True
            except RuntimeError:
                pass
            await asyncio.sleep(0.1)
        return False


def _devtools_port(profile, process, timeout=30.0):
    end = time.monotonic() + timeout
    marker = profile / "DevToolsActivePort"
    while time.monotonic() < end:
        if marker.is_file():
            first = marker.read_text().splitlines()
            if first and first[0].isdigit():
                return int(first[0])
        if process.poll() is not None:
            break
        time.sleep(0.2)
    pytest.fail("Chrome gave no DevTools port")


def _signed_in_cookies(base, email):
    import requests

    session = requests.Session()
    page = session.get(f"{base}/login").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    session.post(f"{base}/login", data={"login_name": email, "password": conftest.PASSWORD, "csrf_token": token},
                 allow_redirects=False)
    return [{"name": c.name, "value": c.value} for c in session.cookies]


STATE = """(() => { const line = document.querySelector('.ask-wait'), form = line.closest('form') || line.previousElementSibling;
  const button = form.querySelector('button[type="submit"]');
  return JSON.stringify({step: line.getAttribute('data-wait-step'), text: line.textContent, disabled: button.disabled,
                         value: form.querySelector('input[name="question"]').value, path: location.pathname}); })()"""

SEND = """(() => { const line = document.querySelector('.ask-wait'), form = line.closest('form') || line.previousElementSibling;
  const button = form.querySelector('button[type="submit"]'), input = form.querySelector('input[name="question"]');
  const started = performance.now(), record = [];
  const sample = () => { record.push({t: Math.round(performance.now() - started), step: line.getAttribute('data-wait-step'),
                                      text: line.textContent, disabled: button.disabled, value: input.value});
                         sessionStorage.setItem('wait-log', JSON.stringify(record)); };
  setInterval(sample, 100);
  setTimeout(() => { button.click(); form.requestSubmit(); sessionStorage.setItem('second-click', 'sent'); }, 1200);
  button.click();
  sample();
  return JSON.stringify(record[0]); })()"""


async def _walk(port, base, cookies):
    import websockets

    connect = websockets.connect

    request = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
    target = json.loads(urllib.request.urlopen(request).read())
    seen = {}
    async with connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
        tab = Tab(ws)
        await tab.call("Page.enable")
        await tab.call("Network.enable")
        for cookie in cookies:
            await tab.call("Network.setCookie", name=cookie["name"], value=cookie["value"], url=base)

        for box, url in (("search", base + "/"), ("follow-up", None)):
            if url:
                await tab.call("Page.navigate", url=url)
            assert await tab.until("document.readyState === 'complete' && !!document.querySelector('.ask-wait')")
            question = QUESTION if box == "search" else FOLLOW
            await tab.js("(() => { const line = document.querySelector('.ask-wait');"
                         " line.setAttribute('data-wait-slow-ms', '1000'); line.setAttribute('data-wait-giveup-ms', '3000');"
                         " const input = document.querySelector('input[name=\"question\"]'); input.focus();"
                         f" input.value = {json.dumps(question)}; input.dispatchEvent(new Event('input')); }})()")
            before = await tab.js("document.querySelectorAll('.ask-statement').length")
            # The page keeps its own record (DevTools holds a call while a form's navigation is pending): its state
            # every 100 ms from the click on, and a second click and a second submit 1.2 s after the first.
            first = json.loads(await tab.js(SEND))
            arrived = await tab.until(f"document.readyState === 'complete' && !!document.querySelector('.ask-wait') "
                                      f"&& document.querySelectorAll('.ask-statement').length > {before}")
            record = json.loads(await tab.js("sessionStorage.getItem('wait-log') || '[]'"))
            seen[box] = {"first": first, "record": record, "arrived": arrived,
                         "second_click": await tab.js("sessionStorage.getItem('second-click')"),
                         "statements": await tab.js("document.querySelectorAll('.ask-statement').length"),
                         "path": await tab.js("location.pathname"),
                         "after": json.loads(await tab.js(STATE))}
            await tab.js("sessionStorage.clear()")
    urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close/{target['id']}").read()
    return seen


def test_in_the_browser_the_line_follows_the_question_and_a_second_click_sends_nothing(workbench_app, alice,
                                                                                       switch_on, tmp_path):
    from test_assistant_page_layout import _chrome

    chrome = _chrome()
    if not chrome:
        pytest.skip("no Chrome or Chromium installed")
    pytest.importorskip("websockets")
    from werkzeug.serving import make_server

    slow = SlowService()
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: slow
    server = make_server("127.0.0.1", 0, workbench_app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    profile = tmp_path / f"chrome-{uuid.uuid4().hex[:6]}"
    process = subprocess.Popen([chrome, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
                                "--no-first-run", "--no-default-browser-check", "--disable-extensions", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port = _devtools_port(profile, process)
        seen = asyncio.run(_walk(port, base, _signed_in_cookies(base, alice.email)))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        server.shutdown()
    for box, wanted_statements in (("search", 1), ("follow-up", 2)):
        walk = seen[box]
        assert walk["first"]["step"] == "1" and walk["first"]["text"] == WAIT_TEXTS[0], box
        assert walk["first"]["disabled"] is True and walk["first"]["value"], box        # the box keeps the question
        later = [s for s in walk["record"] if s["t"] >= 1100]
        assert later, f"{box}: the page recorded nothing after 1.1 s ({walk['record'][-1:]})"
        assert all(s["step"] == "2" and s["text"] == WAIT_TEXTS[1] and s["disabled"] for s in later), box
        assert all(s["value"] for s in walk["record"]), box
        assert walk["second_click"] == "sent", box                                       # tried while waiting
        assert walk["arrived"] and walk["path"].startswith("/fragen/"), box             # the answer replaced the page
        assert walk["statements"] == wanted_statements, box
        assert walk["after"]["step"] in (None, "0") and walk["after"]["disabled"] is False, box
    assert slow.calls == 2                                       # one per box: the second click sent nothing
