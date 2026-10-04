from datetime import datetime, timezone
from pathlib import Path

from demo_kanzlei.filenames import letterhead_block
from demo_kanzlei.render import render_docx, render_msg, render_pdf, render_txt

LETTERHEAD = letterhead_block(
    unser_zeichen="2024-017 / MB-lz",
    aktenzeichen="2024-017",
    in_sachen="Meierhans Bau AG ./. Rüegg",
)
BODY = (
    LETTERHEAD
    + "\n"
    + "Sehr geehrte Damen und Herren\n\n"
    + "Die Klagefrist läuft am 30. November 2024 ab. "
    + "Wir beantragen Fristansetzung gemäss Art. 59 ZPO.\n"
)


def _extract(path: Path) -> str:
    from sync.document_text import extract_document

    return extract_document(path).text


def test_render_txt_roundtrips_letterhead(tmp_path: Path):
    path = tmp_path / "note.txt"
    render_txt(path, BODY)
    text = _extract(path)
    assert "Aktenzeichen:    2024-017" in text
    assert "Klagefrist" in text
    assert path.suffix == ".txt"


def test_render_docx_roundtrips_letterhead(tmp_path: Path):
    path = tmp_path / "brief.docx"
    render_docx(path, BODY, title="Brief an Mandant")
    text = _extract(path)
    assert "2024-017" in text
    assert "Meierhans Bau AG" in text
    assert "Klagefrist" in text


def test_render_pdf_roundtrips_letterhead(tmp_path: Path):
    path = tmp_path / "klage.pdf"
    render_pdf(path, BODY, title="Klage")
    text = _extract(path)
    assert "2024-017" in text
    assert "Rüegg" in text or "Rueegg" in text
    assert "Klagefrist" in text


def test_render_msg_is_outlook_email_and_roundtrips(tmp_path: Path):
    path = tmp_path / "mail.msg"
    render_msg(
        path,
        subject="Akte 2024-017 — Frist",
        body=BODY,
        sender=("mb@quarzfels.example", "Dr. iur. Markus Bär"),
        to=[("kanzlei@meierhans-bau.example", "Meierhans Bau AG")],
        sent=datetime(2024, 3, 5, 9, 30, tzinfo=timezone.utc),
    )
    assert path.suffix == ".msg"
    extracted = _extract(path)
    assert "Klagefrist" in extracted
    assert "2024-017" in extracted
    from sync.document_text import extract_document

    doc = extract_document(path)
    assert doc.title == "Akte 2024-017 — Frist"
