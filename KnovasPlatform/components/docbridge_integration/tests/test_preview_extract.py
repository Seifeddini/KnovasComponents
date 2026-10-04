import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fixtures.make_msg import build_sample_msg  # noqa: E402


def test_generated_msg_parses_with_extract_msg(tmp_path):
    import extract_msg

    target = tmp_path / "sample.msg"
    build_sample_msg(str(target))

    msg = extract_msg.openMsg(str(target))
    assert msg.subject == "Rückfrage zum Kaufvertrag 2024-001"
    assert msg.sender == "Anna Muster"
    assert msg.isSent is True
    assert msg.date is not None
    assert msg.date.year == 2026 and msg.date.month == 3 and msg.date.day == 15
    assert [r.email for r in msg.recipients] == ["beat.beispiel@example.com"]
    assert "Kaufpreis von EUR 485.000,00" in msg.body


from web_interface.preview import preview_kind  # noqa: E402


def test_preview_kind_recognises_supported_suffixes():
    assert preview_kind("a/b/report.pdf") == "pdf"
    assert preview_kind("Vertrag.DOCX") == "docx"
    assert preview_kind("notiz.txt") == "txt"
    assert preview_kind("mail.msg") == "msg"


def test_preview_kind_rejects_everything_else():
    assert preview_kind("bild.png") is None
    assert preview_kind("archiv.zip") is None
    assert preview_kind("ohne_endung") is None


import pytest  # noqa: E402

from web_interface.preview import (  # noqa: E402
    PreviewFailed,
    PreviewUnsupported,
    extract_markdown,
)


def test_extract_txt(tmp_path):
    target = tmp_path / "notiz.txt"
    target.write_text("Zeile eins.\nZeile zwei mit Umlauten: äöü.\n", encoding="utf-8")

    result = extract_markdown(str(target))
    assert result["kind"] == "txt"
    assert "Zeile eins." in result["markdown"]
    assert "äöü" in result["markdown"]


def test_extract_docx(tmp_path):
    import docx

    target = tmp_path / "vertrag.docx"
    document = docx.Document()
    document.add_heading("Kaufvertrag", 1)
    document.add_paragraph("Der Kaufpreis betraegt EUR 485.000.")
    document.save(str(target))

    result = extract_markdown(str(target))
    assert result["kind"] == "docx"
    assert "# Kaufvertrag" in result["markdown"]
    assert "EUR 485.000" in result["markdown"]
    assert result["meta"]["word_count"] > 0


def test_extract_msg(tmp_path):
    target = tmp_path / "mail.msg"
    build_sample_msg(str(target))

    result = extract_markdown(str(target))
    assert result["kind"] == "msg"
    assert "Kaufpreis von EUR 485.000,00" in result["markdown"]
    assert result["meta"]["title"] == "Rückfrage zum Kaufvertrag 2024-001"
    assert result["meta"]["msg:from"] == "Anna Muster"
    assert result["meta"]["msg:to"] == "beat.beispiel@example.com"


def test_extract_rejects_pdf_and_unknown(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(PreviewUnsupported):
        extract_markdown(str(pdf))
    with pytest.raises(PreviewUnsupported):
        extract_markdown(str(tmp_path / "bild.png"))


def test_extract_missing_file_raises_preview_failed(tmp_path):
    """A deleted-file race or bad path must surface as PreviewFailed.

    ``knovas_extract.extract`` opens the path with a bare ``open("rb")`` and
    lets ``FileNotFoundError`` (an ``OSError``) escape uncaught. Without a
    broad except clause here, that raw OSError would defeat the typed
    PreviewUnsupported/PreviewFailed contract the Flask route relies on.
    """
    missing = tmp_path / "does-not-exist.txt"
    with pytest.raises(PreviewFailed):
        extract_markdown(str(missing))


def test_extraction_does_not_escape_document_text(tmp_path):
    """Haelt die Annahme fest, auf der der Client-Renderer beruht.

    knovas_extract entfernt *Markup* aus feindlichen Dokumenten, escaped aber
    keinen *Textinhalt*: steht "<script>" woertlich im Fliesstext, kommt es
    unveraendert zurueck. static/js/markdown.js escaped deshalb zuerst und
    formatiert erst danach. Schlaegt dieser Test fehl, weil die Bibliothek
    inzwischen selbst escaped, ist das kein Fehler -- aber der Renderer und
    dieser Kommentar gehoeren dann ueberprueft.
    """
    import docx

    target = tmp_path / "hostile.docx"
    document = docx.Document()
    document.add_paragraph("<script>alert(1)</script>")
    document.save(str(target))

    markdown = extract_markdown(str(target))["markdown"]
    assert "<script>" in markdown


def test_a_markdown_limit_falls_back_to_the_plain_text(tmp_path, monkeypatch):
    """L7: a DOCX whose tables make the Markdown many times its text trips
    the expansion guard (ResourceExhaustedError "markdown expansion ratio");
    the preview then shows the plain text instead of failing."""
    import docx
    import knovas_extract
    from knovas_extract.errors import ResourceExhaustedError

    target = tmp_path / "honorar.docx"
    document = docx.Document()
    document.add_paragraph("Honorarabrechnung Mandat 2024-001.")
    document.save(str(target))

    real_extract = knovas_extract.extract
    calls: list = []

    def guarded_extract(path, **kwargs):
        calls.append(dict(kwargs))
        if kwargs.get("emit_markdown"):
            raise ResourceExhaustedError("markdown expansion ratio", 3.0, observed=93.0)
        return real_extract(path, **kwargs)

    monkeypatch.setattr(knovas_extract, "extract", guarded_extract)
    result = extract_markdown(str(target))
    assert [c.get("emit_markdown", False) for c in calls] == [True, False]
    assert result["kind"] == "docx"
    assert "Honorarabrechnung Mandat 2024-001." in result["markdown"]
    assert result["warnings"][0] == "preview: markdown limit exceeded; plain text shown"
    assert result["meta"]["word_count"] > 0


def test_other_resource_limits_still_fail_the_preview(tmp_path, monkeypatch):
    import knovas_extract
    from knovas_extract.errors import ResourceExhaustedError

    target = tmp_path / "notiz.txt"
    target.write_text("Zeile.", encoding="utf-8")

    def too_big(path, **kwargs):
        raise ResourceExhaustedError("input size", 1, observed=2)

    monkeypatch.setattr(knovas_extract, "extract", too_big)
    with pytest.raises(PreviewFailed):
        extract_markdown(str(target))


def _fee_statement_docx(path, rows: int) -> None:
    """One paragraph and a time-entry table, the shape of a fee statement."""
    import docx

    document = docx.Document()
    document.add_paragraph("Honorarabrechnung Mandat 2024-001.")
    table = document.add_table(rows=rows + 1, cols=4)
    for col, title in enumerate(("Datum", "Stunden", "Leistung", "Betrag")):
        table.cell(0, col).text = title
    for row in range(1, rows + 1):
        values = (f"2024-03-{row % 28 + 1:02d}", f"{row % 7 + 1}.5",
                  f"Besprechung Klient Position {row}", f"CHF {row * 37 % 900 + 100}.00")
        for col, value in enumerate(values):
            table.cell(row, col).text = value
    document.save(str(path))


def _library_renders_docx_tables() -> bool:
    """True when the installed knovas-extract writes a DOCX's tables into the
    text in layout mode. Builds before the 0.4.0a1 release (b5d4540 and
    older) do not (they answer ``text_mode="layout"`` for DOCX with plain
    text and a warning); the release, which CI installs as the pin, does."""
    import io

    import docx
    import knovas_extract

    document = docx.Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Sonde A", "Sonde B"
    buffer = io.BytesIO()
    document.save(buffer)
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    try:
        result = knovas_extract.extract(buffer.getvalue(), mime=mime, text_mode="layout")
    except TypeError:  # a library before text_mode
        return False
    return "Sonde A | Sonde B" in (result.content.text or "")


needs_docx_layout = pytest.mark.skipif(
    not _library_renders_docx_tables(),
    reason="the installed knovas-extract has no DOCX layout mode (a build before "
           "the 0.4.0a1 release)")


@needs_docx_layout
def test_a_docx_table_reaches_the_preview(tmp_path):
    """Real library, no fake. In knovas-extract's default text mode a DOCX's
    tables are not in the text, so the expansion guard compared the Markdown,
    tables included, with the paragraphs alone: every DOCX whose tables
    outweigh its prose tripped it, and the fallback showed the paragraphs
    only. In layout mode the rows are in the text (spec L3), the guard
    compares like with like and the Markdown is shown."""
    from web_interface.preview import MARKDOWN_FALLBACK_WARNING

    target = tmp_path / "honorar.docx"
    _fee_statement_docx(target, rows=120)

    result = extract_markdown(str(target))
    assert "Honorarabrechnung Mandat 2024-001." in result["markdown"]
    assert "Besprechung Klient Position 120" in result["markdown"]
    assert MARKDOWN_FALLBACK_WARNING not in result["warnings"]


@needs_docx_layout
def test_the_fallback_text_keeps_the_docx_table_rows(tmp_path, monkeypatch):
    """When the Markdown trips a limit even so (a table of mostly empty cells
    does), the text shown is the layout text, rows included."""
    import knovas_extract
    from knovas_extract.errors import ResourceExhaustedError

    target = tmp_path / "honorar.docx"
    _fee_statement_docx(target, rows=3)
    real_extract = knovas_extract.extract
    calls: list = []

    def guarded_extract(path, *, text_mode="plain", **kwargs):
        calls.append((kwargs.get("emit_markdown", False), text_mode))
        if kwargs.get("emit_markdown"):
            raise ResourceExhaustedError("markdown expansion ratio", 3.0, observed=7.5)
        return real_extract(path, text_mode=text_mode, **kwargs)

    monkeypatch.setattr(knovas_extract, "extract", guarded_extract)
    result = extract_markdown(str(target))
    assert calls == [(True, "layout"), (False, "layout")]
    assert "Honorarabrechnung Mandat 2024-001." in result["markdown"]
    assert "Besprechung Klient Position 3" in result["markdown"]
    assert result["warnings"][0] == "preview: markdown limit exceeded; plain text shown"


def test_a_library_without_text_mode_is_not_sent_it(tmp_path, monkeypatch):
    """knovas-extract before 0.4 has no ``text_mode``: the keyword would raise
    TypeError, and the route answers that with a 500."""
    import knovas_extract

    target = tmp_path / "honorar.docx"
    _fee_statement_docx(target, rows=3)
    real_extract = knovas_extract.extract

    def extract_without_text_mode(path, *, limits=None, emit_markdown=False):
        return real_extract(path, limits=limits, emit_markdown=emit_markdown)

    monkeypatch.setattr(knovas_extract, "extract", extract_without_text_mode)
    result = extract_markdown(str(target))
    assert result["kind"] == "docx"
    assert "Honorarabrechnung Mandat 2024-001." in result["markdown"]


def test_a_limit_of_the_layout_text_falls_back_to_the_default_mode(tmp_path, monkeypatch):
    """The table rows are an addition: when the layout text trips a limit of
    its own, the preview shows what it showed without them."""
    import knovas_extract
    from knovas_extract.errors import ResourceExhaustedError

    target = tmp_path / "honorar.docx"
    _fee_statement_docx(target, rows=3)
    real_extract = knovas_extract.extract
    modes: list = []

    def layout_text_too_big(path, *, text_mode="plain", **kwargs):
        modes.append(text_mode)
        if text_mode == "layout":
            raise ResourceExhaustedError("text size", 1, observed=2)
        return real_extract(path, **kwargs)

    monkeypatch.setattr(knovas_extract, "extract", layout_text_too_big)
    result = extract_markdown(str(target))
    assert modes[0] == "layout"
    assert modes[1:] and set(modes[1:]) == {"plain"}
    assert "Honorarabrechnung Mandat 2024-001." in result["markdown"]
