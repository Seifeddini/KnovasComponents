"""The rules behind the directory, card and console screens. No server, no DB."""
import graph_directory as gd

MANDAT = {"id": "t1", "name": "Mandat", "created_at": "2025-01-01"}
PERSON = {"id": "t2", "name": "Person", "created_at": "2025-01-02"}

AZ = {"id": "a1", "name": "Aktenzeichen", "datatype": "text", "required": True, "sort_order": 10}
STATUS = {"id": "a2", "name": "Status", "datatype": "enum", "required": True, "sort_order": 20,
          "enum_values": ["offen", "ruhend", "abgeschlossen"]}
MANDANT = {"id": "a3", "name": "Mandant", "datatype": "entity_ref", "sort_order": 30}
STREITWERT = {"id": "a4", "name": "Streitwert", "datatype": "money", "sort_order": 40}
EROEFFNET = {"id": "a5", "name": "Eröffnet", "datatype": "date", "sort_order": 50}
NOTIZ = {"id": "a6", "name": "Notiz", "datatype": "text", "sort_order": 60}
ALT = {"id": "a9", "name": "Kostenvorschuss", "datatype": "money", "sort_order": 70,
       "deprecated_at": "2026-01-01"}
SCHEMA = [NOTIZ, STATUS, AZ, MANDANT, STREITWERT, EROEFFNET, ALT]


def fact(fid, attribute, value, **extra):
    return {"id": fid, "attribute_id": attribute, "value": value, **extra}


class TestValues:
    def test_money_uses_the_swiss_apostrophe_and_two_decimals(self):
        assert gd.format_money({"amount": "1250000", "currency": "CHF"}) == "1’250’000.00 CHF"
        assert gd.format_money({"amount": 84500.5, "currency": "EUR"}) == "84’500.50 EUR"
        assert gd.format_money({"amount": "-3000", "currency": "CHF"}) == "-3’000.00 CHF"
        assert gd.format_money({"amount": 0, "currency": "CHF"}) == "0.00 CHF"

    def test_a_month_precise_date_never_shows_a_day(self):
        assert gd.display("date", {"value": "2026-03-04", "precision": "month"}) == "März 2026"

    def test_a_reference_shows_the_name_it_points_to(self):
        shown = gd.cell(MANDANT, fact("f1", "a3", {"node_id": "q1"}), {"q1": "Elsbeth Bühlmann"})
        assert shown["display"] == "Elsbeth Bühlmann"
        assert shown["ref"] == {"id": "q1", "name": "Elsbeth Bühlmann"}

    def test_a_reference_to_an_invisible_entry_names_nothing(self):
        shown = gd.cell(MANDANT, fact("f1", "a3", {"node_id": "secret"}), {})
        assert shown["display"] == gd.HIDDEN_REF
        assert shown["ref"] is None
        assert "secret" not in shown["display"]

    def test_the_newest_fact_per_field_wins(self):
        facts = [fact("f1", "a1", "alt", updated_at="2026-01-01"),
                 fact("f2", "a1", "neu", updated_at="2026-02-01")]
        assert gd.facts_by_attribute(facts)["a1"]["value"] == "neu"

    def test_live_attributes_are_ordered_and_exclude_the_retired(self):
        assert [a["id"] for a in gd.live_attributes(SCHEMA)] == ["a1", "a2", "a3", "a4", "a5", "a6"]
        assert [a["id"] for a in gd.retired_attributes(SCHEMA)] == ["a9"]

    def test_tones_are_distinct_for_the_first_four_types_and_stable(self):
        tones = gd.type_tones([PERSON, MANDAT])
        assert tones == {"t1": "t1", "t2": "t2"}
        later = gd.type_tones([PERSON, MANDAT, {"id": "t3", "name": "Frist",
                                                "created_at": "2026-01-01"}])
        assert later["t1"] == "t1" and later["t2"] == "t2" and later["t3"] == "t3"


class TestDirectoryRows:
    def test_required_gaps_are_named_not_errors(self):
        live = gd.live_attributes(SCHEMA)
        row = gd.directory_row({"id": "n1", "name": "Bühlmann"}, live[:2], live,
                               [fact("f1", "a1", "2026-0114")], {}, 3)
        assert row["gaps"] == ["a2"]
        assert row["cells"]["a2"]["missing"] is True
        assert row["connections"] == 3

    def test_an_entry_without_loaded_values_says_so(self):
        row = gd.directory_row({"id": "n1", "name": "X"}, [AZ], [AZ], None, {}, 0)
        assert row["loaded"] is False and "cells" not in row

    def test_without_chosen_columns_the_first_four_fields_are_shown(self):
        live = gd.live_attributes(SCHEMA)
        assert [a["id"] for a in gd.columns_for({"columns": []}, live)] == ["a1", "a2", "a3", "a4"]
        assert [a["id"] for a in gd.columns_for({"columns": ["a5", "gone"]}, live)] == ["a5"]

    def test_connections_count_both_ends(self):
        counts = gd.connection_counts([{"node_lo": "n1", "node_hi": "q1", "relation": "Mandant"},
                                       {"node_lo": "q1", "node_hi": "n2", "relation": "Mandant"}])
        assert counts == {"n1": 1, "q1": 2, "n2": 1}


class TestCardLayout:
    def test_unknown_and_duplicate_fields_drop_out_and_the_rest_is_loose(self):
        live = gd.live_attributes(SCHEMA)
        layout = gd.card_layout({"head": ["a2", "a2", "zz"], "rail": ["a2", "a1"],
                                 "sections": [{"name": "Beteiligte", "fields": ["a3", "a9"]}]},
                                live)
        assert layout["head"] == ["a2"]
        assert layout["rail"] == ["a1"]
        assert layout["sections"] == [{"name": "Beteiligte", "fields": ["a3"]}]
        assert layout["loose"] == ["a4", "a5", "a6"]

    def test_a_type_without_a_layout_gets_one_section_and_all_fields_loose(self):
        layout = gd.card_layout(None, [AZ, NOTIZ])
        assert layout["sections"] == [{"name": "Details", "fields": []}]
        assert layout["loose"] == ["a1", "a6"]

    def test_saving_needs_named_sections(self):
        import pytest

        with pytest.raises(gd.LayoutError):
            gd.clean_layout({"head": [], "rail": [], "sections": []}, [AZ])
        with pytest.raises(gd.LayoutError):
            gd.clean_layout({"sections": [{"name": "  ", "fields": []}]}, [AZ])
        saved = gd.clean_layout({"head": ["a1"], "sections": [{"name": "A", "fields": ["a1"]}]},
                                [AZ])
        assert saved == {"head": ["a1"], "rail": [], "sections": [{"name": "A", "fields": []}]}

    def test_auto_place_sorts_by_datatype(self):
        live = gd.live_attributes(SCHEMA)
        layout = gd.auto_place(gd.card_layout(None, live), live)
        sections = {s["name"]: s["fields"] for s in layout["sections"]}
        assert layout["head"] == ["a2"]
        assert sections["Beteiligte"] == ["a3"]
        assert sections["Termine"] == ["a5"]
        assert sections["Wirtschaftliches"] == ["a4"]
        assert sections["Details"] == ["a1", "a6"]


def _inventory():
    nodes = [{"id": f"n{i}", "name": f"Akte {i}", "created_at": "2026-01-0%d" % i}
             for i in range(1, 5)]
    facts = {
        "n1": [fact("f1", "a1", "1"), fact("f2", "a2", "offen"), fact("f3", "a6", "am 12.03.2026")],
        "n2": [fact("f4", "a1", "2"), fact("f5", "a2", "offen"), fact("f6", "a6", "2026-04-01")],
        "n3": [fact("f7", "a1", "3"), fact("f8", "a2", "abgeschlossen")],
        "n4": [fact("f9", "a1", "4")],
    }
    return nodes, facts


class TestFieldSuggestions:
    def test_a_never_filled_field_is_proposed_for_retirement_on_complete_data(self):
        nodes, facts = _inventory()
        found = {s["id"] for s in gd.field_suggestions(MANDAT, SCHEMA, nodes, facts,
                                                       complete=True, view=None)}
        assert "leer:t1:a4" in found and "leer:t1:a3" in found

    def test_a_sample_never_proposes_retirement_or_counts_gaps(self):
        nodes, facts = _inventory()
        found = gd.field_suggestions(MANDAT, SCHEMA, nodes, facts, complete=False, view=None)
        assert not [s for s in found if s["id"].startswith(("leer:", "luecken:", "enum:"))]

    def test_a_field_younger_than_every_entry_is_not_judged(self):
        nodes, facts = _inventory()
        young = dict(STREITWERT, created_at="2027-01-01")
        found = {s["id"] for s in gd.field_suggestions(MANDAT, [AZ, STATUS, young], nodes,
                                                       facts, complete=True, view=None)}
        assert "leer:t1:a4" not in found

    def test_unused_enum_values_are_proposed_for_removal(self):
        nodes, facts = _inventory()
        enum = next(s for s in gd.field_suggestions(MANDAT, SCHEMA, nodes, facts, complete=True,
                                                    view=None) if s["id"].startswith("enum:"))
        assert enum["id"] == "enum:t1:a2:ruhend"
        assert enum["action"] == {"kind": "trim_enum", "attribute_id": "a2",
                                  "keep": ["offen", "abgeschlossen"]}

    def test_dates_hidden_in_text_propose_a_new_field_and_convert_nothing(self):
        nodes, facts = _inventory()
        datum = next(s for s in gd.field_suggestions(MANDAT, SCHEMA, nodes, facts, complete=True,
                                                     view=None) if s["id"].startswith("datum:"))
        assert datum["action"]["kind"] == "add_date_attribute"
        assert datum["action"]["name"] == "Notiz (Datum)"

    def test_gaps_in_required_fields_are_counted_and_cannot_be_dismissed(self):
        nodes, facts = _inventory()
        gaps = next(s for s in gd.field_suggestions(
            MANDAT, SCHEMA, nodes, facts, complete=True,
            view={"slug": "mandate", "active": True}) if s["id"].startswith("luecken:"))
        assert gaps["title"].startswith("1 von 4")
        assert gaps["dismissable"] is False
        assert gaps["action"]["href"] == "/verzeichnis/mandate?luecken=1"


class TestCardAndDirectorySuggestions:
    def test_loose_fields_and_a_crowded_header(self):
        live = gd.live_attributes(SCHEMA)
        layout = gd.card_layout({"head": ["a1", "a2", "a3", "a4", "a5"]}, live)
        found = {s["id"].split(":")[0] for s in gd.card_suggestions(MANDAT, live, layout)}
        assert found == {"karte", "kopf"}

    def test_a_type_with_entries_but_no_directory(self):
        found = gd.directory_suggestions([MANDAT, PERSON], [{"node_type_id": "t1", "title": "M",
                                                              "columns": []}],
                                         {"t1": 5, "t2": 3}, {}, {})
        assert [s["id"] for s in found] == ["verz:t2"]

    def test_an_empty_column_on_complete_data(self):
        view = {"node_type_id": "t1", "title": "Mandate", "columns": ["a4"]}
        found = gd.directory_suggestions([MANDAT], [view], {"t1": 4},
                                         {"t1": {"a4": {"filled": 0, "total": 4}}},
                                         {"t1": SCHEMA})
        assert [s["id"] for s in found] == ["spalte:t1:a4"]


class TestNeighbourhood:
    CENTRE = {"id": "n1", "name": "Bühlmann ./. Regazzoni", "node_type_id": "t1"}

    def test_edges_are_induced_on_the_visible_nodes_only(self):
        response = {"neighbors": [{"id": "q1", "name": "Elsbeth", "hop": 1}],
                    "edges": [{"id": "e1", "node_lo": "n1", "node_hi": "q1", "relation": "Mandant"},
                              {"id": "e2", "node_lo": "n1", "node_hi": "hidden",
                               "relation": "Gegenpartei"}]}
        net = gd.neighbourhood(self.CENTRE, response, names={}, types={})
        assert [n["id"] for n in net["nodes"]] == ["n1", "q1"]
        assert net["edges"] == [{"id": "e1", "from": "n1", "to": "q1", "label": "Mandant",
                                 "manual": False}]

    def test_without_include_edges_the_visible_edges_are_induced_from_the_export(self):
        response = {"neighbors": [{"id": "q1", "hop": 1}, {"id": "q2", "hop": 2}], "edges": []}
        export = [{"node_lo": "q1", "node_hi": "n1", "relation": "Mandant"},
                  {"node_lo": "q1", "node_hi": "q2", "relation": "gleiche Verwaltung",
                   "edge_source": "manual"},
                  {"node_lo": "q2", "node_hi": "elsewhere", "relation": "x"}]
        net = gd.neighbourhood(self.CENTRE, response, names={"q1": "Elsbeth", "q2": "Weber"},
                               types={}, fallback_edges=export)
        assert {(e["from"], e["to"], e["label"], e["manual"]) for e in net["edges"]} == {
            ("q1", "n1", "Mandant", False), ("q1", "q2", "gleiche Verwaltung", True)}
        assert [n["name"] for n in net["nodes"]] == ["Bühlmann ./. Regazzoni", "Elsbeth", "Weber"]

    def test_a_first_ring_neighbour_is_linked_even_when_no_relation_is_known(self):
        net = gd.neighbourhood(self.CENTRE, {"neighbors": [{"id": "q1", "hop": 1}]},
                               names={}, types={})
        assert net["edges"] == [{"id": "", "from": "n1", "to": "q1", "label": "", "manual": False}]

    def test_connections_of_the_centre(self):
        net = gd.neighbourhood(self.CENTRE, {"neighbors": [{"id": "q1", "name": "E", "hop": 1}],
                                             "edges": [{"node_lo": "n1", "node_hi": "q1",
                                                        "relation": "Mandant"}]},
                               names={}, types={})
        assert gd.connections_of("n1", net)[0]["label"] == "Mandant"


class TestHistoryAndDocuments:
    def test_events_are_newest_first_with_field_names(self):
        result = gd.history(
            {"id": "n1", "created_at": "2026-01-14T09:00:00"},
            [fact("f1", "a4", {"amount": 1, "currency": "CHF"})],
            {"f1": [{"event_type": "updated", "actor_ref": "u1", "occurred_at": "2026-02-01T10:00:00"},
                    {"event_type": "created", "actor": "RemoteController",
                     "occurred_at": "2026-01-14T09:05:00"}]},
            {"a4": "Streitwert"}, {"u1": "A. Brunner"})
        assert result["available"] is True
        assert [(e["what"], e["who"]) for e in result["events"]] == [
            ("geändert", "A. Brunner"), ("gesetzt", "RemoteController"), ("Eintrag angelegt", "—")]
        assert result["events"][0]["when"] == "01.02.2026, 10:00"

    def test_a_named_service_keeps_its_name_and_a_nameless_one_is_knovas(self):
        rows = [{"event_type": "created", "actor": "Knovas Connector", "actor_kind": "service",
                 "occurred_at": "2026-01-02"},
                {"event_type": "updated", "actor_kind": "system", "occurred_at": "2026-01-03"}]
        events = gd.history({"id": "n1"}, [fact("f1", "a1", "x")], {"f1": rows}, {}, {})["events"]
        assert [e["who"] for e in events] == ["Knovas", "Knovas Connector"]

    def test_an_api_without_history_says_so(self):
        result = gd.history({"id": "n1"}, [fact("f1", "a1", "x")], {"f1": None}, {}, {})
        assert result["available"] is False

    def test_documents_are_readable_names(self):
        docs = gd.documents({"assignments": [{"pointer": "akten/buehlmann/Klageschrift.pdf",
                                              "created_at": "2026-01-14T08:00:00"},
                                             "akten/buehlmann/Klageschrift.pdf",
                                             "mail/Korrespondenz.eml"]})
        assert [(d["title"], d["meta"]) for d in docs] == [
            ("Klageschrift.pdf", "PDF · 14.01.2026"), ("Korrespondenz.eml", "E-Mail")]
