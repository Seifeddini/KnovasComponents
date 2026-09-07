"""Seeded fictional people, clients and the hero-matter timeline."""
from __future__ import annotations

from demo_kanzlei.models import Client, Event, Matter, Staff

STAFF: list[Staff] = [
    Staff(
        id="mb",
        name="Dr. iur. Markus Bär",
        role="partner",
        title="Rechtsanwalt, Partner",
        initials="MB",
        extension="201",
        email="mb@{domain}",
        voice="terse",
    ),
    Staff(
        id="as",
        name="lic. iur. Anna Steiner",
        role="partner",
        title="Rechtsanwältin, Partnerin",
        initials="AS",
        extension="202",
        email="as@{domain}",
        voice="precise",
    ),
    Staff(
        id="lz",
        name="MLaw Luca Zimmermann",
        role="associate",
        title="Rechtsanwalt",
        initials="LZ",
        extension="311",
        email="lz@{domain}",
        voice="verbose",
    ),
    Staff(
        id="sk",
        name="MLaw Sophie Keller",
        role="associate",
        title="Rechtsanwältin",
        initials="SK",
        extension="312",
        email="sk@{domain}",
        voice="practical",
    ),
    Staff(
        id="jf",
        name="MLaw Jonas Frei",
        role="associate",
        title="Rechtsanwalt",
        initials="JF",
        extension="313",
        email="jf@{domain}",
        voice="academic",
    ),
    Staff(
        id="eh",
        name="cand. iur. Elena Hofmann",
        role="substitut",
        title="Substitutin",
        initials="EH",
        extension="401",
        email="eh@{domain}",
        voice="formal",
    ),
    Staff(
        id="rg",
        name="Regula Graf",
        role="sekretariat",
        title="Sekretariat / Buchhaltung",
        initials="RG",
        extension="100",
        email="rg@{domain}",
        voice="admin",
    ),
]

CLIENTS: list[Client] = [
    Client("c-meierhans", "Meierhans Bau AG", "AG", "Bau", "Winterthur", "Pia Meierhans", "pia.meierhans@meierhans-bau.example"),
    Client("c-kellerit", "Keller Informatik GmbH", "GmbH", "IT", "Zürich", "Reto Keller", "reto@keller-informatik.example"),
    Client("c-riethof", "Riethof Treuhand GmbH", "GmbH", "Treuhand", "Uster", "Silvia Bachmann", "s.bachmann@riethof-treuhand.example"),
    Client("c-linde", "Gasthof zur Linde AG", "AG", "Gastro", "Wädenswil", "Omar Haddad", "omar@linde-waedenswil.example"),
    Client("c-seehalde", "Seehalde Immobilien AG", "AG", "Immobilien", "Zürich", "Nora Widmer", "n.widmer@seehalde.example"),
    Client("c-helvetiq", "Helvetiq Pharma Zuliefer AG", "AG", "Pharma", "Schlieren", "Dr. Yves Kunz", "y.kunz@helvetiq-pharma.example"),
    Client("c-tannen", "Tannenfels Holzbau GmbH", "GmbH", "Bau", "Hinwil", "Beat Toggenburg", "beat@tannenfels.example"),
    Client("c-quorum", "Quorum Soft AG", "AG", "IT", "Zürich", "Lea Brunner", "lea.brunner@quorum-soft.example"),
    Client("c-alpen", "Alpenblick Gastro GmbH", "GmbH", "Gastro", "Zürich", "Marco Pedretti", "m.pedretti@alpenblick.example"),
    Client("c-forum", "Forum Revisions AG", "AG", "Treuhand", "Zürich", "Claudia Sutter", "c.sutter@forum-revision.example"),
]

COMPANY_NAMES = [c.name for c in CLIENTS] + [
    "Rüegg Haustechnik",
    "Opus Stahlhandel GmbH",
    "Nordhang Logistik AG",
]


def bind_staff(domain: str) -> list[Staff]:
    return [
        Staff(
            id=s.id,
            name=s.name,
            role=s.role,
            title=s.title,
            initials=s.initials,
            extension=s.extension,
            email=s.email.format(domain=domain),
            voice=s.voice,
        )
        for s in STAFF
    ]


def hero_meierhans() -> Matter:
    """The matter opened on screen: 2024-017 Meierhans Bau AG ./. Rüegg."""
    return Matter(
        aktenzeichen="2024-017",
        title="Werklohn und Mängelrüge Neubau Schaffhauserstrasse",
        practice_area="Bau-/Planungsrecht",
        status="laufend",
        client_id="c-meierhans",
        opposing="Rüegg Haustechnik",
        in_sachen="Meierhans Bau AG ./. Rüegg",
        counsel_id="mb",
        assistant_id="lz",
        opened="2024-01-15",
        court="Bezirksgericht Winterthur",
        hero=True,
        events=[
            Event("e01", "2024-01-15", "mandatsannahme", "Mandatsvereinbarung unterzeichnet.", {"honorar_ansatz": "CHF 420"}),
            Event("e02", "2024-01-16", "vollmacht", "Prozessvollmacht erteilt."),
            Event("e03", "2024-01-18", "konflikt", "Konfliktprüfung negativ."),
            Event("e04", "2024-02-02", "aktennotiz", "Besprechung Mängelrüge und Einbehalte."),
            Event("e05", "2024-02-05", "email", "E-Mail an Mandantin zur Beweislage."),
            Event("e06", "2024-02-20", "abmahnung", "Abmahnung an Rüegg wegen Werklohn."),
            Event("e07", "2024-03-05", "brief", "Brief an Gegenanwalt mit Zahlungsfrist."),
            Event("e08", "2024-03-06", "email", "E-Mail-Thread mit Gegenanwalt."),
            Event("e09", "2024-04-12", "klage", "Klage eingereicht.", {"streitwert": "CHF 184'500", "frist": "2024-11-30"}),
            Event("e10", "2024-04-13", "email", "Eingangsbestätigung an das Gericht."),
            Event("e11", "2024-05-20", "klageantwort", "Klageantwort der Gegenpartei (Posteingang)."),
            Event("e12", "2024-06-03", "replik", "Replik der Klägerin."),
            Event("e13", "2024-06-10", "telefonnotiz", "Telefonat mit Frau Meierhans."),
            Event("e14", "2024-08-15", "vorladung", "Vorladung zur Hauptverhandlung."),
            Event("e15", "2024-09-10", "protokoll", "Verhandlungsprotokoll."),
            Event("e16", "2024-10-02", "urteil", "Urteil erstinstanzlich."),
            Event("e17", "2024-10-20", "honorarnote", "Honorarnote Nr. 2024-017-3.", {"betrag": "CHF 21'840"}),
            Event("e18", "2024-10-21", "email", "Honorarnote an Mandantin per Mail."),
            Event("e19", "2024-03-01", "leistung", "Leistungserfassung Q1."),
            Event("e20", "2024-01-20", "kostenvorschuss", "Kostenvorschussrechnung.", {"betrag": "CHF 8'000"}),
        ],
    )


KIND_TO_DOC = {
    "mandatsannahme": ("Mandatsvereinbarung", "Eroeffnung", "firm_authored"),
    "vollmacht": ("Vollmacht", "Eroeffnung", "firm_authored"),
    "konflikt": ("Konfliktpruefung", "Eroeffnung", "firm_authored"),
    "aktennotiz": ("Aktennotiz", "Interna", "firm_authored"),
    "email": ("E-Mail", "Korrespondenz", "email"),
    "abmahnung": ("Abmahnung", "Beilagen", "firm_authored"),
    "brief": ("Brief-Gegenanwalt", "Korrespondenz", "firm_authored"),
    "klage": ("Klage", "Rechtsschriften", "firm_authored"),
    "klageantwort": ("Klageantwort", "Gerichtliches", "scan"),
    "replik": ("Replik", "Rechtsschriften", "firm_authored"),
    "telefonnotiz": ("Telefonnotiz", "Interna", "firm_authored"),
    "vorladung": ("Vorladung", "Gerichtliches", "firm_authored"),
    "protokoll": ("Verhandlungsprotokoll", "Gerichtliches", "firm_authored"),
    "urteil": ("Urteil", "Gerichtliches", "firm_authored"),
    "honorarnote": ("Honorarnote", "Finanzen", "firm_authored"),
    "leistung": ("Leistungserfassung", "Finanzen", "firm_authored"),
    "kostenvorschuss": ("Kostenvorschuss", "Finanzen", "firm_authored"),
}

REGULAR_MATTER_SEEDS = [
    ("2025-004", "c-kellerit", "Arbeitsrecht", "Kündigung während Krankheit", "Keller Informatik GmbH ./. Rossi", "laufend"),
    ("2023-041", "c-linde", "Mietrecht", "Mietzinsherabsetzung Gastraum", "Gasthof zur Linde AG ./. Liegenschaften Hof AG", "abgeschlossen"),
    ("2024-112", "c-seehalde", "Gesellschaftsrecht", "Gesellschafterstreit Seehalde", "Seehalde Immobilien AG ./. Brunner", "laufend"),
    ("2025-019", "c-helvetiq", "Datenschutz", "EDÖB-Stellungnahme Auftragsverarbeitung", "Helvetiq Pharma Zuliefer AG", "laufend"),
    ("2022-088", "c-quorum", "Kartellrecht", "WEKO-Auskunftsbegehren Softwarelizenzen", "Quorum Soft AG", "sistiert"),
    ("2024-055", "c-forum", "Finanzmarkt", "FINMA-Anzeige GwG-Verdacht", "Forum Revisions AG", "laufend"),
    ("2021-030", "c-riethof", "Erb-/Familienrecht", "Erbteilung Nachlass Bachmann", "Nachlass Bachmann", "abgeschlossen"),
]
