"""«Betrieb» › «Verbindung»: one card per site (connection layer MVP-28, KC-7b).

The card says whether a site works and what to do when it does not: Knovas Link (state, version, certificate, work
channel, last probe and its round trip), each component behind it, each source read there (last good read, last run,
how many mappings read it), the last 24 hours of jobs from the tenant (GET /secured/connector/jobs/stats), the open
write proposals and the alerts with their fix.

State is derived honestly: a site is green only when a probe went round the work channel within the last 10 minutes
(``link_probe_minutes``) and nothing else is wrong; a value the Builder does not have is «unbekannt», never green. The
card holds codes, counts, versions and times; source names come from the workspace model when the page renders and
are not stored.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Mapping, Optional

from builder import components as C

STATES = {"ok": "Alles in Ordnung", "warning": "Eingeschränkt", "critical": "Gestört", "unknown": "Unbekannt",
          "pending": "Wartet auf die Installation"}
UNKNOWN = "unbekannt"
CERTIFICATE = {"present": "Zertifikat vorhanden", "waiting": "wartet auf das Zertifikat (DEC-0056)"}
WORK_CHANNEL = {"connected": "verbunden", "down": "unterbrochen"}
#: What a capability means on the card; one not listed is shown as it is.
CAPABILITIES = {"dry_run": "lesen", "apply": "lesen", "fetch": "Original", "write": "Vorschlag schreiben",
                "documents": "Dokumente"}
#: How often the page reads the cards again.
REFRESH_SECONDS = 30
WINDOW_HOURS = 24


def ago(moment: Optional[dt.datetime], now: dt.datetime) -> Optional[str]:
    if moment is None:
        return None
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 90:
        return f"vor {seconds} s"
    if seconds < 2 * 3600:
        return f"vor {seconds // 60} Min."
    if seconds < 48 * 3600:
        return f"vor {seconds // 3600} Std."
    return f"vor {seconds // 86400} Tagen"


def seconds_text(ms: Any) -> Optional[str]:
    if ms is None or isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    return f"{ms / 1000:.1f} s".replace(".", ",")


def ms_text(ms: Any) -> Optional[str]:
    if ms is None or isinstance(ms, bool) or not isinstance(ms, (int, float)):
        return None
    return f"{int(ms)} ms" if ms < 1000 else seconds_text(ms)


def capabilities_text(caps: Iterable[str]) -> str:
    out: list[str] = []
    for cap in caps or []:
        word = CAPABILITIES.get(str(cap).split(":", 1)[0], str(cap))
        if word not in out:
            out.append(word)
    return ", ".join(out) or "–"


def stat_counts(stats: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """{(source, kind, outcome): count} from GET /secured/connector/jobs/stats, or None when there are none to read.

    The route answers ``{since, truncated, counts: [{source, kind, outcome, count}], duration_ms: {<kind>: {count,
    median, p95}}, queue: {depth, in_flight, oldest_queued_age_s}, probe: {count, last_round_trip_ms, last_at,
    median_round_trip_ms}, stale_found?}``; ``stale_found`` is ``{<source key>: n}`` and may be absent."""
    if not isinstance(stats, Mapping) or not isinstance(stats.get("counts"), list):
        return None
    out: dict = {}
    for r in stats["counts"]:
        n = r.get("count") if isinstance(r, Mapping) else None
        if isinstance(n, int) and not isinstance(n, bool):
            key = (str(r.get("source")), str(r.get("kind")), str(r.get("outcome")))
            out[key] = out.get(key, 0) + n
    return out


def last_day(stats: Optional[Mapping[str, Any]], keys: set, proposals: Optional[list], now: dt.datetime) -> dict:
    """The card's «Letzte 24 h» line for the site's sources."""
    counts = stat_counts(stats)
    out: dict = {"known": counts is not None}
    if counts is not None:
        def total(kind: str, outcomes: Iterable[str]) -> int:
            return sum(n for (s, k, o), n in counts.items() if s in keys and k == kind and o in outcomes)

        out["fetched"] = total("fetch", ("ok",))
        out["unreachable"] = total("fetch", ("unreachable", "failed", "refused"))
        out["pull_requests"] = total("write", ("ok",))
        out["truncated"] = bool((stats or {}).get("truncated"))
    # Stale documents found by fetches, per source: the site's sources summed; absent means unknown.
    stale = (stats or {}).get("stale_found") if isinstance(stats, Mapping) else None
    out["stale"] = sum(n for s, n in stale.items() if s in keys and isinstance(n, int) and not isinstance(n, bool)) \
        if isinstance(stale, Mapping) else None
    if proposals is not None:
        since = now - dt.timedelta(hours=WINDOW_HOURS)
        mine = [p for p in proposals if str(p.get("source")) in keys]
        out["proposals"] = sum(1 for p in mine if (C._moment(p.get("created_at")) or now) >= since)
        out["open"] = sum(1 for p in mine if p.get("status") == "proposed")
    return out


def site_card(site: Mapping[str, Any], link: Optional[Mapping[str, Any]], children: list[Mapping[str, Any]], *,
              sources: list[Mapping[str, Any]], stats: Optional[Mapping[str, Any]], proposals: Optional[list],
              now: dt.datetime, limits: Mapping[str, Any], proposals_url: Optional[str] = None) -> dict:
    """One site's card. ``sources``: the sources read through the site's components, each with ``workspace``, ``key``,
    ``name`` and ``freshness`` (the shortest window of the mappings reading it) and ``mappings`` (how many)."""
    telemetry = (link or {}).get("telemetry") or {}
    probe = telemetry.get("probe") or {}
    probe_at = C._moment(probe.get("last_at"))
    link_state = C.state_of(link, now, limits) if link else "pending"
    reported = {(s.get("workspace"), s.get("key")): s for s in telemetry.get("sources") or []}
    source_rows, freshness = [], []
    for s in sources:
        seen = reported.get((s.get("workspace"), s.get("key"))) or {}
        last_ok = C._moment(seen.get("last_read_ok_at"))
        source_rows.append({"name": s.get("name"), "key": s.get("key"), "mappings": s.get("mappings", 0),
                            "last_read_ok": ago(last_ok, now) or UNKNOWN,
                            "last_run": {"ok": "ok", "held": "zurückgehalten", "failed": "fehlgeschlagen"}.get(
                                str(seen.get("last_run_status")), UNKNOWN)})
        if s.get("freshness") and seen:
            freshness.append({"component": s.get("component"), "name": f"Quelle «{s.get('name')}»",
                              "last_ok_at": seen.get("last_read_ok_at"), "window": s.get("freshness")})
    keys = {str(s.get("key")) for s in sources}
    mine = [p for p in proposals or [] if str(p.get("source")) in keys] if proposals is not None else None
    alerts = C.alerts([c for c in [link, *children] if c], {}, {}, now, limits, proposals=mine, freshness=freshness,
                      proposals_url=proposals_url)
    rows = []
    for c in children:
        state = C.state_of(c, now, limits)
        rows.append({"id": c["id"], "name": c.get("local_name") or c.get("name"), "kind": c.get("kind"),
                     "kind_label": C.KINDS.get(str(c.get("kind")), {}).get("short", c.get("kind")),
                     "state": state, "state_label": C.STATES[state], "version": c.get("version") or UNKNOWN,
                     "can": capabilities_text(c.get("capabilities") or []),
                     "ping": ms_text((c.get("telemetry") or {}).get("local_ping_ms")) or UNKNOWN})
    green = (link_state == "online" and telemetry.get("certificate") == "present"
             and telemetry.get("work_channel") == "connected"
             and all(r["state"] == "online" for r in rows) and not alerts)
    if link is None or link.get("state") == "pending":
        state = "pending"
    elif any(a["severity"] == "critical" for a in alerts):
        state = "critical"
    elif green:
        state = "ok"
    else:
        state = "warning"
    return {
        "id": site.get("id"), "name": site.get("name"), "state": state, "state_label": STATES[state],
        "link": None if link is None else {
            "id": link["id"], "state": link_state, "state_label": C.STATES[link_state],
            "version": link.get("version") or UNKNOWN,
            "certificate": CERTIFICATE.get(str(telemetry.get("certificate")), f"Zertifikat {UNKNOWN}"),
            "work_channel": WORK_CHANNEL.get(str(telemetry.get("work_channel")), UNKNOWN),
            "probe": ("geprüft " + ago(probe_at, now)) if probe_at and probe.get("ok") is True else
                     (("Probe fehlgeschlagen " + ago(probe_at, now)) if probe_at else f"Probe {UNKNOWN}"),
            "round_trip": seconds_text(probe.get("round_trip_ms")),
            "seen": ago(C._moment(link.get("last_heartbeat_at")), now) or UNKNOWN},
        "components": rows, "sources": source_rows,
        "last_day": last_day(stats, keys, proposals, now), "alerts": alerts,
    }
