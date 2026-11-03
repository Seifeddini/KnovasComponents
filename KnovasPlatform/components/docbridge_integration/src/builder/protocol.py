"""Connector protocol v1, the Platform's side (build plan step B13; SS-534, DEC-0040).

A component calls out to the Platform only; the Platform never calls in. The
contract as the Platform implements it, until KC-7a writes it down in full:

  POST /api/connectors/v1/enrol
       {token, csr, version, capabilities}            the one-time enrolment token (24 h)
    -> {component, control_token, certificate, certificate_chain, config, brain_url, brain_ca}
       The CSR is signed by the Brain for this Platform's tenant (/secured/sign_certificate);
       the control token is shown this once and kept as a hash.

  Every other call: Authorization: Bearer <control token>, X-Component: <component id>, and
  X-Client-Cert-Fingerprint (sha256 of the client certificate, hex), which the TLS terminator sets from the
  certificate it verified -- never passing on one the client sent. It must be the one issued at enrolment;
  without it the call is refused, unless the deployment says explicitly that no terminator checks client
  certificates (builder.components.require_client_certificate: false).

  POST /api/connectors/v1/heartbeat
       {state: online|degraded, version, lag_seconds, open_errors, capabilities}
    -> {paused, schedule, commands: [{id, command, args}]}   commands are delivered once
  GET  /api/connectors/v1/config
    -> {component, paused, schedule, brain_url, sources: [{workspace, key, kind, settings}],
        mappings: [{workspace, id, source}]}
  GET  /api/connectors/v1/package
    -> {workspaces: [{workspace, files: {path: base64}}]}    the configuration of the workspaces it serves, compiled
  POST /api/connectors/v1/runs
       {kind: dry_run|apply, status: ok|held|failed, started_at, finished_at, duration_ms,
        added, changed, retired, errors, held: [{mapping, retire: {type: n}, share}], command}
    -> {id}
  POST /api/connectors/v1/commands/<id>/done   {result: ok|failed|refused}
  POST /api/connectors/v1/events               {events: [{code, counts}]}
  POST /api/connectors/v1/checks               {checks: [{workspace, source, code, counts}]}    can it read each source?

Commands: run {kind}, pause, resume, approve_plan {plan, mapping, reason}, reject_plan {plan, mapping},
reload_config, check. Reports carry codes and counts, never document names or content (GI-DATA-02/04): what does
not fit that shape is refused, not stored.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Any, Callable, Mapping, Optional

from builder import components as C

logger = logging.getLogger(__name__)
VERSION = "v1"
COMMANDS = ("run", "pause", "resume", "approve_plan", "reject_plan", "reload_config", "check")


class ProtocolError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code


def secret_key(component_id: str, which: str) -> str:
    """The settings-registry key of a component's token hash: ``enrol`` or ``control``."""
    return f"builder.components.{component_id}.{which}"


def _int(value: Any, name: str, *, minimum: int = 0, maximum: int = 10**9) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ProtocolError(400, "bad_field", f"{name}: a whole number")
    return value


def _when(value: Any, name: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ProtocolError(400, "bad_field", f"{name}: an ISO 8601 time") from None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).isoformat()


# ------------------------------------------------------------------ enrolment

def issue_enrolment(store: Any, secrets: Any, tenant: str, *, kind: str, name: str, site: str, owner: str,
                    actor: Any, now: dt.datetime) -> tuple[str, str]:
    """A pending v1 component and its one-time enrolment token (shown once); (component id, token)."""
    if kind not in C.KINDS or C.KINDS[kind].get("planned") or C.KINDS[kind].get("adapter_only"):
        raise ProtocolError(400, "kind", "Art: Sync-Komponente, Dateiablage/DMS oder GitHub; die Datenbank ist geplant.")
    token, digest = C.new_token("kce")
    component_id = store.create_component(tenant, kind=kind, name=name, site=site, owner=owner, actor_id=actor.id,
                                          enrol_expires_at=(now + dt.timedelta(hours=C.ENROL_HOURS)).isoformat())
    if component_id is None:
        raise ProtocolError(409, "name_taken", f"Eine Komponente «{name}» gibt es schon.")
    secrets.put(secret_key(component_id, "enrol"), digest, actor)
    store.add_event(component_id, "enrol.token_issued", {})
    return component_id, token


def rotate_enrolment(store: Any, secrets: Any, tenant: str, component_id: str, actor: Any, now: dt.datetime) -> str:
    """A new enrolment token for the component (rotate): it enrols again and gets a new certificate and control
    token; the old control token stops working at once."""
    c = store.component(tenant, component_id, any_state=True)
    if c is None or c.get("transport") != "v1" or c.get("state") == "revoked":
        raise ProtocolError(404, "unknown", "Diese Komponente gibt es nicht.")
    token, digest = C.new_token("kce")
    secrets.put(secret_key(component_id, "enrol"), digest, actor)
    secrets.clear(secret_key(component_id, "control"), actor)
    store.update_component(component_id, state="pending", certificate_fingerprint=None,
                           enrol_expires_at=(now + dt.timedelta(hours=C.ENROL_HOURS)).isoformat())
    store.add_event(component_id, "token.rotated", {})
    return token


def revoke(store: Any, secrets: Any, tenant: str, component_id: str, actor: Any, now: dt.datetime) -> None:
    c = store.component(tenant, component_id, any_state=True)
    if c is None:
        raise ProtocolError(404, "unknown", "Diese Komponente gibt es nicht.")
    secrets.clear(secret_key(component_id, "enrol"), actor)
    secrets.clear(secret_key(component_id, "control"), actor)
    store.update_component(component_id, state="revoked", revoked_at=now.isoformat(), certificate_fingerprint=None)
    store.add_event(component_id, "revoked", {})


def enrol(store: Any, secrets: Any, signer: Optional[Callable[[str, str], Mapping[str, Any]]], tenant: str,
          body: Mapping[str, Any], now: dt.datetime) -> dict:
    """A component presents its enrolment token and a CSR: it gets its certificate and a control token."""
    token = str(body.get("token") or "")
    csr = str(body.get("csr") or "")
    found = None
    for c in store.pending_components(tenant):
        if C.token_matches(token, secrets.get(secret_key(c["id"], "enrol"))):
            found = c
            break
    if found is None:
        raise ProtocolError(401, "bad_token", "unknown or used enrolment token")
    expires = C._moment(found.get("enrol_expires_at"))
    if expires is None or expires < now:
        raise ProtocolError(401, "token_expired", "the enrolment token has expired; ask for a new one")
    if "BEGIN CERTIFICATE REQUEST" not in csr:
        raise ProtocolError(400, "csr", "a PEM certificate signing request")
    if signer is None:
        raise ProtocolError(503, "no_signer", "this Platform cannot have certificates signed")
    try:
        signed = signer(csr, found["name"])
    except Exception as exc:  # noqa: BLE001 - said to the component as a code; the token stays valid for another try
        logger.warning("Connector enrolment: the Brain did not sign (%s)", type(exc).__name__)
        raise ProtocolError(502, "not_signed", "the Brain did not sign the request") from None
    certificate = str(signed.get("certificate") or "")
    if "BEGIN CERTIFICATE" not in certificate:
        raise ProtocolError(502, "not_signed", "the Brain did not sign the request")
    control, digest = C.new_token("kcc")
    fingerprint = certificate_fingerprint(certificate)
    secrets.put(secret_key(found["id"], "control"), digest, None)
    secrets.clear(secret_key(found["id"], "enrol"), None)           # one time only
    store.update_component(found["id"], state="enrolled", enrolled_at=now.isoformat(), certificate_fingerprint=fingerprint,
                           version=str(body.get("version") or "")[:60],
                           capabilities=[str(x)[:60] for x in (body.get("capabilities") or [])][:30],
                           last_heartbeat_at=now.isoformat(), reported_state="online")
    store.add_event(found["id"], "enrolled", {})
    return {"component": found["id"], "control_token": control, "certificate": certificate,
            "certificate_chain": signed.get("certificate_chain"), "config": config_of(store, found)}


def certificate_fingerprint(pem: str) -> str:
    """sha256 over the certificate's DER bytes, as a TLS terminator reports it (hex, lower case)."""
    import base64
    import re

    body = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem, re.S)
    der = base64.b64decode("".join(body.group(1).split())) if body else pem.encode("utf-8")
    return hashlib.sha256(der).hexdigest()


# ------------------------------------------------------------------ authenticated calls

def authenticate(store: Any, secrets: Any, tenant: str, component_id: str, bearer: str,
                 fingerprint: Optional[str], *, require_certificate: bool = True) -> dict:
    c = store.component(tenant, component_id, any_state=True) if component_id else None
    if c is None or c.get("transport") != "v1" or c.get("state") != "enrolled":
        raise ProtocolError(401, "unknown_component", "not an enrolled component")
    if not C.token_matches(bearer, secrets.get(secret_key(c["id"], "control"))):
        raise ProtocolError(401, "bad_token", "control token refused")
    issued = c.get("certificate_fingerprint")
    if issued and not fingerprint and require_certificate:
        raise ProtocolError(401, "certificate_required", "the client certificate's fingerprint is missing")
    if issued and fingerprint and fingerprint.replace(":", "").lower() != issued:
        raise ProtocolError(401, "bad_certificate", "not the certificate issued at enrolment")
    return c


def heartbeat(store: Any, c: Mapping[str, Any], body: Mapping[str, Any], now: dt.datetime) -> dict:
    state = body.get("state") or "online"
    if state not in ("online", "degraded"):
        raise ProtocolError(400, "bad_field", "state: online or degraded")
    was = c.get("reported_state")
    store.update_component(c["id"], last_heartbeat_at=now.isoformat(), reported_state=state,
                           version=str(body.get("version") or c.get("version") or "")[:60],
                           lag_seconds=_int(body.get("lag_seconds"), "lag_seconds") if body.get("lag_seconds") is not None else None,
                           open_errors=_int(body.get("open_errors"), "open_errors", maximum=100000),
                           capabilities=[str(x)[:60] for x in (body.get("capabilities") or c.get("capabilities") or [])][:30])
    if was != state:
        store.add_event(c["id"], f"state.{state}", {})
    commands = store.deliver_commands(c["id"], now.isoformat())
    for cmd in commands:
        store.add_event(c["id"], "command.delivered", {})
    return {"paused": bool(c.get("paused")), "schedule": c.get("schedule") or {"mode": "manual"},
            "commands": [{"id": cmd["id"], "command": cmd["command"], "args": cmd.get("args") or {}} for cmd in commands]}


def config_of(store: Any, c: Mapping[str, Any]) -> dict:
    """What the component runs: the sources served through it and their mappings, in every workspace."""
    served = store.served_by(c["id"]) if hasattr(store, "served_by") else {"sources": [], "mappings": []}
    return {"component": c["id"], "paused": bool(c.get("paused")), "schedule": c.get("schedule") or {"mode": "manual"},
            "sources": served["sources"], "mappings": served["mappings"]}


def package_of(store: Any, c: Mapping[str, Any], write: Optional[Callable[[Mapping[str, Any]], Mapping[str, bytes]]] = None) -> dict:
    """The configuration package of each workspace this component serves: the workspace model stays the only record,
    so a component is never configured by hand. It holds definitions and source settings, no credential and no data."""
    import base64

    if write is None:
        from builder import package, publisher

        write = lambda model: package.write_package(publisher.for_engine(model))  # noqa: E731
    out = []
    served = store.served_by(c["id"]) if hasattr(store, "served_by") else {"sources": []}
    for workspace in dict.fromkeys(s["workspace"] for s in served["sources"]):
        model = store.model(workspace)
        if model is None:
            continue
        try:
            files = write(model)
        except ValueError:
            raise ProtocolError(409, "package_invalid", "the workspace does not compile into a package") from None
        out.append({"workspace": workspace, "files": {path: base64.b64encode(data).decode("ascii") for path, data in files.items()}})
    return {"workspaces": out}


def report_run(store: Any, c: Mapping[str, Any], body: Mapping[str, Any], now: dt.datetime) -> str:
    kind, status = body.get("kind"), body.get("status")
    if kind not in ("dry_run", "apply") or status not in ("ok", "held", "failed"):
        raise ProtocolError(400, "bad_field", "kind: dry_run|apply, status: ok|held|failed")
    held = body.get("held") or []
    if not isinstance(held, list) or len(held) > 200:
        raise ProtocolError(400, "bad_field", "held: a list")
    run_id = store.add_run(c["id"], kind=kind, status=status, started_at=_when(body.get("started_at"), "started_at"),
                           finished_at=_when(body.get("finished_at"), "finished_at") or now.isoformat(),
                           duration_ms=_int(body.get("duration_ms"), "duration_ms"),
                           added=_int(body.get("added"), "added"), changed=_int(body.get("changed"), "changed"),
                           retired=_int(body.get("retired"), "retired"), errors=_int(body.get("errors"), "errors"))
    for h in held:
        mapping = str((h or {}).get("mapping") or "")
        if not C.EVENT_CODE.match(mapping.replace("-", "_")):
            raise ProtocolError(400, "bad_field", "held.mapping: a mapping id")
        share = (h or {}).get("share")
        store.add_held(c["id"], mapping=mapping, retire=C.clean_counts((h or {}).get("retire") or {}),
                       share=float(share) if isinstance(share, (int, float)) and not isinstance(share, bool) else None)
        store.add_event(c["id"], "plan.held", {"retire": sum(C.clean_counts((h or {}).get("retire") or {}).values())})
    if body.get("command"):
        store.finish_command(c["id"], str(body["command"]), "ok" if status != "failed" else "failed", now.isoformat())
    store.add_event(c["id"], "run.failed" if status == "failed" else "run.finished",
                    {k: _int(body.get(k), k) for k in ("added", "changed", "retired", "errors")})
    return run_id


def command_done(store: Any, c: Mapping[str, Any], command_id: str, body: Mapping[str, Any], now: dt.datetime) -> None:
    result = body.get("result")
    if result not in ("ok", "failed", "refused"):
        raise ProtocolError(400, "bad_field", "result: ok|failed|refused")
    if not store.finish_command(c["id"], command_id, result, now.isoformat()):
        raise ProtocolError(404, "unknown_command", "no such open command")
    store.add_event(c["id"], "command.done", {})


def report_checks(store: Any, c: Mapping[str, Any], body: Mapping[str, Any], now: dt.datetime) -> int:
    """Whether the component can read each source it serves, the latest only. A source it does not serve is dropped;
    what does not fit a code and counts is refused (GI-DATA-02/04)."""
    items = body.get("checks")
    if not isinstance(items, list) or len(items) > 200:
        raise ProtocolError(400, "bad_field", "checks: a list of up to 200")
    served = store.served_by(c["id"]) if hasattr(store, "served_by") else {"sources": []}
    mine = {(s["workspace"], s["key"]) for s in served["sources"]}
    kept = {}
    for item in items:
        code = str((item or {}).get("code") or "")
        if not C.EVENT_CODE.match(code):
            raise ProtocolError(400, "bad_field", "code: lower case letters, digits, dots and underscores")
        workspace, source = str(item.get("workspace") or ""), str(item.get("source") or "")
        if (workspace, source) in mine:
            kept[f"{workspace}/{source}"] = {"code": code, "counts": C.clean_counts(item.get("counts")), "at": now.isoformat()}
    store.update_component(c["id"], source_checks=kept)
    return len(kept)


def events(store: Any, c: Mapping[str, Any], body: Mapping[str, Any]) -> int:
    items = body.get("events") or []
    if not isinstance(items, list) or len(items) > 100:
        raise ProtocolError(400, "bad_field", "events: a list of up to 100")
    for e in items:
        code = str((e or {}).get("code") or "")
        if not C.EVENT_CODE.match(code):
            raise ProtocolError(400, "bad_field", "code: lower case letters, digits, dots and underscores")
    for e in items:
        store.add_event(c["id"], str(e["code"]), C.clean_counts(e.get("counts")))
    return len(items)
