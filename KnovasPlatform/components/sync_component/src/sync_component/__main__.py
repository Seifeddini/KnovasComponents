"""Start a sync component. Everything comes from the environment.

Link mode (MVP-20), when the site runs Knovas Link:

    SYNC_LINK_URL         Link's local API on the site's network, e.g. http://knovas-link:8460 (turns link mode on)
    SYNC_LINK_TOKEN_FILE  the token Link wrote for this component (default /link/token: its folder, mounted read only)
    SYNC_NAME             the component's name, as Link knows it and wrote its token for (default sync-component)
    SYNC_DATA_DIR, SYNC_INTERVAL_SECONDS, SYNC_CREDENTIAL_<KEY>, SYNC_CREDENTIAL_GITHUB, SYNC_FILES_ROOT  as below
    SYNC_PROTOCOL_V2      true: also connector protocol 2.0 (draft): the component registers the GitHub connector's
                          declaration and answers preview, act, reread and review_outcome (github_v2.py). Off by
                          default; needs Knovas Link with LINK_PROTOCOL_V2 on.

  In link mode the component does not enrol and holds no certificate: it registers with Link, sends its heartbeat
  there (commands come back with it), reads its configuration and package from Link, reports there, answers Link's
  jobs (fetch and write for GitHub sources, probe), and the runner's Brain calls go through Link's relay.
  SYNC_PLATFORM_URL and the certificate settings are not read.

Direct mode (connector protocol v1, without Link):

    SYNC_PLATFORM_URL     the Platform's protocol address, https://…/api/connectors/v1 (required)
    SYNC_ENROL_TOKEN      the one-time token from the Platform (only for the first start)
    SYNC_DATA_DIR         keeps the key, certificate, plans and state (default /data; keep it on a volume)
    SYNC_NAME             the name in the certificate (default sync-component)
    SYNC_INTERVAL_SECONDS between heartbeats (default 30)
    SYNC_CA_FILE          the CA of the Platform's TLS certificate, when it is not a public one
    SYNC_BRAIN_CA         the CA of the Brain API, when the Platform's chain does not cover it
    SYNC_CREDENTIAL_<KEY> a source's credential, by its key in upper case
    SYNC_CREDENTIAL_GITHUB the credential of every GitHub source (the same for any kind, in upper case)
                          A credential with a SYNC_CREDENTIAL_<KEY>_HOST line beside it belongs to a connector of its
                          own (the Jira connector, its own file knovas-jira.env) and is never used here (fetchers.py)
    SYNC_BRAIN_CERT, SYNC_BRAIN_KEY  the tenant's certificate, where the Platform issues none (only to test)
    SYNC_FILES_ROOT       where a file share is mounted, read only (default /sources: -v <folder>:/sources:ro)

``--once`` runs one heartbeat with its commands and ends.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
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


#: How long link mode waits between attempts to register while Link has no configuration yet.
REGISTER_RETRY_SECONDS = 15.0


def register(client, *, once: bool = False, sleep=time.sleep, declaration=None) -> bool:
    """Register with Link (a later registration replaces the earlier one). While Link has no configuration from the
    Platform yet (503) or is not up, try again, unless ``once``. ``declaration``: a callable giving the protocol 2.0
    declaration (SYNC_PROTOCOL_V2), read at each registration because it names the facts the package writes."""
    from sync_component.jobs import JOB_CAPABILITIES, JOB_KINDS, V2_JOB_KINDS
    while True:
        try:
            if declaration is not None:
                sources = client.register(CAPABILITIES + JOB_CAPABILITIES, JOB_KINDS + V2_JOB_KINDS, declaration())
            else:
                sources = client.register(CAPABILITIES + JOB_CAPABILITIES, JOB_KINDS)
            logging.info("registered with Knovas Link, sources %d", len(sources))
            return True
        except Refused as exc:
            if exc.status == 401:
                logging.error("Knovas Link refused this component's token (%s): check SYNC_NAME and the mounted "
                              "token folder.", exc.code)
                return False
            logging.warning("Knovas Link cannot register this component yet (%s)", exc.code)
        except OSError as exc:
            logging.warning("Knovas Link is not reachable at SYNC_LINK_URL (%s)", type(exc).__name__)
        if once:
            return False
        sleep(REGISTER_RETRY_SECONDS)


def protocol_v2(environ=os.environ) -> bool:
    """SYNC_PROTOCOL_V2 (1, true, yes or on): the GitHub connector's write path of protocol 2.0 besides 1.x. Reading,
    fetch and 1.x write jobs stay as they are (FEAT-2026-11-10-write-back C-5, C-Q3)."""
    return str(environ.get("SYNC_PROTOCOL_V2") or "").strip().lower() in ("1", "true", "yes", "on")


def link_main(args: argparse.Namespace, environ=os.environ) -> int:
    """Link mode: everything through Knovas Link's local API; no enrolment, no certificate."""
    from sync_component.jobs import JOB_CAPABILITIES, WRITABLE_KINDS, JobWorker
    from sync_component.link_client import LinkClient, read_token

    url = str(environ.get("SYNC_LINK_URL") or "").rstrip("/")
    if not url.startswith(("http://", "https://")):
        logging.error("SYNC_LINK_URL must be Knovas Link's local address, e.g. http://knovas-link:8460.")
        return 2
    if environ.get("SYNC_PLATFORM_URL"):
        logging.warning("SYNC_PLATFORM_URL is ignored in link mode: the component talks to Knovas Link only.")
    name = environ.get("SYNC_NAME") or "sync-component"
    try:
        token = read_token(environ.get("SYNC_LINK_TOKEN_FILE") or "/link/token")
    except (OSError, ValueError) as exc:
        logging.error("No token from Knovas Link (%s): mount Link's folder for this component at /link, read only, "
                      "or set SYNC_LINK_TOKEN_FILE.", type(exc).__name__)
        return 2
    client = LinkClient(url, name, token)
    data = Path(environ.get("SYNC_DATA_DIR") or "/data")
    v2 = protocol_v2(environ)
    engine = RunnerEngine(data / "sources", {"BRAIN_API_URL": url + "/local/v1/brain", "BRAIN_LOCAL_TOKEN": token,
                                             "BRAIN_COMPONENT_NAME": name}, keep_stream=True)
    agent = Agent(client, engine, writable_kinds=WRITABLE_KINDS, capabilities=CAPABILITIES + JOB_CAPABILITIES)
    worker = JobWorker(client, engine, protocol_v2=v2, data_dir=data)
    declaration = worker.v2.declaration if worker.v2 is not None else None
    worker.register = lambda: register(client, once=True, declaration=declaration)
    if not register(client, once=args.once, declaration=declaration):
        return 2
    logging.info("sync component %s running behind Knovas Link", VERSION)
    if args.once:
        agent.tick()
        worker.poll_once(wait=0)
        worker.close()
        return 0
    threading.Thread(target=worker.forever, name="jobs", daemon=True).start()
    agent.forever(float(environ.get("SYNC_INTERVAL_SECONDS") or 30))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.environ.get("SYNC_LINK_URL"):
        return link_main(args)
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
