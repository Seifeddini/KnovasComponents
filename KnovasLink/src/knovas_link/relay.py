"""The relays (MVP-17): a component's Brain calls to secure-api, on an allow-list, and its reports to the Platform,
checked first that they hold only codes and counts.

The report rules are the Platform's (``docbridge_integration/src/builder/components.py`` and ``protocol.py``: the
event code and count-key patterns, the run fields), with one difference: where the Platform silently drops a count
whose key does not fit, Link refuses the report, because Link passes reports on unchanged and nothing shaped like a
name may leave the site in one.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping, Optional

from knovas_link.errors import LocalError

# ------------------------------------------------------------------ the Brain

#: Exact paths (local-api.md, "ANY /local/v1/brain/<path>").
ALLOWED = frozenset({
    "secured/init_document_transmission", "secured/transmit_document_part", "secured/delete_information_object",
    "secured/documents", "secured/documents/source_ref", "secured/folder_rules",
})
#: Everything below it, not the path itself.
ALLOWED_BELOW = "secured/graph/"
_PATH_CHARS = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@%-]+$")
_ENCODED_SEPARATORS = ("%2f", "%5c", "%2e", "%00")

#: Request headers passed on to secure-api; never the component's own token.
REQUEST_HEADERS = ("accept", "content-type", "content-length", "idempotency-key", "if-match", "if-none-match")
#: Response headers not passed back (hop by hop, or set again by Link's server).
_DROPPED = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
                      "transfer-encoding", "upgrade", "server", "date"})


def relayed_path(raw: str) -> Optional[str]:
    """The path to call on secure-api for ``raw`` (what follows /local/v1/brain/, still percent-encoded), or None
    when it is not on the allow-list. Encoded separators and dot segments are refused, not normalised."""
    if not raw or not _PATH_CHARS.match(raw):
        return None
    lowered = raw.lower()
    if any(sep in lowered for sep in _ENCODED_SEPARATORS):
        return None
    if any(segment in ("", ".", "..") for segment in raw.split("/")):
        return None
    if raw in ALLOWED or (raw.startswith(ALLOWED_BELOW) and len(raw) > len(ALLOWED_BELOW)):
        return raw
    return None


def response_headers(headers: Mapping[str, str]) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _DROPPED}


# ------------------------------------------------------------------ reports

#: builder/components.py EVENT_CODE and COUNT_KEY, unchanged (tests/test_relay.py holds them to the Platform's).
EVENT_CODE = re.compile(r"^[a-z][a-z0-9_.]{1,60}$")
COUNT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,60}$")
#: A workspace or source key: an identifier the Platform made, not a sentence or a path.
SOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
COMMAND_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
#: The extensions of documents and files: a code or a key ending in one is a file's name, not a code.
_FILE_NAME = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|odt|ods|rtf|txt|md|markdown|html?|xml|json|ya?ml|csv|tsv|eml|msg|"
                        r"png|jpe?g|gif|tiff?|zip|7z|tar|gz)$", re.I)
RUN_KEYS = frozenset({"kind", "status", "started_at", "finished_at", "duration_ms", "added", "changed", "retired",
                      "errors", "held", "command"})


def _refuse(what: str) -> LocalError:
    return LocalError(400, "bad_field", what)


def _not_a_name(value: str, what: str) -> str:
    if _FILE_NAME.search(value):
        raise _refuse(f"{what}: a code, not a file name")
    return value


def _code(value: Any, what: str = "code") -> str:
    if not isinstance(value, str) or not EVENT_CODE.match(value):
        raise _refuse(f"{what}: lower case letters, digits, dots and underscores")
    return _not_a_name(value, what)


def _int(value: Any, what: str, maximum: int = 10 ** 9) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise _refuse(f"{what}: a whole number")


def _counts(value: Any, what: str = "counts") -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise _refuse(f"{what}: names of counts to whole numbers")
    for k, v in value.items():
        if not isinstance(k, str) or not COUNT_KEY.match(k):
            raise _refuse(f"{what}: a count's name is a word, never a path or a sentence")
        _not_a_name(k, what)
        if isinstance(v, bool) or not isinstance(v, int):
            raise _refuse(f"{what}: whole numbers")


def _when(value: Any, what: str) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, str) or len(value) > 40:
        raise _refuse(f"{what}: an ISO 8601 time")
    try:
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _refuse(f"{what}: an ISO 8601 time") from None


def _only(body: Any, keys: frozenset, what: str) -> dict:
    if not isinstance(body, dict):
        raise _refuse(f"{what}: an object")
    if set(body) - keys:
        raise _refuse(f"{what}: only codes and counts")
    return body


def check_report(kind: str, body: Any) -> dict:
    """``body`` if it holds only codes and counts, as the Platform takes them; else 400 ``bad_field``."""
    if kind == "runs":
        body = _only(body, RUN_KEYS, "run")
        if body.get("kind") not in ("dry_run", "apply") or body.get("status") not in ("ok", "held", "failed"):
            raise _refuse("kind: dry_run|apply, status: ok|held|failed")
        for name in ("duration_ms", "added", "changed", "retired", "errors"):
            _int(body.get(name), name)
        _when(body.get("started_at"), "started_at")
        _when(body.get("finished_at"), "finished_at")
        held = body.get("held")
        if held is not None:
            if not isinstance(held, list) or len(held) > 200:
                raise _refuse("held: a list")
            for h in held:
                h = _only(h, frozenset({"mapping", "retire", "share"}), "held")
                mapping = h.get("mapping")
                if not isinstance(mapping, str) or not EVENT_CODE.match(mapping.replace("-", "_")):
                    raise _refuse("held.mapping: a mapping id")
                _not_a_name(mapping, "held.mapping")
                _counts(h.get("retire"), "held.retire")
                share = h.get("share")
                if share is not None and (isinstance(share, bool) or not isinstance(share, (int, float))):
                    raise _refuse("held.share: a number")
        command = body.get("command")
        if command is not None and (not isinstance(command, str) or not COMMAND_ID.match(command)):
            raise _refuse("command: a command id")
        return body
    if kind == "events":
        body = _only(body, frozenset({"events"}), "events")
        items = body.get("events")
        if items is None:
            items = []
        if not isinstance(items, list) or len(items) > 100:
            raise _refuse("events: a list of up to 100")
        for e in items:
            e = _only(e, frozenset({"code", "counts"}), "event")
            _code(e.get("code"))
            _counts(e.get("counts"))
        return body
    if kind == "checks":
        body = _only(body, frozenset({"checks"}), "checks")
        items = body.get("checks")
        if not isinstance(items, list) or len(items) > 200:
            raise _refuse("checks: a list of up to 200")
        for c in items:
            c = _only(c, frozenset({"workspace", "source", "code", "counts"}), "check")
            for key in ("workspace", "source"):
                value = c.get(key)
                if not isinstance(value, str) or not SOURCE_KEY.match(value):
                    raise _refuse(f"{key}: a key")
                _not_a_name(value, key)
            _code(c.get("code"))
            _counts(c.get("counts"))
        return body
    raise LocalError(404, "not_found", "no such report")
