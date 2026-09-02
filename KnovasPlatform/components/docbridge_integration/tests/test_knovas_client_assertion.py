"""The gap these tests exist to close.

`assertion.py` was complete, correct and thoroughly unit-tested — and nothing
called it. So these assert on the *wire*, not on the mint function: the bug was
never in minting.
"""
import pytest

from conftest import PLATFORM_DB_TEST_DSN, _ASSERTION_PASSWORD, platform_db_reachable
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


def _write_dummy_pair(tmp_path):
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    cert.write_text("CERT\n", encoding="utf-8")
    key.write_text("KEY\n", encoding="utf-8")
    return cert, key


def test_legacy_renew_and_validation_do_not_deadlock_or_send_unsigned_query(
    client_with_broker_no_user, captured_requests, tmp_path
):
    """Control-plane cert routes may omit a user; they must not dump
    unsigned `/secured/query`, and must finish while `_cert_lock` is held."""
    import threading

    api = client_with_broker_no_user._client
    cert, key = _write_dummy_pair(tmp_path)
    api.cert_path = str(cert)
    api.key_path = str(key)
    api.customer_id = "cust-1"

    result = {}

    def run():
        try:
            acquired = api._cert_lock.acquire(blocking=False)
            result["acquired"] = acquired
            try:
                result["validated"] = api._validate_renewed_certificate(
                    str(cert), str(key)
                )
                result["legacy"] = api._attempt_certificate_renewal_legacy()
            finally:
                if acquired:
                    api._cert_lock.release()
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), (
        "cert validation/legacy deadlocked while holding _cert_lock"
    )
    assert "error" not in result, result.get("error")
    assert result.get("acquired") is True

    urls = [call.url for call in captured_requests]
    assert any("/secured/health" in url for url in urls)
    assert any("/secured/generate_certificate" in url for url in urls)
    query_calls = [call for call in captured_requests if "/secured/query" in call.url]
    assert query_calls == []
    for call in query_calls:
        assert ASSERTION_FIELD in call.body


def test_control_plane_cert_calls_attach_assertion_when_a_user_exists(
    client_with_broker, captured_requests, tmp_path
):
    api = client_with_broker._client
    cert, key = _write_dummy_pair(tmp_path)
    api.cert_path = str(cert)
    api.key_path = str(key)
    api.customer_id = "cust-1"

    api._validate_renewed_certificate(str(cert), str(key))
    api._attempt_certificate_renewal_legacy()

    health = [c for c in captured_requests if "/secured/health" in c.url]
    generate = [c for c in captured_requests if "/secured/generate_certificate" in c.url]
    assert health, "validation must call /secured/health"
    assert generate, "legacy renew must call /secured/generate_certificate"
    assert ASSERTION_FIELD in health[-1].body
    assert ASSERTION_FIELD in generate[-1].body
    assert not any("/secured/query" in c.url for c in captured_requests)


def test_health_check_without_a_user_is_control_plane_not_query(
    client_with_broker_no_user, captured_requests
):
    """Unauthenticated probes may hit /secured/health; they must not send
    unsigned data-plane `/secured/query`."""
    ok = client_with_broker_no_user._client.health_check()
    assert ok is True
    urls = [call.url for call in captured_requests]
    assert any("/secured/health" in url for url in urls)
    assert not any("/secured/query" in url for url in urls)


def test_production_request_scoped_broker_mints_via_gate_users_method(
    identity_app, identity_repo, broker_keypair
):
    """The production wrapper calls `gate.users()` (a method). Passing the
    attribute would mint nothing useful and only show up on a live request."""
    from identity.assertion import AssertionVerifier
    from identity.webauth import IdentityGate
    from web_interface.app import _RequestScopedBroker

    signer, public_pem, kid = broker_keypair
    user = identity_repo.create(
        email="brokered@testco.example",
        display_name="Brokered",
        password=_ASSERTION_PASSWORD,
    )
    identity_repo.set_access_groups(user.id, ["litigation"])

    gate = IdentityGate()
    assert callable(gate.users)
    broker = _RequestScopedBroker(gate, signer, "tenant-a")

    with identity_app.test_request_context("/"):
        token = broker.assertion_for(user)

    claims = AssertionVerifier({kid: public_pem}).verify(token, tenant="tenant-a")
    assert claims.subject == str(user.id)
    assert claims.groups == ("litigation",)
