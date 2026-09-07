"""Seeded world model → world.json. Never written into the watch root."""
from __future__ import annotations

import json
from pathlib import Path

from demo_kanzlei.catalog import (
    CLIENTS,
    COMPANY_NAMES,
    REGULAR_MATTER_SEEDS,
    bind_staff,
    events_for_mandate,
    hero_meierhans,
)
from demo_kanzlei.models import Firm, Matter, World, world_from_dict, world_to_dict
from demo_kanzlei.screen_names import screen_names
from demo_kanzlei.stories import story_for


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


def build_world(cfg: dict, *, pilot: bool, skip_zefix: bool) -> World:
    domain = cfg["firm"]["email_domain"]
    firm_name = pick_firm_name(cfg, skip_zefix=skip_zefix)
    staff = bind_staff(domain)
    if not skip_zefix:
        screen_names([firm_name, *COMPANY_NAMES])
    clients = list(CLIENTS)
    matters = [hero_meierhans()]
    if not pilot:
        counsel_ids = [s.id for s in staff if s.role in {"partner", "associate"}]
        assistant_id = next(s.id for s in staff if s.role == "associate")
        for index, seed in enumerate(REGULAR_MATTER_SEEDS):
            az, client_id, area, title, in_sachen, status = seed
            opened = f"{az.split('-')[0]}-03-01"
            story = story_for(
                aktenzeichen=az,
                practice_area=area,
                seed=int(cfg["seed"]),
                client_name=next(c.name for c in clients if c.id == client_id),
            )
            matters.append(
                Matter(
                    aktenzeichen=az,
                    title=story.title,
                    practice_area=area,
                    status=status,
                    client_id=client_id,
                    opposing=story.opposing,
                    in_sachen=in_sachen,
                    counsel_id=counsel_ids[index % len(counsel_ids)],
                    assistant_id=assistant_id,
                    opened=opened,
                    closed=f"{az.split('-')[0]}-11-30" if status == "abgeschlossen" else None,
                    hero=index < 7,
                    story=story,
                    events=events_for_mandate(
                        story=story, practice_area=area, status=status, opened=opened
                    ),
                )
            )
        target = int(cfg["matters"]["count"])
        remaining = _remaining_statuses(cfg, matters)
        year = 2019
        seq = 1
        while len(matters) < target:
            az = f"{year}-{seq:03d}"
            if any(m.aktenzeichen == az for m in matters):
                seq += 1
                if seq > 120:
                    year += 1
                    seq = 1
                continue
            client = clients[(len(matters)) % len(clients)]
            status = remaining.pop(0) if remaining else "laufend"
            opened = f"{year}-04-15"
            story = story_for(
                aktenzeichen=az,
                practice_area=client.sector,
                seed=int(cfg["seed"]),
                client_name=client.name,
            )
            matters.append(
                Matter(
                    aktenzeichen=az,
                    title=story.title,
                    practice_area=client.sector,
                    status=status,
                    client_id=client.id,
                    opposing=story.opposing,
                    in_sachen=f"{client.name} ./. {story.opposing}",
                    counsel_id=counsel_ids[len(matters) % len(counsel_ids)],
                    assistant_id=assistant_id,
                    opened=opened,
                    closed=f"{year}-11-30" if status == "abgeschlossen" else None,
                    hero=False,
                    story=story,
                    events=events_for_mandate(
                        story=story,
                        practice_area=client.sector,
                        status=status,
                        opened=opened,
                    ),
                )
            )
            seq += 1
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
