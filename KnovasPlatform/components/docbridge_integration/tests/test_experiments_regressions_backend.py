"""Regression tests for the backend review fixes (experiments store and
service) against the real PostgreSQL. Each test names the finding it guards.

The world (people, the marketing pack, a fake runner) is the one the service
tests use.
"""

import dataclasses
import json
import threading
import time

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)

from experiments import packs, stats, store  # noqa: E402
from experiments import service as service_mod  # noqa: E402
from experiments.errors import ValidationError  # noqa: E402
from experiments.jobs import RetryLater  # noqa: E402
from experiments.service import ExperimentService  # noqa: E402
from test_experiments_service import (  # noqa: E402
    SETTINGS, World, make_custom_evaluator, measurements,
)


@pytest.fixture
def w(platform_db, identity_repo):
    return World(platform_db, identity_repo)


@pytest.fixture
def open_conn(platform_db):
    """Further connections to the test's schema (for concurrent work)."""
    import psycopg

    schema_name = platform_db.execute("SELECT current_schema()").fetchone()[0]
    opened = []

    def _open():
        conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True,
                               options=f"-c search_path={schema_name}")
        opened.append(conn)
        return conn

    yield _open
    for conn in opened:
        if not conn.closed:
            conn.close()


def _group(e):
    return (e["evaluator_key"], e["metric_key"], json.dumps(e["params"], sort_keys=True),
            json.dumps(e["scope"], sort_keys=True))


# -- e2e-ui-3 / review-backend-1 ---------------------------------------------------------------


def test_undoing_an_import_gives_a_fresh_current_evaluation(w):
    svc = w.experimenter
    exp = w.create()
    key = exp["key"]

    def pipeline_job():
        service_mod.run_pipeline_job(w.conn, exp["id"], settings=SETTINGS, runner=None)

    def bayes():
        return [e for e in svc.get_experiment(key)["evaluations"]
                if e["evaluator_key"] == "builtin.bayes_proportion"]

    svc.add_measurements(key, measurements(A=(100, 10000), B=(200, 10000)))
    pipeline_job()
    assert bayes()[0]["verdict"] == "better"
    wrong = svc.add_measurements(key, measurements(A=(2000, 10000), B=(100, 10000)))
    pipeline_job()
    assert bayes()[0]["verdict"] == "worse"
    svc.delete_batch(key, wrong["batch_id"])  # "Rueckgaengig": the data is as after the first import
    pipeline_job()

    current = bayes()
    assert len(current) == 3  # a fresh evaluation, not the first one reused
    assert current[0]["verdict"] == "better"
    assert [e["superseded"] for e in current] == [False, True, True]
    groups = {}
    for e in svc.get_experiment(key)["evaluations"]:
        groups.setdefault(_group(e), []).append(e["superseded"])
    assert all(flags[0] is False and all(flags[1:]) for flags in groups.values())
    item = next(i for i in svc.list_experiments()["items"] if i["key"] == key)
    assert item["latest"]["verdict"] == "better"
    assert svc.get_evaluation(key, current[1]["id"])["superseded"] is True

    # The CI path returns the current evaluations; unchanged input is reused.
    result = svc.run_pipeline(key, None, trigger="api")
    returned = [r for r in result if r.get("id")]
    assert current[0]["id"] in {r["id"] for r in returned}
    assert not any(r["superseded"] for r in returned)
    assert len(bayes()) == 3


# -- review-backend-2 ------------------------------------------------------------------------


def test_an_archived_default_metric_does_not_block_new_experiments(w):
    manager = w.manager
    domain = store.get_domain(w.conn, "marketing")
    bounce = store.resolve_metrics(w.conn, domain["id"], ["bounce_rate"])["bounce_rate"]
    manager.update_metric(bounce["id"], {"archived": True})

    exp = w.create()  # the create form's body: no metrics
    assert [m["key"] for m in exp["metrics"]] == ["ctr", "conversion_rate", "cost_per_click"]
    detail = w.audit("experiments.experiment.create")[-1]["detail"]
    assert detail["skipped_archived_metrics"] == ["bounce_rate"]
    with pytest.raises(ValidationError) as info:  # named explicitly: still refused
        w.create(metrics=[{"metric": "bounce_rate", "role": "secondary"}])
    assert set(info.value.fields) == {"metrics.0.metric"}
    # A type definition naming the archived metric cannot be saved any more.
    definition = store.find_type(w.conn, domain["id"], "ab_test")["definition"]
    with pytest.raises(ValidationError) as info:
        manager.validate_type({"domain": "marketing", "definition": definition})
    assert info.value.fields["metrics[3].metric"] == "Die Metrik \u00abbounce_rate\u00bb ist archiviert."


# -- e2e-api-5 --------------------------------------------------------------------------------


def test_the_owner_must_be_able_to_see_the_module(w):
    svc = w.experimenter
    key = w.create()["key"]

    def owner(ident):
        version = svc.get_experiment(key)["row_version"]
        return svc.update_experiment(key, {"owner_id": str(ident), "row_version": version})

    with pytest.raises(ValidationError) as info:
        owner(w.mia.id)  # a member: the module is invisible to her
    assert set(info.value.fields) == {"owner_id"}
    assert owner(w.ina.id)["owner"]["display_name"] == "Ina"
    assert owner(w.chef.id)["owner"]["display_name"] == "Chef"  # administrators see it
    w.conn.execute("UPDATE users SET status = 'disabled' WHERE id = %s", (str(w.ina.id),))
    with pytest.raises(ValidationError):
        owner(w.ina.id)
    # Repeating the current owner keeps working after that person lost access.
    w.conn.execute("DELETE FROM user_roles WHERE user_id = %s", (str(w.chef.id),))
    assert owner(w.chef.id)["owner"]["display_name"] == "Chef"


# -- e2e-api-6 --------------------------------------------------------------------------------


def test_run_metric_errors_name_the_metric_and_land_on_its_input(w):
    svc = w.experimenter
    key = w.create()["key"]
    with pytest.raises(ValidationError) as info:
        svc.add_run(key, {"variant": "A", "metrics": {"cost_per_click": {"value": 20}}})
    assert info.value.fields == {"metrics.cost_per_click.denominator": "Der Nenner fehlt."}
    assert info.value.message == "\u00abKosten pro Klick\u00bb: Der Nenner fehlt."
    with pytest.raises(ValidationError) as info:
        svc.add_run(key, {"variant": "A", "metrics": {
            "cost_per_click": {"value": 20, "denominator": 4}, "ctr": {"value": 10, "count": 5}}})
    assert info.value.fields == {"metrics.ctr": "Es kann nicht mehr Erfolge als Versuche geben."}
    assert info.value.message.startswith("\u00abKlickrate\u00bb: ")
    # Rows sent as a list keep their rows.<i> keys; a missing count is named.
    with pytest.raises(ValidationError) as info:
        svc.add_run(key, {"variant": "A", "rows": [{"metric": "ctr", "value": 3}]})
    assert list(info.value.fields) == ["rows.0.count"]
    assert info.value.message.startswith("Messwert 1: \u00abVersuche\u00bb fehlt; ohne Angabe gilt 1")
    assert w.conn.execute("SELECT count(*) FROM exp_runs").fetchone()[0] == 0


# -- e2e-ui-4 ---------------------------------------------------------------------------------


def test_a_describe_target_must_fit_the_metric(w):
    svc = w.experimenter
    manager = w.manager
    key = w.create()["key"]
    svc.add_measurements(key, measurements(A=(4, 5), B=(3, 5)))
    with pytest.raises(ValidationError) as info:
        svc.run_evaluation(key, {"evaluator": "builtin.describe", "metric": "ctr",
                                 "params": {"target": 80}})
    assert info.value.fields == {"params.target": service_mod.MSG_TARGET_PROPORTION}
    assert svc.run_evaluation(key, {"evaluator": "builtin.describe", "metric": "ctr",
                                    "params": {"target": 0.8}})["status"] == "done"

    manager.create_metric({"domain": "marketing", "key": "sus", "name": "SUS", "kind": "mean",
                           "definition": {"min": 0, "max": 100}})
    metrics = [{"metric": m["key"], "role": m["role"], "guardrail_op": m["guardrail_op"],
                "guardrail_value": m["guardrail_value"]} for m in svc.get_experiment(key)["metrics"]]
    svc.set_metrics(key, {"metrics": metrics + [{"metric": "sus", "role": "secondary"}]})
    with pytest.raises(ValidationError) as info:
        svc.run_evaluation(key, {"evaluator": "builtin.describe", "metric": "sus",
                                 "params": {"target": 150}})
    assert list(info.value.fields) == ["params.target"]

    # A type asking for an impossible target: the pipeline skips that step.
    definition = json.loads(json.dumps(
        store.find_type(w.conn, store.get_domain(w.conn, "marketing")["id"], "ab_test")["definition"]))
    definition["evaluation"] = [{"evaluator": "builtin.describe", "metric": "primary",
                                 "params": {"target": 80}, "scope": {}}]
    manager.create_type({"domain": "marketing", "key": "zielfalsch", "name": "Ziel falsch",
                         "definition": definition})
    other = w.create(type="zielfalsch")["key"]
    assert svc.run_pipeline(other) == [{
        "evaluator_key": "builtin.describe", "metric_key": "ctr", "status": "skipped",
        "warning": "\u00abZiel\u00bb: " + service_mod.MSG_TARGET_PROPORTION}]


# -- review-security-4 ----------------------------------------------------------------------


def test_lists_and_objects_where_a_code_belongs_are_refused_not_crashing(w):
    svc = w.experimenter
    manager = w.manager
    key = w.create()["key"]
    for verdict in (["ship"], {}, 3):
        with pytest.raises(ValidationError) as info:
            svc.decide(key, {"verdict": verdict})
        assert "verdict" in info.value.fields
    for body in ({"kind": ["mean"]}, {"kind": {}}, {"kind": "mean", "direction": {"a": 1}},
                 {"kind": "mean", "direction": ["higher"]}):
        with pytest.raises(ValidationError):
            manager.create_metric(dict({"key": "abc_x", "name": "X"}, **body))
    ctr = store.resolve_metrics(w.conn, store.get_domain(w.conn, "marketing")["id"], ["ctr"])["ctr"]
    for body in ({"direction": {}}, {"direction": [1]}, {"kind": [1]}, {"kind": {}}):
        with pytest.raises(ValidationError):
            manager.update_metric(ctr["id"], body)
    assert store.get_metric(w.conn, ctr["id"])["direction"] == "higher"
    with pytest.raises(ValidationError) as info:
        make_custom_evaluator(w, input_kinds=[["mean"]])
    assert "input_kinds" in info.value.fields
    with pytest.raises(ValidationError) as info:
        svc.set_metrics(key, {"metrics": [{"metric": "ctr", "role": ["primary"]}]})
    assert "metrics.0.role" in info.value.fields


# -- review-backend-8 / review-stats-11 / e2e-ui-13 (d) ----------------------------------------------


def test_sample_size_refuses_absurd_effects_and_plans_for_holm(w):
    svc = w.experimenter
    for bad in ({"kind": "mean", "sd": 1, "mde": 1e-300}, {"kind": "mean", "sd": 1e300, "mde": 1e-5},
                {"kind": "proportion", "base": 0.5, "mde": 1e-12},
                {"kind": "proportion", "base": 0.5, "mde": 1e-160},
                {"kind": "proportion", "base": 0.5, "mde": 1e-200}):
        with pytest.raises(ValidationError) as info:
            svc.sample_size(bad)
        assert info.value.fields == {"mde": service_mod.MSG_SAMPLE_TOO_LARGE}, bad
    with pytest.raises(ValidationError) as info:
        svc.sample_size({"kind": "mean", "sd": 1, "mde": 0})
    assert info.value.message == ("\u00abKleinster relevanter Unterschied\u00bb: Bitte den kleinsten "
                                  "relevanten Unterschied angeben (nicht 0).")
    # Extreme scales with a sensible ratio are ordinary plans: only sd / mde
    # counts (stats takes the ratio before squaring, review-stats).
    for sd, mde in ((1e-300, 1e-300), (1e300, 1e299)):
        assert svc.sample_size({"kind": "mean", "sd": sd, "mde": mde}) == \
            svc.sample_size({"kind": "mean", "sd": sd / mde, "mde": 1})
    # Rare events keep realistic plans.
    assert svc.sample_size({"kind": "proportion", "base": 0.001, "mde": 0.0001})["per_variant"] > 10 ** 5

    one = svc.sample_size({"kind": "proportion", "base": 0.012, "mde": 0.004})
    assert one == {"per_variant": 13543, "alpha_used": 0.05, "comparisons": 1}
    two = svc.sample_size({"kind": "proportion", "base": 0.012, "mde": 0.004, "comparisons": "2"})
    assert two["alpha_used"] == 0.025 and two["comparisons"] == 2
    assert two["per_variant"] == stats.sample_size_proportion(0.012, 0.004, 0.025) > one["per_variant"]
    for bad in (0, 51, "x", 1.5, [2]):
        with pytest.raises(ValidationError) as info:
            svc.sample_size({"kind": "mean", "sd": 1, "mde": 0.5, "comparisons": bad})
        assert "comparisons" in info.value.fields


# -- review-jobs-6 ------------------------------------------------------------------------------


def test_runner_give_up_counts_from_the_first_attempt(w):
    make_custom_evaluator(w)
    svc = w.experimenter
    key = w.create()["key"]
    svc.add_measurements(key, measurements(A=(3, 30), B=(5, 30)))
    evaluation = svc.run_evaluation(key, {"evaluator": "team.custom_py", "metric": "ctr"})["id"]
    # 31 minutes in the queue before the first attempt, then one refusal
    # (a busy runner answers 503): that is no reason to give up.
    w.conn.execute("UPDATE exp_evaluations SET created_at = now() - interval '31 minutes' WHERE id = %s",
                   (evaluation,))
    w.runner.unavailable = True
    for _ in range(2):
        with pytest.raises(RetryLater):
            service_mod.execute_evaluation(w.conn, evaluation, settings=SETTINGS, runner=w.runner)
        assert svc.get_evaluation(key, evaluation)["status"] == "queued"
    # 31 minutes after the first attempt it gives up.
    w.conn.execute("UPDATE exp_evaluations SET started_at = now() - interval '31 minutes' WHERE id = %s",
                   (evaluation,))
    service_mod.execute_evaluation(w.conn, evaluation, settings=SETTINGS, runner=w.runner)
    failed = svc.get_evaluation(key, evaluation)
    assert failed["status"] == "failed" and failed["error"] == service_mod.MSG_RUNNER_GONE_30


# -- review-backend-5 ---------------------------------------------------------------------------


def test_pack_import_reindexes_what_the_knovas_copies_show(w):
    manager = w.manager
    exp = w.create()
    pack = packs.parse_pack_text(manager.export_domain("marketing"), known_metrics=[],
                                 known_evaluators=[])

    def import_with(change):
        change(pack)
        w.conn.execute("DELETE FROM exp_jobs")
        w.conn.execute("UPDATE exp_experiments SET index_state = 'indexed'")
        return manager.import_pack({"text": packs.dump_pack(pack)})

    def item(kind, key_):
        return next(i for i in pack[kind] if i["key"] == key_)

    counts = import_with(lambda p: item("metrics", "ctr").update(name="Klickrate neu", unit="pp"))
    assert counts["metrics"] == 1
    assert [j["payload"]["experiment_id"] for j in w.jobs("index")] == [exp["id"]]
    assert w.row(exp["key"])[6] == "pending"
    counts = import_with(lambda p: item("types", "ab_test").update(name="A/B-Test neu"))
    assert counts["types"] == 1
    assert [j["payload"]["experiment_id"] for j in w.jobs("index")] == [exp["id"]]
    assert w.row(exp["key"])[6] == "pending"
    # A description is not in the Knovas copy: nothing to re-upload.
    counts = import_with(lambda p: item("metrics", "ctr").update(description="Neu beschrieben"))
    assert counts["metrics"] == 1 and w.jobs("index") == []
    assert w.row(exp["key"])[6] == "indexed"
    assert w.audit("experiments.pack.import")[0]["detail"]["requeued"] == 1


# -- review-backend-6 ---------------------------------------------------------------------------


def test_a_global_pack_import_does_not_wait_for_an_experiment_being_created(w, open_conn):
    manager = w.manager
    manager.create_metric({"key": "gm", "name": "GM", "kind": "mean"})
    definition = {
        "states": [{"key": "draft", "label": "Entwurf"},
                   {"key": "stopped", "label": "Abgebrochen", "phase": "stopped"}],
        "transitions": [{"from": "*", "to": "stopped", "label": "Abbrechen"}],
        "variants": {"min": 0, "max": 4},
        "metrics": [{"metric": "gm", "role": "primary"}],
    }
    type_ = manager.create_type({"key": "gt", "name": "GT", "definition": definition})
    gm = store.resolve_metrics(w.conn, None, ["gm"])["gm"]
    pack = {"pack": "global", "title": "Global", "version": 1,
            "metrics": [{"key": "gm", "name": "GM neu", "kind": "mean"}],
            "types": [{"key": "gt", "name": "GT neu", "definition": definition}]}
    creator = open_conn()
    # What an experiment creation in flight holds: the foreign-key KEY SHARE
    # on its type (insert_experiment) and then on its metrics.
    with creator.transaction():
        creator.execute("SELECT 1 FROM exp_types WHERE id = %s FOR KEY SHARE", (type_["id"],))
        creator.execute("SELECT 1 FROM exp_metrics WHERE id = %s FOR KEY SHARE", (gm["id"],))
        w.conn.execute("SET lock_timeout = '2s'")
        try:
            counts = manager.import_pack({"text": json.dumps(pack)})
        finally:
            w.conn.execute("RESET lock_timeout")
    assert counts["metrics"] == 1 and counts["types"] == 1
    assert store.get_metric(w.conn, gm["id"])["name"] == "GM neu"


# -- review-backend-9 ---------------------------------------------------------------------------


def test_a_kind_change_waits_for_a_measurement_insert_in_flight(w, open_conn, monkeypatch):
    manager = w.manager
    svc = w.experimenter
    fresh = manager.create_metric({"domain": "marketing", "key": "fresh", "name": "Frisch",
                                   "kind": "proportion"})
    key = w.create()["key"]
    metrics = [{"metric": m["key"], "role": m["role"], "guardrail_op": m["guardrail_op"],
                "guardrail_value": m["guardrail_value"]} for m in svc.get_experiment(key)["metrics"]]
    svc.set_metrics(key, {"metrics": metrics + [{"metric": "fresh", "role": "secondary"}]})
    other = open_conn()
    watcher = open_conn()
    editor = ExperimentService(other, w.max, SETTINGS)
    outcome = {}
    original = ExperimentService._write_rows

    def change_kind():
        try:
            editor.update_metric(fresh["id"], {"kind": "mean"})
            outcome["result"] = "changed"
        except ValidationError as exc:
            outcome["result"] = exc

    def paused(self, *args, **kwargs):
        # The rows are validated as proportions; now a manager changes the kind.
        thread = threading.Thread(target=change_kind)
        thread.start()
        outcome["thread"] = thread
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and thread.is_alive():
            row = watcher.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                                  (other.info.backend_pid,)).fetchone()
            if row and row[0] == "Lock":
                outcome["waited"] = True
                break
            time.sleep(0.02)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ExperimentService, "_write_rows", paused)
    svc.add_measurements(key, {"rows": [{"metric": "fresh", "variant": "A", "value": 30, "count": 100}]})
    outcome["thread"].join(10)
    assert outcome.get("waited") is True
    assert isinstance(outcome["result"], ValidationError) and "kind" in outcome["result"].fields
    assert store.get_metric(w.conn, fresh["id"])["kind"] == "proportion"


# -- review-backend-7 ---------------------------------------------------------------------------


def test_ci_evaluations_are_pruned_like_the_pipeline_jobs(w):
    svc = w.experimenter
    exp = w.create()
    key = exp["key"]
    for i in range(12):
        svc.add_run(key, {"variant": "A", "metrics": {"ctr": {"value": i, "count": 100}}}, source="api")
        svc.run_pipeline(key, None, trigger="api")  # what client.evaluate() does
        service_mod.run_pipeline_job(w.conn, exp["id"], settings=SETTINGS, runner=None)
    rows = w.conn.execute(
        "SELECT trigger, count(*) FROM exp_evaluations "
        "GROUP BY trigger, evaluator_id, metric_id, params, scope").fetchall()
    assert {r[0] for r in rows} == {"api"}
    assert max(r[1] for r in rows) == 10


# -- review-jobs-5 ------------------------------------------------------------------------------


def test_edits_while_indexing_is_off_leave_a_state_maintenance_can_find(w):
    off = dataclasses.replace(SETTINGS, index_enabled=False)
    svc = w.svc(w.eva, settings=off)
    exp = w.create()
    store.set_index_state(w.conn, exp["id"], "indexed")
    version = svc.get_experiment(exp["key"])["row_version"]
    svc.update_experiment(exp["key"], {"title": "Neu", "row_version": version})
    assert svc.get_experiment(exp["key"])["index"]["state"] == "off"
    assert svc.get_experiment(exp["key"])["index"]["error"] is None
    assert store.experiments_for_reindex(w.conn, states=("pending", "error"),
                                         switched_off=True) == [exp["id"]]


# -- e2e-ui-13 ------------------------------------------------------------------------------------


def test_allocation_messages_name_percent_and_csv_errors_point_to_the_list(w):
    svc = w.experimenter
    key = w.create()["key"]
    with pytest.raises(ValidationError) as info:
        svc.set_variants(key, {"variants": [{"key": "A", "allocation": 0.7},
                                            {"key": "B", "allocation": 0.5}]})
    assert info.value.message == ("\u00abVarianten\u00bb: Die Zuteilungen ergeben zusammen mehr als "
                                  "100 % (Summe der Anteile > 1).")
    with pytest.raises(ValidationError) as info:
        svc.set_variants(key, {"variants": [{"key": "A", "allocation": 70}, {"key": "B"}]})
    assert info.value.fields == {"variants.0.allocation": "Eine Zuteilung zwischen 0 und 1 (0\u2013100 %)."}
    stranger = "6f1c1e8e-7c55-4f7a-9d42-5a3a0c1b2d3e"
    with pytest.raises(ValidationError) as info:
        svc.import_csv(key, f"metric,variant,value,count,run\nctr,A,1,2,{stranger}\nctr,B,1,2,{stranger}\n"
                       .encode("utf-8"), "daten.csv")
    assert info.value.message == ("Zeile 2: Diesen Lauf gibt es in diesem Experiment nicht; "
                                  "Zeile 3: Diesen Lauf gibt es in diesem Experiment nicht.")
    assert info.value.fields == {"file": "Siehe Fehlerliste."}


# -- review-security-1 / review-backend-4 --------------------------------------------------------------


def test_counts_that_could_overflow_the_sums_are_refused(w):
    svc = w.experimenter
    key = w.create()["key"]
    with pytest.raises(ValidationError) as info:
        svc.add_measurements(key, {"rows": [{"metric": "ctr", "variant": "A", "value": 0,
                                             "count": 2 ** 53 - 1}] * 1025})
    assert info.value.fields["rows.0.count"] == "\u00abVersuche\u00bb ist zu gross."
    svc.add_measurements(key, {"rows": [{"metric": "ctr", "variant": "A", "value": 0,
                                         "count": 10 ** 12}] * 1025})
    exp = svc.get_experiment(key)  # still readable, and n is exact
    ctr = next(m for m in exp["metrics"] if m["key"] == "ctr")
    assert ctr["aggregates"][0]["n"] == 1025 * 10 ** 12
    assert svc.get_timeseries(key, "ctr", "day")
    svc.run_pipeline(key)
