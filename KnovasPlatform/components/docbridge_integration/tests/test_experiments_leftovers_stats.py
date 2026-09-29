"""Leftovers of the verification round for the statistics group: aggregates
without a distribution (levels None), the proportion target as the second
line of defence, sample sizes that never crash on extreme ratios, and the
generic global metrics of the core pack (e2e-ui-2)."""

import copy
import math
import shutil

import pytest

from conftest import _person, platform_db_reachable
from experiments import evaluators as E
from experiments import kinds, stats
from experiments.packs import load_pack, validate_pack

CONTROL_B = [{"key": "A", "name": "Kontrolle", "is_control": True},
             {"key": "B", "name": "Variante B", "is_control": False}]
GENERIC = ["generic_success_rate", "generic_events_per_period", "generic_duration_s", "generic_score",
           "generic_rating"]


def agg(variant, n, value_sum, **extra):
    row = {"variant": variant, "rows": extra.pop("rows", 1), "n": n, "value_sum": value_sum,
           "denominator_sum": None, "sum_sq": None, "estimate": None, "levels": None}
    row.update(extra)
    return row


def data(kind, aggregates, *, variants=None, params=None, definition=None, name="Score"):
    return {
        "experiment": {"key": "X-1", "title": "t", "hypothesis": "", "domain": "d", "type": "t",
                       "status": "running", "fields": {}, "tags": []},
        "metric": {"key": "m", "name": name, "kind": kind, "unit": "", "direction": "higher",
                   "role": "primary", "definition": definition or {}, "guardrail": None},
        "variants": CONTROL_B if variants is None else variants,
        "aggregates": aggregates, "rows": [], "rows_truncated": False, "scope": {},
        "params": params or {},
    }


# A scale without defined levels: A has 60 distinct values (the store sends
# levels None), B has three.
MANY = agg("A", 60, 1770.0, sum_sq=70000.0, rows=60, levels=None)
FEW = agg("B", 8, 16.0, sum_sq=40.0, rows=3, levels={"1": 2, "2": 4, "3": 2})
NO_DISTRIBUTION = "A: mehr als 50 verschiedene Werte; keine Verteilung je Stufe."

# The "Neuer Typ" template of the management page (experiments_manage.js),
# with generic metrics instead of "metrics: []".
NEW_TYPE = {
    "fields": [],
    "states": [{"key": "draft", "label": "Entwurf"},
               {"key": "running", "label": "L\u00e4uft", "phase": "running"},
               {"key": "analysis", "label": "Auswertung"},
               {"key": "decided", "label": "Entschieden", "phase": "decided"},
               {"key": "stopped", "label": "Abgebrochen", "phase": "stopped"}],
    "initial": "draft",
    "transitions": [
        {"from": "draft", "to": "running", "label": "Starten",
         "requires": ["hypothesis", "primary_metric", "variants:2"]},
        {"from": "running", "to": "analysis", "label": "Zur Auswertung", "requires": ["measurements"]},
        {"from": "analysis", "to": "decided", "label": "Entscheiden", "requires": ["decision"]},
        {"from": "analysis", "to": "running", "label": "Weiterlaufen lassen"},
        {"from": "*", "to": "stopped", "label": "Abbrechen"}],
    "variants": {"min": 2, "max": 10, "defaults": [{"key": "A", "name": "Kontrolle", "is_control": True},
                                                   {"key": "B", "name": "Variante B"}]},
    "metrics": [{"metric": "generic_success_rate", "role": "primary"},
                {"metric": "generic_rating", "role": "secondary"}],
    "evaluation": [{"evaluator": "builtin.describe", "metric": "all", "params": {}},
                   {"evaluator": "builtin.two_proportion", "metric": "primary", "params": {}}],
    "decision": {"require_learning": True},
}


# -- levels None: chi_square says "not applicable", never "no data" ------------------------


def test_chi_square_is_not_applicable_when_a_variant_has_more_than_50_levels():
    out = E.run_builtin("builtin.chi_square", data("ordinal", [MANY, FEW]))
    assert out["verdict"] == "n/a"
    assert out["headline"] == "Chi-Quadrat-Test: nicht anwendbar: mehr als 50 Stufen"
    assert kinds.MAX_AGGREGATE_LEVELS == 50
    assert out["warnings"] == [NO_DISTRIBUTION]
    # Not a goodness-of-fit test of B alone, and no comparison.
    assert out["comparisons"] == [] and "test" not in out["values"]
    assert ("Ergebnis: Der Test ist nicht anwendbar: mehr als 50 Stufen. Ohne definierte Stufen gibt "
            "es keine Verteilung, die sich testen l\u00e4sst; die mittlere Stufe vergleicht der "
            "Welch-t-Test.") in out["summary"]
    # The data is there and reported.
    assert "- A (Kontrolle): mittlere Stufe 29,50 bei 60 Antworten" in out["summary"]
    assert out["table"]["rows"][0][-3:] == ["\u2013", "\u2013", "\u2013"]


def test_chi_square_without_any_distribution_is_not_applicable_rather_than_empty():
    both = E.run_builtin("builtin.chi_square", data(
        "ordinal", [MANY, dict(FEW, levels=None)]))
    assert both["verdict"] == "n/a" and "noch keine Messwerte" not in both["headline"]
    assert both["warnings"] == [NO_DISTRIBUTION,
                                "B: mehr als 50 verschiedene Werte; keine Verteilung je Stufe."]
    # Rows without a variant, merged from two aggregates of which one has too many values.
    alone = E.run_builtin("builtin.chi_square", data(
        "ordinal", [agg(None, 5, 10.0, levels={"2": 5}), agg(None, 60, 1770.0, levels=None)],
        variants=[]))
    assert alone["verdict"] == "n/a"
    assert alone["headline"] == "Chi-Quadrat-Test: nicht anwendbar: mehr als 50 Stufen"
    assert alone["warnings"] == [
        "ohne Variante: mehr als 50 verschiedene Werte; keine Verteilung je Stufe."]
    # Categories: the same, in their words.
    categories = E.run_builtin("builtin.chi_square", data(
        "categorical", [agg("A", 60, 0.0), agg("B", 10, 0.0, levels={"1": 4, "2": 6})]))
    assert categories["verdict"] == "n/a"
    assert categories["headline"] == "Chi-Quadrat-Test: nicht anwendbar: mehr als 50 Kategorien"
    assert categories["warnings"] == [
        "A: mehr als 50 verschiedene Werte; keine Verteilung je Kategorie."]
    assert "Ergebnis: Der Test ist nicht anwendbar: mehr als 50 Kategorien." in categories["summary"]


def test_chi_square_ignores_a_missing_distribution_it_does_not_test():
    # Rows without a variant are not compared when the variants have data.
    out = E.run_builtin("builtin.chi_square", data(
        "ordinal", [dict(FEW, variant="A"), FEW, agg(None, 60, 1770.0, levels=None)]))
    assert out["headline"].startswith("Kein belegter Unterschied in der Verteilung")
    assert not any("verschiedene Werte" in w for w in out["warnings"])


def test_describe_shows_unknown_level_shares_as_unknown():
    out = E.run_builtin("builtin.describe", data("ordinal", [MANY, FEW]))
    assert out["table"]["headers"][-3:] == ["1", "2", "3"]
    a_row, b_row = out["table"]["rows"]
    assert a_row[-3:] == ["\u2013", "\u2013", "\u2013"]  # was "0,0 % (0)": no data claimed
    assert b_row[-3:] == ["25,0 % (2)", "50,0 % (4)", "25,0 % (2)"]
    assert out["warnings"] == [NO_DISTRIBUTION]
    assert out["variants"][0]["value"] == pytest.approx(29.5)
    # Without level columns there is nothing to explain.
    alone = E.run_builtin("builtin.describe", data("ordinal", [MANY], variants=CONTROL_B[:1]))
    assert alone["warnings"] == [] and alone["headline"].startswith("Score 29,50")


def test_describe_of_categories_without_a_distribution_names_the_count():
    out = E.run_builtin("builtin.describe", data("categorical", [agg("A", 60, 0.0)],
                                                 variants=CONTROL_B[:1]))
    assert out["headline"] == "Score: n = 60"  # was "Score: am haeufigsten "
    assert out["warnings"] == ["A: mehr als 50 verschiedene Werte; keine Verteilung je Kategorie."]
    assert "- A (Kontrolle): 60 Eintr\u00e4ge." in out["summary"]


# -- the proportion target as the second line of defence ------------------------------------


def test_describe_refuses_a_proportion_target_outside_0_to_1_with_the_fixed_hint():
    out = E.run_builtin("builtin.describe", data(
        "proportion", [agg(None, 80, 74)], variants=[], params={"target": 80}))
    assert out["verdict"] == "n/a" and "Ziel" not in out["headline"]
    assert out["warnings"] == ["Ziel ausserhalb 0..1 \u2013 f\u00fcr Anteile 0,8 statt 80 angeben."]
    description = E.BUILTINS["builtin.describe"].params_schema["properties"]["target"]["description"]
    assert description == "Zielwert; Anteile als 0..1 (80 % = 0.8), sonst in der Einheit der Metrik."


# -- sample sizes: ratios before squares, StatsInputError instead of a crash ----------------


def test_sample_size_mean_depends_on_the_ratio_only():
    # sd * sd and mde * mde underflowed to 0 (ZeroDivisionError) or overflowed
    # to inf / inf (ValueError from NaN) although the ratio is harmless.
    assert stats.sample_size_mean(1e-300, 1e-300) == stats.sample_size_mean(1.0, 1.0) == 17
    assert stats.sample_size_mean(5e-324, 5e-324) == 17
    assert stats.sample_size_mean(1e300, 1e299) == stats.sample_size_mean(10.0, 1.0)
    assert stats.sample_size_mean(1e-300, 1e300) == 2


@pytest.mark.parametrize("call", [
    lambda: stats.sample_size_mean(1.0, 1e-300),
    lambda: stats.sample_size_mean(1e300, 1e-5),
    lambda: stats.sample_size_mean(1e200, 1e-100),
    lambda: stats.sample_size_proportion(0.5, 1e-160),
    lambda: stats.sample_size_proportion(0.5, 1e-200),
    lambda: stats.sample_size_proportion(0.5, 5e-324),
])
def test_sample_size_without_a_finite_answer_is_a_german_input_error(call):
    with pytest.raises(stats.StatsInputError) as info:
        call()
    assert info.value.message == ("Der gesuchte Effekt ist im Verh\u00e4ltnis zur Streuung zu klein; "
                                  "die Stichprobe l\u00e4sst sich nicht berechnen.")


def test_t_test_power_is_a_probability_for_every_n():
    # The tiny sd underflowed sd * sqrt(2 / n) to 0 (ZeroDivisionError).
    assert stats._t_test_power(17, 5e-324, 5e-324, 0.05) == stats._t_test_power(17, 1.0, 1.0, 0.05)
    # The log density cancelled for large df: 5.5e34 at n = 1e16, an
    # OverflowError from 1e18 on. Now it is the z test's power there.
    for n, delta in ((10 ** 14, 0.0707), (10 ** 16, 0.7071), (10 ** 18, 7.071), (10 ** 300, 7e141)):
        power = stats._t_test_power(n, 1.0, 1e-8, 0.05)
        z = stats.normal_ppf(0.975)
        expected = stats.normal_cdf(delta - z) + stats.normal_cdf(-delta - z)
        assert 0.0 <= power <= 1.0 and power == pytest.approx(expected, rel=1e-3), n
    assert stats.sample_size_mean(1.0, 1e-8) == pytest.approx(2 * 7.849 * 1e16, rel=1e-3)


def test_sample_sizes_of_the_plan_are_unchanged():
    assert stats.sample_size_proportion(0.012, 0.004) == 13543
    assert stats.sample_size_mean(1.0, 0.5) == 64


# -- the generic global metrics of the core pack (e2e-ui-2) ----------------------------------


def test_core_ships_one_generic_global_metric_per_common_kind():
    core = load_pack("core")
    assert core["version"] == 3
    metrics = {m["key"]: m for m in core["metrics"]}
    assert list(metrics) == GENERIC
    assert {k: (m["kind"], m["direction"]) for k, m in metrics.items()} == {
        "generic_success_rate": ("proportion", "higher"),
        "generic_events_per_period": ("count", "higher"),
        "generic_duration_s": ("duration", "lower"),
        "generic_score": ("mean", "higher"),
        "generic_rating": ("ordinal", "higher"),
    }
    assert metrics["generic_rating"]["definition"]["levels"] == {
        "1": "sehr schlecht", "2": "schlecht", "3": "mittel", "4": "gut", "5": "sehr gut"}
    for m in metrics.values():
        assert m["name"] and m["description"] and "\u00df" not in m["description"]
    # No shipped domain pack uses these keys, or the prefix.
    for name in ("engineering", "marketing", "sales", "product"):
        pack = load_pack(name)
        assert not any(m["key"].startswith("generic_") for m in pack["metrics"]), name
        assert not set(pack["requires_metrics"]) & set(GENERIC)


def test_types_can_reference_the_generic_metrics():
    # The core type, within its own pack ...
    core = copy.deepcopy(load_pack("core"))
    hypothesis = core["types"][0]["definition"]
    hypothesis["metrics"] = [{"metric": "generic_success_rate", "role": "primary"},
                             {"metric": "generic_rating", "role": "secondary"}]
    hypothesis["evaluation"].append({"evaluator": "builtin.two_proportion", "metric": "primary"})
    assert validate_pack(core)["requires_metrics"] == []
    # ... and a new domain's type, which names them among the global metrics.
    definition = copy.deepcopy(NEW_TYPE)
    definition["metrics"].append({"metric": "generic_duration_s", "role": "guardrail", "op": "max",
                                  "value": 600})
    own = {"pack": "events", "title": "Veranstaltungen", "version": 1,
           "domain": {"key": "events", "name": "Veranstaltungen", "id_prefix": "EVT"},
           "types": [{"key": "anlass", "name": "Anlass", "definition": definition}]}
    assert validate_pack(own, known_metrics=GENERIC)["requires_metrics"] == [
        "generic_success_rate", "generic_rating", "generic_duration_s"]


# -- the example evaluators divide only by a positive baseline (review-stats-10) ------------


def _example_input(rows, kind, aggregates=()):
    return {"metric": {"key": "s", "name": "S", "kind": kind, "unit": "", "direction": "higher",
                       "definition": {}, "guardrail": None},
            "variants": [{"key": "A", "name": "", "is_control": True},
                         {"key": "B", "name": "", "is_control": False}],
            "aggregates": list(aggregates), "rows": rows, "rows_truncated": False, "scope": {},
            "params": {"draws": 300}}


def test_python_example_has_no_relative_change_against_a_zero_control():
    pytest.importorskip("numpy")
    code = next(e for e in load_pack("core")["evaluators"] if e["language"] == "python")["code"]
    namespace = {}
    exec(compile(code, "evaluator.py", "exec"), namespace)  # noqa: S102 - our own template
    rows = ([{"variant": "A", "value": v, "count": 1} for v in (-1.0, 1.0, -2.0, 2.0)]
            + [{"variant": "B", "value": v, "count": 1} for v in (3.0, 4.0, 5.0, 4.0)])
    (c,) = namespace["evaluate"](_example_input(rows, "mean"))["comparisons"]
    assert c["estimate"] == pytest.approx(4.0) and c["relative"] is None


@pytest.mark.skipif(shutil.which("julia") is None, reason="julia is not installed here")
def test_julia_example_has_no_relative_change_against_a_control_without_successes(tmp_path):
    import json
    import subprocess

    from test_experiments_packs import _JULIA_DRIVER, _julia_code, _to_julia

    data = _example_input([], "proportion", [
        {"variant": "A", "rows": 1, "n": 200, "value_sum": 0.0},
        {"variant": "B", "rows": 1, "n": 200, "value_sum": 12.0}])
    data["params"] = {"draws": 2000}
    (tmp_path / "code.jl").write_text(_julia_code(), encoding="utf-8")
    (tmp_path / "data.jl").write_text(_to_julia(data), encoding="utf-8")
    (tmp_path / "driver.jl").write_text(_JULIA_DRIVER, encoding="utf-8")
    done = subprocess.run(["julia", "--startup-file=no", "--history-file=no", str(tmp_path / "driver.jl"),
                           str(tmp_path / "code.jl"), str(tmp_path / "data.jl")],
                          capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-2000:]
    (c,) = json.loads(done.stdout)["comparisons"]
    assert c["relative"] is None and c["verdict"] == "better"


# -- end to end: an existing installation gets the generic metrics --------------------------


def _counts(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM exp_types), (SELECT count(*) FROM exp_type_versions), "
        "(SELECT count(*) FROM exp_evaluator_versions)").fetchone()


@pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")
def test_core_upgrade_adds_the_generic_metrics_and_keeps_edits(platform_db):
    from experiments import store

    conn = platform_db
    store.ensure_builtin_evaluators(conn)
    previous = copy.deepcopy(load_pack("core"))
    previous.update(version=2, metrics=[])  # what installations of version 2 have
    store.install_pack(conn, previous)
    assert not set(GENERIC) & set(store.global_metric_keys(conn))
    before = _counts(conn)

    store.ensure_core_pack(conn)  # the next start with version 3
    metrics = {m["key"]: m for m in store.list_metrics(conn, restrict=True, domain_id=None)}
    assert set(GENERIC) <= set(metrics)
    assert all(metrics[k]["domain_id"] is None for k in GENERIC)
    assert metrics["generic_rating"]["definition"]["levels"]["5"] == "sehr gut"
    assert _counts(conn) == before  # no new type or evaluator version

    # A manager's edit survives the next start; nothing is added twice.
    store.update_metric(conn, metrics["generic_score"]["id"], {"name": "Qualit\u00e4t"})
    store.ensure_core_pack(conn)
    again = [m for m in store.list_metrics(conn, include_archived=True) if m["key"] in GENERIC]
    assert len(again) == len(GENERIC)
    assert next(m for m in again if m["key"] == "generic_score")["name"] == "Qualit\u00e4t"


@pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")
@pytest.mark.parametrize("way", ["install", "import"])
def test_core_upgrade_through_the_manager_pages(platform_db, identity_repo, way):
    from experiments import store
    from experiments.packs import dump_pack
    from experiments.service import ExperimentService
    from experiments.settings import ExperimentsSettings

    settings = ExperimentsSettings(enabled=True, index_enabled=False, worker_enabled=False)
    store.ensure_builtin_evaluators(platform_db)
    previous = copy.deepcopy(load_pack("core"))
    previous.update(version=2, metrics=[])
    store.install_pack(platform_db, previous)
    manager = ExperimentService(platform_db, _person(identity_repo, "max@knovas.ch", "Max",
                                                     "experiments_manager"), settings,
                                request_meta={"ip": "10.1.2.3", "user_agent": "pytest", "token_id": None})
    if way == "install":
        upgrade = lambda: manager.install_pack("core")  # noqa: E731
    else:
        upgrade = lambda: manager.import_pack({"text": dump_pack(load_pack("core"))})  # noqa: E731
    assert upgrade() == {"domain": 0, "types": 0, "metrics": 5, "evaluators": 0}
    assert upgrade() == {"domain": 0, "types": 0, "metrics": 0, "evaluators": 0}
    assert set(GENERIC) <= set(store.global_metric_keys(platform_db))
    assert {p["name"]: p["installed"] for p in manager.list_packs()}["core"] is True


@pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")
def test_a_new_domain_measures_with_the_generic_metrics(platform_db, identity_repo):
    from experiments import store
    from experiments.service import ExperimentService
    from experiments.settings import ExperimentsSettings

    settings = ExperimentsSettings(enabled=True, index_enabled=False, worker_enabled=False)
    meta = {"ip": "10.1.2.3", "user_agent": "pytest", "token_id": None}
    store.ensure_builtin_evaluators(platform_db)
    store.ensure_core_pack(platform_db)
    max_ = _person(identity_repo, "max@knovas.ch", "Max", "experiments_manager")
    eva = _person(identity_repo, "eva@knovas.ch", "Eva", "experimenter")
    manager = ExperimentService(platform_db, max_, settings, request_meta=dict(meta))
    svc = ExperimentService(platform_db, eva, settings, request_meta=dict(meta))
    manager.create_domain({"key": "events", "name": "Veranstaltungen", "id_prefix": "EVT"})
    assert set(GENERIC) <= {m["key"] for m in svc.list_metrics(domain="events")}

    manager.create_type({"domain": "events", "key": "anlass", "name": "Anlass",
                         "definition": copy.deepcopy(NEW_TYPE)})
    key = svc.create_experiment({"domain": "events", "type": "anlass", "title": "Einladung",
                                 "hypothesis": "Eine kurze Einladung bringt mehr Anmeldungen."})["key"]
    assert key == "EVT-1"
    svc.add_measurements(key, {"rows": [
        {"metric": "generic_success_rate", "variant": "A", "value": 30, "count": 200},
        {"metric": "generic_success_rate", "variant": "B", "value": 60, "count": 200},
        {"metric": "generic_rating", "variant": "A", "value": 3, "count": 10},
        {"metric": "generic_rating", "variant": "B", "value": 5, "count": 10}]})
    result = svc.run_evaluation(key, {"evaluator": "builtin.two_proportion",
                                      "metric": "generic_success_rate"})
    assert result["status"] == "done" and result["verdict"] == "better"
    # The generic metrics also go on an experiment of the global type.
    other = svc.create_experiment({"domain": "events", "type": "hypothesis", "title": "Ablauf",
                                   "metrics": [{"metric": "generic_duration_s", "role": "primary"}]})
    assert [m["key"] for m in other["metrics"]] == ["generic_duration_s"]


@pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")
def test_chi_square_on_a_scale_with_many_values_is_not_applicable_end_to_end(platform_db,
                                                                            identity_repo):
    from experiments import store
    from experiments.service import ExperimentService
    from experiments.settings import ExperimentsSettings

    settings = ExperimentsSettings(enabled=True, index_enabled=False, worker_enabled=False)
    meta = {"ip": "10.1.2.3", "user_agent": "pytest", "token_id": None}
    store.ensure_builtin_evaluators(platform_db)
    store.ensure_core_pack(platform_db)
    max_ = _person(identity_repo, "max@knovas.ch", "Max", "experiments_manager")
    manager = ExperimentService(platform_db, max_, settings, request_meta=dict(meta))
    manager.create_domain({"key": "events", "name": "Veranstaltungen", "id_prefix": "EVT"})
    manager.create_metric({"domain": "events", "key": "punkte", "name": "Punkte", "kind": "ordinal",
                           "definition": {"min": 0, "max": 100}})
    key = manager.create_experiment({
        "domain": "events", "type": "hypothesis", "title": "Punkte",
        "variants": [{"key": "A", "name": "Kontrolle", "is_control": True}, {"key": "B", "name": "Neu"}],
        "metrics": [{"metric": "punkte", "role": "primary"}]})["key"]
    manager.add_measurements(key, {"rows": [
        {"metric": "punkte", "variant": v, "value": i + (0.5 if v == "B" else 0.0)}
        for v in "AB" for i in range(60)]})
    result = manager.run_evaluation(key, {"evaluator": "builtin.chi_square", "metric": "punkte"})
    assert result["status"] == "done" and result["verdict"] == "n/a"
    assert result["headline"] == "Chi-Quadrat-Test: nicht anwendbar: mehr als 50 Stufen"
    output = manager.get_evaluation(key, result["id"])["output"]
    assert "A: mehr als 50 verschiedene Werte; keine Verteilung je Stufe." in output["warnings"]
    assert not math.isnan(output["variants"][0]["value"])
