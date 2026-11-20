"""The firm's records as a SQL Server database, built the way such databases are.

Vendors of practice software do not publish their tables, so whoever connects
a client's database finds it as it is and has to read it without a manual. The
schema here is invented. Its traits are the ones old line-of-business
databases share, because a connector has to cope with exactly these:

- German names, one flat table per thing, relations implied by column names
  only: no foreign keys, as in software that speaks plain SELECT/INSERT/DELETE.
- Codes that point to lookup tables (``RolleID``, ``FristArtID``, ``LeistArt``).
- Change markers of three kinds: ``ErfasstAm``/``MutiertAm`` set by the
  software, a ``rowversion`` (``TS``) on the big tables, and nothing at all on
  the link tables. Time entries are often entered days after their date.
- Deletion of three kinds: a flag (``DOKUMENTE.Geloescht``, ``ADRESSEN.Inaktiv``),
  rows that are simply removed (``LEISTUNGEN``), and rows that never go.
- Old data types: ``datetime`` for every date, ``smallint`` -1/0 for yes/no in
  the oldest tables, ``varchar`` for codes, ``ntext`` for a remark, a 1900
  date where a date is unknown.
- Fields the firm defined itself, as rows (``ZUSATZFELDER``, ``ZUSATZFELDWERTE``).
- Tables that are not the firm's data: the software's users, counters and
  locks, a backup copy somebody made in 2019, a half-finished import.

``tables()`` is the one description of the database. The SQL files, the
per-table truth (``truth/records/*.jsonl``) and the catalogue of sources are
written from it, so they cannot disagree. The files hold no credentials; the
site's loader creates the logins.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from demo_kanzlei.namepool import DEADLINE_KINDS, PRACTICE_AREAS
from demo_kanzlei.office import ACTIVITY_CODES, CUSTOM_FIELDS, ROLES, activity_code
from demo_kanzlei.records import MIGRATION_USER, Records

BATCH = 500
UNKNOWN_DATE = "1900-01-01T00:00:00"
BACKUP_DAY = "2019-03-12"


@dataclass
class Table:
    name: str
    ddl: str
    columns: list[str]
    rows: list[tuple]
    key: list[str]
    file: str
    #: Columns a reader can use to find what changed; empty: read everything.
    changed_by: list[str] = field(default_factory=list)
    #: How a row disappears: ``flag:<column>``, ``removed``, ``never``.
    deleted_by: str = "never"
    #: What the table is to someone connecting the firm: ``records``, ``lookup``, ``software`` or ``leftover``.
    role: str = "records"
    note: str = ""


def lit(value) -> str:
    """A Python value as a T-SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(round(value, 2)) if isinstance(value, float) else str(value)
    return "N'" + str(value).replace("'", "''") + "'"


def _salt(*parts) -> int:
    return int(hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:8], 16)


def _at(day: str | None, *salt, first_hour: int = 8, hours: int = 10) -> str | None:
    """A time on ``day`` during office hours, fixed by ``salt``."""
    if not day:
        return None
    number = _salt(*salt)
    return f"{day[:10]}T{first_hour + number % hours:02d}:{number // 16 % 60:02d}:00"


def _midnight(day: str | None) -> str | None:
    return f"{day}T00:00:00" if day else None


def _plus(day: str, days: int, limit: str) -> str:
    return min((date.fromisoformat(day) + timedelta(days=days)).isoformat(), limit)


def area_ids(records: Records) -> dict[str, int]:
    """Practice areas get a number; the written matters bring a few of their own."""
    names = [area for area, _weight, _topics in PRACTICE_AREAS]
    for matter in records.matters:
        if matter.rechtsgebiet not in names:
            names.append(matter.rechtsgebiet)
    return {name: index for index, name in enumerate(names, start=1)}


def tables(records: Records, *, firm_name: str, autodoc_unc: str) -> list[Table]:
    today = records.today.isoformat()
    migration = f"{records.software_since}-01-09"
    areas = area_ids(records)
    roles = {name: index for index, name in enumerate(ROLES, start=1)}
    kinds = {name: index for index, name in enumerate(DEADLINE_KINDS, start=1)}
    secretaries = [e for e in records.employees if e.funktion.startswith("Sekretariat")]
    invoice_day = {i.nr: i.datum for i in records.invoices}
    matter_by_nr = {m.nr: m for m in records.matters}

    def desk(day: str, *salt) -> str:
        """Who in the secretariat was there to type it."""
        there = [e.kuerzel for e in secretaries if e.at_work(day)]
        return there[_salt(*salt) % len(there)] if there else MIGRATION_USER

    out: list[Table] = []

    def add(name: str, ddl: str, columns: list[str], rows: list[tuple], key: list[str], file: str, **kw) -> None:
        out.append(Table(name, ddl, columns, rows, key, file, **kw))

    # ---- the software's own tables -------------------------------------------------
    add("SYS_PARAMETER", "Name nvarchar(50) NOT NULL PRIMARY KEY, Wert nvarchar(400) NULL", ["Name", "Wert"], [
        ("Kanzlei", firm_name), ("ProgrammVersion", "7.4.2"), ("AutoDocPfad", autodoc_unc),
        ("AutoDocOrdnerGroesse", "500"), ("DatenuebernahmeAm", migration), ("Waehrung", "CHF"),
        ("LetzteSicherung", f"{today}T01:30:00"),
    ], ["Name"], "10_stammdaten.sql", role="software")
    year = today[:4]
    add("SYS_NUMMERN", "Bereich varchar(20) NOT NULL PRIMARY KEY, NaechsteNr int NOT NULL", ["Bereich", "NaechsteNr"], [
        ("ADRESSE", max((a.nr for a in records.addresses), default=10000) + 1),
        ("AKTE", max((m.nr for m in records.matters), default=0) + 1),
        ("DOKUMENT", max((d.dok_nr for d in records.documents), default=0) + 1),
        ("FRIST", max((d.nr for d in records.deadlines), default=0) + 1),
        ("JOURNAL", max((j.nr for j in records.journal), default=0) + 1),
        ("LEISTUNG", max((e.nr for e in records.time_entries), default=0) + 1),
        (f"RECHNUNG_{year}", max((i.nr for i in records.invoices if str(i.nr).startswith(year)), default=int(year) * 10000) + 1),
        ("TERMIN", max((a.nr for a in records.appointments), default=0) + 1),
    ], ["Bereich"], "10_stammdaten.sql", role="software", note="the next free number per kind; the software counts here")
    add("SYS_BENUTZER",
        "Login varchar(50) NOT NULL PRIMARY KEY, Kuerzel varchar(5) NULL, Profil varchar(20) NOT NULL, "
        "LetzteAnmeldung datetime NULL, Gesperrt smallint NOT NULL DEFAULT 0",
        ["Login", "Kuerzel", "Profil", "LetzteAnmeldung", "Gesperrt"],
        [(u.login, u.kuerzel, u.profil, u.letzte_anmeldung, -1 if u.gesperrt else 0) for u in records.software_users],
        ["Login"], "10_stammdaten.sql", role="software", note="yes/no is -1/0 here, as in the oldest tables")
    add("SYS_SPERREN",
        "Tabelle varchar(40) NOT NULL, Schluessel varchar(40) NOT NULL, Login varchar(50) NOT NULL, Seit datetime NOT NULL, "
        "CONSTRAINT PK_SYS_SPERREN PRIMARY KEY (Tabelle, Schluessel)",
        ["Tabelle", "Schluessel", "Login", "Seit"], [(l.tabelle, l.schluessel, l.login, l.seit) for l in records.locks],
        ["Tabelle", "Schluessel"], "10_stammdaten.sql", role="software", note="records open in somebody's window; changes by the minute")

    # ---- lookups --------------------------------------------------------------------
    add("MITARBEITER",
        "Kuerzel varchar(5) NOT NULL PRIMARY KEY, Name nvarchar(120) NOT NULL, Funktion nvarchar(80) NULL, Login varchar(50) NULL, "
        "Stundenansatz money NULL, Eintritt datetime NULL, Austritt datetime NULL, Aktiv smallint NOT NULL DEFAULT -1",
        ["Kuerzel", "Name", "Funktion", "Login", "Stundenansatz", "Eintritt", "Austritt", "Aktiv"],
        [(e.kuerzel, e.name, e.funktion, e.login, e.ansatz, _midnight(e.eintritt), _midnight(e.austritt), -1 if e.austritt is None else 0)
         for e in records.employees],
        ["Kuerzel"], "10_stammdaten.sql", deleted_by="flag:Aktiv", note="people who left stay, with Aktiv = 0")
    add("RECHTSGEBIETE", "RgID int NOT NULL PRIMARY KEY, Bezeichnung nvarchar(80) NOT NULL", ["RgID", "Bezeichnung"],
        [(number, name) for name, number in areas.items()], ["RgID"], "10_stammdaten.sql", role="lookup")
    add("ROLLEN", "RolleID int NOT NULL PRIMARY KEY, Bezeichnung nvarchar(40) NOT NULL", ["RolleID", "Bezeichnung"],
        [(number, name) for name, number in roles.items()], ["RolleID"], "10_stammdaten.sql", role="lookup")
    add("FRISTARTEN", "FristArtID int NOT NULL PRIMARY KEY, Bezeichnung nvarchar(40) NOT NULL", ["FristArtID", "Bezeichnung"],
        [(number, name) for name, number in kinds.items()], ["FristArtID"], "10_stammdaten.sql", role="lookup")
    add("LEISTUNGSARTEN",
        "LeistArt varchar(5) NOT NULL PRIMARY KEY, Bezeichnung nvarchar(60) NOT NULL, Verrechenbar smallint NOT NULL DEFAULT -1",
        ["LeistArt", "Bezeichnung", "Verrechenbar"], [(code, label, 0 if code == "ADM" else -1) for code, label in ACTIVITY_CODES],
        ["LeistArt"], "10_stammdaten.sql", role="lookup")
    add("ZUSATZFELDER",
        "FeldID int NOT NULL PRIMARY KEY, Tabelle varchar(40) NOT NULL, Bezeichnung nvarchar(80) NOT NULL, Datentyp char(1) NOT NULL",
        ["FeldID", "Tabelle", "Bezeichnung", "Datentyp"], [(number, "AKTEN", label, "T") for number, label in CUSTOM_FIELDS],
        ["FeldID"], "10_stammdaten.sql", role="lookup", note="fields the firm defined itself; their values are rows in ZUSATZFELDWERTE")

    # ---- addresses ------------------------------------------------------------------
    address_rows = []
    for a in records.addresses:
        changed = None
        if _salt("adr", a.nr) % 4 == 0:
            span = max(1, (records.today - date.fromisoformat(a.erfasst_am)).days)
            changed = _plus(a.erfasst_am, 1 + _salt("adr-m", a.nr) % span, today)
        address_rows.append((
            a.nr, a.kind, a.anrede or None, a.titel or None, a.vorname or None, a.name or None, a.firma or None,
            a.strasse or None, a.plz or None, a.ort or None, a.telefon or None, a.email or None, a.inaktiv,
            a.bemerkung or None, _at(a.erfasst_am, "adr-e", a.nr), a.erfasst_von,
            _at(changed, "adr-c", a.nr), desk(changed, "adr-d", a.nr) if changed else None,
        ))
    add("ADRESSEN",
        "AdrNr int NOT NULL PRIMARY KEY, AdrTyp char(1) NOT NULL, Anrede nvarchar(20) NULL, Titel nvarchar(30) NULL, "
        "Vorname nvarchar(60) NULL, Name nvarchar(120) NULL, Firma nvarchar(160) NULL, Strasse nvarchar(120) NULL, "
        "PLZ varchar(10) NULL, Ort nvarchar(80) NULL, Land char(2) NOT NULL DEFAULT 'CH', Telefon varchar(40) NULL, "
        "EMail varchar(160) NULL, Inaktiv bit NOT NULL DEFAULT 0, Bemerkung ntext NULL, ErfasstAm datetime NULL, "
        "ErfasstVon varchar(10) NULL, MutiertAm datetime NULL, MutiertVon varchar(10) NULL, TS rowversion",
        ["AdrNr", "AdrTyp", "Anrede", "Titel", "Vorname", "Name", "Firma", "Strasse", "PLZ", "Ort", "Telefon", "EMail",
         "Inaktiv", "Bemerkung", "ErfasstAm", "ErfasstVon", "MutiertAm", "MutiertVon"],
        address_rows, ["AdrNr"], "20_adressen.sql", changed_by=["TS", "MutiertAm", "ErfasstAm"], deleted_by="flag:Inaktiv",
        note="AdrTyp: P person, F firm, B public body, A lawyer. The same party can be in here twice")
    people = [a for a in records.addresses if a.kind == "P" and a.vorname][:30]
    add("tmp_Import_Adressen",
        "Zeile int NOT NULL, Name varchar(200) NULL, Strasse varchar(200) NULL, PLZOrt varchar(200) NULL, Telefon varchar(100) NULL, "
        "Importiert varchar(1) NULL",
        ["Zeile", "Name", "Strasse", "PLZOrt", "Telefon", "Importiert"],
        [(index, f"{a.name} {a.vorname}", a.strasse, f"{a.plz} {a.ort}".strip(), a.telefon or None, "J" if index % 3 else "N")
         for index, a in enumerate(people, start=1)],
        [], "20_adressen.sql", role="leftover", note="a spreadsheet import somebody started; not the firm's addresses")

    # ---- matters --------------------------------------------------------------------
    matter_rows = []
    for m in records.matters:
        migrated = int(m.aktenzeichen[:4]) < records.software_since
        closed = _midnight(m.abgeschlossen)
        if migrated and m.status == "A":
            closed = UNKNOWN_DATE  # taken over without its closing date
        entered = migration if migrated else m.eroeffnet
        changed = None if migrated else (m.abgeschlossen if m.status == "A" else (_plus(m.eroeffnet, 120, today) if m.status == "S" else None))
        matter_rows.append((
            m.nr, m.aktenzeichen, m.bezeichnung, areas[m.rechtsgebiet], m.status, _midnight(m.eroeffnet), closed,
            m.verantwortlich, m.sachbearbeiter, m.mandant, m.streitwert, m.ansatz, m.gesperrt, m.archiv_nr or None,
            m.bemerkung or None, _at(entered, "akte-e", m.nr), MIGRATION_USER if migrated else desk(entered, "akte-d", m.nr),
            _at(changed, "akte-c", m.nr), m.sachbearbeiter if changed else None,
        ))
    add("AKTEN",
        "AkteNr int NOT NULL PRIMARY KEY, Aktenzeichen varchar(20) NOT NULL, Bezeichnung nvarchar(250) NULL, RgID int NULL, "
        "Status char(1) NOT NULL, EroeffnetAm datetime NULL, AbgeschlossenAm datetime NULL, VerantwAnwalt varchar(5) NULL, "
        "Sachbearbeiter varchar(5) NULL, MandantAdrNr int NULL, Streitwert money NULL, Honoraransatz money NULL, "
        "Gesperrt bit NOT NULL DEFAULT 0, ArchivNr varchar(20) NULL, Bemerkung nvarchar(max) NULL, ErfasstAm datetime NULL, "
        "ErfasstVon varchar(10) NULL, MutiertAm datetime NULL, MutiertVon varchar(10) NULL, TS rowversion",
        ["AkteNr", "Aktenzeichen", "Bezeichnung", "RgID", "Status", "EroeffnetAm", "AbgeschlossenAm", "VerantwAnwalt",
         "Sachbearbeiter", "MandantAdrNr", "Streitwert", "Honoraransatz", "Gesperrt", "ArchivNr", "Bemerkung", "ErfasstAm",
         "ErfasstVon", "MutiertAm", "MutiertVon"],
        matter_rows, ["AkteNr"], "30_akten.sql", changed_by=["TS", "MutiertAm", "ErfasstAm"],
        note="Status: O open, A closed, S stayed. AbgeschlossenAm 1900-01-01 means unknown (matters from before the software). "
             "Gesperrt = 1: only the people in AKTENRECHTE may open it")
    add("AKTENBETEILIGTE",
        "AkteNr int NOT NULL, AdrNr int NOT NULL, RolleID int NOT NULL, CONSTRAINT PK_AKTENBETEILIGTE PRIMARY KEY (AkteNr, AdrNr, RolleID)",
        ["AkteNr", "AdrNr", "RolleID"], sorted({(p.akte_nr, p.adr_nr, roles[p.rolle]) for p in records.participants}),
        ["AkteNr", "AdrNr", "RolleID"], "30_akten.sql", deleted_by="removed", note="no change marker: read it whole")
    add("AKTENRECHTE",
        "AkteNr int NOT NULL, Kuerzel varchar(5) NOT NULL, Recht char(1) NOT NULL, Grund nvarchar(250) NULL, "
        "CONSTRAINT PK_AKTENRECHTE PRIMARY KEY (AkteNr, Kuerzel)",
        ["AkteNr", "Kuerzel", "Recht", "Grund"], [(r.akte_nr, r.kuerzel, r.recht, r.grund) for r in records.rights],
        ["AkteNr", "Kuerzel"], "30_akten.sql", deleted_by="removed",
        note="Recht: Z may open a locked matter, X is excluded from a matter. Who may read a matter is here and nowhere else")
    add("ZUSATZFELDWERTE",
        "FeldID int NOT NULL, Schluessel int NOT NULL, Wert nvarchar(250) NULL, CONSTRAINT PK_ZUSATZFELDWERTE PRIMARY KEY (FeldID, Schluessel)",
        ["FeldID", "Schluessel", "Wert"], [(v.feld, v.akte_nr, v.wert) for v in records.custom_values],
        ["FeldID", "Schluessel"], "30_akten.sql", deleted_by="removed", note="Schluessel is the AkteNr; FeldID 1 is the court's own number")
    backup = []
    for m in records.matters:
        if m.eroeffnet > BACKUP_DAY:
            continue
        still_open = m.abgeschlossen is None or m.abgeschlossen > BACKUP_DAY
        backup.append((m.nr, m.aktenzeichen, m.bezeichnung, "O" if still_open and m.status != "S" else m.status,
                       _midnight(m.eroeffnet), None if still_open else _midnight(m.abgeschlossen), m.verantwortlich))
    add("AKTEN_BACKUP_20190312",
        "AkteNr int NOT NULL, Aktenzeichen varchar(20) NOT NULL, Bezeichnung nvarchar(250) NULL, Status char(1) NOT NULL, "
        "EroeffnetAm datetime NULL, AbgeschlossenAm datetime NULL, VerantwAnwalt varchar(5) NULL",
        ["AkteNr", "Aktenzeichen", "Bezeichnung", "Status", "EroeffnetAm", "AbgeschlossenAm", "VerantwAnwalt"],
        backup, [], "30_akten.sql", role="leftover", note="a copy made before an update in 2019; its statuses are of that day")

    # ---- time, money, deadlines -----------------------------------------------------
    add("LEISTUNGEN",
        "LeistNr int NOT NULL PRIMARY KEY, AkteNr int NOT NULL, Datum datetime NOT NULL, Kuerzel varchar(5) NOT NULL, "
        "LeistArt varchar(5) NOT NULL, Stunden decimal(6,2) NOT NULL, Ansatz money NOT NULL, Betrag money NOT NULL, "
        "LeistText nvarchar(250) NULL, RechNr int NULL, ErfasstAm datetime NULL, MutiertAm datetime NULL, TS rowversion",
        ["LeistNr", "AkteNr", "Datum", "Kuerzel", "LeistArt", "Stunden", "Ansatz", "Betrag", "LeistText", "RechNr", "ErfasstAm",
         "MutiertAm"],
        [(e.nr, e.akte_nr, _midnight(e.datum), e.kuerzel, activity_code(e.text, e.art), e.stunden, e.ansatz, e.betrag, e.text,
          e.rechnung, _at(_plus(e.datum, (0, 0, 1, 3)[_salt("leist", e.nr) % 4], today), "leist-e", e.nr, first_hour=16, hours=3),
          _at(invoice_day.get(e.rechnung), "leist-c", e.nr) if e.rechnung else None)
         for e in records.time_entries],
        ["LeistNr"], "40_leistungen.sql", changed_by=["TS", "MutiertAm", "ErfasstAm"], deleted_by="removed",
        note="RechNr NULL: not billed yet. ErfasstAm is often days after Datum. A wrong entry is deleted, not flagged")
    add("RECHNUNGEN",
        "RechNr int NOT NULL PRIMARY KEY, AkteNr int NOT NULL, Art char(1) NOT NULL, Datum datetime NOT NULL, Honorar money NOT NULL, "
        "Auslagen money NOT NULL, MwStSatz decimal(4,1) NOT NULL, Total money NOT NULL, Status varchar(15) NOT NULL, "
        "BezahltAm datetime NULL, ErfasstAm datetime NULL, MutiertAm datetime NULL, TS rowversion",
        ["RechNr", "AkteNr", "Art", "Datum", "Honorar", "Auslagen", "MwStSatz", "Total", "Status", "BezahltAm", "ErfasstAm", "MutiertAm"],
        [(i.nr, i.akte_nr, i.art, _midnight(i.datum), i.honorar, i.auslagen, i.mwst_satz, i.total, i.status, _midnight(i.bezahlt_am),
          _at(i.datum, "rech-e", i.nr), _at(i.bezahlt_am, "rech-c", i.nr))
         for i in records.invoices],
        ["RechNr"], "50_rechnungen_fristen.sql", changed_by=["TS", "MutiertAm", "ErfasstAm"],
        note="Art: R invoice, V advance on costs. Status: offen, bezahlt, gemahnt, abgeschrieben")
    add("FRISTEN",
        "FristNr int NOT NULL PRIMARY KEY, AkteNr int NOT NULL, Datum datetime NOT NULL, FristArtID int NULL, FristText nvarchar(250) NULL, "
        "Verantwortlich varchar(5) NULL, Erledigt bit NOT NULL DEFAULT 0, ErledigtAm datetime NULL, ErfasstAm datetime NULL, "
        "MutiertAm datetime NULL",
        ["FristNr", "AkteNr", "Datum", "FristArtID", "FristText", "Verantwortlich", "Erledigt", "ErledigtAm", "ErfasstAm", "MutiertAm"],
        [(d.nr, d.akte_nr, _midnight(d.datum), kinds.get(d.art), d.text or None, d.verantwortlich, d.erledigt, _midnight(d.erledigt_am),
          _at(max(matter_by_nr[d.akte_nr].eroeffnet, _plus(d.datum, -30, today)), "frist-e", d.nr), _at(d.erledigt_am, "frist-c", d.nr))
         for d in records.deadlines],
        ["FristNr"], "50_rechnungen_fristen.sql", changed_by=["MutiertAm", "ErfasstAm"], deleted_by="removed",
        note="no rowversion here; MutiertAm is set when a deadline is marked done")
    add("TERMINE",
        "TerminNr int NOT NULL PRIMARY KEY, AkteNr int NULL, Beginn datetime NOT NULL, Ende datetime NOT NULL, Art char(1) NOT NULL, "
        "Betreff nvarchar(200) NULL, Ort nvarchar(120) NULL, Kuerzel varchar(5) NULL, ErfasstAm datetime NULL",
        ["TerminNr", "AkteNr", "Beginn", "Ende", "Art", "Betreff", "Ort", "Kuerzel", "ErfasstAm"],
        [(a.nr, a.akte_nr, a.beginn, a.ende, a.art, a.betreff, a.ort or None, a.kuerzel,
          _at(_plus(a.beginn[:10], -14, today), "termin-e", a.nr))
         for a in records.appointments],
        ["TerminNr"], "50_rechnungen_fristen.sql", changed_by=["ErfasstAm"], deleted_by="removed",
        note="Art: V hearing, B meeting, I internal (no matter)")
    add("JOURNAL",
        "JournalNr int NOT NULL PRIMARY KEY, AkteNr int NOT NULL, Zeit datetime NOT NULL, Kuerzel varchar(5) NULL, Art char(1) NOT NULL, "
        "JournalText nvarchar(max) NULL",
        ["JournalNr", "AkteNr", "Zeit", "Kuerzel", "Art", "JournalText"],
        [(j.nr, j.akte_nr, j.zeit, j.kuerzel, j.art, j.text) for j in records.journal],
        ["JournalNr"], "50_rechnungen_fristen.sql", changed_by=["JournalNr"],
        note="notes typed into the software; this text is in no file on the share. Rows are only added")

    # ---- the document index ---------------------------------------------------------
    add("DOKUMENTE",
        "DokNr int NOT NULL PRIMARY KEY, DokGuid uniqueidentifier NOT NULL, AkteNr int NULL, DokTyp nvarchar(60) NULL, "
        "Bezeichnung nvarchar(250) NULL, Dateiname nvarchar(260) NOT NULL, Ordner varchar(20) NOT NULL, ErstelltAm datetime NULL, "
        "ErstelltVon varchar(10) NULL, Groesse int NULL, Geloescht bit NOT NULL DEFAULT 0, GeloeschtAm datetime NULL, TS rowversion",
        ["DokNr", "DokGuid", "AkteNr", "DokTyp", "Bezeichnung", "Dateiname", "Ordner", "ErstelltAm", "ErstelltVon", "Groesse",
         "Geloescht", "GeloeschtAm"],
        [(d.dok_nr, d.guid, d.akte_nr, d.typ, d.bezeichnung, d.dateiname, d.ordner, d.erstellt_am, d.erstellt_von, d.groesse,
          d.geloescht, _at(_plus(d.erstellt_am[:10], 40, today), "dok-del", d.dok_nr) if d.geloescht else None)
         for d in records.documents],
        ["DokNr"], "60_dokumente.sql", changed_by=["TS", "ErstelltAm"], deleted_by="flag:Geloescht",
        note="one row per file in the AutoDoc store: <AutoDocPfad>\\<Ordner>\\<Dateiname>. Some files are gone without the flag")
    return out


VIEWS = """\
CREATE VIEW dbo.V_AKTEN_OFFEN AS
    SELECT a.AkteNr, a.Aktenzeichen, a.Bezeichnung, r.Bezeichnung AS Rechtsgebiet,
           a.EroeffnetAm, a.VerantwAnwalt, a.Sachbearbeiter,
           COALESCE(NULLIF(m.Firma, ''), m.Name) AS Mandant
      FROM dbo.AKTEN a
      LEFT JOIN dbo.RECHTSGEBIETE r ON r.RgID = a.RgID
      LEFT JOIN dbo.ADRESSEN m ON m.AdrNr = a.MandantAdrNr
     WHERE a.Status = 'O';
GO
CREATE VIEW dbo.V_FRISTEN_OFFEN AS
    SELECT f.FristNr, f.Datum, k.Bezeichnung AS Art, f.FristText, f.Verantwortlich, a.Aktenzeichen, a.Bezeichnung
      FROM dbo.FRISTEN f
      JOIN dbo.AKTEN a ON a.AkteNr = f.AkteNr
      LEFT JOIN dbo.FRISTARTEN k ON k.FristArtID = f.FristArtID
     WHERE f.Erledigt = 0;
GO
"""

INDEXES = """\
CREATE UNIQUE INDEX IX_AKTEN_Aktenzeichen ON dbo.AKTEN (Aktenzeichen);
CREATE INDEX IX_AKTEN_Mandant ON dbo.AKTEN (MandantAdrNr);
CREATE INDEX IX_DOKUMENTE_Akte ON dbo.DOKUMENTE (AkteNr);
CREATE INDEX IX_LEISTUNGEN_Akte ON dbo.LEISTUNGEN (AkteNr);
CREATE INDEX IX_RECHNUNGEN_Akte ON dbo.RECHNUNGEN (AkteNr);
CREATE INDEX IX_FRISTEN_Datum ON dbo.FRISTEN (Datum);
GO
"""


def _insert(table: Table) -> str:
    out: list[str] = []
    head = f"INSERT INTO dbo.{table.name} ({', '.join(table.columns)}) VALUES\n"
    for start in range(0, len(table.rows), BATCH):
        chunk = table.rows[start : start + BATCH]
        out.append(head + ",\n".join("(" + ", ".join(lit(v) for v in row) + ")" for row in chunk) + ";\nGO\n")
    return "".join(out)


def write_sql(all_tables: list[Table], dest: Path, *, database: str) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.sql"):
        old.unlink()
    files: dict[str, str] = {
        "00_schema.sql": (
            f"IF DB_ID(N'{database}') IS NULL\n    CREATE DATABASE [{database}] COLLATE Latin1_General_CI_AS;\nGO\n"
            f"USE [{database}];\nGO\n"
            + "".join(f"CREATE TABLE dbo.{t.name} (\n    " + t.ddl.replace(", ", ",\n    ") + "\n);\n" for t in all_tables)
            + "GO\n" + INDEXES + VIEWS
        )
    }
    for table in all_tables:
        files.setdefault(table.file, f"USE [{database}];\nGO\nSET NOCOUNT ON;\nGO\n")
        files[table.file] += _insert(table)
    written = []
    for name in sorted(files):
        path = dest / name
        path.write_text(files[name], encoding="utf-8", newline="\n")
        written.append(path)
    return written


def write_record_truth(all_tables: list[Table], dest: Path) -> dict[str, int]:
    """One JSON line per row of every table, as it should be read. Truth: never on the share."""
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.jsonl"):
        old.unlink()
    counts: dict[str, int] = {}
    for table in all_tables:
        with (dest / f"{table.name}.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for row in table.rows:
                handle.write(json.dumps(dict(zip(table.columns, row)), ensure_ascii=False) + "\n")
        counts[table.name] = len(table.rows)
    return counts


def catalogue(all_tables: list[Table]) -> list[dict]:
    """What someone connecting the database should know about each table."""
    return [
        {
            "table": t.name,
            "role": t.role,
            "rows": len(t.rows),
            "key": t.key,
            "changed_by": t.changed_by,
            "deleted_by": t.deleted_by,
            "columns": t.columns + (["TS"] if "TS rowversion" in t.ddl else []),
            "note": t.note,
            "truth": f"records/{t.name}.jsonl",
        }
        for t in all_tables
    ]
