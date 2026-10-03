"""Fail unless the installed knovas-extract is exactly the pin.

The pin comes from the environment, as scripts/ci/check_knovas_extract_pin.sh
prints it: KNOVAS_EXTRACT_VERSION, and KNOVAS_EXTRACT_GIT_REF (empty for a
release from PyPI). CI runs this in both test jobs after their requirements
and inside both built images (``docker run -i ... python - < this file``),
so the tests and the images are held to the same two values.

Exit codes: 0 the pin is installed, 1 it is not, 2 no pin in the environment.
Prints the version and the git revision only.
"""
from __future__ import annotations

import json
import os
import sys
from importlib.metadata import PackageNotFoundError, distribution


def main() -> int:
    want = os.environ.get("KNOVAS_EXTRACT_VERSION", "").strip()
    ref = os.environ.get("KNOVAS_EXTRACT_GIT_REF", "").strip()
    if not want:
        print("KNOVAS_EXTRACT_VERSION is not set (scripts/ci/check_knovas_extract_pin.sh prints it)",
              file=sys.stderr)
        return 2
    try:
        dist = distribution("knovas-extract")
    except PackageNotFoundError:
        print("knovas-extract is not installed", file=sys.stderr)
        return 1
    import knovas_extract

    got = getattr(knovas_extract, "__version__", None)
    url = json.loads(dist.read_text("direct_url.json") or "{}")
    commit = (url.get("vcs_info") or {}).get("commit_id") or ""
    print(f"knovas-extract {got} ({'git ' + commit if commit else 'no git revision'})")
    problems = []
    if got != want:
        problems.append(f"version {got}, the pin is {want}")
    if commit != ref:
        problems.append(f"git revision {commit or 'none'}, the pin is {ref or 'none (PyPI)'}")
    for problem in problems:
        print(f"knovas-extract is not the pin: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
