"""Sync components: their state, schedule, alerts and tokens (build plan step B13).

A component reads a company's sources and runs its mappings: the KnovasConnector
(file share, DMS), the GitHub runner, later database and CRM connectors. It
reaches the Platform in one of two ways:

- ``v1`` -- connector protocol v1 (SS-534): the component calls out only, with
  its own client certificate and a control token it received when it enrolled
  with a one-time enrolment token. It sends heartbeats, run reports and the
  plans its deletion guard holds, and pulls its configuration and commands
  (builder.protocol, web_interface.connector_protocol).
- ``adapter`` -- what a component reports today, read by the Platform until it
  speaks v1 (builder.adapters): the KnovasConnector's /health and
  /sync/status, the Platform's own GitHub sync.

This module holds what both share: the state a registry row shows, the alerts,
the schedule, the tokens. Tokens are random and kept only as hashes, sealed
in the settings registry (builder_routes); events carry codes and counts,
never names or content (GI-DATA-02/04).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import secrets
from typing import Any, Iterable, Mapping, Optional

#: The kinds a component can be enrolled as; the database connector waits for protocol v1.
KINDS = {
    "knovas_connector": {"label": "Dokumente aus Dateiablage / DMS (KnovasConnector)", "short": "Dateiablage / DMS"},
    "github_runner": {"label": "GitHub-Repository", "short": "GitHub"},
    "sync_component": {"label": "Sync-Komponente (vor Ort)", "short": "Sync-Komponente"},
    "database_connector": {"label": "Datenbank-Connector", "short": "Datenbank", "planned": True},
    "http_json": {"label": "HTTP/JSON-Leser", "short": "HTTP/JSON", "adapter_only": True},
}
STATES = {"online": "online", "offline": "offline", "degraded": "eingeschränkt", "paused": "pausiert",
          "pending": "wartet auf Anmeldung", "revoked": "widerrufen"}
#: How long an enrolment token is good for.
ENROL_HOURS = 24
#: Defaults of the limits the alerts use (minutes, hours); the settings registry may set others.
LIMITS = {"offline_after_minutes": 30, "lag_hours": 24, "held_plan_hours": 24}
DAYS = ("mo", "di", "mi", "do", "fr", "sa", "so")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EVENT_CODE = re.compile(r"^[a-z][a-z0-9_.]{1,60}$")
#: The name of a count: a word like files or a type like Akte -- never a path, a file name or a sentence.
COUNT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,60}$")
#: Event codes the Platform writes itself; a component may send its own of the same shape.
EVENTS = ("enrol.token_issued", "enrolled", "token.rotated", "revoked", "state.online", "state.offline",
          "state.degraded", "run.requested", "run.started", "run.finished", "run.failed", "plan.held",
          "plan.approved", "plan.rejected", "paused", "resumed", "schedule.changed", "config.changed",
          "command.delivered", "command.done", "check.requested")


# ------------------------------------------------------------------ tokens

def new_token(prefix: str) -> tuple[str, str]:
    """(token, its hash): the token goes to the person or component once, the hash is kept."""
    token = f"{prefix}_{secrets.token_urlsafe(32)}"
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def token_matches(token: Optional[str], kept_hash: Optional[str]) -> bool:
    if not token or not kept_hash:
        return False
    return hmac.compare_digest(token_hash(token), str(kept_hash))


# ------------------------------------------------------------------ schedule

def check_schedule(raw: Any) -> dict:
    """A schedule the component can follow: manual, every n minutes, or every n minutes inside a window."""
    if not isinstance(raw, Mapping):
        raise ValueError("Zeitplan: Angaben erwartet.")
    mode = raw.get("mode")
    if mode == "manual":
        return {"mode": "manual"}
    try:
        every = int(raw.get("every_minutes"))
    except (TypeError, ValueError):
        raise ValueError("Zeitplan: alle wie viele Minuten?") from None
    if not 5 <= every <= 7 * 24 * 60:
        raise ValueError("Zeitplan: alle 5 Minuten bis einmal die Woche.")
    if mode == "interval":
        return {"mode": "interval", "every_minutes": every}
    if mode == "window":
        days = [d for d in DAYS if d in (raw.get("days") or [])]
        start, end = str(raw.get("from") or ""), str(raw.get("to") or "")
        if not days or not _TIME.match(start) or not _TIME.match(end) or start == end:
            raise ValueError("Zeitfenster: Tage und von–bis wie 22:00–05:00.")
        return {"mode": "window", "every_minutes": every, "days": days, "from": start, "to": end}
    raise ValueError("Zeitplan: manuell, regelmäßig oder in einem Zeitfenster.")


def schedule_text(schedule: Mapping[str, Any]) -> str:
    mode = (schedule or {}).get("mode", "manual")
    if mode == "interval":
        return f"alle {schedule['every_minutes']} Minuten"
    if mode == "window":
        days = ", ".join(d.capitalize() for d in schedule.get("days") or [])
        return f"{days} {schedule['from']}–{schedule['to']}, alle {schedule['every_minutes']} Minuten"
    return "manuell"


# ------------------------------------------------------------------ state

def _moment(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        t = value
    else:
        try:
            t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def state_of(component: Mapping[str, Any], now: dt.datetime, limits: Mapping[str, Any]) -> str:
    """online, offline, degraded, paused, pending or revoked."""
    if component.get("state") == "revoked":
        return "revoked"
    if component.get("transport") == "v1" and component.get("state") == "pending":
        return "pending"
    if component.get("paused"):
        return "paused"
    seen = _moment(component.get("last_heartbeat_at"))
    if seen is None or now - seen > dt.timedelta(minutes=int(limits.get("offline_after_minutes", 30))):
        return "offline"
    if component.get("reported_state") == "degraded" or int(component.get("open_errors") or 0) > 0:
        return "degraded"
    return "online"


def row(component: Mapping[str, Any], runs: list[Mapping[str, Any]], held: list[Mapping[str, Any]],
        now: dt.datetime, limits: Mapping[str, Any]) -> dict:
    """What the registry shows of one component."""
    state = state_of(component, now, limits)
    seen = _moment(component.get("last_heartbeat_at"))
    last = runs[0] if runs else None
    waiting = [h for h in held if h.get("state") == "held"]
    offline_for = int((now - seen).total_seconds() // 60) if seen and state == "offline" else None
    return {
        **{k: component.get(k) for k in ("id", "name", "kind", "site", "owner", "version", "transport", "paused",
                                         "schedule", "lag_seconds", "open_errors", "last_heartbeat_at", "settings")},
        "kind_label": KINDS.get(str(component.get("kind")), {}).get("short", component.get("kind")),
        "state": state, "state_label": STATES[state], "offline_minutes": offline_for,
        "beyond_limit": state == "offline" and (seen is None or offline_for is not None and
                                                offline_for > int(limits.get("offline_after_minutes", 30))),
        "last_run": last, "held": len(waiting), "schedule_text": schedule_text(component.get("schedule") or {}),
    }


def registry(rows: list[dict], *, state: Optional[str] = None, kind: Optional[str] = None) -> list[dict]:
    """Filtered by state and kind; offline beyond the limit first, then by name."""
    out = [r for r in rows if (not state or r["state"] == state) and (not kind or r["kind"] == kind)]
    return sorted(out, key=lambda r: (not (r["state"] == "offline" and r["beyond_limit"]), str(r["name"]).lower()))


def alerts(components: Iterable[Mapping[str, Any]], runs: Mapping[str, list], held: Mapping[str, list],
           now: dt.datetime, limits: Mapping[str, Any]) -> list[dict]:
    """Offline beyond the limit, two failed runs in a row, lag beyond the limit, a plan held more than a day."""
    out = []
    for c in components:
        cid, name = str(c["id"]), c.get("name")
        state = state_of(c, now, limits)
        if state in ("revoked", "pending", "paused"):
            continue
        seen = _moment(c.get("last_heartbeat_at"))
        if state == "offline":
            minutes = int((now - seen).total_seconds() // 60) if seen else None
            out.append({"component": cid, "name": name, "code": "offline", "severity": "critical",
                        "text": f"{name} ist offline" + (f" seit {_duration(minutes)}" if minutes is not None
                                                          else ", noch nie gemeldet") + "."})
        mine = runs.get(cid) or []
        if len(mine) >= 2 and all(r.get("status") == "failed" for r in mine[:2]):
            out.append({"component": cid, "name": name, "code": "failed_twice", "severity": "critical",
                        "text": f"{name}: die letzten zwei Läufe sind fehlgeschlagen."})
        lag = c.get("lag_seconds")
        if lag is not None and int(lag) > int(limits.get("lag_hours", 24)) * 3600:
            out.append({"component": cid, "name": name, "code": "lag", "severity": "warning",
                        "text": f"{name} liegt {_duration(int(lag) // 60)} hinter der Quelle."})
        for h in held.get(cid) or []:
            at = _moment(h.get("held_at"))
            if h.get("state") == "held" and at and now - at > dt.timedelta(hours=int(limits.get("held_plan_hours", 24))):
                out.append({"component": cid, "name": name, "code": "held_plan", "severity": "warning",
                            "text": f"{name}: der Plan der Zuordnung {h.get('mapping')} wartet seit "
                                    f"{_duration(int((now - at).total_seconds() // 60))} auf Freigabe."})
    order = {"critical": 0, "warning": 1}
    return sorted(out, key=lambda a: (order[a["severity"]], str(a["name"])))


#: What a component's report about a source means, for the person who has to fix it. Codes, because the component
#: reports no address and no name; the sentence names only what to do.
CHECK_TEXTS = {
    "source.ok": "Liest die Quelle.",
    "source.kind_unknown": "Diese Version der Komponente kennt diese Art von Quelle nicht: das Abbild aktualisieren.",
    "source.bad_key": "Der Schlüssel der Quelle ist nicht gültig.",
    "source.write_unsupported": "Diese Komponente kann in diese Quelle nicht schreiben: die Quelle auf «Nur lesen» stellen, oder das Abbild aktualisieren.",
    "fetch.not_https": "Die Adresse muss mit https:// beginnen.",
    "fetch.bad_repository": "Repository oder Branch ist nicht gültig.",
    "fetch.bad_folder": "Der Ordner darf kein «..» enthalten.",
    "fetch.folder_missing": "Den Ordner gibt es nicht: die Ablage beim Start einhängen (-v <Ordner>:/sources:ro) und den Ordner der Quelle prüfen.",
    "fetch.folder_unreadable": "Die Komponente darf den Ordner nicht lesen: die Rechte für Benutzer 10001 prüfen.",
    "fetch.folder_empty": "Der Ordner ist leer. Ist die Ablage eingehängt? Eine leere Ablage wird nicht gelesen, damit nichts stillgelegt wird.",
    "fetch.http_401": "Der Zugang wurde abgewiesen: das Token prüfen (-e SYNC_CREDENTIAL_…).",
    "fetch.http_403": "Der Zugang reicht nicht: dem Token Leserechte geben.",
    "fetch.http_404": "Nicht gefunden, oder der Zugang fehlt: Adresse oder Repository und Branch prüfen, das Token setzen (-e SYNC_CREDENTIAL_…).",
    "fetch.connectionerror": "Keine Verbindung zur Quelle: Netzwerk oder Firewall der Firma prüfen.",
    "fetch.timeout": "Die Quelle antwortet nicht rechtzeitig.",
    "fetch.sslerror": "Das TLS-Zertifikat der Quelle wird nicht anerkannt.",
    "fetch.too_large": "Die Antwort der Quelle ist zu gross (SYNC_FETCH_MAX_BYTES).",
    "fetch.not_json": "Die Antwort der Quelle ist kein JSON.",
    "documents.no_access_choice": "Bei der Quelle fehlen die Zugriffsgruppen: Gruppen eintragen, oder «alle» für den ganzen Mandanten.",
    "documents.bad_config": "Die Einstellungen der Quelle ergeben keinen gültigen Lauf: Ordner und Zugriffsgruppen prüfen.",
    "documents.mappings_not_run": "Zuordnungen führt diese Komponente nicht aus; sie überträgt Dokumente. Für Zuordnungen die Quelle auf eine Sync-Komponente legen.",
}


def check_rows(component: Mapping[str, Any], sources: Iterable[Mapping[str, Any]]) -> list[dict]:
    """The component's sources with its latest report on each, for its page."""
    found = component.get("source_checks") or {}
    rows = []
    for s in sources:
        item = found.get(f"{s.get('workspace')}/{s.get('key')}") or {}
        code = item.get("code")
        rows.append({"name": s.get("name"), "workspace_name": s.get("workspace_name"), "code": code, "ok": code == "source.ok",
                     "counts": item.get("counts") or {}, "at": item.get("at"),
                     "text": CHECK_TEXTS.get(str(code), f"Nicht lesbar ({code}).") if code else "Noch nicht geprüft."})
    return rows


def _duration(minutes: Optional[int]) -> str:
    if minutes is None:
        return "–"
    if minutes < 120:
        return f"{minutes} Minuten"
    if minutes < 48 * 60:
        return f"{minutes // 60} Stunden"
    return f"{minutes // (24 * 60)} Tagen"


def clean_counts(raw: Any) -> dict:
    """An event's counts: names to whole numbers only -- never a document name or a value."""
    if not isinstance(raw, Mapping):
        return {}
    out = {}
    for k, v in list(raw.items())[:20]:
        if isinstance(k, str) and COUNT_KEY.match(k) and isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    return out
