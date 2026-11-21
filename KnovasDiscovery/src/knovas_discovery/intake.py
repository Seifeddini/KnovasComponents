"""intake.yaml: what the questionnaire found out, and where the scan may look. Written by a person."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .util import load_yaml

TEMPLATE = """\
# intake.yaml: filled in by the consultant after the questionnaire (docs/runbook.md, docs/fragebogen.md).
# The scan looks only where this file points. It holds NO password: name the environment variable that holds it
# (password_env, secret_env). If that variable is not set when the scan runs, the scan asks for it at the terminal.

client:
  key: example            # short key, lower case; it appears in findings.json (no other client name does)
  name: Example Ltd       # stays here; findings.json and the reports do not carry it
  sector: law-firm        # law-firm, fiduciary, engineering, other
  people: 0               # people who work there
  lawyers: 0              # of which lawyers (or the firm's professionals)
  it_provider: external   # external, internal, none

# What the client says it uses. The scan confirms or contradicts it. One entry per piece of software.
# category: practice, dms, mail, accounting, time, collaboration, web-api, other
# product:  a catalogue key if known (winjur, vertec, abacus, sharepoint, exchange-online, exchange-server, jira,
#           github), else the product's name in lower case, or "unknown"
software:
  - {category: practice, product: unknown}

targets:
  # File shares: `path` is where the share is visible on the scanning machine: a mount point (/mnt/share) or a
  # Windows UNC path (\\\\server\\share$). `roots` are folders below `path` to scan, one result each; empty: the
  # whole path. Nothing is opened: the scan lists folders and reads file sizes and dates.
  fileshares:
    - key: share
      path: /mnt/share
      roots: []

  # Directories (Active Directory over LDAPS, simple bind). verify_tls: false accepts a self-signed certificate;
  # ca_file: a CA certificate to verify against instead.
  directories:
    - key: ad
      url: "ldaps://dc.example.local"
      bind_user: "svc-knovas@example.local"
      password_env: DISCOVERY_LDAP_PASSWORD
      base_dn: "DC=example,DC=local"
      verify_tls: true

  # Databases. kind: mssql. The login needs to see the catalogue views only (VIEW DEFINITION or db_datareader).
  # The connection is always encrypted; verify_tls: false accepts a self-signed certificate.
  databases:
    - key: praxis
      kind: mssql
      host: sql.example.local
      port: 1433
      user: knovas_ro
      password_env: DISCOVERY_SQL_PASSWORD
      verify_tls: true

  # Microsoft 365: an app registration with read-only application permissions (User.Read.All, Directory.Read.All,
  # Sites.Read.All, Group.Read.All, Organization.Read.All) and admin consent. Leave the list empty if there is none.
  m365: []
  #  - {key: tenant, tenant_id: "", client_id: "", secret_env: DISCOVERY_M365_SECRET}

# Where Knovas will be reached from the client's network (the environment probe tries a TCP connection only).
knovas: {api_host: api.knovas.ch, api_port: 8443}
"""

FORBIDDEN_KEYS = {"password", "passwd", "pwd", "secret", "client_secret", "token", "api_key"}


class IntakeError(Exception):
    pass


def write_template(folder: Path, force: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "intake.yaml"
    if path.exists() and not force:
        raise IntakeError(f"{path} exists; use --force to overwrite it")
    path.write_text(TEMPLATE, encoding="utf-8", newline="\n")
    return path


def _check_no_secret(node: Any, where: str) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                raise IntakeError(f"intake.yaml must not hold a secret ({where}{k}): name an environment variable "
                                  f"in password_env or secret_env instead")
            _check_no_secret(v, f"{where}{k}.")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _check_no_secret(v, f"{where}{i}.")


def _list(value: Any, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise IntakeError(f"{what} must be a list")
    return value


def normalise(data: Any) -> dict:
    if not isinstance(data, dict):
        raise IntakeError("intake.yaml must be a mapping")
    _check_no_secret(data, "")
    client = data.get("client") or {}
    if not isinstance(client, dict) or not client.get("key"):
        raise IntakeError("client.key is required")
    targets = data.get("targets") or {}
    if not isinstance(targets, dict):
        raise IntakeError("targets must be a mapping")
    out = {
        "client": client,
        "software": [],
        "targets": {kind: _list(targets.get(kind), f"targets.{kind}")
                    for kind in ("fileshares", "directories", "databases", "m365")},
        "knovas": data.get("knovas") or {},
    }
    for item in _list(data.get("software"), "software"):
        if not isinstance(item, dict):
            raise IntakeError("each software entry must be a mapping with category and product")
        out["software"].append({"category": str(item.get("category") or "other").strip().lower(),
                                "product": str(item.get("product") or "unknown").strip().lower()})
    seen: set[str] = set()
    for kind, items in out["targets"].items():
        for t in items:
            if not isinstance(t, dict) or not t.get("key"):
                raise IntakeError(f"every entry of targets.{kind} needs a key")
            if t["key"] in seen:
                raise IntakeError(f"duplicate target key {t['key']!r}")
            seen.add(t["key"])
    return out


def load(path: Path) -> dict:
    try:
        data = load_yaml(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise IntakeError(f"no intake file at {path}") from None
    except yaml.YAMLError as exc:
        raise IntakeError(f"intake.yaml is not valid YAML: {exc}") from None
    return normalise(data)


def shareable_client(intake: dict) -> dict:
    """What findings.json may say about the client: no name."""
    c = intake["client"]
    return {k: c.get(k) for k in ("key", "sector", "people", "lawyers", "it_provider")}
