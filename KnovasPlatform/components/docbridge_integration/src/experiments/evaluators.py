"""Built-in evaluators and the evaluator input/output contract.

An evaluator reads one metric of one experiment -- the aggregates per
variant, and for some the individual measurement rows -- and returns a
verdict, a headline, a Markdown summary and the numbers behind them. The
built-in ones below run in-process on ``experiments.stats``; custom Python
and Julia evaluators run in the experiments-runner sandbox and receive the
same input (``build_input``). Whatever any evaluator returns goes through
``sanitize_output`` before it is stored, because the output is shown in the
browser and written into the Knovas index.

Rules every built-in follows (plan section 7):

- Each non-control variant is compared with the control (``is_control``,
  else the first variant). Rows and aggregates without a variant are never
  compared.
- ``direction`` decides what "better" means: ``lower`` flips it, ``none``
  reports the numbers with ``n/a`` verdicts.
- Too little data never raises: the comparison is ``inconclusive`` and a
  German warning says why.
- Headlines and summaries are German and name the numbers; they are read by
  people who are not statisticians and are indexed into Knovas.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from experiments import kinds, labels, stats
from experiments.errors import NotFound, ValidationError


log = logging.getLogger(__name__)

__all__ = [
    "BuiltinSpec", "BUILTINS", "VERDICTS", "OUTPUT_KEYS",
    "validate_params", "build_input", "run_builtin", "sanitize_output", "default_params",
]

VERDICTS = ("better", "worse", "inconclusive", "n/a")
OUTPUT_KEYS = ("verdict", "headline", "summary", "comparisons", "variants", "values",
               "table", "warnings")

_ALL_KINDS = ("proportion", "mean", "count", "duration", "currency", "ratio", "ordinal",
              "categorical")
_MEAN_KINDS = ("mean", "duration", "currency")
_DASH = kinds.DASH

_KIND_LABELS = {key: spec.label for key, spec in kinds.KINDS.items()}
_DIRECTION_LABELS = labels.DIRECTION_LABELS
_VERDICT_WORDS = labels.EVALUATION_VERDICT_LABELS

_HOLM_WARNING = "p-Werte nach Holm korrigiert."
_ENTRIES = "Eintr\u00e4ge"
_NO_VARIANT = "ohne Variante"


# -- number formatting -----------------------------------------------------------
#
# Numbers go through kinds.format_* (decimal comma, ASCII apostrophe for
# thousands, a space before "%", "Pp." for percentage points), the rules the
# page's KX.fmt* follow too, so an evaluation reads the same everywhere.


def _finite(x: Any) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        value = float(x)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _fmt_number(x: Any, decimals: int = 2) -> str:
    return kinds.format_number(_finite(x), decimals)


def _fmt_value(kind: str, x: Any, unit: str, decimals: int) -> str:
    return kinds.format_value(kind, _finite(x), unit or "", decimals)


def _fmt_diff(kind: str, x: Any, unit: str, decimals: int) -> str:
    return kinds.format_diff(kind, _finite(x), unit or "", decimals)


def _signed(x: Any, decimals: int) -> str:
    """A bare number signed like kinds.format_diff ("+0,11", "-0,20")."""
    text = _fmt_number(x, decimals)
    if text == _DASH or text.startswith("-"):
        return text
    return "+" + text


def _fmt_count(x: Any) -> str:
    value = _finite(x)
    if value is None:
        return _DASH
    return _fmt_number(value, 0 if value == int(value) else 2)


def _fmt_p(p: Any) -> str:
    value = _finite(p)
    if value is None:
        return "p = " + _DASH
    if value < 0.001:
        return "p < " + _fmt_number(0.001, 3)
    return "p = " + _fmt_number(value, 3)


def _p_cell(p: Any) -> str:
    value = _finite(p)
    if value is None:
        return _DASH
    return "< " + _fmt_number(0.001, 3) if value < 0.001 else _fmt_number(value, 3)


def _fmt_prob(p: Any) -> str:
    value = _finite(p)
    if value is None:
        return _DASH
    if value > 0.999:
        return "> 99,9 %"
    if value < 0.001:
        return "< 0,1 %"
    return _fmt_number(value * 100.0, 1) + " %"


def _fmt_plain(x: float) -> str:
    """0.05 -> "0,05", 1000 -> "1000": parameter values as people typed them."""
    return format(x, "g").replace(".", ",")


def _level_text(alpha: float) -> str:
    level = round((1.0 - alpha) * 100.0, 6)
    decimals = 0 if abs(level - round(level)) < 1e-9 else 1
    return f"{_fmt_number(level, decimals)} %-KI"


def _auto_decimals(configured: Optional[int], default: int, magnitudes: Sequence[Any]) -> int:
    """The metric's own decimals, else enough for two significant digits of
    the smallest non-zero magnitude (0.0042 must not print as 0,00)."""
    if configured is not None:
        return configured
    values = [abs(v) for v in (_finite(m) for m in magnitudes) if v]
    if not values:
        return default
    smallest = min(values)
    if smallest >= 1.0:
        return default
    return max(default, min(6, 1 - int(math.floor(math.log10(smallest)))))


def _md(text: Any) -> str:
    """User text inside Markdown: one line, no link syntax."""
    value = " ".join(str(text if text is not None else "").split())
    return value.replace("](", "] (")


def _exact_decimals(kind: Optional[str], x: float, decimals: int) -> int:
    """At least ``decimals``, more (up to 6) until a configured number such as
    a limit or target of 0.7005 prints as itself and not as 70,0 %."""
    scaled = x * (100.0 if kind == "proportion" else 1.0)
    d = decimals
    while d < 6 and abs(round(scaled, d) - scaled) > 1e-9 * max(1.0, abs(scaled)):
        d += 1
    return d


def _distinct_decimals(kind: Optional[str], a: float, b: float, unit: str, decimals: int) -> int:
    """Enough decimals (up to 6) that two different numbers set side by side
    with ">" or "<" do not print the same ("70,0 % > 70,0 %")."""
    d = decimals
    while d < 6 and a != b and _fmt_value(kind or "", a, unit, d) == _fmt_value(kind or "", b, unit, d):
        d += 1
    return d


def _relative(diff: Any, base: Any) -> Optional[float]:
    """diff / base as a relative change, only against a positive baseline:
    against a negative control mean (a margin of -20 CHF) the ratio has the
    wrong sign ("+30 CHF, relativ -150 %"), against 0 it is undefined."""
    d, b = _finite(diff), _finite(base)
    if d is None or b is None or not b > 0.0:
        return None
    return _finite(d / b)


def _hints(warnings: Sequence[str]) -> str:
    """The summary's "Hinweise:" line, each warning once (as in ``warnings``)."""
    return "Hinweise: " + " ".join(dict.fromkeys(warnings))


# -- input -----------------------------------------------------------------------


@dataclass
class _Group:
    """One aggregate, normalised."""

    key: Optional[str]
    rows: int = 0
    n: float = 0.0
    value_sum: Optional[float] = None
    denominator_sum: Optional[float] = None
    sum_sq: Optional[float] = None
    estimate: Optional[float] = None
    levels: Dict[str, float] = field(default_factory=dict)


@dataclass
class _Input:
    kind: Optional[str]
    unit: str
    direction: str
    name: str
    definition: Dict[str, Any]
    guardrail: Optional[Tuple[str, float]]
    params: Dict[str, Any]
    order: List[str]
    names: Dict[str, str]
    control: Optional[str]
    groups: Dict[str, _Group]
    null_group: Optional[_Group]
    rows: List[Dict[str, Any]]
    rows_truncated: bool
    decimals: Optional[int]

    def label(self, key: Optional[str]) -> str:
        return key if key is not None else _NO_VARIANT

    def has_data(self, key: str) -> bool:
        group = self.groups.get(key)
        return group is not None and group.n > 0


def _estimate(kind: Optional[str], group: _Group) -> Optional[float]:
    if group.n <= 0:
        return None
    if kind == "ratio":
        if group.value_sum is None or not group.denominator_sum:
            return None
        return group.value_sum / group.denominator_sum
    if kind == "categorical":
        return None
    if group.value_sum is None:
        return group.estimate
    return group.value_sum / group.n


def _merge(into: _Group, other: _Group) -> None:
    into.rows += other.rows
    into.n += other.n
    for name in ("value_sum", "denominator_sum"):
        a, b = getattr(into, name), getattr(other, name)
        setattr(into, name, None if a is None and b is None else (a or 0.0) + (b or 0.0))
    into.sum_sq = (into.sum_sq + other.sum_sq
                   if into.sum_sq is not None and other.sum_sq is not None else None)
    for key, units in other.levels.items():
        into.levels[key] = into.levels.get(key, 0.0) + units


def _group(agg: Dict[str, Any]) -> _Group:
    key = agg.get("variant")
    levels: Dict[str, float] = {}
    if isinstance(agg.get("levels"), dict):
        for level, units in agg["levels"].items():
            value = _finite(units)
            if value is not None and value > 0:
                levels[str(level)] = value
    rows = _finite(agg.get("rows"))
    n = _finite(agg.get("n"))
    return _Group(
        key=None if key is None else str(key),
        rows=int(rows) if rows is not None and rows > 0 else 0,
        n=n if n is not None and n > 0 else 0.0,
        value_sum=_finite(agg.get("value_sum")),
        denominator_sum=_finite(agg.get("denominator_sum")),
        sum_sq=_finite(agg.get("sum_sq")),
        estimate=_finite(agg.get("estimate")),
        levels=levels,
    )


def _decimals_setting(definition: Dict[str, Any]) -> Optional[int]:
    raw = definition.get("decimals") if isinstance(definition, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= 6:
        return None
    return raw


def _parse(data: Dict[str, Any], defaults: Dict[str, Any]) -> _Input:
    metric = data.get("metric") if isinstance(data.get("metric"), dict) else {}
    kind = metric.get("kind") if metric.get("kind") in _ALL_KINDS else None
    direction = metric.get("direction") if metric.get("direction") in _DIRECTION_LABELS else "higher"
    definition = metric.get("definition") if isinstance(metric.get("definition"), dict) else {}
    guardrail = None
    raw_guardrail = metric.get("guardrail")
    if isinstance(raw_guardrail, dict) and raw_guardrail.get("op") in ("max", "min"):
        limit = _finite(raw_guardrail.get("value"))
        if limit is not None:
            guardrail = (raw_guardrail["op"], limit)

    params = dict(defaults)
    if isinstance(data.get("params"), dict):
        params.update(data["params"])

    order: List[str] = []
    names: Dict[str, str] = {}
    control = None
    for variant in data.get("variants") or []:
        if not isinstance(variant, dict) or variant.get("key") in (None, ""):
            continue
        key = str(variant["key"])
        if key in names:
            continue
        order.append(key)
        names[key] = str(variant.get("name") or "")
        if control is None and variant.get("is_control") is True:
            control = key

    groups: Dict[str, _Group] = {}
    null_group: Optional[_Group] = None
    for agg in data.get("aggregates") or []:
        if not isinstance(agg, dict):
            continue
        group = _group(agg)
        if group.key is None:
            if null_group is None:
                null_group = group
            else:
                _merge(null_group, group)
            continue
        if group.key in groups:
            _merge(groups[group.key], group)
        else:
            groups[group.key] = group
            if group.key not in names:
                order.append(group.key)
                names[group.key] = ""
    if control is None and order:
        control = order[0]
    for group in list(groups.values()) + ([null_group] if null_group else []):
        group.estimate = _estimate(kind, group)

    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    return _Input(
        kind=kind, unit=str(metric.get("unit") or ""), direction=direction,
        name=str(metric.get("name") or metric.get("key") or "Metrik"),
        definition=definition, guardrail=guardrail, params=params, order=order,
        names=names, control=control, groups=groups, null_group=null_group, rows=rows,
        rows_truncated=bool(data.get("rows_truncated")),
        decimals=_decimals_setting(definition),
    )


def _param_number(params: Dict[str, Any], name: str, default: float, lo: float, hi: float) -> float:
    value = _finite(params.get(name))
    if value is None or not lo <= value <= hi:
        return default
    return value


def _alpha(ctx: _Input) -> float:
    return _param_number(ctx.params, "alpha", 0.05, 0.001, 0.2)


def _correction(ctx: _Input) -> str:
    return "none" if ctx.params.get("correction") == "none" else "holm"


# -- shared pieces of every evaluator ----------------------------------------------


def _output(*, verdict: str, headline: str, summary: str, comparisons=None, variants=None,
            values=None, table=None, warnings=None) -> Dict[str, Any]:
    return {
        "verdict": verdict, "headline": headline, "summary": summary,
        "comparisons": list(comparisons or []), "variants": list(variants or []),
        "values": dict(values or {}), "table": table or {"headers": [], "rows": []},
        "warnings": list(dict.fromkeys(warnings or [])),
    }


def _intro(title: str, ctx: _Input) -> str:
    kind_label = _KIND_LABELS.get(ctx.kind or "", "unbekannte Art")
    direction = _DIRECTION_LABELS.get(ctx.direction, "")
    text = f"**{title}** f\u00fcr \u00ab{_md(ctx.name)}\u00bb ({kind_label}, {direction})"
    if ctx.control is not None and len(ctx.order) > 1:
        text += f", verglichen mit der Kontrolle {_md(ctx.control)}"
    return text + "."


def _variant_title(ctx: _Input, key: Optional[str]) -> str:
    if key is None:
        return "Messwerte ohne Variante"
    name = ctx.names.get(key) or ""
    parts = [_md(name)] if name and name != key else []
    if key == ctx.control and len(ctx.order) > 1 and "kontrolle" not in name.lower():
        parts.append("Kontrolle")
    return _md(key) + (f" ({', '.join(parts)})" if parts else "")


def _wrong_kind(ctx: _Input, spec_name: str, kinds: Sequence[str]) -> Optional[Dict[str, Any]]:
    if ctx.kind in kinds:
        return None
    kind_label = _KIND_LABELS.get(ctx.kind or "", "unbekannt")
    warning = f"{spec_name} passt nicht zur Art der Metrik ({kind_label})."
    return _output(verdict="n/a", headline=f"{spec_name}: nicht anwendbar",
                   summary=_intro(spec_name, ctx) + "\n\n" + warning, warnings=[warning])


def _comparison_targets(ctx: _Input, warnings: List[str]) -> List[str]:
    """The variants to compare with the control, with warnings for the gaps."""
    if ctx.control is None or len(ctx.order) < 2:
        warnings.append("Es gibt keine zwei Varianten zum Vergleichen.")
        return []
    if not ctx.has_data(ctx.control):
        warnings.append(f"Die Kontrolle {ctx.control} hat noch keine Messwerte.")
        return []
    targets = []
    for key in ctx.order:
        if key == ctx.control:
            continue
        if ctx.has_data(key):
            targets.append(key)
        else:
            warnings.append(f"Variante {key} hat noch keine Messwerte.")
    return targets


def _judge(sign: float, p_value: Optional[float], alpha: float, direction: str) -> str:
    if direction == "none":
        return "n/a"
    if p_value is None or not p_value < alpha or sign == 0:
        return "inconclusive"
    up = sign > 0
    return "better" if up == (direction == "higher") else "worse"


def _gain(comparison: Dict[str, Any], direction: str) -> float:
    estimate = _finite(comparison.get("estimate"))
    if estimate is None:
        return 0.0
    return -estimate if direction == "lower" else estimate


def _overall(comparisons: List[Dict[str, Any]], direction: str, *, rank=None) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Overall verdict and the comparison the headline is about."""
    if not comparisons:
        return "inconclusive", None
    rank = rank or (lambda c: _gain(c, direction))
    better = [c for c in comparisons if c["verdict"] == "better"]
    if better:
        return "better", max(better, key=rank)
    worse = [c for c in comparisons if c["verdict"] == "worse"]
    if worse:
        return "worse", min(worse, key=rank)
    if all(c["verdict"] == "n/a" for c in comparisons):
        verdict = "n/a"
    else:
        verdict = "inconclusive"
    with_p = [c for c in comparisons if _finite(c.get("p_value")) is not None]
    if with_p:
        return verdict, min(with_p, key=lambda c: c["p_value"])
    return verdict, comparisons[0]


def _adjust(results: List[Dict[str, Any]], ctx: _Input, warnings: List[str]) -> None:
    """Holm-adjust the raw p-values in place (when more than one) and judge."""
    alpha = _alpha(ctx)
    raw = [r.get("p_raw") for r in results]
    adjusted = raw
    if _correction(ctx) != "none" and sum(p is not None for p in raw) > 1:
        adjusted = stats.holm(raw)
        warnings.append(_HOLM_WARNING)
    for result, p in zip(results, adjusted):
        result["p_value"] = p
        result["verdict"] = _judge(result.pop("sign", 0.0), p, alpha, ctx.direction)


def _comparison(variant: str, baseline: str, label: str, unit: str, **numbers) -> Dict[str, Any]:
    entry = {"variant": variant, "baseline": baseline, "label": label, "estimate": None,
             "ci_low": None, "ci_high": None, "p_value": None, "prob_better": None,
             "relative": None, "unit": unit, "verdict": "inconclusive"}
    entry.update(numbers)
    return entry


def _public(comparisons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keys = ("variant", "baseline", "label", "estimate", "ci_low", "ci_high", "p_value",
            "prob_better", "relative", "unit", "verdict")
    return [{k: c.get(k) for k in keys} for c in comparisons]


def _conclusion(verdict: str, focus: Optional[Dict[str, Any]], ctx: _Input, alpha: Optional[float]) -> str:
    level = f" (alpha = {_fmt_plain(alpha)})" if alpha is not None else ""
    if verdict == "better" and focus:
        return f"Ergebnis: {_md(focus['variant'])} ist besser als {_md(focus['baseline'])}{level}."
    if verdict == "worse" and focus:
        return f"Ergebnis: {_md(focus['variant'])} ist schlechter als {_md(focus['baseline'])}{level}."
    if verdict == "n/a":
        return "Ergebnis: Die Metrik hat keine Richtung, darum gibt es kein Urteil \u00fcber besser oder schlechter."
    return f"Ergebnis: Ein Unterschied ist nicht belegt{level}."


def _relative_text(relative: Any, sep: str = ",") -> str:
    value = _finite(relative)
    if value is None:
        return ""
    return f"{sep} relativ {_signed(value * 100.0, 1)} %"


def _verdict_suffix(verdict: str) -> str:
    return "" if verdict == "n/a" else f" {_DASH} {_VERDICT_WORDS.get(verdict, _DASH)}"


def _plural(n: Any, one: str, many: str) -> str:
    return f"{_fmt_count(n)} {one if _finite(n) == 1 else many}"


def _fmt_share(share: Any) -> str:
    """A share of units as a percentage (0 % stays 0,0 %, unlike a probability)."""
    value = _finite(share)
    return _DASH if value is None else _fmt_number(value * 100.0, 1) + " %"


def _values_for(comparisons: List[Dict[str, Any]], focus: Optional[Dict[str, Any]],
                extra: Dict[str, Any], names: Sequence[str]) -> Dict[str, Any]:
    values: Dict[str, Any] = dict(extra)
    if focus is not None:
        for name in names:
            values[name] = focus.get(name)
    if len(comparisons) > 1:
        for c in comparisons:
            for name in names:
                if len(values) >= 95:
                    return values
                values[f"{c['variant']}.{name}"] = c.get(name)
    return values


# -- per-variant description -------------------------------------------------------


def _describe_group(ctx: _Input, group: _Group, alpha: float, warnings: List[str]) -> Dict[str, Any]:
    kind = ctx.kind
    n = group.n
    entry: Dict[str, Any] = {"variant": group.key, "n": n, "value": None, "sd": None,
                             "sum": group.value_sum, "ci_low": None, "ci_high": None}
    if kind == "categorical":
        entry["sum"] = None
    if n <= 0:
        return entry
    s = group.value_sum
    label = ctx.label(group.key)
    try:
        if kind == "proportion" and s is not None:
            if not 0 <= s <= n:
                warnings.append(f"{label}: mehr Erfolge als Versuche; die Daten sind widerspr\u00fcchlich.")
                return entry
            entry["value"] = s / n
            entry["ci_low"], entry["ci_high"] = stats.wilson_interval(s, n, alpha)
        elif kind in _MEAN_KINDS + ("ordinal",) and s is not None:
            mean = s / n
            entry["value"] = mean
            var = stats.variance(s, group.sum_sq, n)
            if var is not None:
                entry["sd"] = math.sqrt(var)
                if var > 0.0:
                    entry["ci_low"], entry["ci_high"] = stats.t_interval(mean, var, n, alpha)
                elif n >= 2:
                    # Identical values: a t interval of width 0 would claim
                    # certainty from two answers of 5, so there is none.
                    warnings.append(f"{label}: keine Streuung in den Daten; kein Konfidenzintervall.")
        elif kind == "count" and s is not None:
            if s < 0:
                warnings.append(f"{label}: negative Anzahl Ereignisse; die Daten sind widerspr\u00fcchlich.")
                return entry
            entry["value"] = s / n
            entry["ci_low"], entry["ci_high"] = stats.poisson_interval(s, n, alpha)
        elif kind == "ratio":
            entry["value"] = group.estimate
        elif kind not in ("categorical",):
            entry["value"] = group.estimate
    except (ValueError, ArithmeticError):
        warnings.append(f"{label}: Kennzahlen lassen sich nicht berechnen.")
    return entry


def _groups_in_order(ctx: _Input, *, with_null: bool = True) -> List[_Group]:
    result = [ctx.groups.get(key) or _Group(key=key) for key in ctx.order]
    if with_null and ctx.null_group is not None:
        result.append(ctx.null_group)
    return result


def _describe_all(ctx: _Input, alpha: float, warnings: List[str]) -> List[Dict[str, Any]]:
    return [_describe_group(ctx, g, alpha, warnings) for g in _groups_in_order(ctx)]


def _value_decimals(ctx: _Input, entries: Sequence[Dict[str, Any]]) -> int:
    default = 1 if ctx.kind == "proportion" else 2
    scale = 100.0 if ctx.kind == "proportion" else 1.0
    magnitudes = []
    for e in entries:
        for name in ("value", "ci_low", "ci_high"):
            v = _finite(e.get(name))
            if v is not None:
                magnitudes.append(v * scale)
    return _auto_decimals(ctx.decimals, default, magnitudes)


def _range_text(ctx: _Input, lo: Any, hi: Any, decimals: int) -> str:
    lo_v, hi_v = _finite(lo), _finite(hi)
    if lo_v is None or hi_v is None:
        return _DASH
    if ctx.kind == "proportion":
        return f"{_fmt_number(lo_v * 100.0, decimals)}\u2013{_fmt_number(hi_v * 100.0, decimals)} %"
    sep = " bis " if lo_v < 0 or hi_v < 0 else "\u2013"
    text = f"{_fmt_number(lo_v, decimals)}{sep}{_fmt_number(hi_v, decimals)}"
    return f"{text} {ctx.unit}" if ctx.unit else text


def _level_labels(ctx: _Input) -> Dict[str, str]:
    levels = ctx.definition.get("levels")
    if not isinstance(levels, dict):
        return {}
    return {str(k): str(v) for k, v in levels.items()}


def _level_sort_key(key: str) -> Tuple[int, float, str]:
    try:
        return (0, float(key), key)
    except ValueError:
        return (1, 0.0, key)


def _categories(ctx: _Input, groups: Sequence[_Group]) -> List[str]:
    """Level keys to show or test: the defined levels, then any others seen."""
    labels = _level_labels(ctx)
    keys = list(labels)
    if ctx.kind == "ordinal":
        keys.sort(key=_level_sort_key)
    seen = sorted({k for g in groups for k in g.levels if k not in labels}, key=_level_sort_key)
    return keys + seen


def _level_name(ctx: _Input, key: str) -> str:
    return _level_labels(ctx).get(key) or key


def _variant_sentence(ctx: _Input, entry: Dict[str, Any], group: _Group, decimals: int, level: str) -> str:
    kind = ctx.kind
    title = _variant_title(ctx, group.key)
    n = group.n
    if n <= 0:
        return f"- {title}: noch keine Messwerte."
    value = _fmt_value(kind or "", entry.get("value"), ctx.unit, decimals)
    ci = ""
    if _finite(entry.get("ci_low")) is not None:
        ci = f", {level} {_range_text(ctx, entry['ci_low'], entry['ci_high'], decimals)}"
    if kind == "proportion":
        return (f"- {title}: {value} bei {_fmt_count(n)} Versuchen "
                f"({_fmt_count(group.value_sum)} Erfolge){ci}.")
    if kind in _MEAN_KINDS:
        sd = entry.get("sd")
        sd_text = (f", Standardabweichung {_fmt_value(kind, sd, ctx.unit, decimals)}"
                   if _finite(sd) is not None else "")
        return f"- {title}: Mittelwert {value} aus {_fmt_count(n)} Werten{sd_text}{ci}."
    if kind == "count":
        return (f"- {title}: {_fmt_number(entry.get('value'), decimals)} Ereignisse je Einheit "
                f"({_fmt_count(group.value_sum)} Ereignisse in {_fmt_count(n)} Einheiten){ci}.")
    if kind == "ratio":
        den = group.denominator_sum
        if not den:
            return (f"- {title}: kein Wert, die Summe des Nenners ist 0 "
                    f"(Z\u00e4hler {_fmt_number(group.value_sum, 2)}).")
        return (f"- {title}: {value} (Z\u00e4hler {_fmt_number(group.value_sum, 2)}, "
                f"Nenner {_fmt_number(den, 2)}, {_plural(group.rows, 'Zeile', 'Zeilen')}).")
    if kind == "ordinal":
        return f"- {title}: mittlere Stufe {_fmt_number(entry.get('value'), decimals)} bei {_fmt_count(n)} Antworten{ci}."
    if kind == "categorical":
        top = _top_level(ctx, group)
        if top is None:
            return f"- {title}: {_plural(n, 'Eintrag', _ENTRIES)}."
        return (f"- {title}: {_plural(n, 'Eintrag', _ENTRIES)}, am h\u00e4ufigsten \u00ab{_md(top[0])}\u00bb "
                f"({_fmt_share(top[1])}).")
    return f"- {title}: {value} (n = {_fmt_count(n)})."


def _top_level(ctx: _Input, group: _Group) -> Optional[Tuple[str, float]]:
    if not group.levels or group.n <= 0:
        return None
    key = max(sorted(group.levels, key=_level_sort_key), key=lambda k: group.levels[k])
    return _level_name(ctx, key), group.levels[key] / group.n


def _variants_table(ctx: _Input, entries: List[Dict[str, Any]], groups: List[_Group],
                    decimals: int, level: str) -> Dict[str, Any]:
    kind = ctx.kind
    headers = ["Variante", "n"]
    if kind != "categorical":
        headers += ["Mittel" if kind == "ordinal" else "Wert", level]
    extra: List[str] = []
    if kind == "proportion":
        extra = ["Erfolge"]
    elif kind in _MEAN_KINDS or kind == "ordinal":
        extra = ["Standardabweichung"]
    elif kind == "count":
        extra = ["Ereignisse"]
    elif kind == "ratio":
        extra = ["Z\u00e4hler", "Nenner"]
    headers += extra
    categories: List[str] = []
    if kind in ("categorical", "ordinal"):
        categories = _categories(ctx, groups)[: 20 - len(headers)]
        headers += [_level_name(ctx, c)[:200] for c in categories]
    rows = []
    for entry, group in zip(entries, groups):
        row = [ctx.label(group.key), _fmt_count(group.n)]
        if kind != "categorical":
            row += [_fmt_value(kind or "", entry.get("value"), ctx.unit, decimals)
                    if kind != "ordinal" else _fmt_number(entry.get("value"), decimals),
                    _range_text(ctx, entry.get("ci_low"), entry.get("ci_high"), decimals)]
        if kind == "proportion":
            row.append(_fmt_count(group.value_sum))
        elif kind in _MEAN_KINDS:
            row.append(_fmt_value(kind, entry.get("sd"), ctx.unit, decimals))
        elif kind == "ordinal":
            row.append(_fmt_number(entry.get("sd"), decimals))
        elif kind == "count":
            row.append(_fmt_count(group.value_sum))
        elif kind == "ratio":
            row += [_fmt_number(group.value_sum, 2), _fmt_number(group.denominator_sum, 2)]
        for c in categories:
            units = group.levels.get(c, 0.0)
            share = units / group.n if group.n > 0 else None
            row.append(f"{_fmt_share(share)} ({_fmt_count(units)})" if share is not None else _DASH)
        rows.append(row)
    return {"headers": headers, "rows": rows}


# -- builtin.describe --------------------------------------------------------------


def _describe(data: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _parse(data, default_params("builtin.describe"))
    spec_name = "Beschreibung je Variante"
    warnings: List[str] = []
    if ctx.kind is None:
        warning = "Die Art der Metrik ist unbekannt."
        return _output(verdict="n/a", headline=f"{ctx.name}: {warning}", summary=warning,
                       warnings=[warning])
    alpha = 0.05
    level = _level_text(alpha)
    groups = _groups_in_order(ctx)
    entries = _describe_all(ctx, alpha, warnings)
    decimals = _value_decimals(ctx, entries)
    with_data = [(e, g) for e, g in zip(entries, groups) if g.n > 0]
    total = sum(g.n for g in groups)
    values: Dict[str, Any] = {"n_total": int(total) if float(total).is_integer() and total < 2 ** 53 else total}
    lines = [_intro(spec_name, ctx), ""]
    lines += [_variant_sentence(ctx, e, g, decimals, level) for e, g in zip(entries, groups)]

    if not with_data:
        warnings.append("Noch keine Messwerte.")
        return _output(verdict="n/a", headline=f"{ctx.name}: noch keine Messwerte",
                       summary="\n".join(lines + ["", "Es gibt noch keine Messwerte."]),
                       variants=entries, values=values, warnings=warnings,
                       table=_variants_table(ctx, entries, groups, decimals, level))

    # Guardrail: every group whose estimate lies beyond the limit.
    violations: List[str] = []
    if ctx.guardrail is not None:
        op, limit = ctx.guardrail
        checked = False
        for entry, group in with_data:
            estimate = _finite(entry.get("value"))
            if estimate is None:
                continue
            checked = True
            if (op == "max" and estimate > limit) or (op == "min" and estimate < limit):
                who = f"Variante {group.key}" if group.key is not None else "Messwerte ohne Variante"
                # The test is exact; the text must not round both sides to
                # the same number ("70,0 % > 70,0 %" for 0.7003 > 0.7).
                d = _distinct_decimals(ctx.kind, estimate, limit, ctx.unit,
                                       _exact_decimals(ctx.kind, limit, decimals))
                violations.append(
                    f"Leitplanke verletzt: {who} {_fmt_value(ctx.kind, estimate, ctx.unit, d)} "
                    f"{'>' if op == 'max' else '<'} {_fmt_value(ctx.kind, limit, ctx.unit, d)}.")
        limit_text = _fmt_value(ctx.kind, limit, ctx.unit, _exact_decimals(ctx.kind, limit, decimals))
        bound = "h\u00f6chstens" if op == "max" else "mindestens"
        if checked:
            values["guardrail_ok"] = not violations
            lines += ["", f"Leitplanke: {bound} {limit_text}. "
                      + ("Verletzt: " + " ".join(v[len('Leitplanke verletzt: '):] for v in violations)
                         if violations else "Von allen Varianten eingehalten.")]
            values["guardrail_limit"] = limit
        warnings.extend(violations)

    verdict = "n/a"
    headline = ""
    target = _finite(ctx.params.get("target"))
    if target is not None and ctx.kind == "proportion" and not 0.0 <= target <= 1.0:
        # 80 typed for 80 %: compared with a share of 0..1 it would be
        # "verfehlt" (or "erreicht" for a lower-is-better rate) for certain.
        warnings.append(f"Ziel {_fmt_plain(target)} ignoriert: F\u00fcr Anteile das Ziel als Bruch "
                        "angeben (0.8 f\u00fcr 80 %).")
        values["target_invalid"] = target
        target = None
    if target is not None:
        values["target"] = target
        target_decimals = _exact_decimals(ctx.kind, target, decimals)
        verdict, reason = _target_verdict(ctx, with_data, target, warnings)
        if len(with_data) == 1 and verdict in ("better", "worse"):
            # The interval end that decides must not print as the target.
            entry = with_data[0][0]
            for bound in (entry.get("ci_low"), entry.get("ci_high")):
                if _finite(bound) is not None:
                    target_decimals = _distinct_decimals(ctx.kind, bound, target, ctx.unit,
                                                         target_decimals)
        target_text = _fmt_value(ctx.kind, target, ctx.unit, target_decimals)
        values["target_met"] = {"better": True, "worse": False}.get(verdict)
        status_text = {"better": "erreicht", "worse": "verfehlt"}.get(verdict, "nicht belegt")
        if len(with_data) == 1:
            entry, group = with_data[0]
            shown = max(decimals, target_decimals)
            ci = ""
            if _finite(entry.get("ci_low")) is not None:
                ci = f" ({level} {_range_text(ctx, entry['ci_low'], entry['ci_high'], shown)})"
            headline = (f"{ctx.name} {_fmt_value(ctx.kind, entry.get('value'), ctx.unit, shown)}"
                        f"{ci} {_DASH} Ziel {target_text} {status_text}.")
        elif verdict in ("better", "worse"):
            headline = f"{ctx.name}: Ziel {target_text} von allen Varianten {status_text}."
        else:
            headline = f"{ctx.name}: Ziel {target_text} nicht f\u00fcr alle Varianten belegt."
        lines += ["", f"Ziel {target_text}: {status_text}. {reason}".rstrip()]

    if violations:
        verdict = "worse"
        headline = violations[0].rstrip(".")
        if len(violations) > 1:
            headline += f" (und {len(violations) - 1} weitere)"
    if not headline:
        headline = _estimates_headline(ctx, with_data, decimals, level)
    return _output(verdict=verdict, headline=headline, summary="\n".join(lines),
                   variants=entries, values=values, warnings=warnings,
                   table=_variants_table(ctx, entries, groups, decimals, level))


def _target_verdict(ctx: _Input, with_data, target: float, warnings: List[str]) -> Tuple[str, str]:
    """better/worse when every interval lies on the good/bad side of the target."""
    if ctx.direction == "none":
        warnings.append("Die Metrik hat keine Richtung; das Ziel wird nicht bewertet.")
        return "n/a", "Die Metrik hat keine Richtung."
    sides = []
    for entry, group in with_data:
        lo, hi = _finite(entry.get("ci_low")), _finite(entry.get("ci_high"))
        if lo is None or hi is None:
            warnings.append(f"{ctx.label(group.key)}: kein Konfidenzintervall, das Ziel l\u00e4sst sich nicht pr\u00fcfen.")
            return "inconclusive", "Nicht f\u00fcr alle Varianten gibt es ein Konfidenzintervall."
        above, below = lo > target, hi < target
        if ctx.direction == "higher":
            sides.append("good" if above else "bad" if below else "open")
        else:
            sides.append("good" if below else "bad" if above else "open")
    if all(s == "good" for s in sides):
        return "better", "Das Konfidenzintervall liegt ganz auf der guten Seite des Ziels."
    if all(s == "bad" for s in sides):
        return "worse", "Das Konfidenzintervall liegt ganz auf der schlechten Seite des Ziels."
    return "inconclusive", "Das Konfidenzintervall schliesst das Ziel ein (oder die Varianten liegen verschieden)."


def _estimates_headline(ctx: _Input, with_data, decimals: int, level: str) -> str:
    if ctx.kind == "categorical":
        parts = []
        for _, group in with_data:
            top = _top_level(ctx, group)
            if top is not None:
                prefix = "" if len(with_data) == 1 else f"{ctx.label(group.key)} "
                parts.append(f"{prefix}\u00ab{top[0]}\u00bb {_fmt_share(top[1])}")
        lead = f"{ctx.name}: am h\u00e4ufigsten " if len(with_data) == 1 else f"{ctx.name}: "
        return _join_clipped(lead, parts)
    if len(with_data) == 1:
        entry, group = with_data[0]
        text = f"{ctx.name} {_fmt_value(ctx.kind, entry.get('value'), ctx.unit, decimals)}"
        if _finite(entry.get("ci_low")) is not None:
            text += f" ({level} {_range_text(ctx, entry['ci_low'], entry['ci_high'], decimals)})"
        return text + f", n = {_fmt_count(group.n)}"
    parts = [f"{ctx.label(g.key)} {_fmt_value(ctx.kind, e.get('value'), ctx.unit, decimals)}"
             for e, g in with_data]
    return _join_clipped(f"{ctx.name}: ", parts)


def _join_clipped(lead: str, parts: List[str], limit: int = 190) -> str:
    text = lead
    for i, part in enumerate(parts):
        piece = part if i == 0 else " \u00b7 " + part
        if len(text) + len(piece) > limit:
            return text + " \u2026"
        text += piece
    return text


# -- builtin.two_proportion --------------------------------------------------------


def _diff_decimals(ctx: _Input, comparisons: Sequence[Dict[str, Any]]) -> int:
    scale = 100.0 if ctx.kind == "proportion" else 1.0
    magnitudes = []
    for c in comparisons:
        for name in ("estimate", "ci_low", "ci_high"):
            v = _finite(c.get(name))
            if v is not None:
                magnitudes.append(v * scale)
    return _auto_decimals(ctx.decimals, 2, magnitudes)


def _effect_text(ctx: _Input, c: Dict[str, Any], decimals: int, level: str, *, with_unit: bool) -> str:
    """"+0,42 Pp. (95 %-KI +0,11 bis +0,73)", the unit repeated in the summary."""
    kind = ctx.kind or ""
    scale = 100.0 if kind == "proportion" else 1.0
    text = _fmt_diff(kind, c.get("estimate"), ctx.unit, decimals)
    lo, hi = _finite(c.get("ci_low")), _finite(c.get("ci_high"))
    if lo is not None and hi is not None:
        unit = ("Pp." if kind == "proportion" else ctx.unit) if with_unit else ""
        bounds = f"{_signed(lo * scale, decimals)} bis {_signed(hi * scale, decimals)}"
        text += f" ({level} {bounds}{' ' + unit if unit else ''})"
    return text


def _frequentist_headline(ctx: _Input, focus: Optional[Dict[str, Any]], decimals: int, level: str,
                          fallback: str) -> str:
    if focus is None or _finite(focus.get("p_value")) is None:
        return fallback
    return f"{focus['variant']}: {_effect_text(ctx, focus, decimals, level, with_unit=False)}, {_fmt_p(focus.get('p_value'))}"


def _comparison_sentence(ctx: _Input, c: Dict[str, Any], decimals: int, level: str, adjusted: bool) -> str:
    p_text = _fmt_p(c.get("p_value"))
    if adjusted and _finite(c.get("p_raw")) is not None:
        p_text += f" (nach Holm; unkorrigiert {_fmt_p(c.get('p_raw'))})"
    return (f"- {_md(c['variant'])} gegen\u00fcber {_md(c['baseline'])}: "
            f"{_effect_text(ctx, c, decimals, level, with_unit=True)}{_relative_text(c.get('relative'))}, "
            f"{p_text}{_verdict_suffix(c['verdict'])}.")


def _finish_frequentist(ctx: _Input, spec_name: str, results: List[Dict[str, Any]],
                        warnings: List[str], *, variant_entries, variant_groups,
                        headline_fn=None, sentence_fn=None, extra_values=None,
                        value_names=("estimate", "ci_low", "ci_high", "p_value"),
                        rank=None, extra_lines=()) -> Dict[str, Any]:
    alpha = _alpha(ctx)
    level = _level_text(alpha)
    _adjust(results, ctx, warnings)
    adjusted = _HOLM_WARNING in warnings
    for c in results:
        if _interval_disagrees(c, adjusted):
            warnings.append(f"Variante {c['variant']}: p-Wert und Konfidenzintervall widersprechen "
                            "sich knapp; das Ergebnis mit Vorsicht lesen.")
    verdict, focus = _overall(results, ctx.direction, rank=rank)
    decimals = _diff_decimals(ctx, results)
    value_decimals = _value_decimals(ctx, variant_entries)
    lines = [_intro(spec_name, ctx), ""]
    lines += [_variant_sentence(ctx, e, g, value_decimals, _level_text(alpha))
              for e, g in zip(variant_entries, variant_groups)]
    if results:
        lines.append("")
        lines += [(sentence_fn or _comparison_sentence)(ctx, c, decimals, level, adjusted)
                  for c in results]
    lines += list(extra_lines)
    if not any(_finite(c.get("p_value")) is not None for c in results):
        conclusion = "Ergebnis: Mit diesen Daten ist noch kein Vergleich m\u00f6glich."
    else:
        conclusion = _conclusion(verdict, focus, ctx, alpha)
    lines += ["", conclusion]
    if warnings:
        lines += ["", _hints(warnings)]
    fallback = f"{ctx.name}: zu wenig Daten f\u00fcr einen Vergleich"
    headline = (headline_fn or _frequentist_headline)(ctx, focus, decimals, level, fallback)
    values = _values_for(results, focus, {"alpha": alpha, "correction": _correction(ctx),
                                          **(extra_values or {})}, value_names)
    table = _comparisons_table(ctx, results, decimals, level)
    return _output(verdict=verdict, headline=headline, summary="\n".join(lines),
                   comparisons=_public(results), variants=variant_entries, values=values,
                   table=table, warnings=warnings)


def _interval_disagrees(c: Dict[str, Any], adjusted: bool) -> bool:
    """Whether a comparison's verdict and its interval tell different stories.

    Every built-in test is paired with the interval it inverts, so this only
    guards against rounding at the very boundary and future changes: a
    better/worse whose interval still holds the reference (0, or 1 for a
    ratio), or, without a Holm correction (which widens only the p-values),
    an undecided comparison whose interval excludes it.
    """
    lo, hi = _finite(c.get("ci_low")), _finite(c.get("ci_high"))
    if lo is None and hi is None:
        return False
    reference = 1.0 if c.get("unit") == "x" else 0.0
    lo = -math.inf if lo is None else lo
    hi = math.inf if hi is None else hi
    excludes = lo > reference or hi < reference
    if c.get("verdict") in ("better", "worse"):
        return not excludes
    return (not adjusted and c.get("verdict") == "inconclusive"
            and _finite(c.get("p_value")) is not None and excludes)


def _comparisons_table(ctx: _Input, results: List[Dict[str, Any]], decimals: int, level: str) -> Dict[str, Any]:
    headers = ["Variante", "Vergleich mit", "Unterschied", level, "p-Wert", "Ergebnis"]
    rows = []
    kind = ctx.kind or ""
    scale = 100.0 if kind == "proportion" else 1.0
    for c in results:
        estimate = c.get("estimate")
        lo, hi = _finite(c.get("ci_low")), _finite(c.get("ci_high"))
        if c.get("unit") == "x":
            effect = f"\u00d7 {_ratio_text(estimate)}"
            ci = f"{_ratio_text(lo)} bis {_ratio_text(hi)}" if lo is not None else _DASH
        else:
            effect = _fmt_diff(kind, estimate, ctx.unit, decimals)
            ci = (f"{_signed(lo * scale, decimals)} bis {_signed(hi * scale, decimals)}"
                  if lo is not None and hi is not None else _DASH)
        rows.append([c["variant"], c["baseline"], effect, ci, _p_cell(c.get("p_value")),
                     _VERDICT_WORDS.get(c["verdict"], _DASH)])
    return {"headers": headers, "rows": rows}


def _two_proportion(data: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _parse(data, default_params("builtin.two_proportion"))
    spec_name = "Zwei-Anteile-Test"
    wrong = _wrong_kind(ctx, spec_name, ("proportion",))
    if wrong:
        return wrong
    alpha = _alpha(ctx)
    warnings: List[str] = []
    groups = _groups_in_order(ctx)
    entries = _describe_all(ctx, alpha, warnings)
    results = []
    small = False
    for key in _comparison_targets(ctx, warnings):
        c, v = ctx.groups[ctx.control], ctx.groups[key]
        try:
            r = stats.two_proportion_test(c.value_sum or 0.0, c.n, v.value_sum or 0.0, v.n, alpha)
        except (ValueError, ArithmeticError):
            warnings.append(f"Variante {key}: der Test l\u00e4sst sich mit diesen Daten nicht rechnen.")
            continue
        pooled = ((c.value_sum or 0) + (v.value_sum or 0)) / (c.n + v.n)
        if min(c.n, v.n) * min(pooled, 1 - pooled) < 5:
            small = True
        diff = r["diff"] or 0.0
        results.append(_comparison(key, ctx.control, "Differenz", "Pp.", estimate=r["diff"],
                                   ci_low=r["ci_low"], ci_high=r["ci_high"], p_raw=r["p_value"],
                                   relative=r["relative_lift"], sign=diff))
    if small:
        warnings.append("Weniger als 5 erwartete Erfolge oder Misserfolge je Variante: "
                        "der p-Wert ist nur eine grobe N\u00e4herung.")
    return _finish_frequentist(ctx, spec_name, results, warnings, variant_entries=entries,
                               variant_groups=groups,
                               value_names=("estimate", "ci_low", "ci_high", "p_value", "relative"))


# -- builtin.bayes_proportion ------------------------------------------------------


def _beta_interval(a: float, b: float) -> Tuple[Optional[float], Optional[float]]:
    try:
        return stats.beta_ppf(0.025, a, b), stats.beta_ppf(0.975, a, b)
    except (ValueError, ArithmeticError):
        return None, None


def _loss_text(loss: Any, decimals: int) -> str:
    value = _finite(loss) or 0.0
    smallest = 10.0 ** -decimals
    if 0 < value * 100.0 < smallest / 2:
        return f"< {_fmt_number(smallest, decimals)} Pp."
    return _fmt_number(value * 100.0, decimals) + " Pp."


def _bayes_proportion(data: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _parse(data, default_params("builtin.bayes_proportion"))
    spec_name = "Bayes-Vergleich (Anteile)"
    wrong = _wrong_kind(ctx, spec_name, ("proportion",))
    if wrong:
        return wrong
    threshold = _param_number(ctx.params, "threshold", 0.95, 0.5, 0.999)
    prior_a = _param_number(ctx.params, "prior_a", 1.0, 0.01, 1000.0)
    prior_b = _param_number(ctx.params, "prior_b", 1.0, 0.01, 1000.0)
    warnings: List[str] = []
    groups = _groups_in_order(ctx)
    entries = _describe_all(ctx, 0.05, warnings)
    # Credible intervals of each rate instead of Wilson intervals.
    for entry, group in zip(entries, groups):
        s = group.value_sum
        if group.n > 0 and s is not None and 0 <= s <= group.n:
            entry["ci_low"], entry["ci_high"] = _beta_interval(prior_a + s, prior_b + group.n - s)

    word = {"higher": "besser als", "lower": "besser als", "none": "h\u00f6her als"}[ctx.direction]
    results = []
    for key in _comparison_targets(ctx, warnings):
        c, v = ctx.groups[ctx.control], ctx.groups[key]
        try:
            r = stats.bayes_beta_binomial(c.value_sum or 0.0, c.n, v.value_sum or 0.0, v.n,
                                          prior_a=prior_a, prior_b=prior_b)
        except (ValueError, ArithmeticError):
            warnings.append(f"Variante {key}: der Vergleich l\u00e4sst sich mit diesen Daten nicht rechnen.")
            continue
        prob_up = r["prob_better"]
        prob = 1.0 - prob_up if ctx.direction == "lower" else prob_up
        if ctx.direction == "none":
            verdict = "n/a"
        elif prob >= threshold:
            verdict = "better"
        elif prob <= 1.0 - threshold:
            verdict = "worse"
        else:
            verdict = "inconclusive"
        control_mean = (prior_a + (c.value_sum or 0.0)) / (prior_a + prior_b + c.n)
        # Expected loss of choosing the variant, in the metric's direction.
        loss = r["expected_loss"] if ctx.direction != "lower" else r["expected_loss"] + r["diff_mean"]
        results.append(_comparison(
            key, ctx.control, "Differenz", "Pp.", estimate=r["diff_mean"], ci_low=r["ci_low"],
            ci_high=r["ci_high"], prob_better=prob, verdict=verdict,
            relative=r["diff_mean"] / control_mean if control_mean > 0 else None,
            expected_loss=max(0.0, loss)))

    verdict, focus = _overall(results, ctx.direction, rank=lambda c: c["prob_better"])
    decimals = _diff_decimals(ctx, results)
    value_decimals = _value_decimals(ctx, entries)
    if focus is not None and verdict not in ("better", "worse"):
        focus = max(results, key=lambda c: c["prob_better"])
    if focus is not None:
        headline = f"P({focus['variant']} {word} {focus['baseline']}) = {_fmt_prob(focus['prob_better'])}"
    else:
        headline = f"{ctx.name}: zu wenig Daten f\u00fcr einen Vergleich"
    lines = [_intro(spec_name, ctx), ""]
    lines += [_variant_sentence(ctx, e, g, value_decimals, "95 %-Glaubw\u00fcrdigkeitsintervall")
              for e, g in zip(entries, groups)]
    if results:
        lines.append("")
    for c in results:
        lines.append(
            f"- Mit einer Wahrscheinlichkeit von {_fmt_prob(c['prob_better'])} ist {_md(c['variant'])} "
            f"{word} {_md(c['baseline'])}. Erwarteter Unterschied {_fmt_diff('proportion', c['estimate'], '', decimals)} "
            f"(95 %-Glaubw\u00fcrdigkeitsintervall {_signed(c['ci_low'] * 100.0, decimals)} bis "
            f"{_signed(c['ci_high'] * 100.0, decimals)} Pp.{_relative_text(c.get('relative'), ';')}), "
            f"erwarteter Verlust bei Wahl von {_md(c['variant'])} {_loss_text(c['expected_loss'], decimals)}"
            f"{'' if c['verdict'] == 'n/a' else ' ' + _DASH + ' ' + _VERDICT_WORDS[c['verdict']]}.")
    if not results:
        conclusion = "Ergebnis: Mit diesen Daten ist noch kein Vergleich m\u00f6glich."
    elif verdict == "inconclusive":
        conclusion = (f"Ergebnis: Keine Variante erreicht die Schwelle von {_fmt_prob(threshold)}; "
                      "ein Unterschied ist nicht belegt.")
    else:
        conclusion = _conclusion(verdict, focus, ctx, None)
    lines += ["", f"Schwelle f\u00fcr ein Urteil: {_fmt_prob(threshold)}; Prior Beta({_fmt_plain(prior_a)}, "
              f"{_fmt_plain(prior_b)}).", "", conclusion]
    if warnings:
        lines += ["", _hints(warnings)]
    values = _values_for(results, focus, {"threshold": threshold, "prior_a": prior_a,
                                          "prior_b": prior_b},
                         ("prob_better", "expected_loss", "estimate", "ci_low", "ci_high"))
    table_rows = [[c["variant"], c["baseline"], _fmt_prob(c["prob_better"]),
                   _fmt_diff("proportion", c["estimate"], "", decimals),
                   f"{_signed(c['ci_low'] * 100.0, decimals)} bis {_signed(c['ci_high'] * 100.0, decimals)}",
                   _loss_text(c["expected_loss"], decimals),
                   _VERDICT_WORDS[c["verdict"]]] for c in results]
    table = {"headers": ["Variante", "Vergleich mit", f"P({word.split()[0]})", "Unterschied",
                         "95 %-Glaubw\u00fcrdigkeitsintervall (Pp.)", "Erwarteter Verlust", "Ergebnis"],
             "rows": table_rows}
    return _output(verdict=verdict, headline=headline, summary="\n".join(lines),
                   comparisons=_public(results), variants=entries, values=values, table=table,
                   warnings=warnings)


# -- builtin.welch_t ---------------------------------------------------------------


def _welch_t(data: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _parse(data, default_params("builtin.welch_t"))
    spec_name = "Welch-t-Test"
    wrong = _wrong_kind(ctx, spec_name, _MEAN_KINDS + ("ordinal",))
    if wrong:
        return wrong
    alpha = _alpha(ctx)
    warnings: List[str] = []
    groups = _groups_in_order(ctx)
    entries = _describe_all(ctx, alpha, warnings)
    results = []
    targets = _comparison_targets(ctx, warnings)
    moments = {}
    for key in ([ctx.control] if targets else []) + targets:
        g = ctx.groups[key]
        var = stats.variance(g.value_sum, g.sum_sq, g.n)
        if g.value_sum is None or var is None:
            reason = "weniger als 2 Werte" if g.n < 2 else "die Quadratsumme fehlt"
            warnings.append(f"Variante {key}: Streuung unbekannt ({reason}); kein Test m\u00f6glich.")
            continue
        moments[key] = (g.value_sum / g.n, var, g.n)
    if ctx.control in moments:
        m1, v1, n1 = moments[ctx.control]
        for key in targets:
            if key not in moments:
                continue
            m2, v2, n2 = moments[key]
            try:
                r = stats.welch_t_test(m1, v1, n1, m2, v2, n2, alpha)
            except (ValueError, ArithmeticError):
                warnings.append(f"Variante {key}: der Test l\u00e4sst sich mit diesen Daten nicht rechnen.")
                continue
            if r["p_value"] is None:
                warnings.append(f"Variante {key}: keine Streuung in den Daten; kein Test m\u00f6glich.")
            results.append(_comparison(key, ctx.control, "Differenz", ctx.unit, estimate=r["diff"],
                                       ci_low=r["ci_low"], ci_high=r["ci_high"], p_raw=r["p_value"],
                                       relative=_relative(r["diff"], m1),
                                       sign=r["diff"] or 0.0))
    return _finish_frequentist(ctx, spec_name, results, warnings, variant_entries=entries,
                               variant_groups=groups,
                               value_names=("estimate", "ci_low", "ci_high", "p_value", "relative"))


# -- builtin.paired_t --------------------------------------------------------------


def _paired_t(data: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _parse(data, default_params("builtin.paired_t"))
    spec_name = "Gepaarter t-Test"
    wrong = _wrong_kind(ctx, spec_name, _MEAN_KINDS + ("ordinal",))
    if wrong:
        return wrong
    alpha = _alpha(ctx)
    pair_by = ctx.params.get("pair_by")
    if not isinstance(pair_by, str) or not pair_by:
        pair_by = "query"
    warnings: List[str] = []
    groups = _groups_in_order(ctx)
    entries = _describe_all(ctx, alpha, warnings)
    if ctx.rows_truncated:
        warnings.append("Nur die neuesten Zeilen wurden ausgewertet (Zeilenlimit erreicht).")
    targets = _comparison_targets(ctx, warnings)

    # Per pairing key and variant: weighted mean of the rows (sum / count).
    sums: Dict[str, Dict[str, List[float]]] = {}
    row_keys: List[Tuple[str, str]] = []
    no_key = bad_value = 0
    relevant = set(targets) | ({ctx.control} if targets else set())
    for row in ctx.rows:
        variant = row.get("variant")
        if variant is None or str(variant) not in relevant:
            continue
        variant = str(variant)
        dims = row.get("dims") if isinstance(row.get("dims"), dict) else {}
        pair = dims.get(pair_by)
        value = _finite(row.get("value"))
        count = _finite(row.get("count"))
        if count is None or count <= 0:
            count = 1.0
        if value is None:
            bad_value += 1
            continue
        if pair is None or pair == "":
            no_key += 1
            continue
        pair = str(pair)
        total = value * count if ctx.kind == "ordinal" else value
        slot = sums.setdefault(pair, {}).setdefault(variant, [0.0, 0.0])
        slot[0] += total
        slot[1] += count
        row_keys.append((pair, variant))
    if no_key:
        warnings.append(f"{no_key} {'Zeile' if no_key == 1 else 'Zeilen'} ohne Merkmal \u00ab{pair_by}\u00bb ignoriert.")
    if bad_value:
        warnings.append(f"{bad_value} {'Zeile' if bad_value == 1 else 'Zeilen'} ohne g\u00fcltigen Wert ignoriert.")
    if targets and not ctx.rows:
        warnings.append("Es wurden keine Einzelwerte \u00fcbergeben; der gepaarte Test braucht Zeilen.")

    used: set = set()
    results = []
    pairs_by_variant: Dict[str, int] = {}
    for key in targets:
        diffs, control_values = [], []
        for pair, per_variant in sums.items():
            if ctx.control in per_variant and key in per_variant:
                c_sum, c_count = per_variant[ctx.control]
                v_sum, v_count = per_variant[key]
                diffs.append(v_sum / v_count - c_sum / c_count)
                control_values.append(c_sum / c_count)
                used.add((pair, ctx.control))
                used.add((pair, key))
        pairs_by_variant[key] = len(diffs)
        if len(diffs) < 2:
            warnings.append(f"Variante {key}: zu wenige Paare ({len(diffs)}) f\u00fcr einen gepaarten Test.")
            results.append(_comparison(key, ctx.control, "Mittlere Differenz", ctx.unit,
                                       estimate=(diffs[0] if diffs else None), p_raw=None, sign=0.0))
            continue
        try:
            r = stats.paired_t_test(diffs, alpha)
        except (ValueError, ArithmeticError):
            warnings.append(f"Variante {key}: der Test l\u00e4sst sich mit diesen Daten nicht rechnen.")
            continue
        if r["p_value"] is None:
            warnings.append(f"Variante {key}: alle Paardifferenzen sind gleich; kein Test m\u00f6glich.")
        base = math.fsum(control_values) / len(control_values)
        results.append(_comparison(key, ctx.control, "Mittlere Differenz", ctx.unit,
                                   estimate=r["mean_diff"], ci_low=r["ci_low"], ci_high=r["ci_high"],
                                   p_raw=r["p_value"], relative=_relative(r["mean_diff"], base),
                                   sign=r["mean_diff"] or 0.0, n_pairs=r["n_pairs"]))
    unmatched = sum(1 for pv in row_keys if pv not in used)
    if unmatched:
        warnings.append(f"{unmatched} {'Zeile' if unmatched == 1 else 'Zeilen'} ohne Partner ignoriert.")

    def headline(ctx_, focus, decimals, level, fallback):
        text = _frequentist_headline(ctx_, focus, decimals, level, fallback)
        if text != fallback and focus.get("n_pairs"):
            text += f", {_plural(focus['n_pairs'], 'Paar', 'Paare')}"
        return text

    extra = {"pair_by": pair_by}
    for key, count in pairs_by_variant.items():
        extra[f"{key}.n_pairs" if len(pairs_by_variant) > 1 else "n_pairs"] = count
    return _finish_frequentist(ctx, spec_name, results, warnings, variant_entries=entries,
                               variant_groups=groups, headline_fn=headline, extra_values=extra,
                               value_names=("estimate", "ci_low", "ci_high", "p_value", "relative"),
                               extra_lines=["", f"Gepaart nach \u00ab{_md(pair_by)}\u00bb: "
                                            + ", ".join(f"{_md(k)} {_plural(n, 'Paar', 'Paare')}" for k, n in pairs_by_variant.items())
                                            + "."] if pairs_by_variant else ())


# -- builtin.poisson_rate ----------------------------------------------------------


def _poisson_rate(data: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _parse(data, default_params("builtin.poisson_rate"))
    spec_name = "Raten-Vergleich"
    wrong = _wrong_kind(ctx, spec_name, ("count",))
    if wrong:
        return wrong
    alpha = _alpha(ctx)
    warnings: List[str] = []
    groups = _groups_in_order(ctx)
    entries = _describe_all(ctx, alpha, warnings)
    results = []
    unbounded = False
    for key in _comparison_targets(ctx, warnings):
        c, v = ctx.groups[ctx.control], ctx.groups[key]
        try:
            r = stats.poisson_rate_test(c.value_sum or 0.0, c.n, v.value_sum or 0.0, v.n, alpha)
        except (ValueError, ArithmeticError):
            warnings.append(f"Variante {key}: der Test l\u00e4sst sich mit diesen Daten nicht rechnen.")
            continue
        if not c.value_sum and not v.value_sum:
            warnings.append(f"Keine Ereignisse in {ctx.control} und {key}.")
        elif not c.value_sum:
            unbounded = True
        sign = (r["rate2"] or 0.0) - (r["rate1"] or 0.0)
        # rate2 travels along (not in the output: _public drops it) to rank
        # the variants: with a control without events every ratio is
        # unbounded (None), but the variants' own rates still order them.
        results.append(_comparison(key, ctx.control, "Verh\u00e4ltnis der Raten", "x", estimate=r["ratio"],
                                   ci_low=r["ci_low"], ci_high=r["ci_high"], p_raw=r["p_value"],
                                   relative=(r["ratio"] - 1.0) if r["ratio"] is not None else None,
                                   sign=sign, rate2=r["rate2"]))
    if unbounded:
        warnings.append(f"Die Kontrolle {ctx.control} hat keine Ereignisse; das Verh\u00e4ltnis ist unbegrenzt.")

    def rank(c):
        rate = _finite(c.get("rate2")) or 0.0
        return -rate if ctx.direction == "lower" else rate

    def headline(ctx_, focus, decimals, level, fallback):
        if focus is None or _finite(focus.get("p_value")) is None:
            return fallback
        ci = ""
        if _finite(focus.get("ci_low")) is not None:
            ci = f" ({level} {_ratio_text(focus['ci_low'])} bis {_ratio_text(focus.get('ci_high'))})"
        return (f"{focus['variant']}: Rate \u00d7 {_ratio_text(focus.get('estimate'))} gegen\u00fcber "
                f"{focus['baseline']}{ci}, {_fmt_p(focus.get('p_value'))}")

    def sentence(ctx_, c, decimals, level, adjusted):
        # A rate comparison is a ratio, not a difference.
        p_text = _fmt_p(c.get("p_value"))
        if adjusted and _finite(c.get("p_raw")) is not None:
            p_text += f" (nach Holm; unkorrigiert {_fmt_p(c.get('p_raw'))})"
        return (f"- {_md(c['variant'])} gegen\u00fcber {_md(c['baseline'])}: Rate \u00d7 "
                f"{_ratio_text(c.get('estimate'))} ({level} {_ratio_text(c.get('ci_low'))} bis "
                f"{_ratio_text(c.get('ci_high'))}), {p_text}{_verdict_suffix(c['verdict'])}.")

    return _finish_frequentist(ctx, spec_name, results, warnings, variant_entries=entries,
                               variant_groups=groups, headline_fn=headline, sentence_fn=sentence,
                               value_names=("estimate", "ci_low", "ci_high", "p_value"),
                               rank=rank)


def _ratio_text(value: Any) -> str:
    """A rate ratio; None means unbounded (the control had no events)."""
    return _fmt_number(value, 2) if _finite(value) is not None else "unbegrenzt"


# -- builtin.ratio_delta -----------------------------------------------------------


def _ratio_delta(data: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _parse(data, default_params("builtin.ratio_delta"))
    spec_name = "Verh\u00e4ltnis-Vergleich"
    wrong = _wrong_kind(ctx, spec_name, ("ratio",))
    if wrong:
        return wrong
    alpha = _alpha(ctx)
    warnings: List[str] = []
    groups = _groups_in_order(ctx)
    entries = _describe_all(ctx, alpha, warnings)
    if ctx.rows_truncated:
        warnings.append("Nur die neuesten Zeilen wurden ausgewertet (Zeilenlimit erreicht).")
    targets = _comparison_targets(ctx, warnings)
    units: Dict[str, Tuple[List[float], List[float]]] = {}
    no_den = 0
    for row in ctx.rows:
        variant = row.get("variant")
        if variant is None:
            continue
        num, den = _finite(row.get("value")), _finite(row.get("denominator"))
        if num is None or den is None or den < 0:
            no_den += 1
            continue
        xs, ys = units.setdefault(str(variant), ([], []))
        xs.append(num)
        ys.append(den)
    if no_den:
        warnings.append(f"{no_den} {'Zeile' if no_den == 1 else 'Zeilen'} ohne g\u00fcltigen Nenner ignoriert.")
    if targets and not ctx.rows:
        warnings.append("Es wurden keine Einzelwerte \u00fcbergeben; der Vergleich braucht Zeilen.")
    results = []
    control_units = units.get(ctx.control or "", ([], []))
    if targets and len(control_units[1]) >= 2 and math.fsum(control_units[1]) == 0:
        # Said once, of the control: its zero sum blocks every comparison.
        warnings.append(f"Kontrolle {ctx.control}: die Summe des Nenners ist 0; kein Vergleich m\u00f6glich.")
    for key in targets:
        xs, ys = units.get(key, ([], []))
        if len(xs) < 2 or len(control_units[0]) < 2:
            short = key if len(xs) < 2 else ctx.control
            warnings.append(f"Variante {short}: weniger als 2 Zeilen; kein Test m\u00f6glich.")
            continue
        try:
            r = stats.ratio_delta(control_units[0], control_units[1], xs, ys, alpha)
        except (ValueError, ArithmeticError):
            warnings.append(f"Variante {key}: der Vergleich l\u00e4sst sich mit diesen Daten nicht rechnen.")
            continue
        if r["ratio1"] is None or r["ratio2"] is None:
            # Name the group whose denominators sum to 0 (the control's is
            # reported once above), not always the variant.
            if r["ratio2"] is None:
                warnings.append(f"Variante {key}: die Summe des Nenners ist 0; kein Vergleich m\u00f6glich.")
        elif r["p_value"] is None:
            warnings.append(f"Variante {key}: keine Streuung in den Daten; kein Test m\u00f6glich.")
        results.append(_comparison(key, ctx.control, "Differenz", ctx.unit, estimate=r["diff"],
                                   ci_low=r["ci_low"], ci_high=r["ci_high"], p_raw=r["p_value"],
                                   relative=_relative(r["diff"], r["ratio1"]),
                                   sign=r["diff"] or 0.0))
    return _finish_frequentist(ctx, spec_name, results, warnings, variant_entries=entries,
                               variant_groups=groups,
                               value_names=("estimate", "ci_low", "ci_high", "p_value", "relative"))


# -- builtin.chi_square ------------------------------------------------------------


def _chi_square(data: Dict[str, Any]) -> Dict[str, Any]:
    ctx = _parse(data, default_params("builtin.chi_square"))
    spec_name = "Chi-Quadrat-Test"
    wrong = _wrong_kind(ctx, spec_name, ("categorical", "ordinal"))
    if wrong:
        return wrong
    alpha = _alpha(ctx)
    warnings: List[str] = []
    groups = _groups_in_order(ctx)
    entries = _describe_all(ctx, alpha, warnings)
    variant_groups = [ctx.groups[k] for k in ctx.order if ctx.has_data(k) and ctx.groups[k].levels]
    lines = [_intro(spec_name, ctx), ""]
    value_decimals = _value_decimals(ctx, entries)
    lines += [_variant_sentence(ctx, e, g, value_decimals, _level_text(alpha))
              for e, g in zip(entries, groups)]
    values: Dict[str, Any] = {"alpha": alpha}
    table = _variants_table(ctx, entries, groups, value_decimals, _level_text(alpha))

    if len(variant_groups) >= 2:
        categories = _categories(ctx, variant_groups)
        observed = [[g.levels.get(c, 0.0) for c in categories] for g in variant_groups]
        overall = stats.chi_square_independence(observed)
        _expected_warning(observed, warnings)
        values.update({"test": "independence", "chi2": overall["chi2"], "df": overall["df"],
                       "p_value": overall["p_value"], "cramers_v": overall["cramers_v"]})
        results = []
        control = ctx.groups.get(ctx.control or "")
        targets = _comparison_targets(ctx, warnings) if control is not None else []
        for key in targets:
            v = ctx.groups[key]
            if not control.levels or not v.levels:
                continue
            pair = stats.chi_square_independence(
                [[control.levels.get(c, 0.0) for c in categories], [v.levels.get(c, 0.0) for c in categories]])
            if ctx.kind == "ordinal" and control.value_sum is not None and v.value_sum is not None:
                c_mean, v_mean = control.value_sum / control.n, v.value_sum / v.n
                results.append(_comparison(key, ctx.control, "Differenz der mittleren Stufe", ctx.unit,
                                           estimate=v_mean - c_mean, p_raw=pair["p_value"],
                                           relative=_relative(v_mean - c_mean, c_mean),
                                           sign=v_mean - c_mean))
            else:
                results.append(_comparison(key, ctx.control, "Cram\u00e9rs V", "", estimate=pair["cramers_v"],
                                           p_raw=pair["p_value"], sign=0.0))
        _adjust(results, ctx, warnings)
        # The chi-square test asks whether the distributions differ at all,
        # not whether one is shifted up or down: a large change of shape
        # (everyone at 3 against half at 1 and half at 5) is significant
        # with a mean that barely moves. So it names no better or worse
        # variant, on a scale either; the mean level is welch_t's question.
        for c in results:
            c["verdict"] = "n/a"
        verdict = "n/a" if results else "inconclusive"
        p = overall["p_value"]
        differs = p is not None and p < alpha
        stat_text = (f"\u03c7\u00b2 = {_fmt_number(overall['chi2'], 2)}, df = {overall['df']}, {_fmt_p(p)}")
        if differs:
            headline = f"Verteilung unterscheidet sich zwischen den Varianten ({stat_text})"
        else:
            headline = f"Kein belegter Unterschied in der Verteilung ({stat_text})"
        lines += ["", f"Unabh\u00e4ngigkeitstest \u00fcber alle Varianten: {stat_text}, "
                  f"Cram\u00e9rs V = {_fmt_number(overall['cramers_v'], 3)}. "
                  + ("Die Verteilungen unterscheiden sich." if differs else "Ein Unterschied der Verteilungen ist nicht belegt.")]
        for c in results:
            if ctx.kind == "ordinal":
                effect = f"mittlere Stufe {_signed(c['estimate'], 2)}"
            else:
                effect = f"Cram\u00e9rs V {_fmt_number(c['estimate'], 3)}"
            lines.append(f"- {_md(c['variant'])} gegen\u00fcber {_md(c['baseline'])}: {effect}, "
                         f"{_fmt_p(c['p_value'])}{_verdict_suffix(c['verdict'])}.")
        if ctx.kind == "ordinal":
            conclusion = ("Ergebnis: Der Chi-Quadrat-Test pr\u00fcft nur, ob sich die Verteilung der "
                          "Stufen unterscheidet, nicht welche Variante besser ist; das zeigt der "
                          "Welch-t-Test auf die mittlere Stufe.")
        else:
            conclusion = ("Ergebnis: Eine Verteilung \u00fcber Kategorien hat keine Richtung; es gibt "
                          "kein Urteil \u00fcber besser oder schlechter.")
        lines += ["", conclusion]
        if warnings:
            lines += ["", _hints(warnings)]
        return _output(verdict=verdict, headline=headline, summary="\n".join(lines),
                       comparisons=_public(results), variants=entries, values=values, table=table,
                       warnings=warnings)

    # One group with data: goodness of fit against equal shares or ``expected``.
    single = variant_groups[0] if variant_groups else (
        ctx.null_group if ctx.null_group is not None and ctx.null_group.levels else None)
    if single is None:
        warnings.append("Noch keine Messwerte mit Kategorien.")
        return _output(verdict="inconclusive", headline=f"{ctx.name}: noch keine Messwerte",
                       summary="\n".join(lines + ["", "Es gibt noch keine Messwerte."]),
                       variants=entries, values=values, table=table, warnings=warnings)
    categories = _categories(ctx, [single])
    observed = [single.levels.get(c, 0.0) for c in categories]
    expected = ctx.params.get("expected")
    weights = None
    if isinstance(expected, list) and expected:
        weights = [_finite(w) for w in expected]
        if len(weights) != len(categories) or any(w is None or w <= 0 for w in weights):
            warnings.append(f"Die erwarteten Anteile passen nicht zu den {len(categories)} Kategorien.")
            return _output(verdict="inconclusive", headline=f"{ctx.name}: erwartete Anteile passen nicht",
                           summary="\n".join(lines + ["", warnings[-1]]), variants=entries,
                           values=values, table=table, warnings=warnings)
    if len(categories) < 2:
        warnings.append("Es gibt nur eine Kategorie; kein Test m\u00f6glich.")
        return _output(verdict="inconclusive", headline=f"{ctx.name}: nur eine Kategorie",
                       summary="\n".join(lines + ["", warnings[-1]]), variants=entries,
                       values=values, table=table, warnings=warnings)
    total = sum(observed)
    reference = "den erwarteten Anteilen" if weights else "gleichen Anteilen"
    reference_acc = "die erwarteten Anteile" if weights else "gleiche Anteile"
    who = f"von Variante {_md(single.key)}" if single.key is not None else "der Messwerte ohne Variante"
    if len(categories) == 2 and total < 30:
        share = (weights[0] / (weights[0] + weights[1])) if weights else 0.5
        p = stats.binomial_test(observed[0], total, share)["p_value"]
        values.update({"test": "binomial", "p_value": p})
        stat_text = f"exakter Binomialtest, {_fmt_p(p)}"
    else:
        gof = stats.chi_square_goodness_of_fit(observed, weights)
        p = gof["p_value"]
        values.update({"test": "goodness_of_fit", "chi2": gof["chi2"], "df": gof["df"], "p_value": p})
        _expected_warning([observed], warnings, expected_shares=weights)
        stat_text = f"\u03c7\u00b2 = {_fmt_number(gof['chi2'], 2)}, df = {gof['df']}, {_fmt_p(p)}"
    differs = p is not None and p < alpha
    headline = (f"Verteilung weicht von {reference} ab ({stat_text})" if differs
                else f"Verteilung passt zu {reference} ({stat_text})")
    lines += ["", f"Test der Verteilung {who} gegen {reference_acc}: {stat_text}. "
              + ("Die Verteilung weicht ab." if differs else "Eine Abweichung ist nicht belegt."),
              "", "Ergebnis: Ein Test gegen feste Anteile vergleicht keine Varianten; es gibt kein Urteil \u00fcber besser oder schlechter."]
    if warnings:
        lines += ["", _hints(warnings)]
    return _output(verdict="n/a", headline=headline, summary="\n".join(lines), variants=entries,
                   values=values, table=table, warnings=warnings)


def _expected_warning(observed: List[List[float]], warnings: List[str], expected_shares=None) -> None:
    """Cochran's rule: more than 20 % of expected counts below 5."""
    if len(observed) == 1:
        row = observed[0]
        total = sum(row)
        weights = expected_shares or [1.0] * len(row)
        wsum = sum(weights)
        expected = [total * w / wsum for w in weights]
    else:
        rows = [r for r in observed if sum(r) > 0]
        cols = [j for j in range(len(observed[0])) if any(r[j] > 0 for r in rows)]
        total = sum(sum(r) for r in rows)
        if not rows or not cols or total <= 0:
            return
        expected = [sum(r) * sum(rr[j] for rr in rows) / total for r in rows for j in cols]
    if expected and sum(1 for e in expected if e < 5) > 0.2 * len(expected):
        warnings.append("Mehr als 20 % der erwarteten H\u00e4ufigkeiten liegen unter 5; "
                        "der p-Wert ist nur eine N\u00e4herung.")


# -- registry --------------------------------------------------------------------


@dataclass(frozen=True)
class BuiltinSpec:
    key: str
    name: str
    description: str
    input_kinds: Tuple[str, ...]
    params_schema: dict
    needs_rows: bool
    fn: Callable[[dict], dict]


def _closed(properties: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "properties": properties}


_ALPHA_SCHEMA = {"type": "number", "minimum": 0.001, "maximum": 0.2, "default": 0.05,
                 "description": "Signifikanzniveau (Standard 0,05)."}
_CORRECTION_SCHEMA = {"type": "string", "enum": ["holm", "none"], "default": "holm",
                      "description": "Korrektur f\u00fcr mehrere Vergleiche: holm oder none."}
_TEST_SCHEMA = _closed({"alpha": _ALPHA_SCHEMA, "correction": _CORRECTION_SCHEMA})


def _schema(properties: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(_closed(properties)))


BUILTINS: Dict[str, BuiltinSpec] = {
    spec.key: spec for spec in (
        BuiltinSpec(
            key="builtin.describe", name="Beschreibung je Variante",
            description=("Anzahl, Sch\u00e4tzwert und 95 %-Intervall je Variante (Wilson f\u00fcr Anteile, "
                         "t-Intervall f\u00fcr Mittelwerte, exakt f\u00fcr Raten); pr\u00fcft die Leitplanke und "
                         "optional ein Ziel (Parameter target)."),
            input_kinds=_ALL_KINDS,
            params_schema=_schema({"target": {"type": "number",
                                              "description": "Zielwert in der Einheit der Metrik (Anteile als 0..1)."}}),
            needs_rows=False, fn=_describe),
        BuiltinSpec(
            key="builtin.two_proportion", name="Zwei-Anteile-Test",
            description=("Vergleicht jeden Anteil mit der Kontrolle: Differenz in Prozentpunkten mit "
                         "z-Test und dem dazu passenden Score-Intervall (Mee); mehrere Vergleiche "
                         "nach Holm korrigiert."),
            input_kinds=("proportion",), params_schema=_schema(_TEST_SCHEMA["properties"]),
            needs_rows=False, fn=_two_proportion),
        BuiltinSpec(
            key="builtin.bayes_proportion", name="Bayes-Vergleich (Anteile)",
            description=("Wahrscheinlichkeit, dass eine Variante besser ist als die Kontrolle "
                         "(Beta-Binomial-Modell), mit erwartetem Unterschied und erwartetem Verlust."),
            input_kinds=("proportion",),
            params_schema=_schema({
                "threshold": {"type": "number", "minimum": 0.5, "maximum": 0.999, "default": 0.95,
                              "description": "Ab dieser Wahrscheinlichkeit gilt eine Variante als besser."},
                "prior_a": {"type": "number", "minimum": 0.01, "maximum": 1000, "default": 1.0,
                            "description": "Prior Beta(a, b): a."},
                "prior_b": {"type": "number", "minimum": 0.01, "maximum": 1000, "default": 1.0,
                            "description": "Prior Beta(a, b): b."},
            }),
            needs_rows=False, fn=_bayes_proportion),
        BuiltinSpec(
            key="builtin.welch_t", name="Welch-t-Test",
            description=("Vergleicht Mittelwerte mit der Kontrolle (ungleiche Varianzen erlaubt); "
                         "braucht die Quadratsumme oder Einzelwerte."),
            input_kinds=_MEAN_KINDS + ("ordinal",), params_schema=_schema(_TEST_SCHEMA["properties"]),
            needs_rows=False, fn=_welch_t),
        BuiltinSpec(
            key="builtin.paired_t", name="Gepaarter t-Test",
            description=("Vergleicht Paare mit gleichem Merkmal (z. B. dieselbe Anfrage in Kontrolle "
                         "und Kandidat); Parameter pair_by nennt das Merkmal in dims."),
            input_kinds=_MEAN_KINDS + ("ordinal",),
            params_schema=_schema({
                "alpha": _ALPHA_SCHEMA, "correction": _CORRECTION_SCHEMA,
                "pair_by": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,40}$", "default": "query",
                            "description": "Merkmal in dims, das die Paare bildet."},
            }),
            needs_rows=True, fn=_paired_t),
        BuiltinSpec(
            key="builtin.poisson_rate", name="Raten-Vergleich",
            description=("Vergleicht Ereignisraten (Ereignisse je Einheit) mit der Kontrolle: "
                         "Verh\u00e4ltnis der Raten mit exaktem Intervall und exaktem Test."),
            input_kinds=("count",), params_schema=_schema(_TEST_SCHEMA["properties"]),
            needs_rows=False, fn=_poisson_rate),
        BuiltinSpec(
            key="builtin.ratio_delta", name="Verh\u00e4ltnis-Vergleich",
            description=("Vergleicht Verh\u00e4ltnisse wie Kosten pro Klick mit der Kontrolle "
                         "(Delta-Methode, jede Zeile eine Einheit)."),
            input_kinds=("ratio",), params_schema=_schema(_TEST_SCHEMA["properties"]),
            needs_rows=True, fn=_ratio_delta),
        BuiltinSpec(
            key="builtin.chi_square", name="Chi-Quadrat-Test",
            description=("Vergleicht Verteilungen \u00fcber Kategorien oder Stufen zwischen den Varianten; "
                         "mit nur einer Gruppe Test gegen gleiche oder erwartete Anteile."),
            input_kinds=("categorical", "ordinal"),
            params_schema=_schema({
                "alpha": _ALPHA_SCHEMA, "correction": _CORRECTION_SCHEMA,
                "expected": {"type": "array", "minItems": 2, "maxItems": 50,
                             "items": {"type": "number", "exclusiveMinimum": 0},
                             "description": "Erwartete Anteile je Kategorie (Gewichte > 0)."},
            }),
            needs_rows=False, fn=_chi_square),
    )
}


def default_params(key: str) -> dict:
    """The defaults of a built-in's parameters ({} for other evaluators)."""
    spec = BUILTINS.get(key)
    if spec is None:
        return {}
    return {name: prop["default"] for name, prop in spec.params_schema.get("properties", {}).items()
            if isinstance(prop, dict) and "default" in prop}


# -- parameters ------------------------------------------------------------------

_PARAMS_MAX_BYTES = 4096

_TYPE_WORDS = {"number": "eine Zahl", "integer": "eine ganze Zahl", "string": "ein Text",
               "boolean": "true oder false", "array": "eine Liste", "object": "ein Objekt",
               "null": "null"}


def _schema_number(value: Any) -> str:
    return _fmt_plain(value) if _finite(value) is not None else str(value)


def _error_message(error) -> str:
    kind = error.validator
    limit = error.validator_value
    if kind == "type":
        wanted = limit if isinstance(limit, list) else [limit]
        return "Erwartet " + " oder ".join(_TYPE_WORDS.get(str(w), str(w)) for w in wanted) + "."
    if kind == "minimum":
        return f"Muss mindestens {_schema_number(limit)} sein."
    if kind == "maximum":
        return f"Darf h\u00f6chstens {_schema_number(limit)} sein."
    if kind == "exclusiveMinimum":
        return f"Muss gr\u00f6sser als {_schema_number(limit)} sein."
    if kind == "exclusiveMaximum":
        return f"Muss kleiner als {_schema_number(limit)} sein."
    if kind == "enum" and isinstance(limit, list):
        return ("Erlaubt: " + ", ".join(str(v) for v in limit[:10]) + ".")[:200]
    if kind == "pattern":
        return "Ung\u00fcltiges Format."
    if kind == "minLength":
        return f"Mindestens {limit} Zeichen."
    if kind == "maxLength":
        return f"H\u00f6chstens {limit} Zeichen."
    if kind == "minItems":
        return f"Mindestens {limit} Eintr\u00e4ge."
    if kind == "maxItems":
        return f"H\u00f6chstens {limit} Eintr\u00e4ge."
    if kind == "uniqueItems":
        return "Eintr\u00e4ge d\u00fcrfen sich nicht wiederholen."
    return "Ung\u00fcltiger Wert."


def _field_path(path) -> str:
    return "params" + "".join(f".{p}" for p in path)


def validate_params(schema: dict, params: dict | None) -> dict:
    """Check evaluator parameters against its JSON Schema; a deep copy back.

    At most 4 KB of JSON, finite numbers only. Errors come back as German
    messages per field (``params.alpha``: "Darf h\u00f6chstens 0,2 sein."), never
    as the validator's English text.
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValidationError("Die Parameter m\u00fcssen ein JSON-Objekt sein.",
                              fields={"params": "Bitte ein JSON-Objekt angeben."})
    try:
        text = json.dumps(params, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ValidationError("Die Parameter enthalten ung\u00fcltige Werte.",
                              fields={"params": "Nur JSON-Werte mit endlichen Zahlen sind erlaubt."}) from None
    if len(text.encode("utf-8")) > _PARAMS_MAX_BYTES:
        raise ValidationError("Die Parameter sind zu gross (h\u00f6chstens 4 KB).",
                              fields={"params": "H\u00f6chstens 4 KB."})
    if not isinstance(schema, dict):
        raise ValidationError("Das Parameterschema des Auswerters ist ung\u00fcltig.")
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        errors = list(validator.iter_errors(params))
    except (SchemaError, RecursionError, TypeError, ValueError):
        raise ValidationError("Das Parameterschema des Auswerters ist ung\u00fcltig.") from None
    if errors:
        fields: Dict[str, str] = {}
        for error in sorted(errors, key=lambda e: [str(p) for p in e.absolute_path]):
            path = list(error.absolute_path)
            if error.validator == "additionalProperties" and isinstance(error.instance, dict):
                known = set((error.schema or {}).get("properties", {}) or {})
                for extra in sorted(k for k in error.instance if k not in known):
                    fields.setdefault(_field_path(path + [extra])[:200], "Unbekannter Parameter.")
                continue
            if error.validator == "required" and isinstance(error.instance, dict):
                for missing in error.validator_value or []:
                    if missing not in error.instance:
                        fields.setdefault(_field_path(path + [missing])[:200], "Pflichtangabe.")
                continue
            fields.setdefault(_field_path(path)[:200], _error_message(error))
            if len(fields) >= 20:
                break
        raise ValidationError("Die Parameter sind ung\u00fcltig.", fields=dict(list(fields.items())[:20]))
    return json.loads(text)


# -- input -----------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """A JSON-safe copy: non-finite floats become None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def build_input(*, snapshot: dict, metric: dict, aggregates: list, rows: list | None,
                rows_truncated: bool, params: dict, scope: dict) -> dict:
    """The evaluator input contract (plan section 7) from an experiment snapshot.

    ``metric`` is the snapshot's metric entry (its aggregates are not copied:
    ``aggregates`` are the ones computed for this evaluation's scope).
    """
    snapshot = snapshot or {}
    domain = snapshot.get("domain") or {}
    type_ = snapshot.get("type") or {}
    experiment = {
        "key": snapshot.get("key"),
        "title": snapshot.get("title") or "",
        "hypothesis": snapshot.get("hypothesis") or "",
        "domain": domain.get("key") if isinstance(domain, dict) else None,
        "type": type_.get("key") if isinstance(type_, dict) else None,
        "status": snapshot.get("status"),
        "fields": dict(snapshot.get("field_values") or {}),
        "tags": list(snapshot.get("tags") or []),
    }
    metric_entry = {k: v for k, v in (metric or {}).items() if k != "aggregates"}
    op, limit = metric_entry.get("guardrail_op"), _finite(metric_entry.get("guardrail_value"))
    existing = metric_entry.get("guardrail")
    if op is None and isinstance(existing, dict):
        # An entry that already has the input shape (build_input applied twice).
        op, limit = existing.get("op"), _finite(existing.get("value"))
    metric_entry["guardrail"] = {"op": op, "value": limit} if op in ("max", "min") and limit is not None else None
    metric_entry["definition"] = dict(metric_entry.get("definition") or {})
    variants = [{"key": v.get("key"), "name": v.get("name") or "", "is_control": bool(v.get("is_control"))}
                for v in (snapshot.get("variants") or []) if isinstance(v, dict)]
    result = _jsonable({
        "experiment": experiment,
        "metric": metric_entry,
        "variants": variants,
        "aggregates": [dict(a) for a in (aggregates or []) if isinstance(a, dict)],
        "rows": [],
        "rows_truncated": bool(rows_truncated),
        "scope": dict(scope or {}),
        "params": dict(params or {}),
    })
    # Rows come from the database, which refuses non-finite numbers; up to
    # evaluator_max_rows of them are passed on without a copy per row.
    result["rows"] = list(rows) if rows else []
    return result


def run_builtin(key: str, data: dict) -> dict:
    """Validate data["params"] against the built-in's schema, run it, return
    the sanitized output."""
    spec = BUILTINS.get(key)
    if spec is None:
        raise NotFound("Diesen Auswerter gibt es nicht.")
    if not isinstance(data, dict):
        raise ValidationError("Die Eingabe der Auswertung ist ung\u00fcltig.")
    params = validate_params(spec.params_schema, data.get("params"))
    merged = default_params(key)
    merged.update(params)
    payload = dict(data)
    payload["params"] = merged
    try:
        raw = spec.fn(payload)
    except (ValueError, ArithmeticError) as exc:
        # Data far outside anything plausible (counts near the float limit)
        # can still defeat a numeric routine. The evaluation then says so
        # instead of failing; the log keeps the cause for a developer.
        log.warning("builtin evaluator %s could not compute: %s: %s", key, type(exc).__name__, exc)
        warning = "Die Auswertung l\u00e4sst sich mit diesen Daten nicht rechnen."
        raw = {"verdict": "inconclusive", "headline": f"{spec.name}: nicht berechenbar",
               "summary": f"**{spec.name}**: {warning}", "warnings": [warning]}
    metric = data.get("metric") if isinstance(data.get("metric"), dict) else {}
    kind = metric.get("kind") if isinstance(metric.get("kind"), str) else None
    return sanitize_output(raw, evaluator_name=spec.name, metric_kind=kind)


# -- output ----------------------------------------------------------------------

_MAX_OUTPUT_BYTES = 256 * 1024
_COMPARISON_KEYS = ("variant", "baseline", "label", "estimate", "ci_low", "ci_high", "p_value",
                    "prob_better", "relative", "unit", "verdict")
_COMPARISON_NUMBERS = ("estimate", "ci_low", "ci_high", "p_value", "prob_better", "relative")
_VARIANT_KEYS = ("variant", "n", "value", "sd", "sum", "ci_low", "ci_high")
_SCALARS = (str, int, float, bool, type(None))


def _clean_text(value: Any, limit: int, *, single_line: bool = False) -> str:
    """Text safe for JSONB and the page: no NUL or other control characters
    (tab and newline stay in multi-line text), no lone surrogates, clipped."""
    if not isinstance(value, str):
        value = str(value)
    if len(value) > limit * 2 + 64:
        value = value[: limit * 2 + 64]
    chars = []
    for ch in value:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            continue
        if code < 32 or code == 127:
            if not single_line and ch in "\n\t":
                chars.append(ch)
            elif ch in "\r\n\t":
                chars.append(" ")
            continue
        chars.append(ch)
    text = "".join(chars)
    if single_line:
        text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "\u2026"
    return text


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        if abs(value) <= 2 ** 53:
            return value
        try:
            value = float(value)
        except OverflowError:
            return None
    return value if math.isfinite(value) else None


def _verdict(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in VERDICTS:
        return value.strip().lower()
    return "n/a"


def _key_text(key: Any) -> str:
    return _clean_text(key, 100, single_line=True)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            value = json.dumps(value, ensure_ascii=False, allow_nan=False)[:400]
        except (TypeError, ValueError):
            value = ""
    return _clean_text(value, 200, single_line=True)


def _size(obj: Any) -> int:
    return len(json.dumps(obj))


class _Names:
    """Names for a warning: the first few spelled out, the rest counted, so a
    runner output with a million stray keys costs no more than one with ten."""

    def __init__(self, limit: int = 30) -> None:
        self.limit = limit
        self.names: List[str] = []
        self.more = 0

    def add(self, name: str) -> None:
        if name in self.names:
            return
        if len(self.names) < self.limit:
            self.names.append(name)
        else:
            self.more += 1

    def __bool__(self) -> bool:
        return bool(self.names)

    def text(self) -> str:
        return ", ".join(self.names) + (f" (und {self.more} weitere)" if self.more else "")


def _missing_unit(entry: Dict[str, Any], metric_kind: Optional[str]) -> Optional[str]:
    """The unit of a comparison that came without one.

    On a proportion metric a difference of shares (every number within
    -1..1) is in percentage points: "Pp.", as the built-ins write it, so
    0.0041 reads "+0,41 Pp." and not "+0,00". Otherwise null, which the page
    reads as "the metric's own unit" (an explicit "" stays "no unit").
    """
    if metric_kind == "proportion":
        numbers = [entry.get(k) for k in ("estimate", "ci_low", "ci_high")]
        numbers = [x for x in numbers if x is not None]
        if numbers and all(-1.0 <= x <= 1.0 for x in numbers):
            return "Pp."
    return None


def sanitize_output(raw, *, evaluator_name: str = "", metric_kind: Optional[str] = None) -> dict:
    """Bring any evaluator's output into the output contract.

    Keeps the contract keys; unknown top-level scalars move into ``values``,
    everything else unknown is dropped, each with a warning. Enforces every
    size limit of the plan (section 7), turns NaN and infinity into null,
    strips control characters and lone surrogates (PostgreSQL's JSONB refuses
    them), and keeps the whole object below 256 KB of JSON.

    A comparison without ``unit`` (or with null) gets "Pp." when
    ``metric_kind`` is "proportion" and its numbers are a difference of
    shares, else null (the page then uses the metric's unit); see
    _missing_unit.
    """
    if not isinstance(raw, dict):
        raise ValidationError("Der Auswerter hat kein Objekt zur\u00fcckgegeben.")
    notes: List[str] = []
    dropped = _Names()

    values: Dict[str, Any] = {}
    raw_values = raw.get("values")
    if isinstance(raw_values, dict):
        for key, value in raw_values.items():
            name = _key_text(key)
            if len(values) >= 100:
                dropped.add(f"values.{name}")
                continue
            if isinstance(value, bool) or value is None:
                values[name] = value
            elif isinstance(value, (int, float)):
                values[name] = _number(value)
            elif isinstance(value, str):
                values[name] = _clean_text(value, 500)
            else:
                dropped.add(f"values.{name}")
    elif raw_values is not None:
        dropped.add("values")

    moved = _Names(limit=100)
    for key, value in raw.items():
        if key in OUTPUT_KEYS:
            continue
        name = _key_text(key)
        if isinstance(value, _SCALARS) and name not in values and len(values) < 100:
            values[name] = (value if isinstance(value, bool) or value is None
                            else _number(value) if isinstance(value, (int, float))
                            else _clean_text(value, 500))
            moved.add(name)
        else:
            dropped.add(name)

    headline_raw = raw.get("headline")
    if isinstance(headline_raw, (str, int, float)) and not isinstance(headline_raw, bool) \
            and str(headline_raw).strip():
        headline = _clean_text(headline_raw, 200, single_line=True)
    else:
        headline = _clean_text(f"{evaluator_name or 'Auswertung'}: {len(values)} Werte", 200,
                               single_line=True)

    summary_raw = raw.get("summary")
    summary = _clean_text(summary_raw, 20000) if isinstance(summary_raw, str) else ""
    if summary_raw is not None and not isinstance(summary_raw, str):
        dropped.add("summary")

    comparisons = []
    raw_comparisons = raw.get("comparisons")
    if isinstance(raw_comparisons, list):
        for item in raw_comparisons[:50]:
            if not isinstance(item, dict):
                dropped.add("comparisons[]")
                continue
            entry = {}
            for name in _COMPARISON_KEYS:
                value = item.get(name)
                if name in _COMPARISON_NUMBERS:
                    entry[name] = _number(value)
                elif name == "verdict":
                    entry[name] = _verdict(value)
                elif name == "unit":
                    entry[name] = _clean_text(value, 20, single_line=True) if value is not None else None
                elif name == "label":
                    entry[name] = _clean_text(value, 80, single_line=True) if value is not None else ""
                else:
                    entry[name] = _clean_text(value, 100, single_line=True) if value is not None else None
            if entry["unit"] is None:
                entry["unit"] = _missing_unit(entry, metric_kind)
            for extra in list(item)[:100]:
                if extra not in _COMPARISON_KEYS:
                    dropped.add(f"comparisons.{_key_text(extra)}")
            comparisons.append(entry)
        if len(raw_comparisons) > 50:
            notes.append("Nur die ersten 50 Vergleiche \u00fcbernommen.")
    elif raw_comparisons is not None:
        dropped.add("comparisons")

    variants = []
    raw_variants = raw.get("variants")
    if isinstance(raw_variants, list):
        for item in raw_variants[:50]:
            if not isinstance(item, dict):
                dropped.add("variants[]")
                continue
            entry = {}
            for name in _VARIANT_KEYS:
                value = item.get(name)
                if name == "variant":
                    entry[name] = _clean_text(value, 100, single_line=True) if value is not None else None
                else:
                    number = _number(value)
                    if name == "n" and isinstance(number, float) and number.is_integer() and abs(number) <= 2 ** 53:
                        number = int(number)
                    entry[name] = number
            for extra in list(item)[:100]:
                if extra not in _VARIANT_KEYS:
                    dropped.add(f"variants.{_key_text(extra)}")
            variants.append(entry)
        if len(raw_variants) > 50:
            notes.append("Nur die ersten 50 Varianten \u00fcbernommen.")
    elif raw_variants is not None:
        dropped.add("variants")

    table = {"headers": [], "rows": []}
    raw_table = raw.get("table")
    if isinstance(raw_table, dict):
        headers = raw_table.get("headers")
        rows = raw_table.get("rows")
        if isinstance(headers, list):
            table["headers"] = [_cell(h) for h in headers[:20]]
        if isinstance(rows, list):
            for row in rows[:200]:
                if isinstance(row, (list, tuple)):
                    table["rows"].append([_cell(c) for c in list(row)[:20]])
            if len(rows) > 200:
                notes.append("Nur die ersten 200 Tabellenzeilen \u00fcbernommen.")
    elif raw_table is not None:
        dropped.add("table")

    warnings = []
    raw_warnings = raw.get("warnings")
    if isinstance(raw_warnings, list):
        warnings = [_clean_text(w, 300, single_line=True) for w in raw_warnings[:40]
                    if isinstance(w, (str, int, float)) and not isinstance(w, bool)]
    elif isinstance(raw_warnings, str):
        warnings = [_clean_text(raw_warnings, 300, single_line=True)]

    if moved:
        notes.append(_clean_text("Nicht vorgesehene Felder nach values verschoben: " + moved.text(), 300))
    if dropped:
        notes.append(_clean_text("Nicht \u00fcbernommen: " + dropped.text(), 300))

    out = {
        "verdict": _verdict(raw.get("verdict")),
        "headline": headline,
        "summary": summary,
        "comparisons": comparisons,
        "variants": variants,
        "values": values,
        "table": table,
        "warnings": [],
    }
    _set_warnings(out, warnings, notes)
    if _size(out) > _MAX_OUTPUT_BYTES:
        _shrink(out)
        _set_warnings(out, warnings, notes + ["Ausgabe gek\u00fcrzt."])
        _shrink(out)
    return out


def _set_warnings(out: Dict[str, Any], own: List[str], notes: List[str]) -> None:
    """Evaluator warnings first, but never at the expense of our own notes."""
    notes = [n for n in notes if n][:20]
    room = max(0, 20 - len(notes))
    out["warnings"] = [w for w in own if w][:room] + notes


def _shrink(out: Dict[str, Any]) -> None:
    """Clip the table, then the summary, then values, until the JSON fits."""
    rows = out["table"]["rows"]
    if _size(out) > _MAX_OUTPUT_BYTES and rows:
        without = _size({**out, "table": {"headers": out["table"]["headers"], "rows": []}})
        budget = _MAX_OUTPUT_BYTES - without
        kept, used = [], 0
        for row in rows:
            cost = _size(row) + 2
            if used + cost > budget:
                break
            kept.append(row)
            used += cost
        out["table"]["rows"] = kept
    if _size(out) > _MAX_OUTPUT_BYTES and out["summary"]:
        excess = _size(out) - _MAX_OUTPUT_BYTES
        summary = out["summary"]
        # One character costs up to six bytes of escaped JSON.
        keep = max(0, len(summary) - excess - 16)
        while keep > 0 and _size({**out, "summary": summary[:keep] + "\u2026"}) > _MAX_OUTPUT_BYTES:
            keep = keep // 2 if keep > 64 else keep - 16
        out["summary"] = (summary[: max(keep, 0)] + "\u2026") if keep > 0 else ""
    if _size(out) > _MAX_OUTPUT_BYTES and out["values"]:
        for key in sorted(out["values"], key=lambda k: -_size(out["values"][k])):
            if _size(out) <= _MAX_OUTPUT_BYTES:
                break
            del out["values"][key]
    if _size(out) > _MAX_OUTPUT_BYTES:
        out["table"] = {"headers": [], "rows": []}
    if _size(out) > _MAX_OUTPUT_BYTES:
        out["comparisons"] = out["comparisons"][:10]
        out["variants"] = out["variants"][:10]
