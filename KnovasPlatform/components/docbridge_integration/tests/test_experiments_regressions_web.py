"""Regressions in the Experimente web wiring (app.py, experiments_routes,
identity.webauth), one class per verified finding:

* e2e-api-4: a JSON body nested deeper than the parser's recursion limit is
  a 400 "kein gueltiges JSON", not a 500 with a traceback -- on the session
  API, the machine API and the rest of the app alike.
* e2e-api-7: the machine API's root (/api/experiments/v1 and .../v1/) is
  the machine API's: a valid token gets its JSON 404, no token the bearer
  401 -- never the session gate's "Anmeldung erforderlich".
* review-security-3: a wrong HTTP method on a module route is a 404 for
  someone without a viewing role (as for any other request of theirs) and a
  JSON 405 for a viewer, not Flask's HTML 405.
* review-security-5: experiment hits taken out of /api/search no longer
  shrink the page: Knovas is asked for more than the page and the documents
  ranked below the experiments fill it.
* review-security-6: the audit and session address is the X-Forwarded-For
  entry the trusted proxy added, not the first one the client wrote.
* review-security-7: one CSV import at a time per process; another is
  refused with 503 before its upload is read.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md
"""

from __future__ import annotations

import io
import logging
import threading

import pytest

pytest.importorskip("flask")

from conftest import PASSWORD, DummyKnovasClient, _csrf_from, _signed_in  # noqa: E402
from conftest import platform_db_reachable  # noqa: E402

needs_db = pytest.mark.skipif(not platform_db_reachable(),
                              reason="No PostgreSQL at the identity test DSN")

V1 = "/api/experiments/v1"
NOT_FOUND = {"success": False, "error": "Nicht gefunden."}
BAD_JSON = {"success": False, "error": "Die Anfrage ist kein g\u00fcltiges JSON."}
BAD_TOKEN = {"success": False,
             "error": "Ung\u00fcltiger oder abgelaufener Zugangsschl\u00fcssel."}
METHOD = {"success": False, "error": "Diese Methode ist hier nicht erlaubt."}
CSV_BUSY = ("Es l\u00e4uft gerade schon ein CSV-Import. Bitte in einem Moment noch "
            "einmal versuchen.")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


# -- helpers ----------------------------------------------------------------------------


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def make_token(client):
    r = client.post("/api/experiments/tokens", json={"name": "CI", "expires_days": 30})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]["token"]


def deep_json(levels):
    return "[" * levels + "]" * levels


@pytest.fixture
def mkt(exp_manager_client, experimenter_client):
    """The marketing pack installed and MKT-1 created by Eva."""
    r = exp_manager_client.post("/api/experiments/packs/marketing/install", json={})
    assert r.status_code == 200, r.get_json()
    r = experimenter_client.post("/api/experiments", json={
        "domain": "marketing", "type": "ab_test", "title": "Betreffzeile mit Frage",
        "hypothesis": "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate."})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["experiment"]


@pytest.fixture
def api(experiments_app):
    """No session at all, the way CI calls."""
    return experiments_app.test_client()


# -- e2e-api-4: deeply nested JSON ---------------------------------------------------------


@needs_db
class TestDeeplyNestedJson:
    def _assert_bad_json(self, response, caplog):
        assert response.status_code == 400, response.data[:200]
        assert response.get_json() == BAD_JSON
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_session_api(self, experimenter_client, mkt, caplog):
        body = '{"rows":' + deep_json(100000) + '}'
        with caplog.at_level(logging.INFO):
            r = experimenter_client.post(f"/api/experiments/{mkt['key']}/measurements",
                                         data=body, content_type="application/json")
        self._assert_bad_json(r, caplog)

    def test_machine_api(self, api, experimenter_client, mkt, caplog):
        token = make_token(experimenter_client)
        body = '{"body":' + deep_json(5000) + '}'
        with caplog.at_level(logging.INFO):
            r = api.post(f"{V1}/experiments/{mkt['key']}/notes", data=body,
                         content_type="application/json", headers=bearer(token))
        self._assert_bad_json(r, caplog)

    def test_the_search_and_anonymous_callers_too(self, experiments_app, exp_member_client,
                                                  caplog):
        with caplog.at_level(logging.INFO):
            signed_in = exp_member_client.post("/api/search", data=deep_json(5000),
                                               content_type="application/json")
            anonymous = experiments_app.test_client().post(
                "/api/search", data=deep_json(5000), content_type="application/json")
        self._assert_bad_json(signed_in, caplog)
        self._assert_bad_json(anonymous, caplog)

    def test_ordinary_bodies_are_untouched(self, experimenter_client, mkt):
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/notes",
                                     json={"body": "Notiz"})
        assert r.status_code == 201, r.get_json()
        # Nested, but well within what the parser reads: the service decides.
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/notes",
                                     data='{"body": "Notiz", "extra": ' + deep_json(100) + '}',
                                     content_type="application/json")
        assert r.status_code in (201, 400) and r.get_json() != BAD_JSON


# -- e2e-api-7: the machine API root ---------------------------------------------------------


@needs_db
class TestMachineApiRoot:
    @pytest.mark.parametrize("path", [V1, V1 + "/"])
    @pytest.mark.parametrize("method", METHODS)
    def test_a_valid_token_gets_the_api_404(self, api, experimenter_client, path, method):
        token = make_token(experimenter_client)
        r = api.open(path, method=method, headers=bearer(token))
        assert r.status_code == 404
        assert r.get_json() == NOT_FOUND
        assert r.headers["Cache-Control"] == "no-store"
        assert "Set-Cookie" not in r.headers

    @pytest.mark.parametrize("path", [V1, V1 + "/"])
    @pytest.mark.parametrize("method", METHODS)
    def test_without_a_token_it_is_the_bearer_401(self, api, experiments_app, path, method):
        r = api.open(path, method=method)
        assert r.status_code == 401
        assert r.get_json() == BAD_TOKEN
        assert r.headers.get("WWW-Authenticate", "").startswith("Bearer")

    def test_a_session_alone_is_not_enough_there_either(self, experimenter_client):
        r = experimenter_client.get(V1)
        assert r.status_code == 401 and r.get_json() == BAD_TOKEN

    def test_the_route_is_the_machine_apis(self, experiments_app):
        adapter = experiments_app.url_map.bind("localhost")
        for path in (V1, V1 + "/"):
            for method in METHODS:
                endpoint, args = adapter.match(path, method=method)
                assert endpoint == "experiments_api.unknown" and args == {"rest": ""}
        # The session routes keep their keys.
        assert adapter.match("/api/experiments/MKT-1", method="GET")[0] == \
            "experiments.get_experiment"


# -- review-security-3: wrong methods ----------------------------------------------------------


@needs_db
class TestWrongMethods:
    API_PATHS = [("PUT", "/api/experiments"), ("DELETE", "/api/experiments"),
                 ("PUT", "/api/experiments/MKT-1"), ("POST", "/api/experiments/MKT-1"),
                 ("PUT", "/api/experiments/meta"), ("POST", "/api/experiments/tokens/abc")]
    PAGE_PATHS = [("POST", "/experiments"), ("POST", "/experiments/verwaltung"),
                  ("DELETE", "/experiments/MKT-1")]

    @pytest.mark.parametrize("method, path", API_PATHS)
    def test_a_member_gets_the_same_404_as_for_a_get(self, exp_member_client, mkt, method,
                                                    path):
        assert exp_member_client.get("/api/experiments").get_json() == NOT_FOUND
        r = exp_member_client.open(path, method=method)
        assert r.status_code == 404
        assert r.get_json() == NOT_FOUND
        assert "Allow" not in r.headers

    @pytest.mark.parametrize("method, path", PAGE_PATHS)
    def test_a_member_gets_a_404_page(self, exp_member_client, mkt, method, path):
        r = exp_member_client.open(path, method=method)
        assert r.status_code == 404
        assert "Allow" not in r.headers

    @pytest.mark.parametrize("method, path", API_PATHS)
    def test_a_viewer_gets_a_json_405(self, experimenter_client, mkt, method, path):
        r = experimenter_client.open(path, method=method)
        assert r.status_code == 405
        assert r.get_json() == METHOD
        allowed = {m.strip() for m in r.headers["Allow"].split(",")}
        assert allowed and method not in allowed
        assert r.headers["Cache-Control"] == "no-store"

    def test_a_viewer_on_a_page_keeps_the_ordinary_405(self, experimenter_client, mkt):
        r = experimenter_client.open("/experiments", method="POST")
        assert r.status_code == 405

    def test_signed_out_callers_are_still_asked_to_sign_in(self, experiments_app):
        r = experiments_app.test_client().open("/api/experiments", method="PUT")
        assert r.status_code == 401

    def test_the_rest_of_the_app_keeps_its_answers(self, exp_member_client, experimenter_client):
        for client in (exp_member_client, experimenter_client):
            r = client.open("/api/search", method="PUT")
            assert r.status_code == 405 and r.get_json() is None
            r = client.open("/api/nothing-here", method="PUT")
            assert r.status_code == 404 and r.get_json() is None


# -- review-security-5: the search page does not shrink ------------------------------------------


def _doc(i, score):
    pointer = f"corpus/akte-{i:03d}/Dokument.pdf"
    return {"doc_id": pointer, "path": pointer, "title": f"Dokument {i}", "score": score,
            "cosine_similarity": score}


def _exp(n, score):
    pointer = f"experiments/marketing/MKT-{n}"
    return {"doc_id": pointer, "path": pointer, "title": f"MKT-{n}", "score": score,
            "cosine_similarity": score, "top_chunks": [{"text": "Hypothese"}]}


def _ranked(n_experiments, n_documents):
    """Knovas' ranking: the experiments first, the documents below them."""
    rows = [_exp(n + 1, 0.99 - n * 0.001) for n in range(n_experiments)]
    rows += [_doc(i + 1, 0.9 - i * 0.005) for i in range(n_documents)]
    return rows


class _TopN:
    """search_documents that honours ``limit`` the way Knovas does."""

    def __init__(self, ranked):
        self.ranked = ranked
        self.limits = []

    def __call__(self, query, limit=20, filters=None):
        self.limits.append(limit)
        rows = [dict(row) for row in self.ranked[:limit]]
        return {"results": rows, "total": len(rows),
                "semantix": {"status": "ok", "result_count": len(rows),
                             "pointers": [row["doc_id"] for row in rows]}}


def _search(client, limit):
    r = client.post("/api/search", json={"query": "Dokument", "limit": limit})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _use(knovas_fake):
    DummyKnovasClient.last_instance.search_documents = knovas_fake
    return knovas_fake


class TestSearchPageHelpers:
    def test_fetch_size(self):
        from web_interface.app import _search_fetch_size

        assert _search_fetch_size(1) == 2
        assert _search_fetch_size(10) == 20
        assert _search_fetch_size(20) == 40
        # The ceiling is Knovas' own maximum (50, see _SEARCH_LIMIT_MAX).
        assert _search_fetch_size(30) == 50
        assert _search_fetch_size(50) == 50
        assert _search_fetch_size(100) == 100
        assert _search_fetch_size(190) == 190
        assert _search_fetch_size(500) == 500

    @pytest.mark.parametrize("raw, expected", [
        (10, 10), ("15", 15), (7.9, 7), (0, 1), (-5, 1), (None, 20), ("x", 20), (True, 20),
        ([3], 20), (float("inf"), 20),
    ])
    def test_page_limit(self, raw, expected):
        from web_interface.app import _search_page_limit

        assert _search_page_limit(raw, 20) == expected

    def test_cut_keeps_semantix_in_step(self):
        from web_interface.app import _cut_search_page

        answer = {"results": [_doc(i, 0.9) for i in range(1, 6)], "total": 5,
                  "semantix": {"status": "ok", "result_count": 5,
                               "pointers": ["/corpus/akte-001/Dokument.pdf",
                                            {"pointer": "corpus/akte-004/Dokument.pdf"},
                                            "corpus/akte-005/Dokument.pdf", "other"]}}
        out, cut = _cut_search_page(answer, 3)
        assert cut is True
        assert [r["doc_id"] for r in out["results"]] == [
            "corpus/akte-001/Dokument.pdf", "corpus/akte-002/Dokument.pdf",
            "corpus/akte-003/Dokument.pdf"]
        assert out["total"] == 3
        assert out["semantix"]["pointers"] == ["/corpus/akte-001/Dokument.pdf", "other"]
        assert out["semantix"]["result_count"] == 3
        assert len(answer["results"]) == 5  # the input is not changed
        assert _cut_search_page(answer, 5) == (answer, False)

    def test_one_wider_question_when_the_margin_is_not_enough(self):
        from experiments.search import SearchIntegration
        from web_interface.app import _fetch_search_page

        split = SearchIntegration(settings=None, connection=lambda: None,
                                  current_user=lambda: None, enabled=False).split
        knovas = _TopN(_ranked(12, 20))
        answer, hits, more = _fetch_search_page(lambda n: knovas("q", n), split, 5)
        assert knovas.limits == [10, 20]
        assert len(answer["results"]) == 5 and len(hits) == 12 and more is True

    def test_a_failing_wider_question_keeps_the_first_answer(self):
        from experiments.search import SearchIntegration
        from web_interface.app import _fetch_search_page

        split = SearchIntegration(settings=None, connection=lambda: None,
                                  current_user=lambda: None, enabled=False).split
        knovas = _TopN(_ranked(8, 20))

        def ask(n):
            if knovas.limits:
                raise RuntimeError("Knovas weg")
            return knovas("q", n)

        answer, hits, more = _fetch_search_page(ask, split, 5)
        assert [r["doc_id"] for r in answer["results"]] == [
            "corpus/akte-001/Dokument.pdf", "corpus/akte-002/Dokument.pdf"]
        assert len(hits) == 8 and more is True

    def test_without_over_fetch_it_asks_for_the_page(self):
        from experiments.search import SearchIntegration
        from web_interface.app import _fetch_search_page

        split = SearchIntegration(settings=None, connection=lambda: None,
                                  current_user=lambda: None, enabled=False).split
        knovas = _TopN(_ranked(0, 3))
        answer, _, more = _fetch_search_page(lambda n: knovas("q", n), split, 5,
                                             over_fetch=False)
        assert knovas.limits == [5] and len(answer["results"]) == 3 and more is False


@needs_db
class TestSearchPageDoesNotShrink:
    def test_a_member_gets_a_full_page_of_documents(self, exp_member_client):
        knovas = _use(_TopN(_ranked(8, 29)))
        payload = _search(exp_member_client, 10)
        assert knovas.limits == [20]
        assert [r["doc_id"] for r in payload["results"]] == [
            f"corpus/akte-{i:03d}/Dokument.pdf" for i in range(1, 11)]
        assert payload["total"] == 10 and payload["has_more"] is True
        assert payload["semantix"]["pointers"] == [r["doc_id"] for r in payload["results"]]
        assert payload["semantix"]["result_count"] == 10
        assert "experiments/" not in str(payload)

    def test_the_margin_is_widened_once_when_experiments_fill_it(self, exp_member_client):
        knovas = _use(_TopN(_ranked(12, 20)))
        payload = _search(exp_member_client, 5)
        assert knovas.limits == [10, 20]
        assert len(payload["results"]) == 5 and payload["has_more"] is True

    def test_when_knovas_has_no_more_the_page_is_what_there_is(self, exp_member_client):
        knovas = _use(_TopN(_ranked(3, 2)))
        payload = _search(exp_member_client, 5)
        assert knovas.limits == [10]
        assert len(payload["results"]) == 2 and payload["has_more"] is False

    def test_documents_below_the_page_are_not_granted(self, exp_member_client, member,
                                                      tmp_path):
        from document_grants import DocumentGrantStore

        _use(_TopN(_ranked(8, 29)))
        _search(exp_member_client, 10)
        grants = DocumentGrantStore(str(tmp_path / "grants.sqlite3"))
        assert grants.granted(str(member.id), "corpus/akte-010/Dokument.pdf")
        assert not grants.granted(str(member.id), "corpus/akte-011/Dokument.pdf")

    def test_a_viewer_gets_the_experiment_in_its_place(self, experimenter_client, mkt):
        _use(_TopN(_ranked(1, 29)))
        payload = _search(experimenter_client, 5)
        assert [r["doc_id"] for r in payload["results"]] == [
            "experiments/marketing/MKT-1"] + [
            f"corpus/akte-{i:03d}/Dokument.pdf" for i in range(1, 5)]
        assert payload["total"] == 5 and payload["has_more"] is True

    def test_a_viewer_who_switched_them_off_gets_a_full_page(self, experimenter_client, mkt):
        r = experimenter_client.open("/api/experiments/preferences", method="PUT",
                                     json={"show_in_search": False})
        assert r.status_code == 200
        _use(_TopN(_ranked(8, 29)))
        payload = _search(experimenter_client, 10)
        assert len(payload["results"]) == 10
        assert all(r["doc_id"].startswith("corpus/") for r in payload["results"])


# -- review-security-6: the caller's address ------------------------------------------------------


class TestClientIp:
    @pytest.fixture
    def ip_in(self, monkeypatch):
        from flask import Flask

        from identity.webauth import client_ip

        app = Flask(__name__)

        def run(xff=None, remote="10.9.8.7", hops=None, headers=None):
            if hops is None:
                monkeypatch.delenv("PLATFORM_TRUSTED_PROXY_HOPS", raising=False)
            else:
                monkeypatch.setenv("PLATFORM_TRUSTED_PROXY_HOPS", str(hops))
            sent = list(headers or [])
            if xff is not None:
                sent.append(("X-Forwarded-For", xff))
            with app.test_request_context("/", headers=sent,
                                          environ_base={"REMOTE_ADDR": remote}):
                return client_ip()

        return run

    def test_the_entry_the_proxy_added_not_the_one_the_client_wrote(self, ip_in):
        assert ip_in("203.0.113.9, 198.51.100.4") == "198.51.100.4"
        assert ip_in("198.51.100.4") == "198.51.100.4"

    def test_two_proxies(self, ip_in):
        assert ip_in("203.0.113.9, 198.51.100.4, 172.17.0.1", hops=2) == "198.51.100.4"

    def test_fewer_entries_than_proxies_or_none_trusted(self, ip_in):
        assert ip_in("198.51.100.4", hops=2) == "10.9.8.7"
        assert ip_in("203.0.113.9, 198.51.100.4", hops=0) == "10.9.8.7"
        assert ip_in(None) == "10.9.8.7"
        assert ip_in("198.51.100.4", hops="nonsense") == "198.51.100.4"

    def test_anything_but_an_address_is_ignored(self, ip_in):
        assert ip_in("203.0.113.9, x") == "10.9.8.7"
        assert ip_in("") == "10.9.8.7"
        assert ip_in(None, remote="") is None

    def test_repeated_headers_count_as_one_list(self, ip_in):
        assert ip_in(None, headers=[("X-Forwarded-For", "203.0.113.9"),
                                    ("X-Forwarded-For", "198.51.100.4")]) == "198.51.100.4"

    def test_the_experiments_audit_uses_the_same_rule(self, monkeypatch):
        from flask import Flask

        from web_interface import experiments_routes

        monkeypatch.delenv("PLATFORM_TRUSTED_PROXY_HOPS", raising=False)
        with Flask(__name__).test_request_context(
                "/", headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.4"},
                environ_base={"REMOTE_ADDR": "10.9.8.7"}):
            meta = experiments_routes._request_meta("t-1")
        assert meta["ip"] == "198.51.100.4" and meta["token_id"] == "t-1"


@needs_db
class TestAuditAndSessionAddress:
    FORWARDED = {"X-Forwarded-For": "203.0.113.77, 198.51.100.4"}

    def _audit_ip(self, platform_db, action):
        row = platform_db.execute(
            "SELECT host(ip) FROM audit_log WHERE action = %s ORDER BY id DESC LIMIT 1",
            (action,)).fetchone()
        return row[0] if row else None

    def test_a_note_through_the_session_api(self, experimenter_client, mkt, platform_db,
                                            monkeypatch):
        monkeypatch.delenv("PLATFORM_TRUSTED_PROXY_HOPS", raising=False)
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/notes",
                                     json={"body": "x"}, headers=self.FORWARDED)
        assert r.status_code == 201, r.get_json()
        assert self._audit_ip(platform_db, "experiments.note.add") == "198.51.100.4"

    def test_a_note_through_the_machine_api(self, api, experimenter_client, mkt, platform_db,
                                            monkeypatch):
        monkeypatch.delenv("PLATFORM_TRUSTED_PROXY_HOPS", raising=False)
        token = make_token(experimenter_client)
        r = api.post(f"{V1}/experiments/{mkt['key']}/notes", json={"body": "CI"},
                     headers={**bearer(token), **self.FORWARDED})
        assert r.status_code == 201, r.get_json()
        assert self._audit_ip(platform_db, "experiments.note.add") == "198.51.100.4"

    def test_the_refusal_log_line(self, api, experiments_app, caplog, monkeypatch):
        monkeypatch.delenv("PLATFORM_TRUSTED_PROXY_HOPS", raising=False)
        with caplog.at_level(logging.INFO, logger="web_interface.experiments_routes"):
            r = api.get(f"{V1}/ping", headers={**bearer("kxp_bogus"),
                                               "X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
        assert r.status_code == 401
        lines = [rec.getMessage() for rec in caplog.records if "refused" in rec.getMessage()]
        assert lines and "from 10.0.0.1" in lines[-1] and "203.0.113.9" not in lines[-1]

    @pytest.mark.parametrize("forwarded, expected", [
        ("203.0.113.9, 198.51.100.5", "198.51.100.5"),
        # Not an address: the connection's own, and the sign-in still works
        # (the column is INET; the raw value used to break the insert).
        ("x", "127.0.0.1"),
    ])
    def test_the_session_list(self, experiments_app, experimenter, platform_db, monkeypatch,
                              forwarded, expected):
        monkeypatch.delenv("PLATFORM_TRUSTED_PROXY_HOPS", raising=False)
        client = experiments_app.test_client()
        page = client.get("/login")
        r = client.post("/login", data={"login_name": experimenter.email, "password": PASSWORD,
                                        "csrf_token": _csrf_from(page.data.decode("utf-8"))},
                        headers={"X-Forwarded-For": forwarded})
        assert r.status_code == 302, r.data[:300]
        row = platform_db.execute(
            "SELECT host(ip) FROM sessions WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
            (str(experimenter.id),)).fetchone()
        assert row[0] == expected


# -- review-security-7: one CSV import at a time --------------------------------------------------


CSV = ("variant;observed_at;ctr;ctr.count\n"
       "A;01.09.2026;12;1000\n"
       "B;01.09.2026;19;1010\n").encode("utf-8")


def _upload(client, key, content=CSV):
    return client.post(f"/api/experiments/{key}/measurements/csv",
                       data={"file": (io.BytesIO(content), "import.csv")},
                       content_type="multipart/form-data")


@needs_db
class TestOneCsvImportAtATime:
    def test_a_second_import_is_refused_before_its_upload_is_read(self, experimenter_client,
                                                                  mkt, monkeypatch):
        from werkzeug.wrappers import Request

        from web_interface import experiments_routes

        def boom(self):  # pragma: no cover - must not be reached
            raise AssertionError("the upload was parsed")

        assert experiments_routes._CSV_IMPORT_SLOTS.acquire(blocking=False)
        try:
            with monkeypatch.context() as patched:
                patched.setattr(Request, "_load_form_data", boom)
                r = _upload(experimenter_client, mkt["key"])
        finally:
            experiments_routes._CSV_IMPORT_SLOTS.release()
        assert r.status_code == 503
        assert r.get_json() == {"success": False, "error": CSV_BUSY}
        assert r.headers["Retry-After"] == "10"
        # Free again: the next one goes through.
        r = _upload(experimenter_client, mkt["key"])
        assert r.status_code == 201, r.get_json()

    def test_while_one_runs_another_waits_its_turn(self, experiments_app, experimenter, mkt,
                                                   monkeypatch):
        from experiments.service import ExperimentService

        started, finish = threading.Event(), threading.Event()
        real = ExperimentService.import_csv

        def slow(self, *args, **kwargs):
            started.set()
            assert finish.wait(30)
            return real(self, *args, **kwargs)

        monkeypatch.setattr(ExperimentService, "import_csv", slow)
        first_client = _signed_in(experiments_app, experimenter.email)
        second_client = _signed_in(experiments_app, experimenter.email)
        answers = {}
        worker = threading.Thread(
            target=lambda: answers.setdefault("first", _upload(first_client, mkt["key"])))
        worker.start()
        try:
            assert started.wait(30)
            second = _upload(second_client, mkt["key"])
        finally:
            finish.set()
            worker.join(60)
        assert second.status_code == 503 and second.get_json()["error"] == CSV_BUSY
        assert answers["first"].status_code == 201, answers["first"].get_json()

    def test_a_refused_import_frees_the_slot(self, experimenter_client, mkt):
        from web_interface import experiments_routes

        r = _upload(experimenter_client, mkt["key"], b"variant;ctr;ctr.count\nZ;1;10\n")
        assert r.status_code == 400
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/measurements/csv",
                                     data={"other": "x"}, content_type="multipart/form-data")
        assert r.status_code == 400
        assert experiments_routes._CSV_IMPORT_SLOTS.acquire(blocking=False)
        experiments_routes._CSV_IMPORT_SLOTS.release()
