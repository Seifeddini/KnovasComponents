"""The star-view demo snapshot: one client planned from experiments.

HelioGrid AG is the worked example. It keeps several target markets,
personas, ICPs and channels, and a campaign is scheduled only when an
experiment has produced a decisive evaluation.
"""
import json
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[1] / "demo_data" / "knovas_ontology.json"


def _load():
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _detail(data, entity_id):
    return data["details"][entity_id]


def _out(detail, predicate):
    return sorted(
        rel["target"]["id"]
        for rel in detail["relations"]
        if rel["direction"] == "out" and rel["predicate"] == predicate
    )


def test_snapshot_matches_the_demo_server_shape():
    data = _load()
    summary = data["summary"]
    assert summary["success"] is True
    type_ids = {row["id"] for row in summary["types"]}
    assert type_ids == set(data["entities"])
    for type_id, rows in data["entities"].items():
        listed = {row["id"] for row in rows}
        assert listed
        for row in rows:
            assert row["type"] == type_id
            detail = _detail(data, row["id"])
            assert detail["entity"]["id"] == row["id"]
            assert detail["entity"]["label"] == row["label"]
            assert detail["entity"]["type"] == type_id
    known = set(data["details"])
    for detail in data["details"].values():
        for rel in detail["relations"]:
            assert rel["direction"] in ("out", "in")
            assert rel["target"]["id"] in known
            assert rel["predicate"]


def test_heliogrid_holds_markets_personas_icps_and_channels():
    data = _load()
    client = _detail(data, "client-heliogrid")
    assert client["entity"]["label"] == "HelioGrid AG"
    assert client["entity"]["type"] == "type-client"
    assert _out(client, "targets") == ["market-dach", "market-food", "market-utilities"]
    assert _out(client, "addresses") == [
        "persona-cio", "persona-energy", "persona-ops", "persona-procurement",
    ]
    assert _out(client, "defines") == ["icp-food", "icp-manufacturers", "icp-utilities"]
    assert _out(client, "reaches through") == [
        "channel-email", "channel-events", "channel-linkedin", "channel-webinar",
    ]
    labels = {row["label"]: row["count"] for row in data["summary"]["types"]}
    assert labels["Client"] == 1
    assert labels["Target market"] == 3
    assert labels["Persona"] == 4
    assert labels["ICP"] == 3
    assert labels["Channel"] == 4


def test_campaigns_are_planned_from_experiments_and_evaluations():
    data = _load()
    client = _detail(data, "client-heliogrid")
    assert _out(client, "plans") == [
        "campaign-roundtable", "campaign-sprint", "campaign-winter",
    ]
    assert _out(client, "learns from") == [
        "experiment-14", "experiment-18", "experiment-21",
    ]
    assert _out(client, "reads") == [
        "evaluation-09", "evaluation-11", "evaluation-21",
    ]

    winter = _detail(data, "campaign-winter")
    assert _out(winter, "rests on") == ["experiment-14"]
    assert _out(winter, "informed by") == ["evaluation-09"]
    assert _out(winter, "blocked by") == []
    assert _out(_detail(data, "experiment-14"), "produced") == ["evaluation-09"]

    roundtable = _detail(data, "campaign-roundtable")
    assert _out(roundtable, "rests on") == ["experiment-18"]
    assert _out(roundtable, "informed by") == ["evaluation-11"]
    assert _out(_detail(data, "experiment-18"), "produced") == ["evaluation-11"]

    sprint = _detail(data, "campaign-sprint")
    assert _out(sprint, "rests on") == ["experiment-21"]
    assert _out(sprint, "informed by") == []
    assert _out(sprint, "blocked by") == ["evaluation-21"]
    assert "Not scheduled" in sprint["entity"]["description"]
    assert _out(_detail(data, "experiment-21"), "produced") == ["evaluation-21"]


def test_type_links_put_the_client_story_on_the_next_ring():
    data = _load()
    linked = {
        (rel["src"], rel["predicate"], rel["dst"])
        for rel in data["summary"]["relations"]
    }
    assert ("type-client", "targets", "type-market") in linked
    assert ("type-client", "addresses", "type-persona") in linked
    assert ("type-client", "defines", "type-icp") in linked
    assert ("type-client", "reaches through", "type-channel") in linked
    assert ("type-client", "plans", "type-campaign") in linked
    assert ("type-client", "learns from", "type-experiment") in linked
    assert ("type-client", "reads", "type-evaluation") in linked
    assert ("type-campaign", "informed by", "type-evaluation") in linked
    assert ("type-campaign", "blocked by", "type-evaluation") in linked
    assert ("type-experiment", "produced", "type-evaluation") in linked
