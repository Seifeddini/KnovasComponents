"""
Hardening / correctness regression tests for the Knovas mTLS API client.

Each test targets a CONFIRMED bug. Fakes are injected (fake requests.Session /
response) so nothing hits the network and the live ``knovas_api`` path is never used.

Covered:
  C3  Non-idempotent POSTs must not be retried on 4xx/5xx (duplicate ingestion).
  C4  Cert auto-renewal must validate the new pair before overwriting on-disk
      key/cert or swapping the live session, and must not leak temp key files.
  C5  ``filters`` must not be silently dropped in secured search mode.
  L1  Secured/mTLS mode must refuse a non-https base_url.
  L2  A ``{"results": null}`` secured response must not crash.
  L3  API requests must not follow redirects (defense-in-depth).
  L4  An unset required (``${VAR:?}``) env var must be surfaced, not fail-open to ''.
"""

import os

import pytest
import requests
import tenacity

from knovas_client import KnovasAPIClient
from config_loader import ConfigLoader


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class StubConfig:
    """Minimal stand-in for ConfigLoader so the client needs no YAML file/network."""

    def __init__(self, values):
        self._v = dict(values)

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        if key not in self._v:
            return default
        v = self._v[key]
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "yes", "1", "on")
        return bool(v)

    def get_int(self, key, default=0):
        if key not in self._v:
            return default
        try:
            return int(self._v[key])
        except (TypeError, ValueError):
            return default


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = {} if json_data is None else json_data
        self.content = b"{}"
        self.text = ""

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"{self.status_code} Server Error")
            err.response = self
            raise err


class FakeSession:
    """Records every request and delegates the response to a responder callable."""

    def __init__(self, responder):
        self.cert = None
        self.verify = None
        self._responder = responder
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responder(method, url, **kwargs)

    def close(self):
        pass


def make_client(
    base_url="https://knovas.test",
    use_secured_api=True,
    allow_legacy_api_fallback=False,
    cert_path="",
    key_path="",
    ca_cert_path="",
    customer_id="",
    cert_auto_renew_enabled=False,
    cert_renew_method="csr",
):
    values = {
        "api.base_url": base_url,
        "api.auth_type": "bearer",
        "api.api_key": "",
        "api.use_secured_api": use_secured_api,
        "api.allow_legacy_api_fallback": allow_legacy_api_fallback,
        "api.cert_path": cert_path,
        "api.key_path": key_path,
        "api.ca_cert_path": ca_cert_path,
        "api.customer_id": customer_id,
        "api.cert_auto_renew_enabled": cert_auto_renew_enabled,
        "api.cert_renew_threshold_days": 30,
        "api.cert_check_interval_seconds": 3600,
        "api.cert_renew_method": cert_renew_method,
        "api.encryption_matrix_path": "",
        "api.rate_limit.requests_per_second": 0,  # disable rate-limit sleeps
        "api.rate_limit.retry_attempts": 3,
        "api.rate_limit.retry_backoff": 2,
        "api.http_read_timeout": 5,
    }
    return KnovasAPIClient(config_loader=StubConfig(values))


def make_secured_client(**kw):
    kw.setdefault("cert_path", "/certs/client.crt")
    kw.setdefault("key_path", "/certs/client.key")
    kw.setdefault("ca_cert_path", "/certs/ca.crt")
    return make_client(**kw)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Neutralize tenacity backoff waits so retry tests run instantly."""
    monkeypatch.setattr(
        KnovasAPIClient._make_request.retry, "wait", tenacity.wait_none()
    )
    yield


# ---------------------------------------------------------------------------
# C3 — non-idempotent POSTs must not be retried on 4xx/5xx
# ---------------------------------------------------------------------------

class TestC3RetryOnlyOnConnectionErrors:
    def test_transmit_post_http_500_attempted_once(self):
        """Init/transmit POST that returns HTTP 500 must be attempted EXACTLY once."""
        client = make_secured_client()
        calls = {"n": 0}

        def responder(method, url, **kw):
            calls["n"] += 1
            return FakeResponse(500)

        client._session = FakeSession(responder)

        with pytest.raises(requests.exceptions.HTTPError):
            client._sync_single_document_secured({"doc_id": "D1", "path": "/x/y.txt"})

        assert calls["n"] == 1, f"expected 1 attempt, got {calls['n']} (retried!)"

    def test_http_error_on_idempotent_get_not_retried(self):
        """A 4xx/5xx on a GET propagates immediately (HTTPError, not retried)."""
        client = make_client()
        calls = {"n": 0}

        def responder(method, url, **kw):
            calls["n"] += 1
            return FakeResponse(500)

        client._session = FakeSession(responder)

        with pytest.raises(requests.exceptions.HTTPError):
            client._make_request("GET", "/secured/health")

        assert calls["n"] == 1, f"expected 1 attempt, got {calls['n']} (retried!)"

    def test_connection_error_on_idempotent_get_still_retries(self):
        """Transient connection errors on an idempotent GET are still retried (3x)."""
        client = make_client()
        calls = {"n": 0}

        def responder(method, url, **kw):
            calls["n"] += 1
            raise requests.exceptions.ConnectionError("boom")

        client._session = FakeSession(responder)

        with pytest.raises(Exception):
            client._make_request("GET", "/secured/health")

        assert calls["n"] == 3, f"expected 3 attempts, got {calls['n']}"


# ---------------------------------------------------------------------------
# C4 — cert auto-renewal: validate before overwrite/swap, no temp-key leak
# ---------------------------------------------------------------------------

class TestC4CertRenewalSafety:
    def _write_pair(self, tmp_path):
        cert = tmp_path / "client.crt"
        key = tmp_path / "client.key"
        ca = tmp_path / "ca.crt"
        cert.write_text("ORIGINAL CERT\n", encoding="utf-8")
        key.write_text("ORIGINAL KEY\n", encoding="utf-8")
        ca.write_text("CA\n", encoding="utf-8")
        return cert, key, ca

    def test_invalid_pair_keeps_original_and_leaves_no_temp(self, tmp_path, monkeypatch):
        cert, key, ca = self._write_pair(tmp_path)
        client = make_client(
            base_url="https://knovas.test",
            cert_path=str(cert),
            key_path=str(key),
            ca_cert_path=str(ca),
            customer_id="cust-1",
            cert_renew_method="legacy",
        )

        def responder(method, url, **kw):
            return FakeResponse(
                200,
                {"certificate_pem": "NEW INVALID CERT", "private_key": "NEW INVALID KEY"},
            )

        original_session = FakeSession(responder)
        client._session = original_session

        # Force the injected validation/health check to FAIL for the new pair.
        monkeypatch.setattr(
            client, "_validate_renewed_certificate", lambda c, k: False, raising=False
        )

        result = client._attempt_certificate_renewal()

        assert result is False
        # Original on-disk key/cert are UNTOUCHED.
        assert cert.read_text(encoding="utf-8") == "ORIGINAL CERT\n"
        assert key.read_text(encoding="utf-8") == "ORIGINAL KEY\n"
        # Live session was NOT swapped.
        assert client._session is original_session
        # No temp key/cert file leaked in the cert directory.
        leftovers = sorted(p.name for p in tmp_path.iterdir())
        assert leftovers == ["ca.crt", "client.crt", "client.key"], leftovers

    def test_valid_pair_installs_and_swaps(self, tmp_path, monkeypatch):
        cert, key, ca = self._write_pair(tmp_path)
        client = make_client(
            base_url="https://knovas.test",
            cert_path=str(cert),
            key_path=str(key),
            ca_cert_path=str(ca),
            customer_id="cust-1",
            cert_renew_method="legacy",
        )

        def responder(method, url, **kw):
            return FakeResponse(
                200, {"certificate_pem": "NEW CERT", "private_key": "NEW KEY"}
            )

        original_session = FakeSession(responder)
        client._session = original_session
        monkeypatch.setattr(
            client, "_validate_renewed_certificate", lambda c, k: True, raising=False
        )

        result = client._attempt_certificate_renewal()

        assert result is True
        assert cert.read_text(encoding="utf-8").strip() == "NEW CERT"
        assert key.read_text(encoding="utf-8").strip() == "NEW KEY"
        assert client._session is not original_session  # swapped in
        leftovers = sorted(p.name for p in tmp_path.iterdir())
        assert leftovers == ["ca.crt", "client.crt", "client.key"], leftovers


# ---------------------------------------------------------------------------
# C5 — filters must not be silently dropped in secured mode
# ---------------------------------------------------------------------------

class TestC5SecuredFilters:
    def test_filters_forwarded_into_secured_request_body(self):
        client = make_secured_client()
        captured = {}

        def responder(method, url, **kw):
            captured.update(kw)
            return FakeResponse(200, {"results": []})

        client._session = FakeSession(responder)

        client.search_documents("hello world", limit=5, filters={"akten_id": "A-42"})

        body = captured.get("json")
        assert isinstance(body, dict)
        assert body.get("filters") == {"akten_id": "A-42"}, (
            "secured search silently dropped 'filters' (case/matter scoping ignored)"
        )


# ---------------------------------------------------------------------------
# L1 — https enforcement for secured/mTLS mode
# ---------------------------------------------------------------------------

class TestL1HttpsEnforcement:
    def test_mtls_with_http_base_url_refused(self):
        with pytest.raises(ValueError):
            make_client(
                base_url="http://knovas.test",
                cert_path="/certs/client.crt",
                key_path="/certs/client.key",
                ca_cert_path="/certs/ca.crt",
            )

    def test_mtls_with_https_base_url_ok(self):
        client = make_client(
            base_url="https://knovas.test",
            cert_path="/certs/client.crt",
            key_path="/certs/client.key",
            ca_cert_path="/certs/ca.crt",
        )
        assert client.mtls_enabled is True


# ---------------------------------------------------------------------------
# L2 — {"results": null} must not crash None[:limit]
# ---------------------------------------------------------------------------

class TestL2NullResults:
    def test_null_results_returns_empty(self):
        client = make_secured_client()
        client._session = FakeSession(lambda m, u, **k: FakeResponse(200, {"results": None}))

        out = client.search_documents("q", limit=5)

        assert out["results"] == []
        assert out["total"] == 0


# ---------------------------------------------------------------------------
# L3 — requests must not follow redirects
# ---------------------------------------------------------------------------

class TestL3NoRedirects:
    def test_search_request_disables_redirects(self):
        client = make_secured_client()
        captured = {}

        def responder(method, url, **kw):
            captured.update(kw)
            return FakeResponse(200, {"results": []})

        client._session = FakeSession(responder)

        client.search_documents("q", limit=3)

        assert captured.get("allow_redirects") is False


# ---------------------------------------------------------------------------
# L4 — config_loader: required ${VAR:?} env must be surfaced, not fail-open ''
# ---------------------------------------------------------------------------

class TestL4RequiredEnvVar:
    def test_required_marker_raises_when_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KNOVAS_REQUIRED_SECRET", raising=False)
        cfg = tmp_path / "config.yaml"
        cfg.write_text('api:\n  api_key: "${KNOVAS_REQUIRED_SECRET:?must be set}"\n', encoding="utf-8")
        with pytest.raises(Exception):
            ConfigLoader(str(cfg))

    def test_required_marker_ok_when_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KNOVAS_REQUIRED_SECRET", "s3cr3t")
        cfg = tmp_path / "config.yaml"
        cfg.write_text('api:\n  api_key: "${KNOVAS_REQUIRED_SECRET:?}"\n', encoding="utf-8")
        loader = ConfigLoader(str(cfg))
        assert loader.get("api.api_key") == "s3cr3t"

    def test_default_marker_still_works(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KNOVAS_OPTIONAL", raising=False)
        cfg = tmp_path / "config.yaml"
        cfg.write_text('api:\n  api_key: "${KNOVAS_OPTIONAL:-fallback}"\n', encoding="utf-8")
        loader = ConfigLoader(str(cfg))
        assert loader.get("api.api_key") == "fallback"


# ---------------------------------------------------------------------------
# Guard tests — confirm existing good behavior is NOT weakened by the fixes
# ---------------------------------------------------------------------------

class TestGuardsDoNotWeaken:
    def test_timeout_and_json_on_every_request(self):
        client = make_secured_client()
        captured = {}

        def responder(method, url, **kw):
            captured.update(kw)
            return FakeResponse(200, {"results": []})

        client._session = FakeSession(responder)
        client.search_documents("q", limit=3)

        assert captured.get("timeout") == client.http_read_timeout
        # Body is passed as a JSON dict (serialized by requests), never string-built.
        assert isinstance(captured.get("json"), dict)
        assert captured["json"].get("Input") == "q"

    def test_tls_verification_not_disabled_for_mtls(self):
        client = make_client(
            base_url="https://knovas.test",
            cert_path="/certs/client.crt",
            key_path="/certs/client.key",
            ca_cert_path="/certs/ca.crt",
        )
        sess = client._build_session()
        assert sess.verify == "/certs/ca.crt"  # verify NEVER set to False
        assert sess.cert == ("/certs/client.crt", "/certs/client.key")
