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
       {state: online|degraded, version, lag_seconds, open_errors, capabilities, site?}
    -> {paused, schedule, commands: [{id, command, args, component?}]}   commands are delivered once
       ``site`` comes from Knovas Link only (connection layer MVP-19, MVP-23): {certificate: present|waiting,
       work_channel: connected|down, probe: {last_at, ok, round_trip_ms}, components: [{name, kind, version, state,
       capabilities, local_ping_ms}], sources: [{workspace, key, last_read_ok_at, last_run_status}],
       jobs: {<kind>: {<status>: n}}} -- codes, counts, versions and times. It is kept as telemetry on Link's row, and
       each component becomes a child row (transport ``link``). Link's answer also carries its components' commands,
       each naming the component (``component``: its name on the site).

  A report Link forwards for one of its components names it: header X-Sub-Component: <name on the site>, or the
  body field ``sub_component``. Runs, events, checks, command results, config and package are then the component's.
  GET  /api/connectors/v1/config
    -> {component, paused, schedule, brain_url, sources: [{workspace, key, kind, access, settings, fetch, write}],
        mappings: [{workspace, id, source}]}
       For Knovas Link: the sources and mappings of every component behind it, each with ``component`` (its name on
       the site). ``fetch`` and ``write`` are the source's switches as last published (MVP-25).
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
reload_config, check, probe (Knovas Link: go round the work channel now). Reports carry codes and counts, never document names or content (GI-DATA-02/04): what does
not fit that shape is refused, not stored.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import uuid
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
                    actor: Any, now: dt.datetime, site_id: Optional[str] = None) -> tuple[str, str]:
    """A pending v1 component and its one-time enrolment token (shown once); (component id, token). Knovas Link is
    made with its site only (``site_id``, builder.sites)."""
    known = C.KINDS.get(kind) or {}
    if not known or known.get("planned") or known.get("adapter_only") or bool(known.get("site_only")) != bool(site_id):
        raise ProtocolError(400, "kind", "Art: Sync-Komponente, Dateiablage/DMS oder GitHub; die Datenbank ist geplant. "
                                         "Knovas Link entsteht mit seinem Standort.")
    token, digest = C.new_token("kce")
    extra = {"site_id": site_id} if site_id else {}
    component_id = store.create_component(tenant, kind=kind, name=name, site=site, owner=owner, actor_id=actor.id,
                                          enrol_expires_at=(now + dt.timedelta(hours=C.ENROL_HOURS)).isoformat(),
                                          **extra)
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


def _secrets_usable(secrets: Any) -> None:
    """Without a usable key no token can be checked; saying «bad token» would send a person after the wrong thing."""
    if not getattr(secrets, "available", True):
        raise ProtocolError(503, "no_secrets", "this Platform's PLATFORM_SETTINGS_KEY is missing or not valid")


def enrol(store: Any, secrets: Any, signer: Optional[Callable[[str, str], Mapping[str, Any]]], tenant: str,
          body: Mapping[str, Any], now: dt.datetime, *, issue_certificate: bool = True) -> dict:
    """A component presents its enrolment token and a CSR: it gets its certificate and a control token.

    ``issue_certificate=False``: this deployment has no signing (builder.components.issue_certificates). The component
    gets a control token only and is given the tenant's certificate by whoever installs it: for a test, not for a
    company, because a leak then exposes the tenant and one component cannot be revoked (DEC-0040, DEC-0056)."""
    token = str(body.get("token") or "")
    csr = str(body.get("csr") or "")
    _secrets_usable(secrets)
    found = None
    unreadable = False
    for c in store.pending_components(tenant):
        kept = secrets.get(secret_key(c["id"], "enrol"))
        unreadable = unreadable or kept is None
        if C.token_matches(token, kept):
            found = c
            break
    if found is None and unreadable:
        # Not the token's fault: a stored hash does not open with this key, so the token cannot be told from a wrong one.
        raise ProtocolError(503, "secret_unreadable", "a stored enrolment token does not open with this Platform's "
                                                      "PLATFORM_SETTINGS_KEY: restore the key, or ask for a new token")
    if found is None:
        raise ProtocolError(401, "bad_token", "unknown or used enrolment token")
    expires = C._moment(found.get("enrol_expires_at"))
    if expires is None or expires < now:
        raise ProtocolError(401, "token_expired", "the enrolment token has expired; ask for a new one")
    certificate, chain, fingerprint = None, None, None
    if issue_certificate:
        if "BEGIN CERTIFICATE REQUEST" not in csr:
            raise ProtocolError(400, "csr", "a PEM certificate signing request")
        if signer is None:
            raise ProtocolError(503, "no_signer", "this Platform cannot have certificates signed")
        try:
            signed = signer(csr, found["name"])
        except Exception as exc:  # noqa: BLE001 - said to the component as a code; the token stays valid for another try
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning("Connector enrolment: the Brain did not sign (%s, HTTP %s)", type(exc).__name__, status)
            raise ProtocolError(502, "not_signed", "the Brain did not sign the request") from None
        certificate = str(signed.get("certificate") or "")
        if "BEGIN CERTIFICATE" not in certificate:
            logger.warning("Connector enrolment: the Brain answered without a certificate")
            raise ProtocolError(502, "not_signed", "the Brain did not sign the request")
        chain = signed.get("certificate_chain")
        fingerprint = certificate_fingerprint(certificate)
    control, digest = C.new_token("kcc")
    secrets.put(secret_key(found["id"], "control"), digest, None)
    secrets.clear(secret_key(found["id"], "enrol"), None)           # one time only
    store.update_component(found["id"], state="enrolled", enrolled_at=now.isoformat(), certificate_fingerprint=fingerprint,
                           version=str(body.get("version") or "")[:60],
                           capabilities=[str(x)[:60] for x in (body.get("capabilities") or [])][:30],
                           last_heartbeat_at=now.isoformat(), reported_state="online")
    store.add_event(found["id"], "enrolled", {})
    return {"component": found["id"], "control_token": control, "certificate": certificate,
            "certificate_chain": chain, "config": config_of(store, found)}


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
    _secrets_usable(secrets)
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


def heartbeat(store: Any, c: Mapping[str, Any], body: Mapping[str, Any], now: dt.datetime,
              tenant: Optional[str] = None) -> dict:
    state = body.get("state") or "online"
    if state not in ("online", "degraded"):
        raise ProtocolError(400, "bad_field", "state: online or degraded")
    site = None
    if body.get("site") is not None:
        if not C.is_link(c):
            raise ProtocolError(400, "bad_field", "site: only Knovas Link reports a site")
        site = check_site(body["site"])          # all of it checked before anything is stored
    was = c.get("reported_state")
    store.update_component(c["id"], last_heartbeat_at=now.isoformat(), reported_state=state,
                           version=str(body.get("version") or c.get("version") or "")[:60],
                           lag_seconds=_int(body.get("lag_seconds"), "lag_seconds") if body.get("lag_seconds") is not None else None,
                           open_errors=_int(body.get("open_errors"), "open_errors", maximum=100000),
                           capabilities=[str(x)[:60] for x in (body.get("capabilities") or c.get("capabilities") or [])][:30])
    if was != state:
        store.add_event(c["id"], f"state.{state}", {})
    if site is not None:
        _keep_site(store, c, site, now, tenant)
    commands = store.deliver_commands(c["id"], now.isoformat())
    for cmd in commands:
        store.add_event(c["id"], "command.delivered", {})
    out = [{"id": cmd["id"], "command": cmd["command"], "args": cmd.get("args") or {}} for cmd in commands]
    if C.is_link(c) and hasattr(store, "children"):
        # The commands of the components behind Link travel with Link's answer, each naming its component.
        for child in store.children(c["id"]):
            for cmd in store.deliver_commands(child["id"], now.isoformat()):
                store.add_event(child["id"], "command.delivered", {})
                out.append({"id": cmd["id"], "command": cmd["command"], "args": cmd.get("args") or {},
                            "component": child["local_name"]})
    return {"paused": bool(c.get("paused")), "schedule": c.get("schedule") or {"mode": "manual"}, "commands": out}


# ------------------------------------------------------------------ Knovas Link's site (MVP-19, MVP-23)

#: A component's name on its site: a container's name, never a person's or a document's.
LOCAL_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,59}$")
VERSION_TEXT = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9_.-]{0,40}(:[a-z][a-z0-9_.-]{0,40})?$")
SOURCE_KEY = re.compile(r"^[a-z][a-z0-9-]{0,59}$")
#: A component's state as Link sees it on the site.
CHILD_STATES = ("online", "degraded", "offline")
RUN_STATUSES = ("ok", "held", "failed")
MAX_CHILDREN = 30
MAX_SITE_SOURCES = 200


def _bad(field: str, what: str) -> ProtocolError:
    return ProtocolError(400, "bad_field", f"site.{field}: {what}")


def _site_int(value: Any, field: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10**9:
        raise _bad(field, "a whole number")
    return value


def _site_when(value: Any, field: str) -> Optional[str]:
    try:
        return _when(value, field)
    except ProtocolError:
        raise _bad(field, "an ISO 8601 time") from None


def check_site(raw: Any) -> dict:
    """Link's ``site`` object as the Platform keeps it: codes, counts, versions and times only. Anything shaped like a
    name or free text is refused (400); a field this Platform does not know is dropped, so a newer Link still beats."""
    if not isinstance(raw, Mapping):
        raise _bad("", "an object")
    out: dict = {}
    if raw.get("certificate") is not None:
        if raw["certificate"] not in ("present", "waiting"):
            raise _bad("certificate", "present or waiting")
        out["certificate"] = raw["certificate"]
    if raw.get("work_channel") is not None:
        if raw["work_channel"] not in ("connected", "down"):
            raise _bad("work_channel", "connected or down")
        out["work_channel"] = raw["work_channel"]
    if raw.get("probe") is not None:
        probe = raw["probe"]
        if not isinstance(probe, Mapping):
            raise _bad("probe", "an object")
        ok = probe.get("ok")
        if ok is not None and not isinstance(ok, bool):
            raise _bad("probe.ok", "true or false")
        out["probe"] = {"last_at": _site_when(probe.get("last_at"), "probe.last_at"), "ok": ok,
                        "round_trip_ms": _site_int(probe.get("round_trip_ms"), "probe.round_trip_ms")}
    components = raw.get("components") or []
    if not isinstance(components, list) or len(components) > MAX_CHILDREN:
        raise _bad("components", f"a list of up to {MAX_CHILDREN}")
    out["components"] = []
    for item in components:
        if not isinstance(item, Mapping):
            raise _bad("components", "objects")
        name, kind = str(item.get("name") or ""), str(item.get("kind") or "")
        if not LOCAL_NAME.match(name):
            raise _bad("components.name", "the component's name on the site: lower case, digits, - . _")
        if not C.EVENT_CODE.match(kind):
            raise _bad("components.kind", "a kind")
        version = str(item.get("version") or "")
        if version and not VERSION_TEXT.match(version):
            raise _bad("components.version", "a version")
        if item.get("state") not in CHILD_STATES:
            raise _bad("components.state", "online, degraded or offline")
        caps = item.get("capabilities") or []
        if not isinstance(caps, list) or len(caps) > 30 or not all(isinstance(x, str) and CAPABILITY.match(x) for x in caps):
            raise _bad("components.capabilities", "up to 30 codes such as write:github")
        out["components"].append({"name": name, "kind": kind, "version": version, "state": item["state"],
                                  "capabilities": list(caps),
                                  "local_ping_ms": _site_int(item.get("local_ping_ms"), "components.local_ping_ms")})
    sources = raw.get("sources") or []
    if not isinstance(sources, list) or len(sources) > MAX_SITE_SOURCES:
        raise _bad("sources", f"a list of up to {MAX_SITE_SOURCES}")
    out["sources"] = []
    for item in sources:
        if not isinstance(item, Mapping):
            raise _bad("sources", "objects")
        workspace, key = str(item.get("workspace") or ""), str(item.get("key") or "")
        try:
            uuid.UUID(workspace)
        except ValueError:
            raise _bad("sources.workspace", "a workspace id") from None
        if not SOURCE_KEY.match(key):
            raise _bad("sources.key", "a source key")
        status = item.get("last_run_status")
        if status is not None and status not in RUN_STATUSES:
            raise _bad("sources.last_run_status", "ok, held or failed")
        out["sources"].append({"workspace": workspace, "key": key, "last_run_status": status,
                               "last_read_ok_at": _site_when(item.get("last_read_ok_at"), "sources.last_read_ok_at")})
    jobs = raw.get("jobs") or {}
    if not isinstance(jobs, Mapping) or len(jobs) > 20:
        raise _bad("jobs", "an object of job kinds")
    out["jobs"] = {}
    for kind, outcomes in jobs.items():
        if not isinstance(kind, str) or not C.EVENT_CODE.match(kind) or not isinstance(outcomes, Mapping) or len(outcomes) > 20:
            raise _bad("jobs", "{kind: {outcome: n}}")
        out["jobs"][kind] = {}
        for outcome, n in outcomes.items():
            if not isinstance(outcome, str) or not C.EVENT_CODE.match(outcome):
                raise _bad("jobs", "outcomes are codes")
            out["jobs"][kind][outcome] = _site_int(n, "jobs") or 0
    return out


def _keep_site(store: Any, link: Mapping[str, Any], site: Mapping[str, Any], now: dt.datetime,
               tenant: Optional[str]) -> None:
    """Link's telemetry on its row; its components as child rows, made the first time Link reports them."""
    store.update_component(link["id"], telemetry={**{k: v for k, v in site.items() if k != "components"},
                                                  "components": [c["name"] for c in site["components"]],
                                                  "at": now.isoformat()})
    unknown = 0
    for item in site["components"]:
        known = C.KINDS.get(item["kind"]) or {}
        if not known or known.get("site_only") or known.get("adapter_only"):
            unknown += 1              # a kind this Platform does not know: not stored, counted
            continue
        child_id = store.upsert_child(tenant or "", parent_id=link["id"], site_id=link.get("site_id"),
                                      local_name=item["name"], kind=item["kind"],
                                      name=f"{link.get('name')} · {item['name']}"[:120])
        fields = {"version": item["version"], "capabilities": item["capabilities"],
                  "reported_state": "degraded" if item["state"] == "degraded" else "online",
                  "telemetry": {"state": item["state"], "local_ping_ms": item["local_ping_ms"], "at": now.isoformat()}}
        if item["state"] != "offline":
            fields["last_heartbeat_at"] = now.isoformat()   # seen by Link on the site just now
        store.update_component(child_id, **fields)
    if unknown:
        store.add_event(link["id"], "site.component_unknown_kind", {"components": unknown})


def resolve_reporter(store: Any, tenant: str, c: Mapping[str, Any], name: Optional[str]) -> Mapping[str, Any]:
    """The component a call is about: the caller, or -- Knovas Link forwarding for one of its components -- the
    child it names (X-Sub-Component or ``sub_component``)."""
    if not name:
        return c
    if not C.is_link(c):
        raise ProtocolError(400, "bad_field", "sub_component: only Knovas Link forwards for a component")
    if not LOCAL_NAME.match(str(name)):
        raise ProtocolError(400, "bad_field", "sub_component: the component's name on the site")
    child = next((x for x in store.children(c["id"]) if x.get("local_name") == name), None)
    if child is None:
        raise ProtocolError(409, "unknown_sub_component", "no component of that name behind this Knovas Link: "
                                                          "report it in a heartbeat first")
    return child


def _served(store: Any, c: Mapping[str, Any]) -> dict:
    """The sources and mappings a component serves. Knovas Link serves those of the components behind it, each
    named by its component (``component``: its name on the site), so Link knows which of them serves which source."""
    if not hasattr(store, "served_by"):
        return {"sources": [], "mappings": []}
    if not C.is_link(c) or not hasattr(store, "children"):
        return store.served_by(c["id"])
    sources, mappings = [], []
    for child in store.children(c["id"]):
        found = store.served_by(child["id"])
        sources += [{**s, "component": child["local_name"]} for s in found["sources"]]
        mappings += [{**m, "component": child["local_name"]} for m in found["mappings"]]
    return {"sources": sources, "mappings": mappings}


def config_of(store: Any, c: Mapping[str, Any]) -> dict:
    """What the component runs: the sources served through it and their mappings, in every workspace."""
    served = _served(store, c)
    return {"component": c["id"], "paused": bool(c.get("paused")), "schedule": c.get("schedule") or {"mode": "manual"},
            "sources": served["sources"], "mappings": served["mappings"]}


def package_of(store: Any, c: Mapping[str, Any], write: Optional[Callable[[Mapping[str, Any]], Mapping[str, bytes]]] = None) -> dict:
    """The configuration package of each workspace this component serves: the workspace model stays the only record,
    so a component is never configured by hand. It holds definitions and source settings, no credential and no data;
    not the workspace's own parts either (package.WORKSPACE_FILE: every source, the access design, the role mapping):
    a component learns the sources it serves from config_of."""
    import base64

    if write is None:
        from builder import package, publisher

        write = lambda model: package.write_package(publisher.for_engine(model), workspace_parts=False)  # noqa: E731
    out = []
    served = _served(store, c)
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
