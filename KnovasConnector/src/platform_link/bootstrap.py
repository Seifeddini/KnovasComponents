"""Start a connector under protocol v1. ``prepare`` runs before the configuration is read; ``start`` after the app exists.

    RC_PLATFORM_URL     the Platform's protocol address, https://…/api/connectors/v1 (switches this on)
    RC_ENROL_TOKEN      the one-time token from the Platform (first start, and after «Token erneuern»)
    RC_ENROL_NAME       the name in the certificate (default knovas-connector)
    RC_PLATFORM_CA      the CA of the Platform's TLS certificate, when it is not a public one
    RC_BRAIN_CA         the CA of the Brain API, when what the Platform hands over at enrolment does not cover it
    RC_LINK_INTERVAL_SECONDS  between heartbeats (default 30)

An installation that sets its own certificate and Brain address keeps them: they are only filled in when empty.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import MutableMapping, Optional

import requests

from platform_link.agent import CAPABILITIES, Agent
from platform_link.client import PlatformClient, Refused
from platform_link.identity import Enrolled, Store

log = logging.getLogger("platform_link")


def enabled(environ: MutableMapping[str, str] = os.environ) -> bool:
    return bool((environ.get("RC_PLATFORM_URL") or "").strip())


def _folder(environ: MutableMapping[str, str]) -> Path:
    return Path(environ.get("RC_SYNC_STATE_PATH") or ".rc-sync-state.json").parent / "platform-link"


def enrolled(client: PlatformClient, store: Store, token: str, name: str) -> Enrolled:
    """What an earlier start left, or a new enrolment; a token these credentials did not come from enrols again."""
    found = store.load()
    if found is not None and (not token or store.used(token)):
        return found
    if not token:
        raise RuntimeError("Not enrolled yet: set RC_ENROL_TOKEN to the one-time token from the Platform.")
    return store.save(client.enrol(token, store.request(name), CAPABILITIES), token)


def prepare(environ: MutableMapping[str, str] = os.environ, *, session=None) -> Optional[tuple[PlatformClient, Enrolled]]:
    """Enrol if needed and fill in what the connector would otherwise need by hand. None when this is not switched on."""
    if not enabled(environ):
        return None
    client = PlatformClient(environ["RC_PLATFORM_URL"].strip(), ca_file=environ.get("RC_PLATFORM_CA") or None, session=session)
    store = Store(_folder(environ))
    try:
        found = enrolled(client, store, (environ.get("RC_ENROL_TOKEN") or "").strip(),
                         environ.get("RC_ENROL_NAME") or "knovas-connector")
    except Refused as exc:
        if exc.code in ("no_secrets", "secret_unreadable"):
            raise RuntimeError(f"The Platform cannot read its stored tokens ({exc.code}): its PLATFORM_SETTINGS_KEY needs fixing; the token may be fine.") from None
        raise RuntimeError(f"Enrolment refused ({exc.code}, HTTP {exc.status}). A used or expired token needs a new one from the Platform.") from None
    except OSError as exc:
        raise RuntimeError(f"The Platform is not reachable at RC_PLATFORM_URL ({type(exc).__name__}). Check the address and its port.") from None
    client.use(found)
    environ.setdefault("SEMANTIX_CLIENT_CERT_PATH", str(found.certificate))
    environ.setdefault("SEMANTIX_CLIENT_KEY_PATH", str(found.key))
    # Without a CA from the enrolment the Brain's certificate is taken to be a public one.
    environ.setdefault("SEMANTIX_CA_CERT_PATH", environ.get("RC_BRAIN_CA") or (str(found.chain) if found.chain else requests.certs.where()))
    if found.brain_url:
        environ.setdefault("SEMANTIX_SECURE_BASE_URL", found.brain_url)
    environ.setdefault("RC_WATCH_ROOTS", "/sources")
    # The certificate the Platform knows is the one it issued; a new one comes with «Token erneuern».
    environ["SEMANTIX_CERT_AUTO_RENEW_ENABLED"] = "false"
    # Run from the Platform: nothing is sent unless a person gave the command after a dry run.
    environ["RC_SYNC_AUTO_START_CONTINUOUS"] = "false"
    return client, found


def start(linked: tuple[PlatformClient, Enrolled], environ: MutableMapping[str, str] = os.environ) -> threading.Thread:
    from platform_link.operations import ConnectorOperations

    client, found = linked
    agent = Agent(client, ConnectorOperations(_folder(environ)))
    thread = threading.Thread(target=agent.forever, args=(float(environ.get("RC_LINK_INTERVAL_SECONDS") or 30),), daemon=True,
                              name="platform-link")
    thread.start()
    log.info("linked to the Platform as component %s", found.component)
    return thread
