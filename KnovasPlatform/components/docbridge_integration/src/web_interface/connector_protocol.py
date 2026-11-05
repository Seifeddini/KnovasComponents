"""Connector protocol v1 over HTTP: /api/connectors/v1 (build plan step B13; builder.protocol has the contract).

Components call these endpoints; people never do. They carry no session: the
identity gate stands aside for them (``allow_bearer_endpoints``) because each
one refuses every request without the component's own credential -- the
one-time enrolment token for /enrol, the control token (and, behind a TLS
terminator, the client certificate's fingerprint) for everything else. The
tenant is the Platform's own, from its certificate; a component never names it.
Logs carry the component id and counts only.

Knovas Link (connection layer MVP-23) speaks the same calls for its site: its heartbeat carries the site's telemetry,
and a report it forwards for a component behind it names that component (header X-Sub-Component or the body field
``sub_component``), so it is kept as that component's. A component enrolled on its own keeps working as before.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable, Optional

from flask import Blueprint, jsonify, request

from builder import protocol

logger = logging.getLogger(__name__)

#: The endpoint names the identity gate lets through without a session (each checks its own credential).
ENDPOINTS = ("connector_protocol.enrol", "connector_protocol.heartbeat", "connector_protocol.config", "connector_protocol.package",
             "connector_protocol.runs", "connector_protocol.command_done", "connector_protocol.events",
             "connector_protocol.checks")
MAX_BODY = 256 * 1024


def create_connector_protocol_blueprint(*, store_factory: Callable[[], Any], secrets: Callable[[], Any],
                                        tenant_provider: Callable[[], Optional[str]],
                                        signer: Optional[Callable[[str, str], Any]] = None,
                                        require_client_certificate: bool = True,
                                        brain_url: Optional[Callable[[], Optional[str]]] = None,
                                        brain_ca: Optional[Callable[[], Optional[str]]] = None,
                                        issue_certificates: bool = True) -> Blueprint:
    """``secrets()`` is the settings registry's store of the components' token hashes (sealed);
    ``signer(csr, name)`` has the Brain sign a component's certificate for this Platform's tenant.
    ``require_client_certificate``: refuse a call without the certificate fingerprint the TLS terminator sets
    (the default); False only for a deployment where no terminator checks client certificates.
    ``brain_url()`` is the Brain API a component writes to with its own certificate; ``brain_ca()`` the CA (PEM, public)
    its TLS certificate is checked against, so that nobody has to copy it to the company by hand.
    ``issue_certificates``: False where the Brain does not sign at enrolment; see builder.protocol.enrol."""
    bp = Blueprint("connector_protocol", __name__, url_prefix="/api/connectors/v1")

    def _now() -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def _error(exc: protocol.ProtocolError):
        return jsonify({"error": exc.code, "message": exc.message}), exc.status

    def _tenant() -> str:
        tenant = tenant_provider()
        if not tenant:
            raise protocol.ProtocolError(503, "no_tenant", "this Platform does not know its tenant")
        return str(tenant)

    def _body() -> dict:
        if (request.content_length or 0) > MAX_BODY:
            raise protocol.ProtocolError(413, "too_large", "body too large")
        body = request.get_json(silent=True)
        if body is None and request.method == "POST":
            raise protocol.ProtocolError(400, "not_json", "a JSON body")
        return body if isinstance(body, dict) else {}

    def _component():
        auth = request.headers.get("Authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return protocol.authenticate(store_factory(), secrets(), _tenant(), request.headers.get("X-Component", ""),
                                     bearer, request.headers.get("X-Client-Cert-Fingerprint"),
                                     require_certificate=require_client_certificate)

    def _reporter(body: Optional[dict] = None, c=None):
        """The component a report is about: the caller, or the one behind Knovas Link it names (MVP-23)."""
        c = c if c is not None else _component()
        header = request.headers.get("X-Sub-Component", "").strip()
        field = str((body or {}).get("sub_component") or "").strip()
        if header and field and header != field:
            raise protocol.ProtocolError(400, "bad_field", "sub_component: header and field name different components")
        return protocol.resolve_reporter(store_factory(), _tenant(), c, header or field or None)

    @bp.route("/enrol", methods=["POST"])
    def enrol():
        try:
            result = protocol.enrol(store_factory(), secrets(), signer, _tenant(), _body(), _now(),
                                    issue_certificate=issue_certificates)
        except protocol.ProtocolError as exc:
            logger.info("Connector enrolment refused: %s", exc.code)
            return _error(exc)
        logger.info("Connector enrolled: component %s", result["component"])
        return jsonify({**result, "brain_url": brain_url() if brain_url else None, "brain_ca": brain_ca() if brain_ca else None}), 201

    @bp.route("/heartbeat", methods=["POST"])
    def heartbeat():
        try:
            c = _component()
            return jsonify(protocol.heartbeat(store_factory(), c, _body(), _now(), tenant=_tenant()))
        except protocol.ProtocolError as exc:
            return _error(exc)

    @bp.route("/config")
    def config():
        try:
            return jsonify({**protocol.config_of(store_factory(), _reporter()), "brain_url": brain_url() if brain_url else None})
        except protocol.ProtocolError as exc:
            return _error(exc)

    @bp.route("/package")
    def package():
        try:
            return jsonify(protocol.package_of(store_factory(), _reporter()))
        except protocol.ProtocolError as exc:
            return _error(exc)

    @bp.route("/runs", methods=["POST"])
    def runs():
        try:
            c = _component()
            body = _body()
            c = _reporter(body, c)
            run_id = protocol.report_run(store_factory(), c, body, _now())
        except protocol.ProtocolError as exc:
            return _error(exc)
        logger.info("Connector run reported: component %s", c["id"])
        return jsonify({"id": run_id}), 201

    @bp.route("/commands/<command_id>/done", methods=["POST"])
    def command_done(command_id: str):
        try:
            c = _component()
            body = _body()
            protocol.command_done(store_factory(), _reporter(body, c), command_id, body, _now())
        except protocol.ProtocolError as exc:
            return _error(exc)
        return jsonify({"ok": True})

    @bp.route("/events", methods=["POST"])
    def events():
        try:
            c = _component()
            body = _body()
            n = protocol.events(store_factory(), _reporter(body, c), body)
        except protocol.ProtocolError as exc:
            return _error(exc)
        return jsonify({"stored": n}), 201

    @bp.route("/checks", methods=["POST"])
    def checks():
        try:
            c = _component()
            body = _body()
            n = protocol.report_checks(store_factory(), _reporter(body, c), body, _now())
        except protocol.ProtocolError as exc:
            return _error(exc)
        return jsonify({"stored": n}), 201

    return bp
