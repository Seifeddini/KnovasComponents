"""One working day as a change to the firm's sources (plan item DEMO-53, scenario S5).

The day is the difference between two states of the same firm. These tests check that the statements, the files
and the truth describe the same difference, and that what is new is marked the way a reader looks for it.
"""
import json
from pathlib import Path

import pytest

from demo_kanzlei.changes import NAME, diff_sql
from demo_kanzlei.pipeline import run_pipeline
from demo_kanzlei.sqlgen import Table

CONFIG = Path(__file__).resolve().parents[3] / "scripts" / "demo_kanzlei" / "world.toml"
DAY = "2026-11-19"


@pytest.fixture(scope="module")
def day(tmp_path_factory):
    base = tmp_path_factory.mktemp("day")
    out, work = base / "share", base / "work"
    assert run_pipeline(cfg_path=CONFIG, out_root=out, work_root=work, pilot=True, skip_zefix=True,
                        until_stage="changes", layout="client") == 0
    truth = work / "truth" / "changes" / NAME

    def lines(path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    return {
        "out": out,
        "site": work / "site" / "changes" / NAME,
        "changes": json.loads((truth / "changes.json").read_text(encoding="utf-8")),
        "before": {row["path"]: row for row in lines(work / "manifest.jsonl")},
        "after": {row["path"]: row for row in lines(truth / "manifest.jsonl")},
        "table": lambda name, when="after": lines((truth if when == "after" else work / "truth") / "records" / f"{name}.jsonl"),
        "rights_before": (work / "truth" / "rights.csv").read_text(encoding="utf-8"),
    }


def test_statements_are_the_difference_between_two_states():
    before = [Table("T", "", ["Nr", "Wert"], [(1, "a"), (2, "b"), (3, "c")], ["Nr"], "x.sql"),
              Table("LOG", "", ["Zeile"], [("x",)], [], "x.sql")]
    after = [Table("T", "", ["Nr", "Wert"], [(1, "a"), (2, "B'"), (4, None)], ["Nr"], "x.sql"),
             Table("LOG", "", ["Zeile"], [("x",)], [], "x.sql")]
    sql, summary = diff_sql(before, after, "KanzleiDB")
    assert "DELETE FROM dbo.T WHERE Nr = 3;" in sql
    assert "UPDATE dbo.T SET Wert = N'B''' WHERE Nr = 2;" in sql
    assert "INSERT INTO dbo.T (Nr, Wert) VALUES (4, NULL);" in sql
    assert summary == {"T": {"inserted": [[4]], "updated": {"2": ["Wert"]}, "deleted": [[3]]}}


def test_a_table_without_a_key_must_not_change():
    before = [Table("LOG", "", ["Zeile"], [("x",)], [], "x.sql")]
    after = [Table("LOG", "", ["Zeile"], [("y",)], [], "x.sql")]
    with pytest.raises(AssertionError):
        diff_sql(before, after, "KanzleiDB")


def test_the_day_is_the_next_working_day_and_touches_database_and_share(day):
    changes = day["changes"]
    assert changes["day"] == DAY
    assert set(changes["database"]) >= {"LEISTUNGEN", "AKTEN", "ADRESSEN", "FRISTEN", "DOKUMENTE", "TERMINE", "JOURNAL", "SYS_NUMMERN"}
    assert changes["share"]["added"] and changes["share"]["changed"] and changes["share"]["deleted"]
    sql = (day["site"] / "db.sql").read_text(encoding="utf-8")
    assert sql.startswith("USE [KanzleiDB];")
    assert "DELETE FROM dbo.LEISTUNGEN" in sql and "INSERT INTO dbo.LEISTUNGEN" in sql and "UPDATE dbo.AKTEN" in sql


def test_files_and_manifest_describe_the_same_change(day):
    share = day["changes"]["share"]
    assert set(day["after"]) == (set(day["before"]) - set(share["deleted"])) | set(share["added"])
    assert (day["site"] / "delete.txt").read_text(encoding="utf-8").split() == share["deleted"]
    delivered = {p.relative_to(day["site"] / "share").as_posix() for p in (day["site"] / "share").rglob("*") if p.is_file()}
    assert delivered == set(share["added"]) | set(share["changed"])
    for path in share["changed"]:
        assert day["after"][path]["sha256"] != day["before"][path]["sha256"], "saved over: same name, other content"
    for path in share["deleted"]:
        assert (day["out"] / path).exists(), "only files that were there can go"


def test_new_rows_carry_the_day_so_a_reader_by_entry_time_finds_them(day):
    old = {row["LeistNr"] for row in day["table"]("LEISTUNGEN", "before")}
    new = [row for row in day["table"]("LEISTUNGEN") if row["LeistNr"] not in old]
    assert len(new) == 24
    assert all(row["ErfasstAm"].startswith(DAY) for row in new)
    late = [row for row in new if row["Datum"][:10] < DAY]
    assert late, "some of today's entries are for last week: a reader by Datum would miss them"
    removed = old - {row["LeistNr"] for row in day["table"]("LEISTUNGEN")}
    assert len(removed) == 2, "rows that are simply gone"
    closed = [row for row in day["table"]("AKTEN") if (row["AbgeschlossenAm"] or "").startswith(DAY)]
    assert len(closed) == 1 and closed[0]["Status"] == "A" and closed[0]["MutiertAm"].startswith(DAY)
    moved = [row for row in day["table"]("ADRESSEN") if (row["MutiertAm"] or "").startswith(DAY)]
    assert len(moved) == 3


def test_document_index_follows_the_share(day):
    index = {row["Dateiname"]: row for row in day["table"]("DOKUMENTE")}
    counters = {row["Bereich"]: row["NaechsteNr"] for row in day["table"]("SYS_NUMMERN")}
    assert counters["DOKUMENT"] == max(row["DokNr"] for row in index.values()) + 1
    for path in day["changes"]["share"]["added"]:
        row = index[Path(path).name]
        assert row["ErstelltAm"].startswith(DAY) and not row["Geloescht"]
    flagged = [row for row in index.values() if (row["GeloeschtAm"] or "").startswith(DAY)]
    assert len(flagged) == 1 and flagged[0]["Geloescht"], "removed in the software: flagged today"
    gone = {Path(path).name for path in day["changes"]["share"]["deleted"]}
    assert flagged[0]["Dateiname"] in gone
    by_hand = [index[name] for name in gone if name in index and not index[name]["Geloescht"]]
    assert len(by_hand) == 1, "deleted in the Explorer: the row still says the file is there"


def test_the_day_makes_one_question_answerable_and_keeps_the_barrier(day):
    question = day["changes"]["question"]
    pointer = question["supporting_pointers"][0]
    assert pointer in day["after"] and pointer not in day["before"]
    point = question["expected_points"][0]
    assert point in (day["site"] / "share" / pointer).read_text(encoding="utf-8")
    for path, row in day["before"].items():
        if row["fmt"] == "txt" and row["size"]:
            assert point.encode("utf-8") not in (day["out"] / path).read_bytes(), "the answer was in no file before"
    rights = {(r["person"], r["pointer"]): r for r in day["changes"]["rights_of_new_files"]}
    assert rights[("nm", pointer)]["may_read"] is False and rights[("nm", pointer)]["rule"] == "conflict:2024-017"
    assert rights[("lz", pointer)]["may_read"] is True
    assert len(rights) == 13 * len(day["changes"]["share"]["added"])
