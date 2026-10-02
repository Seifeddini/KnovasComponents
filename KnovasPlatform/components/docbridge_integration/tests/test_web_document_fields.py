"""A document's values in the search page: registry view, name suggestions,
read and edit (spec 4.4, D6, D10, D12, H4, H6, H9).

Edits are the one place the search page writes to Knovas, so the order of
checks is what is pinned here: CSRF (app-wide header gate), identity, a role
in ``web.doc_fields.edit_roles`` (``admin`` for special fields), a live grant
-- the person's own search or listing returned the document -- and only then
the PATCH, sent once and non-strict. Knovas still decides; its 403 and 409
answers each have a defined outcome, and the audit row records keys and
versions, never a value and never the pointer.

Placeholder names only ("Muster AG", "Beispiel GmbH").
"""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("flask")

from conftest import platform_db_reachable  # noqa: E402
from doc_fields_fakes import FakeDocFieldsApi, legal_ch_fields  # noqa: E402
from test_web_search_doc_fields import (  # noqa: E402
    CONTRACT,
    INVOICE,
    MEMO,
    doc_fields_config,
    make_app,
    search,
    seed,
    signed_in,
)

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason="identity routes need a real PostgreSQL",
)

import doc_fields_view as dfv  # noqa: E402


@pytest.fixture
def values_app(platform_db, tmp_path, monkeypatch):
    app, api = make_app(platform_db, tmp_path, monkeypatch, "values")
    seed(api)
    for spec, _type in legal_ch_fields():
        api.registry.append(spec)
    return app, api


def read(client, doc_id):
    return client.post("/api/document-fields/read", json={"doc_id": doc_id})


def edit(client, doc_id, if_version, **ops):
    body = {"doc_id": doc_id, "if_version": if_version}
    body.update(ops)
    return client.post("/api/document-fields/edit", json=body)


def _calls(api, method):
    return [args for m, args in api.doc_calls if m == method]


def _audit_rows(platform_db):
    return platform_db.execute(
        "SELECT target_type, target_id, outcome, detail FROM audit_log "
        "WHERE action = 'document.values_edited' ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# GET /api/doc-fields
# ---------------------------------------------------------------------------

class TestRegistryView:
    def test_off_answers_without_asking_for_the_registry(self, platform_db, tmp_path,
                                                         monkeypatch, identity_repo):
        app, api = make_app(platform_db, tmp_path, monkeypatch, "off")
        client = signed_in(app, identity_repo, role="admin")
        body = client.get("/api/doc-fields").get_json()
        assert body == {"capability": "off", "fields": []}
        assert _calls(api, "doc_fields") == []

    def test_legacy_mode_is_off_without_any_request(self, identity_repo, identity_app):
        client = signed_in(identity_app, identity_repo, role="admin")
        assert client.get("/api/doc-fields").get_json() == {"capability": "off", "fields": []}

    def test_values_registry_and_what_an_admin_may_edit(self, values_app, identity_repo):
        app, api = values_app
        api.registry.append(dict(api.registry[0], key="old_key", status="deprecated"))
        client = signed_in(app, identity_repo, role="admin")
        body = client.get("/api/doc-fields").get_json()
        assert body["capability"] == "values"
        keys = [f["key"] for f in body["fields"]]
        assert "doc_type" in keys and "patient" in keys
        assert all("target_node_type_id" not in f for f in body["fields"])
        editable = body["editable_keys"]
        assert editable[:2] == ["title", "description"]
        assert "patient" in editable and "doc_type" in editable
        assert "old_key" not in editable, "deprecated fields no longer resolve for writes"

    def test_a_member_may_edit_nothing_by_default(self, values_app, identity_repo):
        app, api = values_app
        client = signed_in(app, identity_repo, role="member")
        assert client.get("/api/doc-fields").get_json()["editable_keys"] == []

    def test_configured_edit_roles_but_special_stays_admin(self, values_app, identity_repo):
        app, api = values_app
        doc_fields_config(edit_roles="admin,member")
        client = signed_in(app, identity_repo, role="member")
        editable = client.get("/api/doc-fields").get_json()["editable_keys"]
        assert "doc_type" in editable and "title" in editable
        assert "patient" not in editable

    def test_the_registry_is_cached_per_person(self, values_app, identity_repo):
        app, api = values_app
        first = signed_in(app, identity_repo, "erste@kanzlei.ch", role="member")
        second = signed_in(app, identity_repo, "zweite@kanzlei.ch", role="member")
        first.get("/api/doc-fields")
        first.get("/api/doc-fields")
        assert len(_calls(api, "doc_fields")) == 1
        second.get("/api/doc-fields")
        assert len(_calls(api, "doc_fields")) == 2

    def test_a_feature_off_answer_turns_it_off(self, values_app, identity_repo):
        from knovas_client import DocFieldsUnavailable

        app, api = values_app
        api.fail_call("doc_fields", DocFieldsUnavailable())
        client = signed_in(app, identity_repo, role="admin")
        body = client.get("/api/doc-fields").get_json()
        assert body["capability"] == "off" and body["fields"] == []


# ---------------------------------------------------------------------------
# POST /api/doc-fields/entities
# ---------------------------------------------------------------------------

class TestSuggestions:
    def test_names_only_filtered_here(self, values_app, identity_repo):
        app, api = values_app
        api.nodes["m3"] = {"id": "m3", "name": "Holding Muster GmbH", "node_type_id": "t-mandant"}
        client = signed_in(app, identity_repo, role="member")
        response = client.post("/api/doc-fields/entities", json={"field": "mandant", "q": "mus"})
        assert response.status_code == 200
        # Prefix first, then substring; names only, no ids.
        assert response.get_json() == {"items": [{"name": "Muster AG"},
                                                 {"name": "Holding Muster GmbH"}]}

    def test_the_typed_text_never_reaches_knovas(self, values_app, identity_repo):
        app, api = values_app
        client = signed_in(app, identity_repo, role="member")
        for typed in ("Bei", "Beispiel", "Sentinel-Prefix"):
            client.post("/api/doc-fields/entities", json={"field": "mandant", "q": typed})
        assert api.graph_nodes_calls, "names come from the node list"
        assert all(call["q"] is None for call in api.graph_nodes_calls)
        assert all(call["node_type_id"] == "t-mandant" for call in api.graph_nodes_calls)

    @pytest.mark.parametrize("field", ["patient", "party", "doc_type", "nope"])
    def test_special_untargeted_or_other_fields_get_none(self, values_app, identity_repo,
                                                         field):
        app, api = values_app
        client = signed_in(app, identity_repo, role="member")
        response = client.post("/api/doc-fields/entities", json={"field": field, "q": "Bei"})
        assert response.status_code == 400
        assert api.graph_nodes_calls == []

    def test_short_or_malformed_input(self, values_app, identity_repo):
        app, api = values_app
        client = signed_in(app, identity_repo, role="member")
        short = client.post("/api/doc-fields/entities", json={"field": "mandant", "q": "M"})
        assert short.get_json() == {"items": []}
        for body in ({"field": "mandant"}, {"field": "Mandant!", "q": "Mu"},
                     {"field": "mandant", "q": "x" * 201}):
            assert client.post("/api/doc-fields/entities", json=body).status_code == 400

    def test_post_with_csrf_only(self, values_app, identity_repo):
        app, api = values_app
        raw = signed_in(app, identity_repo, role="member", with_csrf=False)
        response = raw.post("/api/doc-fields/entities", json={"field": "mandant", "q": "Mu"})
        assert response.status_code == 403

    def test_off_is_refused(self, platform_db, tmp_path, monkeypatch, identity_repo):
        app, api = make_app(platform_db, tmp_path, monkeypatch, "off")
        client = signed_in(app, identity_repo, role="member")
        response = client.post("/api/doc-fields/entities", json={"field": "mandant", "q": "Mu"})
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/document-fields/read
# ---------------------------------------------------------------------------

class TestRead:
    def test_refused_without_csrf(self, values_app, identity_repo):
        app, api = values_app
        raw = signed_in(app, identity_repo, role="member", with_csrf=False)
        assert read(raw, INVOICE).status_code == 403
        assert _calls(api, "doc_values") == []

    def test_refused_without_a_grant(self, values_app, identity_repo):
        """Naming a pointer is not permission to read it: 404, never 403."""
        app, api = values_app
        client = signed_in(app, identity_repo, role="member")
        response = read(client, INVOICE)
        assert response.status_code == 404
        assert _calls(api, "doc_values") == []

    def test_after_search_the_panel_reads(self, values_app, identity_repo):
        app, api = values_app
        client = signed_in(app, identity_repo, role="admin")
        search(client)
        response = read(client, INVOICE)
        assert response.status_code == 200
        body = response.get_json()
        assert _calls(api, "doc_values") == [{"pointer": INVOICE}]
        assert body["pointer"] == INVOICE and body["held"] is False
        assert body["title"] == "Rechnung Muster AG GJ 2024"
        assert body["title_note"] == dfv.TITLE_NOT_SEARCHABLE
        rows = {r["key"]: r for r in body["fields"]}
        assert rows["doc_type"]["text"] == "Rechnung"
        assert rows["doc_type"]["edit_value"] == "invoice"
        assert rows["doc_type"]["layer"] == "upload"
        assert rows["doc_type"]["layer_label"] == "Upload"
        assert rows["document_date"]["text"] == "15.03.2024"
        assert rows["mandant"]["text"] == "Muster AG"
        assert "changed_at" not in rows["doc_type"]
        assert "doc_type" in body["editable_keys"]

    def test_a_held_document_shows_nothing_and_is_read_only(self, values_app, identity_repo):
        app, api = values_app
        api.add_document("rc-sync/Gehalten.pdf", fields={"doc_type": "report"}, held=True)
        client = signed_in(app, identity_repo, role="admin")
        search(client)
        body = read(client, "rc-sync/Gehalten.pdf").get_json()
        assert body["held"] is True and body["fields"] == [] and body["editable_keys"] == []
        assert "zur\u00fcckgehalten" in body["held_text"]

    def test_a_hidden_value_keeps_its_field_read_only(self, values_app, identity_repo):
        """Saving the visible rest would drop a value the person cannot see."""
        app, api = values_app
        api.add_document("rc-sync/Verborgen.pdf",
                         fields={"party": [{"name": "Muster AG"}, {"hidden": True}]})
        client = signed_in(app, identity_repo, role="admin")
        search(client)
        body = read(client, "rc-sync/Verborgen.pdf").get_json()
        row = next(r for r in body["fields"] if r["key"] == "party")
        assert row["text"] == "Muster AG; verborgen" and row["has_hidden"] is True
        assert "party" not in body["editable_keys"]

    def test_privileged_is_a_flag_not_a_restriction(self, values_app, identity_repo):
        app, api = values_app
        api.add_document("rc-sync/Akte/Brief.pdf", fields={"privileged": True})
        client = signed_in(app, identity_repo, role="admin")
        search(client)
        row = read(client, "rc-sync/Akte/Brief.pdf").get_json()["fields"][0]
        assert row["text"] == "Ja" and row["note"] == dfv.PRIVILEGED_HINT

    def test_a_mount_relative_spelling_is_not_guessed(self, values_app, identity_repo,
                                                      monkeypatch):
        """Search grants both spellings for the file routes; the panel sends
        the doc_id verbatim, and Knovas does not know the short one."""
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "rc-sync")
        app, api = values_app
        client = signed_in(app, identity_repo, role="admin")
        search(client)
        response = read(client, "Muster AG/GJ 2024/Rechnung_17.pdf")
        assert response.status_code == 404
        assert _calls(api, "doc_values") == [{"pointer": "Muster AG/GJ 2024/Rechnung_17.pdf"}]
        assert edit(client, "Muster AG/GJ 2024/Rechnung_17.pdf", 1,
                    set={"doc_type": "contract"}).status_code == 404

    def test_a_server_without_s2_says_update(self, values_app, identity_repo):
        app, api = values_app
        api.fail_call("doc_values", 400, "invalid_value", path="pointer")
        client = signed_in(app, identity_repo, role="admin")
        search(client)
        response = read(client, INVOICE)
        assert response.status_code == 502
        body = response.get_json()
        assert body["error_code"] == "knovas_update_needed"
        assert "Knovas-Update n\u00f6tig" in body["error"]
        assert len(_calls(api, "doc_values")) == 1, "no fallback to the query string"

    def test_unknown_at_knovas_is_404(self, values_app, identity_repo):
        app, api = values_app
        client = signed_in(app, identity_repo, role="admin")
        search(client)
        api.denied_pointers.add(MEMO)
        assert read(client, MEMO).status_code == 404

    def test_legacy_login_reads_without_grants(self, tmp_path, monkeypatch):
        """Identity off: no subject to hold a grant, so the check stands aside
        exactly as the file-route gate does; editing needs identity."""
        app, api = _legacy_app(tmp_path, monkeypatch)
        client, token = _legacy_login(app)
        headers = {"X-CSRF-Token": token}
        response = client.post("/api/document-fields/read", json={"doc_id": INVOICE},
                               headers=headers)
        assert response.status_code == 200
        refused = client.post("/api/document-fields/edit", headers=headers,
                              json={"doc_id": INVOICE, "if_version": 1,
                                    "set": {"doc_type": "contract"}})
        assert refused.status_code == 403
        assert refused.get_json()["error_code"] == "edit_not_allowed"
        assert _calls(api, "patch_doc_values") == []


def _legacy_app(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-doc-fields-legacy")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        'web:\n'
        '  secret_key: "${WEB_SECRET_KEY}"\n'
        '  session_lifetime: 3600\n'
        '  login:\n'
        '    enabled: true\n'
        '    company_name: "TestCo"\n'
        '    username: "office"\n'
        '    password: "s3cret-office-login"\n'
        'identity:\n'
        '  enabled: false\n'
        'api:\n'
        '  base_url: "http://example.test"\n'
        'open:\n'
        '  companion_enabled: false\n'
        f'  grant_store_path: "{(tmp_path / "grants.sqlite3").as_posix()}"\n',
        encoding="utf-8",
    )
    from conftest import DummyFileHandler
    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", FakeDocFieldsApi.bind("values"))
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)
    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    api = FakeDocFieldsApi.current
    seed(api)
    return flask_app, api


def _legacy_login(app):
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as sess:
        login_csrf = sess["csrf_token"]
    client.post("/login", data={"login_name": "office", "password": "s3cret-office-login",
                                "csrf_token": login_csrf})
    with client.session_transaction() as sess:
        return client, sess["csrf_token"]


# ---------------------------------------------------------------------------
# POST /api/document-fields/edit
# ---------------------------------------------------------------------------

@pytest.fixture
def admin(values_app, identity_repo):
    app, api = values_app
    client = signed_in(app, identity_repo, "chef@kanzlei.ch", role="admin")
    search(client)
    return client, api


class TestWhoMayEdit:
    def test_refused_without_csrf(self, values_app, identity_repo):
        app, api = values_app
        client = signed_in(app, identity_repo, role="admin", with_csrf=False)
        response = client.post("/api/document-fields/edit",
                               json={"doc_id": INVOICE, "if_version": 1,
                                     "set": {"doc_type": "contract"}})
        assert response.status_code == 403
        assert _calls(api, "patch_doc_values") == []

    def test_a_member_is_read_only_by_default(self, values_app, identity_repo):
        app, api = values_app
        client = signed_in(app, identity_repo, role="member")
        search(client)
        response = edit(client, INVOICE, 1, set={"doc_type": "contract"})
        assert response.status_code == 403
        assert response.get_json()["error_code"] == "edit_not_allowed"
        assert _calls(api, "patch_doc_values") == []

    def test_special_fields_need_admin_even_with_an_edit_role(self, values_app,
                                                              identity_repo):
        app, api = values_app
        doc_fields_config(edit_roles="admin,member")
        client = signed_in(app, identity_repo, role="member")
        search(client)
        refused = edit(client, INVOICE, 1, set={"patient": "Beispiel Patient"})
        assert refused.status_code == 403 and refused.get_json()["field"] == "patient"
        assert _calls(api, "patch_doc_values") == []
        ok = edit(client, INVOICE, 1, set={"doc_type": "contract"})
        assert ok.status_code == 200

    def test_no_grant_no_edit(self, values_app, identity_repo):
        app, api = values_app
        client = signed_in(app, identity_repo, role="admin")
        response = edit(client, INVOICE, 1, set={"doc_type": "contract"})
        assert response.status_code == 404
        assert _calls(api, "patch_doc_values") == []

    @pytest.mark.parametrize("body", [
        {"if_version": 1},
        {"if_version": -1, "set": {"doc_type": "contract"}},
        {"if_version": True, "set": {"doc_type": "contract"}},
        {"if_version": "1", "set": {"doc_type": "contract"}},
        {"if_version": 1, "set": {"path": "x"}},
        {"if_version": 1, "set": {"Doc Type": "x"}},
        {"if_version": 1, "set": []},
        {"if_version": 1, "unset": ["title"]},
        {"if_version": 1, "add": {"title": ["x"]}},
        {"if_version": 1, "add": {"party": "Muster AG"}},
        {"if_version": 1, "set": {"party": [{"nested": {"deep": 1}}]}},
        {"if_version": 1, "set": {"party": ["x"] * 33}},
    ])
    def test_malformed_edits_never_leave_the_platform(self, admin, body):
        client, api = admin
        response = client.post("/api/document-fields/edit", json={"doc_id": INVOICE, **body})
        assert response.status_code == 400
        assert _calls(api, "patch_doc_values") == []

    def test_an_unknown_key_is_refused_here(self, admin):
        client, api = admin
        response = edit(client, INVOICE, 1, set={"no_such_field": "x"})
        assert response.status_code == 400
        assert response.get_json()["code"] == "unknown_field"
        assert _calls(api, "patch_doc_values") == []


class TestTheEdit:
    def test_non_strict_once_with_an_opaque_actor(self, admin, identity_repo):
        client, api = admin
        response = edit(client, CONTRACT, 1, set={"counterparty": "Beispiel GmbH"})
        assert response.status_code == 200
        body = response.get_json()
        patch = _calls(api, "patch_doc_values")
        assert len(patch) == 1
        assert patch[0]["fields_strict"] is False
        assert patch[0]["if_version"] == 1
        user = identity_repo.get_by_email("chef@kanzlei.ch")
        assert patch[0]["actor_ref"] == f"platform-user:{user.id}"
        # "nicht verknuepft" is information, not an error.
        assert body["warnings"] == [{"key": "counterparty", "label": "Gegenpartei",
                                     "code": "unresolved_entity",
                                     "text": "nicht verkn\u00fcpft"}]
        rows = {r["key"]: r for r in body["document"]["fields"]}
        assert rows["counterparty"]["text"] == "Beispiel GmbH"
        assert rows["counterparty"]["layer_label"] == "Manuell"
        assert rows["counterparty"]["changed_at"]
        assert body["document"]["version"] == 2

    def test_reads_again_after_a_title_only_edit(self, admin):
        """A PATCH that names no typed key answers fields: {} -- not "cleared"."""
        client, api = admin
        response = edit(client, INVOICE, 1, set={"title": "Rechnung 17"})
        body = response.get_json()
        assert response.status_code == 200
        methods = [m for m, _ in api.doc_calls]
        assert methods[-2:] == ["patch_doc_values", "doc_values"]
        assert body["document"]["title"] == "Rechnung 17"
        assert {r["key"] for r in body["document"]["fields"]} >= {"doc_type", "mandant"}

    def test_version_conflict_reloads_and_never_overwrites(self, admin, platform_db):
        client, api = admin
        response = edit(client, INVOICE, 0, set={"doc_type": "contract"})
        assert response.status_code == 409
        body = response.get_json()
        assert body["error_code"] == "version_conflict" and body["current_version"] == 1
        assert body["document"]["version"] == 1
        assert len(_calls(api, "patch_doc_values")) == 1, "not retried with the new version"
        # audit_log.outcome allows ok/denied/error: a conflict is "denied"
        # with its reason.
        row = _audit_rows(platform_db)[-1]
        assert row[2] == "denied" and row[3]["reason"] == "version_conflict"

    def test_change_not_authorized_turns_read_only(self, admin, platform_db):
        client, api = admin
        api.change_denied.add(INVOICE)
        response = edit(client, INVOICE, 1, set={"doc_type": "contract"})
        assert response.status_code == 403
        assert response.get_json()["read_only"] is True
        row = _audit_rows(platform_db)[-1]
        assert row[2] == "denied" and row[3]["reason"] == "change_not_authorized"

    def test_held_values_are_said_to_be_held(self, admin):
        client, api = admin
        api.docs[INVOICE]["held"] = True
        response = edit(client, INVOICE, 1, set={"doc_type": "contract"})
        assert response.status_code == 409
        body = response.get_json()
        assert body["error_code"] == "anchor_quarantined"
        assert "Werte zur\u00fcckgehalten" in body["error"]

    def test_a_value_knovas_refuses_is_shown_at_its_field(self, admin):
        client, api = admin
        response = edit(client, INVOICE, 1, set={"doc_type": "Kreditorenrechnung"})
        assert response.status_code == 400
        body = response.get_json()
        assert body["field"] == "doc_type" and body["field_label"] == "Dokumentart"
        assert "Kreditorenrechnung" not in body["error"]
        assert api.docs[INVOICE]["version"] == 1, "nothing saved"

    def test_feature_off_mid_session(self, admin):
        from knovas_client import DocFieldsUnavailable

        client, api = admin
        api.fail_call("patch_doc_values", DocFieldsUnavailable())
        response = edit(client, INVOICE, 1, set={"doc_type": "contract"})
        assert response.status_code == 409
        assert response.get_json()["error_code"] == "doc_fields_off"


class TestAuditAndLogs:
    def test_the_audit_row_holds_keys_and_versions_only(self, admin, platform_db):
        client, api = admin
        edit(client, INVOICE, 1, set={"counterparty": "Sentinel-Gegenpartei AG",
                                      "title": "Sentinel-Titel"},
             add={"party": ["Sentinel-Partei"]})
        target_type, target_id, outcome, detail = _audit_rows(platform_db)[-1]
        assert (target_type, outcome) == ("document", "ok")
        assert target_id == api.docs[INVOICE]["document_uuid"]
        detail = detail if isinstance(detail, dict) else json.loads(detail)
        assert detail == {
            "keys": ["counterparty", "party"],
            "ops": {"set": 2, "unset": 0, "add": 1, "remove": 0},
            "title_changed": True, "description_changed": False,
            "version_from": 1, "version_to": 2,
            "warning_codes": ["unresolved_entity"],
        }
        dumped = json.dumps([target_id, detail])
        assert "Sentinel" not in dumped and "rc-sync" not in dumped

    def test_no_value_or_pointer_in_a_log_line(self, values_app, identity_repo, caplog):
        app, api = values_app
        pointer = "rc-sync/Sentinel-Mandant/Sentinel-Akte.pdf"
        api.add_document(pointer, title="Sentinel-Titel", fields={"doc_type": "contract"})
        client = signed_in(app, identity_repo, role="admin")
        search(client)
        caplog.set_level(logging.DEBUG)
        read(client, pointer)
        edit(client, pointer, 1, set={"counterparty": "Sentinel-Name AG"})
        edit(client, pointer, 0, set={"counterparty": "Sentinel-Name AG"})
        edit(client, pointer, 2, set={"doc_type": "Sentinel-Wert"})
        client.post("/api/doc-fields/entities", json={"field": "mandant", "q": "Sentinel-Tipp"})
        text = "\n".join(r.getMessage() for r in caplog.records)
        for needle in ("Sentinel-Mandant", "Sentinel-Akte", "Sentinel-Titel", "Sentinel-Name",
                       "Sentinel-Wert", "Sentinel-Tipp"):
            assert needle not in text, needle


# ---------------------------------------------------------------------------
# Cortex: "Dokumente mit <Feld> = <Name>" (spec 4.5, stretch)
# ---------------------------------------------------------------------------

class TestCortexLink:
    def _app(self, platform_db, tmp_path, monkeypatch, mode):
        from conftest import _identity_app

        monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")

        class CortexFake(FakeDocFieldsApi.bind(mode)):
            def graph_export(self):
                return {"node_types": list(self.node_types),
                        "nodes": list(self.nodes.values()), "edges": []}

            def graph_edges(self):
                return []

        app = _identity_app(platform_db, tmp_path, monkeypatch, client_cls=CortexFake)
        return app, FakeDocFieldsApi.current

    def test_links_for_fields_targeting_the_node_type(self, platform_db, tmp_path,
                                                      monkeypatch, identity_repo):
        app, api = self._app(platform_db, tmp_path, monkeypatch, "filters")
        client = signed_in(app, identity_repo, role="member")
        body = client.get("/api/ontology/entities/m1").get_json()
        assert body["entity"]["label"] == "Muster AG"
        assert body["doc_field_links"] == [{"key": "mandant", "label": "Mandant"}]
        # The target node type id never reaches the browser.
        assert "t-mandant" not in json.dumps(body["doc_field_links"])

    def test_a_node_no_field_points_at(self, platform_db, tmp_path, monkeypatch,
                                       identity_repo):
        app, api = self._app(platform_db, tmp_path, monkeypatch, "listing_only")
        client = signed_in(app, identity_repo, role="member")
        assert client.get("/api/ontology/entities/n1").get_json()["doc_field_links"] == []

    @pytest.mark.parametrize("mode", ["values", "off"])
    def test_no_key_without_the_listing(self, platform_db, tmp_path, monkeypatch,
                                        identity_repo, mode):
        app, api = self._app(platform_db, tmp_path, monkeypatch, mode)
        client = signed_in(app, identity_repo, role="member")
        body = client.get("/api/ontology/entities/m1").get_json()
        assert body["success"] is True and "doc_field_links" not in body
