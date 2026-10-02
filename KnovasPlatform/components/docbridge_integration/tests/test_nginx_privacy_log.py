"""The Platform's nginx access log carries no request URI (spec 4.9).

Without an `access_log` directive, nginx:alpine logs its `main` format, whose
`"$request"` is the full request line: path and query string. Platform URLs
carry document pointers (preview and open routes), and pointers name clients
and matters. Both vhosts therefore log `knovas_privacy`: time, method,
status, size and duration -- nothing a person typed or a document is called.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NGINX_DIR = Path(__file__).resolve().parents[1] / "nginx"
CONFS = ("docbridge-web-local.conf", "docbridge-web.conf")
COMPOSE = Path(__file__).resolve().parents[4] / "docker-compose.yml"

# Every nginx variable that can carry the path, the query string, a value from
# it, or a URL the browser sent along (the Referer names the page it came from).
URI_VARIABLES = frozenset({
    "request", "request_uri", "uri", "document_uri", "args", "query_string",
    "request_body", "http_referer", "request_filename", "is_args",
})
ALLOWED_VARIABLES = frozenset({
    "time_iso8601", "request_method", "status", "body_bytes_sent", "request_time",
})


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _conf(name: str) -> str:
    return _strip_comments((NGINX_DIR / name).read_text(encoding="utf-8"))


def _log_format(text: str) -> str:
    match = re.search(r"log_format\s+knovas_privacy\s+((?:'[^']*'\s*)+);", text)
    assert match, "log_format knovas_privacy is missing"
    return match.group(1)


def _server_block(text: str) -> str:
    start = text.index("server {")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise AssertionError("unbalanced server block")


@pytest.mark.parametrize("name", CONFS)
def test_privacy_format_has_no_uri_variable(name):
    variables = set(re.findall(r"\$\{?([A-Za-z0-9_]+)", _log_format(_conf(name))))
    assert not variables & URI_VARIABLES, name
    assert not {v for v in variables if v.startswith("arg_")}, name
    # An allow-list, so a variable added later is a decision, not a drift.
    assert variables == ALLOWED_VARIABLES, name


@pytest.mark.parametrize("name", CONFS)
def test_log_format_sits_in_the_http_context(name):
    # A conf.d file is included inside `http {}`, the only context that
    # accepts log_format; inside `server {}` nginx refuses to start.
    text = _conf(name)
    assert "log_format" not in _server_block(text)
    assert text.index("log_format") < text.index("server {")


@pytest.mark.parametrize("name", CONFS)
def test_server_logs_only_the_privacy_format(name):
    # In the server block, so it replaces the inherited http-level `main`
    # log instead of adding a second line per request.
    server = _server_block(_conf(name))
    directives = re.findall(r"^\s*access_log\s+([^;]+);", server, flags=re.MULTILINE)
    assert directives == ["/dev/stdout knovas_privacy"]
    assert "access_log" not in _conf(name).replace(server, "")


def test_compose_mounts_the_checked_conf():
    if not COMPOSE.is_file():
        pytest.skip("repository-root docker-compose.yml is not in this checkout")
    text = COMPOSE.read_text(encoding="utf-8")
    assert ("nginx/docbridge-web-local.conf:/etc/nginx/conf.d/default.conf" in text)
