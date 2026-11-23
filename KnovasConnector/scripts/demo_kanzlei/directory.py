"""The firm's directory (people, groups, service accounts) and the share's access lists.

At a client both exist before Knovas arrives: an Active Directory kept by the
firm's IT provider, and folder permissions that grew over the years. The site
(``demo/kanzlei/site``) builds a domain controller and a file server from the
two files written here.

Who may open which *matter* is not in here. That is kept in the practice
software (``AKTENRECHTE``), and the practice software's document store is one
folder everyone can read. This is the situation at a client.
"""
from __future__ import annotations

from demo_kanzlei.catalog import FORMER_STAFF
from demo_kanzlei.models import Staff, World

TITLES = ("Dr. iur.", "lic. iur.", "MLaw", "cand. iur.")

#: (login, what it is for)
SERVICE_ACCOUNTS = [
    ("svc-knovas", "Knovas Connector, nur lesend"),
    ("svc-praxis", "Praxissoftware, Dokumentenablage"),
    ("svc-scan", "Multifunktionsgerät, Scan in Ordner"),
    ("svc-backup", "Datensicherung"),
]

DEPARTMENT = {"partner": "Partner", "associate": "Anwälte", "sekretariat": "Sekretariat"}


def split_name(name: str) -> tuple[str, str]:
    """``Dr. iur. Markus Bär`` → (``Markus``, ``Bär``)."""
    rest = name
    for title in TITLES:
        rest = rest.replace(title, "")
    parts = rest.split()
    return " ".join(parts[:-1]), parts[-1]


def _person(staff: Staff, phone: str, index: int) -> dict:
    given, surname = split_name(staff.name)
    return {
        "key": staff.id,
        "sam": staff.sam,
        "given": given,
        "surname": surname,
        "display": staff.name,
        "mail": staff.email,
        "title": staff.title,
        "department": DEPARTMENT[staff.role],
        "telephone": f"{phone[:-2]}{index + 1:02d}",
        "enabled": True,
        "service": False,
        "description": f"Eintritt {staff.joined}",
    }


def build_directory(world: World, cfg: dict) -> dict:
    estate = cfg["estate"]
    staff = world.firm.staff
    domain = world.firm.email.split("@", 1)[1]
    users = [_person(s, world.firm.phone, index) for index, s in enumerate(staff)]
    for (key, name, role, title, _initials, login, _joined, left) in FORMER_STAFF:
        given, surname = split_name(name)
        users.append({
            "key": key, "sam": login, "given": given, "surname": surname, "display": name,
            "mail": f"{key}@{domain}", "title": title, "department": DEPARTMENT[role], "telephone": "",
            "enabled": False, "service": False, "description": f"Austritt {left}",
        })
    for login, purpose in SERVICE_ACCOUNTS:
        users.append({
            "key": login, "sam": login, "given": "", "surname": login, "display": login, "mail": "",
            "title": "", "department": "IT", "telephone": "", "enabled": True, "service": True,
            "description": purpose,
        })

    def logins(*, role: str | None = None, team: str | None = None) -> list[str]:
        return [s.sam for s in staff if (role is None or s.role == role) and (team is None or s.team == team)]

    groups = [
        {"name": "GG_Partner", "description": "Partner", "members": logins(role="partner"), "member_groups": []},
        {"name": "GG_Anwaelte", "description": "Alle Anwältinnen und Anwälte", "members": logins(role="associate"),
         "member_groups": ["GG_Partner"]},
        {"name": "GG_Sekretariat", "description": "Sekretariat", "members": logins(role="sekretariat"), "member_groups": []},
        {"name": "GG_Alle", "description": "Alle Mitarbeitenden", "members": [],
         "member_groups": ["GG_Anwaelte", "GG_Sekretariat"]},
        {"name": "GG_Buchhaltung", "description": "Buchhaltung",
         "members": [s.sam for s in staff if "Buchhaltung" in s.title], "member_groups": []},
        {"name": "GG_Team-Prozess", "description": "Team Prozessführung", "members": logins(team="prozess"), "member_groups": []},
        {"name": "GG_Team-Wirtschaft", "description": "Team Wirtschaftsrecht", "members": logins(team="wirtschaft"),
         "member_groups": []},
    ]
    return {
        "realm": estate["realm"],
        "dns_domain": estate["realm"].lower(),
        "netbios": estate["netbios"],
        "share": {"name": estate["share"], "comment": ""},
        "database": estate["database"],
        "users": users,
        "groups": groups,
    }


def build_acl(world: World, cfg: dict) -> list[dict]:
    """Folder permissions, top-down. ``protect`` breaks inheritance."""
    firm = cfg["estate"]["firm_folder"]
    bookkeeper = next(s.sam for s in world.firm.staff if "Buchhaltung" in s.title)

    def grant(principal: str, access: str) -> dict:
        return {"principal": principal, "access": access}

    acl = [
        {"path": "", "protect": True, "grants": [
            grant("GG_Alle", "read"), grant("svc-knovas", "read"), grant("svc-backup", "read"),
            grant("Domain Admins", "full")]},
        {"path": f"{firm}/Praxis", "protect": False, "grants": [grant("GG_Alle", "modify"), grant("svc-praxis", "full")]},
        {"path": f"{firm}/Gemeinsam", "protect": False, "grants": [grant("GG_Alle", "modify")]},
        {"path": f"{firm}/Gemeinsam/Partner", "protect": True, "grants": [
            grant("GG_Partner", "modify"), grant("svc-knovas", "read"), grant("svc-backup", "read")]},
        {"path": f"{firm}/Gemeinsam/Personal", "protect": True, "grants": [
            grant("GG_Partner", "modify"), grant("svc-knovas", "read"), grant("svc-backup", "read")]},
        {"path": f"{firm}/Gemeinsam/Buchhaltung", "protect": True, "grants": [
            grant("GG_Partner", "modify"), grant("GG_Buchhaltung", "modify"), grant("svc-knovas", "read"),
            grant("svc-backup", "read")]},
        # The IT provider never gave the Knovas account this folder: wages.
        {"path": f"{firm}/Gemeinsam/Buchhaltung/Lohn", "protect": True, "grants": [
            grant("GG_Partner", "modify"), grant(bookkeeper, "modify"), grant("svc-backup", "read")]},
        {"path": f"{firm}/Gemeinsam/Scan", "protect": False, "grants": [grant("svc-scan", "modify")]},
        {"path": f"{firm}/Gemeinsam/IT", "protect": True, "grants": [
            grant("GG_Partner", "read"), grant("svc-backup", "modify")]},
        {"path": f"{firm}/Users", "protect": True, "grants": [grant("GG_Alle", "read"), grant("svc-backup", "read")]},
        {"path": f"{firm}/Mail", "protect": True, "grants": [grant("GG_Alle", "read"), grant("svc-backup", "read")]},
    ]
    for person in world.firm.staff:
        for top in ("Users", "Mail"):
            acl.append({"path": f"{firm}/{top}/{person.sam}", "protect": True, "grants": [
                grant(person.sam, "modify"), grant("svc-backup", "read")]})
    return acl


def members_of(directory: dict, group: str) -> set[str]:
    """Logins in ``group``, through nested groups."""
    by_name = {g["name"]: g for g in directory["groups"]}
    found: set[str] = set()
    todo = [group]
    while todo:
        current = by_name.get(todo.pop())
        if not current:
            continue
        found.update(current["members"])
        todo.extend(current["member_groups"])
    return found


def effective_grants(acl: list[dict], rel_path: str) -> tuple[list[dict], str]:
    """The grants that apply to ``rel_path`` and the folder that last broke inheritance."""
    applicable = [e for e in acl if e["path"] == "" or rel_path == e["path"] or rel_path.startswith(e["path"] + "/")]
    applicable.sort(key=lambda e: len(e["path"]))
    grants: list[dict] = []
    decided = ""
    for entry in applicable:
        if entry["protect"]:
            grants = []
            decided = entry["path"]
        grants.extend(entry["grants"])
    return grants, decided


def granted_through(directory: dict, acl: list[dict], rel_path: str, login: str) -> str | None:
    """The group (or the account itself) that lets ``login`` read ``rel_path``; None if nothing does."""
    groups = {g["name"] for g in directory["groups"]}
    grants, _decided = effective_grants(acl, rel_path)
    for item in grants:
        principal = item["principal"]
        if principal == login or (principal in groups and login in members_of(directory, principal)):
            return principal
    return None


def readers(directory: dict, acl: list[dict], rel_path: str) -> tuple[set[str], str]:
    """Who may read ``rel_path`` under the folder permissions, and the entry that decides.

    Returns the logins (people and service accounts) and the deciding path.
    """
    applicable = [e for e in acl if e["path"] == "" or rel_path == e["path"] or rel_path.startswith(e["path"] + "/")]
    applicable.sort(key=lambda e: len(e["path"]))
    grants: list[dict] = []
    decided = ""
    for entry in applicable:
        if entry["protect"]:
            grants = []
            decided = entry["path"]
        grants.extend(entry["grants"])
    groups = {g["name"] for g in directory["groups"]}
    allowed: set[str] = set()
    for item in grants:
        if item["principal"] in groups:
            allowed |= members_of(directory, item["principal"])
        else:
            allowed.add(item["principal"])
    return allowed, decided
