"""PMVP-42: what the frozen acceptance file leaves open about the position, held on web_interface/admin_access.py.

1. ``position_of`` reads only what the client reports: the three modes, «unbekannt» for no call, an error, garbage or
   another mode; ``tenant_switch`` counts only when it is exactly true; the reasons are the page's words, once each,
   in the Core's order, a code it does not know as one sentence;
2. the switch offers the other position, and both from «teilweise» or while the position is unknown;
3. the template offers no Probe and no enforcing: its switch posts ``ein`` or ``aus`` only.

Pure functions and the template's source: no database, no app. The route is held in
test_web_admin_enforcement_routes.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from web_interface.admin_access import REASON_OTHER, REASONS, TO_CORE, Position, position_of, reason_for

TEMPLATE = Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates" / "admin_access.html"


class Client:
    def __init__(self, answer):
        self.answer = answer

    def rbac_posture(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize("mode, value", [("brokered", "ein"), ("disabled", "aus"), ("enforcing", "teilweise"),
                                         ("probe", "unbekannt"), (None, "unbekannt"), ("EIN", "unbekannt")])
def test_the_three_modes_and_everything_else(mode, value):
    position = position_of(Client({"mode": mode, "tenant_switch": True}))
    assert position.value == value
    assert position.mode == (mode if value != "unbekannt" else None)


@pytest.mark.parametrize("answer", [None, "brokered", ["brokered"], ConnectionError("unreachable")])
def test_no_answer_garbage_or_an_error_is_unbekannt_and_offers_no_switch(answer):
    assert position_of(Client(answer)) == Position("unbekannt")


def test_a_client_without_the_call_is_unbekannt():
    assert position_of(object()) == Position("unbekannt")


@pytest.mark.parametrize("flag, switchable", [(True, True), (False, False), ("true", False), (1, False), (None, False)])
def test_the_switch_is_offered_only_when_the_core_says_exactly_true(flag, switchable):
    assert position_of(Client({"mode": "disabled", "tenant_switch": flag})).switchable is switchable


def test_the_reasons_are_the_pages_words_once_each_in_the_cores_order():
    codes = ["rights_runs_missing", "no_broker_key", "rights_runs_missing", "x_new", "y_new", 7]
    position = position_of(Client({"mode": "disabled", "not_ready": codes}))
    assert position.not_ready == (REASONS["rights_runs_missing"], REASONS["no_broker_key"], REASON_OTHER)
    assert all(code not in " ".join(position.not_ready) for code in REASONS)
    assert position_of(Client({"mode": "disabled", "not_ready": "no_broker_key"})).not_ready == ()


def test_each_known_code_has_words_of_its_own():
    assert len({reason_for(code) for code in REASONS} | {reason_for("unbekannt_neu"), reason_for(None)}) == 4


@pytest.mark.parametrize("value, offers", [("aus", ("ein",)), ("ein", ("aus",)), ("teilweise", ("ein", "aus")),
                                           ("unbekannt", ("ein", "aus"))])
def test_the_switch_offers_the_other_position(value, offers):
    assert Position(value).offers == offers


def test_the_switch_sends_brokered_or_disabled_and_the_template_never_offers_probe_or_enforcing():
    assert TO_CORE == {"ein": "brokered", "aus": "disabled"}
    source = TEMPLATE.read_text(encoding="utf-8")
    part = source[source.index('parts.section("Durchsetzung")'):source.index("{% if answer %}")]
    assert "probe" not in part.lower() and "enforcing" not in part
    assert 'name="bestaetigt" value="1"' in part and 'type="checkbox"' in part
