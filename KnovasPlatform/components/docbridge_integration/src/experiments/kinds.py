"""Measurement kinds: how one row of ``exp_measurements`` is read.

Every row stores sufficient statistics (value, count, denominator, sum_sq), so
a row can be one observation or a pre-aggregated block -- a day of an ad
campaign, one CI run, a hundred survey answers at the same level. What the
four columns mean depends on the metric's kind; the plan's "Measurement rows"
section is the contract, and this module is the one place that enforces it
before a row is written. A row that slips past here would silently skew
every later estimate, so validation is strict and every refusal says, in
German, which value is wrong.

The ``format_*`` helpers are mirrored by ``KX.fmtNumber`` / ``fmtEstimate`` /
``fmtDiff`` in experiments_common.js. Both follow the same rules (decimal
comma, ASCII apostrophe as thousands separator, percent with a space,
proportion differences in percentage points) so a number reads the same on
the page, in an API answer and in the Markdown Knovas indexes. Intl de-CH is
deliberately not used: its separators differ between browsers.
"""

from __future__ import annotations

import decimal
import math
import numbers
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from experiments.errors import ValidationError

#: Shown wherever a number is missing or not finite.
DASH = "\u2013"

#: Counts must stay exact in a JSON double (and in JavaScript), and they sum
#: into a BIGINT; 2**53 - 1 keeps both true with room for many rows.
MAX_COUNT = 2 ** 53 - 1

#: Kinds whose rows are sums of observations with an optional sum of squares.
MEAN_LIKE_KINDS = frozenset({"mean", "duration", "currency"})

#: Kinds whose value is a level that ``definition.levels`` may name.
LEVEL_KINDS = frozenset({"ordinal", "categorical"})

#: Kinds whose values ``definition.min`` / ``definition.max`` bound.
BOUNDED_KINDS = frozenset({"mean", "duration", "currency", "ordinal"})

_SUM_SQ_LABEL = "Quadratsumme (optional)"

#: Enough digits for any finite double (up to 309 before the point) plus
#: ten decimals, so quantize never runs out of precision.
_DECIMAL_CONTEXT = decimal.Context(prec=400)


@dataclass(frozen=True)
class KindSpec:
    """One measurement kind as the UI and the evaluators see it.

    An empty ``denominator_label`` / ``sum_sq_label`` means the kind does not
    use that column: the entry form leaves the input out and ``validate_row``
    stores NULL there.
    """

    key: str
    label: str
    description: str
    needs_denominator: bool
    #: Rows describe a distribution over levels (aggregates carry ``levels``).
    is_distribution: bool
    #: The value must be a whole number (successes, events, a category code).
    integral_value: bool
    value_label: str
    count_label: str
    denominator_label: str
    sum_sq_label: str
    default_evaluators: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        """JSON-ready form for the API ``meta`` answer."""
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "needs_denominator": self.needs_denominator,
            "is_distribution": self.is_distribution,
            "integral_value": self.integral_value,
            "value_label": self.value_label,
            "count_label": self.count_label,
            "denominator_label": self.denominator_label,
            "sum_sq_label": self.sum_sq_label,
            "default_evaluators": list(self.default_evaluators),
        }


KINDS: Dict[str, KindSpec] = {
    "proportion": KindSpec(
        key="proportion",
        label="Anteil",
        description=(
            "Erfolge von Versuchen, z. B. Klicks von Impressionen oder Antworten "
            "von Anschreiben. Sch\u00e4tzung: Erfolge geteilt durch Versuche."
        ),
        needs_denominator=False,
        is_distribution=False,
        integral_value=True,
        value_label="Erfolge",
        count_label="Versuche",
        denominator_label="",
        sum_sq_label="",
        default_evaluators=(
            "builtin.describe", "builtin.bayes_proportion", "builtin.two_proportion",
        ),
    ),
    "mean": KindSpec(
        key="mean",
        label="Mittelwert",
        description=(
            "Messwerte, deren Mittel z\u00e4hlt, z. B. ein Ranking-Wert je Anfrage "
            "oder ein SUS-Wert je Person. Eine Zeile ist ein Wert (Anzahl 1) oder "
            "die Summe mehrerer Werte."
        ),
        needs_denominator=False,
        is_distribution=False,
        integral_value=False,
        value_label="Summe der Werte",
        count_label="Anzahl",
        denominator_label="",
        sum_sq_label=_SUM_SQ_LABEL,
        default_evaluators=("builtin.describe", "builtin.welch_t", "builtin.paired_t"),
    ),
    "count": KindSpec(
        key="count",
        label="Rate",
        description=(
            "Ereignisse je Einheit, z. B. Fehler je 1000 Anfragen oder Anmeldungen "
            "je Woche. Sch\u00e4tzung: Ereignisse geteilt durch Einheiten."
        ),
        needs_denominator=False,
        is_distribution=False,
        integral_value=True,
        value_label="Ereignisse",
        count_label="Einheiten",
        denominator_label="",
        sum_sq_label="",
        default_evaluators=("builtin.describe", "builtin.poisson_rate"),
    ),
    "duration": KindSpec(
        key="duration",
        label="Dauer",
        description=(
            "Zeitspannen wie Latenzen oder Durchlaufzeiten; gerechnet wie ein "
            "Mittelwert."
        ),
        needs_denominator=False,
        is_distribution=False,
        integral_value=False,
        value_label="Summe der Werte",
        count_label="Anzahl",
        denominator_label="",
        sum_sq_label=_SUM_SQ_LABEL,
        default_evaluators=("builtin.describe", "builtin.welch_t", "builtin.paired_t"),
    ),
    "currency": KindSpec(
        key="currency",
        label="Geldbetrag",
        description=(
            "Betr\u00e4ge wie Vertrags- oder Pipeline-Werte; gerechnet wie ein "
            "Mittelwert."
        ),
        needs_denominator=False,
        is_distribution=False,
        integral_value=False,
        value_label="Summe der Werte",
        count_label="Anzahl",
        denominator_label="",
        sum_sq_label=_SUM_SQ_LABEL,
        default_evaluators=("builtin.describe", "builtin.welch_t"),
    ),
    "ratio": KindSpec(
        key="ratio",
        label="Verh\u00e4ltnis",
        description=(
            "Summe durch Summe, z. B. Kosten pro Klick: Z\u00e4hler = Kosten, "
            "Nenner = Klicks, eine Zeile je Tag oder Woche."
        ),
        needs_denominator=True,
        is_distribution=False,
        integral_value=False,
        value_label="Z\u00e4hler",
        count_label="Einheiten",
        denominator_label="Nenner",
        sum_sq_label="",
        default_evaluators=("builtin.describe", "builtin.ratio_delta"),
    ),
    "ordinal": KindSpec(
        key="ordinal",
        label="Skala",
        description=(
            "Stufen einer Skala, z. B. Zufriedenheit von 1 bis 5. Wert = Stufe, "
            "Anzahl = Personen auf dieser Stufe."
        ),
        needs_denominator=False,
        is_distribution=True,
        integral_value=False,
        value_label="Stufe",
        count_label="Anzahl",
        denominator_label="",
        sum_sq_label="",
        default_evaluators=("builtin.describe", "builtin.welch_t", "builtin.chi_square"),
    ),
    "categorical": KindSpec(
        key="categorical",
        label="Kategorie",
        description=(
            "Eine Auswahl ohne Reihenfolge, z. B. die bevorzugte Variante. Wert = "
            "Kategorie, Anzahl = Personen in dieser Kategorie."
        ),
        needs_denominator=False,
        is_distribution=True,
        integral_value=True,
        value_label="Kategorie",
        count_label="Anzahl",
        denominator_label="",
        sum_sq_label="",
        default_evaluators=("builtin.describe", "builtin.chi_square"),
    ),
}


# -- helpers ------------------------------------------------------------------


def _spec(kind: Any) -> KindSpec:
    spec = KINDS.get(kind) if isinstance(kind, str) else None
    if spec is None:
        shown = str(kind)[:40]
        raise ValidationError(f"Unbekannte Messart \u00ab{shown}\u00bb.")
    return spec


def _refuse(field: str, message: str) -> ValidationError:
    return ValidationError(message, fields={field: message})


def _as_float(raw: Any) -> Optional[float]:
    """A finite float, or None for anything that is not a real finite number.

    Booleans are refused although Python counts them as ints: ``true`` in a
    JSON body is a mistake, never the number one.
    """
    if isinstance(raw, bool) or not isinstance(raw, numbers.Real):
        return None
    try:
        value = float(raw)
    except (OverflowError, ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _is_whole(x: float) -> bool:
    return float(x).is_integer()


def format_plain(x: Optional[float], max_decimals: int = 6) -> str:
    """A number without padding: ``0.5 -> "0,5"``, ``1000 -> "1'000"``.

    For bounds, levels and field values, where "0,50" would suggest a
    precision nobody asked for.
    """
    v = _as_float(x)
    if v is None:
        return DASH
    if v.is_integer() and abs(v) < 1e15:
        return format_number(v, 0)
    text = format_number(v, _decimals(max_decimals, 6))
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return "0" if text in ("", "-0", "-") else text


def _near(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _check_bounds(x: float, definition: Dict[str, Any]) -> None:
    lo = _as_float(definition.get("min"))
    hi = _as_float(definition.get("max"))
    tol = 1e-9
    below = lo is not None and x < lo - tol * max(1.0, abs(lo))
    above = hi is not None and x > hi + tol * max(1.0, abs(hi))
    if not (below or above):
        return
    if lo is not None and hi is not None:
        raise _refuse(
            "value", f"Der Wert liegt ausserhalb von {format_plain(lo)}\u2013{format_plain(hi)}."
        )
    if below:
        raise _refuse("value", f"Der Wert liegt unter dem Minimum {format_plain(lo)}.")
    raise _refuse("value", f"Der Wert liegt \u00fcber dem Maximum {format_plain(hi)}.")


def _check_level(x: float, definition: Dict[str, Any]) -> None:
    levels = definition.get("levels")
    if not isinstance(levels, dict) or not levels:
        return
    if level_key(x) in levels:
        return
    name = definition.get("_name")
    if isinstance(name, str) and name.strip():
        message = f"Wert {format_plain(x)} ist keine Stufe von \u00ab{name.strip()}\u00bb."
    else:
        message = f"Wert {format_plain(x)} ist keine definierte Stufe."
    raise _refuse("value", message)


# -- rows ---------------------------------------------------------------------


def validate_row(kind: str, row: Dict[str, Any],
                 definition: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One measurement row, normalised for ``exp_measurements``.

    Returns ``{"value", "count", "denominator", "sum_sq"}``: floats, an int
    count, and NULL (None) in the columns the kind does not use -- a
    denominator sent for a proportion is dropped rather than stored where no
    aggregate would ever read it. ``definition`` is the metric's definition;
    callers may add ``_name`` (the metric's name) for the level message.
    """
    spec = _spec(kind)
    if not isinstance(row, dict):
        raise ValidationError("Ein Messwert muss ein Objekt sein.")
    definition = definition if isinstance(definition, dict) else {}

    value = _as_float(row.get("value"))
    if value is None:
        raise _refuse("value", "Der Wert muss eine endliche Zahl sein.")

    raw_count = row.get("count")
    if raw_count is None:
        count = 1
    else:
        count_f = _as_float(raw_count)
        if count_f is None or not _is_whole(count_f) or count_f < 1:
            raise _refuse(
                "count", f"\u00ab{spec.count_label}\u00bb muss eine ganze Zahl ab 1 sein."
            )
        if count_f > MAX_COUNT:
            raise _refuse("count", f"\u00ab{spec.count_label}\u00bb ist zu gross.")
        count = int(count_f)

    denominator: Optional[float] = None
    sum_sq: Optional[float] = None

    if spec.key == "proportion":
        if not _is_whole(value):
            raise _refuse("value", "Erfolge m\u00fcssen eine ganze Zahl sein.")
        if value < 0:
            raise _refuse("value", "Erfolge d\u00fcrfen nicht negativ sein.")
        if value > count:
            raise _refuse("value", "Es kann nicht mehr Erfolge als Versuche geben.")

    elif spec.key == "count":
        if not _is_whole(value) or value < 0:
            raise _refuse("value", "Ereignisse m\u00fcssen eine ganze Zahl sein.")

    elif spec.key == "ratio":
        raw_den = row.get("denominator")
        if raw_den is None:
            raise _refuse("denominator", "Der Nenner fehlt.")
        denominator = _as_float(raw_den)
        if denominator is None:
            raise _refuse("denominator", "Der Nenner muss eine endliche Zahl sein.")
        if denominator < 0:
            raise _refuse("denominator", "Der Nenner darf nicht negativ sein.")
        denominator = denominator + 0.0  # -0.0 -> 0.0

    elif spec.key in MEAN_LIKE_KINDS:
        _check_bounds(value / count, definition)
        square = value * value
        if not math.isfinite(square):
            raise _refuse("value", "Der Wert ist zu gross.")
        raw_sq = row.get("sum_sq")
        if raw_sq is None:
            # One observation: its square is known, so store it and keep the
            # variance of every aggregate that contains the row computable.
            sum_sq = square if count == 1 else None
        else:
            given = _as_float(raw_sq)
            if given is None or given < 0:
                raise _refuse(
                    "sum_sq", "Die Quadratsumme muss eine endliche Zahl ab 0 sein."
                )
            if count == 1:
                if not _near(given, square):
                    raise _refuse("sum_sq", "Die Quadratsumme passt nicht zum Wert.")
                sum_sq = square
            else:
                floor = square / count
                # By Cauchy-Schwarz sum(x^2) >= (sum x)^2 / n; anything below
                # would give a negative variance.
                if given < floor and not _near(given, floor):
                    raise _refuse("sum_sq", "Die Quadratsumme passt nicht zum Wert.")
                sum_sq = max(given, floor)

    elif spec.key == "ordinal":
        _check_bounds(value, definition)
        _check_level(value, definition)

    elif spec.key == "categorical":
        if not _is_whole(value):
            raise _refuse("value", "Die Kategorie muss eine ganze Zahl sein.")
        _check_level(value, definition)

    return {
        "value": value + 0.0,
        "count": count,
        "denominator": denominator,
        "sum_sq": sum_sq,
    }


# -- aggregates ---------------------------------------------------------------


def estimate(kind: str, agg: Dict[str, Any]) -> Optional[float]:
    """The per-variant estimate of an aggregate (see the plan, section 3).

    ``None`` when it is undefined: no units yet, a ratio whose denominators
    sum to zero, or a categorical metric (a distribution has no single
    number).
    """
    spec = _spec(kind)
    if spec.key == "categorical" or not isinstance(agg, dict):
        return None
    value_sum = _as_float(agg.get("value_sum"))
    if value_sum is None:
        return None
    if spec.needs_denominator:
        denominator_sum = _as_float(agg.get("denominator_sum"))
        if denominator_sum is None or denominator_sum <= 0:
            return None
        result = value_sum / denominator_sum
    else:
        n = _as_float(agg.get("n"))
        if n is None or n <= 0:
            return None
        result = value_sum / n
    return result if math.isfinite(result) else None


def level_key(value: float) -> str:
    """The key under which ``definition.levels`` and aggregates name a level.

    ``3.0 -> "3"``, ``2.5 -> "2.5"``: whole numbers without a decimal part,
    everything else in Python's shortest round-trip form, so the key a level
    is stored under and the key a measured value maps to always agree.
    """
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("level must be finite")
    if x == 0:
        return "0"
    if x.is_integer() and abs(x) < 1e16:
        return str(int(x))
    return repr(x)


# -- formatting ---------------------------------------------------------------


def _decimals(decimals: Any, default: int) -> int:
    if decimals is None or isinstance(decimals, bool):
        return default
    try:
        d = int(decimals)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, min(10, d))


def format_number(x: Optional[float], decimals: int = 2) -> str:
    """``10714.5 -> "10'714,50"``; None and non-finite values -> ``"\u2013"``."""
    v = _as_float(x)
    if v is None:
        return DASH
    d = _decimals(decimals, 2)
    # Half away from zero on the exact binary value -- what JavaScript's
    # toFixed does, so KX.fmtNumber shows the same digits (Python's format()
    # would round 0.125 to "0,12", toFixed to "0,13").
    exact = decimal.Decimal(abs(v)).quantize(
        decimal.Decimal(1).scaleb(-d), rounding=decimal.ROUND_HALF_UP, context=_DECIMAL_CONTEXT
    )
    text = f"{exact:,.{d}f}".replace(",", "'").replace(".", ",")
    # A value that rounds to zero is shown without a sign ("-0,00" reads as
    # a loss that is not there).
    if v < 0 and any(ch not in "0'," for ch in text):
        return "-" + text
    return text


def format_value(kind: str, x: Optional[float], unit: str = "",
                 decimals: Optional[int] = None) -> str:
    """An estimate for people: proportions as percent (``"1,63 %"``), other
    kinds as the number followed by the unit (``"12,50 ms"``, ``"3,00 %"``)."""
    d = _decimals(decimals, 2)
    if kind == "proportion":
        v = _as_float(x)
        if v is None:
            return DASH
        text = format_number(v * 100.0, d)
        return DASH if text == DASH else f"{text} %"
    text = format_number(x, d)
    if text == DASH:
        return DASH
    unit = str(unit or "").strip()
    return f"{text} {unit}" if unit else text


def format_diff(kind: str, x: Optional[float], unit: str = "",
                decimals: Optional[int] = None) -> str:
    """A signed difference: ``"+0,42 Pp."`` for proportions (percentage
    points, never percent), ``"+12,5 ms"`` / ``"-3,00 CHF"`` otherwise."""
    d = _decimals(decimals, 2)
    v = _as_float(x)
    if v is None:
        return DASH
    if kind == "proportion":
        v = v * 100.0
        suffix = " Pp."
    else:
        unit = str(unit or "").strip()
        suffix = f" {unit}" if unit else ""
    text = format_number(v, d)
    if text == DASH:
        return DASH
    if not text.startswith("-"):
        text = "+" + text
    return text + suffix
