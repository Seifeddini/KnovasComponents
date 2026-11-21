"""The dossier's page (PMVP-30a, plan B-02; L2-PLATFORM-BRAIN-VIEWS PRE-01, POST-03): who gets it, and what it reads.

1. Signed out: sent to sign-in. Switched off: 404, and the Core is not asked. Without accounts: 404.
2. A member opens one node: its name, its kind, the tabs. A node the person may not see answers exactly as a node
   that does not exist.
3. One starting point: one node id in the address, nothing else; a malformed id is not sent to the Core.
4. Every read is a per-thing read made on the person's request through the web app's one client (whose broker signs
   it as that person); a whole-tenant read fails the test.
5. A failed read is one plain message; a graph switched off in the Core is a 404.

The Core is the shared FakeGraphApi with the per-thing answers the dossier reads put on it here (`brain`); the
other dossier test file uses the same fixture.
"""
from __future__ import annotations

import copy

import pytest

pytest.importorskip("flask")

NODE = "6f1c2c1e-0000-4000-8000-000000000001"
HIDDEN = "6f1c2c1e-0000-4000-8000-000000000002"
MISSING = "6f1c2c1e-0000-4000-8000-000000000003"
TYPE = "7a2b0000-0000-4000-8000-0000000000aa"
MIETVERTRAG = "corpus/Mandanten/Limmat/Mietvertrag Seefeldstrasse 48.pdf"
MEMO = "corpus/Mandanten/Limmat/Memo Kuendigung.docx"
FOREIGN = "corpus/Mandanten/Wand/Vergleich Hardturm.pdf"   # a fact's source the member was not handed

WHOLE_TENANT = ("graph_export", "graph_edges", "graph_nodes", "graph_node_types")


def _fact(fact_id, value, attribute_id=None, label=None, pointer=None, chunk=None, status="ok", curation="extracted"):
    return {"id": fact_id, "node_id": NODE, "attribute_id": attribute_id, "label": label, "value": value,
            "provenance_pointer": pointer, "provenance_chunk_id": chunk, "provenance_status": status,
            "curation_status": curation, "confidence": None}


class Brain:
    """The Core's per-thing answers, as graph_api.py gives them (flat envelope), on the app's one client.

    ``calls`` records every read with the person signed in when it was made, read from the broker the web app
    attached to the client (the one that signs each call). Nodes in ``hidden`` answer 404 for everyone but
    ``seen_by``, as the Core answers a node outside the person's access.
    """

    def __init__(self, fake, monkeypatch):
        self.fake = fake
        self.calls: list[tuple] = []
        self.hidden = {HIDDEN}
        self.seen_by = {"chef@kanzlei.ch"}
        self.node_type = {"id": TYPE, "name": "Mandant"}
        self.attributes = [
            {"id": "a-branche", "name": "Branche", "datatype": "text", "sort_order": 1},
            {"id": "a-kontakt", "name": "Kontakt", "datatype": "text", "sort_order": 2},
            {"id": "a-vertrag", "name": "Erster Vertrag", "datatype": "text", "sort_order": 3},
            {"id": "a-partei", "name": "Gegenpartei", "datatype": "entity_ref", "sort_order": 4},
        ]
        self.nodes = {
            NODE: {
                "node": {"id": NODE, "name": "Limmat Immobilien AG", "node_type_id": TYPE,
                         "description": "Zürich · Mandant seit 2018"},
                "assignments": [
                    {"id": "as1", "node_id": NODE, "pointer": MIETVERTRAG, "chunk_id": None, "status": "active",
                     "rank": 0.9},
                    {"id": "as2", "node_id": NODE, "pointer": MIETVERTRAG, "chunk_id": "c-7", "status": "active",
                     "rank": 0.4},
                    {"id": "as3", "node_id": NODE, "pointer": MEMO, "chunk_id": None, "status": "active", "rank": 0.5},
                ],
                "sections": [],
                "facts": [
                    _fact("f-branche", "Immobilienverwaltung", "a-branche", pointer=MIETVERTRAG, chunk="c-12"),
                    _fact("f-kontakt", "S. Vogt, CFO", "a-kontakt", curation="manual"),
                    _fact("f-vertrag", "Mietvertrag Seefeldstrasse 48, 2019", "a-vertrag", pointer=FOREIGN,
                          chunk="c-99"),
                    _fact("f-alt", "Verwaltung von Gewerbeflächen", "a-branche", curation="rejected"),
                ],
            },
            HIDDEN: {
                "node": {"id": HIDDEN, "name": "Vergleich Hardturm", "node_type_id": TYPE},
                "assignments": [], "sections": [], "facts": [],
            },
        }
        monkeypatch.setattr(fake, "graph_node", self.graph_node, raising=False)
        monkeypatch.setattr(fake, "graph_get_many", self.graph_get_many, raising=False)
        for name in WHOLE_TENANT:
            monkeypatch.setattr(fake, name, self._refused(name), raising=False)

    def person(self):
        user = self.fake.principal_broker.current_user()
        return None if user is None else user.email

    def graph_node(self, node_id):
        who = self.person()
        self.calls.append(("graph_node", node_id, who))
        if node_id in self.hidden and who not in self.seen_by:
            return None
        found = self.nodes.get(node_id)
        return None if found is None else {"status": "success", "message": "Node detail", **copy.deepcopy(found)}

    def graph_get_many(self, calls, *, workers=4, acquire=None, max_retries=3):
        who = self.person()
        answers = []
        for path, params in calls:
            self.calls.append(("graph_get_many", path, who))
            if path == f"/node-types/{TYPE}":
                answers.append((200, {"status": "success", "node_type": dict(self.node_type)}))
            elif path == f"/node-types/{TYPE}/schema":
                answers.append((200, {"status": "success", "attributes": copy.deepcopy(self.attributes)}))
            else:
                answers.append((404, {"status": "error"}))
        return answers

    def _refused(self, name):
        def refuse(*args, **kwargs):
            self.calls.append((name, "whole tenant", self.person()))
            raise AssertionError(f"the dossier made a whole-tenant read: {name}")
        return refuse


@pytest.fixture
def brain(fake_graph, monkeypatch):
    """The Core's answers, and the switch on (pinned, as BRAIN_VIEWS_ENABLED pins it)."""
    monkeypatch.setenv("BRAIN_VIEWS_ENABLED", "true")
    return Brain(fake_graph, monkeypatch)


def _page(client, node_id=NODE, **query):
    response = client.get(f"/dossier/{node_id}", query_string=query)
    return response, response.get_data(as_text=True)


# --- 1: who gets the page --------------------------------------------------------------------------------------


def test_a_signed_out_caller_is_sent_to_sign_in(brain, anon_client):
    response = anon_client.get(f"/dossier/{NODE}")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert brain.calls == []


def test_switched_off_the_address_answers_404_and_the_core_is_not_asked(brain, member_client, monkeypatch):
    monkeypatch.delenv("BRAIN_VIEWS_ENABLED")
    response, _ = _page(member_client)
    assert response.status_code == 404
    assert brain.calls == []


@pytest.mark.parametrize("value, status", [("an", 200), ("true", 200), ("1", 200), ("aus", 404), ("false", 404)])
def test_the_switch_reads_the_page_word_and_the_environment_word(brain, member_client, monkeypatch, value, status):
    monkeypatch.setenv("BRAIN_VIEWS_ENABLED", value)
    response, _ = _page(member_client)
    assert response.status_code == status


def test_the_switch_is_in_the_settings_registry_installation_wide_and_off_by_default():
    import platform_settings as ps

    setting = ps.REGISTRY["brain_views.enabled"]
    assert setting.scope == "installation" and setting.kind == "choice"
    assert setting.env == ("BRAIN_VIEWS_ENABLED",) and setting.choices == ("aus", "an")
    assert setting.fallback == "aus"


def test_without_per_user_accounts_nothing_answers():
    from flask import Flask

    from web_interface.brain_views_routes import create_brain_views_blueprint

    app = Flask(__name__)
    asked = []
    app.register_blueprint(create_brain_views_blueprint(
        None, client=lambda: asked.append("client"), settings=lambda: asked.append("settings"),
        grant=lambda rows: asked.append("grant"), page_context=dict))
    assert app.test_client().get(f"/dossier/{NODE}").status_code == 404
    assert asked == []


# --- 2: a member opens one node ---------------------------------------------------------------------------------


def test_a_member_opens_a_node(brain, member_client):
    response, html = _page(member_client)
    assert response.status_code == 200
    assert '<h1 class="k-title">Limmat Immobilien AG</h1>' in html
    assert '<p class="k-eyebrow">Mandant</p>' in html
    assert "Zürich · Mandant seit 2018" in html
    assert 'class="k-tabs"' in html and "Überblick" in html and "Dokumente" in html
    # the Workspace's navigation, as every page of the area has it
    assert 'data-area="work"' in html


def test_the_tabs_are_addresses_of_this_dossier(brain, member_client):
    _, html = _page(member_client)
    assert f'href="/dossier/{NODE}"' in html
    assert f'href="/dossier/{NODE}?tab=dokumente"' in html
    assert 'k-tab k-tab--on" href="/dossier/' + NODE + '"' in html
    _, html = _page(member_client, tab="dokumente")
    assert 'k-tab k-tab--on" href="/dossier/' + NODE + '?tab=dokumente"' in html


def test_a_node_the_person_may_not_see_answers_as_a_node_that_does_not_exist(brain, member_client):
    hidden, hidden_html = _page(member_client, HIDDEN)
    missing, missing_html = _page(member_client, MISSING)
    assert hidden.status_code == missing.status_code == 404
    assert hidden_html == missing_html
    assert "Vergleich Hardturm" not in hidden_html
    assert hidden.headers.get("Content-Type") == missing.headers.get("Content-Type")


def test_the_same_node_is_shown_to_the_person_the_core_shows_it_to(brain, admin_client):
    response, html = _page(admin_client, HIDDEN)
    assert response.status_code == 200 and "Vergleich Hardturm" in html


# --- 3: one starting point ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("address", ["/dossier", "/dossier/", f"/dossier/{NODE}/{HIDDEN}"])
def test_there_is_no_dossier_without_exactly_one_node(brain, member_client, address):
    assert member_client.get(address).status_code == 404
    assert brain.calls == []


@pytest.mark.parametrize("node_id", ["..", "-x", "a b", "x" * 200, "%3Cscript%3E"])
def test_a_malformed_id_is_not_sent_to_the_core(brain, member_client, node_id):
    assert member_client.get(f"/dossier/{node_id}").status_code == 404
    assert [c for c in brain.calls if c[0] == "graph_node"] == []


# --- 4: per-thing reads, as the person -------------------------------------------------------------------------


def test_every_read_is_a_per_thing_read_made_as_the_person_signed_in(brain, member_client, member):
    for tab in (None, "dokumente"):
        response, _ = _page(member_client, **({"tab": tab} if tab else {}))
        assert response.status_code == 200
    assert brain.calls == 2 * [
        ("graph_node", NODE, member.email),
        ("graph_get_many", f"/node-types/{TYPE}", member.email),
        ("graph_get_many", f"/node-types/{TYPE}/schema", member.email),
    ]


def test_no_whole_tenant_read_even_without_a_kind(brain, member_client):
    brain.nodes[NODE]["node"]["node_type_id"] = None
    response, html = _page(member_client)
    assert response.status_code == 200
    assert 'class="k-eyebrow"' not in html
    assert [c[0] for c in brain.calls] == ["graph_node"]


def test_the_page_stands_when_the_kind_and_the_fields_cannot_be_read(brain, member_client, monkeypatch):
    def nothing(calls, **kwargs):
        return [(503, None) for _ in calls]

    monkeypatch.setattr(brain.fake, "graph_get_many", nothing)
    response, html = _page(member_client)
    assert response.status_code == 200
    assert 'class="k-eyebrow"' not in html
    # the facts keep their values; without the type's field names they are called by their own label or "Angabe"
    assert "Immobilienverwaltung" in html and "Angabe" in html


# --- 5: failures -------------------------------------------------------------------------------------------------


def test_a_failed_read_is_one_plain_message(brain, member_client, monkeypatch):
    from knovas_client import GraphError

    def broken(node_id):
        raise GraphError(500, "internal", "Knowledge graph operation failed")

    monkeypatch.setattr(brain.fake, "graph_node", broken)
    response, html = _page(member_client)
    assert response.status_code == 502
    assert "Akte nicht geladen" in html
    assert "Traceback" not in html and "GraphError" not in html and "Knowledge graph operation failed" not in html


def test_a_graph_switched_off_in_the_core_is_a_404(brain, member_client, monkeypatch):
    from knovas_client import KnowledgeGraphDisabled

    def off(node_id):
        raise KnowledgeGraphDisabled("aus")

    monkeypatch.setattr(brain.fake, "graph_node", off)
    response, _ = _page(member_client)
    assert response.status_code == 404
