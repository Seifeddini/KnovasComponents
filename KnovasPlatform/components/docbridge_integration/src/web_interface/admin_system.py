"""Systemstatus: laeuft das wirklich, was hier laufen soll?

Diese Seite existiert, weil "es sieht so aus, als ginge es" und "es geht" in
dieser Anwendung erschreckend weit auseinanderliegen koennen. Eine Suche ohne
Textausschnitt, ein Wissensgraph, der einen Typ annimmt und beim Neuladen
vergisst, eine Konsole, die vollstaendig ausgeliefert wird und trotzdem leer
aussieht -- jedes Mal war die Antwort erst nach einem Blick ins Log da.

Jede Pruefung geht denselben Weg wie die Anwendung selbst: durch denselben
Client, mit denselben Zertifikaten und mit der Assertion der angemeldeten
Person. Ein gruener Punkt hier bedeutet deshalb, dass genau dieser Aufruf mit
genau diesen Rechten funktioniert -- nicht, dass ein Port offen ist.

Nichts wird geschrieben. Der Wissensgraph wird gelesen, die Suche mit einem
einzelnen Wort abgefragt; danach ist der Mandant unveraendert.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

#: Ein einzelnes Wort. Mehrwortanfragen sind eine Eigenschaft der Suche, kein
#: Merkmal der Erreichbarkeit -- und wenn die API bei mehreren Woertern
#: stolpert, soll das hier nicht als "API nicht erreichbar" erscheinen.
PROBE_QUERY = "Vertrag"

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"


class Check(dict):
    """Ein Pruefergebnis. dict, damit das Template ohne Zusatzfilter auskommt."""

    def __init__(self, key: str, label: str, state: str, detail: str,
                 ms: int | None = None, hint: str = "") -> None:
        super().__init__(key=key, label=label, state=state, detail=detail,
                         ms=ms, hint=hint)


def _timed(fn: Callable[[], Any]) -> tuple[Any, int, Exception | None]:
    started = time.monotonic()
    try:
        return fn(), int((time.monotonic() - started) * 1000), None
    except Exception as exc:  # noqa: BLE001 - jede Pruefung faengt ihren Fehler selbst
        return None, int((time.monotonic() - started) * 1000), exc


def _short(exc: Exception, limit: int = 180) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def collect(client_factory: Callable[[], Any], *, gate=None,
            rc_client_factory: Callable[[], Any] | None = None) -> List[Check]:
    """Alle Pruefungen ausfuehren. Wirft nie."""
    checks: List[Check] = []
    try:
        client = client_factory()
    except Exception as exc:  # noqa: BLE001
        return [Check("client", "Knovas-Client", FAIL, _short(exc),
                      hint="Ohne Client ist keine weitere Pruefung moeglich.")]

    # ── Zertifikate ────────────────────────────────────────────────────────
    missing = [
        name for name, path in (
            ("Zertifikat", getattr(client, "cert_path", "")),
            ("Schluessel", getattr(client, "key_path", "")),
            ("CA", getattr(client, "ca_cert_path", "")),
        )
        if not path or not os.path.isfile(str(path))
    ]
    if getattr(client, "mtls_enabled", False):
        checks.append(Check(
            "certs", "mTLS-Zertifikate",
            OK if not missing else FAIL,
            "Alle drei Dateien vorhanden" if not missing
            else "Fehlt: " + ", ".join(missing),
            hint="" if not missing else
                 "Die Dateien liegen im Wurzelverzeichnis certs/; ./scripts/setup.sh legt die Namen an.",
        ))
    else:
        checks.append(Check("certs", "mTLS-Zertifikate", WARN, "mTLS ist nicht aktiviert",
                            hint="Ohne mTLS spricht die Plattform die ungesicherte API."))

    # ── Erreichbarkeit ─────────────────────────────────────────────────────
    healthy, ms, exc = _timed(client.health_check)
    checks.append(Check(
        "api", "Knovas-API erreichbar",
        OK if healthy else FAIL,
        f"{getattr(client, 'base_url', '?')} antwortet" if healthy
        else (_short(exc) if exc else "Antwort war nicht 200"),
        ms=ms,
        hint="" if healthy else "Ohne diesen Punkt kann keine Suche funktionieren.",
    ))
    api_up = bool(healthy)

    # ── Mandant ────────────────────────────────────────────────────────────
    tenant = str(getattr(client, "customer_id", "") or "")
    checks.append(Check(
        "tenant", "Mandant (SEMANTIX_CUSTOMER_ID)",
        OK if tenant else FAIL,
        tenant or "nicht gesetzt",
        hint="" if tenant else
             "Die Assertion jeder Person wird an den Mandanten gebunden; ohne Id startet die Plattform nicht.",
    ))

    # ── Suche ──────────────────────────────────────────────────────────────
    if not api_up:
        checks.append(Check("search", "Suche", SKIP, "Uebersprungen: API nicht erreichbar"))
    else:
        result, ms, exc = _timed(
            lambda: client.search_documents(query=PROBE_QUERY, limit=3))
        if exc is not None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            checks.append(Check(
                "search", "Suche", FAIL,
                f"HTTP {status}: {_short(exc)}" if status else _short(exc), ms=ms,
                hint="Die Antwort der API steht im Log von docbridge-web ('Response body').",
            ))
        else:
            hits = len((result or {}).get("results") or [])
            checks.append(Check(
                "search", "Suche", OK,
                f"„{PROBE_QUERY}“ liefert {hits} Treffer", ms=ms,
                hint="" if hits else
                     "Erreichbar, aber ohne Treffer — moeglicherweise ist noch nichts indexiert.",
            ))

    # ── Wissensgraph ───────────────────────────────────────────────────────
    source = (os.getenv("ONTOLOGY_SOURCE") or "fixture").strip().lower()
    if source != "graph":
        fixture = (os.getenv("ONTOLOGY_FIXTURE_PATH") or "").strip()
        writable = bool(fixture) and os.access(fixture, os.W_OK)
        checks.append(Check(
            "cortex", "Cortex-Quelle",
            OK if writable else WARN,
            f"Fixture: {fixture or 'kein Pfad gesetzt'}",
            hint="" if writable else
                 "Ohne beschreibbare Datei verschwinden neue Typen beim Neuladen. "
                 "ONTOLOGY_SOURCE=graph legt sie stattdessen in Knovas ab.",
        ))
    elif not api_up:
        checks.append(Check("cortex", "Cortex (Wissensgraph)", SKIP,
                            "Uebersprungen: API nicht erreichbar"))
    else:
        from knovas_client import KnowledgeGraphDisabled

        payload, ms, exc = _timed(client.graph_node_types)
        if isinstance(exc, KnowledgeGraphDisabled):
            checks.append(Check(
                "cortex", "Cortex (Wissensgraph)", FAIL,
                "Der Wissensgraph ist fuer diesen Mandanten nicht aktiviert", ms=ms,
                hint="Knovas muss ihn freischalten; bis dahin ist ONTOLOGY_SOURCE=fixture die Alternative.",
            ))
        elif exc is not None:
            checks.append(Check("cortex", "Cortex (Wissensgraph)", FAIL, _short(exc), ms=ms))
        else:
            anzahl = len(payload or [])
            checks.append(Check(
                "cortex", "Cortex (Wissensgraph)", OK,
                f"{anzahl} Typ(en) im Mandanten", ms=ms,
                hint="" if anzahl else "Erreichbar und leer — der erste Typ kann angelegt werden.",
            ))

    # ── Dokumentbestand ────────────────────────────────────────────────────
    # Eigener Punkt, weil die Verwaltung ihn braucht und Suche und Cortex nicht:
    # /secured/documents kann fehlen, waehrend alles andere laeuft.
    if not api_up:
        checks.append(Check("documents", "Dokumentbestand", SKIP,
                            "Uebersprungen: API nicht erreichbar"))
    else:
        payload, ms, exc = _timed(lambda: client.documents(limit=1))
        if exc is not None:
            checks.append(Check("documents", "Dokumentbestand", FAIL, _short(exc), ms=ms))
        elif (payload or {}).get("unavailable"):
            checks.append(Check(
                "documents", "Dokumentbestand", WARN,
                "GET /secured/documents antwortet 404", ms=ms,
                hint="Der Reiter Dokumente bleibt leer. Suche und Cortex sind nicht betroffen; "
                     "Knovas muss den Endpunkt fuer diesen Mandanten freischalten.",
            ))
        else:
            gesamt = int((payload or {}).get("total_count") or 0)
            checks.append(Check(
                "documents", "Dokumentbestand", OK,
                f"{gesamt} Dokument(e) sichtbar", ms=ms,
                hint="" if gesamt else
                     "Endpunkt vorhanden und leer — fuer Sie ist nichts freigegeben, "
                     "oder es wurde noch nichts eingelesen.",
            ))

    # ── Identitaet ─────────────────────────────────────────────────────────
    def _count_users():
        from identity import db
        conn = db.connect()
        try:
            return conn.execute("SELECT count(*) FROM users").fetchone()[0]
        finally:
            conn.close()

    anzahl, ms, exc = _timed(_count_users)
    checks.append(Check(
        "identity", "Identitaetsdatenbank",
        OK if exc is None else FAIL,
        f"{anzahl} Konto/Konten" if exc is None else _short(exc), ms=ms,
        hint="" if exc is None else "Ohne sie kann sich niemand anmelden.",
    ))

    # ── Broker-Schluessel ──────────────────────────────────────────────────
    key_dir = (os.getenv("PLATFORM_BROKER_KEY_DIR") or "/app/secrets/broker").strip()
    key_file = os.path.join(key_dir, "broker_ed25519.pem")
    has_key = os.path.isfile(key_file)
    checks.append(Check(
        "broker", "Broker-Signaturschluessel",
        OK if has_key else WARN,
        key_dir if has_key else f"Noch kein Schluessel in {key_dir}",
        hint="Sichern: ohne ihn kann die Plattform niemanden mehr gegenueber Knovas ausweisen."
             if has_key else "Er entsteht beim ersten Aufruf, der ihn braucht.",
    ))

    # ── RemoteController ───────────────────────────────────────────────────
    if rc_client_factory is None:
        checks.append(Check("rc", "RemoteController", SKIP, "Nicht konfiguriert",
                            hint="Ohne ihn fehlt der Reiter Ingestion."))
    else:
        def _rc_ping():
            rc = rc_client_factory()
            probe = getattr(rc, "health", None) or getattr(rc, "get_health", None)
            if probe is None:
                raise RuntimeError("Client kennt keine Health-Pruefung")
            return probe()

        _, ms, exc = _timed(_rc_ping)
        checks.append(Check(
            "rc", "RemoteController",
            OK if exc is None else WARN,
            "antwortet" if exc is None else _short(exc), ms=ms,
            hint="" if exc is None else "Betrifft nur den Reiter Ingestion.",
        ))

    return checks


def summarise(checks: List[Check]) -> Dict[str, Any]:
    """Eine Zeile fuer den Kopf der Seite."""
    states = [c["state"] for c in checks]
    if FAIL in states:
        return {"state": FAIL, "text": f"{states.count(FAIL)} Pruefung(en) fehlgeschlagen"}
    if WARN in states:
        return {"state": WARN, "text": f"{states.count(WARN)} Hinweis(e)"}
    return {"state": OK, "text": "Alle Pruefungen bestanden"}
