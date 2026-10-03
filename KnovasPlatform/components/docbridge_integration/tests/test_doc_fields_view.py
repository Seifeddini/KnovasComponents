"""doc_fields_view: the pure functions every document-fields screen relies on.

Each honesty rule of the integration (spec 2.4) that lives in this module is
pinned here: the filter state (H2), the listing notice (H5), what a card may
show (H4, special fields), the title rule (4.1) and the edit matrix (D12).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import doc_fields_view as view
from doc_fields_fakes import core_fields, field_def

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def registry():
    return view.sanitize_registry(core_fields())


def _spec(registry, key):
    return next(s for s in registry if s["key"] == key)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestSanitizeRegistry:
    def test_shape_and_order(self, registry):
        doc_type = _spec(registry, "doc_type")
        assert set(doc_type) == {"key", "label", "datatype", "cardinality", "display", "facet",
                                 "sensitivity", "enum", "has_target", "status", "date_role"}
        assert doc_type["label"] == "Dokumentart"
        assert {"code": "invoice", "label": "Rechnung"} in doc_type["enum"]
        assert [s["key"] for s in registry] == sorted(s["key"] for s in registry)

    def test_the_target_id_never_reaches_the_sanitized_form(self, registry):
        mandant = _spec(registry, "mandant")
        assert mandant["has_target"] is True
        assert "target_node_type_id" not in mandant
        assert _spec(registry, "party")["has_target"] is False
        assert view.registry_targets(core_fields())["mandant"] == "t-mandant"
        assert "party" not in view.registry_targets(core_fields())

    def test_label_preference_de_fr_it_en_then_key(self):
        raw = [
            {"key": "a", "labels": {"en": "A en", "it": "A it"}},
            {"key": "b", "labels": {"fr": "B fr", "en": "B en"}},
            {"key": "c", "labels": {}},
            {"key": "d", "labels": {"de": "  ", "en": "D en"}},
        ]
        labels = {s["key"]: s["label"] for s in view.sanitize_registry(raw)}
        assert labels == {"a": "A it", "b": "B fr", "c": "c", "d": "D en"}
        assert view.sanitize_registry(raw, lang="en")[0]["label"] == "A en"

    def test_enum_codes_without_labels_fall_back_to_the_code(self):
        raw = [{"key": "k", "datatype": "enum", "enum_values": ["x", {"code": "y"}]}]
        assert view.sanitize_registry(raw)[0]["enum"] == [
            {"code": "x", "label": "x"}, {"code": "y", "label": "y"}]

    def test_an_unclassifiable_sensitivity_is_treated_as_special(self):
        raw = [{"key": "k", "sensitivity": "weird"}, {"key": "n"}]
        out = {s["key"]: s["sensitivity"] for s in view.sanitize_registry(raw)}
        assert out == {"k": "special", "n": "normal"}

    def test_junk_entries_are_skipped(self):
        assert view.sanitize_registry([None, "x", {"key": ""}, {"no": "key"}]) == []
        assert view.sanitize_registry(None) == []


class TestCardReturnFields:
    def test_display_fields_plus_title_never_special(self, registry):
        keys = view.card_return_fields(registry)
        assert keys[0] == "title"
        assert set(keys) == {"title", "doc_type", "document_date", "mandant"}
        assert "patient" not in keys  # special, even with display set below

    def test_a_special_field_with_display_set_is_still_left_out(self):
        reg = view.sanitize_registry([
            field_def("patient", "entity_ref", "Patient", sensitivity="special", display=True),
            field_def("doc_type", "enum", "Dokumentart", display=True, enum=[]),
        ])
        assert view.card_return_fields(reg) == ["title", "doc_type"]

    def test_deprecated_fields_are_not_requested(self):
        reg = view.sanitize_registry([
            field_def("old", "text", "Alt", display=True, status="deprecated"),
            field_def("prov", "text", "Neu", display=True, status="provisional"),
        ])
        assert view.card_return_fields(reg) == ["title", "prov"]

    def test_at_most_64_keys_and_title_always_fits(self):
        reg = view.sanitize_registry(
            [field_def(f"f{i:03d}", "text", f"F{i}", display=True) for i in range(100)])
        keys = view.card_return_fields(reg)
        assert len(keys) == 64 and "title" in keys


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatValue:
    @pytest.mark.parametrize("value, text", [
        ({"lo": "2024-03-15", "hi": "2024-03-15", "precision": "day"}, "15.03.2024"),
        ({"lo": "2024-03-01", "hi": "2024-03-31", "precision": "month"}, "M\u00e4rz 2024"),
        ({"lo": "2024-01-01", "hi": "2024-03-31", "precision": "quarter"}, "Q1 2024"),
        ({"lo": "2024-10-01", "hi": "2024-12-31", "precision": "quarter"}, "Q4 2024"),
        ({"lo": "2024-01-01", "hi": "2024-12-31", "precision": "year"}, "2024"),
        ({"lo": "2024-01-01", "hi": "2024-06-30"}, "01.01.2024 \u2013 30.06.2024"),
    ])
    def test_dates_by_precision(self, registry, value, text):
        assert view.format_value(_spec(registry, "document_date"), value) == text

    def test_period_label_else_interval(self, registry):
        period = _spec(registry, "period")
        assert view.format_value(period, {"lo": "2024-01-01", "hi": "2024-12-31",
                                          "label": "GJ 2024"}) == "GJ 2024"
        assert view.format_value(period, {"lo": "2023-07-01", "hi": "2024-06-30"}) \
            == "01.07.2023 \u2013 30.06.2024"

    @pytest.mark.parametrize("amount, text", [
        ("1234.50", "CHF 1'234.50"),
        ("12", "CHF 12.00"),
        ("1234567.5", "CHF 1'234'567.50"),
        ("-1234.5", "CHF -1'234.50"),
        ("999", "CHF 999.00"),
    ])
    def test_money_with_the_swiss_apostrophe(self, registry, amount, text):
        assert view.format_value(_spec(registry, "amount"),
                                 {"amount": amount, "currency": "CHF"}) == text

    def test_enum_label_or_the_code(self, registry):
        doc_type = _spec(registry, "doc_type")
        assert view.format_value(doc_type, "invoice") == "Rechnung"
        assert view.format_value(doc_type, "minutes") == "minutes"  # H4

    def test_bool(self):
        spec = {"datatype": "bool"}
        assert view.format_value(spec, True) == "Ja"
        assert view.format_value(spec, False) == "Nein"

    def test_entities_by_name_and_hidden(self, registry):
        mandant = _spec(registry, "mandant")
        assert view.format_value(mandant, {"node_id": "m1", "name": "Muster AG"}) == "Muster AG"
        assert view.format_value(mandant, {"name": "Beispiel GmbH"}) == "Beispiel GmbH"
        assert view.format_value(mandant, {"hidden": True}) == "verborgen"
        # A value written as a node id carries no name; the id is never shown.
        assert view.format_value(mandant, {"node_id": "m1"}) == view.LINKED_ENTITY

    def test_codes(self, registry):
        reference = _spec(registry, "reference")
        assert view.format_value(reference, {"scheme": "che_uid", "value": "CHE-116.281.710",
                                             "vat": True}) == "CHE-116.281.710 MWST"
        assert view.format_value(reference, [{"scheme": "bger", "value": "4A_123/2024"},
                                             {"scheme": "generic", "value": "R-17"}]) \
            == "4A_123/2024; R-17"

    def test_multi_value_and_empty(self, registry):
        party = _spec(registry, "party")
        assert view.format_value(party, [{"name": "Muster AG"}, {"hidden": True}]) \
            == "Muster AG; verborgen"
        assert view.format_value(party, []) == ""
        assert view.format_value(party, None) == ""

    def test_number(self):
        assert view.format_value({"datatype": "number"}, "1234.5") == "1'234.5"

    def test_unknown_field_is_guessed_from_the_shape(self):
        assert view.format_value(None, {"amount": "5", "currency": "EUR"}) == "EUR 5.00"
        assert view.format_value(None, {"lo": "2024-03-15", "hi": "2024-03-15",
                                        "precision": "day"}) == "15.03.2024"
        assert view.format_value(None, "x") == "x"


class TestFieldsDisplay:
    def test_registry_order_without_title_or_empties(self, registry):
        shown = view.fields_display(registry, {
            "title": "Vertrag", "mandant": {"name": "Muster AG"}, "doc_type": "invoice",
            "author": [], "zzz": "frei",
        })
        assert shown == [
            {"key": "doc_type", "label": "Dokumentart", "text": "Rechnung"},
            {"key": "mandant", "label": "Mandant", "text": "Muster AG"},
            {"key": "zzz", "label": "zzz", "text": "frei"},
        ]

    def test_special_fields_stay_off_cards(self, registry):
        fields = {"patient": {"name": "Beispiel Patient"}}
        assert view.fields_display(registry, fields) == []
        assert view.fields_display(registry, fields, include_special=True)[0]["key"] == "patient"

    def test_nothing_returned_means_nothing_shown(self, registry):
        assert view.fields_display(registry, None) == []
        assert view.fields_display(registry, {}) == []


def test_layer_labels():
    assert [view.layer_label(x) for x in ("manual", "upload", "rule", "extracted")] == [
        "Manuell", "Upload", "Ordnervorgabe", "Extrahiert"]
    assert view.layer_label(None) == ""


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

class TestDisplayTitle:
    POINTER = "rc-sync/Muster AG/GJ 2024/Rechnung_17.pdf"

    # /secured/query hits carry no title of their own (query_two_stage.py),
    # so hit_title is None here, as in production.
    def test_the_file_name_as_title_gives_the_stem(self):
        assert view.display_title(self.POINTER, None, "Rechnung_17.pdf") == ("Rechnung_17", False)
        assert view.display_title(self.POINTER, None, "RECHNUNG_17") == ("Rechnung_17", False)

    def test_a_run_on_title_gives_the_stem(self):
        run_on = "Rechnung " + "Position " * 14
        assert len(run_on) > 100
        assert view.display_title(self.POINTER, None, run_on) == ("Rechnung_17", False)

    def test_a_real_title_is_kept(self):
        assert view.display_title(self.POINTER, "Rechnung_17.pdf", "Vertrag mit Muster AG") \
            == ("Vertrag mit Muster AG", True)

    def test_without_a_values_title_the_old_rule_runs(self):
        from knovas_client import _display_title_for_hit

        for hit_title in (None, "", "Kurz", "x" * 300):
            assert view.display_title(self.POINTER, hit_title, None) == (
                _display_title_for_hit(self.POINTER, hit_title), False)

    def test_title_from_values_bounds(self):
        assert view.title_from_values("a/b.pdf", "x" * 100) == "x" * 100
        assert view.title_from_values("a/b.pdf", "x" * 101) is None
        assert view.title_from_values("a/b.pdf", "   ") is None
        assert view.title_from_values("a\\B.PDF", "b.pdf") is None
        assert view.title_from_values("", "Titel") == "Titel"
        assert view.title_from_values("a/b.pdf", 42) is None


class TestFindRow:
    def test_a_find_document_reads_like_a_search_row(self, registry):
        row = view.find_row({"pointer": "rc-sync/Muster AG/Rechnung_17.pdf",
                             "document_uuid": "u-1", "title": "Rechnung_17.pdf",
                             "fields": {"doc_type": "invoice", "title": "Rechnung_17.pdf"}},
                            registry)
        assert row["doc_id"] == row["path"] == "rc-sync/Muster AG/Rechnung_17.pdf"
        assert (row["title"], row["title_from_values"]) == ("Rechnung_17", False)
        assert row["fields_display"] == [{"key": "doc_type", "label": "Dokumentart",
                                          "text": "Rechnung"}]
        assert row["document_uuid"] == "u-1"

    def test_a_real_anchor_title_is_kept_without_fields(self, registry):
        row = view.find_row({"pointer": "a/b.pdf", "title": "Vertrag mit Muster AG"}, registry)
        assert (row["title"], row["title_from_values"]) == ("Vertrag mit Muster AG", True)
        assert "fields" not in row and "fields_display" not in row


class TestMatchNames:
    NAMES = ["Beispiel GmbH", "Muster AG", "Muster & Partner", "Treuhand Muster"]

    def test_prefix_first_then_substring(self):
        assert view.match_names(self.NAMES, "mus") == [
            "Muster AG", "Muster & Partner", "Treuhand Muster"]

    def test_short_input_and_limit(self):
        assert view.match_names(self.NAMES, " m ") == []
        assert view.match_names(self.NAMES, "er", limit=2) == [
            "Muster AG", "Muster & Partner"]
        assert view.match_names(None, "muster") == []


# ---------------------------------------------------------------------------
# Honesty
# ---------------------------------------------------------------------------

class TestFilterState:
    @pytest.mark.parametrize("where_sent, meta, state", [
        (None, {"where": {"applied": True}}, "none"),
        ({}, {}, "none"),
        (False, {}, "none"),
        ({"doc_type": "invoice"}, {"where": {"applied": True}}, "applied"),
        ({"doc_type": "invoice"}, {"where": {"applied": True, "may_be_partial": False}}, "applied"),
        ({"doc_type": "invoice"}, {"where": {"applied": True, "may_be_partial": True}}, "partial"),
        ({"doc_type": "invoice"}, {}, "not_applied"),
        ({"doc_type": "invoice"}, {"where": None}, "not_applied"),
        ({"doc_type": "invoice"}, {"where": {"applied": "true"}}, "not_applied"),
        ({"doc_type": "invoice"}, {"where": {"applied": 1}}, "not_applied"),
        ({"doc_type": "invoice"}, {"where": {"applied": False}}, "not_applied"),
        ({"doc_type": "invoice"}, {"where": ["applied"]}, "not_applied"),
        ({"doc_type": "invoice"}, None, "not_applied"),
        (True, {"where": {"applied": True}}, "applied"),
    ])
    def test_truth_table(self, where_sent, meta, state):
        assert view.filter_state(where_sent, meta) == state


class TestListingNotice:
    @pytest.mark.parametrize("page, incomplete, total", [
        # first page of several: complete is false on every page with a successor
        ({"next_after": "o50", "complete": False, "total_count": 120}, False, 120),
        # a middle page
        ({"next_after": "o100", "complete": False}, False, None),
        # the last page, the walk reached the end
        ({"next_after": None, "complete": True}, False, None),
        # the last page, the scan budget ran out
        ({"next_after": None, "complete": False}, True, None),
        # a single page that is both first and last
        ({"next_after": None, "complete": True, "total_count": 3}, False, 3),
        # total_count null on overflow, and never a bool
        ({"next_after": None, "complete": False, "total_count": None}, True, None),
        ({"next_after": None, "complete": True, "total_count": True}, False, None),
        # complete missing is not "false"
        ({"next_after": None}, False, None),
    ])
    def test_truth_table(self, page, incomplete, total):
        notice = view.listing_notice(page)
        assert notice["incomplete"] is incomplete
        assert notice["text"] == (view.INCOMPLETE_LISTING if incomplete else None)
        assert notice["total_count"] == total

    def test_garbage(self):
        assert view.listing_notice(None) == {"incomplete": False, "text": None,
                                             "total_count": None}


class TestResolvedChips:
    def test_chips_come_from_the_persons_own_input(self, registry):
        where = {"doc_type": "invoice", "mandant": {"name": "Muster AG"}, "period": "GJ 2024"}
        echo = {"applied": True, "clauses": 3, "resolved": [
            {"field": "doc_type", "op": "eq", "value": "invoice"},
            {"field": "mandant", "op": "eq", "value": {"name": "Muster AG"}, "resolved_nodes": 1},
            {"field": "period", "op": "eq", "interval": {"lo": "2024-01-01", "hi": "2024-12-31"}},
        ]}
        chips = view.resolved_chips(where, echo, registry)
        assert chips[0] == {"field": "doc_type", "label": "Dokumentart", "op": "eq",
                            "text": "Rechnung"}
        assert chips[1]["text"] == "Muster AG" and chips[1]["linked_count"] == 1
        assert chips[1]["linked_text"] == "1 verkn\u00fcpfter Eintrag"
        assert chips[2]["text"] == "GJ 2024" and chips[2]["label"] == "Zeitraum"

    def test_ranges_lists_and_node_ids(self, registry):
        chips = view.resolved_chips({
            "document_date": {"gte": "01.10.2026"},
            "doc_type": ["invoice", "contract"],
            "mandant": {"node_id": "m1"},
            "period": {"gte": "2024", "lte": "2025"},
        }, None, registry)
        texts = {c["field"]: c["text"] for c in chips}
        assert texts == {"document_date": "ab 01.10.2026", "doc_type": "Rechnung; Vertrag",
                         "mandant": view.LINKED_ENTITY, "period": "2024 \u2013 2025"}
        assert {c["field"]: c["op"] for c in chips}["doc_type"] == "in"
        assert all("linked_count" not in c for c in chips)

    def test_counts_add_up_and_plural(self, registry):
        chips = view.resolved_chips(
            {"mandant": ["Muster AG", "Beispiel GmbH"]},
            {"resolved": [{"field": "mandant", "op": "in", "resolved_nodes": 2}]}, registry)
        assert chips[0]["linked_count"] == 2
        assert chips[0]["linked_text"] == "2 verkn\u00fcpfte Eintr\u00e4ge"

    def test_not_a_filter(self, registry):
        assert view.resolved_chips(None, None, registry) == []


# ---------------------------------------------------------------------------
# Bounds and permissions
# ---------------------------------------------------------------------------

class TestValidateWhere:
    def test_a_valid_filter_passes_unchanged(self):
        where = {"doc_type": ["invoice"], "mandant": {"in": [{"name": "Muster AG"}]},
                 "legal_ch.court": "x", "a-b c": 1}
        assert view.validate_where(where) == where

    @pytest.mark.parametrize("bad", [
        None, [], "doc_type", {},
        {f"k{i}": 1 for i in range(9)},
        {"Doc_Type": "x"}, {"": "x"}, {"k" * 65: "x"}, {"doc/type": "x"},
        {"k": {"in": [{"name": {"deep": "x"}}]}},
        {"k": "x" * 9000},
        {"k": object()},
    ])
    def test_out_of_bounds_raises(self, bad):
        with pytest.raises(ValueError):
            view.validate_where(bad)

    def test_eight_keys_and_three_levels_are_allowed(self):
        view.validate_where({f"k{i}": {"in": [{"name": "x"}]} for i in range(8)})

    def test_the_message_never_carries_a_value(self):
        with pytest.raises(ValueError) as caught:
            view.validate_where({"k": "Muster-Sentinel " * 1000})
        assert "Sentinel" not in str(caught.value)


class TestCanEdit:
    @pytest.mark.parametrize("roles, edit_roles, sensitivity, held, identity_on, allowed", [
        (["admin"], ["admin"], "normal", False, True, True),
        (["member"], ["admin"], "normal", False, True, False),
        (["member"], ["admin", "member"], "normal", False, True, True),
        (["member"], ["admin", "member"], "special", False, True, False),
        (["admin"], ["admin"], "special", False, True, True),
        (["admin"], ["admin"], "normal", True, True, False),
        (["admin"], ["admin"], "normal", False, False, False),
        ([], ["admin"], "normal", False, True, False),
        (["ingestion_manager"], ["ingestion_manager"], "normal", False, True, True),
        (["Admin "], ["admin"], "normal", False, True, True),
    ])
    def test_matrix(self, roles, edit_roles, sensitivity, held, identity_on, allowed):
        assert view.can_edit(roles, edit_roles, sensitivity, held, identity_on) is allowed


def test_deadline_fields(registry):
    reg = view.sanitize_registry([field_def("deadline", "date", "Frist", date_role="due")])
    assert view.is_deadline_field(reg, "deadline")
    assert not view.is_deadline_field(registry, "document_date")
    assert not view.is_deadline_field(registry, "nope")


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

SAMPLE_PROFILE = {
    "identifier_prefix": "rc-sync",
    "sources": [
        {"path": "/mnt/autodoc/Mandate", "recursive": True, "access_groups": [],
         "fields": {"doc_type": "invoice", "period": "GJ 2024"},
         "field_templates": ["{mandant}/{period}/**", "Archiv/*/{matter}"]},
        {"path": "/mnt/autodoc/Postfach", "recursive": True, "access_groups": ["g-lit"],
         "metadata_fields": ["email_date", "email_doc_type", "language"]},
        {"path": "/mnt/autodoc/Alt", "recursive": True, "access_groups": []},
    ],
}


class TestProfileFieldKeys:
    def test_static_capture_and_metadata_keys(self):
        assert view.profile_field_keys(SAMPLE_PROFILE) == {
            "doc_type", "period", "mandant", "matter", "document_date", "language"}

    def test_json_text_and_dataclass_forms(self):
        assert view.profile_field_keys(json.dumps(SAMPLE_PROFILE)) == \
            view.profile_field_keys(SAMPLE_PROFILE)

        class Source:
            fields = (("doc_type", "invoice"),)
            field_templates = ("{client}/**",)
            metadata_fields = ("document_author",)

        class Profile:
            sources = [Source()]

        class Version:
            profile = Profile()

        assert view.profile_field_keys(Version()) == {"doc_type", "client", "author"}

    def test_nothing_readable_is_nothing(self):
        assert view.profile_field_keys(None) == set()
        assert view.profile_field_keys("not json") == set()
        assert view.profile_field_keys({"sources": [{"path": "/x"}]}) == set()

    def test_metadata_targets(self):
        assert view.METADATA_TARGETS == {
            "language": "language", "email_date": "document_date",
            "email_doc_type": "doc_type", "email_author": "author",
            "document_author": "author", "keywords": "keywords",
            "document_status": "status"}

    def test_the_file_property_items_name_their_targets(self):
        profile = {"sources": [{"path": "/a", "metadata_fields": ["keywords", "document_status"]}]}
        assert view.profile_field_keys(profile) == {"keywords", "status"}


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class TestMessages:
    def test_a_field_is_named_by_its_label(self, registry):
        text = view.error_message("unknown_field",
                                  {"path": "where.mandat", "suggest": ["mandant"]}, registry)
        assert "\u201emandat\u201c" in text and "\u201eMandant\u201c" in text
        text = view.error_message("invalid_value", {"path": "set.doc_type[0]"}, registry)
        assert "Dokumentart" in text

    def test_no_value_is_ever_repeated(self, registry):
        sentinel = "Muster-Sentinel-AG"
        for code in ("invalid_value", "type_mismatch", "unknown_field", "ambiguous_field",
                     "version_conflict", "where_requires_calibration", "something_new"):
            text = view.error_message(code, {"path": "where.mandant", "value": sentinel,
                                             "error": sentinel}, registry)
            assert sentinel not in text

    def test_missing_calibration_is_never_called_temporary(self):
        text = view.error_message("where_requires_calibration")
        assert "Kalibrierung fehlt" in text
        assert "vor\u00fcbergehend" not in text.lower()
        assert view.error_message("filters_need_calibration") == text

    def test_pointer_path_means_knovas_needs_an_update(self):
        assert "Knovas-Update n\u00f6tig" in view.error_message(
            "invalid_value", {"path": "pointer"})

    def test_every_code_has_a_text(self):
        for code in ("where_unsupported", "where_unavailable", "filter_not_applied",
                     "ambiguous_date", "restricted_identifier", "where_too_complex",
                     "invalid_cursor", "change_not_authorized", "anchor_quarantined",
                     "registry_write_requires_full_clearance", "key_looks_personal",
                     "field_key_exists", "field_type_locked", "field_cap_reached",
                     "invalid_field_definition", "pack_not_found", "NOT_FOUND", "HTTP_404",
                     "doc_fields_unavailable", "too_many_requests", "transport_error"):
            assert view.error_message(code)

    def test_warnings(self):
        assert view.warning_text("unresolved_entity") == "nicht verkn\u00fcpft"
        assert view.warning_text("ambiguous_date") == "Datum mehrdeutig \u2013 bitte pr\u00fcfen"
        assert view.warning_text("new_code") == "new_code"

    @pytest.mark.parametrize("reason", ["no_candidates", "below_relevance_floor",
                                        "empty_where", "empty_scope", None, "other"])
    def test_empty_states_speak_only_of_what_the_person_can_see(self, reason):
        text = view.no_results_message(reason)
        if reason != "below_relevance_floor":
            assert "f\u00fcr Sie sichtbar" in text
        assert "keine Dokumente" not in text.lower()

    def test_hint_texts(self):
        assert view.TITLE_NOT_SEARCHABLE == "Titel wird angezeigt, nicht durchsucht"
        assert view.PRIVILEGED_HINT == "Kennzeichnung, keine Zugriffsbeschr\u00e4nkung"
        assert "keine Fristenkontrolle" in view.DEADLINE_BANNER


class TestValuesEditAudit:
    """One ``document.values_edited`` convention for both edit routes."""

    def test_outcome_fits_the_audit_log_check(self):
        # 0001_identity.sql: CHECK (outcome IN ('ok', 'denied', 'error')).
        assert view.AUDIT_OUTCOME_REFUSED in ("ok", "denied", "error")
        assert view.AUDIT_OUTCOME_REFUSED == "denied"
        assert view.VALUES_EDIT_REFUSALS == {"version_conflict", "change_not_authorized",
                                             "anchor_quarantined"}

    def test_keys_counts_and_versions_never_values(self):
        detail = view.values_edit_audit_detail(
            {"set": {"title": "Sentinel", "party": "Sentinel AG"}, "unset": ["status"],
             "add": {"tags": ["Sentinel-Tag"]}},
            version_from=3, version_to=4, warning_codes=["unresolved_entity", "", None])
        assert detail == {"keys": ["party", "status", "tags"],
                          "ops": {"set": 2, "unset": 1, "add": 1, "remove": 0},
                          "title_changed": True, "description_changed": False,
                          "version_from": 3, "version_to": 4,
                          "warning_codes": ["unresolved_entity"]}
        assert "Sentinel" not in json.dumps(detail)

    def test_a_refusal_names_the_code(self):
        detail = view.values_edit_audit_detail({"set": {"doc_type": "x"}}, version_from=1,
                                               version_to=True, code="version_conflict")
        assert detail["code"] == "version_conflict"
        assert detail["version_to"] is None, "a bool is not a version"
        assert "code" not in view.values_edit_audit_detail({}, version_from=None,
                                                           version_to=None)


def test_new_modules_are_ascii_only():
    """scripts/check_ascii_py.py: umlauts go in templates or as escapes."""
    tests = Path(__file__).resolve().parent
    for path in (SRC / "doc_fields_view.py", SRC / "doc_fields_capability.py",
                 tests / "doc_fields_fakes.py", tests / "test_doc_fields_view.py",
                 tests / "test_doc_fields_capability.py",
                 tests / "test_knovas_client_doc_fields.py"):
        data = path.read_bytes()
        assert all(b < 0x80 for b in data), path.name
