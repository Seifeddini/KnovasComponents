"""PMVP-03: the page checks of ``pmvp_pages`` on three pages that exist today, one per area.

A page item of the Platform MVP copies the class of its area and puts its own addresses in. Each class runs the
checks the plan asks of every page (section 11): signed out, wrong role, wrong owner, missing token. Where a check
has nothing to check on today's page, a comment says why instead of a test that proves nothing.

What "refused" is today is written at the top of ``pmvp_pages.py``.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from conftest import PASSWORD, PLATFORM_DB_TEST_DSN, _csrf_from, platform_db_reachable
from pmvp_pages import (assert_missing_token_refused, assert_signed_out_refused, assert_wrong_owner_looks_missing,
                        assert_wrong_role_refused)

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)


# --- Workspace: search and documents ------------------------------------------------------------------

VERTRAG = "Mandate/2024-017/Vertrag.pdf"
SEARCH = {"query": "Vertrag Frist"}


class TestWorkspaceSearchAndDocuments:
    def test_signed_out(self, anon_client):
        assert_signed_out_refused(anon_client, "/")
        assert_signed_out_refused(anon_client, "/api/search", method="POST", json=SEARCH)
        assert_signed_out_refused(anon_client, f"/api/document/{VERTRAG}")

    # Wrong role: nothing to check. Search and documents are open to everyone who is signed in; what a person
    # finds is decided by Knovas per document, which is the next check.

    def test_wrong_owner(self, fake_graph, alice_client, bob_client):
        """A document belongs to the people whose own search returned it. For everyone else it is not there."""
        fake_graph.search_results = [{"doc_id": VERTRAG, "path": VERTRAG, "title": "Vertrag", "score": 0.9,
                                      "snippet": "Die Frist beträgt drei Monate."}]
        assert alice_client.post("/api/search", json=SEARCH).status_code == 200
        assert alice_client.get(f"/api/document/{VERTRAG}").status_code == 200

        assert_wrong_owner_looks_missing(bob_client, f"/api/document/{VERTRAG}",
                                         "/api/document/Mandate/2024-017/Gibt-es-nicht.pdf",
                                         role="a member whose search did not return it")

    def test_missing_token(self, fake_graph, member_client):
        assert_missing_token_refused(member_client, "/api/search", role="member", json=SEARCH,
                                     unchanged=lambda: list(fake_graph.search_calls))
        assert member_client.post("/api/search", json=SEARCH).status_code == 200     # with the token it is taken
        assert fake_graph.search_calls == [SEARCH["query"]]


# --- Admin: the people page ---------------------------------------------------------------------------

NEW_PERSON = {"email": "neu@kanzlei.ch", "display_name": "Neu", "password": PASSWORD}


class TestAdminPeople:
    def test_signed_out(self, anon_client):
        assert_signed_out_refused(anon_client, "/admin/people")
        assert_signed_out_refused(anon_client, "/admin/people/create", method="POST", data=NEW_PERSON)

    def test_wrong_role(self, member_client, identity_repo):
        assert_wrong_role_refused(member_client, "/admin/people", role="member")
        assert_wrong_role_refused(member_client, "/admin/people/create", role="member", method="POST",
                                  data=NEW_PERSON)
        assert identity_repo.get_by_email(NEW_PERSON["email"]) is None

    # Wrong owner: nothing to check. The page lists every account of the firm; nothing on it is one person's.

    def test_missing_token(self, admin_client, identity_repo):
        """The console's forms carry the token as a field, so the form is sent without it (400, not 403)."""
        assert_missing_token_refused(admin_client, "/admin/people/create", role="admin", data=NEW_PERSON,
                                     unchanged=lambda: len(identity_repo.list_all()))
        page = admin_client.get("/admin/people").get_data(as_text=True)
        taken = admin_client.post("/admin/people/create", data={**NEW_PERSON, "csrf_token": _csrf_from(page)})
        assert taken.status_code == 200                                               # with the token it is taken
        assert identity_repo.get_by_email(NEW_PERSON["email"]) is not None


# --- Builder: workspaces ------------------------------------------------------------------------------


def _workspaces(platform_db) -> int:
    return platform_db.execute("SELECT count(*) FROM builder_workspace").fetchone()[0]


def _new_workspace(admin_client, name: str) -> str:
    created = admin_client.post("/builder/api/workspaces", data={"name": name})
    assert created.status_code == 201
    return created.get_json()["url"].rsplit("/", 1)[1]


class TestBuilderWorkspaces:
    def test_signed_out(self, anon_client):
        assert_signed_out_refused(anon_client, "/builder/arbeitsbereich")
        # The Builder's JSON routes are under /builder/api/, not /api/: signed out they redirect like a page.
        assert_signed_out_refused(anon_client, "/builder/api/workspaces", method="POST", data={"name": "Muster AG"})

    def test_wrong_role(self, member_client, platform_db):
        assert_wrong_role_refused(member_client, "/builder/arbeitsbereich", role="member")
        assert_wrong_role_refused(member_client, "/builder/api/workspaces", role="member", method="POST",
                                  data={"name": "Muster AG"})
        assert _workspaces(platform_db) == 0

    def test_wrong_owner(self, admin_client, alice_client, alice):
        """Someone invited to one workspace cannot tell another workspace from an id that does not exist.

        Today both are 403, not 404: the Builder's role check answers before the workspace is looked up.
        """
        hers = _new_workspace(admin_client, "Muster A")
        another = _new_workspace(admin_client, "Muster B")
        ends_on = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        invited = admin_client.post(f"/builder/api/w/{hers}/access",
                                    json={"email": alice.email, "roles": ["builder_author"], "ends_on": ends_on})
        assert invited.status_code == 201
        assert alice_client.get(f"/builder/w/{hers}").status_code == 200

        role, nobodys = "someone invited to another workspace", uuid.uuid4()
        assert_wrong_owner_looks_missing(alice_client, f"/builder/w/{another}", f"/builder/w/{nobodys}", role=role)
        edit = {"op": "type.create", "key": "Invoice", "parent": "euo:Agreement"}
        assert_wrong_owner_looks_missing(alice_client, f"/builder/api/w/{another}/ontology",
                                         f"/builder/api/w/{nobodys}/ontology", role=role, method="POST", json=edit)

    def test_missing_token(self, admin_client, platform_db):
        assert_missing_token_refused(admin_client, "/builder/api/workspaces", role="admin",
                                     data={"name": "Muster AG"}, unchanged=lambda: _workspaces(platform_db))
        assert _new_workspace(admin_client, "Muster AG")                              # with the token it is taken
        assert _workspaces(platform_db) == 1
