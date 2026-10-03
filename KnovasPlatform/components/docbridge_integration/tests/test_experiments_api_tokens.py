"""The Experimente machine API (/api/experiments/v1): personal access tokens.

A CI job authenticates with ``Authorization: Bearer kxp_...`` and with
nothing else. Every refusal path is asserted here, each with the same 401
and the same German message, so a caller cannot tell an unknown token from
a revoked one or from one whose owner lost the role:

* no header, another scheme, an empty token, a token in the query string;
* an unknown, revoked or expired token;
* a token whose owner is disabled, locked, owes a password change, or no
  longer holds a viewing role (a token acts with its owner's roles *now*);
* a session cookie alone -- the API never reads it, and the session API in
  turn never accepts a token.

Then the happy paths a CI job uses: logging runs with per-query rows,
measurements, notes, the evaluation pipeline and reading its verdicts.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (section 10)
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("flask")

from conftest import _signed_in, platform_db_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(not platform_db_reachable(),
                                reason="No PostgreSQL at the identity test DSN")

V1 = "/api/experiments/v1"
BAD_TOKEN = {"success": False, "error": "Ung\u00fcltiger oder abgelaufener Zugangsschl\u00fcssel."}
TRIMMED_KEYS = {"key", "title", "status", "domain", "type", "variants", "metrics", "row_version"}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def make_token(client, name="CI", days=30):
    r = client.post("/api/experiments/tokens", json={"name": name, "expires_days": days})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["token"]


@pytest.fixture
def eva_token(experimenter_client):
    return make_token(experimenter_client)


@pytest.fixture
def api(experiments_app):
    """A client with no session at all, the way CI calls."""
    return experiments_app.test_client()


@pytest.fixture
def engineering(exp_manager_client):
    r = exp_manager_client.post("/api/experiments/packs/engineering/install", json={})
    assert r.status_code == 200, r.get_json()


def assert_refused(response):
    assert response.status_code == 401
    assert response.get_json() == BAD_TOKEN
    assert response.headers.get("WWW-Authenticate", "").startswith("Bearer")
    assert "Set-Cookie" not in response.headers


# -- who may call ----------------------------------------------------------------


class TestPing:
    def test_a_valid_token_acts_as_its_owner(self, api, eva_token):
        r = api.get(f"{V1}/ping", headers=bearer(eva_token["token"]))
        assert r.status_code == 200
        assert r.get_json() == {"success": True,
                                "user": {"display_name": "Eva", "roles": ["experimenter"]}}
        assert r.headers["Cache-Control"] == "no-store"
        assert "Set-Cookie" not in r.headers

    def test_the_scheme_is_case_insensitive(self, api, eva_token):
        r = api.get(f"{V1}/ping", headers={"Authorization": f"bearer  {eva_token['token']} "})
        assert r.status_code == 200

    def test_last_used_is_recorded(self, api, eva_token, platform_db):
        api.get(f"{V1}/ping", headers=bearer(eva_token["token"]))
        used = platform_db.execute("SELECT last_used_at FROM exp_api_tokens WHERE id = %s",
                                   (eva_token["id"],)).fetchone()[0]
        assert used is not None


class TestRefusals:
    def test_without_a_header(self, api, eva_token):
        assert_refused(api.get(f"{V1}/ping"))

    @pytest.mark.parametrize("header", [
        "Basic ZXZhOnBhc3N3b3Jk", "Bearer", "Bearer ", "Token kxp_abc", "kxp_abc",
    ])
    def test_other_schemes_and_empty_tokens(self, api, eva_token, header):
        assert_refused(api.get(f"{V1}/ping", headers={"Authorization": header}))

    @pytest.mark.parametrize("token", [
        "kxp_" + "A" * 43, "kxp_", "abc", "kxp_" + "x" * 500, "kxp_\u00e4\u00f6\u00fc",
    ])
    def test_unknown_tokens(self, api, eva_token, token):
        assert_refused(api.get(f"{V1}/ping", headers={"Authorization": f"Bearer {token}"}))

    def test_a_token_in_the_query_string_is_not_looked_at(self, api, eva_token):
        assert_refused(api.get(f"{V1}/ping?access_token={eva_token['token']}"))
        assert_refused(api.get(f"{V1}/ping?token={eva_token['token']}"))

    def test_a_revoked_token(self, api, experimenter_client, eva_token):
        assert api.get(f"{V1}/ping", headers=bearer(eva_token["token"])).status_code == 200
        experimenter_client.delete(f"/api/experiments/tokens/{eva_token['id']}")
        assert_refused(api.get(f"{V1}/ping", headers=bearer(eva_token["token"])))

    def test_an_expired_token(self, api, eva_token, platform_db):
        platform_db.execute("UPDATE exp_api_tokens SET expires_at = now() - interval '1 second' "
                            "WHERE id = %s", (eva_token["id"],))
        assert_refused(api.get(f"{V1}/ping", headers=bearer(eva_token["token"])))

    def test_a_disabled_owner(self, api, eva_token, experimenter, identity_repo):
        identity_repo.disable(experimenter.id)
        assert_refused(api.get(f"{V1}/ping", headers=bearer(eva_token["token"])))

    def test_a_locked_owner(self, api, eva_token, experimenter, platform_db):
        platform_db.execute("UPDATE users SET locked_until = now() + interval '1 hour' "
                            "WHERE id = %s", (experimenter.id,))
        assert_refused(api.get(f"{V1}/ping", headers=bearer(eva_token["token"])))

    def test_a_locked_status(self, api, eva_token, experimenter, platform_db):
        platform_db.execute("UPDATE users SET status = 'locked' WHERE id = %s", (experimenter.id,))
        assert_refused(api.get(f"{V1}/ping", headers=bearer(eva_token["token"])))

    def test_an_owner_who_must_change_the_password(self, api, eva_token, experimenter, platform_db):
        platform_db.execute("UPDATE users SET must_change_password = TRUE WHERE id = %s",
                            (experimenter.id,))
        assert_refused(api.get(f"{V1}/ping", headers=bearer(eva_token["token"])))

    def test_an_owner_who_lost_the_role(self, api, eva_token, experimenter, identity_repo):
        """The token acts with its owner's roles now, not at creation."""
        identity_repo.revoke_role(experimenter.id, "experimenter")
        assert_refused(api.get(f"{V1}/ping", headers=bearer(eva_token["token"])))

    def test_every_route_is_behind_the_token(self, api, eva_token):
        for method, path in [("POST", "/experiments"), ("GET", "/experiments/MKT-1"),
                             ("POST", "/experiments/MKT-1/runs"),
                             ("POST", "/experiments/MKT-1/measurements"),
                             ("POST", "/experiments/MKT-1/notes"),
                             ("POST", "/experiments/MKT-1/pipeline"),
                             ("GET", "/experiments/MKT-1/evaluations")]:
            assert_refused(api.open(V1 + path, method=method, json={}))

    def test_refusals_are_logged_without_the_token(self, api, eva_token, platform_db, caplog):
        platform_db.execute("UPDATE exp_api_tokens SET revoked_at = now() WHERE id = %s",
                            (eva_token["id"],))
        with caplog.at_level(logging.INFO, logger="web_interface.experiments_routes"):
            assert_refused(api.get(f"{V1}/ping", headers=bearer(eva_token["token"])))
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "refused" in text
        assert eva_token["token"] not in text

    def test_a_failing_lookup_is_500_not_401(self, api, eva_token, monkeypatch):
        from experiments import store

        def boom(*a, **kw):
            raise RuntimeError("connection refused to db:5432")

        monkeypatch.setattr(store, "resolve_api_token", boom)
        r = api.get(f"{V1}/ping", headers=bearer(eva_token["token"]))
        assert r.status_code == 500
        assert r.get_json() == {"success": False, "error": "Interner Serverfehler"}
        assert "5432" not in r.get_data(as_text=True)


class TestTheCookieIsNeverTheCredential:
    def test_a_signed_in_browser_without_a_token_is_refused(self, experiments_app, experimenter):
        browser = _signed_in(experiments_app, experimenter.email, with_csrf=False)
        assert_refused(browser.get(f"{V1}/ping"))
        assert_refused(browser.post(f"{V1}/experiments/MKT-1/notes", json={"body": "x"}))

    def test_the_token_decides_who_acts_not_the_cookie(self, experiments_app, exp_manager,
                                                       eva_token):
        browser = _signed_in(experiments_app, exp_manager.email, with_csrf=False)
        r = browser.get(f"{V1}/ping", headers=bearer(eva_token["token"]))
        assert r.get_json()["user"]["display_name"] == "Eva"
        # Nor is the browser's session touched (Flask would refresh a
        # permanent session's cookie on any other response).
        assert "Set-Cookie" not in r.headers

    def test_the_session_api_does_not_take_a_token(self, api, eva_token):
        r = api.get("/api/experiments", headers=bearer(eva_token["token"]))
        assert r.status_code == 401
        assert r.get_json()["error"] == "Anmeldung erforderlich"

    def test_no_csrf_header_is_needed(self, api, eva_token, experimenter_client, mkt_experiment):
        r = api.post(f"{V1}/experiments/{mkt_experiment}/notes", json={"body": "Aus CI."},
                     headers=bearer(eva_token["token"]))
        assert r.status_code == 201, r.get_json()

    def test_a_body_naming_access_groups_is_refused(self, api, eva_token, mkt_experiment):
        r = api.post(f"{V1}/experiments/{mkt_experiment}/notes",
                     json={"body": "x", "access_groups": ["alle"]},
                     headers=bearer(eva_token["token"]))
        assert r.status_code == 400
        assert "access_groups" in r.get_json()["error"]


@pytest.fixture
def mkt_experiment(exp_manager_client, experimenter_client):
    exp_manager_client.post("/api/experiments/packs/marketing/install", json={})
    r = experimenter_client.post("/api/experiments", json={
        "domain": "marketing", "type": "ab_test", "title": "Anzeige mit Zahl"})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["experiment"]["key"]


# -- what CI does with it ------------------------------------------------------------


class TestCiWorkflow:
    def _create(self, api, token):
        r = api.post(f"{V1}/experiments", headers=bearer(token), json={
            "domain": "engineering", "type": "offline_eval", "title": "Reranker v2",
            "hypothesis": "Der neue Reranker verbessert NDCG@10.",
            "fields": {"component": "Suche"}})
        assert r.status_code == 201, r.get_json()
        return r.get_json()["experiment"]

    def test_create_returns_the_trimmed_experiment(self, api, eva_token, engineering):
        exp = self._create(api, eva_token["token"])
        assert set(exp) == TRIMMED_KEYS
        assert exp["key"] == "ENG-1" and exp["status"] == "draft"
        assert [v["key"] for v in exp["variants"]] == ["baseline", "candidate"]
        assert exp["metrics"] and all("aggregates" not in m for m in exp["metrics"])

    def test_read_returns_the_trimmed_experiment(self, api, eva_token, engineering):
        key = self._create(api, eva_token["token"])["key"]
        r = api.get(f"{V1}/experiments/{key}", headers=bearer(eva_token["token"]))
        assert r.status_code == 200
        assert set(r.get_json()["experiment"]) == TRIMMED_KEYS

    @pytest.mark.parametrize("key", ["ENG-999", "eng-1", "ENG-1x"])
    def test_unknown_or_malformed_keys_are_404_in_json(self, api, eva_token, engineering, key):
        r = api.get(f"{V1}/experiments/{key}", headers=bearer(eva_token["token"]))
        assert r.status_code == 404
        assert r.get_json() == {"success": False, "error": "Nicht gefunden."}

    def test_log_runs_with_per_query_rows_then_evaluate(self, api, eva_token, engineering,
                                                        experimenter_client, platform_db):
        token = eva_token["token"]
        key = self._create(api, token)["key"]
        scores = {"baseline": [0.61, 0.55, 0.70, 0.48, 0.66, 0.52],
                  "candidate": [0.68, 0.59, 0.74, 0.55, 0.71, 0.60]}
        for variant, values in scores.items():
            r = api.post(f"{V1}/experiments/{key}/runs", headers=bearer(token), json={
                "name": f"nightly {variant}", "variant": variant, "commit": "abc1234",
                "params": {"k": 10}, "environment": {"ci": "github"},
                "rows": [{"metric": "ndcg_at_10", "value": v, "dims": {"query": f"q{i}"}}
                         for i, v in enumerate(values)],
                "metrics": {"latency_p95_ms": 180.0}})
            assert r.status_code == 201, r.get_json()
            run = r.get_json()["run"]
            assert run["source"] == "api" and run["variant"] == variant
            assert run["metrics"]["ndcg_at_10"] == pytest.approx(sum(values) / len(values))

        batches = experimenter_client.get(f"/api/experiments/{key}/batches").get_json()
        assert {b["source"] for b in batches["result"]["items"]} == {"api"}

        r = api.post(f"{V1}/experiments/{key}/pipeline", headers=bearer(token), json={})
        assert r.status_code == 200, r.get_json()
        evaluations = r.get_json()["evaluations"]
        paired = [e for e in evaluations if e.get("evaluator_key") == "builtin.paired_t"
                  and e.get("metric_key") == "ndcg_at_10"]
        assert paired and paired[0]["status"] == "done" and paired[0]["trigger"] == "api"
        assert paired[0]["verdict"] == "better"

        listed = api.get(f"{V1}/experiments/{key}/evaluations?metric=ndcg_at_10",
                         headers=bearer(token)).get_json()["evaluations"]
        assert listed and {e["metric_key"] for e in listed} == {"ndcg_at_10"}
        assert all("logs" not in e for e in listed)
        one = api.get(f"{V1}/experiments/{key}/evaluations?limit=1",
                      headers=bearer(token)).get_json()["evaluations"]
        assert len(one) == 1
        none = api.get(f"{V1}/experiments/{key}/evaluations?metric=gibtsnicht",
                       headers=bearer(token)).get_json()["evaluations"]
        assert none == []
        garbage = api.get(f"{V1}/experiments/{key}/evaluations?limit=viele",
                          headers=bearer(token))
        assert garbage.status_code == 200

        # The audit names the token, never its plaintext.
        rows = platform_db.execute(
            "SELECT action, detail::text FROM audit_log WHERE target_id = %s", (key,)).fetchall()
        runs = [d for a, d in rows if a == "experiments.run.add"]
        assert len(runs) == 2 and all(eva_token["id"] in d for d in runs)
        everything = platform_db.execute("SELECT coalesce(string_agg(detail::text, ' '), '') "
                                         "FROM audit_log").fetchone()[0]
        assert token not in everything

    def test_measurements_and_notes(self, api, eva_token, engineering, experimenter_client):
        token = eva_token["token"]
        key = self._create(api, token)["key"]
        r = api.post(f"{V1}/experiments/{key}/measurements", headers=bearer(token), json={
            "rows": [{"metric": "latency_p95_ms", "variant": "baseline", "value": 210.0},
                     {"metric": "latency_p95_ms", "variant": "candidate", "value": 190.0}]})
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["result"]["inserted"] == 2
        note = api.post(f"{V1}/experiments/{key}/notes", headers=bearer(token),
                        json={"body": "Lauf auf Staging.", "kind": "observation"})
        assert note.status_code == 201 and note.get_json()["note"]["kind"] == "observation"

    def test_refusals_carry_their_fields(self, api, eva_token, engineering):
        token = eva_token["token"]
        key = self._create(api, token)["key"]
        r = api.post(f"{V1}/experiments/{key}/runs", headers=bearer(token),
                     json={"status": "irgendwas"})
        assert r.status_code == 400
        assert "status" in r.get_json()["fields"]
        r = api.post(f"{V1}/experiments/{key}/measurements", headers=bearer(token),
                     data='{"rows": [{"metric": "latency_p95_ms", "value": Infinity}]}',
                     content_type="application/json")
        assert r.status_code == 400

    def test_a_manager_only_action_is_not_offered(self, api, eva_token, engineering):
        """The machine API has no delete (the unmatched method falls to the
        session gate), and the session route does not take a token."""
        token = eva_token["token"]
        key = self._create(api, token)["key"]
        r = api.delete(f"{V1}/experiments/{key}", headers=bearer(token))
        assert r.status_code == 404 and r.get_json()["error"] == "Nicht gefunden."
        assert api.delete(f"/api/experiments/{key}", headers=bearer(token)).status_code == 401
        assert api.get(f"{V1}/experiments/{key}", headers=bearer(token)).status_code == 200


class TestUnknownPaths:
    @pytest.mark.parametrize("method, path", [
        ("GET", "/pong"), ("POST", "/ping"), ("GET", "/experiments/MKT-1/runs"),
        ("PUT", "/experiments"), ("GET", "/experiments/MKT-1/evaluations/x/y"),
    ])
    def test_with_a_valid_token_a_json_404(self, api, eva_token, method, path):
        r = api.open(V1 + path, method=method, json={}, headers=bearer(eva_token["token"]))
        assert r.status_code == 404
        assert r.get_json() == {"success": False, "error": "Nicht gefunden."}

    def test_without_a_token_still_the_token_refusal(self, api, eva_token):
        assert_refused(api.get(f"{V1}/pong"))
