"""Ground-truth key — lives outside the watch root and is never ingested."""
from __future__ import annotations

import json
from pathlib import Path

from demo_kanzlei.models import World, world_to_dict


def write_ground_truth(world: World, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "world.json").write_text(
        json.dumps(world_to_dict(world), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    questions = []
    for matter in world.matters:
        frist = None
        for event in matter.events:
            frist = event.facts.get("frist", frist)
        questions.append(
            {
                "id": f"q-{matter.aktenzeichen}-frist",
                "question": f"Welche Frist läuft in Akte {matter.aktenzeichen}?",
                "aktenzeichen": matter.aktenzeichen,
                "expected": frist,
            }
        )
        if "Kündigung während Krankheit" in matter.title:
            questions.append(
                {
                    "id": "q-kuendigung-krankheit",
                    "question": "Welche Mandate betreffen Kündigung während Krankheit?",
                    "aktenzeichen": matter.aktenzeichen,
                    "expected": matter.aktenzeichen,
                }
            )
    # Seed up to 60 slots; extras are per-matter chronology prompts.
    while len(questions) < min(60, max(8, len(world.matters) * 3)):
        matter = world.matters[len(questions) % len(world.matters)]
        questions.append(
            {
                "id": f"q-{len(questions):03d}",
                "question": f"Wer ist Mandant in Akte {matter.aktenzeichen}?",
                "aktenzeichen": matter.aktenzeichen,
                "expected": matter.in_sachen,
            }
        )
    (dest / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    entities = {
        "firm": world.firm.name,
        "staff": [s.name for s in world.firm.staff],
        "clients": [c.name for c in world.clients],
        "matters": [
            {
                "aktenzeichen": m.aktenzeichen,
                "in_sachen": m.in_sachen,
                "status": m.status,
                "timeline": [{"date": e.date, "kind": e.kind} for e in m.events],
            }
            for m in world.matters
        ],
    }
    (dest / "entities.json").write_text(
        json.dumps(entities, ensure_ascii=False, indent=2), encoding="utf-8"
    )
