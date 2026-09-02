# Broker identity cutover

This runbook is the release gate for moving a Knovas tenant to brokered
per-user identity. Perform the steps in order. Do not enable `BROKERED` until
the evidence required by each preceding step is complete.

## Preconditions

- The KnowledgeBase Secure API must remain usable in `ENFORCING` mode while it
  verifies a `principal_assertion` when one is present but does not require one.
  The ENFORCING pytest for that behavior belongs in the KnowledgeBase repo, not
  this repo. Its wire contract is the JSON body field `principal_assertion`,
  not a request header.
- `identity.broker_key_dir` must exist before identity is enabled. Confirm its
  ownership, permissions, backup, and persistence; do not rely on startup to
  create the directory.
- Keep the tenant in `DISABLED` or `ENFORCING` while preparing the cutover.
  Flipping to `BROKERED` before assertions flow locks everyone out.
- Operators must review GI-BROKER-01 through GI-BROKER-04 in
  `KnowledgeBase/docs/superpowers/plans/2026-09-02-kb-auth-attribution-and-topology.md`.
  The Golden Invariants catalog is owned by KnowledgeBase and is not duplicated
  here.

## Ordered cutover

### 1. Register the broker public key

Register the Platform broker public key and its `kid` against the intended
tenant. Verify the registered key and tenant match the Platform deployment.
The tenant stays `DISABLED` or `ENFORCING` during this step.

Gate: key registration is confirmed without changing the tenant to
`BROKERED`.

### 2. Upgrade the Platform

Deploy the Platform version that signs per-user assertions. Assertions must
start flowing in the JSON request body as `principal_assertion`.

Gate: the upgraded Platform is healthy, the broker key loads successfully, and
requests continue to work while the tenant remains `DISABLED` or `ENFORCING`.

### 3. Confirm every route carries assertions

Exercise every Platform route that calls the KnowledgeBase Secure API,
including error and less-frequently used paths. Confirm at the Secure API
boundary that each request body contains a valid `principal_assertion` for the
acting user. Checking most routes is not sufficient.

Gate: retain route-by-route evidence. Any route without an assertion blocks
cutover.

### 4. Flip the tenant to BROKERED

Only after all earlier gates pass, change the tenant from `DISABLED` or
`ENFORCING` to `BROKERED`. Repeat the route inventory and confirm authorized
users retain access while rejected identities fail closed.

## Rollback

If authentication or authorization fails after cutover, flip the tenant back
to `ENFORCING`. This restores service immediately while assertions can continue
to flow and be investigated. Do not delete or regenerate the broker key as a
rollback action.

## Review boundary

The Platform `_with_principal` and `broker_key` paths are reviewed on this
branch through a controller-owned adversarial pass. Review of KnowledgeBase
`services/rbac/assertion.py` and `principal_resolver.py` is a separate
KnowledgeBase responsibility; this runbook does not claim those files were
reviewed here.

## Residual risk

The cutover does not remove these accepted pilot risks:

- The Platform host holds both the tenant certificate and the broker signing
  key.
- MFA and OIDC are still dropped.
- Ranking-signal leakage across walls is unverified. Authorization must be
  enforced on every read path; do not describe this as leaving “no trace.”
