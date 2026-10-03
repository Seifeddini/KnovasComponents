"""Every timeout in front of the Platform outlasts the admin upload's extraction.

An admin upload extracts in a child that the request thread kills at
RC_EXTRACT_TIMEOUT_SECONDS (default 120 s) and then answers "extraction
timeout". gunicorn's --timeout (DOCBRIDGE_WEB_TIMEOUT, default 180) stays
above that ceiling, or the worker dies first (769881c). Every nginx in front
-- docbridge-web-nginx (both confs) and the host-nginx template -- must wait
at least as long as gunicorn: at 120 s it gave up in the same second the
ceiling fired, and the person got a 504 instead of the message.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[2]
COMPOSE = REPO / "docker-compose.yml"
NGINX_CONFS = (
    COMPONENT / "nginx" / "docbridge-web-local.conf",
    COMPONENT / "nginx" / "docbridge-web.conf",
    REPO / "KnovasPlatform" / "deploy" / "host-nginx" / "knovas-platform.conf.example",
)
# compose escapes the dollar ($${...}); the image's sh -c does not (${...}).
GUNICORN_TIMEOUT = re.compile(r"--timeout=\$?\$\{DOCBRIDGE_WEB_TIMEOUT:-(\d+)\}")


def _compose_timeout() -> int:
    if not COMPOSE.is_file():
        pytest.skip("repository-root docker-compose.yml is not in this checkout")
    match = GUNICORN_TIMEOUT.search(COMPOSE.read_text(encoding="utf-8"))
    assert match, "docbridge-web's gunicorn has no DOCBRIDGE_WEB_TIMEOUT default"
    return int(match.group(1))


def test_gunicorn_outlasts_the_extraction_ceiling():
    from knovas_extract_upload import DEFAULT_EXTRACT_TIMEOUT_SECONDS

    assert _compose_timeout() > DEFAULT_EXTRACT_TIMEOUT_SECONDS


@pytest.mark.parametrize("conf", NGINX_CONFS, ids=lambda p: p.name)
def test_every_nginx_waits_at_least_as_long_as_gunicorn(conf):
    if not conf.is_file():
        pytest.skip(f"{conf.name} is not in this checkout")
    seconds = [int(v) for v in re.findall(r"^\s*proxy_read_timeout\s+(\d+)s\s*;",
                                          conf.read_text(encoding="utf-8"), re.M)]
    assert seconds, f"{conf.name} sets no proxy_read_timeout (nginx default: 60 s)"
    assert min(seconds) >= _compose_timeout(), (conf.name, seconds)
