"""Expand the world model into one plan row per document, filed in an Aktenplan."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from demo_kanzlei.aktenplan import document_dir, matter_dir, register_folder, slug
from demo_kanzlei.catalog import KANZLEI_DOCS, KIND_TO_DOC
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
    clients = {c.id: c for c in world.clients}
    for matter in world.matters:
        counsel = by_id[matter.counsel_id]
        assistant = by_id[matter.assistant_id]
        client = clients[matter.client_id]
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
            sender, to = _email_parties(event.kind, counsel, client, matter)
            row = PlanRow(
                id=f"{matter.aktenzeichen}-{event.id}",
                matter_aktenzeichen=matter.aktenzeichen,
                event_id=event.id,
                doc_type=_typ_token(doc_type),
                category=category,
                fmt=fmt,
                tier=tier,
                author_id=counsel.id if tier != "scan" else None,
                date=event.date,
                title=_title(doc_type, matter),
                facts={
                    **(asdict(matter.story) if matter.story else {}),
                    "unser_zeichen": zeichen,
                    "aktenzeichen": matter.aktenzeichen,
                    "in_sachen": matter.in_sachen,
                    "client": client.name,
                    "contact_name": client.contact_name,
                    "opposing": matter.opposing,
                    "court": matter.court or "das zuständige Gericht",
                    "author": counsel.name,
                    "voice": counsel.voice,
                    "kind": event.kind,
                    "summary": event.summary,
                    **event.facts,
                },
                brief=_brief(event, matter, counsel),
                hero=matter.hero,
                email_from=sender if tier == "email" else None,
                email_to=to if tier == "email" else None,
                register=register_folder(category),
            )
            rows.append(row)
    rows.extend(_kanzlei_rows(world, long_formats, long_index))
    _assign_aktenplan(rows, world)
    return rows


def _kanzlei_rows(world: World, long_formats: tuple[str, ...], long_index: int) -> list[PlanRow]:
    partner = next(s for s in world.firm.staff if s.role == "partner")
    rows = []
    for offset, (kid, date, category, doc_type, _tier, summary) in enumerate(KANZLEI_DOCS):
        fmt = long_formats[(long_index + offset) % len(long_formats)]
        rows.append(
            PlanRow(
                id=f"kanzlei-{kid}",
                matter_aktenzeichen="Kanzlei",
                event_id=kid,
                doc_type=_typ_token(doc_type),
                category=category,
                fmt=fmt,
                tier="kanzlei_wide",
                author_id=partner.id,
                date=date,
                title=f"{doc_type} — {world.firm.name}",
                facts={
                    "unser_zeichen": f"INT / {partner.initials}",
                    "aktenzeichen": "Kanzlei",
                    "in_sachen": world.firm.name,
                    "client": world.firm.name,
                    "opposing": "",
                    "author": partner.name,
                    "voice": partner.voice,
                },
                brief=summary,
                register=register_folder(category, kanzlei=True),
            )
        )
    return rows


def _assign_aktenplan(rows: list[PlanRow], world: World) -> None:
    clients = {c.id: c for c in world.clients}
    matters = {m.aktenzeichen: m for m in world.matters}
    grouped: dict[tuple[str | None, str], list[PlanRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.matter_aktenzeichen, row.register)].append(row)
    for (aktenzeichen, register), group in grouped.items():
        group.sort(key=lambda r: (r.date, r.id))
        kanzlei = aktenzeichen == "Kanzlei"
        if kanzlei:
            parent = "Kanzlei"
        else:
            matter = matters[aktenzeichen]
            parent = matter_dir(clients[matter.client_id], matter)
        for seq, row in enumerate(group, start=1):
            row.seq = seq
            row.rel_dir = document_dir(
                rel_parent=parent,
                register=register,
                seq=seq,
                date=row.date,
                doc_type=row.doc_type,
            )


def _email_parties(kind: str, counsel: Staff, client, matter: Matter) -> tuple[list[str], list[list[str]]]:
    sender = [counsel.email, counsel.name]
    if kind == "email_gegenpartei":
        to = [[f"kanzlei@{slug(matter.opposing)}.example", f"RA {matter.opposing}"]]
    elif kind == "email_gericht":
        to = [["gericht@bezirksgericht.example", matter.court or "Gericht"]]
    elif kind == "email_behoerde":
        to = [["eingang@behoerde.example", "Behörde"]]
    else:
        to = [[client.contact_email, client.contact_name]]
    return sender, to


def _typ_token(doc_type: str) -> str:
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
