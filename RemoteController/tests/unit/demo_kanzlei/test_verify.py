import hashlib
import json
from pathlib import Path

from demo_kanzlei.mess import apply_mess
from demo_kanzlei.verify import _is_image_only_pdf, verify_extraction


def test_truncated_pdf_does_not_crash_image_only_check(tmp_path: Path):
    broken = tmp_path / "Eingang-Scanner-2024-03.pdf"
    broken.write_bytes(b"PK" + b"x" * 198)
    assert _is_image_only_pdf(broken) is False


def test_verify_skips_engineered_mess_not_in_manifest(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    good = root / "00_HINWEIS_DEMODATEN.txt"
    good.write_text("HINWEIS — SYNTHETISCHE DEMODATEN\n", encoding="utf-8")
    source = root / "probe.docx"
    source.write_bytes(b"PK" + b"x" * 300)
    (root / "manifest.jsonl").write_text(
        json.dumps(
            {
                "path": "00_HINWEIS_DEMODATEN.txt",
                "sha256": hashlib.sha256(good.read_bytes()).hexdigest(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    apply_mess(root, pilot=False)
    assert list((root / "06_posteingang").glob("*.pdf"))
    problems = verify_extraction(root, skip_scan_ocr=True)
    assert problems == []
