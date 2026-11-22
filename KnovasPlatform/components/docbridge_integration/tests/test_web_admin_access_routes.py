"""«Was sieht …?» (PMVP-41): the routes, where the frozen acceptance file leaves them open.

test_pmvp41_what_sees_acceptance.py pins the gates, the claim rule, the answers, the audit row and the logs. Held here
against the real app and the identity database, with a small fake Core:

1. a Core that cannot be read answers 502 with words of the page's own, and writes no audit row;
2. ``fuer`` counts for the Knovas Assistent only: an external agent acting on its own is asked about as itself, and
   the assistant for a deactivated person reads nothing;
3. the answer keeps X (and the assistant's person) chosen in all three forms, and the reach's «weitere» posts X and
   the next page in form fields: no form of the page puts a value into its address;
4. the page offers no revoked agent, and marks a deactivated person.
"""
from __future__ import annotations

import contextlib
import html as htmllib
import re

import pytest

import conftest
from conftest import DummyKnovasClient, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity.agents import AgentStore  # noqa: E402
from identity.roles import RoleStore  # noqa: E402


class Core(DummyKnovasClient):
    """Groups, one walled folder rule per document group, documents with rights of their own; ``broken`` makes every
    read fail as an unreachable Core would."""

    broken = False
    docs: list = []

    def _maybe_fail(self):
        if Core.broken:
            raise ConnectionError("unreachable")

    def access_groups(self):
        self._maybe_fail()
        return [{"group_id": "g-bau", "name": "Bauabteilung", "parent_id": None}]

    def folder_rules(self):
        self._maybe_fail()
        return [{"rule_id": "r-bau", "pointer_prefix": "Bau/", "access_groups": ["g-bau"]}]

    def documents(self, after=None, limit=100, prefix=None, group=None, **_):
        self._maybe_fail()
        rows = [{"pointer": p, "document_uuid": f"u-{n:03d}", "access_groups": ["g-bau"],
                 "acl_folder_id": "__acl_folder_none__"} for n, p in enumerate(Core.docs)]
        rows = [r for r in rows if (not prefix or r["pointer"].startswith(prefix)) and (not group or group == "g-bau")]
        start = int(after or 0)
        return {"documents": rows[start:start + limit],
                "next_after": str(start + limit) if start + limit < len(rows) else None}

    def document_access(self, pointer):
        self._maybe_fail()
        return {"pointer": pointer, "access_groups": ["g-bau"]}

    def search_documents(self, query, limit=20, **kwargs):
        self._maybe_fail()
        return super().search_documents(query, limit=limit, **kwargs)

    @contextlib.contextmanager
    def asserting_for(self, user):
        yield


@pytest.fixture
def world(platform_db, identity_repo, tmp_path, monkeypatch):
    Core.broken, Core.docs = False, []
    app = conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=Core)
    chef = conftest._person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna Ast", "member")
    erik = conftest._person(identity_repo, "erik@kanzlei.ch", "Erik Eich", "member")
    bau = RoleStore(platform_db).create_role(name="Bau", functions=set(), core_group_id="g-bau")
    for someone in (anna, erik):
        RoleStore(platform_db).add_member(bau.id, someone.id)
    identity_repo.disable(erik.id)
    agents = AgentStore(platform_db)
    bot = agents.create_agent(name="Bau-Bot", by=chef.id)
    agents.add_to_role(bau.id, bot.id, by=chef.id)
    old = agents.create_agent(name="Alt-Bot", by=chef.id)
    agents.revoke_agent(old.id, by=chef.id)
    client = conftest._signed_in(app, chef.email, with_csrf=False)
    with client.session_transaction() as sess:
        token = sess["csrf_token"]

    class World:
        pass

    w = World()
    w.conn, w.client, w.token = platform_db, client, token
    w.anna, w.erik, w.bot, w.old, w.assistant = anna, erik, bot, old, agents.assistant()
    return w


def _post(world, path, **fields):
    return world.client.post(path, data=dict(fields, csrf_token=world.token))


def _audit(world):
    return world.conn.execute("SELECT count(*) FROM audit_log WHERE action = 'access.what_sees'").fetchone()[0]


def _values(html, attribute):
    return [htmllib.unescape(v) for v in re.findall(rf'\s{attribute}="([^"]*)"', html)]


def test_a_core_that_cannot_be_read_answers_502_and_writes_no_row(world):
    Core.broken = True
    for path, fields in (("/admin/access/reach", {}), ("/admin/access/check", {"pointer": "Bau/a.pdf"}),
                         ("/admin/access/search", {"query": "Frist"})):
        response = _post(world, path, wer=f"person:{world.anna.id}", fuer="", **fields)
        assert response.status_code == 502, path
        html = response.data.decode("utf-8")
        assert "Knovas hat nicht geantwortet" in html and 'role="alert"' in html
        assert _values(html, "data-what-sees") == []
    assert _audit(world) == 0


def test_fuer_counts_for_the_assistant_only(world):
    response = _post(world, "/admin/access/reach", wer=f"agent:{world.bot.id}", fuer=str(world.anna.id))
    assert response.status_code == 200                                   # ignored: the bot acts on its own
    assert _values(response.data.decode("utf-8"), "data-reads") == ["rule:r-bau"]
    html = _post(world, "/admin/access/reach", wer=f"agent:{world.assistant.id}",
                 fuer=str(world.erik.id)).data.decode("utf-8")
    assert _values(html, "data-reads-nothing") == ["person-inaktiv"]
    row = world.conn.execute("SELECT detail FROM audit_log WHERE action = 'access.what_sees' ORDER BY id DESC "
                             "LIMIT 1").fetchone()[0]
    assert row["fuer"] == str(world.erik.id) and row["reads_nothing"] == "person-inaktiv"


def test_the_answer_keeps_x_chosen_and_the_list_pages_through_form_fields(world):
    Core.docs = [f"Akten/{n:03d}.pdf" for n in range(40)]
    html = _post(world, "/admin/access/reach", wer=f"agent:{world.assistant.id}",
                 fuer=str(world.anna.id)).data.decode("utf-8")
    chosen = f'value="agent:{world.assistant.id}" selected'
    assert html.count(chosen) == 3 and html.count(f'value="{world.anna.id}" selected') == 3
    assert len(_values(html, "data-reads")) == 25 and _values(html, "data-more") == ["16"]
    more = html[html.index('data-more="16"') - 900:html.index('data-more="16"')]
    assert 'name="seite" value="2"' in more and f'name="wer" value="agent:{world.assistant.id}"' in more
    assert f'name="fuer" value="{world.anna.id}"' in more
    assert all("?" not in a for a in re.findall(r'action="([^"]*)"', html))
    page = _post(world, "/admin/access/reach", wer=f"agent:{world.assistant.id}", fuer=str(world.anna.id), seite="2")
    assert len(_values(page.data.decode("utf-8"), "data-reads")) == 16


def test_the_page_offers_no_revoked_agent_and_marks_a_deactivated_person(world):
    html = world.client.get("/admin/access").data.decode("utf-8")
    offered = set(_values(html, "value"))
    assert f"agent:{world.bot.id}" in offered and f"agent:{world.old.id}" not in offered
    assert re.search(rf'value="person:{world.erik.id}"[^>]*>\s*Erik Eich \(deaktiviert\)', html)
    assert str(world.erik.id) not in offered                             # nobody acts for a deactivated person
