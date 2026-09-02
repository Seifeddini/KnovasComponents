"""The gap these tests exist to close.

`assertion.py` was complete, correct and thoroughly unit-tested — and nothing
called it. So these assert on the *wire*, not on the mint function: the bug was
never in minting.
"""
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable
from identity.assertion import AssertionVerifier
from knovas_client import ASSERTION_FIELD

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)


def test_every_request_carries_an_assertion(client_with_broker, captured_requests):
    client_with_broker.search("Mietrecht")
    assert ASSERTION_FIELD in captured_requests[-1].body


def test_non_empty_post_body_keeps_payload_and_carries_assertion(
    client_with_broker, captured_requests
):
    documents = [{"doc_id": "doc-1", "title": "Mandate"}]

    client_with_broker.sync_document_batch(documents)

    request = captured_requests[-1]
    assert request.method == "POST"
    assert request.body["documents"] == documents
    assert ASSERTION_FIELD in request.body


def test_the_assertion_verifies_and_carries_the_users_groups(
    client_with_broker, captured_requests, broker_public_pem, broker_kid
):
    client_with_broker.search("Mietrecht")
    token = captured_requests[-1].body[ASSERTION_FIELD]
    claims = AssertionVerifier({broker_kid: broker_public_pem}).verify(
        token, tenant="tenant-a"
    )
    assert claims.groups == ("litigation",)
    assert claims.subject == client_with_broker.subject


def test_no_session_means_no_request_at_all(client_with_broker_no_user):
    """Fail closed. An unsigned call would resolve to 'unrestricted documents
    only' at the backend — more data than a correct request would return."""
    with pytest.raises(PermissionError):
        client_with_broker_no_user.search("Mietrecht")


def test_the_assertion_contains_no_personal_data(client_with_broker, captured_requests):
    client_with_broker.search("Mietrecht")
    assert "@" not in captured_requests[-1].body[ASSERTION_FIELD]


def test_two_calls_get_distinct_jtis(
    client_with_broker, captured_requests, broker_public_pem, broker_kid
):
    """Minted per request, not per session — so a revocation lands on the next
    request rather than at session expiry."""
    client_with_broker.search("a")
    client_with_broker.search("b")
    verifier = AssertionVerifier({broker_kid: broker_public_pem})
    first = verifier.verify(
        captured_requests[-2].body[ASSERTION_FIELD], tenant="tenant-a"
    )
    second = verifier.verify(
        captured_requests[-1].body[ASSERTION_FIELD], tenant="tenant-a"
    )
    assert first.jti != second.jti


def test_assertion_is_not_sent_as_a_header(client_with_broker, captured_requests):
    client_with_broker.search("Mietrecht")
    assert ASSERTION_FIELD not in captured_requests[-1].headers
