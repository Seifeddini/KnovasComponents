from knovas_discovery import effort as eff
from knovas_discovery.catalogue import load_pricing

ALL = ["record_tables", "foreign_keys", "change_markers", "removed_rows", "rights_in_source", "custom_fields",
       "unknown_product", "volume", "authentication"]


def facts(**over):
    f = {"record_tables": 12, "foreign_keys": 0, "record_tables_without_change_marker": 5,
         "record_tables_without_delete_flag": 9, "rights_tables": 1, "custom_field_tables": 2, "product": None,
         "record_rows": 93000, "authentication": "mixed"}
    f.update(over)
    return f


def points(f):
    return {d["driver"]: d["points"] for d in eff.compute(ALL, f, [], load_pricing(), True)["drivers"]}


def test_each_driver_by_the_design_table():
    assert points(facts()) == {"record_tables": 12, "foreign_keys": 3, "change_markers": 2, "removed_rows": 3,
                               "rights_in_source": 5, "custom_fields": 2, "unknown_product": 5, "volume": 0,
                               "authentication": 0}
    assert points(facts(record_tables=20))["record_tables"] == 12            # at most 12
    assert points(facts(foreign_keys=4))["foreign_keys"] == 0
    assert points(facts(record_tables_without_change_marker=0))["change_markers"] == 0      # all
    assert points(facts(record_tables_without_change_marker=12))["change_markers"] == 5     # none
    assert points(facts(record_tables_without_delete_flag=0))["removed_rows"] == 0
    assert points(facts(rights_tables=0))["rights_in_source"] == 0
    assert points(facts(custom_field_tables=0))["custom_fields"] == 0
    assert points(facts(product="winjur"))["unknown_product"] == 0
    assert points(facts(record_rows=1_000_001))["volume"] == 2
    assert points(facts(record_rows=1_000_000))["volume"] == 0
    assert points(facts(authentication="windows"))["authentication"] == 2


def test_volume_falls_back_to_the_table_list():
    f = facts()
    del f["record_rows"]
    f["table_list"] = [{"role": "records", "rows": 900_000}, {"role": "records", "rows": 200_000},
                       {"role": "leftover", "rows": 5_000_000}]
    assert points(f)["volume"] == 2


def test_total_and_effort_class():
    e = eff.compute(ALL, facts(), [{"points": 1, "reason": "x"}], load_pricing(), True)
    assert e["points"] == 32 + 1 and e["effort_class"] == "XL" and e["mode"] == "computed"
    classes = load_pricing()["effort_classes"]
    assert [eff.effort_class(p, classes) for p in (0, 8, 9, 16, 17, 26, 27)] == ["S", "S", "M", "M", "L", "L", "XL"]


def test_manual_effort():
    e = eff.compute(["manual"], None, [], load_pricing(), False)
    assert e["points"] is None and e["note"] == "effort: set by the consultant in review.yaml"
    e = eff.compute(["manual"], None, [{"points": 7, "reason": "API study"}], load_pricing(), False)
    assert (e["mode"], e["points"], e["effort_class"]) == ("manual", 7, "S")


def test_prices():
    pricing = load_pricing()
    assert eff.price("A", None, pricing) == "price not set"
    assert eff.price("C", "XL", pricing) == "price not set"
    assert eff.price("C", None, pricing) == "price not set"
    assert eff.price("D", None, pricing) == "not offered"
    pricing["classes"]["C"]["XL"] = 48000
    pricing["classes"]["B"]["chf"] = 3500
    assert eff.price("C", "XL", pricing) == "CHF 48,000"
    assert eff.price("B", None, pricing) == "CHF 3,500"
