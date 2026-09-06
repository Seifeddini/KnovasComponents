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
