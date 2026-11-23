"""Seeded world model → world.json. Never written into the watch root."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from demo_kanzlei.catalog import (
    CLIENTS,
    COMPANY_NAMES,
    REGULAR_MATTER_SEEDS,
    bind_staff,
    events_for_mandate,
    hero_meierhans,
    mandate_family,
)
from demo_kanzlei.matter_numbers import as_of, opened_anchored, split, year_counts
from demo_kanzlei.models import Event, Firm, Matter, Staff, World, world_from_dict, world_to_dict
from demo_kanzlei.screen_names import screen_names
from demo_kanzlei.stories import story_for

#: Who runs the two confidential matters (they are locked in the practice software).
COUNSEL_OVERRIDES = {"2024-055": ("as", "sk"), "2024-112": ("as", "jf")}

#: Steps the firm skipped. The process truth file names them as missing.
PLANTED_GAPS = {"2025-004": {"kostenvorschuss"}, "2024-112": {"konflikt"}}

FILLER_FIRST_YEAR = 2020


def load_config(path: Path) -> dict:
    import tomllib

    with path.open("rb") as handle:
        return tomllib.load(handle)


def pick_firm_name(cfg: dict, *, skip_zefix: bool) -> str:
    candidates = list(cfg["firm"]["name_candidates"])
    if skip_zefix:
        return candidates[0]
    last_error = None
    for name in candidates:
        try:
            screen_names([name])
            return name
        except Exception as exc:  # NameCollision or HTTP
            last_error = exc
            continue
    raise RuntimeError(f"no firm name passed Zefix screening: {last_error}")


def _pick_counsel(staff: list[Staff], opened: str, index: int, area: str) -> tuple[str, str]:
    """A lawyer who worked here on the opening day, and an associate of the same team."""
    team = "prozess" if mandate_family(area) == "litigation" else "wirtschaft"
    lawyers = [s for s in staff if s.role in {"partner", "associate"} and s.joined <= opened]
    in_team = [s for s in lawyers if s.team == team] or lawyers
    counsel = in_team[index % len(in_team)]
    associates = [s for s in in_team if s.role == "associate" and s.id != counsel.id]
    if not associates:
        associates = [s for s in lawyers if s.id != counsel.id] or [counsel]
    return counsel.id, associates[index % len(associates)].id


def _filler_references(count: int, taken: set[str], last_year: int) -> list[str]:
    """Spread the simply written matters over recent years, with low running numbers."""
    years = list(range(FILLER_FIRST_YEAR, last_year + 1))
    refs: list[str] = []
    index = 0
    while len(refs) < count:
        year = years[index % len(years)]
        seq = 3 + 4 * (index // len(years)) + (index % 3)
        ref = f"{year}-{seq:03d}"
        while ref in taken:
            seq += 1
            ref = f"{year}-{seq:03d}"
        taken.add(ref)
        refs.append(ref)
        index += 1
    return refs


def _trim(events: list[Event], today: str, gaps: set[str]) -> list[Event]:
    """Nothing is dated after the stand-in's today; planted gaps are left out."""
    return [event for event in events if event.date <= today and event.kind not in gaps]


def _closed(opened: str, status: str) -> str | None:
    if status != "abgeschlossen":
        return None
    return (date.fromisoformat(opened) + timedelta(days=230)).isoformat()


def build_world(cfg: dict, *, pilot: bool, skip_zefix: bool) -> World:
    domain = cfg["firm"]["email_domain"]
    firm_name = pick_firm_name(cfg, skip_zefix=skip_zefix)
    staff = bind_staff(domain)
    if not skip_zefix:
        screen_names([firm_name, *COMPANY_NAMES])
    clients = list(CLIENTS)
    today = as_of(cfg)
    hero = hero_meierhans()
    matters = [hero]
    if not pilot:
        target = int(cfg["matters"]["count"])
        named = [seed[0] for seed in REGULAR_MATTER_SEEDS]
        taken = {hero.aktenzeichen, *named}
        fillers = _filler_references(max(0, target - 1 - len(named)), taken, today.year)
        counts = year_counts(cfg, [hero.aktenzeichen, *named, *fillers], pilot=False)

        hero_year, hero_seq = split(hero.aktenzeichen)
        fixed = {hero_year: [(hero_seq, date.fromisoformat(hero.opened))]}

        def opened_for(ref: str) -> str:
            year, seq = split(ref)
            return opened_anchored(year, seq, counts[year], today, fixed.get(year, [])).isoformat()

        for index, seed in enumerate(REGULAR_MATTER_SEEDS):
            az, client_id, area, _title, in_sachen, status = seed
            opened = opened_for(az)
            story = story_for(
                aktenzeichen=az,
                practice_area=area,
                seed=int(cfg["seed"]),
                client_name=next(c.name for c in clients if c.id == client_id),
            )
            counsel_id, assistant_id = COUNSEL_OVERRIDES.get(az) or _pick_counsel(staff, opened, index, area)
            matters.append(
                Matter(
                    aktenzeichen=az,
                    title=story.title,
                    practice_area=area,
                    status=status,
                    client_id=client_id,
                    opposing=story.opposing,
                    in_sachen=in_sachen,
                    counsel_id=counsel_id,
                    assistant_id=assistant_id,
                    opened=opened,
                    closed=_closed(opened, status),
                    hero=index < 7,
                    story=story,
                    events=_trim(
                        events_for_mandate(story=story, practice_area=area, status=status, opened=opened),
                        today.isoformat(),
                        PLANTED_GAPS.get(az, set()),
                    ),
                )
            )
        remaining = _remaining_statuses(cfg, matters)
        for index, az in enumerate(fillers):
            client = clients[(len(matters)) % len(clients)]
            status = remaining.pop(0) if remaining else "laufend"
            opened = opened_for(az)
            story = story_for(
                aktenzeichen=az,
                practice_area=client.sector,
                seed=int(cfg["seed"]),
                client_name=client.name,
            )
            counsel_id, assistant_id = _pick_counsel(staff, opened, index, client.sector)
            matters.append(
                Matter(
                    aktenzeichen=az,
                    title=story.title,
                    practice_area=client.sector,
                    status=status,
                    client_id=client.id,
                    opposing=story.opposing,
                    in_sachen=f"{client.name} ./. {story.opposing}",
                    counsel_id=counsel_id,
                    assistant_id=assistant_id,
                    opened=opened,
                    closed=_closed(opened, status),
                    hero=False,
                    story=story,
                    events=_trim(
                        events_for_mandate(
                            story=story,
                            practice_area=client.sector,
                            status=status,
                            opened=opened,
                        ),
                        today.isoformat(),
                        set(),
                    ),
                )
            )
    return World(
        seed=int(cfg["seed"]),
        firm=Firm(
            name=firm_name,
            seat=cfg["seat"],
            founded=int(cfg["founded"]),
            street=cfg["firm"]["street"],
            zip_code=cfg["firm"]["zip_code"],
            phone=cfg["firm"]["phone"],
            email=f"info@{domain}",
            staff=staff,
        ),
        clients=clients,
        matters=matters,
    )


def _remaining_statuses(cfg: dict, matters: list[Matter]) -> list[str]:
    wanted = {
        "laufend": int(cfg["matters"]["open"]),
        "abgeschlossen": int(cfg["matters"]["closed"]),
        "sistiert": int(cfg["matters"]["stayed"]),
    }
    for matter in matters:
        if matter.status in wanted:
            wanted[matter.status] -= 1
    leftover: list[str] = []
    for status, count in wanted.items():
        leftover.extend([status] * max(0, count))
    return leftover


def write_world(world: World, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(world_to_dict(world), ensure_ascii=False, indent=2), encoding="utf-8")


def read_world(path: Path) -> World:
    return world_from_dict(json.loads(path.read_text(encoding="utf-8")))
