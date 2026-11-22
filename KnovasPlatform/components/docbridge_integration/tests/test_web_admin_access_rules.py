"""«Was sieht …?» (PMVP-41): what the frozen acceptance file leaves open, held on web_interface/admin_access.py.

test_pmvp41_what_sees_acceptance.py pins the page, the claim rule, the reach, one document, the search, the audit
row and the logs against the real app. Held here:

1. the pages of the reach: page 1 draws at most 25 (``bounded``), the rest pages of 50 (``paged``); a page past the
   last is the last, a page of a list without a rest is page 1; every row once;
2. the reach reads per thing: X's groups and the groups below them, each walked at most RB-5's five pages; a walk that
   cannot finish keeps what it read and says the list may be incomplete; a document is one row however many of X's
   groups list it;
3. one document: a folder rule without a group leaves the document open (no rule named); rights of its own win over
   a rule; once ``brokered`` a person's or an agent's answer is Knovas's, asked inside ``asserting_for``, a role's
   stays computed; a subject that reads nothing is never asked about;
4. the search: names only (no passage, summary or chunk key reaches the answer), at most 25, and a hit whose rights
   cannot be read carries no mark;
5. the form's fields: a pointer or a query out of bounds is refused before anything is asked;
6. the template: nothing marked safe, every form action from url_for.

Pure functions against a small fake Core: no database, no app. The routes are held in test_web_admin_access_routes.py.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest

from web_interface.admin_access import (
    Refused,
    Subject,
    check_document,
    clean_pointer,
    clean_query,
    reach_page,
    reach_rows,
    search_as,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates" / "admin_access.html"
NONE = "__acl_folder_none__"


class Core:
    """A small Core: groups (a tree), folder rules, documents (``own`` None: governed by its rule); every read and
    whose claim it carried is recorded in ``calls``."""

    def __init__(self, *, groups=(), rules=(), docs=(), readable=None, hits=(), fail=()):
        self.groups = [dict(g) for g in groups]
        self.rules = [dict(r) for r in rules]
        self.docs = [dict(d) for d in docs]
        self.readable = dict(readable or {})
        self.hits = list(hits)
        self.fail = set(fail)
        self.calls = []
        self.principal = None

    def _call(self, name, **detail):
        self.calls.append(dict(call=name, **detail, principal=self.principal))
        if name in self.fail:
            raise ConnectionError("unreachable")

    @contextlib.contextmanager
    def asserting_for(self, user):
        previous, self.principal = self.principal, user
        try:
            yield
        finally:
            self.principal = previous

    def access_groups(self):
        self._call("access_groups")
        return [dict(g) for g in self.groups]

    def folder_rules(self):
        self._call("folder_rules")
        return [dict(r) for r in self.rules]

    def _rule(self, pointer):
        covering = [r for r in self.rules if pointer.startswith(r["pointer_prefix"])]
        return max(covering, key=lambda r: len(r["pointer_prefix"])) if covering else None

    def _entry(self, doc):
        rule = None if doc.get("own") is not None else self._rule(doc["pointer"])
        groups = list(doc["own"]) if doc.get("own") is not None else list(rule["access_groups"]) if rule else []
        return {"pointer": doc["pointer"], "document_uuid": doc["uuid"], "access_groups": groups,
                "acl_folder_id": rule["rule_id"] if rule else NONE, "title": doc.get("title")}

    def documents(self, after=None, limit=100, prefix=None, group=None, **_):
        self._call("documents", prefix=prefix, group=group)
        assert prefix or group, "an unfiltered listing"
        rows = [self._entry(d) for d in self.docs if not prefix or d["pointer"].startswith(prefix)]
        rows = [r for r in rows if not group or group in r["access_groups"]]
        start = int(after or 0)
        return {"documents": rows[start:start + limit],
                "next_after": str(start + limit) if start + limit < len(rows) else None}

    def document_access(self, pointer):
        self._call("document_access", pointer=pointer)
        doc = next((d for d in self.docs if d["pointer"] == pointer), None)
        return None if doc is None else {"pointer": pointer, "access_groups": self._entry(doc)["access_groups"]}

    def document_readable(self, pointer):
        self._call("document_readable", pointer=pointer)
        return self.readable.get(pointer, False)

    def search_documents(self, query, limit=20, **_):
        self._call("search_documents", query=query, limit=limit)
        return {"results": [dict(h) for h in self.hits][:limit], "total": len(self.hits)}


TREE = [{"group_id": "g-partner", "name": "Partner", "parent_id": None},
        {"group_id": "g-team", "name": "Mandatsteam", "parent_id": "g-partner"},
        {"group_id": "g-bau", "name": "Bauabteilung", "parent_id": None}]


def person(*groups, nothing=None):
    return Subject("person:p", "person", "p", "Pia", tuple(groups), principal="pia", nothing=nothing)


def role(*groups):
    return Subject("role:r", "role", "r", "Bau", tuple(groups))


# --- 1. the pages of the reach --------------------------------------------------------------------------------------


def test_page_one_draws_at_most_25_and_the_rest_comes_in_pages_of_50():
    rows = [{"key": f"document:{n:03d}"} for n in range(140)]
    first = reach_page(rows, 1)
    assert len(first["rows"]) == 25 and first["more"] == 115 and first["pages"] == 4
    seen = list(first["rows"])
    for seite, size, more in ((2, 50, 65), (3, 50, 15), (4, 15, 0)):
        page = reach_page(rows, seite)
        assert (len(page["rows"]), page["more"], page["page"]) == (size, more, seite)
        seen += page["rows"]
    assert seen == rows                                                   # every row once, in order
    assert reach_page(rows, 4)["first"] == 126 and reach_page(rows, 4)["last"] == 140
    assert reach_page(rows, 99)["page"] == 4                              # past the last: the last


def test_a_short_list_has_one_page_whatever_page_is_asked():
    rows = [{"key": f"rule:{n}"} for n in range(7)]
    for seite in (1, 2, 5):
        page = reach_page(rows, seite)
        assert (len(page["rows"]), page["more"], page["page"], page["pages"]) == (7, 0, 1, 1)
    assert reach_page([], 1) == {"rows": [], "more": 0, "page": 1, "pages": 1, "first": 0, "last": 0}


# --- 2. the reach reads per thing -------------------------------------------------------------------------------------


def test_the_reach_walks_xs_groups_and_those_below_once_each_and_lists_a_document_once():
    core = Core(groups=TREE, rules=[{"rule_id": "r-team", "pointer_prefix": "M/Team/", "access_groups": ["g-team"]}],
                docs=[{"pointer": "A/beide.pdf", "uuid": "u-1", "own": ["g-partner", "g-team"]},
                      {"pointer": "M/Team/t.pdf", "uuid": "u-2", "own": None}])
    rows, partial = reach_rows(core, person("g-partner"))
    assert [r["key"] for r in rows] == ["rule:r-team", "document:u-1"] and not partial
    walked = [c["group"] for c in core.calls if c["call"] == "documents"]
    assert walked == ["g-partner", "g-team"]                             # at and below X's group, once each
    assert [g["name"] for g in rows[1]["groups"]] == ["Mandatsteam", "Partner"]


def test_a_walk_that_cannot_finish_keeps_what_it_read_and_says_so():
    core = Core(groups=TREE, docs=[{"pointer": f"A/{n:04d}.pdf", "uuid": f"u-{n:04d}", "own": ["g-bau"]}
                                   for n in range(1100)])
    rows, partial = reach_rows(core, person("g-bau"))
    assert partial and len(rows) == 1000                                  # five pages of 200
    assert len([c for c in core.calls if c["call"] == "documents"]) == 5


def test_a_subject_that_reads_nothing_asks_nothing():
    core = Core(groups=TREE)
    assert reach_rows(core, person("g-bau", nothing="widerrufen")) == ([], False)
    assert core.calls == []


# --- 3. one document --------------------------------------------------------------------------------------------------


def test_a_rule_without_a_group_leaves_the_document_open_and_names_no_rule():
    core = Core(groups=TREE, rules=[{"rule_id": "r-o", "pointer_prefix": "Offen/", "access_groups": []}],
                docs=[{"pointer": "Offen/x.pdf", "uuid": "u-x", "own": None}])
    answer = check_document(core, person(), "Offen/x.pdf", False)
    assert (answer["answer"], answer["rule"], answer["groups"]) == ("ja", None, [])


def test_rights_of_its_own_win_over_the_rule_of_its_folder():
    core = Core(groups=TREE, rules=[{"rule_id": "r-t", "pointer_prefix": "M/", "access_groups": ["g-team"]}],
                docs=[{"pointer": "M/eigen.pdf", "uuid": "u-e", "own": ["g-bau"]}])
    answer = check_document(core, person("g-partner"), "M/eigen.pdf", False)
    assert (answer["answer"], answer["rule"], answer["own"]) == ("nein", None, True)
    assert [g["id"] for g in answer["groups"]] == ["g-bau"]
    via = check_document(core, person("g-bau"), "M/eigen.pdf", False)
    assert via["answer"] == "ja" and via["via"] == ["Bauabteilung"]


def test_under_ein_knovas_answers_for_a_person_and_a_role_stays_computed():
    core = Core(groups=TREE, rules=[{"rule_id": "r-t", "pointer_prefix": "M/", "access_groups": ["g-team"]}],
                docs=[{"pointer": "M/t.pdf", "uuid": "u-t", "own": None}], readable={"M/t.pdf": False})
    answer = check_document(core, person("g-partner"), "M/t.pdf", True)
    assert (answer["answer"], answer["source"], answer["computed"]) == ("nein", "knovas", "ja")
    assert [c["principal"] for c in core.calls if c["call"] == "document_readable"] == ["pia"]
    core.calls.clear()
    answer = check_document(core, role("g-team"), "M/t.pdf", True)
    assert (answer["answer"], answer["source"]) == ("ja", "berechnet")
    assert not [c for c in core.calls if c["call"] == "document_readable"]


def test_under_ein_a_subject_that_reads_nothing_is_not_asked_about():
    core = Core(groups=TREE, docs=[{"pointer": "F/f.pdf", "uuid": "u-f", "own": None}], readable={"F/f.pdf": True})
    answer = check_document(core, person(nothing="deaktiviert"), "F/f.pdf", True)
    assert answer["answer"] == "nein" and not [c for c in core.calls if c["call"] == "document_readable"]


def test_an_unknown_document_is_refused():
    with pytest.raises(Refused):
        check_document(Core(groups=TREE), person(), "fehlt.pdf", False)


# --- 4. the search ----------------------------------------------------------------------------------------------------


def _hit(pointer):
    return {"doc_id": pointer, "path": pointer, "title": pointer.rsplit("/", 1)[-1], "snippet": "Geheimer Satz",
            "ingested_summary": "Zusammenfassung", "top_chunks": [{"text": "Geheimer Satz"}]}


def test_the_search_shows_names_only_at_most_25_and_asks_as_x():
    core = Core(groups=TREE, docs=[{"pointer": f"F/{n:02d}.pdf", "uuid": f"u-{n}", "own": None} for n in range(30)],
                hits=[_hit(f"F/{n:02d}.pdf") for n in range(30)])
    answer = search_as(core, person("g-bau"), "Frist", False)
    assert len(answer["hits"]) == 25 and answer["total"] == 30
    assert all(set(hit) == {"pointer", "title", "would"} for hit in answer["hits"])
    assert [c["principal"] for c in core.calls if c["call"] == "search_documents"] == ["pia"]
    assert [c["limit"] for c in core.calls if c["call"] == "search_documents"] == [25]


def test_a_hit_whose_rights_cannot_be_read_carries_no_mark():
    core = Core(groups=TREE, hits=[_hit("F/a.pdf")], fail={"document_access"})
    assert search_as(core, person("g-bau"), "Frist", False)["hits"][0]["would"] is None


def test_under_ein_the_hits_carry_no_mark_and_no_rights_are_read():
    core = Core(groups=TREE, hits=[_hit("F/a.pdf")])
    assert search_as(core, person("g-bau"), "Frist", True)["hits"][0]["would"] is None
    assert [c["call"] for c in core.calls] == ["search_documents"]


# --- 5. the form's fields ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "x" * 1025, "a\x00b.pdf", "a\nb.pdf", None, 7])
def test_a_pointer_out_of_bounds_is_refused(value):
    with pytest.raises(Refused):
        clean_pointer(value)


@pytest.mark.parametrize("value", ["", "  \t ", "x" * 501, "a\x07b", None])
def test_a_query_out_of_bounds_is_refused(value):
    with pytest.raises(Refused):
        clean_query(value)


def test_a_query_keeps_its_words_and_a_pointer_its_text():
    assert clean_query("  Kündigung   Frist ") == "Kündigung Frist"
    assert clean_pointer("  DMS/Steuer/a b.pdf ") == "DMS/Steuer/a b.pdf"


# --- 6. the template --------------------------------------------------------------------------------------------------


def test_the_template_marks_nothing_safe_and_takes_every_action_from_url_for():
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "|safe" not in source.replace(" ", "") and "Markup" not in source
    actions = re.findall(r'action="([^"]*)"', source)
    assert actions and all(a.startswith("{{ url_for(") for a in actions)
    assert "href=" not in source.replace('href="{{ url_for(\'static\'', "")
