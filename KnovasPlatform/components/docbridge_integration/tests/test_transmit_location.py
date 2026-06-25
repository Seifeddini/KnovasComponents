"""Tests for transmit_document_part location metadata."""

from knovas_client import _secured_transmit_part_payload
from part_metadata import enrich_transmit_parts_with_location


def test_enrich_transmit_parts_adds_page_and_sentence():
    text = "## Page 3\n\nAlpha one. Beta two."
    parts = enrich_transmit_parts_with_location([{"snippet": text}], full_text=text)
    assert parts[0]["page_number"] == 3
    assert parts[0]["sentence_number"] == 1


def test_secured_transmit_part_payload_includes_both_fields():
    payload = _secured_transmit_part_payload(
        "key-1",
        0,
        {"snippet": "x", "page_number": 2, "sentence_number": 5},
    )
    assert payload["page_number"] == 2
    assert payload["sentence_number"] == 5
