"""Parsing helpers for Knovas /secured/query hits."""

from knovas_client import (
    _display_title_for_hit,
    _ingested_summary_from_hit,
    _ingested_summary_text,
    _merge_secured_query_hit,
    _merge_secured_query_result_rows,
    _normalize_top_chunks,
    _prepare_secured_query_hit,
    _secured_query_hit_to_row,
    _unwrap_secured_query_response,
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


def test_ingested_summary_preserves_long_llm_output():
    """LLM summaries up to server cap must reach the UI. The server caps at
    LLM_SUMMARIZE_MAX_OUTPUT_CHARS=4000; the client must not silently drop
    anything below that."""
    blob = "a" * 3500
    result = _ingested_summary_text({"present": True, "text": blob})
    assert result == blob


def test_ingested_summary_soft_truncates_beyond_cap():
    """Anything longer than the client cap is soft-truncated with an ellipsis,
    never dropped. Dropping produced an empty UI gap for real LLM summaries."""
    blob = "a" * 5000
    result = _ingested_summary_text({"present": True, "text": blob})
    assert result is not None
    assert len(result) == 4000
    assert result.endswith("…")


def test_ingested_summary_plain_string_soft_truncates():
    """Legacy plain-string payloads share the same cap + ellipsis path."""
    blob = "b" * 5000
    result = _ingested_summary_text(blob)
    assert result is not None
    assert len(result) == 4000
    assert result.endswith("…")


def test_ingested_summary_from_hit_nested():
    hit = {
        "pointer": "corpus/wikipedia_de/Pleite.txt",
        "ingested_summary": {"present": True, "text": "Document about bankruptcy."},
    }
    assert _ingested_summary_from_hit(hit) == "Document about bankruptcy."


def test_normalize_top_chunks_location_only():
  chunks = [
      {"page_number": 3, "sentence_number": 12, "cosine_similarity": 0.91},
      {"page_number": 2, "sentence_number": 8, "cosine_similarity": 0.78},
  ]
  assert _normalize_top_chunks(chunks) == chunks


def test_merge_secured_query_hit_does_not_invent_snippet():
    hit = {
        "pointer": "doc/a.pdf",
        "page_number": None,
        "sentence_number": None,
        "top_chunks": [
            {"page_number": 1, "sentence_number": 4, "cosine_similarity": 0.8},
        ],
    }
    merged = _merge_secured_query_hit(hit)
    assert merged["page_number"] == 1
    assert merged["sentence_number"] == 4
    assert "snippet" not in merged
    assert "text" not in merged


def test_location_from_camel_case_and_top_chunks_alias():
    hit = _prepare_secured_query_hit({
        "pointer": "doc/b.pdf",
        "topChunks": [
            {"pageNumber": 5, "sentenceNumber": 9, "cosineSimilarity": 0.88},
            {"page": 2, "sentence": 3, "cosine_distance": 0.2},
        ],
    })
    merged = _merge_secured_query_hit(hit)
    assert merged["page_number"] == 5
    assert merged["sentence_number"] == 9
    chunks = _normalize_top_chunks(hit["top_chunks"])
    assert len(chunks) == 2
    assert chunks[0]["page_number"] == 5
    assert chunks[1]["page_number"] == 2
    assert chunks[1]["sentence_number"] == 3


def test_coalesce_secured_query_pascal_case_keys():
    hit = _prepare_secured_query_hit({
        "Pointer": "tenant/doc.pdf",
        "PageNumber": 4,
        "SentenceNumber": 9,
        "TopChunks": [{"PageNumber": 4, "SentenceNumber": 9, "CosineSimilarity": 0.9}],
    })
    assert hit["pointer"] == "tenant/doc.pdf"
    assert hit["page_number"] == 4
    assert hit["sentence_number"] == 9
    row = _secured_query_hit_to_row(hit)
    assert row["page_number"] == 4
    assert row["sentence_number"] == 9


def test_secured_query_hit_to_row_api_shape():
    raw = {
        "pointer": "corpus/foo.pdf",
        "document_uuid": "uuid-1",
        "page_number": 3,
        "sentence_number": 12,
        "top_chunks": [
            {"cosine_similarity": 0.9, "page_number": 3, "sentence_number": 12},
            {"cosine_similarity": 0.8, "page_number": 2, "sentence_number": 8},
        ],
        "cosine_similarity": 0.9,
        "ingested_summary": {"present": True, "text": "summary"},
    }
    row = _secured_query_hit_to_row(raw)
    assert row["page_number"] == 3
    assert row["sentence_number"] == 12
    assert len(row["top_chunks"]) == 2


def test_secured_query_hit_to_row_location_from_top_chunks_when_top_level_null():
    raw = {
        "pointer": "doc/a.pdf",
        "page_number": None,
        "sentence_number": None,
        "top_chunks": [
            {"page_number": 7, "sentence_number": 2, "cosine_similarity": 0.77},
        ],
        "cosine_similarity": 0.77,
    }
    row = _secured_query_hit_to_row(raw)
    assert row["page_number"] == 7
    assert row["sentence_number"] == 2
    assert row["top_chunks"][0]["page_number"] == 7


def test_unwrap_secured_query_merges_nested_location_fields():
    body = {
        "status": "success",
        "query_session_id": "sess-1",
        "results": [
            {"pointer": "tenant/a.pdf", "cosine_similarity": 0.9},
        ],
        "data": {
            "results": [
                {
                    "pointer": "tenant/a.pdf",
                    "page_number": 4,
                    "sentence_number": 11,
                    "top_chunks": [
                        {"page_number": 4, "sentence_number": 11, "cosine_similarity": 0.9},
                    ],
                },
            ],
        },
    }
    unwrapped = _unwrap_secured_query_response(body)
    merged = unwrapped["results"][0]
    assert merged["cosine_similarity"] == 0.9
    assert merged["page_number"] == 4
    assert merged["sentence_number"] == 11
    assert len(merged["top_chunks"]) == 1
    row = _secured_query_hit_to_row(merged)
    assert row["page_number"] == 4
    assert row["sentence_number"] == 11


def test_merge_secured_query_result_rows_by_pointer_when_lengths_differ():
    top = [{"pointer": "x", "cosine_similarity": 0.5}]
    inner = [
        {
            "pointer": "x",
            "page_number": 2,
            "sentence_number": 3,
            "top_chunks": [{"page_number": 2, "sentence_number": 3}],
        },
    ]
    merged = _merge_secured_query_result_rows(top, inner)
    assert merged[0]["page_number"] == 2
    assert merged[0]["cosine_similarity"] == 0.5
