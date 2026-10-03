"""Experiments in the normal search (/api/search).

Knovas holds one document per experiment under ``experiments/<domain>/<KEY>``
and returns it to anyone whose access groups allow it -- Knovas cannot know
who holds an experiments role. So the search route takes every experiment
hit out of every answer (pointers in the ``semantix`` block included) and puts
them back as experiment rows, rendered from the Platform database, only for
people with a viewing role who have not switched them off.

The Dummy client stands in for Knovas: tests put experiment pointers into
``DummyKnovasClient.last_instance.search_results`` exactly as the real
retrieval would return them.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (section 13.7)
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("flask")

from conftest import DummyKnovasClient, platform_db_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(not platform_db_reachable(),
                                reason="No PostgreSQL at the identity test DSN")

EXP_POINTER = "experiments/marketing/MKT-1"


def doc_hit(doc_id, score, **extra):
    row = {"doc_id": doc_id, "path": doc_id, "title": doc_id.rsplit("/", 1)[-1], "score": score,
           "cosine_similarity": score}
    row.update(extra)
    return row


def exp_hit(pointer=EXP_POINTER, score=0.8, **extra):
    row = {"doc_id": pointer, "path": pointer, "title": "MKT-1 \u00b7 alter Titel aus Knovas",
           "score": score, "cosine_similarity": score, "cosine_distance": round(1 - score, 4),
           "top_chunks": [{"text": "Hypothese: Eine Frage in der Betreffzeile wirkt."}],
           "client_open_unc": r"\\fileserver\x"}
    row.update(extra)
    return row


def search(client, query="Betreffzeile", limit=20, **extra):
    body = {"query": query, "limit": limit}
    body.update(extra)
    r = client.post("/api/search", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def experiment_rows(payload):
    return [r for r in payload["results"] if r.get("result_kind") == "experiment"]


@pytest.fixture
def mkt(exp_manager_client, experimenter_client):
    assert exp_manager_client.post("/api/experiments/packs/marketing/install",
                                   json={}).status_code == 200
    r = experimenter_client.post("/api/experiments", json={
        "domain": "marketing", "type": "ab_test", "title": "Betreffzeile mit Frage",
        "hypothesis": "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate."})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["experiment"]


@pytest.fixture
def knovas(experiments_app):
    """The search client the app was built with (the index client is the fake
    from the experiments_app fixture, so this stays the search client)."""
    client = DummyKnovasClient.last_instance
    client.search_results = [doc_hit("corpus/2024-001/Vertrag.pdf", 0.9), exp_hit(),
                             doc_hit("corpus/2024-001/Notiz.txt", 0.5)]
    return client


class TestAViewerSeesExperimentCards:
    def test_the_card_comes_from_the_database(self, experimenter_client, mkt, knovas):
        payload = search(experimenter_client)
        rows = experiment_rows(payload)
        assert len(rows) == 1
        row = rows[0]
        assert row["title"] == "MKT-1 \u00b7 Betreffzeile mit Frage"  # not the Knovas copy
        assert row["app_url"] == "/experiments/MKT-1"
        assert row["experiment"] == {"key": "MKT-1", "domain_key": "marketing",
                                     "domain_name": "Marketing", "domain_color": "#eb6834",
                                     "status_label": "Entwurf", "type_name": "A/B-Test"}
        assert row["snippet"] == "Hypothese: Eine Frage in der Betreffzeile wirkt."
        assert row["file_exists"] is False and row["can_open"] is False

    def test_no_open_hints_and_no_file_fields(self, experimenter_client, mkt, knovas):
        row = experiment_rows(search(experimenter_client))[0]
        for field in ("open_via_browser", "open_via_companion", "client_open_unc",
                      "client_open_path", "external_url", "onedrive_open_available",
                      "autodoc_rel_path", "top_chunks"):
            assert field not in row, field

    def test_merged_by_score_with_the_documents(self, experimenter_client, mkt, knovas):
        payload = search(experimenter_client)
        assert [r["doc_id"] for r in payload["results"]] == [
            "corpus/2024-001/Vertrag.pdf", EXP_POINTER, "corpus/2024-001/Notiz.txt"]
        assert payload["total"] == 3

    def test_the_limit_holds_with_experiments_merged_in(self, experimenter_client, mkt, knovas):
        payload = search(experimenter_client, limit=2)
        assert [r["doc_id"] for r in payload["results"]] == [
            "corpus/2024-001/Vertrag.pdf", EXP_POINTER]

    def test_an_experiment_that_no_longer_exists_is_dropped(self, experimenter_client, mkt,
                                                           knovas):
        knovas.search_results = [exp_hit("experiments/marketing/MKT-99"), exp_hit()]
        rows = experiment_rows(search(experimenter_client))
        assert [r["app_url"] for r in rows] == ["/experiments/MKT-1"]

    def test_a_leading_slash_is_still_an_experiment(self, experimenter_client, mkt, knovas):
        knovas.search_results = [exp_hit("/" + EXP_POINTER)]
        payload = search(experimenter_client)
        assert len(experiment_rows(payload)) == 1 and len(payload["results"]) == 1

    def test_duplicate_hits_become_one_card(self, experimenter_client, mkt, knovas):
        knovas.search_results = [exp_hit(), exp_hit(score=0.7)]
        assert len(experiment_rows(search(experimenter_client))) == 1

    def test_the_search_refinement_applies_to_them_too(self, experiments_app,
                                                       experimenter_client, mkt, knovas,
                                                       monkeypatch):
        """exact_match filters experiment rows like documents."""
        payload = search(experimenter_client, query="Kaufvertrag Grundbuch",
                         filters={"exact_match": True})
        assert experiment_rows(payload) == []
        kept = search(experimenter_client, query="Betreffzeile Frage",
                      filters={"exact_match": True})
        assert len(experiment_rows(kept)) == 1

    def test_min_similarity_applies_to_them_too(self, experiments_app, experimenter_client, mkt,
                                                knovas, monkeypatch):
        from config_loader import get_config

        config = get_config()
        real = config.get_float

        def get_float(key, default=0.0):
            return 0.85 if key == "web.search.min_similarity_score" else real(key, default)

        monkeypatch.setattr(config, "get_float", get_float)
        payload = search(experimenter_client)
        assert [r["doc_id"] for r in payload["results"]] == ["corpus/2024-001/Vertrag.pdf"]


class TestEveryoneElseSeesNothing:
    def test_a_member_gets_neither_cards_nor_pointers(self, exp_member_client, mkt, knovas):
        payload = search(exp_member_client)
        assert [r["doc_id"] for r in payload["results"]] == [
            "corpus/2024-001/Vertrag.pdf", "corpus/2024-001/Notiz.txt"]
        assert "experiments/marketing" not in json.dumps(payload)
        assert payload["total"] == 2

    def test_semantix_pointers_are_stripped(self, exp_member_client, experimenter_client, mkt,
                                            knovas, monkeypatch):
        original = knovas.search_documents

        def with_semantix(query, limit=20, filters=None):
            out = original(query, limit=limit, filters=filters)
            out["semantix"] = {"status": "ok", "result_count": 3,
                               "pointers": ["corpus/2024-001/Vertrag.pdf", EXP_POINTER,
                                            {"pointer": "/" + EXP_POINTER},
                                            "corpus/2024-001/Notiz.txt"]}
            return out

        monkeypatch.setattr(knovas, "search_documents", with_semantix)
        for client in (exp_member_client, experimenter_client):
            payload = search(client)
            assert payload["semantix"]["pointers"] == ["corpus/2024-001/Vertrag.pdf",
                                                       "corpus/2024-001/Notiz.txt"]
            assert payload["semantix"]["result_count"] == 1

    def test_a_viewer_who_switched_it_off(self, experimenter_client, mkt, knovas):
        r = experimenter_client.open("/api/experiments/preferences", method="PUT",
                                     json={"show_in_search": False})
        assert r.status_code == 200
        payload = search(experimenter_client)
        assert experiment_rows(payload) == []
        assert "experiments/marketing" not in json.dumps(payload)

    def test_switched_off_for_everyone_by_a_manager(self, exp_manager_client, experimenter_client,
                                                    mkt, knovas):
        r = exp_manager_client.open("/api/experiments/settings", method="PUT",
                                    json={"show_in_search": False})
        assert r.status_code == 200
        for client in (experimenter_client, exp_manager_client):
            assert experiment_rows(search(client)) == []


class TestNoGrantsForExperiments:
    def test_the_search_grants_documents_but_never_experiments(self, experimenter_client,
                                                               experimenter, mkt, knovas,
                                                               tmp_path):
        from document_grants import DocumentGrantStore

        search(experimenter_client)
        grants = DocumentGrantStore(str(tmp_path / "grants.sqlite3"))
        assert grants.granted(str(experimenter.id), "corpus/2024-001/Vertrag.pdf")
        assert not grants.granted(str(experimenter.id), EXP_POINTER)
        assert not grants.granted(str(experimenter.id), "/" + EXP_POINTER)

    def test_the_file_routes_refuse_experiment_pointers_even_with_a_grant(
            self, experimenter_client, experimenter, mkt, tmp_path):
        from document_grants import DocumentGrantStore

        grants = DocumentGrantStore(str(tmp_path / "grants.sqlite3"))
        grants.grant(str(experimenter.id), [EXP_POINTER, "corpus/a.pdf"])
        for suffix in ("", "/preview-content?path=x", "/thumbnail?path=x", "/download?path=x"):
            r = experimenter_client.get(f"/api/document/{EXP_POINTER}{suffix}")
            assert r.status_code == 404, suffix
        # The gate itself still serves a granted ordinary document.
        assert experimenter_client.get("/api/document/corpus/a.pdf").status_code == 200


class TestLocalTestFixtures:
    def test_sample_hits_pass_through_untouched(self, experimenter_client, mkt, monkeypatch):
        monkeypatch.setenv("SEARCH_USE_TEST_RESULTS", "true")
        payload = search(experimenter_client, query="Mustervertrag")
        assert payload["results"] and experiment_rows(payload) == []
        assert payload["semantix"]["status"] == "test_data"


# -- the module's own search (/api/experiments/search) ---------------------------------


def _indexed_app(platform_db, tmp_path, monkeypatch, groups=""):
    from conftest import FakeIndexClient, _identity_app

    monkeypatch.setattr("experiments.indexer.make_index_client", FakeIndexClient)
    yaml = ('experiments:\n  enabled: "true"\n  index:\n    enabled: "true"\n'
            f'    access_groups: "{groups}"\n  worker:\n    enabled: "false"\n')
    return _identity_app(platform_db, tmp_path, monkeypatch, extra_yaml=yaml)


class TestTheModuleSearchAsksKnovas:
    """With indexing on, /api/experiments/search asks Knovas through the
    search client -- the one signed as the person -- and merges the database
    hits; without the configured access group it says so and uses the
    database alone."""

    def _setup(self, app, identity_repo):
        from conftest import _person, _signed_in

        manager = _person(identity_repo, "max@knovas.ch", "Max", "experiments_manager")
        eva = _person(identity_repo, "eva@knovas.ch", "Eva", "experimenter")
        mgr = _signed_in(app, manager.email)
        assert mgr.post("/api/experiments/packs/marketing/install", json={}).status_code == 200
        client = _signed_in(app, eva.email)
        r = client.post("/api/experiments", json={
            "domain": "marketing", "type": "ab_test", "title": "Betreffzeile mit Frage"})
        assert r.status_code == 201, r.get_json()
        return eva, client

    def test_knovas_hits_through_the_signed_client(self, platform_db, tmp_path, monkeypatch,
                                                   identity_repo):
        app = _indexed_app(platform_db, tmp_path, monkeypatch)
        _, client = self._setup(app, identity_repo)
        knovas = DummyKnovasClient.last_instance
        knovas.search_results = [exp_hit(), doc_hit("corpus/a.pdf", 0.9)]
        result = client.get("/api/experiments/search?q=Frage").get_json()["result"]
        assert result["source"] == "knovas"
        assert [i["key"] for i in result["items"]] == ["MKT-1"]
        assert result["items"][0]["snippet"] == "Hypothese: Eine Frage in der Betreffzeile wirkt."
        assert knovas.search_calls == ["Frage"]

    def test_without_the_access_group_the_database_answers(self, platform_db, tmp_path,
                                                           monkeypatch, identity_repo):
        app = _indexed_app(platform_db, tmp_path, monkeypatch, groups="exp-team")
        _, client = self._setup(app, identity_repo)
        knovas = DummyKnovasClient.last_instance
        knovas.search_results = [exp_hit()]
        result = client.get("/api/experiments/search?q=Betreffzeile").get_json()["result"]
        assert result["source"] == "database"
        assert "Zugriffsgruppe" in result["warning"]
        assert [i["key"] for i in result["items"]] == ["MKT-1"]
        assert knovas.search_calls == []
