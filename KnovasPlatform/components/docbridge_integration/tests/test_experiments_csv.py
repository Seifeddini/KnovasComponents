"""CSV import of measurements (experiments.csv_import): layouts, number and
date formats, and the refusals that keep a bad file from skewing results."""

import datetime as dt

import pytest

from experiments import csv_import
from experiments.csv_import import parse_csv, parse_instant, parse_number
from experiments.errors import ValidationError

METRICS = {
    "ctr": {"kind": "proportion", "definition": {}, "name": "Klickrate"},
    "cost_per_click": {"kind": "ratio", "definition": {}, "name": "Kosten pro Klick"},
    "latency_ms": {"kind": "duration", "definition": {}, "name": "Latenz"},
    "sus_score": {"kind": "mean", "definition": {"min": 0, "max": 100}, "name": "SUS-Wert"},
    "preferred": {"kind": "categorical",
                  "definition": {"levels": {"1": "A", "2": "B"}}, "name": "Bevorzugt"},
    "satisfaction": {"kind": "ordinal", "definition": {}, "name": "Zufriedenheit"},
}
VARIANTS = {"A", "B"}
RUN = "6f1c1e8e-7c55-4f7a-9d42-5a3a0c1b2d3e"


def parse(text, *, max_rows=1000, metrics=METRICS, variants=VARIANTS):
    data = text.encode("utf-8") if isinstance(text, str) else text
    return parse_csv(data, metrics=metrics, variants=variants, max_rows=max_rows)


def refused(text, **kwargs):
    with pytest.raises(ValidationError) as info:
        parse(text, **kwargs)
    return info.value.message


# -- cells ----------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("12", 12.0), ("-3.5", -3.5), ("+4", 4.0), ("1'250,5", 1250.5), ("1\u2019250.5", 1250.5),
    ("1 250", 1250.0), ("1\u00a0250,25", 1250.25), ("1\u202f000", 1000.0), ("0,5", 0.5),
    (",5", 0.5), ("1e3", 1000.0), ("  7  ", 7.0),
])
def test_parse_number_accepts_swiss_and_plain_formats(text, expected):
    assert parse_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [
    "", "abc", "nan", "NaN", "inf", "-Infinity", "1e999", "1,2,3", "1.234,5", "12%", "0x10", "1_000",
])
def test_parse_number_refuses_anything_else(text):
    assert parse_number(text) is None


def test_parse_instant_formats_are_utc():
    assert parse_instant("01.09.2026") == dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    assert parse_instant("2026-09-01") == dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    assert parse_instant("2026-09-01T10:00:00") == dt.datetime(2026, 9, 1, 10, tzinfo=dt.timezone.utc)
    assert parse_instant("2026-09-01T12:00:00+02:00") == dt.datetime(2026, 9, 1, 10, tzinfo=dt.timezone.utc)
    assert parse_instant("2026-09-01T10:00:00Z") == dt.datetime(2026, 9, 1, 10, tzinfo=dt.timezone.utc)
    for bad in ("31.02.2026", "2026-13-01", "gestern", "1.9.26", ""):
        assert parse_instant(bad) is None


# -- long format ------------------------------------------------------------------


def test_long_format_with_comma_bom_and_crlf():
    text = ("\ufeffmetric,variant,value,count,observed_at,run,dim.query\r\n"
            f"ctr,A,11,900,2026-09-01,{RUN},q1\r\n"
            "ctr,B,15,910,,,\r\n"
            "latency_ms,,120.5,,01.09.2026,,\r\n")
    result = parse(text)
    rows = result["rows"]
    assert result["ignored_columns"] == []
    assert result["lines"] == [2, 3, 4]
    assert rows[0] == {"metric": "ctr", "variant": "A", "value": 11.0, "count": 900,
                       "denominator": None, "sum_sq": None,
                       "observed_at": "2026-09-01T00:00:00+00:00", "run_id": RUN,
                       "dims": {"query": "q1"}}
    assert rows[1]["variant"] == "B" and rows[1]["observed_at"] is None and rows[1]["dims"] == {}
    # A single observation of a mean-like metric gets its square as sum_sq.
    assert rows[2]["variant"] is None and rows[2]["count"] == 1
    assert rows[2]["sum_sq"] == pytest.approx(120.5 ** 2)


def test_long_format_semicolon_decimal_comma_and_unknown_columns():
    text = ("metric;variant;value;denominator;Kommentar;Metric Notes\n"
            "cost_per_click;A;1'250,50;500;teuer;x\n")
    result = parse(text)
    assert result["ignored_columns"] == ["Kommentar", "Metric Notes"]
    row = result["rows"][0]
    assert row["value"] == pytest.approx(1250.5) and row["denominator"] == 500.0


def test_tab_delimited_and_quoted_cells():
    text = 'metric\tvariant\tvalue\tcount\tdim.note\nctr\tA\t3\t10\t"a, b; c"\n'
    rows = parse(text)["rows"]
    assert rows[0]["dims"] == {"note": "a, b; c"}


def test_header_names_are_case_insensitive_for_standard_columns():
    rows = parse("Metric,Variant,Value,Count\nctr,b,1,10\n")["rows"]
    # Variants match case-insensitively when that is unambiguous.
    assert rows[0]["variant"] == "B" and rows[0]["count"] == 10


def test_variant_case_is_ambiguous_when_two_keys_differ_only_in_case():
    message = refused("metric,variant,value\nctr,AB,1\n", variants={"Ab", "aB"})
    assert "Unbekannte Variante \u00abAB\u00bb" in message
    assert parse("metric,variant,value\nctr,aB,1\n", variants={"Ab", "aB"})["rows"][0]["variant"] == "aB"


def test_long_format_needs_a_value_column():
    assert "value" in refused("metric,variant\nctr,A\n")


def test_long_format_errors_name_the_file_line():
    text = ("metric,variant,value,count\n"
            "ctr,A,5,10\n"
            "nope,A,1,1\n"
            "ctr,C,1,1\n"
            "ctr,A,11,10\n"
            "ctr,A,1.5,10\n"
            "ctr,A,,10\n"
            ",A,1,1\n"
            "ctr,A,abc,1\n"
            "ctr,A,nan,1\n")
    message = refused(text)
    # One list: no ".;" between the entries, one period at the end.
    assert ".;" not in message and message.endswith(".")
    assert "Zeile 3: Unbekannte Metrik \u00abnope\u00bb;" in message
    assert "Zeile 4: Unbekannte Variante \u00abC\u00bb;" in message
    assert "Zeile 5: Es kann nicht mehr Erfolge als Versuche geben;" in message
    assert "Zeile 6: Erfolge m\u00fcssen eine ganze Zahl sein;" in message
    assert "Zeile 7: Der Wert fehlt;" in message
    assert "Zeile 8: Die Metrik fehlt;" in message
    assert "Zeile 9: \u00ababc\u00bb ist keine Zahl (Spalte \u00abvalue\u00bb);" in message
    assert message.endswith("Zeile 10: \u00abnan\u00bb ist keine Zahl (Spalte \u00abvalue\u00bb).")
    assert "Zeile 10:" in message and "Zeile 2" not in message


def test_bounds_and_levels_are_checked_with_the_metric_name():
    message = refused("metric,value\npreferred,3\n")
    assert message == "Zeile 2: Wert 3 ist keine Stufe von \u00abBevorzugt\u00bb."
    assert "ausserhalb" in refused("metric,value\nsus_score,120\n")


def test_sum_sq_must_fit_the_value():
    ok = parse("metric,value,count,sum_sq\nlatency_ms,30,3,400\n")["rows"][0]
    assert ok["sum_sq"] == 400.0 and ok["count"] == 3
    without = parse("metric,value,count\nlatency_ms,30,3\n")["rows"][0]
    assert without["sum_sq"] is None
    assert "Quadratsumme" in refused("metric,value,count,sum_sq\nlatency_ms,30,3,10\n")


def test_huge_values_are_refused():
    assert "zu gross" in refused("metric,value,count\nlatency_ms,1e20,1\n")


def test_dates_and_run_ids_are_validated():
    message = refused(f"metric,value,observed_at,run\nctr,1,31.02.2026,{RUN}\nctr,1,,kein-lauf\n")
    assert "Zeile 2: \u00ab31.02.2026\u00bb ist kein g\u00fcltiges Datum" in message
    assert "Zeile 3: \u00abkein-lauf\u00bb ist keine g\u00fcltige Lauf-ID." in message


# -- wide format ------------------------------------------------------------------


def test_wide_format_linkedin_export():
    text = ("variant;observed_at;ctr;ctr.count;cost_per_click;cost_per_click.denominator;Kampagne;"
            "value;ctr.unknown\n"
            "A;01.09.2026;11;900;12,5;4;Herbst;1;x\n"
            "B;08.09.2026;15;910;;;Herbst;1;x\n"
            "A;15.09.2026;;;;;;;\n")
    result = parse(text)
    assert result["ignored_columns"] == ["Kampagne", "value", "ctr.unknown"]
    rows = result["rows"]
    assert [(r["metric"], r["variant"]) for r in rows] == [
        ("ctr", "A"), ("cost_per_click", "A"), ("ctr", "B")]
    assert rows[0]["count"] == 900 and rows[1]["denominator"] == 4.0
    assert rows[0]["observed_at"] == rows[1]["observed_at"] == "2026-09-01T00:00:00+00:00"
    assert result["lines"] == [2, 2, 3]


def test_wide_format_companion_without_its_metric_column_is_ignored():
    result = parse("variant,ctr,latency_ms.count\nA,1,5\n")
    assert result["ignored_columns"] == ["latency_ms.count"]
    assert len(result["rows"]) == 1


def test_wide_format_errors_name_line_and_metric():
    message = refused("variant,ctr,ctr.count\nA,20,10\n")
    assert message == "Zeile 2: ctr: Es kann nicht mehr Erfolge als Versuche geben."


def test_wide_format_without_a_count_column_says_the_count_is_missing():
    # e2e-api-6: a proportion without ctr.count is one trial per row; the
    # message says so instead of "more successes than trials".
    message = refused("variant;ctr\nA;10\n")
    assert message == ("Zeile 2: ctr: \u00abVersuche\u00bb fehlt; ohne Angabe gilt 1, und 10 Erfolge "
                       "brauchen mindestens so viele Versuche.")


def test_errors_are_listed_once_and_the_file_field_points_to_the_list():
    # e2e-ui-13 (f): no ".;" between entries, and the file field does not
    # repeat the whole list the message already carries.
    with pytest.raises(ValidationError) as info:
        parse("metric,variant,value\nnope,A,1\nctr,C,1\n")
    assert info.value.message == ("Zeile 2: Unbekannte Metrik \u00abnope\u00bb; "
                                  "Zeile 3: Unbekannte Variante \u00abC\u00bb.")
    assert info.value.fields == {"file": csv_import.MSG_SEE_ERRORS}
    assert csv_import.join_messages(["a.", "b"], more=True) == "a; b; weitere Fehler nicht aufgef\u00fchrt."


def test_wide_format_needs_a_metric_column():
    assert "Metrik" in refused("variant,Kampagne\nA,x\n")


def test_dimension_columns():
    rows = parse("metric,value,dim.query,dim.set\nctr,1,q17,gold\n")["rows"]
    assert rows[0]["dims"] == {"query": "q17", "set": "gold"}
    header = ",".join(["metric", "value"] + [f"dim.d{i}" for i in range(21)])
    assert "20" in refused(header + "\nctr,1" + ",x" * 21 + "\n")
    assert "Dimension" in refused("metric,value,dim.a b\nctr,1,x\n")


# -- the file as a whole ------------------------------------------------------------


def test_row_limit_is_refused_with_the_contract_message():
    text = "metric,value\n" + "ctr,1\n" * 4
    assert refused(text, max_rows=3) == "Die Datei hat mehr als 3 Zeilen; bitte aufteilen."
    assert len(parse(text, max_rows=4)["rows"]) == 4
    wide = "variant,ctr,latency_ms\n" + "A,1,2\n" * 2
    # Two lines, but four rows: the rows count against the limit too.
    assert "mehr als 3 Zeilen" in refused(wide, max_rows=3)


def test_error_list_is_capped():
    text = "metric,value\n" + "nope,1\n" * 30
    message = refused(text)
    assert message.count("Unbekannte Metrik") == 20
    assert message.endswith("weitere Fehler nicht aufgef\u00fchrt.")


@pytest.mark.parametrize("content, fragment", [
    (b"", "leer"),
    (b"   \n\n", "leer"),
    (b"\xff\xfe\x00m", "UTF-8"),
    ("metric,value\nctr,1\x00\n".encode("utf-8"), "Nullzeichen"),
    (b"metric,value\n", "keine Messwerte"),
    (b"metric,value\n,\n\n", "keine Messwerte"),
])
def test_unusable_files(content, fragment):
    assert fragment in refused(content)


def test_header_is_checked():
    assert "Spalte 2" in refused("metric,val\u00fce\nctr,1\n")
    assert "doppelt" in refused("metric,value,Value\nctr,1,2\n")
    long_name = "x" * 61
    assert "Spaltenname" in refused(f"metric,value,{long_name}\nctr,1,2\n")
    # A trailing delimiter in the header is tolerated.
    assert len(parse("metric,value,\nctr,1,\n")["rows"]) == 1


def test_cells_are_limited():
    assert "200 Zeichen" in refused("metric,value,dim.q\nctr,1," + "x" * 201 + "\n")
    assert "mehr Werte" in refused("metric,value\nctr,1,3\n")
    # Trailing empty cells beyond the header are harmless.
    assert len(parse("metric,value\nctr,1,,\n")["rows"]) == 1


def test_broken_csv_is_refused_without_internal_detail():
    huge = "x" * 200_000
    message = refused(f'metric,value\nctr,"{huge}"\n')
    assert message.startswith("Die Datei ist kein g\u00fcltiges CSV") or "200 Zeichen" in message
    assert "field" not in message.lower()


def test_metric_definitions_must_name_a_kind():
    with pytest.raises(ValueError):
        parse_csv(b"metric,value\nctr,1\n", metrics={"ctr": {}}, variants=set(), max_rows=10)


def test_limits_are_the_documented_ones():
    assert csv_import.MAX_CELL_CHARS == 200
    assert csv_import.MAX_DIM_COLUMNS == 20
    assert csv_import.MAX_ERRORS == 20
