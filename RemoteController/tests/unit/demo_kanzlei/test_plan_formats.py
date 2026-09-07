from pathlib import Path

from demo_kanzlei.build_world import build_world, load_config
from demo_kanzlei.plan_documents import plan_documents

CONFIG = Path(__file__).resolve().parents[3] / "scripts" / "demo_kanzlei" / "world.toml"


def _world(pilot: bool):
    cfg = load_config(CONFIG)
    return cfg, build_world(cfg, pilot=pilot, skip_zefix=True)


def test_pilot_plan_emits_docx_pdf_txt_and_msg_emails():
    cfg, world = _world(True)
    rows = plan_documents(world, cfg)
    fmts = {row.fmt for row in rows}
    assert {"docx", "pdf", "txt", "msg"} <= fmts
    emails = [row for row in rows if row.tier == "email"]
    assert emails, "pilot matter must include emails"
    assert all(row.fmt == "msg" for row in emails)
    assert not any(row.fmt == "eml" for row in rows)
    long_docs = [row for row in rows if row.tier == "firm_authored"]
    assert {row.fmt for row in long_docs} >= {"docx", "pdf", "txt"}


def test_every_matter_has_opening_correspondence_and_fees():
    cfg, world = _world(False)
    rows = plan_documents(world, cfg)
    by_matter: dict[str, list] = {}
    for row in rows:
        if row.matter_aktenzeichen and row.matter_aktenzeichen != "Kanzlei":
            by_matter.setdefault(row.matter_aktenzeichen, []).append(row)
    assert len(by_matter) == len(world.matters)
    for az, group in by_matter.items():
        types = {row.doc_type for row in group}
        registers = {row.register for row in group}
        assert any(t.startswith("Mandatsvereinbarung") or t == "Mandatsvereinbarung" for t in types), az
        assert any("Vollmacht" in t for t in types), az
        assert any(t.startswith("E-Mail") for t in types), az
        assert any("Honorarnote" in t for t in types), az
        assert any(r.endswith("Eroeffnung") for r in registers), az
        assert any(r.endswith("Korrespondenz") for r in registers), az
        assert any(r.endswith("Finanzen") for r in registers), az


def test_mix_follows_mandate_status():
    cfg, world = _world(False)
    rows = plan_documents(world, cfg)
    stayed = [m for m in world.matters if m.status == "sistiert"]
    closed = [m for m in world.matters if m.status == "abgeschlossen"]
    assert stayed and closed
    for matter in stayed:
        types = [r.doc_type for r in rows if r.matter_aktenzeichen == matter.aktenzeichen]
        assert "Klage" not in types
        assert any("Sistierung" in t or "sistier" in t.lower() for t in types)
    for matter in closed:
        types = [r.doc_type for r in rows if r.matter_aktenzeichen == matter.aktenzeichen]
        assert any(t in types or any(k in t for k in ("Urteil", "Vergleich", "Schluss")) for t in types) or any(
            "Urteil" in t or "Vergleich" in t for t in types
        )


def test_plan_rows_are_ordered_and_have_aktenplan_paths():
    cfg, world = _world(True)
    rows = plan_documents(world, cfg)
    matter_rows = [r for r in rows if r.matter_aktenzeichen == "2024-017"]
    assert matter_rows
    assert all(r.rel_dir and r.register and r.seq >= 1 for r in matter_rows)
    assert any(r.rel_dir.startswith("Mandanten/") for r in matter_rows)
    eroeffnung = [r for r in matter_rows if r.register.endswith("Eroeffnung")]
    dates = [r.date for r in eroeffnung]
    assert dates == sorted(dates)
    seqs = [r.seq for r in eroeffnung]
    assert seqs == list(range(1, len(seqs) + 1))


def test_kanzlei_wide_docs_live_outside_mandanten():
    cfg, world = _world(False)
    rows = plan_documents(world, cfg)
    kanzlei = [r for r in rows if r.rel_dir.startswith("Kanzlei/")]
    assert kanzlei
    assert all(r.tier == "kanzlei_wide" for r in kanzlei)
