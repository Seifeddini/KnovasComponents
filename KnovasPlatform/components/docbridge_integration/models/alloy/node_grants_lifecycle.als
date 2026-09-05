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
