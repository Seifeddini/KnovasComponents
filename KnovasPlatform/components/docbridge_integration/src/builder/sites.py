"""Sites: Knovas Link and the components beside it, installed from one package (connection layer MVP-23, KC-7b).

A site is one machine at a company. Knovas Link runs there and is the only process that calls Knovas; the components
ticked for the site run beside it, on an internal Docker network, and talk only to Link. The Builder makes the site,
its install package and Link's one-time enrolment token:

- ``docker-compose.yml``: Link and the ticked components, each image by digest; the internal network
  ``knovas-site`` (``internal: true``) where the components reach Link, and the egress network every service also
  joins to reach its own sources (GitHub, Microsoft 365, …); the token volume, where Link writes
  ``/link/<component>/token`` and each component mounts only its own folder; a read-only share mount when a file
  share is ticked.
- ``knovas-link.env``: the Platform's address and Link's one-time enrolment token, nothing else, read by the
  knovas-link service only.
- ``knovas-sources.env``: the sources' credentials, lines the person adds there themselves
  (``SYNC_CREDENTIAL_<KEY>=``, MVP-24), read by the source components only. No credential passes through the Platform.

**One door per site** is held by credentials, not by blocking egress: only Link holds a Knovas certificate and
token; a component holds neither, knows only Link's local address, and so can reach Knovas only through Link.

The one command at the company is ``docker compose up -d``, then the certificate step DEC-0056 decides. The site page ticks off what is done and says, in German, how to fix what is not; the «Verbindung»
card (MVP-28) shows the running site.

What is not decided here and is marked so:

- **Images by digest.** There is no image registry yet: the images are built from this repository and copied to the
  company (``docker save`` / ``docker load``), and a copied image has no digest a compose file could name. The
  digests are therefore variables (``KNOVAS_LINK_DIGEST``, ``SYNC_COMPONENT_DIGEST``) that the page calls
  placeholders until Knovas publishes images with digests.
- **Link's local address** (``LINK_LOCAL_URL``: service ``knovas-link``, port 8460) and the token files
  (``/link/<component>/token``, each component mounting ``/link/<component>`` at ``/link``) are Knovas Link's (MVP-16);
  they are constants here so that both sides change in one place.

Nothing here knows a company: names, options and addresses come from the request.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile
from typing import Any, Iterable, Mapping, Optional

from builder import components as C

#: What a site can be ticked for, and the component that serves it on the site.
OPTIONS: dict[str, dict] = {
    "github": {"label": "GitHub", "component": "sync-component"},
    "http_json": {"label": "HTTP/JSON-Schnittstelle", "component": "sync-component"},
    "share": {"label": "Dateiablage", "component": "sync-component", "share": True},
}
#: The containers a package can hold: the image the repository builds, its digest variable (a placeholder until a
#: registry publishes digests), and the component kind it enrols or is reported as.
CONTAINERS: dict[str, dict] = {
    "knovas-link": {"kind": "knovas_link", "image": "knovas-link", "digest": "KNOVAS_LINK_DIGEST",
                    "label": "Knovas Link"},
    "sync-component": {"kind": "sync_component", "image": "sync-component", "digest": "SYNC_COMPONENT_DIGEST",
                       "label": "Sync-Komponente"},
}
INTERNAL_NETWORK = "knovas-site"
EGRESS_NETWORK = "knovas-egress"
TOKEN_VOLUME = "link-tokens"
#: Link's address of the Platform and its one-time token: the knovas-link service alone reads it.
LINK_ENV_FILE = "knovas-link.env"
#: The sources' credentials: the source components alone read it.
SOURCES_ENV_FILE = "knovas-sources.env"
COMPOSE_FILE = "docker-compose.yml"
COMMAND = "docker compose up -d"
#: Where the components reach Link's local API (MVP-16): service knovas-link, port 8460, on the internal network
#: only, no published port. The sync component reads it from SYNC_LINK_URL (the Knovas Connector later from RC_LINK_URL).
LINK_LOCAL_URL = "http://knovas-link:8460"
#: The certificate step, as long as DEC-0056 is open.
CERTIFICATE_STEP = "wie in DEC-0056 entschieden; bis dahin wartet Knovas Link (waiting_for_certificate)"
#: Where a ticked share is mounted in the component, read only; the person names the folder on the machine.
SHARE_TARGET = "/sources"
SHARE_PLACEHOLDER = "${KNOVAS_SHARE_FOLDER:?Ordner der Dateiablage auf diesem Rechner eintragen}"


def check_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("Name des Standorts: 1 bis 120 Zeichen.")
    return name


def check_options(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(o not in OPTIONS for o in raw):
        raise ValueError("Komponenten: " + ", ".join(v["label"] for v in OPTIONS.values()) + ".")
    return [o for o in OPTIONS if o in raw]


def containers_of(options: Iterable[str]) -> list[str]:
    """The containers a site's package holds: Link, and the components serving the ticked options."""
    wanted = {OPTIONS[o]["component"] for o in options if o in OPTIONS}
    return ["knovas-link"] + [c for c in CONTAINERS if c != "knovas-link" and c in wanted]


def link_name(site_name: str) -> str:
    return f"Knovas Link · {site_name}"[:120]


# ------------------------------------------------------------------ credentials (MVP-24)

def credential_env(source_key: str) -> str:
    """The variable a component at the site reads a source's credential from: the sync component's own rule
    (``SYNC_CREDENTIAL_`` and the key in upper case, every other character an underscore)."""
    return "SYNC_CREDENTIAL_" + "".join(c if c.isalnum() else "_" for c in str(source_key)).upper()


def credential_line(source_key: str) -> str:
    """The line for knovas-sources.env, the value left for the person to type at the company."""
    return credential_env(source_key) + "="


# ------------------------------------------------------------------ the package

def _yaml(data: Any) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _image(container: str) -> str:
    spec = CONTAINERS[container]
    return f"{spec['image']}@${{{spec['digest']}:?Platzhalter: noch keine Registry, siehe Seite des Standorts}}"


def compose_file(site: Mapping[str, Any], options: Iterable[str]) -> str:
    """docker-compose.yml for the site: Link and the components its options need."""
    options = list(options)
    services: dict[str, dict] = {
        "knovas-link": {
            "image": _image("knovas-link"),
            "restart": "unless-stopped",
            # LINK_PLATFORM_URL and LINK_ENROL_TOKEN, for this service only; its key and certificate stay in its data
            # volume. Link alone holds what reaches Knovas.
            "env_file": [LINK_ENV_FILE],
            # Link writes /link/<component>/token for every component it serves.
            "volumes": ["link-data:/data", {"type": "volume", "source": TOKEN_VOLUME, "target": "/link"}],
            "networks": [INTERNAL_NETWORK, EGRESS_NETWORK],
        },
    }
    volumes: dict[str, dict] = {"link-data": {}, TOKEN_VOLUME: {}}
    for container in containers_of(options)[1:]:
        mounts: list = [f"{container}-data:/data",
                        {"type": "volume", "source": TOKEN_VOLUME, "target": "/link", "read_only": True,
                         "volume": {"subpath": container}}]
        # Its own folder of the token volume at /link: it reads /link/token and sees no other component's.
        environment = {"SYNC_LINK_URL": LINK_LOCAL_URL, "SYNC_DATA_DIR": "/data"}
        if any(OPTIONS[o].get("share") and OPTIONS[o]["component"] == container for o in options):
            mounts.append({"type": "bind", "source": SHARE_PLACEHOLDER, "target": SHARE_TARGET, "read_only": True})
            environment["SYNC_FILES_ROOT"] = SHARE_TARGET
        services[container] = {
            "image": _image(container),
            "restart": "unless-stopped",
            # A source's credential is a line SYNC_CREDENTIAL_<KEY>= the person adds to knovas-sources.env. No Knovas
            # certificate and no Knovas token: Knovas is reached through Link's local address only.
            "env_file": [SOURCES_ENV_FILE],
            "environment": environment,
            "volumes": mounts,
            # The egress network reaches the component's own sources (GitHub, Microsoft 365, …).
            "networks": [INTERNAL_NETWORK, EGRESS_NETWORK],
            "depends_on": ["knovas-link"],
        }
        volumes[f"{container}-data"] = {}
    head = ("# Knovas: install package of a site, made by the Brain Builder.\n"
            f"# Start: {COMMAND}\n"
            "# Images by digest: there is no image registry yet. The digest variables are placeholders until Knovas\n"
            "# publishes images with digests; the site's page in the Builder says what to do meanwhile.\n"
            f"# Networks: {INTERNAL_NETWORK} (internal) is where the components reach Knovas Link; every service is\n"
            f"# also on {EGRESS_NETWORK} to reach its own sources. One door per site is held by credentials, not by\n"
            f"# blocking egress: only knovas-link holds a Knovas certificate and token ({LINK_ENV_FILE}); a component\n"
            "# holds neither and knows only Link's local address.\n")
    return head + _yaml({
        "name": "knovas-site",
        "services": services,
        "networks": {INTERNAL_NETWORK: {"internal": True}, EGRESS_NETWORK: {}},
        "volumes": volumes,
    })


def env_file(platform_url: str, token: Optional[str]) -> str:
    """knovas-link.env: the Platform's address and Link's one-time token, nothing else; for the knovas-link service."""
    lines = ["# Knovas Link: where it enrols and with which one-time token (24 hours, used once).",
             "# Read by the knovas-link service only. Credentials of sources go into knovas-sources.env."]
    if not token:
        lines.append("# Knovas Link is already enrolled; a new token comes with «Token erneuern» in the Builder.")
    lines += [f"LINK_PLATFORM_URL={platform_url}", f"LINK_ENROL_TOKEN={token or ''}"]
    return "\n".join(lines) + "\n"


def sources_env_file() -> str:
    """knovas-sources.env: empty but for how to fill it; the person adds each source's line at the company."""
    return ("# Credentials of the sources, one line each: SYNC_CREDENTIAL_<KEY>=<value>.\n"
            "# The Builder shows the exact line for every source on this site. Read by the source components only.\n")


def package_zip(site: Mapping[str, Any], *, platform_url: str, token: Optional[str]) -> bytes:
    """The install package: docker-compose.yml, knovas-link.env and knovas-sources.env, as one zip."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in ((COMPOSE_FILE, compose_file(site, site.get("options") or [])),
                           (LINK_ENV_FILE, env_file(platform_url, token)),
                           (SOURCES_ENV_FILE, sources_env_file())):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o600 << 16           # tokens and credentials: readable by their owner only
            z.writestr(info, text)
    return out.getvalue()


# ------------------------------------------------------------------ the state of a site

def _fresh(moment: Optional[dt.datetime], now: dt.datetime, minutes: int) -> bool:
    return moment is not None and now - moment <= dt.timedelta(minutes=minutes)


def checklist(site: Mapping[str, Any], link: Optional[Mapping[str, Any]], children: list[Mapping[str, Any]],
              now: dt.datetime, limits: Mapping[str, Any]) -> list[dict]:
    """The setup checklist of the site page: each line done or not, and the German fix of one that is not."""
    minutes = int(limits.get("link_probe_minutes", 10))
    telemetry = (link or {}).get("telemetry") or {}
    enrolled = bool(link) and link.get("state") == "enrolled"
    seen = C._moment((link or {}).get("last_heartbeat_at"))
    lines = [
        {"key": "installed", "label": "Paket installiert", "ok": enrolled,
         "fix": f"«Installationspaket herunterladen», auf dem Rechner des Standorts entpacken und «{COMMAND}» "
                f"ausführen. Das Token im Paket gilt 24 Stunden und nur einmal."},
        {"key": "certificate", "label": "Zertifikat", "ok": enrolled and telemetry.get("certificate") == "present",
         "fix": CERTIFICATE_STEP[0].upper() + CERTIFICATE_STEP[1:] + "."},
        {"key": "link_online", "label": "Knovas Link online", "ok": enrolled and _fresh(seen, now, minutes),
         "fix": "Am Standort «docker compose ps»: läuft knovas-link? Erreicht der Rechner die Plattform unter "
                f"LINK_PLATFORM_URL ({LINK_ENV_FILE})? «docker compose logs knovas-link» nennt den Grund."},
    ]
    reported = {c.get("kind") for c in children if C.state_of(c, now, limits) in ("online", "degraded")}
    for container in containers_of(site.get("options") or [])[1:]:
        kind = CONTAINERS[container]["kind"]
        served = [OPTIONS[o]["label"] for o in site.get("options") or [] if OPTIONS[o]["component"] == container]
        label = f"{served[0]}-Komponente gemeldet" if len(served) == 1 else \
            f"{CONTAINERS[container]['label']} ({', '.join(served)}) gemeldet"
        lines.append({"key": f"component:{kind}", "label": label, "ok": kind in reported,
                      "fix": f"Ist «{container}» im Paket und gestartet? «docker compose logs {container}»; die "
                             f"Komponente meldet sich bei Knovas Link, sobald sie läuft."})
    lines.append({"key": "work_channel", "label": "Arbeitskanal geprüft",
                  "ok": enrolled and _fresh(C.probe_ok_at(link or {}), now, minutes),
                  "fix": "«Jetzt prüfen». Bleibt es rot: die ausgehende Verbindung des Standorts zu Knovas prüfen "
                         "(Firewall, Proxy)."})
    return lines


def site_link(store: Any, tenant: str, site_id: str) -> Optional[dict]:
    """The site's Knovas Link row, in any state."""
    return next((c for c in store.components(tenant, every=True)
                 if c.get("kind") == "knovas_link" and c.get("site_id") == site_id), None)
