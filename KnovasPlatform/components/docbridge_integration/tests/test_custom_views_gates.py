"""Custom views: when the feature is there at all (D-4; SS-626, SS-627).

The sidebar entry and the page exist only when all four hold: the switch is on,
user accounts are on, the Cortex reads the graph, and the signed-in person may
open at least one valid view. In every other combination there is no entry and
all four routes answer 404, the same 404 as an address that does not exist.
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("flask")

from conftest import _person, _signed_in, platform_db_reachable  # noqa: E402
from custom_views_app import (  # noqa: E402
    NAV_LABEL,
    accounts_off_app,
    identity_app,
    views_folder,
    write_view,
)

needs_db = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

VIEW = "uebersicht"
READ = {"type": "Invariant"}


@pytest.fixture(autouse=True)
def _fresh_budget(monkeypatch):
    """The read budget is process-wide; every test starts with a full one."""
    monkeypatch.setattr("custom_views.budget._shared", {})


def _fill(folder, has_view: bool) -> None:
    """With a view for a member, or with only views a member may not open (and an invalid one)."""
    if has_view:
        write_view(folder, VIEW)
    else:
        write_view(folder, VIEW, roles=["admin"])
        write_view(folder, "niemand", roles=[])
        (write_view(folder, "kaputt") / "view.json").write_text("{not json", encoding="utf-8")


def _routes(client):
    """The four routes, as the page's script calls them (CSRF header sent by the client)."""
    return {
        "page": client.get("/custom-views"),
        "list": client.get("/api/custom-views"),
        "document": client.get(f"/api/custom-views/{VIEW}/document"),
        "read": client.post(f"/api/custom-views/{VIEW}/read", json=READ),
    }


COMBINATIONS = list(itertools.product([True, False], repeat=3))


@needs_db
@pytest.mark.parametrize("switch,graph_mode,has_view", COMBINATIONS)
def test_with_accounts_on_only_all_four_give_the_entry_and_the_routes(
        platform_db, identity_repo, tmp_path, monkeypatch, switch, graph_mode, has_view):
    folder = views_folder(tmp_path, monkeypatch)
    _fill(folder, has_view)
    app = identity_app(platform_db, tmp_path, monkeypatch, folder, enabled=switch, graph_mode=graph_mode)
    member = _person(identity_repo, "mia@kanzlei.ch", "Mia", "member")
    client = _signed_in(app, member.email)

    home = client.get("/").data.decode("utf-8")
    answers = _routes(client)

    if switch and graph_mode and has_view:
        assert NAV_LABEL in home
        assert {name: r.status_code for name, r in answers.items()} == {
            "page": 200, "list": 200, "document": 200, "read": 200}
    else:
        assert NAV_LABEL not in home
        assert {name: r.status_code for name, r in answers.items()} == {
            "page": 404, "list": 404, "document": 404, "read": 404}


def _fill_without_a_valid_view(folder, contents: str) -> None:
    if contents == "invalid":
        (write_view(folder, "kaputt") / "view.json").write_text("{not json", encoding="utf-8")
    elif contents == "nobody":
        write_view(folder, "niemand", roles=[])
    else:
        assert contents == "empty"


@needs_db
@pytest.mark.parametrize("graph_mode", [True, False])
@pytest.mark.parametrize("role,contents,offered", [
    ("admin", "invalid", True),     # the admin must be able to read why (spec P-1)
    ("admin", "nobody", True),
    ("admin", "empty", False),
    ("member", "invalid", False),
    ("member", "nobody", False),
])
def test_without_a_valid_view_only_an_admin_gets_the_entry_and_the_page_while_a_view_folder_exists(
        platform_db, identity_repo, tmp_path, monkeypatch, graph_mode, role, contents, offered):
    folder = views_folder(tmp_path, monkeypatch)
    _fill_without_a_valid_view(folder, contents)
    app = identity_app(platform_db, tmp_path, monkeypatch, folder, graph_mode=graph_mode)
    person = _person(identity_repo, "pat@kanzlei.ch", "Pat", role)
    client = _signed_in(app, person.email)

    home = client.get("/").data.decode("utf-8")
    page = client.get("/custom-views")
    listing = client.get("/api/custom-views")
    per_view = [client.get(f"/api/custom-views/{view_id}/document").status_code
                for view_id in ("kaputt", "niemand")]
    per_view += [client.post(f"/api/custom-views/{view_id}/read", json=READ).status_code
                 for view_id in ("kaputt", "niemand")]

    if offered and graph_mode:
        assert NAV_LABEL in home
        assert page.status_code == 200
        assert listing.status_code == 200
        body = listing.get_json()
        assert body["views"] == []
        assert body["invalid"] == ([{"id": "kaputt", "reason": "Die Datei view.json ist kein gültiges JSON."}]
                                   if contents == "invalid" else [])
    else:
        assert NAV_LABEL not in home
        assert (page.status_code, listing.status_code) == (404, 404)
    # a view must be valid and allowed for its own routes, admin or not
    assert per_view == [404, 404, 404, 404]


@pytest.mark.parametrize("switch,graph_mode,has_view", COMBINATIONS)
def test_with_accounts_off_there_is_never_an_entry_or_a_route(tmp_path, monkeypatch, switch, graph_mode, has_view):
    folder = views_folder(tmp_path, monkeypatch)
    _fill(folder, has_view)
    client = accounts_off_app(tmp_path, monkeypatch, folder, enabled=switch, graph_mode=graph_mode).test_client()

    home = client.get("/")
    assert home.status_code == 200
    assert NAV_LABEL not in home.data.decode("utf-8")
    assert {name: r.status_code for name, r in _routes(client).items()} == {
        "page": 404, "list": 404, "document": 404, "read": 404}
