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
