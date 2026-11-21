"""directory: count people, service accounts, groups and nesting in a directory over LDAPS (simple bind).

The LDAP access is a thin reader (`Ldap3Reader`) with two methods, `root_dse()` and `search()`; the facts are
computed from plain entries by `directory_facts()`, which the tests feed with fake entries.
"""

from __future__ import annotations

import ssl
from urllib.parse import urlparse

from ..util import ProbeError, error_code, result
from . import Context, Output

FACTS = {
    "is_active_directory": "the root DSE shows Active Directory (its capability OID or a defaultNamingContext)",
    "people_enabled": "enabled user accounts that are people (not service or built-in accounts)",
    "people_disabled": "disabled user accounts that are people (userAccountControl bit 2)",
    "service_accounts": "accounts that look like services: name starts with svc, sa- or service, or in an OU named *Dienst* or *Service*",
    "groups": "groups the firm made (not in CN=Builtin, not the default groups in CN=Users)",
    "groups_empty": "of those, groups without any member",
    "max_nesting": "longest chain of group-in-group among those groups (0: no group is in another)",
    "computers": "computer accounts",
}

AD_CAPABILITY = "1.2.840.113556.1.4.800"
BUILTIN_RIDS = {500, 501, 502}                    # Administrator, Guest, krbtgt
BUILTIN_NAMES = {"administrator", "guest", "krbtgt"}
SERVICE_PREFIXES = ("svc", "sa-", "service")
SERVICE_OU_WORDS = ("dienst", "service")
# Groups AD or Samba creates in CN=Users with a relative id of 1000 or more.
DEFAULT_USERS_GROUPS = {"dnsadmins", "dnsupdateproxy", "winrmremotewmiusers__"}
UAC_DISABLED = 0x2

USER_FILTER = "(&(objectCategory=person)(objectClass=user))"
GROUP_FILTER = "(objectClass=group)"
COMPUTER_FILTER = "(objectCategory=computer)"
OU_FILTER = "(objectClass=organizationalUnit)"
USER_ATTRS = ["sAMAccountName", "userAccountControl", "objectSid"]
GROUP_ATTRS = ["sAMAccountName", "cn", "member", "objectSid"]


# --------------------------------------------------------------------------- the thin LDAP layer
class Ldap3Reader:
    """LDAPS with a simple bind, read-only, paged searches. Values come back as lists of str (objectSid: bytes)."""

    def __init__(self, url: str, user: str, password: str, verify_tls: bool = True, ca_file: str | None = None,
                 timeout: int = 20):
        import ldap3

        u = urlparse(url)
        if u.scheme.lower() != "ldaps":
            raise ProbeError("ldaps_required")
        tls = ldap3.Tls(validate=ssl.CERT_REQUIRED if verify_tls else ssl.CERT_NONE, ca_certs_file=ca_file)
        server = ldap3.Server(u.hostname, port=u.port or 636, use_ssl=True, tls=tls, get_info=ldap3.NONE,
                              connect_timeout=timeout)
        self._ldap3 = ldap3
        self._conn = ldap3.Connection(server, user=user, password=password, authentication=ldap3.SIMPLE,
                                      read_only=True, auto_range=True, receive_timeout=timeout,
                                      raise_exceptions=False)
        try:
            ok = self._conn.bind()
        except ldap3.core.exceptions.LDAPSocketOpenError as exc:
            raise ProbeError("ldap_connect_failed" + (":tls" if "ssl" in str(exc).lower() else "")) from None
        if not ok:
            raise ProbeError("ldap_bind_failed:" + str(self._conn.result.get("description") or "unknown"))

    @staticmethod
    def _entry(dn: str, raw: dict) -> dict:
        out: dict = {"dn": dn}
        for name, values in raw.items():
            key = name.lower()
            if key == "objectsid":
                out[key] = [bytes(v) for v in values]
            else:
                out[key] = [v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)
                            for v in values]
        return out

    def root_dse(self) -> dict:
        self._conn.search("", "(objectClass=*)", search_scope=self._ldap3.BASE,
                          attributes=["defaultNamingContext", "supportedCapabilities", "dnsHostName"])
        for e in self._conn.response or []:
            if e.get("type") == "searchResEntry":
                return self._entry("", e.get("raw_attributes") or {})
        return {"dn": ""}

    def search(self, base: str, flt: str, attrs: list[str]) -> list[dict]:
        gen = self._conn.extend.standard.paged_search(base, flt, search_scope=self._ldap3.SUBTREE,
                                                      attributes=attrs, paged_size=500, generator=True)
        out = []
        for e in gen:
            if e.get("type") == "searchResEntry":
                out.append(self._entry(e["dn"], e.get("raw_attributes") or {}))
        return out

    def close(self) -> None:
        try:
            self._conn.unbind()
        except Exception:
            pass


# --------------------------------------------------------------------------- pure functions (unit-tested)
def _first(entry: dict, attr: str, default=None):
    values = entry.get(attr.lower()) or []
    return values[0] if values else default


def rdns(dn: str) -> list[tuple[str, str]]:
    """The relative names of a DN, leaf first, as (type upper case, value)."""
    try:
        from ldap3.utils.dn import parse_dn

        return [(t.upper(), v) for t, v, _ in parse_dn(dn, escape=False)]
    except Exception:
        parts = []
        for p in dn.split(","):
            if "=" in p:
                t, v = p.split("=", 1)
                parts.append((t.strip().upper(), v.strip()))
        return parts


def rid(entry: dict) -> int | None:
    sid = _first(entry, "objectSid")
    if isinstance(sid, (bytes, bytearray)) and len(sid) >= 8:
        return int.from_bytes(sid[-4:], "little")
    if isinstance(sid, str) and sid.upper().startswith("S-"):
        try:
            return int(sid.rsplit("-", 1)[1])
        except ValueError:
            return None
    return None


def is_builtin_account(entry: dict) -> bool:
    name = str(_first(entry, "sAMAccountName", "")).lower()
    return rid(entry) in BUILTIN_RIDS or name in BUILTIN_NAMES


def is_service_account(entry: dict) -> bool:
    name = str(_first(entry, "sAMAccountName", "")).lower()
    if name.startswith(SERVICE_PREFIXES):
        return True
    return any(t == "OU" and any(w in v.lower() for w in SERVICE_OU_WORDS) for t, v in rdns(entry["dn"])[1:])


def is_disabled(entry: dict) -> bool:
    try:
        return bool(int(_first(entry, "userAccountControl", "0")) & UAC_DISABLED)
    except ValueError:
        return False


def is_default_group(entry: dict) -> bool:
    parents = rdns(entry["dn"])[1:]
    if any(t == "CN" and v.lower() == "builtin" for t, v in parents):
        return True
    if parents and parents[0][0] == "CN" and parents[0][1].lower() == "users":
        r = rid(entry)
        name = str(_first(entry, "sAMAccountName") or _first(entry, "cn") or "").lower()
        return (r is not None and r < 1000) or name in DEFAULT_USERS_GROUPS
    return False


def max_nesting(groups: list[dict]) -> int:
    """Longest chain of group-in-group (in edges) among the given groups; a cycle is cut where it closes."""
    by_dn = {g["dn"].lower(): g for g in groups}
    children = {dn: [m.lower() for m in g.get("member", []) if m.lower() in by_dn and m.lower() != dn]
                for dn, g in by_dn.items()}
    memo: dict[str, int] = {}

    def depth(dn: str, path: frozenset) -> int:
        if dn in memo:
            return memo[dn]
        best = 0
        for child in children[dn]:
            if child in path:
                continue
            best = max(best, 1 + depth(child, path | {child}))
        memo[dn] = best
        return best

    return max((depth(dn, frozenset({dn})) for dn in by_dn), default=0)


def is_active_directory(root_dse: dict) -> bool:
    caps = [c.strip() for c in root_dse.get("supportedcapabilities", [])]
    return AD_CAPABILITY in caps or bool(root_dse.get("defaultnamingcontext"))


def ou_path(dn: str) -> str:
    """OU=Mitarbeiter,OU=Kanzlei,DC=... -> Kanzlei/Mitarbeiter"""
    return "/".join(v for t, v in reversed(rdns(dn)) if t == "OU")


def directory_facts(root_dse: dict, users: list[dict], groups: list[dict], computers: list[dict],
                    ous: list[dict]) -> tuple[dict, dict]:
    enabled = disabled = services = 0
    service_names = []
    for u in users:
        if is_builtin_account(u):
            continue
        if is_service_account(u):
            services += 1
            service_names.append(str(_first(u, "sAMAccountName", "")))
            continue
        if is_disabled(u):
            disabled += 1
        else:
            enabled += 1
    firm_groups = [g for g in groups if not is_default_group(g)]
    facts = {
        "is_active_directory": is_active_directory(root_dse),
        "people_enabled": enabled,
        "people_disabled": disabled,
        "service_accounts": services,
        "groups": len(firm_groups),
        "groups_empty": sum(1 for g in firm_groups if not g.get("member")),
        "max_nesting": max_nesting(firm_groups),
        "computers": len(computers),
    }
    detail = {
        "groups": {str(_first(g, "sAMAccountName") or _first(g, "cn") or g["dn"]): len(g.get("member", []))
                   for g in sorted(firm_groups, key=lambda g: g["dn"].lower())},
        "ous": sorted({ou_path(o["dn"]) for o in ous if ou_path(o["dn"])}),
        "service_accounts": sorted(service_names),
    }
    return facts, detail


# --------------------------------------------------------------------------- the probe
def run(target: dict, ctx: Context) -> list[Output]:
    key = target["key"]
    password = ctx.secrets.get(target.get("password_env"), f"directory {key} ({target.get('bind_user', '')})")
    if not password:
        return [Output(result(key, "failed", f"password_missing:{target.get('password_env') or 'password_env'}"))]
    factory = ctx.ldap_factory or Ldap3Reader
    reader = None
    try:
        reader = factory(target.get("url", ""), target.get("bind_user", ""), password,
                         verify_tls=bool(target.get("verify_tls", True)), ca_file=target.get("ca_file"))
        root = reader.root_dse()
        base = target.get("base_dn") or _first(root, "defaultNamingContext")
        if not base:
            raise ProbeError("no_base_dn")
        users = reader.search(base, USER_FILTER, USER_ATTRS)
        groups = reader.search(base, GROUP_FILTER, GROUP_ATTRS)
        computers = reader.search(base, COMPUTER_FILTER, ["cn"])
        ous = reader.search(base, OU_FILTER, ["ou"])
        facts, detail = directory_facts(root, users, groups, computers, ous)
        ctx.log(f"directory {key}: ok, {facts['people_enabled']} people enabled, {facts['groups']} groups")
        return [Output(result(key, "ok", None, facts), detail)]
    except Exception as exc:
        ctx.log(f"directory {key}: failed ({error_code(exc)})")
        return [Output(result(key, "failed", error_code(exc)),
                       {"message": ctx.secrets.scrub(f"{type(exc).__name__}: {exc}")})]
    finally:
        if reader is not None and hasattr(reader, "close"):
            reader.close()
