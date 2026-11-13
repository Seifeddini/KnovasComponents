"""Custom views: the page and its bridge host (Task 10; SS-626).

The server side: the picker in the list's order with the current view marked, the
frame's sandbox in the page's markup (scripts only, never the page's origin), the
admin block for administrators only, and a page without inline script or event
handlers. The host's logic runs in node against fakes: the shape of a message,
the links a view may open, one read at a time, the height and the relay of a
read's answer. The browser itself is exercised in Task 12.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("flask")

from conftest import _person, _signed_in, platform_db_reachable  # noqa: E402
from custom_views_app import identity_app, views_folder, write_view  # noqa: E402

needs_db = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

WEB = Path(__file__).resolve().parents[1] / "src" / "web_interface"
TEMPLATE = WEB / "templates" / "custom_views.html"
SCRIPT = WEB / "static" / "js" / "custom_views.js"
STYLES = WEB / "static" / "css" / "custom_views.css"
XSS = "<img src=x onerror=alert(1)>"
FRAME = '<iframe class="cv-frame" sandbox="allow-scripts" referrerpolicy="no-referrer"'


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch):
    monkeypatch.setattr("custom_views.budget._shared", {})


@pytest.fixture
def folder(tmp_path, monkeypatch):
    return views_folder(tmp_path, monkeypatch)


@pytest.fixture
def make_app(platform_db, tmp_path, monkeypatch, folder):
    def make(**kwargs):
        return identity_app(platform_db, tmp_path, monkeypatch, folder, **kwargs)
    return make


@pytest.fixture
def member(identity_repo):
    return _person(identity_repo, "mia@kanzlei.ch", "Mia", "member")


@pytest.fixture
def admin(identity_repo):
    return _person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")


def _picker(html: str) -> list:
    """The picker's entries as (id, marked as current)."""
    return [(found.group(1), "aria-current" in found.group(0))
            for found in re.finditer(r'<a class="cv-pick" href="/custom-views/[^"]*"\s+data-view-id="([^"]*)"[^>]*>',
                                     html)]


# -- the page ------------------------------------------------------------------------


@needs_db
def test_the_picker_holds_the_views_in_the_lists_order_and_marks_the_first(make_app, folder, member):
    write_view(folder, "a-ohne-ordnung")
    write_view(folder, "b-zweite", order=2)
    write_view(folder, "c-erste", order=1)
    write_view(folder, "d-nur-admins", roles=["admin"])
    client = _signed_in(make_app(), member.email)

    html = client.get("/custom-views").data.decode("utf-8")
    listed = [v["id"] for v in client.get("/api/custom-views").get_json()["views"]]

    assert _picker(html) == [("c-erste", True), ("b-zweite", False), ("a-ohne-ordnung", False)]
    assert [view_id for view_id, _ in _picker(html)] == listed
    assert 'data-view="c-erste"' in html
    assert re.search(r'id="cvBarTitle">Ansicht c-erste<', html)


@needs_db
def test_the_address_selects_the_view(make_app, folder, member):
    write_view(folder, "c-erste", order=1)
    write_view(folder, "b-zweite", order=2)
    client = _signed_in(make_app(), member.email)

    html = client.get("/custom-views/b-zweite").data.decode("utf-8")

    assert _picker(html) == [("c-erste", False), ("b-zweite", True)]
    assert 'data-view="b-zweite"' in html
    assert re.search(r'id="cvBarTitle">Ansicht b-zweite<', html)
    assert "Eigene Ansicht" in html


@needs_db
def test_a_title_is_text_in_the_picker_and_in_the_bar(make_app, folder, member):
    write_view(folder, "uebersicht", title={"de": XSS, "en": XSS})
    client = _signed_in(make_app(), member.email)

    html = client.get("/custom-views/uebersicht").data.decode("utf-8")

    assert XSS not in html
    assert html.count("&lt;img src=x onerror=alert(1)&gt;") == 2   # the picker and the bar


@needs_db
def test_the_page_frames_a_view_in_a_sandbox_for_scripts_only(make_app, folder, member):
    write_view(folder, "uebersicht")
    client = _signed_in(make_app(), member.email)

    html = client.get("/custom-views").data.decode("utf-8")

    assert html.count("<iframe") == 1
    assert FRAME in html
    assert "allow-same-origin" not in html
    assert 'id="cvFrameHost"' in html
    assert re.search(r'href="/static/css/custom_views\.css\?v=[^"]+"', html)
    assert re.search(r'<script src="/static/js/custom_views\.js\?v=[^"]+"></script>', html)


@needs_db
def test_the_admin_block_is_there_for_an_admin_only(make_app, folder, member, admin):
    write_view(folder, "uebersicht")
    (write_view(folder, "kaputt") / "view.json").write_text("{not json", encoding="utf-8")
    app = make_app()

    for_admin = _signed_in(app, admin.email).get("/custom-views").data.decode("utf-8")
    for_member = _signed_in(app, member.email).get("/custom-views").data.decode("utf-8")

    assert 'id="cvAdmin"' in for_admin and "Dateien und Prüfung" in for_admin
    assert 'id="cvAdminFiles"' in for_admin
    assert "kaputt" in for_admin and "Die Datei view.json ist kein gültiges JSON." in for_admin
    assert 'id="cvAdmin"' not in for_member and "Dateien und Prüfung" not in for_member
    assert "cvAdminFiles" not in for_member and "kaputt" not in for_member


@needs_db
def test_an_admin_whose_views_are_all_valid_still_gets_the_block_and_a_member_never(make_app, folder, member,
                                                                                   admin):
    write_view(folder, "uebersicht")
    write_view(folder, "zweite", order=2)
    app = make_app()

    for_admin = _signed_in(app, admin.email)
    for_member = _signed_in(app, member.email)

    for path in ("/custom-views", "/custom-views/zweite"):
        admin_page = for_admin.get(path).data.decode("utf-8")
        member_page = for_member.get(path).data.decode("utf-8")
        assert 'id="cvAdmin"' in admin_page and "Dateien und Prüfung" in admin_page
        assert 'id="cvAdminFiles"' in admin_page
        assert "Alle Ansichten im Ordner sind gültig." in admin_page and "ungültig</span>" not in admin_page
        assert 'id="cvAdmin"' not in member_page and "Dateien und Prüfung" not in member_page
        assert "cvAdminFiles" not in member_page and "Alle Ansichten im Ordner" not in member_page


# -- the files, as written ------------------------------------------------------------------


def test_the_template_frames_with_scripts_only_and_carries_no_inline_script():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert source.count("<iframe") == 1
    assert FRAME in source
    assert 'sandbox="allow-scripts"' in source
    assert "allow-same-origin" not in source
    assert not re.search(r"\son[a-z]+\s*=", source, re.I)           # no inline event handlers
    assert not re.search(r"\sstyle\s*=", source, re.I)               # no inline styles
    for tag in re.findall(r"<script\b[^>]*>", source, re.I):         # every script is a file
        assert re.search(r'\ssrc="', tag), tag


def test_the_script_never_loosens_the_frame_or_writes_markup():
    source = SCRIPT.read_text(encoding="utf-8")

    for refused in ("allow-same-origin", "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                    "eval(", "new Function", "setAttribute('style'", 'setAttribute("style"'):
        assert refused not in source, refused
    assert "addEventListener('message'" not in source and 'addEventListener("message"' not in source
    assert "window.open(href, '_blank', 'noopener,noreferrer')" in source
    assert STYLES.read_text(encoding="utf-8").strip()


@needs_node
def test_the_script_parses():
    completed = subprocess.run([NODE, "--check", str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr


# -- the host's logic, run in node ---------------------------------------------------------

HOST_SCENARIO = r"""
const host = require(process.argv[2]);
const out = {exports: Object.keys(host).sort()};

class Odd {}
const shapes = {
  read: {id: 1, kind: "read", query: {type: "A"}},
  open: {id: 2, kind: "open", url: "https://example.org/x"},
  resize: {id: 3, kind: "resize", height: 480},
  resizeZero: {id: 0, kind: "resize", height: -5},
  notObject: "read",
  nullMessage: null,
  array: [1, "read"],
  noId: {kind: "read", query: {}},
  stringId: {id: "1", kind: "read", query: {}},
  floatId: {id: 1.5, kind: "read", query: {}},
  hugeId: {id: 2 ** 60, kind: "read", query: {}},
  unknownKind: {id: 4, kind: "write", query: {}},
  protoKind: {id: 5, kind: "toString"},
  queryNull: {id: 6, kind: "read", query: null},
  queryArray: {id: 7, kind: "read", query: []},
  queryString: {id: 8, kind: "read", query: "{}"},
  queryOdd: {id: 9, kind: "read", query: new Odd()},
  queryDate: {id: 9, kind: "read", query: new Date(0)},
  urlNumber: {id: 10, kind: "open", url: 7},
  urlLong: {id: 11, kind: "open", url: "https://example.org/" + "a".repeat(2029)},
  urlLimit: {id: 12, kind: "open", url: "https://example.org/" + "a".repeat(2028)},
  heightNaN: {id: 13, kind: "resize", height: NaN},
  heightInfinite: {id: 14, kind: "resize", height: Infinity},
  heightString: {id: 15, kind: "resize", height: "300"},
};
out.shapes = Object.fromEntries(Object.entries(shapes).map(([k, m]) => [k, host.shapeOf(m)]));

const links = ["knovas.atlassian.net", "example.org"];
const urls = {
  plain: "https://knovas.atlassian.net/browse/SS-1",
  upper: "https://KNOVAS.Atlassian.NET/browse/SS-1",
  defaultPort: "https://example.org:443/a",
  http: "http://example.org/a",
  javascript: "javascript:alert(1)",
  data: "data:text/html,x",
  user: "https://me@example.org/a",
  password: "https://me:pw@example.org/a",
  port: "https://example.org:8443/a",
  sub: "https://evil.example.org/a",
  suffix: "https://example.org.evil.test/a",
  trailingDot: "https://example.org./a",
  relative: "/browse/SS-1",
  garbage: "not a url",
  backslash: "https:\\\\example.org\\a",
};
out.links = Object.fromEntries(Object.entries(urls).map(([k, u]) => [k, host.allowedLink(u, links)]));
out.linksNoList = host.allowedLink(urls.plain, undefined);
out.linksOddList = host.allowedLink(urls.plain, [7, null, "KNOVAS.atlassian.net"]);

out.heights = [100, 200, 480.4, 20000, 99999, -1].map(host.clampHeight);
out.retry = ["1", " 7 ", "0", "", null, undefined, "Wed, 21 Oct 2015 07:28:00 GMT", "-3", "2.5", "99999999999"]
  .map((v) => host.retryAfterSeconds(v));

function response(status, body, headers) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {get: (name) => (headers || {})[name.toLowerCase()] ?? null},
    json: () => (body === undefined ? Promise.reject(new SyntaxError("no json")) : Promise.resolve(body)),
  };
}

(async () => {
  const answers = [];
  const calls = [];
  const opened = [];
  const heights = [];
  let release;
  const effects = {
    csrf: () => "token-1",
    open: (href) => opened.push(href),
    resize: (px) => heights.push(px),
    fetch: (url, init) => {
      calls.push({url, method: init.method, headers: init.headers, body: init.body, signal: !!init.signal});
      const query = JSON.parse(init.body);
      if (query.type === "slow") return new Promise((resolve) => { release = () => resolve(response(200, {nodes: []})); });
      if (query.type === "budget") return Promise.resolve(response(429, {success: false, error: "Das Budget ist aufgebraucht."}, {"retry-after": "12"}));
      if (query.type === "proxy") return Promise.resolve(response(502, undefined));
      if (query.type === "offline") return Promise.reject(new TypeError("Failed to fetch"));
      return Promise.resolve(response(200, {nodes: [{key: "A-1"}], complete: true}));
    },
  };
  const session = host.createHost("uebersicht", ["example.org"], effects, (a) => answers.push(a));

  await session.handle("garbage");
  await session.handle({id: "x", kind: "read", query: {}});
  await session.handle({id: 1, kind: "read", query: {type: "A"}});
  const slow = session.handle({id: 2, kind: "read", query: {type: "slow"}});
  await session.handle({id: 3, kind: "read", query: {type: "A"}});          // while 2 runs: refused
  release();
  await slow;
  await session.handle({id: 4, kind: "read", query: {type: "budget"}});
  await session.handle({id: 5, kind: "read", query: {type: "proxy"}});
  await session.handle({id: 6, kind: "read", query: {type: "offline"}});
  await session.handle({id: 7, kind: "read", query: null});
  const cyclic = {type: "A"}; cyclic.self = cyclic;
  await session.handle({id: 8, kind: "read", query: cyclic});
  await session.handle({id: 9, kind: "open", url: "https://example.org/a?b=c#d"});
  await session.handle({id: 10, kind: "open", url: "https://example.org.evil.test/"});
  await session.handle({id: 11, kind: "resize", height: 50000});
  await session.handle({id: 12, kind: "resize", height: 10});
  const late = session.handle({id: 13, kind: "read", query: {type: "slow"}});
  session.close();
  release();
  await late;
  await session.handle({id: 14, kind: "resize", height: 300});             // closed: nothing
  out.session = {answers, calls, opened, heights};
  process.stdout.write(JSON.stringify(out));
})().catch((error) => { process.stdout.write(JSON.stringify({crash: String(error && error.stack)})); });
"""


@pytest.fixture(scope="module")
def host_result(tmp_path_factory):
    path = tmp_path_factory.mktemp("host") / "scenario.js"
    path.write_text(HOST_SCENARIO, encoding="utf-8")
    completed = subprocess.run([NODE, str(path), str(SCRIPT)], capture_output=True, text=True, encoding="utf-8",
                               timeout=60)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert "crash" not in result, result.get("crash")
    return result


@needs_node
def test_the_host_exports_its_logic_only_to_node(host_result):
    assert host_result["exports"] == [
        "MAX_HEIGHT", "MAX_URL", "MIN_HEIGHT", "allowedLink", "clampHeight", "createHost", "retryAfterSeconds",
        "shapeOf",
    ]


@needs_node
def test_a_message_is_a_request_only_in_its_exact_shape(host_result):
    assert host_result["shapes"] == {
        "read": "read", "open": "open", "resize": "resize", "resizeZero": "resize",
        # not a request: never answered
        "notObject": "ignore", "nullMessage": "ignore", "array": "ignore", "noId": "ignore", "stringId": "ignore",
        "floatId": "ignore", "hugeId": "ignore", "unknownKind": "ignore", "protoKind": "ignore",
        # a request with a wrong field: answered with a refusal
        "queryNull": "invalid", "queryArray": "invalid", "queryString": "invalid", "queryOdd": "invalid",
        "queryDate": "invalid", "urlNumber": "invalid", "urlLong": "invalid", "urlLimit": "open",
        "heightNaN": "invalid", "heightInfinite": "invalid", "heightString": "invalid",
    }


@needs_node
def test_a_view_opens_only_https_links_to_its_own_hosts(host_result):
    assert host_result["links"] == {
        "plain": "https://knovas.atlassian.net/browse/SS-1",
        "upper": "https://knovas.atlassian.net/browse/SS-1",
        "defaultPort": "https://example.org/a",
        "http": None, "javascript": None, "data": None, "user": None, "password": None, "port": None,
        "sub": None, "suffix": None, "trailingDot": None, "relative": None, "garbage": None,
        "backslash": "https://example.org/a",
    }
    assert host_result["linksNoList"] is None
    assert host_result["linksOddList"] == "https://knovas.atlassian.net/browse/SS-1"


@needs_node
def test_heights_and_retry_after(host_result):
    assert host_result["heights"] == [200, 200, 480, 20000, 20000, 200]
    assert host_result["retry"] == [1, 7, 0, None, None, None, None, None, None, None]


@needs_node
def test_the_host_relays_reads_one_at_a_time_and_answers_in_german(host_result):
    session = host_result["session"]
    busy = "Bitte warten, bis die vorige Abfrage fertig ist."
    assert session["answers"] == [
        {"id": 1, "ok": True, "data": {"nodes": [{"key": "A-1"}], "complete": True}},
        {"id": 3, "ok": False, "status": 429, "error": busy},
        {"id": 2, "ok": True, "data": {"nodes": []}},
        {"id": 4, "ok": False, "status": 429, "error": "Das Budget ist aufgebraucht.", "retry_after": 12},
        {"id": 5, "ok": False, "status": 502,
         "error": "Der Server ist gerade nicht erreichbar. Bitte später erneut versuchen."},
        {"id": 6, "ok": False, "status": 0, "error": "Keine Verbindung zum Server. Bitte erneut versuchen."},
        {"id": 7, "ok": False, "status": 400, "error": "Diese Anfrage ist ungültig."},
        {"id": 8, "ok": False, "status": 400, "error": "Diese Anfrage ist ungültig."},
        {"id": 9, "ok": True, "data": None},
        {"id": 10, "ok": False, "status": 403, "error": "Dieser Link ist für diese Ansicht nicht freigegeben."},
        {"id": 11, "ok": True, "data": None},
        {"id": 12, "ok": True, "data": None},
    ]
    assert len(session["calls"]) == 6          # 1, 2, 4, 5, 6 and 13; never 3, 7, 8
    for call in session["calls"]:
        assert call["url"] == "/api/custom-views/uebersicht/read"
        assert call["method"] == "POST"
        assert call["headers"]["X-CSRF-Token"] == "token-1"
        assert call["headers"]["Content-Type"] == "application/json"
        assert call["signal"] is True
    assert session["opened"] == ["https://example.org/a?b=c#d"]
    assert session["heights"] == [20000, 200]
