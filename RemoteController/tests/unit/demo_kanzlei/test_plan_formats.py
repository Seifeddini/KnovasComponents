from pathlib import Path

from demo_kanzlei.build_world import build_world, load_config
from demo_kanzlei.plan_documents import plan_documents

CONFIG = Path(__file__).resolve().parents[3] / "scripts" / "demo_kanzlei" / "world.toml"


def test_pilot_plan_emits_docx_pdf_txt_and_msg_emails():
    cfg = load_config(CONFIG)
    world = build_world(cfg, pilot=True, skip_zefix=True)
    rows = plan_documents(world, cfg)
    fmts = {row.fmt for row in rows}
    assert {"docx", "pdf", "txt", "msg"} <= fmts
    emails = [row for row in rows if row.tier == "email"]
    assert emails, "pilot matter must include emails"
    assert all(row.fmt == "msg" for row in emails)
    assert not any(row.fmt == "eml" for row in rows)
    long_docs = [row for row in rows if row.tier == "firm_authored"]
    assert {row.fmt for row in long_docs} >= {"docx", "pdf", "txt"}
