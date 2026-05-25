"""Parsing helpers for Knovas /secured/query hits."""

from knovas_client import _ingested_summary_from_hit, _ingested_summary_text


def test_ingested_summary_plain_string():
    assert _ingested_summary_text("  Hello summary.  ") == "Hello summary."


def test_ingested_summary_api_object():
    assert _ingested_summary_text({"present": True, "text": "AI summary text"}) == "AI summary text"


def test_ingested_summary_absent():
    assert _ingested_summary_text({"present": False, "text": "ignored"}) is None


def test_ingested_summary_from_hit_nested():
    hit = {
        "pointer": "corpus/wikipedia_de/Pleite.txt",
        "ingested_summary": {"present": True, "text": "Document about bankruptcy."},
    }
    assert _ingested_summary_from_hit(hit) == "Document about bankruptcy."
