# Platform: Mint and Send the Principal Assertion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Knovas Secure API call carry a subject the backend cryptographically verified, so that a lawyer's access groups are enforced rather than assumed.

**Architecture:** The Platform authenticates a person, resolves their access groups server-side, and signs both into a short-lived Ed25519 JWS. The Secure API verifies that signature against a key registered per tenant, binds the asserted tenant to the mTLS certificate tenant, and — under a new `BROKERED` posture — refuses to answer at all when the assertion is absent or invalid. The signing code already exists and is unit-tested on the Platform side; almost all of this plan is wiring it into the request path, building the verifier, and proving the failure modes.

**Tech Stack:** Python 3.13, Flask, PostgreSQL 15, Redis (replay cache), `cryptography` (Ed25519), pytest, GitHub Actions.

**Spec:** `docs/superpowers/plans/2026-08-14-section-b-buildout.md` (Pflichtenheft §3 Section B). Jira epic SS-295; this plan covers SS-336, SS-339–SS-343, SS-345 and the REQ-2/REQ-3 checkpoints.

## Global Constraints

- **The algorithm is never read from the token header.** `alg` is pinned to `EdDSA` in code, both when signing and when verifying. A `kid` selects *which registered key* to check against; it never selects an algorithm.
- **Assertion lifetime ≤ 300 s** (`MAX_LIFETIME_SECONDS`), default 120 s (`DEFAULT_LIFETIME_SECONDS`). Refused at mint time above the cap. This bound is the formal statement of how long a disabled user keeps access.
- **Clock-skew tolerance is 30 s** (`CLOCK_SKEW_SECONDS`) and stays. Platform and Knovas are different hosts; a second of drift must not deny a lawyer their file.
- **No fallback is ever silent.** A missing key, an unverifiable assertion, or an unreachable posture lookup fails closed with an error. None of them may degrade to `asserted=False`, which means "unrestricted documents only" and would return *more* data than a correct request.
- **The assertion carries no personal data.** `sub` is the opaque local user uuid. There is an existing test asserting the token contains no `@`; it stays.
- **The wire contract is a request body field**: `principal_assertion`, alongside the existing `access_groups`. Defined backend-side at `KnowledgeBase .../services/rbac/assertion.py:65`. Not a header.
- **Enforcement postures are `DISABLED` / `ENFORCING` / `BROKERED`** (`models.py:68-72`, already implemented). The 2026-08-14 plan doc calls the second one "ENABLED" and is wrong.
- **Repos:** `KnovasComponents` (customer-hosted Platform) and `KnowledgeBase` (Knovas backend). Task headers name which.
- **Branch:** `feat/section-b-buildout` in KnovasComponents until Task 2 merges it. The KnowledgeBase half is a separate plan on `feat/kb-auth-attribution` — see the Tasks 6-9 section below.

## Existing API this plan builds on

Already implemented and unit-tested in `KnovasPlatform/components/docbridge_integration/src/identity/`:

```python
# assertion.py
ALGORITHM = "EdDSA"
TOKEN_TYPE = "knovas-principal+jws"
DUAL_CONTROL_TYPE = "knovas-dual-control+jws"
DEFAULT_LIFETIME_SECONDS = 120
MAX_LIFETIME_SECONDS = 300
CLOCK_SKEW_SECONDS = 30

class InvalidAssertionError(Exception): ...
class ExpiredAssertionError(InvalidAssertionError): ...

@dataclass(frozen=True)
class Keypair:  # .private_pem: bytes, .public_pem: bytes, .key_id: str

@dataclass(frozen=True)
class PrincipalClaims:
    subject: str; tenant: str; groups: tuple[str, ...]; roles: tuple[str, ...]
    jti: str; issued_at: int; expires_at: int

def generate_keypair() -> Keypair: ...

class AssertionSigner:
    def __init__(self, private_pem: bytes | str, *, key_id: str) -> None: ...
    def mint(self, *, subject: str, tenant: str, groups: Iterable[str],
             roles: Iterable[str] = (), lifetime_seconds: int = DEFAULT_LIFETIME_SECONDS,
             issued_at: float | None = None) -> str: ...

class AssertionVerifier:
    def __init__(self, public_keys: Mapping[str, bytes | str]) -> None: ...
    def verify(self, token: str, *, tenant: str) -> PrincipalClaims: ...

# principal.py
class ClientAssertedGroupsError(ValueError): ...

class PrincipalBroker:
    def __init__(self, *, user_repo: Any, signer: AssertionSigner, tenant_id: str) -> None: ...
    def groups_for(self, user: Any) -> tuple[str, ...]: ...
    def assertion_for(self, user: Any) -> str: ...
    @staticmethod
    def reject_client_assertion(body: Mapping[str, Any] | None) -> None: ...

# webauth.py
class IdentityGate:
    def current_user(self): ...   # None when unauthenticated
    def guard(self): ...
```

`grep -rn "assertion" src/ --include=*.py | grep -v "^src/identity/"` currently returns **zero hits**. That is the gap this plan closes.

---

## File Structure

**KnovasComponents** (`KnovasPlatform/components/docbridge_integration/`)

| File | Responsibility |
|------|----------------|
| `.github/workflows/ci.yml` *(repo root)* | Add the `postgres:15-alpine` service so the ~151 identity tests execute |
| `tests/conftest.py` | Turn a PostgreSQL skip into a hard failure under CI |
| `src/identity/broker_key.py` *(new)* | Load or generate the Ed25519 signing key; fail closed when unreadable |
| `src/knovas_client.py:1254` | Attach the assertion in `_get_headers()` — one place, every call site inherits it |
| `src/web_interface/app.py` | Build the broker at startup; reject body-supplied `access_groups` |

**KnowledgeBase** — no files. That half already exists (verifier, broker keys, `BROKERED`), and
what remains of it has its own plan and its own branch. See the Tasks 6-9 section below.

------|----------------|
| `src/DB/migrations/20260902_principal_broker_key.sql` *(new)* | `clients` gains broker key, `kid`, previous key + overlap expiry |
| `src/api/internal_api.py` | `PUT`/`GET /admin/clients/<id>/principal_broker_key` |
| `src/services/rbac/assertion.py` *(new)* | Verify the JWS: alg pinned, tenant bound, typ checked, replay burned |
| `src/services/rbac/models.py:42` | `BROKERED` beside `DISABLED` / `ENFORCING` |
| `src/services/rbac/principal_resolver.py` | Assertion path in `from_request`; fail closed under `BROKERED` |
| `src/api/graph_api.py` | `actor_ref` overwritten from the verified `sub` |

---

### Task 1: PostgreSQL in CI — make the identity tests actually run

**Repo:** KnovasComponents. **Branch:** `feat/section-b-buildout`. **Jira:** SS-336, SS-348, SS-349.

This is first because every later task cites tests as its evidence, and right now the suite reports `1 failed, 281 passed, 155 skipped` — of which **151 skips** are `No PostgreSQL at postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test`. CI runs a bare `pytest` with no `services:` block, so it skips them too. Merging first would land ~2,400 lines of unexercised authentication on main.

**Files:**
- Modify: `.github/workflows/ci.yml:20-29`
- Modify: `KnovasPlatform/components/docbridge_integration/tests/conftest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a CI job in which `identity` tests execute. Every later task depends on this.

- [ ] **Step 1: Confirm the current skip count, so the improvement is measurable**

```bash
cd KnovasPlatform/components/docbridge_integration
.venv/Scripts/python.exe -m pytest tests/ -q --no-header -rs 2>&1 | grep -c "No PostgreSQL"
```

Expected: `151`

- [ ] **Step 2: Add the service to the workflow**

In `.github/workflows/ci.yml`, on the job that runs `pytest` in `KnovasPlatform/components/docbridge_integration` (currently line 28-29), add:

```yaml
    services:
      platform-db:
        image: postgres:15-alpine
        env:
          POSTGRES_USER: platform
          POSTGRES_PASSWORD: testpw
          POSTGRES_DB: knovas_platform_test
        ports:
          - 55433:5432
        options: >-
          --health-cmd "pg_isready -U platform -d knovas_platform_test"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
```

The port, user, password and database name must match the DSN the tests already expect. Do not invent new ones.

- [ ] **Step 3: Run migrations before the suite**

Add a step immediately before `run: pytest`:

```yaml
      - name: Run identity migrations
        working-directory: KnovasPlatform/components/docbridge_integration
        env:
          PLATFORM_DB_DSN: postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test
        run: python -m src.identity.migrate
```

- [ ] **Step 4: Write the failing test that forbids silent skipping**

> **Do not add a second `pytest_collection_modifyitems`.** `tests/conftest.py:121` already
> defines one (it applies the `--knovas-api` skip). A second definition in the same module
> silently shadows the first, disabling that skip — a plausible way to make three live-API
> tests start hitting the network in CI. Add the *new* hook below, which is a different hook,
> and leave the existing one alone.

Append to `tests/conftest.py` (do not modify lines 112-126):

```python
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Under CI a PostgreSQL skip is a failure, not a pass.

    151 identity tests skipped silently for weeks because an unreachable
    database looked exactly like a green run. In CI we would rather be
    loudly broken than quietly untested.
    """
    outcome = yield
    report = outcome.get_result()
    if os.environ.get("CI") != "true":
        return
    if report.skipped and "No PostgreSQL" in str(report.longrepr):
        report.outcome = "failed"
        report.longrepr = (
            "PostgreSQL was unreachable in CI. Identity tests must execute, "
            "not skip — a skipped security test is a test that does not exist."
        )
```

`import os` is needed at the top of the file if it is not already imported.

- [ ] **Step 5: Verify it fails without the database**

```bash
CI=true PLATFORM_DB_DSN=postgresql://platform:testpw@127.0.0.1:1/nope \
  .venv/Scripts/python.exe -m pytest tests/test_identity_users.py -q
```

Expected: FAIL, with the "PostgreSQL was unreachable in CI" message. Not `skipped`.

- [ ] **Step 6: Start a local PostgreSQL and run the full suite**

```bash
docker run -d --name plat-test -e POSTGRES_USER=platform -e POSTGRES_PASSWORD=testpw \
  -e POSTGRES_DB=knovas_platform_test -p 55433:5432 postgres:15-alpine
python -m src.identity.migrate
.venv/Scripts/python.exe -m pytest tests/ -q --no-header -rs 2>&1 | grep -c "No PostgreSQL"
```

Expected: `0`

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ci.yml KnovasPlatform/components/docbridge_integration/tests/conftest.py
git commit -m "ci: run identity tests against a real PostgreSQL, and fail on skip

151 of 155 skips were 'No PostgreSQL'. Every identity test — passwords,
sessions, users, roles, bootstrap, migrations, approvals, the broker and
the assertion — had never executed on any machine. A suite that reports
green while skipping its security coverage is worse than no suite."
```

---

### Task 2: Land the branch on main

**Repo:** KnovasComponents. **Jira:** SS-339, SS-350, SS-351.

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/identity/passwords.py` (conflict)
- Modify: `KnovasPlatform/components/docbridge_integration/src/identity/ingestion_compiler.py` (conflict)

**Interfaces:**
- Consumes: Task 1's green CI.
- Produces: `origin/main` containing the 14-file identity package. Tasks 3–5 branch from it.

- [ ] **Step 1: Fetch and inspect the divergence**

```bash
cd E:/Knovas/KnovasComponents
git fetch origin
git log --oneline origin/main..feat/section-b-buildout
```

Expected: 14 commits. `origin/main` is at `026293e`; local `main` is stale at `e4e6992` — do not use it.

- [ ] **Step 2: Rebase**

```bash
git checkout feat/section-b-buildout
git rebase origin/main
```

Only `passwords.py` and `ingestion_compiler.py` should conflict — those two landed via PR #7 (`d8f59e0`, `64cc00f`) and exist on both sides. The other ten identity files are new on the branch and apply cleanly. Resolve by keeping the branch version of both.

- [ ] **Step 3: Re-run the full suite against PostgreSQL**

```bash
cd KnovasPlatform/components/docbridge_integration
.venv/Scripts/python.exe -m pytest tests/ -q --no-header
```

Expected: all pass except `test_ontology_api.py::test_sidebar_shows_corpus_only_when_fixture_has_it`.

- [ ] **Step 4: Deal with the one unrelated failure**

That test asserts `"corpus-status" in body`; the sidebar refactor on `main` (`e4e6992` "Refactor sidebar and remove corpus document display") removed the element. It is not an auth failure. Either update the assertion to match the new template, or mark it `xfail` with a linked ticket (SS-351) and a named owner. Do not merge with an unexplained red test — a suite with one accepted failure trains everyone to ignore the next one.

- [ ] **Step 5: Open the PR and request security review before merging**

The diff is ~2,400 lines of authentication. Review it while it is still a diff. See Task 10 for the checklist.

- [ ] **Step 6: Merge**

```bash
git checkout main && git pull origin main
git merge --no-ff feat/section-b-buildout
git push origin main
```

---

### Task 3: The broker signing key — fail closed when it is missing

**Repo:** KnovasComponents. **Jira:** SS-355.

**Files:**
- Create: `KnovasPlatform/components/docbridge_integration/src/identity/broker_key.py`
- Create: `KnovasPlatform/components/docbridge_integration/tests/test_identity_broker_key.py`
- Modify: `docs/certificates.md`

**Interfaces:**
- Consumes: `generate_keypair()`, `Keypair`, `AssertionSigner` from `identity/assertion.py`.
- Produces:
  ```python
  class BrokerKeyUnavailableError(RuntimeError): ...
  def load_or_create_signer(key_dir: Path, *, key_id: str | None = None) -> AssertionSigner: ...
  def public_pem(key_dir: Path) -> bytes: ...
  ```
  Task 4 calls `load_or_create_signer` at startup. Task 6 registers `public_pem` output with the backend.

- [ ] **Step 1: Write the failing tests**

```python
import stat
from pathlib import Path

import pytest

from src.identity.broker_key import (
    BrokerKeyUnavailableError,
    load_or_create_signer,
    public_pem,
)


def test_first_call_creates_a_key(tmp_path: Path):
    signer = load_or_create_signer(tmp_path)
    token = signer.mint(subject="u1", tenant="t1", groups=["g1"])
    assert token.count(".") == 2


def test_key_file_is_owner_only(tmp_path: Path):
    load_or_create_signer(tmp_path)
    mode = (tmp_path / "broker_ed25519.pem").stat().st_mode
    assert not mode & stat.S_IRGRP
    assert not mode & stat.S_IROTH


def test_second_call_reuses_the_same_key(tmp_path: Path):
    first = public_pem(tmp_path) if (tmp_path / "broker_ed25519.pem").exists() else None
    load_or_create_signer(tmp_path)
    once = public_pem(tmp_path)
    load_or_create_signer(tmp_path)
    assert public_pem(tmp_path) == once
    assert first is None


def test_unreadable_key_raises_rather_than_regenerating(tmp_path: Path):
    """The dangerous failure is a silent new key.

    A regenerated key still signs, so the Platform looks healthy while every
    assertion it mints is rejected by a backend holding the old public key.
    Refuse loudly instead.
    """
    load_or_create_signer(tmp_path)
    (tmp_path / "broker_ed25519.pem").write_bytes(b"not a pem")
    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path)


def test_directory_missing_raises(tmp_path: Path):
    with pytest.raises(BrokerKeyUnavailableError):
        load_or_create_signer(tmp_path / "nope" / "deeper")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/test_identity_broker_key.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.identity.broker_key'`

- [ ] **Step 3: Implement**

```python
"""The Platform's Ed25519 signing key — the thing that makes a person portable.

This key is the Platform's half of the trust boundary. Knovas holds the public
half against the tenant record; whoever holds this private half can assert any
of the firm's people. It therefore never leaves the firm's host, never enters
an image, and never appears in a log.

The failure mode this module exists to prevent is a *silently regenerated* key.
A fresh key still signs perfectly well, so the Platform would look healthy while
every assertion it minted was refused by a backend still holding the old public
key — and the symptom would surface as "search returns nothing", days later, far
from the cause. So: unreadable is an error, never a reason to make a new one.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .assertion import AssertionSigner, Keypair, generate_keypair

_KEY_NAME = "broker_ed25519.pem"
_PUB_NAME = "broker_ed25519.pub"
_KID_NAME = "broker_ed25519.kid"


class BrokerKeyUnavailableError(RuntimeError):
    """The signing key could not be loaded, and must not be replaced."""


def _paths(key_dir: Path) -> tuple[Path, Path, Path]:
    return key_dir / _KEY_NAME, key_dir / _PUB_NAME, key_dir / _KID_NAME


def load_or_create_signer(key_dir: Path, *, key_id: str | None = None) -> AssertionSigner:
    key_dir = Path(key_dir)
    priv, pub, kid = _paths(key_dir)

    if priv.exists():
        try:
            return AssertionSigner(priv.read_bytes(), key_id=kid.read_text().strip())
        except Exception as exc:  # noqa: BLE001 - any failure here is fatal by design
            raise BrokerKeyUnavailableError(
                f"{priv} exists but could not be loaded as an Ed25519 signing key "
                f"({exc}). Refusing to generate a replacement: a new key would sign "
                "happily and every assertion would then be rejected by Knovas, which "
                "still holds the old public key. Restore the key from backup, or "
                "rotate deliberately via the Employee Kit."
            ) from exc

    if not key_dir.is_dir():
        raise BrokerKeyUnavailableError(
            f"{key_dir} is not a directory. The broker key directory must exist and "
            "be writable before the Platform starts."
        )

    pair: Keypair = generate_keypair()
    priv.write_bytes(pair.private_pem)
    os.chmod(priv, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    pub.write_bytes(pair.public_pem)
    kid.write_text(key_id or pair.key_id)
    return AssertionSigner(pair.private_pem, key_id=key_id or pair.key_id)


def public_pem(key_dir: Path) -> bytes:
    """The half that is registered with Knovas. Safe to print, copy and mail."""
    _, pub, _ = _paths(Path(key_dir))
    if not pub.exists():
        raise BrokerKeyUnavailableError(f"{pub} does not exist; no key has been generated.")
    return pub.read_bytes()
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_identity_broker_key.py -v`
Expected: PASS (5 tests)

> On Windows, `S_IRGRP`/`S_IROTH` are not enforced by the filesystem; the two mode assertions are meaningful on the Linux container the Platform actually ships in. If they fail locally on Windows, guard them with `@pytest.mark.skipif(os.name == "nt", ...)` rather than deleting them.

- [ ] **Step 5: Document the third key artifact**

`docs/certificates.md` opens by warning that mismatched certificate filenames "are the most common setup failure". Add a section for the broker key beside the mTLS pair: where it lives, that it is `0600`, that it must be backed up, and that losing it means re-registering with Knovas. If it is not documented here it becomes the *next* most common setup failure.

- [ ] **Step 6: Commit**

```bash
git add src/identity/broker_key.py tests/test_identity_broker_key.py ../../../docs/certificates.md
git commit -m "feat(identity): broker signing key, refusing to regenerate itself

An unreadable key is fatal, never a reason to mint a new one: a fresh key
signs fine and every assertion is then refused by a backend holding the old
public half, surfacing days later as 'search returns nothing'."
```

---

### Task 4: Attach the assertion to every outbound call

**Repo:** KnovasComponents. **Jira:** SS-340, SS-352, SS-353.

The highest-value change in the epic. One edit in `_get_headers()` makes search, previews, downloads and AI answers wall-aware, because they all funnel through this client.

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_client.py:1254-1267`
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/app.py` (startup wiring)
- Create: `KnovasPlatform/components/docbridge_integration/tests/test_knovas_client_assertion.py`

**Interfaces:**
- Consumes: `load_or_create_signer` (Task 3), `PrincipalBroker.assertion_for(user)`, `IdentityGate.current_user()`.
- Produces: header `principal_assertion` on every request. Task 7 verifies it under exactly that name.

- [ ] **Step 1: Write the failing tests**

```python
"""The gap these tests exist to close.

`assertion.py` was complete, correct and thoroughly unit-tested — and nothing
called it. So these assert on the *wire*, not on the mint function: the bug was
never in minting.
"""
import pytest

from src.identity.assertion import AssertionVerifier
from src.knovas_client import ASSERTION_FIELD, KnovasClient


def test_every_request_carries_an_assertion(client_with_broker, captured_requests):
    client_with_broker.search("Mietrecht")
    assert ASSERTION_FIELD in captured_requests[-1].body


def test_the_assertion_verifies_and_carries_the_users_groups(
    client_with_broker, captured_requests, broker_public_pem, broker_kid
):
    client_with_broker.search("Mietrecht")
    token = captured_requests[-1].body[ASSERTION_FIELD]
    claims = AssertionVerifier({broker_kid: broker_public_pem}).verify(token, tenant="tenant-a")
    assert claims.groups == ("litigation",)
    assert claims.subject == "11111111-1111-1111-1111-111111111111"


def test_no_session_means_no_request_at_all(client_with_broker_no_user):
    """Fail closed. An unsigned call would resolve to 'unrestricted documents
    only' at the backend — more data than a correct request would return."""
    with pytest.raises(PermissionError):
        client_with_broker_no_user.search("Mietrecht")


def test_the_assertion_contains_no_personal_data(client_with_broker, captured_requests):
    client_with_broker.search("Mietrecht")
    assert "@" not in captured_requests[-1].body[ASSERTION_FIELD]


def test_two_calls_get_distinct_jtis(client_with_broker, captured_requests,
                                     broker_public_pem, broker_kid):
    """Minted per request, not per session — so a revocation lands on the next
    request rather than at session expiry."""
    client_with_broker.search("a")
    client_with_broker.search("b")
    verifier = AssertionVerifier({broker_kid: broker_public_pem})
    first = verifier.verify(captured_requests[-2].body[ASSERTION_FIELD], tenant="tenant-a")
    second = verifier.verify(captured_requests[-1].body[ASSERTION_FIELD], tenant="tenant-a")
    assert first.jti != second.jti
```

**The fixtures these tests need.** `tests/conftest.py` already provides `platform_db`,
`identity_app`, `identity_client`, `identity_repo`, `docbridge_app` and the `DummyKnovasClient`
mock (`tests/conftest.py:128`). Build on those rather than inventing a parallel set — in
particular `DummyKnovasClient` is the seam where outbound headers can be captured, because
`identity_app` already monkeypatches it in as `web_app.KnovasAPIClient`.

Append to `tests/conftest.py`:

```python
@pytest.fixture
def broker_keypair(tmp_path):
    from src.identity.broker_key import load_or_create_signer, public_pem
    signer = load_or_create_signer(tmp_path)
    kid = (tmp_path / "broker_ed25519.kid").read_text().strip()
    return signer, public_pem(tmp_path), kid


@pytest.fixture
def broker_public_pem(broker_keypair):
    return broker_keypair[1]


@pytest.fixture
def broker_kid(broker_keypair):
    return broker_keypair[2]


@pytest.fixture
def captured_requests(monkeypatch):
    """Every outbound call's headers, in order.

    The bug this whole task fixes was that minting was well tested and never
    called — so assert on what left the process, not on what mint() returned.
    """
    calls = []

    class _Captured:
        def __init__(self, body, headers):
            self.body = body or {}
            self.headers = headers

    def _record(self, method, endpoint, data=None, params=None, **kwargs):
        # Capture *after* _with_principal has run, so the test sees the body
        # that actually goes on the wire.
        body = self._with_principal(data)
        calls.append(_Captured(body, dict(self._get_headers())))

        class _Resp:
            status_code = 200
            @staticmethod
            def json():
                return {"results": [], "total": 0}
        return _Resp()

    from knovas_client import KnovasAPIClient
    monkeypatch.setattr(KnovasAPIClient, "_request", _record)
    return calls


@pytest.fixture
def client_with_broker(identity_app, identity_repo, broker_keypair):
    """A signed-in user holding exactly one access group."""
    signer, _, _ = broker_keypair
    user = identity_repo.create_user(
        email="anwalt@testco.example",
        user_id="11111111-1111-1111-1111-111111111111",
    )
    identity_repo.set_access_groups(user.id, ["litigation"])
    client = identity_app.test_client()
    # Sign in through the real login route so the session is built the way
    # production builds it — a hand-forged session cookie would not exercise
    # the gate this task depends on.
    client.post("/login", data={"email": user.email, "password": "…"})
    return client


@pytest.fixture
def client_with_broker_no_user(identity_app):
    return identity_app.test_client()
```

> **Confirm the two `identity_repo` methods before writing this.** `create_user` and
> `set_access_groups` are the names this plan assumes; check them against
> `src/identity/users.py` and adjust the fixture — not the production code — if they differ.
> Likewise the login form field names, against the real `/login` handler.

- [ ] **Step 2: Run and watch them fail**

Run: `pytest tests/test_knovas_client_assertion.py -v`
Expected: FAIL — `ImportError: cannot import name 'ASSERTION_FIELD' from 'src.knovas_client'`

- [ ] **Step 3: Implement in `knovas_client.py`**

> **The contract is a request body field, not a header.** KnowledgeBase's
> `services/rbac/assertion.py:65` defines `ASSERTION_FIELD = "principal_assertion"`, read from the
> JSON body alongside the existing `access_groups`. An earlier revision of this plan invented a
> header; a Platform sending one would have been silently ignored by a `BROKERED` backend, which
> would then have refused every request with 401. Match the field name exactly.

At module level:

```python
# The field that carries a person across the mTLS boundary. KnowledgeBase reads
# this exact name (services/rbac/assertion.py:65). Changing it breaks the contract.
ASSERTION_FIELD = "principal_assertion"
```

Extend `__init__` to accept the broker (keyword-only, defaulting to `None` so existing
construction sites keep working until Task 5 wires them):

```python
def __init__(self, ..., principal_broker=None):
    ...
    self._principal_broker = principal_broker
```

Then inject the assertion into the outgoing body. There are two choke points — `_request` (the
retrying path, `json=data` at `:1314`) and `_request_no_retry` (`:1324`, `json=data` at `:1341`).
Both must go through one helper so they cannot drift:

```python
    def _with_principal(self, data):
        """Attach the caller's assertion to an outgoing body.

        Fail closed when there is no authenticated user. Sending an unsigned
        call would resolve to asserted=False at the Secure API — "unrestricted
        documents only" — so the request would return *more* than a correctly
        scoped one. A wall that widens under failure is not a wall.
        """
        if self._principal_broker is None:
            return data

        user = self._principal_broker.current_user()
        if user is None:
            raise PermissionError(
                "No authenticated user for this request; refusing to call "
                "Knovas without a principal assertion."
            )

        assertion = self._principal_broker.assertion_for(user)
        if data is None:
            return {ASSERTION_FIELD: assertion}
        if not isinstance(data, dict):
            # Every secured endpoint takes a JSON object. A non-dict body here
            # means a caller we have not accounted for, and guessing how to
            # attach the assertion would be how one route quietly loses it.
            raise TypeError(
                f"Cannot attach a principal assertion to a {type(data).__name__} body."
            )
        return {**data, ASSERTION_FIELD: assertion}
```

Call it in both request methods, immediately before the `self._session.request(...)` call:

```python
        data = self._with_principal(data)
```

> **Do not attach it in `_get_headers()`.** That was the earlier design and it is the wrong
> transport. `_get_headers()` stays exactly as it is.
```

- [ ] **Step 4: Wire it at startup in `app.py`**

Where the Knovas client is constructed, build the broker from the identity gate:

```python
from src.identity.broker_key import load_or_create_signer
from src.identity.principal import PrincipalBroker


class _RequestScopedBroker:
    """Binds PrincipalBroker to whoever is signed in on *this* request.

    The broker reads user_access_groups at mint time with no caching, so an
    administrator's revocation takes effect on the user's next request rather
    than when their session happens to expire.
    """

    def __init__(self, gate, signer, tenant_id):
        self._gate = gate
        self._broker = PrincipalBroker(
            user_repo=gate.users, signer=signer, tenant_id=tenant_id
        )

    def current_user(self):
        return self._gate.current_user()

    def assertion_for(self, user):
        return self._broker.assertion_for(user)


if identity_enabled:
    signer = load_or_create_signer(Path(config.get("identity.broker_key_dir")))
    principal_broker = _RequestScopedBroker(identity_gate, signer, config.get("knovas.client_id"))
else:
    principal_broker = None
```

`load_or_create_signer` raising `BrokerKeyUnavailableError` here is intended: with identity enabled and no usable key, the Platform must not start.

> **Both config keys are new.** `app.py:699` today reads only `identity.enabled`
> (`config.get_bool('identity.enabled', False)`). Neither `identity.broker_key_dir` nor a client
> id under `api.` exists. Add both, in three places or the fixtures break:
>
> - `knovas.env.example` and `KnovasPlatform/docs/setup.md`, beside `PLATFORM_ADMIN_EMAIL`.
> - `tests/conftest.py:56 identity_app` — its inline `config.yaml` must gain them, or every test
>   using that fixture fails at startup once this wiring lands:
>
>   ```yaml
>   identity:
>     enabled: true
>     broker_key_dir: "<the fixture's tmp_path>"
>   api:
>     base_url: "http://example.test"
>     client_id: "tenant-a"
>   ```
>
>   Point `broker_key_dir` at the fixture's existing `tmp_path` so each test gets a fresh key —
>   otherwise leftover state between runs trips Task 3's "never regenerate" rule.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_knovas_client_assertion.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Prove the gap is closed**

```bash
grep -rn "assertion\|ASSERTION_FIELD" src/ --include=*.py | grep -v "^src/identity/"
```

Expected: hits in `knovas_client.py` and `web_interface/app.py`. Before this task the same command returned nothing.

- [ ] **Step 7: Commit**

```bash
git add src/knovas_client.py src/web_interface/app.py tests/test_knovas_client_assertion.py tests/conftest.py
git commit -m "feat(identity): attach the principal assertion to every Knovas call

assertion.py was complete and nothing called it, so every request reached the
Secure API with no subject and resolved to 'unrestricted documents only'. One
edit in _get_headers() makes search, previews and AI answers wall-aware,
because they all funnel through this client. No session, no request."
```

---

### Task 5: Reject browser-supplied access groups

**Repo:** KnovasComponents. **Jira:** SS-354.

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/app.py`
- Create: `KnovasPlatform/components/docbridge_integration/tests/test_web_rejects_client_groups.py`

**Interfaces:**
- Consumes: `PrincipalBroker.reject_client_assertion(body)`, `ClientAssertedGroupsError`.
- Produces: HTTP 400 on any request whose JSON body contains `access_groups`.

- [ ] **Step 1: Write the failing tests**

```python
def test_body_supplied_access_groups_are_rejected(client_with_broker):
    """Rejected, not ignored.

    Silently dropping the field would let a caller believe a scope applied when
    it never did, and would make a future merging bug invisible — the field
    would sit there unused until someone 'fixed' it by honouring it.
    """
    r = client_with_broker.post("/api/search", json={"q": "x", "access_groups": ["litigation"]})
    assert r.status_code == 400


def test_empty_access_groups_list_is_also_rejected(client_with_broker):
    r = client_with_broker.post("/api/search", json={"q": "x", "access_groups": []})
    assert r.status_code == 400


def test_a_normal_request_is_unaffected(client_with_broker):
    r = client_with_broker.post("/api/search", json={"q": "x"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run and watch them fail**

Run: `pytest tests/test_web_rejects_client_groups.py -v`
Expected: FAIL — the first two return 200.

- [ ] **Step 3: Implement as a `before_request` hook**

In `app.py`, beside the existing CSRF gate:

```python
    @app.before_request
    def reject_client_asserted_groups():
        """The group list has exactly one source: user_access_groups, read
        server-side for the signed-in user. A browser that supplies its own is
        refused rather than quietly overruled."""
        if not request.is_json:
            return None
        try:
            PrincipalBroker.reject_client_assertion(request.get_json(silent=True))
        except ClientAssertedGroupsError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return None
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_web_rejects_client_groups.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/web_interface/app.py tests/test_web_rejects_client_groups.py
git commit -m "feat(identity): refuse a browser-supplied access_groups with 400"
```

---

## Tasks 6-9 are already done — verified 2026-09-02

**Correction.** An earlier revision of this plan specified building broker-key registration, the
assertion verifier and the `BROKERED` posture in KnowledgeBase. **They already exist.** That claim
came from grepping the `docs/data-lake-spec` branch; the work lives on `feat/section-b-buildout`,
which is 8 commits ahead of `origin/master`:

| Item | Where | Jira |
|------|-------|------|
| Per-tenant broker key store + migration | `src/services/rbac/broker_keys.py`, `migrations/20260814_principal_broker_key.sql` | KB-B2-1 |
| Assertion verifier | `src/services/rbac/assertion.py` (`PrincipalAssertionVerifier`, `RedisReplayStore`) | KB-B2-2 |
| `BROKERED` posture, failing closed | `models.py:51,70`, wired at `principal_resolver.py:147-161` | KB-B2-3 |

`pytest tests/test_rbac_brokered_assertion.py tests/test_rbac_broker_keys.py` → **34 passed**, no
PostgreSQL required.

Building them again would have produced a second, competing verifier over the same tokens.

**What remains in KnowledgeBase** — `actor_ref` binding, the topology export, the four node-attached
routes, the Golden Invariants and the Alloy model — is now its own plan on its own branch:

- Branch: `feat/kb-auth-attribution` (worktree `.worktrees/kb-auth-attribution`), based on
  `feat/section-b-buildout`.
- Plan: `KnowledgeBase/docs/superpowers/plans/2026-09-02-kb-auth-attribution-and-topology.md`

This plan is therefore **KnovasComponents only**: Tasks 1-5, then Task 10's cutover.

---

### Task 6: Cutover, and the review that gates it

**Repos:** both, coordinated. **Jira:** SS-365, SS-346, SS-376.

**Files:**
- Modify: `docs/Knovas_Employee_Kit/` (cutover runbook)
- Modify: `docs/Docs/01_SYSTEM/Golden_Invariants.md`

- [ ] **Step 1: Verify the intermediate state exists**

Flipping a live tenant to `BROKERED` before its Platform sends assertions locks every user out of
every document. Confirm by test that under `ENFORCING` the backend **accepts and verifies** an
assertion when present without **requiring** one. If that state does not exist, there is no safe way
to turn this on for a real firm — build it before going further.

```python
def test_enforcing_verifies_an_assertion_but_does_not_require_one(enforcing_tenant, api, valid_assertion):
    assert api.get("/secured/query?q=x").status_code == 200
    r = api.get("/secured/query?q=x", headers={"principal_assertion": valid_assertion})
    assert r.status_code == 200
    assert {d["id"] for d in r.json["documents"]} == {"litigation-doc"}
```

- [ ] **Step 2: Write the ordered cutover into the Employee Kit**

1. Register the broker key. Tenant stays `DISABLED`/`ENFORCING`.
2. Platform upgrade — assertions start flowing. Verified when present, not required.
3. Confirm from telemetry that assertions arrive on **every** route, not most.
4. Flip to `BROKERED`.

Rollback: flipping back to `ENFORCING` restores service immediately. State that explicitly — an
operator under pressure should not have to infer it.

- [ ] **Step 3: Record the Golden Invariants**

GI-BROKER-01 … 04 in `docs/Docs/01_SYSTEM/Golden_Invariants.md`, worded as in SS-374. These are lint
inputs: `check_alloy_coverage.py` fails if a model cites a `GI-*` that does not exist, so they land
in the same commit as the model that cites them.

- [ ] **Step 4: Adversarial review**

Run the `security-exploit-analyzer` agent over `services/rbac/assertion.py`,
`principal_resolver.py` and the Platform's `_get_headers()`. Treat findings as input, not verdict —
verify each against the code before acting. Every confirmed finding becomes a failing test first.

- [ ] **Step 5: Confirm the residual risk is written down, not just known**

Three things are true after this plan and must reach the pilot contract (REQ-5 / SS-338):

- The Platform host holds **both** the tenant certificate and the broker signing key. This narrows
  the trust boundary from "anyone with the certificate" to "the broker process on the firm's own
  host". It does not eliminate it. Full elimination needs per-user client certificates or an
  IdP-signed token verified directly by secure-api.
- **MFA and OIDC are still dropped**, so a single stolen password reaches the corpus.
- **Ranking-signal leakage across walls is unverified** — the German BM25 corpus model and the two
  learned identifier channels are tenant-wide and have not been measured for score drift on a walled
  corpus. Until that test runs, say "enforced on every read path", never "no trace".

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "docs: broker cutover runbook, GI-BROKER-01..04, residual risk"
```

---

## Out of scope — these need their own plans

Each is independently shippable and none changes the security posture this plan establishes:

- **Access groups tab** (SS-344). Until it exists, `user_access_groups` is populated only by hand, so
  every assertion carries an empty group list. *This plan makes walls enforceable; that one makes
  them usable.* It should be the next plan written.
- **Everything remaining in KnowledgeBase** — `actor_ref` binding, the topology export, the four
  node-attached routes, GI-BROKER-01…04 and the Alloy model (SS-370 to SS-375). Own branch
  (`feat/kb-auth-attribution`), own plan
  (`KnowledgeBase/docs/superpowers/plans/2026-09-02-kb-auth-attribution-and-topology.md`).
- **Session and credential hardening** (SS-347). Per-user lockout, session rotation on privilege
  change, the `open_token_redeem` CSRF exemption, login enumeration.

## Self-review

**Spec coverage.** REQ-2 (SS-335) → Tasks 4 and 5 on this side; its backend half is already built
(see the Tasks 6-9 section) apart from `actor_ref`, which is in the KnowledgeBase plan. REQ-3
(SS-336) → Task 1. REQ-4's signing-key clauses → Task 3; its session and credential clauses are out
of scope above and named as such. REQ-5 (SS-338) → Task 6 Step 5. REQ-1 (SS-334) is **not** covered
here: deleting the legacy shared-login path belongs with the session work, and this plan
deliberately leaves `identity.enabled` defaulting to `False` so the cutover in Task 6 can be staged.
A gap by design, stated rather than left to be discovered.

**Type consistency.** `ASSERTION_FIELD = "principal_assertion"` is defined in Task 4 and must match
`KnowledgeBase .../services/rbac/assertion.py:65` exactly — it is a cross-repo wire contract with no
shared import to keep the two honest, so a rename on either side is a silent break.
`load_or_create_signer` / `public_pem` are produced in Task 3 and consumed in Task 4; `public_pem`'s
output is what an operator registers with Knovas in Task 6. `PrincipalClaims` (Platform, minting)
and `VerifiedPrincipal` (backend, verifying) are deliberately distinct types in distinct repos.

**Ordering.** Task 1 gates everything, because until it passes no later task's tests actually run.
Task 2 gates 3–5. Task 3 gates Task 4 (no key, nothing to sign with). Task 6 Step 1 gates the
production flip.

**Known softness, stated rather than hidden.** Four defects were found reviewing this plan against
the real code, and fixed:

1. Task 1 originally added a second `pytest_collection_modifyitems`, which would have silently
   shadowed the one at `tests/conftest.py:121` and re-enabled three live-API tests in CI. Now adds
   `pytest_runtest_makereport`, a different hook.
2. `identity.broker_key_dir` and `api.client_id` do not exist; only `identity.enabled` does. Task 4
   lists the three places they must be added, including the `identity_app` fixture's inline config.
3. Platform fixtures were invented. Task 4 now builds on the real `identity_app` / `identity_repo` /
   `DummyKnovasClient`, with an instruction to verify two repository method names first.
4. **The transport was wrong.** This plan originally attached the assertion as an
   `X-Knovas-Principal-Assertion` header. The backend reads a `principal_assertion` *body field*. A
   Platform shipping the header would have been ignored by a `BROKERED` tenant, which would then
   have refused every request with 401 — and the symptom, "search returns nothing after we turned
   on the security feature", would have looked like a backend bug. Task 4 now injects into the body
   via `_with_principal`, called from both `_request` and `_request_no_retry`.

The claim that KnowledgeBase was "plan-only" was also wrong, and came from grepping the wrong
branch. Corrected in the Tasks 6-9 section.
