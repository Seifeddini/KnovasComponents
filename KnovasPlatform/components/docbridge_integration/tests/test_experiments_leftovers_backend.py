"""Regressions of the backend leftovers after the review round (service,
store, CSV import, web layer), against the real PostgreSQL. Each test names
the item it guards; the maintenance and purge-index parts of review-jobs-5
are in test_experiments_jobs.py and test_experiments_regressions_jobs.py.
"""

import copy
import json
import logging
import pickle

import pytest

from conftest import platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)

from experiments import csv_import, packs, schema, store, tasks  # noqa: E402
from experiments import service as service_mod  # noqa: E402
from experiments.errors import Conflict, ValidationError  # noqa: E402
from test_experiments_service import (  # noqa: E402
    SETTINGS, World, make_custom_evaluator, measurements,
)


@pytest.fixture
def w(platform_db, identity_repo):
    return World(platform_db, identity_repo)


def audits(w, action):
    return [a["detail"] for a in w.audit(action)]


# -- sample size with several variants ------------------------------------------------


def test_sample_size_route_passes_the_comparisons_through(experimenter_client):
    base = "/api/experiments/sample-size?kind=proportion&base=0.02&mde=0.005"
    one = experimenter_client.get(base).get_json()["result"]
    three = experimenter_client.get(base + "&comparisons=3").get_json()["result"]
    assert one["comparisons"] == 1 and one["alpha_used"] == pytest.approx(0.05)
    assert three["comparisons"] == 3 and three["alpha_used"] == pytest.approx(0.05 / 3)
    assert three["per_variant"] > one["per_variant"]
    refused = experimenter_client.get(base + "&comparisons=0")
    assert refused.status_code == 400 and "comparisons" in refused.get_json()["fields"]


# -- custom evaluator output units ------------------------------------------------------


def comparison_output():
    # A difference of shares without a unit, as a hand-written evaluator sends it.
    return {"ok": True, "error": None, "logs": "", "duration_ms": 3,
            "output": {"verdict": "better", "headline": "Eigen",
                       "comparisons": [{"variant": "B", "estimate": 0.0041, "ci_low": 0.001,
                                        "ci_high": 0.007, "verdict": "better"}]}}


def test_custom_evaluator_difference_of_shares_is_stored_in_percentage_points(w):
    make_custom_evaluator(w)
    svc = w.experimenter
    key = w.create()["key"]
    svc.add_measurements(key, measurements(A=(3, 30), B=(5, 30)))
    queued = svc.run_evaluation(key, {"evaluator": "team.custom_py", "metric": "ctr"})["id"]
    w.runner.result = comparison_output()
    service_mod.execute_evaluation(w.conn, queued, settings=SETTINGS, runner=w.runner)
    done = svc.get_evaluation(key, queued)
    assert done["status"] == "done"
    assert done["output"]["comparisons"][0]["unit"] == "Pp."


def test_evaluator_test_run_gives_the_same_unit(w):
    created = make_custom_evaluator(w)
    key = w.create()["key"]
    w.experimenter.add_measurements(key, measurements(A=(3, 30), B=(5, 30)))
    w.runner.result = comparison_output()
    manager = w.svc(w.max, runner=w.runner)
    result = manager.test_evaluator(created["id"], {"experiment": key, "metric": "ctr"})
    assert result["ok"] is True
    assert result["output"]["comparisons"][0]["unit"] == "Pp."


# -- runs pagination on the detail page -------------------------------------------------


def test_snapshot_runs_cursor_continues_exactly_after_the_last_run_shown(w):
    svc = w.experimenter
    exp = w.create()
    assert svc.get_experiment(exp["key"])["runs_next_after"] is None
    # 103 runs with one and the same created_at: only the id orders them.
    w.conn.execute("INSERT INTO exp_runs (experiment_id, name, created_at) "
                   "SELECT %s, 'bulk', now() - interval '1 hour' FROM generate_series(1, 103)",
                   (exp["id"],))
    newest = svc.add_run(exp["key"], {"name": "neu"})["id"]
    snapshot = svc.get_experiment(exp["key"])
    shown = [r["id"] for r in snapshot["runs"]]
    assert len(shown) == store.SNAPSHOT_RUNS and shown[0] == newest
    cursor = snapshot["runs_next_after"]
    assert cursor
    page = svc.list_runs(exp["key"], after=cursor)
    rest = [r["id"] for r in page["items"]]
    assert page["next_after"] is None
    everything = [r[0] for r in w.conn.execute(
        "SELECT id::text FROM exp_runs WHERE experiment_id = %s ORDER BY created_at DESC, id DESC",
        (exp["id"],)).fetchall()]
    assert shown + rest == everything and len(everything) == 104


def test_runs_cursor_works_through_the_route(experimenter_client, exp_manager_client,
                                             platform_db):
    assert exp_manager_client.post("/api/experiments/packs/marketing/install",
                                   json={}).status_code == 200
    exp = experimenter_client.post("/api/experiments", json={
        "domain": "marketing", "type": "ab_test", "title": "Viele L\u00e4ufe"}).get_json()["experiment"]
    platform_db.execute("INSERT INTO exp_runs (experiment_id, name) "
                        "SELECT %s, 'lauf ' || g FROM generate_series(1, 102) g", (exp["id"],))
    detail = experimenter_client.get(f"/api/experiments/{exp['key']}").get_json()["experiment"]
    cursor = detail["runs_next_after"]
    more = experimenter_client.get(f"/api/experiments/{exp['key']}/runs?after={cursor}")
    assert more.status_code == 200
    items = more.get_json()["result"]["items"]
    assert len(items) == 2
    assert not {r["id"] for r in items} & {r["id"] for r in detail["runs"]}


# -- audit clarity (e2e-ui-7) --------------------------------------------------------------


def test_archiving_and_restoring_say_which_in_the_audit(w):
    svc = w.experimenter
    exp = w.create()
    first = svc.update_experiment(exp["key"], {"archived": True,
                                               "row_version": exp["row_version"]})
    svc.update_experiment(exp["key"], {"archived": False, "row_version": first["row_version"]})
    svc.update_experiment(exp["key"], {"title": "Neu", "row_version": first["row_version"] + 1})
    details = audits(w, "experiments.experiment.update")
    assert details[0]["archived"] is True and details[0]["changed"] == ["archived"]
    assert details[1]["archived"] is False
    assert "archived" not in details[2]


def test_saving_unchanged_variants_or_metrics_changes_nothing(w):
    svc = w.experimenter
    exp = w.create()
    key = exp["key"]
    variants = [{k: v[k] for k in ("key", "name", "description", "is_control", "allocation")}
                for v in exp["variants"]]
    metrics = [{"metric": m["key"], "role": m["role"], "guardrail_op": m["guardrail_op"],
                "guardrail_value": m["guardrail_value"]} for m in exp["metrics"]]
    before = w.row(key)
    same = svc.set_variants(key, {"variants": variants, "row_version": exp["row_version"]})
    same = svc.set_metrics(key, {"metrics": metrics, "row_version": same["row_version"]})
    assert same["row_version"] == exp["row_version"]
    assert w.row(key)[:2] == before[:2]  # row_version and updated_at
    assert audits(w, "experiments.experiment.variants") == []
    assert audits(w, "experiments.experiment.metrics") == []
    # An outdated view is still told so, even when nothing would change.
    changed = svc.set_variants(key, {"variants": variants[::-1], "row_version": exp["row_version"]})
    assert changed["row_version"] == exp["row_version"] + 1
    assert len(audits(w, "experiments.experiment.variants")) == 1
    with pytest.raises(Conflict):
        svc.set_variants(key, {"variants": variants[::-1], "row_version": exp["row_version"]})
    with pytest.raises(Conflict):
        svc.set_metrics(key, {"metrics": metrics, "row_version": exp["row_version"]})
    # A real change still goes through.
    svc.set_metrics(key, {"metrics": metrics[:1], "row_version": changed["row_version"]})
    assert audits(w, "experiments.experiment.metrics")[0]["metrics"] == [metrics[0]["metric"]]


# -- index page orphans ----------------------------------------------------------------------


def test_index_status_counts_deleted_experiments_still_in_knovas(w):
    exp = w.create()
    store.record_index_document(w.conn, "experiments/marketing/" + exp["key"], exp["id"])
    assert w.manager.index_status()["orphans"] == 0
    w.manager.delete_experiment(exp["key"])
    assert w.manager.index_status()["orphans"] == tasks.orphan_count(w.conn) == 1


def test_index_route_carries_the_orphans(exp_manager_client):
    body = exp_manager_client.get("/api/experiments/index").get_json()
    assert body["success"] is True and body["index"]["orphans"] == 0


# -- concurrency safety net (review-backend-6) ---------------------------------------------


@pytest.mark.parametrize("error_name", ["DeadlockDetected", "SerializationFailure",
                                        "TransactionRollback"])
def test_a_deadlock_or_serialization_failure_is_a_409_to_retry(experimenter_client, monkeypatch,
                                                              caplog, error_name):
    import psycopg

    from experiments.service import ExperimentService

    def rolled_back(self, *a, **kw):
        raise getattr(psycopg.errors, error_name)("deadlock detected DETAIL: Process 4711")

    monkeypatch.setattr(ExperimentService, "list_experiments", rolled_back)
    with caplog.at_level(logging.WARNING, logger="web_interface.experiments_routes"):
        r = experimenter_client.get("/api/experiments")
    assert r.status_code == 409
    assert r.get_json() == {"success": False,
                            "error": "Gleichzeitige \u00c4nderung; bitte erneut versuchen."}
    assert "4711" not in r.get_data(as_text=True)
    records = [rec for rec in caplog.records if rec.name == "web_interface.experiments_routes"]
    assert records and all(rec.levelno == logging.WARNING for rec in records)


def test_other_database_errors_stay_a_500(experimenter_client, monkeypatch):
    import psycopg

    from experiments.service import ExperimentService

    def broken(self, *a, **kw):
        raise psycopg.errors.UndefinedTable("relation exp_x does not exist")

    monkeypatch.setattr(ExperimentService, "list_experiments", broken)
    r = experimenter_client.get("/api/experiments")
    assert r.status_code == 500 and r.get_json()["error"] == "Interner Serverfehler"


# -- CSV memory (review-security-7) ------------------------------------------------------------


def test_rows_stream_into_copy_in_chunks(w, monkeypatch):
    svc = w.experimenter
    key = w.create()["key"]
    monkeypatch.setattr(service_mod, "COPY_CHUNK_ROWS", 3)
    sizes = []
    original = store.copy_measurements

    def counting(conn, rows):
        rows = list(rows)
        sizes.append(len(rows))
        return original(conn, rows)

    monkeypatch.setattr(store, "copy_measurements", counting)
    rows = [{"metric": "ctr", "variant": "AB"[i % 2], "value": 1, "count": 10} for i in range(8)]
    rows.insert(4, {"metric": "cost_per_click", "variant": "A", "value": 2, "denominator": 1})
    result = svc.add_measurements(key, {"rows": rows})
    assert sizes == [3, 3, 3] and result["inserted"] == 9
    batch = svc.list_batches(key)["items"][0]
    assert batch["rows"] == 9 and batch["metric_keys"] == ["ctr", "cost_per_click"]
    assert audits(w, "experiments.measurements.add")[-1]["metrics"] == ["ctr", "cost_per_click"]


def test_a_refused_row_after_written_chunks_leaves_nothing_behind(w, monkeypatch):
    svc = w.experimenter
    key = w.create()["key"]
    monkeypatch.setattr(service_mod, "COPY_CHUNK_ROWS", 2)
    good = "ctr,A,1,10\n" * 5
    text = "metric,variant,value,count\n" + good + "ctr,A,20,10\n" + "ctr,Z,1,10\n" + good
    with pytest.raises(ValidationError) as info:
        svc.import_csv(key, text.encode("utf-8"), "teil.csv")
    assert info.value.message == ("Zeile 7: Es kann nicht mehr Erfolge als Versuche geben; "
                                  "Zeile 8: Unbekannte Variante \u00abZ\u00bb.")
    assert info.value.fields == {"file": csv_import.MSG_SEE_ERRORS}
    assert w.conn.execute("SELECT count(*) FROM exp_measurements").fetchone()[0] == 0
    assert w.conn.execute("SELECT count(*) FROM exp_batches").fetchone()[0] == 0
    assert svc.get_experiment(key)["measurement_count"] == 0


def test_csv_rows_share_their_dims_read_only():
    metrics = {"ctr": {"kind": "proportion", "definition": {}, "name": "CTR"},
               "cpc": {"kind": "mean", "definition": {}, "name": "CPC"}}
    parsed = csv_import.parse_csv(
        b"variant,ctr,ctr.count,cpc,dim.channel\nA,1,10,2,LinkedIn\nB,1,10,3,LinkedIn\nA,1,10,,\n",
        metrics=metrics, variants={"A", "B"}, max_rows=100)
    rows = parsed["rows"]
    assert [r["dims"] for r in rows] == [{"channel": "LinkedIn"}] * 4 + [{}]
    assert len({id(r["dims"]) for r in rows[:4]}) == 1  # one object for the equal dims
    with pytest.raises(TypeError):
        rows[0]["dims"]["channel"] = "X"
    with pytest.raises(TypeError):
        rows[4]["dims"].update(channel="X")
    assert rows[1]["dims"] == {"channel": "LinkedIn"} and rows[4]["dims"] == {}
    assert json.loads(json.dumps(rows[0]["dims"])) == {"channel": "LinkedIn"}
    private = dict(rows[0]["dims"])
    private["channel"] = "X"
    assert copy.deepcopy(rows[0]["dims"]) == pickle.loads(pickle.dumps(rows[0]["dims"])) \
        == {"channel": "LinkedIn"}
    assert schema.normalize_dims(rows[0]["dims"]) == {"channel": "LinkedIn"}


# -- run names in the CSV run column (e2e-ui-12) -----------------------------------------------


def test_csv_run_column_takes_a_unique_run_name(w):
    svc = w.experimenter
    key = w.create()["key"]
    nightly = svc.add_run(key, {"name": "nightly-17"})["id"]
    for _ in range(2):
        svc.add_run(key, {"name": "doppelt"})
    ok = svc.import_csv(key, ("metric,variant,value,count,run\n"
                              f"ctr,A,1,10,nightly-17\nctr,B,2,10,{nightly}\n").encode(), "a.csv")
    assert ok["inserted"] == 2
    assert svc.list_runs(key)["items"][-1]["metrics"] == {"ctr": pytest.approx(3 / 20)}
    with pytest.raises(ValidationError) as info:
        svc.import_csv(key, (b"metric,variant,value,count,run\nctr,A,1,10,doppelt\n"
                             b"ctr,A,1,10,gibtsnicht\nctr,A,1,10,doppelt\n"), "b.csv")
    assert info.value.message == (
        "Zeile 2: 2 L\u00e4ufe heissen \u00abdoppelt\u00bb; bitte die Lauf-ID angeben; "
        "Zeile 3: \u00abgibtsnicht\u00bb ist weder eine Lauf-ID noch der Name eines Laufs dieses "
        "Experiments; "
        "Zeile 4: 2 L\u00e4ufe heissen \u00abdoppelt\u00bb; bitte die Lauf-ID angeben.")


def test_run_names_are_only_read_when_a_cell_is_not_an_id():
    calls = []

    def names():
        calls.append(1)
        return {}

    metrics = {"ctr": {"kind": "proportion", "definition": {}, "name": "CTR"}}
    run = "11111111-1111-1111-1111-111111111111"
    parsed = csv_import.parse_csv(f"metric,value,count,run\nctr,1,10,{run}\n".encode(),
                                  metrics=metrics, variants=set(), max_rows=10, runs=names)
    assert parsed["rows"][0]["run_id"] == run and calls == []


# -- type editor readability ----------------------------------------------------------------


def jsonb_order(value):
    """Keys the way PostgreSQL's JSONB returns them (shorter first)."""
    if isinstance(value, dict):
        return {k: jsonb_order(value[k])
                for k in sorted(value, key=lambda k: (len(k.encode()), k.encode()))}
    if isinstance(value, list):
        return [jsonb_order(v) for v in value]
    return value


def pack_types():
    for item in packs.available_packs():
        for type_ in packs.load_pack(item["name"]).get("types") or []:
            yield item["name"], type_


@pytest.mark.parametrize("pack, type_", list(pack_types()),
                         ids=[f"{p}.{t['key']}" for p, t in pack_types()])
def test_definition_yaml_reads_back_as_the_stored_definition(pack, type_):
    stored = jsonb_order(type_["definition"])
    text = service_mod.definition_yaml(stored)
    assert schema.parse_definition_text(text) == stored
    top = [line.split(":")[0] for line in text.splitlines() if line and not line[0].isspace()]
    order = [k for k in service_mod.DEFINITION_YAML_ORDER if k in stored]
    assert top == order
    if stored.get("transitions"):
        assert "  - from: " in text  # entries in their natural order, not JSONB's
    assert "&id" not in text and "*id" not in text  # no anchors or aliases


def test_type_output_carries_the_definition_as_yaml(w):
    ab = next(t for t in w.manager.list_types("marketing") if t["key"] == "ab_test")
    out = w.manager.get_type(ab["id"])
    assert schema.parse_definition_text(out["definition_yaml"]) == out["definition"]
    assert out["definition_yaml"].startswith("fields:\n  - key: channel\n")
    assert "L\u00e4uft" in out["definition_yaml"]  # real umlauts, not \\u escapes
    changed = dict(out["definition"], initial="running")
    after = w.manager.add_type_version(ab["id"], {"definition_text": out["definition_yaml"]
                                                  .replace("initial: draft", "initial: running")})
    assert after["definition"] == schema.validate_type_definition(changed)
    assert "initial: running" in after["definition_yaml"]
