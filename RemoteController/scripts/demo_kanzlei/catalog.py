"""Seeded fictional people, clients and the hero-matter timeline."""
from __future__ import annotations

from demo_kanzlei.models import Client, Event, Matter, Staff, Story
from demo_kanzlei.stories import NAMED

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
    story = NAMED["2024-017"]
    return Matter(
        aktenzeichen="2024-017",
        title=story.title,
        practice_area="Bau-/Planungsrecht",
        status="laufend",
        client_id="c-meierhans",
        opposing=story.opposing,
        in_sachen="Meierhans Bau AG ./. Rüegg",
        counsel_id="mb",
        assistant_id="lz",
        opened="2024-01-15",
        court="Bezirksgericht Winterthur",
        hero=True,
        story=story,
        events=_stamp(
            [
            Event("e01", "2024-01-15", "mandatsannahme", "Mandatsvereinbarung für den Neubau Schaffhauserstrasse 41.", {"honorar_ansatz": story.honorar_ansatz}),
            Event("e02", "2024-01-16", "vollmacht", "Prozessvollmacht erteilt."),
            Event("e03", "2024-01-18", "konflikt", "Konfliktprüfung negativ."),
            Event("e04", "2024-02-02", "aktennotiz", f"Besprechung: {story.trigger}."),
            Event("e05", "2024-02-05", "email_mandant", f"Beweislage zum Einbehalt am Objekt {story.place}."),
            Event("e06", "2024-02-20", "abmahnung", f"Abmahnung an {story.opposing} wegen Restwerklohn {story.streitwert}."),
            Event("e07", "2024-03-05", "brief", "Brief an Gegenanwalt mit Zahlungsfrist."),
            Event("e08", "2024-03-06", "email_gegenpartei", "Begleitmail zur Abmahnung."),
            Event("e09", "2024-04-12", "klage", f"Klage über {story.streitwert} ({story.statute}).", {"streitwert": story.streitwert, "frist": story.frist}),
            Event("e10", "2024-04-13", "email_gericht", "Eingabe an das Bezirksgericht Winterthur."),
            Event("e11", "2024-05-20", "klageantwort", f"{story.opposing} bestreitet den Werklohnanspruch."),
            Event("e12", "2024-06-03", "replik", "Replik: Mängel berechtigen nicht zum vollen Einbehalt."),
            Event("e13", "2024-06-10", "telefonnotiz", "Telefonat mit Frau Meierhans zur Verhandlung."),
            Event("e14", "2024-08-15", "vorladung", "Vorladung Hauptverhandlung Winterthur."),
            Event("e15", "2024-09-10", "protokoll", "Verhandlung zum Restwerklohn Schaffhauserstrasse."),
            Event("e16", "2024-10-02", "urteil", story.outcome),
            Event("e17", "2024-10-20", "honorarnote", f"Honorarnote {story.honorar_total}.", {"betrag": story.honorar_total}),
            Event("e18", "2024-10-21", "email_mandant", "Honorarnote und Urteilsversand."),
            Event("e19", "2024-03-01", "leistung", "Leistungserfassung Q1 Schaffhauserstrasse."),
            Event("e20", "2024-01-20", "kostenvorschuss", "Kostenvorschuss.", {"betrag": story.vorschuss}),
            Event("e21", "2024-01-22", "beilage", story.beilage),
            Event("e22", "2024-02-12", "pendenzen", f"Frist {story.frist} und Beilagen {story.place}."),
            ],
            story,
        ),
    )


KIND_TO_DOC = {
    "mandatsannahme": ("Mandatsvereinbarung", "Eroeffnung", "firm_authored"),
    "vollmacht": ("Vollmacht", "Eroeffnung", "firm_authored"),
    "konflikt": ("Konfliktpruefung", "Eroeffnung", "firm_authored"),
    "aktennotiz": ("Aktennotiz", "Interna", "firm_authored"),
    "email": ("E-Mail-Mandant", "Korrespondenz", "email"),
    "email_mandant": ("E-Mail-Mandant", "Korrespondenz", "email"),
    "email_gegenpartei": ("E-Mail-Gegenpartei", "Korrespondenz", "email"),
    "email_gericht": ("E-Mail-Gericht", "Korrespondenz", "email"),
    "email_behoerde": ("E-Mail-Behoerde", "Korrespondenz", "email"),
    "abmahnung": ("Abmahnung", "Beilagen", "firm_authored"),
    "brief": ("Brief-Gegenanwalt", "Korrespondenz", "firm_authored"),
    "brief_mandant": ("Brief-Mandant", "Korrespondenz", "firm_authored"),
    "klage": ("Klage", "Rechtsschriften", "firm_authored"),
    "klageantwort": ("Klageantwort", "Gerichtliches", "scan"),
    "replik": ("Replik", "Rechtsschriften", "firm_authored"),
    "telefonnotiz": ("Telefonnotiz", "Interna", "firm_authored"),
    "vorladung": ("Vorladung", "Gerichtliches", "firm_authored"),
    "protokoll": ("Verhandlungsprotokoll", "Gerichtliches", "firm_authored"),
    "urteil": ("Urteil", "Gerichtliches", "firm_authored"),
    "vergleich": ("Vergleich", "Rechtsschriften", "firm_authored"),
    "stellungnahme": ("Stellungnahme", "Rechtsschriften", "firm_authored"),
    "sistierung": ("Sistierungsvermerk", "Interna", "firm_authored"),
    "beilage": ("Beilage-Vertrag", "Beilagen", "firm_authored"),
    "pendenzen": ("Pendenzenliste", "Interna", "firm_authored"),
    "gutachten": ("Rechtsgutachten", "Interna", "firm_authored"),
    "behoerde_eingang": ("Eingang-Behoerde", "Gerichtliches", "scan"),
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


def _plus(opened: str, days: int) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(opened) + timedelta(days=days)).isoformat()


def mandate_family(practice_area: str) -> str:
    area = practice_area.casefold()
    if any(token in area for token in ("datenschutz", "kartell", "weko", "finma", "finanz", "edöb", "edoeb")):
        return "authority"
    if any(token in area for token in ("arbeit", "miet", "bau", "plan", "gesellschaft", "erb", "familie", "gastro", "immobil")):
        return "litigation"
    return "advisory"


def events_for_mandate(*, story: Story, practice_area: str, status: str, opened: str) -> list[Event]:
    """Same register mix as before, but every beat quotes this mandate's story."""
    family = mandate_family(practice_area)
    events = [
        Event("e01", opened, "mandatsannahme", f"Mandat: {story.title}. {story.hook}", {"honorar_ansatz": story.honorar_ansatz}),
        Event("e02", _plus(opened, 1), "vollmacht", "Prozessvollmacht erteilt."),
        Event("e03", _plus(opened, 2), "konflikt", f"Konfliktprüfung gegen {story.opposing}: negativ."),
        Event("e04", _plus(opened, 3), "email_mandant", f"Bestätigung: wir führen die Sache {story.place}."),
        Event("e05", _plus(opened, 5), "kostenvorschuss", "Kostenvorschuss.", {"betrag": story.vorschuss}),
        Event("e06", _plus(opened, 8), "beilage", story.beilage),
        Event("e07", _plus(opened, 18), "aktennotiz", f"Intern: {story.trigger}."),
        Event("e08", _plus(opened, 22), "email_mandant", f"Sachstand {story.place}: {story.hook}"),
    ]
    if family == "authority":
        events += [
            Event("e09", _plus(opened, 40), "brief_mandant", f"Entwurf Eingabe, {story.statute}."),
            Event("e10", _plus(opened, 48), "stellungnahme", f"Stellungnahme: {story.hook}"),
            Event("e11", _plus(opened, 49), "email_behoerde", f"Übermittlung, Frist {story.frist}."),
            Event("e12", _plus(opened, 70), "behoerde_eingang", f"Eingang Behörde zu {story.place}."),
            Event("e13", _plus(opened, 85), "gutachten", f"Gutachten {story.statute}."),
        ]
    elif family == "litigation":
        events += [
            Event("e09", _plus(opened, 35), "abmahnung", f"Abmahnung {story.opposing}: {story.trigger}."),
            Event("e10", _plus(opened, 50), "brief", f"Schreiben an die Gegenseite, Forderung {story.streitwert}."),
            Event("e11", _plus(opened, 51), "email_gegenpartei", "Begleitmail zur Abmahnung."),
            Event("e12", _plus(opened, 72), "telefonnotiz", f"Telefonat zum Objekt {story.place}."),
        ]
        if status != "sistiert":
            events += [
                Event("e13", _plus(opened, 90), "klage", f"Klage {story.streitwert}, {story.statute}.", {"streitwert": story.streitwert, "frist": story.frist}),
                Event("e14", _plus(opened, 91), "email_gericht", "Einreichung der Klage."),
            ]
    else:
        events += [
            Event("e09", _plus(opened, 30), "brief_mandant", f"Beratung: {story.hook}"),
            Event("e10", _plus(opened, 45), "gutachten", f"Gutachten {story.statute}."),
            Event("e11", _plus(opened, 46), "email_mandant", "Gutachten per Mail."),
            Event("e12", _plus(opened, 60), "pendenzen", f"Pendenzen, Frist {story.frist}."),
        ]
    if status == "sistiert":
        events.append(Event("e20", _plus(opened, 120), "sistierung", story.outcome or f"Sistiert: {story.trigger}"))
    elif status == "abgeschlossen":
        if family == "litigation":
            events.append(Event("e21", _plus(opened, 200), "urteil", story.outcome or f"Erledigung {story.place}."))
        else:
            events.append(Event("e21", _plus(opened, 160), "vergleich", story.outcome or "Vergleich."))
        events.append(Event("e22", _plus(opened, 210), "email_mandant", f"Abschluss {story.title}."))
    events += [
        Event("e30", _plus(opened, 100), "leistung", f"Zeiterfassung {story.place}."),
        Event("e31", _plus(opened, 220 if status == "abgeschlossen" else 140), "honorarnote", f"Honorar {story.honorar_total}.", {"betrag": story.honorar_total}),
    ]
    return _stamp(events, story)


def _stamp(events: list[Event], story: Story) -> list[Event]:
    extra = {
        "hook": story.hook,
        "place": story.place,
        "streitwert": story.streitwert,
        "frist": story.frist,
        "beilage": story.beilage,
        "trigger": story.trigger,
        "statute": story.statute,
        "honorar_ansatz": story.honorar_ansatz,
        "honorar_total": story.honorar_total,
        "vorschuss": story.vorschuss,
        "outcome": story.outcome,
        "plot_id": story.plot_id,
    }
    for event in events:
        event.facts = {**extra, **event.facts}
    return events


KANZLEI_DOCS = [
    ("k01", "2024-01-08", "Vorlagen", "Mandatsvereinbarung-Muster", "firm_authored", "Muster Mandatsvereinbarung der Kanzlei."),
    ("k02", "2024-01-08", "Vorlagen", "Vollmacht-Muster", "firm_authored", "Muster Prozessvollmacht."),
    ("k03", "2024-06-12", "Organisation", "Partnersitzung-Protokoll", "firm_authored", "Protokoll der Partnersitzung."),
    ("k04", "2023-03-01", "Compliance", "GwG-Weisung", "firm_authored", "Interne GwG-Weisung."),
    ("k05", "2024-02-01", "Compliance", "Merkblatt-Datenschutz", "firm_authored", "Merkblatt Bearbeitung von Mandantendaten."),
    ("k06", "2024-01-02", "Organisation", "Honorarordnung", "firm_authored", "Kanzlei-Honorarordnung."),
]

