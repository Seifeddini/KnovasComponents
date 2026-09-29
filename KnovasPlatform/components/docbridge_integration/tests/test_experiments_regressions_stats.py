"""Regression tests for the statistics, built-in evaluators and shipped packs
of the experiments module: one test (or more) per verified finding, named
after it (review-stats-*, e2e-ui-9, e2e-api-1, review-contract-frontend-7)."""

import copy
import json
import math
import random
import re

import pytest

from conftest import _person, platform_db_reachable
from experiments import evaluators as E
from experiments import stats
from experiments.packs import load_pack

CONTROL_B = [{"key": "A", "name": "Kontrolle", "is_control": True},
             {"key": "B", "name": "Variante B", "is_control": False}]
ABC = [{"key": k, "name": "", "is_control": k == "A"} for k in "ABC"]


def agg(variant, n, value_sum, **extra):
    row = {"variant": variant, "rows": extra.pop("rows", 1), "n": n, "value_sum": value_sum,
           "denominator_sum": None, "sum_sq": None, "estimate": None, "levels": None}
    row.update(extra)
    return row


def values_agg(variant, values):
    """Aggregate of single observations (count 1), as the store builds it."""
    return agg(variant, len(values), math.fsum(values), rows=len(values),
               sum_sq=math.fsum(v * v for v in values))


def levels_agg(variant, counts):
    """Ordinal aggregate from the people per level 1..len(counts)."""
    levels = {str(i + 1): c for i, c in enumerate(counts) if c}
    return agg(variant, sum(counts), sum((i + 1) * c for i, c in enumerate(counts)),
               sum_sq=sum((i + 1) ** 2 * c for i, c in enumerate(counts)), levels=levels,
               rows=len(levels))


def data(kind, aggregates, *, direction="higher", unit="", variants=None, rows=None, params=None,
         guardrail=None, definition=None, name="Metrik"):
    return {
        "experiment": {"key": "X-1", "title": "t", "hypothesis": "", "domain": "d", "type": "t",
                       "status": "running", "fields": {}, "tags": []},
        "metric": {"key": "m", "name": name, "kind": kind, "unit": unit, "direction": direction,
                   "role": "primary", "definition": definition or {}, "guardrail": guardrail},
        "variants": CONTROL_B if variants is None else variants,
        "aggregates": aggregates, "rows": rows or [], "rows_truncated": False, "scope": {},
        "params": params or {},
    }


def excludes(c, reference=0.0):
    lo = -math.inf if c["ci_low"] is None else c["ci_low"]
    hi = math.inf if c["ci_high"] is None else c["ci_high"]
    return lo > reference or hi < reference


# -- review-stats-1: chi_square on ordinal names no winner -------------------------------


@pytest.mark.parametrize("b_counts", [[50, 0, 0, 0, 51], [51, 0, 0, 0, 50]])
def test_stats1_chi_square_ordinal_shape_change_is_not_better_or_worse(b_counts):
    out = E.run_builtin("builtin.chi_square", data(
        "ordinal", [levels_agg("A", [0, 0, 100, 0, 0]), levels_agg("B", b_counts)],
        name="Zufriedenheit"))
    assert out["verdict"] == "n/a"
    assert [c["verdict"] for c in out["comparisons"]] == ["n/a"]
    assert out["headline"].startswith("Verteilung unterscheidet sich zwischen den Varianten")
    assert "ist besser als" not in out["summary"] and "ist schlechter als" not in out["summary"]
    assert "Welch-t-Test auf die mittlere Stufe" in out["summary"]
    # The direction question belongs to welch_t, which sees no shift here.
    welch = E.run_builtin("builtin.welch_t", data(
        "ordinal", [levels_agg("A", [0, 0, 100, 0, 0]), levels_agg("B", b_counts)]))
    assert welch["verdict"] == "inconclusive"


# -- review-stats-2: the Python example bootstraps people on a scale -----------------------


def _python_example():
    pytest.importorskip("numpy")
    code = next(e for e in load_pack("core")["evaluators"] if e["language"] == "python")["code"]
    namespace = {}
    exec(compile(code, "evaluator.py", "exec"), namespace)  # noqa: S102 - our own template
    return namespace["evaluate"]


def _example_input(rows, kind="ordinal", params=None, direction="higher"):
    return {"metric": {"key": "s", "name": "S", "kind": kind, "unit": "", "direction": direction,
                       "definition": {}, "guardrail": None},
            "variants": [{"key": "A", "name": "", "is_control": True},
                         {"key": "B", "name": "", "is_control": False}],
            "aggregates": [], "rows": rows, "rows_truncated": False, "scope": {},
            "params": params or {}}


def _level_rows(variant, counts, per_person=False):
    rows = []
    for level, count in enumerate(counts, start=1):
        if per_person:
            rows += [{"variant": variant, "value": float(level), "count": 1}] * count
        elif count:
            rows.append({"variant": variant, "value": float(level), "count": count})
    return rows


def test_stats2_bootstrap_example_resamples_people_not_level_rows():
    evaluate = _python_example()
    a, b = [5, 12, 30, 35, 18], [3, 8, 22, 42, 25]
    grouped = evaluate(_example_input(_level_rows("A", a) + _level_rows("B", b)))
    single = evaluate(_example_input(_level_rows("A", a, True) + _level_rows("B", b, True)))
    for out in (grouped, single):
        (c,) = out["comparisons"]
        assert c["estimate"] == pytest.approx(0.29)
        # Welch on the 200 answers: 95 %-KI -0,001 bis +0,581, p = 0,051. The
        # old row bootstrap gave -1,23 bis +1,63 and p = 0,67 for five rows.
        assert -0.05 < c["ci_low"] < 0.05 and 0.52 < c["ci_high"] < 0.62
        assert 0.02 < c["p_value"] < 0.1
        va = out["variants"][0]
        assert va["n"] == 100 and 3.2 < va["ci_low"] < 3.35 and 3.63 < va["ci_high"] < 3.76
        assert va["sd"] == pytest.approx(1.0777, abs=1e-3)
    assert grouped["variants"][0]["sum"] == pytest.approx(349.0)


def test_stats2_bootstrap_example_compares_a_variant_on_a_single_level():
    evaluate = _python_example()
    out = evaluate(_example_input([{"variant": "A", "value": 4.0, "count": 100}]
                                  + _level_rows("B", [3, 8, 22, 42, 25])))
    assert not any("zu wenige" in w for w in out["warnings"])
    assert out["variants"][0]["ci_low"] == out["variants"][0]["ci_high"] == 4.0
    (c,) = out["comparisons"]
    assert c["estimate"] == pytest.approx(-0.22)
    # Two answers are still too few, and say so in the words of a scale.
    few = evaluate(_example_input([{"variant": "A", "value": 4.0, "count": 1}]
                                  + _level_rows("B", [3, 8, 22, 42, 25])))
    assert "Variante A: zu wenige Antworten (mindestens 2)." in few["warnings"]


def test_stats2_bootstrap_example_keeps_row_resampling_for_means():
    evaluate = _python_example()
    rows = ([{"variant": "A", "value": v, "count": 1} for v in (10.0, 12.0, 11.0, 13.0)]
            + [{"variant": "B", "value": v, "count": 1} for v in (20.0, 22.0, 21.0, 23.0)])
    out = evaluate(_example_input(rows, kind="mean", params={"draws": 500}))
    assert out["verdict"] == "better" and out["comparisons"][0]["estimate"] == pytest.approx(10.0)
    assert out["summary"].startswith("**Bootstrap** mit 500 Ziehungen je Variante; gezogen werden "
                                     "die Messzeilen")


# -- review-stats-3: the verdict and the interval beside it agree --------------------------


@pytest.mark.parametrize("s1, n1, s2, n2, direction, verdict", [
    (3, 200, 10, 200, "higher", "better"),       # was "besser" next to -0,11 bis +7,58
    (15, 1000, 6, 1000, "lower", "better"),      # was "besser" next to ... bis +0,017
])
def test_stats3_two_proportion_interval_excludes_zero_when_significant(s1, n1, s2, n2, direction,
                                                                       verdict):
    out = E.run_builtin("builtin.two_proportion", data(
        "proportion", [agg("A", n1, s1), agg("B", n2, s2)], direction=direction))
    (c,) = out["comparisons"]
    assert c["p_value"] < 0.05 and c["verdict"] == verdict == out["verdict"]
    assert excludes(c)
    assert not any("widersprechen" in w for w in out["warnings"])


def test_stats3_two_proportion_score_interval_is_dual_to_the_pooled_z_test():
    rng = random.Random(3)
    for _ in range(300):
        n1, n2 = rng.choice([10, 40, 200, 1000]), rng.choice([15, 60, 200, 1500])
        s1, s2 = rng.randint(0, n1), rng.randint(0, n2)
        alpha = rng.choice([0.01, 0.05, 0.1])
        r = stats.two_proportion_test(s1, n1, s2, n2, alpha)
        assert (r["p_value"] < alpha) == (r["ci_low"] > 0 or r["ci_high"] < 0), (s1, n1, s2, n2)
        assert -1.0 <= r["ci_low"] <= r["diff"] <= r["ci_high"] <= 1.0


def test_stats3_two_proportion_interval_edges():
    # No successes anywhere: the ends are the Wilson bounds of 0/n.
    r = stats.two_proportion_test(0, 50, 0, 70)
    z2 = 1.959963984540054 ** 2
    assert r["ci_low"] == pytest.approx(-z2 / (50 + z2), rel=1e-9)
    assert r["ci_high"] == pytest.approx(z2 / (70 + z2), rel=1e-9)
    # All against none: the interval reaches the bound it must.
    assert stats.two_proportion_test(0, 10, 10, 10)["ci_high"] == 1.0
    assert stats.two_proportion_test(1, 1, 0, 3)["ci_low"] == -1.0
    # Huge and extreme groups stay finite and ordered.
    big = stats.two_proportion_test(3e8, 1e10, 3.3e8, 1e10)
    assert big["ci_low"] < big["diff"] < big["ci_high"]


def test_stats3_poisson_rate_p_value_is_dual_to_its_exact_interval():
    # A 5/500 against B 2/1000, fewer is better: binomtest's rule gave
    # p = 0,045 ("besser") next to 95 %-KI 0,02 bis 1,22.
    out = E.run_builtin("builtin.poisson_rate", data(
        "count", [agg("A", 500, 5), agg("B", 1000, 2)], direction="lower"))
    (c,) = out["comparisons"]
    assert c["p_value"] == pytest.approx(0.09053497942386832, rel=1e-9)
    assert c["verdict"] == "inconclusive" and not excludes(c, 1.0)
    rng = random.Random(8)
    for _ in range(300):
        e1, e2 = rng.randint(0, 40), rng.randint(0, 40)
        t1, t2 = rng.choice([100.0, 500.0, 1000.0]), rng.choice([100.0, 800.0, 2500.0])
        alpha = rng.choice([0.01, 0.05])
        r = stats.poisson_rate_test(e1, t1, e2, t2, alpha)
        lo = r["ci_low"] if r["ci_low"] is not None else 0.0
        hi = r["ci_high"] if r["ci_high"] is not None else math.inf
        if e1 + e2:
            assert (r["p_value"] < alpha) == (lo > 1.0 or hi < 1.0), (e1, t1, e2, t2)


def test_stats3_a_remaining_contradiction_is_never_silent(monkeypatch):
    real = stats.two_proportion_test

    def contradicting(*args):
        result = dict(real(*args))
        result["ci_low"] = -0.001  # an interval that still holds 0
        return result

    monkeypatch.setattr(stats, "two_proportion_test", contradicting)
    out = E.run_builtin("builtin.two_proportion", data(
        "proportion", [agg("A", 10688, 129), agg("B", 10714, 175)]))
    assert out["verdict"] == "better"
    assert ("Variante B: p-Wert und Konfidenzintervall widersprechen sich knapp; das Ergebnis "
            "mit Vorsicht lesen.") in out["warnings"]


# -- review-stats-4: no spread, no zero-width interval ------------------------------------


def test_stats4_describe_without_spread_gives_no_interval_and_no_target_verdict():
    sus = E.run_builtin("builtin.describe", data(
        "mean", [values_agg(None, [80.0, 80.0])], variants=[], unit="Punkte", name="SUS",
        params={"target": 68}))
    assert sus["verdict"] == "inconclusive"
    assert sus["headline"] == "SUS 80,00 Punkte \u2013 Ziel 68,00 Punkte nicht belegt."
    assert sus["variants"][0]["ci_low"] is None and sus["variants"][0]["sd"] == 0.0
    assert "ohne Variante: keine Streuung in den Daten; kein Konfidenzintervall." in sus["warnings"]
    likert = E.run_builtin("builtin.describe", data(
        "ordinal", [levels_agg("A", [0, 0, 0, 0, 3])], variants=CONTROL_B[:1], name="Zufriedenheit",
        params={"target": 4.5}))
    assert likert["verdict"] == "inconclusive"
    assert "A: keine Streuung in den Daten; kein Konfidenzintervall." in likert["warnings"]
    assert "5,00\u20135,00" not in json.dumps(likert, ensure_ascii=False)


def test_stats4_variance_rounding_residue_is_no_spread():
    values = [4.3] * 11
    s, ss = sum(values), sum(v * v for v in values)
    assert (ss - s * s / 11) / 10 != 0.0  # the residue this is about
    assert stats.variance(s, ss, 11) == 0.0
    out = E.run_builtin("builtin.describe", data(
        "mean", [agg(None, 11, s, sum_sq=ss)], variants=[], params={"target": 4.0}))
    assert out["verdict"] == "inconclusive" and out["variants"][0]["ci_low"] is None
    # Real spread keeps its interval.
    assert stats.variance(6.0, 14.0, 3) == pytest.approx(1.0)


# -- review-stats-5: guardrail and target texts never print both sides equal ---------------


def test_stats5_guardrail_message_shows_enough_decimals():
    out = E.run_builtin("builtin.describe", data(
        "proportion", [agg("A", 10000, 6500), agg("B", 10000, 7003)], direction="lower",
        guardrail={"op": "max", "value": 0.7}, name="Absprungrate"))
    assert out["verdict"] == "worse"
    assert out["headline"] == "Leitplanke verletzt: Variante B 70,03 % > 70,00 %"
    assert "Leitplanke: h\u00f6chstens 70,0 %. Verletzt: Variante B 70,03 % > 70,00 %." in out["summary"]
    # The plan's example keeps its one decimal.
    plan = E.run_builtin("builtin.describe", data(
        "proportion", [agg("A", 100, 60), agg("B", 100, 78)], guardrail={"op": "max", "value": 0.7}))
    assert plan["warnings"] == ["Leitplanke verletzt: Variante B 78,0 % > 70,0 %."]
    # A limit with more digits than the metric shows is printed as configured.
    odd = E.run_builtin("builtin.describe", data(
        "proportion", [agg("A", 100, 60)], variants=CONTROL_B[:1],
        guardrail={"op": "max", "value": 0.7005}))
    assert "Leitplanke: h\u00f6chstens 70,05 %." in odd["summary"]


def test_stats5_target_text_shows_the_configured_target_and_a_distinct_bound():
    out = E.run_builtin("builtin.describe", data(
        "proportion", [agg(None, 400, 360)], variants=[], params={"target": 0.8005}))
    assert "Ziel 80,05 %" in out["headline"]
    text = out["headline"]
    bound = re.search(r"KI ([0-9,]+)\u2013", text).group(1)
    assert bound != "80,05"


# -- review-stats-7: a proportion target outside 0..1 is ignored with a warning --------------


@pytest.mark.parametrize("target, direction", [(80, "higher"), (30, "lower"), (-0.2, "higher")])
def test_stats7_proportion_target_out_of_range_is_ignored(target, direction):
    out = E.run_builtin("builtin.describe", data(
        "proportion", [agg(None, 80, 74 if direction == "higher" else 20)], variants=[],
        params={"target": target}, direction=direction, name="Aufgabenerfolg"))
    assert out["verdict"] == "n/a"
    assert "Ziel" not in out["headline"]
    # Leftover (stats group): the fixed wording of the second line of defence.
    assert "Ziel ausserhalb 0..1 \u2013 f\u00fcr Anteile 0,8 statt 80 angeben." in out["warnings"]
    assert out["values"]["target_invalid"] == target and "target_met" not in out["values"]
    # A share typed as a fraction still counts, and so do targets of other kinds.
    ok = E.run_builtin("builtin.describe", data(
        "proportion", [agg(None, 80, 74)], variants=[], params={"target": 0.8}))
    assert ok["values"]["target"] == 0.8
    mean = E.run_builtin("builtin.describe", data(
        "mean", [values_agg(None, [70.0, 75.0, 80.0])], variants=[], params={"target": 80}))
    assert mean["values"]["target"] == 80


# -- review-stats-8: poisson_rate names the best variant when the control has no events ------


@pytest.mark.parametrize("direction, verdict", [("higher", "better"), ("lower", "worse")])
def test_stats8_poisson_rate_ranks_by_rate_when_ratios_are_unbounded(direction, verdict):
    out = E.run_builtin("builtin.poisson_rate", data(
        "count", [agg("A", 500, 0), agg("B", 500, 9), agg("C", 500, 20)], variants=ABC,
        direction=direction))
    assert out["verdict"] == verdict
    assert out["headline"].startswith("C: Rate \u00d7 unbegrenzt gegen\u00fcber A")
    assert f"Ergebnis: C ist {'besser' if verdict == 'better' else 'schlechter'} als A" in out["summary"]
    sentence = "Die Kontrolle A hat keine Ereignisse; das Verh\u00e4ltnis ist unbegrenzt."
    assert out["summary"].count(sentence) == 1 and out["warnings"].count(sentence) == 1
    assert out["values"]["ci_low"] == pytest.approx(out["values"]["C.ci_low"])
    # Bounded ratios keep ranking as before: the largest ratio is the best.
    bounded = E.run_builtin("builtin.poisson_rate", data(
        "count", [agg("A", 500, 5), agg("B", 500, 30), agg("C", 500, 60)], variants=ABC))
    assert bounded["headline"].startswith("C:")
    # The rate used for ranking does not leak into the output.
    assert set(bounded["comparisons"][0]) == {"variant", "baseline", "label", "estimate", "ci_low",
                                              "ci_high", "p_value", "prob_better", "relative",
                                              "unit", "verdict"}


def test_stats8_summary_hints_list_each_warning_once():
    # A control with a single row is reported for each variant compared with
    # it; the "Hinweise" line of the summary says it once, like ``warnings``.
    rows = [{"variant": "A", "value": 10.0, "denominator": 1.0},
            {"variant": "B", "value": 30.0, "denominator": 3.0},
            {"variant": "B", "value": 34.0, "denominator": 4.0},
            {"variant": "C", "value": 50.0, "denominator": 5.0},
            {"variant": "C", "value": 44.0, "denominator": 4.0}]
    out = E.run_builtin("builtin.ratio_delta", data(
        "ratio", [agg("A", 1, 10.0, denominator_sum=1.0), agg("B", 2, 64.0, denominator_sum=7.0),
                  agg("C", 2, 94.0, denominator_sum=9.0)], variants=ABC, rows=rows))
    hints = next(line for line in out["summary"].split("\n") if line.startswith("Hinweise:"))
    assert "Variante A: weniger als 2 Zeilen; kein Test m\u00f6glich." in out["warnings"]
    for warning in out["warnings"]:
        assert hints.count(warning) == 1, warning


# -- review-stats-9: ratio_delta blames the group whose denominators sum to 0 ----------------


def test_stats9_ratio_delta_names_the_control_for_its_zero_denominators():
    rows = ([{"variant": "A", "value": v, "denominator": 0.0} for v in (100.0, 120.0, 90.0)]
            + [{"variant": "B", "value": v, "denominator": d}
               for v, d in ((500.0, 5.0), (520.0, 6.0), (480.0, 5.0), (505.0, 6.0))]
            + [{"variant": "C", "value": v, "denominator": d}
               for v, d in ((600.0, 7.0), (610.0, 6.0), (590.0, 7.0))])
    out = E.run_builtin("builtin.ratio_delta", data(
        "ratio", [agg("A", 3, 310.0, denominator_sum=0.0), agg("B", 4, 2005.0, denominator_sum=22.0),
                  agg("C", 3, 1800.0, denominator_sum=20.0)], variants=ABC, rows=rows, unit="CHF"))
    zero = [w for w in out["warnings"] if "Summe des Nenners ist 0" in w]
    assert zero == ["Kontrolle A: die Summe des Nenners ist 0; kein Vergleich m\u00f6glich."]
    assert out["verdict"] == "inconclusive"
    # A variant with zero denominators is still named itself.
    rows_b = ([{"variant": "A", "value": v, "denominator": 2.0} for v in (10.0, 12.0, 11.0)]
              + [{"variant": "B", "value": v, "denominator": 0.0} for v in (10.0, 12.0)])
    only_b = E.run_builtin("builtin.ratio_delta", data(
        "ratio", [agg("A", 3, 33.0, denominator_sum=6.0), agg("B", 2, 22.0, denominator_sum=0.0)],
        rows=rows_b))
    assert [w for w in only_b["warnings"] if "Nenner" in w] == [
        "Variante B: die Summe des Nenners ist 0; kein Vergleich m\u00f6glich."]


# -- review-stats-10: no relative change against a negative or zero baseline --------------------


NEG_A = [-30.0, -10.0, -25.0, -15.0, -20.0, -22.0, -18.0, -20.0]
POS_B = [12.0, 8.0, 5.0, 15.0, 10.0, 9.0, 11.0, 10.0]


def test_stats10_relative_is_omitted_against_a_negative_control():
    welch = E.run_builtin("builtin.welch_t", data(
        "currency", [values_agg("A", NEG_A), values_agg("B", POS_B)], unit="CHF"))
    (c,) = welch["comparisons"]
    assert c["verdict"] == "better" and c["relative"] is None
    assert "relativ" not in welch["summary"]
    rows = ([{"variant": "A", "value": v, "count": 1, "dims": {"query": str(i)}} for i, v in enumerate(NEG_A)]
            + [{"variant": "B", "value": v, "count": 1, "dims": {"query": str(i)}} for i, v in enumerate(POS_B)])
    paired = E.run_builtin("builtin.paired_t", data(
        "currency", [values_agg("A", NEG_A), values_agg("B", POS_B)], rows=rows, unit="CHF"))
    assert paired["comparisons"][0]["relative"] is None
    ratio_rows = ([{"variant": "A", "value": v, "denominator": 2.0} for v in NEG_A]
                  + [{"variant": "B", "value": v, "denominator": 2.0} for v in POS_B])
    ratio = E.run_builtin("builtin.ratio_delta", data(
        "ratio", [agg("A", 8, sum(NEG_A), denominator_sum=16.0), agg("B", 8, sum(POS_B), denominator_sum=16.0)],
        rows=ratio_rows, unit="CHF"))
    assert ratio["comparisons"][0]["verdict"] == "better" and ratio["comparisons"][0]["relative"] is None
    # A positive control keeps its relative change.
    positive = E.run_builtin("builtin.welch_t", data(
        "currency", [values_agg("A", [v + 100 for v in NEG_A]), values_agg("B", [v + 100 for v in POS_B])]))
    assert positive["comparisons"][0]["relative"] == pytest.approx(30.0 / 80.0)


def test_stats10_bootstrap_example_omits_relative_against_a_negative_control():
    evaluate = _python_example()
    rows = ([{"variant": "A", "value": v, "count": 1} for v in NEG_A]
            + [{"variant": "B", "value": v, "count": 1} for v in POS_B])
    out = evaluate(_example_input(rows, kind="currency", params={"draws": 300}))
    assert out["comparisons"][0]["relative"] is None


# -- e2e-ui-9: the example evaluators write no Markdown tables --------------------------------


def test_e2e_ui9_example_summaries_have_no_pipe_tables():
    evaluate = _python_example()
    rows = ([{"variant": "A", "value": v, "count": 1} for v in (10.0, 12.0, 11.0, 13.0)]
            + [{"variant": "B", "value": v, "count": 1} for v in (20.0, 22.0, 21.0, 23.0)])
    out = evaluate(_example_input(rows, kind="mean", params={"draws": 300}))
    lines = out["summary"].split("\n")
    assert not any(line.lstrip().startswith("|") for line in lines)
    assert any(line.startswith("- B gegen\u00fcber A: +10,00") and "P(besser)" in line for line in lines)
    julia = next(e for e in load_pack("core")["evaluators"] if e["language"] == "julia")["code"]
    assert "|---|" not in julia and '"| ' not in julia
    assert 'push!(lines, "- $(c["variant"]) gegen\u00fcber $(control_text): ' in julia


# -- e2e-api-1: offline_eval judges the newest CI run per variant -----------------------------


def test_e2e_api1_offline_eval_describe_uses_the_latest_run():
    pack = load_pack("engineering")
    assert pack["version"] == 2
    offline = next(t for t in pack["types"] if t["key"] == "offline_eval")
    assert offline["definition"]["evaluation"][0] == {
        "evaluator": "builtin.describe", "metric": "all", "params": {}, "scope": {"runs": "latest"}}
    # The other engineering types compare many runs on purpose.
    for key in ("performance", "rollout"):
        t = next(t for t in pack["types"] if t["key"] == key)
        assert t["definition"]["evaluation"][0]["scope"] == {}
    assert load_pack("core")["version"] == 3  # 3: the generic_* metrics (e2e-ui-2)


# -- review-contract-frontend-7: a missing unit is not "no unit" ---------------------------


def test_contract_frontend7_missing_unit_on_a_proportion_difference_is_pp():
    raw = {"verdict": "better", "headline": "h",
           "comparisons": [{"variant": "B", "baseline": "A", "estimate": 0.0041, "ci_low": 0.0012,
                            "ci_high": 0.0071},
                           {"variant": "C", "baseline": "A", "estimate": 0.0041, "unit": None},
                           {"variant": "D", "baseline": "A", "estimate": 0.0041, "unit": ""},
                           {"variant": "E", "baseline": "A", "estimate": 1.8, "ci_low": 1.1},
                           {"variant": "F", "baseline": "A", "estimate": 0.5, "unit": "x"}]}
    out = E.sanitize_output(copy.deepcopy(raw), evaluator_name="Eigen", metric_kind="proportion")
    assert [c["unit"] for c in out["comparisons"]] == ["Pp.", "Pp.", "", None, "x"]
    # Without the metric's kind (or on another kind) a missing unit stays
    # null, which the page reads as the metric's own unit.
    unknown = E.sanitize_output(copy.deepcopy(raw), evaluator_name="Eigen")
    assert [c["unit"] for c in unknown["comparisons"]] == [None, None, "", None, "x"]
    currency = E.sanitize_output(copy.deepcopy(raw), metric_kind="currency")
    assert currency["comparisons"][0]["unit"] is None
    # Idempotent, and no "Nicht \u00fcbernommen" note for a missing unit.
    assert E.sanitize_output(out, metric_kind="proportion") == out
    assert not any("Nicht" in w for w in out["warnings"])


def test_contract_frontend7_builtins_keep_their_units():
    out = E.run_builtin("builtin.two_proportion", data(
        "proportion", [agg("A", 10688, 129), agg("B", 10714, 175)]))
    assert out["comparisons"][0]["unit"] == "Pp."
    welch = E.run_builtin("builtin.welch_t", data(
        "mean", [values_agg("A", [1.0, 2.0, 3.0]), values_agg("B", [2.0, 3.0, 4.0])]))
    assert welch["comparisons"][0]["unit"] == ""


# -- e2e-api-1 end to end: each CI push is judged on its own latency --------------------------


def _pipeline_world(platform_db, identity_repo):
    from experiments import store
    from experiments.service import ExperimentService
    from experiments.settings import ExperimentsSettings

    settings = ExperimentsSettings(enabled=True, index_enabled=False, worker_enabled=False)
    meta = {"ip": "10.1.2.3", "user_agent": "pytest", "token_id": None}
    store.ensure_builtin_evaluators(platform_db)
    store.ensure_core_pack(platform_db)
    manager = _person(identity_repo, "max@knovas.ch", "Max", "experiments_manager")
    eva = _person(identity_repo, "eva@knovas.ch", "Eva", "experimenter")
    ExperimentService(platform_db, manager, settings, request_meta=dict(meta)).install_pack("engineering")
    return ExperimentService(platform_db, eva, settings, request_meta=dict(meta))


@pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")
def test_e2e_api1_guardrail_follows_the_newest_push(platform_db, identity_repo):
    svc = _pipeline_world(platform_db, identity_repo)
    key = svc.create_experiment({"domain": "engineering", "type": "offline_eval",
                                 "title": "Reranker", "hypothesis": "Besseres NDCG"})["key"]

    def push(candidate_p95):
        for variant, p95, shift in (("baseline", 180.0, 0.0), ("candidate", candidate_p95, 0.02)):
            rows = [{"metric": "ndcg_at_10", "value": 0.5 + shift + 0.01 * (i % 5),
                     "dims": {"query": f"q{i}"}} for i in range(20)]
            rows.append({"metric": "latency_p95_ms", "value": p95})
            svc.add_run(key, {"variant": variant, "rows": rows}, source="api")
        results = svc.run_pipeline(key, trigger="api")
        describe = next(e for e in results if e["evaluator_key"] == "builtin.describe"
                        and e["metric_key"] == "latency_p95_ms")
        assert describe["scope"] == {"runs": "latest"}
        return svc.get_evaluation(key, describe["id"])["output"]

    first = push(400.0)
    assert first["verdict"] == "worse" and first["values"]["guardrail_ok"] is False
    assert first["headline"] == "Leitplanke verletzt: Variante candidate 400 ms > 250 ms"
    # The fix: 200 ms is judged on its own, not averaged with the 400 ms push.
    fixed = push(200.0)
    assert fixed["verdict"] == "n/a" and fixed["values"]["guardrail_ok"] is True
    # And a regression after good pushes is caught, not averaged away.
    push(200.0)
    regressed = push(320.0)
    assert regressed["verdict"] == "worse"
    assert "candidate 320 ms > 250 ms" in regressed["headline"]
