"""Start Knovas Link: enrol (first start, or after «Token erneuern»), then serve the local API on the site's internal
network and run the control plane and the work channel. Settings come from the environment only; see
``knovas_link.settings`` and the README.

With a command, run it in the running container instead and exit (``knovas_link.certificate``, DEC-0056):

    docker compose exec -T knovas-link python -m knovas_link certificate-request > site.csr
    docker compose exec -T knovas-link python -m knovas_link install-certificate < site.crt
"""
from __future__ import annotations

import logging
import os
import sys

import uvicorn

from knovas_link.errors import StartupError
from knovas_link.link import Link
from knovas_link.local_api import create_app
from knovas_link.settings import Settings


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        from knovas_link import certificate

        return certificate.run(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env(os.environ)
    except StartupError as exc:
        logging.getLogger("knovas_link").error("%s: check the setting and start again", exc)
        return 2
    app = create_app(Link(settings))
    # No access log: a relayed path or query may name a document (GI-DATA-02); Link logs codes and counts itself.
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, log_level="warning",
                server_header=False, proxy_headers=False, lifespan="on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
