"""Seeded world model → world.json. Never written into the watch root."""
from __future__ import annotations

import json
from pathlib import Path

from demo_kanzlei.catalog import (
    CLIENTS,
    COMPANY_NAMES,
    REGULAR_MATTER_SEEDS,
    bind_staff,
    hero_meierhans,
)
from demo_kanzlei.models import Event, Firm, Matter, World, world_from_dict, world_to_dict
from demo_kanzlei.screen_names import screen_names


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
            matters.append(
                Matter(
                    aktenzeichen=az,
                    title=title,
                    practice_area=area,
                    status=status,
                    client_id=client_id,
                    opposing=in_sachen.split(" ./. ")[-1] if " ./. " in in_sachen else "",
                    in_sachen=in_sachen,
                    counsel_id=counsel_ids[index % len(counsel_ids)],
                    assistant_id=assistant_id,
                    opened=opened,
                    closed=f"{az.split('-')[0]}-11-30" if status == "abgeschlossen" else None,
                    hero=index < 7,
                    events=_generic_events(opened),
                )
            )
        target = int(cfg["matters"]["count"])
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
            matters.append(
                Matter(
                    aktenzeichen=az,
                    title=f"Mandat {az} {client.sector}",
                    practice_area=client.sector,
                    status="laufend" if len(matters) % 3 else "abgeschlossen",
                    client_id=client.id,
                    opposing="Gegenpartei Demo",
                    in_sachen=f"{client.name} ./. Gegenpartei Demo",
                    counsel_id=counsel_ids[len(matters) % len(counsel_ids)],
                    assistant_id=assistant_id,
                    opened=f"{year}-04-15",
                    hero=False,
                    events=_generic_events(f"{year}-04-15"),
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


def _generic_events(opened: str) -> list[Event]:
    year = opened[:4]
    return [
        Event("e01", opened, "mandatsannahme", "Mandatsannahme."),
        Event("e02", f"{year}-04-20", "vollmacht", "Vollmacht."),
        Event("e03", f"{year}-05-02", "aktennotiz", "Interne Notiz."),
        Event("e04", f"{year}-05-15", "email", "E-Mail an Mandant."),
        Event("e05", f"{year}-06-01", "brief", "Brief an Gegenpartei."),
        Event("e06", f"{year}-07-10", "klage", "Rechtsschrift."),
        Event("e07", f"{year}-08-01", "email", "E-Mail-Thread."),
        Event("e08", f"{year}-09-12", "honorarnote", "Honorarnote."),
    ]


def write_world(world: World, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(world_to_dict(world), ensure_ascii=False, indent=2), encoding="utf-8")


def read_world(path: Path) -> World:
    return world_from_dict(json.loads(path.read_text(encoding="utf-8")))
