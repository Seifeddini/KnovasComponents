"""Engineered mess: version chains (hyphen suffixes), broken names, empties."""
from __future__ import annotations

import shutil
from pathlib import Path

from demo_kanzlei.filenames import broken_filename, make_filename, parse_autodoc_filename


def apply_mess(out_root: Path, *, pilot: bool) -> dict[str, int]:
    """Small, checkable mess in pilot; full rates belong to --full."""
    akten = out_root / "05_akten"
    files = [p for p in akten.rglob("*") if p.is_file()]
    stats = {"versions": 0, "broken": 0, "empty": 0, "latin1": 0}
    if not files:
        return stats
    source = next((p for p in files if p.suffix.lower() == ".docx"), files[0])
    parsed = parse_autodoc_filename(source.name)
    if parsed["guid"] and parsed["akten_id"] and parsed["doc_type"]:
        v2 = source.with_name(
            make_filename(parsed["guid"], parsed["akten_id"], f"{parsed['doc_type']}-v2", source.suffix.lstrip("."))
        )
        shutil.copy2(source, v2)
        stats["versions"] += 1
    if not pilot:
        broken = out_root / "06_posteingang" / broken_filename("Eingang-Scanner-2024-03.pdf")
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_bytes(source.read_bytes()[:200] if source.stat().st_size > 200 else source.read_bytes())
        stats["broken"] += 1
        empty = out_root / "06_posteingang" / "leer.bin"
        # keep extension syncable
        empty = out_root / "06_posteingang" / "Leerdatei.txt"
        empty.write_bytes(b"x" * 3)
        stats["empty"] += 1
    return stats
