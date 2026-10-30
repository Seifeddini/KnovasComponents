"""Settings an administrator changes on the settings page, and where each value comes from.

The registry (``REGISTRY``) lists every key the page may change: its German label and help text, its
type (text, number, choice, role-to-profile map, secret), its validation, and whether it belongs to
one tenant or to the whole installation. Adding a key is one entry.

Where a value comes from, highest first:

1. **deployment**: an environment variable the deployment sets (non-empty) pins it, and the page
   shows it read-only with that reason. A secret is also pinned by a token file that exists.
2. **page**: a value saved on the settings page (table ``platform_settings``, migration 0009).
3. **default**: config.yaml's default for the key (``${VAR:-default}``), else the code's.

Secrets are encrypted with the deployment's ``PLATFORM_SETTINGS_KEY`` (a Fernet key, or a file named
by ``PLATFORM_SETTINGS_KEY_FILE``) before they are stored. They are never returned to the browser
and never logged; without the key they cannot be saved and the page says so.

``Resolver`` reads through all three, cached for a few seconds, so a change applies without a
restart: ``view_settings()`` is what the views read.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

KINDS = ("text", "number", "choice", "role_map", "secret")
SECRET_SET = "geändert"                      # what the audit log says about a secret: never its value


class SettingError(Exception):
    """A value is refused; the message is for the page."""

    status = 400


class Pinned(SettingError):
    """The deployment pins this key; the page cannot change it."""

    status = 409


class NoSecretKey(SettingError):
    """No (valid) PLATFORM_SETTINGS_KEY: a secret cannot be saved."""

    status = 409


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    help: str
    kind: str
    scope: str                               # "tenant" | "installation"
    env: Tuple[str, ...] = ()                # environment variables that pin the value
    file_key: Optional[str] = None           # config key naming a file that pins a secret
    integer: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Tuple[str, ...] = ()
    check: Optional[Callable[[str], None]] = None
    fallback: Any = ""                       # when config.yaml has no default either


def _bundle_source(value: str) -> None:
    from graph_views.bundle import BundleError, parse_source

    try:
        parse_source(value)
    except BundleError as exc:
        raise SettingError(str(exc)) from None


REGISTRY: Dict[str, Setting] = {s.key: s for s in (
    Setting("views.config_source", "Definitionsbündel",
            "Woher die Ansichten die Definitionen dieses Mandanten lesen: file:<Pfad> oder "
            "github:<owner>/<repo>@<ref>:<Pfad zu bundle.json>.",
            "text", "tenant", env=("VIEWS_CONFIG_SOURCE",), check=_bundle_source),
    Setting("views.profile_roles", "Rollen und Profile",
            "Welche Profile des Definitionsbündels die Rollen der Plattform sehen (Profil-Id oder Zielgruppe). "
            "Leer: alle sehen alle Profile.",
            "role_map", "tenant", env=("VIEWS_PROFILE_ROLES",)),
    Setting("views.map_fact_budget", "Lesebudget der Systemkarte",
            "Wie viele Fakten eine Ebene der Systemkarte höchstens liest (bezahlt aus dem Lesebudget des Mandanten).",
            "number", "installation", env=("VIEWS_MAP_FACT_BUDGET",), integer=True, minimum=0, maximum=10_000,
            fallback=120),
    Setting("views.ttl_seconds", "Gültigkeit des Graph-Abbilds (Sekunden)",
            "Wie lange die Ansichten das Abbild des Wissensgraphen wiederverwenden, bevor sie es neu lesen.",
            "number", "installation", env=("VIEWS_TTL_SECONDS",), integer=True, minimum=5, maximum=3600, fallback=60),
    Setting("views.definitions_ttl_seconds", "Gültigkeit der Definitionen (Sekunden)",
            "Wie lange das Definitionsbündel und die Definitionen gelten, bevor sie neu gelesen werden.",
            "number", "installation", env=("VIEWS_DEFINITIONS_TTL_SECONDS",), integer=True, minimum=10,
            maximum=86_400, fallback=600),
    Setting("views.edge_limit", "Kantengrenze",
            "Ab dieser Zahl von Kanten liest eine Ansicht Knoten für Knoten statt der ganzen Kantenliste.",
            "number", "installation", env=("VIEWS_EDGE_LIMIT",), integer=True, minimum=1000, maximum=10_000_000,
            fallback=100_000),
    Setting("views.edge_budget_seconds", "Zeitbudget der Kantenliste (Sekunden)",
            "Wie lange das Lesen der Kantenliste höchstens dauern darf.",
            "number", "installation", env=("VIEWS_EDGE_BUDGET_SECONDS",), minimum=0.1, maximum=60, fallback=2.0),
    Setting("views.change_requests.github_token", "Token für die Wissensbasis (GitHub)",
            "Liest das Definitionsbündel und öffnet Änderungsanträge. Rechte: Contents und Pull requests auf "
            "dem Repository der Wissensbasis, sonst nichts. Wird nur geschrieben, nie angezeigt.",
            "secret", "tenant", env=("KB_GITHUB_TOKEN",), file_key="views.change_requests.github_token_file"),
)}


# -- validation -------------------------------------------------------------------------------------------

def _number(s: Setting, raw: Any) -> Any:
    text = str(raw).strip().replace(",", ".") if raw is not None else ""
    try:
        value = float(text)
    except ValueError:
        raise SettingError(f"{s.label}: {raw!r} ist keine Zahl.") from None
    if s.integer:
        if value != int(value):
            raise SettingError(f"{s.label} muss eine ganze Zahl sein.")
        value = int(value)
    if s.minimum is not None and value < s.minimum:
        raise SettingError(f"{s.label} ist mindestens {s.minimum:g}.")
    if s.maximum is not None and value > s.maximum:
        raise SettingError(f"{s.label} ist höchstens {s.maximum:g}.")
    return value


def _role_map(s: Setting, raw: Any, roles: Iterable[str]) -> str:
    known = {str(r) for r in roles}
    if isinstance(raw, Mapping):
        pairs = [(str(k).strip(), v) for k, v in raw.items()]
    else:
        pairs = []
        for part in re.split(r"[;\n]", str(raw or "")):
            if not part.strip():
                continue
            role, sep, names = part.partition("=")
            if not sep:
                raise SettingError(f"{s.label}: {part.strip()!r} ist nicht Rolle=Profil[, Profil].")
            pairs.append((role.strip(), names))
    out: Dict[str, List[str]] = {}
    for role, names in pairs:
        if role not in known:
            raise SettingError(f"{s.label}: Unbekannte Rolle {role!r} (bekannt: {', '.join(sorted(known))}).")
        items = names if isinstance(names, (list, tuple)) else str(names or "").split(",")
        cleaned = [str(n).strip() for n in items if str(n).strip()]
        if any(len(n) > 80 or "=" in n or ";" in n for n in cleaned):
            raise SettingError(f"{s.label}: ein Profilname ist zu lang oder enthält = oder ;.")
        if cleaned:
            out.setdefault(role, [])
            out[role] += [n for n in cleaned if n not in out[role]]
    return "; ".join(f"{role}={', '.join(names)}" for role, names in sorted(out.items()))


def clean(s: Setting, raw: Any, *, roles: Iterable[str] = ()) -> Any:
    """The value to store, in its canonical form; SettingError with the reason otherwise."""
    if s.kind == "number":
        return _number(s, raw)
    if s.kind == "role_map":
        return _role_map(s, raw, roles)
    text = "" if raw is None else str(raw)
    if s.kind == "secret":
        if not text.strip():
            raise SettingError(f"{s.label} darf nicht leer sein (zum Entfernen: Zurücksetzen).")
        if re.search(r"\s", text.strip()):
            raise SettingError(f"{s.label} enthält Leerzeichen oder Zeilenumbrüche.")
        if len(text.strip()) > 500:
            raise SettingError(f"{s.label} ist länger als 500 Zeichen.")
        return text.strip()
    text = text.strip()
    if len(text) > 500:
        raise SettingError(f"{s.label} ist länger als 500 Zeichen.")
    if s.kind == "choice":
        if text not in s.choices:
            raise SettingError(f"{s.label} ist eines von {', '.join(s.choices)}.")
        return text
    if text and s.check is not None:
        s.check(text)
    return text


# -- secrets ----------------------------------------------------------------------------------------------

class SecretBox:
    """Encrypts secrets with the deployment's key; without one, ``available`` is False and ``reason`` says why."""

    def __init__(self, key: Optional[str], reason: str = ""):
        self._fernet = None
        self.reason = reason
        if key:
            from cryptography.fernet import Fernet

            try:
                self._fernet = Fernet(key.strip().encode("ascii"))
            except (ValueError, UnicodeEncodeError):
                self.reason = "PLATFORM_SETTINGS_KEY ist ungültig (erwartet: ein Fernet-Schlüssel)."
        elif not reason:
            self.reason = "PLATFORM_SETTINGS_KEY ist nicht gesetzt: Geheimnisse können nicht gespeichert werden."

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "SecretBox":
        env = os.environ if environ is None else environ
        key = (env.get("PLATFORM_SETTINGS_KEY") or "").strip()
        path = (env.get("PLATFORM_SETTINGS_KEY_FILE") or "").strip()
        if not key and path:
            try:
                key = Path(path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                return cls(None, f"PLATFORM_SETTINGS_KEY_FILE nicht lesbar ({type(exc).__name__}).")
        return cls(key or None)

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def seal(self, text: str) -> bytes:
        if self._fernet is None:
            raise NoSecretKey(self.reason)
        return self._fernet.encrypt(text.encode("utf-8"))

    def open(self, sealed: Any) -> Optional[str]:
        if self._fernet is None or not sealed:
            return None
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(bytes(sealed)).decode("utf-8")
        except InvalidToken:
            logger.warning("platform settings: a stored secret does not open with this key (InvalidToken)")
            return None


# -- the page layer ---------------------------------------------------------------------------------------

class SettingStore:
    """Rows of ``platform_settings`` and the audit entry for every change, on one connection."""

    def __init__(self, conn: Any, *, owned: bool = False):
        self._conn = conn
        self._owned = owned

    def close(self) -> None:
        if self._owned:
            try:
                self._conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("platform settings: close failed (%s)", type(exc).__name__)

    def rows(self, tenant: str) -> Dict[str, Dict[str, Any]]:
        """The tenant's rows and the installation's (tenant '')."""
        found = self._conn.execute(
            "SELECT s.key, s.value, s.secret, u.email, s.updated_at FROM platform_settings s "
            "LEFT JOIN users u ON u.id = s.updated_by WHERE s.tenant IN (%s, '')", (tenant,)).fetchall()
        return {k: {"value": v, "secret": sec, "updated_by": str(email) if email else None,
                    "updated_at": at.isoformat() if hasattr(at, "isoformat") else at}
                for k, v, sec, email, at in found}

    def _audit(self, user: Any, key: str, tenant: str, old: Any, new: Any) -> None:
        from identity import audit

        audit.record(self._conn, action="settings.changed", actor=user, target_type="platform_setting",
                     target_id=key, detail={"key": key, "tenant": tenant, "old": old, "new": new})

    def put(self, key: str, tenant: str, value: Any, user: Any, old: Any = None) -> None:
        self._conn.execute(
            "INSERT INTO platform_settings (tenant, key, value, secret, updated_by, updated_at) "
            "VALUES (%s, %s, %s, NULL, %s, now()) ON CONFLICT (tenant, key) DO UPDATE SET value = EXCLUDED.value, "
            "secret = NULL, updated_by = EXCLUDED.updated_by, updated_at = now()",
            (tenant, key, json.dumps(value), str(user.id)))
        self._audit(user, key, tenant, old, value)

    def put_secret(self, key: str, tenant: str, sealed: bytes, user: Any) -> None:
        self._conn.execute(
            "INSERT INTO platform_settings (tenant, key, value, secret, updated_by, updated_at) "
            "VALUES (%s, %s, NULL, %s, %s, now()) ON CONFLICT (tenant, key) DO UPDATE SET value = NULL, "
            "secret = EXCLUDED.secret, updated_by = EXCLUDED.updated_by, updated_at = now()",
            (tenant, key, sealed, str(user.id)))
        self._audit(user, key, tenant, SECRET_SET, SECRET_SET)

    def clear(self, key: str, tenant: str, user: Any, old: Any = None) -> None:
        self._conn.execute("DELETE FROM platform_settings WHERE tenant = %s AND key = %s", (tenant, key))
        self._audit(user, key, tenant, old, None)


# -- reading through deployment, page and default ---------------------------------------------------------

@dataclass
class Effective:
    value: Any
    source: str                               # "deployment" | "page" | "default"
    detail: Dict[str, Any] = field(default_factory=dict)
    pinned: bool = False


_DEFAULT = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$", re.S)


class Resolver:
    """Effective settings for one tenant: deployment pin, else page value, else config.yaml default."""

    def __init__(self, config: Any, *, tenant: str = "", store: Optional[Callable[[], Any]] = None,
                 box: Optional[SecretBox] = None, environ: Optional[Mapping[str, str]] = None, ttl: float = 5.0,
                 now: Callable[[], float] = time.monotonic):
        self._config = config
        self.tenant = str(tenant or "")
        self._store = store
        self.box = box if box is not None else SecretBox.from_env(environ)
        self._env = os.environ if environ is None else environ
        self._ttl = ttl
        self._now = now
        self._lock = threading.Lock()
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._raw = self._read_raw()

    # -- the three layers ----------------------------------------------------------------------------

    def _read_raw(self) -> Dict[str, Any]:
        path = getattr(self._config, "config_path", None)
        try:
            return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {} if path else {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("platform settings: config.yaml not readable for defaults (%s)", type(exc).__name__)
            return {}

    def _default(self, s: Setting) -> Any:
        node: Any = self._raw
        for part in s.key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return s.fallback
        text = str(node)
        m = _DEFAULT.match(text.strip())
        if m:
            text = m.group(2) or ""
        if s.kind == "number":
            try:
                return _number(s, text) if text != "" else s.fallback
            except SettingError:
                return s.fallback
        return text

    def _pin(self, s: Setting) -> Optional[Tuple[Any, str]]:
        for name in s.env:
            value = self._env.get(name)
            if value is not None and str(value).strip() != "":
                return value, f"Umgebungsvariable {name} der Installation"
        if s.file_key:
            path = str(self._config.get(s.file_key, "") or "").strip()
            if path and os.path.isfile(path):
                return None, f"Datei {path} ({s.file_key})"
        return None

    def _rows(self) -> Dict[str, Dict[str, Any]]:
        return self._cached("rows", self._load_rows)

    def _load_rows(self) -> Dict[str, Dict[str, Any]]:
        if self._store is None:
            return {}
        store = None
        try:
            store = self._store()
            return store.rows(self.tenant) if store is not None else {}
        except Exception as exc:  # noqa: BLE001 - no page layer then; deployment and defaults still apply
            logger.warning("platform settings: page values not readable (%s)", type(exc).__name__)
            return {}
        finally:
            if store is not None and hasattr(store, "close"):
                store.close()

    def _cached(self, name: str, build: Callable[[], Any]) -> Any:
        now = self._now()
        with self._lock:
            hit = self._cache.get(name)
            if hit is not None and now - hit[0] < self._ttl:
                return hit[1]
        value = build()
        with self._lock:
            self._cache[name] = (now, value)
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    # -- reading -------------------------------------------------------------------------------------

    def effective(self, key: str) -> Effective:
        s = REGISTRY[key]
        pin = self._pin(s)
        if pin is not None:
            value, reason = pin
            if s.kind == "secret":
                value = None
            elif s.kind == "number":
                try:
                    value = _number(s, value)
                except SettingError:
                    pass
            return Effective(value, "deployment", {"reason": reason}, pinned=True)
        row = self._rows().get(key)
        if row is not None:
            detail = {"updated_by": row.get("updated_by"), "updated_at": row.get("updated_at")}
            if s.kind == "secret":
                return Effective(None, "page", detail)
            value = row.get("value")
            if isinstance(value, str) and s.kind != "text" and s.kind != "role_map":
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            return Effective(value, "page", detail)
        return Effective(None if s.kind == "secret" else self._default(s), "default", {})

    def value(self, key: str) -> Any:
        return self.effective(key).value

    def secret(self, key: str) -> Optional[str]:
        """A secret's plain value for the Platform's own use (never for a response)."""
        s = REGISTRY[key]
        for name in s.env:
            value = self._env.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
        if s.file_key:
            path = str(self._config.get(s.file_key, "") or "").strip()
            if path and os.path.isfile(path):
                try:
                    return Path(path).read_text(encoding="utf-8").strip() or None
                except OSError as exc:
                    logger.warning("platform settings: token file not readable (%s)", type(exc).__name__)
                    return None
        row = self._rows().get(key)
        return self.box.open(row.get("secret")) if row else None

    def entries(self) -> List[Dict[str, Any]]:
        """Every registered setting as the page shows it; a secret only as set or not set."""
        out = []
        for key, s in REGISTRY.items():
            e = self.effective(key)
            entry = {"key": key, "label": s.label, "help": s.help, "kind": s.kind, "scope": s.scope,
                     "source": e.source, "pinned": e.pinned, "reason": e.detail.get("reason"),
                     "updated_by": e.detail.get("updated_by"), "updated_at": e.detail.get("updated_at"),
                     "default": None if s.kind == "secret" else self._default(s)}
            if s.kind == "secret":
                # "readable": a page value that opens with this installation's key (a changed key
                # leaves it set but unusable, and the page says so).
                entry.update(value=None, set=e.source != "default", secrets=self.box.available,
                             secrets_reason=None if self.box.available else self.box.reason,
                             readable=e.source != "page" or self.secret(key) is not None)
            else:
                entry["value"] = e.value
            if s.kind == "number":
                entry.update(integer=s.integer, minimum=s.minimum, maximum=s.maximum)
            if s.kind == "choice":
                entry["choices"] = list(s.choices)
            out.append(entry)
        return out

    # -- writing -------------------------------------------------------------------------------------

    def save(self, user: Any, values: Mapping[str, Any], *, roles: Iterable[str]) -> List[str]:
        """Validate every value first, then write them; ``None`` (or an empty text) removes the page value."""
        plan = []
        for key, raw in (values or {}).items():
            s = REGISTRY.get(key)
            if s is None:
                raise SettingError(f"Unbekannte Einstellung {key!r}.")
            pin = self._pin(s)
            if pin is not None:
                raise Pinned(f"{s.label} legt die Installation fest ({pin[1]}); hier nicht änderbar.")
            remove = raw is None or (s.kind != "secret" and str(raw).strip() == "" and not isinstance(raw, Mapping))
            if remove:
                plan.append((s, None, True))
            elif s.kind == "secret":
                if not self.box.available:
                    raise NoSecretKey(self.box.reason)
                plan.append((s, self.box.seal(clean(s, raw, roles=roles)), False))
            else:
                plan.append((s, clean(s, raw, roles=roles), False))
        if not plan:
            return []
        if self._store is None:
            raise SettingError("Einstellungen speichern braucht Benutzerkonten (identity.enabled).")
        before = {s.key: self.effective(s.key) for s, _, _ in plan}
        store = self._store()
        changed = []
        try:
            for s, value, remove in plan:
                tenant = self.tenant if s.scope == "tenant" else ""
                old = before[s.key]
                old_value = SECRET_SET if s.kind == "secret" else old.value
                if remove:
                    if old.source == "page":
                        store.clear(s.key, tenant, user, old=old_value)
                        changed.append(s.key)
                elif s.kind == "secret":
                    store.put_secret(s.key, tenant, value, user)
                    changed.append(s.key)
                elif not (old.source == "page" and old.value == value):
                    store.put(s.key, tenant, value, user, old=old_value)
                    changed.append(s.key)
        finally:
            if hasattr(store, "close"):
                store.close()
            self.invalidate()
        return changed

    # -- what the views read --------------------------------------------------------------------------

    def view_settings(self):
        """ViewSettings with every registered ``views.<field>`` read through deployment, page and default."""
        return self._cached("view_settings", self._build_view_settings)

    def _build_view_settings(self):
        from graph_views.settings import ViewSettings

        base = ViewSettings.from_config(self._config)
        fields = {f.name for f in dataclasses.fields(ViewSettings)}
        over = {}
        for key, s in REGISTRY.items():
            name = key[len("views."):] if key.startswith("views.") else None
            if name in fields and s.kind != "secret":
                value = self.value(key)
                over[name] = value if value is not None else getattr(base, name)
        return dataclasses.replace(base, **over)


# -- the Test buttons -------------------------------------------------------------------------------------

def test_bundle_source(spec: str, github: Optional[Callable[[str], Any]] = None) -> Dict[str, Any]:
    """Load a bundle source as the views would: its format version, hash and counts, or the exact error."""
    from graph_views import bundle

    try:
        b = bundle.read(str(spec or "").strip(), github)
    except bundle.BundleError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the page names the failure; no exception text (it may quote data)
        logger.warning("platform settings: bundle test failed (%s)", type(exc).__name__)
        return {"ok": False, "error": f"Unerwarteter Fehler ({type(exc).__name__})."}
    return {"ok": True, "label": b.label, "format_version": b.header.get("format_version"),
            "content_hash": b.content_hash, "source_commit": b.source_commit, "report_errors": b.report_errors,
            "counts": {"types": len(b.types.get("node_types") or []),
                       "processes": len(b.processes.get("processes") or []),
                       "profiles": len(b.profiles or {})}}


def test_repository_access(repo: str, token: Optional[str], make: Callable[[str, str], Any]) -> Dict[str, Any]:
    """Whether a token reads ``repo``; never echoes the token."""
    from graph_views.github_repo import GitHubError

    if not token:
        return {"ok": False, "error": "Es ist kein Token gesetzt."}
    if not repo:
        return {"ok": False, "error": "Kein Repository konfiguriert (views.change_requests.github_repo)."}
    try:
        access = make(repo, token).access()
    except GitHubError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("platform settings: repository test failed (%s)", type(exc).__name__)
        return {"ok": False, "error": f"Unerwarteter Fehler ({type(exc).__name__})."}
    return {"ok": bool(access.get("read")), "repository": repo, "read": bool(access.get("read")),
            "write": bool(access.get("write"))}
