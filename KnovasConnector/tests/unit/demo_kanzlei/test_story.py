from pathlib import Path

from demo_kanzlei.build_world import build_world, load_config
from demo_kanzlei.plan_documents import plan_documents
from demo_kanzlei.stories import NAMED
from demo_kanzlei.write_documents import write_all

CONFIG = Path(__file__).resolve().parents[3] / "scripts" / "demo_kanzlei" / "world.toml"


def _bodies(pilot: bool, tmp_path: Path):
    cfg = load_config(CONFIG)
    world = build_world(cfg, pilot=pilot, skip_zefix=True)
    rows = plan_documents(world, cfg)
    return world, rows, write_all(rows, world, tmp_path / "cache")


def _matter_text(az: str, rows, bodies) -> str:
    return "\n".join(bodies[r.id] for r in rows if r.matter_aktenzeichen == az)


def test_hero_mandate_repeats_the_same_plot_everywhere(tmp_path: Path):
    _, rows, bodies = _bodies(True, tmp_path)
    blob = _matter_text("2024-017", rows, bodies)
    assert blob.count("Schaffhauserstrasse") >= 5
    assert "184'500" in blob or "184500" in blob.replace("'", "")
    assert "Rüegg" in blob
    klage = next(bodies[r.id] for r in rows if r.doc_type == "Klage")
    honorar = next(bodies[r.id] for r in rows if r.doc_type == "Honorarnote")
    email = next(bodies[r.id] for r in rows if r.doc_type.startswith("E-Mail-Mandant"))
    assert "Schaffhauserstrasse" in klage and "Schaffhauserstrasse" in email
    assert "21'840" in honorar
    # fee note is the same matter, not a new dispute
    assert "Meierhans" in honorar and "2024-017" in honorar


def test_named_mandates_have_different_plots(tmp_path: Path):
    _, rows, bodies = _bodies(False, tmp_path)
    bau = _matter_text("2024-017", rows, bodies)
    arbeit = _matter_text("2025-004", rows, bodies)
    miete = _matter_text("2023-041", rows, bodies)
    assert "Schaffhauserstrasse" in bau
    assert "Schaffhauserstrasse" not in arbeit
    assert "Krankheit" in arbeit or "336c" in arbeit
    assert "Krankheit" not in bau
    assert "Mietzins" in miete or "Gastraum" in miete
    assert "WEKO" in _matter_text("2022-088", rows, bodies) or "Kartell" in _matter_text(
        "2022-088", rows, bodies
    )


def test_filler_mandates_are_not_clones_of_each_other(tmp_path: Path):
    world, rows, bodies = _bodies(False, tmp_path)
    fillers = [m for m in world.matters if m.aktenzeichen not in NAMED and m.story]
    hooks = [m.story.hook for m in fillers]
    assert len(hooks) >= 4
    assert len(set(hooks)) >= 4
    sample = fillers[0]
    blob = _matter_text(sample.aktenzeichen, rows, bodies)
    assert sample.story.place in blob
    assert sample.story.hook in blob
