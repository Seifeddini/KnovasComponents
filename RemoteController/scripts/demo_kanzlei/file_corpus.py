"""File rendered documents into the watch root with AutoDoc names and letterhead."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from demo_kanzlei.filenames import make_filename, validate_generated_filename
from demo_kanzlei.models import PlanRow, World
from demo_kanzlei.render import render_document

HINT = """HINWEIS — SYNTHETISCHE DEMODATEN

Dieser Aktenbestand ist vollständig fiktiv. Personen, Gesellschaften und
Sachverhalte sind erfunden. Er dient ausschliesslich der Produktdemonstration
von Knovas. Real publizierte Entscheide, die hier als Recherchebeilage liegen,
behalten ihre ursprüngliche Lizenz (siehe manifest.jsonl / LICENSES.md).

Fiktive Parteien wurden nie in echte Urteile eingefügt.
"""


def file_corpus(
    rows: list[PlanRow],
    bodies: dict[str, str],
    world: World,
    out_root: Path,
) -> list[dict]:
    del world  # letterhead already baked into prose
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "00_HINWEIS_DEMODATEN.txt").write_text(HINT, encoding="utf-8")
    manifest: list[dict] = []
    for row in rows:
        guid = str(uuid.uuid5(uuid.NAMESPACE_URL, row.id))
        ext = "pdf" if row.fmt == "scan_pdf" else row.fmt
        name = make_filename(guid, row.matter_aktenzeichen or "ohne-akte", row.doc_type, ext)
        validate_generated_filename(name)
        folder = out_root / "05_akten" / (row.matter_aktenzeichen or "kanzlei")
        path = folder / name
        body = bodies[row.id]
        sent = datetime.fromisoformat(row.date).replace(tzinfo=timezone.utc)
        sender = (row.email_from[0], row.email_from[1]) if row.email_from else None
        to = [(item[0], item[1]) for item in row.email_to] if row.email_to else None
        render_document(
            path,
            body,
            fmt=row.fmt,
            title=row.title,
            subject=row.title if row.fmt == "msg" else None,
            sender=sender,
            to=to,
            sent=sent if row.fmt == "msg" else None,
        )
        rel = path.relative_to(out_root).as_posix()
        manifest.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source": "generated",
                "license": "synthetic-demo",
                "matter": row.matter_aktenzeichen,
                "doc_type": row.doc_type,
                "fmt": ext,
                "date": row.date,
                "plan_id": row.id,
            }
        )
    (out_root / "manifest.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in manifest) + "\n",
        encoding="utf-8",
    )
    return manifest
