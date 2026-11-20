"""The firm's records as T-SQL files for the stand-in's SQL Server.

The files hold no credentials. The site's loader runs them in name order with
``sqlcmd -f 65001`` (they are UTF-8) and creates the logins itself.

The schema is what a client's practice database tends to look like: German
names, one flat table per thing, relations that are only implied by column
names (no foreign keys), a table of parameters. It is invented.
"""
from __future__ import annotations

from pathlib import Path

from demo_kanzlei.namepool import PRACTICE_AREAS
from demo_kanzlei.records import Records

BATCH = 500

SCHEMA = """\
CREATE TABLE dbo.SYS_PARAMETER (
    Name        nvarchar(50)  NOT NULL PRIMARY KEY,
    Wert        nvarchar(400) NULL
);
CREATE TABLE dbo.MITARBEITER (
    Kuerzel       nvarchar(5)   NOT NULL PRIMARY KEY,
    Name          nvarchar(120) NOT NULL,
    Funktion      nvarchar(80)  NULL,
    Login         nvarchar(50)  NULL,
    Stundenansatz money         NULL,
    Eintritt      date          NULL,
    Austritt      date          NULL,
    Aktiv         bit           NOT NULL DEFAULT 1
);
CREATE TABLE dbo.RECHTSGEBIETE (
    RgID        int          NOT NULL PRIMARY KEY,
    Bezeichnung nvarchar(80) NOT NULL
);
CREATE TABLE dbo.ADRESSEN (
    AdrNr      int           NOT NULL PRIMARY KEY,
    AdrTyp     char(1)       NOT NULL,          -- P Person, F Firma, B Behoerde, A Anwalt
    Anrede     nvarchar(20)  NULL,
    Titel      nvarchar(30)  NULL,
    Vorname    nvarchar(60)  NULL,
    Name       nvarchar(120) NULL,
    Firma      nvarchar(160) NULL,
    Strasse    nvarchar(120) NULL,
    PLZ        nvarchar(10)  NULL,
    Ort        nvarchar(80)  NULL,
    Land       char(2)       NOT NULL DEFAULT 'CH',
    Telefon    nvarchar(40)  NULL,
    EMail      nvarchar(160) NULL,
    Inaktiv    bit           NOT NULL DEFAULT 0,
    Bemerkung  nvarchar(max) NULL,
    ErfasstAm  datetime      NULL,
    ErfasstVon nvarchar(10)  NULL
);
CREATE TABLE dbo.AKTEN (
    AkteNr          int           NOT NULL PRIMARY KEY,
    Aktenzeichen    nvarchar(20)  NOT NULL,
    Bezeichnung     nvarchar(250) NULL,
    RgID            int           NULL,
    Status          char(1)       NOT NULL,     -- O offen, A abgeschlossen, S sistiert
    EroeffnetAm     date          NULL,
    AbgeschlossenAm date          NULL,
    VerantwAnwalt   nvarchar(5)   NULL,
    Sachbearbeiter  nvarchar(5)   NULL,
    MandantAdrNr    int           NULL,
    Streitwert      money         NULL,
    Honoraransatz   money         NULL,
    Gesperrt        bit           NOT NULL DEFAULT 0,
    ArchivNr        nvarchar(20)  NULL,
    Bemerkung       nvarchar(max) NULL
);
CREATE UNIQUE INDEX IX_AKTEN_Aktenzeichen ON dbo.AKTEN (Aktenzeichen);
CREATE INDEX IX_AKTEN_Mandant ON dbo.AKTEN (MandantAdrNr);
CREATE TABLE dbo.AKTENBETEILIGTE (
    AkteNr int          NOT NULL,
    AdrNr  int          NOT NULL,
    Rolle  nvarchar(30) NOT NULL,
    CONSTRAINT PK_AKTENBETEILIGTE PRIMARY KEY (AkteNr, AdrNr, Rolle)
);
CREATE TABLE dbo.AKTENRECHTE (
    AkteNr  int           NOT NULL,
    Kuerzel nvarchar(5)   NOT NULL,
    Recht   char(1)       NOT NULL,             -- Z Zugriff, X Ausschluss
    Grund   nvarchar(250) NULL,
    CONSTRAINT PK_AKTENRECHTE PRIMARY KEY (AkteNr, Kuerzel)
);
CREATE TABLE dbo.DOKUMENTE (
    DokNr       int              NOT NULL PRIMARY KEY,
    DokGuid     uniqueidentifier NOT NULL,
    AkteNr      int              NULL,
    DokTyp      nvarchar(60)     NULL,
    Bezeichnung nvarchar(250)    NULL,
    Dateiname   nvarchar(260)    NOT NULL,
    Ordner      nvarchar(20)     NOT NULL,
    ErstelltAm  datetime         NULL,
    ErstelltVon nvarchar(10)     NULL,
    Groesse     int              NULL
);
CREATE INDEX IX_DOKUMENTE_Akte ON dbo.DOKUMENTE (AkteNr);
CREATE TABLE dbo.LEISTUNGEN (
    LeistNr   int           NOT NULL PRIMARY KEY,
    AkteNr    int           NOT NULL,
    Datum     date          NOT NULL,
    Kuerzel   nvarchar(5)   NOT NULL,
    Art       char(1)       NOT NULL,           -- H Honorar, A Auslagen
    Stunden   decimal(6,2)  NOT NULL,
    Ansatz    money         NOT NULL,
    Betrag    money         NOT NULL,
    LeistText nvarchar(250) NULL,
    RechNr    int           NULL                -- NULL = noch nicht verrechnet
);
CREATE INDEX IX_LEISTUNGEN_Akte ON dbo.LEISTUNGEN (AkteNr);
CREATE TABLE dbo.RECHNUNGEN (
    RechNr    int          NOT NULL PRIMARY KEY,
    AkteNr    int          NOT NULL,
    Art       char(1)      NOT NULL,            -- R Rechnung, V Kostenvorschuss
    Datum     date         NOT NULL,
    Honorar   money        NOT NULL,
    Auslagen  money        NOT NULL,
    MwStSatz  decimal(4,1) NOT NULL,
    Total     money        NOT NULL,
    Status    nvarchar(15) NOT NULL,            -- offen, bezahlt, gemahnt, abgeschrieben
    BezahltAm date         NULL
);
CREATE INDEX IX_RECHNUNGEN_Akte ON dbo.RECHNUNGEN (AkteNr);
CREATE TABLE dbo.FRISTEN (
    FristNr        int           NOT NULL PRIMARY KEY,
    AkteNr         int           NOT NULL,
    Datum          date          NOT NULL,
    Art            nvarchar(30)  NULL,
    FristText      nvarchar(250) NULL,
    Verantwortlich nvarchar(5)   NULL,
    Erledigt       bit           NOT NULL DEFAULT 0,
    ErledigtAm     date          NULL
);
CREATE INDEX IX_FRISTEN_Datum ON dbo.FRISTEN (Datum);
GO
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
    SELECT f.FristNr, f.Datum, f.Art, f.FristText, f.Verantwortlich, a.Aktenzeichen, a.Bezeichnung
      FROM dbo.FRISTEN f
      JOIN dbo.AKTEN a ON a.AkteNr = f.AkteNr
     WHERE f.Erledigt = 0;
GO
"""


def lit(value) -> str:
    """A Python value as a T-SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(round(value, 2)) if isinstance(value, float) else str(value)
    return "N'" + str(value).replace("'", "''") + "'"


def _insert(table: str, columns: list[str], rows: list[tuple]) -> str:
    out: list[str] = []
    head = f"INSERT INTO dbo.{table} ({', '.join(columns)}) VALUES\n"
    for start in range(0, len(rows), BATCH):
        chunk = rows[start : start + BATCH]
        out.append(head + ",\n".join("(" + ", ".join(lit(v) for v in row) + ")" for row in chunk) + ";\nGO\n")
    return "".join(out)


def _file(database: str, body: str) -> str:
    return f"USE [{database}];\nGO\nSET NOCOUNT ON;\nGO\n{body}"


def area_ids(records: Records) -> dict[str, int]:
    """Practice areas get a number; the written matters bring a few of their own."""
    names = [area for area, _weight, _topics in PRACTICE_AREAS]
    for matter in records.matters:
        if matter.rechtsgebiet not in names:
            names.append(matter.rechtsgebiet)
    return {name: index for index, name in enumerate(names, start=1)}


def _moment(day: str | None, salt: int) -> str | None:
    """A working-hours time for a day, fixed by the row's number."""
    if not day:
        return None
    return f"{day}T{8 + salt % 10:02d}:{(salt * 7) % 60:02d}:00"


def write_sql(records: Records, dest: Path, *, database: str, firm_name: str, autodoc_unc: str) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.sql"):
        old.unlink()
    areas = area_ids(records)
    files: dict[str, str] = {}
    files["00_schema.sql"] = (
        f"IF DB_ID(N'{database}') IS NULL\n    CREATE DATABASE [{database}] COLLATE Latin1_General_CI_AS;\nGO\n"
        f"USE [{database}];\nGO\n{SCHEMA}"
    )
    files["10_stammdaten.sql"] = _file(
        database,
        _insert("SYS_PARAMETER", ["Name", "Wert"], [
            ("Kanzlei", firm_name),
            ("ProgrammVersion", "7.4.2"),
            ("AutoDocPfad", autodoc_unc),
            ("AutoDocOrdnerGroesse", "500"),
            ("DatenuebernahmeAm", f"{records.software_since}-01-09"),
            ("Waehrung", "CHF"),
        ])
        + _insert("MITARBEITER", ["Kuerzel", "Name", "Funktion", "Login", "Stundenansatz", "Eintritt", "Austritt", "Aktiv"], [
            (e.kuerzel, e.name, e.funktion, e.login, e.ansatz, e.eintritt, e.austritt, e.austritt is None)
            for e in records.employees
        ])
        + _insert("RECHTSGEBIETE", ["RgID", "Bezeichnung"], [(number, name) for name, number in areas.items()]),
    )
    files["20_adressen.sql"] = _file(
        database,
        _insert(
            "ADRESSEN",
            ["AdrNr", "AdrTyp", "Anrede", "Titel", "Vorname", "Name", "Firma", "Strasse", "PLZ", "Ort", "Telefon",
             "EMail", "Inaktiv", "Bemerkung", "ErfasstAm", "ErfasstVon"],
            [
                (a.nr, a.kind, a.anrede or None, a.titel or None, a.vorname or None, a.name or None, a.firma or None,
                 a.strasse or None, a.plz or None, a.ort or None, a.telefon or None, a.email or None, a.inaktiv,
                 a.bemerkung or None, _moment(a.erfasst_am, a.nr), a.erfasst_von)
                for a in records.addresses
            ],
        ),
    )
    files["30_akten.sql"] = _file(
        database,
        _insert(
            "AKTEN",
            ["AkteNr", "Aktenzeichen", "Bezeichnung", "RgID", "Status", "EroeffnetAm", "AbgeschlossenAm",
             "VerantwAnwalt", "Sachbearbeiter", "MandantAdrNr", "Streitwert", "Honoraransatz", "Gesperrt", "ArchivNr",
             "Bemerkung"],
            [
                (m.nr, m.aktenzeichen, m.bezeichnung, areas[m.rechtsgebiet], m.status, m.eroeffnet, m.abgeschlossen,
                 m.verantwortlich, m.sachbearbeiter, m.mandant, m.streitwert, m.ansatz, m.gesperrt, m.archiv_nr or None,
                 m.bemerkung or None)
                for m in records.matters
            ],
        )
        + _insert("AKTENBETEILIGTE", ["AkteNr", "AdrNr", "Rolle"],
                  sorted({(p.akte_nr, p.adr_nr, p.rolle) for p in records.participants}))
        + _insert("AKTENRECHTE", ["AkteNr", "Kuerzel", "Recht", "Grund"],
                  [(r.akte_nr, r.kuerzel, r.recht, r.grund) for r in records.rights]),
    )
    files["40_leistungen.sql"] = _file(
        database,
        _insert(
            "LEISTUNGEN",
            ["LeistNr", "AkteNr", "Datum", "Kuerzel", "Art", "Stunden", "Ansatz", "Betrag", "LeistText", "RechNr"],
            [(e.nr, e.akte_nr, e.datum, e.kuerzel, e.art, e.stunden, e.ansatz, e.betrag, e.text, e.rechnung)
             for e in records.time_entries],
        ),
    )
    files["50_rechnungen_fristen.sql"] = _file(
        database,
        _insert(
            "RECHNUNGEN",
            ["RechNr", "AkteNr", "Art", "Datum", "Honorar", "Auslagen", "MwStSatz", "Total", "Status", "BezahltAm"],
            [(i.nr, i.akte_nr, i.art, i.datum, i.honorar, i.auslagen, i.mwst_satz, i.total, i.status, i.bezahlt_am)
             for i in records.invoices],
        )
        + _insert(
            "FRISTEN",
            ["FristNr", "AkteNr", "Datum", "Art", "FristText", "Verantwortlich", "Erledigt", "ErledigtAm"],
            [(d.nr, d.akte_nr, d.datum, d.art, d.text or None, d.verantwortlich, d.erledigt, d.erledigt_am)
             for d in records.deadlines],
        ),
    )
    files["60_dokumente.sql"] = _file(
        database,
        _insert(
            "DOKUMENTE",
            ["DokNr", "DokGuid", "AkteNr", "DokTyp", "Bezeichnung", "Dateiname", "Ordner", "ErstelltAm", "ErstelltVon",
             "Groesse"],
            [(d.dok_nr, d.guid, d.akte_nr, d.typ, d.bezeichnung, d.dateiname, d.ordner, d.erstellt_am, d.erstellt_von,
              d.groesse)
             for d in records.documents],
        ),
    )
    written = []
    for name, text in files.items():
        path = dest / name
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written
