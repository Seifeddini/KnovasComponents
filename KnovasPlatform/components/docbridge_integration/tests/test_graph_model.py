"""Codecs for the five fact datatypes.

The shapes are the API's, not ours: graph_api.py validates them server-side and
a malformed payload is a 422 the user cannot act on. Encoding here means a bad
value is caught with the field still on screen.
"""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from graph_model import DATATYPES, FactValueError, decode, encode, format_date


class TestText:
    def test_text_is_the_trimmed_string(self):
        assert encode("text", "  Vertrag  ") == "Vertrag"

    def test_empty_text_is_refused(self):
        """An empty fact is an absent fact. Writing one would make the
        completeness report count a gap as filled."""
        with pytest.raises(FactValueError):
            encode("text", "   ")


class TestDate:
    def test_a_day_precision_date(self):
        assert encode("date", {"value": "2026-03-04", "precision": "day"}) == {
            "value": "2026-03-04", "precision": "day"}

    def test_precision_defaults_to_day(self):
        assert encode("date", {"value": "2026-03-04"})["precision"] == "day"

    def test_an_unparseable_date_is_refused(self):
        with pytest.raises(FactValueError):
            encode("date", {"value": "04.03.2026"})

    def test_an_unknown_precision_is_refused(self):
        with pytest.raises(FactValueError):
            encode("date", {"value": "2026-03-04", "precision": "hour"})

    def test_month_precision_renders_as_a_month(self):
        """A month-precision fact drawn on a specific day is a fabricated
        detail in a document a court may see."""
        assert format_date({"value": "2026-03-04", "precision": "month"}) == "März 2026"

    def test_year_precision_renders_as_a_year(self):
        assert format_date({"value": "2026-03-04", "precision": "year"}) == "2026"

    def test_day_precision_renders_as_a_swiss_date(self):
        assert format_date({"value": "2026-03-04", "precision": "day"}) == "04.03.2026"


class TestMoney:
    def test_amount_and_iso_currency(self):
        assert encode("money", {"amount": "1500.50", "currency": "chf"}) == {
            "amount": "1500.50", "currency": "CHF"}

    def test_a_non_iso_currency_is_refused(self):
        with pytest.raises(FactValueError):
            encode("money", {"amount": "10", "currency": "Franken"})

    def test_a_non_numeric_amount_is_refused(self):
        with pytest.raises(FactValueError):
            encode("money", {"amount": "viel", "currency": "CHF"})


class TestEnum:
    def test_a_member_of_the_declared_values(self):
        assert encode("enum", "offen", enum_values=["offen", "erledigt"]) == "offen"

    def test_a_non_member_is_refused(self):
        with pytest.raises(FactValueError):
            encode("enum", "schwebend", enum_values=["offen", "erledigt"])

    def test_an_enum_without_declared_values_is_refused(self):
        with pytest.raises(FactValueError):
            encode("enum", "offen", enum_values=None)


class TestEntityRef:
    def test_a_node_id(self):
        assert encode("entity_ref", {"node_id": "abc"}) == {"node_id": "abc"}

    def test_a_bare_string_is_accepted_as_the_node_id(self):
        assert encode("entity_ref", "abc") == {"node_id": "abc"}

    def test_a_missing_node_id_is_refused(self):
        with pytest.raises(FactValueError):
            encode("entity_ref", {})


class TestDatatypeSet:
    def test_the_five_datatypes_match_the_api(self):
        assert DATATYPES == ("text", "date", "money", "enum", "entity_ref")

    def test_an_unknown_datatype_is_refused(self):
        with pytest.raises(FactValueError):
            encode("timestamp", "now")


class TestDecode:
    def test_decode_round_trips_every_datatype(self):
        assert decode("text", "Vertrag") == "Vertrag"
        assert decode("entity_ref", {"node_id": "abc"}) == {"node_id": "abc"}
        assert decode("money", {"amount": "10", "currency": "CHF"})["currency"] == "CHF"

    def test_decode_tolerates_a_payload_it_did_not_write(self):
        """Facts predate this module; a shape from an older writer must render
        as something rather than raise on a read path."""
        assert decode("date", "2026-03-04") == {"value": "2026-03-04",
                                                "precision": "day"}
