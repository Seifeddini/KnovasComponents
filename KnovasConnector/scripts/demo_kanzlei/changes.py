"""One working day at the firm, as a change to its sources (plan item DEMO-53).

A source that never changes proves only a first read. This plays the day after
the stand-in's "today": people book time (some of it late), close a matter,
tick off deadlines, file the scanned post, write a note, save over a document,
remove one in the software and delete another in the Explorer.

Written next to the build, never into the share:

- ``<work>/site/changes/tag-1/``: ``db.sql`` (what the practice software would
  have written), ``share/`` (files that are new or saved over), ``delete.txt``
  (files that go). The site's ``apply-changes.sh`` applies them.
- ``<work>/truth/changes/tag-1/``: ``records/<TABLE>.jsonl`` (every table as it
  is afterwards), ``manifest.jsonl`` (every file afterwards) and ``changes.json``
  (what was added, changed and removed, the question the day makes answerable,
  and the rights of the new files).

``db.sql`` is the difference between the tables before and after, so the truth
and the statements cannot disagree.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from demo_kanzlei.directory import build_acl, build_directory
from demo_kanzlei.estate import Entry, _guid, _moment, _Share, bucket_name
from demo_kanzlei.filenames import letterhead_block, make_filename, unser_zeichen
from demo_kanzlei.models import World
from demo_kanzlei.office import Appointment, JournalEntry, RecordLock
from demo_kanzlei.records import Deadline, DocRecord, Records, TimeEntry
from demo_kanzlei.sqlgen import Table, lit, tables, write_record_truth
from demo_kanzlei.truth import Access

NAME = "tag-1"
HERO = "2024-017"


def next_workday(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def diff_sql(before: list[Table], after: list[Table], database: str) -> tuple[str, dict]:
    """The statements that turn ``before`` into ``after``, and the same as a summary by key."""
    out = [f"USE [{database}];\nGO\nSET NOCOUNT ON;\nGO\n"]
    summary: dict = {}
    old_tables = {t.name: t for t in before}
    for table in after:
        old = old_tables[table.name]
        if not table.key:
            assert table.rows == old.rows, f"{table.name} has no key and must not change"
            continue
        index = [table.columns.index(k) for k in table.key]

        def key_of(row: tuple) -> tuple:
            return tuple(row[i] for i in index)

        def where(key: tuple) -> str:
            return " AND ".join(f"{name} = {lit(value)}" for name, value in zip(table.key, key))

        was = {key_of(row): row for row in old.rows}
        now = {key_of(row): row for row in table.rows}
        inserted = [key for key in now if key not in was]
        deleted = [key for key in was if key not in now]
        updated = {key: [c for c, a, b in zip(table.columns, was[key], now[key]) if a != b]
                   for key in now if key in was and was[key] != now[key]}
        for key in deleted:
            out.append(f"DELETE FROM dbo.{table.name} WHERE {where(key)};\n")
        for key, columns in updated.items():
            row = dict(zip(table.columns, now[key]))
            out.append(f"UPDATE dbo.{table.name} SET " + ", ".join(f"{c} = {lit(row[c])}" for c in columns) + f" WHERE {where(key)};\n")
        for key in inserted:
            out.append(f"INSERT INTO dbo.{table.name} ({', '.join(table.columns)}) VALUES (" + ", ".join(lit(v) for v in now[key]) + ");\n")
        if inserted or deleted or updated:
            out.append("GO\n")
            summary[table.name] = {
                "inserted": [list(k) for k in inserted],
                "updated": {"/".join(str(v) for v in k): cols for k, cols in updated.items()},
                "deleted": [list(k) for k in deleted],
            }
    return "".join(out), summary


def build_day(world: World, cfg: dict, records: Records, entries: list[Entry], bodies: dict[str, str],
              before: list[Table], out_root: Path, work_root: Path) -> dict:
    estate = cfg["estate"]
    firm = estate["firm_folder"]
    day = next_workday(records.today).isoformat()
    after = copy.deepcopy(records)
    staff = {s.initials: s for s in world.firm.staff}
    site_dir = work_root / "site" / "changes" / NAME
    truth_dir = work_root / "truth" / "changes" / NAME
    for folder in (site_dir, truth_dir):
        if folder.exists():
            import shutil

            shutil.rmtree(folder)
    share = _Share(site_dir / "share", build_directory(world, cfg), build_acl(world, cfg))
    hero = after.matter(HERO)
    by_path = {e.path: e for e in entries}
    removed: list[str] = []
    changed: list[str] = []

    # --- time: booked today, and some booked today for last week -----------------------------------
    open_matters = [m for m in after.matters if m.status == "O" and int(m.aktenzeichen[:4]) >= after.software_since]
    busy = [hero, *[m for m in open_matters if m.nr != hero.nr][-11:]]
    # Two entries booked on the wrong matter are taken out again: the rows are gone.
    wrong = [e for e in after.time_entries if e.rechnung is None and e.akte_nr != hero.nr][-2:]
    after.time_entries = [e for e in after.time_entries if e not in wrong]
    number = max(e.nr for e in records.time_entries)
    for index, matter in enumerate(busy):
        late = index % 4 == 3
        when = (date.fromisoformat(day) - timedelta(days=5)).isoformat() if late else day
        for who, hours, text in ((matter.sachbearbeiter, 1.5, "Studium Akten"), (matter.verantwortlich, 0.5, "Besprechung intern")):
            number += 1
            after.time_entries.append(TimeEntry(number, matter.nr, when, who, "H", hours, matter.ansatz,
                                                round(hours * matter.ansatz, 2), text, None, erfasst=day))

    # --- master data ----------------------------------------------------------------------------------
    for address in [a for a in after.addresses if a.kind == "P" and a.telefon and not a.duplicate_of][-3:]:
        address.telefon = "+41 44 555 01 " + f"{address.nr % 90 + 10}"
        address.mutiert = day
    closing = next(m for m in reversed(open_matters) if not m.in_world and m.nr not in {b.nr for b in busy})
    closing.status, closing.abgeschlossen = "A", day
    for deadline in [d for d in after.deadlines if not d.erledigt and d.datum >= day][:2]:
        deadline.erledigt, deadline.erledigt_am = True, day
    unpaid = next((i for i in after.invoices if i.status == "offen"), None)
    if unpaid:
        unpaid.status, unpaid.bezahlt_am = "bezahlt", day

    # --- the scanned court order is filed to its matter ----------------------------------------------
    size = int(estate["bucket_size"])
    dok_nr = max(d.dok_nr for d in after.documents)

    def file_document(key: str, typ: str, ext: str) -> tuple[int, str]:
        nonlocal dok_nr
        dok_nr += 1
        name = make_filename(_guid(key), HERO, typ, ext)
        return dok_nr, f"{firm}/Praxis/Autodoc/{bucket_name(dok_nr, size)}/{name}"

    def index_row(nr: int, key: str, path: str, typ: str, title: str, author: str, length: int) -> None:
        after.documents.append(DocRecord(nr, _guid(key).upper(), hero.nr, typ, title, path.rsplit("/", 1)[1], path.rsplit("/", 2)[1],
                                         _moment(day, key).strftime("%Y-%m-%dT%H:%M:%S"), author, length))

    scan = f"{firm}/Gemeinsam/Scan/Scan_20261116_083412.pdf"
    added: list[Entry] = []
    if scan in by_path:
        nr, path = file_document(f"{NAME}:verfuegung", "Verfuegung", "pdf")
        entry = share.put(path, (out_root / scan).read_bytes(), day=day, kind="autodoc", written=True, matter=HERO,
                          doc_type="Verfuegung", category="Gerichtliches", dok_nr=nr)
        index_row(nr, f"{NAME}:verfuegung", path, "Verfuegung", "Verfügung Bezirksgericht vom 12.11.2026", "MS", entry.size)
        added.append(entry)
        removed.append(scan)
        after.appointments.append(Appointment(max(a.nr for a in after.appointments) + 1, hero.nr, "2027-01-14T09:00:00",
                                              "2027-01-14T11:00:00", "V", "Instruktionsverhandlung Meierhans Bau AG ./. Rüegg",
                                              "Bezirksgericht Winterthur", hero.verantwortlich, erfasst=day))
        after.deadlines.append(Deadline(max(d.nr for d in after.deadlines) + 1, hero.nr, "2027-01-07", "Wiedervorlage",
                                        "Vorbereitung Instruktionsverhandlung", hero.sachbearbeiter, False, None, erfasst=day))

    # --- a new note in the matter: the one fact of the day that is in no other file -------------------
    counsel, assistant = staff[hero.verantwortlich], staff[hero.sachbearbeiter]
    floor = "CHF 150'000"
    note = (
        f"{world.firm.name}\n\n"
        + letterhead_block(unser_zeichen(HERO, counsel.initials, assistant.initials), HERO, "Meierhans Bau AG ./. Rüegg")
        + f"\nAKTENNOTIZ vom {day}\n\nTelefon mit Frau Meierhans zum Vergleichsangebot der Gegenseite.\n\n"
        f"Die Mandantin lehnt das Angebot ab. Sie ist zu einem Vergleich bereit, wenn sie mindestens {floor} erhält "
        "und Rüegg die Gerichtskosten trägt. Darunter will sie die Instruktionsverhandlung abwarten.\n\n"
        f"{assistant.name}\n"
    )
    nr, path = file_document(f"{NAME}:aktennotiz", "Aktennotiz", "txt")
    entry = share.put(path, note.encode("utf-8"), day=day, kind="autodoc", written=True, matter=HERO, doc_type="Aktennotiz",
                      category="Interna", dok_nr=nr)
    index_row(nr, f"{NAME}:aktennotiz", path, "Aktennotiz", "Aktennotiz Vergleich", assistant.initials, entry.size)
    added.append(entry)
    after.journal.append(JournalEntry(max(j.nr for j in after.journal) + 1, hero.nr, f"{day}T10:40:00", assistant.initials, "T",
                                      "Mandantin lehnt Vergleichsangebot ab, siehe Aktennotiz."))

    # --- a document saved over: same file name, new content -----------------------------------------
    # The matter's list of open points gets a line; it stays the same file, in whatever format it is.
    target = next((e for e in entries if e.matter == HERO and e.doc_type == "Pendenzenliste" and e.mess is None and e.plan_id), None)
    if target:
        body = bodies[target.plan_id] + f"\nNachtrag {day}: Instruktionsverhandlung am 14. Januar 2027, 09.00 Uhr, Bezirksgericht Winterthur.\n"
        entry = share.render(target.path, body, fmt=target.fmt, title=f"Pendenzenliste — {HERO}", day=day, author=assistant.name,
                             kind="autodoc", matter=HERO, doc_type=target.doc_type, category=target.category,
                             plan_id=target.plan_id, dok_nr=target.dok_nr)
        added.append(entry)
        changed.append(target.path)
        for row in after.documents:
            if row.dok_nr == target.dok_nr:
                row.groesse = entry.size

    # --- one document removed in the software, one deleted by hand ----------------------------------
    duplicate = next((e for e in entries if e.mess == "duplicate" and e.dok_nr), None)
    if duplicate:
        removed.append(duplicate.path)
        for row in after.documents:
            if row.dok_nr == duplicate.dok_nr:
                row.geloescht, row.on_share = True, False
    by_hand = next((e for e in entries if e.kind == "autodoc" and e.mess is None and not e.syncable and e.dok_nr), None)
    if by_hand:
        removed.append(by_hand.path)
        for row in after.documents:
            if row.dok_nr == by_hand.dok_nr:
                row.on_share = False

    # --- the software's own tables move too ------------------------------------------------------------
    for user in after.software_users:
        if not user.gesperrt and user.login != "admin":
            user.letzte_anmeldung = f"{day}T07:{40 + len(user.login) % 20:02d}:00"
    after.locks = [RecordLock("AKTEN", str(hero.nr), assistant.sam, f"{day}T10:38:00")]

    # --- write it down ---------------------------------------------------------------------------------
    after_tables = tables(after, firm_name=world.firm.name,
                          autodoc_unc=f"\\\\SRV-DC01\\{estate['share']}\\{firm}\\Praxis\\Autodoc")
    # A soft delete carries the day it happened, not a derived one.
    for table in after_tables:
        if table.name == "DOKUMENTE":
            flag, when, key = (table.columns.index(c) for c in ("Geloescht", "GeloeschtAm", "DokNr"))
            before_flags = {row[key]: row[flag] for row in next(t for t in before if t.name == "DOKUMENTE").rows}
            table.rows = [tuple(f"{day}T16:05:00" if i == when and row[flag] and not before_flags.get(row[key]) else v
                                for i, v in enumerate(row)) for row in table.rows]
    sql, summary = diff_sql(before, after_tables, estate["database"])
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "db.sql").write_text(sql, encoding="utf-8", newline="\n")
    (site_dir / "delete.txt").write_text("".join(path + "\n" for path in removed), encoding="utf-8", newline="\n")
    write_record_truth(after_tables, truth_dir / "records")
    gone = set(removed) | set(changed)
    manifest = sorted([asdict(e) for e in entries if e.path not in gone] + [asdict(e) for e in added], key=lambda e: e["path"])
    (truth_dir / "manifest.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in manifest) + "\n", encoding="utf-8")
    access = Access(world, after, cfg)
    new_files = [e for e in added if e.path not in changed]
    result = {
        "name": NAME,
        "day": day,
        "what_happened": [
            "Time was booked on twelve matters; on every fourth of them for a day last week.",
            "Two time entries were removed again.",
            "Three addresses got a new telephone number.",
            "One matter was closed, two deadlines ticked off, one invoice paid.",
            "The scanned court order was filed to matter 2024-017: a new file in the store, the scan gone, an appointment and a deadline entered.",
            "A note was written in matter 2024-017. It holds the one fact of the day that is in no other file.",
            "One document was saved over under its old name.",
            "One document was removed in the software (row flagged, file gone); one file was deleted by hand (the row stays).",
        ],
        "database": summary,
        "share": {
            "added": [e.path for e in new_files],
            "changed": changed,
            "deleted": removed,
        },
        "question": {
            "id": f"q-{NAME}-vergleich-untergrenze",
            "question": "Welchen Betrag will die Meierhans Bau AG im Vergleich mit Rüegg mindestens erhalten?",
            "kind": "fact", "expected_points": [floor], "supporting_pointers": [entry_path(added, "Aktennotiz")],
            "asked_as": "", "in_mvp_set": False,
            "note": "No file holds the answer before this day. Afterwards it must be found; asked by nm it must not be.",
        },
        "rights_of_new_files": [
            {"person": person.id, "pointer": e.path, "may_read": access.decide(person, e)[0], "rule": access.decide(person, e)[1]}
            for e in new_files for person in world.firm.staff
        ],
    }
    (truth_dir / "changes.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"statements": sql.count(";\n"), "tables": len(summary), "added": len(new_files), "changed": len(changed), "deleted": len(removed)}


def entry_path(added: list[Entry], doc_type: str) -> str:
    return next(e.path for e in added if e.doc_type == doc_type)
