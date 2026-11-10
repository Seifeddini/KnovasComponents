"""J-1: the project's issues as ``record`` streams (fields, ``computed`` from the edit metadata, the parent link), the
issue's change-log position as its ``version``, complete ``generation`` listings so deletions are seen and a cut
listing reports no absence (GI-CONN-01, GI-CONN-02; SPEC-write-back-12). J-4: the stream carries what a Brain mapping
of an issue reads and no more (the recommendation for the founder's decision D-34): the default fields, the parent
as the one link, of the change log only the version id, of the edit metadata only ``computed``, no person's name or
address anywhere, no issue property but the permit markers, and those only where an act or a reread needs them."""
from __future__ import annotations

import json

import pytest

from harness import SOURCE, JiraHarness
from knovas_connector_jira.client import DEFAULT_FIELDS, JiraError
from knovas_connector_jira.connector import ACTION_FIELDS, MARK_PREFIX
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


def test_the_parent_is_the_one_link_and_issue_links_and_sub_tasks_are_neither_read_nor_carried(h):
    h.jira.issues["DEMO-1"]["fields"]["subtasks"] = [{"id": "20005", "key": "DEMO-5", "fields": {"summary": "x"}}]
    got = records(h.connector.read(h.source, None))
    assert got["DEMO-5"]["payload"]["links"] == [{"relation": "parent", "external_id": "DEMO-1"}]
    assert got["DEMO-2"]["payload"]["links"] == [] and got["DEMO-1"]["payload"]["links"] == []
    searches = [body for method, path, _params, body in h.jira.requests if path == "/search/jql"]
    assert searches and all(set(b["fields"]) == set(DEFAULT_FIELDS) | {"parent"} for b in searches)


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
    assert {"created", "resolutiondate"} <= set(got["DEMO-1"]["payload"]["computed"])


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


# ------------------------------------------------------------------ what the stream carries (J-4, D-34 recommendation)

def test_without_fields_in_the_settings_a_record_carries_the_default_fields_description_in_reporter_out(h):
    assert DEFAULT_FIELDS == ("summary", "description", "status", "assignee", "duedate", "priority", "labels",
                              "issuetype", "created", "resolutiondate")
    fields = records(h.connector.read(h.source, None))["DEMO-1"]["payload"]["fields"]
    assert set(fields) == set(DEFAULT_FIELDS)
    for left_out in ("reporter", "resolution", "comment", "attachment", "worklog", "creator", "watches", "subtasks",
                     "issuelinks"):
        assert left_out not in fields, left_out


def test_no_display_name_or_email_reaches_the_stream_whatever_fields_the_settings_name():
    """Persons are objects with an account id, a display name and an address, in the fields, in the change log's
    authors and in its earlier values; the stream carries the account id only."""
    named = ["summary", "assignee", "reporter", "creator", "customfield_10070", "customfield_10071", "description"]
    h = JiraHarness(dict(SOURCE, settings=dict(SOURCE["settings"], fields=named)))
    person = h.jira._person
    h.jira.issues["DEMO-1"]["fields"].update(
        creator=person("acct-3"), customfield_10070=[person("acct-4"), person("acct-5")],
        customfield_10071={"name": "someone-login", "key": "someone-key", "displayName": "Someone Without An Account",
                           "emailAddress": "no-account@example.invalid"},     # a person without an account id
        description={"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [
            {"type": "mention", "attrs": {"id": "acct-6", "text": "@Person 6"}}]}]})
    h.jira.edit_by_someone("DEMO-1", assignee=person("acct-2"))           # an entry whose author and values are persons
    stream = h.connector.read(h.source, None)
    text = json.dumps(stream)
    assert "Person" not in text and "@example.invalid" not in text and "Someone Without" not in text
    assert "someone-login" not in text and "someone-key" not in text, "nor a login name or key in its place"
    assert "displayName" not in text and "emailAddress" not in text
    one = records(stream)["DEMO-1"]["payload"]["fields"]
    assert one["assignee"] == "acct-2" and one["reporter"] == "acct-1" and one["creator"] == "acct-3"
    assert one["customfield_10070"] == ["acct-4", "acct-5"] and one["customfield_10071"] is None
    assert one["description"] == "@acct-6"


def test_of_the_change_log_only_the_newest_id_reaches_the_record(h):
    h.jira.edit_by_someone("DEMO-3", summary="Plan the review after next", duedate="2026-12-24", labels=["moved"])
    history = h.jira.issues["DEMO-3"]["history"]
    stream = h.connector.read(h.source, None)
    three = records(stream)["DEMO-3"]
    assert three["version"] == history[-1]["id"]
    text = json.dumps(stream)
    assert "Plan the next review" not in text and "2026-11-20" not in text and "planning" not in text, \
        "no earlier value from the change log"
    assert "acct-someone" not in text and "changelog" not in text and "histories" not in text
    assert set(three) == {"type", "source", "external_id", "version", "payload"}
    assert set(three["payload"]) == {"record_kind", "fields", "computed", "links"}


def test_of_the_edit_metadata_only_computed_field_ids_reach_the_record(h):
    stream = h.connector.read(h.source, None)
    text = json.dumps(stream)
    assert "editmeta" not in text and "operations" not in text and "allowedValues" not in text
    for m in records(stream).values():
        assert set(m["payload"]["computed"]) <= set(DEFAULT_FIELDS)


def test_issue_properties_are_read_only_for_the_permit_markers_of_an_act_or_a_reread(h):
    h.connector.read(h.source, None)
    h.connector.preview(h.source, h.sample_steps(), "direct", None)
    assert not any("properties" in params or "properties" in (body or {}) for _m, _p, params, body in h.jira.requests)
    preview = h.connector.preview(h.source, [h.sample_steps()[0]], "direct", None)["preview"]
    version = preview["read_versions"][0]["version"]
    permit = {"permit_id": "p-1", "expected_version": version}
    h.connector.act(h.source, [h.sample_steps()[0]], [permit], "direct")
    h.connector.reread(h.source, ["DEMO-1"], [], ["p-1"])
    asked = [params["properties"] for method, _p, params, _b in h.jira.requests if method == "GET" and "properties" in params]
    assert asked and all(k.startswith(MARK_PREFIX) for keys in asked for k in keys.split(","))


def test_a_preview_reads_the_fields_its_actions_write_and_no_link(h):
    h = JiraHarness(dict(SOURCE, settings=dict(SOURCE["settings"], fields=["summary"])))
    got = h.connector.preview(h.source, [h.sample_steps()[0]], "direct", None)
    assert got["preview"]["changes"][0]["before"] == "acct-1", "the assignee is read although the settings omit it"
    asked = [params["fields"].split(",") for method, path, params, _b in h.jira.requests
             if method == "GET" and path == "/issue/DEMO-1"]
    assert asked and all(set(f) == {"summary", *ACTION_FIELDS} for f in asked)
