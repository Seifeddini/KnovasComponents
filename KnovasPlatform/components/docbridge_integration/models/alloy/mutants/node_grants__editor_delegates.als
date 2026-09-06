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
