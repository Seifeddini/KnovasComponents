"""Extract PDF text as Markdown with per-page headings."""
from __future__ import annotations


def pdf_bytes_to_markdown(raw_bytes: bytes) -> str:
    try:
        import fitz  # pymupdf
    except ImportError as exc:
        raise ImportError("pymupdf is required for PDF conversion") from exc

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    blocks: list[str] = []
    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = (page.get_text() or "").strip()
            if not text:
                continue
            blocks.append(f"## Page {page_num + 1}\n\n{text}")
    finally:
        doc.close()

    return "\n\n".join(blocks).strip()
