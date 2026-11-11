## Serves

<!-- Name the item this change serves: a bet's work package (BB-02, …), a spec item (SPEC-…), a plan (FEAT-…),
a risk (RISK-…) or a decision (DEC-…). Or tick maintenance. Code that serves no item shows up as scope creep in the
weekly agenda (KnowledgeBase: docs/ModernDocs/steering/README.md). -->

- Item(s):
- [ ] Maintenance (no bet; kept to about 20 % of capacity)

## Change class

<!-- The highest class among the files touched (tenancy, mTLS, certificates, secrets, migrations, CI and Golden Invariants make a file critical). -->

- [ ] Routine — inside an approved spec, outside the security list
- [ ] Sensitive — public API contract or data model (approved change), performance-critical path, dependency upgrade: the engineering lead approves
- [ ] Critical — on the security list (tenancy, mTLS, certificates, secrets, migrations, CI, Golden Invariants): management approves
- [ ] Production — release or production change: both founders approve

## Evidence

<!-- Commands run and their outcome: tests, Alloy, benchmarks, docs completeness, the component tests.
The verifier, not the builder, attaches the evidence bundle. Whoever requested a change never approves it. -->

- Built by:
- Verified by:
