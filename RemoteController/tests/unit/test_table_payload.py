from sync.chunking import build_transmission_parts
from sync.table_payload import assign_tables_to_parts, map_extractor_tables


def test_map_extractor_tables_basic():
    raw = [
        {
            "client_table_hint": "sales",
            "headers": ["Q", "Rev"],
            "rows": [["Q1", "10"]],
            "title": "Sales",
            "page": 2,
        }
    ]
    mapped = map_extractor_tables(raw)
    assert mapped[0]["headers"] == ["Q", "Rev"]
    assert mapped[0]["page"] == 2


def test_assign_tables_to_part_by_char_offset():
    text = "aaaa" + ("b" * 10)
    parts = [{"snippet": text[:8]}, {"snippet": text[8:]}]
    tables = [
        {
            "client_table_hint": "t1",
            "headers": ["C"],
            "rows": [["x"]],
            "_char_start": 9,
        }
    ]
    assign_tables_to_parts(parts, tables, text=text, part_max_chars=8)
    assert "tables" not in parts[0]
    assert parts[1]["tables"][0]["client_table_hint"] == "t1"


def test_build_transmission_parts_attaches_tables():
    text = "hello world"
    tables = [
        {
            "client_table_hint": "t1",
            "headers": ["H"],
            "rows": [["v"]],
            "_char_start": 0,
        }
    ]
    parts = build_transmission_parts(text, 20, tables=tables)
    assert len(parts) == 1
    assert parts[0]["tables"][0]["headers"] == ["H"]
