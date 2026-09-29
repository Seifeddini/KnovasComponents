"""The Experimente web layer: pages, the session JSON API and their gates.

Asserted on the routes, never on what a page draws: hiding a button is
presentation, refusing the request is the control. The service behind the
routes has its own tests (test_experiments_service.py); these check what the
web layer adds -- the viewing-role gate (404, as if the module were not
there), CSRF, strict JSON bodies, the error mapping (German message and
status, never exception text), the CSV upload limits and the page context.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (sections 10, 13)
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import logging
import uuid

import pytest

pytest.importorskip("flask")

from conftest import _signed_in, platform_db_reachable  # noqa: E402

needs_db = pytest.mark.skipif(not platform_db_reachable(),
                              reason="No PostgreSQL at the identity test DSN")

NOT_FOUND = "Nicht gefunden."
MANAGE_ONLY = "Nur f\u00fcr Verantwortliche der Experimente."


# -- helpers --------------------------------------------------------------------


def put(client, url, body):
    return client.open(url, method="PUT", json=body)


def install(client, pack="marketing"):
    r = client.post(f"/api/experiments/packs/{pack}/install", json={})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["result"]


def create(client, **extra):
    body = {"domain": "marketing", "type": "ab_test", "title": "Betreffzeile mit Frage",
            "hypothesis": "Eine Frage in der Betreffzeile erh\u00f6ht die Klickrate."}
    body.update(extra)
    r = client.post("/api/experiments", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["experiment"]


def page_data(html):
    marker = '<script type="application/json" id="kxPageData">'
    start = html.index(marker) + len(marker)
    return json.loads(html[start:html.index("</script>", start)])


@pytest.fixture
def mkt(exp_manager_client, experimenter_client):
    """The marketing pack installed and one A/B test (MKT-1) created by Eva."""
    install(exp_manager_client)
    return create(experimenter_client)


# -- pure helpers (no database) ----------------------------------------------------


class TestPlainJson:
    def test_non_finite_numbers_become_null(self):
        from web_interface.experiments_routes import _plain

        out = _plain({"a": float("nan"), "b": [float("inf"), -float("inf"), 1.5], "c": 2})
        assert out == {"a": None, "b": [None, None, 1.5], "c": 2}
        # And the result is strict JSON.
        json.dumps(out, allow_nan=False)

    def test_other_types_become_json_types(self):
        from web_interface.experiments_routes import _plain

        moment = _dt.datetime(2026, 9, 28, 10, 0, tzinfo=_dt.timezone.utc)
        ident = uuid.uuid4()
        out = _plain({"at": moment, "day": _dt.date(2026, 9, 28), "id": ident,
                      "d": decimal.Decimal("1.25"), "bad": decimal.Decimal("NaN"),
                      "s": frozenset({"b", "a"}), "t": (1, 2), 3: "x"})
        assert out == {"at": "2026-09-28T10:00:00+00:00", "day": "2026-09-28", "id": str(ident),
                       "d": 1.25, "bad": None, "s": ["a", "b"], "t": [1, 2], "3": "x"}


class TestKeyPattern:
    @pytest.mark.parametrize("key", ["MKT-1", "ENG-123456789", "A1-7", "ABCDEFGH-1"])
    def test_valid(self, key):
        from web_interface.experiments_routes import KEY_RE

        assert KEY_RE.fullmatch(key)

    @pytest.mark.parametrize("key", ["mkt-1", "MKT-", "MKT-1x", "M-1", "ABCDEFGHI-1",
                                     "MKT-1234567890", "MKT_1", "../MKT-1", "MKT-1\n", ""])
    def test_invalid(self, key):
        from web_interface.experiments_routes import KEY_RE

        assert not KEY_RE.fullmatch(key)


class TestStrictBody:
    """_body() in a bare request context: declared JSON only, no NaN."""

    def _body(self, data, content_type="application/json"):
        from flask import Flask

        from web_interface.experiments_routes import _body

        app = Flask(__name__)
        with app.test_request_context("/x", method="POST", data=data, content_type=content_type):
            return _body()

    def test_empty_body_is_none(self):
        assert self._body(b"") is None
        assert self._body(b"   ") is None

    def test_json_object(self):
        assert self._body(b'{"a": 1}') == {"a": 1}

    @pytest.mark.parametrize("raw", [b'{"a": NaN}', b'{"a": Infinity}', b'[-Infinity]'])
    def test_non_finite_constants_are_refused(self, raw):
        from experiments.errors import ValidationError

        with pytest.raises(ValidationError):
            self._body(raw)

    def test_malformed_json_is_refused(self):
        from experiments.errors import ValidationError

        with pytest.raises(ValidationError):
            self._body(b'{"a": ')

    def test_deep_nesting_is_refused_not_crashing(self):
        from experiments.errors import ValidationError

        with pytest.raises(ValidationError):
            self._body(b"[" * 100_000)

    def test_undeclared_json_is_refused(self):
        """The access_groups hook only reads declared JSON; reading anything
        else as JSON here would step around it."""
        from experiments.errors import ValidationError

        with pytest.raises(ValidationError):
            self._body(b'{"access_groups": ["g"]}', content_type="text/plain")


class TestEvaluationsLimit:
    @pytest.mark.parametrize("raw, expected", [
        (None, 20), ("", 20), ("5", 5), ("0", 1), ("-3", 1), ("61", 60), ("1000", 60),
        ("abc", 20), (" 7 ", 7),
    ])
    def test_clamped(self, raw, expected):
        from web_interface.experiments_routes import _evaluations_limit

        assert _evaluations_limit(raw) == expected


class TestBearerEndpointNames:
    def test_only_the_machine_api(self):
        from web_interface.experiments_routes import bearer_endpoints

        names = ["experiments.list_page", "experiments_api.ping", "experiments_apix.nope",
                 "experiments_api.add_run", "static"]
        assert bearer_endpoints(names) == ["experiments_api.add_run", "experiments_api.ping"]


class TestMergeExperimentRows:
    def test_sorted_by_score_and_cut_to_the_limit(self):
        from web_interface.app import _merge_experiment_rows

        docs = [{"doc_id": "a", "score": 0.9}, {"doc_id": "b", "score": 0.5}]
        rows = [{"doc_id": "experiments/m/MKT-1", "score": 0.7}]
        merged = _merge_experiment_rows(docs, rows, 2)
        assert [r["doc_id"] for r in merged] == ["a", "experiments/m/MKT-1"]

    def test_bad_scores_and_limits_do_not_raise(self):
        from web_interface.app import _merge_experiment_rows

        docs = [{"doc_id": "a", "score": "x"}, {"doc_id": "b"}]
        rows = [{"doc_id": "e", "score": 0.1}]
        assert [r["doc_id"] for r in _merge_experiment_rows(docs, rows, "zwanzig")] == ["e", "a", "b"]
        assert len(_merge_experiment_rows(docs, rows, 0)) == 1


# -- pages -----------------------------------------------------------------------


@needs_db
class TestPages:
    def test_list_page_for_an_experimenter(self, experimenter_client):
        r = experimenter_client.get("/experiments")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert 'name="csrf-token"' in html
        data = page_data(html)
        assert data["canManage"] is False and data["pointerPrefix"] == "experiments"
        assert "no-store" in r.headers.get("Cache-Control", "")

    def test_manage_page_for_every_viewer_and_the_flag_for_managers(
            self, experimenter_client, exp_manager_client, exp_admin_client):
        assert page_data(experimenter_client.get("/experiments/verwaltung")
                         .get_data(as_text=True))["canManage"] is False
        assert page_data(exp_manager_client.get("/experiments/verwaltung")
                         .get_data(as_text=True))["canManage"] is True
        assert page_data(exp_admin_client.get("/experiments/verwaltung")
                         .get_data(as_text=True))["canManage"] is True

    def test_detail_page_of_an_existing_experiment(self, experimenter_client, mkt):
        r = experimenter_client.get(f"/experiments/{mkt['key']}")
        assert r.status_code == 200
        assert page_data(r.get_data(as_text=True))["experimentKey"] == mkt["key"]

    @pytest.mark.parametrize("path", ["/experiments/MKT-999", "/experiments/mkt-1",
                                      "/experiments/MKT-1x", "/experiments/..%2Fadmin"])
    def test_detail_page_of_anything_else_is_404(self, experimenter_client, mkt, path):
        assert experimenter_client.get(path).status_code == 404

    @pytest.mark.parametrize("path", ["/experiments", "/experiments/verwaltung", "/experiments/MKT-1"])
    def test_a_member_gets_404_on_every_page(self, exp_member_client, mkt, path):
        r = exp_member_client.get(path)
        assert r.status_code == 404
        assert "Experimente" not in r.get_data(as_text=True)

    def test_the_search_page_sidebar_offers_it_to_viewers_only(
            self, experimenter_client, exp_member_client):
        assert 'href="/experiments"' in experimenter_client.get("/").get_data(as_text=True)
        assert 'href="/experiments"' not in exp_member_client.get("/").get_data(as_text=True)

    def test_anonymous_callers_are_sent_to_sign_in(self, experiments_app):
        client = experiments_app.test_client()
        r = client.get("/experiments")
        assert r.status_code == 302 and "/login" in r.headers["Location"]
        assert client.get("/api/experiments").status_code == 401
        assert client.get("/api/experiments/meta").status_code == 401


# -- the viewing-role gate and the manager gate --------------------------------------


@needs_db
class TestRoleGate:
    @pytest.mark.parametrize("method, path", [
        ("GET", "/api/experiments"), ("GET", "/api/experiments/meta"),
        ("POST", "/api/experiments"), ("GET", "/api/experiments/tokens"),
        ("POST", "/api/experiments/tokens"), ("GET", "/api/experiments/MKT-1"),
        ("GET", "/api/experiments/domains"), ("PUT", "/api/experiments/preferences"),
        ("POST", "/api/experiments/MKT-1/measurements/csv"), ("GET", "/api/experiments/search?q=x"),
    ])
    def test_a_member_gets_404_as_if_nothing_were_there(self, exp_member_client, mkt,
                                                        method, path):
        r = exp_member_client.open(path, method=method, json={})
        assert r.status_code == 404
        assert r.get_json() == {"success": False, "error": NOT_FOUND}

    def test_an_experimenter_may_read(self, experimenter_client, mkt):
        assert experimenter_client.get("/api/experiments").status_code == 200
        assert experimenter_client.get("/api/experiments/meta").status_code == 200
        assert experimenter_client.get(f"/api/experiments/{mkt['key']}").status_code == 200

    @pytest.mark.parametrize("method, path, body", [
        ("POST", "/api/experiments/domains", {"key": "legal", "name": "Recht", "id_prefix": "LEG"}),
        ("POST", "/api/experiments/packs/sales/install", {}),
        ("GET", "/api/experiments/index", None),
        ("POST", "/api/experiments/index/reindex", {}),
        ("PUT", "/api/experiments/settings", {"show_in_search": False}),
        ("DELETE", "/api/experiments/MKT-1", None),
        ("POST", "/api/experiments/packs/import", {"text": "pack: x"}),
        ("POST", "/api/experiments/metrics", {"key": "nps", "name": "NPS", "kind": "mean"}),
    ])
    def test_manager_only_routes_refuse_an_experimenter_with_403(
            self, experimenter_client, mkt, method, path, body):
        r = experimenter_client.open(path, method=method, json=body)
        assert r.status_code == 403
        assert r.get_json() == {"success": False, "error": MANAGE_ONLY}

    def test_an_administrator_may_manage(self, exp_admin_client):
        counts = install(exp_admin_client, "sales")
        assert counts["domain"] == 1 and counts["types"] >= 1
        assert exp_admin_client.get("/api/experiments/index").status_code == 200


# -- CSRF, bodies, sizes -------------------------------------------------------------


@needs_db
class TestCsrf:
    @pytest.mark.parametrize("method, path", [
        ("POST", "/api/experiments"), ("PUT", "/api/experiments/preferences"),
        ("POST", "/api/experiments/tokens"), ("DELETE", "/api/experiments/MKT-1"),
        ("PATCH", "/api/experiments/MKT-1"), ("POST", "/api/experiments/MKT-1/measurements/csv"),
    ])
    def test_state_changes_without_the_header_are_refused(self, experiments_app, experimenter,
                                                          mkt, method, path):
        client = _signed_in(experiments_app, experimenter.email, with_csrf=False)
        r = client.open(path, method=method, json={})
        assert r.status_code == 403

    def test_a_wrong_header_is_refused_too(self, experiments_app, experimenter):
        client = _signed_in(experiments_app, experimenter.email, with_csrf=False)
        r = client.post("/api/experiments/tokens", json={"name": "CI"},
                        headers={"X-CSRF-Token": "falsch"})
        assert r.status_code == 403

    def test_reads_need_no_header(self, experiments_app, experimenter):
        client = _signed_in(experiments_app, experimenter.email, with_csrf=False)
        assert client.get("/api/experiments/meta").status_code == 200


@needs_db
class TestBodies:
    def test_a_body_naming_access_groups_is_refused(self, experimenter_client, mkt):
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/notes",
                                     json={"body": "x", "access_groups": ["g"]})
        assert r.status_code == 400
        assert "access_groups" in r.get_json()["error"]

    def test_undeclared_json_is_refused(self, experimenter_client, mkt):
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/notes",
                                     data='{"body": "x", "access_groups": ["g"]}',
                                     content_type="text/plain")
        assert r.status_code == 400
        assert r.get_json()["error"] == "Die Anfrage muss JSON sein (Content-Type: application/json)."

    def test_malformed_json_is_400_not_500(self, experimenter_client, mkt):
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/notes", data="{nope",
                                     content_type="application/json")
        assert r.status_code == 400
        assert r.get_json()["success"] is False

    def test_nan_is_refused(self, experimenter_client, mkt):
        r = experimenter_client.post(
            f"/api/experiments/{mkt['key']}/measurements",
            data='{"rows": [{"metric": "ctr", "variant": "A", "value": NaN, "count": 10}]}',
            content_type="application/json")
        assert r.status_code == 400
        assert r.get_json()["error"] == "Die Anfrage ist kein g\u00fcltiges JSON."

    def test_a_json_array_is_refused_by_the_service(self, experimenter_client):
        r = experimenter_client.post("/api/experiments", json=[1, 2])
        assert r.status_code == 400
        assert r.get_json()["error"] == "Die Anfrage muss ein JSON-Objekt sein."

    def test_an_oversized_body_is_413_in_json(self, experiments_app, experimenter_client, mkt):
        experiments_app.config["MAX_CONTENT_LENGTH"] = 2000
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/notes",
                                     json={"body": "x" * 5000})
        assert r.status_code == 413
        assert r.get_json() == {"success": False, "error": "Die Anfrage ist zu gross."}


@needs_db
class TestCsvUpload:
    CSV = ("variant;observed_at;ctr;ctr.count;cost_per_click;cost_per_click.denominator;notiz\n"
           "A;01.09.2026;12;1000;30,5;12;x\n"
           "B;01.09.2026;19;1010;41;19;y\n").encode("utf-8")

    def test_a_wide_csv_is_imported(self, experimenter_client, mkt):
        import io

        r = experimenter_client.post(
            f"/api/experiments/{mkt['key']}/measurements/csv",
            data={"file": (io.BytesIO(self.CSV), "linkedin.csv")},
            content_type="multipart/form-data")
        assert r.status_code == 201, r.get_json()
        result = r.get_json()["result"]
        assert result["inserted"] == 4
        assert result["ignored_columns"] == ["notiz"]
        batches = experimenter_client.get(f"/api/experiments/{mkt['key']}/batches").get_json()
        assert batches["result"]["items"][0]["source"] == "csv"
        assert batches["result"]["items"][0]["filename"] == "linkedin.csv"

    def test_without_a_file_it_says_so(self, experimenter_client, mkt):
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/measurements/csv",
                                     data={"other": "x"}, content_type="multipart/form-data")
        assert r.status_code == 400
        body = r.get_json()
        assert body["error"] == "Bitte eine CSV-Datei hochladen." and "file" in body["fields"]

    def test_more_than_20_mb_is_refused_before_it_is_parsed(self, experimenter_client, mkt,
                                                            monkeypatch):
        from werkzeug.wrappers import Request

        def boom(self):  # pragma: no cover - must not be reached
            raise AssertionError("the body was parsed")

        monkeypatch.setattr(Request, "_load_form_data", boom)
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/measurements/csv",
                                     data=b"x" * (20 * 1024 * 1024 + 1),
                                     content_type="multipart/form-data; boundary=zzz")
        assert r.status_code == 413
        assert r.get_json() == {"success": False, "error": "Die Datei ist gr\u00f6sser als 20 MB."}

    def test_a_bad_csv_is_a_400_with_the_line(self, experimenter_client, mkt):
        import io

        r = experimenter_client.post(
            f"/api/experiments/{mkt['key']}/measurements/csv",
            data={"file": (io.BytesIO(b"variant;ctr;ctr.count\nZ;1;10\n"), "x.csv")},
            content_type="multipart/form-data")
        assert r.status_code == 400
        assert "Zeile" in r.get_json()["error"]

    def test_unknown_key_is_404(self, experimenter_client, mkt):
        import io

        r = experimenter_client.post("/api/experiments/MKT-999/measurements/csv",
                                     data={"file": (io.BytesIO(self.CSV), "a.csv")},
                                     content_type="multipart/form-data")
        assert r.status_code == 404


# -- error mapping ---------------------------------------------------------------------


@needs_db
class TestErrorMapping:
    def test_validation_errors_carry_their_fields(self, experimenter_client, mkt):
        r = experimenter_client.post("/api/experiments", json={"domain": "gibtsnicht",
                                                               "type": "ab_test", "title": "x"})
        assert r.status_code == 400
        body = r.get_json()
        assert body["success"] is False and "domain" in body["fields"]

    @pytest.mark.parametrize("path", ["/api/experiments/MKT-999", "/api/experiments/mkt-1",
                                      "/api/experiments/MKT-1x/runs", "/api/experiments/v1"])
    def test_unknown_or_malformed_keys_are_404(self, experimenter_client, mkt, path):
        r = experimenter_client.get(path)
        assert r.status_code == 404
        assert r.get_json() == {"success": False, "error": NOT_FOUND}

    def test_a_stale_row_version_is_409(self, experimenter_client, mkt):
        url = f"/api/experiments/{mkt['key']}"
        ok = experimenter_client.patch(url, json={"title": "Neu", "row_version": mkt["row_version"]})
        assert ok.status_code == 200
        stale = experimenter_client.patch(url, json={"title": "Alt",
                                                     "row_version": mkt["row_version"]})
        assert stale.status_code == 409
        assert stale.get_json()["error"] == ("Das Experiment wurde inzwischen ge\u00e4ndert. "
                                             "Bitte neu laden.")

    def test_a_custom_evaluator_without_runner_is_503(self, experimenter_client, mkt):
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/evaluations",
                                     json={"evaluator": "example.beta_binomial_jl", "metric": "ctr"})
        assert r.status_code == 503
        assert r.get_json()["error"] == "Die Rechenumgebung ist nicht eingerichtet."

    def test_an_unexpected_failure_is_500_without_its_text(self, experimenter_client, mkt,
                                                            monkeypatch, caplog):
        from experiments.service import ExperimentService

        def boom(self, *a, **kw):
            raise RuntimeError("psql: FATAL /etc/geheim.conf")

        monkeypatch.setattr(ExperimentService, "list_experiments", boom)
        with caplog.at_level(logging.ERROR, logger="web_interface.experiments_routes"):
            r = experimenter_client.get("/api/experiments")
        assert r.status_code == 500
        assert r.get_json() == {"success": False, "error": "Interner Serverfehler"}
        assert "geheim" not in r.get_data(as_text=True)
        assert any("geheim" in (rec.exc_text or "") or rec.exc_info for rec in caplog.records)

    def test_a_failure_in_the_role_gate_is_a_json_500(self, experimenter_client, monkeypatch,
                                                      caplog):
        from experiments import permissions

        def boom(user):
            raise RuntimeError("roles table: permission denied for relation user_roles")

        monkeypatch.setattr(permissions, "can_view", boom)
        with caplog.at_level(logging.ERROR, logger="web_interface.experiments_routes"):
            r = experimenter_client.get("/api/experiments/meta")
        assert r.status_code == 500
        assert r.get_json() == {"success": False, "error": "Interner Serverfehler"}
        assert any(rec.exc_info for rec in caplog.records)
        page = experimenter_client.get("/experiments")
        assert page.status_code == 500 and "user_roles" not in page.get_data(as_text=True)

    def test_an_unexpected_failure_on_a_page_is_not_leaked_either(self, experimenter_client, mkt,
                                                                   monkeypatch):
        from experiments import store

        def boom(*a, **kw):
            raise RuntimeError("SELECT secret FROM x")

        monkeypatch.setattr(store, "get_experiment_row", boom)
        experimenter_client._client.application.testing = False
        try:
            r = experimenter_client.get(f"/experiments/{mkt['key']}")
        finally:
            experimenter_client._client.application.testing = True
        assert r.status_code == 500
        assert "secret" not in r.get_data(as_text=True)


# -- the API end to end ------------------------------------------------------------------


@needs_db
class TestMeta:
    def test_what_the_pages_need(self, experimenter_client):
        from experiments import kinds, labels, schema

        meta = experimenter_client.get("/api/experiments/meta").get_json()["meta"]
        assert meta["me"]["display_name"] == "Eva"
        assert meta["can_manage"] is False
        assert [k["key"] for k in meta["kinds"]] == list(kinds.KINDS)
        assert meta["field_types"] == list(schema.FIELD_TYPES)
        assert meta["decision_verdicts"] == labels.DECISION_VERDICT_LABELS
        assert meta["evaluation_verdicts"] == labels.EVALUATION_VERDICT_LABELS
        assert meta["note_kinds"] == labels.NOTE_KIND_LABELS
        assert meta["directions"] == labels.DIRECTION_LABELS
        assert meta["metric_roles"] == labels.METRIC_ROLE_LABELS
        assert meta["run_statuses"] == labels.RUN_STATUS_LABELS
        assert meta["evaluation_statuses"] == labels.EVALUATION_STATUS_LABELS
        assert meta["index_states"] == labels.INDEX_STATE_LABELS
        assert meta["sources"] == labels.SOURCE_LABELS
        assert meta["runner"] == {"configured": False, "ok": False, "languages": {}}
        assert meta["index_enabled"] is False
        assert meta["pointer_prefix"] == "experiments"
        assert meta["max_csv_rows"] == 200_000 and meta["max_rows_per_request"] == 10_000

    def test_a_manager_is_told_so(self, exp_manager_client):
        assert exp_manager_client.get("/api/experiments/meta").get_json()["meta"]["can_manage"] is True

    def test_answers_are_not_cached(self, experimenter_client):
        r = experimenter_client.get("/api/experiments/meta")
        assert r.headers["Cache-Control"] == "no-store"


@needs_db
class TestConfiguration:
    def test_domains_types_metrics_evaluators_packs(self, exp_manager_client, experimenter_client):
        packs = exp_manager_client.get("/api/experiments/packs").get_json()["packs"]
        assert {p["name"] for p in packs} >= {"core", "marketing", "engineering"}
        assert install(exp_manager_client)["domain"] == 1
        # Installing twice is harmless.
        install(exp_manager_client)

        domains = experimenter_client.get("/api/experiments/domains").get_json()["domains"]
        assert [d["key"] for d in domains] == ["marketing"]

        created = exp_manager_client.post("/api/experiments/domains", json={
            "key": "legal-ops", "name": "Legal Ops", "id_prefix": "LOP"})
        assert created.status_code == 200, created.get_json()
        patched = exp_manager_client.patch("/api/experiments/domains/legal-ops",
                                           json={"name": "Legal Operations"})
        assert patched.get_json()["domain"]["name"] == "Legal Operations"
        text = exp_manager_client.get("/api/experiments/domains/marketing/export").get_json()["text"]
        assert "pack: marketing" in text or "marketing" in text

        types = experimenter_client.get("/api/experiments/types?domain=marketing").get_json()["types"]
        ab = next(t for t in types if t["key"] == "ab_test")
        assert experimenter_client.get(f"/api/experiments/types/{ab['id']}").status_code == 200
        checked = exp_manager_client.post("/api/experiments/types/validate",
                                          json={"definition": ab["definition"], "domain": "marketing"})
        assert checked.status_code == 200 and "states" in checked.get_json()["definition"]
        archived = exp_manager_client.post(f"/api/experiments/types/{ab['id']}/archive",
                                           json={"archived": True})
        assert archived.get_json()["type"]["archived"] is True
        bad = exp_manager_client.post(f"/api/experiments/types/{ab['id']}/archive",
                                      json={"archived": "ja"})
        assert bad.status_code == 400
        assert exp_manager_client.get("/api/experiments/types/kein-uuid").status_code == 404

        metrics = experimenter_client.get("/api/experiments/metrics?domain=marketing").get_json()
        assert "ctr" in {m["key"] for m in metrics["metrics"]}
        evaluators = experimenter_client.get("/api/experiments/evaluators").get_json()["evaluators"]
        assert "builtin.describe" in {e["key"] for e in evaluators}

    def test_settings_and_preferences(self, exp_manager_client, experimenter_client):
        assert experimenter_client.get("/api/experiments/preferences").get_json() == {
            "success": True, "preferences": {"show_in_search": True}}
        r = put(experimenter_client, "/api/experiments/preferences", {"show_in_search": False})
        assert r.get_json()["preferences"] == {"show_in_search": False}
        assert put(experimenter_client, "/api/experiments/preferences",
                   {"show_in_search": "nein"}).status_code == 400
        assert put(experimenter_client, "/api/experiments/preferences",
                   {"show_in_search": True, "x": 1}).status_code == 400

        assert experimenter_client.get("/api/experiments/settings").get_json()["settings"] == {
            "show_in_search": True}
        r = put(exp_manager_client, "/api/experiments/settings", {"show_in_search": False})
        assert r.get_json()["settings"] == {"show_in_search": False}
        assert experimenter_client.get("/api/experiments/settings").get_json()["settings"] == {
            "show_in_search": False}

    def test_sample_size(self, experimenter_client):
        r = experimenter_client.get(
            "/api/experiments/sample-size?kind=proportion&base=0.02&mde=0.005")
        assert r.status_code == 200 and r.get_json()["result"]["per_variant"] > 1000
        assert experimenter_client.get(
            "/api/experiments/sample-size?kind=proportion&base=0.02&mde=0").status_code == 400
        assert experimenter_client.get("/api/experiments/sample-size?kind=nope").status_code == 400

    def test_tokens_are_personal(self, experimenter_client, exp_manager_client):
        made = experimenter_client.post("/api/experiments/tokens",
                                        json={"name": "CI", "expires_days": 30})
        assert made.status_code == 200
        token = made.get_json()["token"]
        assert token["token"].startswith("kxp_") and token["hint"].endswith(token["token"][-4:])
        listed = experimenter_client.get("/api/experiments/tokens").get_json()["tokens"]
        assert [t["id"] for t in listed] == [token["id"]]
        assert "token" not in listed[0]
        # Someone else's token cannot be revoked, and is not even there for them.
        assert exp_manager_client.get("/api/experiments/tokens").get_json()["tokens"] == []
        assert exp_manager_client.delete(f"/api/experiments/tokens/{token['id']}").status_code == 404
        revoked = experimenter_client.delete(f"/api/experiments/tokens/{token['id']}")
        assert revoked.status_code == 200 and revoked.get_json()["token"]["revoked"] is True


@needs_db
class TestAnExperimentThroughTheApi:
    def test_the_whole_lifecycle(self, experimenter_client, exp_manager_client, mkt):
        key = mkt["key"]
        base = f"/api/experiments/{key}"
        assert key == "MKT-1" and mkt["status"] == "draft"

        listed = experimenter_client.get("/api/experiments?domain=marketing&archived=0").get_json()
        assert [i["key"] for i in listed["result"]["items"]] == [key]
        assert listed["result"]["total"] == 1

        full = experimenter_client.get(base).get_json()["experiment"]
        assert full["key"] == key and "transitions" in full and "definition" in full

        patched = experimenter_client.patch(base, json={
            "title": "Frage in der Betreffzeile", "row_version": full["row_version"],
            "fields": {"channel": "LinkedIn"}, "tags": ["newsletter"]})
        assert patched.status_code == 200, patched.get_json()
        snap = patched.get_json()["experiment"]
        assert snap["title"] == "Frage in der Betreffzeile" and snap["tags"] == ["newsletter"]

        variants = put(experimenter_client, f"{base}/variants", {"variants": [
            {"key": "A", "name": "Kontrolle", "is_control": True},
            {"key": "B", "name": "Mit Frage"}], "row_version": snap["row_version"]})
        assert variants.status_code == 200, variants.get_json()
        snap = variants.get_json()["experiment"]

        started = experimenter_client.post(f"{base}/transition", json={"to": "running"})
        assert started.status_code == 200, started.get_json()
        assert started.get_json()["experiment"]["status"] == "running"

        added = experimenter_client.post(f"{base}/measurements", json={"rows": [
            {"metric": "ctr", "variant": "A", "value": 120, "count": 10000,
             "observed_at": "2026-09-01"},
            {"metric": "ctr", "variant": "B", "value": 190, "count": 10100,
             "observed_at": "2026-09-08"}]})
        assert added.status_code == 201, added.get_json()
        assert added.get_json()["result"]["inserted"] == 2
        batch_id = added.get_json()["result"]["batch_id"]
        assert experimenter_client.get(f"{base}/batches").get_json()["result"]["items"][0][
            "batch_id"] == batch_id

        run = experimenter_client.post(f"{base}/runs", json={"name": "Woche 1", "variant": "B"})
        assert run.status_code == 201 and run.get_json()["run"]["name"] == "Woche 1"
        assert len(experimenter_client.get(f"{base}/runs").get_json()["result"]["items"]) == 1

        evaluation = experimenter_client.post(f"{base}/evaluations", json={
            "evaluator": "builtin.bayes_proportion", "metric": "ctr"})
        assert evaluation.status_code == 201, evaluation.get_json()
        ev = evaluation.get_json()["evaluation"]
        assert ev["status"] == "done"
        fetched = experimenter_client.get(f"{base}/evaluations/{ev['id']}")
        assert fetched.status_code == 200 and fetched.get_json()["evaluation"]["id"] == ev["id"]
        assert experimenter_client.get(f"{base}/evaluations/{uuid.uuid4()}").status_code == 404
        assert experimenter_client.get(f"{base}/evaluations/nope").status_code == 404

        pipeline = experimenter_client.post(f"{base}/pipeline", json={})
        assert pipeline.status_code == 200
        assert pipeline.get_json()["evaluations"]

        series = experimenter_client.get(f"{base}/metrics/ctr/timeseries?bucket=week")
        assert series.status_code == 200 and series.get_json()["series"]
        assert experimenter_client.get(
            f"{base}/metrics/ctr/timeseries?bucket=year").status_code == 400
        assert experimenter_client.get(f"{base}/metrics/nope/timeseries").status_code == 404

        note = experimenter_client.post(f"{base}/notes", json={"body": "Erste Woche ruhig.",
                                                                "kind": "observation"})
        assert note.status_code == 201
        note_id = note.get_json()["note"]["id"]
        assert experimenter_client.delete(f"{base}/notes/{note_id}").get_json()["result"] == {
            "deleted": note_id}

        analysis = experimenter_client.post(f"{base}/transition", json={"to": "analysis"})
        assert analysis.status_code == 200, analysis.get_json()
        no_learning = experimenter_client.post(f"{base}/decisions", json={"verdict": "ship"})
        assert no_learning.status_code == 400 and "learning" in no_learning.get_json()["fields"]
        decided = experimenter_client.post(f"{base}/decisions", json={
            "verdict": "ship", "rationale": "Klar besser.",
            "learning": "Fragen in Betreffzeilen wirken."})
        assert decided.status_code == 201, decided.get_json()
        assert decided.get_json()["experiment"]["status"] == "decided"

        assert experimenter_client.post(f"{base}/reindex", json={}).get_json()["result"] == {
            "queued": False}
        activity = experimenter_client.get(f"{base}/activity").get_json()["activity"]
        assert {"experiments.experiment.create", "experiments.experiment.decide"} <= {
            a["action"] for a in activity}

        found = experimenter_client.get("/api/experiments/search?q=Betreffzeile").get_json()
        assert found["result"]["source"] == "database"
        assert [i["key"] for i in found["result"]["items"]] == [key]

        # Deleting is for managers, and the batch undo for anyone who may view.
        assert experimenter_client.delete(base).status_code == 403
        undo = experimenter_client.delete(f"{base}/batches/{batch_id}")
        assert undo.status_code == 200 and undo.get_json()["result"] == {"deleted": 2}
        gone = exp_manager_client.delete(base)
        assert gone.status_code == 200 and gone.get_json()["result"] == {"deleted": key}
        assert experimenter_client.get(base).status_code == 404

    def test_a_stop_needs_a_reason(self, experimenter_client, mkt):
        r = experimenter_client.post(f"/api/experiments/{mkt['key']}/transition",
                                     json={"to": "stopped"})
        assert r.status_code == 400
        assert "comment" in r.get_json()["fields"]

    def test_metrics_can_be_reassigned(self, experimenter_client, mkt):
        r = put(experimenter_client, f"/api/experiments/{mkt['key']}/metrics", {"metrics": [
            {"metric": "conversion_rate", "role": "primary"}]})
        assert r.status_code == 200, r.get_json()
        assert [m["key"] for m in r.get_json()["experiment"]["metrics"]] == ["conversion_rate"]


@needs_db
class TestRouteTable:
    """Plan section 10: every contract route exists with its method, route
    parameters are never named doc_id (the file gate would read them), and
    the static paths win over <key>."""

    EXPECTED = {
        ("GET", "/api/experiments"), ("POST", "/api/experiments"),
        ("GET", "/api/experiments/meta"), ("GET", "/api/experiments/search"),
        ("GET", "/api/experiments/sample-size"),
        ("GET", "/api/experiments/preferences"), ("PUT", "/api/experiments/preferences"),
        ("GET", "/api/experiments/settings"), ("PUT", "/api/experiments/settings"),
        ("GET", "/api/experiments/<key>"), ("PATCH", "/api/experiments/<key>"),
        ("DELETE", "/api/experiments/<key>"), ("POST", "/api/experiments/<key>/transition"),
        ("PUT", "/api/experiments/<key>/variants"), ("PUT", "/api/experiments/<key>/metrics"),
        ("POST", "/api/experiments/<key>/measurements"),
        ("POST", "/api/experiments/<key>/measurements/csv"),
        ("GET", "/api/experiments/<key>/batches"),
        ("DELETE", "/api/experiments/<key>/batches/<batch_id>"),
        ("GET", "/api/experiments/<key>/runs"), ("POST", "/api/experiments/<key>/runs"),
        ("POST", "/api/experiments/<key>/notes"),
        ("DELETE", "/api/experiments/<key>/notes/<note_id>"),
        ("POST", "/api/experiments/<key>/evaluations"), ("POST", "/api/experiments/<key>/pipeline"),
        ("GET", "/api/experiments/<key>/evaluations/<evaluation_id>"),
        ("GET", "/api/experiments/<key>/metrics/<metric_key>/timeseries"),
        ("POST", "/api/experiments/<key>/decisions"), ("POST", "/api/experiments/<key>/reindex"),
        ("GET", "/api/experiments/<key>/activity"),
        ("GET", "/api/experiments/domains"), ("POST", "/api/experiments/domains"),
        ("PATCH", "/api/experiments/domains/<domain_key>"),
        ("GET", "/api/experiments/domains/<domain_key>/export"),
        ("GET", "/api/experiments/types"), ("POST", "/api/experiments/types"),
        ("POST", "/api/experiments/types/validate"), ("GET", "/api/experiments/types/<type_id>"),
        ("POST", "/api/experiments/types/<type_id>/versions"),
        ("POST", "/api/experiments/types/<type_id>/archive"),
        ("GET", "/api/experiments/metrics"), ("POST", "/api/experiments/metrics"),
        ("PATCH", "/api/experiments/metrics/<metric_id>"),
        ("GET", "/api/experiments/evaluators"), ("POST", "/api/experiments/evaluators"),
        ("GET", "/api/experiments/evaluators/<evaluator_id>"),
        ("POST", "/api/experiments/evaluators/<evaluator_id>/versions"),
        ("POST", "/api/experiments/evaluators/<evaluator_id>/test"),
        ("GET", "/api/experiments/tokens"), ("POST", "/api/experiments/tokens"),
        ("DELETE", "/api/experiments/tokens/<token_id>"),
        ("GET", "/api/experiments/index"), ("POST", "/api/experiments/index/reindex"),
        ("GET", "/api/experiments/packs"), ("POST", "/api/experiments/packs/<name>/install"),
        ("POST", "/api/experiments/packs/import"),
        ("GET", "/experiments"), ("GET", "/experiments/verwaltung"), ("GET", "/experiments/<key>"),
        ("GET", "/api/experiments/v1/ping"), ("POST", "/api/experiments/v1/experiments"),
        ("GET", "/api/experiments/v1/experiments/<key>"),
        ("POST", "/api/experiments/v1/experiments/<key>/runs"),
        ("POST", "/api/experiments/v1/experiments/<key>/measurements"),
        ("POST", "/api/experiments/v1/experiments/<key>/notes"),
        ("POST", "/api/experiments/v1/experiments/<key>/pipeline"),
        ("GET", "/api/experiments/v1/experiments/<key>/evaluations"),
        # Anything else under /v1 is a JSON 404 once the token checked out.
        ("GET", "/api/experiments/v1/<path:rest>"), ("POST", "/api/experiments/v1/<path:rest>"),
        ("PUT", "/api/experiments/v1/<path:rest>"), ("PATCH", "/api/experiments/v1/<path:rest>"),
        ("DELETE", "/api/experiments/v1/<path:rest>"),
    }

    def _rules(self, app):
        return [r for r in app.url_map.iter_rules()
                if r.endpoint.split(".")[0] in ("experiments", "experiments_api")]

    def test_every_contract_route_is_there(self, experiments_app):
        have = {(m, r.rule) for r in self._rules(experiments_app)
                for m in r.methods - {"HEAD", "OPTIONS"}}
        assert self.EXPECTED <= have
        assert have - self.EXPECTED == set()

    def test_no_route_parameter_is_called_doc_id(self, experiments_app):
        for rule in self._rules(experiments_app):
            assert "doc_id" not in rule.arguments, rule.rule

    def test_page_endpoint_names(self, experiments_app):
        names = {r.endpoint for r in self._rules(experiments_app)}
        assert {"experiments.list_page", "experiments.manage_page",
                "experiments.detail_page"} <= names

    @pytest.mark.parametrize("path, endpoint", [
        ("/api/experiments/meta", "experiments.meta"),
        ("/api/experiments/metrics", "experiments.list_metrics"),
        ("/api/experiments/settings", "experiments.module_settings"),
        ("/api/experiments/v1/ping", "experiments_api.ping"),
        ("/api/experiments/MKT-1", "experiments.get_experiment"),
        ("/experiments/verwaltung", "experiments.manage_page"),
    ])
    def test_static_paths_win_over_the_key(self, experiments_app, path, endpoint):
        adapter = experiments_app.url_map.bind("localhost")
        assert adapter.match(path, method="GET")[0] == endpoint
