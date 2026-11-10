"""Connector protocol 2.0 at the site (draft; ``KnovasPlatform/docs/connector-protocol-v2.md``; BET-2026-11-write-back
WB-10, WB-11, the site halves of WB-21, WB-06, WB-25). Only while ``LINK_PROTOCOL_V2`` is on.

What it adds to the 1.x Link:

- **The site declaration.** ``PUT /secured/connector/v2/site`` with the site's id (Link's component id from its
  enrolment), the protocol, Link's version and, per connector that registered with a declaration, the declaration and
  the source keys assigned to it whose kind equals the declared source kind (the one generic matching rule; no rule
  here reads a kind's value). Sent once the Brain is reachable and whenever a connector registers again.
- **Permit keys.** ``GET /secured/connector/v2/permit-keys``, pinned on Link's volume, read again at start and when a
  permit names a key Link does not hold (at most once a minute).
- **The write path.** ``write_path.check_act`` before an ``act`` reaches its connector; the used-permit store and the
  write log; the first result kept under every permit id before it leaves Link.

The site's tenant and certificate serial, which permits name, come from the certificate Link uses for the Brain.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Optional

from cryptography import x509

from knovas_link import VERSION, certificate
from knovas_link.brain_client import BrainRefused, BrainUnreachable
from knovas_link.permits import KeyRing
from knovas_link.write_path import PermitStore, utc_now

if TYPE_CHECKING:                                     # pragma: no cover
    from knovas_link.link import Link

log = logging.getLogger("knovas_link.protocol_v2")

PROTOCOL = "2.0"
KEYS_AGAIN_SECONDS = 60


class ProtocolV2:
    def __init__(self, link: "Link", *, clock=time.time):
        self.link = link
        self.clock = clock
        self.folder = link.state_dir / "v2"
        self.keys_path = self.folder / "permit-keys.json"
        self.ring = KeyRing()
        self.store = PermitStore(self.folder, clock=clock)
        self._keys_read_at: Optional[float] = None
        self.declared: Optional[dict] = None              # the last site declaration the Brain took
        try:
            self.ring.keys = KeyRing.parse(json.loads(self.keys_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            self.ring = KeyRing()

    # ------------------------------------------------------------------ who this site is

    def identity(self) -> tuple[Optional[str], Optional[str]]:
        """(tenant, serial) of the certificate Link uses for the Brain; (None, None) while it waits for one."""
        link = self.link
        path = None
        if link.certificate_mode == "issued" and link.credentials is not None:
            path = link.credentials.certificate
        elif link.certificate_mode == "test_only_shared":
            path = link.settings.brain_cert
        if not path:
            return None, None
        try:
            leaf = x509.load_pem_x509_certificate(open(path, "rb").read())
        except (OSError, ValueError):
            return None, None
        # The tenant the certificate names; install-certificate took it only for the tenant the Platform named at
        # enrolment, which stands in where a certificate carries none (the test-only shared one, older test CAs).
        return certificate.tenant_of(leaf) or link.store.tenant(), str(leaf.serial_number)

    def site_id(self) -> Optional[str]:
        return self.link.credentials.component if self.link.credentials is not None else None

    # ------------------------------------------------------------------ the site declaration

    def site_body(self) -> Optional[dict]:
        site_id = self.site_id()
        if not site_id:
            return None
        connectors = []
        for c in self.link.registry.components():
            if c.registered and c.declaration is not None:
                kind = c.declaration["source_kind"]
                sources = sorted(s["key"] for s in self.link.assigned(c.name) if s.get("kind") == kind)
                connectors.append({"declaration": c.declaration, "sources": sources[:32]})
        return {"site_id": site_id, "protocol": PROTOCOL, "link_version": VERSION, "connectors": connectors[:32]}

    async def declare_site(self) -> bool:
        brain, body = self.link.brain, self.site_body()
        if brain is None or body is None or not self.link.certificate_present:
            return False
        try:
            await brain.declare_site(body)
        except (BrainUnreachable, BrainRefused) as exc:
            log.warning("v2.site_not_declared (%s)", getattr(exc, "status", None) or type(exc).__name__)
            return False
        self.declared = body
        log.info("v2.site_declared, connectors %d", len(body["connectors"]))
        return True

    # ------------------------------------------------------------------ permit keys

    async def refresh_keys(self, *, force: bool = False) -> bool:
        now = self.clock()
        if not force and self._keys_read_at is not None and now - self._keys_read_at < KEYS_AGAIN_SECONDS:
            return False
        self._keys_read_at = now
        brain = self.link.brain
        if brain is None or not self.link.certificate_present:
            return False
        try:
            document = await brain.permit_keys()
            held = self.ring.pin(document, utc_now())
        except (BrainUnreachable, BrainRefused, ValueError) as exc:
            log.warning("v2.permit_keys_not_read (%s)", getattr(exc, "status", None) or type(exc).__name__)
            return False
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.keys_path.with_name(self.keys_path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.ring.to_json(), handle)
        os.replace(tmp, self.keys_path)
        log.info("v2.permit_keys_pinned %d", held)
        return True

    async def start(self) -> None:
        """Once the Brain is reachable: pin the keys and declare the site."""
        await self.refresh_keys(force=True)
        await self.declare_site()
