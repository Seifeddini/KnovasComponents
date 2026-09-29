"""Client for the experiments-runner sandbox (Python and Julia evaluators).

The runner has no network at all; the Platform reaches it over a unix socket
on a shared volume (``unix:///run/experiments-runner/runner.sock``). For
development it may listen on TCP (``http://host:port``). Both speak plain
HTTP/1.1, so http.client does the protocol and only the socket differs --
no dependency beyond the standard library.

Two failure classes are kept apart on purpose:

* The runner cannot be reached, or answers 503 (all slots busy, tmpfs full):
  ``Unavailable``. Nothing ran; the caller may try again later.
* The runner accepted the job and then took too long: an ``ok: False``
  result. The job did run (and was killed); retrying the same code would
  only burn another slot.

Messages are German and fixed; details stay in the log.
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import socket
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from experiments.errors import Unavailable

logger = logging.getLogger(__name__)

MSG_UNREACHABLE = "Die Rechenumgebung ist nicht erreichbar."
MSG_TIMEOUT = "Zeitlimit \u00fcberschritten."
MSG_BAD_ANSWER = "Die Rechenumgebung hat eine ung\u00fcltige Antwort geliefert."
MSG_REFUSED = "Die Rechenumgebung hat die Anfrage abgelehnt."
MSG_SERVER_ERROR = "Die Rechenumgebung hat mit einem Fehler geantwortet (HTTP {status})."
MSG_INPUT_INVALID = "Die Eingabedaten enthalten ung\u00fcltige Werte."
MSG_INPUT_TOO_LARGE = "Die Eingabedaten sind zu gross f\u00fcr die Rechenumgebung."
MSG_CODE_TOO_LARGE = "Der Code ist zu lang."
MSG_CONNECTION_LOST = "Die Rechenumgebung hat die Verbindung abgebrochen."

#: What the runner accepts (plan section 15).
MAX_CODE_CHARS = 200_000
MAX_REQUEST_BYTES = 64 * 1024 * 1024
#: output.json <= 8 MB plus logs <= 64 KB plus the envelope.
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
MAX_LOG_CHARS = 64 * 1024
MAX_ERROR_CHARS = 500
LANGUAGES = ("python", "julia")


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP over an AF_UNIX stream socket."""

    def __init__(self, path: str, *, connect_timeout: float, read_timeout: float) -> None:
        super().__init__("localhost", timeout=connect_timeout)
        self._socket_path = path
        self._read_timeout = read_timeout

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self._socket_path)
            sock.settimeout(self._read_timeout)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class _TCPHTTPConnection(http.client.HTTPConnection):
    """Plain TCP, with a short connect timeout and a long read timeout."""

    def __init__(self, host: str, port: int, *, connect_timeout: float, read_timeout: float) -> None:
        super().__init__(host, port, timeout=connect_timeout)
        self._read_timeout = read_timeout

    def connect(self) -> None:
        super().connect()
        self.sock.settimeout(self._read_timeout)


class _NotSent(Exception):
    """The request did not reach the runner (connect or send failed)."""


def _clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit]


def _finite_or_none(value: Any) -> Any:
    """Replace NaN/Infinity anywhere in a JSON value with None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite_or_none(v) for v in value]
    return value


def _reject_constant(name: str) -> Any:
    # json.loads accepts NaN/Infinity by default; the contract never does.
    return None


class RunnerClient:
    """One per process. Thread-safe: every call opens its own connection."""

    #: Connecting must be quick: the runner is local or not there at all.
    connect_timeout_seconds = 5.0
    #: How long to wait for an answer beyond the job's own time limit (the
    #: runner kills the job at the limit, then collects and answers).
    read_grace_seconds = 30.0
    health_timeout_seconds = 5.0
    health_cache_seconds = 30.0

    def __init__(self, url: str, *, timeout_seconds: int = 90) -> None:
        self.url = str(url or "").strip()
        self.timeout_seconds = int(timeout_seconds) if timeout_seconds else 90
        self._socket_path: Optional[str] = None
        self._host: Optional[str] = None
        self._port: Optional[int] = None
        self._valid = self._parse(self.url)
        if not self._valid:
            # A typo in an optional feature must not stop the Platform from
            # starting; the runner simply reads as unreachable.
            logger.error(
                "EXPERIMENTS_RUNNER_URL is not usable (expected unix:///path/runner.sock "
                "or http://host:port); Python and Julia evaluators stay unavailable."
            )
        self._health_lock = threading.Lock()
        self._health_cache: Optional[Tuple[float, Dict[str, Any]]] = None

    def _parse(self, url: str) -> bool:
        if url.startswith("unix://"):
            path = url[len("unix://"):]
            if not path.startswith("/") or "\x00" in path:
                return False
            self._socket_path = path
            return True
        try:
            parts = urlsplit(url)
            if parts.scheme != "http" or not parts.hostname:
                return False
            port = parts.port or 80
        except ValueError:
            return False
        self._host, self._port = parts.hostname, int(port)
        return True

    def _connection(self, read_timeout: float) -> http.client.HTTPConnection:
        if self._socket_path is not None:
            return _UnixHTTPConnection(self._socket_path, connect_timeout=self.connect_timeout_seconds,
                                       read_timeout=read_timeout)
        return _TCPHTTPConnection(self._host or "localhost", self._port or 80,
                                  connect_timeout=self.connect_timeout_seconds,
                                  read_timeout=read_timeout)

    def _exchange(self, method: str, path: str, body: Optional[bytes], read_timeout: float,
                  max_bytes: int) -> Tuple[int, bytes, bool]:
        """One request. Returns (status, body, truncated). Raises _NotSent when
        the request never reached the runner; other OSErrors mean it did."""
        conn = self._connection(read_timeout)
        try:
            headers = {"Accept": "application/json", "Connection": "close"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            try:
                conn.request(method, path, body=body, headers=headers)
            except (OSError, http.client.HTTPException) as exc:
                raise _NotSent(str(exc)) from exc
            response = conn.getresponse()
            data = response.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            return response.status, data[:max_bytes], truncated
        finally:
            conn.close()

    # -- health ------------------------------------------------------------

    def invalidate_health(self) -> None:
        with self._health_lock:
            self._health_cache = None

    def health(self) -> Dict[str, Any]:
        """{"configured": True, "ok", "languages", "busy"}; cached; never raises."""
        now = time.monotonic()
        with self._health_lock:
            cached = self._health_cache
            if cached is not None and now - cached[0] < self.health_cache_seconds:
                return dict(cached[1])
        result = self._probe()
        with self._health_lock:
            self._health_cache = (time.monotonic(), result)
        return dict(result)

    def _probe(self) -> Dict[str, Any]:
        down = {"configured": True, "ok": False, "languages": {}, "busy": 0}
        if not self._valid:
            return down
        try:
            status, data, truncated = self._exchange("GET", "/health", None,
                                                     self.health_timeout_seconds, 64 * 1024)
        except Exception as exc:  # noqa: BLE001 - health never raises
            logger.info("Experiments runner health check failed: %s", exc)
            return down
        if status != 200 or truncated:
            logger.info("Experiments runner health check: HTTP %s", status)
            return down
        try:
            payload = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError):
            return down
        if not isinstance(payload, dict):
            return down
        languages: Dict[str, str] = {}
        raw_languages = payload.get("languages")
        if isinstance(raw_languages, dict):
            for key, value in list(raw_languages.items())[:10]:
                languages[_clip(key, 40)] = _clip(value, 200)
        busy = payload.get("busy")
        busy_n = int(busy) if isinstance(busy, int) and not isinstance(busy, bool) and busy >= 0 else 0
        return {"configured": True, "ok": payload.get("ok") is True, "languages": languages,
                "busy": busy_n}

    # -- run ---------------------------------------------------------------

    def run(self, *, language: str, code: str, data: dict, timeout_seconds: int) -> Dict[str, Any]:
        """Run ``evaluate(data)`` in the sandbox.

        -> {"ok", "output", "error", "logs", "duration_ms"}. Raises
        Unavailable when the runner cannot take the job.
        """
        if not self._valid:
            raise Unavailable(MSG_UNREACHABLE)
        limit = int(max(1, min(3600, int(timeout_seconds or self.timeout_seconds))))
        if language not in LANGUAGES:
            return self._failed(MSG_REFUSED)
        code_text = "" if code is None else str(code)
        if len(code_text) > MAX_CODE_CHARS:
            return self._failed(MSG_CODE_TOO_LARGE)
        try:
            body = json.dumps(
                {"language": language, "code": code_text, "data": data,
                 "timeout_seconds": limit},
                allow_nan=False, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return self._failed(MSG_INPUT_INVALID)
        if len(body) > MAX_REQUEST_BYTES:
            return self._failed(MSG_INPUT_TOO_LARGE)

        started = time.monotonic()
        try:
            status, raw, truncated = self._exchange(
                "POST", "/v1/run", body, float(limit) + self.read_grace_seconds, MAX_RESPONSE_BYTES
            )
        except _NotSent as exc:
            logger.warning("Experiments runner not reachable: %s", exc)
            self.invalidate_health()
            raise Unavailable(MSG_UNREACHABLE) from None
        except (socket.timeout, TimeoutError):
            logger.warning("Experiments runner did not answer within %s s (+%s s).",
                           limit, self.read_grace_seconds)
            return self._failed(MSG_TIMEOUT, started=started)
        except (OSError, http.client.HTTPException) as exc:
            # Accepted, then the connection died: the runner restarted mid-job.
            logger.warning("Experiments runner dropped the connection: %s", exc)
            self.invalidate_health()
            raise Unavailable(MSG_UNREACHABLE) from None

        if status == 503:
            self.invalidate_health()
            raise Unavailable(MSG_UNREACHABLE)
        if status == 400 or status == 413:
            logger.warning("Experiments runner refused the job (HTTP %s): %s",
                           status, _clip(raw.decode("utf-8", "replace"), 500))
            return self._failed(MSG_REFUSED, started=started)
        if status != 200:
            logger.warning("Experiments runner answered HTTP %s: %s",
                           status, _clip(raw.decode("utf-8", "replace"), 500))
            return self._failed(MSG_SERVER_ERROR.format(status=int(status)), started=started)
        if truncated:
            logger.warning("Experiments runner answer exceeds %s bytes.", MAX_RESPONSE_BYTES)
            return self._failed(MSG_BAD_ANSWER, started=started)
        return self._result(raw, started)

    def _result(self, raw: bytes, started: float) -> Dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, UnicodeDecodeError):
            logger.warning("Experiments runner answer is not JSON.")
            return self._failed(MSG_BAD_ANSWER, started=started)
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            logger.warning("Experiments runner answer lacks the result envelope.")
            return self._failed(MSG_BAD_ANSWER, started=started)
        ok = payload["ok"]
        output = payload.get("output")
        error = payload.get("error")
        logs = payload.get("logs")
        duration = payload.get("duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) \
                or not math.isfinite(float(duration)) or duration < 0:
            duration = (time.monotonic() - started) * 1000.0
        return {
            "ok": ok,
            "output": _finite_or_none(output) if output is not None else None,
            "error": None if ok and not error else _clip(error or MSG_BAD_ANSWER, MAX_ERROR_CHARS),
            "logs": _clip(logs if isinstance(logs, str) else "", MAX_LOG_CHARS),
            "duration_ms": int(min(float(duration), 2 ** 31 - 1)),
        }

    @staticmethod
    def _failed(message: str, *, started: Optional[float] = None, logs: str = "") -> Dict[str, Any]:
        elapsed = 0 if started is None else int((time.monotonic() - started) * 1000)
        return {"ok": False, "output": None, "error": message, "logs": logs,
                "duration_ms": elapsed}
