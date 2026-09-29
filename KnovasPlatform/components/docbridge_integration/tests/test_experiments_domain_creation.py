"""Creating a domain (Bereich): the suggestions, the entry point and the errors.

Reported as "I can't add new domains". Three causes were found: the dialog
suggested a prefix that was already taken ("Engineering Team" -> ENG) and
the refusal was not tied to the field; the list page offered no way to add
one; an experimenter saw no hint that managers set domains up.
"""

from __future__ import annotations

import pytest

from conftest import platform_db_reachable
from test_experiments_frontend import MANAGE_EXPORTS, _LIST_SETUP, LIST_EXPORTS, _run_page, needs_node

# -- the dialog's suggestions ---------------------------------------------------


def _suggest(names, domains):
    body = r"""
    __t.state.domains = %s;
    out(%s.map((n) => [__t.suggestDomainKey(n), __t.suggestDomainPrefix(n)]));
    """ % (domains, names)
    setup = r"""
    window.pageData = { canManage: true };
    routes.push((m, u) => (u === '/api/experiments/meta' ? { body: { success: true, meta: {} } } : null));
    """
    return _run_page("experiments_manage.js", body, setup=setup,
                     exports=MANAGE_EXPORTS + ("state", "suggestDomainKey", "suggestDomainPrefix"))


_PACKS = """[{ key: 'engineering', id_prefix: 'ENG', name: 'Engineering' },
             { key: 'sales', id_prefix: 'SAL', name: 'Vertrieb' },
             { key: 'kundenservice', id_prefix: 'KUN', name: 'Kundenservice' },
             { key: 'alt', id_prefix: 'ALT', name: 'Alt', archived: true }]"""


@needs_node
class TestSuggestions:
    def test_free_names_keep_the_simple_suggestion(self):
        result = _suggest("['Einkauf', 'Recht & Compliance', 'Qualität', 'HR']", _PACKS)
        assert result == [["einkauf", "EIN"], ["recht-compliance", "REC"],
                          ["qualitaet", "QUA"], ["hr", "HR"]]

    def test_taken_prefixes_and_keys_are_not_offered(self):
        result = _suggest("['Engineering Team', 'Sales DACH', 'Kundenservice', 'Alternativen']", _PACKS)
        prefixes = [p for _, p in result]
        assert prefixes[0] == "ET" and prefixes[1] == "SD"
        # An archived domain keeps its prefix as well.
        assert prefixes[3] not in ("ALT", "ENG", "SAL", "KUN")
        assert result[2][0] == "kundenservice-2"
        assert all(p not in ("ENG", "SAL", "KUN", "ALT") for p in prefixes)

    def test_single_word_falls_back_to_longer_prefixes(self):
        result = _suggest("['Engine']", _PACKS)
        assert result[0][1] == "ENGI"


# -- the entry point on the list page ---------------------------------------------------


def _chips(can_manage):
    setup = _LIST_SETUP.replace("canManage: false", "canManage: %s" % ("true" if can_manage else "false"))
    return _run_page("experiments_list.js", r"""
    await tick();
    const links = document.getElementById('kxDomainChips').querySelectorAll('a');
    out(links.map((a) => [a.textContent, a.getAttribute ? a.getAttribute('href') : a.href]));
    """, setup=setup, exports=LIST_EXPORTS)


@needs_node
class TestListEntryPoint:
    def test_managers_get_a_new_domain_link_next_to_the_filter(self):
        assert _chips(True) == [["+ Neuer Bereich", "/experiments/verwaltung#bereich-neu"]]

    def test_experimenters_do_not(self):
        assert _chips(False) == []


# -- the server names the field that holds a taken value --------------------------------

db = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")


@db
def test_a_taken_prefix_or_key_is_refused_at_its_field(exp_manager_client):
    first = exp_manager_client.post("/api/experiments/domains",
                                    json={"key": "einkauf", "name": "Einkauf", "id_prefix": "EIN"})
    assert first.status_code in (200, 201), first.get_json()
    prefix = exp_manager_client.post("/api/experiments/domains",
                                     json={"key": "einkauf-ch", "name": "Einkauf CH", "id_prefix": "EIN"})
    assert prefix.status_code == 409
    assert set(prefix.get_json()["fields"]) == {"id_prefix"}
    key = exp_manager_client.post("/api/experiments/domains",
                                  json={"key": "einkauf", "name": "Einkauf 2", "id_prefix": "EK"})
    assert key.status_code == 409
    assert set(key.get_json()["fields"]) == {"key"}


@db
def test_a_taken_metric_key_is_refused_at_its_field(exp_manager_client):
    body = {"domain": None, "key": "generic_score", "name": "Doppelt", "kind": "mean"}
    refused = exp_manager_client.post("/api/experiments/metrics", json=body)
    assert refused.status_code == 409
    assert set(refused.get_json()["fields"]) == {"key"}
