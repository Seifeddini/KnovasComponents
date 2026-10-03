"""Tests for the run context manager (Client.run)."""

from __future__ import annotations

import datetime as dt

import pytest

import knovas_experiments as kx
from kx_fake_platform import Reply

API = "/api/experiments/v1"
RUNS = f"{API}/experiments/ENG-12/runs"


def _run_reply(fake):
    fake.ok("POST", RUNS, "run", {"id": "r1", "status": "finished",
                                  "metrics": {"ndcg_at_10": 0.41}}, status=201)


def test_run_posts_once_on_success(fake, client):
    _run_reply(fake)

    with client.run("eng-12", variant="candidate", name="nightly", params={"k1": 1.2},
                    commit="abc123", environment={"runner": "ci"}, note="Nachtlauf") as run:
        run.log(ndcg_at_10=0.41)
        run.log({"recall_at_20": 0.8}, mrr=0.5)
        run.add_rows([{"metric": "ndcg_at_10", "value": 0.5, "dims": {"query": "q1"}}])
        run.add_row("ndcg_at_10", 0.32, dims={"query": "q2"})
        assert fake.requests == []

    assert len(fake.requests) == 1
    body = fake.requests[0].json
    assert body["status"] == "finished"
    assert body["variant"] == "candidate"
    assert body["name"] == "nightly"
    assert body["params"] == {"k1": 1.2}
    assert body["commit"] == "abc123"
    assert body["environment"] == {"runner": "ci"}
    assert body["note"] == "Nachtlauf"
    assert body["metrics"] == {"ndcg_at_10": 0.41, "recall_at_20": 0.8, "mrr": 0.5}
    assert body["rows"] == [
        {"metric": "ndcg_at_10", "value": 0.5, "dims": {"query": "q1"}},
        {"metric": "ndcg_at_10", "value": 0.32, "dims": {"query": "q2"}},
    ]
    started = dt.datetime.fromisoformat(body["started_at"])
    ended = dt.datetime.fromisoformat(body["ended_at"])
    assert started.tzinfo is not None and started <= ended
    assert run.result["id"] == "r1"
    assert run.status == "finished"


def test_add_row_keeps_optional_fields(fake, client):
    _run_reply(fake)
    with client.run("ENG-12") as run:
        run.add_row("error_rate", 3, count=1200, variant="an",
                    observed_at="2026-09-28T00:00:00+00:00")
        run.add_row("cost_per_click", 120.5, denominator=80, sum_sq=None)
    assert fake.requests[0].json["rows"] == [
        {"metric": "error_rate", "value": 3, "count": 1200, "variant": "an",
         "observed_at": "2026-09-28T00:00:00+00:00"},
        {"metric": "cost_per_click", "value": 120.5, "denominator": 80},
    ]


def test_failed_block_reports_a_failed_run_without_measurements(fake, client):
    _run_reply(fake)

    with pytest.raises(RuntimeError, match="benchmark broke"):
        with client.run("ENG-12", variant="candidate", commit="abc") as run:
            run.log(ndcg_at_10=0.41)
            run.add_row("ndcg_at_10", 0.5, dims={"query": "q1"})
            raise RuntimeError("benchmark broke")

    body = fake.requests[0].json
    assert body["status"] == "failed"
    assert body["variant"] == "candidate"
    assert body["commit"] == "abc"
    assert "metrics" not in body and "rows" not in body
    assert run.status == "failed"


def test_interrupted_block_reports_a_cancelled_run(fake, client):
    _run_reply(fake)
    with pytest.raises(KeyboardInterrupt):
        with client.run("ENG-12"):
            raise KeyboardInterrupt
    assert fake.requests[0].json["status"] == "cancelled"


def test_report_failure_does_not_hide_the_block_error(fake, client):
    fake.on("POST", RUNS, Reply(500, {"success": False, "error": "Interner Serverfehler"}))

    with pytest.warns(UserWarning, match="nicht gemeldet"):
        with pytest.raises(ValueError, match="original"):
            with client.run("ENG-12"):
                raise ValueError("original")


def test_report_failure_after_success_raises(fake, client):
    fake.on("POST", RUNS, Reply(400, {"success": False, "error": "Metrik unbekannt.",
                                      "fields": {"metrics.x": "unbekannt"}}))
    with pytest.raises(kx.ValidationError) as info:
        with client.run("ENG-12") as run:
            run.log(x=1)
    assert info.value.fields == {"metrics.x": "unbekannt"}


def test_non_finite_metric_fails_the_report_not_silently(fake, client):
    with pytest.raises(kx.ValidationError, match="metrics.ndcg_at_10"):
        with client.run("ENG-12") as run:
            run.log(ndcg_at_10=float("nan"))
    assert fake.requests == []


def test_run_is_single_use_and_closed_after_the_block(fake, client):
    _run_reply(fake)
    run = client.run("ENG-12")
    with pytest.raises(kx.ExperimentsError):
        run.log(m=1)
    with run:
        pass
    with pytest.raises(kx.ExperimentsError):
        run.log(m=1)
    with pytest.raises(kx.ExperimentsError):
        run.add_rows([])
    with pytest.raises(kx.ExperimentsError):
        with run:
            pass
    assert len(fake.requests) == 1


def test_run_validates_its_arguments_early(fake, client):
    _run_reply(fake)
    with pytest.raises(kx.ValidationError):
        client.run("not a key")
    with pytest.raises(kx.ValidationError):
        client.run("ENG-12", params=["a"])
    with client.run("ENG-12") as run:
        with pytest.raises(kx.ValidationError):
            run.add_rows({"metric": "m", "value": 1})
        with pytest.raises(kx.ValidationError):
            run.log(["m"])
    # The empty run is still reported (status finished, nothing measured).
    assert len(fake.requests) == 1
    assert "rows" not in fake.requests[0].json
