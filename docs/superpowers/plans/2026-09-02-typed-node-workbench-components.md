# Typed-node workbench — Platform slice implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one schema-driven surface in KnovasPlatform where an admin defines node types and their fields, any user creates and edits entities of those types, and a searchable list opens the selected node's immediate-neighbourhood graph beside a reader for its fields — with per-user editor grants.

**Architecture:** Five layers, each independently testable: datatype codecs (`graph_model.py`), a widened Knowledge Graph client, a Platform-local grant store (`node_grants.py`), a screen composer (`graph_workbench.py`), and a new `/api/graph/*` route namespace behind the existing CSRF hook. The UI is generated at runtime from `GET /node-types/<id>/schema` — no node type name appears anywhere in the code. The grant store is a new authorisation model, so it gets an Alloy model first (Task C0) — the Platform repo's first, run with the same headless driver and lockfile discipline as `KnowledgeBase/knovas-software/models/alloy/`.

**Tech Stack:** Python 3, Flask, psycopg (v3, tuple rows), PostgreSQL 15 (Platform-local `platform-db`), pytest, vanilla JS, cytoscape.js (already vendored), Alloy 6.2.0 (headless CLI).

**Spec:** `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md` (§6, §7)

**Jira:** SS-315 *Platform Projekt und Mandantenmanagement*

**Repository:** `KnovasComponents`. The backend slice is `docs/superpowers/plans/2026-09-02-typed-node-workbench-backend.md`; Tasks E2 and E4 consume it.

**Branch:** `design/typed-node-workbench`

**Working directory for all commands:** `KnovasPlatform/components/docbridge_integration/`

**Validation status of this plan (2026-09-04):** the two Alloy models and four mutants in Task C0 were run with the pinned Alloy 6.2.0 jar through a copy of KnowledgeBase's `ci/alloy_driver.py`: 9 checks hold, 5 witnesses are satisfiable, 4 mutants produce counterexamples, and `run_all.sh` prints `alloy-checks: ok`. Every code anchor below was verified against `origin/feat/section-b-buildout` (the identity stack this plan needs); where the earlier revision cited line numbers, symbols are used instead.

## Global Constraints

- **No node type appears in code.** Every form, column, label and validation is generated from the schema. A change that would need code to support a new node type is wrong — stop and re-read §3.1 of the spec.
- **Schemas never block writes.** A `required` attribute left empty produces a visible gap and a completeness entry, never a blocked save. Do not add client-side or server-side required validation.
- **Deprecate is not delete.** The API soft-deprecates attributes and facts keep their `attribute_id`. UI copy says "stillgelegt", never "gelöscht".
- **One read model.** Read visibility is the backend ACL. `node_grants` controls writes only. Never filter a list by `node_grants`.
- **Graph mode only.** Every new surface requires `ONTOLOGY_SOURCE=graph`. In fixture mode each renders the literal string `Wissensnetz-Modus erforderlich` — never a 500, never invented data.
- **All UI copy is German**, matching the existing screens. Identifiers, code comments and commit messages are English.
- **CSRF is already global.** The `before_request` hook `require_csrf_for_state_changing_requests` in `src/web_interface/app.py` validates `X-CSRF-Token` on every non-safe request; it exempts only `_CSRF_EXEMPT_ENDPOINTS` and endpoints whose name starts with `admin.` (server-rendered forms). The graph blueprint is JSON and must **not** be exempted. New routes inherit the hook; do not add a second check, but do assert it per route in tests.
- **The identity stack must be on the branch before Task C1.** That means `src/identity/{db,migrate,users,sessions,webauth}.py`, `src/identity/migrations/0001_identity.sql`, `src/web_interface/admin.py`, the `platform-db` service in `docker-compose.yml`, and the `platform_db` / `identity_app` / `identity_client` / `identity_repo` fixtures in `tests/conftest.py`. On `main` today only `identity/passwords.py` and the ingestion compiler have merged (PR #7). The full stack exists, unmerged, on **both** `feat/section-b-buildout` and `feat/admin-document-rbac`; the two have diverged (24 and 20 commits apart, neither contains the other), so one merges first and the other rebases. This plan's anchors were verified against `feat/section-b-buildout`. Tasks B1–B4 and C0 do not depend on it and may proceed first.
- **Model before code.** Task C0 lands the Alloy models for the grant rules before C1 writes the store, following `KnowledgeBase/docs/Docs/01_SYSTEM/Feature_Design_Workflow.md` and the unified-model idiom in `KnowledgeBase/knovas-software/models/alloy/README.md` (mechanism preds mirrored from code, checks as `Mechanism implies Property`, witnesses, one open-based mutant per load-bearing conjunct, a pinned lockfile). The Platform has no Golden Invariants catalog; the header cites `KC-GRANT-01 (PROPOSED)` and the pin test carries the must-agree contract.
- **Identity rows are tuples.** `identity/db.py` opens psycopg 3 connections with the default row factory and `identity/users.py` maps columns by position. `NodeGrantStore` does the same — never `row["column"]`.
- Run tests with `pytest` from `KnovasPlatform/components/docbridge_integration/`.

---

## Part Overview

| Task | Deliverable | Depends on |
| --- | --- | --- |
| B1 | `GraphError` — actionable API error codes | — |
| B2 | `graph_model.py` — the five datatype codecs | — |
| B3 | Client: schema reads, type/node updates, server-side filters | B1 |
| B4 | Client: facts CRUD, neighbours with edges | B1, backend A2 |
| C0 | Alloy: `models/alloy/node_grants.als`, `node_grants_lifecycle.als`, four mutants, driver, CI step, pins | — |
| C1 | `node_grants` table and store | C0, identity stack on the branch |
| C2 | `may_write` guard and the route decorator | C1 |
| D1 | Node-type and schema routes (admin-gated) | B3, C2 |
| D2 | `graph_workbench.py` composer + node list/detail routes | B3, B4, C2 |
| D3 | Fact and grant routes | B4, C2 |
| E1 | Workbench shell and searchable list pane | D2 |
| E2 | Neighbourhood graph pane | D2, backend A2 |
| E3 | Field reader pane | D2 |
| E4 | Creation form, Typ-Werkstatt, editors panel | D1, D3, backend A4 |

## File Structure

| File | Responsibility |
| --- | --- |
| `src/graph_model.py` *(new)* | Datatype codecs only. No I/O, no Flask, no HTTP. |
| `src/graph_workbench.py` *(new)* | Compose one screen payload from several client calls. No SQL, no Flask. |
| `src/identity/node_grants.py` *(new)* | Owner/editor grants. One `may_write` predicate. |
| `src/identity/migrations/0002_node_grants.sql` *(new)* | The grant table. |
| `src/web_interface/graph_routes.py` *(new)* | The `/api/graph/*` blueprint. Thin: parse, authorise, delegate, serialise. |
| `src/knovas_client.py` *(modify)* | Knowledge Graph API coverage + `GraphError`. |
| `src/web_interface/templates/workbench.html` *(new)* | Three-pane shell. |
| `src/web_interface/static/js/workbench.js` *(new)* | List, graph pane, field reader, forms. |
| `src/web_interface/static/css/workbench.css` *(new)* | Pane layout; reuses existing tokens. |
| `models/alloy/node_grants.als`, `models/alloy/node_grants_lifecycle.als` *(new)* | The grant rules as mechanisms + checks (C0). |
| `models/alloy/mutants/node_grants__*.als` *(new, 4)* | One refuting weakening per load-bearing conjunct. |
| `models/alloy/ci/{alloy_driver.py, run_all.sh, alloy.version}` *(new, copied verbatim from KnowledgeBase)*, `ci/expected_results.json` *(generated)* | Headless runner + pinned outcomes. |
| `tests/test_node_grants_alloy.py` *(new)* | Pins every model command and outcome (must-agree). |
| `tests/conftest.py` *(modify)* | `FakeGraphApi`, signed-in API clients, `grants`, `node_owned_by_alice` (C2 Step 0). |

`graph_routes.py` is a new module rather than more routes in `app.py` because
that file is already ~1900 lines and every new namespace added to it makes the
next one harder to place. It follows the blueprint-factory pattern
`web_interface/admin.py` established.

---

### Task B1: `GraphError` for actionable API error codes

`_graph_request` maps every 404 to `None` and re-raises everything else
(`knovas_client.py:1531-1566`). A route cannot then tell "you asked for
something that is not there" from "the operator is mid-migration, retry in a
minute", and rendering the second as a failure teaches users to distrust a
feature that is working correctly.

**Files:**
- Modify: `src/knovas_client.py` (near `_graph_request`, ~line 1520)
- Test: `tests/test_knovas_client_secured_api.py`

**Interfaces:**
- Produces: `GraphError(Exception)` with attributes `status: int`, `error_code: str | None`, `message: str`. `_graph_request` raises it for any non-2xx that is not a 404; 404 keeps returning `None`.

- [ ] **Step 1: Write the failing test**

```python
class TestGraphError:
    def test_404_still_returns_none(self, client, requests_mock):
        requests_mock(status=404, json={"message": "Node not found"})
        assert client.graph_node("missing") is None

    def test_a_422_raises_with_its_error_code(self, client, requests_mock):
        requests_mock(status=422, json={"error_code": "identifier_limit_exceeded",
                                        "message": "Max 16"})
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 422
        assert caught.value.error_code == "identifier_limit_exceeded"

    def test_a_503_carries_its_code_so_a_route_can_say_retry(self, client, requests_mock):
        requests_mock(status=503, json={"error_code": "relevance_calibration_missing"})
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 503
        assert caught.value.error_code == "relevance_calibration_missing"

    def test_a_body_without_an_error_code_still_raises(self, client, requests_mock):
        requests_mock(status=500, json={})
        with pytest.raises(GraphError) as caught:
            client.graph_node("n1")
        assert caught.value.status == 500 and caught.value.error_code is None
```

Follow the mocking style already used in that module for `_graph_request`.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_knovas_client_secured_api.py::TestGraphError -v`
Expected: FAIL — `NameError: name 'GraphError' is not defined`

- [ ] **Step 3: Implement**

Above `_graph_request` in `src/knovas_client.py`:

```python
class GraphError(Exception):
    """A Knowledge Graph API call failed in a way the caller can act on.

    404 is deliberately NOT raised: an unknown or foreign id is the API's
    documented answer for "not yours", and every caller already treats None as
    that. What callers cannot currently distinguish is a 422 they should show
    the user from a 503 that means "retry once the operator finishes", which is
    what error_code carries.
    """

    def __init__(self, status: int, error_code: Optional[str], message: str):
        super().__init__(f"{status} {error_code or ''}: {message}".strip())
        self.status = status
        self.error_code = error_code
        self.message = message
```

In `_graph_request`, replace the bare re-raise for non-404 errors:

```python
        if not response.ok:
            try:
                body = response.json() or {}
            except ValueError:
                body = {}
            raise GraphError(response.status_code,
                             body.get("error_code"),
                             body.get("message") or response.reason or "")
```

leaving the existing 404 branch that logs and returns `None` exactly as it is.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_knovas_client_secured_api.py::TestGraphError -v`
Expected: 4 passed

- [ ] **Step 5: Run the client suites for regressions**

Run: `pytest tests/test_knovas_client_secured_api.py tests/test_knovas_client_hardening.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/knovas_client.py tests/test_knovas_client_secured_api.py
git commit -m "feat(client): GraphError carries status and error_code (SS-315)"
```

---

### Task B2: `graph_model.py` — the five datatype codecs

A fact value is a JSONB payload whose shape depends on its attribute's
datatype. Encoding it in the route handler would put five shapes into every
handler that touches a fact; this module is the one place that knows them.

**Files:**
- Create: `src/graph_model.py`
- Test: `tests/test_graph_model.py`

**Interfaces:**
- Produces:
  - `class FactValueError(ValueError)` — message is written for a user and is surfaced verbatim by the routes in D3.
  - `encode(datatype: str, raw: Any, *, enum_values: list | None = None) -> Any` — form input to the JSONB payload. Raises `FactValueError` on a shape the API would reject.
  - `decode(datatype: str, value: Any) -> Any` — payload to a display-ready dict.
  - `format_date(value: dict) -> str` — `{"value": "2026-03-04", "precision": "month"}` → `"März 2026"`.
  - `DATATYPES: tuple[str, ...]` = `("text", "date", "money", "enum", "entity_ref")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_model.py`:

```python
"""Codecs for the five fact datatypes.

The shapes are the API's, not ours: graph_api.py validates them server-side and
a malformed payload is a 422 the user cannot act on. Encoding here means a bad
value is caught with the field still on screen.
"""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from graph_model import DATATYPES, FactValueError, decode, encode, format_date


class TestText:
    def test_text_is_the_trimmed_string(self):
        assert encode("text", "  Vertrag  ") == "Vertrag"

    def test_empty_text_is_refused(self):
        """An empty fact is an absent fact. Writing one would make the
        completeness report count a gap as filled."""
        with pytest.raises(FactValueError):
            encode("text", "   ")


class TestDate:
    def test_a_day_precision_date(self):
        assert encode("date", {"value": "2026-03-04", "precision": "day"}) == {
            "value": "2026-03-04", "precision": "day"}

    def test_precision_defaults_to_day(self):
        assert encode("date", {"value": "2026-03-04"})["precision"] == "day"

    def test_an_unparseable_date_is_refused(self):
        with pytest.raises(FactValueError):
            encode("date", {"value": "04.03.2026"})

    def test_an_unknown_precision_is_refused(self):
        with pytest.raises(FactValueError):
            encode("date", {"value": "2026-03-04", "precision": "hour"})

    def test_month_precision_renders_as_a_month(self):
        """A month-precision fact drawn on a specific day is a fabricated
        detail in a document a court may see."""
        assert format_date({"value": "2026-03-04", "precision": "month"}) == "März 2026"

    def test_year_precision_renders_as_a_year(self):
        assert format_date({"value": "2026-03-04", "precision": "year"}) == "2026"

    def test_day_precision_renders_as_a_swiss_date(self):
        assert format_date({"value": "2026-03-04", "precision": "day"}) == "04.03.2026"


class TestMoney:
    def test_amount_and_iso_currency(self):
        assert encode("money", {"amount": "1500.50", "currency": "chf"}) == {
            "amount": "1500.50", "currency": "CHF"}

    def test_a_non_iso_currency_is_refused(self):
        with pytest.raises(FactValueError):
            encode("money", {"amount": "10", "currency": "Franken"})

    def test_a_non_numeric_amount_is_refused(self):
        with pytest.raises(FactValueError):
            encode("money", {"amount": "viel", "currency": "CHF"})


class TestEnum:
    def test_a_member_of_the_declared_values(self):
        assert encode("enum", "offen", enum_values=["offen", "erledigt"]) == "offen"

    def test_a_non_member_is_refused(self):
        with pytest.raises(FactValueError):
            encode("enum", "schwebend", enum_values=["offen", "erledigt"])

    def test_an_enum_without_declared_values_is_refused(self):
        with pytest.raises(FactValueError):
            encode("enum", "offen", enum_values=None)


class TestEntityRef:
    def test_a_node_id(self):
        assert encode("entity_ref", {"node_id": "abc"}) == {"node_id": "abc"}

    def test_a_bare_string_is_accepted_as_the_node_id(self):
        assert encode("entity_ref", "abc") == {"node_id": "abc"}

    def test_a_missing_node_id_is_refused(self):
        with pytest.raises(FactValueError):
            encode("entity_ref", {})


class TestDatatypeSet:
    def test_the_five_datatypes_match_the_api(self):
        assert DATATYPES == ("text", "date", "money", "enum", "entity_ref")

    def test_an_unknown_datatype_is_refused(self):
        with pytest.raises(FactValueError):
            encode("timestamp", "now")


class TestDecode:
    def test_decode_round_trips_every_datatype(self):
        assert decode("text", "Vertrag") == "Vertrag"
        assert decode("entity_ref", {"node_id": "abc"}) == {"node_id": "abc"}
        assert decode("money", {"amount": "10", "currency": "CHF"})["currency"] == "CHF"

    def test_decode_tolerates_a_payload_it_did_not_write(self):
        """Facts predate this module; a shape from an older writer must render
        as something rather than raise on a read path."""
        assert decode("date", "2026-03-04") == {"value": "2026-03-04",
                                                "precision": "day"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_graph_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'graph_model'`

- [ ] **Step 3: Implement**

Create `src/graph_model.py`:

```python
"""Fact value shapes for the five schema datatypes.

The shapes belong to the Knowledge Graph API (graph_api.py validates them
server-side); this module is the single place the Platform knows them, so a
malformed value is caught with the field still on screen instead of arriving
as a 422 the user cannot act on.

Deliberately I/O-free: no Flask, no HTTP, no database. That is what makes the
whole datatype surface testable in one fast file.

Design: docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md (7.0)
"""
from __future__ import annotations

import re
from datetime import date as _date
from typing import Any, Optional, Sequence

DATATYPES = ("text", "date", "money", "enum", "entity_ref")
PRECISIONS = ("day", "month", "year")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")

# German month names: the UI is German and a date is rendered, never localised
# at read time by a library the tests would then have to pin.
_MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember")


class FactValueError(ValueError):
    """A value does not match its attribute's datatype. Message is for a user."""


def encode(datatype: str, raw: Any, *, enum_values: Optional[Sequence[str]] = None) -> Any:
    if datatype == "text":
        return _text(raw)
    if datatype == "date":
        return _date_value(raw)
    if datatype == "money":
        return _money(raw)
    if datatype == "enum":
        return _enum(raw, enum_values)
    if datatype == "entity_ref":
        return _entity_ref(raw)
    raise FactValueError(f"Unbekannter Datentyp: {datatype}")


def decode(datatype: str, value: Any) -> Any:
    """Payload to a display-ready value.

    Tolerant by design: facts predate this module and a shape written by an
    older path must still render. A read path that raises turns one odd row
    into a blank screen.
    """
    if datatype == "date" and isinstance(value, str):
        return {"value": value, "precision": "day"}
    if datatype == "entity_ref" and isinstance(value, str):
        return {"node_id": value}
    return value


def format_date(value: Any) -> str:
    """Render honouring precision. A month-precision fact must never appear as
    a specific day — that is a fabricated detail in a document a court may see.
    """
    decoded = decode("date", value)
    if not isinstance(decoded, dict):
        return str(value)
    raw = str(decoded.get("value") or "")
    precision = decoded.get("precision") or "day"
    if not _ISO_DATE.match(raw):
        return raw
    year, month, day = raw.split("-")
    if precision == "year":
        return year
    if precision == "month":
        return f"{_MONTHS[int(month) - 1]} {year}"
    return f"{day}.{month}.{year}"


def _text(raw: Any) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        raise FactValueError("Text darf nicht leer sein.")
    return text


def _date_value(raw: Any) -> dict:
    if isinstance(raw, str):
        raw = {"value": raw}
    if not isinstance(raw, dict):
        raise FactValueError("Datum erwartet {value, precision}.")
    value = str(raw.get("value") or "").strip()
    precision = str(raw.get("precision") or "day").strip()
    if not _ISO_DATE.match(value):
        raise FactValueError("Datum muss im Format JJJJ-MM-TT vorliegen.")
    try:
        _date.fromisoformat(value)
    except ValueError as exc:
        raise FactValueError("Kein gültiges Datum.") from exc
    if precision not in PRECISIONS:
        raise FactValueError("Genauigkeit muss day, month oder year sein.")
    return {"value": value, "precision": precision}


def _money(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise FactValueError("Betrag erwartet {amount, currency}.")
    amount = str(raw.get("amount") or "").strip().replace("'", "")
    currency = str(raw.get("currency") or "").strip().upper()
    try:
        float(amount)
    except ValueError as exc:
        raise FactValueError("Betrag muss eine Zahl sein.") from exc
    if not _ISO_CURRENCY.match(currency):
        raise FactValueError("Währung muss ein ISO-4217-Code sein, z. B. CHF.")
    return {"amount": amount, "currency": currency}


def _enum(raw: Any, enum_values: Optional[Sequence[str]]) -> str:
    if not enum_values:
        raise FactValueError("Für dieses Attribut sind keine Werte definiert.")
    value = str(raw or "").strip()
    if value not in list(enum_values):
        raise FactValueError("Wert ist für dieses Attribut nicht zugelassen.")
    return value


def _entity_ref(raw: Any) -> dict:
    node_id = raw if isinstance(raw, str) else (raw or {}).get("node_id")
    node_id = str(node_id or "").strip()
    if not node_id:
        raise FactValueError("Verknüpfung braucht einen Knoten.")
    return {"node_id": node_id}
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_graph_model.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/graph_model.py tests/test_graph_model.py
git commit -m "feat(graph): datatype codecs for the five fact shapes (SS-315)"
```

---

### Task B3: Client — schema reads, type and node updates, server-side filters

**Files:**
- Modify: `src/knovas_client.py` (the graph section: `graph_nodes` … `graph_delete_schema_attribute`, ~lines 1573–1645)
- Modify: `src/ontology_graph.py:312-330` (`create_type_relation`)
- Test: `tests/test_knovas_client_secured_api.py`

**Interfaces:**
- Consumes: `_graph_request`, `GraphError` (B1).
- Produces:
  - `graph_schema(type_id, include_deprecated=False) -> list[dict]`
  - `graph_create_schema_attribute(type_id, name, datatype='entity_ref', required=False, description=None, sort_order=0, enum_values=None, target_node_type_id=None) -> dict | None`
  - `graph_update_schema_attribute(type_id, attribute_id, **fields) -> dict | None`
  - `graph_deprecate_schema_attribute(type_id, attribute_id) -> dict | None` — **renamed** from `graph_delete_schema_attribute`
  - `graph_update_node_type(type_id, **fields) -> dict | None`
  - `graph_update_node(node_id, **fields) -> dict | None`
  - `graph_nodes(node_type_id=None, q=None) -> list[dict]` — **signature widened**

- [ ] **Step 1: Write the failing tests**

```python
class TestSchemaAndFilters:
    def test_graph_nodes_sends_the_server_side_filters(self, client, capture):
        client.graph_nodes(node_type_id="t1", q="Müller")
        assert capture.last.params == {"node_type_id": "t1", "q": "Müller"}

    def test_graph_nodes_omits_absent_filters(self, client, capture):
        client.graph_nodes()
        assert capture.last.params == {}

    def test_graph_schema_reads_the_attributes(self, client, requests_mock):
        requests_mock(json={"attributes": [{"id": "a1", "name": "Frist",
                                            "datatype": "date"}]})
        assert client.graph_schema("t1")[0]["name"] == "Frist"

    def test_graph_schema_can_include_deprecated(self, client, capture):
        client.graph_schema("t1", include_deprecated=True)
        assert capture.last.params == {"include_deprecated": "true"}

    def test_create_attribute_sends_the_target_type(self, client, capture):
        client.graph_create_schema_attribute(
            "t1", "Zustaendig", datatype="entity_ref", target_node_type_id="t2")
        assert capture.last.data["target_node_type_id"] == "t2"

    def test_create_attribute_omits_a_null_target(self, client, capture):
        client.graph_create_schema_attribute("t1", "Notiz", datatype="text")
        assert "target_node_type_id" not in capture.last.data

    def test_deprecate_is_the_name_and_delete_is_gone(self, client):
        assert hasattr(client, "graph_deprecate_schema_attribute")
        assert not hasattr(client, "graph_delete_schema_attribute")

    def test_update_node_sends_only_the_given_fields(self, client, capture):
        client.graph_update_node("n1", name="Neu")
        assert capture.last.data == {"name": "Neu"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_knovas_client_secured_api.py::TestSchemaAndFilters -v`
Expected: FAIL — `AttributeError: 'KnovasClient' object has no attribute 'graph_schema'`

- [ ] **Step 3: Implement**

In `src/knovas_client.py`, replace `graph_nodes` and
`graph_create_schema_attribute`, delete `graph_delete_schema_attribute`, and
add the rest:

```python
    def graph_nodes(self, node_type_id: Optional[str] = None,
                    q: Optional[str] = None) -> List[Dict[str, Any]]:
        """GET /secured/graph/nodes - filtered server-side.

        The endpoint accepts node_type_id and q (ILIKE on name), so pulling the
        whole topology and filtering in Python costs a request that grows with
        the tenant for an answer the database already has.
        """
        params: Dict[str, Any] = {}
        if node_type_id:
            params['node_type_id'] = node_type_id
        if q:
            params['q'] = q
        return _graph_payload_list(
            self._graph_request('GET', '/nodes', params=params), 'nodes')

    def graph_schema(self, type_id: str,
                     include_deprecated: bool = False) -> List[Dict[str, Any]]:
        """GET /secured/graph/node-types/<id>/schema - the field definitions."""
        params = {'include_deprecated': 'true'} if include_deprecated else {}
        payload = self._graph_request(
            'GET', f'/node-types/{quote(str(type_id), safe="")}/schema',
            params=params)
        return _graph_payload_list(payload, 'attributes')

    def graph_create_schema_attribute(
            self, type_id: str, name: str, datatype: str = 'entity_ref',
            required: bool = False, description: Optional[str] = None,
            sort_order: int = 0, enum_values: Optional[List[str]] = None,
            target_node_type_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/node-types/<id>/schema - one field definition."""
        data: Dict[str, Any] = {
            'name': name, 'datatype': datatype,
            'required': bool(required), 'sort_order': int(sort_order),
        }
        if description:
            data['description'] = description
        if enum_values is not None:
            data['enum_values'] = list(enum_values)
        if target_node_type_id:
            data['target_node_type_id'] = target_node_type_id
        return self._graph_request(
            'POST', f'/node-types/{quote(str(type_id), safe="")}/schema', data=data)

    def graph_update_schema_attribute(self, type_id: str, attribute_id: str,
                                      **fields: Any) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/node-types/<id>/schema/<aid>."""
        return self._graph_request(
            'PATCH',
            f'/node-types/{quote(str(type_id), safe="")}'
            f'/schema/{quote(str(attribute_id), safe="")}',
            data=dict(fields))

    def graph_deprecate_schema_attribute(self, type_id: str,
                                         attribute_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/node-types/<id>/schema/<aid>.

        Named for what the server does: it soft-deprecates and existing facts
        keep their attribute_id. A method called "delete" would describe an
        operation the API does not perform, and a UI built on that name would
        promise the user something untrue.
        """
        return self._graph_request(
            'DELETE',
            f'/node-types/{quote(str(type_id), safe="")}'
            f'/schema/{quote(str(attribute_id), safe="")}')

    def graph_update_node_type(self, type_id: str,
                               **fields: Any) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/node-types/<id>."""
        return self._graph_request(
            'PATCH', f'/node-types/{quote(str(type_id), safe="")}', data=dict(fields))

    def graph_update_node(self, node_id: str,
                          **fields: Any) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/nodes/<id> - name, description, node_type_id,
        and required_groups (the backend ACL)."""
        return self._graph_request(
            'GET' if not fields else 'PATCH',
            f'/nodes/{quote(str(node_id), safe="")}', data=dict(fields))
```

Then delete the now-stale verification comment in `graph_create_node` — the
`node_type_id` guess is confirmed correct by `graph_api.py:625-629`.

In `src/ontology_graph.py`, `create_type_relation` currently returns `None`
with a comment explaining that a type-level line cannot survive a reload. With
`target_node_type_id` (backend Task A4) it can. Leave the method returning
`None` for now and update its docstring to point at Task E4, which supersedes
it; do not delete it, because `ontology.js` still calls the route.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_knovas_client_secured_api.py::TestSchemaAndFilters -v`
Expected: 8 passed

- [ ] **Step 5: Check for callers of the renamed method**

Run: `grep -rn "graph_delete_schema_attribute" --include=*.py --include=*.js src/ tests/`
Expected: no results. If any appear, update them in this commit.

- [ ] **Step 6: Run the client and ontology suites**

Run: `pytest tests/test_knovas_client_secured_api.py tests/test_knovas_client_hardening.py tests/test_ontology_graph.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/knovas_client.py src/ontology_graph.py tests/test_knovas_client_secured_api.py
git commit -m "feat(client): schema reads, node/type updates, server-side node filters (SS-315)

Renames graph_delete_schema_attribute to graph_deprecate_schema_attribute:
the server soft-deprecates and facts keep their attribute_id."
```

---

### Task B4: Client — facts CRUD and neighbours with edges

**Files:**
- Modify: `src/knovas_client.py` (after the schema methods from B3)
- Test: `tests/test_knovas_client_secured_api.py`

**Interfaces:**
- Consumes: B1, B3. Backend Task A2 for `include_edges`.
- Produces:
  - `graph_facts(node_id) -> list[dict]`
  - `graph_create_fact(node_id, value, attribute_id=None, label=None) -> dict | None`
  - `graph_update_fact(fact_id, **fields) -> dict | None`
  - `graph_delete_fact(fact_id) -> dict | None`
  - `graph_neighbors(node_id, depth=1, include_edges=False) -> dict` — **return type changed** from `list` to `{"neighbors": [...], "edges": [...]}`. `edges` is `[]` when not requested.

- [ ] **Step 1: Write the failing tests**

```python
class TestFactsAndNeighbours:
    def test_create_fact_requires_an_attribute_or_a_label(self, client):
        with pytest.raises(ValueError):
            client.graph_create_fact("n1", "Wert")

    def test_create_fact_with_an_attribute_id(self, client, capture):
        client.graph_create_fact("n1", {"value": "2026-03-04", "precision": "day"},
                                 attribute_id="a1")
        assert capture.last.data == {
            "attribute_id": "a1",
            "value": {"value": "2026-03-04", "precision": "day"}}

    def test_create_fact_with_a_free_form_label(self, client, capture):
        client.graph_create_fact("n1", "Wert", label="Notiz")
        assert capture.last.data == {"label": "Notiz", "value": "Wert"}

    def test_facts_reads_the_list(self, client, requests_mock):
        requests_mock(json={"facts": [{"id": "f1", "value": "Wert"}]})
        assert client.graph_facts("n1")[0]["id"] == "f1"

    def test_neighbours_returns_a_mapping_with_both_keys(self, client, requests_mock):
        requests_mock(json={"neighbors": [{"id": "n2"}], "edges": [{"id": "e1"}]})
        result = client.graph_neighbors("n1", depth=1, include_edges=True)
        assert result["neighbors"][0]["id"] == "n2"
        assert result["edges"][0]["id"] == "e1"

    def test_neighbours_sends_include_edges_only_when_asked(self, client, capture):
        client.graph_neighbors("n1", depth=1)
        assert capture.last.params == {"depth": 1}
        client.graph_neighbors("n1", depth=1, include_edges=True)
        assert capture.last.params == {"depth": 1, "include_edges": "true"}

    def test_neighbours_edges_default_to_empty_not_missing(self, client, requests_mock):
        requests_mock(json={"neighbors": []})
        assert client.graph_neighbors("n1")["edges"] == []

    def test_neighbours_depth_is_clamped_to_the_api_cap(self, client, capture):
        client.graph_neighbors("n1", depth=9)
        assert capture.last.params["depth"] == 3
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_knovas_client_secured_api.py::TestFactsAndNeighbours -v`
Expected: FAIL — `TypeError: graph_neighbors() got an unexpected keyword argument 'include_edges'`

- [ ] **Step 3: Implement**

Replace `graph_neighbors` and add the fact methods in `src/knovas_client.py`:

```python
    def graph_neighbors(self, node_id: str, depth: int = 1,
                        include_edges: bool = False) -> Dict[str, Any]:
        """GET /secured/graph/nodes/<id>/neighbors - traversal, max 3 hops.

        Returns {"neighbors": [...], "edges": [...]}. The endpoint omits the
        edges key when include_edges is not requested; we normalise it to an
        empty list so callers never branch on a missing key.
        """
        depth = max(1, min(3, int(depth)))
        params: Dict[str, Any] = {'depth': depth}
        if include_edges:
            params['include_edges'] = 'true'
        payload = self._graph_request(
            'GET', f'/nodes/{quote(str(node_id), safe="")}/neighbors',
            params=params) or {}
        return {
            'neighbors': _graph_payload_list(payload, 'neighbors', 'nodes'),
            'edges': _graph_payload_list(payload, 'edges'),
        }

    def graph_facts(self, node_id: str) -> List[Dict[str, Any]]:
        """GET /secured/graph/nodes/<id>/facts - typed values on this node."""
        return _graph_payload_list(
            self._graph_request(
                'GET', f'/nodes/{quote(str(node_id), safe="")}/facts'), 'facts')

    def graph_create_fact(self, node_id: str, value: Any,
                          attribute_id: Optional[str] = None,
                          label: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/nodes/<id>/facts.

        The server's CHECK requires attribute_id OR label; refusing here means
        the caller sees the rule rather than a 422 from three layers away.
        """
        if not attribute_id and not label:
            raise ValueError("a fact needs an attribute_id or a label")
        data: Dict[str, Any] = {'value': value}
        if attribute_id:
            data['attribute_id'] = attribute_id
        else:
            data['label'] = label
        return self._graph_request(
            'POST', f'/nodes/{quote(str(node_id), safe="")}/facts', data=data)

    def graph_update_fact(self, fact_id: str,
                          **fields: Any) -> Optional[Dict[str, Any]]:
        """PATCH /secured/graph/facts/<fid>."""
        return self._graph_request(
            'PATCH', f'/facts/{quote(str(fact_id), safe="")}', data=dict(fields))

    def graph_delete_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """DELETE /secured/graph/facts/<fid>."""
        return self._graph_request(
            'DELETE', f'/facts/{quote(str(fact_id), safe="")}')
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_knovas_client_secured_api.py::TestFactsAndNeighbours -v`
Expected: 8 passed

- [ ] **Step 5: Run the client suites**

Run: `pytest tests/test_knovas_client_secured_api.py tests/test_knovas_client_hardening.py -q`
Expected: all pass. `graph_neighbors` had zero callers, so the widened return
breaks nothing.

- [ ] **Step 6: Commit**

```bash
git add src/knovas_client.py tests/test_knovas_client_secured_api.py
git commit -m "feat(client): facts CRUD and neighbours with induced edges (SS-315)"
```

---

### Task C0: Alloy — the grant rules, modelled before the store

`node_grants` is a permission model. Knovas lands the model before the code
(`KnowledgeBase/docs/Docs/01_SYSTEM/Feature_Design_Workflow.md`), and the
Platform repo has no Alloy tree yet, so this task creates one with the same
driver, mutant and lockfile discipline as `KnowledgeBase/knovas-software/models/alloy/`.
Nothing here needs PostgreSQL or the identity stack.

**Files:**
- Create: `models/alloy/node_grants.als`, `models/alloy/node_grants_lifecycle.als`
- Create: `models/alloy/mutants/node_grants__editor_delegates.als`, `…__two_owners.als`, `…__reads_narrowed.als`, `…__revoke_ignores_role.als`
- Create (copied verbatim from `KnowledgeBase/knovas-software/models/alloy/ci/`): `models/alloy/ci/alloy_driver.py`, `models/alloy/ci/run_all.sh`, `models/alloy/ci/alloy.version`
- Generate: `models/alloy/ci/expected_results.json`
- Create: `models/alloy/README.md`, `tests/test_node_grants_alloy.py`
- Modify: `pyproject.toml` (markers), `.github/workflows/ci.yml` (`knovas-platform` job), repository `.gitignore`

**Interfaces:**
- Produces: checks `an_editor_cannot_delegate`, `who_may_delegate_is_unambiguous`, `grants_never_narrow_reads`, `write_needs_a_grant_or_admin`, `an_admin_always_writes` (static) and `the_owner_survives_a_revoke`, `a_revoke_removes_only_the_named_editor`, `the_creator_owns_the_new_node`, `the_table_shape_is_preserved` (lifecycle); preds `mayWrite`, `mayGrant`, `GrantTableShape`, `WriteGateMechanism`, `GrantGateMechanism`, `ReadGateMechanism`, `RevokeMechanism`, `CreateMechanism` that C1/C2/D3 mirror line for line.

- [ ] **Step 1: Write the static model**

Create `models/alloy/node_grants.als`:

```alloy
/*
 * @invariant_id    KC-GRANT-01 (PROPOSED — Platform-side; no Golden Invariants
 *                  row exists for customer-hosted Platform code, see plan C0)
 * @plan            docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md (C0, C1, C2, D3)
 * @code_under_check
 *   - src/identity/node_grants.py (NodeGrantStore.may_write, .for_node)
 *   - src/identity/migrations/0002_node_grants.sql (PRIMARY KEY (node_id,
 *     user_id); idx_node_grants_one_owner)
 *   - src/web_interface/graph_routes.py (require_node_write, _may_grant,
 *     list_nodes — deliberately not filtered by grants)
 * @pytest_must_agree
 *   - tests/test_node_grants.py
 *   - tests/test_graph_routes_auth.py, tests/test_graph_routes_grants.py
 *   - tests/test_node_grants_alloy.py (pins)
 * @scope           5
 *
 * Who may WRITE a knowledge-graph node through the Platform. The design's
 * one structural decision is that this is not a second read model: reads
 * are the backend ACL's answer and nothing here narrows a listing. The
 * checks pin the three ways that decision erodes — an editor who can hand
 * out further editorships, a node with two owners so that "who may grant?"
 * has two answers, and a listing quietly filtered by grants.
 *
 * Why this is not a tautology: the mechanisms mirror three separate code
 * paths (may_write, _may_grant, the list route); the properties cross them
 * (an admitted grant implies an OWNER row; a grant-less reader is still
 * served). Each mutant breaks one path and one property falls.
 */
module knovas_platform/node_grants

sig User {}
sig Admin in User {}                 // platform role `admin`
sig Node {}                          // a kg_nodes id — opaque, no FK by design

abstract sig Role {}
one sig Owner, Editor extends Role {}

sig Grant {
  gNode: one Node,
  gUser: one User,
  gRole: one Role
}

/* The table as 0002_node_grants.sql constrains it. */
pred GrantTableShape[rows: set Grant] {
  all disj a, b: rows | not (a.gNode = b.gNode and a.gUser = b.gUser)   // PRIMARY KEY
  all n: Node | lone g: rows | g.gNode = n and g.gRole = Owner            // one owner
}

/* NodeGrantStore.may_write: the owner, an editor, or any admin. */
pred mayWrite[u: User, n: Node] {
  u in Admin or some g: Grant | g.gNode = n and g.gUser = u
}

/* graph_routes._may_grant: the owner or an admin — never an editor. */
pred mayGrant[u: User, n: Node] {
  u in Admin or some g: Grant | g.gNode = n and g.gUser = u and g.gRole = Owner
}

one sig Admitted {}

sig WriteAttempt { wUser: one User, wNode: one Node, wAdmitted: lone Admitted }
sig GrantAttempt { gaUser: one User, gaNode: one Node, gaAdmitted: lone Admitted }

/* require_node_write on every mutating node/fact route. */
pred WriteGateMechanism {
  all w: WriteAttempt | some w.wAdmitted iff mayWrite[w.wUser, w.wNode]
}

/* add_grant / remove_grant. */
pred GrantGateMechanism {
  all g: GrantAttempt | some g.gaAdmitted iff mayGrant[g.gaUser, g.gaNode]
}

/* Reads: list_nodes and node_detail hand back what the backend returned.
 * BackendVisible is GraphAccessGuard's verdict, opaque here. */
sig BackendVisible in Node {}
sig ReadAttempt { rUser: one User, rNode: one Node, rServed: lone Admitted }

pred ReadGateMechanism {
  all r: ReadAttempt | some r.rServed iff r.rNode in BackendVisible
}

pred Mechanisms {
  GrantTableShape[Grant]
  WriteGateMechanism
  GrantGateMechanism
  ReadGateMechanism
}

/* ── properties ─────────────────────────────────────────────────────────── */

/* A non-admin who is admitted to grant holds the OWNER row — editorship
 * never delegates. */
pred AnEditorCannotDelegate {
  all g: GrantAttempt | (some g.gaAdmitted and g.gaUser not in Admin) implies
    (some r: Grant | r.gNode = g.gaNode and r.gUser = g.gaUser and r.gRole = Owner)
}

/* Per node, at most one non-admin may grant: "who decides?" has one answer. */
pred WhoMayDelegateIsUnambiguous {
  all n: Node | lone u: User - Admin | mayGrant[u, n]
}

/* A reader with no grant at all is still served a backend-visible node. */
pred GrantsNeverNarrowReads {
  all r: ReadAttempt |
    (r.rNode in BackendVisible and no g: Grant | g.gUser = r.rUser)
      implies some r.rServed
}

/* A write is admitted only for the owner, an editor, or an admin. */
pred WriteNeedsAGrantOrAdmin {
  all w: WriteAttempt | some w.wAdmitted implies
    (w.wUser in Admin or some g: Grant | g.gNode = w.wNode and g.gUser = w.wUser)
}

/* An admin can always repair a node — including one with no grants at all. */
pred AnAdminAlwaysWrites {
  all w: WriteAttempt | w.wUser in Admin implies some w.wAdmitted
}

/* ── checks ─────────────────────────────────────────────────────────────── */

check an_editor_cannot_delegate        { Mechanisms implies AnEditorCannotDelegate } for 5
check who_may_delegate_is_unambiguous  { Mechanisms implies WhoMayDelegateIsUnambiguous } for 5
check grants_never_narrow_reads        { Mechanisms implies GrantsNeverNarrowReads } for 5
check write_needs_a_grant_or_admin     { Mechanisms implies WriteNeedsAGrantOrAdmin } for 5
check an_admin_always_writes           { Mechanisms implies AnAdminAlwaysWrites } for 5

/* ── witnesses ─────────────────────────────────────────────────────────── */

/* Owner, editor and stranger on one node; the editor writes but cannot
 * grant; the stranger reads a backend-visible node. */
run witness_mechanism_live {
  Mechanisms
  some n: Node, disj owner, editor, stranger: User - Admin {
    some g: Grant | g.gNode = n and g.gUser = owner and g.gRole = Owner
    some g: Grant | g.gNode = n and g.gUser = editor and g.gRole = Editor
    no g: Grant | g.gUser = stranger
    some w: WriteAttempt | w.wUser = editor and w.wNode = n and some w.wAdmitted
    some g: GrantAttempt | g.gaUser = editor and g.gaNode = n and no g.gaAdmitted
    some w: WriteAttempt | w.wUser = stranger and w.wNode = n and no w.wAdmitted
    n in BackendVisible
    some r: ReadAttempt | r.rUser = stranger and r.rNode = n and some r.rServed
  }
} for 5

/* The breach is representable absent the mechanism: an editor admitted to
 * grant, and a visible node withheld from a grant-less reader. */
run witness_breach_expressible {
  some g: GrantAttempt | some g.gaAdmitted and g.gaUser not in Admin
    and no r: Grant | r.gNode = g.gaNode and r.gUser = g.gaUser and r.gRole = Owner
  some r: ReadAttempt | r.rNode in BackendVisible and no r.rServed
} for 4
```

- [ ] **Step 2: Write the lifecycle model**

Create `models/alloy/node_grants_lifecycle.als`:

```alloy
/*
 * @invariant_id    KC-GRANT-01 (PROPOSED — see node_grants.als)
 * @plan            docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md (C0, C1)
 * @code_under_check
 *   - src/identity/node_grants.py (NodeGrantStore.set_owner, .revoke —
 *     `DELETE ... AND role = 'editor'`)
 *   - src/web_interface/graph_routes.py (create_node writes the creator as
 *     owner in the same request)
 * @pytest_must_agree
 *   - tests/test_node_grants.py (TestOwnership, TestEditors)
 * @scope           5
 *
 * One mutation, Pre → Post. The rule worth a model: a revoke deletes editor
 * rows only, so the owner survives any revoke — otherwise a node can end up
 * with nobody who may grant anything, and the "who decides?" question of
 * node_grants.als has zero answers instead of one.
 */
module knovas_platform/node_grants_lifecycle

open knovas_platform/node_grants

abstract sig Snap { rows: set Grant }
one sig Pre, Post extends Snap {}

lone sig Revoke { rvNode: one Node, rvUser: one User }
lone sig Create { crNode: one Node, crUser: one User }

/* NodeGrantStore.revoke: DELETE ... WHERE node_id AND user_id AND role='editor'. */
pred RevokeMechanism {
  some Revoke implies
    Post.rows = Pre.rows - { g: Grant |
      g.gNode = Revoke.rvNode and g.gUser = Revoke.rvUser and g.gRole = Editor }
}

/* create_node → set_owner: the node is new (no rows yet), one owner row is
 * inserted for the creator, and nothing else changes. */
pred CreateMechanism {
  some Create implies {
    no g: Pre.rows | g.gNode = Create.crNode
    Pre.rows in Post.rows
    one (Post.rows - Pre.rows)
    all g: Post.rows - Pre.rows |
      g.gNode = Create.crNode and g.gUser = Create.crUser and g.gRole = Owner
  }
}

pred OneMutation { lone (Revoke + Create) }

pred LifecycleMechanism {
  GrantTableShape[Pre.rows]
  OneMutation
  RevokeMechanism
  CreateMechanism
  (no Revoke and no Create) implies Post.rows = Pre.rows
}

/* ── properties ─────────────────────────────────────────────────────────── */

pred TheOwnerSurvivesARevoke {
  all g: Pre.rows | g.gRole = Owner implies g in Post.rows
}

pred ARevokeRemovesOnlyTheNamedEditor {
  some Revoke implies
    all g: Pre.rows - Post.rows | g.gUser = Revoke.rvUser and g.gRole = Editor
}

pred TheCreatorOwnsTheNewNode {
  some Create implies
    one g: Post.rows | g.gNode = Create.crNode and g.gRole = Owner
      and g.gUser = Create.crUser
}

pred TheTableShapeIsPreserved { GrantTableShape[Post.rows] }

/* ── checks ─────────────────────────────────────────────────────────────── */

check the_owner_survives_a_revoke        { LifecycleMechanism implies TheOwnerSurvivesARevoke } for 5
check a_revoke_removes_only_the_named_editor { LifecycleMechanism implies ARevokeRemovesOnlyTheNamedEditor } for 5
check the_creator_owns_the_new_node      { LifecycleMechanism implies TheCreatorOwnsTheNewNode } for 5
check the_table_shape_is_preserved       { LifecycleMechanism implies TheTableShapeIsPreserved } for 5

/* ── witnesses ─────────────────────────────────────────────────────────── */

run witness_revoke_of_an_editor {
  LifecycleMechanism
  some Revoke
  some g: Pre.rows | g.gNode = Revoke.rvNode and g.gUser = Revoke.rvUser and g.gRole = Editor
  some g: Pre.rows | g.gNode = Revoke.rvNode and g.gRole = Owner
} for 5

run witness_create_makes_an_owner {
  LifecycleMechanism
  some Create
} for 5

/* The breach is representable: the owner row gone after a revoke. */
run witness_breach_expressible {
  some Revoke
  some g: Pre.rows | g.gRole = Owner and g.gNode = Revoke.rvNode and g not in Post.rows
} for 4
```

- [ ] **Step 3: Write the four mutants**

Create `models/alloy/mutants/node_grants__editor_delegates.als`:

```alloy
/*
 * MUTANT — expected outcome: counterexample.
 *
 * Shadows: node_grants.als :: an_editor_cannot_delegate
 * Simulated bug: graph_routes._may_grant is written over may_write — "anyone
 * who may edit may also grant". One editorship then silently becomes the
 * right to hand out every further one.
 */
module knovas_platform/mutants/node_grants__editor_delegates

open knovas_platform/node_grants

pred GrantGateOverMayWrite {
  all g: GrantAttempt | some g.gaAdmitted iff mayWrite[g.gaUser, g.gaNode]
}

check editor_delegates_when_grant_gate_is_may_write {
  (GrantTableShape[Grant] and WriteGateMechanism and GrantGateOverMayWrite and ReadGateMechanism)
    implies AnEditorCannotDelegate
} for 5
```

Create `models/alloy/mutants/node_grants__two_owners.als`:

```alloy
/*
 * MUTANT — expected outcome: counterexample.
 *
 * Shadows: node_grants.als :: who_may_delegate_is_unambiguous
 * Simulated bug: the partial unique index idx_node_grants_one_owner is
 * dropped from 0002_node_grants.sql (only the primary key remains). Two
 * owner rows on one node are then storable, and two non-admins may grant.
 */
module knovas_platform/mutants/node_grants__two_owners

open knovas_platform/node_grants

pred PrimaryKeyOnly[rows: set Grant] {
  all disj a, b: rows | not (a.gNode = b.gNode and a.gUser = b.gUser)
}

check two_owners_without_the_partial_index {
  (PrimaryKeyOnly[Grant] and WriteGateMechanism and GrantGateMechanism and ReadGateMechanism)
    implies WhoMayDelegateIsUnambiguous
} for 5
```

Create `models/alloy/mutants/node_grants__reads_narrowed.als`:

```alloy
/*
 * MUTANT — expected outcome: counterexample.
 *
 * Shadows: node_grants.als :: grants_never_narrow_reads
 * Simulated bug: list_nodes / node_detail filter by node_grants "for
 * tidiness" — the second read model the design forbids. A member with no
 * grant no longer sees a node the backend ACL says they may see.
 */
module knovas_platform/mutants/node_grants__reads_narrowed

open knovas_platform/node_grants

pred ReadGateNarrowedByGrants {
  all r: ReadAttempt | some r.rServed iff
    (r.rNode in BackendVisible and mayWrite[r.rUser, r.rNode])
}

check reader_without_a_grant_is_withheld {
  (GrantTableShape[Grant] and WriteGateMechanism and GrantGateMechanism and ReadGateNarrowedByGrants)
    implies GrantsNeverNarrowReads
} for 5
```

Create `models/alloy/mutants/node_grants__revoke_ignores_role.als`:

```alloy
/*
 * MUTANT — expected outcome: counterexample.
 *
 * Shadows: node_grants_lifecycle.als :: the_owner_survives_a_revoke
 * Simulated bug: NodeGrantStore.revoke deletes by (node_id, user_id) without
 * the `AND role = 'editor'` predicate. An owner can then be revoked — by
 * themselves or by an admin who meant to remove an editor — leaving a node
 * nobody may grant on.
 */
module knovas_platform/mutants/node_grants__revoke_ignores_role

open knovas_platform/node_grants_lifecycle

pred RevokeWithoutRoleFilter {
  some Revoke implies
    Post.rows = Pre.rows - { g: Grant | g.gNode = Revoke.rvNode and g.gUser = Revoke.rvUser }
}

check owner_lost_when_revoke_ignores_role {
  (GrantTableShape[Pre.rows] and OneMutation and RevokeWithoutRoleFilter and CreateMechanism
    and ((no Revoke and no Create) implies Post.rows = Pre.rows))
    implies TheOwnerSurvivesARevoke
} for 5
```

- [ ] **Step 4: Vendor the runner and generate the lockfile**

```bash
mkdir -p models/alloy/ci models/alloy/.cache
KB=../../../../KnowledgeBase/knovas-software/models/alloy   # adjust to where the KnowledgeBase checkout lives
cp "$KB/ci/alloy_driver.py" "$KB/ci/run_all.sh" "$KB/ci/alloy.version" models/alloy/ci/
cd models/alloy
curl -fsSL -o .cache/alloy.jar "$(sed -n 2p ci/alloy.version)"
python3 ci/alloy_driver.py --emit-expected > ci/expected_results.json
bash ci/run_all.sh
```

Expected: `alloy-checks: ok`. `ci/expected_results.json` holds 13 checks (9 `no_counterexample` in the two models, 4 `counterexample` under `mutants/`) and 5 `satisfiable` runs. The driver's rules apply unchanged: a check under `mutants/` must find a counterexample, every file with checks must carry a `run` witness, and results must match the lockfile byte for byte. Add `KnovasPlatform/components/docbridge_integration/models/alloy/.cache/` and `…/.out/` to the repository `.gitignore`.

- [ ] **Step 5: Write the README**

Create `models/alloy/README.md`:

```markdown
# Platform Alloy models

Formal models for Platform-side authorisation, run exactly like
`KnowledgeBase/knovas-software/models/alloy/` (same driver, same lockfile
rules — `ci/alloy_driver.py` is a verbatim copy; do not edit it here, update
it from KnowledgeBase). Idiom guide: that tree's `README.md`.

| File | Pins |
| --- | --- |
| `node_grants.als` | who may write, who may grant, reads never narrowed |
| `node_grants_lifecycle.als` | the owner survives a revoke; the creator owns |
| `mutants/node_grants__*.als` | one refuting weakening per conjunct |

Run: `bash ci/run_all.sh` (jar per `ci/alloy.version` into `.cache/`).
Regenerate the lockfile after an intended change:
`python3 ci/alloy_driver.py --emit-expected > ci/expected_results.json`.
Pytest half of the contract: `tests/test_node_grants_alloy.py`.
```

- [ ] **Step 6: Register the markers and write the pin test**

In `pyproject.toml`, `[tool.pytest.ini_options] markers`, add:

```toml
    "alloy: pins an Alloy model's commands and outcomes (the pytest half of must-agree)",
    "precondition: verifies the implementation satisfies an Alloy mechanism pred",
```

Create `tests/test_node_grants_alloy.py`:

```python
"""Alloy pins — node grants (SS-315, plan C0).

The pytest half of the must-agree contract, mirroring KnowledgeBase's
tests/alloy_invariants/test_kg_v1_alloy_pins.py: every command in the models is
registered in models/alloy/ci/expected_results.json with the right outcome, so a
silently dropped check or a mutant that stopped refuting fails pytest as well as
the Alloy CI step.
"""
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.alloy

MODELS = Path(__file__).resolve().parents[1] / "models" / "alloy"

# file (relative to models/alloy) -> {command: kind}, kind in {check, run}
COMMANDS = {
    "node_grants.als": {
        "an_editor_cannot_delegate": "check",
        "who_may_delegate_is_unambiguous": "check",
        "grants_never_narrow_reads": "check",
        "write_needs_a_grant_or_admin": "check",
        "an_admin_always_writes": "check",
        "witness_mechanism_live": "run",
        "witness_breach_expressible": "run",
    },
    "node_grants_lifecycle.als": {
        "the_owner_survives_a_revoke": "check",
        "a_revoke_removes_only_the_named_editor": "check",
        "the_creator_owns_the_new_node": "check",
        "the_table_shape_is_preserved": "check",
        "witness_revoke_of_an_editor": "run",
        "witness_create_makes_an_owner": "run",
        "witness_breach_expressible": "run",
    },
}
MUTANTS = {
    "mutants/node_grants__editor_delegates.als": "editor_delegates_when_grant_gate_is_may_write",
    "mutants/node_grants__two_owners.als": "two_owners_without_the_partial_index",
    "mutants/node_grants__reads_narrowed.als": "reader_without_a_grant_is_withheld",
    "mutants/node_grants__revoke_ignores_role.als": "owner_lost_when_revoke_ignores_role",
}
_CMD = re.compile(r"^\s*(run|check)\s+(\w+)\b", re.MULTILINE)


def _expected():
    path = MODELS / "ci" / "expected_results.json"
    assert path.is_file(), f"missing {path}"
    return json.loads(path.read_text(encoding="utf-8"))


class TestCommandsRegistered:
    def test_every_command_is_pinned_with_its_outcome(self):
        data = _expected()
        checks = {(c["file"], c["command"]): c["outcome"] for c in data["checks"]}
        runs = {(r["file"], r["command"]): r["outcome"] for r in data["runs"]}
        problems = []
        for fname, commands in COMMANDS.items():
            for cmd, kind in commands.items():
                table, want = (checks, "no_counterexample") if kind == "check" else (runs, "satisfiable")
                got = table.get((f"models/alloy/{fname}", cmd))
                if got != want:
                    problems.append(f"{fname}::{cmd} = {got!r} (want {want!r})")
        for fname, cmd in MUTANTS.items():
            got = checks.get((f"models/alloy/{fname}", cmd))
            if got != "counterexample":
                problems.append(f"{fname}::{cmd} = {got!r} (want 'counterexample': the mutant must refute)")
        assert not problems, problems

    def test_no_command_on_disk_is_unpinned(self):
        for fname, commands in COMMANDS.items():
            text = (MODELS / fname).read_text(encoding="utf-8")
            on_disk = {m.group(2) for m in _CMD.finditer(text)}
            assert on_disk == set(commands), f"{fname}: disk {sorted(on_disk)} != pinned {sorted(commands)}"

    def test_every_mutant_exists(self):
        gone = [f for f in MUTANTS if not (MODELS / f).is_file()]
        assert not gone, gone


class TestHeadersTrace:
    def test_models_name_the_plan_and_the_code(self):
        for fname in COMMANDS:
            text = (MODELS / fname).read_text(encoding="utf-8")
            assert "2026-09-02-typed-node-workbench-components.md" in text, fname
            assert "@code_under_check" in text and "node_grants.py" in text, fname
```

Run: `pytest tests/test_node_grants_alloy.py -v`
Expected: 4 passed.

- [ ] **Step 7: Add the CI step**

In `.github/workflows/ci.yml`, job `knovas-platform`, after the `Pytest` step:

```yaml
      - name: Set up Java 17 (Alloy)
        uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "17"

      - name: Alloy formal models (node grants)
        working-directory: KnovasPlatform/components/docbridge_integration/models/alloy
        run: |
          set -euo pipefail
          mkdir -p .cache
          [[ -f .cache/alloy.jar ]] || curl -fsSL -o .cache/alloy.jar "$(sed -n 2p ci/alloy.version)"
          bash ci/run_all.sh
```

- [ ] **Step 8: Commit**

```bash
git add models/alloy pyproject.toml tests/test_node_grants_alloy.py \
        ../../../.github/workflows/ci.yml ../../../.gitignore
git commit -m "alloy(identity): node grant rules modelled before the store (SS-315)

Owner/editor/admin write gate, owner-only delegation, one owner per node,
reads never narrowed by grants, owner survives revoke. Four mutants refute.
First Alloy tree in this repo; driver and lockfile discipline copied from
KnowledgeBase."
```

---

### Task C1: `node_grants` table and store

**Depends on the identity stack being on the branch** (Global Constraints).
Verify before starting:

```bash
ls src/identity/users.py src/identity/db.py src/identity/migrate.py src/identity/webauth.py \
   src/identity/migrations/0001_identity.sql src/web_interface/admin.py
grep -n 'def platform_db\|def identity_repo\|def identity_app' tests/conftest.py
```

All six files and all three fixtures must exist. If they do not, stop — this
task and everything after it have no `users` table to reference.

Mirrors `models/alloy/node_grants.als` (`GrantTableShape`, `mayWrite`) and
`node_grants_lifecycle.als` (`RevokeMechanism`, `CreateMechanism`); the
comments below name the pred each method implements.

**Files:**
- Create: `src/identity/migrations/0002_node_grants.sql`
- Create: `src/identity/node_grants.py`
- Test: `tests/test_node_grants.py`

**Interfaces:**
- Consumes: `identity.db` connection, `identity.users.UserRepository`.
- Produces: `class NodeGrantStore` with
  - `set_owner(node_id, user_id) -> None` — idempotent; used at node creation.
  - `grant_editor(node_id, user_id, granted_by) -> None` — no-op if already owner.
  - `revoke(node_id, user_id) -> None` — refuses to revoke the owner (`OwnerRevokeError`).
  - `for_node(node_id) -> dict` → `{"owner": user_id | None, "editors": [user_id, ...]}`
  - `may_write(node_id, user) -> bool` — True for the owner, an editor, or any user with the `admin` platform role.
  - `class OwnerRevokeError(Exception)`

- [ ] **Step 1: Write the migration**

Create `src/identity/migrations/0002_node_grants.sql`:

```sql
-- Who may EDIT a knowledge-graph node (SS-315).
--
-- Deliberately not a second read model. Read visibility is decided by the
-- backend ACL (GI-GRAPH-12) and this table never narrows a listing; it answers
-- one question, "may this user write this node?", which the backend has no
-- concept of because it has no concept of a user.
--
-- node_id has no foreign key ON PURPOSE. It names a row in kg_nodes, in a
-- different database on the other side of the mTLS boundary. The Platform
-- cannot guarantee referential integrity here and must not pretend to: a grant
-- whose node was deleted is dead data, cleaned by reconciliation, never by a
-- constraint that cannot exist.
--
-- Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md (C1)

CREATE TABLE IF NOT EXISTS node_grants (
    node_id    UUID        NOT NULL,
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       VARCHAR(8)  NOT NULL CHECK (role IN ('owner', 'editor')),
    granted_by UUID        NULL REFERENCES users(id) ON DELETE SET NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, user_id)
);

-- One owner per node. A second owner would make "who may grant editors?" have
-- two answers, and transfer is an admin action rather than a race.
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_grants_one_owner
    ON node_grants (node_id) WHERE role = 'owner';

CREATE INDEX IF NOT EXISTS idx_node_grants_user ON node_grants (user_id);
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_node_grants.py`:

```python
"""Per-user write grants on graph nodes (SS-315, plan C1).

The rules in one place: the creator owns; the owner grants and revokes editors;
an admin overrides both; and nothing here decides who may READ, which is the
backend's ACL.

Alloy: models/alloy/node_grants.als (WriteGateMechanism, GrantTableShape) and
models/alloy/node_grants_lifecycle.als (RevokeMechanism, CreateMechanism).
"""
import uuid

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = [
    pytest.mark.precondition,
    pytest.mark.skipif(not platform_db_reachable(),
                       reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}"),
]

from identity.node_grants import NodeGrantStore, OwnerRevokeError  # noqa: E402

PASSWORD = "korrektes-pferd-batterie"


class FakeUser:
    """A principal as may_write sees it: an id and the platform roles."""

    def __init__(self, roles=frozenset()):
        self.id = uuid.uuid4()
        self.roles = frozenset(roles)


@pytest.fixture
def store(platform_db):
    """platform_db comes from conftest: a migrated per-test schema."""
    return NodeGrantStore(platform_db)


@pytest.fixture
def alice(identity_repo):
    return identity_repo.create(email="alice@kanzlei.ch", display_name="Alice",
                                password=PASSWORD)


@pytest.fixture
def bob(identity_repo):
    return identity_repo.create(email="bob@kanzlei.ch", display_name="Bob",
                                password=PASSWORD)


class TestOwnership:
    def test_the_creator_becomes_the_owner(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert store.for_node(node)["owner"] == str(alice.id)

    def test_setting_the_owner_twice_is_idempotent(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        store.set_owner(node, alice.id)
        assert store.for_node(node)["editors"] == []

    def test_the_owner_may_write(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert store.may_write(node, alice)


class TestEditors:
    def test_an_editor_may_write(self, store, alice, bob):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        store.grant_editor(node, bob.id, granted_by=alice.id)
        assert store.may_write(node, bob)

    def test_a_stranger_may_not_write(self, store, alice, bob):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert not store.may_write(node, bob)

    def test_granting_editor_to_the_owner_does_not_demote_them(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        store.grant_editor(node, alice.id, granted_by=alice.id)
        assert store.for_node(node)["owner"] == str(alice.id)

    def test_revoking_removes_the_write_right(self, store, alice, bob):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        store.grant_editor(node, bob.id, granted_by=alice.id)
        store.revoke(node, bob.id)
        assert not store.may_write(node, bob)

    def test_the_owner_cannot_be_revoked(self, store, alice):
        """Otherwise a node ends up with nobody who may grant anything."""
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        with pytest.raises(OwnerRevokeError):
            store.revoke(node, alice.id)


class TestAdminOverride:
    def test_an_admin_may_write_any_node(self, store, alice):
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert store.may_write(node, FakeUser(roles={"admin"}))

    def test_an_admin_may_write_a_node_with_no_grants_at_all(self, store):
        """Nodes created before this feature have no owner. An admin must still
        be able to repair them."""
        assert store.may_write(str(uuid.uuid4()), FakeUser(roles={"admin"}))

    def test_a_member_may_not_write_an_ungranted_node(self, store, bob):
        assert not store.may_write(str(uuid.uuid4()), bob)


class TestDeadData:
    def test_a_grant_for_an_unknown_node_is_simply_inert(self, store, alice):
        """node_id has no FK by design; a grant whose node was deleted must not
        raise on read."""
        node = str(uuid.uuid4())
        store.set_owner(node, alice.id)
        assert store.for_node(str(uuid.uuid4())) == {"owner": None, "editors": []}
```

`platform_db` (a migrated schema of its own per test, dropped afterwards)
and `identity_repo` already exist in `tests/conftest.py`; `alice` and `bob`
are real `users` rows created through `UserRepository.create`, and the module
skips without PostgreSQL exactly like `tests/test_identity_users.py` does.

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/test_node_grants.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'identity.node_grants'`

- [ ] **Step 4: Implement**

Create `src/identity/node_grants.py`:

```python
"""Who may edit a knowledge-graph node.

This is write control only. Read visibility belongs to the backend ACL
(GI-GRAPH-12) and this module never narrows a listing — two systems answering
"may I see this?" is the failure mode the design exists to avoid.

Honest about its limit: these grants are enforced by the Platform's own routes.
Anything holding the tenant certificate and calling /secured/graph directly
bypasses them. That is not new here — principal_resolver.py states the same
boundary for RBAC itself — but it must be described to buyers as a control over
who may edit through the product, not a cryptographic guarantee.

Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md (C1)
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

OWNER = "owner"
EDITOR = "editor"


class OwnerRevokeError(Exception):
    """The owner's grant cannot be revoked, only transferred by an admin."""


class NodeGrantStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def set_owner(self, node_id: str, user_id: UUID | str) -> None:
        """CreateMechanism: record the creator. Idempotent; a second owner on
        the same node hits idx_node_grants_one_owner and raises, which is the
        right answer — transfer is an admin action, not a race."""
        self._conn.execute(
            "INSERT INTO node_grants (node_id, user_id, role, granted_by) "
            "VALUES (%s, %s, 'owner', %s) "
            "ON CONFLICT (node_id, user_id) DO UPDATE SET role = 'owner'",
            (str(node_id), str(user_id), str(user_id)),
        )

    def grant_editor(self, node_id: str, user_id: UUID | str,
                     granted_by: UUID | str | None = None) -> None:
        """Add an editor. A no-op for the owner, who already outranks it."""
        self._conn.execute(
            "INSERT INTO node_grants (node_id, user_id, role, granted_by) "
            "VALUES (%s, %s, 'editor', %s) "
            "ON CONFLICT (node_id, user_id) DO NOTHING",
            (str(node_id), str(user_id),
             str(granted_by) if granted_by is not None else None),
        )

    def revoke(self, node_id: str, user_id: UUID | str) -> None:
        """RevokeMechanism: DELETE ... AND role = 'editor' — the owner row is
        never touched, so a node cannot end up with nobody who may grant."""
        removed = self._conn.execute(
            "DELETE FROM node_grants WHERE node_id = %s AND user_id = %s "
            "AND role = 'editor' RETURNING user_id",
            (str(node_id), str(user_id)),
        ).fetchall()
        if not removed:
            # Either they were never an editor, or they are the owner. Only the
            # second is an error worth a message: silently succeeding would let
            # an admin believe they removed access they did not.
            if self.for_node(node_id)["owner"] == str(user_id):
                raise OwnerRevokeError(
                    "Die Eigentümerschaft kann nur übertragen, nicht entzogen werden.")

    def for_node(self, node_id: str) -> dict:
        # psycopg 3 tuple rows, indexed by position like identity/users.py.
        rows = self._conn.execute(
            "SELECT user_id, role FROM node_grants WHERE node_id = %s "
            "ORDER BY role, granted_at",
            (str(node_id),),
        ).fetchall()
        owner = next((str(user_id) for user_id, role in rows if role == OWNER), None)
        editors = [str(user_id) for user_id, role in rows if role == EDITOR]
        return {"owner": owner, "editors": editors}

    def may_write(self, node_id: str, user: Any) -> bool:
        """mayWrite: the owner, an editor, or any admin.

        An admin passes even when a node has no grants at all: nodes created
        before this feature have no owner, and somebody has to be able to
        repair them (an_admin_always_writes).
        """
        if user is None:
            return False
        if "admin" in getattr(user, "roles", frozenset()):
            return True
        grants = self.for_node(node_id)
        return str(user.id) == grants["owner"] or str(user.id) in grants["editors"]

    def nodes_for_user(self, user_id: UUID | str) -> list[str]:
        """Node ids this user owns or edits. For an account-deletion review."""
        rows = self._conn.execute(
            "SELECT node_id FROM node_grants WHERE user_id = %s", (str(user_id),)
        ).fetchall()
        return [str(node_id) for (node_id,) in rows]
```

`identity/db.py` returns a plain psycopg 3 connection (autocommit); `conn.execute(...)` returns a cursor whose `fetchall()` yields tuples — the same idiom `identity/users.py` uses. Do not add a dict row factory for this store alone.

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/test_node_grants.py -v`
Expected: all pass.

- [ ] **Step 6: Verify the migration applies**

Run (as CI does, with `PLATFORM_DB_DSN` pointing at the local `platform-db`): `python -m src.identity.migrate`
Expected: `0002_node_grants.sql` applies once and is a no-op on a second run. The migration runner discovers `NNNN_slug.sql` files by name, so the number must be `0002`.

- [ ] **Step 7: Commit**

```bash
git add src/identity/node_grants.py src/identity/migrations/0002_node_grants.sql \
        tests/test_node_grants.py tests/conftest.py
git commit -m "feat(identity): per-user owner/editor grants on graph nodes (SS-315)"
```

---

### Task C2: The `may_write` route guard

**Files:**
- Create: `src/web_interface/graph_routes.py` (the blueprint factory and the decorator only; routes arrive in D1–D3)
- Test: `tests/test_graph_routes_auth.py`

**Interfaces:**
- Consumes: `NodeGrantStore.may_write` (C1); `IdentityGate` from `src/identity/webauth.py` — `current_user()`, `users()`, and `connection()`, the request-scoped psycopg connection that `app.teardown_request(identity_gate.close)` closes. `IdentityGate.guard` already answers 401 (JSON) for any `/api/` path without a session, so the blueprint's own `require_user` is a second, explicit line rather than the first.
- Produces: `create_graph_blueprint(gate, grant_store, source, *, graph_mode) -> Blueprint` registered at `/api/graph` — `grant_store` is a zero-argument callable returning a `NodeGrantStore` over the **current request's** connection (a store built once at app start would hold a connection the first request's teardown closes) — plus four decorators used by every later task:
  - `@require_user` — 401 JSON for an unauthenticated caller.
  - `@require_admin` — 403 JSON when `admin` is not among the user's roles.
  - `@require_node_write(param="node_id")` — 403 JSON when `grants.may_write` is False.
  - `@require_graph_mode` — 409 JSON `{"error": "Wissensnetz-Modus erforderlich"}` in fixture mode.

- [ ] **Step 0: Add the workbench fixtures to `tests/conftest.py`**

Every route test in C2–E1 runs the real Flask app in identity mode with
`ONTOLOGY_SOURCE=graph`, a fake Knowledge Graph client, real `users` rows and a
real `node_grants` table. First, make the existing `identity_app` fixture
reusable: move its body into a helper `_identity_app(platform_db, tmp_path, monkeypatch, *, client_cls=DummyKnovasClient)` (identical code, except `monkeypatch.setattr(web_app, "KnovasAPIClient", client_cls)`) and let `identity_app` call it. Then append:

```python
# ── typed-node workbench (SS-315): graph mode, people, grants ─────────────

PASSWORD = "korrektes-pferd-batterie"


class FakeGraphApi(DummyKnovasClient):
    """The Knowledge Graph client as the workbench sees it, in memory.

    Instance state, seeded by tests through the `fake_graph` fixture; the app
    holds the same instance because create_app constructs exactly one client.
    Response shapes follow Knowledge_Graph_API.md (`{"node": …}`,
    `{"attribute": …}`, `{"neighbors": …, "edges": …}`).
    """

    current = None

    def __init__(self, config, *, principal_broker=None):
        super().__init__(config, principal_broker=principal_broker)
        self.node_types = [{"id": "t1", "name": "Mandat"}]
        self.schema = {}
        self.nodes = {"n1": {"id": "n1", "name": "Müller AG", "node_type_id": "t1"}}
        self.facts = {"n1": []}
        self.neighbours = {}
        self.last_attribute = self.last_node_filters = None
        self.last_fact = self.last_neighbours = None
        self.deprecated = []
        FakeGraphApi.current = self

    # node types + schema
    def graph_node_types(self):
        return list(self.node_types)

    def graph_create_node_type(self, name):
        created = {"id": f"t{len(self.node_types) + 1}", "name": name}
        self.node_types.append(created)
        return {"node_type": created}

    def graph_update_node_type(self, type_id, **fields):
        return {"node_type": {"id": type_id, **fields}}

    def graph_schema(self, type_id, include_deprecated=False):
        return list(self.schema.get(type_id, []))

    def graph_create_schema_attribute(self, type_id, name, datatype="entity_ref",
                                      required=False, description=None, sort_order=0,
                                      enum_values=None, target_node_type_id=None):
        attribute = {"id": f"a{sum(len(v) for v in self.schema.values()) + 1}",
                     "name": name, "datatype": datatype, "required": required,
                     "sort_order": sort_order, "enum_values": enum_values,
                     "target_node_type_id": target_node_type_id}
        self.schema.setdefault(type_id, []).append(attribute)
        self.last_attribute = attribute
        return {"attribute": attribute}

    def graph_update_schema_attribute(self, type_id, attribute_id, **fields):
        return {"attribute": {"id": attribute_id, **fields}}

    def graph_deprecate_schema_attribute(self, type_id, attribute_id):
        self.deprecated.append((type_id, attribute_id))
        return {"status": "success"}

    # nodes
    def graph_nodes(self, node_type_id=None, q=None):
        self.last_node_filters = {k: v for k, v in (("node_type_id", node_type_id), ("q", q)) if v}
        return [n for n in self.nodes.values()
                if (not node_type_id or n.get("node_type_id") == node_type_id)
                and (not q or q.lower() in n["name"].lower())]

    def graph_create_node(self, name, node_type_id=None):
        node = {"id": f"n{len(self.nodes) + 1}", "name": name, "node_type_id": node_type_id}
        self.nodes[node["id"]] = node
        self.facts[node["id"]] = []
        return {"node": node}

    def graph_node(self, node_id):
        node = self.nodes.get(node_id)
        return None if node is None else {"node": node, "facts": self.facts.get(node_id, [])}

    def graph_update_node(self, node_id, **fields):
        if node_id not in self.nodes:
            return None
        self.nodes[node_id].update(fields)
        return {"node": self.nodes[node_id]}

    # facts + neighbours
    def graph_facts(self, node_id):
        return list(self.facts.get(node_id, []))

    def graph_create_fact(self, node_id, value, attribute_id=None, label=None):
        if node_id not in self.nodes:
            return None
        fact = {"id": f"f{len(self.facts[node_id]) + 1}", "attribute_id": attribute_id,
                "label": label, "value": value}
        self.facts[node_id].append(fact)
        self.last_fact = fact
        return {"fact": fact}

    def graph_update_fact(self, fact_id, **fields):
        return {"fact": {"id": fact_id, **fields}}

    def graph_delete_fact(self, fact_id):
        return {"status": "success"}

    def graph_neighbors(self, node_id, depth=1, include_edges=False):
        self.last_neighbours = {"node_id": node_id, "depth": depth,
                                "include_edges": include_edges}
        return self.neighbours.get(node_id, {"neighbors": [], "edges": []})


class _ApiClient:
    """A signed-in test client that sends the session's CSRF token on every
    request, exactly as static/js/app.js does."""

    def __init__(self, client, token):
        self._client, self._token = client, token

    def open(self, *args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-CSRF-Token", self._token)
        return self._client.open(*args, headers=headers, **kwargs)

    def get(self, *a, **kw):
        return self.open(*a, method="GET", **kw)

    def post(self, *a, **kw):
        return self.open(*a, method="POST", **kw)

    def patch(self, *a, **kw):
        return self.open(*a, method="PATCH", **kw)

    def delete(self, *a, **kw):
        return self.open(*a, method="DELETE", **kw)


def _csrf_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def _signed_in(app, email, *, with_csrf=True):
    client = app.test_client()
    page = client.get("/login")
    client.post("/login", data={"login_name": email, "password": PASSWORD,
                                "csrf_token": _csrf_from(page.data.decode("utf-8"))})
    if not with_csrf:
        return client
    with client.session_transaction() as sess:
        token = sess["csrf_token"]
    return _ApiClient(client, token)


def _person(identity_repo, email, display_name, role):
    user = identity_repo.create(email=email, display_name=display_name, password=PASSWORD)
    identity_repo.grant_role(user.id, role)
    return identity_repo.get(user.id)


@pytest.fixture
def workbench_app(platform_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")
    return _identity_app(platform_db, tmp_path, monkeypatch, client_cls=FakeGraphApi)


@pytest.fixture
def fixture_mode_app(platform_db, tmp_path, monkeypatch):
    monkeypatch.delenv("ONTOLOGY_SOURCE", raising=False)
    return _identity_app(platform_db, tmp_path, monkeypatch, client_cls=FakeGraphApi)


@pytest.fixture
def fake_graph(workbench_app):
    return FakeGraphApi.current


@pytest.fixture
def grants(platform_db):
    from identity.node_grants import NodeGrantStore

    return NodeGrantStore(platform_db)


@pytest.fixture
def alice(identity_repo):
    return _person(identity_repo, "alice@kanzlei.ch", "Alice", "member")


@pytest.fixture
def bob(identity_repo):
    return _person(identity_repo, "bob@kanzlei.ch", "Bob", "member")


@pytest.fixture
def carol(identity_repo):
    return _person(identity_repo, "carol@kanzlei.ch", "Carol", "member")


@pytest.fixture
def member(identity_repo):
    return _person(identity_repo, "mia@kanzlei.ch", "Mia", "member")


@pytest.fixture
def platform_admin(identity_repo):
    return _person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")


@pytest.fixture
def anon_client(workbench_app):
    return workbench_app.test_client()


@pytest.fixture
def member_client(workbench_app, member):
    return _signed_in(workbench_app, member.email)


@pytest.fixture
def alice_client(workbench_app, alice):
    return _signed_in(workbench_app, alice.email)


@pytest.fixture
def bob_client(workbench_app, bob):
    return _signed_in(workbench_app, bob.email)


@pytest.fixture
def admin_client(workbench_app, platform_admin):
    return _signed_in(workbench_app, platform_admin.email)


@pytest.fixture
def admin_client_no_csrf(workbench_app, platform_admin):
    return _signed_in(workbench_app, platform_admin.email, with_csrf=False)


@pytest.fixture
def fixture_mode_client(fixture_mode_app, member):
    return _signed_in(fixture_mode_app, member.email)


@pytest.fixture
def node_owned_by_alice(fake_graph, grants, alice):
    node = fake_graph.graph_create_node("Alices Akte", node_type_id="t1")["node"]
    grants.set_owner(node["id"], alice.id)
    return node["id"]
```

`grants` and the app reach the same schema: the fixture holds its own
connection, the app opens fresh ones through the `PLATFORM_DB_DSN` search path
`identity_app` already sets, and both are autocommit — the same arrangement
`identity_repo` and `identity_client` already rely on.

- [ ] **Step 1: Write the failing tests**

Every new route test module (`tests/test_graph_routes_*.py`,
`tests/test_workbench_page.py`, `tests/test_graph_workbench.py`) needs
PostgreSQL and starts with the same three lines as `tests/test_identity_users.py`:

```python
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")
```

Create `tests/test_graph_routes_auth.py`:

```python
"""Authorisation on /api/graph/*, asserted on the route rather than the link.

Hiding a control is presentation; refusing the request is the control. Every
test here calls the endpoint directly for that reason.

Alloy: models/alloy/node_grants.als (WriteGateMechanism, ReadGateMechanism).
"""
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestAuthentication:
    def test_an_anonymous_caller_gets_401(self, anon_client):
        assert anon_client.get("/api/graph/node-types").status_code == 401

    def test_a_member_may_read_the_type_list(self, member_client):
        assert member_client.get("/api/graph/node-types").status_code == 200


class TestAdminGate:
    def test_a_member_may_not_create_a_node_type(self, member_client):
        response = member_client.post("/api/graph/node-types", json={"name": "Mandat"})
        assert response.status_code == 403

    def test_an_admin_may_create_a_node_type(self, admin_client):
        response = admin_client.post("/api/graph/node-types", json={"name": "Mandat"})
        assert response.status_code == 201


class TestNodeWriteGate:
    def test_a_non_editor_may_not_patch_a_node(self, member_client, node_owned_by_alice):
        response = member_client.patch(f"/api/graph/nodes/{node_owned_by_alice}",
                                       json={"name": "Neu"})
        assert response.status_code == 403

    def test_the_owner_may_patch_their_node(self, alice_client, node_owned_by_alice):
        response = alice_client.patch(f"/api/graph/nodes/{node_owned_by_alice}",
                                      json={"name": "Neu"})
        assert response.status_code == 200

    def test_a_non_editor_may_still_read_it(self, member_client, node_owned_by_alice):
        """Read is the backend ACL's decision, never node_grants'."""
        assert member_client.get(
            f"/api/graph/nodes/{node_owned_by_alice}").status_code == 200


class TestCsrf:
    def test_a_state_changing_request_without_the_header_is_refused(
            self, admin_client_no_csrf):
        response = admin_client_no_csrf.post("/api/graph/node-types",
                                             json={"name": "Mandat"})
        assert response.status_code == 403


class TestFixtureMode:
    def test_every_graph_route_refuses_in_fixture_mode(self, fixture_mode_client):
        response = fixture_mode_client.get("/api/graph/node-types")
        assert response.status_code == 409
        assert response.get_json()["error"] == "Wissensnetz-Modus erforderlich"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_graph_routes_auth.py -v`
Expected: FAIL — 404 on every route; the blueprint does not exist.

- [ ] **Step 3: Implement the blueprint factory and decorators**

Create `src/web_interface/graph_routes.py`:

```python
"""The /api/graph/* namespace: schema-driven node types, nodes and facts.

A separate module rather than more routes in app.py, which is already ~1900
lines. Follows the blueprint-factory shape admin.py established: the app's own
helpers are passed in, so this module imports nothing from app.py and can be
tested against a minimal Flask app.

Authorisation is on the route, never on whether the UI draws the control.

Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md
"""
from __future__ import annotations

import functools
import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

_GENERIC_ERROR = "Ein Fehler ist aufgetreten."
_FIXTURE_MODE_ERROR = "Wissensnetz-Modus erforderlich"


def create_graph_blueprint(gate, grant_store, source, *, graph_mode):
    """Build the blueprint.

    Args:
        gate: the IdentityGate; ``gate.current_user()`` or None.
        grant_store: zero-arg callable -> NodeGrantStore over THIS request's
            connection (``gate.connection()`` is request-scoped).
        source: a callable returning the Knovas client for this request.
        graph_mode: a callable returning True when ONTOLOGY_SOURCE=graph.
    """
    bp = Blueprint("graph_api_ui", __name__, url_prefix="/api/graph")

    def require_graph_mode(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not graph_mode():
                # 409, not 500: nothing is broken. The deployment is in fixture
                # mode and the screen says so rather than inventing data.
                return jsonify({"success": False, "error": _FIXTURE_MODE_ERROR}), 409
            return view(*args, **kwargs)
        return wrapped

    def require_user(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate is None or gate.current_user() is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            return view(*args, **kwargs)
        return wrapped

    def require_admin(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = gate.current_user() if gate else None
            if user is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            if "admin" not in user.roles:
                # 403, not 404: the caller is authenticated and the schema
                # editor is not a secret. Hiding it would only make a
                # misconfigured account harder to diagnose.
                return jsonify({"success": False,
                                "error": "Nur für Administratoren."}), 403
            return view(*args, **kwargs)
        return wrapped

    def require_node_write(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = gate.current_user() if gate else None
            if user is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            node_id = kwargs.get("node_id")
            if not grant_store().may_write(node_id, user):
                return jsonify({"success": False,
                                "error": "Keine Bearbeitungsrechte für diesen Knoten."}), 403
            return view(*args, **kwargs)
        return wrapped

    bp.require_graph_mode = require_graph_mode      # exported for D1-D3
    bp.require_user = require_user
    bp.require_admin = require_admin
    bp.require_node_write = require_node_write
    return bp
```

Register it in `src/web_interface/app.py` inside the existing
`if identity_gate is not None:` block that registers the admin blueprint
(`create_admin_blueprint(...)`). `IdentityGate.connection()` opens this
request's connection on first use and `app.teardown_request(identity_gate.close)`
closes it, so the store must be built per request, never once at registration:

```python
        from identity.node_grants import NodeGrantStore
        from web_interface.graph_routes import create_graph_blueprint

        app.register_blueprint(create_graph_blueprint(
            identity_gate,
            lambda: NodeGrantStore(identity_gate.connection()),
            lambda: api_client,
            graph_mode=lambda: _ontology_source_is_graph(),
        ))
```

`api_client` is the `KnovasAPIClient` built a few hundred lines above (the
one `_ontology_source()` also wraps); `_ontology_source_is_graph` is a nested
function defined *later* in `create_app`, which is why it is wrapped in a
lambda — naming it directly at registration time raises `UnboundLocalError`.

- [ ] **Step 4: Run the auth tests that do not need routes yet**

Run: `pytest tests/test_graph_routes_auth.py::TestFixtureMode -v`
Expected: this class passes once D1 adds `GET /node-types`. Until then it
fails with 404 — that is expected and this task is complete when the decorators
exist and are importable. Mark the remaining classes with
`@pytest.mark.xfail(reason="routes arrive in D1-D3", strict=False)` and remove
the marks in D1 and D2.

- [ ] **Step 5: Commit**

```bash
git add src/web_interface/graph_routes.py src/web_interface/app.py \
        tests/test_graph_routes_auth.py
git commit -m "feat(web): /api/graph blueprint with the four authorisation gates (SS-315)"
```

---

### Task D1: Node-type and schema routes

**Files:**
- Modify: `src/web_interface/graph_routes.py`
- Test: `tests/test_graph_routes_schema.py`

**Interfaces:**
- Consumes: B3 client methods; C2 decorators.
- Produces:
  - `GET /api/graph/node-types` → `{"success": true, "node_types": [...]}`
  - `POST /api/graph/node-types` (admin) → 201 `{"node_type": {...}}`
  - `GET /api/graph/node-types/<type_id>/schema` → `{"attributes": [...]}`
  - `POST /api/graph/node-types/<type_id>/schema` (admin) → 201 `{"attribute": {...}}`
  - `PATCH /api/graph/node-types/<type_id>/schema/<attribute_id>` (admin) → `{"attribute": {...}}`
  - `DELETE /api/graph/node-types/<type_id>/schema/<attribute_id>` (admin) → `{"deprecated": true}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_routes_schema.py`:

```python
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestNodeTypes:
    def test_listing_types(self, member_client, fake_graph):
        fake_graph.node_types = [{"id": "t1", "name": "Mandat"}]
        body = member_client.get("/api/graph/node-types").get_json()
        assert body["node_types"][0]["name"] == "Mandat"

    def test_creating_a_type_without_a_name_is_400(self, admin_client):
        assert admin_client.post("/api/graph/node-types", json={}).status_code == 400


class TestSchema:
    def test_reading_a_schema_returns_attributes_in_sort_order(self, member_client,
                                                               fake_graph):
        fake_graph.schema["t1"] = [
            {"id": "a2", "name": "Frist", "datatype": "date", "sort_order": 1},
            {"id": "a1", "name": "Titel", "datatype": "text", "sort_order": 0},
        ]
        body = member_client.get("/api/graph/node-types/t1/schema").get_json()
        assert [a["name"] for a in body["attributes"]] == ["Titel", "Frist"]

    def test_creating_an_attribute_with_an_unknown_datatype_is_400(self, admin_client):
        response = admin_client.post("/api/graph/node-types/t1/schema",
                                     json={"name": "X", "datatype": "timestamp"})
        assert response.status_code == 400

    def test_an_enum_without_values_is_400(self, admin_client):
        response = admin_client.post("/api/graph/node-types/t1/schema",
                                     json={"name": "Status", "datatype": "enum"})
        assert response.status_code == 400

    def test_an_entity_ref_may_carry_a_target_type(self, admin_client, fake_graph):
        response = admin_client.post(
            "/api/graph/node-types/t1/schema",
            json={"name": "Zustaendig", "datatype": "entity_ref",
                  "target_node_type_id": "t2"})
        assert response.status_code == 201
        assert fake_graph.last_attribute["target_node_type_id"] == "t2"

    def test_delete_deprecates_and_says_so(self, admin_client, fake_graph):
        body = admin_client.delete(
            "/api/graph/node-types/t1/schema/a1").get_json()
        assert body["deprecated"] is True
        assert fake_graph.deprecated == [("t1", "a1")]

    def test_a_member_may_not_deprecate_an_attribute(self, member_client):
        assert member_client.delete(
            "/api/graph/node-types/t1/schema/a1").status_code == 403
```

`fake_graph` is the `FakeGraphApi` instance from C2 Step 0 — the same object
the app's `source()` returns, so seeding `fake_graph.schema["t1"]` is seen by
the route.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_graph_routes_schema.py -v`
Expected: FAIL with 404 on every route.

- [ ] **Step 3: Implement**

Inside `create_graph_blueprint`, before the `return bp`:

```python
    def _fail(exc, message):
        logger.error(message, exc_info=True)
        return jsonify({"success": False, "error": _GENERIC_ERROR}), 500

    @bp.route("/node-types", methods=["GET"])
    @require_graph_mode
    @require_user
    def list_node_types():
        try:
            return jsonify({"success": True,
                            "node_types": source().graph_node_types()})
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node-type list failed")

    @bp.route("/node-types", methods=["POST"])
    @require_graph_mode
    @require_admin
    def create_node_type():
        payload = request.get_json(silent=True) or {}
        name = " ".join(str(payload.get("name") or "").split())
        if not name:
            return jsonify({"success": False, "error": "Name fehlt."}), 400
        try:
            created = source().graph_create_node_type(name)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node-type create failed")
        if created is None:
            return jsonify({"success": False, "error": "Typ nicht anlegbar."}), 400
        return jsonify({"success": True, "node_type": created}), 201

    @bp.route("/node-types/<type_id>/schema", methods=["GET"])
    @require_graph_mode
    @require_user
    def read_schema(type_id):
        try:
            attributes = source().graph_schema(type_id)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph schema read failed")
        # The API orders by (sort_order, name); re-sorting here keeps the
        # contract explicit for a client that must render fields in order.
        attributes.sort(key=lambda a: (a.get("sort_order", 0), a.get("name", "")))
        return jsonify({"success": True, "attributes": attributes})

    @bp.route("/node-types/<type_id>/schema", methods=["POST"])
    @require_graph_mode
    @require_admin
    def create_attribute(type_id):
        from graph_model import DATATYPES

        payload = request.get_json(silent=True) or {}
        name = " ".join(str(payload.get("name") or "").split())
        datatype = str(payload.get("datatype") or "").strip()
        enum_values = payload.get("enum_values")
        if not name:
            return jsonify({"success": False, "error": "Name fehlt."}), 400
        if datatype not in DATATYPES:
            return jsonify({"success": False,
                            "error": "Unbekannter Datentyp."}), 400
        if datatype == "enum" and not isinstance(enum_values, list):
            return jsonify({"success": False,
                            "error": "Auswahlfeld braucht Werte."}), 400
        try:
            created = source().graph_create_schema_attribute(
                type_id, name, datatype=datatype,
                required=bool(payload.get("required", False)),
                description=payload.get("description"),
                sort_order=int(payload.get("sort_order", 0)),
                enum_values=enum_values,
                target_node_type_id=payload.get("target_node_type_id"))
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph attribute create failed")
        if created is None:
            return jsonify({"success": False, "error": "Typ nicht gefunden."}), 404
        return jsonify({"success": True,
                        "attribute": created.get("attribute", created)}), 201

    @bp.route("/node-types/<type_id>/schema/<attribute_id>", methods=["PATCH"])
    @require_graph_mode
    @require_admin
    def update_attribute(type_id, attribute_id):
        payload = request.get_json(silent=True) or {}
        fields = {k: payload[k] for k in
                  ("name", "description", "required", "sort_order", "enum_values",
                   "target_node_type_id") if k in payload}
        if not fields:
            return jsonify({"success": False, "error": "Keine Änderung."}), 400
        try:
            updated = source().graph_update_schema_attribute(
                type_id, attribute_id, **fields)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph attribute update failed")
        if updated is None:
            return jsonify({"success": False, "error": "Attribut nicht gefunden."}), 404
        return jsonify({"success": True,
                        "attribute": updated.get("attribute", updated)})

    @bp.route("/node-types/<type_id>/schema/<attribute_id>", methods=["DELETE"])
    @require_graph_mode
    @require_admin
    def deprecate_attribute(type_id, attribute_id):
        """Deprecation, not deletion: existing facts keep their attribute_id.
        The response says `deprecated` so the UI cannot accidentally word it
        as a delete."""
        try:
            result = source().graph_deprecate_schema_attribute(type_id, attribute_id)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph attribute deprecate failed")
        if result is None:
            return jsonify({"success": False, "error": "Attribut nicht gefunden."}), 404
        return jsonify({"success": True, "deprecated": True})
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_graph_routes_schema.py -v`
Expected: all pass.

- [ ] **Step 5: Remove the xfail marks from the admin-gate tests**

Delete the `@pytest.mark.xfail` on `TestAdminGate` and `TestFixtureMode` in
`tests/test_graph_routes_auth.py`.

Run: `pytest tests/test_graph_routes_auth.py -v`
Expected: `TestAdminGate`, `TestFixtureMode`, `TestCsrf` pass;
`TestNodeWriteGate` still xfails until D2.

- [ ] **Step 6: Commit**

```bash
git add src/web_interface/graph_routes.py tests/test_graph_routes_schema.py \
        tests/test_graph_routes_auth.py
git commit -m "feat(web): node-type and schema routes, admin-gated (SS-315)"
```

---

### Task D2: `graph_workbench.py` composer and the node routes

**Files:**
- Create: `src/graph_workbench.py`
- Modify: `src/web_interface/graph_routes.py`
- Test: `tests/test_graph_workbench.py`, `tests/test_graph_routes_nodes.py`

**Interfaces:**
- Consumes: B3, B4, C1, C2, D1.
- Produces:
  - `compose_node(client, grants, node_id) -> dict`:
    ```python
    {
      "node": {...},                       # from GET /nodes/<id>
      "fields": [                          # facts joined to attribute definitions
        {"attribute_id": str | None, "name": str, "datatype": str,
         "required": bool, "sort_order": int, "fact_id": str | None,
         "value": Any, "display": str, "missing": bool}
      ],
      "neighbourhood": {"nodes": [...], "edges": [...]},
      "grants": {"owner": str | None, "editors": [str]},
      "visibility": {"access_group_ids": [str]}
    }
    ```
  - `GET /api/graph/nodes?type=&q=` → `{"nodes": [...]}`
  - `POST /api/graph/nodes` → 201 `{"node": {...}}`; writes the creator as owner
  - `GET /api/graph/nodes/<node_id>` → the composed payload
  - `PATCH /api/graph/nodes/<node_id>` (owner|editor|admin) → `{"node": {...}}`

- [ ] **Step 1: Write the failing composer tests**

Create `tests/test_graph_workbench.py`:

```python
"""One screen, one payload.

The join between facts and attribute definitions happens here rather than in
the browser: the field reader must show an attribute that has NO fact (the
visible gap), which a fact-only response cannot express.
"""
import pytest

from conftest import FakeGraphApi, PLATFORM_DB_TEST_DSN, platform_db_reachable
from graph_workbench import compose_node

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


@pytest.fixture
def client():
    """The composer takes any object with the client's graph_* methods."""
    return FakeGraphApi(config=None)


class TestFieldJoin:
    def test_a_filled_attribute_carries_its_fact(self, client, grants):
        client.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                "required": True, "sort_order": 0}]
        client.facts["n1"] = [{"id": "f1", "attribute_id": "a1",
                               "value": {"value": "2026-03-04", "precision": "day"}}]
        payload = compose_node(client, grants, "n1")
        field = payload["fields"][0]
        assert field["fact_id"] == "f1"
        assert field["display"] == "04.03.2026"
        assert field["missing"] is False

    def test_a_required_attribute_with_no_fact_is_a_visible_gap(self, client, grants):
        """The completeness report exists to count these. They are shown, never
        treated as an error."""
        client.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                "required": True, "sort_order": 0}]
        client.facts["n1"] = []
        field = compose_node(client, grants, "n1")["fields"][0]
        assert field["missing"] is True and field["required"] is True
        assert field["value"] is None

    def test_fields_follow_sort_order(self, client, grants):
        client.schema["t1"] = [
            {"id": "a2", "name": "Frist", "datatype": "date", "sort_order": 1},
            {"id": "a1", "name": "Titel", "datatype": "text", "sort_order": 0}]
        names = [f["name"] for f in compose_node(client, grants, "n1")["fields"]]
        assert names == ["Titel", "Frist"]

    def test_a_free_form_fact_appears_after_the_schema_fields(self, client, grants):
        """attribute_id is NULL for a fact typed in without a definition. It is
        real content and must not vanish because the schema does not name it."""
        client.schema["t1"] = [{"id": "a1", "name": "Titel", "datatype": "text",
                                "sort_order": 0}]
        client.facts["n1"] = [{"id": "f9", "attribute_id": None,
                               "label": "Notiz", "value": "frei"}]
        fields = compose_node(client, grants, "n1")["fields"]
        assert fields[-1]["name"] == "Notiz" and fields[-1]["attribute_id"] is None

    def test_a_fact_for_a_deprecated_attribute_still_renders(self, client, grants):
        """Deprecation keeps facts. Dropping them here would make the UI lie
        about what the node contains."""
        client.schema["t1"] = []
        client.facts["n1"] = [{"id": "f1", "attribute_id": "a-old",
                               "label": "Altfeld", "value": "Wert"}]
        assert len(compose_node(client, grants, "n1")["fields"]) == 1

    def test_a_node_without_a_type_has_no_schema_fields(self, client, grants):
        client.nodes["n1"] = {"id": "n1", "name": "Lose", "node_type_id": None}
        assert compose_node(client, grants, "n1")["fields"] == []


class TestNeighbourhood:
    def test_the_neighbourhood_is_depth_one_with_edges(self, client, grants):
        compose_node(client, grants, "n1")
        assert client.last_neighbours == {"node_id": "n1", "depth": 1,
                                          "include_edges": True}


class TestVisibilityAndGrants:
    def test_the_payload_carries_the_backend_acl(self, client, grants):
        client.nodes["n1"]["access_group_ids"] = ["g-legal"]
        payload = compose_node(client, grants, "n1")
        assert payload["visibility"]["access_group_ids"] == ["g-legal"]

    def test_the_payload_carries_the_platform_grants(self, client, grants, alice):
        grants.set_owner("n1", alice.id)
        assert compose_node(client, grants, "n1")["grants"]["owner"] == str(alice.id)


class TestMissingNode:
    def test_an_unknown_node_composes_to_none(self, client, grants):
        """The client maps 404 to None; the composer must not build a page
        around a node that is not there."""
        assert compose_node(client, grants, "nope") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_graph_workbench.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'graph_workbench'`

- [ ] **Step 3: Implement the composer**

Create `src/graph_workbench.py`:

```python
"""Compose one workbench screen from several Knowledge Graph calls.

Three backend calls per selection, not one per pane: the Secure API is rate
limited at roughly one request a second, and a screen that fans out per widget
becomes unusable at exactly the moment someone opens it in front of a client.

The join between facts and attribute definitions lives here because the field
reader must render an attribute that has NO fact — the visible gap — and a
fact-only response cannot express that.

No Flask, no SQL. Takes a client and a grant store, returns a dict.

Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md (D2)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from graph_model import decode, format_date

logger = logging.getLogger(__name__)


def compose_node(client: Any, grants: Any, node_id: str) -> Optional[dict]:
    detail = client.graph_node(node_id)
    if not detail:
        return None
    node = detail.get("node", detail)
    facts = detail.get("facts")
    if facts is None:
        facts = client.graph_facts(node_id)

    type_id = node.get("node_type_id")
    attributes = client.graph_schema(type_id) if type_id else []
    neighbourhood = client.graph_neighbors(node_id, depth=1, include_edges=True)

    return {
        "node": node,
        "fields": _fields(attributes, facts),
        "neighbourhood": {"nodes": neighbourhood.get("neighbors", []),
                          "edges": neighbourhood.get("edges", [])},
        "grants": grants.for_node(node_id),
        "visibility": {"access_group_ids": list(node.get("access_group_ids") or [])},
    }


def _fields(attributes: list, facts: list) -> list:
    """Schema fields first, in sort order; unschematised facts after.

    A fact whose attribute_id is not in the schema is kept, not dropped. It is
    either free-form (attribute_id NULL) or belongs to a deprecated attribute,
    and both are real content the node genuinely has.
    """
    by_attribute: dict = {}
    for fact in facts:
        key = fact.get("attribute_id")
        if key is not None:
            by_attribute.setdefault(str(key), fact)

    out, claimed = [], set()
    for attribute in sorted(attributes,
                            key=lambda a: (a.get("sort_order", 0), a.get("name", ""))):
        attribute_id = str(attribute.get("id"))
        fact = by_attribute.get(attribute_id)
        if fact is not None:
            claimed.add(str(fact.get("id")))
        out.append(_field(attribute.get("name", ""), attribute.get("datatype", "text"),
                          attribute_id, bool(attribute.get("required", False)),
                          int(attribute.get("sort_order", 0)), fact))

    for fact in facts:
        if str(fact.get("id")) in claimed:
            continue
        out.append(_field(fact.get("label") or "Ohne Bezeichnung", "text",
                          fact.get("attribute_id"), False, 9999, fact))
    return out


def _field(name, datatype, attribute_id, required, sort_order, fact) -> dict:
    value = fact.get("value") if fact else None
    return {
        "attribute_id": attribute_id,
        "name": name,
        "datatype": datatype,
        "required": required,
        "sort_order": sort_order,
        "fact_id": str(fact["id"]) if fact and fact.get("id") else None,
        "value": decode(datatype, value) if fact else None,
        "display": _display(datatype, value) if fact else "",
        "missing": fact is None,
    }


def _display(datatype: str, value: Any) -> str:
    if datatype == "date":
        return format_date(value)
    if datatype == "money" and isinstance(value, dict):
        return f"{value.get('currency', '')} {value.get('amount', '')}".strip()
    if datatype == "entity_ref" and isinstance(value, dict):
        return str(value.get("node_id") or "")
    return "" if value is None else str(value)
```

- [ ] **Step 4: Run the composer tests**

Run: `pytest tests/test_graph_workbench.py -v`
Expected: all pass.

- [ ] **Step 5: Write the failing route tests**

Create `tests/test_graph_routes_nodes.py`:

```python
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestNodeList:
    def test_the_list_passes_the_filters_through(self, member_client, fake_graph):
        member_client.get("/api/graph/nodes?type=t1&q=M%C3%BCller")
        assert fake_graph.last_node_filters == {"node_type_id": "t1", "q": "Müller"}

    def test_the_list_is_not_narrowed_by_grants(self, member_client, fake_graph,
                                                node_owned_by_alice):
        """Read is the backend ACL's decision. Filtering by node_grants here
        would be a second read model."""
        body = member_client.get("/api/graph/nodes").get_json()
        assert node_owned_by_alice in [n["id"] for n in body["nodes"]]


class TestNodeCreate:
    def test_creating_a_node_makes_the_creator_the_owner(self, alice_client,
                                                         grants, fake_graph):
        body = alice_client.post("/api/graph/nodes",
                                 json={"name": "Müller AG",
                                       "node_type_id": "t1"}).get_json()
        assert grants.for_node(body["node"]["id"])["owner"] is not None

    def test_creating_a_node_without_a_name_is_400(self, alice_client):
        assert alice_client.post("/api/graph/nodes",
                                 json={"node_type_id": "t1"}).status_code == 400

    def test_any_member_may_create(self, member_client):
        assert member_client.post("/api/graph/nodes",
                                  json={"name": "X"}).status_code == 201

    def test_a_required_field_left_empty_does_not_block_the_save(
            self, alice_client, fake_graph):
        """Schemas are overlays that make absence visible; they never gate a
        write. Blocking would empty the completeness report of its purpose."""
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                    "required": True, "sort_order": 0}]
        response = alice_client.post("/api/graph/nodes",
                                     json={"name": "Ohne Frist", "node_type_id": "t1"})
        assert response.status_code == 201


class TestNodeDetail:
    def test_detail_returns_the_composed_payload(self, member_client):
        body = member_client.get("/api/graph/nodes/n1").get_json()
        assert set(body) >= {"node", "fields", "neighbourhood", "grants", "visibility"}

    def test_an_unknown_node_is_404(self, member_client):
        assert member_client.get("/api/graph/nodes/nope").status_code == 404
```

- [ ] **Step 6: Run to verify they fail**

Run: `pytest tests/test_graph_routes_nodes.py -v`
Expected: FAIL with 404 on every route.

- [ ] **Step 7: Implement the node routes**

Add to `create_graph_blueprint`:

```python
    @bp.route("/nodes", methods=["GET"])
    @require_graph_mode
    @require_user
    def list_nodes():
        try:
            nodes = source().graph_nodes(
                node_type_id=request.args.get("type") or None,
                q=request.args.get("q") or None)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node list failed")
        # Deliberately NOT filtered by node_grants: read visibility is the
        # backend ACL's answer and it has already been applied.
        return jsonify({"success": True, "nodes": nodes})

    @bp.route("/nodes", methods=["POST"])
    @require_graph_mode
    @require_user
    def create_node():
        payload = request.get_json(silent=True) or {}
        name = " ".join(str(payload.get("name") or "").split())
        if not name:
            return jsonify({"success": False, "error": "Name fehlt."}), 400
        try:
            created = source().graph_create_node(
                name, node_type_id=payload.get("node_type_id") or None)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node create failed")
        if created is None:
            return jsonify({"success": False, "error": "Knoten nicht anlegbar."}), 400
        node = created.get("node", created)
        # CreateMechanism: the creator becomes the owner in the same request.
        grant_store().set_owner(str(node["id"]), gate.current_user().id)
        return jsonify({"success": True, "node": node}), 201

    @bp.route("/nodes/<node_id>", methods=["GET"])
    @require_graph_mode
    @require_user
    def node_detail(node_id):
        from graph_workbench import compose_node

        try:
            payload = compose_node(source(), grant_store(), node_id)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node detail failed")
        if payload is None:
            return jsonify({"success": False, "error": "Knoten nicht gefunden."}), 404
        payload["success"] = True
        payload["may_write"] = grant_store().may_write(node_id, gate.current_user())
        return jsonify(payload)

    @bp.route("/nodes/<node_id>", methods=["PATCH"])
    @require_graph_mode
    @require_node_write
    def update_node(node_id):
        payload = request.get_json(silent=True) or {}
        fields = {k: payload[k] for k in
                  ("name", "description", "node_type_id", "required_groups")
                  if k in payload}
        if not fields:
            return jsonify({"success": False, "error": "Keine Änderung."}), 400
        try:
            updated = source().graph_update_node(node_id, **fields)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node update failed")
        if updated is None:
            return jsonify({"success": False, "error": "Knoten nicht gefunden."}), 404
        return jsonify({"success": True, "node": updated.get("node", updated)})
```

- [ ] **Step 8: Run the route tests and remove the remaining xfails**

Run: `pytest tests/test_graph_routes_nodes.py tests/test_graph_routes_auth.py -v`
Expected: all pass; delete the `xfail` on `TestNodeWriteGate`.

- [ ] **Step 9: Commit**

```bash
git add src/graph_workbench.py src/web_interface/graph_routes.py \
        tests/test_graph_workbench.py tests/test_graph_routes_nodes.py \
        tests/test_graph_routes_auth.py
git commit -m "feat(web): workbench composer and node routes (SS-315)"
```

---

### Task D3: Fact and grant routes

**Files:**
- Modify: `src/web_interface/graph_routes.py`
- Test: `tests/test_graph_routes_facts.py`, `tests/test_graph_routes_grants.py`

**Interfaces:**
- Consumes: B2 codecs, B4 client, C1 grants, C2 decorators, D1 schema read.
- Produces:
  - `POST /api/graph/nodes/<node_id>/facts` (owner|editor|admin) → 201 `{"fact": {...}}`; encodes through `graph_model.encode` using the attribute's datatype and `enum_values`; 400 with the codec's German message on `FactValueError`.
  - `PATCH /api/graph/facts/<fact_id>` — body carries `node_id` so the write gate can resolve; 403 without it.
  - `DELETE /api/graph/facts/<fact_id>` — same.
  - `GET /api/graph/nodes/<node_id>/grants` → `{"owner": {...}, "editors": [...]}` with `id`, `email`, `display_name` resolved from `users`.
  - `POST /api/graph/nodes/<node_id>/grants` (owner|admin) → 201.
  - `DELETE /api/graph/nodes/<node_id>/grants/<user_id>` (owner|admin) → 200; 409 `OwnerRevokeError`.

- [ ] **Step 1: Write the failing fact tests**

```python
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestFactCreate:
    def test_a_date_fact_is_encoded_before_it_leaves(self, alice_client, fake_graph):
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                    "sort_order": 0}]
        alice_client.post("/api/graph/nodes/n1/facts",
                          json={"attribute_id": "a1",
                                "value": {"value": "2026-03-04", "precision": "month"}})
        assert fake_graph.last_fact["value"] == {"value": "2026-03-04",
                                                 "precision": "month"}

    def test_a_malformed_value_is_400_with_a_usable_message(self, alice_client,
                                                            fake_graph):
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Frist", "datatype": "date",
                                    "sort_order": 0}]
        response = alice_client.post("/api/graph/nodes/n1/facts",
                                     json={"attribute_id": "a1",
                                           "value": {"value": "04.03.2026"}})
        assert response.status_code == 400
        assert "JJJJ-MM-TT" in response.get_json()["error"]

    def test_an_enum_value_is_checked_against_the_attribute(self, alice_client,
                                                            fake_graph):
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Status", "datatype": "enum",
                                    "enum_values": ["offen", "erledigt"],
                                    "sort_order": 0}]
        response = alice_client.post("/api/graph/nodes/n1/facts",
                                     json={"attribute_id": "a1", "value": "schwebend"})
        assert response.status_code == 400

    def test_a_non_editor_may_not_write_a_fact(self, member_client):
        response = member_client.post("/api/graph/nodes/n1/facts",
                                      json={"attribute_id": "a1", "value": "x"})
        assert response.status_code == 403

    def test_a_free_form_fact_needs_a_label(self, alice_client):
        response = alice_client.post("/api/graph/nodes/n1/facts", json={"value": "x"})
        assert response.status_code == 400


class TestFactMutation:
    def test_patching_a_fact_without_a_node_id_is_403(self, alice_client):
        """The write gate is per node; without the node there is nothing to
        authorise against, and defaulting to allow would be the bug."""
        assert alice_client.patch("/api/graph/facts/f1",
                                  json={"value": "x"}).status_code == 403

    def test_the_owner_may_patch_a_fact_on_their_node(self, alice_client,
                                                      node_owned_by_alice, fake_graph):
        fake_graph.schema["t1"] = [{"id": "a1", "name": "Titel", "datatype": "text",
                                    "sort_order": 0}]
        response = alice_client.patch(
            "/api/graph/facts/f1",
            json={"node_id": node_owned_by_alice, "attribute_id": "a1",
                  "value": "Neu"})
        assert response.status_code == 200
```

- [ ] **Step 2: Write the failing grant tests**

```python
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestGrantRead:
    def test_grants_resolve_to_people_not_uuids(self, member_client, grants,
                                                node_owned_by_alice, alice):
        body = member_client.get(
            f"/api/graph/nodes/{node_owned_by_alice}/grants").get_json()
        assert body["owner"]["email"] == alice.email

    def test_anyone_may_see_who_the_editors_are(self, member_client,
                                                node_owned_by_alice):
        assert member_client.get(
            f"/api/graph/nodes/{node_owned_by_alice}/grants").status_code == 200


class TestGrantWrite:
    def test_the_owner_may_grant_an_editor(self, alice_client, grants,
                                           node_owned_by_alice, bob):
        response = alice_client.post(f"/api/graph/nodes/{node_owned_by_alice}/grants",
                                     json={"user_id": str(bob.id)})
        assert response.status_code == 201
        assert str(bob.id) in grants.for_node(node_owned_by_alice)["editors"]

    def test_an_editor_may_not_grant_further_editors(self, bob_client, grants,
                                                     node_owned_by_alice, bob, carol):
        grants.grant_editor(node_owned_by_alice, bob.id, granted_by=None)
        response = bob_client.post(f"/api/graph/nodes/{node_owned_by_alice}/grants",
                                   json={"user_id": str(carol.id)})
        assert response.status_code == 403

    def test_an_admin_may_grant_on_any_node(self, admin_client, node_owned_by_alice,
                                            bob):
        assert admin_client.post(f"/api/graph/nodes/{node_owned_by_alice}/grants",
                                 json={"user_id": str(bob.id)}).status_code == 201

    def test_revoking_the_owner_is_409_not_500(self, alice_client,
                                               node_owned_by_alice, alice):
        response = alice_client.delete(
            f"/api/graph/nodes/{node_owned_by_alice}/grants/{alice.id}")
        assert response.status_code == 409

    def test_granting_to_an_unknown_user_is_404(self, alice_client,
                                                node_owned_by_alice):
        import uuid
        response = alice_client.post(f"/api/graph/nodes/{node_owned_by_alice}/grants",
                                     json={"user_id": str(uuid.uuid4())})
        assert response.status_code == 404
```

- [ ] **Step 3: Run both files to verify they fail**

Run: `pytest tests/test_graph_routes_facts.py tests/test_graph_routes_grants.py -v`
Expected: FAIL with 404 on every route.

- [ ] **Step 4: Implement the fact routes**

```python
    def _attribute(type_id, attribute_id):
        """One attribute definition, including deprecated ones.

        A fact may target a deprecated attribute — deprecation keeps facts —
        so a lookup that hid them would make editing an existing value fail.
        """
        for attribute in source().graph_schema(type_id, include_deprecated=True):
            if str(attribute.get("id")) == str(attribute_id):
                return attribute
        return None

    def _encoded(node_id, payload):
        """(value, attribute_id, label, error_response)."""
        from graph_model import FactValueError, encode

        attribute_id = payload.get("attribute_id")
        label = " ".join(str(payload.get("label") or "").split())
        raw = payload.get("value")
        if not attribute_id:
            if not label:
                return None, None, None, (jsonify(
                    {"success": False,
                     "error": "Ein freies Feld braucht eine Bezeichnung."}), 400)
            return raw, None, label, None

        detail = source().graph_node(node_id) or {}
        type_id = (detail.get("node", detail) or {}).get("node_type_id")
        attribute = _attribute(type_id, attribute_id) if type_id else None
        if attribute is None:
            return None, None, None, (jsonify(
                {"success": False, "error": "Attribut nicht gefunden."}), 404)
        try:
            value = encode(attribute.get("datatype", "text"), raw,
                           enum_values=attribute.get("enum_values"))
        except FactValueError as exc:
            # The codec's message is written for a user and names the field
            # rule; replacing it with a generic error would waste it.
            return None, None, None, (jsonify({"success": False,
                                               "error": str(exc)}), 400)
        return value, str(attribute_id), None, None

    @bp.route("/nodes/<node_id>/facts", methods=["POST"])
    @require_graph_mode
    @require_node_write
    def create_fact(node_id):
        payload = request.get_json(silent=True) or {}
        value, attribute_id, label, error = _encoded(node_id, payload)
        if error:
            return error
        try:
            created = source().graph_create_fact(
                node_id, value, attribute_id=attribute_id, label=label)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph fact create failed")
        if created is None:
            return jsonify({"success": False, "error": "Knoten nicht gefunden."}), 404
        return jsonify({"success": True, "fact": created.get("fact", created)}), 201

    @bp.route("/facts/<fact_id>", methods=["PATCH", "DELETE"])
    @require_graph_mode
    @require_user
    def mutate_fact(fact_id):
        payload = request.get_json(silent=True) or {}
        node_id = payload.get("node_id") or request.args.get("node_id")
        # The write gate is per node and a fact does not carry its node in the
        # URL. No node id means nothing to authorise against; defaulting to
        # allow would be the bug.
        if not node_id or not grant_store().may_write(node_id, gate.current_user()):
            return jsonify({"success": False,
                            "error": "Keine Bearbeitungsrechte für diesen Knoten."}), 403
        try:
            if request.method == "DELETE":
                result = source().graph_delete_fact(fact_id)
            else:
                value, attribute_id, label, error = _encoded(node_id, payload)
                if error:
                    return error
                result = source().graph_update_fact(fact_id, value=value)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph fact mutation failed")
        if result is None:
            return jsonify({"success": False, "error": "Fakt nicht gefunden."}), 404
        return jsonify({"success": True, "fact": result.get("fact", result)})
```

- [ ] **Step 5: Implement the grant routes**

```python
    def _person(user_id):
        user = gate.users().get(user_id)
        if user is None:
            return {"id": str(user_id), "email": None,
                    "display_name": "Unbekanntes Konto"}
        return {"id": str(user.id), "email": user.email,
                "display_name": getattr(user, "display_name", None) or user.email}

    @bp.route("/nodes/<node_id>/grants", methods=["GET"])
    @require_graph_mode
    @require_user
    def read_grants(node_id):
        current = grant_store().for_node(node_id)
        return jsonify({
            "success": True,
            "owner": _person(current["owner"]) if current["owner"] else None,
            "editors": [_person(uid) for uid in current["editors"]],
        })

    def _may_grant(node_id):
        user = gate.current_user()
        if user is None:
            return False
        if "admin" in user.roles:
            return True
        # mayGrant (node_grants.als): the owner or an admin, never an editor.
        return grant_store().for_node(node_id)["owner"] == str(user.id)

    @bp.route("/nodes/<node_id>/grants", methods=["POST"])
    @require_graph_mode
    @require_user
    def add_grant(node_id):
        if not _may_grant(node_id):
            # An editor may edit, not delegate. Otherwise one grant silently
            # becomes the right to hand out every further grant.
            return jsonify({"success": False,
                            "error": "Nur Eigentümer oder Administrator."}), 403
        user_id = str((request.get_json(silent=True) or {}).get("user_id") or "")
        if not user_id or gate.users().get(user_id) is None:
            return jsonify({"success": False, "error": "Konto nicht gefunden."}), 404
        grant_store().grant_editor(node_id, user_id, granted_by=gate.current_user().id)
        return jsonify({"success": True, "editor": _person(user_id)}), 201

    @bp.route("/nodes/<node_id>/grants/<user_id>", methods=["DELETE"])
    @require_graph_mode
    @require_user
    def remove_grant(node_id, user_id):
        from identity.node_grants import OwnerRevokeError

        if not _may_grant(node_id):
            return jsonify({"success": False,
                            "error": "Nur Eigentümer oder Administrator."}), 403
        try:
            grant_store().revoke(node_id, user_id)
        except OwnerRevokeError as exc:
            return jsonify({"success": False, "error": str(exc)}), 409
        return jsonify({"success": True})
```

- [ ] **Step 6: Run both files to verify they pass**

Run: `pytest tests/test_graph_routes_facts.py tests/test_graph_routes_grants.py -v`
Expected: all pass.

- [ ] **Step 7: Add CSRF coverage for the new mutating routes**

In `tests/test_csrf_enforcement.py`, extend the existing parametrised list with
every mutating route added in D1–D3:

```python
    ("POST",   "/api/graph/node-types"),
    ("POST",   "/api/graph/node-types/t1/schema"),
    ("PATCH",  "/api/graph/node-types/t1/schema/a1"),
    ("DELETE", "/api/graph/node-types/t1/schema/a1"),
    ("POST",   "/api/graph/nodes"),
    ("PATCH",  "/api/graph/nodes/n1"),
    ("POST",   "/api/graph/nodes/n1/facts"),
    ("PATCH",  "/api/graph/facts/f1"),
    ("DELETE", "/api/graph/facts/f1"),
    ("POST",   "/api/graph/nodes/n1/grants"),
    ("DELETE", "/api/graph/nodes/n1/grants/u1"),
```

Run: `pytest tests/test_csrf_enforcement.py -v`
Expected: all pass — the global `before_request` hook already covers them; this
asserts it and will fail loudly if a future route escapes the hook.

- [ ] **Step 8: Commit**

```bash
git add src/web_interface/graph_routes.py tests/test_graph_routes_facts.py \
        tests/test_graph_routes_grants.py tests/test_csrf_enforcement.py
git commit -m "feat(web): fact and grant routes with codec validation (SS-315)"
```

---

### Task E1: Workbench shell and searchable list pane

**Files:**
- Create: `src/web_interface/templates/workbench.html`
- Create: `src/web_interface/static/js/workbench.js`
- Create: `src/web_interface/static/css/workbench.css`
- Modify: `src/web_interface/app.py` (add the `/workbench` page route beside `ontology_page`)
- Modify: `src/web_interface/templates/_sidebar.html`
- Test: `tests/test_workbench_page.py`

**Interfaces:**
- Consumes: `GET /api/graph/nodes?type=&q=` (D2).
- Produces: `GET /workbench` rendering the shell; `workbench.js` exposing `Workbench.select(nodeId)` used by E2–E4.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestWorkbenchPage:
    def test_the_page_requires_a_session(self, anon_client):
        assert anon_client.get("/workbench").status_code in (302, 401)

    def test_the_page_renders_the_three_panes(self, member_client):
        html = member_client.get("/workbench").get_data(as_text=True)
        assert 'id="nodeList"' in html
        assert 'id="neighbourhoodGraph"' in html
        assert 'id="fieldReader"' in html

    def test_the_page_carries_a_csrf_token(self, member_client):
        html = member_client.get("/workbench").get_data(as_text=True)
        assert 'name="csrf-token"' in html

    def test_fixture_mode_says_so_instead_of_rendering_panes(self, fixture_mode_client):
        html = fixture_mode_client.get("/workbench").get_data(as_text=True)
        assert "Wissensnetz-Modus erforderlich" in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_workbench_page.py -v`
Expected: FAIL — 404 on `/workbench`.

- [ ] **Step 3: Add the page route**

In `app.py`, beside `ontology_page()`:

```python
    @app.route('/workbench')
    def workbench_page():
        """Arbeitsplatz: Liste -> Nachbarschaft -> Felder, typunabhängig."""
        user = identity_gate.current_user() if identity_gate is not None else None
        return render_template(
            'workbench.html',
            active_nav='workbench',
            **_sidebar_context(),
            app_title=web_app_title,
            brand=web_brand,
            graph_mode=_ontology_source_is_graph(),
            is_admin=bool(user is not None and 'admin' in user.roles),
            csrf_token=_ensure_csrf_token(),
            asset_version=_static_asset_version(),
        )
```

There is no gate list to extend: `IdentityGate.guard` protects every endpoint
whose name is not in `identity.webauth.PUBLIC_ENDPOINTS`, and
`_static_asset_version()` already hashes every `.js` and `.css` under
`static/`. The page test asserts both (401/302 without a session; a fresh
`?v=` after touching `workbench.js`).

- [ ] **Step 4: Create the template**

`src/web_interface/templates/workbench.html` — three panes, following the
structure and class vocabulary of `ontology.html`:

```html
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="csrf-token" content="{{ csrf_token }}">
    <title>{{ brand }} Arbeitsplatz</title>
    <link rel="icon" href="{{ url_for('static', filename='img/favicon.svg') }}" type="image/svg+xml">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}?v={{ asset_version }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/workbench.css') }}?v={{ asset_version }}">
</head>
<body>
<div class="app-shell">
    {% include '_sidebar.html' %}
    <div class="app-content">
    <div class="container container-wide">
        <header class="site-header">
            <h1 class="site-greeting">Arbeitsplatz<br>
                <span class="site-greeting-question">Ihre Objekte, verbunden.</span>
            </h1>
        </header>

        {% if not graph_mode %}
        <p class="workbench-unavailable">Wissensnetz-Modus erforderlich</p>
        {% else %}
        <main class="workbench" aria-label="Arbeitsplatz">
            <section class="workbench-list" aria-label="Objekte">
                <div class="workbench-list-header">
                    <label class="sr-only" for="nodeSearch">Suchen</label>
                    <input type="search" id="nodeSearch" placeholder="Suchen …"
                           autocomplete="off">
                    <button type="button" class="btn btn-primary" id="nodeCreate">Neu</button>
                </div>
                <div class="workbench-type-filter" id="typeFilter" role="group"
                     aria-label="Nach Typ filtern"></div>
                <ul class="workbench-node-list" id="nodeList"></ul>
                <p class="workbench-empty" id="nodeListEmpty" hidden></p>
            </section>

            <section class="workbench-detail">
                <div class="workbench-graph" aria-label="Direkte Nachbarschaft">
                    <div class="ontology-pane-header">
                        <h2>Nachbarschaft</h2>
                        <div class="graph-toolbar" role="toolbar" aria-label="Werkzeuge">
                            <button type="button" id="zoomFit" class="btn btn-outline">Einpassen</button>
                        </div>
                    </div>
                    <div id="neighbourhoodGraph" aria-hidden="true"></div>
                </div>
                <div class="workbench-fields" aria-label="Inhalt">
                    <div class="ontology-pane-header">
                        <h2 id="fieldReaderTitle">Inhalt</h2>
                    </div>
                    <div id="fieldReader"></div>
                    <div id="grantsPanel"></div>
                </div>
            </section>
        </main>
        {% endif %}
    </div>
    </div>
</div>
<script src="{{ url_for('static', filename='js/vendor/cytoscape.min.js') }}?v={{ asset_version }}"></script>
<script src="{{ url_for('static', filename='js/workbench.js') }}?v={{ asset_version }}"></script>
</body>
</html>
```

Add an "Arbeitsplatz" entry to `_sidebar.html` beside the Cortex link.

- [ ] **Step 5: Implement the list pane**

Create `src/web_interface/static/js/workbench.js` with the module skeleton and
the list only — E2, E3 and E4 fill in the rest:

```javascript
/* Arbeitsplatz: list -> neighbourhood -> fields.
 *
 * Nothing here knows a node type. Every column, control and label comes from
 * GET /api/graph/node-types/<id>/schema at runtime, so a new type is data
 * entry rather than a deploy.
 */
(function () {
  'use strict';

  const csrf = document.querySelector('meta[name="csrf-token"]');
  const CSRF = csrf ? csrf.getAttribute('content') : '';

  const state = { types: [], nodes: [], typeFilter: null, query: '', selected: null };

  async function api(path, options) {
    const response = await fetch(path, Object.assign({
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
      credentials: 'same-origin'
    }, options || {}));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) { throw Object.assign(new Error(body.error || 'Fehler'), { status: response.status, body }); }
    return body;
  }

  function debounce(fn, ms) {
    let handle;
    return function () {
      const args = arguments;
      clearTimeout(handle);
      handle = setTimeout(() => fn.apply(null, args), ms);
    };
  }

  async function loadTypes() {
    const body = await api('/api/graph/node-types');
    state.types = body.node_types || [];
    const container = document.getElementById('typeFilter');
    container.innerHTML = '';
    container.appendChild(chip('Alle', null));
    state.types.forEach((type) => container.appendChild(chip(type.name, type.id)));
  }

  function chip(label, typeId) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'workbench-chip' + (state.typeFilter === typeId ? ' is-active' : '');
    button.textContent = label;
    button.addEventListener('click', () => {
      state.typeFilter = typeId;
      loadTypes();
      loadNodes();
    });
    return button;
  }

  async function loadNodes() {
    const params = new URLSearchParams();
    if (state.typeFilter) { params.set('type', state.typeFilter); }
    if (state.query) { params.set('q', state.query); }
    const body = await api('/api/graph/nodes?' + params.toString());
    state.nodes = body.nodes || [];
    renderNodes();
  }

  function renderNodes() {
    const nodes = state.nodes;
    const list = document.getElementById('nodeList');
    const empty = document.getElementById('nodeListEmpty');
    list.innerHTML = '';
    if (!nodes.length) {
      empty.hidden = false;
      // Say what was searched. The server matches the NAME only; implying it
      // searched field contents would send people looking for a bug.
      empty.textContent = state.query
        ? 'Keine Objekte, deren Name „' + state.query + '" enthält.'
        : 'Noch keine Objekte vorhanden.';
      return;
    }
    empty.hidden = true;
    nodes.forEach((node) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'workbench-node' + (state.selected === node.id ? ' is-selected' : '');
      button.textContent = node.name;
      button.addEventListener('click', () => select(node.id));
      item.appendChild(button);
      list.appendChild(item);
    });
  }

  async function select(nodeId) {
    state.selected = nodeId;
    history.replaceState(null, '', '?node=' + encodeURIComponent(nodeId));
    renderNodes();                       // repaint so the selection marker moves
    const payload = await api('/api/graph/nodes/' + encodeURIComponent(nodeId));
    window.Workbench.renderNeighbourhood(payload);   // E2
    window.Workbench.renderFields(payload);          // E3
    window.Workbench.renderGrants(payload);          // E4
  }

  window.Workbench = { api, select, state,
    renderNeighbourhood() {}, renderFields() {}, renderGrants() {} };

  document.addEventListener('DOMContentLoaded', async () => {
    if (!document.getElementById('nodeList')) { return; }   // fixture mode
    document.getElementById('nodeSearch').addEventListener('input', debounce((event) => {
      state.query = event.target.value.trim();
      loadNodes();
    }, 250));
    await loadTypes();
    await loadNodes();
    const initial = new URLSearchParams(location.search).get('node');
    if (initial) { select(initial); }
  });
})();
```

Create `workbench.css` with a two-column grid (list ~320px, detail flexible),
the detail column split into graph above and fields below, reusing the existing
colour and spacing custom properties from `style.css`. Do not introduce new
design tokens.

- [ ] **Step 6: Run to verify the page tests pass**

Run: `pytest tests/test_workbench_page.py -v`
Expected: 4 passed.

- [ ] **Step 7: Verify by hand**

Start the app with `ONTOLOGY_SOURCE=graph` against the dev tenant, open
`/workbench`, and confirm: type chips render; typing filters the list; the URL
gains `?node=…` on selection and reloading that URL reselects.

- [ ] **Step 8: Commit**

```bash
git add src/web_interface/templates/workbench.html \
        src/web_interface/templates/_sidebar.html \
        src/web_interface/static/js/workbench.js \
        src/web_interface/static/css/workbench.css \
        src/web_interface/app.py tests/test_workbench_page.py
git commit -m "feat(workbench): shell and searchable node list (SS-315)"
```

---

### Task E2: Neighbourhood graph pane

**Files:**
- Modify: `src/web_interface/static/js/workbench.js`
- Modify: `src/web_interface/static/css/workbench.css`

**Interfaces:**
- Consumes: `payload.neighbourhood.{nodes,edges}` from D2, which requires backend Task A2.
- Produces: `Workbench.renderNeighbourhood(payload)`.

- [ ] **Step 1: Implement the pane**

Replace the `renderNeighbourhood` stub:

```javascript
  let cy = null;

  function renderNeighbourhood(payload) {
    const container = document.getElementById('neighbourhoodGraph');
    const anchor = payload.node;
    const neighbours = payload.neighbourhood.nodes || [];
    const edges = payload.neighbourhood.edges || [];

    const elements = [{ data: { id: anchor.id, label: anchor.name }, classes: 'anchor' }];
    neighbours.forEach((n) => elements.push({ data: { id: n.id, label: n.name } }));

    // Only edges whose BOTH endpoints are drawn. The server already guarantees
    // this, but an edge to a node we did not render would draw to nowhere.
    const drawn = new Set(elements.map((e) => e.data.id));
    edges.forEach((edge) => {
      if (drawn.has(edge.node_lo) && drawn.has(edge.node_hi)) {
        elements.push({ data: { id: edge.id, source: edge.node_lo,
                                target: edge.node_hi, label: edge.relation } });
      }
    });

    if (cy) { cy.destroy(); }
    cy = cytoscape({
      container: container,
      elements: elements,
      style: [
        { selector: 'node', style: {
            'label': 'data(label)', 'font-size': 11, 'text-valign': 'center',
            'background-color': '#9aa7b8', 'color': '#fff',
            'text-outline-width': 2, 'text-outline-color': '#5a6675' } },
        { selector: 'node.anchor', style: { 'background-color': '#2f6fb0', 'width': 44, 'height': 44 } },
        { selector: 'edge', style: {
            'label': 'data(label)', 'font-size': 9, 'curve-style': 'bezier',
            'width': 1.5, 'line-color': '#c3ccd8', 'target-arrow-shape': 'none',
            'text-rotation': 'autorotate' } }
      ],
      layout: { name: 'concentric', concentric: (n) => (n.hasClass('anchor') ? 2 : 1),
                minNodeSpacing: 40 }
    });

    // Walking the graph IS the navigation: clicking a neighbour selects it.
    cy.on('tap', 'node', (event) => {
      const id = event.target.id();
      if (id !== anchor.id) { select(id); }
    });

    container.setAttribute('aria-hidden', neighbours.length ? 'false' : 'true');
    if (!neighbours.length) {
      container.innerHTML = '<p class="workbench-empty">Keine direkten Verbindungen.</p>';
    }
  }
```

Wire `#zoomFit` to `cy.fit()` and register `renderNeighbourhood` on the
`window.Workbench` object in place of the stub.

- [ ] **Step 2: Verify by hand**

With backend Task A2 deployed to the dev tenant: select a node with at least
two neighbours and confirm the anchor is visually distinct, relation labels
appear on the edges, clicking a neighbour re-centres on it, and a node with no
edges shows "Keine direkten Verbindungen." rather than an empty canvas.

- [ ] **Step 3: Commit**

```bash
git add src/web_interface/static/js/workbench.js src/web_interface/static/css/workbench.css
git commit -m "feat(workbench): depth-1 neighbourhood graph pane (SS-315)"
```

---

### Task E3: Field reader pane

**Files:**
- Modify: `src/web_interface/static/js/workbench.js`
- Modify: `src/web_interface/static/css/workbench.css`

**Interfaces:**
- Consumes: `payload.fields` and `payload.visibility` from D2.
- Produces: `Workbench.renderFields(payload)`.

- [ ] **Step 1: Implement**

```javascript
  function renderFields(payload) {
    document.getElementById('fieldReaderTitle').textContent = payload.node.name;
    const host = document.getElementById('fieldReader');
    host.innerHTML = '';

    if (!payload.fields.length) {
      host.innerHTML = '<p class="workbench-empty">Für diesen Typ sind noch keine Felder definiert.</p>';
    }

    const list = document.createElement('dl');
    list.className = 'workbench-fieldlist';
    payload.fields.forEach((field) => {
      const term = document.createElement('dt');
      term.textContent = field.name;
      if (field.required) { term.classList.add('is-required'); }

      const value = document.createElement('dd');
      if (field.missing) {
        // A gap, not an error. The completeness report exists to count these,
        // which requires the node to have been creatable without them.
        value.className = 'is-missing';
        value.textContent = field.required ? 'Fehlt' : '—';
      } else if (field.datatype === 'entity_ref' && field.value && field.value.node_id) {
        const link = document.createElement('button');
        link.type = 'button';
        link.className = 'workbench-ref';
        link.textContent = field.display || field.value.node_id;
        link.addEventListener('click', () => select(field.value.node_id));
        value.appendChild(link);
      } else {
        value.textContent = field.display;
      }
      list.appendChild(term);
      list.appendChild(value);
    });
    host.appendChild(list);

    const groups = (payload.visibility && payload.visibility.access_group_ids) || [];
    const visibility = document.createElement('p');
    visibility.className = 'workbench-visibility';
    visibility.textContent = groups.length
      ? 'Sichtbarkeit: ' + groups.join(', ')
      : 'Sichtbarkeit: keine Einschränkung';
    host.appendChild(visibility);
  }
```

Register it in place of the stub. In CSS, style `.is-missing` as muted and
`dt.is-required` with a marker — never red, and never an error icon: a gap is
information, not a failure.

An `entity_ref` currently displays its raw node id when the composer has no
name for it. Resolve the label in the composer instead if the neighbourhood
already carries that node: prefer a name the payload already has over a second
request.

- [ ] **Step 2: Verify by hand**

Select a node with a filled date field, an empty required field, and an
`entity_ref`. Confirm: the date honours its precision (a month-precision fact
renders "März 2026", never a day); the empty required field reads "Fehlt" in
muted styling; clicking the reference navigates to that node.

- [ ] **Step 3: Commit**

```bash
git add src/web_interface/static/js/workbench.js src/web_interface/static/css/workbench.css
git commit -m "feat(workbench): field reader with visible gaps and honest date precision (SS-315)"
```

---

### Task E4: Creation form, Typ-Werkstatt, editors panel

**Files:**
- Modify: `src/web_interface/static/js/workbench.js`
- Modify: `src/web_interface/templates/workbench.html`
- Modify: `src/web_interface/static/css/workbench.css`

**Interfaces:**
- Consumes: D1 schema routes, D2 node create, D3 fact and grant routes; backend Task A4 for the filtered node picker.
- Produces: `Workbench.renderGrants(payload)`, the creation dialog, and the admin Typ-Werkstatt.

- [ ] **Step 1: Add the dialogs to the template**

Append two `<dialog>` elements before the closing `</main>`: `#nodeCreateDialog`
with a type `<select>` and an empty `<div id="nodeCreateFields">`, and
`#typeWorkshopDialog` with an attribute table and an add-attribute row. Add a
"Typ-Werkstatt" button rendered only when the session is an admin — pass
`is_admin` from `workbench_page()` into the template.

- [ ] **Step 2: Implement the schema-driven form**

```javascript
  // One control per datatype. This map IS the reason no node type appears in
  // code: adding a type adds rows here, never a branch.
  function controlFor(attribute) {
    const wrap = document.createElement('div');
    wrap.className = 'workbench-field';
    const label = document.createElement('label');
    label.textContent = attribute.name + (attribute.required ? ' *' : '');
    label.htmlFor = 'attr-' + attribute.id;
    wrap.appendChild(label);

    let control;
    switch (attribute.datatype) {
      case 'date': {
        control = document.createElement('div');
        const day = document.createElement('input');
        day.type = 'date'; day.id = 'attr-' + attribute.id;
        const precision = document.createElement('select');
        [['day', 'Tag genau'], ['month', 'Monat genau'], ['year', 'Jahr genau']]
          .forEach(([v, t]) => precision.add(new Option(t, v)));
        precision.className = 'workbench-precision';
        control.appendChild(day); control.appendChild(precision);
        break;
      }
      case 'money': {
        control = document.createElement('div');
        const amount = document.createElement('input');
        amount.type = 'text'; amount.inputMode = 'decimal';
        amount.id = 'attr-' + attribute.id; amount.placeholder = '0.00';
        const currency = document.createElement('input');
        currency.type = 'text'; currency.value = 'CHF'; currency.maxLength = 3;
        currency.className = 'workbench-currency';
        control.appendChild(amount); control.appendChild(currency);
        break;
      }
      case 'enum': {
        control = document.createElement('select');
        control.id = 'attr-' + attribute.id;
        control.add(new Option('—', ''));
        (attribute.enum_values || []).forEach((v) => control.add(new Option(v, v)));
        break;
      }
      case 'entity_ref': {
        control = document.createElement('select');
        control.id = 'attr-' + attribute.id;
        control.add(new Option('—', ''));
        // target_node_type_id filters the picker to the declared type. Null on
        // attributes created before that field existed: then offer everything.
        const params = new URLSearchParams();
        if (attribute.target_node_type_id) { params.set('type', attribute.target_node_type_id); }
        api('/api/graph/nodes?' + params.toString()).then((body) => {
          (body.nodes || []).forEach((n) => control.add(new Option(n.name, n.id)));
        });
        break;
      }
      default:
        control = document.createElement('input');
        control.type = 'text';
        control.id = 'attr-' + attribute.id;
    }
    control.dataset.attributeId = attribute.id;
    control.dataset.datatype = attribute.datatype;
    wrap.appendChild(control);
    return wrap;
  }

  function readControl(wrap) {
    const control = wrap.querySelector('[data-attribute-id]');
    const datatype = control.dataset.datatype;
    if (datatype === 'date') {
      const day = wrap.querySelector('input[type="date"]').value;
      if (!day) { return null; }
      return { value: day, precision: wrap.querySelector('.workbench-precision').value };
    }
    if (datatype === 'money') {
      const amount = wrap.querySelector('input[inputmode="decimal"]').value.trim();
      if (!amount) { return null; }
      return { amount: amount, currency: wrap.querySelector('.workbench-currency').value.trim() };
    }
    const raw = control.value.trim();
    if (!raw) { return null; }
    return datatype === 'entity_ref' ? { node_id: raw } : raw;
  }

  async function submitNewNode(typeId, name, fieldWraps) {
    const created = await api('/api/graph/nodes', {
      method: 'POST',
      body: JSON.stringify({ name: name, node_type_id: typeId || null })
    });
    const nodeId = created.node.id;

    // Facts are written one at a time and a rejected value never blocks the
    // node: the node exists, the field stays empty and visible as a gap.
    // Schemas are overlays, not write gates.
    const problems = [];
    for (const wrap of fieldWraps) {
      const control = wrap.querySelector('[data-attribute-id]');
      const value = readControl(wrap);
      if (value === null) { continue; }
      try {
        await api('/api/graph/nodes/' + encodeURIComponent(nodeId) + '/facts', {
          method: 'POST',
          body: JSON.stringify({ attribute_id: control.dataset.attributeId, value: value })
        });
      } catch (error) {
        problems.push(wrap.querySelector('label').textContent + ': ' + error.message);
      }
    }
    if (problems.length) { showFormErrors(problems); }
    await loadNodes();
    await select(nodeId);
  }
```

Wire `#nodeCreate` to open the dialog, populate the type `<select>` from
`state.types`, and rebuild `#nodeCreateFields` from
`GET /api/graph/node-types/<id>/schema` whenever the type changes. **Do not
add a required-field check before submit** — Global Constraint 2.

- [ ] **Step 3: Implement the Typ-Werkstatt**

Admin-only. Lists the type's attributes and offers add and deprecate.

```javascript
  const DATATYPE_LABELS = {
    text: 'Text', date: 'Datum', money: 'Betrag',
    enum: 'Auswahl', entity_ref: 'Verknüpfung'
  };

  async function openTypeWorkshop(typeId) {
    const body = await api('/api/graph/node-types/' + encodeURIComponent(typeId) + '/schema');
    const table = document.getElementById('typeWorkshopAttributes');
    table.innerHTML = '';
    (body.attributes || []).forEach((attribute) => {
      const row = document.createElement('tr');

      const name = document.createElement('td');
      name.textContent = attribute.name;
      const datatype = document.createElement('td');
      datatype.textContent = DATATYPE_LABELS[attribute.datatype] || attribute.datatype;
      const required = document.createElement('td');
      required.textContent = attribute.required ? 'Pflicht' : 'Optional';

      const actions = document.createElement('td');
      const retire = document.createElement('button');
      retire.type = 'button';
      retire.className = 'btn btn-outline';
      // "Stilllegen", never "Löschen": the server soft-deprecates and the
      // facts survive. Offering a delete would promise something untrue.
      retire.textContent = 'Stilllegen';
      retire.addEventListener('click', async () => {
        if (!confirm('Attribut wird stillgelegt — bestehende Fakten bleiben erhalten.')) {
          return;
        }
        await api('/api/graph/node-types/' + encodeURIComponent(typeId) +
                  '/schema/' + encodeURIComponent(attribute.id), { method: 'DELETE' });
        openTypeWorkshop(typeId);
      });
      actions.appendChild(retire);

      [name, datatype, required, actions].forEach((cell) => row.appendChild(cell));
      table.appendChild(row);
    });
    document.getElementById('typeWorkshopDialog').showModal();
  }

  async function addAttribute(typeId) {
    const datatype = document.getElementById('newAttrDatatype').value;
    const payload = {
      name: document.getElementById('newAttrName').value.trim(),
      datatype: datatype,
      required: document.getElementById('newAttrRequired').checked,
      sort_order: Number(document.getElementById('newAttrSortOrder').value || 0)
    };
    if (datatype === 'enum') {
      payload.enum_values = document.getElementById('newAttrEnumValues').value
        .split(',').map((v) => v.trim()).filter(Boolean);
    }
    if (datatype === 'entity_ref') {
      const target = document.getElementById('newAttrTargetType').value;
      if (target) { payload.target_node_type_id = target; }
    }
    try {
      await api('/api/graph/node-types/' + encodeURIComponent(typeId) + '/schema', {
        method: 'POST', body: JSON.stringify(payload)
      });
    } catch (error) {
      showFormErrors([error.message]);
      return;
    }
    openTypeWorkshop(typeId);
  }
```

Wire `#newAttrDatatype`'s `change` event to show `#newAttrEnumValues` only for
`enum` and `#newAttrTargetType` only for `entity_ref`, populating the latter
from `state.types`. Wire a "Typ anlegen" button to
`POST /api/graph/node-types` followed by `loadTypes()`.

- [ ] **Step 4: Implement the editors panel**

```javascript
  function renderGrants(payload) {
    const host = document.getElementById('grantsPanel');
    host.innerHTML = '';
    const heading = document.createElement('h3');
    heading.textContent = 'Bearbeitung';
    host.appendChild(heading);

    api('/api/graph/nodes/' + encodeURIComponent(payload.node.id) + '/grants')
      .then((body) => {
        const list = document.createElement('ul');
        list.className = 'workbench-grants';
        if (body.owner) {
          list.appendChild(person(body.owner, 'Eigentümer', false, payload));
        }
        (body.editors || []).forEach((editor) => {
          list.appendChild(person(editor, 'Bearbeiter', payload.may_write, payload));
        });
        host.appendChild(list);
        if (payload.may_write) { host.appendChild(addEditorControl(payload)); }
      });
  }
```

`person()` renders the display name, the role, and a "Entziehen" button when
revocation is offered; `addEditorControl()` is a user search calling
`POST /api/graph/nodes/<id>/grants`. The revoke button must handle a **409** by
showing the server's message — the owner cannot be revoked, only transferred.

- [ ] **Step 5: Verify by hand, end to end**

As an admin: define a node type `Person` with a `text` field, and a type
`Projekt` with a `text` field, a `date` field, an `enum` field and an
`entity_ref` field targeting `Person`. As a normal user: create a `Person`,
then a `Projekt` referencing them, leaving the required date empty. Confirm the
save succeeds, the field reader shows the gap, and the neighbourhood graph
draws the `Projekt`→`Person` edge the `entity_ref` materialised. As the owner,
grant a second user editor and confirm they can edit and a third user cannot.

- [ ] **Step 6: Run the whole suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/web_interface/static/js/workbench.js \
        src/web_interface/templates/workbench.html \
        src/web_interface/static/css/workbench.css
git commit -m "feat(workbench): schema-driven creation form, Typ-Werkstatt, editors (SS-315)"
```

---

## Verification

From `KnovasPlatform/components/docbridge_integration/` (a `platform-db` must be reachable at `PLATFORM_DB_TEST_DSN`, exactly as CI provides; identity tests otherwise skip, and under `CI=true` a skip is a failure):

```bash
bash models/alloy/ci/run_all.sh          # alloy-checks: ok
pytest -q                                # incl. tests/test_node_grants_alloy.py and the identity suite
```

Then, against the dev tenant with `ONTOLOGY_SOURCE=graph`, walk the end-to-end
scenario in Task E4 Step 5 and confirm each of the seven scope requirements:

1. An admin defines a node type and its fields — and a non-admin cannot.
2. A user creates an entity from those fields, with the right control per datatype.
3. An `entity_ref` field offers only nodes of its target type and materialises an edge.
4. The owner grants a second user editor; a third user gets 403 on write and 200 on read.
5. The list searches by name across all types and filters by type chip.
6. Selecting a node opens its depth-1 neighbourhood with relation labels; clicking a neighbour navigates.
7. The field reader shows every schema field in order, with gaps visible and dates honouring precision.

Finally confirm the two negative cases that are easy to regress:

- With `ONTOLOGY_SOURCE=fixture`, `/workbench` renders "Wissensnetz-Modus erforderlich" and every `/api/graph/*` route answers 409.
- A `required` field left empty **saves**. If it blocks, Global Constraint 2 has been violated.

## Requirement traceability

| Spec § | Requirement | Task |
| --- | --- | --- |
| §4.1, §7.1 | Admin defines node types and fields | D1, E4 |
| §4.2, §7.2 | Schema-driven creation form | D1, E4 |
| §4.3, §5.2 | Connection fields with a target type | B3, D1, E4 |
| §4.4, §6 | Per-user editor grants | C1, C2, D3, E4 |
| §4.5, §7.3 | Searchable list of all nodes | B3, D2, E1 |
| §4.6, §7.4 | Immediate-neighbourhood graph | B4, D2, E2 |
| §4.7, §7.5 | Field reader | B2, D2, E3 |
| §7.0 | Codecs, client, composer, grant layers | B1–B4, C1, D2 |
| §7.7 | `/api/graph/*` namespace with CSRF | D1–D3 |
| §8.1 | No node type in code | E1–E4 |
| §8.2 | Schemas never block writes | D2, E4 |
| §8.3 | Deprecate is not delete | B3, D1, E4 |
| §8.4 | One read model | D2, C1 |
| §3.3 | Fixture mode renders an explicit state | C2, E1 |
| §6.5 | Grant rules modelled before the store; mutants refute; pins | C0 |
| §10 | Grant enforcement tests mirror the mechanisms by name | C1, C2, D3 |

## Related

- Design: `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md`
- Backend plan: `docs/superpowers/plans/2026-09-02-typed-node-workbench-backend.md`
- Identity dependency: `docs/superpowers/plans/2026-08-14-section-b-buildout.md` (partially merged as PR #7; the identity stack is on `feat/section-b-buildout` and `feat/admin-document-rbac`)
- Alloy idiom and runner: `KnowledgeBase/knovas-software/models/alloy/README.md`, `KnowledgeBase/docs/Docs/05_TESTS/Alloy_Unified_Model_Guide.md` §3
- Superseded surfaces: `docs/superpowers/specs/2026-08-14-matters-and-typed-nodes-design.md` §7.3–§7.7
