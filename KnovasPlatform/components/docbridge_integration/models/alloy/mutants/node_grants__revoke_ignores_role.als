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
