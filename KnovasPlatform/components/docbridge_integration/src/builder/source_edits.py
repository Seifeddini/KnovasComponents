"""Edits of a workspace's sources (build plan step B7).

A source is an element of the model's ``sources``: key, name, kind, the
component that serves it (its id in the tenant's register), owner, sync mode
(manual until B13 schedules runs), its access (read, or read and write, as the kind
allows), the kind's settings and whether a credential is set. The credential itself lives in the settings registry
(platform_settings.SourceSecrets); the model only records that it was set, so
the change shows in the review without the secret.

Whether the component exists and serves the kind is the route's check: it
knows the tenant's register, the model does not.

With BUILDER_PROTOCOL_V2 on, a source may also be of a kind the catalogue has no form for, which a connector at a site
declared (SPEC-write-back-13): its settings are names and values (sources.generic_settings), never a credential, and
whether the chosen component declared the kind is again the route's check.
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


def _settings(kind: str, raw: Any, key: str = "") -> dict:
    if kind not in S.SOURCE_KINDS:
        # A kind the Builder has no form for (protocol 2.0, SPEC-write-back-13): names and values, no credential.
        try:
            return S.generic_settings(raw, key or None, kind)
        except S.SettingsError as exc:
            raise EditError(str(exc)) from None
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
    if kind not in S.SOURCE_KINDS and not S.generic_kind(kind):
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
              "settings": _settings(kind, op.get("settings"), key), "credential_set": False}
    _set_text(source, "owner", _text(op.get("owner"), "Verantwortlich", limit=120))
    edit.write(SOURCES, None, source)


def _source_update(edit: _Edit, op: Mapping[str, Any]) -> None:
    old, new = _source(edit, op.get("key"))
    fields = op.get("fields") or {}
    if not isinstance(fields, Mapping):
        raise EditError("Felder: Angaben erwartet.")
    if ("settings" in fields or "component" in fields) and new["kind"] not in S.SOURCE_KINDS \
            and not S.generic_kind(new["kind"]):
        # A kind only a connector declares is set up only while protocol 2.0 is on (its switch hides the form).
        raise EditError("Einstellungen und Komponente dieser Art von Quelle werden nur mit Protokoll 2 geändert.", 409)
    if "name" in fields:
        new["name"] = _text(fields["name"], "Name", limit=120) or new["name"]
    if "owner" in fields:
        _set_text(new, "owner", _text(fields["owner"], "Verantwortlich", limit=120))
    if "component" in fields:
        new["component"] = _text(fields["component"], "Komponente", limit=60) or new["component"]
    if "access" in fields:
        new["access"] = _access(new["kind"], fields["access"])
    if "settings" in fields:
        new["settings"] = _settings(new["kind"], fields["settings"], new["key"])
        # A credential belongs to where it was set for: a new address or repository drops it
        # (the route removes it from the registry), so a token never goes to another host.
        if any(new["settings"].get(k) != old["settings"].get(k) for k in HOST_SETTINGS):
            new["credential_set"] = False
    edit.write(SOURCES, old, new)


def _source_switches(edit: _Edit, op: Mapping[str, Any]) -> None:
    """The source's switches (connection layer MVP-25): ``fetch`` -- agents may fetch its originals -- and ``access``
    -- read, or read_write so that accepted changes are written back. Both off until switched on here; like every
    edit, a switch is reviewed and published before the component sees it (store.served_source). Whether the
    component serving the source can write is the route's check: it knows the register.

    With BUILDER_PROTOCOL_V2 on also ``staging`` (plan C-Q1): how approved writes reach the source, ``direct`` or
    ``native_review``; «Freigaben» sends the published value with each approval. Whether the component declared that
    mode is the route's check too."""
    from builder import protocol_v2

    old, new = _source(edit, op.get("key"))
    if "fetch" not in op and "access" not in op and "staging" not in op:
        raise EditError("Schalter: fetch, access oder staging angeben.")
    if "staging" in op:
        if not protocol_v2.enabled():
            raise EditError("Die Schreibweise wird erst mit Protokoll 2 gewählt.", 409)
        if op["staging"] not in protocol_v2.STAGINGS:
            raise EditError("Schreibweise: direkt (direct) oder als Vorschlag zur Prüfung in der Quelle (native_review).")
        new["staging"] = op["staging"]
    if "fetch" in op:
        if not isinstance(op["fetch"], bool):
            raise EditError(f"«{S.SWITCHES['fetch']}»: an oder aus.")
        # A kind the Builder has no form for: the serving component's declaration decides (the route's check).
        if op["fetch"] and not S.generic_kind(new.get("kind")):
            why = S.fetch_offered(new.get("kind"))
            if why:
                raise EditError(why)
        new["fetch"] = op["fetch"]
    if "access" in op:
        if op["access"] not in S.ACCESS:
            raise EditError(f"«{S.SWITCHES['write']}»: {', '.join(S.ACCESS.values())}.")
        # With protocol 2.0 the serving component's declaration decides whether a kind can be written (the route's
        # check, SPEC-write-back-13), never this catalogue; without it, the catalogue's kinds that write.
        if op["access"] == "read_write" and not protocol_v2.enabled() \
                and not S.SOURCE_KINDS.get(new.get("kind"), {}).get("write"):
            raise EditError("In diese Art von Quelle kann noch nichts zurückgeschrieben werden.")
        new["access"] = op["access"]
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
    "source.switches": _source_switches,
    "source.remove": _source_remove,
})
