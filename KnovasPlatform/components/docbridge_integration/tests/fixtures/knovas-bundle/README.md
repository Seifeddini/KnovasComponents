# Knovas definitions bundle (test fixture)

The definitions the views read for the Knovas tenant, as one bundle in KnowledgeBase's format (KB-7,
`docs/ModernDocs/client-zero/generated/README.md` in KnowledgeBase). It replaces the copies that were
vendored under `src/graph_views/definitions/` until KC-1, with the same content: KnowledgeBase commit
`c325397d` (`header.source_commit`). The views tests run on it, so they test what the Knovas tenant
saw before the bundle.

| File | What it is |
|---|---|
| `bundle.json` | The bundle. Parsed sections are the vendored YAML files as parsed (`types` without `tenant`). Normalised sections are the vendored JSON files without `generated_at` and `source`, and `processes.report_rules[]` without `computed_by`. `profiles` and `health` are empty: they did not exist at that commit. |
| `process-completeness.json`, `docs-completeness.json`, `suggestions.json` | The computed reports the header names (`header.reports`), unchanged. |

**Do not edit it by hand.** The reader recomputes `content_hash` and refuses a file whose sections do
not match it. To test against newer definitions, use a bundle that KnowledgeBase's runner built
(`python -m brain_runner bundle`), not an edited copy of this one.
