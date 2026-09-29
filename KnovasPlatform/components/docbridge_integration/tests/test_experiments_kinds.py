"""Measurement kinds: row validation, estimates, level keys and formatting.

The rows validated here go straight into exp_measurements; a row that slipped
through with the wrong shape would skew every later estimate, so the tests
cover each refusal and its German message.
"""

from __future__ import annotations

import dataclasses
import json
import math

import pytest

from experiments import kinds
from experiments.errors import ValidationError
from experiments.kinds import (
    KINDS,
    estimate,
    format_diff,
    format_number,
    format_plain,
    format_value,
    level_key,
    validate_row,
)

DASH = "\u2013"


def _refused(kind, row, definition=None):
    with pytest.raises(ValidationError) as info:
        validate_row(kind, row, definition)
    return info.value


# -- KINDS ----------------------------------------------------------------------


def test_kinds_are_the_eight_the_database_allows_in_its_order():
    assert list(KINDS) == [
        "proportion", "mean", "count", "duration", "currency", "ratio", "ordinal", "categorical",
    ]
    for key, spec in KINDS.items():
        assert spec.key == key


@pytest.mark.parametrize("key, label, value_label, count_label", [
    ("proportion", "Anteil", "Erfolge", "Versuche"),
    ("mean", "Mittelwert", "Summe der Werte", "Anzahl"),
    ("count", "Rate", "Ereignisse", "Einheiten"),
    ("duration", "Dauer", "Summe der Werte", "Anzahl"),
    ("currency", "Geldbetrag", "Summe der Werte", "Anzahl"),
    ("ratio", "Verh\u00e4ltnis", "Z\u00e4hler", "Einheiten"),
    ("ordinal", "Skala", "Stufe", "Anzahl"),
    ("categorical", "Kategorie", "Kategorie", "Anzahl"),
])
def test_kind_labels_follow_the_contract(key, label, value_label, count_label):
    spec = KINDS[key]
    assert (spec.label, spec.value_label, spec.count_label) == (label, value_label, count_label)
    assert spec.description


def test_denominator_and_sum_sq_labels_only_where_the_kind_uses_the_column():
    for key, spec in KINDS.items():
        assert spec.needs_denominator == (key == "ratio")
        assert spec.denominator_label == ("Nenner" if key == "ratio" else "")
        expected = "Quadratsumme (optional)" if key in ("mean", "duration", "currency") else ""
        assert spec.sum_sq_label == expected


def test_distribution_and_integral_flags():
    assert {k for k, s in KINDS.items() if s.is_distribution} == {"ordinal", "categorical"}
    assert {k for k, s in KINDS.items() if s.integral_value} == {"proportion", "count", "categorical"}


def test_every_kind_starts_with_describe_and_names_only_builtins():
    for spec in KINDS.values():
        assert spec.default_evaluators[0] == "builtin.describe"
        assert all(e.startswith("builtin.") for e in spec.default_evaluators)


def test_kind_spec_is_frozen_and_as_dict_is_json_ready():
    spec = KINDS["ratio"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.label = "x"  # type: ignore[misc]
    data = spec.as_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["default_evaluators"] == list(spec.default_evaluators)
    assert set(data) == {f.name for f in dataclasses.fields(kinds.KindSpec)}


# -- validate_row: common -------------------------------------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), None, "3", True,
                                   [], {}, 10 ** 400])
def test_value_must_be_a_finite_number(value):
    error = _refused("mean", {"value": value})
    assert error.message == "Der Wert muss eine endliche Zahl sein."
    assert error.fields == {"value": "Der Wert muss eine endliche Zahl sein."}


def test_missing_value_is_refused():
    assert _refused("mean", {}).message == "Der Wert muss eine endliche Zahl sein."


def test_row_must_be_an_object():
    assert _refused("mean", [1, 2]).message == "Ein Messwert muss ein Objekt sein."


def test_unknown_kind_is_refused():
    assert "Unbekannte Messart" in _refused("quantile", {"value": 1}).message


def test_count_defaults_to_one_and_comes_back_as_int():
    row = validate_row("mean", {"value": 2})
    assert row == {"value": 2.0, "count": 1, "denominator": None, "sum_sq": 4.0}
    assert isinstance(row["count"], int) and isinstance(row["value"], float)
    assert validate_row("mean", {"value": 2, "count": 3.0})["count"] == 3


@pytest.mark.parametrize("count", [0, -1, 2.5, "3", True, float("nan"), float("inf")])
def test_count_must_be_a_whole_number_from_one(count):
    error = _refused("proportion", {"value": 0, "count": count})
    assert error.message == "\u00abVersuche\u00bb muss eine ganze Zahl ab 1 sein."
    assert error.fields == {"count": error.message}


def test_count_message_names_the_kinds_count_label():
    assert _refused("count", {"value": 1, "count": 0}).message == (
        "\u00abEinheiten\u00bb muss eine ganze Zahl ab 1 sein."
    )


def test_count_beyond_exact_json_integers_is_refused():
    error = _refused("mean", {"value": 1, "count": 2.0 ** 60})
    assert error.message == "\u00abAnzahl\u00bb ist zu gross."
    assert validate_row("mean", {"value": 1, "count": kinds.MAX_COUNT})["count"] == kinds.MAX_COUNT


# -- proportion -----------------------------------------------------------------


def test_proportion_row():
    assert validate_row("proportion", {"value": 11, "count": 900}) == {
        "value": 11.0, "count": 900, "denominator": None, "sum_sq": None,
    }


def test_proportion_drops_columns_it_does_not_use():
    row = validate_row("proportion", {"value": 1, "count": 2, "denominator": 5, "sum_sq": 9})
    assert row["denominator"] is None and row["sum_sq"] is None


def test_proportion_successes_must_be_whole():
    assert _refused("proportion", {"value": 2.5, "count": 10}).message == (
        "Erfolge m\u00fcssen eine ganze Zahl sein."
    )


def test_proportion_successes_not_negative():
    assert _refused("proportion", {"value": -1, "count": 10}).message == (
        "Erfolge d\u00fcrfen nicht negativ sein."
    )


def test_proportion_successes_not_above_trials():
    assert _refused("proportion", {"value": 11, "count": 10}).message == (
        "Es kann nicht mehr Erfolge als Versuche geben."
    )
    # Without a count there is one trial.
    assert _refused("proportion", {"value": 2}).fields == {
        "value": "Es kann nicht mehr Erfolge als Versuche geben."
    }
    assert validate_row("proportion", {"value": 10, "count": 10})["value"] == 10.0


# -- mean-like --------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["mean", "duration", "currency"])
def test_single_observation_gets_its_square(kind):
    assert validate_row(kind, {"value": -3.5})["sum_sq"] == 12.25


def test_single_observation_sum_sq_must_match():
    assert validate_row("mean", {"value": 3, "sum_sq": 9.0000000001})["sum_sq"] == 9.0
    assert _refused("mean", {"value": 3, "sum_sq": 10}).message == (
        "Die Quadratsumme passt nicht zum Wert."
    )


def test_aggregated_row_sum_sq_is_optional():
    assert validate_row("duration", {"value": 30, "count": 3})["sum_sq"] is None


def test_aggregated_row_sum_sq_must_not_imply_negative_variance():
    # Three observations summing to 30 have a sum of squares of at least 300.
    assert validate_row("mean", {"value": 30, "count": 3, "sum_sq": 350})["sum_sq"] == 350.0
    assert validate_row("mean", {"value": 30, "count": 3, "sum_sq": 300})["sum_sq"] == 300.0
    error = _refused("mean", {"value": 30, "count": 3, "sum_sq": 299})
    assert error.message == "Die Quadratsumme passt nicht zum Wert."
    assert error.fields == {"sum_sq": error.message}


def test_rounding_just_below_the_floor_is_clamped_not_refused():
    value, count = 0.1 + 0.2, 3
    floor = value * value / count
    row = validate_row("mean", {"value": value, "count": count, "sum_sq": floor * (1 - 1e-12)})
    assert row["sum_sq"] == floor


@pytest.mark.parametrize("sum_sq", [-1, float("nan"), float("inf"), "9", True])
def test_sum_sq_must_be_finite_and_not_negative(sum_sq):
    assert _refused("mean", {"value": 3, "count": 2, "sum_sq": sum_sq}).message == (
        "Die Quadratsumme muss eine endliche Zahl ab 0 sein."
    )


def test_value_whose_square_overflows_is_refused():
    assert _refused("mean", {"value": 1e200}).message == "Der Wert ist zu gross."


def test_mean_like_may_be_negative_without_bounds():
    assert validate_row("currency", {"value": -500})["value"] == -500.0


def test_bounds_apply_to_the_mean_of_an_aggregated_row():
    definition = {"min": 0, "max": 1}
    assert validate_row("mean", {"value": 0.73}, definition)["value"] == 0.73
    assert validate_row("mean", {"value": 1.5, "count": 2}, definition)["value"] == 1.5
    error = _refused("mean", {"value": 1.5}, definition)
    assert error.message == "Der Wert liegt ausserhalb von 0\u20131."
    assert error.fields == {"value": error.message}


def test_bounds_messages_one_sided_and_decimal_comma():
    assert _refused("mean", {"value": -1}, {"min": 0.5}).message == (
        "Der Wert liegt unter dem Minimum 0,5."
    )
    assert _refused("duration", {"value": 2000}, {"max": 1000}).message == (
        "Der Wert liegt \u00fcber dem Maximum 1'000."
    )


def test_bounds_are_inclusive():
    definition = {"min": 0, "max": 100}
    assert validate_row("mean", {"value": 0}, definition)["value"] == 0.0
    assert validate_row("mean", {"value": 100}, definition)["value"] == 100.0


# -- count ------------------------------------------------------------------------


def test_count_kind_row():
    assert validate_row("count", {"value": 3, "count": 1000}) == {
        "value": 3.0, "count": 1000, "denominator": None, "sum_sq": None,
    }


@pytest.mark.parametrize("value", [2.5, -1])
def test_count_events_must_be_whole_and_not_negative(value):
    assert _refused("count", {"value": value, "count": 10}).message == (
        "Ereignisse m\u00fcssen eine ganze Zahl sein."
    )


# -- ratio ----------------------------------------------------------------------


def test_ratio_row_keeps_the_denominator():
    assert validate_row("ratio", {"value": 120.5, "denominator": 48}) == {
        "value": 120.5, "count": 1, "denominator": 48.0, "sum_sq": None,
    }


def test_ratio_allows_zero_denominator_and_normalises_negative_zero():
    row = validate_row("ratio", {"value": 80, "denominator": -0.0})
    assert row["denominator"] == 0.0 and math.copysign(1, row["denominator"]) == 1


def test_ratio_denominator_missing_negative_or_not_finite():
    assert _refused("ratio", {"value": 1}).message == "Der Nenner fehlt."
    assert _refused("ratio", {"value": 1}).fields == {"denominator": "Der Nenner fehlt."}
    assert _refused("ratio", {"value": 1, "denominator": -2}).message == (
        "Der Nenner darf nicht negativ sein."
    )
    assert _refused("ratio", {"value": 1, "denominator": float("inf")}).message == (
        "Der Nenner muss eine endliche Zahl sein."
    )


def test_ratio_ignores_sum_sq_and_accepts_negative_numerators():
    row = validate_row("ratio", {"value": -5, "denominator": 2, "sum_sq": 25})
    assert row["value"] == -5.0 and row["sum_sq"] is None


# -- ordinal and categorical ------------------------------------------------------

SATISFACTION = {
    "_name": "Zufriedenheit",
    "levels": {"1": "sehr unzufrieden", "2": "unzufrieden", "3": "neutral",
               "4": "zufrieden", "5": "sehr zufrieden"},
}


def test_ordinal_row_with_levels():
    assert validate_row("ordinal", {"value": 4, "count": 17}, SATISFACTION) == {
        "value": 4.0, "count": 17, "denominator": None, "sum_sq": None,
    }
    assert validate_row("ordinal", {"value": 3.0}, SATISFACTION)["value"] == 3.0


def test_ordinal_level_must_be_defined():
    error = _refused("ordinal", {"value": 6}, SATISFACTION)
    assert error.message == "Wert 6 ist keine Stufe von \u00abZufriedenheit\u00bb."
    assert error.fields == {"value": error.message}
    unnamed = {"levels": SATISFACTION["levels"]}
    assert _refused("ordinal", {"value": 2.5}, unnamed).message == (
        "Wert 2,5 ist keine definierte Stufe."
    )


def test_ordinal_fractional_levels_and_no_levels():
    assert validate_row("ordinal", {"value": 2.5}, {"levels": {"2.5": "mittel", "3": "gut"}})
    assert validate_row("ordinal", {"value": 7.25})["value"] == 7.25


def test_ordinal_ignores_sum_sq_and_respects_bounds():
    assert validate_row("ordinal", {"value": 3, "sum_sq": 99})["sum_sq"] is None
    assert "ausserhalb" in _refused("ordinal", {"value": 11}, {"min": 0, "max": 10}).message


PREFERENCE = {"_name": "Bevorzugte Variante",
              "levels": {"0": "Keine Pr\u00e4ferenz", "1": "Variante A", "2": "Variante B"}}


def test_categorical_row():
    assert validate_row("categorical", {"value": 0, "count": 4}, PREFERENCE)["value"] == 0.0
    assert validate_row("categorical", {"value": -0.0}, PREFERENCE)["value"] == 0.0


def test_categorical_code_must_be_whole_and_defined():
    assert _refused("categorical", {"value": 1.5}, PREFERENCE).message == (
        "Die Kategorie muss eine ganze Zahl sein."
    )
    assert _refused("categorical", {"value": 3}, PREFERENCE).message == (
        "Wert 3 ist keine Stufe von \u00abBevorzugte Variante\u00bb."
    )


# -- estimate -------------------------------------------------------------------


def _agg(**kw):
    base = {"variant": "A", "rows": 1, "n": 0, "value_sum": 0.0, "denominator_sum": None,
            "sum_sq": None, "levels": None}
    base.update(kw)
    return base


@pytest.mark.parametrize("kind", ["proportion", "mean", "count", "duration", "currency", "ordinal"])
def test_estimate_is_value_sum_over_n(kind):
    assert estimate(kind, _agg(n=10714, value_sum=175.0)) == pytest.approx(175.0 / 10714)


@pytest.mark.parametrize("kind", ["proportion", "mean", "ordinal"])
def test_estimate_without_units_is_none(kind):
    assert estimate(kind, _agg(n=0, value_sum=0.0)) is None
    assert estimate(kind, _agg(n=None, value_sum=3.0)) is None


def test_ratio_estimate_uses_denominator_sum():
    assert estimate("ratio", _agg(n=7, value_sum=300.0, denominator_sum=120.0)) == 2.5
    assert estimate("ratio", _agg(n=7, value_sum=300.0, denominator_sum=0.0)) is None
    assert estimate("ratio", _agg(n=7, value_sum=300.0, denominator_sum=None)) is None


def test_categorical_has_no_single_estimate():
    assert estimate("categorical", _agg(n=10, value_sum=12.0, levels={"1": 4, "2": 6})) is None


def test_estimate_tolerates_missing_or_broken_aggregates():
    assert estimate("mean", {}) is None
    assert estimate("mean", None) is None  # type: ignore[arg-type]
    assert estimate("mean", _agg(n=2, value_sum=float("nan"))) is None
    with pytest.raises(ValidationError):
        estimate("median", _agg(n=1, value_sum=1.0))


# -- level_key --------------------------------------------------------------------


@pytest.mark.parametrize("value, key", [
    (3.0, "3"), (3, "3"), (2.5, "2.5"), (0.0, "0"), (-0.0, "0"), (-2.0, "-2"), (0.1, "0.1"),
    (1e20, "1e+20"),
])
def test_level_key(value, key):
    assert level_key(value) == key


def test_level_key_refuses_non_finite():
    with pytest.raises(ValueError):
        level_key(float("nan"))


# -- formatting -------------------------------------------------------------------


@pytest.mark.parametrize("x, decimals, text", [
    (10714.5, 2, "10'714,50"),
    (10714.5, 0, "10'715"),
    (0, 2, "0,00"),
    (999.999, 2, "1'000,00"),
    (-1234.567, 2, "-1'234,57"),
    (-0.001, 2, "0,00"),
    (1234567.891, 1, "1'234'567,9"),
    (0.125, 2, "0,13"),
    (2.5, 0, "3"),
    (-2.5, 0, "-3"),
    (1.005, 2, "1,00"),
    (12, 3, "12,000"),
])
def test_format_number(x, decimals, text):
    assert format_number(x, decimals) == text


@pytest.mark.parametrize("x", [None, float("nan"), float("inf"), float("-inf"), "12", True])
def test_format_number_without_a_number_is_a_dash(x):
    assert format_number(x) == DASH


def test_format_number_clamps_odd_decimals():
    assert format_number(1.5, -3) == "2"
    assert format_number(1.5, 99) == "1,5000000000"
    assert format_number(1.5, None) == "1,50"  # type: ignore[arg-type]


def test_format_number_uses_ascii_only_separators():
    text = format_number(-1234567.5, 2)
    assert text.isascii() and text == "-1'234'567,50"


@pytest.mark.parametrize("kind, x, unit, decimals, text", [
    ("proportion", 0.016334, "%", None, "1,63 %"),
    ("proportion", 0.016334, "", 1, "1,6 %"),
    ("proportion", 1, "%", 0, "100 %"),
    ("mean", 12.5, "ms", None, "12,50 ms"),
    ("duration", 12.5, "ms", 1, "12,5 ms"),
    ("currency", 10714.5, "CHF", 0, "10'715 CHF"),
    ("mean", 3, "%", None, "3,00 %"),
    ("mean", 0.734, "", 3, "0,734"),
    ("ratio", 2.5, "  CHF ", None, "2,50 CHF"),
    ("mean", None, "ms", None, DASH),
    ("proportion", None, "%", None, DASH),
    ("proportion", float("nan"), "%", None, DASH),
])
def test_format_value(kind, x, unit, decimals, text):
    assert format_value(kind, x, unit, decimals) == text


@pytest.mark.parametrize("kind, x, unit, decimals, text", [
    ("proportion", 0.0042, "%", None, "+0,42 Pp."),
    ("proportion", -0.0042, "%", None, "-0,42 Pp."),
    ("proportion", 0.0, "%", None, "+0,00 Pp."),
    ("duration", 12.5, "ms", 1, "+12,5 ms"),
    ("currency", -3, "CHF", None, "-3,00 CHF"),
    ("mean", -0.0001, "", None, "+0,00"),
    ("mean", 1500, "", 0, "+1'500"),
    ("ratio", None, "CHF", None, DASH),
])
def test_format_diff(kind, x, unit, decimals, text):
    assert format_diff(kind, x, unit, decimals) == text


@pytest.mark.parametrize("x, text", [
    (0.5, "0,5"), (1000, "1'000"), (2.25, "2,25"), (-0.5, "-0,5"), (0, "0"),
    (1e-9, "0"), (None, DASH), (1234.5678901, "1'234,56789"),
])
def test_format_plain(x, text):
    assert format_plain(x) == text
