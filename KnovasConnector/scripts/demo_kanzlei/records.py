"""The firm's records, as its practice software keeps them in its database.

At a client these records are the only place that says who the clients are,
which matters exist, who works on them, what was billed and which deadlines
run. Knovas does not read them today; the stand-in has them so that this gap
shows (scenario S9) and so that the share has something true to disagree with.

The schema is invented. It is modelled on what such software keeps, not on any
vendor's tables, which are not public.

Everything is derived from the world and its seed. The written matters of the
world are rows here like any other matter; the rest of the register is records
only. Matters opened before the software came (``records.software_since``) were
taken over as master data: no time entries, invoices, deadlines or document rows.
"""
from __future__ import annotations

import bisect
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from demo_kanzlei import namepool
from demo_kanzlei.catalog import FORMER_STAFF
from demo_kanzlei.matter_numbers import as_of, opened_anchored, split, year_counts
from demo_kanzlei.models import Matter, World

STATUS_CODE = {"laufend": "O", "abgeschlossen": "A", "sistiert": "S"}
MANAGING_PARTNER = "MB"
MIGRATION_USER = "ADMIN"

#: Matters only some people may open, and why. (reference → initials)
LOCKED_WORLD_MATTERS = {
    "2024-055": (["AS", "SK", "MB"], "Geldwäschereiverdacht, Zugriff nur Mandatsteam"),
    "2024-112": (["AS", "JF", "MB", "CL"], "Gesellschafterstreit, Zugriff nur Mandatsteam"),
}

#: An information barrier: (reference, initials, reason).
EXCLUSIONS = [
    ("2024-017", "NM", "Informationsschranke: frühere Tätigkeit bei der Kanzlei der Gegenpartei"),
]


@dataclass
class Employee:
    kuerzel: str
    name: str
    funktion: str
    login: str
    ansatz: int
    eintritt: str
    austritt: str | None

    def at_work(self, day: str) -> bool:
        return self.eintritt <= day and (self.austritt is None or day <= self.austritt)


@dataclass
class Address:
    nr: int
    kind: str  # P person, F firm, B public body, A lawyer
    anrede: str
    titel: str
    vorname: str
    name: str
    firma: str
    strasse: str
    plz: str
    ort: str
    telefon: str
    email: str
    erfasst_am: str
    erfasst_von: str
    inaktiv: bool = False
    bemerkung: str = ""
    #: Truth only: the address this one duplicates. Not a column.
    duplicate_of: int | None = None

    @property
    def label(self) -> str:
        if self.firma:
            return self.firma
        return f"{self.vorname} {self.name}".strip()


@dataclass
class DbMatter:
    nr: int
    aktenzeichen: str
    bezeichnung: str
    rechtsgebiet: str
    status: str
    eroeffnet: str
    abgeschlossen: str | None
    verantwortlich: str
    sachbearbeiter: str
    mandant: int
    streitwert: float | None
    ansatz: int
    gesperrt: bool = False
    archiv_nr: str = ""
    bemerkung: str = ""
    #: Truth only: this matter has written documents on the share.
    in_world: bool = False


@dataclass
class Participant:
    akte_nr: int
    adr_nr: int
    rolle: str


@dataclass
class TimeEntry:
    nr: int
    akte_nr: int
    datum: str
    kuerzel: str
    art: str  # H Honorar, A Auslagen
    stunden: float
    ansatz: int
    betrag: float
    text: str
    rechnung: int | None = None


@dataclass
class Invoice:
    nr: int
    akte_nr: int
    art: str  # R Rechnung, V Vorschuss
    datum: str
    honorar: float
    auslagen: float
    mwst_satz: float
    total: float
    status: str  # offen, bezahlt, gemahnt, abgeschrieben
    bezahlt_am: str | None


@dataclass
class Deadline:
    nr: int
    akte_nr: int
    datum: str
    art: str
    text: str
    verantwortlich: str
    erledigt: bool
    erledigt_am: str | None


@dataclass
class DocRecord:
    """One row of the document index. Filled when the share is filed."""

    dok_nr: int
    guid: str
    akte_nr: int
    typ: str
    bezeichnung: str
    dateiname: str
    ordner: str
    erstellt_am: str
    erstellt_von: str
    groesse: int


@dataclass
class MatterRight:
    akte_nr: int
    kuerzel: str
    recht: str  # Z Zugriff, X Ausschluss
    grund: str


@dataclass
class Records:
    today: date
    software_since: int
    employees: list[Employee] = field(default_factory=list)
    addresses: list[Address] = field(default_factory=list)
    matters: list[DbMatter] = field(default_factory=list)
    participants: list[Participant] = field(default_factory=list)
    time_entries: list[TimeEntry] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)
    deadlines: list[Deadline] = field(default_factory=list)
    documents: list[DocRecord] = field(default_factory=list)
    rights: list[MatterRight] = field(default_factory=list)

    def matter(self, aktenzeichen: str) -> DbMatter:
        return self._by_ref()[aktenzeichen]

    def _by_ref(self) -> dict[str, DbMatter]:
        cache = getattr(self, "_ref_cache", None)
        if cache is None or len(cache) != len(self.matters):
            cache = {m.aktenzeichen: m for m in self.matters}
            self._ref_cache = cache
        return cache

    def rights_for(self, akte_nr: int) -> list[MatterRight]:
        return [right for right in self.rights if right.akte_nr == akte_nr]


def chf(value: str) -> float | None:
    """``CHF 21'840`` → 21840.0; ``n/a`` → None."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit() or ch == ".")
    return float(digits) if digits else None


def vat_rate(day: str) -> float:
    year = int(day[:4])
    if year >= 2024:
        return 8.1
    if year >= 2018:
        return 7.7
    if year >= 2011:
        return 8.0
    if year >= 2001:
        return 7.6
    if year >= 1999:
        return 7.5
    return 6.5


def _round5(amount: float) -> float:
    """Swiss invoices round to five centimes."""
    return round(round(amount * 20) / 20, 2)


def _rng(seed: int, purpose: str) -> random.Random:
    return random.Random(f"{seed}:{purpose}")


def _plus(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def company_names(seed: int, count: int) -> list[str]:
    """Invented company names, each stem used once. Screened by ``cli.py screen``."""
    rng = _rng(seed, "companies")
    stems = [a + b for a in namepool.STEM_A for b in namepool.STEM_B if a.lower() != b]
    rng.shuffle(stems)
    names = []
    for index in range(count):
        stem = stems[index % len(stems)]
        sector, _area = namepool.SECTORS[rng.randrange(len(namepool.SECTORS))]
        form = rng.choice(namepool.LEGAL_FORMS)
        if form in {"Genossenschaft", "Stiftung"}:
            names.append(f"{form} {stem}")
        else:
            names.append(f"{stem} {sector} {form}")
    return names


def law_firm_names(seed: int, count: int) -> list[str]:
    rng = _rng(seed, "lawfirms")
    stems = [a + b for a in namepool.STEM_A for b in namepool.STEM_B if a.lower() != b]
    rng.shuffle(stems)
    kinds = ["Advokatur {s}", "{s} Rechtsanwälte", "Anwaltskanzlei {s}", "{s} Legal"]
    # Taken from the end of the shuffled list so they never meet a company stem.
    return [kinds[i % len(kinds)].format(s=stems[-1 - i]) for i in range(count)]


def _employees(world: World) -> list[Employee]:
    base = {"partner": 480, "associate": 340, "sekretariat": 120}
    rows = [
        Employee(s.initials, s.name, s.title, s.sam, base[s.role], s.joined, None)
        for s in world.firm.staff
    ]
    rows += [
        Employee(initials, name, title, login, base[role], joined, left)
        for (_id, name, role, title, initials, login, joined, left) in FORMER_STAFF
    ]
    return sorted(rows, key=lambda e: (e.eintritt, e.kuerzel))


def _phone(rng: random.Random) -> str:
    prefix = rng.choice(["44", "44", "43", "52", "55", "79", "78", "76"])
    a, b, c = rng.randrange(200, 999), rng.randrange(10, 99), rng.randrange(10, 99)
    style = rng.randrange(6)
    if style == 0:
        return f"0{prefix} {a} {b} {c}"
    if style == 1:
        return f"0041{prefix}{a}{b}{c}"
    if style == 2:
        return f"0{prefix}/{a} {b} {c}"
    return f"+41 {prefix} {a} {b} {c}"


def _ascii(value: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "é": "e", "è": "e", "à": "a", " ": "-", "'": ""})
    return "".join(ch for ch in value.lower().translate(table) if ch.isalnum() or ch in "-.")


def _addresses(world: World, cfg: dict, *, pilot: bool, today: date, since: int) -> list[Address]:
    seed = world.seed
    rng = _rng(seed, "addresses")
    wanted = int(cfg["records"]["addresses_pilot" if pilot else "addresses_total"])
    migration = f"{since}-01-09"
    people = [e for e in _employees(world) if e.funktion.startswith("Sekretariat")]

    def entered(day: str) -> str:
        on_duty = [e.kuerzel for e in people if e.at_work(day)]
        return rng.choice(on_duty) if on_duty else MIGRATION_USER

    def when() -> str:
        if rng.random() < 0.35:
            return migration
        span = (today - date(since, 2, 1)).days
        # More addresses in recent years: the firm grew.
        return (date(since, 2, 1) + timedelta(days=int(span * rng.random() ** 0.6))).isoformat()

    def place() -> tuple[str, str, str]:
        plz, ort = rng.choice(namepool.TOWNS)
        return f"{rng.choice(namepool.STREETS)} {rng.randrange(1, 140)}", plz, ort

    rows: list[Address] = []

    def add(**kw) -> Address:
        day = kw.pop("erfasst_am", None) or when()
        strasse, plz, ort = kw.pop("place", None) or place()
        row = Address(
            nr=0,
            kind=kw.pop("kind"),
            anrede=kw.pop("anrede", ""),
            titel=kw.pop("titel", ""),
            vorname=kw.pop("vorname", ""),
            name=kw.pop("name", ""),
            firma=kw.pop("firma", ""),
            strasse=strasse,
            plz=plz,
            ort=ort,
            telefon=kw.pop("telefon", None) or _phone(rng),
            email=kw.pop("email", ""),
            erfasst_am=day,
            erfasst_von=MIGRATION_USER if day == migration else entered(day),
            **kw,
        )
        rows.append(row)
        return row

    for name in namepool.AUTHORITIES:
        add(kind="B", firma=name, erfasst_am=migration, email="")
    # The clients of the written matters, and the people they send.
    for index, client in enumerate(world.clients):
        day = f"{2008 + index}-0{1 + index % 9}-1{index % 9}"
        add(kind="F", firma=client.name, erfasst_am=day, place=(f"{rng.choice(namepool.STREETS)} {3 + index}", "", client.city),
            email=client.contact_email, bemerkung="Stammkunde")
        first, _, last = client.contact_name.rpartition(" ")
        add(kind="P", anrede="", vorname=first, name=last, firma=client.name, erfasst_am=day, email=client.contact_email)
    # The other side of each written matter.
    seen: set[str] = set()
    for matter in world.matters:
        if matter.opposing in seen or any(matter.opposing == a for a in namepool.AUTHORITIES):
            continue
        seen.add(matter.opposing)
        looks_like_firm = any(tok in matter.opposing for tok in ("AG", "GmbH", "Ltd", "BV", " ", "/"))
        day = _plus(matter.opened, 2)
        if looks_like_firm:
            add(kind="F", firma=matter.opposing, erfasst_am=day)
        else:
            add(kind="P", name=matter.opposing, erfasst_am=day, bemerkung="Vorname unbekannt")

    lawyers = max(6, wanted // 20)
    firms = law_firm_names(seed, max(3, lawyers // 3))
    for index in range(lawyers):
        female = rng.random() < 0.45
        first = rng.choice(namepool.FIRST_NAMES_F if female else namepool.FIRST_NAMES_M)
        last = rng.choice(namepool.SURNAMES)
        firm = firms[index % len(firms)]
        add(kind="A", anrede="Frau" if female else "Herr", titel=rng.choice(["lic. iur.", "Dr. iur.", "MLaw", "RA"]),
            vorname=first, name=last, firma=firm, email=f"{_ascii(first)[0]}.{_ascii(last)}@{_ascii(firm)}.example")

    duplicates = wanted // 25
    generic = max(0, wanted - len(rows) - duplicates)
    companies = iter(company_names(seed, generic))
    for _ in range(generic):
        if rng.random() < 0.33:
            firm = next(companies)
            add(kind="F", firma=firm, email=f"info@{_ascii(firm)}.example" if rng.random() < 0.7 else "")
        else:
            female = rng.random() < 0.5
            first = rng.choice(namepool.FIRST_NAMES_F if female else namepool.FIRST_NAMES_M)
            last = rng.choice(namepool.SURNAMES)
            mail = f"{_ascii(first)}.{_ascii(last)}@{rng.choice(['postfach', 'mailbox', 'privat'])}.example"
            add(kind="P", anrede="Frau" if female else "Herr", vorname=first, name=last,
                email=mail if rng.random() < 0.55 else "")

    # Mess a client's address table has: fields left empty, old entries switched off.
    for row in rows:
        if row.kind in {"P", "F"} and row.erfasst_am == migration:
            if rng.random() < 0.12:
                row.plz = ""
            if rng.random() < 0.08:
                row.telefon = ""
            if rng.random() < 0.10:
                row.inaktiv = True
        if row.kind == "P" and row.vorname and rng.random() < 0.03:
            row.name = row.name.upper()
    # The same party entered twice, spelt differently.
    originals = [row for row in rows if row.kind in {"P", "F"}]
    clones: list[tuple[Address, Address]] = []
    for _ in range(duplicates):
        source = rng.choice(originals)
        day = _plus(source.erfasst_am, rng.randrange(30, 2500))
        if day > today.isoformat():
            day = today.isoformat()
        clone = Address(**{**source.__dict__, "erfasst_am": day, "erfasst_von": entered(day), "duplicate_of": -1})
        if clone.firma and not clone.name:
            clone.firma = rng.choice([
                clone.firma.replace(" AG", " A.G."), clone.firma.upper(), clone.firma.replace(" GmbH", " G.m.b.H."),
                clone.firma + " ", clone.firma.replace("strasse", "str."),
            ])
        else:
            clone.vorname = rng.choice([clone.vorname[:1] + ".", clone.vorname, clone.vorname.upper()])
            clone.strasse = clone.strasse.replace("strasse", "str.")
        clone.bemerkung = ""
        clones.append((clone, source))
        rows.append(clone)

    rows.sort(key=lambda row: row.erfasst_am)
    for index, row in enumerate(rows):
        row.nr = 10001 + index
    for clone, source in clones:
        clone.duplicate_of = source.nr
    return rows


class _Picker:
    """Addresses that already existed on a given day."""

    def __init__(self, addresses: list[Address], kinds: set[str]):
        self.rows = [a for a in addresses if a.kind in kinds and not a.duplicate_of]
        self.days = [a.erfasst_am for a in self.rows]

    def pick(self, rng: random.Random, day: str, *, recent: bool = False) -> Address:
        upto = max(1, bisect.bisect_right(self.days, day))
        if recent:
            # A new client is usually entered when the matter is opened.
            return self.rows[min(upto - 1, int(upto * (1 - rng.random() ** 3)))]
        return self.rows[rng.randrange(upto)]


def _staff_on(employees: list[Employee], day: str, lawyers: bool) -> list[Employee]:
    def is_lawyer(e: Employee) -> bool:
        return not e.funktion.startswith("Sekretariat")

    return [e for e in employees if e.at_work(day) and is_lawyer(e) == lawyers]


def _matters(world: World, cfg: dict, records: Records, *, pilot: bool) -> None:
    seed = world.seed
    rng = _rng(seed, "matters")
    today = records.today
    written = {m.aktenzeichen: m for m in world.matters}
    counts = year_counts(cfg, list(written), pilot=pilot)
    by_firm = {a.firma: a for a in records.addresses if a.kind == "F" and not a.duplicate_of}
    by_name = {a.name: a for a in records.addresses if a.kind == "P" and not a.vorname}
    clients = _Picker(records.addresses, {"P", "F"})
    regulars = [a for a in clients.rows if a.kind == "F"][:: max(1, len(clients.rows) // 60)]
    opponents = _Picker(records.addresses, {"P", "F"})
    counsels = _Picker(records.addresses, {"A"})
    bodies = [a for a in records.addresses if a.kind == "B"]
    areas = [area for area, _w, _t in namepool.PRACTICE_AREAS]
    weights = [w for _a, w, _t in namepool.PRACTICE_AREAS]
    topics = {area: t for area, _w, t in namepool.PRACTICE_AREAS}
    staff = {s.id: s for s in world.firm.staff}
    nr = 0
    for year in sorted(counts):
        anchors = sorted((split(ref)[1], date.fromisoformat(m.opened)) for ref, m in written.items() if split(ref)[0] == year)
        for seq in range(1, counts[year] + 1):
            nr += 1
            ref = f"{year}-{seq:03d}"
            matter = written.get(ref)
            if matter:
                _written_matter(world, records, matter, nr, by_firm, by_name, bodies, staff)
                continue
            opened = opened_anchored(year, seq, counts[year], today, anchors).isoformat()
            lawyers = _staff_on(records.employees, opened, lawyers=True)
            partners = [e for e in lawyers if "Partner" in e.funktion] or lawyers
            lead = rng.choice(partners)
            worker = rng.choice([e for e in lawyers if e.kuerzel != lead.kuerzel] or lawyers)
            area = rng.choices(areas, weights)[0]
            client = rng.choice(regulars) if rng.random() < 0.3 and regulars[0].erfasst_am <= opened else clients.pick(rng, opened, recent=True)
            other = opponents.pick(rng, opened)
            if other.nr == client.nr:
                other = opponents.pick(rng, opened)
            age = (today - date.fromisoformat(opened)).days / 365
            roll = rng.random()
            if age > 6:
                status = "A" if roll < 0.97 else ("O" if roll < 0.99 else "S")
            elif age > 2:
                status = "A" if roll < 0.75 else ("O" if roll < 0.95 else "S")
            else:
                status = "A" if roll < 0.25 else ("O" if roll < 0.93 else "S")
            closed = None
            if status == "A":
                closed = _plus(opened, rng.randrange(60, 900))
                if closed >= today.isoformat():
                    closed, status = None, "O"
            value = None if rng.random() < 0.25 else float(rng.choice([8, 12, 15, 20, 30, 45, 60, 85, 120, 180, 250, 400]) * 1000 + rng.randrange(0, 20) * 50)
            scale = 0.6 + 0.4 * (year - 1998) / max(1, today.year - 1998)
            before_software = year < records.software_since
            row = DbMatter(
                nr=nr,
                aktenzeichen=ref,
                bezeichnung=f"{_short(client)} ./. {_short(other)}, {rng.choice(topics[area])}",
                rechtsgebiet=area,
                status=status,
                eroeffnet=opened,
                abgeschlossen=closed,
                verantwortlich=lead.kuerzel,
                sachbearbeiter=worker.kuerzel,
                mandant=client.nr,
                streitwert=value,
                ansatz=int(round(lead.ansatz * scale / 10) * 10),
                archiv_nr=f"A-{year}-{seq:03d}" if before_software else "",
                bemerkung=rng.choice(["", "", "", "", "Rechtsschutzversicherung", "unentgeltliche Rechtspflege", "Pauschalhonorar vereinbart", "nur schriftlich kontaktieren"]),
            )
            if not before_software and rng.random() < 0.007:
                row.gesperrt = True
                for kuerzel in sorted({lead.kuerzel, worker.kuerzel, MANAGING_PARTNER}):
                    records.rights.append(MatterRight(nr, kuerzel, "Z", "Zugriff nur Mandatsteam"))
            records.matters.append(row)
            records.participants.append(Participant(nr, client.nr, "Mandant"))
            records.participants.append(Participant(nr, other.nr, "Gegenpartei"))
            if rng.random() < 0.6:
                records.participants.append(Participant(nr, counsels.pick(rng, today.isoformat()).nr, "Gegenanwalt"))
            if area not in {"Gesellschaftsrecht", "Datenschutz"} and rng.random() < 0.55:
                records.participants.append(Participant(nr, rng.choice(bodies).nr, "Gericht/Behörde"))


def _short(address: Address) -> str:
    if address.firma and address.kind != "P":
        return address.firma
    return address.name.title() if address.name.isupper() else address.name


def _written_matter(world, records: Records, matter: Matter, nr: int, by_firm, by_name, bodies, staff) -> None:
    client = next(c for c in world.clients if c.id == matter.client_id)
    client_row = by_firm[client.name]
    story = matter.story
    lead = staff[matter.counsel_id]
    locked = LOCKED_WORLD_MATTERS.get(matter.aktenzeichen)
    records.matters.append(
        DbMatter(
            nr=nr,
            aktenzeichen=matter.aktenzeichen,
            bezeichnung=f"{matter.in_sachen}, {matter.title}",
            rechtsgebiet=matter.practice_area,
            status=STATUS_CODE[matter.status],
            eroeffnet=matter.opened,
            abgeschlossen=matter.closed,
            verantwortlich=lead.initials,
            sachbearbeiter=staff[matter.assistant_id].initials,
            mandant=client_row.nr,
            streitwert=chf(story.streitwert) if story else None,
            ansatz=int(chf(story.honorar_ansatz) or 400) if story else 400,
            gesperrt=bool(locked),
            in_world=True,
        )
    )
    records.participants.append(Participant(nr, client_row.nr, "Mandant"))
    other = by_firm.get(matter.opposing) or by_name.get(matter.opposing)
    if other:
        records.participants.append(Participant(nr, other.nr, "Gegenpartei"))
    for body in bodies:
        if matter.court and body.firma == matter.court:
            records.participants.append(Participant(nr, body.nr, "Gericht/Behörde"))
    if locked:
        for kuerzel in locked[0]:
            records.rights.append(MatterRight(nr, kuerzel, "Z", locked[1]))
    for ref, kuerzel, reason in EXCLUSIONS:
        if ref == matter.aktenzeichen:
            records.rights.append(MatterRight(nr, kuerzel, "X", reason))


def _billing(world: World, records: Records) -> None:
    """Time entries, invoices and deadlines for every matter the software has run."""
    rng = _rng(world.seed, "billing")
    today = records.today.isoformat()
    written = {m.aktenzeichen: m for m in world.matters}
    entries: list[TimeEntry] = []
    invoices: list[Invoice] = []
    deadlines: list[Deadline] = []
    overdue_left = 3
    for row in records.matters:
        if int(row.aktenzeichen[:4]) < records.software_since:
            continue
        matter = written.get(row.aktenzeichen)
        if matter:
            _bill_written(matter, row, entries, invoices, deadlines, rng, today)
            continue
        end = row.abgeschlossen or today
        span = max(1, (date.fromisoformat(end) - date.fromisoformat(row.eroeffnet)).days)
        count = max(3, min(80, int(rng.lognormvariate(2.6, 0.7))))
        days = sorted(_plus(row.eroeffnet, rng.randrange(span + 1)) for _ in range(count))
        pending: list[TimeEntry] = []
        last_bill = row.eroeffnet
        gap = rng.randrange(90, 270)
        for day in days:
            lawyers = _staff_on(records.employees, day, lawyers=True)
            secretaries = _staff_on(records.employees, day, lawyers=False)
            roll = rng.random()
            named = {e.kuerzel: e for e in lawyers}
            if roll < 0.15 and secretaries:
                who, text = rng.choice(secretaries), rng.choice(namepool.SECRETARY_TEXTS)
            else:
                wanted = row.verantwortlich if roll < 0.45 else row.sachbearbeiter
                who, text = named.get(wanted) or rng.choice(lawyers), rng.choice(namepool.TIME_TEXTS)
            hours = rng.choice([0.25, 0.25, 0.5, 0.5, 0.5, 0.75, 1.0, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0])
            rate = row.ansatz if who.ansatz >= 300 else int(round(row.ansatz * 0.3 / 10) * 10)
            entry = TimeEntry(0, row.nr, day, who.kuerzel, "H", hours, rate, round(hours * rate, 2), text)
            entries.append(entry)
            pending.append(entry)
            if (date.fromisoformat(day) - date.fromisoformat(last_bill)).days > gap:
                invoices.append(_invoice(row.nr, _plus(day, rng.randrange(2, 20)), pending, rng, today))
                pending, last_bill, gap = [], day, rng.randrange(90, 270)
        if pending and row.abgeschlossen:
            invoices.append(_invoice(row.nr, row.abgeschlossen, pending, rng, today))
        # Deadlines: past ones done, running matters have some ahead.
        for _ in range(rng.randrange(0, 4)):
            day = _plus(row.eroeffnet, rng.randrange(10, span + 30))
            if day > today:
                continue
            deadlines.append(Deadline(0, row.nr, day, rng.choice(namepool.DEADLINE_KINDS), "", row.sachbearbeiter, True, _plus(day, -rng.randrange(0, 4))))
        if row.status == "O":
            for _ in range(rng.randrange(0, 3)):
                day = _plus(today, rng.randrange(1, 120))
                deadlines.append(Deadline(0, row.nr, day, rng.choice(namepool.DEADLINE_KINDS), "", row.sachbearbeiter, False, None))
            if overdue_left and rng.random() < 0.02:
                overdue_left -= 1
                deadlines.append(Deadline(0, row.nr, _plus(today, -rng.randrange(3, 40)), "Wiedervorlage", "nicht erledigt", row.sachbearbeiter, False, None))
    for item in invoices:
        if item.datum > today:
            item.datum = today
    entries.sort(key=lambda e: (e.datum, e.akte_nr))
    invoices.sort(key=lambda i: (i.datum, i.akte_nr))
    deadlines.sort(key=lambda d: (d.datum, d.akte_nr))
    running: dict[str, int] = {}
    renumber: dict[int, int] = {}
    for item in invoices:
        year = item.datum[:4]
        running[year] = running.get(year, 0) + 1
        number = int(year) * 10000 + running[year]
        renumber[id(item)] = number
    for entry in entries:
        if entry.rechnung is not None:
            entry.rechnung = renumber[entry.rechnung]
    for item in invoices:
        item.nr = renumber[id(item)]
    for index, entry in enumerate(entries, start=1):
        entry.nr = index
    for index, item in enumerate(deadlines, start=1):
        item.nr = index
    records.time_entries, records.invoices, records.deadlines = entries, invoices, deadlines


def _invoice(akte_nr: int, day: str, pending: list[TimeEntry], rng: random.Random, today: str) -> Invoice:
    honorar = round(sum(e.betrag for e in pending if e.art == "H"), 2)
    auslagen = round(sum(e.betrag for e in pending if e.art == "A"), 2)
    rate = vat_rate(day)
    total = _round5((honorar + auslagen) * (1 + rate / 100))
    age = (date.fromisoformat(today) - date.fromisoformat(min(day, today))).days
    roll = rng.random()
    if age < 45:
        status = "offen" if roll < 0.6 else "bezahlt"
    else:
        status = "bezahlt" if roll < 0.955 else ("gemahnt" if roll < 0.98 else "abgeschrieben")
    paid = None
    if status == "bezahlt":
        paid = min(_plus(day, rng.randrange(5, 55)), today)
    item = Invoice(0, akte_nr, "R", day, honorar, auslagen, rate, total, status, paid)
    for entry in pending:
        entry.rechnung = id(item)  # replaced by the invoice number once numbers are given
    return item


def _bill_written(matter: Matter, row: DbMatter, entries, invoices, deadlines, rng, today: str) -> None:
    """The written matters' records agree with what their documents say."""
    story = matter.story
    events = {event.kind: event for event in matter.events}
    advance = events.get("kostenvorschuss")
    if advance and story:
        amount = chf(story.vorschuss) or 0.0
        invoices.append(Invoice(0, row.nr, "V", advance.date, amount, 0.0, 0.0, amount, "bezahlt", _plus(advance.date, 8)))
    fee = events.get("honorarnote")
    total = chf(story.honorar_total) if story else None
    rate = row.ansatz
    last_day = fee.date if fee else (matter.events[-1].date if matter.events else matter.opened)
    span = max(1, (date.fromisoformat(last_day) - date.fromisoformat(matter.opened)).days)
    if total:
        quarters = int(total / rate * 4)
        remainder = round(total - quarters * rate / 4, 2)
    else:
        quarters, remainder = rng.randrange(20, 80), 0.0
    pieces = max(6, min(40, quarters // 6))
    # Split the hours into entries of a quarter hour or more that add up exactly.
    cuts = sorted(rng.sample(range(1, quarters), pieces - 1)) if quarters > pieces else []
    sizes = [b - a for a, b in zip([0, *cuts], [*cuts, quarters])]
    days = sorted(_plus(matter.opened, rng.randrange(span + 1)) for _ in sizes)
    pending = []
    for day, size in zip(days, sizes):
        who = row.verantwortlich if rng.random() < 0.55 else row.sachbearbeiter
        hours = size / 4
        entry = TimeEntry(0, row.nr, day, who, "H", hours, rate, round(hours * rate, 2), rng.choice(namepool.TIME_TEXTS))
        entries.append(entry)
        pending.append(entry)
    if remainder > 0:
        entry = TimeEntry(0, row.nr, last_day, row.sachbearbeiter, "A", 0.0, 0, remainder, "Auslagen (Porti, Kopien, Gerichtskosten-Vorschuss)")
        entries.append(entry)
        pending.append(entry)
    if fee and total:
        honorar = round(sum(e.betrag for e in pending if e.art == "H"), 2)
        auslagen = round(sum(e.betrag for e in pending if e.art == "A"), 2)
        vat = vat_rate(fee.date)
        age = (date.fromisoformat(today) - date.fromisoformat(fee.date)).days
        status, paid = ("bezahlt", min(_plus(fee.date, 24), today)) if age > 45 else ("offen", None)
        item = Invoice(0, row.nr, "R", fee.date, honorar, auslagen, vat, _round5(total * (1 + vat / 100)), status, paid)
        invoices.append(item)
        for entry in pending:
            entry.rechnung = id(item)
    if story and story.frist:
        done = story.frist < today
        deadlines.append(Deadline(0, row.nr, story.frist, "Gerichtsfrist", f"Frist {story.statute}", row.sachbearbeiter, done, story.frist if done else None))


def build_records(world: World, cfg: dict, *, pilot: bool) -> Records:
    today = as_of(cfg)
    since = int(cfg["records"]["software_since"])
    records = Records(today=today, software_since=since)
    records.employees = _employees(world)
    records.addresses = _addresses(world, cfg, pilot=pilot, today=today, since=since)
    _matters(world, cfg, records, pilot=pilot)
    _billing(world, records)
    return records
