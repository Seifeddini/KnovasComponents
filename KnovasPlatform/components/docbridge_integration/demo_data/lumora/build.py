"""Build the Lumora demo (``serve_views_demo.py``) from a KnowledgeBase checkout.

    python demo_data/lumora/build.py --config <brain-config.yaml> [--at 2026-10-30T08:00:00+00:00]

``--config`` is the ``brain-config.yaml`` of Knovas's own configuration in a KnowledgeBase checkout
(its Client Zero folder under ``docs/ModernDocs``); the bundle is read from the folder of its generated
definitions, and ``brain_runner`` from the checkout's ``scripts``. No default: the product names no
tenant's folders (``tests/test_product_has_no_knovas_defaults.py``).

Writes, next to this file:

- ``bundle.json``: Knovas's definitions bundle (KnowledgeBase KB-7) with Lumora's names: the
  types, relations, descriptions, processes, spines, pages, profiles and health are Knovas's;
  every Knovas name, person and tenant id is replaced, ``owners`` names Lumora's people, and the
  edit map is empty (Lumora's sources are not KnowledgeBase's files). Its hash is recomputed.
- ``graph.json``: Lumora's tenant as an offline graph (``graph_views/offline.py``): the definition
  nodes as KnowledgeBase's loader writes them and the instances of ``company.py``.
- ``process-completeness.json``, ``docs-completeness.json``, ``findings.json``: computed over that
  graph by KnowledgeBase's own ``brain_runner evaluate`` with Knovas's configuration (its Client
  Zero extension left out), so the R-rule results are the evaluator's, not invented here.

The build stops when a Knovas name or person is left anywhere in the output.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE))

from graph_views import bundle as B  # noqa: E402
from graph_views.offline import OfflineClient, OfflineGraph  # noqa: E402

import company as C  # noqa: E402

# First the company's name and its Client Zero programme, everywhere (the source tenant's id is added in main()) ...
GENERIC = [
    (re.compile(r"Client Zero"), "Lumora"), (re.compile(r"client[- ]zero"), "lumora"),
    (re.compile(r"Knovas's"), "Lumora's"), (re.compile(r"Knovas"), "Lumora"),
]
# ... then the examples that still say something only Knovas would, as they read after that. Longer phrase first.
RENAMES = [
    ("COMP-knovas, Lumora GmbH", "COMP-lumora, Lumora AG"),
    ("PROD-PLATFORM, the Lumora Platform", "PROD-DESK, Lumora Desk"),
    ("SYS-KNOVAS-SEMANTIX, the Lumora Semantix platform", "SYS-LUMORA-CORE, the Lumora Core platform"),
    ("the Platform for end users or the API for software vendors", "Lumora Desk for Treuhand firms or the Lumora API for software partners"),
    ("GOAL-2026-Q4-lumora-foundation, Lumora steers itself from its own brain by G1.",
     "GOAL-2026-Q4-first-ten-treuhand, ten Treuhand firms search their files with Lumora Desk."),
    ("FEAT-2026-10-25-lumora-phase-0, the plan for Phase 0 of Lumora.",
     "FEAT-2026-10-21-email-intake, the plan for e-mail intake."),
    ("Core, Cortex and Nexus are Lumora names", "Start, Team and Scale are Lumora names"),
    ("ROLE-ONTOLOGY-STEWARD, held by Siran.", "ROLE-ONTOLOGY-STEWARD, held by Nina."),
    ("pending Jiannis", "pending Lea"),
    ("ENV-knovas-cloudscale-prod, the production cluster at cloudscale", "ENV-lumora-prod, the production cluster"),
    ("ENV-knovas-cloudscale-prod", "ENV-lumora-prod"),
    ("knovas-software/app/src/api/graph_api.py", "lumora-core/search/query_api.py"),
    ("#create_node", "#search"),
    ("TIER-CORTEX, Core plus a simple brain, CHF 59.90 per user and month.",
     "TIER-TEAM, Start plus the e-mail inbox, CHF 49 per user and month."),
    ("Core, Cortex or Nexus", "Start, Team or Scale"), ("(Core, Cortex, Nexus)", "(Start, Team, Scale)"),
    ("20 Cortex seats for a law firm.", "12 Team seats for a Treuhand firm."),
    ("MS-CORTEX-DEFAULT, Cortex is the default module in every law-firm deal.",
     "MS-TEAM-DEFAULT, Team is the default tier in every Treuhand deal."),
    ("A pilot law firm in its first 90 days.", "A pilot Treuhand firm in its first 90 days."),
    ("ICP-03, law firms in Switzerland and Germany.", "ICP-01, Treuhand firms in Switzerland."),
    ("One of the nine large areas", "One of the large areas"),
    ("(Ingest, Search, Graph, \u2026). A proposal in Phase 0.", "(Ingest, Search)."),
    ("COMMIT-215b05af5f77, the merge of Lumora Phase 0.", "COMMIT-215b05af5f77, the merge of ranking v2."),
    ("The Lumora Phase 0 plan as an instance of the feature lifecycle, currently approved.",
     "The usage-billing plan as an instance of the feature lifecycle, currently approved."),
    ("Phase 1 of Lumora, ground truth, ended with gate G1 on 25 Oct 2026.", "Phase 1 of Lumora Desk, pilots, ended with gate G1 on 25 Oct 2026."),
    ("REL-2026.10-brain-mvp, the brain MVP on cloudscale prod, 24 Oct 2026.", "REL-2026.10.0, Microsoft sign-in in prod, 16 Oct 2026."),
    ("The brain MVP on cloudscale prod, 24 Oct 2026.", "Lumora Desk 2026.10.0 in prod, 16 Oct 2026."),
    ("SVC-secure-api, the mTLS API for clients, running on cloudscale prod and AKS dev.",
     "SVC-search-api, the search API for firms, running in prod and dev."),
    ("JOB-analytics-aggregation, the nightly analytics roll-up on cloudscale prod.", "JOB-usage-rollup, the nightly usage roll-up in prod."),
    ("prod has more than one environment (AKS and cloudscale)", "prod may have more than one environment"),
    ("AKS, cloudscale, AWS, Lambda Labs, Docker Compose.", "a Kubernetes cluster, a cloud region, Docker Compose."),
    ("L2-SECURE-API", "L2-QUERY"), ("SVC-secure-api", "SVC-search-api"), ("WF-ci/acr-push", "WF-ci/image-push"),
    ("REC-docs/ModernDocs/glossary/NodeType.md", "REC-handbook/glossary/NodeType.md"),
    ("docs/ModernDocs/lumora/", "handbook/"), ("ModernDocs", "the handbook"),
    ("KnowledgeBase.", "lumora/handbook."),
    ("PERS-siran", "PERS-marco"), ("PERS-jiannis", "PERS-lea"),
]
LEFTOVER = r"Knovas|Siran|Jiannis|Seifeddini|Sarlos|client[- ]zero|Cortex|Nexus|cloudscale|law.firm|KnowledgeBase|ModernDocs|secure-api"
OWNERS = {"version": 1,
          "aliases": {"lea": "PERS-lea", "lea brunner": "PERS-lea", "marco": "PERS-marco", "marco keller": "PERS-marco",
                      "nina": "PERS-nina", "nina hofer": "PERS-nina", "daniel": "PERS-daniel", "daniel frei": "PERS-daniel",
                      "sara": "PERS-sara", "sara meier": "PERS-sara", "engineering": "ROLE-ENGINE-OWNER"},
          "derived": {"plan author": "the owner of the plan the decision row comes from (the key before the first /)"},
          "placeholders": ["unknown", "tbd"]}


def rename(x: Any) -> Any:
    if isinstance(x, dict):
        return {rename(k): rename(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rename(v) for v in x]
    if x == "knovas":                     # the vocabulary level of Knovas's own types (terms.yaml `level`)
        return "lumora"
    if isinstance(x, str):
        for pattern, new in GENERIC:
            x = pattern.sub(new, x)
        for old, new in RENAMES:
            x = x.replace(old, new)
    return x


def leftovers(doc: Any, where: str, pattern: re.Pattern) -> list[str]:
    text = json.dumps(doc, ensure_ascii=False)
    return [f"{where}: ...{text[max(0, m.start() - 60):m.end() + 60]}..." for m in pattern.finditer(text)]


def source_bundle(path: Path) -> tuple[dict, dict, str]:
    """The KnowledgeBase bundle's header, sections and tenant id; the id joins the generic renames."""
    header, sections = B.check(json.loads(path.read_text(encoding="utf-8")), path.name)
    tenant = str(sections["types"].get("tenant") or "")
    if tenant:
        GENERIC.insert(0, (re.compile(re.escape(tenant)), C.TENANT))
        RENAMES.insert(0, (f"runs on tenant {tenant[:8]}.", "runs OCR on its own GPU."))
    return header, sections, tenant


def demo_bundle(header: dict, sections: dict, kb: Path) -> dict:
    sections = rename(copy.deepcopy(sections))
    sections["owners"] = OWNERS
    sections["edit_map"] = copy.deepcopy(B.EMPTY["edit_map"])
    commit = subprocess.run(["git", "-C", str(kb), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return {"header": {"format": B.FORMAT, "format_version": 1, "label": C.LABEL, "content_hash": B.content_hash(sections),
                       "source_commit": commit or header.get("source_commit"),
                       "sources": {"derived_from": f"Knovas's definitions bundle in KnowledgeBase ({header.get('content_hash')}), "
                                                   "names replaced by demo_data/lumora/build.py"},
                       "reports": {"process_completeness": "process-completeness.json",
                                   "docs_completeness": "docs-completeness.json"}},
            "sections": sections}


def type_descriptions(cfg: Any, sections: dict) -> dict[str, str]:
    """Each type's description as KnowledgeBase's ontology apply writes it (runner.ontology.type_description)."""
    from brain_runner.ontology import _Blank, _vals

    tmpl = (cfg.manifest.get("runner") or {}).get("ontology", {}).get("type_description") or {}
    descs = sections["descriptions"].get("types") or {}
    out = {}
    for t in sections["types"]["node_types"]:
        entry, d = dict(t), descs.get(t["name"])
        entry.setdefault("meta", False)
        header = tmpl["header"].format_map(_vals(entry)) if tmpl.get("header") else ""
        if d:
            text = tmpl["text"].format_map(_Blank({"name": t["name"], "en": t["name"], "de": "", "text": "", **d}))
            text = text.strip(tmpl.get("strip", ""))
        else:
            text = tmpl["fallback"].format_map(_vals(entry)) if tmpl.get("fallback") else ""
        out[t["name"]] = (f"{header} {text}" if header else text)[:2000]
    return out


def demo_graph(sections: dict, descriptions: dict[str, str]) -> OfflineGraph:
    g = OfflineGraph(C.TENANT, C.LABEL["name"])
    for name, desc in descriptions.items():
        g.type(name, description=desc)
    procs = {**sections["processes"], "processes": sections["processes"]["processes"]}
    C.build_company(g)
    C.build_definitions(g, procs, sections["spines"])
    C.build_instances(g)
    C.events(g)
    return g


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, type=Path, help="brain-config.yaml of Knovas's configuration in KnowledgeBase")
    ap.add_argument("--at", default="2026-10-30T08:00:00+00:00", help="evaluation time (the demo's 'today')")
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args(argv)
    config = args.config.resolve()
    kb = Path(subprocess.run(["git", "-C", str(config.parent), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip())
    sys.path.insert(0, str(kb / "scripts"))
    from brain_runner import config as CFG
    from brain_runner import evaluate as EV
    from brain_runner import graph as G

    cfg = CFG.load(config)
    generated = config.parent / Path(cfg.manifest["generated"]["processes"]).parent
    header, sections, tenant = source_bundle(generated / "bundle.json")
    leftover = re.compile(LEFTOVER + (f"|{re.escape(tenant[:8])}" if tenant else ""), re.I)
    doc = demo_bundle(header, sections, kb)
    cfg.manifest["runner"]["evaluate"]["extensions"] = []     # Knovas's own repository checks (Client Zero)
    graph = demo_graph(doc["sections"], type_descriptions(cfg, doc["sections"]))
    files = EV.evaluate(cfg, G.read(OfflineClient(graph)), dt.datetime.fromisoformat(args.at))
    files = {name: rename(json.loads(text)) for name, text in files.items()}

    problems = leftovers(doc["sections"], "bundle.json", leftover) + \
        leftovers({**graph.to_json(), "format": ""}, "graph.json", leftover)
    for name, data in files.items():
        problems += leftovers(data, name, leftover)
    if problems:
        print("Knovas names left in the demo:\n  " + "\n  ".join(problems[:20]), file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "bundle.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    graph.save(args.out / "graph.json")
    for name, data in files.items():
        (args.out / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(graph.nodes)} nodes, {len(graph.edges)} edges, {sum(map(len, graph.facts.values()))} facts")
    print("\n".join(EV.summary_lines({k: json.dumps(v) for k, v in files.items()})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
