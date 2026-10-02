"""Test helpers: drive the mock Knovas API through `requests`, in-process.

Load this file by path, never as a package: `app` is a module name the
RemoteController (src/app.py) already uses.

    spec = importlib.util.spec_from_file_location("knovas_mock_testing", path)
    testing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(testing)

and `pytest.skip` when the file is absent (a checkout without the mock).

- `load_mock_app(**kw)`: `create_app(**kw)` of the mock (doc_fields,
  calibrated, refuse_init_fields, brokered); a fresh app with its own state.
- `mock_state(app)`: that app's `MockState` (request log, seeds, switches).
- `WsgiSession(app)`: a `requests.Session` whose adapters hand every request
  to `app.test_client()`. Clients keep their real `https://` base URL and
  their cert/verify settings (ignored here), so a client's rule that mTLS
  needs https is never relaxed for a test.
- `wsgi_request(app)`: a drop-in for `requests.request`, for code that calls
  the module function (monkeypatch `requests.request` with it).
"""

from __future__ import annotations

import importlib.util
import io
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers

MOCK_APP_PATH = Path(__file__).resolve().parent / "app.py"
_MODULE_NAME = "knovas_mock_api_app"

# Hop-by-hop or recomputed by the test client from the body it is given.
_DROPPED_HEADERS = frozenset({"content-length", "host", "connection", "transfer-encoding"})


def mock_module():
    """The mock's app.py, imported once under a name no component uses."""
    module = sys.modules.get(_MODULE_NAME)
    if module is None:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, MOCK_APP_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(_MODULE_NAME, None)
            raise
    return module


def load_mock_app(**kw: Any):
    """A new mock app: create_app(doc_fields=..., calibrated=...,
    refuse_init_fields=..., brokered=...). Apps share no state."""
    return mock_module().create_app(**kw)


def mock_state(app):
    """The `MockState` of an app built by `load_mock_app`."""
    return app.extensions["knovas_mock"]


class WsgiAdapter(BaseAdapter):
    """A transport adapter that answers from a Flask app instead of a socket.

    TLS settings (`verify`, `cert`), proxies and timeouts are accepted and
    ignored: nothing leaves the process.
    """

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._client = app.test_client()

    def send(self, request: requests.PreparedRequest, stream: bool = False, timeout: Any = None,
             verify: Any = True, cert: Any = None, proxies: Any = None) -> requests.Response:
        parts = urlsplit(request.url)
        body = request.body
        if isinstance(body, str):
            body = body.encode("utf-8")
        elif body is not None and not isinstance(body, bytes):
            body = b"".join(body)          # a generator body (chunked upload)
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _DROPPED_HEADERS]
        answer = self._client.open(
            path=parts.path or "/",
            base_url=f"{parts.scheme}://{parts.netloc}",
            query_string=parts.query,
            method=request.method,
            headers=headers,
            data=body or b"",
        )
        content = answer.get_data()
        response = requests.Response()
        response.status_code = answer.status_code
        response.headers = CaseInsensitiveDict(answer.headers.items())
        response.encoding = get_encoding_from_headers(response.headers)
        try:
            response.reason = HTTPStatus(answer.status_code).phrase
        except ValueError:
            response.reason = ""
        response._content = content
        response._content_consumed = True
        response.raw = io.BytesIO(content)
        response.url = request.url
        response.request = request
        response.connection = self
        return response

    def close(self) -> None:
        pass


class WsgiSession(requests.Session):
    """A `requests.Session` bound to one mock app, for `https://` and
    `http://` URLs alike. The environment (proxies, CA bundles, netrc) is
    not consulted, so a test behaves the same on every machine."""

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.trust_env = False
        adapter = WsgiAdapter(app)
        self.mount("https://", adapter)
        self.mount("http://", adapter)


def wsgi_request(app) -> Callable[..., requests.Response]:
    """`requests.request(method, url, **kwargs)` answered by *app*."""

    def request(method: str, url: str, **kwargs: Any) -> requests.Response:
        with WsgiSession(app) as session:
            return session.request(method=method, url=url, **kwargs)

    return request
