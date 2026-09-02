# Admin Console: Ingestion Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the firm's administrator decide what is indexed, when, how fast and behind which wall from one form in the console — and have that decision reach RemoteController without anyone editing a file on the host.

**Architecture:** The profile compiler (KC-IN-4/6, `identity/ingestion_compiler.py`) already turns an `IngestionProfile` into the two schema-valid RemoteController documents. What is missing is everything around it: RemoteController must accept the firm administrator (today every route demands a Knovas-employee JWT), the Platform needs a repository over the existing `ingestion_profiles` table and a small RemoteController client that sends the user's principal assertion, and the console needs the tab. Saving is a guarded action (`ingestion_profile_change`) through `run_guarded` from the Approvals plan, so a change that widens or halts coverage can require a second person.

**Tech Stack:** Python 3.11 (Platform) / 3.12 (RemoteController), Flask, psycopg 3, `cryptography` Ed25519 (both packages already depend on it), pytest.

**Spec:** `docs/superpowers/plans/2026-08-14-section-b-buildout.md` § "Ingestion administration" (KC-IN-1, IN-2, IN-3, IN-5, IN-7); Jira SS-391 acceptance criteria. **Depends on:** `docs/superpowers/plans/2026-09-02-admin-approvals-tab.md` (Tasks 2–4: `run_guarded`, `_require_roles`, the executors registry) — execute that plan first.

## Global Constraints

- **Branch:** `feat/auth-assertion`. Platform tests run from `KnovasPlatform/components/docbridge_integration/` with `.venv/Scripts/python.exe -m pytest`; RemoteController tests from `RemoteController/` with `py -3 -m pytest` and the CI env block from `.github/workflows/ci.yml` (`RC_SKIP_CONFIG_VALIDATION=true TESTING=true ...`).
- **One profile, one form, one write** (spec). The form edits an `IngestionProfile`; `compile_profile` is the only thing that produces RemoteController documents; nothing else writes them.
- **The wire contract Platform → RemoteController is a request header `X-Platform-Principal`** carrying the same Ed25519 JWS the Platform sends Knovas in the body. RemoteController is our own component, so a header is fine here; it must **not** be confused with the Knovas body field.
- **The kid is `"bk-" + sha256(public_pem)[:16]`**, computed identically in `KnovasPlatform/.../identity/broker_key.py::derive_key_id` and `RemoteController/src/auth/platform_principal.py::derive_key_id`. Two copies of three lines, documented as a contract; the two packages share no import.
- **Assertion bounds are the Platform's:** `alg` pinned `EdDSA` in code, `typ` `knovas-principal+jws`, lifetime ≤ 300 s, skew 30 s, `jti` single-use. RemoteController verifies all of them.
- **RemoteController stays unpublished** (KC-IN-5): reachable on `knovas-internal` only, `http://remote-controller:5001`. The console is the sole firm-facing surface.
- **Every state-changing POST validates CSRF first, carries a role gate, writes an audit row** (REQ-A2). German UI copy; ASCII Python.
- **Roles:** the tab is for `admin` and `ingestion_manager`. Saving, restoring and stopping are guarded (`ingestion_profile_change`); starting and previewing are not.
- Do not push. Commit per task.

---

## File Structure

**RemoteController — create**
- `src/auth/platform_principal.py` — verify a Platform-signed principal; `ReplayGuard`; `derive_key_id`.
- `tests/test_platform_principal.py`

**RemoteController — modify**
- `src/auth/knovas_verify_client.py` — `require_operator_or_tenant_admin`.
- `src/config.py` — `rc_platform_broker_pubkey_path`.
- `src/routes/discover.py`, `sync.py`, `sync_config_route.py`, `sync_control.py` — swap the gate in `_RC_DECORATORS`.
- `docs/configuration.md`

**Platform — create**
- `src/identity/ingestion_profiles.py` — repository over `ingestion_profiles`; JSON ↔ `IngestionProfile`.
- `src/remote_controller_client.py` — discover, status, start, stop, push (config then request, with rollback).
- `src/web_interface/admin_ingestion.py`, `templates/admin_ingestion.html`
- `tests/test_identity_ingestion_profiles.py`, `tests/test_remote_controller_client.py`, `tests/test_web_admin_ingestion.py`

**Platform — modify**
- `src/web_interface/admin.py` — mount; register the `ingestion_profile_change` executor.
- `src/web_interface/app.py` — build the RemoteController client with the same broker.
- `templates/_admin_tabs.html`, `config/config.yaml`, `knovas.env.example`, `KnovasPlatform/docker-compose.yml`, `docker-compose.yml` (root), `KnovasPlatform/docs/features/document-administration.md`, `RELEASE_NOTES.md`

Platform paths are relative to `KnovasPlatform/components/docbridge_integration/` unless prefixed.

---

### Task 1: RemoteController accepts the firm administrator (KC-IN-1)

**Files:**
- Create: `RemoteController/src/auth/platform_principal.py`
- Modify: `RemoteController/src/auth/knovas_verify_client.py` (append the gate)
- Modify: `RemoteController/src/config.py` (field + env)
- Modify: `RemoteController/src/routes/discover.py:12-21`, `sync.py:18-27`, `sync_config_route.py:11-20`, `sync_control.py:19-30` — `_RC_DECORATORS`
- Test: `RemoteController/tests/test_platform_principal.py`

**Interfaces:**
- Consumes: the JWS the Platform's `AssertionSigner.mint` produces: header `{"alg": "EdDSA", "typ": "knovas-principal+jws", "kid": ...}`, payload `sub, tid, grp, rol, iat, exp, jti`, signature over `"<h64>.<p64>"` (ASCII).
- Produces:
  ```python
  HEADER = "X-Platform-Principal"
  ADMIN_ROLES = frozenset({"admin", "ingestion_manager"})
  class InvalidPrincipalError(Exception)
  @dataclass(frozen=True) class PlatformPrincipal: subject, tenant, groups, roles, jti, expires_at
  def derive_key_id(public_pem: bytes) -> str
  class ReplayGuard: burn(jti: str, until: float) -> bool
  def verify_platform_principal(token, *, public_pem, expected_tenant, replay, now=None) -> PlatformPrincipal
  def require_operator_or_tenant_admin(func)   # in knovas_verify_client.py; sets g.rc_principal
  ```
  Config: `AppConfig.rc_platform_broker_pubkey_path: str` from `RC_PLATFORM_BROKER_PUBKEY_PATH` (empty = feature off, header refused with 403).

- [ ] **Step 1: Write the failing tests**

```python
"""A Platform-signed principal at RemoteController's door (KC-IN-1).

The Platform already signs each user into its Knovas calls. RemoteController
verifies the same token so the firm's own administrator can configure their
own ingestion — beside, not instead of, the Knovas-employee path.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: E402

from auth.platform_principal import (  # noqa: E402
    InvalidPrincipalError,
    ReplayGuard,
    derive_key_id,
    verify_platform_principal,
)

TENANT = "22222222-2222-2222-2222-222222222222"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.fixture
def keypair():
    private = ed25519.Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_pem


def mint(private, public_pem, *, alg="EdDSA", typ="knovas-principal+jws", kid=None,
         sign=True, **payload_overrides):
    now = int(time.time())
    payload = {"sub": "user-1", "tid": TENANT, "grp": ["litigation"],
               "rol": ["ingestion_manager"], "iat": now, "exp": now + 120,
               "jti": f"j-{time.time_ns()}"}
    payload.update(payload_overrides)
    header = {"alg": alg, "typ": typ, "kid": kid or derive_key_id(public_pem)}
    signing_input = (f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
                     f"{_b64(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode())}")
    sig = private.sign(signing_input.encode("ascii")) if sign else b"\x00" * 64
    return f"{signing_input}.{_b64(sig)}"


class TestVerify:
    def test_a_genuine_token_yields_the_principal(self, keypair):
        private, pub = keypair
        p = verify_platform_principal(mint(private, pub), public_pem=pub,
                                      expected_tenant=TENANT, replay=ReplayGuard())
        assert p.subject == "user-1" and p.roles == ("ingestion_manager",)
        assert p.groups == ("litigation",)

    @pytest.mark.parametrize("kw", [
        dict(sign=False),
        dict(alg="none"),
        dict(alg="HS256"),
        dict(typ="knovas-dual-control+jws"),
        dict(kid="bk-0000000000000000"),
        dict(tid="33333333-3333-3333-3333-333333333333"),
        dict(exp=int(time.time()) - 60),
        dict(iat=int(time.time()) - 400, exp=int(time.time()) + 100),
    ])
    def test_each_forgery_is_refused(self, keypair, kw):
        private, pub = keypair
        with pytest.raises(InvalidPrincipalError):
            verify_platform_principal(mint(private, pub, **kw), public_pem=pub,
                                      expected_tenant=TENANT, replay=ReplayGuard())

    def test_a_token_cannot_be_presented_twice(self, keypair):
        private, pub = keypair
        token, replay = mint(private, pub), ReplayGuard()
        verify_platform_principal(token, public_pem=pub, expected_tenant=TENANT, replay=replay)
        with pytest.raises(InvalidPrincipalError):
            verify_platform_principal(token, public_pem=pub, expected_tenant=TENANT, replay=replay)

    def test_the_kid_matches_the_platforms_derivation(self, keypair):
        import hashlib
        _, pub = keypair
        assert derive_key_id(pub) == "bk-" + hashlib.sha256(pub).hexdigest()[:16]


class TestTheGate:
    @pytest.fixture
    def configured(self, keypair, tmp_path, monkeypatch, tmp_watch_root):
        private, pub = keypair
        pem = tmp_path / "broker_ed25519.pub"
        pem.write_bytes(pub)
        monkeypatch.setenv("RC_PLATFORM_BROKER_PUBKEY_PATH", str(pem))
        monkeypatch.setenv("RC_CLIENT_ID", TENANT)
        monkeypatch.setenv("RC_INTERNAL_LOCAL_BYPASS", "false")
        from config import reset_config, load_config
        reset_config()
        load_config(validate=False, force_reload=True)
        return private, pub

    def test_status_answers_a_signed_ingestion_manager(self, rc_client, configured):
        private, pub = configured
        r = rc_client.get("/sync/status", headers={"X-Platform-Principal": mint(private, pub)})
        assert r.status_code == 200

    def test_a_member_without_the_role_is_refused(self, rc_client, configured):
        private, pub = configured
        r = rc_client.get("/sync/status",
                          headers={"X-Platform-Principal": mint(private, pub, rol=["member"])})
        assert r.status_code == 403

    def test_a_bad_signature_is_refused(self, rc_client, configured):
        private, pub = configured
        r = rc_client.get("/sync/status",
                          headers={"X-Platform-Principal": mint(private, pub, sign=False)})
        assert r.status_code == 401

    def test_without_the_header_the_employee_path_still_applies(self, rc_client, configured):
        assert rc_client.get("/sync/status").status_code == 401
```

Fixture note: `rc_client` builds the app after `tmp_watch_root` reloads config; the `configured` fixture reloads once more so the two new env values are read. Check `tests/conftest.py` for how `RC_INTERNAL_LOCAL_BYPASS` is defaulted in the test env; if the existing suite relies on the bypass being on for `rc_client`, set it to `false` only inside `configured` as above.

- [ ] **Step 2: Run and watch them fail**

Run (from `RemoteController/`): `RC_SKIP_CONFIG_VALIDATION=true TESTING=true RC_MTLS_DEV_BYPASS=true RC_INSTANCE_TOKEN=t RC_CLIENT_ID=22222222-2222-2222-2222-222222222222 KNOVAS_INTERNAL_API_URL=http://x:5000 py -3 -m pytest tests/test_platform_principal.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth.platform_principal'`

- [ ] **Step 3: The verifier**

Create `RemoteController/src/auth/platform_principal.py`:

```python
"""A Platform-signed principal at RemoteController's door (KC-IN-1).

The firm's Platform signs each signed-in user into its Knovas calls with an
Ed25519 key. RemoteController holds the public half and verifies the same
token, so the firm's own administrator can configure their own ingestion.

This mirrors the Platform's assertion rules exactly; the bounds are theirs.
The kid derivation is a three-line contract shared with
KnovasPlatform/.../identity/broker_key.py::derive_key_id.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

HEADER = "X-Platform-Principal"
ALGORITHM = "EdDSA"
TOKEN_TYPE = "knovas-principal+jws"
MAX_LIFETIME_SECONDS = 300
CLOCK_SKEW_SECONDS = 30
ADMIN_ROLES = frozenset({"admin", "ingestion_manager"})


class InvalidPrincipalError(Exception):
    """Refused. The message is deliberately uniform."""


@dataclass(frozen=True)
class PlatformPrincipal:
    subject: str
    tenant: str
    groups: tuple[str, ...]
    roles: tuple[str, ...]
    jti: str
    expires_at: int


def derive_key_id(public_pem: bytes) -> str:
    return "bk-" + hashlib.sha256(public_pem).hexdigest()[:16]


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ReplayGuard:
    """In-process single-use jti store. One RemoteController per firm, so a
    process-local set is the right size; entries expire with the token."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def burn(self, jti: str, until: float) -> bool:
        now = time.time()
        with self._lock:
            for key, expires in list(self._seen.items()):
                if expires <= now:
                    del self._seen[key]
            if jti in self._seen:
                return False
            self._seen[jti] = until
            return True


def verify_platform_principal(
    token: str,
    *,
    public_pem: bytes,
    expected_tenant: str,
    replay: ReplayGuard,
    now: int | None = None,
) -> PlatformPrincipal:
    try:
        h64, p64, s64 = token.split(".")
        header = json.loads(_unb64(h64))
        payload = json.loads(_unb64(p64))
        signature = _unb64(s64)
    except Exception as exc:  # noqa: BLE001
        raise InvalidPrincipalError("refused") from exc
    # Task 1 review ruling: a JSON value that is not an object must refuse
    # uniformly, not raise AttributeError into a 500 (mirrors assertion.py).
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise InvalidPrincipalError("refused")

    # alg is pinned here, never read to choose a verifier; the header's value
    # is only compared.
    if header.get("alg") != ALGORITHM or header.get("typ") != TOKEN_TYPE:
        raise InvalidPrincipalError("refused")
    if header.get("kid") != derive_key_id(public_pem):
        raise InvalidPrincipalError("refused")
    key = serialization.load_pem_public_key(public_pem)
    if not isinstance(key, ed25519.Ed25519PublicKey):  # an X25519 PEM has no verify()
        raise InvalidPrincipalError("refused")
    try:
        key.verify(signature, f"{h64}.{p64}".encode("ascii"))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise InvalidPrincipalError("refused") from exc

    now = int(time.time()) if now is None else now
    try:
        iat, exp = int(payload.get("iat")), int(payload.get("exp"))
    except (TypeError, ValueError) as exc:
        raise InvalidPrincipalError("refused") from exc
    if exp - iat > MAX_LIFETIME_SECONDS or exp - iat <= 0:
        raise InvalidPrincipalError("refused")
    if now > exp + CLOCK_SKEW_SECONDS or iat > now + CLOCK_SKEW_SECONDS:
        raise InvalidPrincipalError("refused")
    if not expected_tenant or payload.get("tid") != expected_tenant:
        raise InvalidPrincipalError("refused")
    jti = str(payload.get("jti") or "")
    if not jti or not replay.burn(jti, exp + CLOCK_SKEW_SECONDS):
        raise InvalidPrincipalError("refused")

    return PlatformPrincipal(
        subject=str(payload.get("sub") or ""),
        tenant=expected_tenant,
        groups=tuple(str(g) for g in (payload.get("grp") or ())),
        roles=tuple(str(r) for r in (payload.get("rol") or ())),
        jti=jti,
        expires_at=exp,
    )
```

- [ ] **Step 4: Config**

In `RemoteController/src/config.py`: add the dataclass field `rc_platform_broker_pubkey_path: str = ""` to `AppConfig`, and in `load_config` pass `rc_platform_broker_pubkey_path=(os.environ.get("RC_PLATFORM_BROKER_PUBKEY_PATH") or "").strip()`. Not in `_REQUIRED_VARS` — empty means the feature is off.

- [ ] **Step 5: The gate**

Append to `RemoteController/src/auth/knovas_verify_client.py`:

```python
from auth.platform_principal import (  # noqa: E402  (placed with the other imports)
    ADMIN_ROLES,
    HEADER as PLATFORM_PRINCIPAL_HEADER,
    InvalidPrincipalError,
    ReplayGuard,
    verify_platform_principal,
)

_platform_replay = ReplayGuard()


def _platform_public_pem(cfg) -> bytes:
    with open(cfg.rc_platform_broker_pubkey_path, "rb") as fh:
        return fh.read()


def require_operator_or_tenant_admin(func):
    """A Knovas employee (existing path) OR the firm's own administrator,
    presenting the Platform-signed principal in X-Platform-Principal with
    the admin or ingestion_manager role (KC-IN-1). Each route declares which
    principals it accepts by using this decorator."""
    operator_path = require_internal_access(func)

    @wraps(func)
    def wrapper(*args, **kwargs):
        token = (request.headers.get(PLATFORM_PRINCIPAL_HEADER) or "").strip()
        if not token:
            return operator_path(*args, **kwargs)
        cfg = get_config()
        if not cfg.rc_platform_broker_pubkey_path:
            return jsonify({"error": "Platform principals are not configured", "status": "error"}), 403
        try:
            principal = verify_platform_principal(
                token, public_pem=_platform_public_pem(cfg),
                expected_tenant=cfg.rc_client_id, replay=_platform_replay,
            )
        except (InvalidPrincipalError, OSError):
            return jsonify({"error": "Not authorized", "status": "error"}), 401
        if not (set(principal.roles) & ADMIN_ROLES):
            return jsonify({"error": "Not authorized", "status": "error"}), 403
        g.rc_client_id = cfg.rc_client_id
        g.rc_principal = principal
        return func(*args, **kwargs)

    return wrapper
```

- [ ] **Step 6: Use it on the six routes**

In each of `discover.py`, `sync.py`, `sync_config_route.py`, `sync_control.py`, import `require_operator_or_tenant_admin` from `auth.knovas_verify_client` and replace `require_internal_access` with it in `_RC_DECORATORS`. Nothing else in those files changes.

- [ ] **Step 7: Run the tests, then the whole RemoteController suite**

Run: the command from Step 2, then `... py -3 -m pytest` for the whole suite.
Expected: the new file PASS (13); the suite unchanged.

- [ ] **Step 8: Document, and commit**

In `RemoteController/docs/configuration.md`, under the environment variables, add `RC_PLATFORM_BROKER_PUBKEY_PATH` — the Platform's `broker_ed25519.pub`, mounted read-only; with it set, the firm's administrator may use `/discover`, `/sync`, `/sync/config`, `/sync/start|stop|status` through the console; without it, only Knovas employees can.

```bash
git add src/auth/platform_principal.py src/auth/knovas_verify_client.py src/config.py src/routes/ tests/test_platform_principal.py docs/configuration.md
git commit -m "feat(rc): accept the firm administrator's Platform-signed principal beside the employee JWT (KC-IN-1)"
```

---

### Task 2: The profile repository (KC-IN-2)

**Files:**
- Create: `src/identity/ingestion_profiles.py`
- Test: `tests/test_identity_ingestion_profiles.py` (PostgreSQL)

**Interfaces:**
- Consumes: `ingestion_profiles` (`0001_identity.sql:191`); `IngestionProfile`, `SourceFolder` from `identity/ingestion_compiler.py`.
- Produces:
  ```python
  def profile_to_json(profile: IngestionProfile) -> dict
  def profile_from_json(data: Mapping) -> IngestionProfile
  @dataclass(frozen=True) class ProfileVersion: id: UUID; name: str; version: int; profile: IngestionProfile; is_current: bool; created_at; created_by: UUID | None; approved_by: UUID | None; pushed_at
  class IngestionProfileRepository:
      def __init__(self, conn)
      def current(self, name: str = "default") -> ProfileVersion | None
      def versions(self, name: str = "default") -> list[ProfileVersion]      # newest first
      def save_new_version(self, profile, *, name="default", by, approved_by=None) -> ProfileVersion
      def mark_pushed(self, version_id) -> None
      def restore(self, name: str, version: int, *, by) -> ProfileVersion   # copies as a new current version
  ```

- [ ] **Step 1: Write the failing tests**

```python
"""ingestion_profiles: versioned, attributed, reversible (KC-IN-2, KC-IN-7)."""

from __future__ import annotations

import pytest

from conftest import platform_db_reachable
from identity.ingestion_compiler import IngestionProfile, SourceFolder
from identity.ingestion_profiles import (
    IngestionProfileRepository,
    profile_from_json,
    profile_to_json,
)

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)


def _profile(prefix="kanzlei", schedule="nightly"):
    return IngestionProfile(
        identifier_prefix=prefix,
        sources=[SourceFolder(path="/mnt/autodoc/mandate", access_groups=("g-lit",))],
        schedule=schedule,
    )


@pytest.fixture
def by(identity_repo):
    return identity_repo.create(email="ing@kanzlei.ch", display_name="I",
                                password="korrektes-pferd-batterie")


def test_json_round_trip_is_lossless():
    p = _profile()
    assert profile_from_json(profile_to_json(p)) == IngestionProfile(
        identifier_prefix="kanzlei",
        sources=[SourceFolder(path="/mnt/autodoc/mandate", recursive=True,
                              access_groups=("g-lit",))],
        schedule="nightly",
    )


def test_first_save_is_version_one_and_current(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    assert repo.current() is None
    v = repo.save_new_version(_profile(), by=by)
    assert (v.version, v.is_current, v.pushed_at) == (1, True, None)
    assert repo.current().id == v.id


def test_a_second_save_supersedes_the_first(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    repo.save_new_version(_profile(), by=by)
    v2 = repo.save_new_version(_profile(schedule="continuous"), by=by)
    assert v2.version == 2 and repo.current().version == 2
    assert [v.version for v in repo.versions()] == [2, 1]
    assert repo.versions()[1].is_current is False


def test_restore_copies_an_old_version_as_a_new_current_one(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    repo.save_new_version(_profile(schedule="nightly"), by=by)
    repo.save_new_version(_profile(schedule="continuous"), by=by)
    v3 = repo.restore("default", 1, by=by)
    assert v3.version == 3 and v3.profile.schedule == "nightly"
    assert repo.current().version == 3


def test_mark_pushed_records_the_moment(platform_db, by):
    repo = IngestionProfileRepository(platform_db)
    v = repo.save_new_version(_profile(), by=by)
    repo.mark_pushed(v.id)
    assert repo.current().pushed_at is not None
```

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_identity_ingestion_profiles.py`
Expected: FAIL (`ModuleNotFoundError`) or SKIP without PostgreSQL — the round-trip test has no `pytestmark`-free path, so move `test_json_round_trip_is_lossless` above the `pytestmark` line if you want it to run locally; it does not need a database.

- [ ] **Step 3: Implement**

```python
"""The versioned ingestion profile — the only artifact a person edits.

Every save is a new row; the previous current row is superseded, never
updated. Restore copies an old version forward as a new one, so "what was
running on Tuesday" is always a row and never a diff.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from identity.ingestion_compiler import IngestionProfile, SourceFolder

_COLUMNS = ("id", "name", "version", "profile", "is_current", "created_at",
            "created_by", "approved_by", "pushed_at")


def profile_to_json(profile: IngestionProfile) -> dict[str, Any]:
    data = asdict(profile)
    data["sources"] = [
        {"path": s.path, "recursive": bool(s.recursive),
         "access_groups": list(s.access_groups)}
        for s in profile.sources
    ]
    return data


def profile_from_json(data: Mapping[str, Any]) -> IngestionProfile:
    fields = dict(data)
    fields["sources"] = [
        SourceFolder(path=str(s["path"]), recursive=bool(s.get("recursive", True)),
                     access_groups=tuple(str(g) for g in (s.get("access_groups") or ())))
        for s in fields.get("sources") or []
    ]
    return IngestionProfile(**fields)


@dataclass(frozen=True)
class ProfileVersion:
    id: UUID
    name: str
    version: int
    profile: IngestionProfile
    is_current: bool
    created_at: datetime
    created_by: UUID | None
    approved_by: UUID | None
    pushed_at: datetime | None


class IngestionProfileRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def _row(self, row) -> ProfileVersion:
        d = dict(zip(_COLUMNS, row))
        raw = d["profile"] if isinstance(d["profile"], dict) else json.loads(d["profile"])
        d["profile"] = profile_from_json(raw)
        return ProfileVersion(**d)

    def current(self, name: str = "default") -> ProfileVersion | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingestion_profiles "
            "WHERE name = %s AND is_current", (name,)
        ).fetchone()
        return None if row is None else self._row(row)

    def versions(self, name: str = "default") -> list[ProfileVersion]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingestion_profiles "
            "WHERE name = %s ORDER BY version DESC", (name,)
        ).fetchall()
        return [self._row(r) for r in rows]

    def save_new_version(self, profile: IngestionProfile, *, name: str = "default",
                         by: Any, approved_by: Any | None = None) -> ProfileVersion:
        with self._conn.transaction():
            self._conn.execute(
                "UPDATE ingestion_profiles SET is_current = FALSE WHERE name = %s AND is_current",
                (name,),
            )
            (next_version,) = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM ingestion_profiles WHERE name = %s",
                (name,),
            ).fetchone()
            row = self._conn.execute(
                "INSERT INTO ingestion_profiles (name, version, profile, is_current, "
                "created_by, approved_by) VALUES (%s, %s, %s, TRUE, %s, %s) "
                f"RETURNING {', '.join(_COLUMNS)}",
                (name, next_version, json.dumps(profile_to_json(profile)),
                 str(by.id), None if approved_by is None else str(approved_by.id)),
            ).fetchone()
        return self._row(row)

    def mark_pushed(self, version_id: UUID | str) -> None:
        self._conn.execute(
            "UPDATE ingestion_profiles SET pushed_at = now() WHERE id = %s", (str(version_id),)
        )

    def restore(self, name: str, version: int, *, by: Any) -> ProfileVersion:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingestion_profiles WHERE name = %s AND version = %s",
            (name, int(version)),
        ).fetchone()
        if row is None:
            raise LookupError(f"Profil {name!r} hat keine Version {version}.")
        return self.save_new_version(self._row(row).profile, name=name, by=by)
```

`asdict` on the frozen dataclass copies nested `SourceFolder`s as dicts; the explicit rebuild above keeps `access_groups` a list in JSON and a tuple in Python, so equality in the round-trip test holds.

- [ ] **Step 4: Run, then commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_identity_ingestion_profiles.py`
Expected: PASS with PostgreSQL; the round-trip test passes everywhere.

```bash
git add src/identity/ingestion_profiles.py tests/test_identity_ingestion_profiles.py
git commit -m "feat(identity): versioned ingestion profiles — save, supersede, restore (KC-IN-2, IN-7)"
```

---

### Task 3: The RemoteController client (Platform side)

**Files:**
- Create: `src/remote_controller_client.py`
- Modify: `config/config.yaml` (a `remote_controller:` block), `src/web_interface/app.py` (construct it beside the Knovas client)
- Test: `tests/test_remote_controller_client.py` (no network)

**Interfaces:**
- Consumes: the broker from the auth work (`current_user()`, `assertion_for(user)`); `CompiledIngestion` from the compiler; RemoteController routes `GET /discover?root=&max_depth=`, `GET /sync/status`, `POST /sync/start`, `POST /sync/stop`, `GET|POST /sync/config`, `POST /sync`.
- Produces:
  ```python
  class RemoteControllerError(RuntimeError): status: int | None
  class RemoteControllerClient:
      def __init__(self, base_url: str, *, principal_broker, session=None, timeout: float = 20.0)
      def discover(self, root: str | None = None, max_depth: int = 3) -> dict
      def status(self) -> dict
      def start(self) -> dict
      def stop(self) -> dict
      def get_sync_config(self) -> dict
      def push(self, compiled: CompiledIngestion) -> dict   # config first, then request; restores the previous config if the request is refused
  ```
  Config: `remote_controller.base_url` ← `${RC_BASE_URL:-http://remote-controller:5001}`.

- [ ] **Step 1: Write the failing tests**

```python
"""The console reaches RemoteController as the signed-in person, never anonymously."""

from __future__ import annotations

import pytest

from identity.ingestion_compiler import CompiledIngestion
from remote_controller_client import RemoteControllerClient, RemoteControllerError


class _Broker:
    def __init__(self, user="u-1"):
        self._user = user

    def current_user(self):
        return self._user

    def assertion_for(self, user):
        return f"token-for-{user}"


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body if body is not None else {}
        self.ok = status < 400

    def json(self):
        return self._body


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json"), dict(kw.get("headers") or {})))
        handler = self.routes.get((method, url.rsplit("/", 1)[-1] if "?" not in url else url.rsplit("/", 1)[-1].split("?")[0]))
        return handler(kw) if callable(handler) else (handler or _Resp(200))


BASE = "http://remote-controller:5001"


def test_every_call_carries_the_principal_header():
    session = _Session({("GET", "status"): _Resp(200, {"state": "idle"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    assert client.status() == {"state": "idle"}
    _, url, _, headers = session.calls[0]
    assert url == f"{BASE}/sync/status"
    assert headers["X-Platform-Principal"] == "token-for-u-1"


def test_no_user_means_no_call():
    session = _Session({})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(user=None), session=session)
    with pytest.raises(PermissionError):
        client.status()
    assert session.calls == []


def test_push_sends_config_then_request():
    session = _Session({("POST", "config"): _Resp(200, {"ok": True}),
                        ("POST", "sync"): _Resp(200, {"accepted": 3}),
                        ("GET", "config"): _Resp(200, {"old": True})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    out = client.push(CompiledIngestion(sync_config={"mode": "scheduled"},
                                        sync_request={"mode": "incremental"}))
    assert out == {"accepted": 3}
    methods_urls = [(m, u.rsplit("/", 1)[-1]) for m, u, _, _ in session.calls]
    assert methods_urls == [("GET", "config"), ("POST", "config"), ("POST", "sync")]


def test_a_refused_request_restores_the_previous_config():
    session = _Session({("GET", "config"): _Resp(200, {"old": True}),
                        ("POST", "config"): _Resp(200),
                        ("POST", "sync"): _Resp(400, {"error": "bad body"})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    with pytest.raises(RemoteControllerError) as excinfo:
        client.push(CompiledIngestion(sync_config={"new": True}, sync_request={}))
    assert excinfo.value.status == 400
    posted_configs = [body for m, u, body, _ in session.calls if m == "POST" and u.endswith("/sync/config")]
    assert posted_configs == [{"new": True}, {"old": True}], "rolled back to the old config"


def test_discover_passes_root_and_depth():
    session = _Session({("GET", "discover"): _Resp(200, {"folders": []})})
    client = RemoteControllerClient(BASE, principal_broker=_Broker(), session=session)
    client.discover(root="/mnt/autodoc", max_depth=2)
    _, url, _, _ = session.calls[0]
    assert "root=%2Fmnt%2Fautodoc" in url and "max_depth=2" in url
```

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_remote_controller_client.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'remote_controller_client'`

- [ ] **Step 3: Implement**

```python
"""The console's client for the firm's own RemoteController.

Every call goes out as the signed-in person: the same Ed25519 assertion the
Platform sends Knovas, here in the X-Platform-Principal header, verified by
RemoteController's require_operator_or_tenant_admin. No session, no call.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import requests

from identity.ingestion_compiler import CompiledIngestion

logger = logging.getLogger(__name__)

PRINCIPAL_HEADER = "X-Platform-Principal"


class RemoteControllerError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RemoteControllerClient:
    def __init__(self, base_url: str, *, principal_broker, session=None,
                 timeout: float = 20.0) -> None:
        self._base = base_url.rstrip("/")
        self._broker = principal_broker
        self._session = session or requests.Session()
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        user = self._broker.current_user()
        if user is None:
            raise PermissionError("Kein angemeldeter Benutzer; RemoteController wird nicht aufgerufen.")
        return {PRINCIPAL_HEADER: self._broker.assertion_for(user),
                "Content-Type": "application/json"}

    def _call(self, method: str, path: str, *, body: Any = None, query: dict | None = None) -> Any:
        url = f"{self._base}{path}"
        if query:
            url += "?" + urlencode({k: v for k, v in query.items() if v is not None})
        resp = self._session.request(method, url, json=body, headers=self._headers(),
                                     timeout=self._timeout)
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001
            payload = {}
        if resp.status_code >= 400:
            message = str((payload or {}).get("error") or f"HTTP {resp.status_code}")
            raise RemoteControllerError(message, status=resp.status_code)
        return payload

    def discover(self, root: str | None = None, max_depth: int = 3) -> dict:
        return self._call("GET", "/discover", query={"root": root, "max_depth": max_depth})

    def status(self) -> dict:
        return self._call("GET", "/sync/status")

    def start(self) -> dict:
        return self._call("POST", "/sync/start", body={})

    def stop(self) -> dict:
        return self._call("POST", "/sync/stop", body={})

    def get_sync_config(self) -> dict:
        return self._call("GET", "/sync/config")

    def push(self, compiled: CompiledIngestion) -> dict:
        """Config first, then the folder list. If RemoteController refuses the
        folder list, the previous config is put back so the two never diverge."""
        previous = self.get_sync_config()
        self._call("POST", "/sync/config", body=compiled.sync_config)
        try:
            return self._call("POST", "/sync", body=compiled.sync_request)
        except RemoteControllerError:
            try:
                self._call("POST", "/sync/config", body=previous)
            except RemoteControllerError as rollback_exc:  # noqa: BLE001
                logger.error("Rollback der Sync-Konfiguration fehlgeschlagen: %s", rollback_exc)
            raise
```

- [ ] **Step 4: Config and startup wiring**

`config/config.yaml`, before the `identity:` block:

```yaml
# The firm's own RemoteController, on knovas-internal only (never published).
remote_controller:
  base_url: "${RC_BASE_URL:-http://remote-controller:5001}"
```

`app.py`, directly after `api_client.attach_principal_broker(...)` inside the `identity_gate is not None` block:

```python
        from remote_controller_client import RemoteControllerClient

        rc_client = RemoteControllerClient(
            str(config.get('remote_controller.base_url', 'http://remote-controller:5001')),
            principal_broker=_RequestScopedBroker(identity_gate, broker_signer,
                                                  str(api_client.customer_id)),
        )
```

and pass `rc_client_factory=lambda: rc_client` into `create_admin_blueprint(...)` (Task 4 adds the parameter). Above the identity block, initialise `rc_client = None` so the name exists when identity is off.

- [ ] **Step 5: Run, then commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_remote_controller_client.py`
Expected: PASS (5)

```bash
git add src/remote_controller_client.py config/config.yaml src/web_interface/app.py tests/test_remote_controller_client.py
git commit -m "feat(platform): RemoteController client that calls as the signed-in person; push is config-then-request with rollback"
```

---

### Task 4: The Ingestion tab (KC-IN-3, IN-7)

**Files:**
- Create: `src/web_interface/admin_ingestion.py`, `src/web_interface/templates/admin_ingestion.html`
- Modify: `src/web_interface/admin.py` (accept `rc_client_factory`; mount; register the executor), `templates/_admin_tabs.html`
- Modify: `tests/test_web_admin_documents.py::TestConsoleShell::test_tab_strip_names_every_tab_that_exists` (add `admin.ingestion`)
- Test: `tests/test_web_admin_ingestion.py`

**Interfaces:**
- Consumes: `run_guarded`, `_require_roles` (Approvals plan); `IngestionProfileRepository` (Task 2); `RemoteControllerClient` (Task 3); `compile_profile`, `ProfileError`, `redact_for_support`, `IngestionProfile`, `SourceFolder`; the presets tables in `identity/ingestion_presets.py`: `SCHEDULE_PRESETS`, `THROUGHPUT_PRESETS`, `FILE_TYPE_PRESETS` (verified).
- Produces: `attach_ingestion_routes(bp, gate, *, csrf_valid, csrf_token, page_context, client_factory, rc_client_factory, require_ingestion)` with routes `GET /admin/ingestion`, `POST /admin/ingestion/preview`, `POST /admin/ingestion/save`, `POST /admin/ingestion/restore/<int:version>`, `POST /admin/ingestion/start`, `POST /admin/ingestion/stop`; and `apply_profile(payload, actor, *, conn, rc_client) -> dict` — the executor for `ingestion_profile_change`.

Form contract (POST `/admin/ingestion/save` and `/preview`): `identifier_prefix`, `description`, `schedule` ∈ presets, `throughput` ∈ presets, `file_types` (multi), `max_document_age_days` (blank = none), up to twelve folder rows named `folder-N-path`, `folder-N-recursive` (`1`), `folder-N-groups` (multi). Blank paths are skipped. `stop` is guarded because it halts coverage; `start` and `preview` are not.

- [ ] **Step 1: Write the failing tests**

```python
"""The Ingestion tab: one form, one write, and a preview before either."""

from __future__ import annotations

import inspect
import pathlib

import pytest

flask = pytest.importorskip("flask")

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"


class TestShape:
    def test_routes_are_gated_and_posts_check_csrf_first(self):
        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        assert src.count("@bp.route") == src.count("@require_ingestion")
        for fn in ("def preview(", "def save(", "def restore(", "def start(", "def stop("):
            body = src[src.index(fn):src.index(fn) + 700]
            assert "csrf_ok" in body

    def test_save_and_stop_are_guarded_start_and_preview_are_not(self):
        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        for fn in ("def save(", "def restore(", "def stop("):
            assert "run_guarded(" in src[src.index(fn):src.index(fn) + 1800], fn
        for fn in ("def start(", "def preview("):
            assert "run_guarded(" not in src[src.index(fn):src.index(fn) + 900], fn

    def test_the_compiler_is_the_only_writer(self):
        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        assert "compile_profile(" in src
        assert "sync_request.schema" not in src and "remote_controller_sync" not in src


class TestFormParsing:
    def test_folders_rows_become_source_folders(self):
        from web_interface.admin_ingestion import profile_from_form

        form = {
            "identifier_prefix": "kanzlei", "schedule": "nightly", "throughput": "normal",
            "folder-0-path": "/mnt/autodoc/mandate", "folder-0-recursive": "1",
            "folder-1-path": "   ",
            "folder-2-path": "/mnt/autodoc/allgemein",
        }
        lists = {"file_types": ["documents", "email"], "folder-0-groups": ["g-lit"],
                 "folder-2-groups": []}
        p = profile_from_form(form, lists)
        assert [s.path for s in p.sources] == ["/mnt/autodoc/mandate", "/mnt/autodoc/allgemein"]
        assert p.sources[0].access_groups == ("g-lit",) and p.sources[0].recursive is True
        assert p.sources[1].recursive is False
        assert p.file_types == ["documents", "email"] and p.max_document_age_days is None

    def test_a_bad_preset_is_a_form_error_not_a_crash(self):
        from identity.ingestion_compiler import ProfileError
        from web_interface.admin_ingestion import profile_from_form

        with pytest.raises(ProfileError):
            profile_from_form({"identifier_prefix": "k", "schedule": "whenever",
                               "throughput": "normal", "folder-0-path": "/x"}, {})


class TestTemplate:
    def test_exists_and_every_post_form_has_csrf(self):
        html = (TEMPLATES / "admin_ingestion.html").read_text(encoding="utf-8")
        assert html.count('method="post"') >= 4
        assert html.count('name="csrf_token"') >= html.count('method="post"')

    def test_presets_are_offered_as_choices_not_free_text(self):
        html = (TEMPLATES / "admin_ingestion.html").read_text(encoding="utf-8")
        for preset in ("continuous", "nightly", "manual", "gentle", "normal", "fast"):
            assert f'value="{preset}"' in html

    def test_the_strip_knows_the_tab(self):
        assert "admin.ingestion" in (TEMPLATES / "_admin_tabs.html").read_text(encoding="utf-8")

    def test_it_renders_with_stub_data(self):
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        html = env.get_template("admin_ingestion.html").render(
            app_title="Knovas", company_name="Kanzlei", feedback_url=None,
            console_url="/admin/people", active_nav="admin", csrf_token="t",
            error=None, notice=None, me=None, asset_version="1",
            form={"identifier_prefix": "kanzlei", "description": "", "schedule": "nightly",
                  "throughput": "normal", "file_types": ["documents"], "max_document_age_days": "",
                  "folders": [{"path": "/mnt/autodoc/mandate", "recursive": True, "groups": ["g-lit"]}]},
            schedules={"nightly": {"label": "Nachts", "description": "..."}},
            throughputs={"normal": {"label": "Normal", "description": "..."}},
            file_types={"documents": {"label": "Dokumente", "description": "..."}},
            groups=[{"group_id": "g-lit", "name": "Litigation"}],
            status={"state": "idle"}, current=None, versions=[], preview=None,
            support_json=None,
        )
        assert "/mnt/autodoc/mandate" in html
```

Also extend the tab-strip test's endpoint tuple in `tests/test_web_admin_documents.py` with `"admin.ingestion"`.

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_admin_ingestion.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'web_interface.admin_ingestion'`

- [ ] **Step 3: The module**

Create `src/web_interface/admin_ingestion.py`:

```python
"""The Ingestion tab: what to index, when, how fast, behind which wall.

One profile, one form, one write (section B plan, "Ingestion administration").
The form edits an IngestionProfile; compile_profile is the only thing that
produces RemoteController documents; RemoteControllerClient.push is the only
thing that sends them. Saving is a guarded action, because a profile change
can widen or halt coverage (KC-B5-2).

Plan: docs/superpowers/plans/2026-09-02-admin-ingestion-tab.md
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from flask import render_template, request

from identity import audit
from identity.approvals import ApprovalService
from identity.ingestion_compiler import (
    IngestionProfile,
    ProfileError,
    SourceFolder,
    compile_profile,
    redact_for_support,
)
from identity import ingestion_presets as presets
from identity.ingestion_profiles import (
    IngestionProfileRepository,
    profile_from_json,
    profile_to_json,
)
from remote_controller_client import RemoteControllerError
from web_interface.guarded import run_guarded

logger = logging.getLogger(__name__)

MAX_FOLDER_ROWS = 12
KIND = "ingestion_profile_change"

# German labels for the presets; the preset ids stay the compiler's.
LABELS = {
    "continuous": ("Laufend", "Neue und geaenderte Dokumente innerhalb weniger Minuten, den ganzen Tag."),
    "nightly": ("Nachts, ausserhalb der Buerozeiten", "Nur zwischen 19:00 und 06:00. Nichts laeuft, waehrend gearbeitet wird."),
    "manual": ("Nur wenn ich starte", "Laeuft einmal, wenn Sie auf Start druecken, und stoppt dann."),
    "gentle": ("Schonend", "Etwa 300 Dokumente pro Stunde. Keine spuerbare Last auf dem Dateiserver."),
    "normal": ("Normal", "Etwa 1'800 Dokumente pro Stunde. Die richtige Wahl fuer die meisten Kanzleien."),
    "fast": ("Schnell", "Etwa 7'200 Dokumente pro Stunde. Fuer den ersten Import, danach zuruecksetzen."),
    "documents": ("Dokumente", "Word, PDF, Text und Markdown."),
    "email": ("E-Mail", "Aus Outlook gespeicherte Nachrichten."),
}


def _labelled(table: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    return {key: {"label": LABELS.get(key, (key, ""))[0],
                  "description": LABELS.get(key, (key, ""))[1]} for key in table}


def profile_from_form(form: Mapping[str, str], lists: Mapping[str, list[str]]) -> IngestionProfile:
    """Build the profile the form describes. Raises ProfileError with a
    sentence a person can act on; compile_profile validates the rest."""
    schedule = str(form.get("schedule", "") or "").strip()
    throughput = str(form.get("throughput", "") or "").strip()
    if schedule not in presets.SCHEDULE_PRESETS:
        raise ProfileError("Bitte einen der angebotenen Zeitplaene waehlen.")
    if throughput not in presets.THROUGHPUT_PRESETS:
        raise ProfileError("Bitte eine der angebotenen Geschwindigkeiten waehlen.")
    sources: list[SourceFolder] = []
    for n in range(MAX_FOLDER_ROWS):
        path = str(form.get(f"folder-{n}-path", "") or "").strip()
        if not path:
            continue
        sources.append(SourceFolder(
            path=path,
            recursive=str(form.get(f"folder-{n}-recursive", "") or "") == "1",
            access_groups=tuple(g for g in (lists.get(f"folder-{n}-groups") or []) if g),
        ))
    if not sources:
        raise ProfileError("Mindestens ein Ordner muss angegeben sein.")
    age = str(form.get("max_document_age_days", "") or "").strip()
    return IngestionProfile(
        identifier_prefix=str(form.get("identifier_prefix", "") or "").strip(),
        sources=sources,
        file_types=[t for t in (lists.get("file_types") or []) if t] or ["documents"],
        schedule=schedule,
        throughput=throughput,
        max_document_age_days=int(age) if age.isdigit() else None,
        description=str(form.get("description", "") or "").strip(),
    )


def form_from_profile(profile: IngestionProfile | None) -> dict[str, Any]:
    if profile is None:
        return {"identifier_prefix": "", "description": "", "schedule": "nightly",
                "throughput": "normal", "file_types": ["documents"],
                "max_document_age_days": "", "folders": []}
    return {
        "identifier_prefix": profile.identifier_prefix,
        "description": profile.description,
        "schedule": profile.schedule,
        "throughput": profile.throughput,
        "file_types": list(profile.file_types),
        "max_document_age_days": "" if profile.max_document_age_days is None else str(profile.max_document_age_days),
        "folders": [{"path": s.path, "recursive": bool(s.recursive),
                     "groups": list(s.access_groups)} for s in profile.sources],
    }


def apply_profile(payload: Mapping[str, Any], actor, *, conn, rc_client) -> dict:
    """Save a new version and push it. The executor for ingestion_profile_change,
    used both when the actor may act alone and after a second person confirms."""
    profile = profile_from_json(payload["profile"])
    compiled = compile_profile(profile)
    repo = IngestionProfileRepository(conn)
    version = repo.save_new_version(profile, by=actor)
    rc_client.push(compiled)
    repo.mark_pushed(version.id)
    audit.record(conn, action="ingestion.profile_pushed", actor=actor,
                 target_type="ingestion_profile", target_id=f"default v{version.version}",
                 detail={"folders": len(profile.sources), "schedule": profile.schedule})
    return {"version": version.version}


def attach_ingestion_routes(bp, gate, *, csrf_valid, csrf_token, page_context,
                            client_factory, rc_client_factory, require_ingestion):
    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _approvals() -> ApprovalService:
        return ApprovalService(gate.connection(), gate.users())

    def _lists() -> dict[str, list[str]]:
        return {key: request.form.getlist(key) for key in request.form.keys()}

    def _page(form=None, *, error=None, notice=None, status=200, preview=None, support_json=None):
        repo = IngestionProfileRepository(gate.connection())
        current = repo.current()
        rc_status: dict[str, Any] = {}
        try:
            rc_status = rc_client_factory().status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("RemoteController-Status nicht abrufbar: %s", exc)
            rc_status = {"state": "unbekannt"}
        groups = []
        try:
            groups = client_factory().access_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsgruppen nicht abrufbar: %s", exc)
        return render_template(
            "admin_ingestion.html",
            active_nav="admin",
            **page_context(),
            form=form if form is not None else form_from_profile(current.profile if current else None),
            schedules=_labelled(presets.SCHEDULE_PRESETS),
            throughputs=_labelled(presets.THROUGHPUT_PRESETS),
            file_types=_labelled(presets.FILE_TYPE_PRESETS),
            groups=groups,
            status=rc_status,
            current=current,
            versions=repo.versions(),
            preview=preview,
            support_json=support_json,
            me=gate.current_user(),
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    def _queued_notice(req) -> str:
        return (f"Zur Freigabe eingereicht (Nr. {str(req.id)[:8]}). Das Profil wird erst "
                "nach Bestaetigung durch eine zweite Person uebernommen.")

    @bp.route("/ingestion")
    @require_ingestion
    def ingestion():
        return _page()

    @bp.route("/ingestion/preview", methods=["POST"])
    @require_ingestion
    def preview():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        try:
            profile = profile_from_form(request.form, _lists())
            compile_profile(profile)
        except ProfileError as exc:
            return _page(dict(request.form), error=str(exc), status=400)
        summary = []
        rc = rc_client_factory()
        for source in profile.sources:
            try:
                found = rc.discover(root=source.path, max_depth=3)
                summary.append({"path": source.path, "files": found.get("file_count"),
                                "folders": len(found.get("folders") or []), "error": None})
            except (RemoteControllerError, PermissionError) as exc:
                summary.append({"path": source.path, "files": None, "folders": None,
                                "error": str(exc)})
        return _page(form_from_profile(profile), preview=summary,
                     support_json=redact_for_support(profile),
                     notice="Vorschau erstellt. Noch nichts gespeichert.")

    @bp.route("/ingestion/save", methods=["POST"])
    @require_ingestion
    def save():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        try:
            profile = profile_from_form(request.form, _lists())
            compile_profile(profile)
        except ProfileError as exc:
            return _page(dict(request.form), error=str(exc), status=400)
        me = gate.current_user()
        payload = {"profile": profile_to_json(profile)}
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref="ingestion_profile:default",
                payload=payload,
                execute=lambda: apply_profile(payload, me, conn=gate.connection(),
                                              rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(form_from_profile(profile),
                         error=f"RemoteController hat das Profil nicht uebernommen: {exc}", status=502)
        if outcome.queued:
            return _page(form_from_profile(profile), notice=_queued_notice(outcome.request))
        return _page(notice=f"Profil gespeichert und uebertragen (Version {outcome.result['version']}).")

    @bp.route("/ingestion/restore/<int:version>", methods=["POST"])
    @require_ingestion
    def restore(version):
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        repo = IngestionProfileRepository(gate.connection())
        old = next((v for v in repo.versions() if v.version == version), None)
        if old is None:
            return _page(error=f"Version {version} gibt es nicht.", status=404)
        me = gate.current_user()
        payload = {"profile": profile_to_json(old.profile)}
        try:
            outcome = run_guarded(
                _approvals(), me, kind=KIND, target_ref=f"ingestion_profile:default@v{version}",
                payload=payload,
                execute=lambda: apply_profile(payload, me, conn=gate.connection(),
                                              rc_client=rc_client_factory()),
            )
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Wiederherstellen fehlgeschlagen: {exc}", status=502)
        if outcome.queued:
            return _page(notice=_queued_notice(outcome.request))
        return _page(notice=f"Version {version} wiederhergestellt als Version {outcome.result['version']}.")

    @bp.route("/ingestion/start", methods=["POST"])
    @require_ingestion
    def start():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        me = gate.current_user()
        try:
            rc_client_factory().start()
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Start fehlgeschlagen: {exc}", status=502)
        audit.record(gate.connection(), action="ingestion.started", actor=me,
                     target_type="remote_controller", target_id="sync", detail={})
        return _page(notice="Abgleich gestartet.")

    @bp.route("/ingestion/stop", methods=["POST"])
    @require_ingestion
    def stop():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        me = gate.current_user()

        def _halt():
            rc_client_factory().stop()
            audit.record(gate.connection(), action="ingestion.stopped", actor=me,
                         target_type="remote_controller", target_id="sync", detail={})
            return {"stopped": True}

        try:
            outcome = run_guarded(_approvals(), me, kind=KIND, target_ref="remote_controller:stop",
                                  payload={"action": "stop"}, execute=_halt)
        except (RemoteControllerError, PermissionError) as exc:
            return _page(error=f"Stopp fehlgeschlagen: {exc}", status=502)
        if outcome.queued:
            return _page(notice=_queued_notice(outcome.request))
        return _page(notice="Abgleich angehalten.")

    return bp
```

The `stop` payload `{"action": "stop"}` has no `"profile"` key, so the approvals executor must dispatch: in `admin.py`, register

```python
            "ingestion_profile_change": lambda payload, actor: (
                (rc_client_factory().stop() or {"stopped": True})
                if payload.get("action") == "stop"
                else apply_profile(payload, actor, conn=gate.connection(),
                                   rc_client=rc_client_factory())
            ),
```

- [ ] **Step 4: Mount it**

In `admin.py`: add `rc_client_factory` as a keyword parameter of `create_admin_blueprint` (default `None`; when `None`, do not mount the ingestion routes or register the executor — a deployment without RemoteController still gets the other tabs). Define `require_ingestion = _require_roles(frozenset({"admin", "ingestion_manager"}))`. Mount with the signature above and register the executor.

- [ ] **Step 5: The template**

Create `src/web_interface/templates/admin_ingestion.html` with the standard head (copy from `admin_access_groups.html`, title `Ingestion`), the sidebar and tab strip includes (`admin_tab = 'ingestion'`), and this body:

```html
    <h1>Ingestion</h1>
    <p class="hint">
        Welche Ordner indexiert werden, wann, wie schnell und hinter welcher Wand.
        Eine Vorschau zeigt, was ein Abgleich finden würde, bevor etwas gespeichert wird.
    </p>

    {% if error %}<p class="msg error" role="alert">{{ error }}</p>{% endif %}
    {% if notice %}<p class="msg ok" role="status">{{ notice }}</p>{% endif %}

    <section class="panel">
        <h2>Status</h2>
        <p>Abgleich: <strong>{{ status.state }}</strong>
            {% if current %}· Profil Version {{ current.version }}{% if current.pushed_at %}, übertragen{% endif %}{% endif %}
        </p>
        <form class="inline" method="post" action="{{ url_for('admin.start') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button type="submit">Start</button>
        </form>
        <form class="inline" method="post" action="{{ url_for('admin.stop') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button type="submit">Anhalten</button>
        </form>
    </section>

    {% if preview %}
    <section class="panel">
        <h2>Vorschau</h2>
        <table>
            <thead><tr><th>Ordner</th><th>Dateien</th><th>Unterordner</th></tr></thead>
            <tbody>
            {% for p in preview %}
                <tr><td class="ptr">{{ p.path }}</td>
                    <td>{% if p.error %}<span class="msg error">{{ p.error }}</span>{% else %}{{ p.files if p.files is not none else '—' }}{% endif %}</td>
                    <td>{{ p.folders if p.folders is not none else '—' }}</td></tr>
            {% endfor %}
            </tbody>
        </table>
        {% if support_json %}<p class="hint">Für den Support (ohne Pfade und Gruppen):</p><pre class="ptr">{{ support_json }}</pre>{% endif %}
    </section>
    {% endif %}

    <section class="panel">
        <h2>Profil</h2>
        <form method="post" action="{{ url_for('admin.save') }}" id="profile-form">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <label>Kennung <input type="text" name="identifier_prefix" value="{{ form.identifier_prefix }}" required></label>
            <label>Beschreibung <input type="text" name="description" value="{{ form.description }}"></label>

            <h3>Ordner</h3>
            <table>
                <thead><tr><th>Pfad</th><th>Mit Unterordnern</th><th>Zugriffsgruppen</th></tr></thead>
                <tbody>
                {% for n in range(12) %}
                    {% set f = form.folders[n] if n < form.folders|length else none %}
                    <tr>
                        <td><input type="text" name="folder-{{ n }}-path" value="{{ f.path if f else '' }}" placeholder="/mnt/autodoc/..."></td>
                        <td><input type="checkbox" name="folder-{{ n }}-recursive" value="1" {% if not f or f.recursive %}checked{% endif %}></td>
                        <td>
                            {% for g in groups %}
                            <label><input type="checkbox" name="folder-{{ n }}-groups" value="{{ g.group_id }}"
                                {% if f and g.group_id in f.groups %}checked{% endif %}> {{ g.name }}</label>
                            {% endfor %}
                        </td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>

            <h3>Was indexiert wird</h3>
            {% for key, t in file_types.items() %}
            <label><input type="checkbox" name="file_types" value="{{ key }}" {% if key in form.file_types %}checked{% endif %}> {{ t.label }} <span class="hint">{{ t.description }}</span></label>
            {% endfor %}

            <h3>Wann</h3>
            {% for key, t in schedules.items() %}
            <label><input type="radio" name="schedule" value="{{ key }}" {% if key == form.schedule %}checked{% endif %}> {{ t.label }} <span class="hint">{{ t.description }}</span></label>
            {% endfor %}

            <h3>Wie schnell</h3>
            {% for key, t in throughputs.items() %}
            <label><input type="radio" name="throughput" value="{{ key }}" {% if key == form.throughput %}checked{% endif %}> {{ t.label }} <span class="hint">{{ t.description }}</span></label>
            {% endfor %}

            <label>Dokumente ignorieren, die älter sind als (Tage)
                <input type="text" name="max_document_age_days" value="{{ form.max_document_age_days }}" placeholder="leer = kein Limit"></label>

            <p>
                <button type="submit" formaction="{{ url_for('admin.preview') }}">Vorschau</button>
                <button type="submit">Speichern und übertragen</button>
            </p>
        </form>
    </section>

    <section class="panel">
        <h2>Versionen</h2>
        <table>
            <thead><tr><th>Version</th><th>Erstellt</th><th>Übertragen</th><th></th></tr></thead>
            <tbody>
            {% for v in versions %}
                <tr>
                    <td>{{ v.version }}{% if v.is_current %} (aktuell){% endif %}</td>
                    <td>{{ v.created_at }}</td>
                    <td>{{ v.pushed_at or '—' }}</td>
                    <td>{% if not v.is_current %}
                        <form class="inline" method="post" action="{{ url_for('admin.restore', version=v.version) }}">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit">Wiederherstellen</button>
                        </form>{% endif %}</td>
                </tr>
            {% else %}
                <tr><td colspan="4" class="hint">Noch kein Profil gespeichert.</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </section>
```

Add the tab to `_admin_tabs.html` after Freigaben:

```html
    <a href="{{ url_for('admin.ingestion') }}"
       class="{% if admin_tab == 'ingestion' %}active{% endif %}"
       {% if admin_tab == 'ingestion' %}aria-current="page"{% endif %}>Ingestion</a>
```

The strip must not `url_for('admin.ingestion')` when the routes are not mounted (no RemoteController). Guard it: `{% if 'admin.ingestion' in (url_for.__globals__ if false else []) %}` is not a thing — instead pass `ingestion_enabled` through `page_context()` from `app.py` (`'ingestion_enabled': rc_client is not None`) and wrap the anchor in `{% if ingestion_enabled %}`. The render tests then pass `ingestion_enabled=True`.

- [ ] **Step 6: Run everything**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_admin_ingestion.py tests/test_web_admin_documents.py tests/test_web_admin_approvals.py`, then the full suite.
Expected: PASS; only the eight known environmental failures in the full suite.

- [ ] **Step 7: Commit**

```bash
git add src/web_interface/admin_ingestion.py src/web_interface/admin.py src/web_interface/app.py src/web_interface/templates/admin_ingestion.html src/web_interface/templates/_admin_tabs.html tests/test_web_admin_ingestion.py tests/test_web_admin_documents.py
git commit -m "feat(admin): Ingestion tab — one profile, one form, one write; preview, versions, restore (KC-IN-3, IN-7)"
```

---

### Task 5: Plumbing and documentation

**Files:**
- Modify: `KnovasPlatform/docker-compose.yml`, `docker-compose.yml` (root), `knovas.env.example`, `RemoteController/docs/configuration.md`, `KnovasPlatform/docs/features/document-administration.md`, `RELEASE_NOTES.md`

- [ ] **Step 1: Share the public key with RemoteController, read-only**

In both compose files, on the `remote-controller` service:

```yaml
    volumes:
      - docbridge_broker_key:/app/secrets/broker:ro
    environment:
      RC_PLATFORM_BROKER_PUBKEY_PATH: /app/secrets/broker/broker_ed25519.pub
```

(merge into the existing `volumes:`/`environment:` lists rather than duplicating the keys). The named volume already exists from the auth work; RemoteController gets the whole directory read-only and reads only the `.pub`. State in a comment that the private key is in the same directory and that `:ro` plus RemoteController never opening it is the whole protection — a reviewer will ask.

- [ ] **Step 2: Environment example**

Add beside `PLATFORM_BROKER_KEY_DIR`:

```
# The console reaches the firm's RemoteController here (knovas-internal only).
# RC_BASE_URL=http://remote-controller:5001
```

- [ ] **Step 3: Feature doc and release note**

Feature doc, new section before `## Scale`:

```markdown
## Ingestion

The Ingestion tab (`admin` and `ingestion_manager`) edits one profile: folders
with their access groups, file kinds, schedule, throughput, age cut-off. *Vorschau*
asks RemoteController what each folder holds without saving anything.
*Speichern und übertragen* compiles the profile into the two RemoteController
documents, validates both against their schemas, saves a new version and pushes
config-then-folders; if the folder list is refused, the previous config is put
back. Every version stays; *Wiederherstellen* copies an old one forward.

Saving, restoring and stopping the sync are four-eyes guarded
(`ingestion_profile_change`); see Freigaben. Starting and previewing are not.

RemoteController accepts the administrator's own Platform-signed principal in
`X-Platform-Principal` (`RC_PLATFORM_BROKER_PUBKEY_PATH`); nobody needs a Knovas
employee token, a shell on the host, or `chmod`.
```

Release note under `### Freigaben`:

```markdown
### Ingestion in der Verwaltung

Was indexiert wird, wann und hinter welcher Wand, wird jetzt in der Verwaltung
eingestellt — mit Vorschau, Versionen und Wiederherstellung. Der RemoteController
akzeptiert dafür die Anmeldung der Kanzlei selbst.
```

- [ ] **Step 4: Commit**

```bash
git add KnovasPlatform/docker-compose.yml docker-compose.yml knovas.env.example KnovasPlatform/docs/features/document-administration.md RELEASE_NOTES.md
git commit -m "docs(admin): ingestion administration — key sharing with RemoteController, env, feature doc"
```

---

## Self-Review

**Spec coverage.** KC-IN-1 → Task 1 (`require_operator_or_tenant_admin`, role `ingestion_manager` or `admin` in `rol`, beside the employee path, each route declares it). KC-IN-2 → Task 2 (versioned row, author, approver column carried, timestamp). KC-IN-3 → Task 4. KC-IN-5 → the client targets `remote-controller:5001` on `knovas-internal`; no port is published (Task 5 adds none). KC-IN-7 → preview via `/discover` (Task 4 `preview`), every save a new version, restore re-compiles and re-pushes (Task 4 `restore` → `apply_profile`). KC-IN-4/6 already landed. SS-391 AC 1 (no host edits) → Tasks 1, 3, 4; AC 2 (one write path) → `compile_profile` + `push` only, asserted by `test_the_compiler_is_the_only_writer`; AC 3 (per-source groups reach the upload) → `SourceFolder.access_groups` through the compiler, already covered by RemoteController's `test_sync_access_groups.py`; AC 4 → gate/CSRF/audit on every route; AC 5 German copy; AC 6 tests in CI. The spec's folder *browser* backed by `/discover` is reduced to a typed path plus a preview per folder — stated here rather than silently dropped; the browser is a UI iteration once the write path is real.

**Placeholder scan.** None. The preset dict names were verified against `ingestion_presets.py`.

**Type consistency.** `apply_profile(payload, actor, *, conn, rc_client)` — Task 4 definition, executor registration, `save`/`restore` calls. `RemoteControllerClient(base_url, *, principal_broker, session=None, timeout)` — Task 3 tests and `app.py`. `profile_to_json`/`profile_from_json` — Tasks 2 and 4. `IngestionProfileRepository.save_new_version(profile, *, name, by, approved_by)` — Tasks 2 and 4. `verify_platform_principal(token, *, public_pem, expected_tenant, replay, now)` — Task 1 tests and gate.
