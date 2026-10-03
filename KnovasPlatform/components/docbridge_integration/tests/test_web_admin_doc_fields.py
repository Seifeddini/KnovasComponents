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


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestDefinitionFromForm:
    def test_an_enum_field_with_labels_and_codes(self):
        from web_interface.admin_doc_fields import definition_from_form

        defn = definition_from_form({
            "key": "kostenstelle", "datatype": "enum", "cardinality": "one",
            "label_de": "Kostenstelle", "label_fr": "Centre de co\u00fbts", "label_it": "",
            "aliases": "kst, kostenstelle-nr, kst", "sensitivity": "normal",
            "enum_values": "4100 = Verwaltung\n\n4200\n", "display": "1",
        })
        assert defn == {
            "key": "kostenstelle", "datatype": "enum", "cardinality": "one",
            "labels": {"de": "Kostenstelle", "fr": "Centre de co\u00fbts"},
            "aliases": ["kst", "kostenstelle-nr"],
            "enum_values": [{"code": "4100", "labels": {"de": "Verwaltung"}}, "4200"],
            "display": True, "facet": False, "sensitivity": "normal",
        }

    def test_type_specific_inputs_go_only_with_their_type(self):
        from web_interface.admin_doc_fields import definition_from_form

        form = {"key": "mandat", "datatype": "text", "target_node_type_id": "t1",
                "date_role": "due", "enum_values": "a"}
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
        {"key": "ok_key", "datatype": "enum", "enum_values": ""},
        {"key": "ok_key", "datatype": "enum", "enum_values": "a b = x"},
        {"key": "ok_key", "datatype": "enum", "enum_values": "a\na"},
        {"key": "ok_key", "datatype": "date", "date_role": "tomorrow"},
    ])
    def test_refused_before_knovas(self, form):
        from web_interface.admin_doc_fields import FormError, definition_from_form

        with pytest.raises(FormError):
            definition_from_form(form)

    def test_an_enum_error_names_the_line_not_the_value(self):
        from web_interface.admin_doc_fields import FormError, parse_enum_lines

        with pytest.raises(FormError) as caught:
            parse_enum_lines("ok\nMuster AG")
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

    def _form(self, **over):
        form = {"label_de": "Dokumentart", "label_fr": "", "label_it": "",
                "label_en": "Document type", "aliases": "art", "flags": "1",
                "display": "1", "facet": "1", "sensitivity": "normal",
                "enum_values": "invoice = Rechnung\nother"}
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

    def test_an_enum_relabel_keeps_other_languages_and_aliases(self):
        from web_interface.admin_doc_fields import changes_from_form

        changes = changes_from_form(
            self._form(enum_values="invoice = Kreditorenbeleg\nother\noffer = Offerte"),
            self.CURRENT)
        assert changes["enum_values"] == [
            {"code": "invoice", "labels": {"de": "Kreditorenbeleg", "fr": "Facture"},
             "aliases": ["rg"]},
            "other",
            {"code": "offer", "labels": {"de": "Offerte"}},
        ]

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
        assert by["doc_type"]["enum_text"].startswith("contract = Vertrag")
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


def test_an_untouched_enum_without_a_german_label_is_no_change():
    """platform-admin-ingestion-3: the textarea shows another language's
    label for a code without a German one; sent back unchanged it is no
    change -- not a German label copied from French, and no in-use
    confirmation for a change nobody made."""
    from web_interface.admin_doc_fields import (
        changes_from_form,
        enum_lines,
        needs_use_confirmation,
    )

    current = {"key": "belegart", "datatype": "enum", "labels": {"de": "Belegart"},
               "display": False, "facet": False,
               "enum_values": [{"code": "rechnung", "labels": {"fr": "Facture", "it": "Fattura"}},
                               {"code": "offerte", "labels": {"de": "Offerte"}}]}
    text = enum_lines(current["enum_values"])
    assert text == "rechnung = Facture\nofferte = Offerte"
    form = {"flags": "1", "display": "1", "label_de": "Belegart", "enum_values": text}
    changes = changes_from_form(form, current)
    assert changes == {"display": True}
    assert not needs_use_confirmation(changes)
    assert changes_from_form(dict(form, display=""), current) == {}
    relabelled = changes_from_form(
        dict(form, enum_values="rechnung = Rechnung\nofferte = Offerte"), current)
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
        # Knovas 1.5.0 has document fields on for every account: "off" means
        # Knovas switched them off, or the server predates them (the System
        # tab's hint), not a step still to come.
        assert ("Knovas hat Dokumentfelder f\u00fcr diesen Mandanten ausgeschaltet, oder der "
                "Knovas-Server kennt sie noch nicht. Suche und Ingestion laufen wie bisher."
                ) in html


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
                         enum_values="4100 = Verwaltung", sensitivity="normal")
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
        assert "bitte den Knovas Connector aktualisieren" in response.data.decode("utf-8")

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
        assert "bitte den Knovas Connector aktualisieren" in response.data.decode("utf-8")
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
        assert "nicht erreichbar" in body and "Knovas Connector aktualisieren" not in body
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
                         enum_values="contract = Vertrag\ninvoice = Rechnung\n"
                                     "correspondence.email = E-Mail\nreport = Bericht\nother = Andere")
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
