"""Fail unless the installed knovas-extract is exactly the pin and reads HTML.

The pin comes from the environment, as scripts/ci/check_knovas_extract_pin.sh
prints it: KNOVAS_EXTRACT_VERSION, and KNOVAS_EXTRACT_GIT_REF (empty for a
release from PyPI). CI runs this in both test jobs after their requirements
and inside both built images (``docker run -i ... python - < this file``),
so the tests and the images are held to the same two values.

It also extracts one small HTML page: selectolax 1.0 dropped
``selectolax.parser``, which knovas-extract 0.4.0a1 imports for HTML and for
the Platform's Markdown preview. Both components cap selectolax below 1; an
install without the cap fails here instead of in a preview or a sync.

Exit codes: 0 the pin is installed and reads HTML, 1 it is not or does not,
2 no pin in the environment. Prints the version, the git revision and what
failed -- never more than the probe page's own error.
"""
from __future__ import annotations

import json
import os
import sys
from importlib.metadata import PackageNotFoundError, distribution

_HTML_PAGE = b"<!doctype html><html><body><p>Knovas HTML probe</p></body></html>"


def _html_problem() -> str | None:
    """None when the library extracts the text of a small HTML page."""
    from knovas_extract import extract

    try:
        result = extract(_HTML_PAGE, mime="text/html")
    except Exception as exc:  # any failure is the finding; its type names it
        return f"knovas-extract cannot read HTML: {type(exc).__name__}: {exc}"
    if "Knovas HTML probe" not in (result.content.text or ""):
        return "knovas-extract cannot read HTML: the page's text is missing"
    return None


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
    html = _html_problem()
    if html:
        print(html, file=sys.stderr)
    return 1 if problems or html else 0


if __name__ == "__main__":
    sys.exit(main())
