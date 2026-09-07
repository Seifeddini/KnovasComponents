"""AutoDoc filenames and Swiss Kanzlei letterhead blocks.

Hard constraints from the corpus design:

- Underscores appear in generated filenames only as the three field
  separators ``{GUID}_{AkteID}_{Typ}.{ext}``. Everything else uses hyphens.
- ``Typ`` may contain underscores (the DocBridge parser joins ``parts[2:]``).
- A stem with fewer than two underscores yields ``akten_id is None`` —
  the genuine "Ohne Aktenbezug" case, not a corrupted ID.
"""
from __future__ import annotations

from pathlib import Path

LETTERHEAD_LABELS = ("Unser Zeichen:", "Aktenzeichen:", "In Sachen:")


def make_filename(guid: str, akten_id: str, typ: str, ext: str) -> str:
    if "_" in guid:
        raise ValueError("underscore not allowed in GUID")
    if "_" in akten_id:
        raise ValueError("underscore not allowed in AkteID")
    if "/" in guid or "/" in akten_id or "/" in typ:
        raise ValueError("path separators not allowed in filename fields")
    suffix = ext if ext.startswith(".") else f".{ext}"
    return f"{guid}_{akten_id}_{typ}{suffix}"


def parse_autodoc_filename(filename: str) -> dict[str, str | None]:
    """Mirror DocBridge ``stem.split('_')`` — guid, akten_id, joined typ."""
    path = Path(filename)
    parts = path.stem.split("_")
    result: dict[str, str | None] = {
        "guid": parts[0] if len(parts) >= 1 else None,
        "akten_id": parts[1] if len(parts) >= 2 else None,
        "doc_type": "_".join(parts[2:]) if len(parts) >= 3 else None,
        "extension": path.suffix,
    }
    return result


def validate_generated_filename(filename: str) -> None:
    parsed = parse_autodoc_filename(filename)
    if not parsed["guid"] or not parsed["akten_id"] or not parsed["doc_type"]:
        raise ValueError(f"generated filename must have three fields: {filename}")
    if "_" in parsed["guid"] or "_" in parsed["akten_id"]:
        raise ValueError(f"separator leaked into guid/akten_id: {filename}")


def broken_filename(stem_with_ext: str) -> str:
    """Fewer than two underscore fields → akten_id absent."""
    name = Path(stem_with_ext).name
    if name.count("_") >= 2:
        raise ValueError("broken filename must have fewer than two underscores")
    return name


def letterhead_block(unser_zeichen: str, aktenzeichen: str, in_sachen: str) -> str:
    return (
        f"Unser Zeichen:   {unser_zeichen}\n"
        f"Aktenzeichen:    {aktenzeichen}\n"
        f"In Sachen:       {in_sachen}\n"
    )


def unser_zeichen(aktenzeichen: str, counsel_initials: str, assistant_initials: str) -> str:
    return f"{aktenzeichen} / {counsel_initials}-{assistant_initials.lower()}"
