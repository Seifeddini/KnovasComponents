"""Die Reihenfolge der Treffer ist die des Servers.

Der Server bewertet zweistufig: Vektorsuche, dann ColBERT-Rerank (plus der
Namensvorfilter-Boost). Was er zurueckgibt, ist seine endgueltige Reihung.
``score`` an einem Treffer ist die Kosinus-Aehnlichkeit der ersten Stufe --
danach neu zu sortieren wirft den Rerank weg (Diagnose P1). Deshalb traegt
jede Zeile ihren ``server_rank``, und ``PLATFORM_KEEP_SERVER_ORDER`` (Vorgabe
an) haelt die Reihenfolge.
"""
from __future__ import annotations

import types

import pytest

pytest.importorskip("flask")

from knovas_client import KnovasAPIClient  # noqa: E402
from web_interface.app import _apply_search_refinement, _result_sort_key  # noqa: E402


class _Cfg:
    def get_float(self, key, default=0.0):
        return default

    def get_bool(self, key, default=False):
        return default

    def get_int(self, key, default=0):
        return default


def _rows():
    # the server's order is a, b, c; the stage-1 cosine says b, c, a
    return [
        {"doc_id": "a.pdf", "score": 0.61, "server_rank": 1},
        {"doc_id": "b.pdf", "score": 0.93, "server_rank": 2},
        {"doc_id": "c.pdf", "score": 0.75, "server_rank": 3},
    ]


@pytest.fixture(autouse=True)
def _default_flag(monkeypatch):
    monkeypatch.delenv("PLATFORM_KEEP_SERVER_ORDER", raising=False)


def test_the_servers_ranking_is_kept_by_default():
    refined = _apply_search_refinement({"results": _rows()}, "bilanz", {}, _Cfg())
    assert [r["doc_id"] for r in refined["results"]] == ["a.pdf", "b.pdf", "c.pdf"]


def test_the_old_re_sort_by_score_is_one_flag_away(monkeypatch):
    monkeypatch.setenv("PLATFORM_KEEP_SERVER_ORDER", "false")
    refined = _apply_search_refinement({"results": _rows()}, "bilanz", {}, _Cfg())
    assert [r["doc_id"] for r in refined["results"]] == ["b.pdf", "c.pdf", "a.pdf"]


def test_rows_without_a_rank_fall_back_to_the_score():
    """Ein Demo-Treffer oder ein Dateinamen-Zusatz hat keinen Rang."""
    rows = [{"doc_id": "x", "score": 0.2}, {"doc_id": "y", "score": 0.9}]
    refined = _apply_search_refinement({"results": rows}, "q", {}, _Cfg())
    assert [r["doc_id"] for r in refined["results"]] == ["y", "x"]


def test_a_row_without_a_rank_sorts_after_the_servers_rows():
    rows = _rows() + [{"doc_id": "zusatz.pdf", "score": 0.99}]
    refined = _apply_search_refinement({"results": rows}, "q", {}, _Cfg())
    assert [r["doc_id"] for r in refined["results"]][-1] == "zusatz.pdf"


def test_the_sort_key_is_stable_on_ties():
    assert _result_sort_key({"doc_id": "b", "score": 0.5, "server_rank": 2}) < _result_sort_key(
        {"doc_id": "a", "score": 0.5, "server_rank": 3}
    )
    assert _result_sort_key({"doc_id": "a", "score": 0.5}) < _result_sort_key({"doc_id": "b", "score": 0.5})


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_every_secured_query_row_carries_its_server_rank():
    payload = {
        "status": "success",
        "results": [
            {"pointer": "corpus/a.pdf", "cosine_similarity": 0.61, "final_score": 0.88},
            {"pointer": "corpus/b.pdf", "cosine_similarity": 0.93, "final_score": 0.80},
            "not a hit",
            {"pointer": "corpus/c.pdf", "cosine_similarity": 0.75},
        ],
    }
    fake = types.SimpleNamespace(
        endpoints={},
        _make_request=lambda **kwargs: _Resp(payload),
        _secured_query_request_body=lambda query, limit=None, filters=None: {"Input": query},
    )
    out = KnovasAPIClient._search_documents_secured(fake, "bilanz", 20)
    rows = out["results"]
    assert [r["doc_id"] for r in rows] == ["corpus/a.pdf", "corpus/b.pdf", "corpus/c.pdf"]
    assert [r["server_rank"] for r in rows] == [1, 2, 3]
    refined = _apply_search_refinement({"results": rows}, "bilanz", {}, _Cfg())
    assert [r["doc_id"] for r in refined["results"]] == ["corpus/a.pdf", "corpus/b.pdf", "corpus/c.pdf"]
