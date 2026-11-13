"""Custom views: the routes, their headers and the audit entry (Task 9; SS-626, SS-627).

The real app with accounts on, graph mode and the switch on; a views folder
under tmp_path; the fake Brain of the views tests behind the Knovas client.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime

import pytest

pytest.importorskip("flask")

from conftest import _person, _signed_in, platform_db_reachable  # noqa: E402
from custom_views_app import NAV_LABEL, client_class, identity_app, views_folder, write_view  # noqa: E402
from graph_views.settings import ViewSettings  # noqa: E402
from graph_views.topology import PartialIndex  # noqa: E402
from views_fixture import FakeViewsClient  # noqa: E402

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

XSS = "<img src=x onerror=alert(1)>"
PAGE_CSP = "frame-ancestors 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'"


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch):
    """The read budget is process-wide; every test starts with a full one."""
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


def _audit(platform_db):
    from identity import audit

    return audit.recent(platform_db, action="custom_view.read")


# -- the list -----------------------------------------------------------------------


def test_the_list_holds_the_users_views_in_order_and_nothing_more(make_app, folder, member):
    write_view(folder, "a-ohne-ordnung")
    write_view(folder, "b-zweite", order=2, description={"de": "Zwei", "en": "Two"})
    write_view(folder, "c-erste", order=1)
    write_view(folder, "d-nur-admins", roles=["admin"])
    write_view(folder, "e-niemand", roles=[])
    client = _signed_in(make_app(), member.email)

    response = client.get("/api/custom-views")

    assert response.status_code == 200
    assert response.get_json() == {
        "views": [
            {"id": "c-erste", "title": {"de": "Ansicht c-erste", "en": "View c-erste"}, "description": None},
            {"id": "b-zweite", "title": {"de": "Ansicht b-zweite", "en": "View b-zweite"},
             "description": {"de": "Zwei", "en": "Two"}},
            {"id": "a-ohne-ordnung", "title": {"de": "Ansicht a-ohne-ordnung", "en": "View a-ohne-ordnung"},
             "description": None},
        ],
        "invalid": [],
    }
    assert "no-store" in response.headers["Cache-Control"]


def test_an_admin_also_gets_each_files_fingerprint_and_the_invalid_views(make_app, folder, admin):
    view = write_view(folder, "uebersicht", files={"index.html": "<p>Hallo</p>", "app.js": "knovas.ready(() => {});"},
                      scripts=["app.js"])
    (write_view(folder, "kaputt") / "view.json").write_text("{not json", encoding="utf-8")
    client = _signed_in(make_app(), admin.email)

    body = client.get("/api/custom-views").get_json()

    [picked] = body["views"]
    assert "files" not in picked                                  # the picker list carries no files
    [item] = body["files_by_view"]
    assert (item["id"], item["title"]) == ("uebersicht", {"de": "Ansicht uebersicht", "en": "View uebersicht"})
    assert [f["name"] for f in item["files"]] == ["view.json", "index.html", "app.js"]
    for file in item["files"]:
        data = (view / file["name"]).read_bytes()
        assert file["sha256"] == hashlib.sha256(data).hexdigest()
        changed = datetime.fromisoformat(file["changed_at"])
        assert changed.utcoffset().total_seconds() == 0
        assert abs(changed.timestamp() - (view / file["name"]).stat().st_mtime) < 1
    assert body["invalid"] == [{"id": "kaputt", "reason": "Die Datei view.json ist kein gültiges JSON."}]


def test_an_admin_gets_the_files_of_every_view_also_those_their_roles_may_not_open(make_app, folder, admin):
    """Spec P-1: per view, so a changed file of any view is noticed, not only of the admin's own."""
    write_view(folder, "a-fuer-alle")
    write_view(folder, "b-nur-mitglieder", roles=["member"])
    write_view(folder, "c-niemand", roles=[])
    client = _signed_in(make_app(), admin.email)

    body = client.get("/api/custom-views").get_json()

    assert [v["id"] for v in body["views"]] == ["a-fuer-alle"]    # the picker: what the admin may open
    assert [v["id"] for v in body["files_by_view"]] == ["a-fuer-alle", "b-nur-mitglieder", "c-niemand"]
    for item in body["files_by_view"]:
        assert [f["name"] for f in item["files"]] == ["view.json", "index.html"]
    assert client.get("/custom-views/b-nur-mitglieder").status_code == 404   # listed, still not opened


def test_a_member_never_sees_the_invalid_views_or_the_files(make_app, folder, member):
    write_view(folder, "uebersicht")
    write_view(folder, "nur-admins", roles=["admin"])
    (write_view(folder, "kaputt") / "view.json").write_text("{not json", encoding="utf-8")
    client = _signed_in(make_app(), member.email)

    body = client.get("/api/custom-views").get_json()
    page = client.get("/custom-views").data.decode("utf-8")

    assert body == {"views": [{"id": "uebersicht", "title": {"de": "Ansicht uebersicht", "en": "View uebersicht"},
                               "description": None}],
                    "invalid": []}                                # no files, no files_by_view
    assert "kaputt" not in page and "nur-admins" not in page


def test_titles_are_text_in_the_page_and_in_the_json(make_app, folder, admin):
    write_view(folder, "uebersicht", title={"de": XSS, "en": XSS})
    (write_view(folder, "kaputt") / "view.json").write_text("{not json", encoding="utf-8")
    client = _signed_in(make_app(), admin.email)

    page = client.get("/custom-views").data.decode("utf-8")
    body = client.get("/api/custom-views").get_json()

    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert XSS not in page
    assert body["views"][0]["title"] == {"de": XSS, "en": XSS}
    # the invalid views are drawn for the admin, as text
    assert "kaputt" in page and "Die Datei view.json ist kein gültiges JSON." in page


# -- one 404 for a view the user may not open and for one that does not exist --------------


def test_roles_empty_hides_a_view_and_answers_the_same_404_as_an_unknown_id(make_app, folder, admin):
    write_view(folder, "uebersicht")
    write_view(folder, "niemand", roles=[])
    client = _signed_in(make_app(), admin.email)

    assert [v["id"] for v in client.get("/api/custom-views").get_json()["views"]] == ["uebersicht"]
    for view_id in ("niemand", "gibt-es-nicht"):
        page = client.get(f"/custom-views/{view_id}")
        document = client.get(f"/api/custom-views/{view_id}/document")
        read = client.post(f"/api/custom-views/{view_id}/read", json={"type": "Invariant"})
        assert (page.status_code, document.status_code, read.status_code) == (404, 404, 404)
    unknown = client.get("/api/custom-views/gibt-es-nicht/document")
    hidden = client.get("/api/custom-views/niemand/document")
    assert hidden.get_json() == unknown.get_json()
    assert client.post("/api/custom-views/niemand/read", json={"type": "Invariant"}).get_json() == \
        client.post("/api/custom-views/gibt-es-nicht/read", json={"type": "Invariant"}).get_json()


def test_a_view_for_other_roles_answers_404_to_a_member(make_app, folder, member):
    write_view(folder, "uebersicht")
    write_view(folder, "nur-admins", roles=["admin"])
    client = _signed_in(make_app(), member.email)

    assert client.get("/custom-views/uebersicht").status_code == 200
    assert client.get("/custom-views/nur-admins").status_code == 404
    assert client.get("/api/custom-views/nur-admins/document").status_code == 404
    assert client.post("/api/custom-views/nur-admins/read", json={"type": "Invariant"}).status_code == 404


# -- the page -------------------------------------------------------------------------


def test_the_page_sends_its_headers_and_marks_the_sidebar(make_app, folder, member):
    write_view(folder, "uebersicht")
    client = _signed_in(make_app(), member.email)

    for path in ("/custom-views", "/custom-views/uebersicht"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert response.headers["Content-Security-Policy"] == PAGE_CSP
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
        assert "no-store" in response.headers["Cache-Control"]
        html = response.data.decode("utf-8")
        assert NAV_LABEL in html
        assert re.search(r'<a href="/custom-views" class="app-nav-item active"\s+aria-current="page">', html)
        assert 'name="csrf-token"' in html
        assert 'href="/custom-views/uebersicht"' in html


# -- the document -------------------------------------------------------------------------


def test_the_document_is_json_with_its_headers(make_app, folder, member):
    write_view(folder, "uebersicht", links={"hosts": ["knovas.atlassian.net"]})
    client = _signed_in(make_app(), member.email)

    response = client.get("/api/custom-views/uebersicht/document", headers={"Sec-Fetch-Dest": "empty"})

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.headers["Content-Security-Policy"] == "sandbox"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response.headers["Cache-Control"]
    body = response.get_json()
    assert set(body) == {"title", "html", "links"}
    assert body["title"] == {"de": "Ansicht uebersicht", "en": "View uebersicht"}
    assert body["links"] == ["knovas.atlassian.net"]
    assert body["html"].startswith("<!DOCTYPE html>")
    assert "<p>Hallo</p>" in body["html"]
    again = client.get("/api/custom-views/uebersicht/document").get_json()["html"]
    assert again != body["html"]            # a new nonce for every response


def test_the_document_is_refused_without_the_csrf_header_and_on_a_navigation(make_app, folder, member):
    write_view(folder, "uebersicht")
    app = make_app()
    bare = _signed_in(app, member.email, with_csrf=False)
    client = _signed_in(app, member.email)

    assert bare.get("/api/custom-views/uebersicht/document").status_code == 403
    assert bare.get("/api/custom-views/uebersicht/document",
                    headers={"X-CSRF-Token": "falsch"}).status_code == 403
    navigation = client.get("/api/custom-views/uebersicht/document", headers={"Sec-Fetch-Dest": "document"})
    assert navigation.status_code == 403
    assert "html" not in (navigation.get_json() or {})
    assert client.get("/api/custom-views/uebersicht/document",
                      headers={"Sec-Fetch-Dest": "iframe"}).status_code == 403


# -- the read ---------------------------------------------------------------------------


def test_a_read_answers_and_is_recorded(make_app, folder, member, platform_db):
    write_view(folder, "uebersicht")
    client = _signed_in(make_app(), member.email)

    response = client.post("/api/custom-views/uebersicht/read", json={"type": "Invariant", "facts": ["key"]})

    assert response.status_code == 200
    body = response.get_json()
    assert [n["name"] for n in body["nodes"]] == ["GI-GRAPH-00", "GI-GRAPH-01", "GI-GRAPH-02"]
    assert body["nodes"][0]["facts"] == {"key": "GI-GRAPH-00"}
    assert body["complete"] is True
    [row] = _audit(platform_db)
    assert row["actor_email"] == member.email
    assert (row["target_type"], row["target_id"], row["outcome"]) == ("custom_view", "uebersicht", "ok")
    assert row["detail"] == {"type": "Invariant", "nodes": 3, "complete": True, "outcome": "ok"}


def test_two_people_read_through_their_own_cache_slots(make_app, folder, identity_repo):
    """The topology and the facts a read caches are per person (subject_key over the client's
    ``_principal_broker``): what Knovas shows one person never reaches another through the cache."""
    write_view(folder, "uebersicht")
    alice = _person(identity_repo, "alice@kanzlei.ch", "Alice", "member")
    bob = _person(identity_repo, "bob@kanzlei.ch", "Bob", "member")
    hidden_for = {}

    class PerPerson(FakeViewsClient):
        """Knovas hides a node from Bob: in the topology, in the edges and in the facts."""

        @property
        def hidden(self):
            return hidden_for.get(self.current_email(), set())

        @hidden.setter
        def hidden(self, value):
            pass

    cls = client_class(base=PerPerson)
    app = make_app(client_cls=cls)
    walled = cls.instances[0].g.by_key["GI-GRAPH-01"]
    hidden_for[bob.email] = {walled}
    as_alice, as_bob = _signed_in(app, alice.email), _signed_in(app, bob.email)
    body = {"type": "Invariant", "facts": ["key"]}

    def read(client):
        response = client.post("/api/custom-views/uebersicht/read", json=body)
        assert response.status_code == 200
        return [(n["name"], n["facts"].get("key")) for n in response.get_json()["nodes"]]

    everything = [("GI-GRAPH-00", "GI-GRAPH-00"), ("GI-GRAPH-01", "GI-GRAPH-01"), ("GI-GRAPH-02", "GI-GRAPH-02")]
    assert read(as_alice) == everything               # Alice first: her topology and facts are cached
    assert read(as_bob) == [everything[0], everything[2]]
    assert read(as_alice) == everything
    slots = app.extensions["views"].cache._states
    assert sorted(json.loads(key)[1] for key in slots) == sorted([str(alice.id), str(bob.id)])
    # Alice's facts were read once, Bob's on their own, without the walled node; Alice's second read hit her cache
    batches = cls.instances[0].batch_bodies
    assert len(batches) == 2 and walled in batches[0]["node_ids"] and walled not in batches[1]["node_ids"]


class _PacedClient(FakeViewsClient):
    """Calls ``acquire`` once per call, as KnovasAPIClient.graph_get_many does: the views' pacer shows."""

    def graph_get_many(self, calls, *, workers=4, acquire=None, max_retries=3):
        for _ in calls:
            if acquire is not None:
                acquire()
        return super().graph_get_many(calls, workers=workers, acquire=acquire, max_retries=max_retries)


def test_a_read_that_loads_a_fallback_state_never_paces_and_leaves_the_views_their_pacer(
        make_app, folder, member, monkeypatch):
    """Spec § 3: the read never waits on the views' pacer, and the state it loads keeps the views'
    paced reader, so an existing view that expands it later stays paced."""
    write_view(folder, "uebersicht", reads={"Component": [], "Invariant": []})
    cls = client_class(base=_PacedClient)
    app = make_app(client_cls=cls)
    views = app.extensions["views"]
    monkeypatch.setattr(views.cache, "_settings", ViewSettings(edge_limit=0))   # the per-node fallback
    paced = []
    monkeypatch.setattr(views, "_acquire", lambda: paced.append(1))
    graph = cls.instances[0].g
    comp = graph.by_key["L2-KNOWLEDGE-GRAPH"]
    client = _signed_in(app, member.email)

    response = client.post("/api/custom-views/uebersicht/read",
                           json={"type": "Component", "ids": [comp],
                                 "links": [{"relation": "keeps", "direction": "out"}]})

    assert response.status_code == 200
    body = response.get_json()
    assert [l["name"] for l in body["nodes"][0]["links"]] == ["GI-GRAPH-00", "GI-GRAPH-01", "GI-GRAPH-02"]
    assert body["incomplete_why"] == ["topology_partial"]
    assert paced == []                                  # loaded and expanded without the views' pacer
    [state] = views.cache._states.values()
    assert isinstance(state.index, PartialIndex)
    state.index.ensure([graph.by_key["GI-GRAPH-00"]])   # an existing view's expansion: no reader of its own
    assert paced == [1]                                 # ... goes through the views' pacer


def test_a_read_outside_the_manifest_is_one_403_and_is_recorded(make_app, folder, member, platform_db):
    write_view(folder, "uebersicht")
    cls = client_class()
    client = _signed_in(make_app(client_cls=cls), member.email)

    response = client.post("/api/custom-views/uebersicht/read", json={"type": "Invariant", "facts": ["owner"]})
    other = client.post("/api/custom-views/uebersicht/read", json={"type": "Component"})

    assert response.status_code == other.status_code == 403
    assert response.get_json() == other.get_json()
    assert cls.instances[0].calls == []       # refused before anything was read
    rows = _audit(platform_db)
    assert len(rows) == 2
    for row in rows:
        assert row["detail"]["outcome"] == "forbidden"
        assert row["detail"]["nodes"] == 0 and row["detail"]["complete"] is False
        assert row["outcome"] == "denied"
        assert (row["target_type"], row["target_id"]) == ("custom_view", "uebersicht")


def test_a_read_without_the_csrf_header_is_refused_by_the_existing_gate(make_app, folder, member, platform_db):
    write_view(folder, "uebersicht")
    bare = _signed_in(make_app(), member.email, with_csrf=False)

    assert bare.post("/api/custom-views/uebersicht/read", json={"type": "Invariant"}).status_code == 403
    assert _audit(platform_db) == []


def test_a_read_with_access_groups_in_the_body_is_refused_by_the_existing_gate(make_app, folder, member):
    write_view(folder, "uebersicht")
    client = _signed_in(make_app(), member.email)

    response = client.post("/api/custom-views/uebersicht/read",
                           json={"type": "Invariant", "access_groups": ["g-lit"]})

    assert response.status_code == 400


def test_a_read_without_a_user_is_401(make_app, folder):
    write_view(folder, "uebersicht")
    response = make_app().test_client().post("/api/custom-views/uebersicht/read", json={"type": "Invariant"})

    assert response.status_code == 401


def test_a_malformed_query_is_400(make_app, folder, member):
    write_view(folder, "uebersicht")
    client = _signed_in(make_app(), member.email)

    assert client.post("/api/custom-views/uebersicht/read", json={"typ": "Invariant"}).status_code == 400
    assert client.post("/api/custom-views/uebersicht/read", json=["Invariant"]).status_code == 400
    assert client.post("/api/custom-views/uebersicht/read", data="kein json",
                       content_type="text/plain").status_code == 400


def test_a_where_over_too_many_nodes_is_422_and_an_empty_budget_429(make_app, folder, member):
    write_view(folder, "uebersicht")
    cls = client_class()
    client = _signed_in(make_app(client_cls=cls, max_nodes=2, facts_per_minute=1), member.email)

    broad = client.post("/api/custom-views/uebersicht/read", json={"type": "Invariant", "where": {"key": "x"}})
    assert broad.status_code == 422

    first = client.post("/api/custom-views/uebersicht/read", json={"type": "Invariant", "facts": ["key"]})
    assert first.status_code == 200 and len(first.get_json()["nodes"]) == 2   # the whole budget
    last = cls.instances[0].g.by_key["GI-GRAPH-02"]
    refused = client.post("/api/custom-views/uebersicht/read",
                          json={"type": "Invariant", "facts": ["key"], "ids": [last]})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    # only the first read's facts were read: the refusal came before the Brain was asked
    assert cls.instances[0].batch_bodies == [{"node_ids": [n["id"] for n in first.get_json()["nodes"]]}]


def test_a_brain_that_does_not_answer_is_502(make_app, folder, member):
    write_view(folder, "uebersicht")

    class Failing(FakeViewsClient):
        def _one(self, path, params):
            return (500, None) if path == "/edges" else super()._one(path, params)

    client = _signed_in(make_app(client_cls=client_class(base=Failing)), member.email)

    assert client.post("/api/custom-views/uebersicht/read", json={"type": "Invariant"}).status_code == 502


def test_a_second_read_of_one_user_while_the_first_runs_is_429(make_app, folder, member):
    write_view(folder, "uebersicht")
    entered, release = threading.Event(), threading.Event()

    class Holding(FakeViewsClient):
        def graph_facts_batch(self, node_ids):
            entered.set()
            assert release.wait(30)
            return super().graph_facts_batch(node_ids)

    client = _signed_in(make_app(client_cls=client_class(base=Holding)), member.email)
    first = {}

    def read_first():
        first["response"] = client.post("/api/custom-views/uebersicht/read",
                                        json={"type": "Invariant", "facts": ["key"]})

    thread = threading.Thread(target=read_first)
    thread.start()
    try:
        assert entered.wait(30)
        second = client.post("/api/custom-views/uebersicht/read", json={"type": "Invariant"})
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) >= 1
    finally:
        release.set()
        thread.join(30)
    assert first["response"].status_code == 200
    # the first read has left: the next one runs
    assert client.post("/api/custom-views/uebersicht/read", json={"type": "Invariant"}).status_code == 200


# -- the folder is refused: the feature stays off ---------------------------------------------


def test_a_views_folder_inside_the_documents_folder_keeps_the_feature_off(
        platform_db, identity_repo, tmp_path, monkeypatch, caplog):
    from conftest import DummyFileHandler

    caplog.set_level(logging.WARNING)
    documents = tmp_path / "documents"
    folder = documents / "custom-views"
    folder.mkdir(parents=True)
    write_view(folder, "uebersicht")
    monkeypatch.setattr(DummyFileHandler, "autodoc_path", str(documents))
    app = identity_app(platform_db, tmp_path, monkeypatch, folder)
    member = _person(identity_repo, "mia@kanzlei.ch", "Mia", "member")
    client = _signed_in(app, member.email)

    assert NAV_LABEL not in client.get("/").data.decode("utf-8")
    assert client.get("/custom-views").status_code == 404
    assert client.get("/api/custom-views").status_code == 404
    # read at start, so the log says it before the first request; once, not again per request
    refused = [r for r in caplog.records if "liegt im Dokumentenordner" in r.getMessage()]
    assert [(r.levelno, r.name) for r in refused] == [(logging.WARNING, "custom_views.registry")]


def test_a_failing_feature_check_counts_as_off_and_warns_once(make_app, folder, member, monkeypatch, caplog):
    from custom_views.registry import Registry

    write_view(folder, "uebersicht")
    client = _signed_in(make_app(), member.email)

    def broken(self):
        raise OSError("simulated")

    monkeypatch.setattr(Registry, "refusal", broken)
    caplog.set_level(logging.WARNING, logger="web_interface.app")
    for _ in range(3):
        home = client.get("/")
        assert home.status_code == 200 and NAV_LABEL not in home.data.decode("utf-8")
    assert client.get("/custom-views").status_code == 404
    warned = [r for r in caplog.records if "Eigene Ansichten nicht ermittelbar" in r.getMessage()]
    assert len(warned) == 1


def test_once_per_window_lets_one_call_through_a_minute():
    from web_interface.custom_views_routes import OncePer

    now = [100.0]
    gate = OncePer(60.0, clock=lambda: now[0])
    assert gate.ready() is True
    assert gate.ready() is False
    now[0] = 159.9
    assert gate.ready() is False
    now[0] = 160.0
    assert gate.ready() is True
    assert gate.ready() is False
