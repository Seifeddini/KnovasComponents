"""Tests for knovas_experiments.Client against a fake Platform over HTTP."""

from __future__ import annotations

import datetime as dt
import decimal
import socket
import warnings

import pytest

import knovas_experiments as kx
from kx_fake_platform import TOKEN, Reply

API = "/api/experiments/v1"


def _only(fake):
    assert len(fake.requests) == 1, fake.requests
    return fake.requests[0]


# -- configuration ---------------------------------------------------------------


def test_ping_sends_bearer_token_and_returns_user(fake, client):
    fake.ok("GET", f"{API}/ping", "user", {"display_name": "Eva", "roles": ["experimenter"]})

    assert client.ping() == {"display_name": "Eva", "roles": ["experimenter"]}

    request = _only(fake)
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"].startswith("knovas-experiments-python/")
    assert "Cookie" not in request.headers


def test_environment_fallbacks(fake, monkeypatch):
    monkeypatch.setenv("KNOVAS_URL", fake.url + "/")
    monkeypatch.setenv("KNOVAS_EXPERIMENTS_TOKEN", f"  {TOKEN}\n")
    fake.ok("GET", f"{API}/ping", "user", {"display_name": "Eva", "roles": []})

    assert kx.Client().ping()["display_name"] == "Eva"
    assert _only(fake).headers["Authorization"] == f"Bearer {TOKEN}"


def test_missing_address_and_token_are_configuration_errors(monkeypatch):
    with pytest.raises(kx.ConfigurationError, match="KNOVAS_URL"):
        kx.Client(token=TOKEN)
    with pytest.raises(kx.ConfigurationError, match="KNOVAS_EXPERIMENTS_TOKEN"):
        kx.Client("https://knovas.example.ch")


@pytest.mark.parametrize("bad", [
    "eyJhbGciOiJIUzI1NiJ9.e30.secret-jwt-value",
    "kxp_short",
    "kxp_" + "a" * 30 + "\r\nX-Injected: 1",
    "Bearer " + TOKEN,
])
def test_foreign_or_malformed_tokens_are_refused_without_echo(bad):
    with pytest.raises(kx.ConfigurationError) as info:
        kx.Client("https://knovas.example.ch", bad)
    assert bad.strip() not in str(info.value)
    assert "secret-jwt-value" not in str(info.value)


@pytest.mark.parametrize("raw, expected", [
    ("https://knovas.example.ch", "https://knovas.example.ch"),
    ("https://knovas.example.ch/", "https://knovas.example.ch"),
    ("https://knovas.example.ch/api/experiments/v1/", "https://knovas.example.ch"),
    ("https://example.ch/knovas/", "https://example.ch/knovas"),
    ("  http://127.0.0.1:8081  ", "http://127.0.0.1:8081"),
])
def test_base_url_is_normalised(raw, expected):
    assert kx.Client(raw, TOKEN).base_url == expected


@pytest.mark.parametrize("raw", [
    "knovas.example.ch",
    "ftp://knovas.example.ch",
    "https://user:pw@knovas.example.ch",
    "https://knovas.example.ch/?x=1",
    "https://knovas.example.ch/#frag",
    "https://",
    "https://knovas.example.ch/x\ny",
    "https://knovas example.ch",
    "https://knovas.example.ch:abc",
    "https://knovas.example.ch:99999",
])
def test_bad_base_urls_are_refused(raw):
    with pytest.raises(kx.ConfigurationError):
        kx.Client(raw, TOKEN)


def test_repr_hides_the_token():
    client = kx.Client("https://knovas.example.ch", TOKEN)
    assert TOKEN not in repr(client)
    assert TOKEN[4:] not in repr(client)


def test_plain_http_to_another_host_warns_but_loopback_does_not():
    with pytest.warns(UserWarning, match="https"):
        kx.Client("http://knovas.example.ch", TOKEN)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        kx.Client("http://localhost:8081", TOKEN)
        kx.Client("http://127.0.0.1:8081", TOKEN)
        kx.Client("http://[::1]:8081", TOKEN)


@pytest.mark.parametrize("timeout", [0, -1, "abc", float("nan"), float("inf")])
def test_timeout_must_be_a_positive_number(timeout):
    with pytest.raises(kx.ConfigurationError):
        kx.Client("https://knovas.example.ch", TOKEN, timeout=timeout)


# -- calls and bodies -----------------------------------------------------------------


def test_create_experiment_body(fake, client):
    fake.ok("POST", f"{API}/experiments", "experiment", {"key": "ENG-13"}, status=201)

    result = client.create_experiment("engineering", "offline_eval", "Stemmer v2",
                                      hypothesis="Besser", fields={"component": "Suche"})

    assert result == {"key": "ENG-13"}
    request = _only(fake)
    assert request.headers["Content-Type"].startswith("application/json")
    assert request.json == {"domain": "engineering", "type": "offline_eval",
                            "title": "Stemmer v2", "hypothesis": "Besser",
                            "fields": {"component": "Suche"}}


def test_experiment_normalises_the_key(fake, client):
    fake.ok("GET", f"{API}/experiments/ENG-12", "experiment", {"key": "ENG-12"})

    assert client.experiment(" eng-12 ")["key"] == "ENG-12"
    assert _only(fake).path == f"{API}/experiments/ENG-12"


@pytest.mark.parametrize("key", ["../tokens", "ENG-12/../../ping", "ENG", "ENG-", "E-1",
                                 "ENG-12?x=1", "ENG-12%2F", "", None, "ENGINEERING-1"])
def test_keys_that_are_no_experiment_keys_never_reach_the_server(fake, client, key):
    with pytest.raises(kx.ValidationError):
        client.experiment(key)
    with pytest.raises(kx.ValidationError):
        client.add_note(key, "x")
    assert fake.requests == []


def test_log_run_sends_only_given_fields(fake, client):
    fake.ok("POST", f"{API}/experiments/ENG-12/runs", "run", {"id": "r1"}, status=201)

    run = client.log_run("ENG-12", variant="candidate", metrics={"latency_p95_ms": 212})

    assert run == {"id": "r1"}
    assert _only(fake).json == {"status": "finished", "variant": "candidate",
                                "metrics": {"latency_p95_ms": 212}}


def test_log_run_full_body_and_value_conversion(fake, client):
    np = pytest.importorskip("numpy")
    fake.ok("POST", f"{API}/experiments/ENG-12/runs", "run", {"id": "r1"}, status=201)
    started = dt.datetime(2026, 9, 28, 8, 0, tzinfo=dt.timezone.utc)
    naive = dt.datetime(2026, 9, 28, 9, 30)

    client.log_run(
        "ENG-12", name="nightly", variant="candidate", params={"k1": 1.2},
        metrics={"ndcg_at_10": np.float64(0.41), "error_rate": {"value": np.int64(3),
                                                                  "count": 1200}},
        rows=({"metric": "ndcg_at_10", "value": v, "dims": {"query": f"q{i}"}}
              for i, v in enumerate([0.5, decimal.Decimal("0.25")])),
        environment={"python": "3.11"}, commit="abc123", status="finished",
        started_at=started, ended_at=naive, note="Lauf aus CI",
    )

    body = _only(fake).json
    assert body["name"] == "nightly"
    assert body["params"] == {"k1": 1.2}
    assert body["metrics"] == {"ndcg_at_10": 0.41, "error_rate": {"value": 3, "count": 1200}}
    assert body["rows"] == [
        {"metric": "ndcg_at_10", "value": 0.5, "dims": {"query": "q0"}},
        {"metric": "ndcg_at_10", "value": 0.25, "dims": {"query": "q1"}},
    ]
    assert body["environment"] == {"python": "3.11"}
    assert body["commit"] == "abc123"
    assert body["started_at"] == "2026-09-28T08:00:00+00:00"
    # A naive datetime is local time and goes out with its offset.
    parsed = dt.datetime.fromisoformat(body["ended_at"])
    assert parsed.tzinfo is not None
    assert parsed == naive.astimezone()
    assert body["note"] == "Lauf aus CI"


@pytest.mark.parametrize("metrics, rows, where", [
    ({"ndcg_at_10": float("nan")}, None, "metrics.ndcg_at_10"),
    ({"x": {"value": float("inf"), "count": 1}}, None, "metrics.x.value"),
    (None, [{"metric": "m", "value": 1.0}, {"metric": "m", "value": float("-inf")}],
     "rows.1.value"),
    ({"m": decimal.Decimal("NaN")}, None, "metrics.m"),
])
def test_non_finite_values_are_refused_before_sending(fake, client, metrics, rows, where):
    with pytest.raises(kx.ValidationError) as info:
        client.log_run("ENG-12", metrics=metrics, rows=rows)
    assert where in str(info.value)
    assert where in info.value.fields
    assert fake.requests == []


def test_numpy_nan_is_refused_too(fake, client):
    np = pytest.importorskip("numpy")
    with pytest.raises(kx.ValidationError, match="rows.0.value"):
        client.add_measurements("ENG-12", [{"metric": "m", "value": np.float32("nan")}])
    assert fake.requests == []


def test_objects_without_json_form_are_refused_before_sending(fake, client):
    with pytest.raises(kx.ValidationError, match="JSON"):
        client.log_run("ENG-12", params={"thing": object()})
    assert fake.requests == []


@pytest.mark.parametrize("status", ["running", "done", "", None])
def test_log_run_status_must_be_final(fake, client, status):
    with pytest.raises(kx.ValidationError):
        client.log_run("ENG-12", status=status)
    assert fake.requests == []


def test_log_run_refuses_non_mapping_arguments(fake, client):
    with pytest.raises(kx.ValidationError):
        client.log_run("ENG-12", params=[1, 2])
    with pytest.raises(kx.ValidationError):
        client.log_run("ENG-12", rows={"metric": "m", "value": 1})
    with pytest.raises(kx.ValidationError):
        client.log_run("ENG-12", rows=[{"metric": "m", "value": 1}, 5])
    assert fake.requests == []


def test_add_measurements(fake, client):
    fake.ok("POST", f"{API}/experiments/MKT-3/measurements", "result",
            {"batch_id": "b1", "inserted": 2}, status=201)
    rows = [{"metric": "ctr", "variant": "A", "value": 129, "count": 10688,
             "observed_at": dt.date(2026, 9, 21)},
            {"metric": "ctr", "variant": "B", "value": 175, "count": 10714,
             "observed_at": "2026-09-21"}]

    assert client.add_measurements("MKT-3", rows) == {"batch_id": "b1", "inserted": 2}
    assert _only(fake).json == {"rows": [
        {"metric": "ctr", "variant": "A", "value": 129, "count": 10688,
         "observed_at": "2026-09-21"},
        {"metric": "ctr", "variant": "B", "value": 175, "count": 10714,
         "observed_at": "2026-09-21"},
    ]}


def test_add_measurements_refuses_empty_or_malformed_rows(fake, client):
    with pytest.raises(kx.ValidationError, match="leer"):
        client.add_measurements("MKT-3", [])
    with pytest.raises(kx.ValidationError):
        client.add_measurements("MKT-3", "metric,value")
    with pytest.raises(kx.ValidationError):
        client.add_measurements("MKT-3", 42)
    assert fake.requests == []


def test_add_note_sends_unicode(fake, client):
    fake.ok("POST", f"{API}/experiments/PRD-2/notes", "note", {"id": "n1"}, status=201)

    client.add_note("PRD-2", "Teilnehmerin P3 fand den Filter \u00abzu versteckt\u00bb.",
                    kind="interview")

    request = _only(fake)
    assert request.json == {"body": "Teilnehmerin P3 fand den Filter \u00abzu versteckt\u00bb.",
                            "kind": "interview"}
    assert "\u00ab".encode("utf-8") in request.raw


def test_add_note_refuses_an_empty_body(fake, client):
    with pytest.raises(kx.ValidationError):
        client.add_note("PRD-2", "   ")
    assert fake.requests == []


def test_evaluate_runs_the_pipeline(fake, client):
    evaluations = [{"id": "e1", "status": "done", "verdict": "better"}]
    fake.ok("POST", f"{API}/experiments/ENG-12/pipeline", "evaluations", evaluations)

    assert client.evaluate("ENG-12") == evaluations
    assert client.evaluate("ENG-12", scope={"runs": "latest"}) == evaluations

    first, second = fake.requests
    assert first.json == {}
    assert second.json == {"scope": {"runs": "latest"}}


def test_evaluations_query(fake, client):
    fake.ok("GET", f"{API}/experiments/ENG-12/evaluations", "evaluations", [{"id": "e1"}])

    assert client.evaluations("ENG-12") == [{"id": "e1"}]
    client.evaluations("ENG-12", metric="ndcg_at_10", limit=5000)

    first, second = fake.requests
    assert first.query == {"limit": ["20"]}
    assert second.query == {"metric": ["ndcg_at_10"], "limit": ["60"]}


def test_wait_for_polls_until_final(fake, client):
    queued = {"id": "e2", "status": "queued", "verdict": None}
    fake.on("GET", f"{API}/experiments/ENG-12/evaluations",
            Reply(200, {"success": True, "evaluations": [{"id": "e2", "status": "running"}]}),
            Reply(200, {"success": True, "evaluations": [
                {"id": "e2", "status": "done", "verdict": "worse"},
                {"id": "e1", "status": "done", "verdict": "better"}]}))
    skipped = {"status": "skipped", "warning": "Keine Rechenumgebung."}

    result = client.wait_for("ENG-12", [{"id": "e1", "status": "done", "verdict": "better"},
                                        queued, skipped], interval=0.01, timeout=10)

    assert [e.get("verdict") for e in result] == ["better", "worse", None]
    assert result[2] == skipped
    assert len(fake.requests) == 2


def test_wait_for_times_out_with_the_last_state(fake, client):
    fake.ok("GET", f"{API}/experiments/ENG-12/evaluations", "evaluations",
            [{"id": "e2", "status": "running"}])

    with pytest.raises(kx.WaitTimeout) as info:
        client.wait_for("ENG-12", [{"id": "e2", "status": "queued"}], interval=0.01,
                        timeout=0.05)
    assert info.value.evaluations == [{"id": "e2", "status": "running"}]


def test_wait_for_returns_at_once_when_nothing_is_pending(fake, client):
    done = [{"id": "e1", "status": "done"}, {"id": "e3", "status": "failed"}]
    assert client.wait_for("ENG-12", done) == done
    assert fake.requests == []


# -- errors ----------------------------------------------------------------------------


def test_invalid_token_is_an_authentication_error_without_the_token(fake, client):
    message = "Ung\u00fcltiger oder abgelaufener Zugangsschl\u00fcssel."
    fake.on("GET", f"{API}/ping", Reply(401, {"success": False, "error": message}))

    with pytest.raises(kx.AuthenticationError) as info:
        client.ping()

    assert info.value.status == 401
    assert info.value.message == message
    assert TOKEN not in str(info.value) and TOKEN not in repr(info.value)


def test_switched_off_module_gives_a_clear_error(fake, client):
    fake.on("POST", f"{API}/experiments/ENG-12/runs",
            Reply(404, {"success": False, "error": kx.MSG_SWITCHED_OFF}))

    with pytest.raises(kx.ModuleDisabledError) as info:
        client.log_run("ENG-12", metrics={"m": 1})

    assert isinstance(info.value, kx.NotFoundError)
    assert "EXPERIMENTS_ENABLED" in str(info.value)
    assert "nicht eingeschaltet" in str(info.value)


def test_other_not_found_is_not_the_switch(fake, client):
    fake.on("GET", f"{API}/experiments/ENG-99",
            Reply(404, {"success": False, "error": "Nicht gefunden."}))
    with pytest.raises(kx.NotFoundError) as info:
        client.experiment("ENG-99")
    assert not isinstance(info.value, kx.ModuleDisabledError)
    assert info.value.message == "Nicht gefunden."


def test_html_not_found_hints_at_the_address(fake, client):
    fake.on("GET", f"{API}/ping", Reply(404, raw=b"<html>Not Found</html>"))
    with pytest.raises(kx.NotFoundError, match="Adresse"):
        client.ping()


def test_validation_error_carries_the_fields(fake, client):
    fake.on("POST", f"{API}/experiments/ENG-12/measurements",
            Reply(400, {"success": False, "error": "Eingaben pr\u00fcfen.",
                        "fields": {"rows.0.value": "Erfolge m\u00fcssen eine ganze Zahl sein."}}))

    with pytest.raises(kx.ValidationError) as info:
        client.add_measurements("ENG-12", [{"metric": "ctr", "value": 1.5, "count": 10}])

    assert info.value.fields == {"rows.0.value": "Erfolge m\u00fcssen eine ganze Zahl sein."}
    assert "rows.0.value" in str(info.value)
    assert "HTTP 400" in str(info.value)


@pytest.mark.parametrize("status, cls", [
    (403, kx.ForbiddenError),
    (409, kx.ConflictError),
    (503, kx.UnavailableError),
    (500, kx.ServerError),
    (502, kx.ServerError),
])
def test_status_classes(fake, client, status, cls):
    fake.on("POST", f"{API}/experiments/ENG-12/notes",
            Reply(status, {"success": False, "error": "Fehler X."}))
    with pytest.raises(cls) as info:
        client.add_note("ENG-12", "x")
    assert info.value.status == status
    assert info.value.message == "Fehler X."
    # A POST is never repeated after an answer: the Platform may have stored it.
    assert len(fake.requests) == 1


def test_unknown_client_status_is_not_called_a_server_error(fake, client):
    fake.on("GET", f"{API}/ping", Reply(405, {"success": False, "error": "Methode."}))
    with pytest.raises(kx.ExperimentsError) as info:
        client.ping()
    assert not isinstance(info.value, kx.ServerError)
    assert info.value.status == 405


def test_request_too_large_without_json(fake, client):
    fake.on("POST", f"{API}/experiments/ENG-12/measurements",
            Reply(413, raw=b"<h1>Request Entity Too Large</h1>"))
    with pytest.raises(kx.ValidationError, match="mehrere Aufrufe"):
        client.add_measurements("ENG-12", [{"metric": "m", "value": 1}])


def test_success_answer_that_is_not_json(fake, client):
    fake.on("GET", f"{API}/ping", Reply(200, raw=b"<html>Anmelden</html>"))
    with pytest.raises(kx.ProtocolError, match="kein JSON"):
        client.ping()


def test_success_answer_without_the_expected_key(fake, client):
    fake.on("GET", f"{API}/ping", Reply(200, {"success": True, "other": 1}))
    with pytest.raises(kx.ProtocolError):
        client.ping()


def test_success_status_with_success_false(fake, client):
    fake.on("GET", f"{API}/ping", Reply(200, {"success": False, "error": "Seltsam."}))
    with pytest.raises(kx.ProtocolError, match="Seltsam"):
        client.ping()


def test_redirects_are_not_followed_and_the_token_stays(fake, second_fake, client):
    second_fake.ok("GET", f"{API}/ping", "user", {"display_name": "Mallory", "roles": []})
    fake.on("GET", f"{API}/ping",
            Reply(302, raw=b"", headers={"Location": f"{second_fake.url}{API}/ping"}))

    with pytest.raises(kx.ProtocolError) as info:
        client.ping()

    assert second_fake.url in str(info.value)
    assert second_fake.requests == []
    assert len(fake.requests) == 1


def test_redirect_of_a_post_is_not_followed(fake, second_fake, client):
    fake.on("POST", f"{API}/experiments/ENG-12/runs",
            Reply(307, raw=b"", headers={"Location": f"{second_fake.url}/steal"}))
    with pytest.raises(kx.ProtocolError):
        client.log_run("ENG-12", metrics={"m": 1})
    assert second_fake.requests == []


def test_get_is_retried_after_a_gateway_error(fake, client):
    fake.on("GET", f"{API}/ping",
            Reply(502, raw=b"<html>Bad Gateway</html>"),
            Reply(200, {"success": True, "user": {"display_name": "Eva", "roles": []}}))
    assert client.ping()["display_name"] == "Eva"
    assert len(fake.requests) == 2


def test_get_gives_up_after_the_retries(fake):
    client = kx.Client(fake.url, TOKEN, timeout=5, retries=1)
    fake.on("GET", f"{API}/ping", Reply(503, {"success": False, "error": "Wartung."}))
    with pytest.raises(kx.UnavailableError):
        client.ping()
    assert len(fake.requests) == 2


def test_post_is_not_retried_after_a_gateway_error(fake, client):
    fake.on("POST", f"{API}/experiments/ENG-12/runs",
            Reply(502, raw=b"<html>Bad Gateway</html>"),
            Reply(201, {"success": True, "run": {"id": "r1"}}))
    with pytest.raises(kx.ServerError):
        client.log_run("ENG-12", metrics={"m": 1})
    assert len(fake.requests) == 1


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_connection_refused_is_a_transport_error():
    client = kx.Client(f"http://127.0.0.1:{_closed_port()}", TOKEN, timeout=2)
    with pytest.raises(kx.TransportError) as info:
        client.log_run("ENG-12", metrics={"m": 1})
    assert "127.0.0.1" in str(info.value)
    assert TOKEN not in str(info.value)


def test_refused_post_is_tried_again(monkeypatch, fake):
    client = kx.Client(fake.url, TOKEN, timeout=5)
    fake.ok("POST", f"{API}/experiments/ENG-12/notes", "note", {"id": "n1"}, status=201)
    real_send = client._send
    calls = []

    def flaky(method, url, data):
        calls.append(method)
        if len(calls) == 1:
            raise kx.urllib.error.URLError(ConnectionRefusedError(111, "refused"))
        return real_send(method, url, data)

    monkeypatch.setattr(client, "_send", flaky)
    assert client.add_note("ENG-12", "x") == {"id": "n1"}
    assert calls == ["POST", "POST"]
    assert len(fake.requests) == 1


def test_timeout_of_a_post_is_not_retried(fake):
    client = kx.Client(fake.url, TOKEN, timeout=0.3)
    fake.on("POST", f"{API}/experiments/ENG-12/runs",
            Reply(201, {"success": True, "run": {}}, delay=3))
    with pytest.raises(kx.TransportError, match="Zeit\u00fcberschreitung"):
        client.log_run("ENG-12", metrics={"m": 1})
    assert len(fake.requests) == 1


def test_oversized_answers_are_refused(fake, client, monkeypatch):
    monkeypatch.setattr(kx, "MAX_RESPONSE_BYTES", 100)
    fake.ok("GET", f"{API}/ping", "user", {"display_name": "x" * 500, "roles": []})
    with pytest.raises(kx.ProtocolError):
        client.ping()


# -- TLS -----------------------------------------------------------------------------------


def test_tls_is_verified_by_default(tls_fake):
    tls_fake.ok("GET", f"{API}/ping", "user", {"display_name": "Eva", "roles": []})
    client = kx.Client(tls_fake.url, TOKEN, timeout=5, retries=0)
    with pytest.raises(kx.TransportError, match="cafile"):
        client.ping()
    assert tls_fake.requests == []


def test_tls_with_a_private_ca(tls_fake, tls_material):
    tls_fake.ok("GET", f"{API}/ping", "user", {"display_name": "Eva", "roles": []})
    client = kx.Client(tls_fake.url, TOKEN, cafile=tls_material[0], timeout=5)
    assert client.ping()["display_name"] == "Eva"
    assert _only(tls_fake).headers["Authorization"] == f"Bearer {TOKEN}"


def test_tls_verification_can_be_switched_off_with_a_warning(tls_fake):
    tls_fake.ok("GET", f"{API}/ping", "user", {"display_name": "Eva", "roles": []})
    with pytest.warns(UserWarning, match="verify=False"):
        client = kx.Client(tls_fake.url, TOKEN, verify=False, timeout=5)
    assert client.ping()["display_name"] == "Eva"


def test_cafile_and_verify_false_contradict(tls_material):
    with pytest.raises(kx.ConfigurationError):
        kx.Client("https://knovas.example.ch", TOKEN, cafile=tls_material[0], verify=False)


def test_unreadable_cafile(tmp_path):
    with pytest.raises(kx.ConfigurationError, match="cafile"):
        kx.Client("https://knovas.example.ch", TOKEN, cafile=str(tmp_path / "missing.pem"))


# -- command line ---------------------------------------------------------------------------


def test_cli_ping(fake, monkeypatch, capsys):
    monkeypatch.setenv("KNOVAS_URL", fake.url)
    monkeypatch.setenv("KNOVAS_EXPERIMENTS_TOKEN", TOKEN)
    fake.ok("GET", f"{API}/ping", "user", {"display_name": "Eva",
                                           "roles": ["experimenter"]})

    assert kx.main(["ping"]) == 0
    assert "Eva" in capsys.readouterr().out


def test_cli_ping_failure_and_usage(fake, monkeypatch, capsys):
    monkeypatch.setenv("KNOVAS_URL", fake.url)
    monkeypatch.setenv("KNOVAS_EXPERIMENTS_TOKEN", TOKEN)
    fake.on("GET", f"{API}/ping", Reply(404, {"success": False,
                                              "error": kx.MSG_SWITCHED_OFF}))

    assert kx.main(["ping"]) == 1
    err = capsys.readouterr().err
    assert "nicht eingeschaltet" in err and TOKEN not in err
    assert kx.main([]) == 2
