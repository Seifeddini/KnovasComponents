"""CSV import of measurements (Flask-free).

People bring measurements from wherever they live -- an ad platform's weekly
export, a spreadsheet of interview results, a benchmark script -- and those
files come in the shapes their tools write: comma, semicolon or tab separated,
with a byte order mark, with Swiss number formatting (``1'250,50``) and dates
as ``TT.MM.JJJJ``. This module turns such a file into the measurement rows the
service stores, and refuses it as a whole when any line is wrong: an import
that silently skips a bad line would skew every later estimate without anyone
noticing.

Two layouts are accepted (plan, section 8):

* Long format -- the header has a ``metric`` column; one measurement per line:
  ``metric, variant, value, count, denominator, sum_sq, observed_at, run,
  dim.<name>``.
* Wide format -- no ``metric`` column; every column named exactly like a
  metric key holds that metric's value, ``<key>.count``, ``<key>.denominator``
  and ``<key>.sum_sq`` its other fields, next to ``variant``, ``observed_at``,
  ``run`` and ``dim.<name>``. Each non-empty metric cell becomes one row, so a
  LinkedIn export ``variant;observed_at;ctr;ctr.count;cost_per_click;
  cost_per_click.denominator`` imports in one go.

Columns the layout does not know are ignored and reported back, so a user sees
that ``Kampagnenname`` was not imported instead of wondering where it went.

Every row is also checked with ``kinds.validate_row`` here, because only this
module knows which file line a row came from; errors name that line
("Zeile 5: ...").
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import math
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from experiments import kinds, schema
from experiments.errors import ValidationError

#: Longest cell accepted (dimension values are stored up to this length).
MAX_CELL_CHARS = 200
#: At most this many ``dim.<name>`` columns (a row's dims hold at most 20 keys).
MAX_DIM_COLUMNS = 20
#: Errors listed in one refusal; the rest of the file is not examined further.
MAX_ERRORS = 20
#: Values beyond this magnitude are refused. Sums of many rows must stay far
#: from the float8 limit, or an aggregate over them would fail for good.
MAX_ABS_VALUE = 1e15

_HEADER_RE = re.compile(r"^[A-Za-z0-9_. -]{1,60}$")
_DIM_KEY_RE = re.compile(schema.DIM_KEY_PATTERN)
_NUMBER_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d{1,3})?")
_SWISS_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Thousands separators and spaces people (and Excel de-CH) put into numbers.
_NUMBER_NOISE = ("'", "\u2019", " ", "\u00a0", "\u202f")
_DELIMITERS = (";", "\t", ",")

_LONG_COLUMNS = ("metric", "variant", "value", "count", "denominator", "sum_sq",
                 "observed_at", "run")
#: Columns the wide format reads besides the metric columns.
_WIDE_COLUMNS = ("variant", "observed_at", "run")
_COMPANIONS = ("count", "denominator", "sum_sq")

_MSG_TOO_MANY_ROWS = "Die Datei hat mehr als {max} Zeilen; bitte aufteilen."


# -- cells --------------------------------------------------------------------


def parse_number(text: str) -> Optional[float]:
    """A finite number from a cell: ``1'250,5`` and ``1 250.5`` are 1250.5.

    A single comma without a dot is a decimal comma (Swiss and German
    exports). ``nan``, ``inf`` and anything else that is not plainly a number
    give None.
    """
    value = str(text).strip()
    for noise in _NUMBER_NOISE:
        value = value.replace(noise, "")
    if value.count(",") == 1 and "." not in value:
        value = value.replace(",", ".")
    if not _NUMBER_RE.fullmatch(value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_instant(text: str) -> Optional[_dt.datetime]:
    """ISO 8601 (a bare date or one without offset is UTC) or ``TT.MM.JJJJ``
    (UTC midnight); None when the text is neither."""
    value = str(text).strip()
    try:
        match = _SWISS_DATE_RE.match(value)
        if match:
            day, month, year = (int(g) for g in match.groups())
            moment = _dt.datetime(year, month, day)
        elif _ISO_DATE_RE.match(value):
            moment = _dt.datetime.combine(_dt.date.fromisoformat(value), _dt.time())
        else:
            moment = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    try:
        return moment.astimezone(_dt.timezone.utc)
    except (OverflowError, ValueError):
        return None


def _decode(content: Any) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, (bytes, bytearray, memoryview)):
        try:
            text = bytes(content).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationError(
                "Die Datei ist keine UTF-8-Textdatei. Bitte als \u00abCSV UTF-8\u00bb speichern."
            ) from None
    else:
        raise ValidationError("Es wurde keine Datei \u00fcbermittelt.")
    text = text.lstrip("\ufeff")
    if "\x00" in text:
        raise ValidationError("Die Datei enth\u00e4lt Nullzeichen; ist es wirklich eine CSV-Datei?")
    if not text.strip():
        raise ValidationError("Die Datei ist leer.")
    return text


def _sniff_delimiter(text: str) -> str:
    """The delimiter of the header line: whichever of ``;``, tab and ``,``
    occurs most often outside quotes (``,`` when none does)."""
    header = ""
    for line in text.splitlines():
        if line.strip():
            header = line
            break
    counts = {d: 0 for d in _DELIMITERS}
    quoted = False
    for ch in header:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in counts:
            counts[ch] += 1
    best = max(_DELIMITERS, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


# -- layout -------------------------------------------------------------------


class _Layout:
    """Which column carries what, decided once from the header."""

    def __init__(self) -> None:
        self.long = False
        #: role -> column index for the fixed columns (metric, variant, ...).
        self.fixed: Dict[str, int] = {}
        #: dimension key -> column index.
        self.dims: List[Tuple[str, int]] = []
        #: wide format: metric key -> {"value": i, "count": j, ...}.
        self.metric_columns: Dict[str, Dict[str, int]] = {}
        self.ignored: List[str] = []
        self.names: List[str] = []


def _header_layout(header: List[str], metrics: Dict[str, Dict[str, Any]]) -> _Layout:
    layout = _Layout()
    names = [cell.strip() for cell in header]
    layout.names = names
    errors: List[str] = []
    seen: Dict[str, int] = {}
    for i, name in enumerate(names):
        if name == "":
            continue  # a trailing delimiter in the header; the column is unused
        if not _HEADER_RE.match(name):
            shown = name[:60]
            errors.append(
                f"Spalte {i + 1}: \u00ab{shown}\u00bb ist kein g\u00fcltiger Spaltenname "
                "(Buchstaben, Ziffern, _, ., - und Leerzeichen, h\u00f6chstens 60 Zeichen)."
            )
            continue
        folded = name.lower()
        if folded in seen:
            errors.append(f"Die Spalte \u00ab{name}\u00bb kommt doppelt vor.")
            continue
        seen[folded] = i
    if errors:
        raise _refuse(errors)

    layout.long = "metric" in seen
    companions: List[Tuple[str, str, int]] = []
    for name, i in ((n, i) for i, n in enumerate(names) if n):
        folded = name.lower()
        if folded.startswith("dim."):
            key = name[4:]
            if not _DIM_KEY_RE.match(key):
                errors.append(
                    f"Die Spalte \u00ab{name}\u00bb nennt keine g\u00fcltige Dimension "
                    "(Buchstaben, Ziffern, _, . und -, h\u00f6chstens 40 Zeichen)."
                )
                continue
            layout.dims.append((key, i))
            continue
        if layout.long:
            if folded in _LONG_COLUMNS:
                layout.fixed[folded] = i
            else:
                layout.ignored.append(name)
            continue
        if folded in _WIDE_COLUMNS:
            layout.fixed[folded] = i
        elif name in metrics:
            layout.metric_columns.setdefault(name, {})["value"] = i
        elif "." in name and name.rsplit(".", 1)[1] in _COMPANIONS \
                and name.rsplit(".", 1)[0] in metrics:
            base, part = name.rsplit(".", 1)
            companions.append((base, part, i))
        else:
            layout.ignored.append(name)
    for base, part, i in companions:
        if base in layout.metric_columns:
            layout.metric_columns[base][part] = i
        else:
            layout.ignored.append(layout.names[i])
    if len(layout.dims) > MAX_DIM_COLUMNS:
        errors.append(f"H\u00f6chstens {MAX_DIM_COLUMNS} Spalten \u00abdim.<Name>\u00bb sind erlaubt.")
    if errors:
        raise _refuse(errors)
    if layout.long:
        if "value" not in layout.fixed:
            raise ValidationError("Es fehlt die Spalte \u00abvalue\u00bb mit den Werten.")
    elif not layout.metric_columns:
        raise ValidationError(
            "Die Datei hat weder eine Spalte \u00abmetric\u00bb noch eine Spalte, die wie eine "
            "Metrik dieses Experiments heisst."
        )
    return layout


def _refuse(errors: List[str], more: bool = False) -> ValidationError:
    shown = errors[:MAX_ERRORS]
    text = "; ".join(shown)
    if more or len(errors) > MAX_ERRORS:
        text += "; weitere Fehler nicht aufgef\u00fchrt."
    return ValidationError(text, fields={"file": text})


# -- rows ---------------------------------------------------------------------


def _variant_index(variants: Iterable[str]) -> Tuple[Set[str], Dict[str, Optional[str]]]:
    exact = {str(v) for v in variants}
    folded: Dict[str, Optional[str]] = {}
    for key in exact:
        low = key.casefold()
        # Two variants differing only in case: no case-insensitive match.
        folded[low] = None if low in folded else key
    return exact, folded


class _LineError(Exception):
    pass


def _cell(row: List[str], index: Optional[int]) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def _number(cell: str, column: str) -> Optional[float]:
    if cell == "":
        return None
    value = parse_number(cell)
    if value is None:
        raise _LineError(f"\u00ab{cell[:40]}\u00bb ist keine Zahl (Spalte \u00ab{column}\u00bb).")
    if abs(value) > MAX_ABS_VALUE:
        raise _LineError(f"Der Wert in \u00ab{column}\u00bb ist zu gross.")
    return value


def _metric_row(kind: str, definition: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return kinds.validate_row(kind, raw, definition)
    except ValidationError as exc:
        raise _LineError(exc.message) from None


def _common(row: List[str], layout: _Layout, variants: Tuple[Set[str], Dict[str, Optional[str]]]
            ) -> Dict[str, Any]:
    exact, folded = variants
    out: Dict[str, Any] = {"variant": None, "observed_at": None, "run_id": None, "dims": {}}
    variant = _cell(row, layout.fixed.get("variant"))
    if variant:
        if variant in exact:
            out["variant"] = variant
        elif folded.get(variant.casefold()):
            out["variant"] = folded[variant.casefold()]
        else:
            raise _LineError(f"Unbekannte Variante \u00ab{variant[:40]}\u00bb.")
    observed = _cell(row, layout.fixed.get("observed_at"))
    if observed:
        moment = parse_instant(observed)
        if moment is None:
            raise _LineError(
                f"\u00ab{observed[:40]}\u00bb ist kein g\u00fcltiges Datum "
                "(JJJJ-MM-TT, TT.MM.JJJJ oder ISO 8601)."
            )
        out["observed_at"] = moment.isoformat()
    run = _cell(row, layout.fixed.get("run"))
    if run:
        try:
            out["run_id"] = str(uuid.UUID(run))
        except ValueError:
            raise _LineError(f"\u00ab{run[:40]}\u00bb ist keine g\u00fcltige Lauf-ID.") from None
    dims: Dict[str, str] = {}
    for key, index in layout.dims:
        value = _cell(row, index)
        if value:
            dims[key] = value
    out["dims"] = dims
    return out


def _definition(metric: Dict[str, Any], key: str) -> Dict[str, Any]:
    definition = dict(metric.get("definition") or {})
    definition["_name"] = str(metric.get("name") or key)
    return definition


def parse_csv(content: bytes, *, metrics: Dict[str, Dict[str, Any]], variants: Set[str],
              max_rows: int) -> Dict[str, Any]:
    """Measurement rows from a CSV file, or ValidationError listing at most
    20 errors ("Zeile 5: ...").

    ``metrics`` maps the experiment's metric keys to ``{"kind", "definition",
    "name"}``; ``variants`` holds its variant keys. Returns ``{"rows",
    "ignored_columns", "lines"}``: rows shaped like add_measurements input
    (numbers already normalised by kinds.validate_row, ``observed_at`` as
    ISO 8601 UTC or None, ``run_id`` as a UUID string or None) and, for each
    row, the file line it came from.
    """
    metrics = {str(k): dict(v or {}) for k, v in (metrics or {}).items()}
    for key, metric in metrics.items():
        if metric.get("kind") not in kinds.KINDS:
            raise ValueError(f"metric {key!r} has no valid kind")
    max_rows = max(1, int(max_rows))
    text = _decode(content)
    delimiter = _sniff_delimiter(text)
    variant_index = _variant_index(variants or ())

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows_out: List[Dict[str, Any]] = []
    lines_out: List[int] = []
    errors: List[str] = []
    layout: Optional[_Layout] = None
    data_lines = 0
    try:
        for row in reader:
            line = reader.line_num
            if layout is None:
                if not any(cell.strip() for cell in row):
                    continue
                layout = _header_layout(row, metrics)
                continue
            if not any(cell.strip() for cell in row):
                continue
            data_lines += 1
            if data_lines > max_rows:
                raise ValidationError(_MSG_TOO_MANY_ROWS.format(max=kinds.format_plain(max_rows)))
            try:
                produced = _parse_line(row, layout, metrics, variant_index)
            except _LineError as exc:
                errors.append(f"Zeile {line}: {exc}")
                if len(errors) > MAX_ERRORS:
                    raise _refuse(errors, more=True) from None
                continue
            rows_out.extend(produced)
            lines_out.extend([line] * len(produced))
            if len(rows_out) > max_rows:
                raise ValidationError(_MSG_TOO_MANY_ROWS.format(max=kinds.format_plain(max_rows)))
    except csv.Error:
        line = reader.line_num
        raise ValidationError(
            f"Die Datei ist kein g\u00fcltiges CSV (Zeile {line}); bitte Anf\u00fchrungszeichen "
            "und Trennzeichen pr\u00fcfen."
        ) from None
    if layout is None:
        raise ValidationError("Die Datei hat keine Kopfzeile.")
    if errors:
        raise _refuse(errors)
    if not rows_out:
        raise ValidationError("Die Datei enth\u00e4lt keine Messwerte.")
    return {"rows": rows_out, "ignored_columns": list(layout.ignored), "lines": lines_out}


def _parse_line(row: List[str], layout: _Layout, metrics: Dict[str, Dict[str, Any]],
                variant_index: Tuple[Set[str], Dict[str, Optional[str]]]) -> List[Dict[str, Any]]:
    width = len(layout.names)
    if len(row) > width and any(cell.strip() for cell in row[width:]):
        raise _LineError("Die Zeile hat mehr Werte als die Kopfzeile Spalten.")
    for index, cell in enumerate(row[:width]):
        if len(cell.strip()) > MAX_CELL_CHARS:
            name = layout.names[index] or f"Spalte {index + 1}"
            raise _LineError(f"Der Wert in \u00ab{name}\u00bb ist l\u00e4nger als {MAX_CELL_CHARS} Zeichen.")
    common = _common(row, layout, variant_index)

    if layout.long:
        key = _cell(row, layout.fixed.get("metric"))
        if not key:
            raise _LineError("Die Metrik fehlt.")
        metric = metrics.get(key)
        if metric is None:
            raise _LineError(f"Unbekannte Metrik \u00ab{key[:48]}\u00bb.")
        value_cell = _cell(row, layout.fixed.get("value"))
        if value_cell == "":
            raise _LineError("Der Wert fehlt.")
        raw = {
            "value": _number(value_cell, "value"),
            "count": _number(_cell(row, layout.fixed.get("count")), "count"),
            "denominator": _number(_cell(row, layout.fixed.get("denominator")), "denominator"),
            "sum_sq": _number(_cell(row, layout.fixed.get("sum_sq")), "sum_sq"),
        }
        normal = _metric_row(metric["kind"], _definition(metric, key), raw)
        return [dict(common, metric=key, **normal, dims=dict(common["dims"]))]

    produced: List[Dict[str, Any]] = []
    for key, columns in layout.metric_columns.items():
        value_cell = _cell(row, columns.get("value"))
        if value_cell == "":
            continue
        metric = metrics[key]
        try:
            raw = {"value": _number(value_cell, key)}
            for part in _COMPANIONS:
                raw[part] = _number(_cell(row, columns.get(part)), f"{key}.{part}")
            normal = _metric_row(metric["kind"], _definition(metric, key), raw)
        except _LineError as exc:
            raise _LineError(f"{key}: {exc}") from None
        produced.append(dict(common, metric=key, **normal, dims=dict(common["dims"])))
    return produced
