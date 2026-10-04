from pathlib import Path

from demo_kanzlei.build_world import build_world, load_config
from demo_kanzlei.plan_documents import plan_documents
from demo_kanzlei.write_documents import template_prose, write_all

CONFIG = Path(__file__).resolve().parents[3] / "scripts" / "demo_kanzlei" / "world.toml"


def _pilot_bodies(tmp_path: Path) -> tuple[object, dict[str, str], list]:
    cfg = load_config(CONFIG)
    world = build_world(cfg, pilot=True, skip_zefix=True)
    rows = plan_documents(world, cfg)
    bodies = write_all(rows, world, tmp_path / "cache")
    return world, bodies, rows


def test_bodies_are_not_the_llm_brief_dumped_into_every_file(tmp_path: Path):
    _, bodies, _ = _pilot_bodies(tmp_path)
    for text in bodies.values():
        assert "Schreibe ein schweizerisches Kanzleidokument" not in text


def test_klage_honorarnote_and_email_have_different_shapes(tmp_path: Path):
    _, bodies, rows = _pilot_bodies(tmp_path)
    by_type = {row.doc_type: bodies[row.id] for row in rows}
    klage = next(text for doc, text in by_type.items() if doc == "Klage")
    honorar = next(text for doc, text in by_type.items() if doc == "Honorarnote")
    email = next(text for doc, text in by_type.items() if doc.startswith("E-Mail"))
    assert "Rechtsbegehren" in klage
    assert "Streitwert" in klage
    assert "Sehr geehrte" not in honorar or "Honorarnote" in honorar
    assert "CHF" in honorar and ("Pos." in honorar or "Ansatz" in honorar)
    assert "Guten Tag" in email or "Sehr geehrte" in email
    assert "Rechtsbegehren" not in honorar
    assert "Ansatz" not in klage
    assert klage.splitlines()[0] != email.splitlines()[0]


def test_incoming_scan_does_not_use_firm_letterhead(tmp_path: Path):
    world, bodies, rows = _pilot_bodies(tmp_path)
    scan = next(row for row in rows if row.tier == "scan")
    text = bodies[scan.id]
    assert world.firm.name not in text.split("\n")[0]
    assert "Quarzfels" not in text.split("\n")[0]


def test_voices_change_length_and_wording(tmp_path: Path):
    cfg = load_config(CONFIG)
    world = build_world(cfg, pilot=True, skip_zefix=True)
    rows = [r for r in plan_documents(world, cfg) if r.doc_type == "Aktennotiz"]
    assert rows
    row = rows[0]
    row.facts = {**row.facts, "voice": "terse"}
    short = template_prose(row, world)
    row.facts = {**row.facts, "voice": "verbose"}
    long = template_prose(row, world)
    assert len(long) > len(short) + 80
    assert short != long


def test_pilot_matter_bodies_are_not_near_duplicates(tmp_path: Path):
    _, bodies, rows = _pilot_bodies(tmp_path)
    matter = [r for r in rows if r.matter_aktenzeichen == "2024-017"]
    openings = [bodies[r.id][:120] for r in matter]
    assert len(set(openings)) >= 8
