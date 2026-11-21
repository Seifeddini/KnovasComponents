"""PMVP-22a: the ask page in a real browser at 390 pixels: a long word, a long file name and a long compound do not
make the page scroll sideways (verification note 1, FU-35).

The page is the real one (POST /fragen with a fake service, no model), saved to a temporary file with its own
stylesheets and fonts, and opened in headless Chrome with ``--dump-dom`` inside a frame 390 pixels wide (a headless
window is never narrower than about 500). A script added at the page's end opens every quote, measures the page and
posts the widths to the frame's parent, which writes them into the dumped document. Skipped where no Chrome or
Chromium is installed. This file is ASCII-only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from assistant.service import Passage, Sentence
from test_assistant_page_routes import (LONG_COMPOUND, LONG_FILE, LONG_WORD, FakeService, _ask, answered,  # noqa: F401
                                        ask_url, switch_on)
from web_interface import assistant_routes

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"
PHONE = (390, 844)

MEASURE = """<script>
(function () {
  document.querySelectorAll('.ask-statement [aria-controls]').forEach(function (chip) { chip.click(); });
  var open = document.querySelectorAll('.ask-quote:not([hidden])').length;
  var wider = Array.prototype.filter.call(document.querySelectorAll('main *'), function (e) {
    return e.getBoundingClientRect().right > window.innerWidth + 0.5; }).length;
  parent.postMessage([document.documentElement.scrollWidth, window.innerWidth, open, wider].join(' '), '*');
})();
</script>"""


FRAME = """<!doctype html><html><body style="margin:0">
<iframe src="ask.html" style="width:{w}px;height:{h}px;border:0"></iframe>
<script>addEventListener('message', function (e) {{ document.body.setAttribute('data-measured', e.data); }});</script>
</body></html>"""


def _chrome():
    named = os.environ.get("CHROME", "")
    candidates = [named] if named else []
    candidates += [shutil.which(n) or "" for n in ("chrome", "google-chrome", "chromium", "chromium-browser")]
    candidates += ["C:/Program Files/Google/Chrome/Application/chrome.exe",
                   "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"]
    return next((c for c in candidates if c and Path(c).is_file()), None)


@pytest.fixture
def chrome():
    found = _chrome()
    if not found:
        pytest.skip("no Chrome or Chromium installed")
    return found


def _measure(chrome, page_html, tmp_path):
    """Open the page at 390 x 844 in headless Chrome: (scroll width, window width, open quotes, wider elements)."""
    html = re.sub(r'(href|src)="/static/([^"?]+)(\?[^"]*)?"', r'\1="\2"', page_html)
    html = re.sub(r"<head>", f'<head><base href="{(WEB / "static").as_uri()}/">', html, count=1)
    html = html.replace("</body>", MEASURE + "</body>", 1)
    (tmp_path / "ask.html").write_text(html, encoding="utf-8")
    page = tmp_path / "frame.html"
    page.write_text(FRAME.format(w=PHONE[0], h=PHONE[1]), encoding="utf-8")
    result = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--allow-file-access-from-files", f"--user-data-dir={tmp_path / 'profile'}", "--window-size=800,1000",
         "--hide-scrollbars", "--virtual-time-budget=10000", "--dump-dom", page.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    found = re.search(r'data-measured="(\d+) (\d+) (\d+) (\d+)"', result.stdout)
    assert found, f"Chrome gave no measurement (exit {result.returncode}): {result.stderr[-500:]}"
    return tuple(int(v) for v in found.groups())


def test_long_words_do_not_make_the_page_scroll_sideways_at_390(chrome, workbench_app, member_client, switch_on,
                                                                ask_url, tmp_path):
    passage = Passage(number=1, pointer="mandate/" + LONG_FILE, title=LONG_FILE, page=3,
                      text="t " + LONG_COMPOUND + " " + LONG_WORD, sentence=None)
    sentences = [
        Sentence(text="Es gilt die " + LONG_COMPOUND + ".", refs=(1,), quote=LONG_COMPOUND + " gilt"),
        Sentence(text="Siehe " + LONG_WORD + ".", refs=(1,), quote=LONG_WORD),
        Sentence(text="Die Datei " + LONG_FILE + " regelt es.", refs=(1,), quote=LONG_FILE),
    ]
    fake = FakeService(answered(passages=[passage], sentences=sentences))
    workbench_app.extensions[assistant_routes.EXTENSION]["service"] = lambda: fake
    page = _ask(member_client, ask_url, question="Was gilt f\u00fcr " + LONG_WORD + "?").data.decode("utf-8")

    scroll_width, window_width, open_quotes, wider = _measure(chrome, page, tmp_path)
    assert window_width == PHONE[0]
    assert open_quotes == 3
    assert scroll_width <= window_width, f"the page is {scroll_width} px wide at {window_width}"
    assert wider == 0
