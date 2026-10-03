"""The Dokumentfelder tab (spec 4.6): registry, packs, settings, folder rules.

Pure helpers first (no app, no database), then the routes through the real
app against ``FakeDocFieldsApi`` and a recording RemoteController. The
route tests pin the controls, not the drawing: ``require_admin`` on every
route, CSRF before any Knovas write, no forms while Knovas has the feature
off, audit rows with keys and ids only.

Placeholder names only ("Muster AG", "Beispiel GmbH").
"""

from __future__ import annotations

import inspect
import json
import logging
import pathlib
import re

import pytest

flask = pytest.importorskip("flask")

from conftest import PLATFORM_DB_TEST_DSN, _identity_app, _person, platform_db_reachable
from doc_fields_fakes import FakeDocFieldsApi, core_fields, field_def

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
TEMPLATES = SRC / "web_interface" / "templates"
STATIC = SRC / "web_interface" / "static"

needs_db = pytest.mark.skipif(not platform_db_reachable(),
                              reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


def _rows(*choices):
    """The choice editor's inputs for ``(code, {lang: label}, "Name, Name")``."""
    form = {}
    for i, (code, labels, aliases) in enumerate(choices):
        form[f"choice_code_{i}"] = code
        for lang in ("de", "fr", "it", "en"):
            form[f"choice_label_{lang}_{i}"] = labels.get(lang, "")
        form[f"choice_aliases_{i}"] = aliases
    return form


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestDefinitionFromForm:
    def test_an_enum_field_with_labels_and_codes(self):
        from web_interface.admin_doc_fields import definition_from_form

        defn = definition_from_form({
            "key": "kostenstelle", "datatype": "enum", "cardinality": "one",
            "label_de": "Kostenstelle", "label_fr": "Centre de co\u00fbts", "label_it": "",
            "aliases": "kst, kostenstelle-nr, kst", "sensitivity": "normal", "display": "1",
            **_rows(("4100", {"de": "Verwaltung", "fr": "Administration"}, "Verw, Admin, Verw"),
                    ("", {}, ""), ("4200", {}, "")),
        })
        assert defn == {
            "key": "kostenstelle", "datatype": "enum", "cardinality": "one",
            "labels": {"de": "Kostenstelle", "fr": "Centre de co\u00fbts"},
            "aliases": ["kst", "kostenstelle-nr"],
            "enum_values": [{"code": "4100", "labels": {"de": "Verwaltung", "fr": "Administration"},
                             "aliases": ["Verw", "Admin"]}, "4200"],
            "display": True, "facet": False, "sensitivity": "normal",
        }

    def test_type_specific_inputs_go_only_with_their_type(self):
        from web_interface.admin_doc_fields import definition_from_form

        form = {"key": "mandat", "datatype": "text", "target_node_type_id": "t1",
                "date_role": "due", **_rows(("a", {}, ""))}
        defn = definition_from_form(form)
        assert "target_node_type_id" not in defn
        assert "date_role" not in defn and "enum_values" not in defn
        entity = definition_from_form({**form, "datatype": "entity_ref"})
        assert entity["target_node_type_id"] == "t1"
        dated = definition_from_form({**form, "datatype": "date"})
        assert dated["date_role"] == "due"

    @pytest.mark.parametrize("form", [
        {"key": "Kostenstelle", "datatype": "text"},
        {"key": "1abc", "datatype": "text"},
        {"key": "title", "datatype": "text"},
        {"key": "pointer", "datatype": "text"},
        {"key": "ok_key", "datatype": "whatever"},
        {"key": "ok_key", "datatype": "enum"},
        {"key": "ok_key", "datatype": "enum", **_rows(("a b", {}, ""))},
        {"key": "ok_key", "datatype": "enum", **_rows(("a", {}, ""), ("a", {}, ""))},
        {"key": "ok_key", "datatype": "enum", **_rows(("", {"de": "Ohne Code"}, ""))},
        {"key": "ok_key", "datatype": "enum",
         **_rows(("a", {}, ", ".join(f"n{i}" for i in range(33))))},
        {"key": "ok_key", "datatype": "date", "date_role": "tomorrow"},
    ])
    def test_refused_before_knovas(self, form):
        from web_interface.admin_doc_fields import FormError, definition_from_form

        with pytest.raises(FormError):
            definition_from_form(form)

    def test_a_choice_error_names_the_row_not_the_value(self):
        from web_interface.admin_doc_fields import FormError, parse_choice_rows

        with pytest.raises(FormError) as caught:
            parse_choice_rows(_rows(("ok", {}, ""), ("Muster AG", {}, "")))
        assert "Zeile 2" in str(caught.value)
        assert "Muster" not in str(caught.value)


class TestChangesFromForm:
    CURRENT = {
        "id": "f1", "key": "doc_type", "datatype": "enum", "cardinality": "one",
        "labels": {"de": "Dokumentart", "en": "Document type", "rm": "Gener"},
        "aliases": ["art"],
        "enum_values": [{"code": "invoice", "labels": {"de": "Rechnung", "fr": "Facture"},
                         "aliases": ["rg"]}, "other"],
        "display": True, "facet": True, "sensitivity": "normal", "warnings": [],
    }

    def _form(self, rows=None, **over):
        form = {"label_de": "Dokumentart", "label_fr": "", "label_it": "",
                "label_en": "Document type", "aliases": "art", "flags": "1",
                "display": "1", "facet": "1", "sensitivity": "normal"}
        form.update(_rows(*(rows if rows is not None else (
            ("invoice", {"de": "Rechnung", "fr": "Facture"}, "rg"), ("other", {}, "")))))
        form.update(over)
        return form

    def test_an_untouched_form_changes_nothing(self):
        from web_interface.admin_doc_fields import changes_from_form

        assert changes_from_form(self._form(), self.CURRENT) == {}

    def test_a_label_change_keeps_languages_the_form_cannot_show(self):
        from web_interface.admin_doc_fields import changes_from_form, needs_use_confirmation

        changes = changes_from_form(self._form(label_de="Art des Dokuments"), self.CURRENT)
        assert changes == {"labels": {"de": "Art des Dokuments", "en": "Document type",
                                      "rm": "Gener"}}
        assert not needs_use_confirmation(changes)

    def test_the_rows_are_what_is_sent(self):
        from web_interface.admin_doc_fields import changes_from_form

        changes = changes_from_form(self._form(rows=[
            ("invoice", {"de": "Kreditorenbeleg", "fr": "Facture"}, "rg"), ("other", {}, ""),
            ("offer", {"de": "Offerte", "en": "Offer"}, "Angebot")]), self.CURRENT)
        assert changes == {"enum_values": [
            {"code": "invoice", "labels": {"de": "Kreditorenbeleg", "fr": "Facture"},
             "aliases": ["rg"]},
            "other",
            {"code": "offer", "labels": {"de": "Offerte", "en": "Offer"}, "aliases": ["Angebot"]},
        ]}

    def test_an_emptied_input_goes_and_unseen_languages_stay(self):
        from web_interface.admin_doc_fields import changes_from_form

        current = dict(self.CURRENT, enum_values=[
            {"code": "invoice", "labels": {"de": "Rechnung", "fr": "Facture", "rm": "Quint"},
             "aliases": ["rg"]}])
        changes = changes_from_form(self._form(rows=[("invoice", {"de": "Rechnung"}, "")]),
                                    current)
        assert changes == {"enum_values": [
            {"code": "invoice", "labels": {"de": "Rechnung", "rm": "Quint"}}]}

    def test_a_removed_row_removes_the_choice(self):
        from web_interface.admin_doc_fields import changes_from_form, needs_use_confirmation

        changes = changes_from_form(self._form(rows=[
            ("invoice", {"de": "Rechnung", "fr": "Facture"}, "rg")]), self.CURRENT)
        assert changes == {"enum_values": [
            {"code": "invoice", "labels": {"de": "Rechnung", "fr": "Facture"}, "aliases": ["rg"]}]}
        assert needs_use_confirmation(changes)

    def test_flags_are_read_only_with_their_marker(self):
        from web_interface.admin_doc_fields import changes_from_form

        without_marker = self._form()
        del without_marker["flags"], without_marker["display"], without_marker["facet"]
        assert changes_from_form(without_marker, self.CURRENT) == {}
        unticked = self._form()
        del unticked["display"]
        assert changes_from_form(unticked, self.CURRENT) == {"display": False}

    def test_a_hidden_target_is_never_sent_back(self):
        from web_interface.admin_doc_fields import changes_from_form

        current = {"key": "client", "datatype": "entity_ref", "target_node_type_id": None,
                   "warnings": ["target_type_hidden"], "labels": {}}
        assert changes_from_form({"target_node_type_id": ""}, current) == {}
        visible = {**current, "target_node_type_id": "t1", "warnings": []}
        assert changes_from_form({"target_node_type_id": ""}, visible) == {
            "target_node_type_id": None}

    def test_type_relevant_changes_need_the_confirmation(self):
        from web_interface.admin_doc_fields import needs_use_confirmation

        assert needs_use_confirmation({"sensitivity": "special"})
        assert needs_use_confirmation({"enum_values": ["a"]})
        assert not needs_use_confirmation({"labels": {}, "aliases": [], "display": True})


class TestChoiceRows:
    def test_rows_in_order_empty_ones_skipped(self):
        from web_interface.admin_doc_fields import parse_choice_rows

        form = {"choice_code_10": "c", "choice_code_2": "b", "choice_label_fr_2": "B fr",
                "choice_aliases_2": "bb, b2, bb", "choice_code_0": "a", "choice_code_5": "",
                "choice_label_de_5": "", "choice_aliases_5": ""}
        assert parse_choice_rows(form) == [
            {"code": "a", "labels": {}, "aliases": []},
            {"code": "b", "labels": {"fr": "B fr"}, "aliases": ["bb", "b2"]},
            {"code": "c", "labels": {}, "aliases": []},
        ]

    def test_a_new_field_s_choices(self):
        from web_interface.admin_doc_fields import merge_choices

        assert merge_choices(None, [
            {"code": "4100", "labels": {"de": "Verwaltung"}, "aliases": ["Verw"]},
            {"code": "4200", "labels": {}, "aliases": []}]) == [
            {"code": "4100", "labels": {"de": "Verwaltung"}, "aliases": ["Verw"]}, "4200"]

    def test_the_editor_shows_each_language_and_the_other_names(self):
        from web_interface.admin_doc_fields import choice_rows

        assert choice_rows([{"code": "invoice", "labels": {"de": "Rechnung", "rm": "Quint"},
                             "aliases": ["RG", "Beleg"]}, "other"]) == [
            {"code": "invoice", "labels": {"de": "Rechnung", "fr": "", "it": "", "en": ""},
             "aliases_text": "RG, Beleg"},
            {"code": "other", "labels": {"de": "", "fr": "", "it": "", "en": ""},
             "aliases_text": ""}]


class TestReadingSettings:
    """F1: how values of a field are read -- per datatype, sent on create
    only when it differs from Knovas's default, on edit only when changed."""

    def test_each_setting_goes_only_with_its_type(self):
        from web_interface.admin_doc_fields import definition_from_form

        everything = {"code_scheme": "bger", "link_policy": "never", "fy_start_month": "7",
                      "fy_label": "start", "date_order": "mdy"}
        code = definition_from_form({"key": "aktenzeichen", "datatype": "code", **everything})
        assert code["code_scheme"] == "bger"
        assert not {"link_policy", "fy_start_month", "fy_label", "date_order"} & set(code)
        entity = definition_from_form({"key": "gegenpartei", "datatype": "entity_ref",
                                       **everything})
        assert entity["link_policy"] == "never" and "code_scheme" not in entity
        period = definition_from_form({"key": "geschaeftsjahr", "datatype": "period",
                                       **everything})
        assert (period["fy_start_month"], period["fy_label"]) == (7, "start")
        date = definition_from_form({"key": "eingang", "datatype": "date", **everything})
        assert date["date_order"] == "mdy" and "fy_start_month" not in date

    def test_defaults_are_not_sent(self):
        from web_interface.admin_doc_fields import definition_from_form

        assert "code_scheme" not in definition_from_form(
            {"key": "belegnummer", "datatype": "code", "code_scheme": "generic"})
        assert "link_policy" not in definition_from_form(
            {"key": "gegenpartei", "datatype": "entity_ref", "link_policy": "resolve"})
        period = definition_from_form({"key": "jahr", "datatype": "period",
                                       "fy_start_month": "", "fy_label": ""})
        assert "fy_start_month" not in period and "fy_label" not in period
        assert "date_order" not in definition_from_form(
            {"key": "eingang", "datatype": "date", "date_order": ""})
        january = definition_from_form({"key": "jahr", "datatype": "period",
                                        "fy_start_month": "1"})
        assert january["fy_start_month"] == 1 and "fy_label" not in january

    @pytest.mark.parametrize("form", [
        {"key": "k", "datatype": "code", "code_scheme": "isbn"},
        {"key": "k", "datatype": "entity_ref", "link_policy": "maybe"},
        {"key": "k", "datatype": "period", "fy_start_month": "13", "fy_label": "start"},
        {"key": "k", "datatype": "period", "fy_start_month": "Juli", "fy_label": "start"},
        {"key": "k", "datatype": "period", "fy_start_month": "\u00b2", "fy_label": "start"},
        {"key": "k", "datatype": "period", "fy_start_month": "7"},
        {"key": "k", "datatype": "period", "fy_label": "middle"},
        {"key": "k", "datatype": "date", "date_order": "dym"},
    ])
    def test_refused_before_knovas(self, form):
        from web_interface.admin_doc_fields import FormError, definition_from_form

        with pytest.raises(FormError):
            definition_from_form(form)

    CODE = {"id": "f2", "key": "aktenzeichen", "datatype": "code", "code_scheme": "generic",
            "labels": {"de": "Aktenzeichen"}, "aliases": [], "display": False, "facet": False,
            "sensitivity": "normal", "warnings": []}
    PERIOD = {"id": "f3", "key": "geschaeftsjahr", "datatype": "period", "fy_start_month": 7,
              "fy_label": "start", "labels": {"de": "Gesch\u00e4ftsjahr"}, "aliases": [],
              "display": False, "facet": False, "sensitivity": "normal", "warnings": []}
    DATE = {"id": "f4", "key": "eingang", "datatype": "date", "date_order": None,
            "labels": {}, "display": False, "facet": False, "warnings": []}

    def test_an_edit_sends_only_changed_settings(self):
        from web_interface.admin_doc_fields import changes_from_form, needs_use_confirmation

        assert changes_from_form({"code_scheme": "generic"}, self.CODE) == {}
        changes = changes_from_form({"code_scheme": "bger"}, self.CODE)
        assert changes == {"code_scheme": "bger"} and needs_use_confirmation(changes)
        assert changes_from_form({"fy_start_month": "7", "fy_label": "start"},
                                 self.PERIOD) == {}
        assert changes_from_form({"fy_start_month": "", "fy_label": ""}, self.PERIOD) == {
            "fy_start_month": None, "fy_label": None}
        assert changes_from_form({"date_order": ""}, self.DATE) == {}
        assert changes_from_form({"date_order": "ymd"}, self.DATE) == {"date_order": "ymd"}
        # A setting of another datatype is never read from the form.
        assert changes_from_form({"date_order": "ymd", "code_scheme": "iban"},
                                 self.PERIOD) == {}

    def test_a_business_year_keeps_its_naming(self):
        from web_interface.admin_doc_fields import FormError, changes_from_form

        with pytest.raises(FormError):
            changes_from_form({"fy_label": ""}, self.PERIOD)  # July stays, naming gone
        assert changes_from_form({"fy_start_month": "9"}, self.PERIOD) == {"fy_start_month": 9}

    def test_a_scheme_the_form_does_not_list_stays(self):
        """Knovas takes any code scheme of its pattern (registry.py
        _SCHEME_RE). The field's own scheme stays a valid choice on edit, so
        an edit that leaves it alone never turns it into "generic"."""
        from web_interface.admin_doc_fields import FormError, changes_from_form

        other = {**self.CODE, "code_scheme": "legal_case_ch"}
        assert changes_from_form({"code_scheme": "legal_case_ch"}, other) == {}
        assert changes_from_form({"code_scheme": "bger"}, other) == {"code_scheme": "bger"}
        with pytest.raises(FormError):
            changes_from_form({"code_scheme": "isbn"}, other)

    def test_reading_text_for_the_registry_table(self):
        from web_interface.admin_doc_fields import reading_text

        assert reading_text({"datatype": "code", "code_scheme": "iban"}) == \
            "Schema: IBAN (mit Pr\u00fcfziffer)"
        assert reading_text({"datatype": "code", "code_scheme": "generic"}) == ""
        assert reading_text(self.PERIOD) == \
            "Gesch\u00e4ftsjahr ab Juli, benannt nach dem Anfangsjahr"
        assert reading_text({"datatype": "date", "date_order": "mdy"}) == \
            "liest 03/04/2024 als Monat/Tag/Jahr"
        assert reading_text({"datatype": "entity_ref", "link_policy": "never"}) == \
            "Namen bleiben unverkn\u00fcpft"
        assert reading_text({"datatype": "text"}) == ""

    def test_the_field_counter(self):
        from web_interface.admin_doc_fields import field_count_text

        assert field_count_text(core_fields()) == "12 von 256 Feldern"
        full = [field_def(f"f{i:03d}", "text", "F") for i in range(256)]
        assert field_count_text(full).startswith(
            "256 von 256 Feldern \u2013 die H\u00f6chstzahl ist erreicht")


class TestRuleValuesFromForm:
    @pytest.fixture(autouse=True)
    def _registry(self):
        from doc_fields_view import sanitize_registry

        self.registry = sanitize_registry(core_fields() + [
            field_def("privileged", "bool", "Anwaltsgeheimnis", pack="legal_ch"),
            field_def("old_key", "text", "Alt", status="deprecated"),
        ])

    def test_values_are_typed_by_the_registry(self):
        from web_interface.admin_doc_fields import rule_values_from_form

        values = rule_values_from_form({
            "rule_key_0": "doc_type", "rule_value_0": "invoice",
            "rule_key_1": "mandant", "rule_value_1": "Muster AG",
            "rule_key_2": "party", "rule_value_2": "Muster AG; Beispiel GmbH;",
            "rule_key_3": "privileged", "rule_value_3": "ja",
            "rule_key_4": "status", "rule_clear_4": "1",
        }, self.registry)
        assert values == {"doc_type": "invoice", "mandant": "Muster AG",
                          "party": ["Muster AG", "Beispiel GmbH"], "privileged": True,
                          "status": None}

    @pytest.mark.parametrize("form", [
        {},
        {"rule_key_0": "unknown_key", "rule_value_0": "x"},
        {"rule_key_0": "old_key", "rule_value_0": "x"},
        {"rule_key_0": "doc_type", "rule_value_0": ""},
        {"rule_key_0": "doc_type", "rule_value_0": "a", "rule_key_1": "doc_type",
         "rule_value_1": "b"},
        {"rule_key_0": "privileged", "rule_value_0": "vielleicht"},
    ])
    def test_refused(self, form):
        from web_interface.admin_doc_fields import FormError, rule_values_from_form

        with pytest.raises(FormError):
            rule_values_from_form(form, self.registry)


class TestRows:
    def test_registry_rows_carry_labels_status_and_use(self):
        from web_interface.admin_doc_fields import registry_rows

        hidden = field_def("client", "entity_ref", "Klient", pack="legal_ch")
        hidden["warnings"] = ["target_type_hidden"]
        rows = registry_rows(
            core_fields() + [hidden, field_def("old", "text", "Alt", status="deprecated")],
            node_types=[{"id": "t-mandant", "name": "Mandant"}], in_use={"doc_type"})
        by = {r["key"]: r for r in rows}
        assert by["doc_type"]["in_use"] is True and by["amount"]["in_use"] is False
        assert by["doc_type"]["choices"][0] == {
            "code": "contract", "labels": {"de": "Vertrag", "fr": "", "it": "", "en": ""},
            "aliases_text": ""}
        assert by["mandant"]["target_name"] == "Mandant"
        assert by["client"]["target_name"] == "verborgen"
        assert by["client"]["warnings"] == ["Ziel-Typ f\u00fcr Sie nicht sichtbar"]
        assert by["old"]["deprecated"] is True
        assert by["patient"]["sensitivity"] == "special"

    def test_packs_are_labelled_by_the_console(self):
        from web_interface.admin_doc_fields import pack_rows

        rows = pack_rows([{"key": "core", "version": 2, "installed": True, "installed_version": 1},
                          {"key": "legal_ch", "version": 1, "installed": False,
                           "installed_version": None},
                          {"key": "x", "version": 1, "installed": False}])
        assert [r["label"] for r in rows] == ["Grundfelder", "Kanzlei (Schweiz)", "x"]
        assert rows[0]["upgradable"] is True and rows[1]["upgradable"] is False

    def test_install_summary_names_skipped_fields_and_missing_targets(self):
        from web_interface.admin_doc_fields import install_summary

        notice, warnings = install_summary(
            "legal_ch", {"installed": 9, "skipped": 1, "warnings": ["target_type_missing:court"]},
            [{"key": "court", "label": "Gericht"}])
        assert "9 Feld(er) angelegt" in notice and "1 \u00fcbersprungen" in notice
        assert warnings and "Gericht" in warnings[0] and "ohne Verkn\u00fcpfungsziel" in warnings[0]

    def test_unknown_profile_keys(self):
        from web_interface.admin_doc_fields import unknown_profile_keys

        raw = core_fields() + [field_def("old", "text", "Alt", status="deprecated")]
        raw[0]["aliases"] = ["Betrag_Total"]
        assert unknown_profile_keys({"doc_type", "old", "jahr", "betrag_total"}, raw) == [
            "jahr", "old"]

    def test_rule_rows_format_values_by_the_registry(self):
        from doc_fields_view import sanitize_registry
        from web_interface.admin_doc_fields import rule_rows

        rows = rule_rows([{"id": "r2", "pointer_prefix": "k/b/", "set": {"doc_type": "invoice",
                                                                        "status": None},
                           "version": 2},
                          {"id": "r1", "pointer_prefix": "k/a/", "set": {}, "version": 1}],
                         sanitize_registry(core_fields()))
        assert [r["id"] for r in rows] == ["r1", "r2"]
        assert rows[1]["values"][0] == {"key": "doc_type", "label": "Dokumentart",
                                        "text": "Rechnung"}
        assert rows[1]["values"][1]["text"].startswith("aufgehoben")

    def test_admin_group_ids_and_the_write_prediction(self):
        from web_interface.admin_doc_fields import admin_group_ids, may_write_registry

        groups = [{"group_id": "g1", "is_admin": True}, {"group_id": "g2", "is_admin": False},
                  {"group_id": "g3"}, "junk"]
        assert admin_group_ids(groups) == frozenset({"g1"})
        assert may_write_registry("ok", None) is True
        assert may_write_registry("ok", False) is True
        assert may_write_registry("forbidden", True) is False
        assert may_write_registry("error", True) is True
        assert may_write_registry("error", False) is False
        assert may_write_registry("error", None) is False


class TestLazyCapability:
    def test_nothing_is_asked_until_read_and_then_once(self, monkeypatch):
        import doc_fields_capability as dfc
        from web_interface.admin_doc_fields import LazyCapability

        calls = []
        monkeypatch.setattr(dfc, "capability_for",
                            lambda client: calls.append(client) or dfc.Capability.filters)
        lazy = LazyCapability(lambda: "client")
        assert calls == []
        assert lazy.shows_values and lazy.shows_listing and lazy.shows_filters
        assert lazy == "filters" and str(lazy) == "filters" and bool(lazy)
        assert calls == ["client"]

    def test_any_failure_reads_as_off(self):
        from web_interface.admin_doc_fields import LazyCapability

        def broken():
            raise RuntimeError("no client")

        lazy = LazyCapability(broken)
        assert lazy.value == "off" and not lazy.shows_values and not lazy


class TestStatic:
    def test_every_route_is_admin_gated(self):
        from web_interface import admin_doc_fields

        src = inspect.getsource(admin_doc_fields)
        assert src.count("@bp.route") == src.count("@require_admin") == 9

    def test_every_post_checks_csrf_before_calling_knovas(self):
        from web_interface import admin_doc_fields

        src = inspect.getsource(admin_doc_fields.attach_doc_field_routes)
        guard = src[src.index("def _guard_write("):src.index("def _write_failed(")]
        assert guard.index("_csrf_ok()") < guard.index("client_factory()")
        posts = re.findall(r'@bp\.route\("([^"]+)", methods=\["POST"\]\)\n'
                           r'    @require_admin\n    def (\w+)\(', src)
        assert len(posts) == 8
        for _path, name in posts:
            start = src.index(f"def {name}(")
            end = src.find("@bp.route", start)
            body = src[start:end if end != -1 else len(src)]
            assert "_guard_write(" in body, name
            first_call = min(i for i in (body.find("client_factory()"), body.find("_rc()"))
                             if i != -1)
            assert body.index("_guard_write(") < first_call, name

    def test_module_is_ascii_only(self):
        assert (SRC / "web_interface" / "admin_doc_fields.py").read_bytes().isascii()

    def test_script_never_parses_server_data_as_html(self):
        js = (STATIC / "js" / "admin_doc_fields.js").read_text(encoding="utf-8")
        assert "innerHTML" not in js and "insertAdjacentHTML" not in js
        assert "textContent" in js

    def test_choice_rows_grow_without_markup(self):
        js = (STATIC / "js" / "admin_doc_fields.js").read_text(encoding="utf-8")
        assert "cloneNode(true)" in js and "data-df-choice-add" in js
        html = (TEMPLATES / "admin_doc_fields.html").read_text(encoding="utf-8")
        assert 'name="enum_values"' not in html and "data-df-choice-row" in html

    def test_every_post_form_carries_the_csrf_token(self):
        html = (TEMPLATES / "admin_doc_fields.html").read_text(encoding="utf-8")
        assert html.count('method="post"') == html.count('name="csrf_token"')

    def test_audit_never_records_the_prefix_or_values(self):
        from web_interface import admin_doc_fields

        src = inspect.getsource(admin_doc_fields.attach_doc_field_routes)
        for block in re.findall(r"audit\.record\((.*?)\n        \)", src, re.S):
            assert "prefix_depth" in block or "pointer_prefix" not in block
            assert "values[" not in block and "values}" not in block
            assert "target_id=prefix" not in block


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class FakeRC:
    """A RemoteController that records what the console asked of it."""

    last = None

    def __init__(self, base_url, *, principal_broker=None, session=None, timeout=20.0):
        self.requeued = []
        self.discovered = []
        FakeRC.last = self

    def discover(self, root=None, max_depth=3):
        self.discovered.append(root)
        return {"root": root or "/data/corpus", "truncated": False,
                "entries": [{"type": "directory", "name": "Mandate", "path": "Mandate"}]}

    def status(self):
        return {"scheduler_state": "not_running"}

    def health(self):
        return self.status()

    def capabilities(self):
        return frozenset({"source_fields_v1", "fields_requeue_v1"})

    def requeue_doc_fields(self, outcome):
        self.requeued.append(outcome)
        return 3


class OldRC(FakeRC):
    """A RemoteController from before document fields."""

    requeue_doc_fields = None


class _Answer:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _OldServerSession:
    """An older RemoteController behind the real client: its /sync/status
    lists no capabilities, and it has no requeue route (404)."""

    def __init__(self):
        self.calls = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append((method, url.rsplit("/", 3)[-2:]))
        if url.endswith("/sync/status"):
            return _Answer(200, {"scheduler_state": "not_running"})
        return _Answer(404, {"error": "Not Found"})


class _DownSession(_OldServerSession):
    def request(self, method, url, json=None, headers=None, timeout=None):
        import requests

        self.calls.append((method, url.rsplit("/", 3)[-2:]))
        raise requests.ConnectionError("connection refused")


def _real_rc_on(monkeypatch, session_cls):
    """The real RemoteControllerClient (a new Platform), talking to
    ``session_cls`` (the RemoteController's side)."""
    import remote_controller_client as rcc

    real = rcc.RemoteControllerClient
    sessions = []

    class RealClient(real):
        def __init__(self, base_url, *, principal_broker=None, session=None, timeout=20.0):
            sessions.append(session_cls())
            super().__init__(base_url, principal_broker=principal_broker,
                             session=sessions[-1], timeout=timeout)

    monkeypatch.setattr(rcc, "RemoteControllerClient", RealClient)
    return sessions


def _token(client):
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def _post(client, path, **fields):
    fields.setdefault("csrf_token", _token(client))
    return client.post(path, data=fields)


def _calls(api, name):
    return [args for method, args in api.doc_calls if method == name]


def _profile(platform_db, owner, *, prefix="kanzlei", sources=("/data/corpus",),
             fields=None, templates=()):
    """Store a current ingestion profile as the JSON the Platform writes."""
    entries = []
    for i, path in enumerate(sources):
        entry = {"path": path, "recursive": True, "access_groups": []}
        if i == 0 and fields:
            entry["fields"] = dict(fields)
        if i == 0 and templates:
            entry["field_templates"] = list(templates)
        entries.append(entry)
    platform_db.execute(
        "INSERT INTO ingestion_profiles (name, version, profile, is_current, created_by) "
        "VALUES ('default', 1, %s, TRUE, %s)",
        (json.dumps({"identifier_prefix": prefix, "sources": entries}), str(owner.id)),
    )


@pytest.fixture
def rc(monkeypatch):
    import remote_controller_client

    FakeRC.last = None
    monkeypatch.setattr(remote_controller_client, "RemoteControllerClient", FakeRC)
    return FakeRC


@pytest.fixture
def admin(identity_repo):
    return _person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")


@pytest.fixture
def member(identity_repo):
    return _person(identity_repo, "mia@kanzlei.ch", "Mia", "member")


@pytest.fixture
def make_app(platform_db, tmp_path, monkeypatch, rc):
    def build(mode="values", **kw):
        return _identity_app(platform_db, tmp_path, monkeypatch,
                             client_cls=FakeDocFieldsApi.bind(mode, **kw))
    return build


def _signed_in(app, email):
    from _console import sign_in

    client = app.test_client()
    sign_in(client, email)
    return client


@pytest.fixture
def as_admin(make_app, admin):
    return _signed_in(make_app("values"), admin.email)


def test_an_untouched_choice_without_a_german_label_is_no_change():
    """platform-admin-ingestion-3, with the row editor: a choice labelled in
    French and Italian only shows in those columns; sent back unchanged it is
    no change -- and no in-use confirmation for a change nobody made."""
    from web_interface.admin_doc_fields import (
        changes_from_form,
        choice_rows,
        needs_use_confirmation,
    )

    current = {"key": "belegart", "datatype": "enum", "labels": {"de": "Belegart"},
               "display": False, "facet": False,
               "enum_values": [{"code": "rechnung", "labels": {"fr": "Facture", "it": "Fattura"}},
                               {"code": "offerte", "labels": {"de": "Offerte"}}]}
    shown = choice_rows(current["enum_values"])
    assert shown[0]["labels"] == {"de": "", "fr": "Facture", "it": "Fattura", "en": ""}
    form = {"flags": "1", "display": "1", "label_de": "Belegart",
            **_rows(*[(r["code"], r["labels"], r["aliases_text"]) for r in shown])}
    changes = changes_from_form(form, current)
    assert changes == {"display": True}
    assert not needs_use_confirmation(changes)
    assert changes_from_form(dict(form, display=""), current) == {}
    relabelled = changes_from_form(dict(form, choice_label_de_0="Rechnung"), current)
    assert relabelled["enum_values"][0]["labels"] == {"fr": "Facture", "it": "Fattura",
                                                      "de": "Rechnung"}
    assert relabelled["enum_values"][1] == {"code": "offerte", "labels": {"de": "Offerte"}}


@needs_db
class TestUnknownIsNotOff:
    """platform-admin-ingestion-6: an unclear probe hides the forms and
    writes nothing, but never claims the feature is not enabled."""

    @pytest.fixture
    def unknown(self, monkeypatch):
        monkeypatch.setattr(FakeDocFieldsApi, "doc_fields_probe", lambda self: "unknown")

    def test_the_page_says_not_determinable(self, make_app, admin, unknown):
        from web_interface.admin_doc_fields import OFF_TEXT, UNKNOWN_TEXT

        client = _signed_in(make_app("values"), admin.email)
        html = client.get("/admin/doc-fields").data.decode("utf-8")
        assert UNKNOWN_TEXT in html and OFF_TEXT not in html
        assert "doc-fields-settings" not in html

    def test_a_write_is_refused_without_blaming_knovas(self, make_app, admin, unknown):
        from web_interface.admin_doc_fields import OFF_TEXT, UNKNOWN_TEXT

        client = _signed_in(make_app("values"), admin.email)
        response = _post(client, "/admin/doc-fields/rules/save", pointer_prefix="kanzlei/a/",
                         rule_key_0="doc_type", rule_value_0="invoice")
        html = response.data.decode("utf-8")
        assert response.status_code == 409
        assert UNKNOWN_TEXT in html and OFF_TEXT not in html
        assert _calls(FakeDocFieldsApi.current, "put_doc_field_rule") == []

    def test_a_confirmed_off_still_says_off(self, make_app, admin):
        from web_interface.admin_doc_fields import OFF_TEXT, UNKNOWN_TEXT

        client = _signed_in(make_app("off"), admin.email)
        html = client.get("/admin/doc-fields").data.decode("utf-8")
        assert OFF_TEXT in html and UNKNOWN_TEXT not in html


@needs_db
class TestWhoMayReachIt:
    POSTS = ("/admin/doc-fields/create", "/admin/doc-fields/f1/update",
             "/admin/doc-fields/f1/deprecate", "/admin/doc-fields/packs/legal_ch/install",
             "/admin/doc-fields/settings", "/admin/doc-fields/rules/save",
             "/admin/doc-fields/rules/delete", "/admin/doc-fields/requeue")

    def test_anonymous_is_sent_to_login(self, make_app):
        client = make_app("values").test_client()
        response = client.get("/admin/doc-fields")
        assert response.status_code == 302 and "/login" in response.headers["Location"]

    def test_a_member_is_refused_every_route(self, make_app, member):
        client = _signed_in(make_app("values"), member.email)
        assert client.get("/admin/doc-fields").status_code == 403
        for path in self.POSTS:
            assert _post(client, path).status_code == 403, path
        assert FakeDocFieldsApi.current.doc_calls == []

    @pytest.mark.parametrize("path", POSTS)
    def test_a_post_without_csrf_writes_nothing(self, as_admin, path):
        api = FakeDocFieldsApi.current
        response = as_admin.post(path, data={"key": "kostenstelle", "datatype": "text",
                                             "rule_id": "x", "unknown_keys": "ignore",
                                             "date_order": "dmy", "pointer_prefix": "k/",
                                             "rule_key_0": "doc_type", "rule_value_0": "invoice"})
        assert response.status_code == 400
        writes = {"create_doc_field", "update_doc_field", "deprecate_doc_field",
                  "install_doc_field_pack", "set_doc_field_settings", "put_doc_field_rule",
                  "retire_doc_field_rule"}
        assert not [m for m, _ in api.doc_calls if m in writes]
        assert FakeRC.last.requeued == []


@needs_db
class TestFeatureOff:
    def test_the_page_says_so_and_offers_nothing(self, make_app, admin):
        client = _signed_in(make_app("off"), admin.email)
        html = client.get("/admin/doc-fields").data.decode("utf-8")
        assert "bei Knovas nicht freigeschaltet" in html
        assert "<form" not in html.split("</nav>", 1)[1]

    def test_the_tab_is_hidden(self, make_app, admin):
        client = _signed_in(make_app("off"), admin.email)
        html = client.get("/admin/people").data.decode("utf-8")
        assert 'href="/admin/doc-fields"' not in html
        assert "Administratorgruppe bei Knovas" not in html

    def test_a_write_is_refused_without_calling_knovas(self, make_app, admin):
        client = _signed_in(make_app("off"), admin.email)
        response = _post(client, "/admin/doc-fields/create", key="kostenstelle", datatype="text")
        assert response.status_code == 409
        assert _calls(FakeDocFieldsApi.current, "create_doc_field") == []

    def test_legacy_mode_never_probes(self, identity_app, admin, monkeypatch):
        from conftest import DummyKnovasClient

        def probe(self):
            raise AssertionError("legacy mode must not probe Knovas (D13)")

        monkeypatch.setattr(DummyKnovasClient, "doc_fields_probe", probe)
        client = _signed_in(identity_app, admin.email)
        assert 'href="/admin/doc-fields"' not in client.get("/admin/people").data.decode("utf-8")
        assert "bei Knovas nicht freigeschaltet" in client.get("/admin/doc-fields").data.decode("utf-8")

    def test_without_a_remote_controller_the_prefix_is_typed(self):
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        from web_interface import admin_doc_fields as adf

        html = env.get_template("admin_doc_fields.html").render(
            app_title="Knovas", company_name="Kanzlei", feedback_url=None,
            console_url="/admin/people", active_nav="admin", csrf_token="t", me=None,
            asset_version="1", enabled=True, error=None, notice=None, warnings=[],
            problems=[], writable=True, offer_requeue=False, requeue_label="",
            confirm={"update": None, "deprecate": None, "reject": False},
            rule_form={"folder_path": "", "pointer_prefix": "", "rows": []},
            rule_rows_count=adf.RULE_ROWS, capability="values",
            datatypes=[], date_roles=[], unknown_key_modes=[], date_orders=[],
            code_schemes=[], link_policies=[], fy_months=[], fy_labels=[],
            field_count_text="", choice_rows_new=adf.CHOICE_ROWS_NEW,
            choice_rows_extra=adf.CHOICE_ROWS_EXTRA,
            texts={"read_only": "", "in_use_deprecate": "", "in_use_update": "",
                   "reapply": "", "multi_source": adf.MULTI_SOURCE_CONFIRM},
            fields=[], registry=[], rule_fields=[], node_types=[], packs=[], settings={},
            unknown_profile_keys=[], rules=[], rules_state="ok", rules_note=None,
            rc_enabled=False, sources=[], multi_source=False, has_profile=False)
        assert 'name="pointer_prefix"' in html
        assert "data-df-folder-roots" not in html and 'name="folder_path"' not in html


@needs_db
class TestPage:
    def test_registry_packs_settings_and_rules_are_shown(self, as_admin):
        html = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        assert 'href="/admin/doc-fields"' in html and 'aria-current="page">Dokumentfelder' in html
        for text in ("doc_type", "Dokumentart", "Grundfelder", "Kanzlei (Schweiz)",
                     "Upload mit unbekanntem Schl\u00fcssel", "Ordnervorgaben",
                     "Noch keine Ordnervorgaben", "doc-field-create-form"):
            assert text in html, text
        assert "fieldset disabled" not in html

    def test_a_rules_403_turns_the_forms_read_only_and_says_why(self, make_app, admin):
        client = _signed_in(make_app("values"), admin.email)
        FakeDocFieldsApi.current.rules_denied = True
        html = client.get("/admin/doc-fields").data.decode("utf-8")
        from web_interface.admin_doc_fields import RULES_FORBIDDEN_TEXT

        assert "Nur Mitglieder der Knovas-Administratorgruppe" in html
        assert RULES_FORBIDDEN_TEXT.split(":")[0] in html
        assert "<fieldset disabled>" in html
        assert "Ordnervorgabe speichern" not in html


@needs_db
class TestRegistryWrites:
    def test_create_sends_the_definition_and_audits_key_and_type(self, as_admin, platform_db):
        from identity import audit

        response = _post(as_admin, "/admin/doc-fields/create", key="kostenstelle",
                         datatype="enum", cardinality="one", label_de="Kostenstelle",
                         sensitivity="normal", **_rows(("4100", {"de": "Verwaltung"}, "")))
        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "\u201ekostenstelle\u201c angelegt" in html
        sent = _calls(FakeDocFieldsApi.current, "create_doc_field")[0]["defn"]
        assert sent["enum_values"] == [{"code": "4100", "labels": {"de": "Verwaltung"}}]
        row = audit.recent(platform_db, action="doc_field.created")[0]
        assert row["target_type"] == "doc_field"
        assert row["detail"] == {"key": "kostenstelle", "datatype": "enum"}

    def test_the_requeue_offer_follows_a_registry_write(self, as_admin):
        html = _post(as_admin, "/admin/doc-fields/create", key="kostenstelle",
                     datatype="text").data.decode("utf-8")
        assert "Abgelehnte Uploads erneut senden" in html
        assert "Abgelehnte Uploads erneut senden" not in as_admin.get("/admin/doc-fields").data.decode("utf-8")

    def test_no_requeue_offer_for_a_remote_controller_without_it(
            self, platform_db, tmp_path, monkeypatch, admin):
        import remote_controller_client

        monkeypatch.setattr(remote_controller_client, "RemoteControllerClient", OldRC)
        app = _identity_app(platform_db, tmp_path, monkeypatch,
                            client_cls=FakeDocFieldsApi.bind("values"))
        client = _signed_in(app, admin.email)
        html = _post(client, "/admin/doc-fields/create", key="kostenstelle",
                     datatype="text").data.decode("utf-8")
        assert "angelegt" in html and "Abgelehnte Uploads erneut senden" not in html
        response = _post(client, "/admin/doc-fields/requeue")
        assert response.status_code == 409
        assert "RemoteController aktualisieren" in response.data.decode("utf-8")

    def test_an_older_remote_controller_server_is_not_offered_requeue(
            self, platform_db, tmp_path, monkeypatch, admin):
        """platform-admin-ingestion-4: the real client always has the method;
        the offer follows what the RemoteController advertises."""
        sessions = _real_rc_on(monkeypatch, _OldServerSession)
        app = _identity_app(platform_db, tmp_path, monkeypatch,
                            client_cls=FakeDocFieldsApi.bind("values"))
        client = _signed_in(app, admin.email)
        html = _post(client, "/admin/doc-fields/create", key="kostenstelle",
                     datatype="text").data.decode("utf-8")
        assert "angelegt" in html and "Abgelehnte Uploads erneut senden" not in html
        response = _post(client, "/admin/doc-fields/requeue")
        assert response.status_code == 409
        assert "RemoteController aktualisieren" in response.data.decode("utf-8")
        assert not [c for s in sessions for c in s.calls if c[0] == "POST"], "never asked"

    def test_an_unreachable_remote_controller_is_not_called_too_old(
            self, platform_db, tmp_path, monkeypatch, admin):
        sessions = _real_rc_on(monkeypatch, _DownSession)
        app = _identity_app(platform_db, tmp_path, monkeypatch,
                            client_cls=FakeDocFieldsApi.bind("values"))
        client = _signed_in(app, admin.email)
        html = _post(client, "/admin/doc-fields/create", key="kostenstelle",
                     datatype="text").data.decode("utf-8")
        assert "Abgelehnte Uploads erneut senden" not in html
        response = _post(client, "/admin/doc-fields/requeue")
        body = response.data.decode("utf-8")
        assert response.status_code == 502
        assert "nicht erreichbar" in body and "RemoteController aktualisieren" not in body
        assert not [c for s in sessions for c in s.calls if c[0] == "POST"]

    def test_the_full_clearance_refusal_is_explained(self, as_admin):
        FakeDocFieldsApi.current.registry_write_denied = True
        response = _post(as_admin, "/admin/doc-fields/create", key="kostenstelle",
                         datatype="text")
        assert response.status_code == 403
        assert "Nur Mitglieder der Knovas-Administratorgruppe" in response.data.decode("utf-8")

    @pytest.mark.parametrize("key, text", [
        ("vorname", "sieht nach Personendaten aus"),
        ("doc_type", "gibt es bereits"),
    ])
    def test_other_refusals_are_german(self, as_admin, key, text):
        response = _post(as_admin, "/admin/doc-fields/create", key=key, datatype="text")
        assert response.status_code in (409, 422)
        assert text in response.data.decode("utf-8")

    def test_update_sends_only_what_changed(self, as_admin):
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "doc_type")
        response = _post(as_admin, f"/admin/doc-fields/{field['id']}/update",
                         label_de="Dokumentart", label_fr="Type de document", label_it="",
                         label_en="doc type", aliases="", flags="1", display="1", facet="1",
                         sensitivity="normal",
                         **_rows(("contract", {"de": "Vertrag"}, ""),
                                 ("invoice", {"de": "Rechnung"}, ""),
                                 ("correspondence.email", {"de": "E-Mail"}, ""),
                                 ("report", {"de": "Bericht"}, ""),
                                 ("other", {"de": "Andere"}, "")))
        assert response.status_code == 200, response.data.decode("utf-8")[:500]
        sent = _calls(api, "update_doc_field")[0]
        assert sent["field_id"] == field["id"]
        assert sent["changes"] == {"labels": {"de": "Dokumentart", "en": "doc type",
                                              "fr": "Type de document"}}

    def test_an_unchanged_form_writes_nothing(self, as_admin):
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "amount")
        response = _post(as_admin, f"/admin/doc-fields/{field['id']}/update",
                         label_de="Betrag", label_en="amount")
        assert "Keine \u00c4nderung" in response.data.decode("utf-8")
        assert _calls(api, "update_doc_field") == []


@needs_db
class TestReadingSettingsOnThePage:
    def test_the_page_counts_the_fields_and_offers_the_settings(self, as_admin):
        html = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        assert "12 von 256 Feldern" in html
        create = html[html.index("doc-field-create-form"):]
        create = create[:create.index("</form>")]
        for name in ("code_scheme", "link_policy", "fy_start_month", "fy_label", "date_order"):
            assert f'name="{name}"' in create, name
        language = next(f for f in FakeDocFieldsApi.current.registry if f["key"] == "language")
        form = html[html.index(f'/admin/doc-fields/{language["id"]}/update'):]
        form = form[:form.index("</form>")]
        assert '<option value="generic" selected>' in form
        assert 'name="fy_start_month"' not in form

    def test_create_sends_the_settings(self, as_admin):
        _post(as_admin, "/admin/doc-fields/create", key="geschaeftsjahr", datatype="period",
              fy_start_month="7", fy_label="start", code_scheme="iban", date_order="mdy")
        sent = _calls(FakeDocFieldsApi.current, "create_doc_field")[0]["defn"]
        assert (sent["fy_start_month"], sent["fy_label"]) == (7, "start")
        assert "code_scheme" not in sent and "date_order" not in sent

    def test_a_locked_setting_is_explained(self, as_admin):
        api = FakeDocFieldsApi.current
        language = next(f for f in api.registry if f["key"] == "language")
        api.fail_call("update_doc_field", 409, "field_type_locked")
        response = _post(as_admin, f"/admin/doc-fields/{language['id']}/update",
                         code_scheme="bcp47")
        assert response.status_code == 409
        assert "Kennungsschema" in response.data.decode("utf-8")
        assert _calls(api, "update_doc_field")[0]["changes"] == {"code_scheme": "bcp47"}

    def test_the_confirmation_keeps_the_posted_setting(self, as_admin, platform_db, admin):
        _profile(platform_db, admin, fields={"language": "de-CH"})
        api = FakeDocFieldsApi.current
        language = next(f for f in api.registry if f["key"] == "language")
        refused = _post(as_admin, f"/admin/doc-fields/{language['id']}/update",
                        code_scheme="bcp47")
        assert refused.status_code == 409
        assert _calls(api, "update_doc_field") == []
        html = refused.data.decode("utf-8")
        form = html[html.index(f'/admin/doc-fields/{language["id"]}/update'):]
        form = form[:form.index("</form>")]
        assert '<option value="bcp47" selected>' in form

    def test_a_scheme_the_form_does_not_list_is_kept(self, as_admin):
        """The edit form shows the field's own scheme selected, so saving a
        label change sends the label only."""
        api = FakeDocFieldsApi.current
        reference = next(f for f in api.registry if f["key"] == "reference")
        reference["code_scheme"] = "legal_case_ch"
        html = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        form = html[html.index(f'/admin/doc-fields/{reference["id"]}/update'):]
        form = form[:form.index("</form>")]
        assert '<option value="legal_case_ch" selected>' in form
        assert '<option value="generic" selected>' not in form
        response = _post(as_admin, f"/admin/doc-fields/{reference['id']}/update",
                         label_de="Referenznummer", code_scheme="legal_case_ch")
        assert response.status_code == 200
        assert _calls(api, "update_doc_field")[0]["changes"] == {
            "labels": {"de": "Referenznummer", "en": "reference"}}


@needs_db
class TestChoiceEditor:
    def _form_of(self, html, field):
        form = html[html.index(f'/admin/doc-fields/{field["id"]}/update'):]
        return form[:form.index("</form>")]

    def test_the_rows_show_each_choice(self, as_admin):
        field = next(f for f in FakeDocFieldsApi.current.registry if f["key"] == "doc_type")
        form = self._form_of(as_admin.get("/admin/doc-fields").data.decode("utf-8"), field)
        assert 'name="choice_code_0" value="contract"' in form
        assert 'name="choice_label_de_0" value="Vertrag"' in form
        assert 'name="choice_code_5" value=""' in form  # five choices, then empty rows
        assert "data-df-choice-add" in form and 'name="enum_values"' not in form

    def test_labels_and_other_names_go_out(self, as_admin):
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "status")
        response = _post(as_admin, f"/admin/doc-fields/{field['id']}/update",
                         **_rows(("draft", {"de": "Entwurf", "fr": "Projet"}, "Vorlage"),
                                 ("final", {"de": "Final"}, ""),
                                 ("signed", {"de": "Unterzeichnet"}, "")))
        assert response.status_code == 200, response.data.decode("utf-8")[:500]
        assert _calls(api, "update_doc_field")[0]["changes"] == {"enum_values": [
            {"code": "draft", "labels": {"de": "Entwurf", "fr": "Projet"}, "aliases": ["Vorlage"]},
            {"code": "final", "labels": {"de": "Final"}},
            {"code": "signed", "labels": {"de": "Unterzeichnet"}}]}

    def test_the_confirmation_shows_the_posted_rows(self, as_admin, platform_db, admin):
        _profile(platform_db, admin, fields={"status": "draft"})
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "status")
        refused = _post(as_admin, f"/admin/doc-fields/{field['id']}/update",
                        **_rows(("draft", {"de": "Entwurf"}, ""), ("final", {"de": "Final"}, ""),
                                ("signed", {"de": "Unterzeichnet"}, ""),
                                ("archived", {"de": "Archiviert"}, "")))
        assert refused.status_code == 409
        assert _calls(api, "update_doc_field") == []
        form = self._form_of(refused.data.decode("utf-8"), field)
        assert 'name="choice_code_3" value="archived"' in form


@needs_db
class TestFieldsInUse:
    def test_deprecating_a_profile_key_needs_the_confirmation(self, as_admin, platform_db,
                                                             admin):
        from identity import audit
        from web_interface.admin_doc_fields import IN_USE_DEPRECATE

        _profile(platform_db, admin, fields={"doc_type": "invoice"})
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "doc_type")
        page = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        assert IN_USE_DEPRECATE in page
        refused = _post(as_admin, f"/admin/doc-fields/{field['id']}/deprecate")
        assert refused.status_code == 409
        assert IN_USE_DEPRECATE in refused.data.decode("utf-8")
        assert _calls(api, "deprecate_doc_field") == []
        done = _post(as_admin, f"/admin/doc-fields/{field['id']}/deprecate", confirm_in_use="1")
        assert done.status_code == 200
        assert _calls(api, "deprecate_doc_field") == [{"field_id": field["id"]}]
        row = audit.recent(platform_db, action="doc_field.deprecated")[0]
        assert row["detail"] == {"key": "doc_type", "datatype": "enum"}

    def test_a_key_the_profile_does_not_use_deprecates_directly(self, as_admin, platform_db,
                                                                admin):
        _profile(platform_db, admin, fields={"doc_type": "invoice"})
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "keywords")
        assert _post(as_admin, f"/admin/doc-fields/{field['id']}/deprecate").status_code == 200
        assert len(_calls(api, "deprecate_doc_field")) == 1

    def test_a_type_relevant_update_of_a_profile_key_needs_the_confirmation(
            self, as_admin, platform_db, admin):
        _profile(platform_db, admin, templates=("{mandant}/{jahr}",))
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "mandant")
        refused = _post(as_admin, f"/admin/doc-fields/{field['id']}/update",
                        sensitivity="special")
        assert refused.status_code == 409
        assert _calls(api, "update_doc_field") == []
        relabel = _post(as_admin, f"/admin/doc-fields/{field['id']}/update",
                        label_de="Mandantin")
        assert relabel.status_code == 200
        assert len(_calls(api, "update_doc_field")) == 1


    def test_the_in_use_confirmation_keeps_the_edit_open(self, as_admin, platform_db, admin):
        """platform-admin-ingestion-2: the 409 shows the posted edit again,
        in an opened form with the confirmation focused; confirming it then
        saves exactly that edit."""
        _profile(platform_db, admin, fields={"doc_type": "invoice"})
        api = FakeDocFieldsApi.current
        field = next(f for f in api.registry if f["key"] == "doc_type")
        posted = {"flags": "1", "label_de": "Belegart NEU", "sensitivity": "special",
                  "display": "1"}
        refused = _post(as_admin, f"/admin/doc-fields/{field['id']}/update", **posted)
        assert refused.status_code == 409
        assert _calls(api, "update_doc_field") == []
        html = refused.data.decode("utf-8")
        form = html[html.index(f'/admin/doc-fields/{field["id"]}/update') - 600:]
        form = form[:form.index("</form>")]
        assert '<details class="field-edit" open>' in form
        assert 'name="label_de" value="Belegart NEU"' in form
        assert '<option value="special" selected>' in form
        assert re.search(r'name="confirm_in_use" value="1"\s+autofocus', form)
        assert html.count('<details class="field-edit" open>') == 1, "only the edited field"
        done = _post(as_admin, f"/admin/doc-fields/{field['id']}/update", confirm_in_use="1",
                     **posted)
        assert done.status_code == 200
        changes = _calls(api, "update_doc_field")[0]["changes"]
        assert changes["sensitivity"] == "special"
        assert changes["labels"]["de"] == "Belegart NEU"


@needs_db
class TestPacksAndSettings:
    def test_a_pack_install_shows_skipped_fields_and_warnings(self, as_admin, platform_db):
        from identity import audit

        html = _post(as_admin, "/admin/doc-fields/packs/legal_ch/install").data.decode("utf-8")
        assert "10 Feld(er) angelegt, 0 \u00fcbersprungen" in html
        assert "\u201eGericht\u201c: kein passender Typ" in html
        row = audit.recent(platform_db, action="doc_field_pack.installed")[0]
        assert row["target_id"] == "legal_ch"
        assert row["detail"] == {"pack": "legal_ch", "installed_count": 10, "skipped_count": 0}
        again = _post(as_admin, "/admin/doc-fields/packs/core/install").data.decode("utf-8")
        assert "0 Feld(er) angelegt, 12 \u00fcbersprungen" in again

    def test_reject_warns_with_the_profile_keys_it_would_refuse(self, as_admin, platform_db,
                                                                admin):
        from identity import audit

        _profile(platform_db, admin, fields={"doc_type": "invoice", "kostenstelle": "4100"},
                 templates=("{mandant}/{jahr}",))
        page = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        assert "<code>jahr</code>, <code>kostenstelle</code>" in page
        api = FakeDocFieldsApi.current
        refused = _post(as_admin, "/admin/doc-fields/settings", unknown_keys="reject",
                        date_order="dmy")
        assert refused.status_code == 409
        assert "jahr, kostenstelle" in refused.data.decode("utf-8")
        assert _calls(api, "set_doc_field_settings") == []
        done = _post(as_admin, "/admin/doc-fields/settings", unknown_keys="reject",
                     date_order="dmy", confirm_reject="1")
        assert done.status_code == 200
        assert api.settings["unknown_keys"] == "reject"
        row = audit.recent(platform_db, action="doc_field_settings.changed")[0]
        assert row["detail"] == {"unknown_keys": "reject", "date_order": "dmy"}

    def test_the_reject_confirmation_keeps_the_choice(self, as_admin, platform_db, admin):
        """platform-admin-ingestion-1: the 409 shows the values the person
        chose, so ticking the box and saving stores "ablehnen" -- not the old
        settings under "Einstellungen gespeichert"."""
        _profile(platform_db, admin, fields={"kostenstelle": "4100"})
        api = FakeDocFieldsApi.current
        refused = _post(as_admin, "/admin/doc-fields/settings", unknown_keys="reject",
                        date_order="mdy")
        assert refused.status_code == 409
        html = refused.data.decode("utf-8")
        section = html[html.index("doc-fields-settings"):]
        section = section[:section.index("</form>")]
        assert '<option value="reject" selected>' in section
        assert '<option value="mdy" selected>' in section
        assert '<option value="ignore" selected>' not in section
        selected = dict(re.findall(r'<select name="(\w+)">.*?<option value="(\w+)" selected>',
                                   section, flags=re.DOTALL))
        done = _post(as_admin, "/admin/doc-fields/settings", confirm_reject="1", **selected)
        assert done.status_code == 200
        assert api.settings == {"unknown_keys": "reject", "date_order": "mdy"}

    def test_unread_settings_cannot_be_saved(self, as_admin):
        api = FakeDocFieldsApi.current
        api.fail_call("doc_field_settings", 503, "doc_fields_unavailable")
        html = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        section = html[html.index("doc-fields-settings"):]
        section = section[:section.index("</form>")]
        assert "Die Einstellungen sind derzeit nicht abrufbar." in html
        assert "<fieldset disabled>" in section.replace("<fieldset  disabled>", "<fieldset disabled>")

    def test_other_settings_need_no_confirmation(self, as_admin):
        response = _post(as_admin, "/admin/doc-fields/settings", unknown_keys="ignore",
                         date_order="mdy")
        assert response.status_code == 200
        assert FakeDocFieldsApi.current.settings["date_order"] == "mdy"


@needs_db
class TestFolderRules:
    SENTINEL = "Sentinelwert Muster AG"

    def test_a_typed_prefix_is_normalised_and_the_audit_holds_no_prefix_or_value(
            self, as_admin, platform_db, caplog):
        from identity import audit
        from web_interface.admin_doc_fields import REAPPLY_TEXT

        caplog.set_level(logging.DEBUG)
        response = _post(as_admin, "/admin/doc-fields/rules/save",
                         pointer_prefix="Sentinelordner\\Mandate",
                         rule_key_0="doc_type", rule_value_0="invoice",
                         rule_key_1="mandant", rule_value_1=self.SENTINEL)
        assert response.status_code == 200, response.data.decode("utf-8")[:800]
        html = response.data.decode("utf-8")
        assert REAPPLY_TEXT in html
        sent = _calls(FakeDocFieldsApi.current, "put_doc_field_rule")[0]
        assert sent == {"prefix": "Sentinelordner/Mandate/",
                        "values": {"doc_type": "invoice", "mandant": self.SENTINEL}}
        row = audit.recent(platform_db, action="doc_field_rule.saved")[0]
        rule = FakeDocFieldsApi.current.rules["Sentinelordner/Mandate/"]
        assert row["target_id"] == rule["id"]
        assert row["detail"] == {"prefix_depth": 2, "keys": ["doc_type", "mandant"]}
        assert "Sentinel" not in json.dumps(row, default=str)
        assert not [r for r in caplog.records if "Sentinel" in r.getMessage()]

    def test_a_folder_from_the_tree_becomes_its_pointer_prefix(self, as_admin, platform_db,
                                                               admin):
        _profile(platform_db, admin, prefix="kanzlei", sources=("/data/corpus",))
        page = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        assert "data-df-folder-roots" in page and "/admin/ingestion/folders" in page
        response = _post(as_admin, "/admin/doc-fields/rules/save",
                         folder_path="/data/corpus/Mandate/2024",
                         rule_key_0="doc_type", rule_value_0="contract")
        assert response.status_code == 200
        assert _calls(FakeDocFieldsApi.current, "put_doc_field_rule")[0]["prefix"] == \
            "kanzlei/Mandate/2024/"

    def test_a_folder_name_ending_in_a_space_keeps_it(self, as_admin, platform_db, admin):
        """platform-admin-ingestion-7: never stripped, so the rule matches the
        picked folder's pointers and not a sibling without the space."""
        _profile(platform_db, admin, prefix="kanzlei", sources=("/data/corpus",))
        response = _post(as_admin, "/admin/doc-fields/rules/save",
                         folder_path="/data/corpus/Muster AG ",
                         rule_key_0="doc_type", rule_value_0="contract")
        assert response.status_code == 200
        prefix = _calls(FakeDocFieldsApi.current, "put_doc_field_rule")[0]["prefix"]
        assert prefix == "kanzlei/Muster AG /"
        assert not "kanzlei/Muster AG/y.pdf".startswith(prefix)

    def test_a_folder_outside_every_source_is_refused(self, as_admin, platform_db, admin):
        _profile(platform_db, admin, sources=("/data/corpus",))
        response = _post(as_admin, "/admin/doc-fields/rules/save",
                         folder_path="/srv/elsewhere", rule_key_0="doc_type",
                         rule_value_0="contract")
        assert response.status_code == 400
        assert "in keiner Quelle" in response.data.decode("utf-8")
        assert _calls(FakeDocFieldsApi.current, "put_doc_field_rule") == []

    def test_several_sources_need_the_confirmation(self, as_admin, platform_db, admin):
        from web_interface.admin_doc_fields import MULTI_SOURCE_CONFIRM

        _profile(platform_db, admin, sources=("/data/a", "/data/b"))
        assert MULTI_SOURCE_CONFIRM in as_admin.get("/admin/doc-fields").data.decode("utf-8")
        refused = _post(as_admin, "/admin/doc-fields/rules/save", folder_path="/data/b/Mandate",
                        rule_key_0="doc_type", rule_value_0="contract")
        assert refused.status_code == 400
        assert MULTI_SOURCE_CONFIRM in refused.data.decode("utf-8")
        assert _calls(FakeDocFieldsApi.current, "put_doc_field_rule") == []
        done = _post(as_admin, "/admin/doc-fields/rules/save", folder_path="/data/b/Mandate",
                     rule_key_0="doc_type", rule_value_0="contract", confirm_all_sources="1")
        assert done.status_code == 200
        assert _calls(FakeDocFieldsApi.current, "put_doc_field_rule")[0]["prefix"] == \
            "kanzlei/Mandate/"

    def test_an_unknown_key_comes_back_with_suggestions(self, as_admin):
        api = FakeDocFieldsApi.current
        api.fail_call("put_doc_field_rule", 400, "unknown_field", path="set.mandat",
                      suggest=["mandant"])
        response = _post(as_admin, "/admin/doc-fields/rules/save", pointer_prefix="k/",
                         rule_key_0="doc_type", rule_value_0="contract")
        assert response.status_code == 400
        assert "Meinten Sie \u201eMandant\u201c?" in response.data.decode("utf-8")

    def test_rules_are_listed_with_labels_and_retired_by_id(self, as_admin, platform_db):
        from identity import audit
        from web_interface.admin_doc_fields import REAPPLY_TEXT

        api = FakeDocFieldsApi.current
        saved = api.put_doc_field_rule("Sentinelordner/a/", {"doc_type": "invoice"})
        api.doc_calls.clear()
        page = as_admin.get("/admin/doc-fields").data.decode("utf-8")
        assert "Sentinelordner/a/" in page and "Rechnung" in page
        response = _post(as_admin, "/admin/doc-fields/rules/delete", rule_id=saved["rule"]["id"])
        assert response.status_code == 200
        assert REAPPLY_TEXT in response.data.decode("utf-8")
        assert _calls(api, "retire_doc_field_rule") == [{"prefix": "Sentinelordner/a/"}]
        row = audit.recent(platform_db, action="doc_field_rule.retired")[0]
        assert row["target_id"] == saved["rule"]["id"]
        assert row["detail"] == {"prefix_depth": 2, "keys": ["doc_type"]}
        assert "Sentinel" not in json.dumps(row, default=str)

    def test_retiring_a_rule_that_is_gone_says_so(self, as_admin):
        response = _post(as_admin, "/admin/doc-fields/rules/delete", rule_id="nope")
        assert "keine aktive Vorgabe" in response.data.decode("utf-8")
        assert _calls(FakeDocFieldsApi.current, "retire_doc_field_rule") == []


@needs_db
class TestRequeue:
    def test_requeue_asks_the_remote_controller_for_refused_uploads(self, as_admin, platform_db):
        from identity import audit

        response = _post(as_admin, "/admin/doc-fields/requeue")
        assert response.status_code == 200
        assert FakeRC.last.requeued == ["refused"]
        assert "3 abgelehnte(r) Upload(s)" in response.data.decode("utf-8")
        # One audit shape for the same billed operation, whichever tab asked.
        row = audit.recent(platform_db, action="ingestion.doc_fields_requeued")[0]
        assert row["detail"] == {"outcome": "refused", "requeued": 3}
        assert (row["target_type"], row["target_id"]) == ("remote_controller", "sync")
        assert audit.recent(platform_db, action="doc_fields.requeue_requested") == []


@needs_db
class TestPeopleHint:
    def test_members_of_the_knovas_admin_group_get_the_badge(self, make_app, admin,
                                                            identity_repo):
        app = make_app("values")
        api = FakeDocFieldsApi.current
        api.access_groups = lambda: [{"group_id": "g-admin", "name": "Administration",
                                      "is_admin": True},
                                     {"group_id": "g-lit", "name": "Litigation"}]
        identity_repo.set_access_groups(admin.id, ["g-admin"])
        other = _person(identity_repo, "mia@kanzlei.ch", "Mia", "member")
        identity_repo.set_access_groups(other.id, ["g-lit"])
        html = _signed_in(app, admin.email).get("/admin/people").data.decode("utf-8")
        assert html.count('class="knovas-admin-badge"') == 1
