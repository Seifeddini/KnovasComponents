"""PMVP-23: the person's chats in the Workspace's sidebar and on /fragen/verlauf (W-07, SPEC-06, D4, FU-03).

The sidebar: "+ Neue Frage" and the newest 20 chats grouped by their last change, the open one marked; "Alle
Gespraeche" only when there are more. The block stands after the menu and its script in a <nav> of its own, so the
first <nav> stays "Suche" alone (PMVP-11). /fragen/verlauf: every chat of the person, 50 a page. A list that cannot
be read draws no block and never breaks the page. At 390 pixels nothing scrolls sideways (headless Chrome; skipped
where none is installed). This file is ASCII-only.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("flask")

from test_assistant_chats_routes import UUID_PATH, FakeService, _main, _page, service, switch_on  # noqa: E402,F401
from web_interface import assistant_routes  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"


def _store(conn, person, count, prefix, *, minutes=1, start=0):
    """``count`` chats, the first the oldest, each ``minutes`` older than the next; their ids, oldest first."""
    from identity.chats import ChatStore

    store, ids = ChatStore(conn), []
    for index in range(count):
        chat_id = store.create_chat(owner=person.id, title=f"{prefix} {index:03d}")
        conn.execute("UPDATE assistant_chats SET updated_at = now() - make_interval(mins => %s) WHERE id = %s",
                     (start + (count - index) * minutes, chat_id))
        ids.append(str(chat_id))
    return ids


def _sidebar(page):
    return re.search(r"<aside\b.*?</aside>", page, flags=re.S).group(0)


def _block(page):
    found = re.search(r'<nav aria-label="Gespr\u00e4che" data-chats>.*?</nav>', _sidebar(page), flags=re.S)
    return found.group(0) if found else ""


def _listed(block):
    return UUID_PATH.findall(block)


def _text(fragment):
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


# --- the sidebar ---------------------------------------------------------------------------------------------------


def test_the_sidebar_lists_her_chats_newest_first_and_marks_only_the_open_one(alice, alice_client, platform_db,
                                                                             service):
    ids = _store(platform_db, alice, 3, "Frage")
    search = _page(alice_client, "/")
    block = _block(search)
    assert _listed(block) == ids[::-1]
    assert "aria-current" not in block                                    # nothing is current on the search page
    assert re.search(r'<a href="/" class="app-nav-item" data-chats-new>\+ Neue Frage</a>', block)
    chat = _page(alice_client, f"/fragen/{ids[1]}")
    marked = re.findall(r'<a href="([^"]+)" class="app-nav-item active"\s*aria-current="page">', _sidebar(chat))
    assert marked == [f"/fragen/{ids[1]}"]


def test_the_sidebar_holds_20_and_offers_all_only_when_there_are_more(alice, alice_client, platform_db, service):
    ids = _store(platform_db, alice, 20, "Frage")
    block = _block(_page(alice_client, "/"))
    assert _listed(block) == ids[::-1] and "Alle Gespr\u00e4che" not in block
    _store(platform_db, alice, 1, "Neueste")
    block = _block(_page(alice_client, "/"))
    assert len(_listed(block)) == 20 and ids[0] not in block
    assert re.search(r'<a href="/fragen/verlauf" class="app-nav-item" data-chats-all>Alle Gespr\u00e4che</a>', block)


def test_the_groups_follow_the_last_change(alice, alice_client, platform_db, service):
    old = _store(platform_db, alice, 1, "Alt", start=60 * 24 * 30)
    new = _store(platform_db, alice, 1, "Neu")
    block = _block(_page(alice_client, "/"))
    groups = re.findall(r'<span class="app-nav-group">([^<]+)</span>', block)
    assert groups == ["Heute", "Fr\u00fcher"]
    assert block.index(new[0]) < block.index("Fr\u00fcher") < block.index(old[0])


@pytest.mark.parametrize("day, label", [(date(2026, 11, 25), "Heute"), (date(2026, 11, 23), "Diese Woche"),
                                        (date(2026, 11, 22), "Fr\u00fcher"), (date(2025, 11, 25), "Fr\u00fcher")])
def test_a_chat_is_grouped_by_its_day_in_the_installations_zone(day, label):
    today = date(2026, 11, 25)                                            # a Wednesday
    moment = datetime(day.year, day.month, day.day, 0, 30, tzinfo=assistant_routes.LOCAL_TZ)
    assert assistant_routes.group_label(moment, today) == label
    assert assistant_routes.group_label(moment.astimezone(timezone.utc), today) == label


def test_the_day_is_the_installations_not_utcs():
    late = datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc)            # 00:30 on the 25th in Zurich
    assert assistant_routes.group_label(late, date(2026, 11, 25)) == "Heute"


def test_a_person_without_chats_is_offered_a_new_question_only(alice_client, service):
    block = _block(_page(alice_client, "/"))
    assert "Neue Frage" in block and "data-chats-list" not in block and "data-chats-fold" not in block


def test_the_search_pages_first_nav_is_still_the_menu_alone(alice, alice_client, platform_db, service):
    _store(platform_db, alice, 2, "Frage")
    sidebar = _sidebar(_page(alice_client, "/"))
    navs = re.findall(r"<nav\b[^>]*>.*?</nav>", sidebar, flags=re.S)
    assert len(navs) == 2
    assert re.findall(r'href="([^"]+)"', navs[0]) == ["/"] and _text(navs[0]) == "Suche"
    # the block stands after the menu's script, which finds the menu as its previous sibling
    assert sidebar.index("</nav>") < sidebar.index("<script>") < sidebar.index("data-chats") < sidebar.index(
        "app-sidebar-foot")


def test_a_title_is_escaped_and_isolated_in_the_sidebar(alice, alice_client, platform_db, service):
    from identity.chats import ChatStore

    ChatStore(platform_db).create_chat(owner=alice.id, title='<img src=x onerror="alert(1)"> \u202eabc')
    block = _block(_page(alice_client, "/"))
    assert "<img" not in block
    assert re.search(r'<span dir="auto">&lt;img src=x onerror=(&#34;|&quot;)alert\(1\)(&#34;|&quot;)&gt; \u202eabc</span>',
                     block)


def test_another_persons_chats_are_never_in_her_sidebar(alice, bob, alice_client, bob_client, platform_db, service):
    alices = _store(platform_db, alice, 2, "Alice")
    bobs = _store(platform_db, bob, 2, "Bob")
    assert _listed(_block(_page(alice_client, "/"))) == alices[::-1]
    assert _listed(_block(_page(bob_client, "/"))) == bobs[::-1]


def test_a_list_that_cannot_be_read_draws_no_block_and_the_page_stands(alice, alice_client, platform_db, service,
                                                                      monkeypatch, caplog):
    from identity.chats import ChatStore

    ids = _store(platform_db, alice, 1, "Frage")

    def broken(self, **kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(ChatStore, "list_chats", broken)
    with caplog.at_level(logging.WARNING, logger="web_interface.assistant_routes"):
        for url in ("/", f"/fragen/{ids[0]}"):
            page = _page(alice_client, url)
            assert "data-chats" not in page
    assert any("chat list is not readable (RuntimeError)" in r.getMessage() for r in caplog.records)
    response = alice_client.get("/fragen/verlauf")
    assert response.status_code == 503
    assert html_lib.escape(assistant_routes.LIST_UNAVAILABLE_TEXT) in response.get_data(as_text=True)


# --- /fragen/verlauf: 50 a page ------------------------------------------------------------------------------------


def _verlauf(client, page=None):
    main = _main(_page(client, "/fragen/verlauf" + (f"?seite={page}" if page is not None else "")))
    links = [html_lib.unescape(h) for h in re.findall(r'<a class="k-link" href="([^"]+)">', main)]
    return UUID_PATH.findall(main), links, main


def test_all_chats_come_in_pages_of_50_newest_first(alice, alice_client, platform_db, service):
    ids = _store(platform_db, alice, 120, "Frage")[::-1]
    first, links, main = _verlauf(alice_client)
    assert first == ids[:50]
    assert [h for h in links if "seite" in h] == ["/fragen/verlauf?seite=2"] and "Neuere" not in main
    second, links, _ = _verlauf(alice_client, 2)
    assert second == ids[50:100]
    assert sorted(h for h in links if "seite" in h) == ["/fragen/verlauf?seite=1", "/fragen/verlauf?seite=3"]
    third, links, main = _verlauf(alice_client, 3)
    assert third == ids[100:] and "\u00c4ltere" not in main
    for wrong in ("0", "-1", "abc", "9" * 40):
        assert _verlauf(alice_client, wrong)[0] == ids[:50], wrong
    assert _verlauf(alice_client, 99)[0] == []


def test_the_page_of_all_chats_shows_titles_as_text_with_their_date(alice, alice_client, platform_db, service):
    from identity.chats import ChatStore

    ChatStore(platform_db).create_chat(owner=alice.id, title="<b>Fett</b>")
    ChatStore(platform_db).create_chat(owner=alice.id, title="")
    _, _, main = _verlauf(alice_client)
    assert "&lt;b&gt;Fett&lt;/b&gt;" in main and "<b>" not in main
    assert "Ohne Titel" in main
    assert re.search(r"\d\d\.\d\d\.\d{4}, \d\d:\d\d", main)


def test_a_person_without_chats_is_told_so(alice_client, service):
    _, _, main = _verlauf(alice_client)
    assert "Noch keine Gespr\u00e4che." in main


# --- the look: one line per title, folded at narrow widths -----------------------------------------------------------


def _css():
    return re.sub(r"/\*.*?\*/", "", (WEB / "static" / "css" / "shell.css").read_text(encoding="utf-8"), flags=re.S)


def test_a_title_is_one_line_cut_with_an_ellipsis():
    rule = re.search(r"\[data-chats-list\] \.app-nav-item > span \{([^}]*)\}", _css()).group(1)
    for declaration in ("overflow: hidden", "text-overflow: ellipsis", "white-space: nowrap", "min-width: 0"):
        assert declaration in rule


def test_in_a_narrow_window_the_list_folds_behind_gespraeche():
    narrow = _css()[_css().index("@media (max-width: 900px) {", _css().index("[data-chats]")):]
    assert re.search(r"\[data-chats\] \[data-chats-list\] \{ display: none;", narrow)
    assert re.search(r"\[data-chats-fold\]:checked ~ \[data-chats-list\] \{ display: flex; \}", narrow)
    sidebar = (WEB / "templates" / "_sidebar.html").read_text(encoding="utf-8")
    assert '<input type="checkbox" id="app-chats-fold" data-chats-fold>' in sidebar
    assert '<label for="app-chats-fold" data-chats-toggle>Gespr\u00e4che</label>' in sidebar


MEASURE = """<script>
(function () {
  var fold = document.getElementById('app-chats-fold');
  var before = document.documentElement.scrollWidth;
  if (fold) { fold.checked = true; }
  var after = document.documentElement.scrollWidth;
  var shown = document.querySelectorAll('[data-chats-list] a:not([data-chats-all])').length;
  var wider = Array.prototype.filter.call(document.querySelectorAll('aside *'), function (e) {
    return e.getBoundingClientRect().right > window.innerWidth + 0.5; }).length;
  parent.postMessage([before, after, window.innerWidth, shown, wider].join(' '), '*');
})();
</script>"""


def test_at_390_pixels_the_sidebar_with_its_chats_does_not_scroll_sideways(alice, alice_client, platform_db, service,
                                                                          tmp_path):
    from test_assistant_page_layout import FRAME, PHONE, _chrome

    chrome = _chrome()
    if not chrome:
        pytest.skip("no Chrome or Chromium installed")
    from identity.chats import ChatStore

    ChatStore(platform_db).create_chat(owner=alice.id, title=("Vollmacht " * 19).strip())
    _store(platform_db, alice, 24, "K\u00fcndigung Mietvertrag Seefeldstrasse Nachtrag")
    page = _page(alice_client, "/")
    html = re.sub(r'(href|src)="/static/([^"?]+)(\?[^"]*)?"', r'\1="\2"', page)
    html = re.sub(r"<head>", f'<head><base href="{(WEB / "static").as_uri()}/">', html, count=1)
    html = html.replace("</body>", MEASURE + "</body>", 1)
    (tmp_path / "ask.html").write_text(html, encoding="utf-8")
    frame = tmp_path / "frame.html"
    frame.write_text(FRAME.format(w=PHONE[0], h=PHONE[1]), encoding="utf-8")
    result = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--allow-file-access-from-files", f"--user-data-dir={tmp_path / 'profile'}", "--window-size=800,1000",
         "--hide-scrollbars", "--virtual-time-budget=10000", "--dump-dom", frame.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    found = re.search(r'data-measured="(\d+) (\d+) (\d+) (\d+) (\d+)"', result.stdout)
    assert found, f"Chrome gave no measurement (exit {result.returncode}): {result.stderr[-500:]}"
    before, after, width, shown, wider = (int(v) for v in found.groups())
    assert width == PHONE[0]
    assert shown == 20
    assert before <= width and after <= width, (before, after, width)
    assert wider == 0
