import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from web_interface.app import (  # noqa: E402
    _build_test_search_results,
    _search_use_test_results,
)


def test_search_use_test_results_off_by_default(monkeypatch):
    monkeypatch.delenv("SEARCH_USE_TEST_RESULTS", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "local")
    assert _search_use_test_results() is False


def test_search_use_test_results_explicit_on(monkeypatch):
    monkeypatch.setenv("SEARCH_USE_TEST_RESULTS", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _search_use_test_results() is True


def test_search_use_test_results_explicit_off(monkeypatch):
    monkeypatch.setenv("SEARCH_USE_TEST_RESULTS", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    assert _search_use_test_results() is False


def test_build_test_search_results_filters_by_query():
    payload = _build_test_search_results("Mustervertrag", limit=10)
    assert payload["semantix"]["status"] == "test_data"
    assert len(payload["results"]) == 1
    assert "Mustervertrag" in payload["results"][0]["title"]


def test_build_test_search_results_includes_session_id():
    payload = _build_test_search_results("Klage", limit=5)
    assert payload["semantix"]["query_session_id"]


def test_build_test_search_results_multihit_demo():
    payload = _build_test_search_results("Mietrecht", limit=10)
    demo = next(r for r in payload['results'] if 'Mietrecht_Kommentar' in str(r.get('doc_id')))
    assert len(demo['top_chunks']) == 15
    assert demo['page_number'] is not None
    assert demo['sentence_number'] is not None


def test_build_test_search_results_fallback_when_no_match():
    payload = _build_test_search_results("zzznomatch", limit=5)
    assert len(payload["results"]) >= 1
