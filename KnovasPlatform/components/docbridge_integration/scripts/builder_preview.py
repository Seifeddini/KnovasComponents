"""Serve the Brain Builder's screens on their own, with a stand-in admin.

For looking at the builder without the full stack (identity database, Knovas
API): the same blueprint, templates and static files, no backend. Never run
this on a server people reach -- everyone who opens it is an admin.

    python scripts/builder_preview.py [--port 5077]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE / "src"), str(HERE / "tests")]

from builder_harness import StandInUser, make_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=5077)
    args = parser.parse_args()
    app = make_app(StandInUser({"admin"}))
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
