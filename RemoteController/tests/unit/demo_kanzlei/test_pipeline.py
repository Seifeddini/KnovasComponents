from pathlib import Path

from demo_kanzlei.pipeline import run_pipeline
from demo_kanzlei.verify import REQUIRED_SUFFIXES, require_format_mix

CONFIG = Path(__file__).resolve().parents[3] / "scripts" / "demo_kanzlei" / "world.toml"


def test_pilot_pipeline_writes_all_four_formats(tmp_path: Path):
    out = tmp_path / "corpus"
    work = tmp_path / "work"
    rc = run_pipeline(
        cfg_path=CONFIG,
        out_root=out,
        work_root=work,
        pilot=True,
        skip_zefix=True,
    )
    assert rc == 0
    require_format_mix(out)
    found = {p.suffix.lower() for p in out.rglob("*") if p.is_file()}
    assert set(REQUIRED_SUFFIXES) <= found
    msgs = list(out.rglob("*.msg"))
    assert msgs, "expected Outlook .msg emails"
    assert list(out.rglob("*.docx"))
    assert list(out.rglob("*.pdf"))
    assert list(out.rglob("*.txt"))
    assert not list(out.rglob("*.eml"))
    assert (work / "ground_truth" / "world.json").exists()
    assert not (out / "world.json").exists()
    assert (work / "stages" / "verify.ok").exists()
