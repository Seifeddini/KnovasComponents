from unittest.mock import MagicMock, patch

import pytest

from sync.chunking import PART_MAX_CHARS
from sync.knovas_uploader import SemantixUploader, _transmit_part_body


@pytest.fixture
def mock_config(monkeypatch):
    cfg = MagicMock()
    cfg.semantix_secure_base_url = "https://api.example:8443"
    cfg.semantix_client_cert_path = "/c/cert.pem"
    cfg.semantix_client_key_path = "/c/key.pem"
    cfg.semantix_ca_cert_path = "/c/ca.pem"
    monkeypatch.setattr("sync.knovas_uploader.get_config", lambda: cfg)
    return cfg


def _ok_response(key: str = "tx-key-1"):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = b'{"key": "' + key.encode() + b'"}'
    resp.json.return_value = {"key": key}
    return resp


def test_upload_defaults_to_max_part_size(mock_config, tmp_path):
    """Omitted part_max_chars uses the Secure API snippet limit."""
    md_file = tmp_path / "large.txt"
    # Two parts at 50k, one part at 500k.
    md_file.write_text("x" * 60_000, encoding="utf-8")

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "corpus"}}

    with patch.object(uploader, "_request") as req:
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(md_file, "large.txt", sync_body)

    assert result.status == "ok"
    assert result.parts == 1
    assert PART_MAX_CHARS == 500_000


def test_upload_caps_part_max_chars_at_api_limit(mock_config, tmp_path):
    md_file = tmp_path / "huge.txt"
    md_file.write_text("y" * 600_000, encoding="utf-8")

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "corpus", "part_max_chars": 999_999}}

    with patch.object(uploader, "_request") as req:
        req.side_effect = [_ok_response(), _ok_response(), _ok_response()]
        result = uploader.upload_file(md_file, "huge.txt", sync_body)

    assert result.status == "ok"
    assert result.parts == 2


def test_upload_uses_original_identifier_and_converted_text(mock_config, tmp_path):
    md_file = tmp_path / "brief.txt"
    md_file.write_text("Ingested markdown body", encoding="utf-8")

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "corpus", "part_max_chars": 50000}}

    with patch.object(uploader, "_request") as req:
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(md_file, "akten/brief.txt", sync_body)

    assert result.status == "ok"
    assert result.transmission_key_id == "tx-key-1"
    init_call = req.call_args_list[0]
    assert init_call[0] == ("POST", "/secured/init_document_transmission")
    init_json = init_call.kwargs["json_body"]
    assert init_json["identifier"] == "corpus/akten/brief.txt"
    assert init_json["path"] == "akten/brief.txt"
    assert init_json["title"] == "brief.txt"

    part_call = req.call_args_list[1]
    part_json = part_call.kwargs["json_body"]
    assert "Ingested markdown body" in part_json["snippet"]


def test_transmit_part_body_includes_location_fields():
    body = _transmit_part_body(
        "k",
        0,
        {"snippet": "text", "page_number": 3, "sentence_number": 9},
    )
    assert body["page_number"] == 3
    assert body["sentence_number"] == 9


def test_delete_by_pointer_success(mock_config):
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req:
        resp = MagicMock()
        resp.status_code = 200
        req.return_value = resp
        ok, err = uploader.delete_by_pointer("corpus/x.pdf")
    assert ok is True
    assert err is None
    req.assert_called_once()
    assert req.call_args[0][0] == "DELETE"


def test_upload_pdf_markdown_sends_page_and_sentence(mock_config, tmp_path):
    from knovas_extract.result import Sentence

    from sync.document_text import ExtractedDocument

    md_file = tmp_path / "brief.pdf"
    md_file.write_bytes(b"%PDF-1.4 not a real pdf")

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "corpus", "part_max_chars": 50000}}

    text = "First sentence. Second sentence."
    fake_doc = ExtractedDocument(
        text=text,
        sentences=[
            Sentence(
                index=0,
                text="First sentence.",
                char_start=0,
                char_end=15,
                line_start=1,
                line_end=1,
                page_index=4,
                page_number=5,
                section_index=None,
            ),
            Sentence(
                index=1,
                text="Second sentence.",
                char_start=16,
                char_end=32,
                line_start=1,
                line_end=1,
                page_index=4,
                page_number=5,
                section_index=None,
            ),
        ],
    )

    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=fake_doc
    ):
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(md_file, "akten/brief.pdf", sync_body)

    assert result.status == "ok"
    part_json = req.call_args_list[1].kwargs["json_body"]
    assert part_json["page_number"] == 5
    assert part_json["sentence_number"] == 1


def test_upload_plain_text_sends_sentence_number(mock_config, tmp_path):
    md_file = tmp_path / "brief.txt"
    md_file.write_text("First sentence. Second sentence.", encoding="utf-8")

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "corpus", "part_max_chars": 50000}}

    with patch.object(uploader, "_request") as req:
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(md_file, "akten/brief.txt", sync_body)

    assert result.status == "ok"
    part_json = req.call_args_list[1].kwargs["json_body"]
    assert part_json["sentence_number"] == 1
    assert "page_number" not in part_json


def test_upload_conversion_error(mock_config, tmp_path):
    bad = tmp_path / "empty.pdf"
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page()
    bad.write_bytes(doc.tobytes())
    doc.close()

    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "rc"}}

    with patch.object(uploader, "_request") as req:
        result = uploader.upload_file(bad, "empty.pdf", sync_body)

    assert result.status == "error"
    assert result.parts == 0
    req.assert_not_called()


# --- page markers, PDF tables and the sidecar (plan §5 M3, GI-INGEST-17) -----


def _two_page_pdf_doc():
    from knovas_extract.result import Page, Sentence

    from sync.document_text import ExtractedDocument

    text = "Seite eins.\n\nSeite zwei."
    pages = [
        Page(index=0, text="Seite eins.", line_start=1, line_end=1),
        Page(index=1, text="Seite zwei.", line_start=3, line_end=3),
    ]
    sentences = [
        Sentence(index=0, text="Seite eins.", char_start=0, char_end=11, line_start=1, line_end=1,
                 page_index=0, page_number=1, section_index=None),
        Sentence(index=1, text="Seite zwei.", char_start=13, char_end=24, line_start=3, line_end=3,
                 page_index=1, page_number=2, section_index=None),
    ]
    tables = [{"client_table_hint": "t1", "headers": ["H"], "rows": [["v"]], "_char_start": 0}]
    return ExtractedDocument(text=text, sentences=sentences, pages=pages, tables=tables)


def test_pdf_part_carries_page_break_marker_and_no_tables(mock_config, tmp_path, monkeypatch):
    monkeypatch.delenv("RC_PAGE_BREAK_MARKERS", raising=False)
    monkeypatch.delenv("RC_SEND_PDF_TABLES", raising=False)
    pdf = tmp_path / "zwei.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    uploader = SemantixUploader()
    sync_body = {"ingestion": {"identifier_prefix": "corpus"}}
    captured: dict = {}

    def fake_sidecar(store_dir, pointer, path, text, sentences):
        captured.update(pointer=pointer, text=text, sentences=sentences)
        return True

    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=_two_page_pdf_doc()
    ), patch("sync.knovas_uploader.write_context_sidecar", side_effect=fake_sidecar):
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(pdf, "akten/zwei.pdf", sync_body)

    assert result.status == "ok" and result.partial is None
    part_json = req.call_args_list[1].kwargs["json_body"]
    assert part_json["snippet"] == "Seite eins.\n\n\fSeite zwei."
    assert part_json["page_number"] == 1 and part_json["sentence_number"] == 1
    assert "tables" not in part_json, "the server drops PDF tables at the Redis buffer anyway"
    # the sidecar is built from the UNMARKED text
    assert captured["text"] == "Seite eins.\n\nSeite zwei."
    assert "\f" not in captured["text"]
    assert captured["pointer"] == "corpus/akten/zwei.pdf"


def test_page_markers_and_pdf_tables_can_be_switched(mock_config, tmp_path, monkeypatch):
    monkeypatch.setenv("RC_PAGE_BREAK_MARKERS", "false")
    monkeypatch.setenv("RC_SEND_PDF_TABLES", "true")
    pdf = tmp_path / "zwei.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=_two_page_pdf_doc()
    ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True):
        req.side_effect = [_ok_response(), _ok_response()]
        uploader.upload_file(pdf, "akten/zwei.pdf", {"ingestion": {"identifier_prefix": "corpus"}})
    part_json = req.call_args_list[1].kwargs["json_body"]
    assert "\f" not in part_json["snippet"]
    assert part_json["tables"][0]["client_table_hint"] == "t1"


def test_docx_tables_are_still_sent(mock_config, tmp_path, monkeypatch):
    monkeypatch.delenv("RC_SEND_PDF_TABLES", raising=False)
    docx = tmp_path / "tab.docx"
    docx.write_bytes(b"PK stub")
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=_two_page_pdf_doc()
    ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True):
        req.side_effect = [_ok_response(), _ok_response()]
        uploader.upload_file(docx, "akten/tab.docx", {"ingestion": {"identifier_prefix": "corpus"}})
    assert req.call_args_list[1].kwargs["json_body"]["tables"]


def test_upload_result_partial_is_filled_from_metadata_extra(mock_config, tmp_path, monkeypatch):
    from sync.document_text import ExtractedDocument

    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "true")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    doc = ExtractedDocument(
        text="Deckblatt.", sentences=None,
        extra={"pdf:ocr_pages_skipped": 12, "pdf:ocr_pages": 40, "pdf:ocr_backend": "tesserocr",
               "rc:ocr_cache_hits": 2, "rc:ocr_cache_misses": 40},
    )
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=doc
    ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True):
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(pdf, "akten/scan.pdf", {"ingestion": {"identifier_prefix": "corpus"}})
    assert result.status == "ok"
    assert result.partial == {"ocr_pages_skipped": 12, "ocr_pages": 40, "ocr_backend": "tesserocr"}


def test_upload_result_partial_when_no_ocr_backend_but_ocr_expected(mock_config, tmp_path, monkeypatch):
    from sync.document_text import ExtractedDocument

    monkeypatch.setenv("RC_PDF_OCR_ENABLED", "true")
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    doc = ExtractedDocument(text="Deckblatt.", sentences=None, extra={"pdf:ocr_backend": "none"})
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=doc
    ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True):
        req.side_effect = [_ok_response(), _ok_response()]
        result = uploader.upload_file(pdf, "akten/scan.pdf", {"ingestion": {"identifier_prefix": "corpus"}})
    assert result.partial == {"reason": "ocr_backend_none", "ocr_backend": "none"}


def test_uploader_passes_the_relative_path_as_cache_key(mock_config, tmp_path):
    """The OCR cache indexes entries by the sync-relative path (not the temp
    copy a Microsoft 365 file is read from), so purge-on-delete finds them."""
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req, patch(
        "sync.knovas_uploader.extract_document_guarded", return_value=_two_page_pdf_doc()
    ) as guarded, patch("sync.knovas_uploader.write_context_sidecar", return_value=True):
        req.side_effect = [_ok_response(), _ok_response()]
        uploader.upload_file(pdf, "akten/x.pdf", {"ingestion": {"identifier_prefix": "corpus"}})
    assert guarded.call_args.kwargs["document_key"] == "akten/x.pdf"


def test_each_extraction_is_counted_once_in_the_parent(mock_config, tmp_path):
    """The extraction child's registry dies with it: the uploader counts the
    returned document (spec L5), even when the upload then fails."""
    from sync.document_text import ExtractedDocument

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    doc = ExtractedDocument(text="Deckblatt.", sentences=None, extra={"pdf:ocr_pages": 3},
                            warnings=("pdf: OCR applied to 3 of 4 pages via tesserocr",))
    failed_init = MagicMock()
    failed_init.status_code = 500
    failed_init.content = b""
    for answers in ([_ok_response(), _ok_response()], [failed_init]):
        uploader = SemantixUploader()
        with patch.object(uploader, "_request") as req, patch(
            "sync.knovas_uploader.extract_document_guarded", return_value=doc
        ), patch("sync.knovas_uploader.write_context_sidecar", return_value=True), patch(
            "sync.extract_metrics.record_extraction"
        ) as record:
            req.side_effect = answers
            uploader.upload_file(pdf, "akten/scan.pdf", {"ingestion": {"identifier_prefix": "corpus"}})
        record.assert_called_once_with(doc)
