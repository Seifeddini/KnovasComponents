import base64

import pytest

pytest.importorskip("knovas_extract")

from knovas_extract_upload import parts_from_base64


def test_parts_from_base64_includes_location_and_section_heading():
    md = "# Title\n\n## Section\n\nHello world. Second sentence."
    b64 = base64.b64encode(md.encode()).decode()
    parts = parts_from_base64(b64, "md", part_max_chars=500)
    assert parts
    assert any("sentence_number" in p for p in parts)
    assert any(p["snippet"].startswith("#") for p in parts)
