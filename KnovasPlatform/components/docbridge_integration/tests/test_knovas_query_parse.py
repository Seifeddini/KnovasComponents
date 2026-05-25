"""Parsing helpers for Knovas /secured/query hits."""

from knovas_client import (
    _display_title_for_hit,
    _ingested_summary_from_hit,
    _ingested_summary_text,
)


def test_ingested_summary_plain_string():
    assert _ingested_summary_text("  Hello summary.  ") == "Hello summary."


def test_ingested_summary_api_object():
    assert _ingested_summary_text({"present": True, "text": "AI summary text"}) == "AI summary text"


def test_ingested_summary_absent():
    assert _ingested_summary_text({"present": False, "text": "ignored"}) is None


def test_display_title_prefers_filename_stem():
    pointer = "corpus/eu_recht/Infocuria.txt"
    garbage = "infocuria https de wikipedia org wiki " + "x" * 200
    assert _display_title_for_hit(pointer, garbage) == "Infocuria"


def test_ingested_summary_skips_huge_blob():
    blob = "a" * 3000
    assert _ingested_summary_text({"present": True, "text": blob}) is None


def test_ingested_summary_from_hit_nested():
    hit = {
        "pointer": "corpus/wikipedia_de/Pleite.txt",
        "ingested_summary": {"present": True, "text": "Document about bankruptcy."},
    }
    assert _ingested_summary_from_hit(hit) == "Document about bankruptcy."
