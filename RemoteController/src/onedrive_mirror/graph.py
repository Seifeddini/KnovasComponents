"""Thin Microsoft Graph client for OneDrive mirror.

Scope: only what the mirror needs — app client-credentials token, paginated
folder enumeration, and authenticated streaming downloads. Retries with
``Retry-After`` honoured for throttling (429) and transient 5xx.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterator, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class GraphAuthError(RuntimeError):
    """Raised when the OAuth token endpoint returns a non-success response."""


class GraphRequestError(RuntimeError):
    """Raised when a Graph API call ultimately fails after retries."""


class DeltaTokenInvalid(RuntimeError):
    """Raised when a saved delta link is no longer accepted by Graph (HTTP 410).

    Callers should discard the saved token and restart with a fresh
    ``/root/delta`` enumeration.
    """


class GraphClient:
    """Application-only Graph client (client_credentials grant)."""

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        request_timeout: float = 60.0,
        max_attempts: int = 8,
        backoff: float = 2.0,
        jitter: float = 1.0,
    ) -> None:
        if not (tenant_id and client_id and client_secret):
            raise ValueError(
                "GraphClient requires tenant_id, client_id, and client_secret"
            )
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._request_timeout = request_timeout
        self._max_attempts = max(1, int(max_attempts))
        self._backoff = max(0.0, float(backoff))
        self._jitter = max(0.0, float(jitter))

        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._session = self._build_session()

    # ------------------------------------------------------------------ session
    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=self._backoff,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        return session

    # ---------------------------------------------------------------- auth
    def _ensure_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token

        token_url = (
            f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        )
        body = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        }
        resp = self._session.post(token_url, data=body, timeout=self._request_timeout)
        if resp.status_code != 200:
            raise GraphAuthError(
                f"OAuth token request failed: {resp.status_code} {resp.text[:300]}"
            )
        payload = resp.json()
        self._token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expires_at = now + timedelta(seconds=max(60, expires_in - 60))
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Accept": "application/json",
        }

    # ---------------------------------------------------------------- requests
    def _retry_after_seconds(self, response: requests.Response) -> float:
        h = response.headers.get("Retry-After")
        if not h:
            return 0.0
        try:
            return float(h)
        except ValueError:
            pass
        try:
            dt = parsedate_to_datetime(h)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _sleep_for_retry(self, attempt: int, response: Optional[requests.Response]) -> None:
        retry_after = self._retry_after_seconds(response) if response is not None else 0.0
        exp = min(2.0 ** (attempt - 1), 120.0)
        delay = max(retry_after, exp) + (random.uniform(0, self._jitter) if self._jitter else 0)
        logger.warning(
            "Graph retry in %.1fs (attempt %s, status=%s)",
            delay,
            attempt,
            response.status_code if response is not None else "n/a",
        )
        time.sleep(delay)

    def _request(
        self,
        method: str,
        url: str,
        *,
        stream: bool = False,
        timeout: Optional[float] = None,
    ) -> requests.Response:
        last: Optional[requests.Response] = None
        timeout = timeout if timeout is not None else self._request_timeout
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = self._session.request(
                    method, url, headers=self._headers(), stream=stream, timeout=timeout
                )
            except requests.RequestException:
                if attempt >= self._max_attempts:
                    raise
                self._sleep_for_retry(attempt, None)
                continue
            last = resp
            if resp.status_code in (429, 502, 503, 504) and attempt < self._max_attempts:
                self._sleep_for_retry(attempt, resp)
                continue
            return resp
        assert last is not None
        return last

    # ---------------------------------------------------------------- public
    def test_drive(self, drive_id: str) -> None:
        resp = self._request("GET", f"{GRAPH_BASE_URL}/drives/{drive_id}")
        if resp.status_code != 200:
            raise GraphRequestError(
                f"Drive probe failed: {resp.status_code} {resp.text[:300]}"
            )

    def list_root_children(self, drive_id: str, root_path: str) -> Iterator[dict[str, Any]]:
        """Yield child items under the given drive root or relative path."""
        cleaned = (root_path or "").strip().strip("/")
        if cleaned:
            url = (
                f"{GRAPH_BASE_URL}/drives/{drive_id}/root:/"
                f"{quote(cleaned)}:/children"
            )
        else:
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root/children"
        yield from self._paginate(url)

    def list_children_by_id(self, drive_id: str, item_id: str) -> Iterator[dict[str, Any]]:
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/children"
        yield from self._paginate(url)

    def _paginate(self, url: str) -> Iterator[dict[str, Any]]:
        next_url: Optional[str] = url
        while next_url:
            resp = self._request("GET", next_url)
            if resp.status_code != 200:
                raise GraphRequestError(
                    f"Graph list failed: {resp.status_code} {resp.text[:300]}"
                )
            data = resp.json()
            for item in data.get("value", []):
                yield item
            next_url = data.get("@odata.nextLink")

    def download_to(self, drive_id: str, item_id: str, dest_path) -> int:
        """Stream item content to ``dest_path``. Returns bytes written."""
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
        resp = self._request("GET", url, stream=True, timeout=self._request_timeout * 3)
        if resp.status_code != 200:
            raise GraphRequestError(
                f"Download failed: {resp.status_code} {resp.text[:300]}"
            )
        written = 0
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                written += len(chunk)
        return written

    def delta_pages(
        self, drive_id: str, delta_url: Optional[str] = None
    ) -> Iterator[tuple[list[dict], Optional[str]]]:
        """Iterate Graph ``/root/delta`` pages.

        Each yielded tuple is ``(items_in_page, delta_link_if_final)``. The
        second element is ``None`` for every page except the last; on the
        final page it carries the new ``@odata.deltaLink`` token to persist
        for the next sync.

        Raises:
            DeltaTokenInvalid: when Graph returns HTTP 410 for the supplied
                ``delta_url``. The caller should drop the saved token and
                call again with ``delta_url=None`` to restart enumeration.
            GraphRequestError: for any other non-200 Graph response.
        """
        url: Optional[str] = (
            delta_url
            if delta_url
            else f"{GRAPH_BASE_URL}/drives/{drive_id}/root/delta"
        )
        while url:
            resp = self._request("GET", url)
            if resp.status_code == 410:
                raise DeltaTokenInvalid(
                    f"delta token rejected by Graph: {resp.text[:300]}"
                )
            if resp.status_code != 200:
                raise GraphRequestError(
                    f"Graph delta failed: {resp.status_code} {resp.text[:300]}"
                )
            data = resp.json()
            items = data.get("value", []) or []
            next_url = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink") if not next_url else None
            yield items, delta_link
            url = next_url
