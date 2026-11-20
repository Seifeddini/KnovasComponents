"""Staged corpus build with a verification gate after each stage.

Two layouts. ``aktenplan`` is the tidy folder plan of the first build: one
matter per folder, for the first end-to-end run. ``client`` is the share as we
find it at a client, with the firm's records and directory beside it
(``estate``, ``records``, ``directory``) and the truth files of the plan.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from demo_kanzlei.build_world import build_world, load_config, read_world, write_world
from demo_kanzlei.file_corpus import file_corpus
from demo_kanzlei.ground_truth import write_ground_truth
from demo_kanzlei.mess import apply_mess
from demo_kanzlei.plan_documents import plan_documents, write_plan
from demo_kanzlei.verify import require_format_mix, verify_client, verify_corpus
from demo_kanzlei.write_documents import write_all

log = logging.getLogger("demo-kanzlei")

STAGES = (
    "world",
    "plan",
    "write",
    "records",
    "file",
    "mess",
    "site",
    "ground_truth",
    "changes",
    "verify",
)
LAYOUTS = ("aktenplan", "client")


class GateError(RuntimeError):
    pass


def _mark(work: Path, stage: str, payload: dict) -> None:
    path = work / "stages"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{stage}.ok").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("gate ok: %s %s", stage, payload)


def _inside(inner: Path, outer: Path) -> bool:
    inner, outer = inner.resolve(), outer.resolve()
    return inner == outer or outer in inner.parents


def run_pipeline(
    *,
    cfg_path: Path,
    out_root: Path,
    work_root: Path,
    pilot: bool,
    skip_zefix: bool,
    until_stage: str | None = None,
    layout: str = "aktenplan",
) -> int:
    if layout not in LAYOUTS:
        raise GateError(f"unknown layout {layout!r}")
    cfg = load_config(cfg_path)
    if _inside(work_root, out_root):
        raise GateError("the work folder (world, truth, manifest) must not be inside the share")
    work_root.mkdir(parents=True, exist_ok=True)
    world_path = work_root / "world.json"
    plan_path = work_root / "plan.jsonl"
    manifest_path = work_root / "manifest.jsonl"
    cache_dir = work_root / "cache" / "prose"
    gt_dir = work_root / "ground_truth"

    world = build_world(cfg, pilot=pilot, skip_zefix=skip_zefix)
    write_world(world, world_path)
    wanted = sum(int(value) for value in cfg["staff"].values())
    if len(world.firm.staff) != wanted:
        raise GateError(f"expected {wanted} staff, got {len(world.firm.staff)}")
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

    if layout == "client":
        return _client(cfg, world, rows, bodies, out_root, work_root, manifest_path, gt_dir, pilot=pilot,
                       until_stage=until_stage)

    file_corpus(rows, bodies, world, out_root, manifest_path)
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
    if _inside(gt_dir, out_root):
        raise GateError("ground truth must not live inside the watch root")
    _mark(work_root, "ground_truth", {"path": str(gt_dir)})
    if until_stage == "ground_truth":
        return 0

    rc = verify_corpus(out_root, cfg, manifest_path=manifest_path, skip_scan_ocr=pilot)
    if rc != 0:
        raise GateError("verify failed")
    _mark(work_root, "verify", {"status": "ok"})
    return 0


def _clear_share(out_root: Path) -> None:
    """Empty a share this generator built before. Anything else is left alone."""
    from demo_kanzlei.estate import HINT_NAME

    if not out_root.exists() or not any(out_root.iterdir()):
        return
    if not (out_root / HINT_NAME).exists():
        raise GateError(
            f"{out_root} is not empty and was not built by this generator ({HINT_NAME} is missing); "
            "choose an empty folder"
        )
    for child in out_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _client(cfg, world, rows, bodies, out_root: Path, work_root: Path, manifest_path: Path, gt_dir: Path, *,
            pilot: bool, until_stage: str | None) -> int:
    from demo_kanzlei.directory import build_acl, build_directory
    from demo_kanzlei.estate import build_estate, manifest_rows
    from demo_kanzlei.records import build_records
    from demo_kanzlei.sqlgen import tables, write_sql
    from demo_kanzlei.truth import write_truth

    records = build_records(world, cfg, pilot=pilot)
    if not any(m.in_world for m in records.matters):
        raise GateError("the written matters are missing from the firm's records")
    if len({m.aktenzeichen for m in records.matters}) != len(records.matters):
        raise GateError("matter references are not unique")
    _mark(work_root, "records", {
        "matters": len(records.matters), "addresses": len(records.addresses),
        "time_entries": len(records.time_entries), "invoices": len(records.invoices),
        "deadlines": len(records.deadlines),
    })
    if until_stage == "records":
        return 0

    _clear_share(out_root)
    entries = build_estate(rows, bodies, world, records, cfg, out_root, pilot=pilot)
    manifest = manifest_rows(entries)
    if len({row["path"].lower() for row in manifest}) != len(manifest):
        raise GateError("two files on the share differ only by case, or share a path")
    manifest_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in manifest) + "\n", encoding="utf-8")
    require_format_mix(out_root)
    mess: dict[str, int] = {}
    for entry in entries:
        if entry.mess:
            mess[entry.mess] = mess.get(entry.mess, 0) + 1
    _mark(work_root, "file", {
        "files": len(entries),
        "syncable_and_reachable": sum(1 for e in entries if e.syncable and e.reachable),
        "documents_in_store": len(records.documents),
    })
    if until_stage == "file":
        return 0
    _mark(work_root, "mess", mess)
    if until_stage == "mess":
        return 0

    site = work_root / "site"
    site.mkdir(parents=True, exist_ok=True)
    estate = cfg["estate"]
    directory = build_directory(world, cfg)
    (site / "directory.json").write_text(json.dumps(directory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (site / "acl.json").write_text(json.dumps(build_acl(world, cfg), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    database = tables(
        records, firm_name=world.firm.name,
        autodoc_unc=f"\\\\SRV-DC01\\{estate['share']}\\{estate['firm_folder']}\\Praxis\\Autodoc",
    )
    sql_files = write_sql(database, site / "db", database=estate["database"])
    _mark(work_root, "site", {"users": len(directory["users"]), "groups": len(directory["groups"]),
                              "sql_files": len(sql_files)})
    if until_stage == "site":
        return 0

    write_ground_truth(world, gt_dir)
    truth_dir = work_root / "truth"
    counts = write_truth(world, rows, bodies, entries, records, cfg, truth_dir, pilot=pilot, database=database)
    if _inside(truth_dir, out_root) or _inside(gt_dir, out_root):
        raise GateError("truth files must not live inside the share")
    if not counts["questions"] or not counts["rights"]:
        raise GateError(f"truth files are empty: {counts}")
    _mark(work_root, "ground_truth", {"path": str(truth_dir), **counts})
    if until_stage == "ground_truth":
        return 0

    from demo_kanzlei.changes import build_day

    day = build_day(world, cfg, records, entries, bodies, database, out_root, work_root)
    if not day["statements"] or not day["added"]:
        raise GateError(f"the working day changed nothing: {day}")
    _mark(work_root, "changes", day)
    if until_stage == "changes":
        return 0

    rc = verify_client(out_root, cfg, manifest_path=manifest_path, skip_scan_ocr=pilot)
    if rc != 0:
        raise GateError("verify failed")
    _mark(work_root, "verify", {"status": "ok"})
    return 0
