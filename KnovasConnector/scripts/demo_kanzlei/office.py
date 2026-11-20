"""What else a practice database holds besides matters and money.

A connector that reads a real firm's database meets more than tidy master
data: appointments, notes typed straight into the software, fields the firm
defined for itself, the software's own users and counters, and the lookup
tables its codes point to. They are here so that reading the stand-in is the
same work as reading a client.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from demo_kanzlei import namepool
from demo_kanzlei.models import World

#: Roles of a party in a matter. ``AKTENBETEILIGTE.RolleID`` points here.
ROLES = ["Mandant", "Gegenpartei", "Gegenanwalt", "Gericht/Behörde", "Versicherung", "Zeuge", "Experte"]

#: (code, label, default text) of a time entry's kind.
ACTIVITY_CODES = [
    ("TEL", "Telefon"), ("KOR", "Korrespondenz"), ("STU", "Aktenstudium"), ("REC", "Rechtsabklärung"),
    ("RS", "Rechtsschrift"), ("BES", "Besprechung"), ("VER", "Verhandlung"), ("ADM", "Administration"),
    ("AUS", "Auslagen"),
]

_CODE_OF = {
    "Telefon mit Mandant": "TEL", "Telefon mit Gegenanwalt": "TEL", "E-Mail an Mandant": "KOR",
    "E-Mail von Gegenanwalt geprüft": "KOR", "Studium Akten": "STU", "Studium Beilagen": "STU",
    "Rechtsabklärung": "REC", "Entwurf Schreiben an Gegenpartei": "KOR", "Entwurf Rechtsschrift": "RS",
    "Überarbeitung Rechtsschrift": "RS", "Besprechung mit Mandant": "BES", "Besprechung intern": "BES",
    "Verhandlung inkl. Weg": "VER", "Vorbereitung Verhandlung": "VER", "Aktennotiz": "ADM",
    "Vergleichsgespräch": "BES", "Durchsicht Entscheid": "STU", "Fristenkontrolle": "ADM",
    "Korrespondenz Gericht": "KOR", "Kostennote erstellt": "ADM",
}

#: Fields the firm added to matters itself: (number, label).
CUSTOM_FIELDS = [
    (1, "Geschäfts-Nr. Gericht"), (2, "Referenz Mandant"), (3, "Policen-Nr. Rechtsschutz"),
    (4, "Archivschachtel"), (5, "Akquise durch"),
]

#: The court's own number for the written matter. The scanned order on the share carries the same one.
COURT_NUMBERS = {"2024-017": "CG240017"}


def activity_code(text: str, art: str) -> str:
    if art == "A":
        return "AUS"
    return _CODE_OF.get(text, "ADM")


@dataclass
class Appointment:
    nr: int
    akte_nr: int | None
    beginn: str  # ISO date and time
    ende: str
    art: str  # V Verhandlung, B Besprechung, I intern
    betreff: str
    ort: str
    kuerzel: str


@dataclass
class JournalEntry:
    nr: int
    akte_nr: int
    zeit: str
    kuerzel: str
    art: str  # T Telefon, N Notiz, E E-Mail-Vermerk
    text: str


@dataclass
class CustomValue:
    feld: int
    akte_nr: int
    wert: str


@dataclass
class SoftwareUser:
    login: str
    kuerzel: str
    profil: str
    letzte_anmeldung: str | None
    gesperrt: bool


@dataclass
class RecordLock:
    tabelle: str
    schluessel: str
    login: str
    seit: str


def _at(day: str, hour: int, minute: int = 0) -> str:
    return f"{day}T{hour:02d}:{minute:02d}:00"


def _plus(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _workday(day: str) -> str:
    current = date.fromisoformat(day)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current.isoformat()


def build_office(world: World, records) -> None:
    """Fill ``records`` with appointments, notes, custom fields, software users and locks."""
    rng = random.Random(f"{world.seed}:office")
    today = records.today.isoformat()
    written = {m.aktenzeichen: m for m in world.matters}
    courts = [name for name in namepool.AUTHORITIES if "gericht" in name.lower()]
    appointments: list[Appointment] = []
    journal: list[JournalEntry] = []
    values: list[CustomValue] = []
    for row in records.matters:
        if int(row.aktenzeichen[:4]) < records.software_since:
            if rng.random() < 0.5:
                values.append(CustomValue(4, row.nr, f"K{rng.randrange(1, 60):02d}/{rng.randrange(1, 40):02d}"))
            continue
        end = row.abgeschlossen or today
        span = max(1, (date.fromisoformat(end) - date.fromisoformat(row.eroeffnet)).days)
        matter = written.get(row.aktenzeichen)
        if matter:
            for event in matter.events:
                if event.kind == "protokoll":
                    appointments.append(Appointment(0, row.nr, _at(event.date, 9), _at(event.date, 11, 30), "V",
                                                    f"Hauptverhandlung {matter.in_sachen}", matter.court or "", row.verantwortlich))
                elif event.kind in {"aktennotiz", "telefonnotiz"}:
                    journal.append(JournalEntry(0, row.nr, _at(event.date, 14, 20), row.sachbearbeiter,
                                                "T" if event.kind == "telefonnotiz" else "N", event.summary))
            number = COURT_NUMBERS.get(row.aktenzeichen)
            if number:
                values.append(CustomValue(1, row.nr, number))
        else:
            for _ in range(rng.choice([0, 0, 1, 1, 2])):
                day = _workday(_plus(row.eroeffnet, rng.randrange(span + 1)))
                hearing = rng.random() < 0.4
                start = rng.choice([8, 9, 10, 14, 15])
                appointments.append(Appointment(
                    0, row.nr, _at(day, start, rng.choice([0, 30])), _at(day, start + (2 if hearing else 1)),
                    "V" if hearing else "B",
                    ("Verhandlung " if hearing else "Besprechung Mandant ") + row.aktenzeichen,
                    rng.choice(courts) if hearing else "Kanzlei, Sitzungszimmer 1",
                    row.verantwortlich if hearing else row.sachbearbeiter))
            for _ in range(rng.choice([0, 1, 2, 3, 5])):
                day = _plus(row.eroeffnet, rng.randrange(span + 1))
                art = rng.choice("TTNNE")
                text = rng.choice([
                    "Mandant ruft zurück, wünscht Sachstand. Rückruf zugesagt.",
                    "Gegenanwalt ersucht um Fristerstreckung, einverstanden.",
                    "Unterlagen vollständig eingegangen, an Sachbearbeitung weitergeleitet.",
                    "Gericht fragt nach Verfügbarkeit für Verhandlung, drei Termine gemeldet.",
                    "Mandant mit Vergleichsvorschlag grundsätzlich einverstanden, Details offen.",
                    "Akte bei Partner zur Durchsicht.",
                    "Rechnung reklamiert, Leistungsauszug zugestellt.",
                ])
                journal.append(JournalEntry(0, row.nr, _at(day, rng.randrange(8, 18), rng.randrange(0, 60)),
                                            rng.choice([row.sachbearbeiter, row.verantwortlich]), art, text))
            if row.status == "O" and rng.random() < 0.25:
                day = _workday(_plus(today, rng.randrange(1, 70)))
                appointments.append(Appointment(0, row.nr, _at(day, 9, 30), _at(day, 11), "V",
                                                f"Verhandlung {row.aktenzeichen}", rng.choice(courts), row.verantwortlich))
            roll = rng.random()
            if roll < 0.18:
                values.append(CustomValue(1, row.nr, f"{rng.choice(['CG', 'FV', 'EZ', 'MJ', 'HG'])}{row.aktenzeichen[2:4]}{rng.randrange(1, 9999):04d}"))
            if 0.1 < roll < 0.3:
                values.append(CustomValue(2, row.nr, f"Ref. {rng.randrange(1000, 99999)}"))
            if row.bemerkung == "Rechtsschutzversicherung":
                values.append(CustomValue(3, row.nr, f"RS-{rng.randrange(100000, 999999)}"))
            if row.status == "A" and rng.random() < 0.35:
                values.append(CustomValue(4, row.nr, f"K{rng.randrange(1, 60):02d}/{rng.randrange(1, 40):02d}"))
    # Appointments without a matter: the firm's own.
    monday = date.fromisoformat(today)
    monday -= timedelta(days=monday.weekday())
    for week in range(-6, 5, 2):
        day = (monday + timedelta(weeks=week)).isoformat()
        appointments.append(Appointment(0, None, _at(day, 17), _at(day, 18, 30), "I", "Partnersitzung", "Kanzlei, Sitzungszimmer 1", "MB"))
    appointments.sort(key=lambda a: (a.beginn, a.akte_nr or 0))
    journal.sort(key=lambda j: (j.zeit, j.akte_nr))
    for index, item in enumerate(appointments, start=1):
        item.nr = index
    for index, item in enumerate(journal, start=1):
        item.nr = index
    records.appointments, records.journal = appointments, journal
    records.custom_values = sorted({(v.feld, v.akte_nr): v for v in values}.values(), key=lambda v: (v.akte_nr, v.feld))

    profile = {"Partner": "ANWALT", "Rechtsanw": "ANWALT", "Sekretariat / Buchhaltung": "BUCHHALTUNG", "Sekretariat": "SEKRETARIAT"}
    users = [SoftwareUser("admin", "ADMIN", "ADMIN", _at(_plus(today, -41), 7, 12), False)]
    for person in records.employees:
        kind = next((value for key, value in profile.items() if person.funktion.startswith(key) or key in person.funktion), "ANWALT")
        if person.funktion == "Sekretariat / Buchhaltung":
            kind = "BUCHHALTUNG"
        last = None if person.austritt else _at(_plus(today, -rng.choice([0, 0, 0, 1, 1, 2, 6])), rng.randrange(7, 10), rng.randrange(0, 60))
        if person.austritt:
            last = _at(person.austritt, 16, rng.randrange(0, 60))
        users.append(SoftwareUser(person.login, person.kuerzel, kind, last, person.austritt is not None))
    records.software_users = users
    # Records somebody has open right now. They change by the minute at a client.
    busy = [m for m in records.matters if m.status == "O" and int(m.aktenzeichen[:4]) >= records.software_since][-40::13]
    records.locks = [
        RecordLock("AKTEN", str(m.nr), next(u.login for u in users if u.kuerzel == m.sachbearbeiter), _at(today, 8 + i, 17 * (i + 1) % 60))
        for i, m in enumerate(busy)
        if any(u.kuerzel == m.sachbearbeiter for u in users)
    ]
