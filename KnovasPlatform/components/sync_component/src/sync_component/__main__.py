"""Start a sync component. Everything comes from the environment:

    SYNC_PLATFORM_URL     the Platform's protocol address, https://…/api/connectors/v1 (required)
    SYNC_ENROL_TOKEN      the one-time token from the Platform (only for the first start)
    SYNC_DATA_DIR         keeps the key, certificate, plans and state (default /data; keep it on a volume)
    SYNC_NAME             the name in the certificate (default sync-component)
    SYNC_INTERVAL_SECONDS between heartbeats (default 30)
    SYNC_CA_FILE          the CA of the Platform's TLS certificate, when it is not a public one
    SYNC_BRAIN_CA         the CA of the Brain API, when the Platform's chain does not cover it
    SYNC_CREDENTIAL_<KEY> a source's credential, by its key in upper case
    SYNC_CREDENTIAL_GITHUB the credential of every GitHub source (the same for any kind, in upper case)
    SYNC_BRAIN_CERT, SYNC_BRAIN_KEY  the tenant's certificate, where the Platform issues none (only to test)
    SYNC_FILES_ROOT       where a file share is mounted, read only (default /sources: -v <folder>:/sources:ro)

``--once`` runs one heartbeat with its commands and ends.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from sync_component import VERSION
from sync_component.agent import CAPABILITIES, Agent
from sync_component.client import PlatformClient, Refused
from sync_component.identity import Credentials, Store
from sync_component.engine import RunnerEngine


def enrolled(client: PlatformClient, store: Store, token: str, name: str) -> Credentials:
    """The credentials of an earlier start, or a new enrolment with the one-time token; a token these credentials did
    not come from (the Platform's «Token erneuern») enrols again."""
    found = store.load()
    if found is not None and (not token or store.used(token)):
        return found
    if not token:
        raise SystemExit("Not enrolled yet: set SYNC_ENROL_TOKEN to the one-time token from the Platform.")
    return store.save(client.enrol(token, store.request(name), CAPABILITIES), token)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = os.environ.get("SYNC_PLATFORM_URL", "")
    if not url:
        raise SystemExit("Set SYNC_PLATFORM_URL to the Platform's protocol address.")
    data = Path(os.environ.get("SYNC_DATA_DIR") or "/data")
    store = Store(data / "identity")
    client = PlatformClient(url, ca_file=os.environ.get("SYNC_CA_FILE") or None)
    try:
        credentials = enrolled(client, store, os.environ.get("SYNC_ENROL_TOKEN", ""), os.environ.get("SYNC_NAME") or "sync-component")
    except Refused as exc:
        if exc.code in ("no_secrets", "secret_unreadable"):
            logging.error("The Platform cannot read its stored tokens (%s): its PLATFORM_SETTINGS_KEY needs fixing; the token may be fine.", exc.code)
        elif exc.code == "not_signed":
            logging.error("The Brain did not sign this component's certificate; the Platform's log says why. The token stays valid.")
        else:
            logging.error("Enrolment refused: %s (HTTP %s). A used or expired token needs a new one from the Platform.", exc.code, exc.status)
        return 2
    except OSError as exc:
        logging.error("The Platform is not reachable at SYNC_PLATFORM_URL (%s). Check the address and its port.", type(exc).__name__)
        return 2
    client.use(credentials)
    brain_url = str(client.config().get("brain_url") or "")
    if not brain_url:
        logging.error("The Platform names no Brain address.")
        return 2
    certificate = os.environ.get("SYNC_BRAIN_CERT") or (str(credentials.certificate) if credentials.certificate else "")
    key = os.environ.get("SYNC_BRAIN_KEY") or (str(credentials.key) if credentials.key else "")
    if not certificate or not key:
        logging.error("The Platform issues no certificates here: set SYNC_BRAIN_CERT and SYNC_BRAIN_KEY to the tenant's files.")
        return 2
    if not (os.access(certificate, os.R_OK) and os.access(key, os.R_OK)):
        logging.error("This component (user 10001) cannot read SYNC_BRAIN_CERT or SYNC_BRAIN_KEY: check the mount and the rights.")
        return 2
    engine = RunnerEngine(data / "sources", {
        "BRAIN_API_URL": brain_url, "BRAIN_CERT": certificate, "BRAIN_KEY": key,
        "BRAIN_CA": os.environ.get("SYNC_BRAIN_CA") or str(credentials.chain or "")})
    agent = Agent(client, engine)
    logging.info("sync component %s running as %s", VERSION, credentials.component)
    if args.once:
        agent.tick()
        return 0
    agent.forever(float(os.environ.get("SYNC_INTERVAL_SECONDS") or 30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
