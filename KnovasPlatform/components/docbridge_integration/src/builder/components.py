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
    # One per site, made with its site (builder.sites), never in the component wizard (MVP-23).
    "knovas_link": {"label": "Knovas Link (Standort)", "short": "Knovas Link", "site_only": True},
}
STATES = {"online": "online", "offline": "offline", "degraded": "eingeschränkt", "paused": "pausiert",
          "pending": "wartet auf Anmeldung", "revoked": "widerrufen"}
#: How long an enrolment token is good for.
ENROL_HOURS = 24
#: Defaults of the limits the alerts use (minutes, hours); the settings registry may set others.
LIMITS = {"offline_after_minutes": 30, "lag_hours": 24, "held_plan_hours": 24,
          # Knovas Link counts as reachable only while a probe went round the work channel this recently (MVP-28).
          "link_probe_minutes": 10, "proposal_waiting_hours": 48}
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


def on_site(component: Mapping[str, Any]) -> bool:
    """Whether the component runs at the company and reports itself: enrolled under protocol v1, or behind Knovas
    Link (transport ``link``). The Platform then reads nothing of its sources itself and sends it commands."""
    return component.get("transport") in ("v1", "link")


def is_link(component: Mapping[str, Any]) -> bool:
    return component.get("kind") == "knovas_link"


def probe_ok_at(component: Mapping[str, Any]) -> Optional[dt.datetime]:
    """When Link's last probe went round the work channel, if that probe succeeded; else None."""
    probe = (component.get("telemetry") or {}).get("probe") or {}
    return _moment(probe.get("last_at")) if probe.get("ok") is True else None


def _offline_minutes(component: Mapping[str, Any], limits: Mapping[str, Any]) -> int:
    if is_link(component):
        return int(limits.get("link_probe_minutes", 10))
    return int(limits.get("offline_after_minutes", 30))


def state_of(component: Mapping[str, Any], now: dt.datetime, limits: Mapping[str, Any]) -> str:
    """online, offline, degraded, paused, pending or revoked.

    Knovas Link is online only while a probe went round the work channel within ``link_probe_minutes``: a heartbeat
    alone says it runs, not that Knovas reaches the site (MVP-28). Without its heartbeat for as long it is offline."""
    if component.get("state") == "revoked":
        return "revoked"
    if component.get("transport") == "v1" and component.get("state") == "pending":
        return "pending"
    if component.get("paused"):
        return "paused"
    seen = _moment(component.get("last_heartbeat_at"))
    if seen is None or now - seen > dt.timedelta(minutes=_offline_minutes(component, limits)):
        return "offline"
    if component.get("transport") == "link" and (component.get("telemetry") or {}).get("state") == "offline":
        return "offline"          # Knovas Link does not reach it on the site
    if is_link(component):
        probed = probe_ok_at(component)
        fresh = probed is not None and now - probed <= dt.timedelta(minutes=int(limits.get("link_probe_minutes", 10)))
        return "online" if fresh else "degraded"
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
                                         "schedule", "lag_seconds", "open_errors", "last_heartbeat_at", "settings",
                                         "site_id", "parent_id")},
        "kind_label": KINDS.get(str(component.get("kind")), {}).get("short", component.get("kind")),
        "state": state, "state_label": STATES[state], "offline_minutes": offline_for,
        "beyond_limit": state == "offline" and (seen is None or offline_for is not None and
                                                offline_for > _offline_minutes(component, limits)),
        "last_run": last, "held": len(waiting), "schedule_text": schedule_text(component.get("schedule") or {}),
    }


def registry(rows: list[dict], *, state: Optional[str] = None, kind: Optional[str] = None) -> list[dict]:
    """Filtered by state and kind; offline beyond the limit first, then by name."""
    out = [r for r in rows if (not state or r["state"] == state) and (not kind or r["kind"] == kind)]
    return sorted(out, key=lambda r: (not (r["state"] == "offline" and r["beyond_limit"]), str(r["name"]).lower()))


def _coded(component: Optional[str], name: Any, code: str, severity: str, text: str, url: Optional[str] = None) -> dict:
    """An alert with its fix from builder.codes."""
    from builder import codes

    found = codes.describe(code)
    return {"component": component, "name": name, "code": code, "severity": severity, "text": text,
            "title": found["title"], "fix": found["fix"], **({"url": url} if url else {})}


def _link_alerts(c: Mapping[str, Any], now: dt.datetime, limits: Mapping[str, Any]) -> list[dict]:
    """Knovas Link: unreachable when no probe succeeded for ``link_probe_minutes`` (counted from its enrolment until the
    first), its work channel down, or waiting for its certificate (MVP-28)."""
    cid, name = str(c["id"]), c.get("name")
    telemetry = c.get("telemetry") or {}
    limit = dt.timedelta(minutes=int(limits.get("link_probe_minutes", 10)))
    seen = _moment(c.get("last_heartbeat_at"))
    beating = seen is not None and now - seen <= limit
    out = []
    if beating and telemetry.get("certificate") == "waiting":
        out.append(_coded(cid, name, "waiting_for_certificate", "warning",
                          f"{name} wartet auf sein Zertifikat und nimmt noch keine Aufträge an."))
        return out
    since = probe_ok_at(c) or _moment(c.get("enrolled_at"))
    if since is None or now - since > limit or not beating:
        minutes = int((now - (probe_ok_at(c) or seen or now)).total_seconds() // 60) if (probe_ok_at(c) or seen) else None
        out.append(_coded(cid, name, "link_unreachable", "critical",
                          "Knovas Link nicht erreichbar" + (f": keine gelungene Probe seit {_duration(minutes)}."
                                                            if probe_ok_at(c) else ": noch keine gelungene Probe.")))
    if beating and telemetry.get("work_channel") == "down":
        out.append(_coded(cid, name, "work_channel_down", "critical", f"{name}: der Arbeitskanal ist unterbrochen."))
    return out


def _proposal_alerts(proposals: Iterable[Mapping[str, Any]], now: dt.datetime, limits: Mapping[str, Any],
                     url: Optional[str]) -> list[dict]:
    """A write proposal waiting over ``proposal_waiting_hours``; a write that ended in conflict or refusal in the last
    day. Counts only: the proposals' nodes are named on «Freigaben», not here."""
    waiting_limit = dt.timedelta(hours=int(limits.get("proposal_waiting_hours", 48)))
    waiting = conflicts = refused = 0
    for p in proposals:
        status = p.get("status")
        created = _moment(p.get("created_at"))
        decided = _moment(p.get("decided_at") or p.get("finished_at") or p.get("created_at"))
        if status == "proposed" and created is not None and now - created > waiting_limit:
            waiting += 1
        elif status in ("conflict", "refused") and decided is not None and now - decided <= dt.timedelta(days=1):
            conflicts += status == "conflict"
            refused += status == "refused"
    out = []
    if waiting:
        out.append(_coded(None, "Freigaben", "proposal_waiting", "warning",
                          f"{waiting} Änderungsvorschl{'ag wartet' if waiting == 1 else 'äge warten'} seit mehr als "
                          f"{int(waiting_limit.total_seconds() // 3600)} Stunden.", url))
    if conflicts:
        out.append(_coded(None, "Freigaben", "write_conflict", "warning",
                          f"{conflicts} angenommene{'r Vorschlag endete' if conflicts == 1 else ' Vorschläge endeten'} "
                          f"im Konflikt.", url))
    if refused:
        out.append(_coded(None, "Freigaben", "write_refused", "warning",
                          f"{refused} angenommene{'r Vorschlag wurde' if refused == 1 else ' Vorschläge wurden'} "
                          f"abgelehnt.", url))
    return out


def alerts(components: Iterable[Mapping[str, Any]], runs: Mapping[str, list], held: Mapping[str, list],
           now: dt.datetime, limits: Mapping[str, Any], *, proposals: Optional[Iterable[Mapping[str, Any]]] = None,
           freshness: Optional[Iterable[Mapping[str, Any]]] = None, proposals_url: Optional[str] = None) -> list[dict]:
    """Offline beyond the limit, two failed runs in a row, lag beyond the limit, a plan held more than a day.

    The connection layer (MVP-28) adds: Knovas Link unreachable, its work channel down or waiting for its certificate;
    a source check failing on a component behind Link; a mapping read through Link stale past its freshness window
    (``freshness``: ``{component, name, last_ok_at, window}``); a write proposal waiting or ending in conflict or
    refusal (``proposals``)."""
    out = []
    for c in components:
        cid, name = str(c["id"]), c.get("name")
        state = state_of(c, now, limits)
        if state in ("revoked", "pending", "paused"):
            continue
        if is_link(c):
            out += _link_alerts(c, now, limits)
            continue
        if c.get("transport") == "link":
            failing = [v for v in (c.get("source_checks") or {}).values() if v.get("code") not in (None, "source.ok")]
            if failing:
                out.append(_coded(cid, name, "source_check_failed", "warning",
                                  f"{name}: {len(failing)} Quelle{'' if len(failing) == 1 else 'n'} nicht lesbar."))
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
    for f in freshness or []:
        window, last = _window(f.get("window")), _moment(f.get("last_ok_at"))
        if window is not None and (last is None or now - last > window):
            out.append(_coded(f.get("component"), f.get("name"), "mapping_stale", "warning",
                              f"{f.get('name')}: " + (f"zuletzt gelesen vor {_duration(int((now - last).total_seconds() // 60))}, "
                                                       if last else "noch nie gelesen, ")
                              + f"erlaubt sind {f.get('window')}."))
    if proposals is not None:
        out += _proposal_alerts(proposals, now, limits, proposals_url)
    order = {"critical": 0, "warning": 1}
    return sorted(out, key=lambda a: (order[a["severity"]], str(a["name"])))


def _window(text: Any) -> Optional[dt.timedelta]:
    """A mapping's freshness window, like «7 d» or «12 h»."""
    match = re.match(r"^\s*(\d+)\s*([dhm])\s*$", str(text or ""))
    if not match:
        return None
    n, unit = int(match.group(1)), match.group(2)
    return dt.timedelta(days=n) if unit == "d" else dt.timedelta(hours=n) if unit == "h" else dt.timedelta(minutes=n)


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
    "fetch.http_422": "Diesen Branch oder Tag gibt es im Repository nicht: den Namen prüfen, oft «main» oder «master». Ein Ordner gehört nicht hierher; er wird beim Zuordnen gewählt.",
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
