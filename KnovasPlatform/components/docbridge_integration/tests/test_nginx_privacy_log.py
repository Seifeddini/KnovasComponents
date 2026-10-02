"""The Platform's access logs carry no request URI (spec 4.9).

Without an `access_log` directive, nginx:alpine logs its `main` format, whose
`"$request"` is the full request line: path and query string. Platform URLs
carry document pointers (preview and open routes), and pointers name clients
and matters. Both vhosts therefore log `knovas_privacy`: time, method,
status, size and duration -- nothing a person typed or a document is called.

The same holds behind and in front of them: gunicorn's own access log in
docker-compose.yml (its default format has the request line, path, query
string and referer) and the host nginx example in KnovasPlatform/deploy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NGINX_DIR = Path(__file__).resolve().parents[1] / "nginx"
CONFS = ("docbridge-web-local.conf", "docbridge-web.conf")
COMPOSE = Path(__file__).resolve().parents[4] / "docker-compose.yml"
HOST_NGINX = Path(__file__).resolve().parents[3] / "deploy" / "host-nginx"

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


# gunicorn atoms (gunicorn.glogging.Logger.atoms) that carry the URI, a value
# from it, or the page the browser came from; and the ones the format may use.
GUNICORN_URI_ATOMS = frozenset({"r", "U", "q", "f"})
GUNICORN_ALLOWED_ATOMS = frozenset({"t", "m", "s", "b", "B", "L", "M", "T", "D"})


def _gunicorn_format(command: str) -> str:
    match = re.search(r"--access-logformat=(?:'([^']*)'|\"([^\"]*)\")", command)
    assert match, "docbridge-web logs requests without --access-logformat"
    return match.group(1) if match.group(1) is not None else match.group(2)


def test_compose_gunicorn_access_log_has_no_uri():
    if not COMPOSE.is_file():
        pytest.skip("repository-root docker-compose.yml is not in this checkout")
    yaml = pytest.importorskip("yaml")
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    command = services["docbridge-web"]["command"]
    command = command if isinstance(command, str) else " ".join(command)
    if "--access-logfile" not in command:
        return  # no access log at all
    atoms = set(re.findall(r"%\(([^)]+)\)s", _gunicorn_format(command)))
    assert atoms, "an empty format would fall back to nothing useful"
    assert not atoms & GUNICORN_URI_ATOMS, atoms
    assert not {a for a in atoms if a.endswith(("}i", "}e"))}, "headers and environ carry the URI too"
    assert atoms <= GUNICORN_ALLOWED_ATOMS, atoms


def test_host_nginx_example_logs_only_the_privacy_format():
    site = HOST_NGINX / "knovas-platform.conf.example"
    shared = HOST_NGINX / "knovas-login-limit.conf"
    if not site.is_file() or not shared.is_file():
        pytest.skip("KnovasPlatform/deploy/host-nginx is not in this checkout")
    # The format lives in the conf.d file (http context, once per host).
    fmt = _log_format(_strip_comments(shared.read_text(encoding="utf-8")))
    variables = set(re.findall(r"\$\{?([A-Za-z0-9_]+)", fmt))
    assert variables == ALLOWED_VARIABLES
    text = _strip_comments(site.read_text(encoding="utf-8"))
    assert "log_format" not in text, "a second definition clashes on a host with two sites"
    blocks = text.split("server {")[1:]
    assert len(blocks) == 2
    for block in blocks:
        directives = re.findall(r"^\s*access_log\s+([^;]+);", block, flags=re.MULTILINE)
        assert len(directives) == 1 and directives[0].endswith(" knovas_privacy"), directives
