import copy

from knovas_discovery.catalogue import check, load_catalogue, load_effort_rules
from knovas_discovery.classify import classify, match
from knovas_discovery.conditions import holds, render
from knovas_discovery.probes import all_facts
from knovas_discovery.util import load_yaml


def test_shipped_catalogue_passes_the_check():
    assert check(load_catalogue(), load_effort_rules(), all_facts()) == []


def test_on_is_a_key_not_a_boolean():
    assert load_yaml("on: fileshare\nflag: yes\nok: true") == {"on": "fileshare", "flag": "yes", "ok": True}


def test_check_finds_problems():
    cat = copy.deepcopy(load_catalogue())
    cat["entries"].append(copy.deepcopy(cat["entries"][0]))                                 # duplicate key
    cat["entries"][1]["detect"][0]["when"][0]["op"] = "~="                                  # unknown operator
    cat["entries"][2]["detect"][0]["when"][0]["fact"] = "no_such_fact"                      # fact no probe produces
    cat["entries"][3]["class"] = "E"
    cat["checks"][0]["text"] = "{nothing_here} files"
    problems = "\n".join(check(cat, load_effort_rules(), all_facts()))
    assert "entry fileshare: duplicate key" in problems
    assert "unknown operator '~='" in problems
    assert "no m365 probe produces the fact 'no_such_fact'" in problems
    assert "class must be one of" in problems
    assert "text names 'nothing_here'" in problems


def test_conditions():
    facts = {"a": 3, "p": {"share": 0.4}, "names": ["AKTEN", "ADRESSEN"], "flag": True, "s": "WinJur", "n": None}
    assert holds({"fact": "a", "op": ">=", "value": 3}, facts)
    assert holds({"fact": "p.share", "op": "<", "value": 0.5}, facts)
    assert holds({"fact": "names", "op": "contains_any", "value": ["akten", "X"]}, facts)
    assert not holds({"fact": "names", "op": "contains_all", "value": ["AKTEN", "X"]}, facts)
    assert holds({"fact": "flag", "op": "==", "value": True}, facts)
    assert not holds({"fact": "flag", "op": ">=", "value": 1}, facts)          # a boolean is not a number here
    assert holds({"fact": "s", "op": "==", "value": "winjur"}, facts)
    assert not holds({"fact": "missing", "op": "==", "value": 1}, facts)
    assert not holds({"fact": "n", "op": "!=", "value": 1}, facts)             # null never holds
    assert not holds({"fact": "s", "op": ">", "value": 1}, facts)              # does not compare
    assert render("{p.share:.0%} of {a} ({missing})", facts) == "40% of 3 (?)"


def share_result(key, **over):
    facts = {"files": 1000, "bytes": 1, "folders": 10, "max_depth": 1, "extensions": {}, "connector_readable_files": 900,
             "connector_readable_share": 0.9, "age": {"newest_days": 1, "oldest_days": 10, "bands": {}},
             "refused_folders": 0, "largest_file_bytes": 1, "listing": {"seconds": 1, "entries_per_second": 5000},
             "first_read_forecast": {"files_to_upload": 900, "hours_at_4_per_minute": 3.8},
             "patterns": {"autodoc_names_share": 0.0, "range_folders": 0, "office_lock_files": 0, "outlook_archives": 0,
                          "dictation_files": 0, "scanner_files": 0, "no_extension_files": 0, "long_paths": 0,
                          "empty_files": 0}}
    for k, v in over.items():
        if k in facts["patterns"]:
            facts["patterns"][k] = v
        else:
            facts[k] = v
    return {"key": key, "status": "ok", "error": None, "facts": facts}


def findings(shares=(), dbs=(), dirs=(), m365=(), software=()):
    return {"tool_version": "t", "scanned_at": "2026-11-20T12:00:00Z", "client": {"key": "c"},
            "software": list(software),
            "probes": {"environment": None, "fileshares": list(shares), "directories": list(dirs),
                       "databases": list(dbs), "m365": list(m365)}}


def test_group_priority_keeps_the_store_not_the_share():
    entries = load_catalogue()["entries"]
    store = share_result("s:store", autodoc_names_share=0.95, dictation_files=4, scanner_files=4)["facts"]
    keys = [e["key"] for e, _ in match("fileshare", store, entries)]
    assert keys == ["autodoc-store", "scanner-output", "dictations"]          # fileshare dropped by its group
    stray = share_result("s:store", autodoc_names_share=0.95, scanner_files=1)["facts"]
    assert [e["key"] for e, _ in match("fileshare", stray, entries)] == ["autodoc-store"], \
        "one stray file named like a scan does not make a scan folder"
    plain = share_result("s:common")["facts"]
    assert [e["key"] for e, _ in match("fileshare", plain, entries)] == ["fileshare"]


def test_classification_of_results():
    db = {"key": "p:DB", "status": "ok", "error": None,
          "facts": {"tables": 20, "vocabulary": {"legal_practice": 6}, "foreign_keys": 0, "record_tables": 10,
                    "rights_tables": 1, "leftover_tables": 2, "table_names": []}}
    other_db = {"key": "p:X", "status": "ok", "error": None, "facts": {"tables": 3, "vocabulary": {"legal_practice": 0}}}
    # A root that refuses the account: what the fileshare probe reports (shares and speed null, not 0).
    empty = share_result("s:locked", files=0, folders=0, refused_folders=1, connector_readable_share=None,
                         autodoc_names_share=None, listing={"seconds": 0.0, "entries_per_second": None},
                         age={"newest_days": None, "oldest_days": None, "bands": {}})
    failed = {"key": "ad", "status": "failed", "error": "ldap_bind_failed:invalidCredentials", "facts": {}}
    r = classify(findings(shares=[share_result("s:common", connector_readable_share=0.2), empty], dbs=[db, other_db],
                          dirs=[failed], software=[{"category": "practice", "product": "unknown"},
                                                   {"category": "time", "product": "vertec"},
                                                   {"category": "other", "product": "fancyapp"}]),
                 load_catalogue(), load_effort_rules())
    by_id = {s["id"]: s for s in r["sources"]}
    assert by_id["fileshare@s:common"]["class"] == "A"
    assert [c["key"] for c in by_id["fileshare@s:common"]["checks"]] == ["mostly-unread-formats"]
    assert by_id["fileshare@s:common"]["checks"][0]["text"].startswith("Only 20% of the files")
    assert by_id["no-access@s:locked"]["class"] == "D"
    assert [c["key"] for c in by_id["no-access@s:locked"]["checks"]] == ["folders-refuse-account"]
    assert by_id["no-access@s:locked"]["questions"][0].startswith("`s:locked`: is it right")
    assert by_id["practice-database@p:DB"]["class"] == "C"
    assert "database-unknown@p:DB" not in by_id                                  # group database-kind
    assert by_id["database-unknown@p:X"]["class"] == "C"
    assert {c["key"] for c in by_id["practice-database@p:DB"]["checks"]} == {"no-foreign-keys", "rights-in-database",
                                                                            "leftover-tables"}
    assert by_id["vertec@intake:2:time:vertec"]["origin"] == "intake"
    assert by_id["vertec@intake:2:time:vertec"]["effort"]["note"] == "effort: set by the consultant in review.yaml"
    assert by_id["unknown@intake:3:other:fancyapp"]["class"] == "C"
    named = {(n["category"], n["product"]): n for n in r["named_by_client"]}
    assert named[("practice", "unknown")]["result"] == "confirmed by the scan"
    assert named[("time", "vertec")]["result"] == "named by the client, not scanned"
    assert r["probes_not_ok"] == [{"probe": "directory", "key": "ad", "status": "failed",
                                   "error": "ldap_bind_failed:invalidCredentials"}]
    assert any("directory probe of ad failed" in q for q in r["open_questions"])
    assert [s["class"] for s in r["sources"]] == sorted(s["class"] for s in r["sources"])
    assert r["counts"] == {"A": 1, "B": 0, "C": 4, "D": 1}


def test_review_overrides_class_adds_points_and_notes():
    db = {"key": "p:DB", "status": "ok", "error": None,
          "facts": {"tables": 20, "vocabulary": {"legal_practice": 6}, "record_tables": 3,
                    "record_tables_without_change_marker": 0, "foreign_keys": 2, "product": None,
                    "authentication": "mixed", "record_rows": 10}}
    review = {"sources": {"practice-database@p:DB": {"effort": [{"points": 4, "reason": "vendor export to study"}],
                                                     "notes": ["Vendor contact known."]},
                          "fileshare@s:common": {"class": "D", "reason": "the client wants it out of scope"},
                          "nope@x": {"class": "A"}},
              "notes": ["First discovery."]}
    r = classify(findings(shares=[share_result("s:common")], dbs=[db]), load_catalogue(), load_effort_rules(), review)
    by_id = {s["id"]: s for s in r["sources"]}
    share = by_id["fileshare@s:common"]
    assert share["class"] == "D" and share["class_from"] == "review" and "out of scope" in share["notes"][0]
    e = by_id["practice-database@p:DB"]["effort"]
    assert e["points"] == 3 + 0 + 0 + 0 + 0 + 0 + 5 + 0 + 0 + 4
    assert e["effort_class"] == "M"
    assert by_id["practice-database@p:DB"]["notes"] == ["Vendor contact known."]
    assert r["review"] == {"used": True, "notes": ["First discovery."], "unknown_ids": ["nope@x"]}
