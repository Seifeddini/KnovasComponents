"""RB-6, RB-7: the agent pages and the assistant's wiring, beyond the frozen acceptance file.

The console's «Agenten» (web_interface/admin_agents.py, admin_agents.html): every agent with whom it acts for, its id
and its roles; no revoke for the assistant or a revoked agent; the switch's state; only active people offered;
refusals (unknown role 404, unknown or revoked agent 400, the assistant revoked 400). The ask page answers as the
Knovas Assistent for the signed-in person (``AsTheAssistant``), and fails closed when the assistant cannot be read.
Work order docs/platform-mvp/work-orders/RB-6-7.md § 3 and § 5. Nothing reaches a tenant.
"""
from __future__ import annotations

import contextlib
import html as html_lib
import re
import uuid

import pytest

import conftest
from conftest import platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from assistant.service import FAILED_TEXT, Reply  # noqa: E402
from identity.agents import AgentPrincipal, AgentStore  # noqa: E402
from identity.roles import RoleStore  # noqa: E402
from identity.users import UserRepository  # noqa: E402


class PrincipalClient(conftest.DummyKnovasClient):
    """The dummy client with ``asserting_for``: it records the principal in force."""

    current = None

    @contextlib.contextmanager
    def asserting_for(self, user):
        previous, PrincipalClient.current = PrincipalClient.current, user
        try:
            yield
        finally:
            PrincipalClient.current = previous


class Seen:
    """A service that records the principal in force while it answers."""

    def __init__(self):
        self.principals = []

    def answer(self, question, history=()):
        self.principals.append(PrincipalClient.current)
        return Reply(status="no_answer", message="")


@pytest.fixture
def app(platform_db, tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_CORE_GATEWAY", raising=False)
    return conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=PrincipalClient)


@pytest.fixture
def repo(platform_db):
    return UserRepository(platform_db)


@pytest.fixture
def chef(repo):
    person = repo.create(email="chef@kanzlei.ch", display_name="Chef", password=conftest.PASSWORD)
    repo.grant_role(person.id, "admin")
    return person


@pytest.fixture
def browser(app, chef):
    return conftest._signed_in(app, chef.email)


def _main(page):
    return re.search(r"<main\b.*?</main>", page, flags=re.S).group(0)


def _rows(page):
    """agent id -> the text of its row."""
    found = {}
    for match in re.finditer(r'data-agent-id="([^"]+)"(.*?)</div>\s*<div class="agent-actions">(.*?)</div>',
                             page, flags=re.S):
        found[match.group(1)] = (html_lib.unescape(re.sub(r"<[^>]+>", " ", match.group(2))), match.group(3))
    return found


def test_the_menu_offers_agenten_and_the_page_lists_every_agent(app, browser, platform_db, repo):
    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    anna = repo.create(email="anna@kanzlei.ch", display_name="Anna Muster", password=conftest.PASSWORD)
    bound = store.create_agent(name="Claude Code Anna", on_behalf_of=anna.id)
    alone = store.create_agent(name="Copilot")
    gone = store.create_agent(name="Alt")
    store.revoke_agent(gone.id)
    role = roles.create_role(name="Prozess", core_group_id="g-1")
    store.add_to_role(role.id, alone.id)
    page = browser.get("/admin/agents").get_data(as_text=True)
    assert 'href="/admin/agents"' in page and "Agenten" in page
    rows = _rows(_main(page))
    assistant = store.assistant()
    assert list(rows) == [str(assistant.id), str(gone.id), str(bound.id), str(alone.id)]   # assistant, then by name
    text, actions = rows[str(assistant.id)]
    assert "Knovas Assistent" in text and "die angemeldete Person" in text and "Mitglied" in text and "Prozess" in text
    assert "revoke" not in actions
    assert "Anna Muster" in rows[str(bound.id)][0] and "Externer Agent" in rows[str(bound.id)][0]
    assert "selbständig" in rows[str(alone.id)][0] and "Prozess" in rows[str(alone.id)][0]
    assert f"/admin/agents/{alone.id}/revoke" in rows[str(alone.id)][1]
    assert "widerrufen" in rows[str(gone.id)][0] and "revoke" not in rows[str(gone.id)][1]
    for agent_id in rows:
        assert agent_id in rows[agent_id][0]                               # the «Kennung»


def test_the_page_says_the_switchs_state_and_that_it_acts_with_ein(app, browser, monkeypatch):
    page = _main(browser.get("/admin/agents").get_data(as_text=True))
    assert 'data-agents-switch="aus"' in page and "wirkt mit «Ein»" in page
    monkeypatch.setenv("AGENTS_CORE_GATEWAY", "an")
    assert 'data-agents-switch="an"' in _main(browser.get("/admin/agents").get_data(as_text=True))


def test_only_active_people_are_offered(app, browser, repo):
    anna = repo.create(email="anna@kanzlei.ch", display_name="Anna Muster", password=conftest.PASSWORD)
    otto = repo.create(email="otto@kanzlei.ch", display_name="Otto Weg", password=conftest.PASSWORD)
    repo.disable(otto.id)
    page = _main(browser.get("/admin/agents").get_data(as_text=True))
    select = re.search(r'<select name="on_behalf_of">(.*?)</select>', page, flags=re.S).group(1)
    assert f'value="{anna.id}"' in select and f'value="{otto.id}"' not in select


def test_refusals_answer_400_or_404_and_write_nothing(app, browser, platform_db, repo):
    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    otto = repo.create(email="otto@kanzlei.ch", display_name="Otto", password=conftest.PASSWORD)
    repo.disable(otto.id)
    token = browser._token
    refused = browser.post("/admin/agents/create", data={"csrf_token": token, "name": "Fuer Otto",
                                                         "on_behalf_of": str(otto.id)})
    assert refused.status_code == 400 and 'role="alert"' in refused.get_data(as_text=True)
    assert browser.post("/admin/agents/create", data={"csrf_token": token, "name": " "}).status_code == 400
    assert [a.name for a in store.list_agents() if a.kind == "external"] == []
    assistant = store.assistant()
    assert browser.post(f"/admin/agents/{assistant.id}/revoke", data={"csrf_token": token}).status_code == 400
    assert browser.post(f"/admin/agents/{uuid.uuid4()}/revoke", data={"csrf_token": token}).status_code == 404
    gone = store.create_agent(name="Alt")
    store.revoke_agent(gone.id)
    role = roles.create_role(name="Prozess", core_group_id="g-1")
    for path in (f"/admin/roles/{uuid.uuid4()}/agents/add", "/admin/roles/kein-uuid/agents/remove"):
        assert browser.post(path, data={"csrf_token": token, "agent_id": str(assistant.id)}).status_code == 404
    for agent_id in (str(gone.id), str(uuid.uuid4()), ""):
        answer = browser.post(f"/admin/roles/{role.id}/agents/add", data={"csrf_token": token, "agent_id": agent_id})
        assert answer.status_code == 400, agent_id
    assert store.get_agent(gone.id).roles == frozenset()
    assert not store.assistant().revoked


def test_the_ask_page_answers_as_the_assistant_for_the_signed_in_person(app, platform_db, repo, monkeypatch):
    import assistant.core as core

    monkeypatch.setenv("ASSISTANT_ENABLED", "an")
    seen = Seen()
    monkeypatch.setattr(core, "make_service", lambda *a, **k: seen)
    anna = repo.create(email="anna@kanzlei.ch", display_name="Anna", password=conftest.PASSWORD)
    repo.grant_role(anna.id, "member")
    person = conftest._signed_in(app, anna.email)
    # PMVP-23: a no_answer is stored as the person's chat and answered with a 303 to it.
    assert person.post("/fragen", data={"csrf_token": person._token, "question": "Frage?"}).status_code == 303
    assistant = AgentStore(platform_db).assistant()
    assert seen.principals == [AgentPrincipal(agent_id=assistant.id, on_behalf_of=anna.id)]
    assert PrincipalClient.current is None                                # the block ended with the answer


def test_without_the_assistant_the_ask_page_fails_closed(app, platform_db, repo, monkeypatch, caplog):
    import assistant.core as core

    monkeypatch.setenv("ASSISTANT_ENABLED", "an")
    seen = Seen()
    monkeypatch.setattr(core, "make_service", lambda *a, **k: seen)
    platform_db.execute("DELETE FROM agents WHERE kind = 'assistant'")
    anna = repo.create(email="anna@kanzlei.ch", display_name="Anna", password=conftest.PASSWORD)
    repo.grant_role(anna.id, "member")
    person = conftest._signed_in(app, anna.email)
    answer = person.post("/fragen", data={"csrf_token": person._token, "question": "Frage?"})
    assert answer.status_code == 200 and html_lib.escape(FAILED_TEXT) in answer.get_data(as_text=True)
    assert seen.principals == [] and "AgentNotFound" in caplog.text


def test_a_roles_agent_rows_and_the_agents_it_offers(platform_db, repo):
    from web_interface.admin_agents import role_agents

    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    anna = repo.create(email="anna@kanzlei.ch", display_name="Anna Muster", password=conftest.PASSWORD)
    bound = store.create_agent(name="Fristenpruefung", on_behalf_of=anna.id)
    alone = store.create_agent(name="Copilot")
    gone = store.create_agent(name="Alt")
    store.revoke_agent(gone.id)
    role = roles.create_role(name="Baurecht", core_group_id="g-bau")
    store.add_to_role(role.id, bound.id)
    people = {anna.id: anna.display_name}
    rows = role_agents(platform_db, roles.get_role(role.id), people)
    assert [(r["name"], r["note"]) for r in rows["members"]] == [
        ("Knovas Assistent", "verwendet diese Daten in Antworten an Mitglieder"),
        ("Fristenpruefung", "externer Agent, handelt für Anna Muster")]
    assert [r["name"] for r in rows["addable"]] == ["Copilot"]
    assert alone.id and role_agents(platform_db, roles.administration(), people) == {"members": [], "addable": []}


def test_the_revoke_says_what_happened_at_knovas(app, browser, platform_db, monkeypatch):
    store = AgentStore(platform_db)
    first, second = store.create_agent(name="Erster"), store.create_agent(name="Zweiter")
    off = browser.post(f"/admin/agents/{first.id}/revoke", data={"csrf_token": browser._token})
    assert off.status_code == 200 and "Übergabe an Knovas ist aus" in off.get_data(as_text=True)
    monkeypatch.setenv("AGENTS_CORE_GATEWAY", "an")                       # the dummy client has no pool route
    failed = browser.post(f"/admin/agents/{second.id}/revoke", data={"csrf_token": browser._token})
    assert failed.status_code == 200 and "Knovas war nicht erreichbar" in failed.get_data(as_text=True)
    assert store.get_agent(first.id).revoked and store.get_agent(second.id).revoked


def _members_part(page):
    return re.search(r'<section\b[^>]*data-role-part="members"[^>]*>(.*?)</section>', _main(page), flags=re.S).group(1)


def test_the_role_page_draws_its_agents_under_mitglieder_and_offers_the_others(app, browser, platform_db, repo):
    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    anna = repo.create(email="anna@kanzlei.ch", display_name="Anna Muster", password=conftest.PASSWORD)
    bound = store.create_agent(name="Fristenpruefung", on_behalf_of=anna.id)
    alone = store.create_agent(name="Copilot")
    role = roles.create_role(name="Baurecht", core_group_id="g-bau")
    store.add_to_role(role.id, bound.id)
    members = _members_part(browser.get(f"/admin/roles/{role.id}").get_data(as_text=True))
    assistant = store.assistant()
    rows = re.findall(r'data-agent-id="([^"]+)"', members)
    assert rows == [str(assistant.id), str(bound.id)]
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", members))
    assert "Knovas Assistent" in text and "Fristenpruefung" in text and "handelt für Anna Muster" in text
    assert members.count(f'action="/admin/roles/{role.id}/agents/remove"') == 2
    add = re.search(rf'<form[^>]*action="/admin/roles/{role.id}/agents/add"[^>]*>(.*?)</form>', members, flags=re.S)
    assert add and re.findall(r'<option value="([^"]+)"', add.group(1)) == [str(alone.id)]
    assert "+ Agent hinzufügen" in members
    answer = browser.post(f"/admin/roles/{role.id}/agents/remove",
                          data={"csrf_token": browser._token, "agent_id": str(assistant.id)})
    assert answer.status_code == 200 and f'data-role-id="{role.id}"' in answer.get_data(as_text=True)
    assert re.findall(r'data-agent-id="([^"]+)"', _members_part(answer.get_data(as_text=True))) == [str(bound.id)]
    administration = _members_part(browser.get(f"/admin/roles/{roles.administration().id}").get_data(as_text=True))
    assert "/agents/add" not in administration and "Kein Agent gehört zur Administration" in administration


def test_a_refused_agent_change_draws_the_role_page_with_its_reason(app, browser, platform_db):
    roles = RoleStore(platform_db)
    administration = roles.administration()
    answer = browser.post(f"/admin/roles/{administration.id}/agents/add",
                          data={"csrf_token": browser._token, "agent_id": str(AgentStore(platform_db).assistant().id)})
    page = answer.get_data(as_text=True)
    assert answer.status_code == 400 and f'data-role-id="{administration.id}"' in page
    assert 'role="alert"' in page and "Kein Agent gehört zur Administration" in page
