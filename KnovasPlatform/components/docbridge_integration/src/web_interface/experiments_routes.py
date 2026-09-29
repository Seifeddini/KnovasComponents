"""Experimente in the web app: its pages, their JSON API and the machine API.

Everything here is plumbing. The use cases live in ``experiments.service``,
which knows nothing about Flask; this module turns requests into service
calls and service answers (or refusals) into responses.

* Blueprint ``experiments``: the pages ``/experiments``,
  ``/experiments/verwaltung`` and ``/experiments/<KEY>`` and the session JSON
  API under ``/api/experiments``. The app's global gates have already asked
  for a signed-in person and, on every non-GET, for the X-CSRF-Token header.
  This blueprint adds the viewing role: anyone without it gets what an
  unknown address gets, a 404, so for them the module does not exist.
* Blueprint ``experiments_api``: ``/api/experiments/v1`` for CI and scripts.
  A personal access token in ``Authorization: Bearer`` is the only thing it
  accepts; the session cookie is never read. That is why the login gate
  (``IdentityGate.bearer_endpoints``) and the CSRF gate (endpoint prefix
  ``experiments_api.``) stand aside for it: a request that carries no token
  of its own is refused here, whatever cookie comes with it.
* ``install_experiments``: what create_app does when the module is switched
  on -- built-in evaluators and the core pack, both blueprints, the index
  client, the runner client and the worker threads.

Refusals: ``ExperimentsError`` carries a German message written for the
person and its HTTP status; both are returned as they are. Anything else is
logged with its traceback and answered with 500 "Interner Serverfehler":
exception text never reaches the browser or the CI log.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (sections 10, 13)
"""
from __future__ import annotations

import datetime as _dt
import decimal
import functools
import json
import logging
import math
import os
import re
import threading
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from flask import Blueprint, abort, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, InternalServerError, MethodNotAllowed
from werkzeug.exceptions import NotFound as HTTPNotFound

from experiments import kinds, labels, permissions, schema, store
from experiments.errors import ExperimentsError, NotFound, ValidationError
from experiments.service import ExperimentService

logger = logging.getLogger(__name__)

#: Experiment keys: <id_prefix>-<n>, e.g. MKT-58 (exp_domains.id_prefix).
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}$")

#: Endpoint names of the machine API start with this; the CSRF gate in
#: app.py exempts exactly these.
API_ENDPOINT_PREFIX = "experiments_api."

#: A CSV import may be at most this large. Checked against Content-Length
#: before the body is read, and against the file itself after.
MAX_CSV_BYTES = 20 * 1024 * 1024

#: CSV imports this process runs at the same time. A maximal import (20 MB,
#: 200'000 rows) is parsed, prepared and copied inside the request thread and
#: holds a few hundred MB while it does; more than one at a time per gunicorn
#: worker would take the threads and the memory the search needs. Another
#: import meanwhile is refused with 503 at once, before its upload is read.
CSV_IMPORTS_PER_PROCESS = 1
_CSV_IMPORT_SLOTS = threading.BoundedSemaphore(CSV_IMPORTS_PER_PROCESS)
#: Seconds a refused import is told to wait (Retry-After).
CSV_BUSY_RETRY_AFTER = 10

#: GET /v1/experiments/<key>/evaluations: default and ceiling of ``limit``.
#: The snapshot holds the newest 60 evaluations, so more cannot be asked for.
EVALUATIONS_DEFAULT_LIMIT = 20
EVALUATIONS_MAX_LIMIT = 60

MSG_NOT_FOUND = "Nicht gefunden."
MSG_INTERNAL = "Interner Serverfehler"
MSG_BAD_TOKEN = "Ung\u00fcltiger oder abgelaufener Zugangsschl\u00fcssel."
MSG_CSV_TOO_LARGE = "Die Datei ist gr\u00f6sser als 20 MB."
MSG_TOO_LARGE = "Die Anfrage ist zu gross."
MSG_NO_FILE = "Bitte eine CSV-Datei hochladen."
MSG_NOT_JSON = "Die Anfrage muss JSON sein (Content-Type: application/json)."
MSG_BAD_JSON = "Die Anfrage ist kein g\u00fcltiges JSON."
MSG_BAD_REQUEST = "Die Anfrage ist ung\u00fcltig."
MSG_METHOD = "Diese Methode ist hier nicht erlaubt."
MSG_FAILED = "Die Anfrage konnte nicht bearbeitet werden."
MSG_CSV_BUSY = ("Es l\u00e4uft gerade schon ein CSV-Import. Bitte in einem Moment noch "
                "einmal versuchen.")

_HTTP_MESSAGES = {
    400: MSG_BAD_REQUEST,
    404: MSG_NOT_FOUND,
    405: MSG_METHOD,
    413: MSG_TOO_LARGE,
}

#: What the machine API tells a CI job about an experiment: enough to log
#: runs against it, nothing it has no use for (notes, decisions, activity).
_TRIMMED_EXPERIMENT_KEYS = ("key", "title", "status", "domain", "type", "variants", "metrics",
                            "row_version")

_SAMPLE_SIZE_ARGS = ("kind", "base", "sd", "mde", "alpha", "power")


# -- responses ------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """``value`` as strict JSON: no NaN or Infinity (None instead), datetimes
    as ISO 8601, UUIDs and Decimals as JSON types.

    The service already promises this; the web layer checks once more, because
    ``jsonify`` would otherwise write ``NaN`` -- which is not JSON, and which
    browsers and the SDK then refuse to parse at all.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return [_plain(v) for v in sorted(value, key=str)]
    return value


def _ok(name: str, value: Any, status: int = 200):
    return jsonify({"success": True, name: _plain(value)}), status


def _error(status: int, message: str, fields: Optional[Dict[str, str]] = None):
    body: Dict[str, Any] = {"success": False, "error": message}
    if fields:
        body["fields"] = {str(k): str(v) for k, v in fields.items()}
    return jsonify(body), status


def _refusal(exc: ExperimentsError):
    status = getattr(exc, "status", 400)
    if not isinstance(status, int) or not 400 <= status <= 599:
        status = 400
    return _error(status, str(exc.message), getattr(exc, "fields", None))


def _json_view(view: Callable) -> Callable:
    """Map what a view raises to a JSON answer.

    HTTP exceptions (a body over MAX_CONTENT_LENGTH, a malformed upload) pass
    through to the blueprint's error handler, which answers them in JSON too.
    """

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except ExperimentsError as exc:
            return _refusal(exc)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - the one place that turns anything into a 500
            logger.error("Experiments request failed: %s %s", request.method, request.path,
                         exc_info=True)
            return _error(500, MSG_INTERNAL)

    return wrapped


def _no_store(response):
    """Answers carry experiment data and, once, a token's plaintext: nothing
    along the way may keep a copy."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


# -- requests -------------------------------------------------------------------


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _body() -> Any:
    """The request's JSON body, or None when there is none.

    Stricter than ``request.get_json``: the body must be declared JSON (the
    global hook that refuses a body naming its own ``access_groups`` only
    looks at declared JSON, so accepting anything else would step around it),
    and NaN or Infinity are refused rather than read as numbers.
    """
    raw = request.get_data(cache=True)
    if not raw or not raw.strip():
        return None
    if not request.is_json:
        raise ValidationError(MSG_NOT_JSON)
    try:
        return json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise ValidationError(MSG_BAD_JSON) from None


def _key(value: str) -> str:
    """The experiment key from the path; anything else is not a route."""
    if not isinstance(value, str) or not KEY_RE.fullmatch(value):
        raise NotFound()
    return value


def _is_api_path() -> bool:
    return (request.path or "").startswith("/api/")


def _client_ip() -> Optional[str]:
    """The same address identity.webauth records for a session, so the audit
    log and the session list name the same caller: the X-Forwarded-For entry
    the trusted proxy added (PLATFORM_TRUSTED_PROXY_HOPS), never the one the
    client wrote itself."""
    from identity.webauth import client_ip

    return client_ip()


def _request_meta(token_id: Optional[str] = None) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"ip": _client_ip(), "user_agent": request.headers.get("User-Agent")}
    if token_id:
        meta["token_id"] = token_id
    return meta


def _args(*names: str) -> Dict[str, str]:
    return {name: request.args[name] for name in names if name in request.args}


def _trimmed(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: snapshot.get(k) for k in _TRIMMED_EXPERIMENT_KEYS}
    out["metrics"] = [{k: v for k, v in metric.items() if k != "aggregates"}
                      for metric in snapshot.get("metrics") or []]
    return out


def _evaluations_limit(raw: Optional[str]) -> int:
    try:
        value = int(str(raw).strip()) if raw not in (None, "") else EVALUATIONS_DEFAULT_LIMIT
    except ValueError:
        value = EVALUATIONS_DEFAULT_LIMIT
    return max(1, min(EVALUATIONS_MAX_LIMIT, value))


def _meta(user: Any, settings: Any, runner: Any) -> Dict[str, Any]:
    """Labels, kinds and limits the pages need; the runner's health is
    cached by RunnerClient (30 s), so a page load does not probe it each time."""
    runner_info: Dict[str, Any] = {"configured": False, "ok": False, "languages": {}}
    if runner is not None:
        health = runner.health() or {}
        runner_info = {"configured": True, "ok": health.get("ok") is True,
                       "languages": dict(health.get("languages") or {})}
    return {
        "me": {"id": str(user.id), "display_name": user.display_name},
        "can_manage": permissions.can_manage(user),
        "kinds": [spec.as_dict() for spec in kinds.KINDS.values()],
        "field_types": list(schema.FIELD_TYPES),
        "decision_verdicts": dict(labels.DECISION_VERDICT_LABELS),
        "evaluation_verdicts": dict(labels.EVALUATION_VERDICT_LABELS),
        "note_kinds": dict(labels.NOTE_KIND_LABELS),
        "directions": dict(labels.DIRECTION_LABELS),
        "metric_roles": dict(labels.METRIC_ROLE_LABELS),
        "run_statuses": dict(labels.RUN_STATUS_LABELS),
        "evaluation_statuses": dict(labels.EVALUATION_STATUS_LABELS),
        "index_states": dict(labels.INDEX_STATE_LABELS),
        "sources": dict(labels.SOURCE_LABELS),
        "runner": runner_info,
        "index_enabled": bool(settings.index_enabled),
        "pointer_prefix": settings.pointer_prefix,
        "max_csv_rows": int(settings.max_csv_rows),
        "max_rows_per_request": int(settings.max_rows_per_request),
    }


def _error_handlers(bp: Blueprint, *, pages: bool) -> None:
    """Errors raised outside ``_json_view`` (the gates, reading the body):
    JSON for API paths. Pages keep the app's ordinary error page, so a
    refused page looks like any unknown one."""

    @bp.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        if pages and not _is_api_path():
            return exc
        code = exc.code or 500
        return _error(code, _HTTP_MESSAGES.get(code, MSG_FAILED if code < 500 else MSG_INTERNAL))

    @bp.errorhandler(Exception)
    def _unexpected(exc: Exception):
        logger.error("Experiments request failed: %s %s", request.method, request.path,
                     exc_info=exc)
        if pages and not _is_api_path():
            return InternalServerError()
        return _error(500, MSG_INTERNAL)


# -- the session blueprint: pages and /api/experiments -------------------------


def create_experiments_blueprint(gate: Any, *, settings: Any, runner: Any = None,
                                 knovas_client: Any = None,
                                 csrf_token: Callable[[], str],
                                 page_context: Callable[[], Dict[str, Any]]) -> Blueprint:
    """Build the ``experiments`` blueprint.

    Args:
        gate: the IdentityGate; ``current_user()`` and the request's
            ``connection()``.
        settings: ``experiments.settings.ExperimentsSettings``.
        runner: the RunnerClient, or None without a runner.
        knovas_client: the request-bound Knovas client the search uses. The
            experiments search goes through it so Knovas answers as the
            signed-in person, with that person's access groups.
        csrf_token: returns the session's CSRF token (for the page meta tag).
        page_context: the sidebar values plus app_title, brand, asset_version.
    """
    bp = Blueprint("experiments", __name__)

    def knovas_search(query: str, limit: int) -> Dict[str, Any]:
        return knovas_client.search_documents(query=query, limit=limit)

    def service() -> ExperimentService:
        return ExperimentService(
            gate.connection(), gate.current_user(), settings, runner=runner,
            knovas_search=knovas_search if knovas_client is not None else None,
            request_meta=_request_meta())

    @bp.before_request
    def require_viewing_role():
        """Without a viewing role the module is not there: 404, never 403,
        so the answer is the same as for an address that does not exist."""
        if permissions.can_view(gate.current_user()):
            return None
        if _is_api_path():
            return _error(404, MSG_NOT_FOUND)
        abort(404)

    @bp.after_request
    def _api_no_store(response):
        return _no_store(response) if _is_api_path() else response

    _error_handlers(bp, pages=True)

    # -- pages -------------------------------------------------------------

    def page(template: str, **extra: Any):
        return render_template(
            template,
            active_nav="experiments",
            **page_context(),
            csrf_token=csrf_token(),
            experiments_can_manage=permissions.can_manage(gate.current_user()),
            pointer_prefix=settings.pointer_prefix,
            **extra,
        )

    @bp.route("/experiments")
    def list_page():
        return page("experiments_list.html")

    @bp.route("/experiments/verwaltung")
    def manage_page():
        # Every viewer: the tokens tab is theirs. The manager tabs are hidden
        # by the template and refused by the service, not by this page.
        return page("experiments_manage.html")

    @bp.route("/experiments/<key>")
    def detail_page(key: str):
        if not KEY_RE.fullmatch(key) or store.get_experiment_row(gate.connection(), key) is None:
            abort(404)
        return page("experiments_detail.html", experiment_key=key)

    # -- /api/experiments: static paths first, then /<key> ------------------------

    @bp.route("/api/experiments", methods=["GET"])
    @_json_view
    def list_experiments():
        a = request.args
        return _ok("result", service().list_experiments(
            domain=a.get("domain"), status=a.get("status"), q=a.get("q"), tag=a.get("tag"),
            include_archived=a.get("archived", "0"), after=a.get("after"), limit=a.get("limit")))

    @bp.route("/api/experiments", methods=["POST"])
    @_json_view
    def create_experiment():
        return _ok("experiment", service().create_experiment(_body()), 201)

    @bp.route("/api/experiments/meta", methods=["GET"])
    @_json_view
    def meta():
        return _ok("meta", _meta(gate.current_user(), settings, runner))

    @bp.route("/api/experiments/search", methods=["GET"])
    @_json_view
    def search():
        return _ok("result", service().search(request.args.get("q"), request.args.get("limit")))

    @bp.route("/api/experiments/sample-size", methods=["GET"])
    @_json_view
    def sample_size():
        return _ok("result", service().sample_size(_args(*_SAMPLE_SIZE_ARGS)))

    @bp.route("/api/experiments/preferences", methods=["GET", "PUT"])
    @_json_view
    def preferences():
        if request.method == "PUT":
            return _ok("preferences", service().update_preferences(_body()))
        return _ok("preferences", service().get_preferences())

    @bp.route("/api/experiments/settings", methods=["GET", "PUT"])
    @_json_view
    def module_settings():
        if request.method == "PUT":
            return _ok("settings", service().update_settings(_body()))
        return _ok("settings", service().get_settings())

    # domains
    @bp.route("/api/experiments/domains", methods=["GET"])
    @_json_view
    def list_domains():
        return _ok("domains", service().list_domains(request.args.get("archived", "0")))

    @bp.route("/api/experiments/domains", methods=["POST"])
    @_json_view
    def create_domain():
        return _ok("domain", service().create_domain(_body()))

    @bp.route("/api/experiments/domains/<domain_key>", methods=["PATCH"])
    @_json_view
    def update_domain(domain_key: str):
        return _ok("domain", service().update_domain(domain_key, _body()))

    @bp.route("/api/experiments/domains/<domain_key>/export", methods=["GET"])
    @_json_view
    def export_domain(domain_key: str):
        return _ok("text", service().export_domain(domain_key))

    # types
    @bp.route("/api/experiments/types", methods=["GET"])
    @_json_view
    def list_types():
        return _ok("types", service().list_types(request.args.get("domain") or None,
                                                  request.args.get("archived", "0")))

    @bp.route("/api/experiments/types", methods=["POST"])
    @_json_view
    def create_type():
        return _ok("type", service().create_type(_body()))

    @bp.route("/api/experiments/types/validate", methods=["POST"])
    @_json_view
    def validate_type():
        return _ok("definition", service().validate_type(_body()))

    @bp.route("/api/experiments/types/<type_id>", methods=["GET"])
    @_json_view
    def get_type(type_id: str):
        return _ok("type", service().get_type(type_id))

    @bp.route("/api/experiments/types/<type_id>/versions", methods=["POST"])
    @_json_view
    def add_type_version(type_id: str):
        return _ok("type", service().add_type_version(type_id, _body()))

    @bp.route("/api/experiments/types/<type_id>/archive", methods=["POST"])
    @_json_view
    def archive_type(type_id: str):
        body = _body()
        archived = body.get("archived") if isinstance(body, dict) else body
        return _ok("type", service().set_type_archived(type_id, archived))

    # metrics
    @bp.route("/api/experiments/metrics", methods=["GET"])
    @_json_view
    def list_metrics():
        return _ok("metrics", service().list_metrics(request.args.get("domain") or None,
                                                      request.args.get("archived", "0")))

    @bp.route("/api/experiments/metrics", methods=["POST"])
    @_json_view
    def create_metric():
        return _ok("metric", service().create_metric(_body()))

    @bp.route("/api/experiments/metrics/<metric_id>", methods=["PATCH"])
    @_json_view
    def update_metric(metric_id: str):
        return _ok("metric", service().update_metric(metric_id, _body()))

    # evaluators
    @bp.route("/api/experiments/evaluators", methods=["GET"])
    @_json_view
    def list_evaluators():
        return _ok("evaluators", service().list_evaluators(request.args.get("archived", "0")))

    @bp.route("/api/experiments/evaluators", methods=["POST"])
    @_json_view
    def create_evaluator():
        return _ok("evaluator", service().create_evaluator(_body()))

    @bp.route("/api/experiments/evaluators/<evaluator_id>", methods=["GET"])
    @_json_view
    def get_evaluator(evaluator_id: str):
        return _ok("evaluator", service().get_evaluator(evaluator_id))

    @bp.route("/api/experiments/evaluators/<evaluator_id>/versions", methods=["POST"])
    @_json_view
    def add_evaluator_version(evaluator_id: str):
        return _ok("evaluator", service().add_evaluator_version(evaluator_id, _body()))

    @bp.route("/api/experiments/evaluators/<evaluator_id>/test", methods=["POST"])
    @_json_view
    def test_evaluator(evaluator_id: str):
        return _ok("result", service().test_evaluator(evaluator_id, _body()))

    # tokens (the caller's own)
    @bp.route("/api/experiments/tokens", methods=["GET"])
    @_json_view
    def list_tokens():
        return _ok("tokens", service().list_tokens())

    @bp.route("/api/experiments/tokens", methods=["POST"])
    @_json_view
    def create_token():
        return _ok("token", service().create_token(_body()))

    @bp.route("/api/experiments/tokens/<token_id>", methods=["DELETE"])
    @_json_view
    def revoke_token(token_id: str):
        return _ok("token", service().revoke_token(token_id))

    # index and packs
    @bp.route("/api/experiments/index", methods=["GET"])
    @_json_view
    def index_status():
        return _ok("index", service().index_status())

    @bp.route("/api/experiments/index/reindex", methods=["POST"])
    @_json_view
    def reindex_all():
        return _ok("result", service().reindex_all())

    @bp.route("/api/experiments/packs", methods=["GET"])
    @_json_view
    def list_packs():
        return _ok("packs", service().list_packs())

    @bp.route("/api/experiments/packs/import", methods=["POST"])
    @_json_view
    def import_pack():
        return _ok("result", service().import_pack(_body()))

    @bp.route("/api/experiments/packs/<name>/install", methods=["POST"])
    @_json_view
    def install_pack(name: str):
        return _ok("result", service().install_pack(name))

    # -- one experiment ------------------------------------------------------------

    @bp.route("/api/experiments/<key>", methods=["GET"])
    @_json_view
    def get_experiment(key: str):
        return _ok("experiment", service().get_experiment(_key(key)))

    @bp.route("/api/experiments/<key>", methods=["PATCH"])
    @_json_view
    def update_experiment(key: str):
        return _ok("experiment", service().update_experiment(_key(key), _body()))

    @bp.route("/api/experiments/<key>", methods=["DELETE"])
    @_json_view
    def delete_experiment(key: str):
        return _ok("result", service().delete_experiment(_key(key)))

    @bp.route("/api/experiments/<key>/transition", methods=["POST"])
    @_json_view
    def transition(key: str):
        return _ok("experiment", service().transition(_key(key), _body()))

    @bp.route("/api/experiments/<key>/variants", methods=["PUT"])
    @_json_view
    def set_variants(key: str):
        return _ok("experiment", service().set_variants(_key(key), _body()))

    @bp.route("/api/experiments/<key>/metrics", methods=["PUT"])
    @_json_view
    def set_metrics(key: str):
        return _ok("experiment", service().set_metrics(_key(key), _body()))

    @bp.route("/api/experiments/<key>/measurements", methods=["POST"])
    @_json_view
    def add_measurements(key: str):
        return _ok("result", service().add_measurements(_key(key), _body()), 201)

    @bp.route("/api/experiments/<key>/measurements/csv", methods=["POST"])
    @_json_view
    def import_csv(key: str):
        key = _key(key)
        # Before the body is read: a 30 MB upload is refused without being
        # parsed (MAX_CONTENT_LENGTH alone would let it through up to 32 MB).
        if request.content_length is not None and request.content_length > MAX_CSV_BYTES:
            return _error(413, MSG_CSV_TOO_LARGE)
        # Before request.files, which is what reads and parses the upload.
        if not _CSV_IMPORT_SLOTS.acquire(blocking=False):
            logger.info("Experiments: CSV import into %s refused, another one is running.", key)
            response, status = _error(503, MSG_CSV_BUSY)
            response.headers["Retry-After"] = str(CSV_BUSY_RETRY_AFTER)
            return response, status
        try:
            upload = request.files.get("file")
            if upload is None:
                raise ValidationError(MSG_NO_FILE, fields={"file": MSG_NO_FILE})
            content = upload.read(MAX_CSV_BYTES + 1)
            if len(content) > MAX_CSV_BYTES:
                return _error(413, MSG_CSV_TOO_LARGE)
            return _ok("result", service().import_csv(key, content, upload.filename), 201)
        finally:
            _CSV_IMPORT_SLOTS.release()

    @bp.route("/api/experiments/<key>/batches", methods=["GET"])
    @_json_view
    def list_batches(key: str):
        return _ok("result", service().list_batches(_key(key), request.args.get("after"),
                                                    request.args.get("limit")))

    @bp.route("/api/experiments/<key>/batches/<batch_id>", methods=["DELETE"])
    @_json_view
    def delete_batch(key: str, batch_id: str):
        return _ok("result", service().delete_batch(_key(key), batch_id))

    @bp.route("/api/experiments/<key>/runs", methods=["GET"])
    @_json_view
    def list_runs(key: str):
        return _ok("result", service().list_runs(_key(key), request.args.get("after"),
                                                 request.args.get("limit")))

    @bp.route("/api/experiments/<key>/runs", methods=["POST"])
    @_json_view
    def add_run(key: str):
        return _ok("run", service().add_run(_key(key), _body()), 201)

    @bp.route("/api/experiments/<key>/notes", methods=["POST"])
    @_json_view
    def add_note(key: str):
        return _ok("note", service().add_note(_key(key), _body()), 201)

    @bp.route("/api/experiments/<key>/notes/<note_id>", methods=["DELETE"])
    @_json_view
    def delete_note(key: str, note_id: str):
        return _ok("result", service().delete_note(_key(key), note_id))

    @bp.route("/api/experiments/<key>/evaluations", methods=["POST"])
    @_json_view
    def run_evaluation(key: str):
        return _ok("evaluation", service().run_evaluation(_key(key), _body()), 201)

    @bp.route("/api/experiments/<key>/pipeline", methods=["POST"])
    @_json_view
    def run_pipeline(key: str):
        return _ok("evaluations", service().run_pipeline(_key(key), _body()))

    @bp.route("/api/experiments/<key>/evaluations/<evaluation_id>", methods=["GET"])
    @_json_view
    def get_evaluation(key: str, evaluation_id: str):
        return _ok("evaluation", service().get_evaluation(_key(key), evaluation_id))

    @bp.route("/api/experiments/<key>/metrics/<metric_key>/timeseries", methods=["GET"])
    @_json_view
    def timeseries(key: str, metric_key: str):
        return _ok("series", service().get_timeseries(_key(key), metric_key,
                                                      request.args.get("bucket") or "week"))

    @bp.route("/api/experiments/<key>/decisions", methods=["POST"])
    @_json_view
    def decide(key: str):
        return _ok("experiment", service().decide(_key(key), _body()), 201)

    @bp.route("/api/experiments/<key>/reindex", methods=["POST"])
    @_json_view
    def reindex(key: str):
        return _ok("result", service().reindex(_key(key)))

    @bp.route("/api/experiments/<key>/activity", methods=["GET"])
    @_json_view
    def activity(key: str):
        return _ok("activity", service().activity(_key(key), request.args.get("limit")))

    return bp


# -- the machine API: /api/experiments/v1 with bearer tokens ----------------------


def _bearer_token() -> Optional[str]:
    """The token of ``Authorization: Bearer <token>`` (scheme case-insensitive,
    RFC 6750), or None. Nothing else is looked at: no cookie, no query string."""
    header = request.headers.get("Authorization", "")
    scheme, _, credentials = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = credentials.strip()
    return token or None


def _without_session() -> None:
    """Replace this request's session with Flask's null session.

    The machine API must not act on the browser session, and must not touch
    it either: Flask would otherwise re-sign and re-send a permanent session's
    cookie on the way out, extending a browser session from a CI call. With
    the null session nothing downstream can read the cookie's contents, and
    nothing is written back.
    """
    from flask import current_app
    from flask.globals import request_ctx

    request_ctx.session = current_app.session_interface.make_null_session(current_app)


def _unauthorized():
    response, status = _error(401, MSG_BAD_TOKEN)
    response.headers["WWW-Authenticate"] = 'Bearer realm="experiments"'
    return response, status


def _admit(conn: Any, token: Optional[str]) -> Tuple[Optional[Any], Optional[str], str]:
    """(user, token_id, reason). user is None when the request is refused;
    reason is for the log only and never names the token."""
    from identity.users import UserRepository

    if token is None:
        return None, None, "no bearer token"
    found = store.resolve_api_token(conn, token)
    if found is None:
        return None, None, "unknown, revoked or expired token"
    user = UserRepository(conn).get(found["user_id"])
    # The token acts as its user with that user's standing now, not as it
    # was when the token was made: a disabled, locked or demoted account's
    # tokens stop working with it.
    if user is None:
        return None, found["token_id"], "user gone"
    if not user.is_active:
        return None, found["token_id"], "user not active"
    if user.is_locked:
        return None, found["token_id"], "user locked"
    if user.must_change_password:
        return None, found["token_id"], "user must change password"
    if not permissions.can_view(user):
        return None, found["token_id"], "user without an experiments role"
    return user, found["token_id"], ""


def create_experiments_api_blueprint(gate: Any, *, settings: Any, runner: Any = None) -> Blueprint:
    """Build the ``experiments_api`` blueprint (``/api/experiments/v1``).

    Only ``gate.connection()`` is used from the gate -- the request's database
    connection, closed at teardown. ``gate.current_user()`` would read the
    session cookie, which this API must never do.
    """
    bp = Blueprint("experiments_api", __name__, url_prefix="/api/experiments/v1")

    @bp.before_request
    def require_bearer_token():
        _without_session()
        try:
            user, token_id, reason = _admit(gate.connection(), _bearer_token())
        except Exception:  # noqa: BLE001 - never a 401 for our own failure
            logger.error("Experiments API: token check failed.", exc_info=True)
            return _error(500, MSG_INTERNAL)
        if user is None:
            logger.info("Experiments API refused %s %s from %s: %s%s", request.method,
                        request.path, _client_ip(), reason,
                        f" (token {token_id})" if token_id else "")
            return _unauthorized()
        g.experiments_api_caller = (user, token_id)
        return None

    @bp.after_request
    def _api_no_store(response):
        return _no_store(response)

    _error_handlers(bp, pages=False)

    def service() -> ExperimentService:
        user, token_id = g.experiments_api_caller
        return ExperimentService(gate.connection(), user, settings, runner=runner,
                                 request_meta=_request_meta(token_id))

    @bp.route("/ping", methods=["GET"])
    @_json_view
    def ping():
        user, _ = g.experiments_api_caller
        return _ok("user", {"display_name": user.display_name, "roles": sorted(user.roles)})

    @bp.route("/experiments", methods=["POST"])
    @_json_view
    def create_experiment():
        return _ok("experiment", _trimmed(service().create_experiment(_body())), 201)

    @bp.route("/experiments/<key>", methods=["GET"])
    @_json_view
    def get_experiment(key: str):
        return _ok("experiment", _trimmed(service().get_experiment(_key(key))))

    @bp.route("/experiments/<key>/runs", methods=["POST"])
    @_json_view
    def add_run(key: str):
        return _ok("run", service().add_run(_key(key), _body(), source="api"), 201)

    @bp.route("/experiments/<key>/measurements", methods=["POST"])
    @_json_view
    def add_measurements(key: str):
        return _ok("result", service().add_measurements(_key(key), _body(), source="api"), 201)

    @bp.route("/experiments/<key>/notes", methods=["POST"])
    @_json_view
    def add_note(key: str):
        return _ok("note", service().add_note(_key(key), _body()), 201)

    @bp.route("/experiments/<key>/pipeline", methods=["POST"])
    @_json_view
    def run_pipeline(key: str):
        return _ok("evaluations", service().run_pipeline(_key(key), _body(), trigger="api"))

    @bp.route("/experiments/<key>/evaluations", methods=["GET"])
    @_json_view
    def evaluations(key: str):
        snapshot = service().get_experiment(_key(key))
        metric = request.args.get("metric") or None
        items: List[Dict[str, Any]] = [
            e for e in snapshot.get("evaluations") or []
            if metric is None or e.get("metric_key") == metric
        ]
        return _ok("evaluations", items[:_evaluations_limit(request.args.get("limit"))])

    # The root itself (/v1 and /v1/) too: <path:rest> never matches an empty
    # rest, and /api/experiments/v1 would otherwise be taken for the session
    # API's /api/experiments/<key> -- whose gate asks a CI job to sign in.
    @bp.route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], defaults={"rest": ""},
              strict_slashes=False)
    @bp.route("/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown(rest: str):
        """Any other path or method under /v1, once the token checked out: a
        404 in JSON. Without it the request would fall through to the session
        gate, and a CI job with a typo in its path would be told to sign in."""
        return _error(404, MSG_NOT_FOUND)

    return bp


# -- wrong methods -------------------------------------------------------------------


def _session_module_path(path: str) -> bool:
    """A path of the ``experiments`` blueprint: its pages and /api/experiments.

    Not the machine API: its catch-all takes every method there itself.
    """
    if path in ("/experiments", "/api/experiments"):
        return True
    if path.startswith("/api/experiments/"):
        return not (path == "/api/experiments/v1" or path.startswith("/api/experiments/v1/"))
    return path.startswith("/experiments/")


def _install_method_not_allowed(app: Any, gate: Any) -> None:
    """Answer a wrong method on a module route the way the module answers.

    A MethodNotAllowed is raised while routing, before any blueprint is
    chosen, so neither the viewing-role gate nor the blueprint's JSON error
    handlers see it, and Flask would send its HTML 405 to everyone. That
    would tell a person without a viewing role that the address exists
    (for them the module answers every route with a 404), and hand a viewer
    HTML where the API promises a JSON failure body. The rest of the app
    keeps Flask's default. Signed-out callers never get here: the login
    gate runs before the route is dispatched.
    """

    def experiments_method_not_allowed(exc: MethodNotAllowed):
        path = request.path or ""
        if not _session_module_path(path):
            return exc
        try:
            viewer = permissions.can_view(gate.current_user())
        except Exception:  # noqa: BLE001 - same answer as a failing role gate
            logger.error("Experiments request failed: %s %s", request.method, path,
                         exc_info=True)
            if _is_api_path():
                response, status = _error(500, MSG_INTERNAL)
                return _no_store(response), status
            return InternalServerError()
        if not viewer:
            # What a GET gets there: the module is not there.
            if _is_api_path():
                response, status = _error(404, MSG_NOT_FOUND)
                return _no_store(response), status
            return HTTPNotFound()
        if not _is_api_path():
            return exc
        response, status = _error(405, MSG_METHOD)
        response.headers["Allow"] = ", ".join(sorted(exc.valid_methods or ()))
        return _no_store(response), status

    app.register_error_handler(MethodNotAllowed, experiments_method_not_allowed)


# -- wiring --------------------------------------------------------------------------


def _prepare_database(connect: Callable[[], Any]) -> None:
    """Register the built-in evaluators and install the core pack.

    Both are idempotent; the identity boot lock serialises the gunicorn
    workers that start at the same moment anyway, so they do not race for the
    same inserts. A failure is logged and the Platform starts regardless: the
    search must not go down with an optional module, and the service registers
    a missing built-in evaluator on first use.
    """
    from identity.startup import BOOT_LOCK_CLASS, BOOT_LOCK_OBJECT

    try:
        conn = connect()
    except Exception:  # noqa: BLE001
        logger.error("Experimente: no database connection at start; built-in evaluators "
                     "and the core pack were not checked.", exc_info=True)
        return
    try:
        conn.execute("SELECT pg_advisory_lock(%s, %s)", (BOOT_LOCK_CLASS, BOOT_LOCK_OBJECT))
        try:
            store.ensure_builtin_evaluators(conn)
            store.ensure_core_pack(conn)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s, %s)", (BOOT_LOCK_CLASS, BOOT_LOCK_OBJECT))
    except Exception:  # noqa: BLE001
        logger.error("Experimente: built-in evaluators or the core pack could not be "
                     "installed at start.", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            logger.debug("Experiments boot connection close failed", exc_info=True)


def worker_connect() -> Callable[[], Any]:
    """How the worker threads open their own connections, decided once:
    PLATFORM_DB_DSN when it is set (tests pin a schema that way), else the
    identity database settings."""
    dsn = (os.environ.get("PLATFORM_DB_DSN") or "").strip()
    if dsn:
        import psycopg

        return functools.partial(psycopg.connect, dsn, autocommit=True)
    from identity import db as identity_db

    return identity_db.connect


def install_experiments(app: Any, *, config: Any, settings: Any, gate: Any, api_client: Any,
                        csrf_token: Callable[[], str],
                        page_context: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Switch the module on in ``app``; create_app calls this only when
    EXPERIMENTS_ENABLED is on and per-user identity is on.

    Returns ``{"index_client", "runner", "workers"}`` for app.extensions.
    """
    from experiments import indexer, jobs, tasks
    from experiments.runner_client import RunnerClient
    from identity import db as identity_db

    _prepare_database(identity_db.connect)

    # Always built while the module is on: index_enabled gates uploads, not
    # deletions -- an experiment deleted after indexing was switched off must
    # still leave Knovas. Looked up on the module at call time, so a test can
    # replace it.
    index_client = indexer.make_index_client(config)
    runner = None
    if settings.runner_url:
        runner = RunnerClient(settings.runner_url, timeout_seconds=settings.runner_timeout_seconds)

    app.register_blueprint(create_experiments_blueprint(
        gate, settings=settings, runner=runner, knovas_client=api_client,
        csrf_token=csrf_token, page_context=page_context))
    app.register_blueprint(create_experiments_api_blueprint(gate, settings=settings, runner=runner))
    gate.allow_bearer_endpoints(bearer_endpoints(app.view_functions))
    _install_method_not_allowed(app, gate)

    workers: List[Any] = []
    if settings.worker_enabled:
        handlers, on_dead, maintenance = tasks.build_handlers(
            settings=settings, index_client=index_client, runner=runner)
        workers = jobs.start_workers_once(settings=settings, connect=worker_connect(),
                                          handlers=handlers, on_dead=on_dead,
                                          maintenance=maintenance)
    logger.info("Experimente eingeschaltet (Index %s, Rechenumgebung %s, Worker %s).",
                "an" if settings.index_enabled else "aus",
                "eingerichtet" if runner is not None else "nicht eingerichtet",
                len(workers))
    return {"index_client": index_client, "runner": runner, "workers": workers}


def bearer_endpoints(view_functions: Iterable[str]) -> List[str]:
    """The machine API's endpoint names among ``view_functions``."""
    return sorted(name for name in view_functions if name.startswith(API_ENDPOINT_PREFIX))
