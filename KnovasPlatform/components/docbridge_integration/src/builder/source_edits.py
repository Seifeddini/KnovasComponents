"""Edits of a workspace's sources (build plan step B7).

A source is an element of the model's ``sources``: key, name, kind, the
component that serves it (its id in the tenant's register), owner, sync mode
(manual until B13 schedules runs), its access (read, or read and write, as the kind
allows), the kind's settings and whether a credential is set. The credential itself lives in the settings registry
(platform_settings.SourceSecrets); the model only records that it was set, so
the change shows in the review without the secret.

Whether the component exists and serves the kind is the route's check: it
knows the tenant's register, the model does not.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any, Mapping

from builder import sources as S
from builder.editing import OPERATIONS, SOURCES, EditError, _Edit, _set_text, _text

SOURCE_KEY = re.compile(r"^[a-z][a-z0-9-]{0,59}$")
#: Settings that say where a source is: changing one drops the credential.
HOST_SETTINGS = ("url", "repository")


def key_from_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name.replace("ß", "ss")).encode("ascii", "ignore").decode("ascii").lower()
    return "-".join(re.findall(r"[a-z0-9]+", folded))[:60].strip("-") or "quelle"


def _settings(kind: str, raw: Any) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise EditError("Einstellungen: Angaben erwartet.")
    out = {}
    for name, label, _ in S.SOURCE_KINDS[kind]["settings"]:
        value = _text(raw.get(name), label, limit=500)
        if value:
            out[name] = value
    problem = S.settings_problem(kind, out)
    if problem:
        raise EditError(problem)
    return out


def _source(edit: _Edit, key: Any) -> tuple[dict, dict]:
    old = edit.find(SOURCES, key)
    if old is None:
        raise EditError(f"Die Quelle «{key}» gibt es nicht.", 404)
    return old, copy.deepcopy(old)


def _access(kind: str, raw: Any) -> str:
    modes = S.access_modes(kind)
    if raw in (None, ""):
        return modes[0]
    if raw not in modes:
        raise EditError(f"Zugriff: Diese Art von Quelle erlaubt nur {', '.join(S.ACCESS[m] for m in modes)}.")
    return str(raw)


def _source_create(edit: _Edit, op: Mapping[str, Any]) -> None:
    kind = op.get("kind")
    if kind not in S.SOURCE_KINDS:
        raise EditError("Art: eine der angebotenen; CRM-Quellen sind geplant.")
    name = _text(op.get("name"), "Name", limit=120)
    if not name:
        raise EditError("Name: bitte angeben.")
    component = _text(op.get("component"), "Komponente", limit=60)
    if not component:
        raise EditError("Komponente: jede Quelle wird über eine registrierte Komponente gelesen.")
    key = _text(op.get("key"), "Schlüssel", limit=60) or key_from_name(name)
    if not SOURCE_KEY.match(key):
        raise EditError("Schlüssel: klein geschrieben, Wörter mit Bindestrich.")
    base, n = key, 2
    while edit.find(SOURCES, key) is not None:
        key, n = f"{base}-{n}", n + 1
    source = {"key": key, "name": name, "kind": kind, "component": component, "sync_mode": "manual",
              "access": _access(kind, op.get("access")),
              "settings": _settings(kind, op.get("settings")), "credential_set": False}
    _set_text(source, "owner", _text(op.get("owner"), "Verantwortlich", limit=120))
    edit.write(SOURCES, None, source)


def _source_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _source(edit, op.get("key"))
    fields = op.get("fields") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Felder: Angaben erwartet.")
    if "name" in fields:
        new["name"] = _text(fields["name"], "Name", limit=120) or new["name"]
    if "owner" in fields:
        _set_text(new, "owner", _text(fields["owner"], "Verantwortlich", limit=120))
    if "component" in fields:
        new["component"] = _text(fields["component"], "Komponente", limit=60) or new["component"]
    if "access" in fields:
        new["access"] = _access(new["kind"], fields["access"])
    if "settings" in fields:
        new["settings"] = _settings(new["kind"], fields["settings"])
        # A credential belongs to where it was set for: a new address or repository drops it
        # (the route removes it from the registry), so a token never goes to another host.
        if any(new["settings"].get(k) != old["settings"].get(k) for k in HOST_SETTINGS):
            new["credential_set"] = False
    edit.write(SOURCES, old, new)


def _source_credential(edit: _Edit, op: Mapping[str, Any]) -> None:
    """That the credential was set or removed; the value is the registry's, never the model's."""
    old, new = _source(edit, op.get("key"))
    new["credential_set"] = bool(op.get("set"))
    edit.write(SOURCES, old, new)


def _source_remove(edit: _Edit, op: Mapping[str, Any]) -> None:
    """A source no mapping reads. Its credential goes with it (builder_routes._forget_moved_credentials)."""
    old, _ = _source(edit, op.get("key"))
    used = [str((m.get("definition") or {}).get("id")) for m in edit.model.get("mappings") or []
            if (m.get("builder") or {}).get("source") == old.get("key")]
    if used:
        raise EditError("Diese Zuordnungen lesen noch aus der Quelle; zuerst entfernen: " + ", ".join(sorted(used)) + ".")
    edit.write(SOURCES, old, None)


OPERATIONS.update({
    "source.create": _source_create,
    "source.update": _source_update,
    "source.credential": _source_credential,
    "source.remove": _source_remove,
})
