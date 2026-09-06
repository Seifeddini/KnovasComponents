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
