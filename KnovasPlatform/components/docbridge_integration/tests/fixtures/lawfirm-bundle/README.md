# Law-firm definitions bundle (test fixture)

The law firm of KnowledgeBase case study 1 (`docs/ModernDocs/client-zero/case-study-1/rev-a.md`,
question 1): contracts filed under matters, a contract review process, a matter page and the edit sources
for both. No spines, descriptions, owners or health. Its sections are empty, as a company without those
parts gets them.

KnowledgeBase has no `scripts/brain_runner/examples/lawfirm`. The configuration under `config/` is the
law firm exactly as KnowledgeBase's own tests write it (`scripts/brain_runner/tests/test_bundle.py`:
`LAW_MANIFEST`, `LAW_TERMS`, `LAW_RELATIONS`, `LAW_MAPPING`, `LAW_PROCESSES`, `LAW_PAGES`, `LAW_EDITS`).
`config/profiles/partner.yaml` is added here (KC-2): the partners' profile, delivered through the
Platform, with one view, the contract review board. KnowledgeBase's own test gives the partner profile
no views. It is valid against KnowledgeBase's `profile.schema.json`.
`bundle.json` was built from it by KnowledgeBase's runner:

```bash
cd <KnowledgeBase>/scripts
python -m brain_runner bundle \
  --config <KnovasComponents>/KnovasPlatform/components/docbridge_integration/tests/fixtures/lawfirm-bundle/config/brain-config.yaml \
  --out    <KnovasComponents>/KnovasPlatform/components/docbridge_integration/tests/fixtures/lawfirm-bundle/bundle.json
```

The same command with `--check` passes, and exits 1 when the file is no longer the bundle of its configuration.
