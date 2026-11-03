"""How a component reads a source, one function per kind: a new kind of connection is one function and one entry.

A fetcher gets the source's settings from the Platform and its credential from this component's own environment
(``SYNC_CREDENTIAL_<SOURCE KEY>``: a credential never travels through the Platform), and writes the source's data where
the source's mappings read it. It runs on site, so a company's internal address is allowed here; the Platform's own
readers stay public-only. Errors carry a code, never an address or content (GI-DATA-02/04).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import requests

#: Where an HTTP/JSON source's mappings read the fetched document (the builder's compiled mappings name this file).
HTTP_DOCUMENT = "response.json"
MAX_BYTES = int(os.environ.get("SYNC_FETCH_MAX_BYTES") or 50_000_000)


class FetchError(RuntimeError):
    """The source could not be read; the message is a code for the run's events."""


def fetch_http_json(settings: Mapping[str, Any], credential: Optional[str], root: Path, *,
                    get: Callable[..., Any] = requests.get) -> dict:
    url = str(settings.get("url") or "")
    if not url.startswith("https://") and os.environ.get("SYNC_ALLOW_HTTP") != "true":
        raise FetchError("fetch.not_https")
    headers = {"Accept": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    try:
        answer = get(url, headers=headers, timeout=120, stream=True, allow_redirects=False)
    except requests.RequestException as exc:
        raise FetchError(f"fetch.{type(exc).__name__.lower()}") from None
    if answer.status_code != 200:
        raise FetchError(f"fetch.http_{answer.status_code}")
    body = b""
    for chunk in answer.iter_content(1 << 16):
        body += chunk
        if len(body) > MAX_BYTES:
            raise FetchError("fetch.too_large")
    try:
        json.loads(body)
    except ValueError:
        raise FetchError("fetch.not_json") from None
    (root / HTTP_DOCUMENT).write_bytes(body)
    return {"bytes": len(body)}


FETCHERS: dict[str, Callable[..., dict]] = {"http_json": fetch_http_json}


def credential_for(source_key: str, environ: Mapping[str, str] = os.environ) -> Optional[str]:
    name = "SYNC_CREDENTIAL_" + "".join(c if c.isalnum() else "_" for c in source_key).upper()
    return environ.get(name) or None
