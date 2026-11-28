#!/usr/bin/env python3
"""ontology/processes.yaml (written for people) -> ontology/generated/process-definitions.json (the form the
views and the weisungen mapping read: KnowledgeBase's process-definition schema, as the Platform pins it).

    python build_definitions.py [--check]

Adds what the schema asks for and people need not write: each step's order and key (STEP-<slug>-<id>),
transitions as {from, to, rework}, an empty status_map (the step is the governing fact a mapping sets) and
empty change classes. The Weisung's file and date go into the description.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "ontology/processes.yaml"
OUT = HERE / "ontology/generated/process-definitions.json"


def build() -> dict:
    doc = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    processes = []
    for p in doc["processes"]:
        slug = p["key"][len("PROC-"):]
        desc = " ".join(str(p.get("description", "")).split())
        extra = [f"Quelle: {p['weisung']}" if p.get("weisung") else "", f"Stand {p['stand']}" if p.get("stand") else ""]
        desc = " ".join(x for x in [desc] + [e + "." for e in extra if e])
        steps = []
        for i, s in enumerate(p["steps"], start=1):
            step = {"id": s["id"], "key": f"STEP-{slug}-{s['id']}", "order": i, "name": s.get("name", s["id"]),
                    "resting": bool(s.get("resting", False)), "requires": s.get("requires", []),
                    "produces": s.get("produces", []), "exit_criteria": s.get("exit_criteria", [])}
            if s.get("description"):
                step["description"] = " ".join(str(s["description"]).split())
            if s.get("owner_role"):
                step["owner_role"] = s["owner_role"]
            steps.append(step)
        ids = {s["id"] for s in steps}
        trans = []
        for t in p.get("transitions", []):
            if t[0] not in ids or t[1] not in ids:
                sys.exit(f"{p['key']}: transition {t} names an unknown step")
            trans.append({"from": t[0], "to": t[1], **({"rework": True} if len(t) > 2 else {})})
        processes.append({
            "key": p["key"], "name": p["name"], "description": desc, "instance_types": p["instance_types"],
            "owner_role": p["owner_role"], "governing_fact": p["governing_fact"],
            "status_map": {t: {} for t in p["instance_types"]},
            "change_classes": {"minor": [], "substantive": [], "on_substantive": None},
            "steps": steps, "transitions": trans, "final": p["final"]})
    return {"version": 1, "generated_at": "from ontology/processes.yaml", "source": "ontology/processes.yaml",
            "rules": [], "report_rules": [], "processes": processes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if a.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("process-definitions.json is not what processes.yaml gives: run build_definitions.py")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
