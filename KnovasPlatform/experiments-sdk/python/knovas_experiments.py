"""Knovas Experiments: Python client for the machine API of the Experiments module.

CI jobs, benchmarks and scripts report runs, measurements and notes to the
Knovas Platform (module "Experimente") and read the verdicts back::

    from knovas_experiments import Client

    client = Client("https://knovas.example.ch", token="kxp_...")
    with client.run("ENG-12", variant="candidate", commit=sha) as run:
        for query_id, score in scores.items():
            run.add_row("ndcg_at_10", score, dims={"query": query_id})
        run.log(latency_p95_ms=212.0)
    for evaluation in client.evaluate("ENG-12"):
        print(evaluation["headline"], evaluation["verdict"])

Standard library only (Python 3.8+) and one file, so CI can vendor it next to
the script that uses it. The API is ``/api/experiments/v1`` of the
Platform, authenticated with a personal access token (``kxp_...``, created
under Experimente -> Verwaltung -> Zugangsschluessel). The token acts as its
owner with the owner's roles at request time.

Security choices, each on purpose:

* Redirects are never followed. A redirect would carry the token to wherever
  the Location header points; instead the call fails and names the target.
* The token is sent only in the Authorization header, never logged, and
  appears in no exception message or repr.
* TLS is verified by default (``cafile`` for a private CA). Plain http to a
  host other than this machine works but warns: the token would travel in
  clear text.
* Only GET requests are retried after a transient failure. A POST that may
  have reached the server is never repeated, because the Platform has no
  idempotency key and a repeated run would count its measurements twice.

Error messages are German, like every message the Platform returns.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (sections 10, 16)
"""

from __future__ import annotations

import datetime as _dt
import decimal
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

__version__ = "1.0.0"

__all__ = [
    "Client",
    "Run",
    "ExperimentsError",
    "ConfigurationError",
    "TransportError",
    "ProtocolError",
    "ValidationError",
    "AuthenticationError",
    "ForbiddenError",
    "NotFoundError",
    "ModuleDisabledError",
    "ConflictError",
    "UnavailableError",
    "ServerError",
    "WaitTimeout",
]

API_PREFIX = "/api/experiments/v1"
ENV_URL = "KNOVAS_URL"
ENV_TOKEN = "KNOVAS_EXPERIMENTS_TOKEN"

#: Tokens are "kxp_" + secrets.token_urlsafe(32) (43 characters).
TOKEN_PREFIX = "kxp_"
_TOKEN_RE = re.compile(r"kxp_[A-Za-z0-9_-]{16,200}")
#: Experiment keys: <id_prefix>-<n>, e.g. ENG-12.
_KEY_RE = re.compile(r"[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}")
#: Statuses a reported run may have (a run is reported once it is over).
RUN_STATUSES = ("finished", "failed", "cancelled")
#: Evaluation statuses that are not final yet.
PENDING_EVALUATION_STATUSES = ("queued", "running")

#: The Platform's answer on every module path while EXPERIMENTS_ENABLED is off.
MSG_SWITCHED_OFF = "Experimente sind nicht eingeschaltet."

#: Largest response body read. The biggest regular answer (20 evaluations
#: with their outputs) stays far below; anything larger is not the Platform.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
#: Pauses before the second and third attempt of a GET.
RETRY_BACKOFF_SECONDS = (1.0, 3.0)
#: HTTP statuses worth another GET attempt (a proxy or a restart in between).
_RETRY_STATUSES = frozenset({502, 503, 504})
#: Largest evaluations list the machine API returns in one answer.
_MAX_EVALUATIONS_LIMIT = 60

_USER_AGENT = f"knovas-experiments-python/{__version__}"


# -- errors ----------------------------------------------------------------------


class ExperimentsError(Exception):
    """Base class of every error this client raises.

    ``status`` is the HTTP status of the Platform's answer (None when there
    was none), ``message`` the German text, ``fields`` the per-field messages
    of a refused input (``{"rows.3.value": "..."}``).
    """

    def __init__(self, message: str, *, status: Optional[int] = None,
                 fields: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.fields = {str(k): str(v) for k, v in dict(fields or {}).items()}

    def __str__(self) -> str:
        text = self.message
        if self.status is not None:
            text = f"{text} (HTTP {self.status})"
        if self.fields:
            details = "; ".join(f"{k}: {v}" for k, v in sorted(self.fields.items()))
            text = f"{text} [{details}]"
        return text


class ConfigurationError(ExperimentsError):
    """The client cannot be used as configured (address, token, TLS)."""


class TransportError(ExperimentsError):
    """No answer from the Platform: connection, DNS, TLS or timeout."""


class ProtocolError(ExperimentsError):
    """An answer that is not the Platform's JSON (a redirect, an HTML page)."""


class ValidationError(ExperimentsError):
    """The input was refused, by the Platform (400, 413) or before sending."""


class AuthenticationError(ExperimentsError):
    """401: token unknown, revoked or expired, or its account may not sign in."""


class ForbiddenError(ExperimentsError):
    """403: the token's owner lacks the role for this action."""


class NotFoundError(ExperimentsError):
    """404: the experiment (or whatever the path names) does not exist."""


class ModuleDisabledError(NotFoundError):
    """404 because the Experiments module is switched off on this Platform."""


class ConflictError(ExperimentsError):
    """409: the data changed meanwhile, or a key is taken."""


class UnavailableError(ExperimentsError):
    """503: a dependency of the Platform (Knovas, the runner) is not reachable."""


class ServerError(ExperimentsError):
    """Any other error status, typically 500."""


class WaitTimeout(ExperimentsError):
    """``wait_for`` gave up; ``evaluations`` holds the last state seen."""

    def __init__(self, message: str, evaluations: List[Dict[str, Any]]) -> None:
        super().__init__(message)
        self.evaluations = evaluations


_STATUS_ERRORS = {
    400: ValidationError,
    401: AuthenticationError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    413: ValidationError,
    503: UnavailableError,
}

_STATUS_FALLBACK_MESSAGES = {
    400: "Die Anfrage wurde abgelehnt.",
    401: "Ung\u00fcltiger oder abgelaufener Zugangsschl\u00fcssel.",
    403: "Daf\u00fcr fehlt die Berechtigung.",
    404: "Nicht gefunden.",
    409: "Die Daten wurden inzwischen ge\u00e4ndert.",
    413: ("Die Anfrage ist zu gross. Bitte die Zeilen auf mehrere Aufrufe "
          "verteilen."),
    503: "Der Dienst ist vor\u00fcbergehend nicht erreichbar.",
}


def _error_class(status: int) -> type:
    if status in _STATUS_ERRORS:
        return _STATUS_ERRORS[status]
    return ServerError if status >= 500 else ExperimentsError


# -- JSON encoding -----------------------------------------------------------------


def _json_default(obj: Any) -> Any:
    """Encode the values CI code tends to hand over besides plain JSON types.

    numpy scalars and arrays, datetimes, dates, Decimals, UUIDs and sets. A
    naive datetime is read as local time (what ``datetime.now()`` returns)
    and sent with its offset, because the Platform needs a timezone.
    """
    if isinstance(obj, _dt.datetime):
        if obj.tzinfo is None or obj.tzinfo.utcoffset(obj) is None:
            obj = obj.astimezone()
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=repr)
    item = getattr(obj, "item", None)
    if callable(item) and getattr(obj, "shape", None) == ():
        return item()
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        return tolist()
    raise TypeError(f"{type(obj).__name__} ist nicht als JSON darstellbar")


def _non_finite_path(obj: Any, path: str = "") -> Optional[str]:
    """Where the first NaN or infinity sits, as ``rows.3.value``."""
    if isinstance(obj, float) or isinstance(obj, decimal.Decimal):
        try:
            return None if math.isfinite(float(obj)) else (path or "Wert")
        except (OverflowError, ValueError):
            return path or "Wert"
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            found = _non_finite_path(value, f"{path}.{key}" if path else str(key))
            if found:
                return found
        return None
    if isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            found = _non_finite_path(value, f"{path}.{index}" if path else str(index))
            if found:
                return found
        return None
    item = getattr(obj, "item", None)
    if callable(item) and getattr(obj, "shape", None) == ():
        return _non_finite_path(item(), path)
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        return _non_finite_path(tolist(), path)
    return None


def _encode(body: Any) -> bytes:
    """JSON bytes; NaN and infinity are refused here, before anything is sent."""
    try:
        text = json.dumps(body, allow_nan=False, default=_json_default,
                          ensure_ascii=False, separators=(",", ":"))
    except ValueError:
        where = _non_finite_path(body) or "Wert"
        raise ValidationError(
            f"{where}: Der Wert muss eine endliche Zahl sein (NaN oder unendlich).",
            fields={where: "Der Wert muss eine endliche Zahl sein."},
        ) from None
    except TypeError as exc:
        raise ValidationError(f"Nicht als JSON darstellbar: {exc}") from None
    return text.encode("utf-8")


# -- small helpers -------------------------------------------------------------------


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _normalise_key(key: Any) -> str:
    """An experiment key, checked before it becomes part of a URL path."""
    text = str(key or "").strip().upper()
    if not _KEY_RE.fullmatch(text):
        raise ValidationError(
            f"{key!r} ist kein Experiment-Schl\u00fcssel (erwartet z. B. ENG-12)."
        )
    return text


def _mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{name} muss ein Objekt (dict) sein.")
    return dict(value)


def _rows(rows: Any, name: str = "rows") -> List[Dict[str, Any]]:
    """A list of row dicts from any iterable of mappings."""
    if isinstance(rows, (str, bytes)) or isinstance(rows, Mapping):
        raise ValidationError(f"{name} muss eine Liste von Zeilen (dicts) sein.")
    try:
        items = list(rows)
    except TypeError:
        raise ValidationError(f"{name} muss eine Liste von Zeilen (dicts) sein.") from None
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(items):
        if not isinstance(row, Mapping):
            raise ValidationError(f"{name}.{index} ist keine Zeile (dict).")
        out.append(dict(row))
    return out


def _is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _reason_text(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException):
        exc = reason
    elif reason:
        return str(reason)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "Zeit\u00fcberschreitung"
    return f"{type(exc).__name__}: {exc}"


def _never_sent(exc: BaseException) -> bool:
    """True when the request certainly did not reach any server.

    Only then may a POST be tried again: refused connections and unresolvable
    names. A timeout or a reset may come after the server read the body.
    """
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into an error answer instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ARG002
        return None


class _Response:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int, headers: Any, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body


def _read_limited(stream: Any) -> bytes:
    data = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ProtocolError("Die Antwort ist gr\u00f6sser als erwartet; abgebrochen.")
    return data


# -- client --------------------------------------------------------------------------


class Client:
    """Talks to ``/api/experiments/v1`` of one Knovas Platform.

    ``base_url`` is the Platform's address as people open it in the browser
    (``https://knovas.example.ch``, also with a path prefix behind a reverse
    proxy); ``token`` a personal access token. Both fall back to the
    environment variables KNOVAS_URL and KNOVAS_EXPERIMENTS_TOKEN.

    ``cafile`` is the CA bundle to trust instead of the system store (for a
    Platform behind a private CA). ``verify=False`` switches TLS verification
    off; only for a test system. ``timeout`` is seconds per
    request. ``retries`` is how often a GET is repeated after a transient
    failure (POSTs are repeated only when the connection was refused).
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None, *,
                 cafile: Optional[str] = None, verify: bool = True, timeout: float = 30,
                 retries: int = 2) -> None:
        raw_url = base_url if base_url is not None else os.environ.get(ENV_URL, "")
        raw_token = token if token is not None else os.environ.get(ENV_TOKEN, "")
        self.base_url = self._check_url(raw_url)
        self._token = self._check_token(raw_token)
        try:
            self.timeout = float(timeout)
        except (TypeError, ValueError):
            raise ConfigurationError("timeout muss eine Zahl (Sekunden) sein.") from None
        if not self.timeout > 0 or not math.isfinite(self.timeout):
            raise ConfigurationError("timeout muss eine positive Zahl (Sekunden) sein.")
        try:
            self.retries = max(0, min(5, int(retries)))
        except (TypeError, ValueError):
            raise ConfigurationError("retries muss eine ganze Zahl sein.") from None
        self._api = self.base_url + API_PREFIX
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme == "http" and not _is_loopback(parsed.hostname or ""):
            warnings.warn(
                f"{self.base_url} ist unverschl\u00fcsselt (http): der Zugangsschl\u00fcssel "
                "geht im Klartext \u00fcber das Netz. Bitte https verwenden.",
                stacklevel=2,
            )
        self._opener = self._build_opener(parsed.scheme, cafile=cafile, verify=verify)

    # -- configuration --

    @staticmethod
    def _check_url(raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            raise ConfigurationError(
                f"Keine Adresse der Knovas Platform: base_url angeben oder {ENV_URL} setzen."
            )
        if any(ord(ch) < 33 or ord(ch) == 127 for ch in text):
            raise ConfigurationError("Die Adresse enth\u00e4lt Leer- oder Steuerzeichen.")
        parsed = urllib.parse.urlsplit(text)
        try:
            parsed.port
        except ValueError:
            raise ConfigurationError(f"{text!r} hat keinen g\u00fcltigen Port.") from None
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigurationError(
                f"{text!r} ist keine http(s)-Adresse (erwartet z. B. https://knovas.example.ch)."
            )
        if parsed.username or parsed.password:
            raise ConfigurationError(
                "Die Adresse darf keine Zugangsdaten enthalten; der Zugangsschl\u00fcssel "
                "geh\u00f6rt in token."
            )
        if parsed.query or parsed.fragment:
            raise ConfigurationError("Die Adresse darf weder ? noch # enthalten.")
        path = parsed.path.rstrip("/")
        if path.endswith(API_PREFIX):
            path = path[: -len(API_PREFIX)]
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    @staticmethod
    def _check_token(raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            raise ConfigurationError(
                f"Kein Zugangsschl\u00fcssel: token angeben oder {ENV_TOKEN} setzen "
                "(Experimente -> Verwaltung -> Zugangsschl\u00fcssel)."
            )
        if not _TOKEN_RE.fullmatch(text):
            # The value itself is never repeated: it may be some other secret.
            raise ConfigurationError(
                "Das ist kein Zugangsschl\u00fcssel f\u00fcr Experimente "
                f"(sie beginnen mit {TOKEN_PREFIX} und enthalten nur Buchstaben, "
                "Ziffern, - und _)."
            )
        return text

    @staticmethod
    def _build_opener(scheme: str, *, cafile: Optional[str], verify: bool):
        handlers: List[Any] = [_NoRedirect()]
        if scheme == "https":
            if not verify:
                if cafile:
                    raise ConfigurationError("cafile und verify=False schliessen sich aus.")
                warnings.warn(
                    "TLS-Pr\u00fcfung ausgeschaltet (verify=False): nur f\u00fcr Testsysteme.",
                    stacklevel=3,
                )
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            else:
                try:
                    context = ssl.create_default_context(cafile=cafile)
                except (OSError, ssl.SSLError) as exc:
                    raise ConfigurationError(
                        f"cafile {cafile!r} ist nicht lesbar: {exc}"
                    ) from None
            handlers.append(urllib.request.HTTPSHandler(context=context))
        return urllib.request.build_opener(*handlers)

    def __repr__(self) -> str:
        return f"Client(base_url={self.base_url!r}, token='{TOKEN_PREFIX}\u2026')"

    # -- transport --

    def _send(self, method: str, url: str, data: Optional[bytes]) -> _Response:
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        # Unredirected: even a redirect handler that follows would not copy it.
        request.add_unredirected_header("Authorization", f"Bearer {self._token}")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return _Response(response.status, response.headers, _read_limited(response))
        except urllib.error.HTTPError as exc:
            try:
                body = _read_limited(exc)
            except Exception:  # noqa: BLE001 - an unreadable error body is just empty
                body = b""
            finally:
                exc.close()
            return _Response(exc.code, exc.headers, body)

    def _request(self, method: str, path: str, *, body: Any = None,
                 query: Optional[Mapping[str, Any]] = None, key: str) -> Any:
        url = self._api + path
        if query:
            pairs = [(k, str(v)) for k, v in query.items() if v is not None]
            if pairs:
                url += "?" + urllib.parse.urlencode(pairs)
        data = _encode(body) if body is not None else None
        attempts = 1 + self.retries
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                response = self._send(method, url, data)
            except ExperimentsError:
                raise
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                if not last and (method == "GET" or _never_sent(exc)):
                    self._pause(attempt)
                    continue
                raise self._transport_error(exc) from None
            if method == "GET" and response.status in _RETRY_STATUSES and not last:
                self._pause(attempt)
                continue
            return self._interpret(response, key)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _pause(attempt: int) -> None:
        delays = RETRY_BACKOFF_SECONDS or (0.0,)
        time.sleep(delays[min(attempt, len(delays) - 1)])

    def _transport_error(self, exc: BaseException) -> TransportError:
        reason = _reason_text(exc)
        hint = ""
        underlying = getattr(exc, "reason", exc)
        if isinstance(underlying, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in reason:
            hint = (" Das Zertifikat der Platform ist nicht vertrauensw\u00fcrdig: "
                    "cafile=\u2026 mit der Zertifizierungsstelle angeben.")
        return TransportError(f"Keine Antwort von {self.base_url}: {reason}.{hint}")

    def _interpret(self, response: _Response, key: str) -> Any:
        status = response.status
        if 300 <= status < 400:
            location = response.headers.get("Location", "") if response.headers else ""
            raise ProtocolError(
                f"Die Platform leitet auf {location or 'eine andere Adresse'} um; der Client "
                "folgt Umleitungen nicht, damit der Zugangsschl\u00fcssel bei der Platform "
                "bleibt. Stimmt die Adresse (https statt http, Pfad)?",
                status=status,
            )
        payload: Any = None
        if response.body:
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                payload = None
        if not isinstance(payload, dict):
            if status >= 400:
                message = _STATUS_FALLBACK_MESSAGES.get(status)
                if message is None:
                    message = ("Die Platform hat mit einem Fehler geantwortet und kein JSON "
                               "geliefert.")
                if status == 404:
                    message = ("Nicht gefunden. Stimmt die Adresse, und hat diese Platform "
                               "das Modul Experimente?")
                raise _error_class(status)(message, status=status)
            raise ProtocolError(
                "Die Antwort ist kein JSON der Knovas Platform. Stimmt die Adresse?",
                status=status,
            )
        if status >= 400 or payload.get("success") is False:
            self._raise_for(status, payload)
        if payload.get("success") is not True or key not in payload:
            raise ProtocolError(
                f"Unerwartete Antwort der Platform (ohne \u00ab{key}\u00bb).", status=status
            )
        return payload[key]

    @staticmethod
    def _raise_for(status: int, payload: Mapping[str, Any]) -> None:
        message = str(payload.get("error") or "").strip()
        fields = payload.get("fields") if isinstance(payload.get("fields"), Mapping) else None
        if status < 400:
            raise ProtocolError(message or "Die Platform meldet einen Fehler.",
                                status=status, fields=fields)
        if status == 404 and message == MSG_SWITCHED_OFF:
            raise ModuleDisabledError(
                "Experimente sind auf dieser Knovas Platform nicht eingeschaltet "
                "(EXPERIMENTS_ENABLED). Bitte die Betreiber fragen oder die Adresse pr\u00fcfen.",
                status=status,
            )
        cls = _error_class(status)
        raise cls(message or _STATUS_FALLBACK_MESSAGES.get(status, "Fehler der Platform."),
                  status=status, fields=fields)

    # -- API -----------------------------------------------------------------

    def ping(self) -> Dict[str, Any]:
        """Who the token acts as: ``{"display_name", "roles"}``.

        A cheap first step in CI: it fails with a clear error when the
        address, the token or the module switch is wrong.
        """
        return self._request("GET", "/ping", key="user")

    def experiment(self, key: str) -> Dict[str, Any]:
        """Key, title, status, domain, type, variants, metrics, row_version."""
        key = _normalise_key(key)
        return self._request("GET", f"/experiments/{urllib.parse.quote(key)}",
                             key="experiment")

    def create_experiment(self, domain: str, type: str, title: str,  # noqa: A002 - API name
                          hypothesis: str = "",
                          fields: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Create an experiment of ``type`` (key or id) in ``domain`` (key).

        Variants and metrics come from the type's defaults. Returns the new
        experiment; its ``key`` (e.g. ENG-13) is what every other call takes.
        """
        body: Dict[str, Any] = {"domain": str(domain), "type": str(type),
                                "title": str(title), "hypothesis": str(hypothesis or "")}
        if fields is not None:
            body["fields"] = _mapping(fields, "fields")
        return self._request("POST", "/experiments", body=body, key="experiment")

    def log_run(self, key: str, *, name: Optional[str] = None, variant: Optional[str] = None,
                params: Optional[Mapping[str, Any]] = None,
                metrics: Optional[Mapping[str, Any]] = None,
                rows: Optional[Iterable[Mapping[str, Any]]] = None,
                environment: Optional[Mapping[str, Any]] = None,
                commit: Optional[str] = None, status: str = "finished",
                started_at: Any = None, ended_at: Any = None,
                note: Optional[str] = None) -> Dict[str, Any]:
        """Report one run with its measurements, in one transaction.

        ``metrics`` maps a metric key to a number (mean, duration, currency)
        or to ``{"value", "count", "denominator", "sum_sq"}`` for the other
        kinds. ``rows`` are measurement rows (``{"metric", "value", "count",
        "dims": {"query": "q17"}, ...}``); their variant defaults to the
        run's. Returns the run with ``metrics`` as estimates.
        """
        key = _normalise_key(key)
        if status not in RUN_STATUSES:
            raise ValidationError(
                f"status muss einer von {', '.join(RUN_STATUSES)} sein, nicht {status!r}."
            )
        body: Dict[str, Any] = {"status": status}
        if name is not None:
            body["name"] = str(name)
        if variant is not None:
            body["variant"] = str(variant)
        if params is not None:
            body["params"] = _mapping(params, "params")
        if environment is not None:
            body["environment"] = _mapping(environment, "environment")
        if commit is not None:
            body["commit"] = str(commit)
        if started_at is not None:
            body["started_at"] = started_at
        if ended_at is not None:
            body["ended_at"] = ended_at
        if metrics is not None:
            metrics = _mapping(metrics, "metrics")
            if metrics:
                body["metrics"] = metrics
        if rows is not None:
            rows = _rows(rows)
            if rows:
                body["rows"] = rows
        if note is not None and str(note).strip():
            body["note"] = str(note)
        return self._request("POST", f"/experiments/{urllib.parse.quote(key)}/runs",
                             body=body, key="run")

    def add_measurements(self, key: str, rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
        """Add measurement rows without a run: ``{"batch_id", "inserted"}``.

        All or nothing: one refused row refuses the whole call. The Platform
        takes at most 10'000 rows per call (EXPERIMENTS setting); split larger
        sets. A row: ``{"metric", "variant", "value", "count", "denominator",
        "sum_sq", "observed_at", "dims", "run_id"}``, only metric and value
        required.
        """
        key = _normalise_key(key)
        rows = _rows(rows)
        if not rows:
            raise ValidationError("rows ist leer: keine Messwerte zum Senden.")
        return self._request("POST", f"/experiments/{urllib.parse.quote(key)}/measurements",
                             body={"rows": rows}, key="result")

    def add_note(self, key: str, body: str, kind: str = "note") -> Dict[str, Any]:
        """Add a note (kind note, observation, interview or feedback).

        Notes are indexed into Knovas with the experiment: write no names of
        interviewees or customers into them.
        """
        key = _normalise_key(key)
        text = str(body or "")
        if not text.strip():
            raise ValidationError("Die Notiz ist leer.")
        return self._request("POST", f"/experiments/{urllib.parse.quote(key)}/notes",
                             body={"body": text, "kind": str(kind)}, key="note")

    def evaluate(self, key: str, scope: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        """Run the evaluations the experiment's type defines, now.

        Built-in evaluators are computed during the call (status ``done``);
        Python and Julia evaluators are queued for the runner (``queued``;
        see ``wait_for``). An unchanged input returns the existing evaluation
        instead of a new one. ``scope`` overrides the type's scope, e.g.
        ``{"runs": "latest"}`` or ``{"since": "2026-09-01"}``.
        """
        key = _normalise_key(key)
        body: Dict[str, Any] = {}
        if scope is not None:
            body["scope"] = _mapping(scope, "scope")
        result = self._request("POST", f"/experiments/{urllib.parse.quote(key)}/pipeline",
                               body=body, key="evaluations")
        return list(result or [])

    def evaluations(self, key: str, metric: Optional[str] = None, *,
                    limit: int = 20) -> List[Dict[str, Any]]:
        """The newest evaluations (without logs), optionally of one metric."""
        key = _normalise_key(key)
        limit = max(1, min(_MAX_EVALUATIONS_LIMIT, int(limit)))
        result = self._request("GET", f"/experiments/{urllib.parse.quote(key)}/evaluations",
                               query={"metric": metric, "limit": limit}, key="evaluations")
        return list(result or [])

    def wait_for(self, key: str, evaluations: Sequence[Mapping[str, Any]], *,
                 timeout: float = 600, interval: float = 5) -> List[Dict[str, Any]]:
        """Poll until none of ``evaluations`` is queued or running any more.

        Returns the list in the same order with the current state of each
        entry. Entries without an id (a skipped step) come back unchanged.
        Raises WaitTimeout after ``timeout`` seconds.
        """
        key = _normalise_key(key)
        current = [dict(e) for e in evaluations]
        deadline = time.monotonic() + max(0.0, float(timeout))
        interval = max(0.2, float(interval))
        while True:
            pending = [e for e in current
                       if e.get("id") and e.get("status") in PENDING_EVALUATION_STATUSES]
            if not pending:
                return current
            if time.monotonic() >= deadline:
                raise WaitTimeout(
                    f"{len(pending)} Auswertung(en) nach {timeout:g} s noch nicht fertig.",
                    current,
                )
            time.sleep(interval)
            latest = {str(e.get("id")): e for e in self.evaluations(key, limit=_MAX_EVALUATIONS_LIMIT)}
            current = [dict(latest.get(str(e.get("id")), e)) if e.get("id") else e
                       for e in current]

    def run(self, key: str, variant: Optional[str] = None, name: Optional[str] = None,
            params: Optional[Mapping[str, Any]] = None, commit: Optional[str] = None, *,
            environment: Optional[Mapping[str, Any]] = None,
            note: Optional[str] = None) -> "Run":
        """A run as a context manager; reported when the block ends.

        ``log(**metrics)`` and ``add_rows(rows)`` collect; on exit the run is
        sent once with status finished. If the block raises, the run is sent
        with status failed (cancelled for Ctrl+C) and without measurements,
        so that half a benchmark never enters an evaluation; the exception
        propagates unchanged.
        """
        return Run(self, _normalise_key(key), variant=variant, name=name, params=params,
                   commit=commit, environment=environment, note=note)


class Run:
    """Collects metrics and rows of one run; see ``Client.run``."""

    def __init__(self, client: Client, key: str, *, variant: Optional[str] = None,
                 name: Optional[str] = None, params: Optional[Mapping[str, Any]] = None,
                 commit: Optional[str] = None, environment: Optional[Mapping[str, Any]] = None,
                 note: Optional[str] = None) -> None:
        self._client = client
        self.key = key
        self.variant = variant
        self.name = name
        self.params = _mapping(params, "params") if params is not None else None
        self.commit = commit
        self.environment = _mapping(environment, "environment") if environment is not None else None
        self.note = note
        self.metrics: Dict[str, Any] = {}
        self.rows: List[Dict[str, Any]] = []
        self.started_at: Optional[_dt.datetime] = None
        self.ended_at: Optional[_dt.datetime] = None
        self.status: Optional[str] = None
        #: The run as the Platform stored it, after the block ended.
        self.result: Optional[Dict[str, Any]] = None
        self._state = "new"

    def __enter__(self) -> "Run":
        if self._state != "new":
            raise ExperimentsError("Ein Lauf l\u00e4sst sich nur einmal verwenden.")
        self._state = "open"
        self.started_at = _utcnow()
        return self

    def _check_open(self) -> None:
        if self._state != "open":
            raise ExperimentsError(
                "Der Lauf ist nicht offen: log() und add_rows() nur innerhalb von "
                "\u00abwith client.run(...)\u00bb."
            )

    def log(self, metrics: Optional[Mapping[str, Any]] = None, /, **values: Any) -> "Run":
        """Set run-level metrics, ``run.log(ndcg_at_10=0.41)``; later calls merge."""
        self._check_open()
        merged: Dict[str, Any] = {}
        if metrics is not None:
            merged.update(_mapping(metrics, "metrics"))
        merged.update(values)
        for metric_key in merged:
            if not isinstance(metric_key, str) or not metric_key:
                raise ValidationError("Metrik-Schl\u00fcssel m\u00fcssen Texte sein.")
        self.metrics.update(merged)
        return self

    def add_rows(self, rows: Iterable[Mapping[str, Any]]) -> "Run":
        """Add measurement rows (for per-query values use ``dims``)."""
        self._check_open()
        self.rows.extend(_rows(rows))
        return self

    def add_row(self, metric: str, value: Any, *, count: Any = None, denominator: Any = None,
                sum_sq: Any = None, variant: Optional[str] = None, observed_at: Any = None,
                dims: Optional[Mapping[str, Any]] = None) -> "Run":
        """Add one row: ``run.add_row("ndcg_at_10", 0.52, dims={"query": "q17"})``."""
        self._check_open()
        row: Dict[str, Any] = {"metric": str(metric), "value": value}
        for field_name, field_value in (("count", count), ("denominator", denominator),
                                        ("sum_sq", sum_sq), ("variant", variant),
                                        ("observed_at", observed_at)):
            if field_value is not None:
                row[field_name] = field_value
        if dims is not None:
            row["dims"] = _mapping(dims, "dims")
        self.rows.append(row)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._state = "closed"
        self.ended_at = _utcnow()
        if exc_type is None:
            self.status = "finished"
        elif issubclass(exc_type, KeyboardInterrupt):
            self.status = "cancelled"
        else:
            self.status = "failed"
        finished = self.status == "finished"
        try:
            self.result = self._client.log_run(
                self.key, name=self.name, variant=self.variant, params=self.params,
                metrics=self.metrics if finished else None,
                rows=self.rows if finished else None,
                environment=self.environment, commit=self.commit, status=self.status,
                started_at=self.started_at, ended_at=self.ended_at, note=self.note,
            )
        except ExperimentsError as post_error:
            if exc_type is None:
                raise
            # The block's own exception is what the caller needs to see.
            warnings.warn(f"Der Lauf konnte nicht gemeldet werden: {post_error}",
                          stacklevel=2)
        return False


# -- command line ---------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python knovas_experiments.py ping``: check address, token and module switch."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["ping"]:
        print("Aufruf: python knovas_experiments.py ping   (mit KNOVAS_URL und "
              "KNOVAS_EXPERIMENTS_TOKEN)", file=sys.stderr)
        return 2
    try:
        user = Client().ping()
    except ExperimentsError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    roles = ", ".join(str(r) for r in (user.get("roles") or [])) or "-"
    print(f"Verbunden als {user.get('display_name', '?')} (Rollen: {roles}).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
