# Admin Console: Approvals Tab (Freigaben) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the console's guarded actions through the existing four-eyes service, and give approvers a queue that shows pending requests *and* every administrator bypass.

**Architecture:** `identity/approvals.py` (KC-B5-1) is complete and called from nowhere. One helper, `web_interface/guarded.py::run_guarded`, becomes the single way a console route performs a guarded action: it either queues a request or executes and records the bypass. The three ACL routes in `admin_documents.py` go through it with kind `acl_change`, and their execution moves into one function, `execute_acl_change`, that the approve path reuses. A new `admin_approvals.py` mounts the queue on the existing admin blueprint behind an `admin`-or-`approver` gate.

**Tech Stack:** Python 3.11, Flask blueprints, Jinja2, psycopg 3, pytest. No build step — the Platform ships no bundler.

**Spec:** `docs/superpowers/plans/2026-08-14-section-b-buildout.md` § B5 (KC-B5-2, KC-B5-4); Jira SS-392 acceptance criteria; SS-384 (REQ-A2), SS-386 (REQ-A4 clause 5).

## Global Constraints

- **Branch:** `feat/auth-assertion` in `E:/Knovas/KnovasComponents`. Tests run from `KnovasPlatform/components/docbridge_integration/` with `.venv/Scripts/python.exe -m pytest` (the project's `addopts` already carries `-q`; do not add another or the summary line disappears).
- **Every state-changing POST validates CSRF via `_csrf_ok()` before doing anything, carries a role gate on the route, and writes an audit row** (REQ-A2). Hiding a link is presentation; refusing the POST is the control.
- **UI copy is German**, matching `admin_people.html`. Python source stays ASCII (`ae`/`ue` transliteration), as the existing modules do.
- **The word `bypass` must not appear in `admin_documents.py`.** `tests/test_web_admin_documents.py::TestNoSystemPrincipal` forbids it there (it is a proxy for "no system principal"). The bypass lives in `guarded.py` and `admin_approvals.py` only.
- **Guarded kinds are exactly `GUARDED_KINDS`** in `identity/approvals.py`: `matter_delete`, `acl_change`, `bulk_export`, `purge_all_documents`, `ingestion_profile_change`. Do not add one.
- **The administrator bypass is a bypass, not an exemption** (decided 2026-08-14): an admin's guarded action executes *and* writes `approval.bypassed`. `set_admin_bypass(False)` makes admins queue like everyone else. The queue shows bypasses — it never hides them (SS-338, REQ-A4 clause 5).
- **PostgreSQL tests** use the `identity_app` / `identity_repo` / `platform_db` fixtures from `tests/conftest.py`; they skip locally without a database and run in CI, where a skip is a failure.
- Do not push. Commit per task on the branch.

---

## File Structure

**Create**
- `src/web_interface/guarded.py` — `run_guarded` and `GuardOutcome`; the one way to perform a guarded action.
- `src/web_interface/admin_approvals.py` — the Freigaben tab: queue, approve, reject, bypass toggle; an `executors` registry keyed by kind.
- `src/web_interface/templates/admin_approvals.html`
- `tests/_console.py` — `csrf_from`, `sign_in`, `post_form` (lifted from `test_web_admin_people.py`, which keeps its own copies untouched).
- `tests/test_web_guarded.py`, `tests/test_identity_audit_recent.py`, `tests/test_web_admin_approvals.py`

**Modify**
- `src/identity/audit.py` — add `recent()`; the table had no read API.
- `src/web_interface/admin.py` — `_require_roles` factory; `require_admin` and `require_approver` built from it; mount the approvals routes.
- `src/web_interface/admin_documents.py` — `execute_acl_change`; the three ACL routes go through `run_guarded`.
- `src/web_interface/templates/_admin_tabs.html` — the fourth tab.
- `tests/conftest.py` — `DummyKnovasClient` gains the RBAC methods the console calls, recording each call.
- `tests/test_web_admin_documents.py` — two assertions follow the refactor (named in Task 3).
- `KnovasPlatform/docs/features/document-administration.md`, `RELEASE_NOTES.md`

All paths below are relative to `KnovasPlatform/components/docbridge_integration/` unless they start with `KnovasPlatform/` or `RELEASE_NOTES.md`.

---

### Task 1: `audit.recent()` — the record becomes readable

**Files:**
- Modify: `src/identity/audit.py` (append after `record`)
- Test: `tests/test_identity_audit_recent.py`

**Interfaces:**
- Consumes: the `audit_log` table (`identity/migrations/0001_identity.sql:167`).
- Produces: `audit.recent(conn, *, action: str | None = None, limit: int = 50) -> list[dict]` with keys `id, occurred_at, actor_user_id, actor_email, action, target_type, target_id, outcome, detail`, newest first.

- [ ] **Step 1: Write the failing test**

```python
"""audit.recent(): the append-only record gets its first read path.

The Approvals tab shows administrator bypasses from here. A bypass that is
recorded but unreadable is, to the person looking at the screen, an exemption.
"""

from __future__ import annotations

import pytest

from conftest import platform_db_reachable
from identity import audit

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)


@pytest.fixture
def actor(identity_repo):
    return identity_repo.create(
        email="a@kanzlei.ch", display_name="A", password="korrektes-pferd-batterie"
    )


def test_recent_is_newest_first_and_filters_by_action(platform_db, actor):
    audit.record(platform_db, action="approval.bypassed", actor=actor,
                 target_type="acl_change", target_id="one")
    audit.record(platform_db, action="user.created", actor=actor,
                 target_type="user", target_id="x")
    audit.record(platform_db, action="approval.bypassed", actor=actor,
                 target_type="acl_change", target_id="two")

    rows = audit.recent(platform_db, action="approval.bypassed")
    assert [r["target_id"] for r in rows] == ["two", "one"]
    assert rows[0]["actor_email"] == "a@kanzlei.ch"
    assert rows[0]["detail"] == {} or isinstance(rows[0]["detail"], dict)


def test_limit_is_honoured(platform_db, actor):
    for i in range(3):
        audit.record(platform_db, action="user.created", actor=actor,
                     target_type="user", target_id=str(i))
    assert len(audit.recent(platform_db, limit=2)) == 2
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_identity_audit_recent.py`
Expected: FAIL — `AttributeError: module 'identity.audit' has no attribute 'recent'` (or SKIP without PostgreSQL; then rely on CI for the red/green and still do Step 3).

- [ ] **Step 3: Implement**

Append to `src/identity/audit.py`:

```python
def recent(
    conn: Any, *, action: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """The newest rows, newest first. A read over an append-only table.

    Keys: id, occurred_at, actor_user_id, actor_email, action, target_type,
    target_id, outcome, detail. ``detail`` is the JSONB column as a dict.
    """
    sql = (
        "SELECT id, occurred_at, actor_user_id, actor_email_snapshot, action, "
        "target_type, target_id, outcome, detail FROM audit_log"
    )
    params: tuple[Any, ...] = ()
    if action:
        sql += " WHERE action = %s"
        params = (action,)
    sql += " ORDER BY occurred_at DESC, id DESC LIMIT %s"
    rows = conn.execute(sql, params + (int(limit),)).fetchall()
    keys = ("id", "occurred_at", "actor_user_id", "actor_email", "action",
            "target_type", "target_id", "outcome", "detail")
    return [dict(zip(keys, row)) for row in rows]
```

- [ ] **Step 4: Run the test**

Run: `.venv/Scripts/python.exe -m pytest tests/test_identity_audit_recent.py`
Expected: PASS (2) with PostgreSQL; SKIP (2) without.

- [ ] **Step 5: Commit**

```bash
git add src/identity/audit.py tests/test_identity_audit_recent.py
git commit -m "feat(identity): audit.recent(), the record's first read path"
```

---

### Task 2: `run_guarded` — one way to perform a guarded action

**Files:**
- Create: `src/web_interface/guarded.py`
- Test: `tests/test_web_guarded.py` (no PostgreSQL)

**Interfaces:**
- Consumes: `ApprovalService.requires_approval(kind, actor) -> bool`, `.request(actor, *, kind, target_ref, payload, ttl=DEFAULT_TTL) -> ApprovalRequest`, `.record_bypass(actor, *, kind, target_ref, detail=None)`; `GUARDED_KINDS`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class GuardOutcome:
      queued: bool
      request: Any = None                    # ApprovalRequest when queued
      result: Mapping[str, Any] | None = None  # execute()'s return when not

  def run_guarded(service, actor, *, kind: str, target_ref: str,
                  payload: Mapping[str, Any],
                  execute: Callable[[], Mapping[str, Any] | None]) -> GuardOutcome
  ```

- [ ] **Step 1: Write the failing tests**

```python
"""run_guarded: queue it, or do it and say that you did it alone."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from web_interface.guarded import GuardOutcome, run_guarded


class _Service:
    def __init__(self, requires: bool):
        self._requires = requires
        self.requests: list[tuple] = []
        self.bypasses: list[tuple] = []

    def requires_approval(self, kind, actor):
        return self._requires

    def request(self, actor, *, kind, target_ref, payload, ttl=None):
        self.requests.append((kind, target_ref, dict(payload)))
        return SimpleNamespace(id="r-1", kind=kind, target_ref=target_ref)

    def record_bypass(self, actor, *, kind, target_ref, detail=None):
        self.bypasses.append((kind, target_ref, dict(detail or {})))


ACTOR = SimpleNamespace(id="u-1", roles=frozenset({"admin"}))


def test_when_approval_is_required_the_action_is_queued_and_not_run():
    service = _Service(requires=True)
    ran = []
    outcome = run_guarded(service, ACTOR, kind="acl_change", target_ref="doc-1",
                          payload={"pointers": ["doc-1"]},
                          execute=lambda: ran.append(1) or {"changed": 1})
    assert outcome.queued is True
    assert outcome.request.id == "r-1"
    assert ran == [], "a queued action must not execute"
    assert service.requests == [("acl_change", "doc-1", {"pointers": ["doc-1"]})]
    assert service.bypasses == []


def test_when_no_approval_is_required_it_runs_once_and_the_bypass_is_recorded():
    service = _Service(requires=False)
    calls = []
    outcome = run_guarded(service, ACTOR, kind="acl_change", target_ref="doc-1",
                          payload={}, execute=lambda: calls.append(1) or {"changed": 1})
    assert outcome == GuardOutcome(queued=False, result={"changed": 1})
    assert calls == [1]
    assert service.bypasses == [("acl_change", "doc-1", {"result": {"changed": 1}})]
    assert service.requests == []


def test_execute_returning_none_still_records_a_bypass():
    service = _Service(requires=False)
    outcome = run_guarded(service, ACTOR, kind="acl_change", target_ref="x",
                          payload={}, execute=lambda: None)
    assert outcome.result == {}
    assert service.bypasses[0][2] == {"result": {}}


def test_an_unguarded_kind_is_refused_before_touching_the_service():
    service = _Service(requires=False)
    with pytest.raises(ValueError):
        run_guarded(service, ACTOR, kind="rename_folder", target_ref="x",
                    payload={}, execute=lambda: {"ok": True})
    assert service.bypasses == [] and service.requests == []


def test_an_exception_in_execute_records_no_bypass():
    """No action happened, so there is nothing to record as done alone."""
    service = _Service(requires=False)

    def boom():
        raise RuntimeError("backend down")

    with pytest.raises(RuntimeError):
        run_guarded(service, ACTOR, kind="acl_change", target_ref="x",
                    payload={}, execute=boom)
    assert service.bypasses == []
```

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_guarded.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'web_interface.guarded'`

- [ ] **Step 3: Implement**

Create `src/web_interface/guarded.py`:

```python
"""One way to perform a four-eyes-guarded action from the console.

Pflichtenheft B5. ``identity/approvals.py`` decides *whether* an actor must
queue an action; this module is the only place a console route asks it. Two
outcomes, and nothing in between:

- queued: a request row exists and the action did NOT run;
- executed: the action ran, and because the actor was allowed to act alone,
  an ``approval.bypassed`` row now says so.

The second half is the part that keeps the record honest. An administrator
acting alone is a decision, not an exemption, and an auditor must be able to
tell the two apart (decided 2026-08-14).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from identity.approvals import GUARDED_KINDS


@dataclass(frozen=True)
class GuardOutcome:
    queued: bool
    request: Any = None
    result: Mapping[str, Any] | None = None


def run_guarded(
    service,
    actor,
    *,
    kind: str,
    target_ref: str,
    payload: Mapping[str, Any],
    execute: Callable[[], Mapping[str, Any] | None],
) -> GuardOutcome:
    """Queue ``kind`` on ``target_ref``, or run ``execute`` and record the bypass.

    Raises:
        ValueError: ``kind`` is not a guarded kind. Ordinary work does not go
            through here; a guarded-action list that grows by accident is how
            a queue becomes something people route around.
        Whatever ``execute`` raises: nothing ran, so nothing is recorded.
    """
    if kind not in GUARDED_KINDS:
        raise ValueError(
            f"{kind!r} is not a guarded action; call it directly. Guarded: "
            f"{', '.join(sorted(GUARDED_KINDS))}."
        )
    if service.requires_approval(kind, actor):
        request = service.request(
            actor, kind=kind, target_ref=target_ref, payload=dict(payload)
        )
        return GuardOutcome(queued=True, request=request)

    result = dict(execute() or {})
    service.record_bypass(
        actor, kind=kind, target_ref=target_ref, detail={"result": result}
    )
    return GuardOutcome(queued=False, result=result)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_guarded.py`
Expected: PASS (5)

- [ ] **Step 5: Commit**

```bash
git add src/web_interface/guarded.py tests/test_web_guarded.py
git commit -m "feat(admin): run_guarded, the one way to perform a four-eyes action"
```

---

### Task 3: The ACL routes go through the guard

**Files:**
- Modify: `src/web_interface/admin.py:39-60` (`require_admin` becomes `_require_roles`; add `require_approver`)
- Modify: `src/web_interface/admin_documents.py` (`execute_acl_change`; the three POST routes)
- Modify: `tests/conftest.py:129` (`DummyKnovasClient` gains the RBAC methods)
- Modify: `tests/test_web_admin_documents.py` (two assertions, named below)
- Create: `tests/_console.py`
- Test: extend `tests/test_web_admin_documents.py`

**Interfaces:**
- Consumes: `run_guarded` (Task 2); `ApprovalService(conn, user_repo)`; `IdentityGate.connection()`, `.users()`, `.current_user()`.
- Produces:
  - `execute_acl_change(client, payload: Mapping, *, actor, conn) -> dict` in `admin_documents.py`. Payload shapes, `action` chooses:
    - `{"action": "document_acl", "pointers": [...], "access_groups": [...]}` → `{"changed": int, "failed": [pointer, ...]}`
    - `{"action": "folder_rule_save", "rule_id": str | None, "pointer_prefix": str, "access_groups": [...]}` → `{"rule_id": str}` (raises on backend failure)
    - `{"action": "folder_rule_delete", "rule_id": str}` → `{"rule_id": str}` (raises on backend failure)
  - `require_approver` on the blueprint factory: allowed roles `{"admin", "approver"}`. Task 4 uses it.
  - `tests/_console.py::csrf_from(html) -> str`, `sign_in(client, email, password="korrektes-pferd-batterie")`, `post_form(client, path, page="/admin/people", **fields)`.

- [ ] **Step 1: Lift the console test helpers**

Create `tests/_console.py` (the People tests keep their private copies; this module is for every test written from now on):

```python
"""Helpers for driving the administration console through the real login."""

from __future__ import annotations

PASSWORD = "korrektes-pferd-batterie"


def csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def sign_in(client, email: str, password: str = PASSWORD):
    """Sign in through the real /login route, so the session is built the way
    production builds it. Returns the client for chaining."""
    page = client.get("/login")
    client.post(
        "/login",
        data={"login_name": email, "password": password,
              "csrf_token": csrf_from(page.data.decode("utf-8"))},
    )
    return client


def post_form(client, path: str, page: str = "/admin/people", **fields):
    """POST ``fields`` to ``path`` with a CSRF token read from ``page``."""
    token = csrf_from(client.get(page).data.decode("utf-8"))
    fields["csrf_token"] = token
    return client.post(path, data=fields, follow_redirects=False)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_web_admin_documents.py`:

```python
from conftest import DummyKnovasClient, platform_db_reachable


class TestGuardedAclRoutes:
    """SS-392 AC 1, 2, 4 at the route: the ACL actions are four-eyes guarded."""

    def test_every_acl_route_goes_through_run_guarded(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        for fn in ("def set_document_acl(", "def save_folder_rule(",
                   "def delete_folder_rule("):
            body = src[src.index(fn):src.index(fn) + 1600]
            assert "run_guarded(" in body, f"{fn} must go through run_guarded"
            assert body.index("csrf_ok") < body.index("run_guarded("), (
                "CSRF before the guard, always"
            )

    def test_execution_lives_in_one_function_the_approve_path_can_reuse(self):
        from web_interface import admin_documents

        assert callable(getattr(admin_documents, "execute_acl_change", None))
        src = inspect.getsource(admin_documents.execute_acl_change)
        for call in ("set_document_access", "create_folder_rule",
                     "update_folder_rule", "delete_folder_rule"):
            assert call in src


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestGuardedAclRoutesLive:
    @pytest.fixture
    def admin(self, identity_repo):
        user = identity_repo.create(email="chef@kanzlei.ch", display_name="Chef",
                                    password="korrektes-pferd-batterie")
        identity_repo.grant_role(user.id, "admin")
        return identity_repo.get(user.id)

    @pytest.fixture
    def as_admin(self, identity_client, admin):
        from _console import sign_in
        return sign_in(identity_client, "chef@kanzlei.ch")

    def test_with_the_bypass_on_an_admin_acts_and_the_bypass_is_recorded(
        self, as_admin, platform_db
    ):
        from _console import post_form
        from identity import audit

        r = post_form(as_admin, "/admin/documents/acl", page="/admin/documents",
                      pointer="rc-sync/a.docx", access_group="g-lit")
        assert r.status_code == 200
        assert DummyKnovasClient.last_instance.acl_calls == [
            ("set_document_access", "rc-sync/a.docx", ["g-lit"])
        ]
        rows = audit.recent(platform_db, action="approval.bypassed")
        assert rows and rows[0]["target_type"] == "acl_change"

    def test_with_the_bypass_off_the_same_action_is_queued_and_not_run(
        self, as_admin, platform_db, identity_repo, admin
    ):
        from _console import post_form
        from identity.approvals import ApprovalService

        ApprovalService(platform_db, identity_repo).set_admin_bypass(False, by=admin)
        r = post_form(as_admin, "/admin/documents/acl", page="/admin/documents",
                      pointer="rc-sync/a.docx", access_group="g-lit")
        assert r.status_code == 200
        assert "Freigabe" in r.data.decode("utf-8")
        assert DummyKnovasClient.last_instance.acl_calls == []
        pending = ApprovalService(platform_db, identity_repo).pending()
        assert len(pending) == 1 and pending[0].kind == "acl_change"
        assert pending[0].payload["pointers"] == ["rc-sync/a.docx"]
```

Also change two existing assertions in the same file:

1. `TestRouteAuthorisation::test_acl_post_validates_csrf_before_writing`: replace
   `write_at = body.index("set_document_access")` with
   `write_at = body.index("run_guarded(")` — the write moved into `execute_acl_change`.
2. `TestAccessGroupsTab::test_folder_rule_save_is_csrf_gated`: replace
   `body.index("folder_rule(")` with `body.index("run_guarded(")` for the same reason.

- [ ] **Step 3: Run and watch them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_admin_documents.py`
Expected: the two `TestGuardedAclRoutes` tests FAIL (`run_guarded(` not in source; no `execute_acl_change`); the two changed assertions FAIL; the live class SKIPs locally.

- [ ] **Step 4: Teach the Dummy client the RBAC surface**

In `tests/conftest.py`, extend `DummyKnovasClient` (keep `health_check`, `search_documents`, `attach_principal_broker`, `last_instance`, `customer_id` as they are):

```python
    def __init__(self, config):
        self.config = config
        self.principal_broker = None
        self.acl_calls: list[tuple] = []
        DummyKnovasClient.last_instance = self

    # -- what the console's Dokumente / Zugriffsgruppen tabs call --------
    def documents(self, **kw):
        return {"documents": [], "next_after": None, "total_count": 0}

    def access_groups(self):
        return [{"group_id": "g-lit", "name": "Litigation", "parent_id": None}]

    def folder_rules(self):
        return []

    def set_document_access(self, pointer, access_groups, acting_as=None):
        self.acl_calls.append(("set_document_access", pointer, list(access_groups)))
        return {"pointer": pointer, "access_groups": list(access_groups)}

    def create_folder_rule(self, pointer_prefix, access_groups, acting_as=None):
        self.acl_calls.append(("create_folder_rule", pointer_prefix, list(access_groups)))
        return {"rule_id": "r-new", "pointer_prefix": pointer_prefix}

    def update_folder_rule(self, rule_id, access_groups, acting_as=None):
        self.acl_calls.append(("update_folder_rule", rule_id, list(access_groups)))
        return {"rule_id": rule_id}

    def delete_folder_rule(self, rule_id):
        self.acl_calls.append(("delete_folder_rule", rule_id, []))
        return True
```

- [ ] **Step 5: The role gate factory in `admin.py`**

Replace the `require_admin` definition (`admin.py:39-60`) with:

```python
    def _require_roles(allowed: frozenset[str]):
        """A route gate: signed in, and holding at least one of ``allowed``."""

        def decorator(view):
            @functools.wraps(view)
            def wrapped(*args, **kwargs):
                user = gate.current_user()
                if user is None:
                    return redirect(
                        url_for("login", next=request.full_path or "/admin/people")
                    )
                if not (allowed & set(user.roles)):
                    # 403, not 404: the person is authenticated and the
                    # console is not a secret. Hiding it would only make a
                    # misconfigured account harder to diagnose.
                    abort(403)
                return view(*args, **kwargs)

            return wrapped

        return decorator

    require_admin = _require_roles(frozenset({"admin"}))
    require_approver = _require_roles(frozenset({"admin", "approver"}))
```

Every existing `@require_admin` keeps working unchanged.

- [ ] **Step 6: `execute_acl_change` and the guarded routes in `admin_documents.py`**

Add the imports at the top:

```python
from identity.approvals import ApprovalService
from web_interface.guarded import run_guarded
```

Add, at module level above `attach_document_routes`:

```python
def execute_acl_change(client, payload, *, actor, conn) -> dict:
    """Carry out one ``acl_change`` payload against Knovas and audit it.

    Called from the route when the actor may act alone, and from the
    Approvals tab when someone else has confirmed. One function, so the two
    paths cannot drift.
    """
    action = str(payload.get("action") or "")
    groups = [str(g) for g in (payload.get("access_groups") or []) if g]

    if action == "document_acl":
        pointers = [str(p) for p in (payload.get("pointers") or []) if p]
        changed, failed = 0, []
        for pointer in pointers:
            try:
                client.set_document_access(pointer, groups)
                changed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("ACL nicht gesetzt fuer %s: %s", pointer, exc)
                failed.append(pointer)
        audit.record(
            conn, action="document.acl_changed", actor=actor,
            target_type="document",
            target_id=pointers[0] if len(pointers) == 1 else f"{len(pointers)} Dokumente",
            detail={"access_groups": groups, "changed": changed, "failed": len(failed)},
        )
        return {"changed": changed, "failed": failed}

    if action == "folder_rule_save":
        rule_id = str(payload.get("rule_id") or "")
        prefix = str(payload.get("pointer_prefix") or "")
        if rule_id:
            result = client.update_folder_rule(rule_id, groups)
        else:
            result = client.create_folder_rule(prefix, groups)
        saved_id = str((result or {}).get("rule_id") or rule_id or prefix)
        audit.record(
            conn, action="folder_rule.saved", actor=actor, target_type="folder_rule",
            target_id=saved_id, detail={"access_groups": groups, "pointer_prefix": prefix},
        )
        return {"rule_id": saved_id}

    if action == "folder_rule_delete":
        rule_id = str(payload.get("rule_id") or "")
        client.delete_folder_rule(rule_id)
        audit.record(
            conn, action="folder_rule.deleted", actor=actor, target_type="folder_rule",
            target_id=rule_id, detail={},
        )
        return {"rule_id": rule_id}

    raise ValueError(f"Unbekannte Aktion in der Zugriffsaenderung: {action!r}")
```

Inside `attach_document_routes`, add two helpers next to `_csrf_ok`:

```python
    def _approvals() -> ApprovalService:
        return ApprovalService(gate.connection(), gate.users())

    def _queued_notice(req) -> str:
        return (
            "Zur Freigabe eingereicht (Nr. "
            f"{str(req.id)[:8]}). Eine zweite Person muss bestaetigen, "
            "bevor die Aenderung wirkt."
        )
```

Rewrite the three POST routes. `set_document_acl`:

```python
    @bp.route("/documents/acl", methods=["POST"])
    @require_admin
    def set_document_acl():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _documents_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400
            )
        pointers = [p for p in request.form.getlist("pointer") if p]
        groups = [g for g in request.form.getlist("access_group") if g]
        if not pointers:
            return _documents_page(error="Kein Dokument ausgewaehlt.", status=400)

        me = gate.current_user()
        payload = {"action": "document_acl", "pointers": pointers, "access_groups": groups}
        try:
            outcome = run_guarded(
                _approvals(), me, kind="acl_change",
                target_ref=pointers[0] if len(pointers) == 1 else f"{len(pointers)} Dokumente",
                payload=payload,
                execute=lambda: execute_acl_change(
                    client_factory(), payload, actor=me, conn=gate.connection()
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsaenderung nicht gespeichert: %s", exc)
            return _documents_page(
                error="Zugriffsaenderung konnte nicht gespeichert werden.", status=400
            )
        if outcome.queued:
            return _documents_page(notice=_queued_notice(outcome.request))
        result = outcome.result or {}
        failed = result.get("failed") or []
        if failed:
            return _documents_page(
                error=f"{len(failed)} Dokument(e) konnten nicht geaendert werden.",
                notice=f"{result.get('changed', 0)} Dokument(e) geaendert.",
            )
        return _documents_page(notice=f"{result.get('changed', 0)} Dokument(e) geaendert.")
```

`save_folder_rule`:

```python
    @bp.route("/folder-rules/save", methods=["POST"])
    @require_admin
    def save_folder_rule():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _groups_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400
            )
        rule_id = str(request.form.get("rule_id", "") or "").strip()
        prefix = str(request.form.get("pointer_prefix", "") or "").strip()
        groups = [g for g in request.form.getlist("access_group") if g]
        if not rule_id and not prefix:
            return _groups_page(error="Bitte einen Ordner angeben.", status=400)

        me = gate.current_user()
        payload = {"action": "folder_rule_save", "rule_id": rule_id or None,
                   "pointer_prefix": prefix, "access_groups": groups}
        try:
            outcome = run_guarded(
                _approvals(), me, kind="acl_change", target_ref=rule_id or prefix,
                payload=payload,
                execute=lambda: execute_acl_change(
                    client_factory(), payload, actor=me, conn=gate.connection()
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregel nicht gespeichert: %s", exc)
            return _groups_page(
                error="Ordnerregel konnte nicht gespeichert werden.", status=400
            )
        if outcome.queued:
            return _groups_page(notice=_queued_notice(outcome.request))
        return _groups_page(notice="Ordnerregel gespeichert.")
```

`delete_folder_rule`:

```python
    @bp.route("/folder-rules/delete", methods=["POST"])
    @require_admin
    def delete_folder_rule():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _groups_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400
            )
        rule_id = str(request.form.get("rule_id", "") or "").strip()
        if not rule_id:
            return _groups_page(error="Keine Regel ausgewaehlt.", status=400)

        me = gate.current_user()
        payload = {"action": "folder_rule_delete", "rule_id": rule_id}
        try:
            outcome = run_guarded(
                _approvals(), me, kind="acl_change", target_ref=rule_id,
                payload=payload,
                execute=lambda: execute_acl_change(
                    client_factory(), payload, actor=me, conn=gate.connection()
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregel nicht geloescht: %s", exc)
            return _groups_page(error="Regel konnte nicht geloescht werden.", status=400)
        if outcome.queued:
            return _groups_page(notice=_queued_notice(outcome.request))
        return _groups_page(notice="Ordnerregel geloescht.")
```

Delete the old inline `audit.record(...)` blocks from the three routes — they now live in `execute_acl_change`. Then grep: `grep -n "bypass" src/web_interface/admin_documents.py` must print nothing.

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_admin_documents.py tests/test_web_guarded.py`
Expected: PASS; the live class SKIPs locally and runs in CI.

- [ ] **Step 8: Commit**

```bash
git add src/web_interface/admin.py src/web_interface/admin_documents.py tests/conftest.py tests/_console.py tests/test_web_admin_documents.py
git commit -m "feat(admin): ACL changes are four-eyes guarded; execution in one reusable function"
```

---

### Task 4: The Freigaben tab

**Files:**
- Create: `src/web_interface/admin_approvals.py`
- Create: `src/web_interface/templates/admin_approvals.html`
- Modify: `src/web_interface/admin.py` (mount the routes; pass `require_approver`, `require_admin`, an `executors` registry)
- Modify: `src/web_interface/templates/_admin_tabs.html` (fourth tab)
- Modify: `tests/test_web_admin_documents.py::TestConsoleShell::test_tab_strip_names_every_tab_that_exists` (add `admin.approvals`)
- Test: `tests/test_web_admin_approvals.py`

**Interfaces:**
- Consumes: `ApprovalService.pending() -> list[ApprovalRequest]`, `.approve(request_id, approver)`, `.reject(request_id, approver, *, reason)`, `.mark_executed(request_id, result)`, `.expire_stale()`, `.admin_bypass_enabled()`, `.set_admin_bypass(enabled, *, by)`; the exceptions `SelfApprovalError`, `NotAnApproverError`, `RequestExpiredError`, `InvalidTransitionError`, `UnknownRequestError`; `audit.recent` (Task 1); `execute_acl_change` (Task 3); `UserRepository.get(user_id)`.
- Produces: `attach_approval_routes(bp, gate, *, csrf_valid, csrf_token, page_context, require_approver, require_admin, executors: dict[str, Callable[[Mapping, Any], Mapping]])`. Routes `GET /admin/approvals`, `POST /admin/approvals/<request_id>/approve`, `POST /admin/approvals/<request_id>/reject`, `POST /admin/approvals/admin-bypass`. The Ingestion plan registers a second executor under `"ingestion_profile_change"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_admin_approvals.py`:

```python
"""The Freigaben tab: a queue for pending requests, and every bypass in view."""

from __future__ import annotations

import inspect
import pathlib

import pytest

flask = pytest.importorskip("flask")

from conftest import DummyKnovasClient, platform_db_reachable

TEMPLATES = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"
)


class TestRoutesAreGated:
    def test_every_route_carries_a_role_gate_and_posts_check_csrf_first(self):
        from web_interface import admin_approvals

        src = inspect.getsource(admin_approvals)
        assert src.count("@bp.route") == (
            src.count("@require_approver") + src.count("@require_admin")
        )
        for fn in ("def approve(", "def reject(", "def set_bypass("):
            body = src[src.index(fn):src.index(fn) + 900]
            assert body.index("csrf_ok") < body.index("_approvals()")

    def test_the_bypass_toggle_is_admin_only(self):
        from web_interface import admin_approvals

        src = inspect.getsource(admin_approvals)
        idx = src.index("def set_bypass(")
        assert "@require_admin" in src[idx - 120:idx]


class TestTemplate:
    def test_every_post_form_carries_the_csrf_token(self):
        html = (TEMPLATES / "admin_approvals.html").read_text(encoding="utf-8")
        assert html.count('method="post"') > 0
        assert html.count('name="csrf_token"') >= html.count('method="post"')

    def test_bypasses_are_shown_not_hidden(self):
        html = (TEMPLATES / "admin_approvals.html").read_text(encoding="utf-8")
        assert "bypasses" in html and "Umgehung" in html

    def test_the_strip_knows_the_tab(self):
        assert "admin.approvals" in (TEMPLATES / "_admin_tabs.html").read_text(encoding="utf-8")

    def test_it_renders_with_stub_data(self):
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        html = env.get_template("admin_approvals.html").render(
            app_title="Knovas", company_name="Kanzlei", feedback_url=None,
            console_url="/admin/people", active_nav="admin", csrf_token="t",
            error=None, notice=None, me=None, asset_version="1",
            can_toggle=True, bypass_enabled=True,
            pending=[{"id": "abc", "kind": "acl_change", "kind_label": "Zugriffsaenderung",
                      "target_ref": "rc-sync/a.docx", "requester_email": "x@kanzlei.ch",
                      "requested_at": "2026-09-02 10:00", "expires_at": "2026-09-03 10:00",
                      "summary": "1 Dokument -> g-lit", "mine": False, "executable": True}],
            bypasses=[{"occurred_at": "2026-09-02 09:00", "actor_email": "chef@kanzlei.ch",
                       "target_type": "acl_change", "target_id": "rc-sync/b.docx",
                       "detail": {"result": {"changed": 1}}}],
        )
        assert "rc-sync/a.docx" in html and "chef@kanzlei.ch" in html


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestLive:
    @pytest.fixture
    def people(self, identity_repo):
        from _console import PASSWORD

        out = {}
        for email, role in (("chef@kanzlei.ch", "admin"), ("pruefer@kanzlei.ch", "approver"),
                            ("anwalt@kanzlei.ch", "member")):
            u = identity_repo.create(email=email, display_name=email.split("@")[0],
                                     password=PASSWORD)
            identity_repo.grant_role(u.id, role)
            out[role] = identity_repo.get(u.id)
        return out

    @pytest.fixture
    def queued(self, identity_client, people, platform_db, identity_repo):
        """An admin with the bypass off queues one ACL change; returns its id."""
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        ApprovalService(platform_db, identity_repo).set_admin_bypass(False, by=people["admin"])
        sign_in(identity_client, "chef@kanzlei.ch")
        post_form(identity_client, "/admin/documents/acl", page="/admin/documents",
                  pointer="rc-sync/a.docx", access_group="g-lit")
        identity_client.get("/logout")
        (req,) = ApprovalService(platform_db, identity_repo).pending()
        return str(req.id)

    def test_who_may_open_it(self, identity_client, people):
        from _console import sign_in

        assert identity_client.get("/admin/approvals").status_code in (302, 303)
        sign_in(identity_client, "anwalt@kanzlei.ch")
        assert identity_client.get("/admin/approvals").status_code == 403
        identity_client.get("/logout")
        sign_in(identity_client, "pruefer@kanzlei.ch")
        assert identity_client.get("/admin/approvals").status_code == 200

    def test_an_approver_confirms_and_the_change_is_executed(
        self, identity_client, people, queued, platform_db, identity_repo
    ):
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        sign_in(identity_client, "pruefer@kanzlei.ch")
        r = post_form(identity_client, f"/admin/approvals/{queued}/approve",
                      page="/admin/approvals")
        assert r.status_code == 200
        assert DummyKnovasClient.last_instance.acl_calls == [
            ("set_document_access", "rc-sync/a.docx", ["g-lit"])
        ]
        row = platform_db.execute(
            "SELECT status, executed_at FROM approval_requests WHERE id = %s", (queued,)
        ).fetchone()
        assert row[0] == "executed" and row[1] is not None

    def test_the_requester_cannot_confirm_their_own(self, identity_client, people, queued):
        from _console import post_form, sign_in

        sign_in(identity_client, "chef@kanzlei.ch")
        r = post_form(identity_client, f"/admin/approvals/{queued}/approve",
                      page="/admin/approvals")
        assert r.status_code == 400
        assert "selbst" in r.data.decode("utf-8")
        assert DummyKnovasClient.last_instance.acl_calls == []

    def test_reject_needs_a_reason_and_keeps_it(
        self, identity_client, people, queued, platform_db
    ):
        from _console import post_form, sign_in

        sign_in(identity_client, "pruefer@kanzlei.ch")
        assert post_form(identity_client, f"/admin/approvals/{queued}/reject",
                         page="/admin/approvals", reason="").status_code == 400
        r = post_form(identity_client, f"/admin/approvals/{queued}/reject",
                      page="/admin/approvals", reason="Falscher Mandant.")
        assert r.status_code == 200
        row = platform_db.execute(
            "SELECT status, decision_reason FROM approval_requests WHERE id = %s", (queued,)
        ).fetchone()
        assert row == ("rejected", "Falscher Mandant.")

    def test_only_an_admin_flips_the_bypass(
        self, identity_client, people, platform_db, identity_repo
    ):
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        sign_in(identity_client, "pruefer@kanzlei.ch")
        assert post_form(identity_client, "/admin/approvals/admin-bypass",
                         page="/admin/approvals", enabled="0").status_code == 403
        identity_client.get("/logout")
        sign_in(identity_client, "chef@kanzlei.ch")
        assert post_form(identity_client, "/admin/approvals/admin-bypass",
                         page="/admin/approvals", enabled="0").status_code == 200
        assert ApprovalService(platform_db, identity_repo).admin_bypass_enabled() is False

    def test_bypasses_appear_in_the_queue_page(self, identity_client, people):
        from _console import post_form, sign_in

        sign_in(identity_client, "chef@kanzlei.ch")
        post_form(identity_client, "/admin/documents/acl", page="/admin/documents",
                  pointer="rc-sync/b.docx", access_group="g-lit")
        html = identity_client.get("/admin/approvals").data.decode("utf-8")
        assert "rc-sync/b.docx" in html and "chef@kanzlei.ch" in html
```

Also extend `TestConsoleShell::test_tab_strip_names_every_tab_that_exists` in `tests/test_web_admin_documents.py`: the endpoint tuple becomes `("admin.people", "admin.documents", "admin.access_groups", "admin.approvals")`.

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_admin_approvals.py tests/test_web_admin_documents.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'web_interface.admin_approvals'`, the template tests fail on a missing file, the tab test fails on `admin.approvals`.

- [ ] **Step 3: The routes**

Create `src/web_interface/admin_approvals.py`:

```python
"""The Freigaben tab: what is waiting for a second person, and who acted alone.

Pflichtenheft B5 (KC-B5-4). The queue lists pending requests with approve and
reject; an approver confirms someone else's request and the console then
carries the change out, once. The page also lists every administrator
bypass, because a control that quietly did not apply is worse than one a
buyer knows they lack (SS-338).

Plan: docs/superpowers/plans/2026-09-02-admin-approvals-tab.md
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from flask import render_template, request

from identity import audit
from identity.approvals import (
    ApprovalError,
    ApprovalService,
    InvalidTransitionError,
    NotAnApproverError,
    RequestExpiredError,
    SelfApprovalError,
    UnknownRequestError,
)

logger = logging.getLogger(__name__)

KIND_LABELS = {
    "acl_change": "Zugriffsaenderung",
    "matter_delete": "Akte loeschen",
    "bulk_export": "Massenexport",
    "purge_all_documents": "Alle Dokumente loeschen",
    "ingestion_profile_change": "Ingestion-Profil aendern",
}


def _summary(kind: str, payload: Mapping[str, Any]) -> str:
    groups = ", ".join(str(g) for g in (payload.get("access_groups") or [])) or "offen"
    action = payload.get("action")
    if action == "document_acl":
        n = len(payload.get("pointers") or [])
        return f"{n} Dokument(e) -> {groups}"
    if action == "folder_rule_save":
        return f"Ordner {payload.get('pointer_prefix') or payload.get('rule_id')} -> {groups}"
    if action == "folder_rule_delete":
        return f"Ordnerregel {payload.get('rule_id')} loeschen"
    return KIND_LABELS.get(kind, kind)


def attach_approval_routes(
    bp,
    gate,
    *,
    csrf_valid,
    csrf_token,
    page_context,
    require_approver,
    require_admin,
    executors: dict[str, Callable[[Mapping[str, Any], Any], Mapping[str, Any]]],
):
    """Mount the Freigaben routes on the admin blueprint.

    ``executors`` maps a guarded kind to ``fn(payload, actor) -> result``; an
    approved request of a kind with no executor stays ``approved`` and the
    page says so, rather than pretending it was carried out.
    """

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _approvals() -> ApprovalService:
        return ApprovalService(gate.connection(), gate.users())

    def _fmt(ts) -> str:
        return ts.strftime("%d.%m.%Y %H:%M") if ts else ""

    def _page(error=None, notice=None, status=200):
        me = gate.current_user()
        service = _approvals()
        service.expire_stale()
        users = gate.users()
        pending = []
        for req in service.pending():
            requester = users.get(req.requested_by)
            pending.append({
                "id": str(req.id),
                "kind": req.kind,
                "kind_label": KIND_LABELS.get(req.kind, req.kind),
                "target_ref": req.target_ref,
                "requester_email": requester.email if requester else "?",
                "requested_at": _fmt(req.requested_at),
                "expires_at": _fmt(req.expires_at),
                "summary": _summary(req.kind, req.payload),
                "mine": bool(me and str(req.requested_by) == str(me.id)),
                "executable": req.kind in executors,
            })
        bypasses = [
            {**row, "occurred_at": _fmt(row["occurred_at"])}
            for row in audit.recent(gate.connection(), action="approval.bypassed", limit=25)
        ]
        return render_template(
            "admin_approvals.html",
            active_nav="admin",
            **page_context(),
            pending=pending,
            bypasses=bypasses,
            bypass_enabled=service.admin_bypass_enabled(),
            can_toggle=bool(me and "admin" in me.roles),
            me=me,
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    @bp.route("/approvals")
    @require_approver
    def approvals():
        return _page()

    @bp.route("/approvals/<request_id>/approve", methods=["POST"])
    @require_approver
    def approve(request_id):
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        me = gate.current_user()
        service = _approvals()
        try:
            req = service.approve(request_id, me)
        except SelfApprovalError:
            return _page(error="Die eigene Anfrage kann man nicht selbst freigeben.", status=400)
        except NotAnApproverError:
            return _page(error="Dieses Konto darf nicht freigeben.", status=403)
        except (RequestExpiredError, InvalidTransitionError, UnknownRequestError) as exc:
            return _page(error=f"Anfrage nicht freigebbar: {exc}", status=400)

        execute = executors.get(req.kind)
        if execute is None:
            return _page(notice=(
                "Freigegeben. Diese Art von Aenderung kann die Konsole noch nicht "
                "selbst ausfuehren; sie bleibt als freigegeben vermerkt."
            ))
        try:
            result = dict(execute(req.payload, me) or {})
        except Exception as exc:  # noqa: BLE001 - surfaced, the request stays approved
            logger.warning("Freigegebene Anfrage %s nicht ausgefuehrt: %s", req.id, exc)
            return _page(error=(
                "Freigegeben, aber die Ausfuehrung ist fehlgeschlagen. "
                "Bitte spaeter erneut versuchen."
            ), status=200)
        service.mark_executed(req.id, result)
        return _page(notice="Freigegeben und ausgefuehrt.")

    @bp.route("/approvals/<request_id>/reject", methods=["POST"])
    @require_approver
    def reject(request_id):
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        reason = str(request.form.get("reason", "") or "").strip()
        if not reason:
            return _page(error="Bitte eine Begruendung angeben.", status=400)
        me = gate.current_user()
        try:
            _approvals().reject(request_id, me, reason=reason)
        except SelfApprovalError:
            return _page(error="Die eigene Anfrage kann man nicht selbst ablehnen.", status=400)
        except ApprovalError as exc:
            return _page(error=f"Anfrage nicht ablehnbar: {exc}", status=400)
        return _page(notice="Abgelehnt.")

    @bp.route("/approvals/admin-bypass", methods=["POST"])
    @require_admin
    def set_bypass():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _page(error="Formular ist abgelaufen. Bitte erneut versuchen.", status=400)
        enabled = str(request.form.get("enabled", "") or "") == "1"
        me = gate.current_user()
        # set_admin_bypass writes the audit row itself; a second one here would
        # make one decision look like two in the record.
        _approvals().set_admin_bypass(enabled, by=me)
        return _page(notice=(
            "Administratoren handeln jetzt ohne zweite Person; jede solche Handlung wird vermerkt."
            if enabled else
            "Administratoren muessen jetzt ebenfalls eine Freigabe einholen."
        ))

    return bp
```

- [ ] **Step 4: Mount it in `admin.py`**

Below the `attach_document_routes(...)` call, add:

```python
    from web_interface.admin_approvals import attach_approval_routes
    from web_interface.admin_documents import execute_acl_change

    attach_approval_routes(
        bp,
        gate,
        csrf_valid=csrf_valid,
        csrf_token=csrf_token,
        page_context=page_context,
        require_approver=require_approver,
        require_admin=require_admin,
        executors={
            "acl_change": lambda payload, actor: execute_acl_change(
                client_factory(), payload, actor=actor, conn=gate.connection()
            ),
        },
    )
```

Update the module docstring's tab list to "Personen, Dokumente, Zugriffsgruppen, Freigaben".

- [ ] **Step 5: The template**

Create `src/web_interface/templates/admin_approvals.html`:

```html
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Freigaben · {{ app_title }}</title>
    <link rel="icon" href="{{ url_for('static', filename='img/favicon.svg') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .admin { max-width: 1100px; margin: 0 auto; padding: 24px; }
        .admin table { width: 100%; border-collapse: collapse; margin-top: 16px; }
        .admin th, .admin td { text-align: left; padding: 9px 12px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
        .admin th { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: #64748b; }
        .admin form.inline { display: inline; }
        .admin .panel { border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px 18px; margin-top: 24px; }
        .admin .msg { padding: 10px 14px; border-radius: 4px; margin-top: 12px; }
        .admin .msg.error { background: #fef2f2; color: #b91c1c; }
        .admin .msg.ok { background: #f0fdf4; color: #15803d; }
        .admin .hint { color: #64748b; font-size: 12.5px; }
        .admin .ptr { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; word-break: break-all; }
        .admin input[type=text] { width: 100%; max-width: 360px; padding: 6px 8px; }
        .admin .warn { background: #fef9c3; color: #854d0e; padding: 10px 14px; border-radius: 4px; margin-top: 12px; }
    </style>
</head>
<body>
{% include '_sidebar.html' %}

<main class="admin">
    {% set admin_tab = 'approvals' %}
    {% include '_admin_tabs.html' %}

    <h1>Freigaben</h1>
    <p class="hint">
        Vier-Augen-Prinzip: Zugriffsänderungen, Löschungen und Massenexporte
        brauchen eine zweite Person. Was hier wartet, ist noch nicht geschehen.
    </p>

    {% if error %}<p class="msg error" role="alert">{{ error }}</p>{% endif %}
    {% if notice %}<p class="msg ok" role="status">{{ notice }}</p>{% endif %}

    <section class="panel">
        <h2>Ausstehend</h2>
        <table>
            <thead>
                <tr><th>Art</th><th>Ziel</th><th>Angefragt von</th><th>Gültig bis</th><th>Entscheidung</th></tr>
            </thead>
            <tbody>
            {% for p in pending %}
                <tr>
                    <td>{{ p.kind_label }}<br><span class="hint">{{ p.summary }}</span></td>
                    <td class="ptr">{{ p.target_ref }}</td>
                    <td>{{ p.requester_email }}<br><span class="hint">{{ p.requested_at }}</span></td>
                    <td>{{ p.expires_at }}</td>
                    <td>
                        {% if p.mine %}
                            <span class="hint">Eigene Anfrage — eine andere Person muss entscheiden.</span>
                        {% else %}
                        <form class="inline" method="post" action="{{ url_for('admin.approve', request_id=p.id) }}">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <button type="submit">Freigeben</button>
                        </form>
                        <form method="post" action="{{ url_for('admin.reject', request_id=p.id) }}">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="text" name="reason" placeholder="Begründung" required>
                            <button type="submit">Ablehnen</button>
                        </form>
                        {% endif %}
                        {% if not p.executable %}
                            <br><span class="hint">Wird nach Freigabe nicht automatisch ausgeführt.</span>
                        {% endif %}
                    </td>
                </tr>
            {% else %}
                <tr><td colspan="5" class="hint">Nichts wartet auf eine Freigabe.</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </section>

    <section class="panel">
        <h2>Umgehungen durch Administratoren</h2>
        {% if bypass_enabled %}
        <p class="warn">
            Administratoren handeln ohne zweite Person. Jede solche Handlung
            wird hier vermerkt — sie ist eine Entscheidung, keine Ausnahme.
        </p>
        {% else %}
        <p class="hint">Administratoren müssen ebenfalls eine Freigabe einholen.</p>
        {% endif %}
        {% if can_toggle %}
        <form method="post" action="{{ url_for('admin.set_bypass') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <input type="hidden" name="enabled" value="{{ '0' if bypass_enabled else '1' }}">
            <button type="submit">
                {% if bypass_enabled %}Strikt: auch Administratoren brauchen eine Freigabe{% else %}Administratoren dürfen allein handeln{% endif %}
            </button>
        </form>
        {% endif %}
        <table>
            <thead><tr><th>Wann</th><th>Wer</th><th>Was</th><th>Ziel</th></tr></thead>
            <tbody>
            {% for b in bypasses %}
                <tr>
                    <td>{{ b.occurred_at }}</td>
                    <td>{{ b.actor_email or '?' }}</td>
                    <td>{{ b.target_type }}</td>
                    <td class="ptr">{{ b.target_id }}</td>
                </tr>
            {% else %}
                <tr><td colspan="4" class="hint">Keine Umgehungen vermerkt.</td></tr>
            {% endfor %}
            </tbody>
        </table>
    </section>
</main>
</body>
</html>
```

- [ ] **Step 6: The tab**

In `_admin_tabs.html`, append after the Zugriffsgruppen anchor:

```html
    <a href="{{ url_for('admin.approvals') }}"
       class="{% if admin_tab == 'approvals' %}active{% endif %}"
       {% if admin_tab == 'approvals' %}aria-current="page"{% endif %}>Freigaben</a>
```

- [ ] **Step 7: Run everything**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_admin_approvals.py tests/test_web_admin_documents.py tests/test_web_guarded.py`
Expected: PASS; `TestLive` SKIPs locally.

Then the full suite: `.venv/Scripts/python.exe -m pytest --tb=no -p no:cacheprovider` — expected: only the eight pre-existing environmental failures (`test_preview_*`, the stale ontology sidebar test).

- [ ] **Step 8: Commit**

```bash
git add src/web_interface/admin_approvals.py src/web_interface/admin.py src/web_interface/templates/admin_approvals.html src/web_interface/templates/_admin_tabs.html tests/test_web_admin_approvals.py tests/test_web_admin_documents.py
git commit -m "feat(admin): Freigaben tab — pending queue, approve and execute, reject, and every bypass in view"
```

---

**Amendment from the Task 4 review (2026-09-02).** The reference code above listed only
`service.pending()`, so a request that was approved but whose executor raised — or whose
kind has no executor — vanished from the page with no retry path. Implemented in addition:

- `ApprovalService.approved() -> list[ApprovalRequest]` (status `approved`, newest first).
- `POST /admin/approvals/<request_id>/execute` (`@require_approver`, CSRF first): runs the
  registered executor for an approved request and calls `mark_executed`; approve and
  execute share one local `_execute(req, me)` helper so the two paths cannot drift.
- A template section *Freigegeben, noch nicht ausgeführt* listing those requests with an
  *Ausführen* button where an executor exists and a plain note where none does.
- Tests: the route-gate/CSRF source test covers `execute`; a live test drives
  queue → approve-with-failing-backend → visible under the new section → execute → executed,
  using a `fail_next` switch on `DummyKnovasClient`; a second live test shows a kind with no
  executor listed without a button.

### Task 5: Documentation

**Files:**
- Modify: `KnovasPlatform/docs/features/document-administration.md`
- Modify: `RELEASE_NOTES.md`

- [ ] **Step 1: Add the Freigaben section to the feature doc**

Insert before `## RemoteController`:

```markdown
## Freigaben (four-eyes)

Access changes made in the console — per-document groups, folder rules — are
guarded actions. Whether they run immediately depends on who acts:

- **An administrator acts alone**, by decision (2026-08-14). The change runs and
  an `approval.bypassed` row records who did what. The Freigaben tab lists these
  under *Umgehungen durch Administratoren*; they are never hidden.
- **Strict mode** (*Strikt* on the Freigaben tab) makes administrators queue like
  everyone else.
- A queued request waits for a second person holding the `approver` or `admin`
  role. The requester cannot confirm their own. On approval the console carries
  the change out and marks the request executed; on rejection the reason is kept.
- Requests expire after 24 hours.

State this to a buyer as it is: with the bypass on, four-eyes covers ordinary
users and not the most privileged account.
```

- [ ] **Step 2: Release note**

Under the existing `### Dokumentverwaltung und Ordner-Zugriffsrechte` in `RELEASE_NOTES.md`, add:

```markdown
### Freigaben

Zugriffsänderungen in der Verwaltung folgen dem Vier-Augen-Prinzip. Ein neuer
Reiter «Freigaben» zeigt, was auf eine zweite Person wartet, und vermerkt jede
Handlung, die ein Administrator allein ausgeführt hat.
```

- [ ] **Step 3: Commit**

```bash
git add KnovasPlatform/docs/features/document-administration.md RELEASE_NOTES.md
git commit -m "docs(admin): Freigaben — how the four-eyes control behaves, and its stated limit"
```

---

## Self-Review

**Spec coverage.** KC-B5-2 (guarded actions) → Task 3 covers `acl_change` for the three console actions that exist; `matter_delete`, `bulk_export`, `purge_all_documents` have no console route yet and no executor — the queue says so rather than pretending (Task 4). `ingestion_profile_change` is the Ingestion plan's. KC-B5-4 (queue, readable diff, approve/reject with reason) → Task 4; the "diff" is the `summary` line, sufficient for the two payload kinds that exist. SS-392 AC 1 → `run_guarded` tests (Task 2) and the bypass-off route test (Task 3); AC 2 → Task 3 live test; AC 3 → Task 4 bypass section; AC 4 → Task 3 bypass-off test and Task 4 toggle test; AC 5 → gate/CSRF/audit on every route; AC 6 (25 approvals tests run in CI) → depends on the PostgreSQL job already on this branch. KC-B5-3 (dual-control token to the backend) is out of scope here and named as such: the console executes approved changes itself; the backend's dual-control enforcement is KB-B5 under SS-295.

**Placeholder scan.** None.

**Type consistency.** `run_guarded(service, actor, *, kind, target_ref, payload, execute)` — same in Tasks 2, 3. `execute_acl_change(client, payload, *, actor, conn)` — same in Tasks 3, 4. `attach_approval_routes(..., executors={kind: fn(payload, actor)})` — Task 4 signature matches the mount call. `audit.recent(conn, *, action, limit)` — Tasks 1, 4. `require_approver` produced in Task 3, consumed in Task 4.
