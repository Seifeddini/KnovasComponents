# Typed-node workbench — backend slice implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Knowledge Graph API the two things the Platform workbench needs and does not have: edges in the neighbours response, and a target node type on `entity_ref` schema attributes.

**Architecture:** Both changes are additive and reuse existing enforcement. `include_edges` induces edges on the already-ACL-filtered neighbour set and runs them through `GraphAccessGuard.filter_edges`, so no new visibility rule is introduced. `target_node_type_id` is a nullable column with a same-tenant validation; existing attributes keep a null target and behave exactly as today.

**Tech Stack:** Python 3, Flask blueprints, psycopg2, PostgreSQL, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md` (§5)

**Jira:** SS-315 *Platform Projekt und Mandantenmanagement*

**Repository:** `KnowledgeBase` only. The Platform slice is
`docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md`; it
consumes Task A2 and Task A3 and can start before either lands.

**Branch:** `design/typed-node-workbench`

## Global Constraints

- **No new Golden Invariant and no new Alloy model.** Both changes fall under invariants that already exist and are already CI-gated: **GI-GRAPH-12** ("an edge is only as visible as its least visible endpoint node") for Task A2, and **GI-GRAPH-11**/tenancy for Task A3. If implementation reveals a rule not covered by those, stop and escalate — do not invent an invariant in passing.
- **Traversal depth cap is 3** (GI-GRAPH-04). Never raise it.
- **Foreign or missing ids answer 404, never 403**, on reads and writes alike. No graph route may become an existence oracle.
- **The tenant comes from the mTLS certificate only** (GI-GRAPH-02). Never from a request body, query string or header.
- **Edges are induced on the post-filter node set**, never on the raw traversal.
- Every route already sits behind `@require_valid_client_certificate` and `KNOWLEDGE_GRAPH_ENABLED`. Do not add a second gate.
- Tests carry the repo's registered markers: `pytestmark = [pytest.mark.api, pytest.mark.l2("L2-KNOWLEDGE-GRAPH")]`.
- Run tests from `knovas-software/app/`.

---

## Part Overview

| Task | Deliverable | Blocks |
| --- | --- | --- |
| A1 | `neighbor_edges()` on the real and fake repositories | A2 |
| A2 | `include_edges=true` on the neighbours route | Platform E2 |
| A3 | `target_node_type_id` on schema attributes | Platform B3, E4 |

---

### Task A1: `neighbor_edges()` on both repositories

The neighbours route returns nodes and a hop count with no edges, so a caller
cannot draw the neighbourhood. This task adds the query only; the route change
is A2.

**Files:**
- Modify: `knovas-software/app/src/services/knowledge_graph/repository.py` (after `list_edges`, ~line 330)
- Modify: `knovas-software/app/tests/fixtures/fake_kg_repository.py` (after `list_edges`, ~line 250)
- Test: `knovas-software/app/tests/test_graph_api.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `KnowledgeGraphRepository.neighbor_edges(client_id, node_ids) -> list[dict]` — every `kg_edges` row of this tenant whose `node_lo` **and** `node_hi` are both in `node_ids`. `node_ids` is any iterable of uuid strings. An empty or single-element `node_ids` returns `[]`. `FakeKnowledgeGraphRepository` gets the same signature.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_graph_api.py`:

```python
class TestNeighborEdges:
    """Edges induced on a node set — the query behind include_edges."""

    def test_returns_only_edges_with_both_endpoints_in_the_set(self, repo, client_id):
        a = repo.create_node(client_id, "A")["id"]
        b = repo.create_node(client_id, "B")["id"]
        c = repo.create_node(client_id, "C")["id"]
        repo.create_edge(client_id, a, b, relation="knows")
        repo.create_edge(client_id, b, c, relation="knows")

        rows = repo.neighbor_edges(client_id, {str(a), str(b)})

        assert len(rows) == 1
        assert rows[0]["relation"] == "knows"
        assert {str(rows[0]["node_lo"]), str(rows[0]["node_hi"])} == {str(a), str(b)}

    def test_a_single_node_has_no_induced_edges(self, repo, client_id):
        a = repo.create_node(client_id, "A")["id"]
        b = repo.create_node(client_id, "B")["id"]
        repo.create_edge(client_id, a, b, relation="knows")

        assert repo.neighbor_edges(client_id, {str(a)}) == []

    def test_an_empty_set_is_not_a_full_table_scan(self, repo, client_id):
        a = repo.create_node(client_id, "A")["id"]
        b = repo.create_node(client_id, "B")["id"]
        repo.create_edge(client_id, a, b, relation="knows")

        assert repo.neighbor_edges(client_id, set()) == []
```

Reuse the `repo` and `client_id` fixtures already defined in that module. If
they are class-scoped, add these tests inside the class that owns them rather
than at module level.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_graph_api.py::TestNeighborEdges -v`
Expected: FAIL with `AttributeError: 'FakeKnowledgeGraphRepository' object has no attribute 'neighbor_edges'`

- [ ] **Step 3: Implement on the real repository**

In `src/services/knowledge_graph/repository.py`, directly after `list_edges`:

```python
    def neighbor_edges(self, client_id, node_ids):
        """Edges whose BOTH endpoints are in ``node_ids`` (the induced subgraph).

        Both endpoints, not either: an edge with one endpoint outside the set
        names a node the caller was not given, which is the graph shape leaking
        around the node ACL. The route passes the already-filtered neighbour
        set for exactly this reason (GI-GRAPH-12).
        """
        ids = [str(n) for n in node_ids]
        if len(ids) < 2:
            return []
        return self._query(
            "SELECT * FROM kg_edges "
            "WHERE client_id = %s AND node_lo = ANY(%s) AND node_hi = ANY(%s)",
            (client_id, ids, ids),
        )
```

- [ ] **Step 4: Implement on the fake repository**

In `tests/fixtures/fake_kg_repository.py`, directly after `list_edges`:

```python
    def neighbor_edges(self, client_id, node_ids):
        ids = {str(n) for n in node_ids}
        if len(ids) < 2:
            return []
        return [
            dict(r) for r in self.tables["kg_edges"].values()
            if str(r["client_id"]) == str(client_id)
            and str(r["node_lo"]) in ids and str(r["node_hi"]) in ids
        ]
```

- [ ] **Step 5: Declare it on the interface**

In `src/interfaces/IKnowledgeGraphRepository.py`, beside the other edge
methods, add the abstract signature matching the style of the neighbours
declaration already there:

```python
    @abstractmethod
    def neighbor_edges(self, client_id, node_ids):
        """Edges whose both endpoints are in node_ids."""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_graph_api.py::TestNeighborEdges -v`
Expected: 3 passed

- [ ] **Step 7: Run the surrounding suite for regressions**

Run: `pytest tests/test_graph_api.py tests/test_kg_object_acl.py -q`
Expected: all pass — this task adds a method and changes no existing path.

- [ ] **Step 8: Commit**

```bash
git add src/services/knowledge_graph/repository.py \
        src/interfaces/IKnowledgeGraphRepository.py \
        tests/fixtures/fake_kg_repository.py \
        tests/test_graph_api.py
git commit -m "feat(graph): neighbor_edges returns the induced subgraph (SS-315)"
```

---

### Task A2: `include_edges=true` on the neighbours route

**Files:**
- Modify: `knovas-software/app/src/api/graph_api.py:795-826` (`node_neighbors`)
- Test: `knovas-software/app/tests/test_kg_object_acl.py`
- Modify: `KnowledgeBase/docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`

**Interfaces:**
- Consumes: `repo.neighbor_edges(client_id, node_ids)` from A1; `guard.filter_edges(principal, edges, visible_node_ids)` which already exists in `services/knowledge_graph/graph_access.py:237`.
- Produces: `GET /secured/graph/nodes/<node_id>/neighbors?depth=N&include_edges=true` returning `{"neighbors": [...], "edges": [...], "depth_applied": int, "depth_cap": 3, "truncated": bool}`. The `edges` key is **absent** unless `include_edges` is truthy — not present-and-empty, so a caller can tell "not requested" from "none found".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kg_object_acl.py`, in the class that already exercises
graph routes through the Flask test client (follow the surrounding fixtures for
building `principal` and posting the certificate info):

```python
class TestNeighborhoodEdges:
    """include_edges must never disclose a node the caller cannot see."""

    def test_edges_are_absent_unless_requested(self, api, anchor, neighbour):
        body = api.get(f"/secured/graph/nodes/{anchor}/neighbors?depth=1")
        assert "edges" not in body["data"]

    def test_edges_are_returned_when_requested(self, api, anchor, neighbour):
        body = api.get(
            f"/secured/graph/nodes/{anchor}/neighbors?depth=1&include_edges=true")
        assert [str(e["node_lo"]) for e in body["data"]["edges"]] == [str(anchor)]
        assert body["data"]["edges"][0]["relation"] == "knows"

    def test_an_edge_to_a_restricted_neighbour_is_withheld(
            self, api_as_hr, anchor, legal_only_neighbour):
        """The node is filtered out of `neighbors`; its edge must go with it.
        Returning the edge would disclose that the node exists and that it is
        attached to something the caller can see (GI-GRAPH-12)."""
        body = api_as_hr.get(
            f"/secured/graph/nodes/{anchor}/neighbors?depth=1&include_edges=true")
        returned = {str(n["id"]) for n in body["data"]["neighbors"]}
        assert str(legal_only_neighbour) not in returned
        for edge in body["data"]["edges"]:
            assert str(legal_only_neighbour) not in (
                str(edge["node_lo"]), str(edge["node_hi"]))

    def test_a_restricted_edge_between_two_visible_nodes_is_withheld(
            self, api_as_hr, anchor, neighbour, legal_only_edge):
        """filter_edges applies the edge's OWN acl too, not only its endpoints."""
        body = api_as_hr.get(
            f"/secured/graph/nodes/{anchor}/neighbors?depth=1&include_edges=true")
        assert str(legal_only_edge) not in {str(e["id"]) for e in body["data"]["edges"]}
```

Build the fixtures from the module's existing helpers: `anchor` and `neighbour`
are unrestricted nodes joined by a `knows` edge; `legal_only_neighbour` is a
node created with `acl={"access_group_ids": ["g-legal"], ...}` attached to the
anchor; `legal_only_edge` is an edge between two unrestricted nodes carrying
the `legal` closure itself. `api_as_hr` is the existing test client wired with
`principal("hr")`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_kg_object_acl.py::TestNeighborhoodEdges -v`
Expected: FAIL — `KeyError: 'edges'` on the second test; the first passes vacuously and must stay passing.

- [ ] **Step 3: Implement the route change**

In `src/api/graph_api.py`, replace the response construction at the end of
`node_neighbors` (currently lines 819-826):

```python
    rows = guard.filter_objects(principal, repo.neighbors(client_id, node_id,
                                                          depth=applied_depth))
    payload = {
        "neighbors": _serialize(rows),
        "depth_applied": applied_depth,
        "depth_cap": 3,
        "truncated": requested_depth > 3,
    }
    if request.args.get("include_edges", "false").lower() == "true":
        # Induce on the FILTERED set plus the anchor. Building this from the
        # raw walk would name endpoints the caller was not given — the graph
        # shape leaking around the node ACL (GI-GRAPH-12). filter_edges then
        # applies each edge's own assignment on top.
        visible_ids = {str(r["id"]) for r in rows} | {str(node_id)}
        payload["edges"] = _serialize(guard.filter_edges(
            principal, repo.neighbor_edges(client_id, visible_ids), visible_ids))
    _bill("graph_read", client_id)
    return response_service.create_success_response("Neighbors", payload)
```

Leave everything above it — the uuid check, the `object_is_visible` gate, the
depth parsing and the GI-GRAPH-04 clamp — untouched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_kg_object_acl.py::TestNeighborhoodEdges -v`
Expected: 4 passed

- [ ] **Step 5: Run the graph and RBAC suites for regressions**

Run: `pytest tests/test_graph_api.py tests/test_kg_object_acl.py tests/test_kg_rbac_routes.py tests/test_kg_rbac_composition.py -q`
Expected: all pass. The default response shape is unchanged, so nothing that
ignores `include_edges` can break.

- [ ] **Step 6: Document the parameter**

In `docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`, in the neighbours
route section, add:

```markdown
`include_edges` (optional, default `false`) — when `true`, the response also
carries an `edges` array: the edges induced on the returned neighbour set plus
the anchor node. Edges are filtered by the same rule as nodes, and an edge with
an endpoint the caller may not see is never returned (GI-GRAPH-12). The key is
absent when the parameter is not sent, so "not requested" is distinguishable
from "none found".
```

- [ ] **Step 7: Commit**

```bash
git add src/api/graph_api.py tests/test_kg_object_acl.py \
        docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md
git commit -m "feat(graph): include_edges on the neighbours route (SS-315)

Edges are induced on the post-filter neighbour set and run through
GraphAccessGuard.filter_edges, so an edge is never returned whose endpoint
the caller cannot see. Reuses GI-GRAPH-12; no new invariant."
```

---

### Task A3: `target_node_type_id` on schema attributes

An `entity_ref` attribute today records that a node references *some* node, not
which kind. The Platform's node picker cannot be filtered without this, and a
type-level expectation ("a Mandat has a responsible Person") cannot be re-read
after it is written.

**Files:**
- Create: `knovas-software/DB/migrations/20260902_kg_attribute_target_type.sql`
- Modify: `knovas-software/DB/init.sql` (the `kg_node_type_attribute` block, ~line 530)
- Modify: `knovas-software/app/src/services/knowledge_graph/repository.py` (`add_attribute`, `update_attribute`)
- Modify: `knovas-software/app/src/api/graph_api.py:535-612` (`node_type_schema`, `modify_schema_attribute`)
- Modify: `knovas-software/app/tests/fixtures/fake_kg_repository.py` (`add_attribute`)
- Test: `knovas-software/app/tests/test_graph_api.py`
- Modify: `KnowledgeBase/docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`

**Interfaces:**
- Consumes: nothing from A1/A2.
- Produces:
  - `POST /secured/graph/node-types/<id>/schema` accepts `target_node_type_id` (uuid string or null).
  - `PATCH /…/schema/<aid>` accepts the same.
  - `GET /…/schema` returns `target_node_type_id` on every attribute.
  - Rejections: `target_node_type_id` with a non-`entity_ref` datatype → **422**, error code `target_type_requires_entity_ref`. A target node type that does not exist in the caller's tenant → **404** (never 403, never a distinct "foreign" message — that would be an existence oracle).
  - `KnowledgeGraphRepository.add_attribute(..., target_node_type_id=None)` and `update_attribute(client_id, attribute_id, target_node_type_id=...)`.

- [ ] **Step 1: Write the migration**

Create `DB/migrations/20260902_kg_attribute_target_type.sql`:

```sql
-- Target node type for entity_ref schema attributes (SS-315).
--
-- An entity_ref attribute materialises a typed edge, but until now it did not
-- record WHICH type it points at. Without that a client cannot offer a filtered
-- picker, and a type-level expectation ("a Mandat has a responsible Person")
-- cannot be re-read after it is written.
--
-- Nullable on purpose: every existing attribute keeps a null target and behaves
-- exactly as it does today. The composite FK carries client_id so a
-- cross-tenant target is impossible at the DATABASE, matching every other
-- kg_* reference (GI-GRAPH-01/02).
--
-- Design: docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md (5.2)

ALTER TABLE kg_node_type_attribute
    ADD COLUMN IF NOT EXISTS target_node_type_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'fk_kg_attribute_target_node_type') THEN
        ALTER TABLE kg_node_type_attribute
            ADD CONSTRAINT fk_kg_attribute_target_node_type
            FOREIGN KEY (target_node_type_id, client_id)
            REFERENCES kg_node_type (id, client_id) ON DELETE SET NULL;
    END IF;
END $$;

-- Only entity_ref may carry a target. Enforced here as well as in the service
-- so a direct write cannot produce a row the API would have refused.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_kg_attribute_target_is_entity_ref') THEN
        ALTER TABLE kg_node_type_attribute
            ADD CONSTRAINT ck_kg_attribute_target_is_entity_ref
            CHECK (target_node_type_id IS NULL OR datatype = 'entity_ref');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_kg_attribute_target
    ON kg_node_type_attribute (client_id, target_node_type_id)
    WHERE target_node_type_id IS NOT NULL;
```

Mirror the column, constraints and index into the `kg_node_type_attribute`
block of `DB/init.sql` so a fresh database and a migrated one agree.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_graph_api.py`:

```python
class TestSchemaAttributeTargetType:
    def test_entity_ref_attribute_stores_and_returns_its_target(self, api, type_id):
        person = api.post("/secured/graph/node-types", {"name": "Person"})["data"]["node_type"]
        created = api.post(f"/secured/graph/node-types/{type_id}/schema", {
            "name": "Zustaendig", "datatype": "entity_ref",
            "target_node_type_id": person["id"],
        })
        assert created["data"]["attribute"]["target_node_type_id"] == person["id"]

        listed = api.get(f"/secured/graph/node-types/{type_id}/schema")
        assert listed["data"]["attributes"][0]["target_node_type_id"] == person["id"]

    def test_a_target_on_a_text_attribute_is_422(self, api, type_id):
        person = api.post("/secured/graph/node-types", {"name": "Person"})["data"]["node_type"]
        response = api.post_raw(f"/secured/graph/node-types/{type_id}/schema", {
            "name": "Notiz", "datatype": "text",
            "target_node_type_id": person["id"],
        })
        assert response.status_code == 422
        assert response.get_json()["error_code"] == "target_type_requires_entity_ref"

    def test_an_unknown_target_is_404_not_403(self, api, type_id):
        """A distinct message for 'exists but not yours' would be an oracle."""
        response = api.post_raw(f"/secured/graph/node-types/{type_id}/schema", {
            "name": "Zustaendig", "datatype": "entity_ref",
            "target_node_type_id": str(uuid.uuid4()),
        })
        assert response.status_code == 404

    def test_a_target_may_be_added_later_by_patch(self, api, type_id):
        person = api.post("/secured/graph/node-types", {"name": "Person"})["data"]["node_type"]
        attribute = api.post(f"/secured/graph/node-types/{type_id}/schema", {
            "name": "Zustaendig", "datatype": "entity_ref",
        })["data"]["attribute"]

        patched = api.patch(
            f"/secured/graph/node-types/{type_id}/schema/{attribute['id']}",
            {"target_node_type_id": person["id"]})

        assert patched["data"]["attribute"]["target_node_type_id"] == person["id"]

    def test_an_existing_attribute_without_a_target_still_works(self, api, type_id):
        attribute = api.post(f"/secured/graph/node-types/{type_id}/schema", {
            "name": "Zustaendig", "datatype": "entity_ref",
        })["data"]["attribute"]
        assert attribute["target_node_type_id"] is None
```

Follow the module's existing helper style for `api.post` / `api.get` /
`api.patch`; add a `post_raw` returning the Flask response object if the module
does not already expose one.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_graph_api.py::TestSchemaAttributeTargetType -v`
Expected: FAIL — `KeyError: 'target_node_type_id'`

- [ ] **Step 4: Widen the repository**

In `src/services/knowledge_graph/repository.py`, change `add_attribute` to
accept and persist the column:

```python
    def add_attribute(self, client_id, node_type_id, name, datatype, required=False,
                      description=None, sort_order=0, enum_values=None,
                      target_node_type_id=None):
        rows = self._query(
            """
            INSERT INTO kg_node_type_attribute
                (client_id, node_type_id, name, datatype, required, description,
                 sort_order, enum_values, target_node_type_id)
            SELECT %s, id, %s, %s, %s, %s, %s, %s, %s
              FROM kg_node_type WHERE client_id = %s AND id = %s
            RETURNING *
            """,
            (client_id, name, datatype, required, description, sort_order,
             Json(enum_values) if enum_values is not None else None,
             target_node_type_id, client_id, node_type_id),
        )
        return self._one(rows)
```

and add `target_node_type_id` to the `allowed` set inside `update_attribute`.

- [ ] **Step 5: Mirror it on the fake repository**

In `tests/fixtures/fake_kg_repository.py`, give `add_attribute` the same
keyword with default `None` and store it on the row, so schema reads in tests
carry the key whether or not it was set.

- [ ] **Step 6: Validate in the route**

In `src/api/graph_api.py`, add a shared helper above `node_type_schema`:

```python
def _validated_target_type(repo, client_id, datatype, body, response_service):
    """(target_node_type_id, error_response). Absent key -> (None, None).

    A target on a non-entity_ref attribute is 422: the caller sent a coherent
    request that asks for something the model does not have, and telling them
    which field is wrong costs nothing. An unknown target is 404, like every
    other unreachable id on this blueprint — a distinct message for "exists but
    belongs to another tenant" would be an existence oracle.
    """
    if "target_node_type_id" not in body:
        return None, None
    target = body.get("target_node_type_id")
    if target in (None, ""):
        return None, None
    if datatype != "entity_ref":
        return None, response_service.create_error_response(
            "target_node_type_id is only valid on an entity_ref attribute",
            status_code=422, error_code="target_type_requires_entity_ref")
    if not _valid_uuid(str(target)) or repo.get_node_type(client_id, target) is None:
        return None, response_service.create_not_found_response("Node type")
    return str(target), None
```

In the `POST` branch of `node_type_schema`, after the `enum_values` check and
before `repo.add_attribute`:

```python
    target, target_err = _validated_target_type(
        repo, client_id, datatype, body, response_service)
    if target_err:
        return target_err
```

and pass `target_node_type_id=target` into `repo.add_attribute`.

In `modify_schema_attribute`'s `PATCH` branch, resolve the datatype from the
stored attribute — a PATCH does not carry it — then apply the same check:

```python
    target, target_err = _validated_target_type(
        repo, client_id, attribute.get("datatype"), body, response_service)
    if target_err:
        return target_err
    fields = {k: body[k] for k in ("name", "description", "required", "sort_order",
                                  "enum_values") if k in body}
    if "target_node_type_id" in body:
        fields["target_node_type_id"] = target
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_graph_api.py::TestSchemaAttributeTargetType -v`
Expected: 5 passed

- [ ] **Step 8: Run the migration against a local database**

Run: `python src/CLI/manage_migrations.py --apply` (or the project's documented
migration entry point) against the local compose Postgres.
Expected: `20260902_kg_attribute_target_type.sql` applies cleanly, and applying
it twice is a no-op — every statement is `IF NOT EXISTS` guarded.

- [ ] **Step 9: Run the full graph suite**

Run: `pytest tests/test_graph_api.py tests/test_kg_object_acl.py -q`
Expected: all pass.

- [ ] **Step 10: Document the field**

In `docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`, in the schema
attribute section, add `target_node_type_id` to the body and response tables,
stating: valid only when `datatype` is `entity_ref`; `422
target_type_requires_entity_ref` otherwise; `404` for an unknown or foreign
target; null on every attribute created before this field existed.

- [ ] **Step 11: Commit**

```bash
git add DB/migrations/20260902_kg_attribute_target_type.sql DB/init.sql \
        src/services/knowledge_graph/repository.py src/api/graph_api.py \
        tests/fixtures/fake_kg_repository.py tests/test_graph_api.py \
        docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md
git commit -m "feat(graph): target_node_type_id on entity_ref attributes (SS-315)"
```

---

## Verification

Run from `knovas-software/app/`:

```bash
pytest tests/test_graph_api.py tests/test_kg_object_acl.py \
       tests/test_kg_rbac_routes.py tests/test_kg_rbac_composition.py -q
```

Then confirm by hand against the dev tenant, because neither change has run
against a live instance:

1. `GET /secured/graph/nodes/<id>/neighbors?depth=1` — response has **no**
   `edges` key.
2. `…&include_edges=true` — response has `edges`, and every edge's `node_lo`
   and `node_hi` appear in `neighbors` or are the anchor itself.
3. `POST …/schema` with `datatype=text` and a `target_node_type_id` — 422,
   `target_type_requires_entity_ref`.
4. `POST …/schema` with `datatype=entity_ref` and a random uuid — 404.
5. `GET …/schema` — every attribute carries `target_node_type_id`, null for
   pre-existing ones.

## Requirement traceability

| Spec § | Requirement | Task |
| --- | --- | --- |
| §5.1 | Edges in the neighbourhood response | A1, A2 |
| §5.1 | Edges induced on the post-filter set (GI-GRAPH-12) | A2 step 3 |
| §5.1 | Opt-in flag; `edges` absent when not requested | A2 steps 1, 3 |
| §5.2 | `target_node_type_id`, entity_ref only | A3 |
| §5.2 | Existing attributes keep a null target | A3 steps 1, 2 |
| §8.5 | Edges only as visible as their least visible endpoint | A2 steps 1, 3 |

## Related

- Design: `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md`
- Platform plan: `docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md`
- `docs/Docs/01_SYSTEM/Golden_Invariants.md` — GI-GRAPH-04, GI-GRAPH-11, GI-GRAPH-12
- **Docs inconsistency to raise separately:** GI-GRAPH-11 describes graph topology as deliberately not access-controlled while GI-GRAPH-12 gives nodes and edges a minimum required group, and `graph_access.py`'s module docstring still carries the pre-`20260804_kg_object_acl.sql` text. GI-GRAPH-12 matches the code. Not fixed by this plan; it needs a backend owner's decision.
