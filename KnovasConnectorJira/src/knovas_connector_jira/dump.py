"""``dump-stream``: one Jira source's complete record stream as JSON Lines, for a Brain mapping run outside the site's
loop (FEAT-2026-11-10-write-back J-4; BET-2026-11-write-back WB-07).

    knovas-connector-jira dump-stream --source <source key> --out <file.jsonl | -> [--settings <file.json>]

- **What it writes:** the messages of one complete read from no earlier state (``JiraConnector.read(source, None)``),
  one JSON object per line, in the stream's order: a ``record`` per issue of the project, then the ``generation``
  (``complete: true``) and the ``state``. Every message is checked against the protocol's stream schema before
  anything is written. ``-`` writes to standard output; a file is written whole or not at all (a temporary file beside
  it, then renamed), readable by its owner only.
- **The settings:** with ``--settings``, a JSON object as the Builder keeps a Jira source's settings (``site``,
  ``project``, optional ``fields``, ``computed_fields``, ``page_size``; client.settings_of checks them). Without it,
  the source of that key as Knovas Link holds it from the Platform's published configuration for this component
  (``SYNC_LINK_URL``, ``SYNC_NAME``, ``SYNC_LINK_TOKEN_FILE``, as for ``serve``).
- **The credential:** the site's, ``SYNC_CREDENTIAL_<SOURCE KEY>`` with ``SYNC_CREDENTIAL_<SOURCE KEY>_HOST`` (else the
  ``_JIRA`` pair), from the environment (``knovas-sources.env``); never an argument, never written.
- **Exit codes:** 0 written; 2 usage, settings or configuration (``dump.*``, ``jira.bad_settings``,
  ``jira.no_credential``); 3 a Jira call failed (its code); 4 Jira cut the listing, so it is not complete and nothing
  is written (``dump.listing_cut``). Standard error carries one line: the code, or the counts.

Nothing here names a site, a project or an issue, and no log line or message carries a value or the credential.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional, TextIO

from knovas_connector_jira import SOURCE_KIND
from knovas_connector_jira.client import JiraError, credential_from_environment
from knovas_connector_jira.connector import JiraConnector
from knovas_connector_kit import contracts

#: The settings a --settings file may hold: those client.settings_of reads.
SETTINGS_KEYS = ("site", "project", "fields", "computed_fields", "page_size")
MAX_SETTINGS_BYTES = 64 * 1024


class DumpError(Exception):
    def __init__(self, code: str, exit_code: int):
        super().__init__(code)
        self.code, self.exit_code = code, exit_code


def settings_from_file(path: str) -> dict:
    """The settings object of a --settings file; ``dump.bad_settings_file`` for anything but a JSON object of the keys
    a Jira source's settings hold."""
    try:
        raw = Path(path).read_bytes()
        if len(raw) > MAX_SETTINGS_BYTES:
            raise ValueError("too large")
        found = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        raise DumpError("dump.bad_settings_file", 2) from None
    if not isinstance(found, dict) or not set(found) <= set(SETTINGS_KEYS):
        raise DumpError("dump.bad_settings_file", 2)
    return found


def source_from_link(key: str, environ: Mapping[str, str], client: Any = None) -> dict:
    """The Jira source of this key as Knovas Link holds it for this component; ``dump.*`` when there is none."""
    from knovas_connector_jira import site

    try:
        client = client or site.link_client(environ)
        config = client.config()
    except site.SetupError:
        raise DumpError("dump.no_link", 2) from None
    except site.LinkError as exc:
        raise DumpError("dump.link_refused_" + str(exc.status), 2) from None
    except OSError:
        raise DumpError("dump.link_unreachable", 2) from None
    found = next((s for s in config.get("sources") or [] if isinstance(s, dict) and s.get("key") == key), None)
    if found is None:
        raise DumpError("dump.unknown_source", 2)
    if found.get("kind") != SOURCE_KIND:
        raise DumpError("dump.not_a_jira_source", 2)
    return found


def stream(key: str, *, settings: Optional[dict] = None, environ: Mapping[str, str] = os.environ,
           session: Any = None, link: Any = None, sleep: Any = None) -> list[dict]:
    """The source's complete stream from no earlier state: records, generation, state."""
    source = ({"key": key, "kind": SOURCE_KIND, "access": "read", "settings": dict(settings)}
              if settings is not None else source_from_link(key, environ, link))
    source = dict(source, key=key)
    kwargs = {"sleep": sleep} if sleep is not None else {}
    connector = JiraConnector(source=lambda k: source if k == key else None,
                              credential=lambda s: credential_from_environment(s, environ), session=session, **kwargs)
    try:
        messages = connector.read(key, None)
    except JiraError as err:
        raise DumpError(err.code, 2 if err.code in ("jira.bad_settings", "jira.no_credential") else 3) from None
    if not any(m.get("type") == "generation" and (m.get("payload") or {}).get("complete") is True for m in messages):
        raise DumpError("dump.listing_cut", 4)
    if not all(contracts.valid("stream-message.schema.json", m) for m in messages):
        raise DumpError("dump.invalid_message", 3)
    return messages


def _lines(messages: list[dict]) -> str:
    return "".join(json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for m in messages)


def write(messages: list[dict], out: str, stdout: TextIO) -> None:
    text = _lines(messages)
    if out == "-":
        stdout.write(text)
        stdout.flush()
        return
    target = Path(out)
    fd, tmp = tempfile.mkstemp(prefix=".dump-", suffix=".tmp", dir=str(target.parent or Path(".")))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(key: str, out: str, settings_file: Optional[str], *, environ: Mapping[str, str] = os.environ,
         session: Any = None, link: Any = None, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr,
         sleep: Any = None) -> int:
    try:
        settings = settings_from_file(settings_file) if settings_file else None
        messages = stream(key, settings=settings, environ=environ, session=session, link=link, sleep=sleep)
        write(messages, out, stdout)
    except DumpError as err:
        print(f"dump-stream: {err.code}", file=stderr)
        return err.exit_code
    except OSError as exc:
        print(f"dump-stream: dump.not_written ({type(exc).__name__})", file=stderr)
        return 2
    records = sum(1 for m in messages if m["type"] == "record")
    generation = next(m["payload"]["id"] for m in messages if m["type"] == "generation")
    print(f"dump-stream: {records} records, generation {generation}, {len(messages)} messages", file=stderr)
    return 0
