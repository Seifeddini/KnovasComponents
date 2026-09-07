"""Expand the world model into one plan row per document."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from demo_kanzlei.catalog import KIND_TO_DOC
from demo_kanzlei.filenames import unser_zeichen
from demo_kanzlei.models import Matter, PlanRow, Staff, World

LONG_FORMATS = ("docx", "pdf", "txt")
EMAIL_FORMAT = "msg"
SCAN_FORMAT = "scan_pdf"


def plan_hash(row: PlanRow) -> str:
    payload = json.dumps(row.cache_payload(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_documents(world: World, cfg: dict | None = None) -> list[PlanRow]:
    long_formats = tuple((cfg or {}).get("formats", {}).get("long", LONG_FORMATS))
    email_fmt = (cfg or {}).get("formats", {}).get("email", EMAIL_FORMAT)
    rows: list[PlanRow] = []
    long_index = 0
    by_id = {s.id: s for s in world.firm.staff}
    for matter in world.matters:
        counsel = by_id[matter.counsel_id]
        assistant = by_id[matter.assistant_id]
        client = next(c for c in world.clients if c.id == matter.client_id)
        for event in matter.events:
            doc_type, category, tier = KIND_TO_DOC[event.kind]
            if tier == "email":
                fmt = email_fmt
            elif tier == "scan":
                fmt = SCAN_FORMAT
            else:
                fmt = long_formats[long_index % len(long_formats)]
                long_index += 1
            zeichen = unser_zeichen(matter.aktenzeichen, counsel.initials, assistant.initials)
            row = PlanRow(
                id=f"{matter.aktenzeichen}-{event.id}",
                matter_aktenzeichen=matter.aktenzeichen,
                event_id=event.id,
                doc_type=_typ_token(doc_type, event.kind),
                category=category,
                fmt=fmt,
                tier=tier,
                author_id=counsel.id if tier != "scan" else None,
                date=event.date,
                title=_title(doc_type, matter),
                facts={
                    "unser_zeichen": zeichen,
                    "aktenzeichen": matter.aktenzeichen,
                    "in_sachen": matter.in_sachen,
                    "client": client.name,
                    "opposing": matter.opposing,
                    "author": counsel.name,
                    "voice": counsel.voice,
                    **event.facts,
                },
                brief=_brief(event, matter, counsel),
                hero=matter.hero,
                email_from=[counsel.email, counsel.name] if tier == "email" else None,
                email_to=[[client.contact_email, client.contact_name]] if tier == "email" else None,
            )
            rows.append(row)
        _add_kanzlei_wide_if_hero(rows, matter, counsel, long_formats, long_index)
    return rows


def _add_kanzlei_wide_if_hero(
    rows: list[PlanRow],
    matter: Matter,
    counsel: Staff,
    long_formats: tuple[str, ...],
    long_index: int,
) -> None:
    if not matter.hero:
        return
    # Hero matters already have a full timeline; nothing extra here.
    del long_formats, long_index, counsel


def _typ_token(doc_type: str, kind: str) -> str:
    if kind == "email":
        return "E-Mail"
    return doc_type.replace(" ", "-")


def _title(doc_type: str, matter: Matter) -> str:
    return f"{doc_type} — {matter.aktenzeichen}"


def _brief(event, matter: Matter, counsel: Staff) -> str:
    return (
        f"Schreibe ein schweizerisches Kanzleidokument ({event.kind}) "
        f"in der Stimme von {counsel.name} ({counsel.voice}). "
        f"Akte {matter.aktenzeichen}, {matter.in_sachen}. {event.summary}"
    )


def write_plan(rows: list[PlanRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.__dict__, ensure_ascii=False) + "\n")


def read_plan(path: Path) -> list[PlanRow]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        rows.append(PlanRow(**raw))
    return rows
