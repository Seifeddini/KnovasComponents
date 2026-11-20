"""report: findings.json (+ intake, + review) -> report.json, report.md, connection-plan.md."""

from __future__ import annotations

from pathlib import Path

from . import intake as intake_mod
from .catalogue import load_catalogue, load_pricing
from .classify import classify
from .render import plan_md, report_md
from .util import load_yaml, read_json, write_json, write_text


def load_review(path: Path | None) -> dict:
    if not path:
        return {}
    data = load_yaml(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("review.yaml must be a mapping")
    return data


def make_report(out_dir: Path, review_path: Path | None = None, intake_path: Path | None = None,
                catalogue: dict | None = None, pricing: dict | None = None) -> dict:
    out_dir = Path(out_dir)
    findings = read_json(out_dir / "findings.json")
    software = intake_mod.load(intake_path)["software"] if intake_path else None
    report = classify(findings, catalogue or load_catalogue(), pricing or load_pricing(), load_review(review_path),
                      software)
    write_json(out_dir / "report.json", report)
    write_text(out_dir / "report.md", report_md(report, findings))
    write_text(out_dir / "connection-plan.md", plan_md(report))
    return report
