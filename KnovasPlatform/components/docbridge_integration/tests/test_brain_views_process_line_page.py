"""PMVP-32a: the process line's page ``GET /ablauf/<node_id>`` and the dossier's card «Läuft gerade» (plan B-06, B-01,
B-02; contract PRE-01, POST-01, POST-03).

1. The gate is the dossier's: signed out 302, switched off 404, a hidden or missing thing 404.
2. The line: eyebrow, title, «Ablauf «…» · Schritt n von N»; the main path's steps marked done, current, open, each
   ``data-bv-node``; the card «Jetzt» with «seit» and who (an owner key is left out); «Danach»; «Andere Wege».
3. Off the main path; not completely described; not in a process: one sentence each, 200.
4. Thirty main steps: at most 25 drawn, about twelve a row, the rest a count that opens the main path as a list.
5. The dossier's card «Läuft gerade» for a thing with ``in_state``, linking to its line; none for a thing without.
6. Reads per thing, never the whole tenant; text stays text; no log line carries a name or a fact.

The Core is ``ProcessCore`` of test_brain_views_process_line_read.py on the shared FakeGraphApi.
"""
from __future__ import annotations

import html as html_lib
import logging
import re

import pytest

pytest.importorskip("flask")

from test_brain_views_process_line_read import (  # noqa: E402
    INSTANCE, PROCESS, STEP_NAMES, WHOLE_TENANT, ProcessCore, step_id)

NODE_MARK = re.compile(r"\bdata-bv-node\b")
ROW_MARK = re.compile(r"\bdata-bv-row\b")
SCRIPT = "<script>alert(1)</script>"
PERSON = "6f1c2c1e-0000-4000-8000-000000327001"


@pytest.fixture
def install(fake_graph, monkeypatch):
    """Puts a ProcessCore on the app's one client, the switch on; every whole-tenant read fails the test."""
    monkeypatch.setenv("BRAIN_VIEWS_ENABLED", "true")
    refused = []

    def put(core):
        for name in ("graph_node", "graph_neighbors", "graph_get_many", "graph_facts_batch"):
            monkeypatch.setattr(fake_graph, name, getattr(core, name), raising=False)
        monkeypatch.setattr(fake_graph, "document_readable", lambda pointer: True, raising=False)
        for name in WHOLE_TENANT:
            def refuse(*args, _name=name, **kwargs):
                refused.append(_name)
                raise AssertionError(f"whole-tenant read: {_name}")
            monkeypatch.setattr(fake_graph, name, refuse, raising=False)
        core.refused = refused
        return core

    return put


def _line(client, node_id=INSTANCE, **query):
    response = client.get(f"/ablauf/{node_id}", query_string=query)
    return response, response.get_data(as_text=True)


def _steps(html):
    """(state, mark, name) of each drawn step, in order."""
    return [(state, mark.strip(), html_lib.unescape(name)) for state, mark, name in re.findall(
        r'<li class="bv-step bv-step--(\w+)" data-bv-node[^>]*>\s*<span class="bv-step-mark"[^>]*>([^<]*)</span>\s*'
        r'<span class="bv-step-name">([^<]*)</span>', html)]


# --- 1: the gate ------------------------------------------------------------------------------------------------------


def test_a_signed_out_caller_is_sent_to_sign_in(install, anon_client):
    core = install(ProcessCore())
    response = anon_client.get(f"/ablauf/{INSTANCE}")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert core.calls == []


def test_switched_off_the_line_answers_404_and_the_core_is_not_asked(install, member_client, monkeypatch):
    core = install(ProcessCore())
    monkeypatch.delenv("BRAIN_VIEWS_ENABLED")
    assert _line(member_client)[0].status_code == 404
    assert core.calls == []


def test_a_hidden_or_missing_thing_answers_404_as_the_dossier_does(install, member_client):
    install(ProcessCore())
    missing = "6f1c2c1e-0000-4000-8000-000000000404"
    line, line_html = _line(member_client, missing)
    dossier = member_client.get(f"/dossier/{missing}")
    assert line.status_code == dossier.status_code == 404
    assert line_html == dossier.get_data(as_text=True)
    assert _line(member_client, "..x")[0].status_code == 404


# --- 2: the line -------------------------------------------------------------------------------------------------------


def test_the_line_shows_where_the_thing_stands(install, member_client):
    install(ProcessCore())
    response, html = _line(member_client)
    assert response.status_code == 200
    assert '<p class="k-eyebrow">Vorhaben</p>' in html
    assert '<h1 class="k-title">Vorhaben Archiv 2026</h1>' in html
    assert "Ablauf «Vorhaben-Ablauf» · Schritt 6 von 10" in html
    steps = _steps(html)
    assert [name for _, _, name in steps] == STEP_NAMES[:10]
    assert [state for state, _, _ in steps] == ["done"] * 5 + ["now"] + ["open"] * 4
    assert [mark for _, mark, _ in steps] == ["✓"] * 5 + [str(n) for n in range(6, 11)]
    assert html.count('aria-current="step"') == 1
    assert len(NODE_MARK.findall(html)) == 11                    # the thing and its ten main steps


def test_the_card_now_says_since_when_and_leaves_an_owner_key_out(install, member_client):
    install(ProcessCore())
    _, html = _line(member_client)
    assert "Jetzt: Freigabe" in html and "seit 9. Nov. 2026" in html
    assert "PERS-erfunden" not in html
    core = ProcessCore()
    core.facts[INSTANCE][2]["value"] = "Erfundene Person"
    install(core)
    _, html = _line(member_client)
    assert "seit 9. Nov. 2026 · Erfundene Person" in html


def test_after_the_current_step_comes_the_next_main_step(install, member_client):
    install(ProcessCore())
    _, html = _line(member_client)
    assert '<span class="k-muted">Danach:</span> In Arbeit' in html
    install(ProcessCore(current=10))
    _, html = _line(member_client)
    assert "Danach:" not in html and "Schritt 10 von 10" in html


def test_the_other_ways_open_on_request(install, member_client):
    install(ProcessCore())
    _, html = _line(member_client)
    ways = html[html.index('<details class="bv-ways">'):html.index("</details>")]
    assert "Andere Wege (2)" in ways
    rows = re.findall(r'<span class="bv-way" data-bv-row>([^<]*)</span>\s*<span class="k-muted k-small">([^<]*)</span>',
                      ways)
    assert rows == [("Verworfen", "Abzweigung"), ("Skizze", "zurück")]
    assert "<details class=\"bv-ways\" open" not in html


def test_the_line_links_back_to_the_dossier(install, member_client):
    install(ProcessCore())
    _, html = _line(member_client)
    assert f'href="/dossier/{INSTANCE}">Zum Dossier</a>' in html


# --- 3: off the main path, not completely described, not in a process ------------------------------------------------


def test_a_thing_off_the_main_path_has_no_current_step_on_it(install, member_client):
    install(ProcessCore(current=11))
    _, html = _line(member_client)
    assert "Dieser Eintrag hat den Hauptweg verlassen: <strong>Verworfen</strong>." in html
    assert 'aria-current="step"' not in html and "Schritt " not in html.split("k-lede", 1)[1].split("</p>", 1)[0]
    assert [state for state, _, _ in _steps(html)] == ["open"] * 10
    assert "Jetzt: Verworfen" in html and "Danach:" not in html


def test_a_process_not_completely_described_draws_no_line(install, member_client):
    core = ProcessCore()
    core.facts[step_id(3)] = []
    install(core)
    response, html = _line(member_client)
    assert response.status_code == 200
    assert "Der Ablauf ist nicht vollständig beschrieben." in html
    assert _steps(html) == [] and "Jetzt:" not in html
    assert len(NODE_MARK.findall(html)) == 1


def test_a_thing_in_no_process_gets_one_sentence(install, member_client):
    install(ProcessCore())
    response, html = _line(member_client, PERSON)
    assert response.status_code == 200
    assert "Für diesen Eintrag ist kein Ablauf hinterlegt." in html
    assert _steps(html) == [] and len(NODE_MARK.findall(html)) == 1


# --- 4: thirty main steps ---------------------------------------------------------------------------------------------


def test_thirty_main_steps_draw_at_most_25_in_rows_of_about_twelve(install, member_client):
    install(ProcessCore(steps=30, transitions=[(n, n + 1) for n in range(1, 30)], reworks=[], current=28))
    _, html = _line(member_client)
    assert len(NODE_MARK.findall(html)) <= 25
    steps = _steps(html)
    assert len(steps) == 24 and steps[-1][2] == "Schritt 30" and ("now", "28", "Schritt 28") in steps
    rows = re.findall(r'<ol class="bv-line" start="(\d+)">(.*?)</ol>', html, re.S)
    assert [len(re.findall(r"data-bv-node", row)) for _, row in rows] == [12, 12]
    assert rows[0][0] == "7"
    more = re.search(r'<a class="k-link" data-bv-more="(\d+)" href="([^"]+)">([^<]*)</a>', html)
    assert more and more.group(1) == "6" and more.group(3) == "6 weitere"
    response = member_client.get(html_lib.unescape(more.group(2)))
    listed = response.get_data(as_text=True)
    assert response.status_code == 200 and len(ROW_MARK.findall(listed)) == 30
    assert "28. Schritt 28" in listed and len(NODE_MARK.findall(listed)) <= 25


def test_thirteen_steps_wrap_into_two_even_rows(install, member_client):
    install(ProcessCore(steps=13, transitions=[(n, n + 1) for n in range(1, 13)], reworks=[], current=2))
    _, html = _line(member_client)
    rows = re.findall(r'<ol class="bv-line" start="\d+">(.*?)</ol>', html, re.S)
    assert [len(re.findall(r"data-bv-node", row)) for row in rows] == [7, 6]
    assert "ansicht=schritte" not in html                        # no step left out (PMVP-32b's «Alle N» has its own count)


# --- 5: the dossier's card «Läuft gerade» ----------------------------------------------------------------------------------


def test_the_dossier_of_a_thing_in_a_step_shows_where_it_runs(install, member_client):
    install(ProcessCore())
    response = member_client.get(f"/dossier/{INSTANCE}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    card = html[html.index('class="bv-running"') - 40:html.index('class="bv-running"') + 600]
    assert "Läuft gerade" in card and "data-bv-node" in card
    assert f'href="/ablauf/{INSTANCE}">Freigabe</a>' in card
    assert "Ablauf «Vorhaben-Ablauf»" in card
    assert len(NODE_MARK.findall(html)) == 2                      # the thing and the card that stands for its step


def test_a_thing_without_a_step_has_no_card(install, member_client):
    install(ProcessCore())
    html = member_client.get(f"/dossier/{PERSON}").get_data(as_text=True)
    assert "Läuft gerade" not in html and "/ablauf/" not in html
    html = member_client.get(f"/dossier/{INSTANCE}?tab=dokumente").get_data(as_text=True)
    assert "Läuft gerade" not in html                            # the card is the Überblick's


def test_the_card_costs_no_further_read(install, member_client):
    core = install(ProcessCore())
    member_client.get(f"/dossier/{INSTANCE}")
    assert [c[0] for c in core.calls].count("graph_neighbors") == 1
    assert "graph_facts_batch" not in [c[0] for c in core.calls]
    assert not any(c[0] == "graph_get_many" and c[1] == "/edges" for c in core.calls)


# --- 6: reads, text, logs ---------------------------------------------------------------------------------------------


def test_no_whole_tenant_read_on_any_of_the_pages(install, member_client):
    core = install(ProcessCore())
    for path in (f"/ablauf/{INSTANCE}", f"/ablauf/{PERSON}", f"/ablauf/{INSTANCE}?ansicht=schritte",
                 f"/dossier/{INSTANCE}"):
        assert member_client.get(path).status_code == 200
    install(ProcessCore(edges_route="404"))
    assert _line(member_client)[0].status_code == 200
    assert core.refused == []


def test_a_failed_read_is_one_plain_message(install, member_client):
    core = ProcessCore()
    core._edges = lambda params: (500, {"status": "error"})
    install(core)
    response, html = _line(member_client)
    assert response.status_code == 502 and "Ablauf nicht geladen" in html


def test_names_holding_mark_up_come_out_as_text(install, member_client):
    core = ProcessCore()
    core.nodes[INSTANCE]["name"] = f"Vorhaben {SCRIPT}"
    core.nodes[PROCESS]["name"] = f"Ablauf {SCRIPT}"
    for sid in core.steps:
        core.nodes[sid]["name"] = core.nodes[sid]["name"].replace("Vorhaben-Ablauf", f"Ablauf {SCRIPT}") + SCRIPT
    core.facts[INSTANCE][2]["value"] = f"Person {SCRIPT}"
    install(core)
    for path in (f"/ablauf/{INSTANCE}", f"/ablauf/{INSTANCE}?ansicht=schritte", f"/dossier/{INSTANCE}"):
        html = member_client.get(path).get_data(as_text=True)
        assert SCRIPT not in html, path
        assert "&lt;script&gt;" in html, path
    for href in re.findall(r'href="([^"]*)"', member_client.get(f"/ablauf/{INSTANCE}").get_data(as_text=True)):
        assert html_lib.unescape(href).startswith("/") and not href.startswith("//"), href


def test_no_log_line_carries_a_name_or_a_fact(install, member_client, caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="web_interface")
    caplog.set_level(logging.DEBUG, logger="brain_views")
    install(ProcessCore())
    _line(member_client)
    member_client.get(f"/dossier/{INSTANCE}")
    install(ProcessCore(edges_route="unfiltered"))
    _line(member_client)
    broken = ProcessCore()
    broken.facts[step_id(3)] = []
    install(broken)
    _line(member_client)
    failing = ProcessCore()
    failing._edges = lambda params: (500, {"status": "error"})
    install(failing)
    _line(member_client)
    lines = [record.getMessage() for record in caplog.records]
    assert any("Process line" in line for line in lines)
    for line in lines:
        for word in ("Vorhaben", "Freigabe", "Erfunden", "Archiv", "PERS-", "2026-11"):
            assert word not in line, line
