"""Copy KnowledgeBase's ontology files the views read into src/graph_views/definitions/.

The views fall back to these copies when the tenant's definition nodes cannot be
read, and take display names and descriptions from them. Each copy is pinned to
the KnowledgeBase commit it came from (SOURCE.json: commit and sha256 per file);
tests/test_views_definitions.py fails when a copy and its pin diverge.

    py -3 scripts/vendor_kb_definitions.py --kb E:/Knovas/KnowledgeBase --commit <sha>

Pinned to KnowledgeBase master; re-run it after a KnowledgeBase merge that changes these files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = "docs/ModernDocs/client-zero"
FILES = {
    "process-definitions.json": f"{ROOT}/ontology/generated/process-definitions.json",
    "spine-definitions.json": f"{ROOT}/ontology/generated/spine-definitions.json",
    "process-definition.schema.json": f"{ROOT}/ontology/schema/process-definition.schema.json",
    "spine-definition.schema.json": f"{ROOT}/ontology/schema/spine-definition.schema.json",
    "descriptions.yaml": f"{ROOT}/ontology/descriptions.yaml",
    "relations.yaml": f"{ROOT}/ontology/relations.yaml",
    "terms.yaml": f"{ROOT}/ontology/terms.yaml",
    "owners.yaml": f"{ROOT}/ontology/owners.yaml",
    # Step 7: where a person edits what the definitions use (DEC-0022), and ranked candidates for what is
    # missing (DEC-0025). Since DEC-0025 the completeness report lives under generated/.
    "edit-map.json": f"{ROOT}/ontology/generated/edit-map.json",
    "edit-map.schema.json": f"{ROOT}/ontology/schema/edit-map.schema.json",
    "suggestions.json": f"{ROOT}/ontology/generated/suggestions.json",
    "process-completeness.json": f"{ROOT}/ontology/generated/process-completeness.json",
    # Step 8: documentation pages (DEC-0028) and their completeness report (DEC-0029)
    "page-definitions.json": f"{ROOT}/ontology/generated/page-definitions.json",
    "page-definition.schema.json": f"{ROOT}/ontology/schema/page-definition.schema.json",
    "docs-completeness.json": f"{ROOT}/ontology/generated/docs-completeness.json",
}
TARGET = Path(__file__).resolve().parents[1] / "src" / "graph_views" / "definitions"


def digest(data: bytes) -> str:
    """sha256 over LF line endings, so a CRLF checkout keeps its pin."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb", required=True, help="path of a KnowledgeBase checkout")
    ap.add_argument("--commit", required=True, help="commit or branch to copy from")
    ap.add_argument("--only", nargs="*", help="vendor only these files (names as in FILES)")
    args = ap.parse_args(argv)

    commit = subprocess.run(["git", "-C", args.kb, "rev-parse", args.commit], check=True,
                            capture_output=True, text=True).stdout.strip()
    source_path = TARGET / "SOURCE.json"
    source = json.loads(source_path.read_text(encoding="utf-8")) if source_path.exists() else {"files": {}}
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, path in FILES.items():
        if args.only and name not in args.only:
            continue
        shown = subprocess.run(["git", "-C", args.kb, "show", f"{commit}:{path}"], capture_output=True)
        if shown.returncode != 0:
            # Not committed at that commit (process-completeness.json was untracked
            # on 2026-10-27): keep the existing copy and its pin, say so.
            print(f"{name}  not in {commit[:12]}; skipped", file=sys.stderr)
            continue
        data = shown.stdout
        (TARGET / name).write_bytes(data)
        source["files"][name] = {"path": path, "commit": commit, "sha256": digest(data)}
        print(f"{name}  {commit[:12]}  {digest(data)[:12]}")
    source["repository"] = "Seifeddini/KnowledgeBase"
    source_path.write_text(json.dumps(source, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
