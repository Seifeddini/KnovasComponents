"""Link's line to secure-api, with the site's certificate: the work channel, probes and the relayed Brain calls.

Nothing here retries: a relayed call is made once, and the work channel's loop decides when to ask again.
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterable, Optional

import httpx

WAIT_SECONDS = 25
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class BrainUnreachable(RuntimeError):
    """secure-api did not answer; the text is the exception's type."""


class BrainBusy(RuntimeError):
    """429: secure-api's waiter caps are reached; ask again after ``retry_after`` seconds."""

    def __init__(self, retry_after: int):
        super().__init__("busy")
        self.retry_after = retry_after


class BrainRefused(RuntimeError):
    def __init__(self, status: int, code: Optional[str]):
        super().__init__(f"{status} {code}")
        self.status, self.code = status, code


def error_code(answer: httpx.Response) -> Optional[str]:
    """secure-api's error code (``error_code``), or None."""
    try:
        data = answer.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        code = data.get("error_code")
        return str(code) if code else None
    return None


def _retry_after(answer: httpx.Response) -> int:
    raw = answer.headers.get("Retry-After", "")
    return int(raw) if raw.isdigit() and 0 < int(raw) <= 600 else 5


class BrainClient:
    def __init__(self, base_url: str, *, ssl_context: Any = None, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.base = base_url.rstrip("/")
        kwargs: dict = {"timeout": httpx.Timeout(30.0)}
        if transport is not None:
            kwargs["transport"] = transport
        elif ssl_context is not None:
            kwargs["verify"] = ssl_context
        self.http = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _job(job_id: str) -> str:
        if not _UUID.match(job_id):
            raise ValueError("job id")
        return job_id

    async def claim(self, sources: list[str], wait: int = WAIT_SECONDS) -> Optional[dict]:
        """One job of the tenant for these sources, or a probe addressed to this certificate; None after ``wait``."""
        params = {"wait": str(wait)}
        if sources:
            params["source"] = ",".join(sources)
        try:
            answer = await self.http.get(self.base + "/secured/connector/jobs", params=params,
                                         timeout=httpx.Timeout(10.0, read=wait + 15.0))
        except httpx.HTTPError as exc:
            raise BrainUnreachable(type(exc).__name__) from None
        if answer.status_code == 204:
            return None
        if answer.status_code == 429:
            raise BrainBusy(_retry_after(answer))
        if answer.status_code == 200:
            try:
                job = answer.json().get("job")
            except (ValueError, AttributeError):
                job = None
            if isinstance(job, dict):
                return job
            raise BrainRefused(200, "no_job")
        raise BrainRefused(answer.status_code, error_code(answer))

    async def post_part(self, job_id: str, n: int, content: AsyncIterable[bytes],
                        length: Optional[int]) -> tuple[int, Optional[str]]:
        """One part, streamed through as it arrives; (status, error code)."""
        headers = {"Content-Type": "application/octet-stream"}
        if length is not None:
            headers["Content-Length"] = str(length)
        try:
            answer = await self.http.post(f"{self.base}/secured/connector/jobs/{self._job(job_id)}/part",
                                          params={"n": str(n)}, content=content, headers=headers,
                                          timeout=httpx.Timeout(30.0, read=60.0, write=60.0))
        except httpx.HTTPError as exc:
            raise BrainUnreachable(type(exc).__name__) from None
        return answer.status_code, error_code(answer)

    async def post_result(self, job_id: str, raw: bytes) -> tuple[int, Optional[str]]:
        try:
            answer = await self.http.post(f"{self.base}/secured/connector/jobs/{self._job(job_id)}/result",
                                          content=raw, headers={"Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            raise BrainUnreachable(type(exc).__name__) from None
        return answer.status_code, error_code(answer)

    async def probe(self) -> str:
        """Queue a probe addressed to this certificate; its job id."""
        try:
            answer = await self.http.post(self.base + "/secured/connector/probe", json={})
        except httpx.HTTPError as exc:
            raise BrainUnreachable(type(exc).__name__) from None
        if answer.status_code == 429:
            raise BrainBusy(_retry_after(answer))
        try:
            job_id = answer.json().get("job_id") if answer.status_code in (200, 201) else None
        except (ValueError, AttributeError):
            job_id = None
        if not isinstance(job_id, str) or not _UUID.match(job_id):
            raise BrainRefused(answer.status_code, error_code(answer))
        return job_id

    async def relay(self, method: str, path: str, query: bytes, headers: dict,
                    content: Optional[AsyncIterable[bytes]]) -> httpx.Response:
        """One relayed call, answered as a stream; the caller closes it. Never retried."""
        url = httpx.URL(self.base + "/" + path, query=query or None)
        request = self.http.build_request(method, url, headers=headers, content=content,
                                          timeout=httpx.Timeout(30.0, read=300.0, write=300.0))
        try:
            return await self.http.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise BrainUnreachable(type(exc).__name__) from None
