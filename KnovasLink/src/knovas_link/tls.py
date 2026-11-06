"""TLS contexts for Link's two outbound lines: the Platform and secure-api."""
from __future__ import annotations

import ssl
from typing import Optional

import certifi


def context(ca_file: Optional[str] = None, cert: Optional[str] = None, key: Optional[str] = None) -> ssl.SSLContext:
    """Checks the server against ``ca_file`` (else the system's and the public CAs) and, with ``cert``, presents
    that client certificate."""
    if ca_file:
        ctx = ssl.create_default_context(cafile=ca_file)
    else:
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(certifi.where())
    if cert:
        ctx.load_cert_chain(cert, key)
    return ctx
