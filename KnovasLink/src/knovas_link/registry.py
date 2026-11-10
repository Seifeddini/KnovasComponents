"""The site's components: their tokens, what they registered, and what Link learns of them (MVP-16).

Link writes one random token per component name into ``<tokens>/<name>/token``. Each component mounts only its own
folder (at ``/link``) and reads ``/link/token``; Link keeps only the token's hash. A token file that is already there
is kept, so a restart of Link does not lock its components out.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from knovas_link.errors import LocalError

log = logging.getLogger("knovas_link.registry")

#: A component name: also a folder name, so no dot, slash or space.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,200}$")
JOB_KINDS = ("fetch", "write", "probe")
#: Protocol 2.0 (draft): the write path's job kinds, taken only from a component that also declared itself.
V2_JOB_KINDS = ("preview", "act", "reread", "review_outcome", "unstage")
#: A component whose last local heartbeat is older than this is shown offline.
STALE_SECONDS = 120


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Component:
    name: str
    kind: Optional[str] = None
    version: Optional[str] = None
    capabilities: list = field(default_factory=list)
    job_kinds: tuple = ()
    registered: bool = False
    reported_state: Optional[str] = None
    last_beat: Optional[float] = None
    local_ping_ms: Optional[int] = None
    #: Protocol 2.0: the connector's declaration (declaration.schema.json), or None for a 1.x component.
    declaration: Optional[dict] = None

    def state(self, now: float) -> str:
        """online or degraded as it said, offline when it fell silent, unknown when it never said."""
        if self.last_beat is None:
            return "unknown"
        if now - self.last_beat > STALE_SECONDS:
            return "offline"
        return self.reported_state or "online"


class Registry:
    def __init__(self, folder: Path, *, clock: Callable[[], float] = time.monotonic):
        self.folder = Path(folder)
        self.clock = clock
        self._tokens: dict[str, str] = {}
        self._components: dict[str, Component] = {}

    # ------------------------------------------------------------------ tokens

    def ensure(self, names: Iterable[str]) -> int:
        """A token for each of these names; the number written new. A name that cannot be a folder is skipped."""
        written = 0
        for name in dict.fromkeys(names):
            if name in self._tokens:
                continue
            if not isinstance(name, str) or not NAME.match(name):
                log.warning("component.bad_name")
                continue
            folder = self.folder / name
            path = folder / "token"
            try:
                folder.mkdir(parents=True, exist_ok=True)
                os.chmod(folder, 0o755)
                kept = path.read_text(encoding="ascii").strip() if path.exists() else ""
                if not _TOKEN.match(kept):
                    kept = secrets.token_urlsafe(32)
                    tmp = folder / ".token.tmp"
                    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                    with os.fdopen(fd, "w", encoding="ascii") as handle:
                        handle.write(kept + "\n")
                    os.chmod(tmp, 0o644)        # the component runs as its own user; only its own folder is mounted
                    os.replace(tmp, path)
                    written += 1
            except OSError as exc:
                log.error("component.token_unwritable (%s)", type(exc).__name__)
                continue
            self._tokens[name] = _hash(kept)
        if written:
            log.info("component.tokens_written %d", written)
        return written

    def names(self) -> list[str]:
        return sorted(self._tokens)

    def check(self, name: str, bearer: str) -> str:
        """The caller's name, or 401 ``unknown_component`` / ``bad_token``."""
        kept = self._tokens.get(name or "")
        if kept is None:
            raise LocalError(401, "unknown_component", "no component of that name on this site")
        if not bearer or not hmac.compare_digest(_hash(bearer), kept):
            raise LocalError(401, "bad_token", "token refused")
        return name

    # ------------------------------------------------------------------ what the components say

    def get(self, name: Optional[str]) -> Optional[Component]:
        return self._components.get(name) if name else None

    def _component(self, name: str) -> Component:
        return self._components.setdefault(name, Component(name))

    def register(self, name: str, *, kind: str, version: str, capabilities: list, job_kinds: list,
                 declaration: Optional[dict] = None) -> Component:
        """A later registration replaces the earlier one. A job kind Link does not know is ignored; the v2 kinds count
        only with a declaration (protocol 2.0)."""
        c = self._component(name)
        c.kind, c.version, c.capabilities = kind, version, list(capabilities)
        c.declaration = declaration
        known = JOB_KINDS + (V2_JOB_KINDS if declaration is not None else ())
        c.job_kinds = tuple(k for k in known if k in job_kinds)
        c.registered = True
        if "probe" not in c.job_kinds:
            c.local_ping_ms = None
        return c

    def beat(self, name: str, *, state: str, version: Optional[str]) -> Component:
        c = self._component(name)
        c.reported_state, c.last_beat = state, self.clock()
        if version and not c.version:
            c.version = version
        return c

    def components(self) -> list[Component]:
        return [self._components[n] for n in sorted(self._components)]

    def telemetry(self) -> list[dict]:
        now = self.clock()
        return [{"name": c.name, "kind": c.kind, "version": c.version, "state": c.state(now),
                 "capabilities": list(c.capabilities), "local_ping_ms": c.local_ping_ms if "probe" in c.job_kinds else None}
                for c in self.components()]
