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
