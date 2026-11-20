"""No log line of the Platform carries customer text (maintenance, 2026-11-19).

The founder's rule: no customer text in the logs. Customer text is a question
or search query; document, passage, snippet, chunk, answer or graph text; an
HTTP response or error body; an exception message (``str(exc)``, ``%s`` with
the exception, ``f"{exc}"``, a ``logger.exception`` or ``exc_info`` traceback,
whose last line quotes the message); a document's name, pointer, identifier,
title or path; a person's name or e-mail address.

A log line may carry ids, counts, sizes, durations, status codes,
``type(exc).__name__`` and ``log_safety.pointer_hash(pointer)``.

The Platform has no formatter or filter that would redact a line on the way out
(``web_interface/app.py`` installs a plain ``logging.Formatter``), so the call
sites are the only place this can hold. Two checks:

(a) A static guard over ``src``: every log call (``logger.*``, ``log.*``,
    ``self._log.*``, ``print``) is parsed, and its arguments may not pass an
    exception or its text, a response body, or a value named like customer
    content. It is a heuristic over names, so it has an allow-list; each entry
    says why that value is not customer text.
(b) Behavioural tests with caplog at DEBUG that drive the client's paths that
    used to log customer text, with a marker in the text, and assert that no
    record carries the marker in its message, its args or its exc_info text.

The KnowledgeBase Core holds the same rule (GI-DATA-04, GI-DATA-02) in
``app/tests/test_log_lines_carry_no_customer_data.py``.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import http.server
import io
import json
import logging
import os
import re
import socket
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Iterator, List, Optional, Set, Tuple

import pytest
import requests
import tenacity

SRC = Path(__file__).resolve().parents[1] / "src"

# -- (a) the static guard ------------------------------------------------------

_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
# The receiver's last name: logger, log, _log, LOGGER, logging, audit_logger, ...
# Not math.log or attempt.exception().
_LOGGER_NAME = re.compile(r"^_*(log|logging|logger|\w*_logger)$", re.IGNORECASE)

# A name (variable, attribute, dict key) is customer content when its whole
# lower-cased name is in _CONTENT_NAMES or one of its "_"-separated words is in
# _CONTENT_WORDS: ``query``, ``file_path``, ``top_chunks``, ``user.email``,
# ``hit.get("title")``.
_CONTENT_WORDS = {
    "query", "queries", "question", "questions", "text", "texts", "body", "bodies",
    "snippet", "snippets", "chunk", "chunks", "answer", "answers", "content", "contents",
    "identifier", "identifiers", "pointer", "pointers", "title", "titles",
    "path", "paths", "name", "names", "email", "emails", "address", "document",
    # A pointer's folder or prefix can name a client or a mandate at a law firm.
    "folder", "folders", "prefix", "prefixes",
}
# doc_id and ident are a document's pointer in this code base (knovas_client
# builds the identifier from doc_id or path).
_CONTENT_NAMES = {"doc_id", "ident", "filename"}
# Attributes and calls that read a response body or a payload.
_BODY_ATTRIBUTES = {"text", "content"}
_BODY_CALLS = {"json", "decode", "read", "read_text", "getvalue"}
# What an exception may contribute: its type, and codes that are identifiers.
_SAFE_EXCEPTION_ATTRIBUTES = {
    "status", "status_code", "code", "error_code", "delay_seconds", "retry_after", "pgcode", "sqlstate",
}
# Wrappers whose result says nothing about the text they are given.
# identity/audit.py's _error_code(exc) is the exception's type, SQLSTATE and constraint name.
_NEUTRAL_CALLS = {"len", "bool", "type", "isinstance", "pointer_hash", "_error_code"}
# A module constant (DEFAULT_PDF_TEXT_MODE, ENV_STATE_PATH) is code, not customer content.
_CONSTANT = re.compile(r"^_*[A-Z][A-Z0-9_]*$")

# (file under src, function, the flagged expression as ast.unparse prints it, why it is not customer text).
# Keyed by function so that an entry never exempts the same name somewhere else in the file.
ALLOWED: List[Tuple[str, str, str, str]] = [
    ("web_interface/app.py", "_enrichment_path_from_config", "path",
     "the search enrichment file (SEARCH_ENRICHMENT_PATH): an operator configures it, not a document"),
    ("web_interface/app.py", "_context_store_path_from_config", "path",
     "the context store directory (SEARCH_CONTEXT_STORE_PATH): an operator configures it, not a document"),
    ("web_interface/app.py", "_load_search_enrichment", "path",
     "the search enrichment file (SEARCH_ENRICHMENT_PATH): an operator configures it, not a document"),
    ("file_utils.py", "find_autodoc_files", "self.autodoc_path",
     "the mount root (KNOVAS_DOCUMENTS_PATH): an operator configures it, not a document"),
    ("ontology_filters.py", "_load_state", "self._state_path",
     "the filter state file (ONTOLOGY_FILTER_STATE_PATH): an operator configures it, not a document"),
    ("ontology_filters.py", "_save_state", "self._state_path",
     "the filter state file (ONTOLOGY_FILTER_STATE_PATH): an operator configures it, not a document"),
    ("ontology_graph_filters.py", "_load_state", "self._state_path",
     "the review-marks state file: an operator configures it, not a document"),
    ("ontology_graph_filters.py", "_save_state", "self._state_path",
     "the review-marks state file: an operator configures it, not a document"),
    ("ontology_store.py", "_persist", "self._path",
     "the ontology fixture file: an operator configures it, not a document"),
    ("ontology_store.py", "load_ontology", "path",
     "the ontology fixture file: an operator configures it, not a document"),
    ("document_grants.py", "_init_store", "path",
     "the grant store's SQLite file: an operator configures it, not a document"),
    ("identity/bootstrap.py", "_write_secret", "path",
     "the first administrator's one-time password file (PLATFORM_ADMIN_BOOTSTRAP_PATH): an operator configures it, not a document"),
    ("identity/bootstrap.py", "ensure_admin", "secret_path",
     "the first administrator's one-time password file (PLATFORM_ADMIN_BOOTSTRAP_PATH): an operator configures it, not a document"),
    ("knovas_client.py", "_rbac_request", "path",
     "a route of the Knovas RBAC API: a fixed path, at most with a quoted group or rule id. A pointer "
     "travels in the query string, which no Platform line logs; urllib3's request lines, the one "
     "place it would show, are dropped by log_safety.guard_logging"),
    ("knovas_client.py", "one", "path",
     "a route of the Knovas graph API: a fixed path with quoted node or type ids; the query string "
     "is not part of it (see _rbac_request)"),
    ("knovas_client.py", "doc_fields_probe", "answer",
     "the document-fields probe's classification: off, values, filters or unknown"),
    ("experiments/indexer.py", "_raise_for_knovas_error", "pointer",
     "an experiment's pointer, <pointer_prefix>/<domain_key>/<experiment key>: ids, no document name"),
    ("experiments/indexer.py", "_upload_locked", "pointer",
     "an experiment's pointer, <pointer_prefix>/<domain_key>/<experiment key>: ids, no document name"),
    ("experiments/indexer.py", "_purge_one", "pointer",
     "an experiment's pointer, <pointer_prefix>/<domain_key>/<experiment key>: ids, no document name"),
    ("experiments/indexer.py", "_listing_failed", "prefix",
     "the experiments' own pointer prefix (settings.pointer_prefix), configuration, not a client folder"),
    ("experiments/indexer.py", "_unlock", "name",
     "an advisory lock's name, exp-upload:<experiment uuid>"),
    ("experiments/packs.py", "available_packs", "name",
     "a shipped experiment pack's file name: code in this repository, not customer data"),
    ("experiments/packs.py", "load_pack", "name",
     "a shipped experiment pack's file name, checked against _PACK_NAME_RE before it is used"),
    ("knovas_extract_upload.py", "_env_flag", "name",
     "the name of an environment variable (RC_..., a constant in the code), configuration"),
    ("knovas_extract_upload.py", "_env_int", "name",
     "the name of an environment variable (RC_..., a constant in the code), configuration"),
    ("knovas_extract_upload.py", "_env_int_at_least", "name",
     "the name of an environment variable (RC_..., a constant in the code), configuration"),
    ("custom_views/registry.py", "_log_changes", "name",
     "a file name inside a custom view's folder: view code an operator ships, not customer data"),
    ("experiments/jobs.py", "_process", "exc.reason",
     "every RetryLater reason is an MSG_ constant (indexer, service): fixed text, no label or value"),
    ("log_safety.py", "_log_uncaught_in_thread", "name",
     "a thread's name, set by the code that starts it (brain-sync, experiments workers)"),
]


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    if not isinstance(func, ast.Attribute) or func.attr not in _LOG_METHODS:
        return False
    receiver = func.value
    last = receiver.attr if isinstance(receiver, ast.Attribute) else getattr(receiver, "id", "")
    return bool(_LOGGER_NAME.match(last or ""))


def _is_content_name(name: str) -> bool:
    if not name or name.startswith("__"):
        return False
    lowered = name.lower().lstrip("_")
    return lowered in _CONTENT_NAMES or any(w in _CONTENT_WORDS for w in lowered.split("_"))


def _innermost_name(node: ast.AST) -> Optional[str]:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _findings(arg: ast.AST, exc_names: Set[str]) -> Iterator[Tuple[str, ast.AST]]:
    """(kind, node) for everything in one log argument that is customer text."""
    if isinstance(arg, ast.Call):
        func = arg.func
        if isinstance(func, ast.Name) and func.id in _NEUTRAL_CALLS:
            return
        if isinstance(func, ast.Name) and func.id == "getattr" and len(arg.args) >= 2:
            target, key = arg.args[0], arg.args[1]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if isinstance(target, ast.Name) and target.id in exc_names:
                    if key.value not in _SAFE_EXCEPTION_ATTRIBUTES:
                        yield "exception text", arg
                    return
                if _is_content_name(key.value):
                    yield "content", arg
                    return
        if isinstance(func, ast.Attribute):
            if func.attr in _BODY_CALLS:
                yield "body", arg
                return
            if func.attr == "get" and arg.args and isinstance(arg.args[0], ast.Constant) \
                    and isinstance(arg.args[0].value, str) and _is_content_name(arg.args[0].value):
                yield "content", arg
                return
            if func.attr in ("format_exc", "format_exception", "format_tb", "print_exc"):
                yield "exception text", arg
                return
        # What is called is code; what it is called on and with is data.
        parts = ([func.value] if isinstance(func, ast.Attribute) else []) + list(arg.args) \
            + [k.value for k in arg.keywords]
        for part in parts:
            yield from _findings(part, exc_names)
        return
    if isinstance(arg, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        # ``[pointer_hash(p) for p in prefixes]`` logs hashes; ``[p for p in prefixes]`` logs prefixes.
        elt_is_neutral = isinstance(arg.elt, ast.Call) and isinstance(arg.elt.func, ast.Name) \
            and arg.elt.func.id in _NEUTRAL_CALLS
        yield from _findings(arg.elt, exc_names)
        if not elt_is_neutral:
            for generator in arg.generators:
                yield from _findings(generator.iter, exc_names)
        return
    if isinstance(arg, ast.IfExp):
        # ``pointer_hash(key) if key else "-"``: the test decides, it is not logged.
        yield from _findings(arg.body, exc_names)
        yield from _findings(arg.orelse, exc_names)
        return
    if isinstance(arg, ast.Attribute):
        if arg.attr in _SAFE_EXCEPTION_ATTRIBUTES and _innermost_name(arg) in exc_names:
            return
        if _innermost_name(arg) in exc_names:
            yield "exception text", arg
            return
        if isinstance(arg.value, ast.Name) and arg.value.id == "os" and arg.attr == "path":
            return  # the module os.path, not a value
        if arg.attr in _BODY_ATTRIBUTES:
            yield "body", arg
            return
        if _is_content_name(arg.attr):
            yield "content", arg
            return
    if isinstance(arg, ast.Subscript) and isinstance(arg.slice, ast.Constant) \
            and isinstance(arg.slice.value, str) and _is_content_name(arg.slice.value):
        yield "content", arg
        return
    if isinstance(arg, ast.Name):
        if arg.id in exc_names or arg.id.endswith("_exc"):
            yield "exception text", arg
        elif _is_content_name(arg.id) and not _CONSTANT.match(arg.id):
            yield "content", arg
        return
    for child in ast.iter_child_nodes(arg):
        yield from _findings(child, exc_names)


def _handler_names(tree: ast.AST) -> dict:
    """id(log call) -> names of the exceptions bound by the handlers around it."""
    bound: dict = {}
    for handler in ast.walk(tree):
        if isinstance(handler, ast.ExceptHandler) and handler.name:
            for stmt in handler.body:
                for node in ast.walk(stmt):
                    if isinstance(node, ast.Call):
                        bound.setdefault(id(node), set()).add(handler.name)
    return bound


def _owners(tree: ast.AST) -> dict:
    """id(node) -> the name of the innermost function around it ("<module>" outside any)."""
    owner: dict = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else name
            owner[id(child)] = inner
            visit(child, inner)

    visit(tree, "<module>")
    return owner


def scan(rel: str, source: str) -> List[Tuple[str, int, str, str, str]]:
    """(file, line, kind, expression, function) for every offending log argument."""
    tree = ast.parse(source)
    handler_names = _handler_names(tree)
    owner = _owners(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and func.value.id == "traceback" and func.attr in ("print_exc", "print_exception"):
            out.append((rel, node.lineno, "traceback", ast.unparse(node), owner.get(id(node))))
            continue
        if not _is_log_call(node):
            continue
        if isinstance(func, ast.Attribute) and func.attr == "exception":
            out.append((rel, node.lineno, "traceback", "logger.exception", owner.get(id(node))))
        for keyword in node.keywords:
            if keyword.arg == "exc_info" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value in (None, False)):
                out.append((rel, node.lineno, "traceback", "exc_info", owner.get(id(node))))
        exc_names = handler_names.get(id(node), set())
        for arg in list(node.args) + [k.value for k in node.keywords if k.arg != "exc_info"]:
            for kind, found in _findings(arg, exc_names):
                out.append((rel, node.lineno, kind, ast.unparse(found), owner.get(id(node))))
    return out


def _scan_src() -> List[Tuple[str, int, str, str, str]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        found.extend(scan(rel, path.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize("snippet, flagged", [
    ('logger.info("q=%s", query)', True),
    ('logger.info(f"Legacy search: query={query!r}")', True),
    ('logger.info("q_len=%d", len(query or ""))', False),
    ('try:\n    x()\nexcept Exception as exc:\n    logger.warning("failed: %s", exc)', True),
    ('try:\n    x()\nexcept Exception as e:\n    logger.error(f"failed: {e}")', True),
    ('try:\n    x()\nexcept Exception as exc:\n    logger.warning("failed: %s", str(exc))', True),
    ('try:\n    x()\nexcept Exception as exc:\n    logger.warning("failed: %s", type(exc).__name__)', False),
    ('try:\n    x()\nexcept Exception as exc:\n    logger.warning("HTTP %s %s", exc.status, exc.error_code)', False),
    ('try:\n    x()\nexcept Exception as exc:\n    logger.warning("%s", exc.response.text)', True),
    ('try:\n    x()\nexcept Exception:\n    logger.exception("failed")', True),
    ('logger.error("failed", exc_info=True)', True),
    ('logger.error("body: %s", (response.text or "")[:600])', True),
    ('logger.error("body: %d chars", len(response.text or ""))', False),
    ('logger.warning("answer: %s", _clip(raw.decode("utf-8"), 500))', True),
    ('logger.info("chunks=%r", raw0.get("top_chunks"))', True),
    ('logger.info("refused for %s", getattr(user, "email", "?"))', True),
    ('logger.info("refused for %s", user.id)', False),
    ('logger.info("open %s", full_path)', True),
    ('logger.info("open %s", pointer_hash(doc_id))', False),
    ('logger.info("file=%s", os.path.basename(document_key))', True),
    ('logger.info("%s done", view.__name__)', False),
    ('logger.info("doc %s", pointer_hash(document_key) if document_key else "-")', False),
    ('logger.info("doc %s", document_key if document_key else "-")', True),
    ('logger.warning("Invalid mode; using %s", DEFAULT_PDF_TEXT_MODE)', False),
    ('logger.info("loaded from %s", _enrichment_path_from_config(config))', False),
    ('logger.info("first %s", (list(pointer_candidates) or ["-"])[0])', True),
    ('logger.info("%d prefixes %s", len(prefixes), [pointer_hash(p) for p in prefixes])', False),
    ('logger.info("prefixes %s", [p.strip() for p in prefixes])', True),
    ('logger.warning("resolved without its leading %r", folder)', True),
    ('math.log(x)', False),
    ('print("question:", question)', True),
])
def test_the_guard_sees_customer_text(snippet, flagged):
    assert bool(scan("x.py", snippet)) is flagged


def test_no_log_call_carries_customer_text():
    allowed = {(rel, function, expr) for rel, function, expr, _ in ALLOWED}
    offenders = [f"{rel}:{line} ({function}) {kind}: {expr}"
                 for rel, line, kind, expr, function in _scan_src()
                 if (rel, function, expr) not in allowed]
    assert not offenders, (
        "log calls carrying customer text (log ids, counts, type(exc).__name__ or "
        "log_safety.pointer_hash(pointer) instead):\n" + "\n".join(offenders)
    )


def test_every_allow_list_entry_is_used_and_explained():
    seen = {(rel, function, expr) for rel, _, _, expr, function in _scan_src()}
    for rel, function, expr, reason in ALLOWED:
        assert len(reason) > 20, (rel, function, expr)
        assert (rel, function, expr) in seen, f"stale allow-list entry: {rel} {function} {expr}"


# -- (b) behaviour -------------------------------------------------------------

MARKER_QUERY = "ZZQUERYMARKER7"
MARKER_BODY = "ZZBODYMARKER7"
MARKER_SNIPPET = "ZZSNIPPETMARKER7"
MARKER_POINTER = "ZZPOINTERMARKER7"
MARKER_EXC = "ZZEXCMARKER7"


def _record_texts(record: logging.LogRecord) -> List[str]:
    texts = [str(record.msg), repr(record.args)]
    try:
        texts.append(record.getMessage())
    except Exception:  # noqa: BLE001 - a broken format string is not this test's concern
        pass
    if record.exc_info:
        texts.append(logging.Formatter().formatException(record.exc_info))
    if record.exc_text:
        texts.append(record.exc_text)
    if record.stack_info:
        texts.append(record.stack_info)
    return texts


def _assert_no_marker(caplog, *markers: str) -> None:
    assert caplog.records, "the path under test logged nothing; the test would prove nothing"
    for record in caplog.records:
        for text in _record_texts(record):
            for marker in markers:
                assert marker not in text, f"{record.name}:{record.lineno} logged {marker}: {text[:300]}"


@pytest.fixture
def debug_logs(caplog):
    # app.create_app sets the knovas_client logger to INFO in other tests.
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="knovas_client")
    caplog.set_level(logging.DEBUG, logger="knovas_extract_upload")
    return caplog


class _Config:
    def __init__(self, values):
        self._v = dict(values)

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        return bool(self._v.get(key, default))

    def get_int(self, key, default=0):
        return int(self._v.get(key, default))


class _Session:
    def __init__(self, responder):
        self.cert = self.verify = None
        self._responder = responder

    def request(self, method, url, **kwargs):
        return self._responder(method, url, **kwargs)

    def close(self):
        pass


def _response(status: int, body: str, url: str) -> requests.Response:
    """A real requests.Response: raise_for_status() names the URL as requests does."""
    response = requests.Response()
    response.status_code = status
    response._content = body.encode("utf-8")
    response.encoding = "utf-8"
    response.url = url
    response.reason = "Server Error" if status >= 500 else "OK"
    response.headers["Content-Type"] = "application/json"
    return response


def _client(*, secured: bool = True, base_url: str = "https://knovas.test"):
    from knovas_client import KnovasAPIClient

    values = {
        "api.base_url": base_url,
        "api.auth_type": "bearer",
        "api.api_key": "",
        "api.use_secured_api": secured,
        "api.allow_legacy_api_fallback": not secured,
        "api.cert_path": "/certs/client.crt" if secured else "",
        "api.key_path": "/certs/client.key" if secured else "",
        "api.ca_cert_path": "/certs/ca.crt" if secured else "",
        "api.customer_id": "",
        "api.cert_auto_renew_enabled": False,
        "api.rate_limit.requests_per_second": 0,
        "api.rate_limit.retry_attempts": 1,
        "api.rate_limit.retry_backoff": 1,
        "api.http_read_timeout": 5,
    }
    return KnovasAPIClient(config_loader=_Config(values))


@pytest.fixture(autouse=True)
def _no_retry_waits(monkeypatch):
    from knovas_client import KnovasAPIClient

    monkeypatch.setattr(KnovasAPIClient._make_request.retry, "wait", tenacity.wait_none())


def test_a_failed_secured_query_logs_neither_the_error_body_nor_the_question(debug_logs):
    client = _client()
    body = '{"detail": "no hit for ' + MARKER_QUERY + '", "echo": "' + MARKER_BODY + '"}'
    client._session = _Session(lambda m, u, **k: _response(500, body, u))

    with pytest.raises(requests.exceptions.HTTPError):
        client.search_documents(f"Kuendigung {MARKER_QUERY}", limit=5)

    _assert_no_marker(debug_logs, MARKER_BODY, MARKER_QUERY)


def test_a_secured_search_does_not_log_the_snippets_of_its_hits(debug_logs):
    client = _client()
    hits = {"results": [{
        "pointer": f"Mandanten/{MARKER_POINTER}.pdf",
        "score": 0.8,
        "top_chunks": [{"snippet": f"Der Vertrag {MARKER_SNIPPET} endet."}],
    }]}
    client._session = _Session(
        lambda m, u, **k: _response(200, json.dumps(hits), u))

    out = client.search_documents(f"Vertrag {MARKER_QUERY}", limit=5)

    assert out["total"] == 1
    _assert_no_marker(debug_logs, MARKER_SNIPPET, MARKER_POINTER, MARKER_QUERY)


def test_the_legacy_search_does_not_log_the_question(debug_logs):
    client = _client(secured=False)

    def answer(method, url, **kwargs):
        full = requests.Request(method, url, params=kwargs.get("params")).prepare().url
        return _response(200, '{"results": [{"doc_id": "d1"}]}', full)

    client._session = _Session(answer)
    client.search_documents(f"Kuendigung {MARKER_QUERY}", limit=5)

    def refuse(method, url, **kwargs):
        full = requests.Request(method, url, params=kwargs.get("params")).prepare().url
        return _response(502, '{"detail": "' + MARKER_BODY + '"}', full)

    client._session = _Session(refuse)
    with pytest.raises(requests.exceptions.HTTPError):
        client.search_documents(f"Kuendigung {MARKER_QUERY}", limit=5)

    _assert_no_marker(debug_logs, MARKER_QUERY, MARKER_BODY)


def test_a_failed_single_doc_extract_logs_neither_the_document_nor_the_error(debug_logs, monkeypatch):
    import knovas_extract_upload

    def broken(*args, **kwargs):
        raise RuntimeError(f"cannot parse {MARKER_EXC} in {MARKER_POINTER}")

    monkeypatch.setattr(knovas_extract_upload, "extract_parts_from_base64", broken)
    client = _client()

    def answer(method, url, **kwargs):
        if url.endswith("/secured/init_document_transmission"):
            return _response(200, '{"transmission_key_id": "tk-1"}', url)
        return _response(200, '{"status": "ok"}', url)

    client._session = _Session(answer)
    document = {
        "doc_id": f"Mandanten/Mueller/{MARKER_POINTER}.pdf",
        "path": f"Mandanten/Mueller/{MARKER_POINTER}.pdf",
        "type": "pdf",
        "content_base64": base64.b64encode(b"%PDF-1.4 not really").decode("ascii"),
    }

    result = client.sync_single_document(document)

    assert result["status"] == "success"
    _assert_no_marker(debug_logs, MARKER_POINTER, MARKER_EXC)


def _login(client) -> None:
    client.get("/login")
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    client.post("/login", data={"login_name": "healthuser", "password": "healthpass123",
                                "csrf_token": token})


def test_an_unhandled_exception_logs_the_route_rule_not_the_path_or_the_message(docbridge_app, caplog):
    """Flask's own line was ``Exception on <request.path> [GET]`` plus the traceback."""

    def boom(doc_id):
        raise RuntimeError(f"cannot open {MARKER_EXC} in {doc_id}")

    docbridge_app.add_url_rule("/api/document/<path:doc_id>/maint-boom", "maint_boom", boom)
    docbridge_app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger=docbridge_app.logger.name)
    client = docbridge_app.test_client()
    _login(client)

    response = client.get(f"/api/document/Mandanten/{MARKER_POINTER}.pdf/maint-boom?path={MARKER_QUERY}")

    assert response.status_code == 500
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Exception on")]
    assert lines == ["Exception on /api/document/<path:doc_id>/maint-boom [GET]: RuntimeError"]
    _assert_no_marker(caplog, MARKER_POINTER, MARKER_EXC, MARKER_QUERY)


def test_the_development_server_logs_no_request_path():
    from web_interface.app import _NoPathRequestHandler

    handler = _NoPathRequestHandler.__new__(_NoPathRequestHandler)
    handler.command = "GET"
    handler.requestline = f"GET /api/document/{MARKER_POINTER}?q={MARKER_QUERY} HTTP/1.1"
    handler.path = f"/api/document/{MARKER_POINTER}?q={MARKER_QUERY}"
    seen = []
    handler.log = lambda level, message, *args: seen.append(message % args)

    handler.log_request(200, 512)
    handler.log_error("code %d, message %s", 400, f"Bad request syntax ({handler.requestline!r})")

    assert seen == ["GET 200 512", "request refused: 400"]


# -- the views bundle's report problems ------------------------------------------------------------

def test_unreadable_bundle_reports_are_logged_by_count_and_kind(tmp_path, caplog):
    from graph_views import bundle

    (tmp_path / f"broken_{MARKER_POINTER}.json").write_text("{" + MARKER_EXC, encoding="utf-8")
    source = bundle.Source(f"file:{tmp_path / 'bundle.json'}", "file", str(tmp_path / "bundle.json"))
    header = {"reports": {f"Mandant_{MARKER_QUERY}": f"missing_{MARKER_POINTER}.json",
                          "broken": f"broken_{MARKER_POINTER}.json"}}
    caplog.set_level(logging.DEBUG)

    reports, errors = bundle._read_reports(source, header, None)

    assert len(errors) == 2  # the administrator still reads both, names included
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["views bundle: 2 report(s) not read (missing, JSONDecodeError)"]
    _assert_no_marker(caplog, MARKER_POINTER, MARKER_QUERY, MARKER_EXC)


# -- third-party loggers under LOG_LEVEL=DEBUG --------------------------------------------------------

class _QuietJSONHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the name http.server calls
        body = b'{"results": [], "readable": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # the test server's own lines are not the Platform's
        pass


@pytest.fixture
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietJSONHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def platform_logging(monkeypatch):
    """create_app's log set-up as gunicorn gets it (no handler yet), LOG_LEVEL=DEBUG; restored after.

    Yields a function returning what the Platform's handler wrote (its stream redirected)."""
    from web_interface.app import _configure_logging_for_wsgi

    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_hooks = (sys.excepthook, threading.excepthook)
    names = ("web_interface", "web_interface.app", "knovas_client", "urllib3")
    saved_levels = {name: logging.getLogger(name).level for name in names}
    root.handlers = []
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    stream = io.StringIO()
    try:
        _configure_logging_for_wsgi(_Config({}))
        assert len(root.handlers) == 1
        root.handlers[0].setStream(stream)
        yield stream.getvalue
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        sys.excepthook, threading.excepthook = saved_hooks
        for name, value in saved_levels.items():
            logging.getLogger(name).setLevel(value)


def test_debug_logging_writes_no_request_url(platform_logging, local_server):
    import log_safety

    assert threading.excepthook is log_safety._log_uncaught_in_thread
    requests.get(f"{local_server}/secured/document_readable",
                 params={"pointer": f"Mandanten/{MARKER_POINTER}.pdf"}, timeout=5)
    client = _client(secured=False, base_url=local_server)
    client.search_documents(f"Kuendigung {MARKER_QUERY}", limit=5)

    out = platform_logging()
    assert "DEBUG [knovas_client] Making GET request" in out  # the Platform's own DEBUG lines pass
    assert "urllib3" not in out
    assert MARKER_POINTER not in out and MARKER_QUERY not in out


def test_a_refused_connection_retried_by_urllib3_logs_no_url(platform_logging):
    from urllib3.util import Retry

    with socket.socket() as sock:  # a port nobody listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    session = requests.Session()
    session.mount("http://", requests.adapters.HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0)))

    with pytest.raises(requests.exceptions.ConnectionError):
        session.get(f"http://127.0.0.1:{port}/api/search",
                    params={"query": MARKER_QUERY, "pointer": MARKER_POINTER}, timeout=2)

    out = platform_logging()
    assert "/api/search" not in out and MARKER_QUERY not in out and MARKER_POINTER not in out


def test_the_filter_names_every_platform_module():
    import log_safety

    tops = {p.stem if p.suffix == ".py" else p.name for p in SRC.iterdir()
            if (p.suffix == ".py" and p.stem != "__init__") or (p.is_dir() and (p / "__init__.py").exists())}
    assert tops <= log_safety.PLATFORM_MODULES
    assert log_safety.PLATFORM_MODULES - tops == {"__main__"}


# -- uncaught exceptions ---------------------------------------------------------------------------

def test_an_uncaught_exception_is_logged_by_its_type_only():
    """In a fresh interpreter: inside pytest its threadexception plugin owns threading.excepthook."""
    script = textwrap.dedent(f"""
        import logging, threading
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")
        import log_safety
        log_safety.install_excepthooks()

        def job():
            raise RuntimeError("cannot open {MARKER_POINTER}")

        worker = threading.Thread(target=job, name="brain-sync")
        worker.start()
        worker.join()
        raise ValueError("bad value {MARKER_EXC}")
    """)
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    proc = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True,
                          timeout=60)

    out = proc.stdout + proc.stderr
    assert "Uncaught RuntimeError in thread brain-sync" in out
    assert "Uncaught ValueError in the main thread" in out
    assert "Traceback" not in out and MARKER_POINTER not in out and MARKER_EXC not in out


# -- pointer_hash ----------------------------------------------------------------------------------

def test_pointer_hash_is_keyed_by_the_installation_secret(monkeypatch):
    import log_safety

    monkeypatch.setattr(log_safety, "_key", None)
    monkeypatch.delenv(log_safety.SECRET_ENV, raising=False)
    assert log_safety.pointer_hash("Mandanten/Mueller") == log_safety.NO_KEY  # never unkeyed

    monkeypatch.setenv(log_safety.SECRET_ENV, "a" * 64)
    keyed = log_safety.pointer_hash("Mandanten/Mueller")
    assert len(keyed) == 16 and keyed != hashlib.sha256(b"Mandanten/Mueller").hexdigest()[:16]
    monkeypatch.setenv(log_safety.SECRET_ENV, "b" * 64)
    assert log_safety.pointer_hash("Mandanten/Mueller") != keyed

    log_safety.use_secret("a" * 64)  # create_app's way: web.secret_key, the same key as the env
    assert log_safety.pointer_hash("Mandanten/Mueller") == keyed


def test_the_operator_command_prints_the_logged_hash(monkeypatch):
    import log_safety

    monkeypatch.setattr(log_safety, "_key", None)
    monkeypatch.setenv(log_safety.SECRET_ENV, "c" * 64)
    pointer = "Mandanten/Müller AG/Vertrag.pdf"
    expected = log_safety.pointer_hash(pointer)
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}

    proc = subprocess.run([sys.executable, "-m", "log_safety", "-"], input=pointer + "\n", env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0 and proc.stdout.split() == [expected]

    env.pop(log_safety.SECRET_ENV)
    proc = subprocess.run([sys.executable, "-m", "log_safety", "x"], env=env, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 2 and not proc.stdout
