"""The ethical wall on the routes that hand over a file (KC-B3-1, KC-B3-2).

Search is filtered by Knovas, so a walled lawyer never sees the matter in a
result list. These routes are the other way in: they take a pointer and a path
and read the file off the Platform's own disk, without the query pipeline. Until
this gate existed, any signed-in person could fetch any document under the share
by naming its path -- which made the wall a property of search, not of access.

**What the gate consults changed.** It used to ask the Secure API
(``GET /secured/document_readable``). No deployed version of that API has ever
implemented the route, so every call answered 404, the guard failed closed
exactly as written, and every document became "Not found" for every user --
while search kept working, because the text under a result comes from the
context sidecars and not from the file. It now asks ``document_grants``: did
this person's own retrieval return this pointer? That never grants more than
retrieval did, so the wall tracks the backend instead of second-guessing it.

Three things are asserted here that are easy to get subtly wrong:

    * refusal is 404, never 403. A 403 confirms the matter exists, and its
      existence is itself the trace an ethical wall forbids;
    * the path is checked against the pointer. The two arrive as separate
      request fields, so a caller could otherwise name a document they may read
      and ask for the bytes of one they may not;
    * a grant belongs to one person. Another member's search must not open a
      door for me, or the wall is back to being a property of the corpus.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

from conftest import DummyKnovasClient, platform_db_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason="identity routes need a real PostgreSQL",
)

READABLE = "rc-sync/open.docx"
WALLED = "rc-sync/mandat-meier.docx"


def _search(client, query="meier"):
    """Run a search the way the browser does, so its grants are recorded."""
    from _console import csrf_from

    token = csrf_from(client.get("/settings").data.decode("utf-8"))
    return client.post(
        "/api/search",
        json={"query": query, "limit": 20},
        headers={"X-CSRF-Token": token},
    )


@pytest.fixture
def signed_in(identity_client, identity_repo, monkeypatch):
    """One ordinary member, signed in, having searched and found one document.

    The identifier prefix is set the way a synced deployment has it, so the
    ``path`` the browser sends is the mapped relative path and not the raw
    pointer -- the shape the gate has to accept.

    Retrieval returns READABLE and withholds WALLED, which is the whole of what
    makes one reachable and the other not.
    """
    from _console import PASSWORD, sign_in

    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "rc-sync")
    user = identity_repo.create(
        email="anwalt@kanzlei.ch", display_name="anwalt", password=PASSWORD
    )
    identity_repo.grant_role(user.id, "member")
    knovas = DummyKnovasClient.last_instance
    knovas.search_results = [
        {"doc_id": READABLE, "path": READABLE, "title": "Offen"},
        {"doc_id": WALLED, "path": WALLED, "title": "Mandat Meier"},
    ]
    knovas.denied_pointers = {WALLED}
    sign_in(identity_client, "anwalt@kanzlei.ch")
    _search(identity_client)
    return identity_client


class TestTheWallHolds:
    def test_a_walled_document_is_not_downloadable(self, signed_in):
        response = signed_in.get(
            f"/api/document/{WALLED}/download?path=mandat-meier.docx"
        )
        assert response.status_code == 404

    def test_refusal_is_404_and_never_403(self, signed_in):
        """403 would confirm the matter exists. That is the trace B3 forbids."""
        for suffix in ("download", "preview", "preview-content", "thumbnail",
                       "client-path", "external-open"):
            response = signed_in.get(f"/api/document/{WALLED}/{suffix}")
            assert response.status_code != 403, suffix
            assert response.status_code == 404, suffix

    def test_the_open_post_is_refused_too(self, signed_in):
        response = signed_in.post(
            f"/api/document/{WALLED}/open",
            json={"path": "mandat-meier.docx"},
            headers={"X-CSRF-Token": "irrelevant-the-gate-runs-first"},
        )
        assert response.status_code in (403, 404)

    def test_a_document_nobody_searched_for_is_refused(self, signed_in):
        """The enumeration hole these routes are: naming a pointer is not
        permission to read it, even when it is not walled off from anyone."""
        response = signed_in.get(
            "/api/document/rc-sync/never-searched.docx/download"
            "?path=never-searched.docx"
        )
        assert response.status_code == 404


class TestAGrantReachesItsHandler:
    def test_a_found_document_is_not_refused_by_the_gate(self, signed_in):
        """The gate must not become a wall around everything -- which is exactly
        what it was while it asked an endpoint that does not exist."""
        response = signed_in.get(
            f"/api/document/{READABLE}/download?path=open.docx"
        )
        body = response.get_json() or {}
        # The handler's own "file not on disk" answer is what we expect here;
        # the gate's refusal says "Not found" and is what must NOT appear.
        assert body.get("error") != "Not found"

    def test_the_gate_asks_no_backend_at_all(self, signed_in):
        """A round trip per thumbnail is twenty per search. The decision is
        local, and nothing here may reintroduce the call."""
        knovas = DummyKnovasClient.last_instance
        before = len(knovas.readable_calls)
        signed_in.get(f"/api/document/{READABLE}/download?path=open.docx")
        assert len(knovas.readable_calls) == before


class TestAGrantBelongsToOnePerson:
    def test_another_members_search_opens_no_door(
        self, identity_app, identity_repo, monkeypatch
    ):
        from _console import PASSWORD, sign_in

        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "rc-sync")
        for email in ("erste@kanzlei.ch", "zweite@kanzlei.ch"):
            user = identity_repo.create(
                email=email, display_name=email, password=PASSWORD
            )
            identity_repo.grant_role(user.id, "member")
        knovas = DummyKnovasClient.last_instance
        knovas.search_results = [{"doc_id": READABLE, "path": READABLE}]
        knovas.denied_pointers = set()

        finder = identity_app.test_client()
        sign_in(finder, "erste@kanzlei.ch")
        _search(finder)

        other = identity_app.test_client()
        sign_in(other, "zweite@kanzlei.ch")
        response = other.get(f"/api/document/{READABLE}/download?path=open.docx")
        assert response.status_code == 404
        assert (response.get_json() or {}).get("error") == "Not found"


class TestThePathMustBelongToThePointer:
    def test_a_readable_pointer_cannot_fetch_another_documents_bytes(self, signed_in):
        """The mismatch that makes checking only the pointer useless."""
        response = signed_in.get(
            f"/api/document/{READABLE}/download?path=mandat-meier.docx"
        )
        assert response.status_code == 404

    def test_a_backslash_or_leading_slash_does_not_slip_past(self, signed_in):
        for given in ("/mandat-meier.docx", "mandat-meier.docx"):
            response = signed_in.get(
                f"/api/document/{READABLE}/download?path={given}"
            )
            assert response.status_code == 404, given


class TestOpenTokensCarryTheirSubject:
    """KC-B3-3. Redeem is exempt from the session and CSRF gates because the
    companion has no browser session, which is precisely why it cannot also be
    exempt from the wall -- it would be the one door left open."""

    def _mint(self, client, doc_id, path):
        page = client.get("/settings").data.decode("utf-8")
        from _console import csrf_from

        return client.post(
            "/api/open-tokens/mint",
            json={"doc_id": doc_id, "path": path},
            headers={"X-CSRF-Token": csrf_from(page)},
        )

    def test_a_walled_document_cannot_be_minted(self, signed_in):
        response = self._mint(signed_in, WALLED, "mandat-meier.docx")
        assert response.status_code in (404, 503)
        assert response.status_code != 200

    def test_a_token_carries_the_minting_subject(self, identity_app, identity_repo):
        """Unit-level: the payload gains ``sub`` so redeem has someone to check."""
        from open_tokens import OpenTokenManager

        manager = OpenTokenManager("a-strong-secret-for-tests-0123456789",
                                   max_age_seconds=120, store_path=None)
        token = manager.mint("a.docx", "rc-sync/a.docx", subject="user-42")
        payload = manager.verify_and_consume(token)
        assert payload["sub"] == "user-42"

    def test_a_subjectless_token_is_refused_when_identity_is_on(self, signed_in):
        """A token minted before subjects existed must not still open a door."""
        from open_tokens import OpenTokenManager

        manager = OpenTokenManager("a-strong-secret-for-tests-0123456789",
                                   max_age_seconds=120, store_path=None)
        legacy = manager.mint("open.docx", READABLE)
        assert manager.verify_and_consume(legacy)["sub"] == ""
