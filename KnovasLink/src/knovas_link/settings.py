"""Link's settings: from the environment only, never from a file in the image.

    LINK_PLATFORM_URL   the Platform's protocol address, https://…/api/connectors/v1 (required)
    LINK_ENROL_TOKEN    the site's one-time token: for the first start, and again after «Token erneuern»
    LINK_PLATFORM_CA    the CA of the Platform's TLS certificate, when it is not a public one (optional)

Optional, with defaults that the install package keeps:

    LINK_DATA_DIR       key, certificate and state (default /data, a volume; the key is readable by Link's user only)
    LINK_TOKENS_DIR     the shared token volume: /link/<component>/token (default /link)
    LINK_HOST, LINK_PORT  where the local API listens (default 0.0.0.0 and 8460, on the site's internal network only)
    LINK_NAME           the name in the certificate request (default knovas-link)
    LINK_HEARTBEAT_SECONDS between heartbeats to the Platform (default 30)
    LINK_PROBE_SECONDS  between probes of the work channel (default 300)
    LINK_BRAIN_CA       the CA of the Brain's TLS certificate, when the Platform's enrolment answer has none
    LINK_TENANT         the site's tenant (its client id), when the Platform's enrolment answer has none: install-certificate
                        takes only a certificate whose OU Semantix-Customer-<id> names it
    LINK_CLIENT_CA      a file with Knovas's client CA (PEM), when the Platform's enrolment answer has none:
                        install-certificate takes only a certificate it signed

Connector protocol 2.0 (draft, BET-2026-11-write-back WB-10/WB-11; KnovasPlatform/docs/connector-protocol-v2.md):

    LINK_PROTOCOL_V2    true: Link also speaks protocol v2 - it declares its site to the Brain, pins the Brain's
                        permit keys, checks permits, keeps the used-permit store and the write log, and routes the
                        jobs preview, act, reread, review_outcome and unstage. Off by default: then Link is the 1.x
                        Link it was, and it refuses a v2 job as unsupported (protocol_unsupported).

Only to test on one's own machine, never for a company (DEC-0056; see README):

    LINK_TEST_ONLY_SHARED_CERTIFICATE=true with LINK_BRAIN_CERT and LINK_BRAIN_KEY: the tenant's own certificate,
    used for the Brain while the Platform issues none. Ignored without the switch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from knovas_link.errors import StartupError

#: The component names Link writes a token for even before the Platform's configuration names any.
DEFAULT_COMPONENTS = ("sync-component", "knovas-connector")


def _flag(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _number(environ: Mapping[str, str], name: str, default: float, *, low: float, high: float) -> float:
    raw = environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except ValueError:
        raise StartupError(f"settings.{name.lower()}") from None
    if not low <= value <= high:
        raise StartupError(f"settings.{name.lower()}")
    return value


@dataclass(frozen=True)
class Settings:
    platform_url: str
    enrol_token: str = ""
    platform_ca: Optional[str] = None
    data_dir: Path = Path("/data")
    tokens_dir: Path = Path("/link")
    host: str = "0.0.0.0"
    port: int = 8460
    name: str = "knovas-link"
    heartbeat_seconds: float = 30.0
    probe_seconds: float = 300.0
    brain_ca: Optional[str] = None
    test_only_shared_certificate: bool = False
    brain_cert: Optional[str] = None
    brain_key: Optional[str] = None
    protocol_v2: bool = False
    default_components: tuple = field(default=DEFAULT_COMPONENTS)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Settings":
        url = str(environ.get("LINK_PLATFORM_URL") or "").strip()
        if not url.startswith(("https://", "http://")):
            raise StartupError("settings.link_platform_url")
        return cls(
            platform_url=url,
            enrol_token=str(environ.get("LINK_ENROL_TOKEN") or "").strip(),
            platform_ca=environ.get("LINK_PLATFORM_CA") or None,
            data_dir=Path(environ.get("LINK_DATA_DIR") or "/data"),
            tokens_dir=Path(environ.get("LINK_TOKENS_DIR") or "/link"),
            host=environ.get("LINK_HOST") or "0.0.0.0",
            port=int(_number(environ, "LINK_PORT", 8460, low=1, high=65535)),
            name=(environ.get("LINK_NAME") or "knovas-link")[:64],
            heartbeat_seconds=_number(environ, "LINK_HEARTBEAT_SECONDS", 30, low=1, high=3600),
            probe_seconds=_number(environ, "LINK_PROBE_SECONDS", 300, low=10, high=86400),
            brain_ca=environ.get("LINK_BRAIN_CA") or None,
            test_only_shared_certificate=_flag(environ.get("LINK_TEST_ONLY_SHARED_CERTIFICATE")),
            brain_cert=environ.get("LINK_BRAIN_CERT") or None,
            brain_key=environ.get("LINK_BRAIN_KEY") or None,
            protocol_v2=_flag(environ.get("LINK_PROTOCOL_V2")),
        )
