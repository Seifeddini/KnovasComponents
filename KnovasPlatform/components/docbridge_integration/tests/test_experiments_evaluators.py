"""Tests for experiments.evaluators: the built-ins, parameters, the input
mapping and the output contract (plan section 7)."""

import copy
import json
import random

import pytest

from experiments import evaluators as E
from experiments import stats
from experiments.errors import NotFound, ValidationError

CONTROL_B = [{"key": "A", "name": "Kontrolle", "is_control": True},
             {"key": "B", "name": "Variante B", "is_control": False}]


def agg(variant, n, value_sum, **extra):
    row = {"variant": variant, "rows": extra.pop("rows", 1), "n": n, "value_sum": value_sum,
           "denominator_sum": None, "sum_sq": None, "estimate": None, "levels": None}
    row.update(extra)
    return row


def mean_agg(variant, n, mean, sd):
    """Aggregate of n observations with the given mean and sample sd."""
    return agg(variant, n, n * mean, sum_sq=n * mean * mean + (n - 1) * sd * sd)


def data(kind, aggregates, *, direction="higher", unit="", variants=None, rows=None, params=None,
         guardrail=None, definition=None, name="Klickrate", rows_truncated=False):
    return {
        "experiment": {"key": "MKT-1", "title": "Test", "hypothesis": "", "domain": "marketing",
                       "type": "ab_test", "status": "running", "fields": {}, "tags": []},
        "metric": {"key": "m", "name": name, "kind": kind, "unit": unit, "direction": direction,
                   "role": "primary", "definition": definition or {}, "guardrail": guardrail},
        "variants": CONTROL_B if variants is None else variants,
        "aggregates": aggregates,
        "rows": rows or [],
        "rows_truncated": rows_truncated,
        "scope": {},
        "params": params or {},
    }


def assert_contract(out):
    """What every stored output must look like."""
    assert set(out) == set(E.OUTPUT_KEYS)
    assert out["verdict"] in E.VERDICTS
    assert isinstance(out["headline"], str) and 0 < len(out["headline"]) <= 200
    assert isinstance(out["summary"], str) and len(out["summary"]) <= 20000
    assert isinstance(out["comparisons"], list) and len(out["comparisons"]) <= 50
    for c in out["comparisons"]:
        assert set(c) == {"variant", "baseline", "label", "estimate", "ci_low", "ci_high",
                          "p_value", "prob_better", "relative", "unit", "verdict"}
        assert c["verdict"] in E.VERDICTS
    assert isinstance(out["variants"], list) and len(out["variants"]) <= 50
    for v in out["variants"]:
        assert set(v) == {"variant", "n", "value", "sd", "sum", "ci_low", "ci_high"}
    assert isinstance(out["values"], dict) and len(out["values"]) <= 100
    assert set(out["table"]) == {"headers", "rows"}
    assert len(out["table"]["headers"]) <= 20 and len(out["table"]["rows"]) <= 200
    assert all(isinstance(cell, str) for row in out["table"]["rows"] for cell in row)
    assert isinstance(out["warnings"], list) and len(out["warnings"]) <= 20
    text = json.dumps(out, allow_nan=False)
    assert len(text) <= 256 * 1024
    assert "\\u0000" not in text


# -- registry and parameters ------------------------------------------------------


EXPECTED_BUILTINS = {
    "builtin.describe": ("Beschreibung je Variante",
                         ("proportion", "mean", "count", "duration", "currency", "ratio",
                          "ordinal", "categorical"), False),
    "builtin.two_proportion": ("Zwei-Anteile-Test", ("proportion",), False),
    "builtin.bayes_proportion": ("Bayes-Vergleich (Anteile)", ("proportion",), False),
    "builtin.welch_t": ("Welch-t-Test", ("mean", "duration", "currency", "ordinal"), False),
    "builtin.paired_t": ("Gepaarter t-Test", ("mean", "duration", "currency", "ordinal"), True),
    "builtin.poisson_rate": ("Raten-Vergleich", ("count",), False),
    "builtin.ratio_delta": ("Verh\u00e4ltnis-Vergleich", ("ratio",), True),
    "builtin.chi_square": ("Chi-Quadrat-Test", ("categorical", "ordinal"), False),
}


def test_builtins_match_the_plan_table():
    assert set(E.BUILTINS) == set(EXPECTED_BUILTINS)
    for key, (name, kinds, needs_rows) in EXPECTED_BUILTINS.items():
        spec = E.BUILTINS[key]
        assert spec.key == key and spec.name == name
        assert set(spec.input_kinds) == set(kinds)
        assert spec.needs_rows is needs_rows
        assert callable(spec.fn) and spec.description


def test_params_schemas_are_closed_and_valid():
    from jsonschema import Draft202012Validator

    for spec in E.BUILTINS.values():
        schema = spec.params_schema
        Draft202012Validator.check_schema(schema)
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        json.dumps(schema, allow_nan=False)
    props = {key: set(spec.params_schema["properties"]) for key, spec in E.BUILTINS.items()}
    assert props["builtin.describe"] == {"target"}
    for key in ("builtin.two_proportion", "builtin.welch_t", "builtin.poisson_rate",
                "builtin.ratio_delta"):
        assert props[key] == {"alpha", "correction"}
    assert props["builtin.paired_t"] == {"alpha", "correction", "pair_by"}
    assert props["builtin.bayes_proportion"] == {"threshold", "prior_a", "prior_b"}
    assert props["builtin.chi_square"] == {"alpha", "correction", "expected"}


def test_default_params():
    assert E.default_params("builtin.describe") == {}
    assert E.default_params("builtin.two_proportion") == {"alpha": 0.05, "correction": "holm"}
    assert E.default_params("builtin.paired_t") == {"alpha": 0.05, "correction": "holm",
                                                     "pair_by": "query"}
    assert E.default_params("builtin.bayes_proportion") == {"threshold": 0.95, "prior_a": 1.0,
                                                             "prior_b": 1.0}
    assert E.default_params("example.custom") == {}
    first = E.default_params("builtin.welch_t")
    first["alpha"] = 0.2
    assert E.default_params("builtin.welch_t")["alpha"] == 0.05


def test_validate_params_accepts_and_copies():
    schema = E.BUILTINS["builtin.chi_square"].params_schema
    params = {"alpha": 0.01, "expected": [1, 2, 3]}
    result = E.validate_params(schema, params)
    assert result == params and result is not params
    result["expected"].append(4)
    assert params["expected"] == [1, 2, 3]
    assert E.validate_params(schema, None) == {}


@pytest.mark.parametrize("params,field,message", [
    ({"alpha": 0.5}, "params.alpha", "Darf h\u00f6chstens 0,2 sein."),
    ({"alpha": 0.0001}, "params.alpha", "Muss mindestens 0,001 sein."),
    ({"alpha": "0.05"}, "params.alpha", "Erwartet eine Zahl."),
    ({"alpha": True}, "params.alpha", "Erwartet eine Zahl."),
    ({"correction": "bonferroni"}, "params.correction", "Erlaubt: holm, none."),
    ({"foo": 1}, "params.foo", "Unbekannter Parameter."),
])
def test_validate_params_refuses_with_german_field_messages(params, field, message):
    with pytest.raises(ValidationError) as excinfo:
        E.validate_params(E.BUILTINS["builtin.two_proportion"].params_schema, params)
    assert excinfo.value.message == "Die Parameter sind ung\u00fcltig."
    assert excinfo.value.fields == {field: message}


def test_validate_params_nested_and_pattern_errors():
    with pytest.raises(ValidationError) as excinfo:
        E.validate_params(E.BUILTINS["builtin.chi_square"].params_schema, {"expected": [1, 0]})
    assert excinfo.value.fields == {"params.expected.1": "Muss gr\u00f6sser als 0 sein."}
    with pytest.raises(ValidationError) as excinfo:
        E.validate_params(E.BUILTINS["builtin.paired_t"].params_schema, {"pair_by": "a b"})
    assert excinfo.value.fields == {"params.pair_by": "Ung\u00fcltiges Format."}
    with pytest.raises(ValidationError) as excinfo:
        E.validate_params(E.BUILTINS["builtin.chi_square"].params_schema, {"expected": [1] * 51})
    assert excinfo.value.fields == {"params.expected": "H\u00f6chstens 50 Eintr\u00e4ge."}


def test_validate_params_refuses_bad_shapes_sizes_and_numbers():
    schema = {"type": "object"}
    for bad in ([1], "x", 3):
        with pytest.raises(ValidationError):
            E.validate_params(schema, bad)
    with pytest.raises(ValidationError) as excinfo:
        E.validate_params(schema, {"x": float("nan")})
    assert "params" in excinfo.value.fields
    with pytest.raises(ValidationError):
        E.validate_params(schema, {"x": float("inf")})
    with pytest.raises(ValidationError) as excinfo:
        E.validate_params(schema, {"x": "a" * 5000})
    assert "4 KB" in excinfo.value.message
    # Exactly the limit is fine: the size counts UTF-8 bytes of the JSON.
    E.validate_params(schema, {"x": "a" * (4096 - len('{"x": ""}'))})
    with pytest.raises(ValidationError):
        E.validate_params(schema, {"x": "\u00e4" * 2100})


def test_validate_params_with_a_broken_schema_is_a_german_error():
    with pytest.raises(ValidationError) as excinfo:
        E.validate_params({"type": "no-such-type"}, {})
    assert excinfo.value.message == "Das Parameterschema des Auswerters ist ung\u00fcltig."
    with pytest.raises(ValidationError):
        E.validate_params(["not", "a", "schema"], {})


def test_validate_params_required_is_reported_per_field():
    schema = {"type": "object", "required": ["window"], "properties": {"window": {"type": "integer"}}}
    with pytest.raises(ValidationError) as excinfo:
        E.validate_params(schema, {})
    assert excinfo.value.fields == {"params.window": "Pflichtangabe."}


# -- build_input ------------------------------------------------------------------


SNAPSHOT = {
    "id": "u1", "key": "MKT-1", "title": "Neuer Betreff", "hypothesis": "K\u00fcrzer ist besser.",
    "description": "lang", "status": "running", "status_label": "L\u00e4uft",
    "domain": {"id": "d1", "key": "marketing", "name": "Marketing", "color": "#eb6834",
               "id_prefix": "MKT"},
    "type": {"id": "t1", "key": "ab_test", "name": "A/B-Test", "version": 2},
    "fields": [{"key": "channel", "label": "Kanal", "type": "enum", "value": "LinkedIn",
                "display": "LinkedIn"}],
    "field_values": {"channel": "LinkedIn"},
    "tags": ["q3"],
    "variants": [
        {"id": "v1", "key": "A", "name": "Kontrolle", "description": "alt", "is_control": True,
         "allocation": 0.5, "position": 0, "has_data": True},
        {"id": "v2", "key": "B", "name": "Variante B", "description": "neu", "is_control": False,
         "allocation": 0.5, "position": 1, "has_data": True},
    ],
    "metrics": [],
}

GUARDRAIL_METRIC = {
    "id": "m2", "key": "bounce_rate", "name": "Absprungrate", "kind": "proportion",
    "kind_label": "Anteil", "unit": "%", "direction": "lower", "direction_label": "tiefer ist besser",
    "role": "guardrail", "role_label": "Leitplanke", "guardrail_op": "max", "guardrail_value": 0.7,
    "definition": {"decimals": 1}, "guardrail_status": "ok",
    "aggregates": [{"variant": "A", "n": 1}],
}


def test_build_input_maps_the_snapshot():
    aggregates = [agg("A", 100, 60), agg("B", 100, 78)]
    rows = [{"variant": "A", "run": None, "value": 1.0, "count": 1, "denominator": None,
             "sum_sq": None, "observed_at": "2026-09-16T00:00:00+00:00", "dims": {}}]
    result = E.build_input(snapshot=SNAPSHOT, metric=GUARDRAIL_METRIC, aggregates=aggregates,
                           rows=rows, rows_truncated=True, params={"alpha": 0.1},
                           scope={"runs": "latest"})
    assert result["experiment"] == {
        "key": "MKT-1", "title": "Neuer Betreff", "hypothesis": "K\u00fcrzer ist besser.",
        "domain": "marketing", "type": "ab_test", "status": "running",
        "fields": {"channel": "LinkedIn"}, "tags": ["q3"]}
    metric = result["metric"]
    assert "aggregates" not in metric
    assert metric["guardrail"] == {"op": "max", "value": 0.7}
    assert metric["definition"] == {"decimals": 1}
    assert metric["key"] == "bounce_rate" and metric["kind"] == "proportion"
    assert metric["direction"] == "lower" and metric["role"] == "guardrail"
    assert result["variants"] == [{"key": "A", "name": "Kontrolle", "is_control": True},
                                  {"key": "B", "name": "Variante B", "is_control": False}]
    assert result["aggregates"] == aggregates and result["aggregates"][0] is not aggregates[0]
    assert result["rows"] == rows and result["rows_truncated"] is True
    assert result["scope"] == {"runs": "latest"} and result["params"] == {"alpha": 0.1}
    assert list(result) == ["experiment", "metric", "variants", "aggregates", "rows",
                            "rows_truncated", "scope", "params"]
    json.dumps(result, allow_nan=False)
    # The snapshot entry itself is untouched.
    assert "guardrail" not in GUARDRAIL_METRIC and GUARDRAIL_METRIC["aggregates"]


def test_build_input_without_guardrail_rows_or_params():
    metric = {"key": "ctr", "name": "Klickrate", "kind": "proportion", "role": "primary",
              "guardrail_op": None, "guardrail_value": None, "definition": None}
    result = E.build_input(snapshot=SNAPSHOT, metric=metric,
                           aggregates=[agg("A", 10, 1, estimate=float("nan"))], rows=None,
                           rows_truncated=False, params=None, scope=None)
    assert result["metric"]["guardrail"] is None
    assert result["metric"]["definition"] == {}
    again = E.build_input(snapshot=SNAPSHOT, metric=E.build_input(
        snapshot=SNAPSHOT, metric=GUARDRAIL_METRIC, aggregates=[], rows=None, rows_truncated=False,
        params={}, scope={})["metric"], aggregates=[], rows=None, rows_truncated=False, params={}, scope={})
    assert again["metric"]["guardrail"] == {"op": "max", "value": 0.7}
    assert result["rows"] == [] and result["params"] == {} and result["scope"] == {}
    assert result["aggregates"][0]["estimate"] is None
    json.dumps(result, allow_nan=False)


def test_build_input_feeds_the_builtins():
    aggregates = [agg("A", 100, 60), agg("B", 100, 78)]
    result = E.build_input(snapshot=SNAPSHOT, metric=GUARDRAIL_METRIC, aggregates=aggregates,
                           rows=None, rows_truncated=False, params={}, scope={})
    out = E.run_builtin("builtin.describe", result)
    assert out["verdict"] == "worse"
    assert "Leitplanke verletzt: Variante B 78,0 % > 70,0 %." in out["warnings"]


# -- run_builtin ------------------------------------------------------------------


def test_run_builtin_refuses_unknown_keys_and_bad_params():
    with pytest.raises(NotFound):
        E.run_builtin("builtin.nope", data("proportion", []))
    with pytest.raises(NotFound):
        E.run_builtin("example.bootstrap_mean_py", data("proportion", []))
    with pytest.raises(ValidationError) as excinfo:
        E.run_builtin("builtin.two_proportion", data("proportion", [], params={"alpha": 2}))
    assert "params.alpha" in excinfo.value.fields
    with pytest.raises(ValidationError):
        E.run_builtin("builtin.describe", "not a dict")


def test_run_builtin_does_not_mutate_its_input():
    payload = data("proportion", [agg("A", 100, 10), agg("B", 100, 20)])
    before = copy.deepcopy(payload)
    E.run_builtin("builtin.two_proportion", payload)
    E.run_builtin("builtin.bayes_proportion", payload)
    assert payload == before


def test_builtin_functions_tolerate_unvalidated_params():
    """The service may call spec.fn directly; invalid params fall back to defaults."""
    payload = data("proportion", [agg("A", 100, 10), agg("B", 100, 20)],
                   params={"alpha": "x", "correction": 5, "threshold": 7})
    for key in ("builtin.two_proportion", "builtin.bayes_proportion", "builtin.describe"):
        assert_contract(E.sanitize_output(E.BUILTINS[key].fn(payload)))


# -- describe ---------------------------------------------------------------------


def test_describe_proportions_with_wilson_intervals():
    out = E.run_builtin("builtin.describe", data("proportion", [agg("A", 10688, 129), agg("B", 10714, 175)],
                                                 definition={"decimals": 2}))
    assert_contract(out)
    assert out["verdict"] == "n/a"
    assert out["headline"] == "Klickrate: A 1,21 % \u00b7 B 1,63 %"
    a, b = out["variants"]
    assert a == pytest.approx({"variant": "A", "n": 10688, "value": 129 / 10688, "sd": None, "sum": 129,
                               "ci_low": 0.010167692378450447, "ci_high": 0.014322145020389466})
    assert b["ci_low"] == pytest.approx(0.01410114466278277)
    assert "- A (Kontrolle): 1,21 % bei 10'688 Versuchen (129 Erfolge), 95 %-KI 1,02\u20131,43 %." in out["summary"]
    assert out["table"]["headers"] == ["Variante", "n", "Wert", "95 %-KI", "Erfolge"]
    assert out["table"]["rows"][1] == ["B", "10'714", "1,63 %", "1,41\u20131,89 %", "175"]


def test_describe_target_headline_from_the_plan():
    out = E.run_builtin("builtin.describe", data("proportion", [agg(None, 8, 6)], variants=[],
                                                 params={"target": 0.8}, name="Aufgabenerfolg"))
    assert_contract(out)
    assert out["headline"] == "Aufgabenerfolg 75,0 % (95 %-KI 40,9\u201392,9 %) \u2013 Ziel 80,0 % nicht belegt."
    assert out["verdict"] == "inconclusive"
    assert out["values"]["target"] == 0.8 and out["values"]["target_met"] is None
    assert out["variants"][0]["variant"] is None
    assert "Messwerte ohne Variante" in out["summary"]


@pytest.mark.parametrize("direction,successes,verdict,word", [
    ("higher", 190, "better", "erreicht"),
    ("higher", 120, "worse", "verfehlt"),
    ("lower", 120, "better", "erreicht"),
    ("lower", 190, "worse", "verfehlt"),
    ("none", 190, "n/a", "nicht belegt"),
])
def test_describe_target_respects_direction(direction, successes, verdict, word):
    out = E.run_builtin("builtin.describe", data("proportion", [agg(None, 200, successes)], variants=[],
                                                 params={"target": 0.8}, direction=direction,
                                                 name="Aufgabenerfolg"))
    assert out["verdict"] == verdict
    assert out["headline"].endswith(f"Ziel 80,0 % {word}.")


def test_describe_target_needs_every_variant_on_one_side():
    both = [agg("A", 400, 360), agg("B", 400, 380)]
    out = E.run_builtin("builtin.describe", data("proportion", both, params={"target": 0.8}))
    assert out["verdict"] == "better"
    assert out["headline"] == "Klickrate: Ziel 80,0 % von allen Varianten erreicht."
    mixed = [agg("A", 400, 360), agg("B", 400, 300)]
    out = E.run_builtin("builtin.describe", data("proportion", mixed, params={"target": 0.8}))
    assert out["verdict"] == "inconclusive"
    assert out["headline"] == "Klickrate: Ziel 80,0 % nicht f\u00fcr alle Varianten belegt."


def test_describe_target_without_interval_is_inconclusive():
    out = E.run_builtin("builtin.describe", data("mean", [agg(None, 1, 5.0)], variants=[],
                                                 params={"target": 4.0}, name="SUS-Wert"))
    assert out["verdict"] == "inconclusive"
    assert any("kein Konfidenzintervall" in w for w in out["warnings"])


def test_describe_guardrail_violation_from_the_plan():
    out = E.run_builtin("builtin.describe", data("proportion", [agg("A", 100, 60), agg("B", 100, 78)],
                                                 guardrail={"op": "max", "value": 0.7},
                                                 direction="lower", name="Absprungrate"))
    assert_contract(out)
    assert out["verdict"] == "worse"
    assert out["warnings"] == ["Leitplanke verletzt: Variante B 78,0 % > 70,0 %."]
    assert out["values"]["guardrail_ok"] is False
    assert out["headline"] == "Leitplanke verletzt: Variante B 78,0 % > 70,0 %"
    assert "Leitplanke: h\u00f6chstens 70,0 %." in out["summary"]


def test_describe_guardrail_min_kept_and_violated():
    ok = E.run_builtin("builtin.describe", data("proportion", [agg("A", 100, 60), agg("B", 100, 78)],
                                                guardrail={"op": "min", "value": 0.5}))
    assert ok["verdict"] == "n/a" and ok["values"]["guardrail_ok"] is True and not ok["warnings"]
    low = E.run_builtin("builtin.describe", data("duration", [mean_agg("A", 30, 250.0, 20.0),
                                                              mean_agg("B", 30, 180.0, 20.0)],
                                                 guardrail={"op": "min", "value": 200.0}, unit="ms"))
    assert low["verdict"] == "worse"
    assert low["warnings"] == ["Leitplanke verletzt: Variante B 180,00 ms < 200,00 ms."]


def test_describe_guardrail_beats_target():
    out = E.run_builtin("builtin.describe", data("proportion", [agg(None, 400, 360)], variants=[],
                                                 params={"target": 0.8},
                                                 guardrail={"op": "max", "value": 0.85}))
    assert out["verdict"] == "worse"
    assert out["headline"].startswith("Leitplanke verletzt: Messwerte ohne Variante 90,0 %")


def test_describe_mean_count_ratio_and_levels():
    mean = E.run_builtin("builtin.describe", data("duration", [mean_agg("A", 200, 120.0, 15.0)],
                                                  variants=CONTROL_B[:1], unit="ms", name="Latenz"))
    entry = mean["variants"][0]
    assert entry["sd"] == pytest.approx(15.0)
    lo, hi = stats.t_interval(120.0, 225.0, 200)
    assert (entry["ci_low"], entry["ci_high"]) == pytest.approx((lo, hi))
    assert mean["headline"] == "Latenz 120,00 ms (95 %-KI 117,91\u2013122,09 ms), n = 200"

    count = E.run_builtin("builtin.describe", data("count", [agg(None, 1000, 120)], variants=[]))
    lo, hi = stats.poisson_interval(120, 1000)
    assert (count["variants"][0]["ci_low"], count["variants"][0]["ci_high"]) == pytest.approx((lo, hi))

    ratio = E.run_builtin("builtin.describe", data("ratio", [agg(None, 7, 700.0, denominator_sum=560.0),
                                                              agg("A", 3, 30.0, denominator_sum=0.0)],
                                                   variants=CONTROL_B[:1], unit="CHF"))
    by_key = {v["variant"]: v for v in ratio["variants"]}
    assert by_key[None]["value"] == pytest.approx(1.25) and by_key[None]["ci_low"] is None
    assert by_key["A"]["value"] is None
    assert "die Summe des Nenners ist 0" in ratio["summary"]

    levels = {"1": "sehr unzufrieden", "2": "unzufrieden", "3": "neutral", "4": "zufrieden",
              "5": "sehr zufrieden"}
    ordinal = E.run_builtin("builtin.describe", data(
        "ordinal", [agg(None, 10, 38.0, sum_sq=152.0, levels={"3": 2, "4": 6, "5": 2})], variants=[],
        definition={"levels": levels}, name="Zufriedenheit"))
    assert ordinal["variants"][0]["value"] == pytest.approx(3.8)
    assert ordinal["table"]["headers"] == ["Variante", "n", "Mittel", "95 %-KI", "Standardabweichung",
                                           "sehr unzufrieden", "unzufrieden", "neutral", "zufrieden",
                                           "sehr zufrieden"]
    assert ordinal["table"]["rows"][0][-5:] == ["0,0 % (0)", "0,0 % (0)", "20,0 % (2)", "60,0 % (6)",
                                                "20,0 % (2)"]

    categorical = E.run_builtin("builtin.describe", data(
        "categorical", [agg("A", 60, 0, levels={"1": 20, "2": 30, "0": 10})], variants=CONTROL_B[:1],
        definition={"levels": {"1": "Variante A", "2": "Variante B", "0": "Keine Pr\u00e4ferenz"}},
        direction="none", name="Bevorzugte Variante"))
    assert categorical["variants"][0]["value"] is None and categorical["variants"][0]["sum"] is None
    assert categorical["headline"] == "Bevorzugte Variante: am h\u00e4ufigsten \u00abVariante B\u00bb 50,0 %"
    assert categorical["table"]["headers"] == ["Variante", "n", "Variante A", "Variante B",
                                               "Keine Pr\u00e4ferenz"]
    assert categorical["table"]["rows"][0] == ["A", "60", "33,3 % (20)", "50,0 % (30)", "16,7 % (10)"]


def test_describe_without_data_never_fails():
    out = E.run_builtin("builtin.describe", data("proportion", []))
    assert_contract(out)
    assert out["verdict"] == "n/a" and out["headline"] == "Klickrate: noch keine Messwerte"
    assert "Noch keine Messwerte." in out["warnings"]
    assert [v["variant"] for v in out["variants"]] == ["A", "B"]
    assert all(v["n"] == 0 and v["value"] is None for v in out["variants"])


def test_describe_reports_variants_without_data_and_rows_without_variant():
    out = E.run_builtin("builtin.describe", data("proportion", [agg("A", 50, 5), agg(None, 20, 2)]))
    keys = [v["variant"] for v in out["variants"]]
    assert keys == ["A", "B", None]
    assert "- B (Variante B): noch keine Messwerte." in out["summary"]


# -- two_proportion ---------------------------------------------------------------


PLAN_AGGREGATES = [agg("A", 10688, 129), agg("B", 10714, 175)]


def test_two_proportion_headline_and_numbers():
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES,
                                                       definition={"decimals": 2}))
    assert_contract(out)
    assert out["verdict"] == "better"
    assert out["headline"] == "B: +0,43 Pp. (95 %-KI +0,11 bis +0,75), p = 0,008"
    (c,) = out["comparisons"]
    expected = stats.two_proportion_test(129, 10688, 175, 10714)
    assert c["estimate"] == pytest.approx(expected["diff"])
    assert (c["ci_low"], c["ci_high"]) == pytest.approx((expected["ci_low"], expected["ci_high"]))
    assert c["p_value"] == pytest.approx(expected["p_value"])
    assert c["relative"] == pytest.approx(expected["relative_lift"])
    assert (c["variant"], c["baseline"], c["unit"], c["label"], c["verdict"]) == \
        ("B", "A", "Pp.", "Differenz", "better")
    assert c["prob_better"] is None
    assert out["values"]["p_value"] == pytest.approx(expected["p_value"])
    assert "Ergebnis: B ist besser als A (alpha = 0,05)." in out["summary"]
    assert "+0,43 Pp. (95 %-KI +0,11 bis +0,75 Pp.), relativ +35,3 %, p = 0,008 \u2013 besser." in out["summary"]


def test_two_proportion_direction_lower_flips_the_verdict():
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES, direction="lower",
                                                       name="Absprungrate"))
    assert out["verdict"] == "worse" and out["comparisons"][0]["verdict"] == "worse"
    assert "Ergebnis: B ist schlechter als A" in out["summary"]
    fewer = [agg("A", 10714, 175), agg("B", 10688, 129)]
    out = E.run_builtin("builtin.two_proportion", data("proportion", fewer, direction="lower"))
    assert out["verdict"] == "better"


def test_two_proportion_direction_none_reports_numbers_without_verdict():
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES, direction="none"))
    assert out["verdict"] == "n/a"
    assert out["comparisons"][0]["verdict"] == "n/a"
    assert out["comparisons"][0]["p_value"] == pytest.approx(0.00839, abs=1e-4)
    assert "keine Richtung" in out["summary"]


def test_two_proportion_alpha_decides():
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES,
                                                       params={"alpha": 0.001}))
    assert out["verdict"] == "inconclusive"
    assert "99,9 %-KI" in out["headline"]
    assert out["values"]["alpha"] == 0.001


def test_two_proportion_multiple_variants_are_holm_corrected():
    three = [agg("A", 5000, 250), agg("B", 5000, 300), agg("C", 5000, 330), agg("D", 5000, 260)]
    variants = [{"key": k, "name": "", "is_control": k == "A"} for k in "ABCD"]
    out = E.run_builtin("builtin.two_proportion", data("proportion", three, variants=variants))
    assert_contract(out)
    assert "p-Werte nach Holm korrigiert." in out["warnings"]
    raw = [stats.two_proportion_test(250, 5000, s, 5000)["p_value"] for s in (300, 330, 260)]
    adjusted = stats.holm(raw)
    assert [c["p_value"] for c in out["comparisons"]] == pytest.approx(adjusted)
    assert [c["verdict"] for c in out["comparisons"]] == ["inconclusive", "better", "inconclusive"]
    assert out["verdict"] == "better"
    assert out["headline"].startswith("C: +1,60 Pp.")
    assert out["values"]["C.p_value"] == pytest.approx(adjusted[1])
    none = E.run_builtin("builtin.two_proportion", data("proportion", three, variants=variants,
                                                        params={"correction": "none"}))
    assert "p-Werte nach Holm korrigiert." not in none["warnings"]
    assert [c["p_value"] for c in none["comparisons"]] == pytest.approx(raw)
    assert none["comparisons"][0]["verdict"] == "better"


def test_two_proportion_headline_names_the_best_of_several():
    aggs = [agg("A", 5000, 250), agg("B", 5000, 330), agg("C", 5000, 400)]
    variants = [{"key": k, "name": "", "is_control": k == "A"} for k in "ABC"]
    out = E.run_builtin("builtin.two_proportion", data("proportion", aggs, variants=variants))
    assert [c["verdict"] for c in out["comparisons"]] == ["better", "better"]
    assert out["headline"].startswith("C:")


def test_two_proportion_control_is_the_is_control_variant_else_the_first():
    variants = [{"key": "B", "name": "", "is_control": False}, {"key": "A", "name": "", "is_control": True}]
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES, variants=variants))
    assert out["comparisons"][0]["baseline"] == "A" and out["comparisons"][0]["variant"] == "B"
    no_control = [{"key": "B", "name": ""}, {"key": "A", "name": ""}]
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES, variants=no_control))
    assert out["comparisons"][0]["baseline"] == "B"
    assert out["verdict"] == "worse"


def test_two_proportion_rows_without_variant_are_never_compared():
    aggs = PLAN_AGGREGATES + [agg(None, 99999, 50000)]
    out = E.run_builtin("builtin.two_proportion", data("proportion", aggs))
    assert [c["variant"] for c in out["comparisons"]] == ["B"]


@pytest.mark.parametrize("aggregates,variants,warning", [
    ([], None, "Die Kontrolle A hat noch keine Messwerte."),
    ([agg("A", 100, 10)], None, "Variante B hat noch keine Messwerte."),
    ([agg("B", 100, 10)], None, "Die Kontrolle A hat noch keine Messwerte."),
    ([agg(None, 100, 10)], [], "Es gibt keine zwei Varianten zum Vergleichen."),
    ([agg("A", 100, 10)], CONTROL_B[:1], "Es gibt keine zwei Varianten zum Vergleichen."),
])
def test_two_proportion_too_little_data_is_inconclusive(aggregates, variants, warning):
    out = E.run_builtin("builtin.two_proportion", data("proportion", aggregates, variants=variants))
    assert_contract(out)
    assert out["verdict"] == "inconclusive"
    assert warning in out["warnings"]
    assert out["headline"] == "Klickrate: zu wenig Daten f\u00fcr einen Vergleich"


def test_two_proportion_small_counts_warn_and_zero_variance_is_not_an_error():
    out = E.run_builtin("builtin.two_proportion", data("proportion", [agg("A", 10, 1), agg("B", 10, 3)]))
    assert any("grobe N\u00e4herung" in w for w in out["warnings"])
    flat = E.run_builtin("builtin.two_proportion", data("proportion", [agg("A", 10, 0), agg("B", 12, 0)]))
    assert flat["comparisons"][0]["p_value"] == 1.0 and flat["verdict"] == "inconclusive"


def test_two_proportion_contradictory_data_does_not_raise():
    out = E.run_builtin("builtin.two_proportion", data("proportion", [agg("A", 10, 12), agg("B", 10, 3)]))
    assert_contract(out)
    assert out["verdict"] == "inconclusive"
    assert any("widerspr\u00fcchlich" in w or "nicht rechnen" in w for w in out["warnings"])


# -- bayes_proportion -------------------------------------------------------------


def test_bayes_headline_from_the_plan():
    out = E.run_builtin("builtin.bayes_proportion", data("proportion", PLAN_AGGREGATES))
    assert_contract(out)
    assert out["headline"] == "P(B besser als A) = 99,6 %"
    assert out["verdict"] == "better"
    (c,) = out["comparisons"]
    assert c["prob_better"] == pytest.approx(0.9958, abs=0.01)
    assert c["p_value"] is None and c["unit"] == "Pp."
    assert c["estimate"] == pytest.approx(0.004263, abs=0.0005)
    assert out["values"]["prob_better"] == c["prob_better"]
    assert out["values"]["threshold"] == 0.95
    # Credible intervals per variant from the Beta posterior.
    a = out["variants"][0]
    assert a["ci_low"] == pytest.approx(stats.beta_ppf(0.025, 130, 10560))
    assert "Mit einer Wahrscheinlichkeit von 99,6 % ist B besser als A." in out["summary"]


def test_bayes_direction_lower_uses_the_probability_of_being_lower():
    out = E.run_builtin("builtin.bayes_proportion", data("proportion", PLAN_AGGREGATES, direction="lower",
                                                         name="Absprungrate"))
    assert out["verdict"] == "worse"
    assert out["headline"] == "P(B besser als A) = 0,4 %"
    assert out["comparisons"][0]["prob_better"] == pytest.approx(0.0042, abs=0.01)


def test_bayes_direction_none_and_threshold():
    out = E.run_builtin("builtin.bayes_proportion", data("proportion", PLAN_AGGREGATES, direction="none"))
    assert out["verdict"] == "n/a" and out["headline"] == "P(B h\u00f6her als A) = 99,6 %"
    strict = E.run_builtin("builtin.bayes_proportion", data("proportion", PLAN_AGGREGATES,
                                                            params={"threshold": 0.999}))
    assert strict["verdict"] == "inconclusive"
    assert "Keine Variante erreicht die Schwelle von 99,9 %" in strict["summary"]


def test_bayes_prior_changes_the_result_and_too_little_data():
    weak = E.run_builtin("builtin.bayes_proportion", data("proportion", [agg("A", 5, 1), agg("B", 5, 3)]))
    strong = E.run_builtin("builtin.bayes_proportion", data("proportion", [agg("A", 5, 1), agg("B", 5, 3)],
                                                            params={"prior_a": 500, "prior_b": 500}))
    assert strong["comparisons"][0]["prob_better"] < weak["comparisons"][0]["prob_better"]
    empty = E.run_builtin("builtin.bayes_proportion", data("proportion", [agg("A", 5, 1)]))
    assert empty["verdict"] == "inconclusive" and empty["comparisons"] == []


# -- welch_t ----------------------------------------------------------------------


LATENCY = [mean_agg("A", 200, 120.0, 15.0), mean_agg("B", 180, 112.0, 14.0)]


def test_welch_t_matches_stats_and_respects_direction():
    out = E.run_builtin("builtin.welch_t", data("duration", LATENCY, unit="ms", direction="lower",
                                                name="Latenz"))
    assert_contract(out)
    expected = stats.welch_t_test(120.0, 225.0, 200, 112.0, 196.0, 180)
    (c,) = out["comparisons"]
    assert c["estimate"] == pytest.approx(-8.0)
    assert c["p_value"] == pytest.approx(expected["p_value"])
    assert (c["ci_low"], c["ci_high"]) == pytest.approx((expected["ci_low"], expected["ci_high"]))
    assert c["unit"] == "ms" and c["relative"] == pytest.approx(-8.0 / 120.0)
    assert out["verdict"] == "better"
    assert out["headline"] == "B: -8,00 ms (95 %-KI -10,93 bis -5,07), p < 0,001"
    higher = E.run_builtin("builtin.welch_t", data("duration", LATENCY, unit="ms"))
    assert higher["verdict"] == "worse"


@pytest.mark.parametrize("aggregates,fragment", [
    ([agg("A", 200, 24000.0), mean_agg("B", 180, 112.0, 14.0)], "die Quadratsumme fehlt"),
    ([agg("A", 1, 120.0, sum_sq=14400.0), mean_agg("B", 180, 112.0, 14.0)], "weniger als 2 Werte"),
    ([agg("A", 10, 100.0, sum_sq=1000.0), agg("B", 10, 200.0, sum_sq=4000.0)], "keine Streuung"),
])
def test_welch_t_without_variance_is_inconclusive(aggregates, fragment):
    out = E.run_builtin("builtin.welch_t", data("mean", aggregates))
    assert_contract(out)
    assert out["verdict"] == "inconclusive"
    assert any(fragment in w for w in out["warnings"]), out["warnings"]


def test_welch_t_on_an_ordinal_scale():
    a = agg("A", 10, 30.0, sum_sq=100.0)
    b = agg("B", 10, 42.0, sum_sq=184.0)
    out = E.run_builtin("builtin.welch_t", data("ordinal", [a, b], name="Zufriedenheit"))
    assert out["comparisons"][0]["estimate"] == pytest.approx(1.2)


# -- paired_t ---------------------------------------------------------------------


def _row(variant, value, query=None, count=1, **dims):
    if query is not None:
        dims["query"] = query
    return {"variant": variant, "run": None, "value": value, "count": count, "denominator": None,
            "sum_sq": None, "observed_at": "2026-09-16T00:00:00+00:00", "dims": dims}


ENG_VARIANTS = [{"key": "baseline", "name": "Ausgangsstand", "is_control": True},
                {"key": "candidate", "name": "Kandidat", "is_control": False}]


def _paired_rows(n=30, shift=0.02, seed=4):
    rng = random.Random(seed)
    rows, diffs = [], []
    for q in range(n):
        base = rng.random()
        new = base + shift + rng.gauss(0, 0.03)
        rows += [_row("baseline", base, f"q{q}"), _row("candidate", new, f"q{q}")]
        diffs.append(new - base)
    return rows, diffs


def test_paired_t_pairs_by_query_and_matches_stats():
    rows, diffs = _paired_rows()
    aggs = [agg("baseline", 30, 15.0), agg("candidate", 30, 16.0)]
    out = E.run_builtin("builtin.paired_t", data("mean", aggs, variants=ENG_VARIANTS, rows=rows,
                                                 definition={"decimals": 3}, name="NDCG@10"))
    assert_contract(out)
    expected = stats.paired_t_test(diffs)
    (c,) = out["comparisons"]
    assert c["estimate"] == pytest.approx(expected["mean_diff"])
    assert c["p_value"] == pytest.approx(expected["p_value"])
    assert c["label"] == "Mittlere Differenz"
    assert out["verdict"] == "better"
    assert out["values"]["n_pairs"] == 30 and out["values"]["pair_by"] == "query"
    assert out["headline"].endswith(", 30 Paare")
    assert out["headline"].startswith("candidate: +0,0")


def test_paired_t_averages_rows_per_key_and_reports_unpaired_rows():
    rows, _ = _paired_rows(n=5)
    rows += [_row("candidate", 0.9, "q0"), _row("candidate", 0.5, "only-candidate"),
             _row("baseline", 0.5, "only-baseline"), _row("candidate", 0.5), _row(None, 0.3, "q1")]
    aggs = [agg("baseline", 6, 3.0), agg("candidate", 8, 4.0)]
    out = E.run_builtin("builtin.paired_t", data("mean", aggs, variants=ENG_VARIANTS, rows=rows))
    assert "2 Zeilen ohne Partner ignoriert." in out["warnings"]
    assert "1 Zeile ohne Merkmal \u00abquery\u00bb ignoriert." in out["warnings"]
    assert out["values"]["n_pairs"] == 5
    by_key = {}
    for r in rows:
        if r["variant"] and r["dims"].get("query", "").startswith("q"):
            by_key.setdefault(r["dims"]["query"], {}).setdefault(r["variant"], []).append(r["value"])
    diffs = [sum(v["candidate"]) / len(v["candidate"]) - sum(v["baseline"]) / len(v["baseline"])
             for v in by_key.values()]
    assert out["comparisons"][0]["estimate"] == pytest.approx(sum(diffs) / len(diffs))


def test_paired_t_uses_counts_and_the_pair_by_param():
    rows = [
        _row("baseline", 2.0, count=2, doc="d1"), _row("candidate", 3.0, count=1, doc="d1"),
        _row("baseline", 1.0, count=1, doc="d2"), _row("candidate", 4.0, count=2, doc="d2"),
        _row("baseline", 5.0, count=1, doc="d3"), _row("candidate", 7.0, count=1, doc="d3"),
    ]
    aggs = [agg("baseline", 4, 8.0), agg("candidate", 4, 14.0)]
    out = E.run_builtin("builtin.paired_t", data("mean", aggs, variants=ENG_VARIANTS, rows=rows,
                                                 params={"pair_by": "doc"}))
    # Row means: d1 1.0 -> 3.0, d2 1.0 -> 2.0, d3 5.0 -> 7.0.
    assert out["comparisons"][0]["estimate"] == pytest.approx((2.0 + 1.0 + 2.0) / 3)
    assert out["values"]["pair_by"] == "doc"


def test_paired_t_too_few_pairs_and_truncated_rows():
    rows = [_row("baseline", 0.4, "q1"), _row("candidate", 0.5, "q1")]
    aggs = [agg("baseline", 1, 0.4), agg("candidate", 1, 0.5)]
    out = E.run_builtin("builtin.paired_t", data("mean", aggs, variants=ENG_VARIANTS, rows=rows,
                                                 rows_truncated=True))
    assert_contract(out)
    assert out["verdict"] == "inconclusive"
    assert out["headline"] == "Klickrate: zu wenig Daten f\u00fcr einen Vergleich"
    assert "Variante candidate: zu wenige Paare (1) f\u00fcr einen gepaarten Test." in out["warnings"]
    assert "Nur die neuesten Zeilen wurden ausgewertet (Zeilenlimit erreicht)." in out["warnings"]
    no_rows = E.run_builtin("builtin.paired_t", data("mean", aggs, variants=ENG_VARIANTS))
    assert any("keine Einzelwerte" in w for w in no_rows["warnings"])
    assert no_rows["verdict"] == "inconclusive"


def test_paired_t_identical_differences_are_inconclusive():
    rows = []
    for q in range(5):
        rows += [_row("baseline", 0.5, f"q{q}"), _row("candidate", 0.6, f"q{q}")]
    aggs = [agg("baseline", 5, 2.5), agg("candidate", 5, 3.0)]
    out = E.run_builtin("builtin.paired_t", data("mean", aggs, variants=ENG_VARIANTS, rows=rows))
    assert out["verdict"] == "inconclusive"
    assert any("alle Paardifferenzen sind gleich" in w for w in out["warnings"])


def test_paired_t_several_candidates_are_holm_corrected():
    rng = random.Random(9)
    rows = []
    for q in range(25):
        base = rng.random()
        rows += [_row("baseline", base, f"q{q}"), _row("c1", base + 0.05 + rng.gauss(0, 0.02), f"q{q}"),
                 _row("c2", base + rng.gauss(0, 0.02), f"q{q}")]
    variants = ENG_VARIANTS[:1] + [{"key": "c1", "name": ""}, {"key": "c2", "name": ""}]
    aggs = [agg("baseline", 25, 12.0), agg("c1", 25, 13.0), agg("c2", 25, 12.0)]
    out = E.run_builtin("builtin.paired_t", data("mean", aggs, variants=variants, rows=rows))
    assert "p-Werte nach Holm korrigiert." in out["warnings"]
    assert out["comparisons"][0]["verdict"] == "better"
    assert out["values"]["c1.n_pairs"] == 25 and out["values"]["c2.n_pairs"] == 25


# -- poisson_rate -----------------------------------------------------------------


def test_poisson_rate_reports_the_ratio():
    out = E.run_builtin("builtin.poisson_rate", data("count", [agg("A", 1000, 120), agg("B", 1000, 160)],
                                                     name="Supportf\u00e4lle"))
    assert_contract(out)
    expected = stats.poisson_rate_test(120, 1000, 160, 1000)
    (c,) = out["comparisons"]
    assert c["estimate"] == pytest.approx(4 / 3) and c["unit"] == "x"
    assert c["relative"] == pytest.approx(1 / 3)
    assert c["p_value"] == pytest.approx(expected["p_value"])
    assert out["verdict"] == "better"
    assert out["headline"] == "B: Rate \u00d7 1,33 gegen\u00fcber A (95 %-KI 1,05 bis 1,70), p = 0,020"
    assert "Rate \u00d7 1,33 (95 %-KI 1,05 bis 1,70), p = 0,020 \u2013 besser." in out["summary"]
    lower = E.run_builtin("builtin.poisson_rate", data("count", [agg("A", 1000, 120), agg("B", 1000, 160)],
                                                       direction="lower"))
    assert lower["verdict"] == "worse"


def test_poisson_rate_without_events_in_the_control():
    out = E.run_builtin("builtin.poisson_rate", data("count", [agg("A", 100, 0), agg("B", 100, 12)]))
    assert_contract(out)
    (c,) = out["comparisons"]
    assert c["estimate"] is None and c["ci_high"] is None
    assert out["verdict"] == "better"
    assert "unbegrenzt" in out["headline"]
    none = E.run_builtin("builtin.poisson_rate", data("count", [agg("A", 100, 0), agg("B", 100, 0)]))
    assert none["verdict"] == "inconclusive"
    assert "Keine Ereignisse in A und B." in none["warnings"]


# -- ratio_delta ------------------------------------------------------------------


def _ratio_rows(variant, nums, dens):
    return [{"variant": variant, "run": None, "value": n, "count": 1, "denominator": d,
             "sum_sq": None, "observed_at": None, "dims": {}} for n, d in zip(nums, dens)]


NUM1 = [100.0, 120.0, 90.0, 110.0, 105.0, 95.0, 130.0]
DEN1 = [80.0, 95.0, 70.0, 90.0, 85.0, 75.0, 100.0]
NUM2 = [90.0, 85.0, 100.0, 95.0, 80.0, 99.0]
DEN2 = [95.0, 90.0, 110.0, 100.0, 92.0, 101.0]


def test_ratio_delta_uses_the_rows():
    rows = _ratio_rows("A", NUM1, DEN1) + _ratio_rows("B", NUM2, DEN2)
    aggs = [agg("A", 7, sum(NUM1), denominator_sum=sum(DEN1), rows=7),
            agg("B", 6, sum(NUM2), denominator_sum=sum(DEN2), rows=6)]
    out = E.run_builtin("builtin.ratio_delta", data("ratio", aggs, rows=rows, unit="CHF",
                                                    direction="lower", name="Kosten pro Klick"))
    assert_contract(out)
    expected = stats.ratio_delta(NUM1, DEN1, NUM2, DEN2)
    (c,) = out["comparisons"]
    assert c["estimate"] == pytest.approx(expected["diff"])
    assert c["p_value"] == pytest.approx(expected["p_value"])
    assert c["unit"] == "CHF" and c["relative"] == pytest.approx(expected["diff"] / expected["ratio1"])
    assert out["verdict"] == "better"
    assert out["headline"].startswith("B: -0,")
    assert out["headline"].endswith("p < 0,001")


def test_ratio_delta_too_few_rows_and_missing_denominators():
    rows = _ratio_rows("A", [10.0], [5.0]) + _ratio_rows("B", [4.0, 5.0], [2.0, 2.0])
    rows.append({"variant": "B", "value": 3.0, "denominator": None, "count": 1, "dims": {}})
    aggs = [agg("A", 1, 10.0, denominator_sum=5.0), agg("B", 3, 12.0, denominator_sum=4.0)]
    out = E.run_builtin("builtin.ratio_delta", data("ratio", aggs, rows=rows))
    assert_contract(out)
    assert out["verdict"] == "inconclusive"
    assert "Variante A: weniger als 2 Zeilen; kein Test m\u00f6glich." in out["warnings"]
    assert "1 Zeile ohne g\u00fcltigen Nenner ignoriert." in out["warnings"]
    zero = _ratio_rows("A", [1.0, 2.0], [0.0, 0.0]) + _ratio_rows("B", [1.0, 2.0], [1.0, 1.0])
    out = E.run_builtin("builtin.ratio_delta", data("ratio", aggs, rows=zero))
    assert any("Summe des Nenners ist 0" in w for w in out["warnings"])


# -- chi_square -------------------------------------------------------------------


PREF_LEVELS = {"1": "Variante A", "2": "Variante B", "3": "Variante C", "0": "Keine Pr\u00e4ferenz"}


def test_chi_square_independence_for_categories_has_no_direction():
    aggs = [agg("A", 60, 0, levels={"1": 20, "2": 30, "0": 10}),
            agg("B", 60, 0, levels={"1": 30, "2": 15, "3": 5, "0": 10})]
    out = E.run_builtin("builtin.chi_square", data("categorical", aggs, definition={"levels": PREF_LEVELS},
                                                   name="Bevorzugte Variante"))
    assert_contract(out)
    expected = stats.chi_square_independence([[20, 30, 0, 10], [30, 15, 5, 10]])
    assert out["values"]["test"] == "independence"
    assert out["values"]["p_value"] == pytest.approx(expected["p_value"])
    assert out["values"]["cramers_v"] == pytest.approx(expected["cramers_v"])
    assert out["verdict"] == "n/a" and out["comparisons"][0]["verdict"] == "n/a"
    assert out["headline"] == "Verteilung unterscheidet sich zwischen den Varianten (\u03c7\u00b2 = 12,00, df = 3, p = 0,007)"
    assert any("erwarteten H\u00e4ufigkeiten" in w for w in out["warnings"])


def test_chi_square_ordinal_reports_the_mean_level_but_names_no_winner():
    levels = {"1": "a", "2": "b", "3": "c", "4": "d", "5": "e"}
    a = agg("A", 100, 1 * 10 + 2 * 20 + 3 * 40 + 4 * 20 + 5 * 10, levels={"1": 10, "2": 20, "3": 40, "4": 20, "5": 10})
    b = agg("B", 100, 1 * 2 + 2 * 8 + 3 * 30 + 4 * 35 + 5 * 25, levels={"1": 2, "2": 8, "3": 30, "4": 35, "5": 25})
    out = E.run_builtin("builtin.chi_square", data("ordinal", [a, b], definition={"levels": levels},
                                                   name="Zufriedenheit"))
    assert_contract(out)
    (c,) = out["comparisons"]
    assert c["estimate"] == pytest.approx(3.73 - 3.0)
    assert c["p_value"] == pytest.approx(stats.chi_square_independence(
        [[10, 20, 40, 20, 10], [2, 8, 30, 35, 25]])["p_value"])
    # The test is about the distribution, not a shift: no better/worse
    # (the mean level is builtin.welch_t's question).
    assert c["verdict"] == "n/a" and out["verdict"] == "n/a"
    assert out["headline"].startswith("Verteilung unterscheidet sich zwischen den Varianten")
    assert "- B gegen\u00fcber A: mittlere Stufe +0,73, p < 0,001." in out["summary"]
    assert "Welch-t-Test auf die mittlere Stufe" in out["summary"]
    lower = E.run_builtin("builtin.chi_square", data("ordinal", [a, b], direction="lower"))
    assert lower["verdict"] == "n/a"


def test_chi_square_goodness_of_fit_with_one_group():
    aggs = [agg(None, 25, 0, levels={"1": 12, "2": 8, "3": 5})]
    out = E.run_builtin("builtin.chi_square", data("categorical", aggs, variants=[],
                                                   definition={"levels": PREF_LEVELS}))
    assert_contract(out)
    expected = stats.chi_square_goodness_of_fit([12, 8, 5, 0])
    assert out["values"]["test"] == "goodness_of_fit"
    assert out["values"]["p_value"] == pytest.approx(expected["p_value"])
    assert out["verdict"] == "n/a"
    assert out["headline"].startswith("Verteilung weicht von gleichen Anteilen ab")
    weighted = E.run_builtin("builtin.chi_square", data("categorical", aggs, variants=[],
                                                        definition={"levels": PREF_LEVELS},
                                                        params={"expected": [3, 2, 1, 1]}))
    assert weighted["values"]["p_value"] == pytest.approx(
        stats.chi_square_goodness_of_fit([12, 8, 5, 0], [3, 2, 1, 1])["p_value"])
    assert "den erwarteten Anteilen" in weighted["headline"]


def test_chi_square_exact_binomial_for_two_categories_and_few_units():
    aggs = [agg("A", 20, 26.0, levels={"1": 14, "2": 6})]
    out = E.run_builtin("builtin.chi_square", data("ordinal", aggs, variants=CONTROL_B[:1]))
    assert out["values"]["test"] == "binomial"
    assert out["values"]["p_value"] == pytest.approx(stats.binomial_test(14, 20, 0.5)["p_value"])
    assert "exakter Binomialtest" in out["headline"]
    weighted = E.run_builtin("builtin.chi_square", data("ordinal", aggs, variants=CONTROL_B[:1],
                                                        params={"expected": [7, 3]}))
    assert weighted["values"]["p_value"] == pytest.approx(stats.binomial_test(14, 20, 0.7)["p_value"])


def test_chi_square_refuses_expected_of_the_wrong_length_and_handles_no_data():
    aggs = [agg(None, 25, 0, levels={"1": 12, "2": 8, "3": 5})]
    out = E.run_builtin("builtin.chi_square", data("categorical", aggs, variants=[],
                                                   params={"expected": [1, 1]}))
    assert out["verdict"] == "inconclusive"
    assert "Die erwarteten Anteile passen nicht zu den 3 Kategorien." in out["warnings"]
    empty = E.run_builtin("builtin.chi_square", data("categorical", []))
    assert_contract(empty)
    assert empty["verdict"] == "inconclusive"
    single = E.run_builtin("builtin.chi_square", data("categorical", [agg(None, 5, 0, levels={"1": 5})],
                                                      variants=[]))
    assert single["verdict"] == "inconclusive"


# -- robustness ---------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(EXPECTED_BUILTINS))
def test_wrong_kind_is_not_applicable(key):
    spec = E.BUILTINS[key]
    other = next(k for k in EXPECTED_BUILTINS["builtin.describe"][1] if k not in spec.input_kinds) \
        if len(spec.input_kinds) < 8 else None
    if other is None:
        out = E.run_builtin(key, data("unknown-kind", PLAN_AGGREGATES))
        assert out["verdict"] == "n/a"
        return
    out = E.run_builtin(key, data(other, PLAN_AGGREGATES))
    assert_contract(out)
    assert out["verdict"] == "n/a"
    assert out["warnings"] and "passt nicht zur Art der Metrik" in out["warnings"][0]


def _junk_aggregates(rng):
    values = [None, 0, -1, 1, 2.5, 10 ** 6, float("nan"), float("inf"), "x", True, [], {}]
    out = []
    for key in rng.sample(["A", "B", "C", None, 7, ""], rng.randint(0, 5)):
        out.append({"variant": key, "rows": rng.choice(values), "n": rng.choice(values),
                    "value_sum": rng.choice(values), "denominator_sum": rng.choice(values),
                    "sum_sq": rng.choice(values), "estimate": rng.choice(values),
                    "levels": rng.choice([None, "x", {"1": rng.choice(values), "2": 3}, {}])})
    out.append("not a dict")
    return out


def _junk_rows(rng):
    values = [None, 0, -1, 1.5, float("nan"), "x", 10 ** 20]
    return [{"variant": rng.choice(["A", "B", None, 3]), "value": rng.choice(values),
             "count": rng.choice(values), "denominator": rng.choice(values),
             "dims": rng.choice([None, {}, {"query": rng.choice(["q1", "q2", None, 5])}, "x"])}
            for _ in range(rng.randint(0, 12))] + ["junk"]


@pytest.mark.parametrize("key", sorted(EXPECTED_BUILTINS))
def test_builtins_never_raise_on_malformed_or_tiny_inputs(key):
    rng = random.Random(hash(key) & 0xFFFF)
    kinds = E.BUILTINS[key].input_kinds
    for _ in range(150):
        payload = data(rng.choice(kinds), _junk_aggregates(rng), rows=_junk_rows(rng),
                       direction=rng.choice(["higher", "lower", "none", "sideways", None]),
                       variants=rng.choice([None, [], [{"key": "B"}, {"key": "A", "is_control": True}],
                                            ["junk", {"name": "no key"}]]),
                       guardrail=rng.choice([None, {"op": "max", "value": 0.5}, {"op": "x"}]),
                       definition=rng.choice([None, {"decimals": 9}, {"levels": {"1": "a"}},
                                              {"decimals": True}]))
        payload["metric"]["unit"] = rng.choice(["", "ms", None])
        out = E.run_builtin(key, payload)
        assert_contract(out)


def test_builtins_survive_magnitudes_near_the_float_limit():
    import time

    rng = random.Random(1)
    extreme = [0.0, 1e-300, 1.0, 1e12, 1e150, 1e300, 1.7e308, -1e300, 2 ** 60]
    started = time.perf_counter()
    for key, spec in sorted(E.BUILTINS.items()):
        for _ in range(25):
            aggregates = [{"variant": v, "rows": rng.choice([1, 10 ** 6]),
                           "n": rng.choice([1, 2, 1e6, 1e12, 2 ** 53, 1e300]),
                           "value_sum": rng.choice(extreme), "denominator_sum": rng.choice(extreme),
                           "sum_sq": rng.choice(extreme), "estimate": None,
                           "levels": {"1": rng.choice(extreme), "2": rng.choice(extreme)}}
                          for v in ("A", "B", None)]
            rows = [{"variant": rng.choice("AB"), "value": rng.choice(extreme),
                     "count": rng.choice([1, 1e300]), "denominator": rng.choice(extreme),
                     "dims": {"query": rng.choice(["q1", "q2"])}} for _ in range(8)]
            payload = data(rng.choice(spec.input_kinds), aggregates, rows=rows,
                           direction=rng.choice(["higher", "lower"]),
                           guardrail={"op": "max", "value": rng.choice(extreme)})
            assert_contract(E.run_builtin(key, payload))
    assert time.perf_counter() - started < 60


def test_run_builtin_turns_numeric_failures_into_an_inconclusive_result(monkeypatch, caplog):
    import dataclasses

    def broken(data):
        raise OverflowError("math range error")

    spec = dataclasses.replace(E.BUILTINS["builtin.welch_t"], fn=broken)
    monkeypatch.setitem(E.BUILTINS, "builtin.welch_t", spec)
    out = E.run_builtin("builtin.welch_t", data("mean", LATENCY))
    assert_contract(out)
    assert out["verdict"] == "inconclusive"
    assert out["warnings"] == ["Die Auswertung l\u00e4sst sich mit diesen Daten nicht rechnen."]
    assert out["headline"] == "Welch-t-Test: nicht berechenbar"
    assert "math range error" in caplog.text and "math range error" not in json.dumps(out)


def test_malformed_input_containers_do_not_raise():
    for payload in ({}, {"metric": None, "variants": None, "aggregates": None},
                    {"metric": {"kind": "proportion"}, "aggregates": [None, 1, "x"]}):
        for key in E.BUILTINS:
            assert_contract(E.run_builtin(key, payload))


def test_user_text_cannot_inject_markdown_structure():
    variants = [{"key": "A", "name": "[klick](https://evil.example)", "is_control": True},
                {"key": "B", "name": "x\n# \u00dcberschrift"}]
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES, variants=variants,
                                                       name="Rate\n## Kopf"))
    assert "](" not in out["summary"]
    assert not any(line.startswith("#") for line in out["summary"].split("\n"))


def test_outputs_are_german_and_name_the_numbers():
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES))
    for fragment in ("Zwei-Anteile-Test", "\u00abKlickrate\u00bb", "10'688", "p = 0,008", "Ergebnis:"):
        assert fragment in out["summary"]
    for key in E.BUILTINS:
        text = json.dumps(E.run_builtin(key, data("proportion", [])), ensure_ascii=False)
        assert "Traceback" not in text and "Error" not in text


# -- sanitize_output --------------------------------------------------------------


def test_sanitize_refuses_non_objects():
    for raw in (None, [], "text", 3):
        with pytest.raises(ValidationError) as excinfo:
            E.sanitize_output(raw)
        assert excinfo.value.message == "Der Auswerter hat kein Objekt zur\u00fcckgegeben."


def test_sanitize_moves_unknown_scalars_and_drops_the_rest():
    out = E.sanitize_output({"verdict": "better", "headline": "H", "a": 1, "b": "x", "c": [1, 2],
                             "d": {"x": 1}, "values": {"k": 2}})
    assert_contract(out)
    assert out["values"] == {"k": 2, "a": 1, "b": "x"}
    assert "Nicht vorgesehene Felder nach values verschoben: a, b" in out["warnings"]
    assert "Nicht \u00fcbernommen: c, d" in out["warnings"]


def test_sanitize_defaults_and_headline_fallback():
    out = E.sanitize_output({"values": {"x": 1, "y": 2}}, evaluator_name="Bootstrap")
    assert_contract(out)
    assert out["headline"] == "Bootstrap: 2 Werte"
    assert out["verdict"] == "n/a" and out["summary"] == "" and out["comparisons"] == []
    assert out["table"] == {"headers": [], "rows": []} and out["warnings"] == []
    assert E.sanitize_output({})["headline"] == "Auswertung: 0 Werte"
    assert E.sanitize_output({"headline": "   "}, evaluator_name="X")["headline"] == "X: 0 Werte"


@pytest.mark.parametrize("verdict,expected", [
    ("better", "better"), ("WORSE", "worse"), (" inconclusive ", "inconclusive"), ("n/a", "n/a"),
    ("great", "n/a"), (None, "n/a"), (1, "n/a"),
])
def test_sanitize_normalises_verdicts(verdict, expected):
    out = E.sanitize_output({"verdict": verdict, "comparisons": [{"verdict": verdict}]})
    assert out["verdict"] == expected and out["comparisons"][0]["verdict"] == expected


def test_sanitize_enforces_the_limits():
    raw = {
        "headline": "H" * 500,
        "summary": "S" * 30000,
        "comparisons": [{"variant": "B", "estimate": 1.0}] * 60,
        "variants": [{"variant": "A", "n": 3.0}] * 70,
        "values": {f"k{i}": i for i in range(150)},
        "table": {"headers": [f"h{i}" for i in range(30)],
                  "rows": [["x" * 500] + [f"c{j}" for j in range(29)]]
                  + [[f"c{j}" for j in range(30)] for _ in range(249)]},
        "warnings": [f"w{i}" * 200 for i in range(40)],
    }
    out = E.sanitize_output(raw)
    assert_contract(out)
    assert len(out["headline"]) == 200 and out["headline"].endswith("\u2026")
    assert len(out["summary"]) == 20000
    assert len(out["comparisons"]) == 50 and len(out["variants"]) == 50
    assert out["variants"][0]["n"] == 3 and isinstance(out["variants"][0]["n"], int)
    assert len(out["values"]) == 100
    assert len(out["table"]["headers"]) == 20
    assert len(out["table"]["rows"]) == 200 and all(len(r) == 20 for r in out["table"]["rows"])
    assert len(out["table"]["rows"][0][0]) == 200 and out["table"]["rows"][0][0].endswith("\u2026")
    assert len(out["warnings"]) == 20 and all(len(w) <= 300 for w in out["warnings"])
    # Our own notes survive the warning limit.
    assert any(w.startswith("Nicht \u00fcbernommen: values.") for w in out["warnings"])
    assert "Nur die ersten 50 Vergleiche \u00fcbernommen." in out["warnings"]


def test_sanitize_value_types():
    out = E.sanitize_output({"values": {"n": 3, "f": 1.5, "s": "x" * 900, "b": True, "none": None,
                                        "nan": float("nan"), "inf": float("-inf"), "big": 10 ** 400,
                                        "list": [1], "obj": {"a": 1}}})
    values = out["values"]
    assert values["n"] == 3 and values["f"] == 1.5 and values["b"] is True and values["none"] is None
    assert len(values["s"]) == 500
    assert values["nan"] is None and values["inf"] is None and values["big"] is None
    assert "list" not in values and "obj" not in values
    assert "Nicht \u00fcbernommen: values.list, values.obj" in out["warnings"]


def test_sanitize_numbers_in_comparisons_and_variants():
    out = E.sanitize_output({
        "comparisons": [{"variant": "B", "baseline": "A", "estimate": float("nan"), "ci_low": "1",
                         "ci_high": True, "p_value": 0.01, "prob_better": float("inf"),
                         "relative": 2, "unit": "Pp." * 20, "label": "L" * 200, "extra": 1},
                        "junk"],
        "variants": [{"variant": None, "n": float("inf"), "value": 1e308 * 10, "sd": 2.0, "x": 1}],
    })
    c = out["comparisons"][0]
    assert c["estimate"] is None and c["ci_low"] is None and c["ci_high"] is None
    assert c["prob_better"] is None and c["p_value"] == 0.01 and c["relative"] == 2
    assert len(c["unit"]) <= 20 and len(c["label"]) <= 80
    v = out["variants"][0]
    assert v["variant"] is None and v["n"] is None and v["value"] is None and v["sd"] == 2.0
    assert any("comparisons.extra" in w and "variants.x" in w and "comparisons[]" in w
               for w in out["warnings"])


def test_sanitize_table_cells_are_strings():
    out = E.sanitize_output({"table": {"headers": ["a", 1, None], "rows": [[1, 2.5, None, True,
                                                                            float("nan"), {"x": 1}],
                                                                           "not a row"]}})
    assert out["table"]["headers"] == ["a", "1", ""]
    assert out["table"]["rows"] == [["1", "2.5", "", "True", "", '{"x": 1}']]
    assert E.sanitize_output({"table": "x"})["table"] == {"headers": [], "rows": []}


def test_sanitize_strips_characters_postgres_and_the_page_refuse():
    raw = {"headline": "a\x00b\nc\td", "summary": "x\x00y\nz\ud800w\x07",
           "warnings": ["w\x00"], "values": {"k\x00": "v\ud83d"}}
    out = E.sanitize_output(raw)
    assert out["headline"] == "ab c d"
    assert out["summary"] == "xy\nzw"
    assert out["warnings"] == ["w"]
    assert out["values"] == {"k": "v"}
    json.dumps(out, ensure_ascii=False).encode("utf-8")


def test_sanitize_keeps_the_total_below_256_kb_by_clipping_the_table_first():
    raw = {
        "summary": "\u00fc" * 20000,
        "table": {"headers": ["h"] * 20, "rows": [["\u20ac" * 200] * 20 for _ in range(200)]},
        "values": {f"v{i}": "\u00f6" * 100 for i in range(20)},
    }
    out = E.sanitize_output(raw)
    assert len(json.dumps(out)) <= 256 * 1024
    assert "Ausgabe gek\u00fcrzt." in out["warnings"]
    assert 0 < len(out["table"]["rows"]) < 200
    assert out["summary"] == "\u00fc" * 20000 and len(out["values"]) == 20


def test_sanitize_keeps_the_total_below_256_kb_when_everything_is_large():
    raw = {
        "summary": "\u00fc" * 20000,
        "table": {"headers": ["h"] * 20, "rows": [["\u20ac" * 200] * 20 for _ in range(200)]},
        "values": {f"v{i}": "\u00f6" * 500 for i in range(100)},
        "comparisons": [{"variant": "\u00e4" * 100, "baseline": "\u00e4" * 100,
                         "label": "\u00e4" * 80}] * 50,
    }
    out = E.sanitize_output(raw)
    assert_contract(out)
    assert "Ausgabe gek\u00fcrzt." in out["warnings"]
    assert out["table"]["rows"] == []


def test_sanitize_stays_cheap_on_pathological_outputs():
    import time

    raw = {f"k{i}": [i] for i in range(200_000)}
    raw["values"] = {f"v{i}": i for i in range(50_000)}
    raw["warnings"] = ["w"] * 1_000_000
    started = time.perf_counter()
    out = E.sanitize_output(raw)
    assert time.perf_counter() - started < 5.0
    assert_contract(out)
    note = next(w for w in out["warnings"] if w.startswith("Nicht \u00fcbernommen: "))
    assert len(note) <= 300


def test_sanitize_is_idempotent_and_does_not_mutate():
    raw = {"verdict": "better", "headline": "x", "extra": 5, "values": {"a": float("nan")},
           "comparisons": [{"variant": "B", "estimate": 0.1}]}
    before = copy.deepcopy(raw)
    once = E.sanitize_output(raw)
    assert raw.keys() == before.keys() and raw["comparisons"] == before["comparisons"]
    twice = E.sanitize_output(once)
    assert twice == once


def test_builtin_outputs_pass_through_sanitize_unchanged():
    out = E.run_builtin("builtin.two_proportion", data("proportion", PLAN_AGGREGATES))
    assert E.sanitize_output(out, evaluator_name="x") == out
