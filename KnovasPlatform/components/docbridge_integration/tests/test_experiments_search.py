"""Experiment hits in the normal search: stripped for everyone, shown to viewers.

split() is a security boundary -- nobody without a viewing role may learn an
experiment exists, not from a result row and not from the semantix pointer
list -- so it runs whether the module is on or off. rows() is presentation for
people who may see experiments, built from the database, never from Knovas.
"""

import copy
import sys
import types
import uuid

import pytest

from experiments.search import SearchIntegration, parse_pointer
from experiments.settings import ExperimentsSettings


# -- parse_pointer -----------------------------------------------------------------


@pytest.mark.parametrize("pointer, key", [
    ("experiments/marketing/MKT-1", "MKT-1"),
    ("/experiments/marketing/MKT-1", "MKT-1"),
    ("  experiments/sales-emea/SAL2-123456789  ", "SAL2-123456789"),
    ("experiments/ab/ENG-7", "ENG-7"),
    ("experiments/marketing/MKT-1\n", "MKT-1"),  # surrounding whitespace is tolerated
])
def test_parse_pointer_accepts_experiment_pointers(pointer, key):
    assert parse_pointer("experiments", pointer) == key


@pytest.mark.parametrize("pointer", [
    "experiments/marketing/mkt-1",          # lower-case key
    "experiments/marketing/MKT-1/extra",    # too deep
    "experiments/MKT-1",                    # no domain
    "experiments/Marketing/MKT-1",          # domain key pattern
    "experiments/marketing/MKT-1 x",       # junk after the key
    "experiments/market ing/MKT-1",         # space inside
    "experiments/marketing/M-1",            # prefix too short
    "experiments/marketing/MKT-1234567890",  # number too long
    "other/marketing/MKT-1",
    "experimentsX/marketing/MKT-1",
    "//experiments/marketing/MKT-1",
    "corpus/Akten/2024/Vertrag.pdf",
    "",
    None,
    42,
])
def test_parse_pointer_refuses_everything_else(pointer):
    assert parse_pointer("experiments", pointer) is None


def test_parse_pointer_uses_the_configured_prefix():
    assert parse_pointer("exp-lab", "exp-lab/product/PRD-3") == "PRD-3"
    assert parse_pointer("exp-lab", "experiments/product/PRD-3") is None
    assert parse_pointer("", "experiments/product/PRD-3") is None


# -- split -----------------------------------------------------------------------------


def integration(enabled=True, user=None, conn="conn", prefix="experiments"):
    return SearchIntegration(settings=ExperimentsSettings(enabled=enabled, pointer_prefix=prefix),
                             connection=lambda: conn, current_user=lambda: user, enabled=enabled)


FILE_HIT = {"doc_id": "corpus/Akten/Vertrag.pdf", "path": "corpus/Akten/Vertrag.pdf",
            "score": 0.8, "title": "Vertrag"}
EXP_HIT = {"doc_id": "experiments/marketing/MKT-1", "path": "experiments/marketing/MKT-1",
           "score": 0.9, "final_score": 0.91, "cosine_similarity": 0.9, "cosine_distance": 0.1,
           "title": "MKT-1 \u00b7 Betreffzeile", "top_chunks": [{"page_number": 1}],
           "ingested_summary": "stale text from Knovas"}


def results_with(*rows, pointers=None, result_count=None):
    payload = {"results": [dict(r) for r in rows], "total": len(rows)}
    payload["semantix"] = {
        "status": "success", "message": None,
        "result_count": len(rows) if result_count is None else result_count,
        "pointers": [r["doc_id"] for r in rows] if pointers is None else pointers,
        "query_session_id": "q1",
    }
    return payload


@pytest.mark.parametrize("enabled", [True, False])
def test_split_strips_hits_and_semantix_pointers_even_when_disabled(enabled):
    original = results_with(FILE_HIT, EXP_HIT)
    before = copy.deepcopy(original)
    cleaned, hits = integration(enabled=enabled).split(original)
    assert original == before  # input untouched
    assert [r["doc_id"] for r in cleaned["results"]] == ["corpus/Akten/Vertrag.pdf"]
    assert cleaned["total"] == 1
    assert cleaned["semantix"]["pointers"] == ["corpus/Akten/Vertrag.pdf"]
    assert cleaned["semantix"]["result_count"] == 1
    assert cleaned["semantix"]["query_session_id"] == "q1"
    assert hits == [EXP_HIT]
    assert "experiments/" not in repr(cleaned)


def test_split_filters_dict_pointers_in_every_spelling():
    pointers = [
        "corpus/a.pdf",
        {"pointer": "experiments/sales/SAL-2", "score": 0.3},
        {"identifier": "/experiments/sales/SAL-3"},
        {"doc_id": "experiments/product/PRD-4"},
        {"pointer": "corpus/b.pdf"},
        7,
    ]
    cleaned, hits = integration().split(results_with(FILE_HIT, pointers=pointers,
                                                     result_count=6))
    assert cleaned["semantix"]["pointers"] == ["corpus/a.pdf", {"pointer": "corpus/b.pdf"}, 7]
    assert cleaned["semantix"]["result_count"] == 3
    assert hits == []


def test_split_recognises_the_pointer_in_any_row_field():
    rows = [
        {"pointer": "experiments/sales/SAL-2", "score": 0.1},
        {"identifier": "experiments/sales/SAL-3"},
        {"path": "/experiments/sales/SAL-4", "doc_id": ""},
        FILE_HIT,
    ]
    cleaned, hits = integration().split({"results": rows, "total": 4})
    assert cleaned == {"results": [FILE_HIT], "total": 1}
    assert len(hits) == 3


def test_split_reduces_result_count_by_hits_when_there_is_no_pointer_list():
    payload = results_with(FILE_HIT, EXP_HIT)
    payload["semantix"]["pointers"] = None
    payload["semantix"]["result_count"] = 10
    cleaned, _ = integration().split(payload)
    assert cleaned["semantix"]["result_count"] == 9
    assert cleaned["semantix"]["pointers"] is None


def test_split_never_goes_below_zero_and_ignores_non_numbers():
    payload = results_with(EXP_HIT, result_count=0)
    cleaned, _ = integration().split(payload)
    assert cleaned["semantix"]["result_count"] == 0
    payload = results_with(EXP_HIT, result_count="many")
    cleaned, _ = integration().split(payload)
    assert cleaned["semantix"]["result_count"] == "many"


def test_split_tolerates_odd_payloads():
    s = integration()
    assert s.split(None) == (None, [])
    assert s.split({"results": None}) == ({"results": []}, [])
    assert s.split({}) == ({}, [])
    cleaned, hits = s.split({"results": ["weird", None, FILE_HIT]})
    assert cleaned["results"] == ["weird", None, FILE_HIT] and hits == []


def test_split_with_another_prefix_keeps_foreign_experiment_pointers():
    cleaned, hits = integration(prefix="lab").split(results_with(EXP_HIT))
    assert hits == [] and len(cleaned["results"]) == 1


def test_split_of_the_local_test_fixtures_payload():
    """The app's SEARCH_USE_TEST_RESULTS payload must pass through untouched."""
    from web_interface.app import _build_test_search_results

    payload = _build_test_search_results("vertrag", 5)
    cleaned, hits = integration().split(payload)
    assert hits == []
    assert cleaned["results"] == payload["results"]
    assert cleaned["semantix"] == payload["semantix"]


# -- rows ----------------------------------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.global_show = True
        self.user_show = {}
        self.experiments = {
            "MKT-1": {"key": "MKT-1", "title": "Betreffzeile mit Frage",
                      "hypothesis": "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate.",
                      "status": "running", "status_label": "L\u00e4uft", "archived": False,
                      "domain_key": "marketing", "domain_name": "Marketing",
                      "domain_color": "#eb6834", "type_name": "A/B-Test",
                      "updated_at": "2026-09-28T10:00:00+00:00"},
            "SAL-2": {"key": "SAL-2", "title": "Playbook", "hypothesis": "",
                      "status": "draft", "status_label": "Entwurf", "archived": False,
                      "domain_key": "sales", "domain_name": "Vertrieb",
                      "domain_color": "#1baf7a", "type_name": "Playbook-Test",
                      "updated_at": "2026-09-28T10:00:00+00:00"},
        }
        self.lookups = []
        self.fail = False

    def get_runtime_setting(self, conn, key):
        assert key == "experiments.show_in_search"
        return self.global_show

    def get_user_show_in_search(self, conn, user_id):
        return self.user_show.get(user_id, True)

    def lookup_by_keys(self, conn, keys):
        if self.fail:
            raise RuntimeError("database gone")
        self.lookups.append(list(keys))
        return {k: v for k, v in self.experiments.items() if k in keys}


@pytest.fixture
def store(monkeypatch):
    import experiments

    fake = FakeStore()
    monkeypatch.setitem(sys.modules, "experiments.store", fake)
    monkeypatch.setattr(experiments, "store", fake, raising=False)
    return fake


def person(*roles):
    return types.SimpleNamespace(id=uuid.uuid4(), roles=frozenset(roles))


def test_rows_for_an_experimenter(store):
    eva = person("experimenter")
    rows = integration(user=eva).rows([EXP_HIT])
    assert len(rows) == 1
    row = rows[0]
    assert row == {
        "doc_id": "experiments/marketing/MKT-1",
        "path": "experiments/marketing/MKT-1",
        "score": 0.9, "final_score": 0.91,
        "cosine_similarity": 0.9, "cosine_distance": 0.1,
        "result_kind": "experiment",
        "title": "MKT-1 \u00b7 Betreffzeile mit Frage",
        "app_url": "/experiments/MKT-1",
        "experiment": {"key": "MKT-1", "domain_key": "marketing", "domain_name": "Marketing",
                       "domain_color": "#eb6834", "status_label": "L\u00e4uft",
                       "type_name": "A/B-Test"},
        "context_snippet": "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate.",
        "snippet": "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate.",
        "file_exists": False,
        "can_open": False,
    }
    # Nothing Knovas said about the document survives into the row.
    assert "ingested_summary" not in row and "top_chunks" not in row


@pytest.mark.parametrize("roles", [("experiments_manager",), ("admin",)])
def test_rows_for_managers_and_admins(store, roles):
    assert len(integration(user=person(*roles)).rows([EXP_HIT])) == 1


@pytest.mark.parametrize("user", [None, person("member"), person()])
def test_rows_are_empty_for_everyone_else(store, user):
    assert integration(user=user).rows([EXP_HIT]) == []
    assert store.lookups == []


def test_rows_are_empty_when_the_module_is_off(store):
    assert integration(enabled=False, user=person("experimenter")).rows([EXP_HIT]) == []


def test_rows_respect_the_global_setting_and_the_personal_preference(store):
    eva = person("experimenter")
    store.global_show = False
    assert integration(user=eva).rows([EXP_HIT]) == []
    store.global_show = True
    store.user_show[eva.id] = False
    assert integration(user=eva).rows([EXP_HIT]) == []
    other = person("experimenter")
    assert len(integration(user=other).rows([EXP_HIT])) == 1


def test_rows_drop_unknown_keys_and_duplicates(store):
    hits = [
        EXP_HIT,
        dict(EXP_HIT, score=0.5),                      # the same experiment again
        {"doc_id": "experiments/marketing/MKT-99"},    # deleted meanwhile
        {"doc_id": "experiments/sales/SAL-2", "score": 0.2},
        {"doc_id": "corpus/not-an-experiment.pdf"},
    ]
    rows = integration(user=person("experimenter")).rows(hits)
    assert [r["experiment"]["key"] for r in rows] == ["MKT-1", "SAL-2"]
    assert rows[0]["score"] == 0.9
    assert store.lookups == [["MKT-1", "MKT-99", "SAL-2"]]


def test_rows_snippet_prefers_chunk_text(store):
    eva = person("experimenter")
    for chunks, expected in [
        (["  Text aus dem Treffer  "], "Text aus dem Treffer"),
        ([{"text": "Aus text"}], "Aus text"),
        ([{"snippet": "Aus snippet"}], "Aus snippet"),
        ([{"content": "Aus content"}], "Aus content"),
        ([{"page_number": 1}, {"text": ""}, "zweiter"], "zweiter"),
        ([], "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate."),
        ("not a list", "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate."),
    ]:
        row = integration(user=eva).rows([dict(EXP_HIT, top_chunks=chunks)])[0]
        assert row["snippet"] == expected == row["context_snippet"]


def test_rows_snippet_is_capped_at_300_characters(store):
    store.experiments["MKT-1"]["hypothesis"] = "Wort " * 200
    row = integration(user=person("experimenter")).rows([dict(EXP_HIT, top_chunks=[])])[0]
    assert len(row["snippet"]) == 300
    assert row["snippet"].endswith("\u2026")
    assert "\n" not in row["snippet"]


def test_rows_fill_doc_id_from_another_pointer_field(store):
    hit = {"pointer": "experiments/marketing/MKT-1", "score": 0.4}
    row = integration(user=person("experimenter")).rows([hit])[0]
    assert row["doc_id"] == "experiments/marketing/MKT-1"
    assert "path" not in row


def test_rows_never_raise(store, caplog):
    store.fail = True
    assert integration(user=person("experimenter")).rows([EXP_HIT]) == []

    def broken_user():
        raise RuntimeError("no request context")

    s = SearchIntegration(settings=ExperimentsSettings(enabled=True), connection=lambda: None,
                          current_user=broken_user, enabled=True)
    assert s.rows([EXP_HIT]) == []


def test_rows_of_nothing(store):
    assert integration(user=person("experimenter")).rows([]) == []
    assert store.lookups == []


def test_split_then_rows_round_trip(store):
    s = integration(user=person("experimenter"))
    cleaned, hits = s.split(results_with(FILE_HIT, EXP_HIT))
    rows = s.rows(hits)
    assert [r["app_url"] for r in rows] == ["/experiments/MKT-1"]
    assert all(not r["can_open"] and not r["file_exists"] for r in rows)
