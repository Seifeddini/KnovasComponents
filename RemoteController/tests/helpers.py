import base64
import json

TEST_EMPLOYEE_ID = "11111111-1111-1111-1111-111111111111"


def make_test_jwt(
    employee_id: str = TEST_EMPLOYEE_ID,
    *,
    jti: str = "test-jti",
) -> str:
    header = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": employee_id, "jti": jti}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


#: knovas-extract 0.4 OCR metadata as its PDF extractor reports it whenever
#: ``ocr=`` is passed (library ``extractors/pdf.py``, ``_OcrSummary``): every
#: key is present, and ``pdf:ocr_backend`` stays "none" unless OCR ran.
#: The Platform's tests/test_knovas_extract_upload.py carries the same table.
OCR_EXTRA_04 = {
    # no page needed OCR: complete, whether or not an engine is installed
    "born_digital": {"pdf:ocr_pages": 0, "pdf:text_pages": 12, "pdf:ocr_pages_skipped": 0,
                     "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "none"},
    # text pages and scanned pages, every scanned page OCR'd
    "mixed": {"pdf:ocr_pages": 3, "pdf:text_pages": 9, "pdf:ocr_pages_skipped": 0,
              "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "tesserocr",
              "pdf:ocr_backend_version": "5.3.0", "pdf:ocr_seconds": 4.2, "pdf:ocr_mean_conf": 91.5},
    # the time budget ran out on a long scan
    "starved": {"pdf:ocr_pages": 40, "pdf:text_pages": 0, "pdf:ocr_pages_skipped": 12,
                "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "tesserocr"},
    # one page timed out in Tesseract: an empty page
    "failed": {"pdf:ocr_pages": 9, "pdf:text_pages": 2, "pdf:ocr_pages_skipped": 0,
               "pdf:ocr_pages_failed": 1, "pdf:ocr_backend": "cli"},
    # no engine: 0.4 keeps the text layers and counts the scanned pages as skipped
    "no_engine": {"pdf:ocr_pages": 0, "pdf:text_pages": 2, "pdf:ocr_pages_skipped": 5,
                  "pdf:ocr_pages_failed": 0, "pdf:ocr_backend": "none"},
    # a library that does not count skipped pages
    "uncounted": {"pdf:ocr_pages": 0, "pdf:ocr_backend": "none"},
}
