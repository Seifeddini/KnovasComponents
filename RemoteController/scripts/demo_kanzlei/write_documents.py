"""Write document prose. Template writer for tests/pilot; Anthropic optional."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from demo_kanzlei.filenames import letterhead_block
from demo_kanzlei.models import PlanRow, World
from demo_kanzlei.plan_documents import plan_hash

VOICE_OPENERS = {
    "terse": "Kurz und ohne Umschweife:",
    "precise": "Unter Hinweis auf die einschlägigen Bestimmungen:",
    "verbose": "Nach eingehender Prüfung der Aktenlage und der uns vorliegenden Belege:",
    "practical": "Sachverhalt und nächster Schritt:",
    "academic": "In doktrinärer Hinsicht ist festzuhalten:",
    "formal": "Hochgeachtete Damen und Herren, mit vorzüglicher Hochachtung darf ich mitteilen:",
    "admin": "Administrativer Vermerk:",
}


def write_prose(row: PlanRow, world: World, cache_dir: Path) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = plan_hash(row)
    cached = cache_dir / f"{digest}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    text = template_prose(row, world)
    cached.write_text(text, encoding="utf-8")
    return text


def template_prose(row: PlanRow, world: World) -> str:
    facts = row.facts
    header = letterhead_block(
        unser_zeichen=facts["unser_zeichen"],
        aktenzeichen=facts["aktenzeichen"],
        in_sachen=facts["in_sachen"],
    )
    voice = facts.get("voice", "practical")
    opener = VOICE_OPENERS.get(voice, VOICE_OPENERS["practical"])
    extra = []
    for key in ("frist", "streitwert", "betrag", "honorar_ansatz"):
        if key in facts:
            extra.append(f"{key}: {facts[key]}")
    extras = ("\n".join(extra) + "\n") if extra else ""
    firm = world.firm
    return (
        f"{firm.name}\n{firm.street}, {firm.zip_code} {firm.seat}\nTel. {firm.phone}\n\n"
        f"{header}\n"
        f"{row.date}\n\n"
        f"{row.title}\n\n"
        f"{opener}\n\n"
        f"{row.brief}\n\n"
        f"{extras}"
        f"Dieses Dokument ist synthetisches Demomaterial der Kanzlei {firm.name}. "
        f"Mandantin: {facts.get('client', '')}. Gegenpartei: {facts.get('opposing', '')}.\n"
    )


def write_all(rows: list[PlanRow], world: World, cache_dir: Path) -> dict[str, str]:
    bodies = {}
    for row in rows:
        bodies[row.id] = write_prose(row, world, cache_dir)
    (cache_dir / "index.json").write_text(
        json.dumps({row_id: hashlib.sha256(text.encode()).hexdigest() for row_id, text in bodies.items()}),
        encoding="utf-8",
    )
    return bodies
