from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Staff:
    id: str
    name: str
    role: str
    title: str
    initials: str
    extension: str
    email: str
    voice: str


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    legal_form: str
    sector: str
    city: str
    contact_name: str
    contact_email: str


@dataclass
class Event:
    id: str
    date: str
    kind: str
    summary: str
    facts: dict[str, str] = field(default_factory=dict)


@dataclass
class Story:
    """Canonical plot for one mandate. Every document in the Akte reads this."""

    plot_id: str
    title: str
    hook: str
    place: str
    opposing: str
    streitwert: str
    frist: str
    beilage: str
    trigger: str
    statute: str
    honorar_ansatz: str
    honorar_total: str
    vorschuss: str
    outcome: str = ""


@dataclass
class Matter:
    aktenzeichen: str
    title: str
    practice_area: str
    status: str
    client_id: str
    opposing: str
    in_sachen: str
    counsel_id: str
    assistant_id: str
    opened: str
    court: str | None = None
    closed: str | None = None
    hero: bool = False
    events: list[Event] = field(default_factory=list)
    story: Story | None = None


@dataclass
class Firm:
    name: str
    seat: str
    founded: int
    street: str
    zip_code: str
    phone: str
    email: str
    staff: list[Staff]


@dataclass
class World:
    seed: int
    firm: Firm
    clients: list[Client]
    matters: list[Matter]


@dataclass
class PlanRow:
    id: str
    matter_aktenzeichen: str | None
    event_id: str | None
    doc_type: str
    category: str
    fmt: str
    tier: str
    author_id: str | None
    date: str
    title: str
    facts: dict[str, str]
    brief: str
    hero: bool = False
    email_to: list[list[str]] | None = None
    email_from: list[str] | None = None
    seq: int = 0
    register: str = ""
    rel_dir: str = ""

    def cache_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "matter": self.matter_aktenzeichen,
            "event_id": self.event_id,
            "doc_type": self.doc_type,
            "fmt": self.fmt,
            "date": self.date,
            "title": self.title,
            "facts": self.facts,
            "brief": self.brief,
            "author_id": self.author_id,
        }


def world_to_dict(world: World) -> dict[str, Any]:
    return asdict(world)


def world_from_dict(data: dict[str, Any]) -> World:
    firm_raw = data["firm"]
    staff = [Staff(**row) for row in firm_raw["staff"]]
    firm = Firm(
        name=firm_raw["name"],
        seat=firm_raw["seat"],
        founded=firm_raw["founded"],
        street=firm_raw["street"],
        zip_code=firm_raw["zip_code"],
        phone=firm_raw["phone"],
        email=firm_raw["email"],
        staff=staff,
    )
    clients = [Client(**row) for row in data["clients"]]
    matters = []
    for raw in data["matters"]:
        events = [Event(**event) for event in raw.pop("events", [])]
        story_raw = raw.pop("story", None)
        story = Story(**story_raw) if story_raw else None
        matters.append(Matter(**raw, events=events, story=story))
    return World(seed=data["seed"], firm=firm, clients=clients, matters=matters)
