"""``python -m knovas_connector_kit.conformance <module>:<factory>``: prints the report as JSON; exit 1 when a
property failed."""
from __future__ import annotations

import importlib
import json
import sys

from knovas_connector_kit.conformance.suite import run


def main(argv: list[str]) -> int:
    if len(argv) != 1 or ":" not in argv[0]:
        print("usage: python -m knovas_connector_kit.conformance <module>:<factory>", file=sys.stderr)
        return 2
    module, name = argv[0].split(":", 1)
    report = run(getattr(importlib.import_module(module), name))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
