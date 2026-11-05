"""The Builder's line to the tenant's connection routes (connection layer MVP-27, MVP-28, KC-7b).

Over the Platform's own client (knovas_client.KnovasAPIClient) on its certificate, as the access window's
builder.access_backend does: the tenant is the Platform's, never one a request names, and every call carries the
signed-in person's principal assertion. Each call is made once (``_request_quiet``: no retry, no response body in a
log): accepting a proposal queues a write job, and a retried accept must not be a second decision.

  GET  /secured/source-writes?status=…               write proposals (MVP-8)
  POST /secured/source-writes/<id>/accept {actor}     the person accepting, as the declared actor
  POST /secured/source-writes/<id>/reject {actor, reason}
  GET  /secured/connector/jobs/stats?since=…          job counts for the «Verbindung» card (MVP-5)

A refusal of the Brain is raised as ConnectionApiError with its status and code; the page says the code in German
(builder.codes). Nothing here logs a proposal's content.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import quote, urlencode

#: How long the Builder waits for the tenant: the pages read it while they render.
TIMEOUT_S = 10


class ConnectionApiError(RuntimeError):
    def __init__(self, status: int, code: str = ""):
        super().__init__(code or f"HTTP {status}")
        self.status, self.code = int(status), str(code or "")


class BrainConnection:
    def __init__(self, client: Any):
        self.client = client

    def _call(self, method: str, endpoint: str, data: Optional[Mapping[str, Any]] = None) -> Any:
        answer = self.client._request_quiet(method, endpoint, dict(data) if data is not None else None, timeout=TIMEOUT_S)
        try:
            body = answer.json()
        except ValueError:
            body = None
        if answer.status_code >= 400:
            code = ""
            if isinstance(body, Mapping):
                code = str(body.get("code") or body.get("error") or body.get("error_code") or "")
            raise ConnectionApiError(answer.status_code, code)
        return body

    def source_writes(self, status: Optional[str] = None) -> list[dict]:
        """The tenant's write proposals, of one status or all."""
        endpoint = "/secured/source-writes" + (f"?{urlencode({'status': status})}" if status else "")
        body = self._call("GET", endpoint)
        rows = body.get("proposals") if isinstance(body, Mapping) else body
        return [dict(r) for r in rows or [] if isinstance(r, Mapping)]

    def accept_source_write(self, proposal_id: str, actor: Mapping[str, Any]) -> dict:
        body = self._call("POST", f"/secured/source-writes/{quote(str(proposal_id), safe='')}/accept", {"actor": dict(actor)})
        return dict(body) if isinstance(body, Mapping) else {}

    def reject_source_write(self, proposal_id: str, actor: Mapping[str, Any], reason: str) -> dict:
        body = self._call("POST", f"/secured/source-writes/{quote(str(proposal_id), safe='')}/reject",
                          {"actor": dict(actor), "reason": reason})
        return dict(body) if isinstance(body, Mapping) else {}

    def job_stats(self, since: str) -> dict:
        body = self._call("GET", f"/secured/connector/jobs/stats?{urlencode({'since': since})}")
        return dict(body) if isinstance(body, Mapping) else {}
