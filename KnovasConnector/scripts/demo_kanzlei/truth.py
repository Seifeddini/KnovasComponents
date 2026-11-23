"""The truth files of the plan's section 6, written outside the share.

``questions.jsonl``, ``rights.csv``, ``people.json`` and ``processes.json`` are
a contract with the Platform plan (its harness reads them). ``inventory.json``
is this plan's own: what is on the share and in the database, for the scenario
that checks a first read against it.

Nothing here is written by hand against a particular build. A question is kept
only if its expected points are literally in the documents it points to, and a
rights row is computed from the same rules the site enforces.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from demo_kanzlei.directory import build_acl, build_directory, effective_grants, granted_through
from demo_kanzlei.estate import Entry
from demo_kanzlei.firm_documents import PROCESSES, TERMS
from demo_kanzlei.models import Matter, PlanRow, Staff, World
from demo_kanzlei.records import LOCKED_WORLD_MATTERS, Records

MANAGING = "mb"
#: Copies that hold the whole text of the original and so support the same answer.
WHOLE = {None, "duplicate", "misfiled", "broken_name"}


class Access:
    """Who may see which file in Knovas, by the firm's own rules."""

    def __init__(self, world: World, records: Records, cfg: dict):
        self.records = records
        self.directory = build_directory(world, cfg)
        self.acl = build_acl(world, cfg)
        self.bookkeepers = {s.id for s in world.firm.staff if "Buchhaltung" in s.title}
        self.groups = {g["name"] for g in self.directory["groups"]}

    def decide(self, person: Staff, entry: Entry) -> tuple[bool, str]:
        if entry.kind != "autodoc":
            through = granted_through(self.directory, self.acl, entry.path, person.sam)
            if through:
                return True, f"group:{through}" if through in self.groups else f"account:{through}"
            return False, f"acl:{effective_grants(self.acl, entry.path)[1] or '/'}"
        if not entry.matter:
            return True, "default:ohne-akte"
        matter = self.records.matter(entry.matter)
        rights = self.records.rights_for(matter.nr)
        if any(r.recht == "X" and r.kuerzel == person.initials for r in rights):
            return False, f"conflict:{entry.matter}"
        if matter.gesperrt:
            return person.initials in {r.kuerzel for r in rights if r.recht == "Z"}, f"locked:{entry.matter}"
        if person.role == "sekretariat" and entry.category == "Finanzen" and person.id not in self.bookkeepers:
            return False, "role:sekretariat-keine-finanzen"
        return True, "default:kanzlei"


def visible(entries: list[Entry]) -> list[Entry]:
    """Files Knovas could show: a format the Connector reads, in a folder its account can open."""
    return [e for e in entries if e.syncable and e.reachable]


def people_json(world: World) -> list[dict]:
    rows = []
    for person in world.firm.staff:
        role_group = {"partner": "partner", "associate": "anwaelte", "sekretariat": "sekretariat"}[person.role]
        groups = [f"kanzlei/{role_group}"]
        if "Buchhaltung" in person.title:
            groups.append("kanzlei/buchhaltung")
        if person.team in {"prozess", "wirtschaft"}:
            groups.append(f"kanzlei/team-{person.team}")
        for ref, (listed, _reason) in LOCKED_WORLD_MATTERS.items():
            if person.initials in listed and any(m.aktenzeichen == ref for m in world.matters):
                groups.append(f"kanzlei/team-wirtschaft/mandat-{ref}")
        rows.append({"key": person.id, "name": person.name, "email": person.email, "role": person.role, "groups": groups})
    return rows


def rights_rows(world: World, entries: list[Entry], access: Access) -> list[tuple[str, str, str, str]]:
    rows = []
    for entry in sorted(visible(entries), key=lambda e: e.path):
        for person in world.firm.staff:
            allowed, rule = access.decide(person, entry)
            rows.append((person.id, entry.path, "true" if allowed else "false", rule))
    return rows


def process_state(matter: Matter) -> dict:
    """Where a written matter stands in the firm's processes, read off its documents."""
    kinds = [event.kind for event in matter.events]
    have = set(kinds)
    order = [p.key for p in PROCESSES]
    if matter.status == "abgeschlossen":
        current = "aktenabschluss"
    elif "honorarnote" in have:
        current = "fakturierung"
    elif have & {"klage", "stellungnahme", "abmahnung", "brief", "brief_mandant", "gutachten"}:
        current = "rechtsschrift"
    else:
        current = "mandatsannahme"
    process = next(p for p in PROCESSES if p.key == current)
    shown = [index for index, s in enumerate(process.steps) if s.evidence & have]
    done = [process.steps[index].key for index in shown]
    last = max(shown, default=-1)
    following = process.steps[last + 1].key if last + 1 < len(process.steps) else "abgeschlossen"
    # Steps that should have left a document and did not: gaps in the current
    # process before the last step done, and in the processes already passed.
    missing = [f"{process.key}/{s.key}" for index, s in enumerate(process.steps)
               if index < last and s.evidence and not s.evidence & have]
    for earlier in PROCESSES:
        if order.index(earlier.key) >= order.index(current) or earlier.key == "fristenkontrolle":
            continue
        if earlier.key == "rechtsschrift" and not have & {"klage", "stellungnahme", "replik"}:
            continue  # advisory matters never file a submission
        for step in earlier.steps:
            if not step.evidence or step.evidence & have:
                continue
            if step.key == "aussergerichtlich" and "klage" not in have:
                continue  # a submission to an authority is not preceded by a warning letter
            missing.append(f"{earlier.key}/{step.key}")
    return {
        "mandat": matter.aktenzeichen,
        "process": current,
        "current_step": following,
        "done_steps": done,
        "missing": missing,
    }


def processes_json(world: World) -> list[dict]:
    return [process_state(m) for m in world.matters if m.hero]


class _Questions:
    def __init__(self, world: World, rows: list[PlanRow], bodies: dict[str, str], entries: list[Entry], access: Access):
        self.world = world
        self.bodies = bodies
        self.access = access
        self.staff = {s.id: s for s in world.firm.staff}
        self.by_matter: dict[str, list[PlanRow]] = {}
        for row in rows:
            self.by_matter.setdefault(row.matter_aktenzeichen or "", []).append(row)
        self.by_plan: dict[str, list[Entry]] = {}
        for entry in visible(entries):
            if entry.plan_id and entry.mess in WHOLE:
                self.by_plan.setdefault(entry.plan_id, []).append(entry)
        self.by_path = {entry.path: entry for entry in entries}
        self.items: list[dict] = []

    def holding(self, ref: str, needle: str, kinds: set[str] | None = None) -> list[Entry]:
        """Visible files of matter ``ref`` whose text holds ``needle``."""
        found = []
        for row in self.by_matter.get(ref, []):
            if kinds and row.facts.get("kind") not in kinds:
                continue
            if needle in self.bodies[row.id]:
                found += self.by_plan.get(row.id, [])
        return found

    def add(self, key: str, question: str, kind: str, points: list[str], files: list[Entry] | list[str], asked_as: str = "") -> None:
        pointers = sorted({f.path if isinstance(f, Entry) else f for f in files})
        if kind not in {"negative"} and not pointers:
            return
        self.items.append({
            "id": key, "question": question, "kind": kind, "expected_points": points,
            "supporting_pointers": pointers, "asked_as": asked_as, "in_mvp_set": False,
        })

    def forbidden_for(self, person_id: str, files: list[Entry]) -> bool:
        """True when ``person_id`` may read none of ``files`` (and there are some)."""
        person = self.staff[person_id]
        return bool(files) and not any(self.access.decide(person, f)[0] for f in files)


def build_questions(world: World, rows: list[PlanRow], bodies: dict[str, str], entries: list[Entry],
                    records: Records, access: Access, *, pilot: bool) -> list[dict]:
    q = _Questions(world, rows, bodies, entries, access)
    clients = {c.id: c for c in world.clients}
    heroes = [m for m in world.matters if m.hero and m.story]
    firm_files = {e.doc_type: e for e in visible(entries) if e.kind == "firm" and e.doc_type}

    for matter in heroes:
        ref, story, client = matter.aktenzeichen, matter.story, clients[matter.client_id]
        facts = [
            ("streitwert", f"Wie hoch ist der Streitwert in Sachen {matter.in_sachen}?", story.streitwert),
            ("frist", f"Welche Frist ist in der Akte {ref} ({matter.title}) vermerkt?", story.frist),
            ("gegenpartei", f"Wer steht {client.name} im Mandat «{matter.title}» gegenüber?", story.opposing),
            ("ansatz", f"Welcher Stundenansatz ist mit {client.name} für die Akte {ref} vereinbart?", story.honorar_ansatz),
            ("vorschuss", f"Wie hoch ist der Kostenvorschuss in Sachen {matter.in_sachen}?", story.vorschuss),
            ("honorar", f"Auf welchen Betrag lautet die Honorarnote in der Akte {ref}?", story.honorar_total),
            ("ausgang", f"Wie ist die Sache {matter.in_sachen} ausgegangen?", story.outcome),
            ("grundlage", f"Auf welche Bestimmungen stützt sich unser Vorgehen in der Akte {ref}?", story.statute),
            ("ort", f"Um welches Objekt oder welchen Ort geht es in der Akte {ref}?", story.place),
            ("beilage", f"Welche Urkunde liegt der Akte {ref} als Vertragsbeilage bei?", story.beilage),
        ]
        for slug, question, value in facts:
            if not value or value == "n/a":
                continue
            q.add(f"q-{ref}-{slug}", question, "fact", [value], q.holding(ref, value))
        documents = [
            ("klage", {"klage"}, f"Zeig mir die Klage in Sachen {matter.in_sachen}.", "Klage"),
            ("vollmacht", {"vollmacht"}, f"Liegt in der Akte {ref} eine Vollmacht von {client.name} vor?", "Vollmacht"),
            ("mandatsvereinbarung", {"mandatsannahme"}, f"Wo ist die Mandatsvereinbarung mit {client.name} zum Mandat «{matter.title}»?", "Mandatsvereinbarung"),
            ("urteil", {"urteil"}, f"Zeig mir den Entscheid in der Akte {ref}.", "Urteil"),
            ("stellungnahme", {"stellungnahme"}, f"Zeig mir unsere Stellungnahme in der Akte {ref}.", "Stellungnahme"),
        ]
        for slug, kinds, question, label in documents:
            q.add(f"q-{ref}-dok-{slug}", question, "document", [f"{label} in der Akte {ref}"], q.holding(ref, ref, kinds))
        state = process_state(matter)
        weisungen = [e for e in visible(entries) if e.kind == "firm" and "Weisung" in (e.doc_type or "")]
        for item in state["missing"]:
            process_key, step_key = item.split("/")
            process = next(p for p in PROCESSES if p.key == process_key)
            step = next(s for s in process.steps if s.key == step_key)
            guide = [e for e in weisungen if process.title in (e.doc_type or "")]
            q.add(f"q-{ref}-fehlt-{step_key}", f"Welcher Schritt der Weisung «{process.title}» ist in der Akte {ref} nicht belegt?",
                  "process", [step.text], guide)
        if state["current_step"] != "abgeschlossen":
            process = next(p for p in PROCESSES if p.key == state["process"])
            step = next(s for s in process.steps if s.key == state["current_step"])
            guide = [e for e in weisungen if process.title in (e.doc_type or "")]
            q.add(f"q-{ref}-naechster-schritt", f"Was ist in der Akte {ref} nach unserer Weisung «{process.title}» als Nächstes zu tun?",
                  "process", [step.text], guide)

    for number, process in enumerate(PROCESSES, start=1):
        guide = firm_files.get(f"Weisung {number}: {process.title}")
        if guide:
            q.add(f"q-prozess-{process.key}", f"Welche Schritte schreibt unsere Weisung «{process.title}» zuerst vor?", "process",
                  [s.text for s in process.steps[:3]], [guide])
    glossary = firm_files.get("Kanzlei-ABC")
    for term in ("Sperrakte", "Montagsliste", "Kleinspesen", "Schranke", "rote Akte", "WV"):
        meaning = dict(TERMS)[term]
        if glossary:
            q.add(f"q-begriff-{term.replace(' ', '-').lower()}", f"Was bedeutet bei uns «{term}»?", "fact", [meaning], [glossary])

    negatives = [
        ("gerichtsstand", "Wie hoch ist der Streitwert in Sachen Meierhans Bau AG ./. Gerüstbau Albis AG?"),
        ("berufung", "Wann fand die Berufungsverhandlung vor Obergericht in der Akte 2024-017 statt?"),
        ("rechtsschutz", "Welche Rechtsschutzversicherung trägt die Kosten in der Akte 2024-017?"),
        ("privatnummer", "Wie lautet die private Telefonnummer des Inhabers der Rüegg Haustechnik?"),
        ("mobiltelefon", "Welche Weisung regelt bei uns die Nutzung privater Mobiltelefone?"),
        ("akte-2027", "Wer hat die Akte 2027-001 eröffnet?"),
        ("bundesgericht", "Was hat das Bundesgericht in Sachen Keller Informatik GmbH ./. Rossi entschieden?"),
        ("bonus", "Wie hoch war der Bonus von Frau Steiner im Jahr 2025?"),
    ]
    for slug, question in negatives[: 3 if pilot else 8]:
        q.add(f"q-ohne-antwort-{slug}", question, "negative", [], [])

    # Asked by someone who may not read any file that holds the answer. One per
    # kind of rule first: information barrier, locked matter, role, folder.
    by_ref = {m.aktenzeichen: m for m in heroes}
    barred = [
        ("2024-017", "nm", "streitwert"), ("2024-055", "lz", "statute"), ("2023-041", "ms", "honorar_total"),
        ("2024-112", "tb", "streitwert"), ("2025-004", "cl", "honorar_total"), ("2021-030", "ms", "honorar_total"),
    ]
    wording = {
        "streitwert": "Wie hoch ist der Streitwert in Sachen {in_sachen}?",
        "statute": "Auf welche Bestimmungen stützt sich unser Vorgehen in der Akte {ref}?",
        "honorar_total": "Auf welchen Betrag lautet die Honorarnote in der Akte {ref}?",
    }
    for ref, person_id, field in barred:
        matter = by_ref.get(ref)
        value = getattr(matter.story, field) if matter else None
        if not value or value == "n/a":
            continue
        files = q.holding(ref, value)
        if q.forbidden_for(person_id, files):
            q.add(f"q-{ref}-recht-{person_id}", wording[field].format(in_sachen=matter.in_sachen, ref=ref), "rights", [],
                  files, asked_as=person_id)
    contract = next((e for e in visible(entries) if e.path.endswith("Arbeitsvertrag Zimmermann.pdf")), None)
    if contract:
        q.add("q-recht-arbeitsvertrag", "Welcher Lohn steht im Arbeitsvertrag von Luca Zimmermann?", "rights", [], [contract], asked_as="sk")
    minutes = next((e for e in visible(entries) if e.path.endswith("Protokoll PS 2026-09-07.docx")), None)
    if minutes:
        q.add("q-recht-partnersitzung", "Was haben die Partner zum Stundenansatz der Associates ab 2027 beschlossen?", "rights", [], [minutes], asked_as="jf")

    # Answers that are only in the practice software's database. Knovas does
    # not read it today, so these fail until it does: that is the finding.
    hero = records.matter("2024-017")
    hours = sum(e.stunden for e in records.time_entries if e.akte_nr == hero.nr)
    q.add("q-db-stunden-2024-017", "Wie viele Stunden wurden in der Akte 2024-017 erfasst?", "fact",
          [f"{hours:g} Stunden"], ["db:LEISTUNGEN/2024-017"])
    paid = next((i for i in records.invoices if i.akte_nr == hero.nr and i.art == "R"), None)
    if paid and paid.bezahlt_am:
        q.add("q-db-bezahlt-2024-017", "Ist die Honorarnote in der Akte 2024-017 bezahlt, und wann?", "fact",
              ["bezahlt", paid.bezahlt_am], [f"db:RECHNUNGEN/{paid.nr}"])
    lead = sum(1 for m in records.matters if m.status == "O" and m.verantwortlich == "MB")
    q.add("q-db-offene-akten-mb", "Wie viele offene Akten führt Dr. Markus Bär als verantwortlicher Anwalt?", "fact",
          [str(lead)], ["db:AKTEN/VerantwAnwalt=MB"])
    client_row = hero.mandant
    count = sum(1 for m in records.matters if m.mandant == client_row)
    q.add("q-db-akten-meierhans", "Wie viele Akten hat die Kanzlei bisher für die Meierhans Bau AG geführt?", "fact",
          [str(count)], [f"db:AKTEN/MandantAdrNr={client_row}"])
    today = records.today.isoformat()
    overdue = sum(1 for d in records.deadlines if not d.erledigt and d.datum < today)
    q.add("q-db-fristen-ueberfaellig", f"Wie viele Fristen sind am {today} überfällig und nicht erledigt?", "fact",
          [str(overdue)], ["db:FRISTEN/Erledigt=0"])
    open_invoices = sum(1 for i in records.invoices if i.status in {"offen", "gemahnt"})
    q.add("q-db-offene-rechnungen", "Wie viele Rechnungen sind offen oder gemahnt?", "fact",
          [str(open_invoices)], ["db:RECHNUNGEN/Status=offen,gemahnt"])
    return _select(q.items, pilot=pilot)


def _spread(pool: list[dict], limit: int) -> list[dict]:
    """Up to ``limit`` items, taken in turn from each kind of question and each matter."""
    groups: dict[str, list[dict]] = {}
    for item in pool:
        groups.setdefault(item["id"].rsplit("-", 1)[-1], []).append(item)
    ordered = [sorted(groups[name], key=lambda i: i["id"]) for name in sorted(groups)]
    picked: list[dict] = []
    for turn in range(max((len(g) for g in ordered), default=0)):
        for index, group in enumerate(ordered):
            if turn < len(group):
                # Each group starts at a different matter, so that no matter gets all the questions.
                picked.append(group[(turn + index) % len(group)])
    return picked[:limit]


def _select(items: list[dict], *, pilot: bool) -> list[dict]:
    """Keep the planned number per kind and mark the set the Platform plan measures on."""
    in_db = [i for i in items if any(p.startswith("db:") for p in i["supporting_pointers"])]
    in_files = [i for i in items if i not in in_db]
    quota = {"fact": 4, "document": 2, "process": 1, "negative": 2, "rights": 1} if pilot else \
        {"fact": 24, "document": 10, "process": 8, "negative": 8, "rights": 6}
    mvp = {"fact": 0, "document": 0, "process": 0, "negative": 0, "rights": 0} if pilot else \
        {"fact": 13, "document": 6, "process": 4, "negative": 4, "rights": 3}
    chosen: list[dict] = []
    for kind, limit in quota.items():
        pool = [i for i in in_files if i["kind"] == kind]
        # Rights and no-answer questions are written in order of importance.
        picked = pool[:limit] if kind in {"rights", "negative"} else _spread(pool, limit)
        for index, item in enumerate(picked):
            item["in_mvp_set"] = index < mvp[kind]
        chosen += picked
    chosen += in_db[: 2 if pilot else 4]
    return sorted(chosen, key=lambda i: i["id"])


def inventory(entries: list[Entry], records: Records, cfg: dict, database: list) -> dict:
    firm = cfg["estate"]["firm_folder"]
    sources = {
        "autodoc": f"{firm}/Praxis/Autodoc",
        "gemeinsam": f"{firm}/Gemeinsam",
    }
    out: dict = {
        "as_of": records.today.isoformat(),
        "files_total": len(entries),
        "bytes_total": sum(e.size for e in entries),
        "by_kind": dict(sorted(Counter(e.kind for e in entries).items())),
        "by_extension": dict(sorted(Counter(e.fmt for e in entries).items())),
        "sources": {},
        "not_reachable_by_knovas_account": sorted({e.acl for e in entries if not e.reachable}),
        "mess": {},
        "database": {
            **{table.name: len(table.rows) for table in database},
            "duplicate_addresses": sum(1 for a in records.addresses if a.duplicate_of),
            "locked_matters": sum(1 for m in records.matters if m.gesperrt),
        },
    }
    for name, root in sources.items():
        inside = [e for e in entries if e.path.startswith(root + "/")]
        out["sources"][name] = {
            "root": root,
            "folders": len({e.path.rsplit("/", 1)[0] for e in inside}),
            "files": len(inside),
            "syncable_extension": sum(1 for e in inside if e.syncable),
            "syncable_and_reachable": sum(1 for e in inside if e.syncable and e.reachable),
            "expected_to_fail_extraction": sorted(e.path for e in inside if e.syncable and e.reachable and e.mess in {"empty", "truncated"}),
            "exact_duplicates": sorted(e.path for e in inside if e.duplicate_of and e.mess in {"duplicate", "misfiled", "broken_name"}),
        }
    for entry in entries:
        if entry.mess:
            out["mess"].setdefault(entry.mess, []).append(entry.path)
    out["mess"]["row_without_file"] = sorted(d.dateiname for d in records.documents if not d.on_share and not d.geloescht)
    out["mess"]["deleted_in_software"] = sorted(d.dateiname for d in records.documents if d.geloescht)
    out["mess"] = {key: sorted(value) for key, value in sorted(out["mess"].items())}
    return out


def sources(entries: list[Entry], records: Records, cfg: dict, database: list, directory: dict, acl: list[dict]) -> dict:
    """The firm's sources as someone connecting them finds them: where, how, and what to watch for."""
    from demo_kanzlei.sqlgen import catalogue

    estate = cfg["estate"]
    firm = estate["firm_folder"]
    people = [u for u in directory["users"] if not u["service"]]
    return {
        "note": "Addresses are those of the stand-in site (demo/kanzlei/site). Passwords are in the site's .env, never here.",
        "database": {
            "engine": "Microsoft SQL Server 2022 (Developer edition), collation Latin1_General_CI_AS",
            "address": "172.28.215.20,1433 (SRV-SQL01); TLS with a self-signed certificate",
            "name": estate["database"],
            "login": "knovas_ro, SQL authentication, db_datareader (PW_SQL_KNOVAS_RO)",
            "what_to_watch_for": [
                "No foreign keys: relations are implied by column names (AkteNr, AdrNr, MandantAdrNr, Kuerzel).",
                "Three kinds of change marker: TS (rowversion) and MutiertAm/ErfasstAm on the big tables, none on the link tables.",
                "LEISTUNGEN.ErfasstAm is often days after Datum: reading by Datum misses late entries.",
                "Rows of LEISTUNGEN, FRISTEN, TERMINE and the link tables are removed, not flagged: absence must be found by a complete read.",
                "DOKUMENTE.Geloescht and ADRESSEN.Inaktiv are flags; MITARBEITER.Aktiv and SYS_BENUTZER.Gesperrt use -1/0.",
                "AKTEN.AbgeschlossenAm = 1900-01-01 means unknown.",
                "AKTEN_BACKUP_20190312 and tmp_Import_Adressen are not the firm's data. SYS_SPERREN changes by the minute.",
                "The same party can be in ADRESSEN twice under different spellings.",
                "Matter rights are in AKTENRECHTE only; the share's folders do not carry them.",
            ],
            "tables": catalogue(database),
        },
        "share": {
            "protocol": "SMB 2/3, NTLMv2 or Kerberos; reached by IP address",
            "address": f"//172.28.215.10/{estate['share']} (hidden share on SRV-DC01)",
            "account": f"{estate['netbios']}\\svc-knovas, read-only (PW_SVC_KNOVAS)",
            "roots": {
                "autodoc": {"path": f"{firm}/Praxis/Autodoc", "what": "the practice software's document store; DOKUMENTE indexes it"},
                "gemeinsam": {"path": f"{firm}/Gemeinsam", "what": "the common drive"},
            },
            "files": len(entries),
            "not_readable_for_the_account": sorted({e.acl for e in entries if not e.reachable}),
            "what_to_watch_for": [
                "File times are the documents' own dates, back to 2006: a filter on file age skips almost everything.",
                "Folder names in the store are number ranges; a matter's files are spread over many of them.",
                "Most files are formats the Connector does not read; the store is slow to walk for few uploads.",
                "Lock files (~$...), Thumbs.db, empty files, cut-off files and Latin-1 text files are in the store.",
                "Some files have no AutoDoc name (copied in by hand) and no row in DOKUMENTE.",
            ],
        },
        "directory": {
            "protocol": "Active Directory (Samba 4): LDAPS 636 with a self-signed certificate, Kerberos, SMB",
            "address": "172.28.215.10 (SRV-DC01)",
            "realm": estate["realm"],
            "netbios": estate["netbios"],
            "bind": f"svc-knovas@{estate['realm'].lower()} (PW_SVC_KNOVAS)",
            "people_enabled": sum(1 for u in people if u["enabled"]),
            "people_disabled": sum(1 for u in people if not u["enabled"]),
            "service_accounts": sum(1 for u in directory["users"] if u["service"]),
            "groups": [g["name"] for g in directory["groups"]],
            "what_to_watch_for": [
                "Groups are nested: GG_Partner is in GG_Anwaelte, which is in GG_Alle.",
                "People who left are disabled accounts in OU=Ehemalige, not deleted.",
                "MITARBEITER.Login in the database is the sAMAccountName here; the Kuerzel is not in the directory.",
                "Folder permissions name these groups; matter rights do not.",
            ],
            "folder_permissions": len(acl),
        },
        "mail": {
            "status": "not a source yet",
            "what_exists": "E-mails filed to a matter are .msg files in the store. Everything else is in Outlook archives "
                           f"({firm}/Mail/<login>/*.pst), which the Knovas account cannot read and the Connector does not parse.",
        },
    }


def write_truth(world: World, rows: list[PlanRow], bodies: dict[str, str], entries: list[Entry], records: Records,
                cfg: dict, dest: Path, *, pilot: bool, database: list) -> dict[str, int]:
    dest.mkdir(parents=True, exist_ok=True)
    access = Access(world, records, cfg)
    questions = build_questions(world, rows, bodies, entries, records, access, pilot=pilot)
    with (dest / "questions.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for item in questions:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    rights = rights_rows(world, entries, access)
    with (dest / "rights.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["person", "pointer", "may_read", "rule"])
        writer.writerows(rights)
    people = people_json(world)
    (dest / "people.json").write_text(json.dumps(people, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    processes = processes_json(world)
    (dest / "processes.json").write_text(json.dumps(processes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (dest / "inventory.json").write_text(
        json.dumps(inventory(entries, records, cfg, database), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    from demo_kanzlei.sqlgen import write_record_truth

    write_record_truth(database, dest / "records")
    (dest / "sources.json").write_text(
        json.dumps(sources(entries, records, cfg, database, access.directory, access.acl), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return {"questions": len(questions), "rights": len(rights), "people": len(people), "processes": len(processes)}
