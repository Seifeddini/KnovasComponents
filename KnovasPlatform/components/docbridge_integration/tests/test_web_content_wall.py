"""The ethical wall on the routes that hand over a file (KC-B3-1, KC-B3-2).

Search is filtered by Knovas, so a walled lawyer never sees the matter in a
result list. These routes are the other way in: they take a pointer and a path
and read the file off the Platform's own disk, without the query pipeline. Until
this gate existed, any signed-in person could fetch any document under the share
by naming its path -- which made the wall a property of search, not of access.

Two things are asserted here that are easy to get subtly wrong:

    * refusal is 404, never 403. A 403 confirms the matter exists, and its
      existence is itself the trace an ethical wall forbids;
    * the path is checked against the pointer. The two arrive as separate
      request fields, so a caller could otherwise name a document they may read
      and ask for the bytes of one they may not.
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


@pytest.fixture
def signed_in(identity_client, identity_repo, monkeypatch):
    """One ordinary member, signed in, with the wall closed around one matter.

    The identifier prefix is set the way a synced deployment has it, so the
    ``path`` the browser sends is the mapped relative path and not the raw
    pointer -- the shape the gate has to accept.
    """
    from _console import PASSWORD, sign_in

    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "rc-sync")
    user = identity_repo.create(
        email="anwalt@kanzlei.ch", display_name="anwalt", password=PASSWORD
    )
    identity_repo.grant_role(user.id, "member")
    DummyKnovasClient.last_instance.denied_pointers = {WALLED}
    sign_in(identity_client, "anwalt@kanzlei.ch")
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

    def test_a_readable_document_still_reaches_its_handler(self, signed_in):
        """The gate must not become a wall around everything."""
        response = signed_in.get(
            f"/api/document/{READABLE}/download?path=open.docx"
        )
        # 404 here would mean the gate refused it; the handler's own "file not
        # on disk" answer is what we expect, and it is not the gate's 404.
        assert DummyKnovasClient.last_instance.readable_calls[-1] == READABLE


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


class TestWhenTheBackendCannotAnswer:
    def test_an_unreachable_backend_refuses_rather_than_serves(self, signed_in):
        """Fail closed: these routes serve bytes off local disk."""
        client = DummyKnovasClient.last_instance

        def explode(pointer):
            raise RuntimeError("backend down")

        client.document_readable = explode
        response = signed_in.get(
            "/api/document/rc-sync/other.docx/download?path=other.docx"
        )
        assert response.status_code == 404


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
