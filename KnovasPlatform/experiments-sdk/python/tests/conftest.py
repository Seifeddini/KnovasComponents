"""Fixtures for the SDK tests: the fake Platform, TLS material, clean env."""

from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from kx_fake_platform import TOKEN, FakePlatform, serve_fake  # noqa: E402

PROXY_VARS = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy",
              "ALL_PROXY", "no_proxy", "NO_PROXY")


@pytest.fixture(autouse=True)
def _no_proxies(monkeypatch):
    """The client honours proxy variables; the tests talk to 127.0.0.1 directly."""
    for name in PROXY_VARS + ("KNOVAS_URL", "KNOVAS_EXPERIMENTS_TOKEN", "SSL_CERT_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    import knovas_experiments

    monkeypatch.setattr(knovas_experiments, "RETRY_BACKOFF_SECONDS", (0.0,))


@pytest.fixture
def fake():
    platform = FakePlatform()
    server = serve_fake(platform)
    yield platform
    platform.release.set()
    server.shutdown()
    server.server_close()


@pytest.fixture
def second_fake():
    """Another server, e.g. the target of a redirect that must never be reached."""
    platform = FakePlatform()
    server = serve_fake(platform)
    yield platform
    platform.release.set()
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(fake):
    import knovas_experiments

    return knovas_experiments.Client(fake.url, TOKEN, timeout=5)


@pytest.fixture(scope="session")
def tls_material(tmp_path_factory):
    """A throwaway self-signed certificate for 127.0.0.1 (needs openssl)."""
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl not available")
    base = tmp_path_factory.mktemp("tls")
    cert, key = base / "cert.pem", base / "key.pem"
    result = subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
         "-out", str(cert), "-days", "1", "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip("openssl could not create a certificate")
    return str(cert), str(key)


@pytest.fixture
def tls_fake(tls_material):
    cert, key = tls_material
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    platform = FakePlatform()
    server = serve_fake(platform, context)
    yield platform
    server.shutdown()
    server.server_close()

