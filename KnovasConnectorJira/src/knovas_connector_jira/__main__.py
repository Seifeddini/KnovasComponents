"""The Jira connector's command line (``knovas-connector-jira``, or ``python -m knovas_connector_jira``).

    knovas-connector-jira serve [--once]
        run at the site behind Knovas Link (site.py; settings from the environment)
    knovas-connector-jira dump-stream --source <source key> --out <file.jsonl | -> [--settings <file.json>]
        one source's complete record stream as JSON Lines (dump.py)
    knovas-connector-jira declaration
        print the connector's protocol 2.0 declaration as JSON

The image's entry point is this command, ``serve`` by default, so ``docker compose run --rm <service> dump-stream ...``
runs the dump with the service's environment (the site's ``knovas-sources.env``).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="knovas-connector-jira", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run at the site behind Knovas Link")
    serve.add_argument("--once", action="store_true", help="register, one heartbeat, one job poll, then end")
    dump = sub.add_parser("dump-stream", help="one source's complete record stream as JSON Lines")
    dump.add_argument("--source", required=True, help="the source's key (its credential: SYNC_CREDENTIAL_<KEY>)")
    dump.add_argument("--out", required=True, help="the file to write, or - for standard output")
    dump.add_argument("--settings", help="a JSON file with the source's settings (site, project, fields); "
                                         "without it, the source as Knovas Link holds it")
    sub.add_parser("declaration", help="print the protocol 2.0 declaration")
    args = ap.parse_args(argv)
    # Logs go to standard error, so `dump-stream --out -` keeps standard output for the stream alone.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "dump-stream":
        from knovas_connector_jira import dump as dump_module

        return dump_module.main(args.source, args.out, args.settings)
    if args.command == "declaration":
        from knovas_connector_jira.connector import JiraConnector

        found = JiraConnector(source=lambda key: None, credential=lambda source: None).declaration()
        print(json.dumps(found, indent=2, sort_keys=True))
        return 0
    from knovas_connector_jira import site

    return site.serve(once=bool(getattr(args, "once", False)))


if __name__ == "__main__":
    sys.exit(main())
