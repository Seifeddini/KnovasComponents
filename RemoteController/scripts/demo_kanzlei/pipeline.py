"""Staged corpus build with a verification gate after each stage."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from demo_kanzlei.build_world import build_world, load_config, read_world, write_world
from demo_kanzlei.file_corpus import file_corpus
from demo_kanzlei.ground_truth import write_ground_truth
from demo_kanzlei.mess import apply_mess
from demo_kanzlei.plan_documents import plan_documents, read_plan, write_plan
from demo_kanzlei.verify import require_format_mix, verify_corpus
from demo_kanzlei.write_documents import write_all

log = logging.getLogger("demo-kanzlei")

STAGES = (
    "world",
    "plan",
    "write",
    "file",
    "mess",
    "ground_truth",
    "verify",
)


class GateError(RuntimeError):
    pass


def _mark(work: Path, stage: str, payload: dict) -> None:
    path = work / "stages"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{stage}.ok").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("gate ok: %s %s", stage, payload)


def run_pipeline(
    *,
    cfg_path: Path,
    out_root: Path,
    work_root: Path,
    pilot: bool,
    skip_zefix: bool,
    until_stage: str | None = None,
) -> int:
    cfg = load_config(cfg_path)
    work_root.mkdir(parents=True, exist_ok=True)
    world_path = work_root / "world.json"
    plan_path = work_root / "plan.jsonl"
    cache_dir = work_root / "cache" / "prose"
    gt_dir = work_root / "ground_truth"

    world = build_world(cfg, pilot=pilot, skip_zefix=skip_zefix)
    write_world(world, world_path)
    if len(world.firm.staff) != 7:
        raise GateError(f"expected 7 staff, got {len(world.firm.staff)}")
    if not world.matters:
        raise GateError("no matters in world")
    if not any(m.aktenzeichen == "2024-017" for m in world.matters):
        raise GateError("hero matter 2024-017 missing")
    _mark(work_root, "world", {"matters": len(world.matters), "staff": len(world.firm.staff)})
    if until_stage == "world":
        return 0

    world = read_world(world_path)
    rows = plan_documents(world, cfg)
    write_plan(rows, plan_path)
    fmts = {row.fmt for row in rows}
    required = {"docx", "pdf", "txt", "msg"}
    if not required.issubset(fmts):
        raise GateError(f"plan missing formats {required - fmts}; have {fmts}")
    if any(row.fmt == "eml" for row in rows):
        raise GateError("emails must be .msg, not .eml")
    if any(row.tier == "email" and row.fmt != "msg" for row in rows):
        raise GateError("every email plan row must use fmt=msg")
    _mark(work_root, "plan", {"rows": len(rows), "formats": sorted(fmts)})
    if until_stage == "plan":
        return 0

    bodies = write_all(rows, world, cache_dir)
    missing_letterhead = [rid for rid, text in bodies.items() if "Aktenzeichen:" not in text]
    if missing_letterhead:
        raise GateError(f"prose missing letterhead: {missing_letterhead[:5]}")
    _mark(work_root, "write", {"bodies": len(bodies)})
    if until_stage == "write":
        return 0

    file_corpus(rows, bodies, world, out_root)
    require_format_mix(out_root)
    _mark(
        work_root,
        "file",
        {
            "files": sum(1 for p in out_root.rglob("*") if p.is_file()),
            "suffixes": sorted({p.suffix.lower() for p in out_root.rglob("*") if p.is_file()}),
        },
    )
    if until_stage == "file":
        return 0

    stats = apply_mess(out_root, pilot=pilot)
    _mark(work_root, "mess", stats)
    if until_stage == "mess":
        return 0

    write_ground_truth(world, gt_dir)
    if not (gt_dir / "world.json").exists():
        raise GateError("ground_truth/world.json missing")
    if gt_dir.resolve() == out_root.resolve() or out_root in gt_dir.resolve().parents:
        raise GateError("ground truth must not live inside the watch root")
    _mark(work_root, "ground_truth", {"path": str(gt_dir)})
    if until_stage == "ground_truth":
        return 0

    rc = verify_corpus(out_root, cfg, skip_scan_ocr=pilot)
    if rc != 0:
        raise GateError("verify failed")
    _mark(work_root, "verify", {"status": "ok"})
    return 0
