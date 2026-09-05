"""The gap these tests exist to close.

``identity/assertion.py`` was complete, correct and thoroughly unit-tested --
and nothing called it. Every Secure API call left the Platform with no
subject and resolved to "unrestricted documents only". So these tests assert
on what leaves the process, not on what ``mint()`` returns: the bug was never
in minting.

Plan: docs/superpowers/plans/2026-09-02-auth-assertion-end-to-end.md, Task 4.

The first class runs without PostgreSQL against a stub broker. The last
class needs the real identity app and skips locally without a database; it
runs in CI, where a PostgreSQL skip is a failure.
"""

from __future__ import annotations

import base64
import json

import pytest

from identity.assertion import AssertionVerifier
from identity.broker_key import load_or_create_signer, public_pem
from knovas_client import ASSERTION_FIELD, KnovasAPIClient

from conftest import DummyKnovasClient, platform_db_reachable

TENANT = "tenant-a"
USER_ID = "11111111-1111-1111-1111-111111111111"


class _StubConfig:
    """Minimal ConfigLoader stand-in so the client needs no YAML or network."""

    def __init__(self, values):
        self._v = dict(values)

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        return bool(self._v.get(key, default))

    def get_int(self, key, default=0):
        return int(self._v.get(key, default))


class _User:
    def __init__(self, user_id=USER_ID, roles=("member",)):
        self.id = user_id
        self.roles = roles


class _StubBroker:
    """Signs with a real key; the only stubbed part is who is signed in."""

    def __init__(self, signer, user, groups=("litigation",)):
        self._signer, self._user, self._groups = signer, user, tuple(groups)
        self.mints = 0

    def current_user(self):
        return self._user

    def assertion_for(self, user):
        self.mints += 1
        return self._signer.mint(
            subject=str(user.id), tenant=TENANT, groups=list(self._groups),
            roles=list(user.roles),
        )


@pytest.fixture
def key_dir(tmp_path):
    return tmp_path / "broker"


@pytest.fixture
def signer(key_dir):
    key_dir.mkdir()
    return load_or_create_signer(key_dir)


@pytest.fixture
def verifier(key_dir, signer):
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    return AssertionVerifier({kid: public_pem(key_dir)})


@pytest.fixture
def client():
    return KnovasAPIClient(config_loader=_StubConfig({
        "api.base_url": "http://example.test",
        "api.customer_id": TENANT,
    }))


class TestWithPrincipal:
    def test_no_broker_means_the_body_goes_out_unchanged(self, client):
        assert client._with_principal({"query": "x"}) == {"query": "x"}
        assert client._with_principal(None) is None

    def test_no_signed_in_user_means_no_request_at_all(self, client, signer):
        """Fail closed. An unsigned call would resolve to 'unrestricted
        documents only' at the backend -- more than a correct request."""
        client.attach_principal_broker(_StubBroker(signer, user=None))
        with pytest.raises(PermissionError):
            client._with_principal({"query": "x"})

    def test_the_assertion_is_added_and_verifies_with_the_users_groups(
        self, client, signer, verifier
    ):
        client.attach_principal_broker(_StubBroker(signer, _User()))
        body = client._with_principal({"query": "Mietrecht"})
        assert body["query"] == "Mietrecht"
        claims = verifier.verify(body[ASSERTION_FIELD], tenant=TENANT)
        assert claims.groups == ("litigation",)
        assert claims.subject == USER_ID

    def test_a_missing_body_becomes_a_body_with_only_the_assertion(
        self, client, signer, verifier
    ):
        client.attach_principal_broker(_StubBroker(signer, _User()))
        body = client._with_principal(None)
        assert set(body) == {ASSERTION_FIELD}
        verifier.verify(body[ASSERTION_FIELD], tenant=TENANT)

    def test_a_non_object_body_is_refused_rather_than_guessed_at(self, client, signer):
        client.attach_principal_broker(_StubBroker(signer, _User()))
        with pytest.raises(TypeError):
            client._with_principal(["not", "a", "dict"])

    def test_the_caller_cannot_smuggle_their_own_assertion(self, client, signer, verifier):
        """The field is overwritten, never merged: a body-supplied token is
        replaced by the one minted for the signed-in user."""
        client.attach_principal_broker(_StubBroker(signer, _User()))
        body = client._with_principal({ASSERTION_FIELD: "forged.token.here"})
        assert body[ASSERTION_FIELD] != "forged.token.here"
        verifier.verify(body[ASSERTION_FIELD], tenant=TENANT)

    def test_the_assertion_contains_no_personal_data(self, client, signer):
        client.attach_principal_broker(_StubBroker(signer, _User()))
        token = client._with_principal({})[ASSERTION_FIELD]
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        assert "@" not in token
        assert "@" not in decoded.decode("utf-8")
        assert json.loads(decoded)["sub"] == USER_ID

    def test_two_calls_get_distinct_jtis(self, client, signer, verifier):
        """Minted per request, not per session -- a revocation lands on the
        next request rather than at session expiry."""
        client.attach_principal_broker(_StubBroker(signer, _User()))
        first = verifier.verify(client._with_principal({})[ASSERTION_FIELD], tenant=TENANT)
        second = verifier.verify(client._with_principal({})[ASSERTION_FIELD], tenant=TENANT)
        assert first.jti != second.jti


class TestBothRequestPathsUseTheSeam:
    """The retrying path and the no-retry path both go through _with_principal;
    a route using either cannot lose the assertion."""

    @pytest.fixture
    def wired(self, client, signer, monkeypatch):
        client.attach_principal_broker(_StubBroker(signer, _User()))
        sent = []

        class _Resp:
            status_code = 200
            ok = True

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"results": [], "total": 0}

        def _fake_request(**kw):
            sent.append(kw.get("json"))
            return _Resp()

        monkeypatch.setattr(client._session, "request", _fake_request)
        monkeypatch.setattr(client, "_rate_limit", lambda: None)
        monkeypatch.setattr(client, "_ensure_certificate_freshness", lambda: None)
        return client, sent

    def test_the_retrying_path_carries_it(self, wired):
        client, sent = wired
        client._make_request("POST", "/secured/query", {"query": "x"})
        assert ASSERTION_FIELD in sent[-1]

    def test_the_no_retry_path_carries_it(self, wired):
        client, sent = wired
        client._request_no_retry("POST", "/secured/analytics", {"event": "x"})
        assert ASSERTION_FIELD in sent[-1]


@pytest.mark.skipif(
    not platform_db_reachable(),
    reason="No PostgreSQL at the identity test DSN; runs in CI",
)
class TestAppWiring:
    """create_app() builds the broker from the gate and hands it to the client."""

    def test_the_app_attaches_a_broker_that_mints_for_the_signed_in_user(
        self, identity_app, identity_repo, tmp_path
    ):
        user = identity_repo.create(
            email="anwalt@testco.example", display_name="Anwalt",
            password="Correct-Horse-Battery-Staple-9",
        )
        identity_repo.set_access_groups(user.id, ["litigation"])

        broker = DummyKnovasClient.last_instance.principal_broker
        assert broker is not None, "create_app() must attach the broker"

        with identity_app.test_request_context():
            token = broker.assertion_for(user)

        key_dir = tmp_path / "broker"
        kid = (key_dir / "broker_ed25519.kid").read_text().strip()
        claims = AssertionVerifier({kid: public_pem(key_dir)}).verify(token, tenant=TENANT)
        assert claims.subject == str(user.id)
        assert claims.groups == ("litigation",)

    def test_nobody_signed_in_means_no_user_for_the_broker(self, identity_app):
        broker = DummyKnovasClient.last_instance.principal_broker
        with identity_app.test_request_context():
            assert broker.current_user() is None
