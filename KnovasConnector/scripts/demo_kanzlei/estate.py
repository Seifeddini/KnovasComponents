"""The share as we find it at a client (``--layout client``).

One hidden share holds everything the firm has on its file server::

    <Firm>/Praxis/Autodoc/000000-000500/{GUID}_{AkteID}_{Typ}.{ext}   the practice software's store
    <Firm>/Praxis/...                                                 its templates, exports, logs
    <Firm>/Gemeinsam/Kanzlei|Partner|Personal|Buchhaltung|Scan|...    the common drive
    <Firm>/Users/<login>/                                             home drives
    <Firm>/Mail/<login>/                                              Outlook archives

The store is filled in the order documents were created, 500 to a folder, so a
matter's files are scattered over many folders and no folder name says whose
they are. Only the written matters have documents the Connector reads; the
other matters have placeholders in formats it skips (``fillers``).

Engineered mess is recorded in the manifest, which stays in the work folder.
"""
from __future__ import annotations

import hashlib
import os
import random
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from demo_kanzlei import fillers, namepool
from demo_kanzlei.directory import build_acl, build_directory, readers
from demo_kanzlei.filenames import make_filename
from demo_kanzlei.firm_documents import firm_documents, scan_texts
from demo_kanzlei.models import PlanRow, World
from demo_kanzlei.records import DocRecord, Records
from demo_kanzlei.render import render_document

HINT_NAME = "LIESMICH_DEMODATEN.txt"
HINT = """HINWEIS: SYNTHETISCHE DEMODATEN

Dieser Datenbestand ist vollständig fiktiv. Personen, Gesellschaften und
Sachverhalte sind erfunden. Er dient ausschliesslich dem Test und der
Vorführung von Knovas. Er enthält keine Daten einer echten Kanzlei.
"""

KANZLEI_FOLDERS = {"Vorlagen": "01-Vorlagen", "Organisation": "02-Organisation", "Compliance": "03-Compliance"}


@dataclass
class Entry:
    """One file on the share. The manifest is a list of these."""

    path: str
    kind: str  # autodoc, firm, scan, archive, praxis, it, home, mail, clutter
    fmt: str
    date: str
    sha256: str = ""
    size: int = 0
    matter: str | None = None  # the AkteID in the file name
    true_matter: str | None = None  # the matter the content belongs to, when filed wrongly
    doc_type: str | None = None
    category: str | None = None
    plan_id: str | None = None
    dok_nr: int | None = None
    mess: str | None = None
    duplicate_of: str | None = None
    written: bool = False  # real prose that should be readable
    syncable: bool = False  # an extension the Connector reads
    reachable: bool = False  # the Knovas account may read the folder
    acl: str = ""  # the folder whose permissions decide


@dataclass
class _Doc:
    """A document on its way into the practice software's store."""

    key: str
    date: str
    matter: str
    typ: str
    ext: str
    author: str
    title: str
    row: PlanRow | None = None
    text: str = ""
    copy_of: "_Doc | None" = None
    cut: float | None = None
    encoding: str | None = None
    empty: bool = False
    category: str | None = None
    true_matter: str | None = None
    mess: str | None = None
    on_share: bool = True
    dok_nr: int = 0
    path: str = ""


def bucket_name(dok_nr: int, size: int) -> str:
    """``000000-000500``, ``000501-001000`` … as the practice software names them."""
    index = (dok_nr - 1) // size
    low = index * size + (1 if index else 0)
    return f"{low:06d}-{(index + 1) * size:06d}"


def _moment(day: str, key: str) -> datetime:
    """A working-hours time on ``day``, fixed by ``key``."""
    digest = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    base = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return base + timedelta(hours=7 + digest % 10, minutes=digest // 10 % 60)


def _guid(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


class _Share:
    def __init__(self, root: Path, directory: dict, acl: list[dict]):
        self.root = root
        self.directory = directory
        self.acl = acl
        self.entries: list[Entry] = []
        self._made: set[Path] = set()

    def put(self, rel: str, data: bytes | None, *, day: str, kind: str, **fields) -> Entry:
        """Register a file. ``data`` None means it was already written by a renderer."""
        path = self.root / rel
        if data is not None:
            folder = path.parent
            if folder not in self._made:
                folder.mkdir(parents=True, exist_ok=True)
                self._made.add(folder)
            path.write_bytes(data)
        stamp = _moment(day, rel).timestamp()
        os.utime(path, (stamp, stamp))
        blob = data if data is not None else path.read_bytes()
        allowed, decided = readers(self.directory, self.acl, rel)
        suffix = path.suffix.lower()
        entry = Entry(
            path=rel,
            kind=kind,
            fmt=suffix.lstrip("."),
            date=day,
            sha256=hashlib.sha256(blob).hexdigest(),
            size=len(blob),
            syncable=suffix in fillers.SYNCABLE,
            reachable="svc-knovas" in allowed,
            acl=decided,
            **fields,
        )
        self.entries.append(entry)
        return entry

    def render(self, rel: str, body: str, *, fmt: str, title: str, day: str, author: str, kind: str, **fields) -> Entry:
        render_document(self.root / rel, body, fmt=fmt, title=title, created=_moment(day, rel), author=author)
        return self.put(rel, None, day=day, kind=kind, written=True, **fields)


def _filler_docs(world: World, records: Records, cfg: dict, *, pilot: bool) -> list[_Doc]:
    estate = cfg["estate"]
    low, high = (2, 8) if pilot else (int(estate["filler_docs_min"]), int(estate["filler_docs_max"]))
    today = records.today.isoformat()
    docs: list[_Doc] = []
    for matter in records.matters:
        year = int(matter.aktenzeichen[:4])
        if matter.in_world or year < records.software_since:
            continue
        rng = random.Random(f"{world.seed}:fill:{matter.aktenzeichen}")
        start = datetime.fromisoformat(matter.eroeffnet)
        span = max(1, (datetime.fromisoformat(matter.abgeschlossen or today) - start).days)
        count = rng.randint(low, high)
        for index in range(count):
            day = (start + timedelta(days=rng.randrange(span + 1) if index > 1 else index)).date().isoformat()
            typ = ("Mandatsvereinbarung", "Vollmacht")[index] if index < 2 else rng.choice(namepool.FILLER_DOC_TYPES)
            ext = _filler_ext(typ, year, rng)
            key = f"filler:{matter.aktenzeichen}:{index}"
            docs.append(_Doc(
                key=key, date=day, matter=matter.aktenzeichen, typ=typ, ext=ext,
                author=matter.sachbearbeiter if rng.random() < 0.7 else matter.verantwortlich,
                title=f"{typ.replace('-', ' ')} {matter.aktenzeichen}",
                text=fillers.letter(typ, matter.aktenzeichen, matter.bezeichnung, day, world.firm.name, key),
            ))
    return docs


def _filler_ext(typ: str, year: int, rng: random.Random) -> str:
    if typ in {"Posteingang", "Verfuegung", "Entscheid", "Klageantwort", "Fax"}:
        return "tif" if year < 2016 or rng.random() < 0.6 else "jpg"
    if typ == "Tabelle":
        return "xls" if year < 2011 else "xlsx"
    if typ == "Foto":
        return "jpg"
    if typ == "Diktat":
        return "dss"
    if typ == "Beilage":
        return rng.choice(["tif", "jpg", "zip", "htm", "xls"])
    roll = rng.random()
    if year < 2009 and roll < 0.05:
        return "wpd"
    return "rtf" if roll < 0.15 else "doc"


def _mess_counts(cfg: dict, *, pilot: bool) -> dict[str, int]:
    wanted = dict(cfg["estate"]["mess"])
    if pilot:
        wanted = {key: 1 for key in wanted}
        wanted.update(misfiled=0, rows_without_file=2, lock_files=2)
    return wanted


def _plan_mess(world: World, records: Records, real: list[_Doc], filler: list[_Doc], cfg: dict, *, pilot: bool) -> tuple[list[_Doc], list[_Doc]]:
    """Returns (documents the software knows, files somebody copied in by hand)."""
    rng = random.Random(f"{world.seed}:mess")
    counts = _mess_counts(cfg, pilot=pilot)
    locked = {m.aktenzeichen for m in records.matters if m.gesperrt}
    excluded = {records.matters[r.akte_nr - 1].aktenzeichen for r in records.rights if r.recht == "X"}
    # Copies that end up where other people can read them are made only from
    # documents everybody may read, so the rights truth stays unambiguous.
    plain = [d for d in real if d.matter not in locked and d.category != "Finanzen" and d.ext in {"pdf", "docx", "txt"}
             and d.row is not None and d.row.fmt != "scan_pdf"]
    open_plain = [d for d in plain if d.matter not in excluded]
    open_refs = sorted({m.aktenzeichen for m in records.matters if not m.gesperrt and m.aktenzeichen not in excluded
                        and int(m.aktenzeichen[:4]) >= records.software_since})
    known: list[_Doc] = []
    loose: list[_Doc] = []

    def pick(pool: list[_Doc], count: int) -> list[_Doc]:
        return rng.sample(pool, min(count, len(pool)))

    for source in pick(plain, counts["duplicates"]):
        known.append(_Doc(key=f"dup:{source.key}", date=source.date, matter=source.matter, typ=source.typ, ext=source.ext,
                          author=source.author, title=source.title, copy_of=source, category=source.category,
                          mess="duplicate"))
    for source in pick(open_plain, counts["misfiled"]):
        target = rng.choice([ref for ref in open_refs if ref != source.matter])
        known.append(_Doc(key=f"misfiled:{source.key}", date=source.date, matter=target, typ=source.typ, ext=source.ext,
                          author=source.author, title=source.title, copy_of=source, category=source.category,
                          true_matter=source.matter, mess="misfiled"))
    for source in pick(plain, counts["truncated"]):
        known.append(_Doc(key=f"cut:{source.key}", date=source.date, matter=source.matter, typ=f"{source.typ}-Kopie",
                          ext=source.ext, author=source.author, title=source.title, copy_of=source, cut=0.4,
                          category=source.category, mess="truncated"))
    hosts = [ref for ref in open_refs if not records.matter(ref).in_world] or open_refs
    for index in range(counts["empty"]):
        ref = rng.choice(hosts)
        day = records.matter(ref).eroeffnet
        known.append(_Doc(key=f"empty:{index}", date=day, matter=ref, typ="Entwurf", ext=("docx", "pdf")[index % 2],
                          author=records.matter(ref).sachbearbeiter, title=f"Entwurf {ref}", empty=True, mess="empty"))
    for index in range(counts["latin1"]):
        ref = rng.choice(hosts)
        matter = records.matter(ref)
        text = (f"Aktenzeichen: {ref}\nIn Sachen: {matter.bezeichnung}\n\nAktennotiz\n\n"
                "Rückruf der Gegenpartei erhalten. Sie wünscht Fristerstreckung bis Ende März und "
                "schlägt für die übrigen Punkte ein Gespräch vor. Für uns ist das möglich.\n")
        known.append(_Doc(key=f"latin1:{index}", date=matter.eroeffnet, matter=ref, typ="Aktennotiz", ext="txt",
                          author=matter.sachbearbeiter, title=f"Aktennotiz {ref}", text=text, encoding="cp1252",
                          mess="latin1"))
    for doc in pick(filler, counts["rows_without_file"]):
        doc.on_share = False
        doc.mess = "row_without_file"
    names = ["Scan0012.pdf", "Vertrag final.docx", "Brief Entwurf.docx", "Kopie von Klage.pdf", "Dokument1.docx",
             "Scan0013.pdf", "neu.docx", "Vollmacht unterschrieben.pdf", "Stellungnahme v2 final.docx", "IMG-4471.pdf",
             "Entwurf Antwort.docx", "Beilage 7.pdf", "RE Offerte.pdf", "Notiz Telefon.docx", "Vertrag (2).pdf",
             "Unbenannt.docx", "Scan0044.pdf", "Besprechung.docx"]
    sources = pick([d for d in open_plain if d.ext in {"pdf", "docx"}], counts["broken_names"])
    used: dict[str, int] = {}
    for index, source in enumerate(sources):
        wanted = [n for n in names if n.endswith(source.ext)]
        stem, _, ext = wanted[index % len(wanted)].rpartition(".")
        used[stem] = used.get(stem, 0) + 1
        loose.append(_Doc(key=f"loose:{index}", date=source.date, matter="", typ="", ext=ext, author="",
                          title=stem if used[stem] == 1 else f"{stem} ({used[stem]})", copy_of=source,
                          true_matter=source.matter, mess="broken_name"))
    return known, loose


def build_estate(
    rows: list[PlanRow],
    bodies: dict[str, str],
    world: World,
    records: Records,
    cfg: dict,
    out_root: Path,
    *,
    pilot: bool,
) -> list[Entry]:
    estate = cfg["estate"]
    firm = estate["firm_folder"]
    size = int(estate["bucket_size"])
    directory = build_directory(world, cfg)
    acl = build_acl(world, cfg)
    share = _Share(out_root, directory, acl)
    out_root.mkdir(parents=True, exist_ok=True)
    staff = {s.id: s for s in world.firm.staff}

    real = [
        _Doc(key=row.id, date=row.date, matter=row.matter_aktenzeichen or "", typ=row.doc_type,
             ext="pdf" if row.fmt == "scan_pdf" else row.fmt,
             author=staff[row.author_id].initials if row.author_id else "SCAN", title=row.title, row=row,
             category=row.category)
        for row in rows
        if row.matter_aktenzeichen != "Kanzlei"
    ]
    filler = _filler_docs(world, records, cfg, pilot=pilot)
    known, loose = _plan_mess(world, records, real, filler, cfg, pilot=pilot)

    # The software numbers documents in the order they were created.
    store = sorted([*real, *filler, *known], key=lambda d: (d.date, d.key))
    autodoc = f"{firm}/Praxis/Autodoc"
    for number, doc in enumerate(store, start=1):
        doc.dok_nr = number
        name = make_filename(_guid(doc.key), doc.matter, doc.typ, doc.ext)
        doc.path = f"{autodoc}/{bucket_name(number, size)}/{name}"

    def common(doc: _Doc) -> dict:
        return dict(matter=doc.matter or None, true_matter=doc.true_matter, doc_type=doc.typ or None,
                    category=doc.category, dok_nr=doc.dok_nr or None, mess=doc.mess)

    written: dict[str, Entry] = {}
    for doc in store:
        if not doc.on_share or doc.copy_of is not None:
            continue
        if doc.row is not None:
            row = doc.row
            when = _moment(row.date, row.id)
            sender = (row.email_from[0], row.email_from[1]) if row.email_from else None
            to = [(item[0], item[1]) for item in row.email_to] if row.email_to else None
            render_document(
                out_root / doc.path, bodies[row.id], fmt=row.fmt, title=row.title,
                subject=row.title if row.fmt == "msg" else None, sender=sender, to=to,
                sent=when if row.fmt == "msg" else None, created=when,
                author=staff[row.author_id].name if row.author_id else None,
            )
            written[doc.key] = share.put(doc.path, None, day=doc.date, kind="autodoc", written=True, plan_id=row.id, **common(doc))
        elif doc.empty:
            share.put(doc.path, b"", day=doc.date, kind="autodoc", **common(doc))
        elif doc.encoding:
            share.put(doc.path, doc.text.encode(doc.encoding), day=doc.date, kind="autodoc", written=True, **common(doc))
        else:
            share.put(doc.path, fillers.make(doc.ext, key=doc.key, text=doc.text, title=doc.title), day=doc.date,
                      kind="autodoc", **common(doc))
    # Copies: saved twice, filed under the wrong matter, cut off, or dropped in by hand.
    buckets = sorted({doc.path.rsplit("/", 1)[0] for doc in store if doc.on_share})
    rng = random.Random(f"{world.seed}:loose")
    for doc in [*[d for d in store if d.copy_of is not None], *loose]:
        source = doc.copy_of
        assert source is not None
        data = (out_root / source.path).read_bytes()
        if doc.cut:
            data = data[: int(len(data) * doc.cut)]
        if not doc.path:
            doc.path = f"{rng.choice(buckets)}/{doc.title}.{doc.ext}"
        share.put(doc.path, data, day=doc.date, kind="autodoc", written=doc.cut is None,
                  duplicate_of=source.path, plan_id=source.row.id if source.row else None, **common(doc))
    # What the software leaves behind, and Windows.
    locks = _mess_counts(cfg, pilot=pilot)["lock_files"]
    docx = [d for d in real if d.ext == "docx"]
    for index, doc in enumerate(rng.sample(docx, min(locks, len(docx)))):
        folder, name = doc.path.rsplit("/", 1)
        share.put(f"{folder}/~${name[2:]}", fillers.noise(f"lock:{index}", 162), day=doc.date, kind="autodoc", mess="lock",
                  matter=doc.matter, category=doc.category)
    for index, folder in enumerate(buckets[:: max(1, len(buckets) // 6)]):
        share.put(f"{folder}/Thumbs.db", fillers.make("db", key=f"thumbs:{index}", text=""), day=records.today.isoformat(),
                  kind="autodoc", mess="lock")

    records.documents = [
        DocRecord(
            dok_nr=doc.dok_nr,
            guid=_guid(doc.key).upper(),
            akte_nr=records.matter(doc.matter).nr,
            typ=doc.typ,
            bezeichnung=doc.title,
            dateiname=doc.path.rsplit("/", 1)[1],
            ordner=doc.path.rsplit("/", 2)[1],
            erstellt_am=_moment(doc.date, doc.key).strftime("%Y-%m-%dT%H:%M:%S"),
            erstellt_von=doc.author,
            groesse=(out_root / doc.path).stat().st_size if doc.on_share else 4096,
        )
        for doc in store
    ]

    _firm_folders(share, rows, bodies, world, records, cfg, pilot=pilot)
    share.put(HINT_NAME, HINT.encode("utf-8"), day=records.today.isoformat(), kind="clutter")
    return share.entries


def _firm_folders(share: _Share, rows, bodies, world: World, records: Records, cfg: dict, *, pilot: bool) -> None:
    firm = cfg["estate"]["firm_folder"]
    staff = {s.id: s for s in world.firm.staff}
    today = records.today.isoformat()
    name = world.firm.name

    def stub(rel: str, day: str, text: str = "", kind: str = "firm", **fields) -> None:
        ext = rel.rsplit(".", 1)[1]
        share.put(f"{firm}/{rel}", fillers.make(ext, key=rel, text=text or rel, title=rel.rsplit("/", 1)[1]), day=day,
                  kind=kind, **fields)

    # The firm-wide documents of the first build keep their text, under human names.
    for row in rows:
        if row.matter_aktenzeichen != "Kanzlei":
            continue
        label = row.doc_type.replace("-", " ")
        if row.doc_type == "Partnersitzung-Protokoll":
            rel = f"{firm}/Gemeinsam/Partner/Partnersitzungen/Protokoll PS {row.date}.{row.fmt}"
        else:
            rel = f"{firm}/Gemeinsam/Kanzlei/{KANZLEI_FOLDERS.get(row.category, '02-Organisation')}/{label}.{row.fmt}"
        author = staff[row.author_id].name if row.author_id else ""
        share.render(rel, bodies[row.id], fmt=row.fmt, title=row.title, day=row.date, author=author, kind="firm",
                     doc_type=row.doc_type, category=row.category, plan_id=row.id)
    for doc in firm_documents(world, pilot=pilot):
        share.render(f"{firm}/{doc.rel_path}", doc.body, fmt=doc.fmt, title=doc.title, day=doc.date,
                     author=staff[doc.author_id].name, kind="firm", doc_type=doc.title)
    for file_name, day, text in scan_texts(world, pilot=pilot):
        share.render(f"{firm}/Gemeinsam/Scan/{file_name}", text, fmt="scan_pdf", title="", day=day, author="", kind="scan",
                     doc_type="Posteingang")

    stub("Gemeinsam/Kanzlei/01-Vorlagen/Briefvorlage.dotx", "2019-03-11")
    stub("Gemeinsam/Kanzlei/01-Vorlagen/Fax.dot", "2007-05-02", f"{name}\nTelefax\n")
    stub("Gemeinsam/Kanzlei/01-Vorlagen/Honorarnote.xltx", "2018-01-15")
    stub("Gemeinsam/Kanzlei/02-Organisation/Ferienplan 2026.xlsx", "2026-01-12", "\n".join(s.name for s in world.firm.staff))
    stub("Gemeinsam/Kanzlei/02-Organisation/Kopie von Ferienplan 2025.xlsx", "2025-01-13", "\n".join(s.name for s in world.firm.staff))
    stub("Gemeinsam/Kanzlei/02-Organisation/Organigramm.pptx", "2025-09-02")
    stub("Gemeinsam/Partner/Budget 2027.xlsx", "2026-10-02", "Budget 2027\nPersonal\nRaum\nIT")
    stub("Gemeinsam/Partner/Gewinnverteilung 2025.xlsx", "2026-03-24", "Gewinnverteilung 2025")
    stub("Gemeinsam/Personal/Lohnrunde 2027.xlsx", "2026-10-30", "Lohnrunde 2027")
    stub("Gemeinsam/Buchhaltung/Debitoren OP 2026-11-18.xlsx", today, "Offene Posten\n" + "\n".join(
        f"{i.nr} {i.total:.2f} {i.status}" for i in records.invoices if i.status in {"offen", "gemahnt"})[:600])
    stub("Gemeinsam/Buchhaltung/FIBU Export 2026-10.csv", "2026-11-03", "Konto;Datum;Text;Soll;Haben\n" + "\n".join(
        f"3000;{i.datum};Honorar {i.nr};;{i.honorar:.2f}" for i in records.invoices if i.datum.startswith("2026-10")))
    stub("Gemeinsam/Buchhaltung/Kontoauszug 2026-10.xml", "2026-11-02",
         '<?xml version="1.0" encoding="UTF-8"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt/></Document>')
    stub("Gemeinsam/Buchhaltung/Kreditoren 2026.xls", "2026-11-10")
    stub("Gemeinsam/Buchhaltung/Lohn/Lohnliste 2026.xlsx", "2026-10-25", "Lohnliste 2026")
    stub("Gemeinsam/Buchhaltung/Lohn/Lohnausweise 2025.zip", "2026-01-28")
    share.render(
        f"{firm}/Gemeinsam/Buchhaltung/Lohn/Lohnabrechnung 2026-10 Zimmermann.pdf",
        f"{name}\n\nLOHNABRECHNUNG OKTOBER 2026\n\nMLaw Luca Zimmermann\nBruttolohn CHF 9'400.00\n"
        "AHV/IV/EO CHF 498.20, ALV CHF 103.40, Pensionskasse CHF 611.00\nNettolohn CHF 8'187.40\n\nVertraulich.\n",
        fmt="pdf", title="Lohnabrechnung Oktober 2026", day="2026-10-25", author=staff["rg"].name, kind="firm",
        doc_type="Lohnabrechnung")
    stub("Gemeinsam/IT/Install/PraxisSetup_7.4.2.exe", "2025-06-17", kind="it")
    stub("Gemeinsam/IT/Install/Scanner-Treiber.exe", "2022-02-08", kind="it")
    stub("Gemeinsam/IT/Inventar.xls", "2024-04-19", kind="it")
    stub("Gemeinsam/IT/Zugangsdaten.xlsx", "2023-08-30", "System\nBenutzer\nKennwort\n(hier nicht eingetragen)", kind="it")
    for day in (["2026-11-15"] if pilot else ["2026-11-01", "2026-11-08", "2026-11-15"]):
        stub(f"Gemeinsam/IT/Backup/{cfg['estate']['database']}_{day.replace('-', '')}.bak", day, kind="it")
    share.render(
        f"{firm}/Gemeinsam/IT/Anleitungen/VPN-Zugang.pdf",
        f"{name}\n\nANLEITUNG VPN-ZUGANG\n\n1. VPN-Client starten.\n2. Mit dem Windows-Konto anmelden.\n"
        "3. Die Laufwerke G und H werden verbunden.\n\nBei Störungen: IT-Dienstleister, Telefon siehe Telefonliste.\n",
        fmt="pdf", title="Anleitung VPN-Zugang", day="2024-05-06", author="", kind="it", doc_type="Anleitung")
    lawyers = [s for s in world.firm.staff if s.role != "sekretariat"]
    for index, person in enumerate(lawyers[:2] if pilot else lawyers):
        for take in range(1 if pilot else 3):
            stub(f"Gemeinsam/Diktate/{person.initials}/DS50{index:02d}{take:02d}.dss", "2026-11-1" + str(take + 3))
    stub("Gemeinsam/Diktate/Testaufnahme.wav", "2021-04-09")
    # Clutter every common drive has.
    share.put(f"{firm}/Gemeinsam/Thumbs.db", fillers.make("db", key="thumbs:gemeinsam", text=""), day=today, kind="clutter")
    share.put(f"{firm}/Gemeinsam/desktop.ini", b"[.ShellClassInfo]\r\nIconResource=shell32.dll,4\r\n", day="2019-01-07", kind="clutter")
    share.put(f"{firm}/Gemeinsam/Kanzlei/02-Organisation/~$rienplan 2026.xlsx", fillers.noise("lock:ferien", 165), day=today, kind="clutter", mess="lock")
    share.put(f"{firm}/Gemeinsam/Neuer Ordner/Neues Textdokument.txt", b"", day="2025-07-21", kind="clutter", mess="empty")
    # Matters from before the software: paper scans and old word-processor files, by archive number.
    old = [m for m in records.matters if m.archiv_nr]
    by_nr = {a.nr: a for a in records.addresses}
    for matter in old:
        rng = random.Random(f"{world.seed}:archive:{matter.aktenzeichen}")
        client = by_nr[matter.mandant].label.strip().replace("/", "-").replace(".", "")
        folder = f"Gemeinsam/Archiv/Akten bis {records.software_since - 1}/{matter.archiv_nr} {client}"
        for index in range(rng.randint(2, 6)):
            ext = rng.choice(["doc", "doc", "doc", "wpd", "rtf", "tif"])
            label = rng.choice(["Brief", "Klage", "Vertrag", "Notiz", "Eingabe", "Urteil", "Rechnung"])
            day = (datetime.fromisoformat(matter.eroeffnet) + timedelta(days=rng.randrange(0, 400))).date().isoformat()
            key = f"archive:{matter.aktenzeichen}:{index}"
            text = fillers.letter(label, matter.aktenzeichen, matter.bezeichnung, day, name, key)
            share.put(f"{firm}/{folder}/{label} {index + 1}.{ext}", fillers.make(ext, key=key, text=text), day=day, kind="archive")
    # The practice software's own folder.
    share.put(f"{firm}/Praxis/praxis.ini", (
        "[Datenbank]\r\nServer=SRV-SQL01\r\nDatenbank=" + cfg["estate"]["database"] + "\r\nAuthentifizierung=Windows\r\n"
        "[AutoDoc]\r\nPfad=\\\\SRV-DC01\\" + cfg["estate"]["share"] + "\\" + firm + "\\Praxis\\Autodoc\r\nOrdnerGroesse=" + str(cfg["estate"]["bucket_size"]) + "\r\n"
    ).encode("cp1252"), day="2025-06-17", kind="praxis")
    stub("Praxis/Vorlagen/Brief.dot", "2012-03-05", f"{name}\n", kind="praxis")
    stub("Praxis/Vorlagen/Honorarnote.dot", "2012-03-05", f"{name}\nHonorarnote\n", kind="praxis")
    stub("Praxis/Vorlagen/Vollmacht.dot", "2012-03-05", f"{name}\nVollmacht\n", kind="praxis")
    stub("Praxis/Export/FIBU_Export_2026-10.csv", "2026-11-03", "Konto;Datum;Text;Betrag\n", kind="praxis")
    stub("Praxis/Update/Update_7.4.2.exe", "2025-06-17", kind="praxis")
    stub("Praxis/Log/praxis_2026-11.log", today, "\n".join(
        f"2026-11-{d:02d} 07:5{d % 10}:01 INFO Dienst gestartet, AutoDoc verbunden" for d in range(2, 19)), kind="praxis")
    # Home drives and mail archives: the Knovas account cannot read them.
    for person in world.firm.staff:
        home = f"Users/{person.sam}"
        stub(f"{home}/Dokumente/Weiterbildung 2026.pptx", "2026-05-20", kind="home")
        stub(f"{home}/Dokumente/Notizen {person.initials}.doc", "2026-09-14", f"Notizen {person.name}\n", kind="home")
        stub(f"{home}/Bilder/IMG_{2000 + int(person.extension)}.jpg", "2025-12-12", "Weihnachtsessen 2025", kind="home")
        stub(f"Mail/{person.sam}/Archiv bis 2024.pst", "2025-01-06", kind="mail")
    share.render(
        f"{firm}/Users/{staff['lz'].sam}/Dokumente/Entwurf Replik Meierhans.docx",
        "Aktenzeichen: 2024-017\nIn Sachen: Meierhans Bau AG ./. Rüegg\n\nENTWURF REPLIK, nicht versandt\n\n"
        "Die Beklagte verkennt, dass die gerügten Mängel den Einbehalt des gesamten Restwerklohns nicht rechtfertigen.\n",
        fmt="docx", title="Entwurf Replik", day="2024-05-29", author=staff["lz"].name, kind="home", doc_type="Entwurf",
        true_matter="2024-017")


def manifest_rows(entries: list[Entry]) -> list[dict]:
    return [asdict(entry) for entry in sorted(entries, key=lambda e: e.path)]
