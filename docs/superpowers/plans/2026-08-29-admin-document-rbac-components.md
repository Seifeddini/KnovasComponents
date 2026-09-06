# Admin Document Inventory and Folder RBAC — KnovasComponents Implementation Plan (Part B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the firm's administrator a console that lists every document their tenant has uploaded, lets them change access on one document or a whole folder, and makes a walled folder stay walled across re-syncs.

**Architecture:** RemoteController learns to send the per-source `access_groups` its own contract has documented since July. `knovas_client.py` gains the RBAC methods it has never had. Two tabs attach to the existing `admin.py` blueprint: **Dokumente** (a virtualised, cursor-fed inventory) and **Zugriffsgruppen** (group tree, folder rules, backfill progress). The console never holds the corpus — it holds one keyset page at a time.

**Tech Stack:** Python 3.11, Flask blueprints, Jinja2, vanilla JS (no build step — the Platform ships no bundler), pytest, `requests` over mTLS.

**Spec:** `docs/superpowers/specs/2026-08-29-admin-document-rbac-design.md`

## Global Constraints

- **Part A ships first.** This plan consumes `GET /secured/documents`, `/secured/folder_rules` and `PUT /admin/clients/<id>/rbac-enforcement` from `KnowledgeBase/docs/superpowers/plans/2026-08-29-admin-document-rbac-knowledgebase.md`. Tasks 1–3 here can land before Part A merges; Tasks 4–8 cannot.
- **Section B is a hard dependency for Tasks 4–8.** `src/web_interface/admin.py`, `identity/users.py` and the `require_admin` gate live on the unmerged `feat/section-b-buildout`. Decision D4 sequences the console after that branch merges. Do not re-create those files here.
- **UI copy is German.** Existing screens (`admin_people.html`, `_sidebar.html`) are German; match them. Client-method docstrings are German, matching the `graph_*` block in `knovas_client.py:1569-1700`.
- **Every state-changing POST validates CSRF** via the blueprint's `_form_csrf_ok()` before doing anything, and every route is authorised on the route — hiding a link is presentation, refusing the POST is the control.
- **Walls bind the administrator** (spec D1). The console never asks the backend for a system principal, and there is no "show everything" toggle.
- Tests run from `KnovasPlatform/components/docbridge_integration/` for Platform work and `RemoteController/` for RemoteController work.

---

## File Structure

**Create:**
- `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_documents.py` — inventory view composer + routes
- `KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_documents.html`
- `KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_access_groups.html`
- `KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/admin_documents.js`
- `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py`
- `KnovasPlatform/components/docbridge_integration/tests/test_knovas_client_rbac.py`
- `RemoteController/tests/test_sync_access_groups.py`

**Modify:**
- `RemoteController/src/sync/sync_executor.py:290,330-360,394-480,627` — carry `access_groups` from source to upload
- `RemoteController/src/sync/knovas_uploader.py:135-195` — put it in `init_body`
- `KnovasPlatform/components/docbridge_integration/src/knovas_client.py` — RBAC client methods
- `KnovasPlatform/components/docbridge_integration/src/web_interface/admin.py` — register the two tabs
- `KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_people.html` — tab strip
- `RemoteController/docs/configuration.md`, `KnovasPlatform/docs/` — documentation

---

## PART KC-A — RemoteController (no dependency on Part A or section B)

### Task 1: Carry `sources[].access_groups` from the source to the uploader

**Files:**
- Modify: `RemoteController/src/sync/sync_executor.py:290` (`upload_queue` type), `:330-360` (`build_walk_targets`), `:394-480` (`_plan`), `:627` (upload loop)
- Modify: `RemoteController/src/sync/knovas_uploader.py:135-195`
- Test: `RemoteController/tests/test_sync_access_groups.py`

**Interfaces:**
- Consumes: `contracts/sync_request.schema.json:18` — `sources[].access_groups`, an array of strings, already contractually defined.
- Produces: `_WalkTarget.access_groups: tuple[str, ...]`; `upload_queue` entries become 5-tuples `(abs_path, rel, mtime_iso, size_bytes, access_groups)`; `SemantixUploader.upload_file(..., access_groups=())` puts `access_groups` in the init body.

- [ ] **Step 1: Write the failing test**

Create `RemoteController/tests/test_sync_access_groups.py`:

```python
"""Per-source access groups reach /secured/init_document_transmission.

`contracts/sync_request.schema.json` has documented `sources[].access_groups`
since July with the rationale "documents from a walled folder are born walled
rather than repaired afterwards". Until this task, no code read it: every
re-sync of a walled folder re-ingested its documents unrestricted.
"""

from __future__ import annotations

import json
import pathlib

import pytest

CONTRACT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "contracts" / "sync_request.schema.json"
)


class TestContract:
    def test_schema_still_declares_per_source_access_groups(self):
        schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
        source_props = schema["properties"]["sources"]["items"]["properties"]
        assert "access_groups" in source_props
        assert source_props["access_groups"]["type"] == "array"


class TestWalkTargetCarriesGroups:
    def test_build_walk_targets_reads_access_groups(self, tmp_path):
        from sync.sync_executor import build_walk_targets

        root = tmp_path / "matters"
        root.mkdir()
        body = {
            "mode": "incremental",
            "sources": [{"path": str(root), "access_groups": ["litigation"]}],
            "filters": {},
            "ingestion": {"identifier_prefix": "rc-sync"},
        }
        targets, _ = build_walk_targets(body, None, None)
        assert len(targets) == 1
        assert targets[0].access_groups == ("litigation",)

    def test_absent_access_groups_is_empty_not_none(self, tmp_path):
        from sync.sync_executor import build_walk_targets

        root = tmp_path / "general"
        root.mkdir()
        body = {
            "mode": "incremental",
            "sources": [{"path": str(root)}],
            "filters": {},
            "ingestion": {"identifier_prefix": "rc-sync"},
        }
        targets, _ = build_walk_targets(body, None, None)
        assert targets[0].access_groups == ()


class TestUploaderSendsGroups:
    def test_init_body_carries_access_groups(self, tmp_path, monkeypatch):
        from sync.knovas_uploader import SemantixUploader

        doc = tmp_path / "note.txt"
        doc.write_text("hello", encoding="utf-8")

        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"transmission_key_id": "k1"}

        def _fake_request(self, method, endpoint, json_body=None, **kw):
            if endpoint.endswith("init_document_transmission"):
                captured.update(json_body or {})
            return _Resp()

        monkeypatch.setattr(SemantixUploader, "_request", _fake_request)
        uploader = SemantixUploader.__new__(SemantixUploader)
        uploader.upload_file(
            doc,
            "note.txt",
            {"ingestion": {"identifier_prefix": "rc-sync"}},
            access_groups=("litigation",),
        )
        assert captured.get("access_groups") == ["litigation"]

    def test_no_groups_means_no_key_so_folder_rules_can_apply(
        self, tmp_path, monkeypatch
    ):
        """An absent key lets the Secure API fall back to its folder rule.

        Sending an explicit empty list would mean "deliberately unrestricted"
        and would override the folder rule — the opposite of what an
        unconfigured source should do.
        """
        from sync.knovas_uploader import SemantixUploader

        doc = tmp_path / "note.txt"
        doc.write_text("hello", encoding="utf-8")
        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"transmission_key_id": "k1"}

        def _fake_request(self, method, endpoint, json_body=None, **kw):
            if endpoint.endswith("init_document_transmission"):
                captured.update(json_body or {})
            return _Resp()

        monkeypatch.setattr(SemantixUploader, "_request", _fake_request)
        uploader = SemantixUploader.__new__(SemantixUploader)
        uploader.upload_file(
            doc, "note.txt", {"ingestion": {"identifier_prefix": "rc-sync"}}
        )
        assert "access_groups" not in captured
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd RemoteController && python -m pytest tests/test_sync_access_groups.py -v`
Expected: FAIL — `AttributeError: '_WalkTarget' object has no attribute 'access_groups'`

- [ ] **Step 3: Add the field to `_WalkTarget` and read it**

In `sync_executor.py`, add the field to the `_WalkTarget` dataclass and populate
it in `build_walk_targets` (both the multi-source branch at `:342-353` and the
sequential-subfolders branch at `:357`):

```python
@dataclass(frozen=True)
class _WalkTarget:
    walk_root: Path
    rel_root: Path
    recursive: bool
    # Knovas RBAC groups for every document from this source. Empty means
    # "unset", which lets the Secure API apply its folder rule instead — an
    # explicit empty list would mean "deliberately unrestricted" and would
    # override that rule.
    access_groups: tuple[str, ...] = ()
```

In the multi-source branch:

```python
        for source in sources:
            root, err = resolve_root(source.get("path"))
            if err or root is None:
                continue
            targets.append(
                _WalkTarget(
                    walk_root=root,
                    rel_root=root,
                    recursive=bool(source.get("recursive", True)),
                    access_groups=tuple(source.get("access_groups") or ()),
                )
            )
```

In the sequential branch, after `source = sources[0]`, pass the same
`access_groups=tuple(source.get("access_groups") or ())` into the `_WalkTarget`
it constructs.

- [ ] **Step 4: Carry it through the upload queue**

Change the `upload_queue` annotation at `:290` and `:394`:

```python
    # (abs_path, relative_path, mtime_iso, size_bytes, access_groups)
    upload_queue: list[tuple[Path, str, str, int, tuple[str, ...]]]
```

At the append site (`:457`), add the walk target's groups:

```python
            if max_upload_files <= 0 or len(upload_queue) < max_upload_files:
                upload_queue.append(
                    (abs_path, rel, mtime_iso, size_bytes, target.access_groups)
                )
```

and at the consuming loop (`:627`):

```python
        for abs_path, rel, mtime_iso, size_bytes, access_groups in plan.upload_queue:
```

passing `access_groups=access_groups` into the `uploader.upload_file(...)` call
inside that loop.

- [ ] **Step 5: Send it from the uploader**

In `knovas_uploader.py`, change the signature at `:135` and the `init_body`
construction at `:177-181`:

```python
    def upload_file(
        self,
        file_path: Path,
        relative_path: str,
        sync_body: dict[str, Any],
        access_groups: tuple[str, ...] = (),
    ) -> UploadResult:
```

```python
        init_body: dict[str, Any] = {
            "identifier": identifier,
            "part_count": part_count,
            "title": title,
            "path": relative_path,
        }
        if access_groups:
            # Only when set. An absent key lets the Secure API apply the
            # folder rule for this pointer; an explicit [] would mean
            # "deliberately unrestricted" and would override it.
            init_body["access_groups"] = list(access_groups)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd RemoteController && python -m pytest tests/test_sync_access_groups.py tests/ -q`
Expected: PASS — the whole RemoteController suite, since the `upload_queue`
tuple width changed and other tests construct it.

- [ ] **Step 7: Document the sequential-mode caveat**

In `RemoteController/docs/configuration.md`, in the sources section:

```markdown
### Per-source access groups

Each `sources[]` entry may carry `access_groups`. Every document ingested from
that folder is born with those groups, so a walled folder stays walled across
re-syncs rather than being repaired afterwards.

Omit the key for unrestricted folders. An *absent* key lets the Secure API
apply whatever folder rule covers the pointer; an explicit empty array means
"deliberately unrestricted" and overrides that rule.

**Caveat:** with `sequential_subfolders` enabled, RemoteController processes
one source per cycle (`sync_executor.py` logs `sequential_subfolders requires
exactly one source; using first only`). In that mode the first source's
`access_groups` applies. Use one profile per walled folder if you need
different groups under sequential mode.
```

- [ ] **Step 8: Commit**

```bash
git add RemoteController/src/sync/sync_executor.py \
        RemoteController/src/sync/knovas_uploader.py \
        RemoteController/tests/test_sync_access_groups.py \
        RemoteController/docs/configuration.md
git commit -m "feat(rc): implement sources[].access_groups end to end"
```

---

## PART KC-B — The client (needs Part A deployed to run against, not to write)

### Task 2: `knovas_client.py` — group tree and per-document ACL

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_client.py` (new block after the `graph_*` methods)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_knovas_client_rbac.py`

**Interfaces:**
- Consumes: `_make_request` (`knovas_client.py:1284`).
- Produces on `KnovasClient`:
  - `_rbac_request(method, path, data=None, params=None) -> Optional[Dict[str, Any]]`
  - `access_groups() -> List[Dict[str, Any]]`
  - `create_access_group(name, parent=None) -> Optional[Dict[str, Any]]`
  - `rename_access_group(identifier, name) -> Optional[Dict[str, Any]]`
  - `delete_access_group(identifier) -> bool`
  - `document_access(pointer) -> Optional[Dict[str, Any]]`
  - `set_document_access(pointer, access_groups, acting_as=None) -> Optional[Dict[str, Any]]`

- [ ] **Step 1: Write the failing test**

Create `KnovasPlatform/components/docbridge_integration/tests/test_knovas_client_rbac.py`:

```python
"""RBAC client methods.

Before this task `knovas_client.py` made zero calls to any of the four shipped
RBAC endpoints — the engine existed in KnowledgeBase and was unreachable from
the product.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _client(monkeypatch, payload, status_code=200):
    from knovas_client import KnovasClient

    client = KnovasClient.__new__(KnovasClient)
    calls = []

    def _fake(self, method, endpoint, data=None, params=None):
        calls.append({"method": method, "endpoint": endpoint,
                      "data": data, "params": params})
        return _Resp(payload, status_code)

    monkeypatch.setattr(KnovasClient, "_make_request", _fake)
    return client, calls


class TestAccessGroups:
    def test_access_groups_hits_the_collection_endpoint(self, monkeypatch):
        client, calls = _client(monkeypatch, {"groups": [{"group_id": "g1"}],
                                              "epoch": 3})
        groups = client.access_groups()
        assert calls[0]["endpoint"] == "/secured/access_groups"
        assert calls[0]["method"] == "GET"
        assert groups == [{"group_id": "g1"}]

    def test_create_access_group_posts_name_and_parent(self, monkeypatch):
        client, calls = _client(monkeypatch, {"group_id": "g2"}, 201)
        client.create_access_group("Litigation", parent="g1")
        assert calls[0]["method"] == "POST"
        assert calls[0]["data"] == {"name": "Litigation", "parent": "g1"}


class TestDocumentAccess:
    def test_read_passes_the_pointer_as_a_query_param(self, monkeypatch):
        client, calls = _client(
            monkeypatch,
            {"pointer": "rc-sync/a.docx", "access_groups": ["g1"], "acl_epoch": 2},
        )
        got = client.document_access("rc-sync/a.docx")
        assert calls[0]["params"] == {"pointer": "rc-sync/a.docx"}
        assert got["access_groups"] == ["g1"]

    def test_write_sends_the_complete_desired_set(self, monkeypatch):
        client, calls = _client(
            monkeypatch, {"pointer": "p", "access_groups": ["g1"], "acl_epoch": 3}
        )
        client.set_document_access("p", ["g1"])
        assert calls[0]["method"] == "PUT"
        assert calls[0]["data"]["access_groups"] == ["g1"]

    def test_acting_as_is_sent_separately_from_the_assignment(self, monkeypatch):
        """The endpoint's two group fields mean different things.

        `access_groups` is the assignment; `acting_as` is the caller's
        clearance. Conflating them would let a caller widen their own
        domination check.
        """
        client, calls = _client(monkeypatch, {"pointer": "p",
                                              "access_groups": [], "acl_epoch": 1})
        client.set_document_access("p", ["g-hr"], acting_as=["g-all"])
        assert calls[0]["data"]["access_groups"] == ["g-hr"]
        assert calls[0]["data"]["acting_as"] == ["g-all"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_knovas_client_rbac.py -v`
Expected: FAIL — `AttributeError: 'KnovasClient' object has no attribute 'access_groups'`

- [ ] **Step 3: Write the methods**

Append to `KnovasClient` in `knovas_client.py`, after the `graph_*` block:

```python
    # -- RBAC: Zugriffsgruppen, Dokument-ACL, Ordnerregeln ------------------

    def _rbac_request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """RBAC-Aufruf. Gibt den geparsten Body zurueck.

        404 heisst 'unbekannt oder nicht deins' und liefert None - ein
        normaler Zustand, kein Fehler. Das entspricht der 404-statt-403-Regel
        der Secure API: keine Route verraet, dass eine Id existiert.
        """
        try:
            response = self._make_request(method=method, endpoint=path,
                                          data=data, params=params)
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            if response is None or response.status_code != 404:
                raise
            logger.info("RBAC 404 (unbekannt oder fremd): %s %s", method, path)
            return None
        if response.status_code == 204:
            return {}
        try:
            return response.json() or {}
        except ValueError:
            logger.warning("RBAC-Antwort ohne JSON-Body: %s %s", method, path)
            return {}

    def access_groups(self) -> List[Dict[str, Any]]:
        """GET /secured/access_groups - der Gruppenbaum des Mandanten."""
        payload = self._rbac_request('GET', '/secured/access_groups') or {}
        return list(payload.get('groups') or [])

    def create_access_group(
        self, name: str, parent: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """POST /secured/access_groups - neue Gruppe, optional unter parent."""
        return self._rbac_request(
            'POST', '/secured/access_groups', data={'name': name, 'parent': parent}
        )

    def rename_access_group(
        self, identifier: str, name: str
    ) -> Optional[Dict[str, Any]]:
        """PATCH /secured/access_groups/<id> - Anzeigename aendern.

        Die group_id bleibt stabil, deshalb kostet ein Rename nichts: in
        acl_reader_ids stehen Ids, keine Namen.
        """
        return self._rbac_request(
            'PATCH', f'/secured/access_groups/{quote(str(identifier), safe="")}',
            data={'name': name})

    def delete_access_group(self, identifier: str) -> bool:
        """DELETE /secured/access_groups/<id>. True, wenn geloescht."""
        result = self._rbac_request(
            'DELETE', f'/secured/access_groups/{quote(str(identifier), safe="")}')
        return result is not None

    def document_access(self, pointer: str) -> Optional[Dict[str, Any]]:
        """GET /secured/document_access - die ACL genau eines Dokuments."""
        return self._rbac_request(
            'GET', '/secured/document_access', params={'pointer': str(pointer)})

    def set_document_access(
        self,
        pointer: str,
        access_groups: List[str],
        acting_as: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """PUT /secured/document_access - ersetzt die ACL vollstaendig.

        Ersetzen, nicht ergaenzen: der Client schickt die vollstaendige
        Zielmenge, damit 'eine Gruppe entfernen' kein zweites Verb braucht.

        `access_groups` ist die Zuweisung, `acting_as` die eigene Freigabe
        des Aufrufers - zwei verschiedene Dinge. Der Server prueft damit,
        dass niemand in eine Gruppe einordnet, die er nicht dominiert.
        """
        body: Dict[str, Any] = {
            'pointer': str(pointer),
            'access_groups': list(access_groups),
        }
        if acting_as is not None:
            body['acting_as'] = list(acting_as)
        return self._rbac_request('PUT', '/secured/document_access', data=body)
```

`quote` is already imported at the top of `knovas_client.py` for the `graph_*`
methods; no new import is needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_knovas_client_rbac.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/knovas_client.py \
        KnovasPlatform/components/docbridge_integration/tests/test_knovas_client_rbac.py
git commit -m "feat(client): access-group and document-ACL methods"
```

---

### Task 3: `knovas_client.py` — inventory, folder rules, backfill

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/knovas_client.py` (extend Task 2's block)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_knovas_client_rbac.py` (extend)

**Interfaces:**
- Consumes: `_rbac_request` (Task 2); Part A's `GET /secured/documents` and `/secured/folder_rules`.
- Produces:
  - `documents(after=None, limit=100, prefix=None, group=None, unrestricted=False, status=None) -> Dict[str, Any]` — keys `documents`, `next_after`, `total_count`
  - `iter_documents(**kw) -> Iterator[Dict[str, Any]]` — follows the cursor
  - `folder_rules() -> List[Dict[str, Any]]`
  - `create_folder_rule(pointer_prefix, access_groups, acting_as=None)`
  - `update_folder_rule(rule_id, access_groups, acting_as=None)`
  - `delete_folder_rule(rule_id) -> bool`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knovas_client_rbac.py`:

```python
class TestDocumentInventory:
    def test_documents_forwards_the_cursor_and_filters(self, monkeypatch):
        client, calls = _client(
            monkeypatch,
            {"documents": [], "next_after": None, "total_count": 0},
        )
        client.documents(after="rc-sync/m/a.docx", limit=250,
                         prefix="rc-sync/m/", unrestricted=True)
        params = calls[0]["params"]
        assert calls[0]["endpoint"] == "/secured/documents"
        assert params["after"] == "rc-sync/m/a.docx"
        assert params["limit"] == 250
        assert params["prefix"] == "rc-sync/m/"
        assert params["unrestricted"] == "true"

    def test_omitted_filters_are_not_sent_as_none(self, monkeypatch):
        client, calls = _client(
            monkeypatch, {"documents": [], "next_after": None, "total_count": 0}
        )
        client.documents()
        assert "prefix" not in calls[0]["params"]
        assert "group" not in calls[0]["params"]

    def test_iter_documents_follows_the_cursor_to_the_end(self, monkeypatch):
        from knovas_client import KnovasClient

        client = KnovasClient.__new__(KnovasClient)
        pages = [
            {"documents": [{"pointer": "a"}, {"pointer": "b"}],
             "next_after": "b", "total_count": 3},
            {"documents": [{"pointer": "c"}], "next_after": None, "total_count": 3},
        ]
        seen_after = []

        def _fake(self, method, endpoint, data=None, params=None):
            seen_after.append((params or {}).get("after"))
            return _Resp(pages.pop(0))

        monkeypatch.setattr(KnovasClient, "_make_request", _fake)
        got = [d["pointer"] for d in client.iter_documents()]
        assert got == ["a", "b", "c"]
        assert seen_after == [None, "b"]

    def test_iter_documents_stops_on_a_repeated_cursor(self, monkeypatch):
        """A server that returns the same cursor must not spin us forever."""
        from knovas_client import KnovasClient

        client = KnovasClient.__new__(KnovasClient)

        def _fake(self, method, endpoint, data=None, params=None):
            return _Resp({"documents": [{"pointer": "a"}],
                          "next_after": "a", "total_count": 1})

        monkeypatch.setattr(KnovasClient, "_make_request", _fake)
        got = list(client.iter_documents(max_pages=5))
        assert len(got) <= 5


class TestFolderRules:
    def test_create_sends_prefix_and_groups(self, monkeypatch):
        client, calls = _client(monkeypatch, {"rule_id": "r1"}, 201)
        client.create_folder_rule("rc-sync/matters/A/", ["g-lit"])
        assert calls[0]["endpoint"] == "/secured/folder_rules"
        assert calls[0]["data"]["pointer_prefix"] == "rc-sync/matters/A/"
        assert calls[0]["data"]["access_groups"] == ["g-lit"]

    def test_update_targets_the_rule_id(self, monkeypatch):
        client, calls = _client(monkeypatch, {"rule_id": "r1", "version": 2})
        client.update_folder_rule("r1", [])
        assert calls[0]["method"] == "PATCH"
        assert calls[0]["endpoint"] == "/secured/folder_rules/r1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_knovas_client_rbac.py::TestDocumentInventory -v`
Expected: FAIL — `AttributeError: 'KnovasClient' object has no attribute 'documents'`

- [ ] **Step 3: Write the methods**

Append to the RBAC block in `knovas_client.py`:

```python
    def documents(
        self,
        after: Optional[str] = None,
        limit: int = 100,
        prefix: Optional[str] = None,
        group: Optional[str] = None,
        unrestricted: bool = False,
        conflicts: bool = False,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /secured/documents - eine Keyset-Seite des Dokumentbestands.

        `after` ist der `next_after` der vorigen Antwort. Die Seite wird
        NICHT ueber offset geblaettert: bei grossen Mandanten waere das
        oberhalb von QUERY_MAXIMUM_RESULTS schlicht ein Fehler.

        Gefiltert wird mit der eigenen Freigabe des Aufrufers. Wer aus einem
        Mandat ausgeschlossen ist, sieht es auch hier nicht.
        """
        params: Dict[str, Any] = {'limit': int(limit)}
        if after:
            params['after'] = str(after)
        if prefix:
            params['prefix'] = str(prefix)
        if group:
            params['group'] = str(group)
        if status:
            params['status'] = str(status)
        if unrestricted:
            params['unrestricted'] = 'true'
        if conflicts:
            params['conflicts'] = 'true'
        return self._rbac_request('GET', '/secured/documents', params=params) or {}

    def iter_documents(
        self, max_pages: int = 10_000, **kwargs: Any
    ) -> Iterator[Dict[str, Any]]:
        """Laeuft den Cursor bis zum Ende ab und liefert einzelne Dokumente.

        `max_pages` ist eine Schleifenbremse, keine Fachgrenze: ein Server,
        der denselben Cursor wiederholt, darf uns nicht endlos drehen.
        """
        after = kwargs.pop('after', None)
        seen: set = set()
        for _ in range(max(1, int(max_pages))):
            page = self.documents(after=after, **kwargs)
            for row in page.get('documents') or []:
                yield row
            after = page.get('next_after')
            if not after or after in seen:
                return
            seen.add(after)

    def folder_rules(self) -> List[Dict[str, Any]]:
        """GET /secured/folder_rules - alle Ordnerregeln des Mandanten."""
        payload = self._rbac_request('GET', '/secured/folder_rules') or {}
        return list(payload.get('rules') or [])

    def create_folder_rule(
        self,
        pointer_prefix: str,
        access_groups: List[str],
        acting_as: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """POST /secured/folder_rules - Ordner einer Gruppe zuordnen.

        Dokumente, die spaeter unter diesen Pfad eingelesen werden, erben die
        Regel beim Ingest. Genau das verhindert, dass ein erneuter Abgleich
        eine geschlossene Wand wieder oeffnet.
        """
        body: Dict[str, Any] = {
            'pointer_prefix': str(pointer_prefix),
            'access_groups': list(access_groups),
        }
        if acting_as is not None:
            body['acting_as'] = list(acting_as)
        return self._rbac_request('POST', '/secured/folder_rules', data=body)

    def update_folder_rule(
        self,
        rule_id: str,
        access_groups: List[str],
        acting_as: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """PATCH /secured/folder_rules/<id> - Gruppen einer Ordnerregel aendern.

        Das ist ein einziger Datenbankschreibvorgang, egal wie viele
        Dokumente unter dem Ordner liegen: auf den Chunks steht die Regel-Id,
        nicht die aufgeloeste Gruppenmenge.
        """
        body: Dict[str, Any] = {'access_groups': list(access_groups)}
        if acting_as is not None:
            body['acting_as'] = list(acting_as)
        return self._rbac_request(
            'PATCH', f'/secured/folder_rules/{quote(str(rule_id), safe="")}',
            data=body)

    def delete_folder_rule(self, rule_id: str) -> bool:
        """DELETE /secured/folder_rules/<id>. True, wenn geloescht."""
        result = self._rbac_request(
            'DELETE', f'/secured/folder_rules/{quote(str(rule_id), safe="")}')
        return result is not None
```

Add `Iterator` to the `typing` import at the top of `knovas_client.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_knovas_client_rbac.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/knovas_client.py \
        KnovasPlatform/components/docbridge_integration/tests/test_knovas_client_rbac.py
git commit -m "feat(client): document inventory cursor and folder-rule methods"
```

---

## PART KC-C — The console (requires `feat/section-b-buildout` merged)

> **Gate:** do not start Task 4 until `src/web_interface/admin.py`, `src/identity/users.py` and `src/identity/audit.py` exist on the working branch. Decision D4.

### Task 4: `admin_documents.py` — the inventory view composer and routes

**Files:**
- Create: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_documents.py`
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin.py` (register)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py`

**Interfaces:**
- Consumes: `gate` (from `admin.create_admin_blueprint`), `csrf_valid`, `csrf_token`, `page_context`; `KnovasClient.documents` / `iter_documents` / `set_document_access` (Tasks 2–3); `identity.audit.record`.
- Produces:
  - `attach_document_routes(bp, gate, *, csrf_valid, csrf_token, page_context, client_factory, require_admin)` — mounts `/admin/documents`, `/admin/documents/acl` (POST), `/admin/documents/page` (GET, JSON)
  - `DocumentsView(client)` with `.page(after=None, **filters) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_admin_documents.py`:

```python
"""The Dokumente tab: authorised on the route, cursor-fed, wall-respecting."""

from __future__ import annotations

import inspect

import pytest

flask = pytest.importorskip("flask")


class _FakeClient:
    def __init__(self, pages=None):
        self._pages = pages or [
            {"documents": [{"pointer": "rc-sync/a.docx", "title": "A",
                            "access_groups": [], "status": "active"}],
             "next_after": None, "total_count": 1}
        ]
        self.acl_writes = []

    def documents(self, **kw):
        return self._pages[0]

    def set_document_access(self, pointer, access_groups, acting_as=None):
        self.acl_writes.append((pointer, list(access_groups)))
        return {"pointer": pointer, "access_groups": list(access_groups)}

    def access_groups(self):
        return [{"group_id": "g-lit", "name": "Litigation", "children": []}]


class TestRouteAuthorisation:
    def test_every_route_is_admin_gated_not_merely_hidden(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        # Count decorated views: each @bp.route must be followed by
        # @require_admin. Hiding a link is presentation; refusing the request
        # is the control.
        assert src.count("@bp.route") == src.count("@require_admin"), (
            "every route must carry @require_admin"
        )

    def test_acl_post_validates_csrf_before_writing(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        idx = src.index("def set_document_acl(")
        body = src[idx:idx + 1200]
        csrf_at = body.index("csrf_ok")
        write_at = body.index("set_document_access")
        assert csrf_at < write_at, "CSRF must be checked before the write"


class TestNoSystemPrincipal:
    def test_view_never_asks_for_an_unfiltered_listing(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        for forbidden in ("system_principal", "show_all", "bypass"):
            assert forbidden not in src, (
                f"spec D1: walls bind the administrator too; found {forbidden!r}"
            )


class TestDocumentsView:
    def test_page_returns_rows_cursor_and_count(self):
        from web_interface.admin_documents import DocumentsView

        view = DocumentsView(_FakeClient())
        page = view.page()
        assert page["total_count"] == 1
        assert page["next_after"] is None
        assert page["documents"][0]["pointer"] == "rc-sync/a.docx"

    def test_page_forwards_filters_verbatim(self):
        from web_interface.admin_documents import DocumentsView

        class _Recording(_FakeClient):
            def __init__(self):
                super().__init__()
                self.kw = None

            def documents(self, **kw):
                self.kw = kw
                return self._pages[0]

        client = _Recording()
        DocumentsView(client).page(after="x", prefix="rc-sync/m/",
                                   unrestricted=True)
        assert client.kw["after"] == "x"
        assert client.kw["prefix"] == "rc-sync/m/"
        assert client.kw["unrestricted"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_web_admin_documents.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web_interface.admin_documents'`

- [ ] **Step 3: Write the view composer and routes**

Create `src/web_interface/admin_documents.py`:

```python
"""The firm's document inventory and its access controls.

Two tabs of the administration console: Dokumente (every document the tenant
has uploaded, as far as the signed-in administrator may see it) and the
folder-rule half of Zugriffsgruppen.

The inventory is cursor-fed. The screen holds one page, never the corpus:
`/admin/documents/page` returns JSON for the next keyset page and the browser
appends it. That is what makes the tab usable on a ten-million-document
tenant.

Walls bind the administrator too (design §2 D1). There is no "show
everything" switch here, and no route asks the backend for one.

Plan: docs/superpowers/plans/2026-08-29-admin-document-rbac-components.md
"""
from __future__ import annotations

import logging

from flask import jsonify, redirect, render_template, request, url_for

from identity import audit

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


class DocumentsView:
    """Composes one page of the inventory from the Knovas client.

    Kept free of Flask so it can be tested without an app context.
    """

    def __init__(self, client) -> None:
        self._client = client

    def page(
        self,
        after: str | None = None,
        *,
        prefix: str | None = None,
        group: str | None = None,
        unrestricted: bool = False,
        conflicts: bool = False,
        status: str | None = None,
        limit: int = PAGE_SIZE,
    ) -> dict:
        payload = self._client.documents(
            after=after,
            limit=limit,
            prefix=prefix,
            group=group,
            unrestricted=unrestricted,
            conflicts=conflicts,
            status=status,
        )
        return {
            "documents": list(payload.get("documents") or []),
            "next_after": payload.get("next_after"),
            "total_count": int(payload.get("total_count") or 0),
        }


def _filters_from_request() -> dict:
    return {
        "prefix": (request.args.get("prefix") or "").strip() or None,
        "group": (request.args.get("group") or "").strip() or None,
        "unrestricted": request.args.get("unrestricted") == "1",
        "conflicts": request.args.get("conflicts") == "1",
        "status": (request.args.get("status") or "").strip() or None,
    }


def attach_document_routes(
    bp,
    gate,
    *,
    csrf_valid,
    csrf_token,
    page_context,
    client_factory,
    require_admin,
):
    """Mount the Dokumente routes onto the existing admin blueprint.

    Takes ``require_admin`` from the blueprint factory rather than redefining
    it, so there is exactly one definition of "who may reach the console".
    """

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _documents_page(error=None, notice=None, status=200):
        view = DocumentsView(client_factory())
        filters = _filters_from_request()
        try:
            first = view.page(**filters)
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            logger.warning("Dokumentliste nicht abrufbar: %s", exc)
            first = {"documents": [], "next_after": None, "total_count": 0}
            error = error or (
                "Die Dokumentliste ist derzeit nicht abrufbar. "
                "Bitte spaeter erneut versuchen."
            )
        groups = []
        try:
            groups = client_factory().access_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsgruppen nicht abrufbar: %s", exc)
        return render_template(
            "admin_documents.html",
            active_nav="admin",
            **page_context(),
            documents=first["documents"],
            next_after=first["next_after"],
            total_count=first["total_count"],
            filters=filters,
            groups=groups,
            me=gate.current_user(),
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    @bp.route("/documents")
    @require_admin
    def documents():
        return _documents_page()

    @bp.route("/documents/page")
    @require_admin
    def documents_page():
        """One further keyset page, as JSON, for the infinite list."""
        view = DocumentsView(client_factory())
        filters = _filters_from_request()
        after = (request.args.get("after") or "").strip() or None
        try:
            return jsonify(view.page(after=after, **filters))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Seite nicht abrufbar: %s", exc)
            return jsonify({"documents": [], "next_after": None,
                            "total_count": 0, "error": "unavailable"}), 503

    @bp.route("/documents/acl", methods=["POST"])
    @require_admin
    def set_document_acl():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _documents_page(
                error="Formular ist abgelaufen. Bitte erneut versuchen.",
                status=400,
            )
        pointers = [p for p in request.form.getlist("pointer") if p]
        groups = [g for g in request.form.getlist("access_group") if g]
        if not pointers:
            return _documents_page(error="Kein Dokument ausgewaehlt.", status=400)

        me = gate.current_user()
        client = client_factory()
        changed = 0
        failed: list[str] = []
        for pointer in pointers:
            try:
                client.set_document_access(pointer, groups)
                changed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("ACL nicht gesetzt fuer %s: %s", pointer, exc)
                failed.append(pointer)

        audit.record(
            gate.connection(), action="document.acl_changed", actor=me,
            target_type="document",
            target_id=pointers[0] if len(pointers) == 1 else f"{len(pointers)} Dokumente",
            detail={"access_groups": groups, "changed": changed,
                    "failed": len(failed)},
        )
        if failed:
            return _documents_page(
                error=f"{len(failed)} Dokument(e) konnten nicht geaendert werden.",
                notice=f"{changed} Dokument(e) geaendert.",
                status=200,
            )
        return _documents_page(notice=f"{changed} Dokument(e) geaendert.")

    return bp
```

- [ ] **Step 4: Register it on the blueprint**

In `admin.py`, at the end of `create_admin_blueprint` before `return bp`:

```python
    from web_interface.admin_documents import attach_document_routes

    attach_document_routes(
        bp,
        gate,
        csrf_valid=csrf_valid,
        csrf_token=csrf_token,
        page_context=page_context,
        client_factory=client_factory,
        require_admin=require_admin,
    )

    return bp
```

and add `client_factory` to the factory signature:

```python
def create_admin_blueprint(
    gate, *, csrf_valid, csrf_token, page_context, client_factory
):
```

Update the docstring's tab list, which currently reads "One tab so far —
People":

```python
"""The firm's administration console.

Two tabs — People and Dokumente. The others (Walls, Approvals, Ingestion)
attach to the same blueprint and reuse ``require_admin``.
...
"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_web_admin_documents.py tests/test_web_admin_people.py -v`
Expected: PASS — the People tab must not regress.

- [ ] **Step 6: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/admin_documents.py \
        KnovasPlatform/components/docbridge_integration/src/web_interface/admin.py \
        KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py
git commit -m "feat(admin): Dokumente routes and inventory view composer"
```

---

### Task 5: `admin_documents.html` and the cursor-fed list

**Files:**
- Create: `KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_documents.html`
- Create: `KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/admin_documents.js`
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_people.html` (tab strip)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py` (extend)

**Interfaces:**
- Consumes: Task 4's template variables — `documents`, `next_after`, `total_count`, `filters`, `groups`, `csrf_token`.
- Produces: a page whose "Mehr laden" button calls `/admin/documents/page?after=…` and appends rows.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_admin_documents.py`:

```python
import pathlib

TEMPLATES = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "web_interface" / "templates"
)


class TestTemplate:
    def test_template_exists(self):
        assert (TEMPLATES / "admin_documents.html").is_file()

    def test_every_mutating_form_carries_the_csrf_token(self):
        html = (TEMPLATES / "admin_documents.html").read_text(encoding="utf-8")
        post_forms = html.count('method="post"')
        tokens = html.count('name="csrf_token"')
        assert post_forms > 0
        assert tokens >= post_forms, (
            "every POST form needs a hidden csrf_token"
        )

    def test_list_is_cursor_fed_not_offset_paged(self):
        html = (TEMPLATES / "admin_documents.html").read_text(encoding="utf-8")
        assert "next_after" in html
        assert "page=" not in html, (
            "the inventory pages by cursor; a page number implies an offset"
        )

    def test_count_comes_from_the_backend_aggregate(self):
        html = (TEMPLATES / "admin_documents.html").read_text(encoding="utf-8")
        assert "total_count" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_web_admin_documents.py::TestTemplate -v`
Expected: FAIL — `assert False` on `admin_documents.html` existing.

- [ ] **Step 3: Write the template**

Create `src/web_interface/templates/admin_documents.html`, matching the styling
already in `admin_people.html`:

```html
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dokumente · {{ app_title }}</title>
    <link rel="icon" href="{{ url_for('static', filename='img/favicon.svg') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        .admin { max-width: 1280px; margin: 0 auto; padding: 24px; }
        .admin table { width: 100%; border-collapse: collapse; margin-top: 16px; }
        .admin th, .admin td { text-align: left; padding: 9px 12px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
        .admin th { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: #64748b; }
        .admin .tabs { display: flex; gap: 18px; border-bottom: 1px solid #e2e8f0; margin-bottom: 18px; }
        .admin .tabs a { padding: 8px 2px; text-decoration: none; color: #475569; border-bottom: 2px solid transparent; }
        .admin .tabs a.active { color: #0f172a; border-bottom-color: #0f172a; font-weight: 600; }
        .admin .filters { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; margin-top: 12px; }
        .admin .filters label { font-size: 13px; }
        .admin .msg { padding: 10px 14px; border-radius: 4px; margin-top: 12px; }
        .admin .msg.error { background: #fef2f2; color: #b91c1c; }
        .admin .msg.ok { background: #f0fdf4; color: #15803d; }
        .admin .hint { color: #64748b; font-size: 12.5px; }
        .admin .ptr { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; word-break: break-all; }
        .admin .badge { display: inline-block; padding: 1px 7px; border-radius: 10px; background: #e2e8f0; font-size: 12px; margin-right: 4px; }
        .admin .badge.open { background: #fef9c3; color: #854d0e; }
        .admin .bulk { border: 1px solid #e2e8f0; border-radius: 6px; padding: 14px 16px; margin-top: 18px; }
        .admin .more { margin-top: 18px; }
    </style>
</head>
<body>
{% include '_sidebar.html' %}

<main class="admin">
    <nav class="tabs" aria-label="Verwaltung">
        <a href="{{ url_for('admin.people') }}">Personen</a>
        <a href="{{ url_for('admin.documents') }}" class="active" aria-current="page">Dokumente</a>
        <a href="{{ url_for('admin.access_groups') }}">Zugriffsgruppen</a>
    </nav>

    <h1>Dokumente</h1>
    <p class="hint">
        Alle Dokumente dieses Mandanten, soweit Sie sie sehen duerfen.
        Abschirmungen gelten auch fuer die Verwaltung: was fuer Sie gesperrt
        ist, erscheint hier nicht.
        <strong>{{ total_count }}</strong> Dokument(e) entsprechen dem Filter.
    </p>

    {% if error %}<p class="msg error" role="alert">{{ error }}</p>{% endif %}
    {% if notice %}<p class="msg ok" role="status">{{ notice }}</p>{% endif %}

    <form class="filters" method="get" action="{{ url_for('admin.documents') }}">
        <label>Ordner
            <input type="text" name="prefix" value="{{ filters.prefix or '' }}"
                   placeholder="rc-sync/mandate/">
        </label>
        <label>Gruppe
            <select name="group">
                <option value="">alle</option>
                {% for g in groups %}
                <option value="{{ g.group_id }}"
                        {% if filters.group == g.group_id %}selected{% endif %}>{{ g.name }}</option>
                {% endfor %}
            </select>
        </label>
        <label>
            <input type="checkbox" name="unrestricted" value="1"
                   {% if filters.unrestricted %}checked{% endif %}>
            nur ohne Gruppe
        </label>
        <label>
            <input type="checkbox" name="conflicts" value="1"
                   {% if filters.conflicts %}checked{% endif %}>
            nur Konflikte
        </label>
        <button type="submit">Filtern</button>
    </form>

    <form method="post" action="{{ url_for('admin.set_document_acl') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">

        <table id="doc-table">
            <thead>
                <tr>
                    <th><span class="sr-only">Auswahl</span></th>
                    <th>Titel</th>
                    <th>Pfad</th>
                    <th>Gruppen</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody id="doc-rows">
                {% for d in documents %}
                <tr>
                    <td><input type="checkbox" name="pointer" value="{{ d.pointer }}"></td>
                    <td>{{ d.title or '—' }}</td>
                    <td class="ptr">{{ d.pointer }}</td>
                    <td>
                        {% if d.access_groups %}
                            {% for g in d.access_groups %}<span class="badge">{{ g }}</span>{% endfor %}
                        {% else %}
                            <span class="badge open">offen</span>
                        {% endif %}
                    </td>
                    <td>{{ d.status or '—' }}</td>
                </tr>
                {% else %}
                <tr><td colspan="5" class="hint">Keine Dokumente fuer diesen Filter.</td></tr>
                {% endfor %}
            </tbody>
        </table>

        <div class="more">
            <button type="button" id="load-more"
                    data-next-after="{{ next_after or '' }}"
                    data-endpoint="{{ url_for('admin.documents_page') }}"
                    {% if not next_after %}hidden{% endif %}>
                Mehr laden
            </button>
            <span class="hint" id="load-status" role="status"></span>
        </div>

        <div class="bulk">
            <strong>Auswahl einer Zugriffsgruppe zuordnen</strong>
            <p class="hint">
                Ersetzt die Gruppen der ausgewaehlten Dokumente vollstaendig.
                Ohne Auswahl einer Gruppe werden sie freigegeben.
            </p>
            {% for g in groups %}
            <label>
                <input type="checkbox" name="access_group" value="{{ g.group_id }}">
                {{ g.name }}
            </label>
            {% endfor %}
            <p><button type="submit">Zuordnen</button></p>
        </div>
    </form>
</main>

<script src="{{ url_for('static', filename='js/admin_documents.js') }}" defer></script>
</body>
</html>
```

- [ ] **Step 4: Write the cursor script**

Create `src/web_interface/static/js/admin_documents.js`:

```javascript
/* Cursor-fed document list.
 *
 * The inventory pages by keyset: the server hands back `next_after`, we hand
 * it straight back on the next request. No page numbers, because a page
 * number implies an offset, and an offset walk fails on a large tenant.
 */
(function () {
  'use strict';

  var button = document.getElementById('load-more');
  if (!button) { return; }
  var rows = document.getElementById('doc-rows');
  var status = document.getElementById('load-status');

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function badges(groups) {
    if (!groups || !groups.length) {
      return '<span class="badge open">offen</span>';
    }
    return groups.map(function (g) {
      return '<span class="badge">' + escapeHtml(g) + '</span>';
    }).join('');
  }

  function renderRow(doc) {
    var tr = document.createElement('tr');
    tr.innerHTML =
      '<td><input type="checkbox" name="pointer" value="' +
        escapeHtml(doc.pointer) + '"></td>' +
      '<td>' + escapeHtml(doc.title || '—') + '</td>' +
      '<td class="ptr">' + escapeHtml(doc.pointer) + '</td>' +
      '<td>' + badges(doc.access_groups) + '</td>' +
      '<td>' + escapeHtml(doc.status || '—') + '</td>';
    return tr;
  }

  button.addEventListener('click', function () {
    var after = button.getAttribute('data-next-after');
    if (!after) { return; }
    var url = new URL(button.getAttribute('data-endpoint'), window.location.origin);
    url.searchParams.set('after', after);
    // Carry the active filters so paging stays inside the same result set.
    new URLSearchParams(window.location.search).forEach(function (v, k) {
      if (k !== 'after') { url.searchParams.set(k, v); }
    });

    button.disabled = true;
    status.textContent = 'Wird geladen …';

    fetch(url.toString(), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (page) {
        (page.documents || []).forEach(function (doc) {
          rows.appendChild(renderRow(doc));
        });
        if (page.next_after) {
          button.setAttribute('data-next-after', page.next_after);
          status.textContent = '';
        } else {
          button.hidden = true;
          status.textContent = 'Alle Dokumente geladen.';
        }
      })
      .catch(function () {
        status.textContent = 'Nachladen fehlgeschlagen. Bitte erneut versuchen.';
      })
      .finally(function () { button.disabled = false; });
  });
}());
```

- [ ] **Step 5: Add the tab strip to the People page**

In `admin_people.html`, immediately after `<main class="admin">`, insert the
same `<nav class="tabs">` block as above with `Personen` carrying `class="active"
aria-current="page"` and the other two plain. Add the matching `.tabs` CSS rules
to its `<style>` block.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_web_admin_documents.py -v`
Expected: PASS (10 tests)

- [ ] **Step 7: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/templates/ \
        KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/admin_documents.js \
        KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py
git commit -m "feat(admin): Dokumente screen with cursor-fed list"
```

---

### Task 6: Zugriffsgruppen tab — tree, folder rules, backfill progress

**Files:**
- Create: `KnovasPlatform/components/docbridge_integration/src/web_interface/templates/admin_access_groups.html`
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/admin_documents.py` (add the routes)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py` (extend)

**Interfaces:**
- Consumes: `KnovasClient.access_groups`, `create_access_group`, `rename_access_group`, `delete_access_group`, `folder_rules`, `create_folder_rule`, `update_folder_rule`, `delete_folder_rule` (Tasks 2–3).
- Produces: `/admin/access-groups` (GET), `/admin/access-groups/create` (POST), `/admin/folder-rules/save` (POST), `/admin/folder-rules/delete` (POST).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_admin_documents.py`:

```python
class TestAccessGroupsTab:
    def test_routes_exist_and_are_admin_gated(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        for route in ('"/access-groups"', '"/folder-rules/save"',
                      '"/folder-rules/delete"'):
            assert route in src, f"missing route {route}"
        assert src.count("@bp.route") == src.count("@require_admin")

    def test_folder_rule_save_is_csrf_gated(self):
        from web_interface import admin_documents

        src = inspect.getsource(admin_documents)
        idx = src.index("def save_folder_rule(")
        body = src[idx:idx + 1200]
        assert body.index("csrf_ok") < body.index("folder_rule")

    def test_template_explains_that_a_rule_change_is_cheap(self):
        html = (TEMPLATES / "admin_access_groups.html").read_text(encoding="utf-8")
        # The whole architecture rests on this being true; the screen should
        # say so, because an administrator who believes it is expensive will
        # not use it.
        assert "Ordnerregel" in html
        assert "sofort" in html.lower() or "unmittelbar" in html.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_web_admin_documents.py::TestAccessGroupsTab -v`
Expected: FAIL — `AssertionError: missing route "/access-groups"`

- [ ] **Step 3: Add the routes**

Append inside `attach_document_routes` in `admin_documents.py`, before
`return bp`:

```python
    def _groups_page(error=None, notice=None, status=200):
        client = client_factory()
        groups, rules = [], []
        try:
            groups = client.access_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zugriffsgruppen nicht abrufbar: %s", exc)
            error = error or "Zugriffsgruppen sind derzeit nicht abrufbar."
        try:
            rules = client.folder_rules()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregeln nicht abrufbar: %s", exc)
        return render_template(
            "admin_access_groups.html",
            active_nav="admin",
            **page_context(),
            groups=groups,
            rules=rules,
            me=gate.current_user(),
            error=error,
            notice=notice,
            csrf_token=csrf_token(),
        ), status

    @bp.route("/access-groups")
    @require_admin
    def access_groups():
        return _groups_page()

    @bp.route("/access-groups/create", methods=["POST"])
    @require_admin
    def create_access_group():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _groups_page(error="Formular ist abgelaufen.", status=400)
        name = str(request.form.get("name", "") or "").strip()
        parent = str(request.form.get("parent", "") or "").strip() or None
        if not name:
            return _groups_page(error="Bitte einen Namen angeben.", status=400)
        try:
            client_factory().create_access_group(name, parent=parent)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gruppe nicht angelegt: %s", exc)
            return _groups_page(error="Gruppe konnte nicht angelegt werden.",
                                status=400)
        audit.record(
            gate.connection(), action="access_group.created",
            actor=gate.current_user(), target_type="access_group",
            target_id=name, detail={"parent": parent},
        )
        return _groups_page(notice=f"Gruppe „{name}“ wurde angelegt.")

    @bp.route("/folder-rules/save", methods=["POST"])
    @require_admin
    def save_folder_rule():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _groups_page(error="Formular ist abgelaufen.", status=400)
        rule_id = str(request.form.get("rule_id", "") or "").strip()
        prefix = str(request.form.get("pointer_prefix", "") or "").strip()
        groups = [g for g in request.form.getlist("access_group") if g]
        client = client_factory()
        try:
            if rule_id:
                client.update_folder_rule(rule_id, groups)
            elif prefix:
                client.create_folder_rule(prefix, groups)
            else:
                return _groups_page(error="Bitte einen Ordner angeben.",
                                    status=400)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregel nicht gespeichert: %s", exc)
            return _groups_page(
                error="Ordnerregel konnte nicht gespeichert werden.", status=400
            )
        audit.record(
            gate.connection(), action="folder_rule.saved",
            actor=gate.current_user(), target_type="folder_rule",
            target_id=rule_id or prefix, detail={"access_groups": groups},
        )
        return _groups_page(notice="Ordnerregel gespeichert.")

    @bp.route("/folder-rules/delete", methods=["POST"])
    @require_admin
    def delete_folder_rule():
        csrf_ok = _csrf_ok()
        if not csrf_ok:
            return _groups_page(error="Formular ist abgelaufen.", status=400)
        rule_id = str(request.form.get("rule_id", "") or "").strip()
        if not rule_id:
            return _groups_page(error="Keine Regel ausgewaehlt.", status=400)
        try:
            client_factory().delete_folder_rule(rule_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ordnerregel nicht geloescht: %s", exc)
            return _groups_page(error="Regel konnte nicht geloescht werden.",
                                status=400)
        audit.record(
            gate.connection(), action="folder_rule.deleted",
            actor=gate.current_user(), target_type="folder_rule",
            target_id=rule_id, detail={},
        )
        return _groups_page(notice="Ordnerregel geloescht.")
```

- [ ] **Step 4: Write the template**

Create `src/web_interface/templates/admin_access_groups.html` with the same
head, sidebar include, `.admin` styling and tab strip as `admin_documents.html`
(with `Zugriffsgruppen` active), and this main body:

```html
    <h1>Zugriffsgruppen</h1>
    <p class="hint">
        Gruppen entscheiden, wer welche Dokumente sieht. Eine Ordnerregel
        ordnet einen ganzen Pfad einer Gruppe zu — auch Dokumente, die spaeter
        dort eingelesen werden, erben sie automatisch.
    </p>

    {% if error %}<p class="msg error" role="alert">{{ error }}</p>{% endif %}
    {% if notice %}<p class="msg ok" role="status">{{ notice }}</p>{% endif %}

    <section class="panel">
        <h2>Gruppen</h2>
        <table>
            <thead><tr><th>Name</th><th>Uebergeordnet</th></tr></thead>
            <tbody>
            {% for g in groups %}
                <tr><td>{{ g.name }}</td><td>{{ g.parent_id or '—' }}</td></tr>
            {% else %}
                <tr><td colspan="2" class="hint">Noch keine Gruppen angelegt.</td></tr>
            {% endfor %}
            </tbody>
        </table>

        <form method="post" action="{{ url_for('admin.create_access_group') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <label>Name <input type="text" name="name" required></label>
            <label>Uebergeordnet
                <select name="parent">
                    <option value="">— keine —</option>
                    {% for g in groups %}
                    <option value="{{ g.group_id }}">{{ g.name }}</option>
                    {% endfor %}
                </select>
            </label>
            <p><button type="submit">Gruppe anlegen</button></p>
        </form>
    </section>

    <section class="panel">
        <h2>Ordnerregeln</h2>
        <p class="hint">
            Eine Ordnerregel wirkt <strong>sofort</strong>: geaendert wird ein
            einziger Datensatz, nicht jedes Dokument. Deshalb ist das Aendern
            einer Regel auch bei sehr grossen Bestaenden unmittelbar fertig.
            Bereits vorhandene Dokumente uebernehmen die Regel erst mit einem
            Abgleich.
        </p>
        <table>
            <thead>
                <tr><th>Ordner</th><th>Gruppen</th><th>Version</th><th></th></tr>
            </thead>
            <tbody>
            {% for r in rules %}
                <tr>
                    <td class="ptr">{{ r.pointer_prefix }}</td>
                    <td>
                        {% if r.access_groups %}
                            {% for g in r.access_groups %}<span class="badge">{{ g }}</span>{% endfor %}
                        {% else %}<span class="badge open">offen</span>{% endif %}
                    </td>
                    <td>{{ r.version }}</td>
                    <td>
                        <form method="post" class="inline"
                              action="{{ url_for('admin.delete_folder_rule') }}">
                            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                            <input type="hidden" name="rule_id" value="{{ r.rule_id }}">
                            <button type="submit">Loeschen</button>
                        </form>
                    </td>
                </tr>
            {% else %}
                <tr><td colspan="4" class="hint">Noch keine Ordnerregeln.</td></tr>
            {% endfor %}
            </tbody>
        </table>

        <form method="post" action="{{ url_for('admin.save_folder_rule') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <label>Ordner
                <input type="text" name="pointer_prefix"
                       placeholder="rc-sync/mandate/muster/" required>
            </label>
            {% for g in groups %}
            <label><input type="checkbox" name="access_group" value="{{ g.group_id }}"> {{ g.name }}</label>
            {% endfor %}
            <p><button type="submit">Regel speichern</button></p>
        </form>
    </section>
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_web_admin_documents.py -v`
Expected: PASS (13 tests)

- [ ] **Step 6: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/ \
        KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py
git commit -m "feat(admin): Zugriffsgruppen tab with folder rules"
```

---

### Task 7: Wire `client_factory` at app registration

**Files:**
- Modify: `KnovasPlatform/components/docbridge_integration/src/web_interface/app.py` (the `create_admin_blueprint(...)` call)
- Test: `KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py` (extend)

**Interfaces:**
- Consumes: Task 4's `create_admin_blueprint(..., client_factory=...)`.
- Produces: the console reaches Knovas through the same client the search path uses.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web_admin_documents.py`:

```python
class TestAppWiring:
    def test_app_passes_a_client_factory_to_the_console(self):
        import pathlib

        app_py = (
            pathlib.Path(__file__).resolve().parents[1]
            / "src" / "web_interface" / "app.py"
        ).read_text(encoding="utf-8")
        idx = app_py.index("create_admin_blueprint(")
        call = app_py[idx:idx + 400]
        assert "client_factory" in call, (
            "the console needs a Knovas client to reach the RBAC endpoints"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/test_web_admin_documents.py::TestAppWiring -v`
Expected: FAIL — `AssertionError: the console needs a Knovas client`

- [ ] **Step 3: Pass the factory**

In `app.py`, at the `create_admin_blueprint(...)` call, add the factory. Reuse
whatever accessor the search path already uses to obtain a `KnovasClient` rather
than constructing a second one — grep for the existing client accessor first:

```bash
grep -n "KnovasClient(" src/web_interface/app.py | head
```

Then:

```python
    admin_bp = create_admin_blueprint(
        gate,
        csrf_valid=_csrf_token_is_valid,
        csrf_token=_ensure_csrf_token,
        page_context=_page_context,
        # The console talks to Knovas through the same client the search path
        # uses, so mTLS material, retries and rate limiting are configured in
        # exactly one place.
        client_factory=get_knovas_client,
    )
    app.register_blueprint(admin_bp)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/ -q`
Expected: PASS — the whole Platform suite.

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/components/docbridge_integration/src/web_interface/app.py \
        KnovasPlatform/components/docbridge_integration/tests/test_web_admin_documents.py
git commit -m "feat(admin): wire the Knovas client into the console"
```

---

### Task 8: Documentation

**Files:**
- Create: `KnovasPlatform/docs/features/document-administration.md`
- Modify: `KnovasPlatform/docs/` index, `RELEASE_NOTES.md`
- Test: whatever docs-link test the repo already runs (grep for it in step 1)

**Interfaces:**
- Consumes: everything above.
- Produces: an administrator-facing description of the two tabs and the folder-rule model.

- [ ] **Step 1: Find the docs index and any link test**

Run:

```bash
grep -rn "features/" KnovasPlatform/docs/*.md | head
grep -rln "docs/features" KnovasPlatform/components/docbridge_integration/tests/ | head
```

Expected: the index file that lists feature docs, and any test asserting the
links resolve.

- [ ] **Step 2: Write the feature document**

Create `KnovasPlatform/docs/features/document-administration.md` covering:

- **Dokumente tab** — what it lists, that it is filtered by the administrator's
  own clearance (design D1), and that the count is the backend's aggregate.
- **Per-document access** — replace-not-merge semantics, and that the caller
  may only assign groups they dominate.
- **Ordnerregeln** — longest-matching prefix; new documents inherit at ingest;
  changing a rule is one write and takes effect immediately; existing documents
  adopt a rule only through a backfill.
- **Deduplication** — identical content filed in two folders is one document
  with one ACL. Most restrictive wins; where two rules leave no reader at all
  the document is parked as a conflict for a human decision, and the
  "nur Konflikte" filter finds those.
- **RemoteController** — `sources[].access_groups`, and the
  `sequential_subfolders` caveat from Task 1.
- **Scale** — the list pages by cursor; there is no page number and no total
  page count, deliberately.

- [ ] **Step 3: Add the release note**

In `RELEASE_NOTES.md`:

```markdown
### Dokumentverwaltung und Ordner-Zugriffsrechte

Die Verwaltung zeigt jetzt alle hochgeladenen Dokumente des Mandanten und
erlaubt, Zugriffsrechte je Dokument oder je Ordner zu setzen. Ordnerregeln
gelten auch fuer spaeter eingelesene Dokumente, sodass ein erneuter Abgleich
eine geschlossene Wand nicht wieder oeffnet.
```

- [ ] **Step 4: Run the suites**

Run:

```bash
cd KnovasPlatform/components/docbridge_integration && python -m pytest tests/ -q
cd ../../../RemoteController && python -m pytest tests/ -q
```

Expected: PASS in both.

- [ ] **Step 5: Commit**

```bash
git add KnovasPlatform/docs/ RELEASE_NOTES.md
git commit -m "docs(admin): document administration and folder access rules"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §6.1 RemoteController `sources[].access_groups` | 1 |
| §6.2 client methods — groups, document ACL | 2 |
| §6.2 client methods — inventory, folder rules | 3 |
| §6.3 Dokumente tab (routes) | 4 |
| §6.3 Dokumente tab (screen, virtualised) | 5 |
| §6.4 Zugriffsgruppen tab, folder rules | 6 |
| §6.3/§6.4 wiring | 7 |
| §5.4 conflicts surfaced to a human | 6 (filter), 8 (documented) |
| Documentation | 1 (RC config), 8 |

**Type consistency:** `access_groups` is a `list[str]` across
`set_document_access`, `create_folder_rule` and `update_folder_rule`; the
RemoteController side uses `tuple[str, ...]` internally and converts once, at
the `init_body` assignment in Task 1. `next_after` is `str | None` in Task 3's
client, Task 4's `DocumentsView.page`, and the `data-next-after` attribute in
Task 5.

**Known gaps carried deliberately:**
- **Backfill progress is not yet a live view.** Tasks 6 and 8 describe folder
  rules and the conflicts filter; starting a backfill and polling
  `acl_backfill_jobs` is Part A §5.5 plumbing with no console surface here.
  Add it once Part A's job endpoint exists.
- **The enforcement switch is deliberately absent from this console.** Spec
  §5.6 puts it on the Knovas-staff internal API, because it has a precondition
  a customer cannot check.
