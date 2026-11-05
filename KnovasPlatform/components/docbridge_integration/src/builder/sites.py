"""Sites: Knovas Link and the components beside it, installed from one package (connection layer MVP-23, KC-7b).

A site is one machine at a company. Knovas Link runs there and is the only process that calls Knovas; the components
ticked for the site run beside it, on an internal Docker network, and talk only to Link. The Builder makes the site,
its install package and Link's one-time enrolment token:

- ``docker-compose.yml``: Link and the ticked components, each image by digest; the internal network
  ``knovas-site`` (``internal: true``), with Link alone also on the outbound network; the token volume, where Link
  writes ``/link/<component>/token`` and each component mounts only its own folder; a read-only share mount when a
  file share is ticked.
- ``knovas-site.env``: the Platform's address and Link's one-time enrolment token, nothing else. A source's credential
  is a line the person adds there themselves (``SYNC_CREDENTIAL_<KEY>=``, MVP-24); no credential passes through the
  Platform.

The one command at the company is ``docker compose --env-file knovas-site.env up -d``, then the certificate step
DEC-0056 decides. The site page ticks off what is done and says, in German, how to fix what is not; the «Verbindung»
card (MVP-28) shows the running site.

What is not decided here and is marked so:

- **Images by digest.** There is no image registry yet: the images are built from this repository and copied to the
  company (``docker save`` / ``docker load``), and a copied image has no digest a compose file could name. The
  digests are therefore variables (``KNOVAS_LINK_DIGEST``, ``SYNC_COMPONENT_DIGEST``) that the page calls
  placeholders until Knovas publishes images with digests.
- **Link's local address** (``LINK_LOCAL_URL``) and the components' settings in link mode follow MVP-16 and MVP-20;
  they are constants here so that both sides change in one place.
- **Egress of the source components.** Only Link is on the outbound network, as the plan decides; a component that
  reads a source outside the machine (GitHub) then reaches it only once that is decided (see the report of MVP-23).

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
OUTBOUND_NETWORK = "knovas-outbound"
TOKEN_VOLUME = "link-tokens"
ENV_FILE = "knovas-site.env"
COMPOSE_FILE = "docker-compose.yml"
COMMAND = f"docker compose --env-file {ENV_FILE} up -d"
#: Where the components reach Link's local API (MVP-16): on the internal network only, no published port.
LINK_LOCAL_URL = "http://knovas-link:8480"
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
    """The line for knovas-site.env, the value left for the person to type at the company."""
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
            "env_file": [ENV_FILE],
            "environment": {"LINK_DATA_DIR": "/data", "LINK_TOKENS_DIR": "/link"},
            "volumes": ["link-data:/data", {"type": "volume", "source": TOKEN_VOLUME, "target": "/link"}],
            "networks": [INTERNAL_NETWORK, OUTBOUND_NETWORK],
        },
    }
    volumes: dict[str, dict] = {"link-data": {}, TOKEN_VOLUME: {}}
    for container in containers_of(options)[1:]:
        mounts: list = [f"{container}-data:/data",
                        {"type": "volume", "source": TOKEN_VOLUME, "target": "/link", "read_only": True,
                         "volume": {"subpath": container}}]
        environment = {"SYNC_LINK_URL": LINK_LOCAL_URL, "SYNC_LINK_TOKEN_FILE": "/link/token", "SYNC_DATA_DIR": "/data"}
        if any(OPTIONS[o].get("share") and OPTIONS[o]["component"] == container for o in options):
            mounts.append({"type": "bind", "source": SHARE_PLACEHOLDER, "target": SHARE_TARGET, "read_only": True})
            environment["SYNC_FILES_ROOT"] = SHARE_TARGET
        services[container] = {
            "image": _image(container),
            "restart": "unless-stopped",
            # A source's credential is a line SYNC_CREDENTIAL_<KEY>= the person adds to knovas-site.env.
            "env_file": [ENV_FILE],
            "environment": environment,
            "volumes": mounts,
            "networks": [INTERNAL_NETWORK],
            "depends_on": ["knovas-link"],
        }
        volumes[f"{container}-data"] = {}
    head = ("# Knovas: install package of a site, made by the Brain Builder.\n"
            f"# Start: {COMMAND}\n"
            "# Images by digest: there is no image registry yet. The digest variables are placeholders until Knovas\n"
            "# publishes images with digests; the site's page in the Builder says what to do meanwhile.\n")
    return head + _yaml({
        "name": "knovas-site",
        "services": services,
        "networks": {INTERNAL_NETWORK: {"internal": True}, OUTBOUND_NETWORK: {}},
        "volumes": volumes,
    })


def env_file(platform_url: str, token: Optional[str]) -> str:
    """knovas-site.env: the Platform's address and Link's one-time token, nothing else."""
    lines = ["# Knovas Link: where it enrols and with which one-time token (24 hours, used once).",
             "# Credentials of sources go below as SYNC_CREDENTIAL_<KEY>=… lines; the Builder shows each one."]
    if not token:
        lines.append("# Knovas Link is already enrolled; a new token comes with «Token erneuern» in the Builder.")
    lines += [f"LINK_PLATFORM_URL={platform_url}", f"LINK_ENROL_TOKEN={token or ''}"]
    return "\n".join(lines) + "\n"


def package_zip(site: Mapping[str, Any], *, platform_url: str, token: Optional[str]) -> bytes:
    """The install package: docker-compose.yml and knovas-site.env, as one zip."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in ((COMPOSE_FILE, compose_file(site, site.get("options") or [])),
                           (ENV_FILE, env_file(platform_url, token))):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o600 << 16           # the token is a secret: readable by its owner only
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
                "LINK_PLATFORM_URL? «docker compose logs knovas-link» nennt den Grund."},
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
