"""J-1: the project's issues as ``record`` streams (fields, ``computed`` from the edit metadata, links), the issue's
change-log position as its ``version``, complete ``generation`` listings so deletions are seen and a cut listing
reports no absence (GI-CONN-01, GI-CONN-02; SPEC-write-back-12)."""
from __future__ import annotations

import pytest

from harness import SOURCE, JiraHarness
from knovas_connector_jira.client import JiraError
from knovas_connector_kit import contracts


@pytest.fixture
def h():
    return JiraHarness()


def records(stream):
    return {m["external_id"]: m for m in stream if m["type"] == "record"}


def test_every_message_is_valid_and_an_issue_is_a_record_of_scalars(h):
    stream = h.connector.read(h.source, None)
    assert all(contracts.valid("stream-message.schema.json", m) for m in stream)
    one = records(stream)["DEMO-1"]
    assert one["payload"]["record_kind"] == "issue"
    assert one["payload"]["fields"]["assignee"] == "acct-1"           # the account id, never a name or an address
    assert one["payload"]["fields"]["status"] == "To Do"
    assert "example.invalid" not in str(stream) and "Person" not in str(stream)


def test_numbers_travel_as_integers_or_strings_never_as_floats():
    h = JiraHarness(dict(SOURCE, settings=dict(SOURCE["settings"], fields=["summary", "customfield_10016"])))
    got = {k: m["payload"]["fields"]["customfield_10016"] for k, m in records(h.connector.read(h.source, None)).items()}
    assert got["DEMO-5"] == "2.5" and got["DEMO-6"] == "3" and got["DEMO-1"] is None


def test_links_name_other_issues_by_key_with_the_link_types_name(h):
    got = records(h.connector.read(h.source, None))
    assert got["DEMO-2"]["payload"]["links"] == [{"relation": "blocks", "external_id": "DEMO-1"}]
    assert got["DEMO-5"]["payload"]["links"] == [{"relation": "parent", "external_id": "DEMO-1"}]


def test_the_version_is_the_newest_change_log_entry_and_moves_with_every_change(h):
    before = records(h.connector.read(h.source, None))["DEMO-1"]["version"]
    h.jira.edit_by_someone("DEMO-1", summary="Renamed")
    after = records(h.connector.read(h.source, None))["DEMO-1"]
    assert before == "0" and after["version"] == h.jira.issues["DEMO-1"]["history"][-1]["id"]
    h.jira.issues["DEMO-1"]["properties"]["knovas.permit.x"] = {"v": 1}     # a property is no change of the record
    assert records(h.connector.read(h.source, None))["DEMO-1"]["version"] == after["version"]


def test_the_version_is_the_newest_entry_also_when_jira_returns_only_the_recent_ones(h):
    for n in range(5):
        h.jira.edit_by_someone("DEMO-1", summary=f"Rename {n}")
    h.jira.changelog_window = 2                                              # newest first, cut to two
    assert records(h.connector.read(h.source, None))["DEMO-1"]["version"] == h.jira.issues["DEMO-1"]["history"][-1]["id"]


def test_computed_comes_from_the_edit_metadata_and_the_automations_the_settings_name():
    h = JiraHarness(dict(SOURCE, settings=dict(SOURCE["settings"], computed_fields=["assignee"])))
    got = records(h.connector.read(h.source, None))
    assert "duedate" in got["DEMO-4"]["payload"]["computed"]                # not in DEMO-4's edit metadata
    assert "duedate" not in got["DEMO-3"]["payload"]["computed"]
    assert "assignee" in got["DEMO-1"]["payload"]["computed"]               # an automation sets it
    assert "status" not in got["DEMO-1"]["payload"]["computed"]             # written through a transition
    assert {"created", "resolution"} <= set(got["DEMO-1"]["payload"]["computed"])


def test_the_edit_metadata_is_read_per_issue_when_the_search_does_not_expand_it(h):
    h.jira.editmeta_in_search = False
    got = records(h.connector.read(h.source, None))
    assert "duedate" in got["DEMO-4"]["payload"]["computed"] and "duedate" not in got["DEMO-3"]["payload"]["computed"]
    assert ("GET", "/issue/DEMO-4/editmeta") in h.jira.calls


def test_a_complete_listing_over_several_pages_reports_a_deleted_issue(h):
    first = h.connector.read(h.source, None)
    state = [m["state"] for m in first if m["type"] == "state"][-1]
    h.jira.delete("DEMO-3")
    again = h.connector.read(h.source, state)
    assert [m["external_id"] for m in again if m["type"] == "tombstone"] == ["DEMO-3"]
    assert [m["payload"]["counts"] for m in again if m["type"] == "generation"] == [{"record": 5}]
    assert sum(1 for c in h.jira.calls if c == ("POST", "/search/jql")) >= 6      # pages of two, both reads


def test_a_cut_listing_reports_no_absence_and_no_state(h):
    state = [m["state"] for m in h.connector.read(h.source, None) if m["type"] == "state"][-1]
    h.jira.delete("DEMO-1")
    h.jira.cut_listing = True
    cut = h.connector.read(h.source, state)
    assert [m["type"] for m in cut] == ["record", "record"]


def test_a_failed_read_raises_a_code_only(h):
    h.jira.fail_reads_with = None
    h.jira.rate_limit_next = 10
    with pytest.raises(JiraError) as raised:
        h.connector.read(h.source, None)
    assert raised.value.code == "jira.rate_limited" and h.credential not in str(raised.value)


def test_the_same_source_read_twice_gives_the_same_stream(h):
    assert h.connector.read(h.source, None) == h.connector.read(h.source, None)
