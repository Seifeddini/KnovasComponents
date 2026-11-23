"""What the firm keeps on its common drive besides matter files.

A client has no system that says how it works. It has instructions, checklists,
minutes and lists in ordinary documents. These are the ones the stand-in has.

``PROCESSES`` is used twice: the instructions on the share are written from it,
and the process truth file is computed from it. So the two cannot disagree.

The texts are drafts until a Swiss lawyer has read them (plan item DEMO-42).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from demo_kanzlei.models import World


@dataclass(frozen=True)
class Step:
    key: str
    text: str
    #: Event kinds whose document shows the step was done. Empty: the step
    #: leaves no document on the share (it is done in the practice software).
    evidence: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Process:
    key: str
    title: str
    purpose: str
    owner: str
    steps: tuple[Step, ...]


def _step(key: str, text: str, *kinds: str) -> Step:
    return Step(key, text, frozenset(kinds))


PROCESSES: tuple[Process, ...] = (
    Process(
        "mandatsannahme",
        "Mandatsannahme",
        "Kein Mandat wird bearbeitet, bevor Konflikte geprüft, die Vertragspartei identifiziert und das Honorar vereinbart ist.",
        "verantwortlicher Partner",
        (
            _step("konfliktpruefung", "Konfliktprüfung gegen alle Parteien durchführen und das Ergebnis in der Akte ablegen.", "konflikt"),
            _step("mandatsvereinbarung", "Mandatsvereinbarung mit Honoraransatz unterzeichnen lassen.", "mandatsannahme"),
            _step("vollmacht", "Vollmacht einholen.", "vollmacht"),
            _step("kostenvorschuss", "Kostenvorschuss in Rechnung stellen; Arbeit über die Erstberatung hinaus erst nach Eingang.", "kostenvorschuss"),
            _step("akte-eroeffnen", "Akte in der Praxissoftware eröffnen, Beteiligte erfassen, Zugriff bei Bedarf sperren."),
            _step("bestaetigung", "Mandatsbestätigung an die Mandantschaft senden.", "email_mandant"),
        ),
    ),
    Process(
        "fristenkontrolle",
        "Fristenkontrolle",
        "Keine Frist wird verpasst. Jede Frist steht in der Praxissoftware und wird von zwei Personen kontrolliert.",
        "sachbearbeitende Anwältin / sachbearbeitender Anwalt, Sekretariat",
        (
            _step("frist-erfassen", "Frist am Tag des Eingangs in der Praxissoftware erfassen (Art, Datum, verantwortliche Person)."),
            _step("vorfrist", "Wiedervorlage fünf Arbeitstage vor Ablauf setzen."),
            _step("pendenzenliste", "Frist in die Pendenzenliste der Akte aufnehmen.", "pendenzen"),
            _step("vier-augen", "Das Sekretariat kontrolliert jeden Montag die offenen Fristen der Woche und meldet sie dem Team."),
            _step("erledigen", "Frist erst als erledigt markieren, wenn die Eingabe nachweislich versandt ist."),
        ),
    ),
    Process(
        "rechtsschrift",
        "Rechtsschrift erstellen und einreichen",
        "Rechtsschriften verlassen die Kanzlei geprüft, vollständig und rechtzeitig.",
        "sachbearbeitende Anwältin / sachbearbeitender Anwalt",
        (
            _step("sachverhalt", "Sachverhalt mit der Mandantschaft klären und in einer Aktennotiz festhalten.", "aktennotiz", "telefonnotiz"),
            _step("aussergerichtlich", "Aussergerichtliche Erledigung versuchen (Abmahnung oder Schreiben an die Gegenseite).", "abmahnung", "brief"),
            _step("entwurf", "Entwurf an die Mandantschaft zur Durchsicht senden.", "brief_mandant", "email_mandant"),
            _step("gegenlesen", "Gegenlesen durch den verantwortlichen Partner."),
            _step("einreichen", "Rechtsschrift mit Beilagenverzeichnis einreichen.", "klage", "stellungnahme", "replik"),
            _step("versandnachweis", "Versandnachweis (Begleitmail oder Postquittung) in der Akte ablegen.", "email_gericht", "email_behoerde"),
        ),
    ),
    Process(
        "fakturierung",
        "Fakturierung",
        "Leistungen werden laufend erfasst und spätestens halbjährlich in Rechnung gestellt.",
        "verantwortlicher Partner, Buchhaltung",
        (
            _step("leistungen", "Leistungen täglich in der Praxissoftware erfassen; Auszug zur Honorarnote ablegen.", "leistung"),
            _step("honorarnote", "Honorarnote erstellen, vom Partner freigeben lassen.", "honorarnote"),
            _step("versand", "Honorarnote an die Mandantschaft senden.", "email_mandant"),
            _step("zahlungskontrolle", "Zahlungseingang kontrollieren; nach 30 Tagen mahnen."),
        ),
    ),
    Process(
        "aktenabschluss",
        "Aktenabschluss",
        "Abgeschlossene Akten sind vollständig, abgerechnet und zehn Jahre auffindbar.",
        "sachbearbeitende Anwältin / sachbearbeitender Anwalt, Sekretariat",
        (
            _step("erledigung", "Erledigung (Urteil, Vergleich oder Abschreibung) in der Akte ablegen.", "urteil", "vergleich"),
            _step("abschlussmitteilung", "Abschlussmitteilung an die Mandantschaft senden; Originale zurückgeben.", "email_mandant"),
            _step("schlussrechnung", "Schlussrechnung stellen.", "honorarnote"),
            _step("archivieren", "Akte in der Praxissoftware abschliessen; Aufbewahrungsfrist zehn Jahre."),
        ),
    ),
)

#: The firm's own words. A new person does not know them; Knovas should.
TERMS: tuple[tuple[str, str], ...] = (
    ("AZ", "Aktenzeichen, Jahr und Laufnummer der Akte, zum Beispiel 2024-017"),
    ("UZ", "Unser Zeichen: Aktenzeichen und die Kürzel von Anwalt und Sachbearbeitung"),
    ("GA", "Gegenanwalt oder Gegenanwältin"),
    ("GP", "Gegenpartei"),
    ("M", "Mandantschaft"),
    ("KV", "Kostenvorschuss"),
    ("HN", "Honorarnote"),
    ("WV", "Wiedervorlage"),
    ("PL", "Pendenzenliste der Akte"),
    ("PS", "Partnersitzung, jeden zweiten Montag"),
    ("KP", "Konfliktprüfung"),
    ("MV", "Mandatsvereinbarung"),
    ("VM", "Vollmacht"),
    ("AN", "Aktennotiz"),
    ("TN", "Telefonnotiz"),
    ("BV", "Beilagenverzeichnis"),
    ("RS", "Rechtsschrift"),
    ("KA", "Klageantwort"),
    ("HV", "Hauptverhandlung"),
    ("SV", "Schlichtungsverhandlung"),
    ("FRA", "Friedensrichteramt"),
    ("BG", "Bezirksgericht"),
    ("OG", "Obergericht"),
    ("HG", "Handelsgericht"),
    ("BGer", "Bundesgericht"),
    ("uP", "unentgeltliche Prozessführung"),
    ("RSV", "Rechtsschutzversicherung"),
    ("Sperrakte", "Akte, die in der Praxissoftware nur das Mandatsteam öffnen kann"),
    ("Schranke", "Informationsschranke: eine Person ist von einer Akte ausgeschlossen"),
    ("grüne Akte", "Akte ohne laufende Frist"),
    ("rote Akte", "Akte mit Frist in den nächsten zehn Tagen"),
    ("Posteingang", "Scans der Tagespost im Ordner Scan, bis das Sekretariat sie der Akte zuweist"),
    ("AutoDoc", "Dokumentenablage der Praxissoftware; Dateien nie von Hand hineinkopieren"),
    ("Stammkunde", "Mandantschaft mit mehr als drei Akten; Honorar nach Rahmenvereinbarung"),
    ("Pikett", "Erreichbarkeit eines Anwalts ausserhalb der Bürozeit für dringende Fristen"),
    ("Montagsliste", "Liste der offenen Fristen der Woche, vom Sekretariat jeden Montag verteilt"),
    ("Semesterabrechnung", "Honorarnoten per Ende Juni und Ende Dezember"),
    ("Kleinspesen", "Pauschale von drei Prozent des Honorars für Porti, Kopien und Telefon"),
    ("Ablage G", "das gemeinsame Laufwerk der Kanzlei"),
    ("Ablage H", "das persönliche Laufwerk"),
    ("Substitution", "Vertretung eines abwesenden Anwalts durch ein Teammitglied, in der Akte zu vermerken"),
    ("Schlusskontrolle", "Durchsicht der Akte durch den Partner vor dem Aktenabschluss"),
)


@dataclass
class FirmDoc:
    """One document outside the practice software's store."""

    rel_path: str  # below the firm's folder, with / separators
    fmt: str  # docx, pdf, txt, scan_pdf
    title: str
    body: str
    date: str
    author_id: str
    #: Who may read it: a directory group, or ``owner:<login>``.
    access: str
    facts: dict[str, str] = field(default_factory=dict)


def _head(world: World, title: str, day: str) -> str:
    f = world.firm
    return f"{f.name}\n{f.street}, {f.zip_code} {f.seat}\n\n{title}\nStand {day}\n\n"


def _weisung(world: World, process: Process, number: int, day: str) -> str:
    lines = [_head(world, f"WEISUNG {number}: {process.title.upper()}", day)]
    lines.append(f"Zweck\n{process.purpose}\n")
    lines.append(f"Verantwortlich\n{process.owner}\n")
    lines.append("Ablauf")
    for index, step in enumerate(process.steps, start=1):
        lines.append(f"{index}. {step.text}")
    lines.append(
        "\nAbweichungen von diesem Ablauf sind in einer Aktennotiz zu begründen und dem "
        "verantwortlichen Partner zu melden.\n"
    )
    return "\n".join(lines)


def _glossar(world: World, day: str) -> str:
    lines = [_head(world, "KANZLEI-ABC: UNSERE ABKÜRZUNGEN UND BEGRIFFE", day)]
    lines += [f"{term}: {meaning}." for term, meaning in TERMS]
    return "\n".join(lines) + "\n"


def _arbeitsvertrag(world: World, staff, day: str) -> str:
    wage = {"partner": "gemäss Partnerschaftsvertrag", "associate": "CHF 9'400 brutto pro Monat, dreizehn Monatslöhne",
            "sekretariat": "CHF 6'300 brutto pro Monat, dreizehn Monatslöhne"}[staff.role]
    return (
        _head(world, "ARBEITSVERTRAG", day)
        + f"zwischen {world.firm.name}, {world.firm.seat}, und {staff.name}.\n\n"
        f"1. Funktion: {staff.title}. Eintritt am {staff.joined}.\n"
        f"2. Lohn: {wage}.\n"
        "3. Arbeitszeit: 42 Stunden pro Woche bei einem vollen Pensum. Ferien: fünf Wochen.\n"
        "4. Berufsgeheimnis: Es gilt Art. 321 StGB und Art. 13 BGFA, auch nach dem Austritt.\n"
        "5. Kündigungsfrist: drei Monate auf das Ende eines Monats.\n\n"
        "Dieses Dokument ist vertraulich und nur den Partnern zugänglich.\n"
    )


def _mag(world: World, staff, partner, day: str) -> str:
    return (
        _head(world, "MITARBEITERGESPRÄCH", day)
        + f"Mitarbeiterin / Mitarbeiter: {staff.name}\nGeführt von: {partner.name}\n\n"
        "Rückblick: Die Arbeitsqualität ist gut, die Leistungserfassung erfolgt nicht immer am selben Tag.\n"
        "Ziele: Leistungen täglich erfassen; ein eigenes Referat an der internen Weiterbildung.\n"
        "Lohn: Anpassung per 1. Januar wird an der Partnersitzung besprochen.\n\n"
        "Vertraulich. Ablage nur im Ordner Personal.\n"
    )


def _schranke(world: World, day: str) -> str:
    return (
        _head(world, "AKTENNOTIZ INFORMATIONSSCHRANKE", day)
        + "Betrifft: Akte 2024-017, Meierhans Bau AG ./. Rüegg\n\n"
        "MLaw Nadia Marti ist am 1. September 2025 von der Kanzlei eingetreten, welche die Gegenpartei "
        "Rüegg Haustechnik in dieser Sache vertritt. Sie hat dort an der Sache nicht mitgearbeitet.\n\n"
        "Beschluss der Partner: Frau Marti ist von der Akte 2024-017 ausgeschlossen. Der Ausschluss ist in der "
        "Praxissoftware eingetragen. Sie erhält keine Auskunft aus dieser Akte und nimmt an Besprechungen dazu "
        "nicht teil. Die Mandantin ist informiert und einverstanden.\n\n"
        "Dr. iur. Markus Bär\n"
    )


def _partnersitzung(world: World, day: str, items: list[str]) -> str:
    lines = [_head(world, "PROTOKOLL PARTNERSITZUNG", day), "Anwesend: Bär, Steiner, Wyss\n", "Beschlüsse"]
    lines += [f"{index}. {item}" for index, item in enumerate(items, start=1)]
    return "\n".join(lines) + "\n\nVertraulich, nur für Partner.\n"


def firm_documents(world: World, *, pilot: bool) -> list[FirmDoc]:
    staff = {s.id: s for s in world.firm.staff}
    managing = staff["mb"]
    docs: list[FirmDoc] = []

    def add(rel_path: str, fmt: str, title: str, body: str, day: str, author: str, access: str) -> None:
        docs.append(FirmDoc(rel_path, fmt, title, body, day, author, access))

    formats = ("docx", "pdf", "docx", "pdf", "docx")
    for number, process in enumerate(PROCESSES, start=1):
        day = f"2023-0{number + 2}-1{number}"
        add(
            f"Gemeinsam/Kanzlei/02-Organisation/Weisungen/Weisung {number:02d} {process.title}.{formats[number - 1]}",
            formats[number - 1], f"Weisung {number}: {process.title}", _weisung(world, process, number, day),
            day, "mb", "GG_Alle",
        )
    add("Gemeinsam/Kanzlei/02-Organisation/Kanzlei-ABC.docx", "docx", "Kanzlei-ABC", _glossar(world, "2025-02-03"),
        "2025-02-03", "rg", "GG_Alle")
    add(
        "Gemeinsam/Kanzlei/03-Compliance/Aktennotiz Informationsschranke 2024-017.pdf", "pdf",
        "Aktennotiz Informationsschranke", _schranke(world, "2025-09-01"), "2025-09-01", "mb", "GG_Partner",
    )
    # Partners only.
    sittings = [
        ("2026-09-07", ["Die Semesterabrechnung per Ende Juni ist abgeschlossen; offene Honorarnoten über 60 Tage mahnt die Buchhaltung.",
                        "Die Akte 2024-055 bleibt gesperrt; Zugriff nur Steiner, Keller und Bär.",
                        "Der Stundenansatz der Associates steigt per 1. Januar 2027 um CHF 20."]),
        ("2026-10-05", ["Budget 2027: zwei neue Arbeitsplätze, Erneuerung der Telefonanlage.",
                        "Die Informationsschranke für MLaw Marti in der Akte 2024-017 bleibt bestehen.",
                        "Die Kanzlei prüft eine Wissenssuche über die Akten; Bär holt Offerten ein."]),
        ("2026-11-02", ["Weihnachtsessen am 11. Dezember 2026.",
                        "Die Fristenkontrolle am Montag übernimmt ab Dezember Frau Suter.",
                        "Gewinnverteilung 2026 nach bisherigem Schlüssel."]),
    ]
    for day, items in sittings[: 1 if pilot else 3]:
        add(f"Gemeinsam/Partner/Partnersitzungen/Protokoll PS {day}.docx", "docx", f"Protokoll Partnersitzung {day}",
            _partnersitzung(world, day, items), day, "as", "GG_Partner")
    # Personnel: contracts and appraisals, partners only.
    people = list(world.firm.staff) if not pilot else [staff["lz"], staff["ms"]]
    for person in people:
        if person.role == "partner":
            continue
        surname = person.name.split()[-1]
        add(f"Gemeinsam/Personal/{surname} {person.name.split()[-2]}/Arbeitsvertrag {surname}.pdf", "pdf",
            f"Arbeitsvertrag {person.name}", _arbeitsvertrag(world, person, person.joined), person.joined, "mb", "GG_Partner")
    for person in ([staff["lz"], staff["sk"], staff["ms"]] if not pilot else [staff["lz"]]):
        surname = person.name.split()[-1]
        add(f"Gemeinsam/Personal/{surname} {person.name.split()[-2]}/MAG 2026 {surname}.docx", "docx",
            f"Mitarbeitergespräch 2026 {person.name}", _mag(world, person, managing, "2026-06-18"), "2026-06-18", "mb",
            "GG_Partner")
    # Accounting: readable by partners and the bookkeeper.
    add(
        "Gemeinsam/Buchhaltung/Abschluss/Jahresabschluss 2025 Kurzbericht.pdf", "pdf", "Jahresabschluss 2025",
        _head(world, "JAHRESABSCHLUSS 2025, KURZBERICHT", "2026-03-20")
        + "Honorarertrag CHF 4'812'300. Personalaufwand CHF 2'104'900. Raum- und Sachaufwand CHF 611'200.\n"
        "Debitoren per 31. Dezember 2025: CHF 386'450, davon CHF 41'200 älter als 90 Tage.\n"
        "Klientengelder werden getrennt geführt und sind nicht Teil dieser Rechnung.\n\nVertraulich.\n",
        "2026-03-20", "rg", "GG_Buchhaltung",
    )
    add(
        "Gemeinsam/Buchhaltung/MWST/MWST-Abrechnung 2026 Q3.pdf", "pdf", "MWST-Abrechnung 3. Quartal 2026",
        _head(world, "MWST-ABRECHNUNG 3. QUARTAL 2026", "2026-10-22")
        + "Vereinbartes Entgelt CHF 1'248'600. Steuersatz 8,1 Prozent. Geschuldete Steuer CHF 101'136.60.\n"
        "Vorsteuer CHF 14'870.25. Zahllast CHF 86'266.35, bezahlt am 28. Oktober 2026.\n",
        "2026-10-22", "rg", "GG_Buchhaltung",
    )
    # Everyone.
    add(
        "Gemeinsam/Kanzlei/02-Organisation/Telefonliste.pdf", "pdf", "Telefonliste",
        _head(world, "TELEFONLISTE INTERN", "2026-09-01")
        + "\n".join(f"{s.name}, {s.title}, intern {s.extension}, {s.email}" for s in world.firm.staff) + "\n",
        "2026-09-01", "rg", "GG_Alle",
    )
    add(
        "Gemeinsam/Kanzlei/02-Organisation/Pikettplan 2026.txt", "txt", "Pikettplan 2026",
        _head(world, "PIKETTPLAN 2026", "2026-01-05")
        + "Januar bis März: Wyss. April bis Juni: Steiner. Juli bis September: Bär. Oktober bis Dezember: Wyss.\n"
        "Das Pikett ist für dringende Fristen ausserhalb der Bürozeit über die Hauptnummer erreichbar.\n",
        "2026-01-05", "rg", "GG_Alle",
    )
    return docs


def scan_texts(world: World, *, pilot: bool) -> list[tuple[str, str, str]]:
    """Scanned post that nobody has filed yet: (file name, day, text)."""
    hero = next(m for m in world.matters if m.aktenzeichen == "2024-017")
    items = [
        ("Scan_20261116_083412.pdf", "2026-11-16",
         f"Bezirksgericht Winterthur\n\nIn Sachen {hero.in_sachen}\n\nVerfügung vom 12. November 2026\n\n"
         "Die Parteien werden zur Instruktionsverhandlung vom 14. Januar 2027, 09.00 Uhr, vorgeladen.\n"
         "Geschäfts-Nr. CG240017\n"),
        ("Scan_20261117_091502.pdf", "2026-11-17",
         "Rüegg Haustechnik\n\nEinschreiben\n\nBetrifft: Neubau Schaffhauserstrasse 41, Ihre Rechnung\n\n"
         "Wir halten an der Mängelrüge fest und bieten ohne Anerkennung einer Rechtspflicht CHF 60'000 "
         "per Saldo aller Ansprüche an. Das Angebot gilt bis 5. Dezember 2026.\n"),
    ]
    if not pilot:
        items += [
            ("Scan_20261117_091733.pdf", "2026-11-17",
             "Betreibungsamt Zürich 1\n\nZahlungsbefehl\n\nGläubigerin: Gasthof zur Linde AG\n"
             "Forderung CHF 12'480 nebst Zins zu 5 Prozent seit 1. August 2026.\n"),
            ("Scan_20261118_074455.pdf", "2026-11-18",
             "Kantonales Steueramt Zürich\n\nVeranlagungsverfügung Staats- und Gemeindesteuern 2024\n\n"
             "Steuerpflichtige: Seehalde Immobilien AG\nEinsprachefrist: 30 Tage ab Zustellung.\n"),
            ("Scan_20261118_074601.pdf", "2026-11-18",
             "Visitenkarten und Notizzettel, ohne Aktenbezug.\n"),
            ("Scan_20261118_101255.pdf", "2026-11-18",
             "Forum Revisions AG\n\nPersönlich / vertraulich\n\nSehr geehrte Frau Steiner\n\n"
             "Beiliegend die ergänzten Unterlagen zur Verdachtsmeldung. Bitte nur im Mandatsteam verwenden.\n"),
        ]
    return items
