"""Swiss-style numbered Aktenplan paths (Register, then chronology)."""
from __future__ import annotations

import re

from demo_kanzlei.models import Client, Matter

UNSAFE = re.compile(r"[^A-Za-z0-9.äöüÄÖÜéèà-]+")

REGISTER = {
    "Eroeffnung": "01-Eroeffnung",
    "Korrespondenz": "02-Korrespondenz",
    "Interna": "03-Interna",
    "Beilagen": "04-Beilagen",
    "Rechtsschriften": "05-Rechtsschriften",
    "Gerichtliches": "06-Gerichtliches",
    "Finanzen": "07-Finanzen",
    "Recherche": "08-Recherche",
    "Vorlagen": "01-Vorlagen",
    "Organisation": "02-Organisation",
    "Compliance": "03-Compliance",
}


def slug(value: str, limit: int = 70) -> str:
    cleaned = UNSAFE.sub("-", (value or "").strip()).strip("-.")
    return (cleaned or "unnamed")[:limit]


def register_folder(category: str, *, kanzlei: bool = False) -> str:
    if kanzlei:
        return REGISTER.get(category, f"00-{slug(category)}")
    return REGISTER.get(category, f"09-{slug(category)}")


def matter_dir(client: Client, matter: Matter) -> str:
    return "/".join(
        (
            "Mandanten",
            slug(client.name),
            f"{matter.aktenzeichen}-{slug(matter.title)}",
        )
    )


def slot_name(seq: int, date: str, doc_type: str) -> str:
    return f"{seq:02d}-{date}-{slug(doc_type)}"


def document_dir(
    *,
    rel_parent: str,
    register: str,
    seq: int,
    date: str,
    doc_type: str,
) -> str:
    return f"{rel_parent}/{register}/{slot_name(seq, date, doc_type)}"
