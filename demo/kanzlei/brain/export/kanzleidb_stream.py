#!/usr/bin/env python3
"""Read Quarzfels Advokatur's practice database (KanzleiDB on SQL Server) and its Kanzlei-ABC, write one record
stream (connector protocol 2.0) for brain-runner.

This is the one-off stand-in for a record connector at the site: it reads the sources read-only (login knovas_ro,
the share as svc-knovas sees it), joins what the database leaves implicit (no foreign keys: AkteNr, AdrNr, Kuerzel),
and computes what the source can compute (totals per matter, the traffic light, open counts). Every computed field
is listed under the record's `computed`, so nothing computed is ever written back.

    python kanzleidb_stream.py --env ~/kanzlei-site/demo/kanzlei/site/.env --share ~/demo-kanzlei/share \
        --out stream.jsonl [--today 2026-11-28]

Keys (the record's external_id is the node key): Akte = Aktenzeichen; ADR-<AdrNr>; MA-<Kuerzel>; RG-<RgID>;
FR-<FristNr>; TE-<TerminNr>; RE-<RechNr>; DOK-<DokNr>; NAME-<normal form>; BEGRIFF-<slug>; KANZLEI.
Prints counts only, never a value.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import decimal
import hashlib
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

SOURCE = "kanzleidb"
READABLE = {".docx", ".pdf", ".txt", ".msg", ".xlsx"}
ELECTRONIC = {".docx", ".pdf", ".txt", ".msg"}
STATUS = {"O": "offen", "S": "sistiert", "A": "abgeschlossen"}
ADRTYP = {"P": "Person", "F": "Firma", "A": "Anwalt", "B": "Behörde"}
ROLLE_REL = {1: "mandant", 2: "gegenpartei", 3: "gegenanwalt", 4: "gericht_behoerde", 5: "versicherung",
             6: "zeuge", 7: "experte"}
TERMINART = {"B": "Besprechung", "V": "Verhandlung", "I": "Intern"}
RECHNUNGSART = {"R": "Honorarnote", "V": "Kostenvorschuss"}
# Document types (DOKUMENTE.DokTyp) that are the evidence a Weisung asks for.
EVIDENCE = {
    "Konfliktpruefung": "konfliktpruefung_zu",
    "Mandatsvereinbarung": "mandatsvereinbarung_zu",
    "Vollmacht": "vollmacht_zu",
    "Kostenvorschuss": "kostenvorschuss_zu",
    "Aktennotiz": "aktennotiz_zu",
    "Abmahnung": "aussergerichtlich_zu",
    "Brief-Gegenanwalt": "aussergerichtlich_zu",
    "E-Mail-Gegenpartei": "aussergerichtlich_zu",
    "Entwurf": "entwurf_zu",
    "Klage": "rechtsschrift_zu",
    "Klageantwort": "rechtsschrift_zu",
    "Eingabe": "rechtsschrift_zu",
    "Stellungnahme": "rechtsschrift_zu",
    "Brief-Gericht": "versandnachweis_zu",
    "E-Mail-Gericht": "versandnachweis_zu",
    "Urteil": "erledigung_zu",
    "Entscheid": "erledigung_zu",
    "Vergleich": "erledigung_zu",
}
ERLEDIGUNG = {"Urteil", "Entscheid", "Vergleich"}
LEGAL_FORMS = r"\b(ag|gmbh|sa|sarl|sàrl|kg|klg|ltd|inc|co|cie|genossenschaft|stiftung|verein|und|&)\b"


def env_value(path: Path, name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    sys.exit(f"{name} missing in {path}")


def sval(v):
    """A field value as the protocol carries it: decimals and dates as strings."""
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, (list, tuple, set)):
        return [sval(x) for x in v]
    if isinstance(v, int):
        return v
    if isinstance(v, decimal.Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else str(v.quantize(decimal.Decimal("0.01")))
    if isinstance(v, float):
        return str(round(v, 2))
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


def day(v) -> str | None:
    if v is None:
        return None
    d = v.date() if isinstance(v, dt.datetime) else v
    return None if d.year <= 1900 else d.isoformat()


def ja(b) -> str:
    return "ja" if b else "nein"


def normal_form(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(LEGAL_FORMS, " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return "-".join(sorted(s.split()))


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def workdays_before(d: dt.date, n: int) -> dt.date:
    while n > 0:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def read_db(env: Path) -> dict[str, list[dict]]:
    import pymssql
    pw = env_value(env, "PW_SQL_KNOVAS_RO")
    conn = pymssql.connect(server="172.28.215.20", port=1433, user="knovas_ro", password=pw, database="KanzleiDB",
                           login_timeout=15, as_dict=True)
    tables = ["AKTEN", "ADRESSEN", "AKTENBETEILIGTE", "AKTENRECHTE", "MITARBEITER", "RECHTSGEBIETE", "FRISTEN",
              "FRISTARTEN", "TERMINE", "RECHNUNGEN", "DOKUMENTE", "LEISTUNGEN", "LEISTUNGSARTEN", "ZUSATZFELDER",
              "ZUSATZFELDWERTE"]
    out = {}
    cur = conn.cursor()
    for t in tables:
        cur.execute(f"SELECT * FROM {t}")          # table names are this list's, never input
        rows = cur.fetchall()
        for r in rows:
            r.pop("TS", None)                      # rowversion: bytes, not a field
        out[t] = rows
    conn.close()
    return out


def read_abc(share: Path) -> tuple[str | None, list[tuple[str, str]]]:
    p = share / "Quarzfels/Gemeinsam/Kanzlei/02-Organisation/Kanzlei-ABC.docx"
    xml = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8")
    text = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
    stand = re.search(r"Stand (\d{4}-\d{2}-\d{2})", text)
    terms = []
    for line in text.splitlines():
        m = re.match(r"^\s*([^:]{1,40}):\s+(.+?)\s*$", line)
        if m and not m.group(1).lower().startswith("stand"):
            terms.append((m.group(1).strip(), m.group(2).strip()))
    return (stand.group(1) if stand else None), terms


def build(db: dict, abc: tuple, today: dt.date) -> list[dict]:
    recs: list[dict] = []

    def rec(kind, key, fields, computed=(), links=()):
        fields = {k: sval(v) for k, v in fields.items()}
        links = sorted({(r, e) for r, e in links if e}, key=lambda x: (x[0], x[1]))
        body = json.dumps([kind, fields, sorted(computed), links], sort_keys=True, ensure_ascii=False)
        recs.append({"type": "record", "source": SOURCE, "external_id": key,
                     "version": hashlib.sha256(body.encode()).hexdigest()[:16],
                     "payload": {"record_kind": kind, "fields": fields,
                                 **({"computed": sorted(set(computed))} if computed else {}),
                                 **({"links": [{"relation": r, "external_id": e} for r, e in links]} if links else {})}})

    akten = {a["AkteNr"]: a for a in db["AKTEN"]}
    az = {n: a["Aktenzeichen"] for n, a in akten.items()}
    if len(set(az.values())) != len(az):
        sys.exit("Aktenzeichen not unique")
    ma = {m["Kuerzel"]: m for m in db["MITARBEITER"]}
    def mkey(k): return f"MA-{k}" if k in ma else None
    fristart = {f["FristArtID"]: f["Bezeichnung"] for f in db["FRISTARTEN"]}
    verrechenbar = {l["LeistArt"]: l["Verrechenbar"] != 0 for l in db["LEISTUNGSARTEN"]}
    zf = {f["FeldID"]: f["Bezeichnung"] for f in db["ZUSATZFELDER"] if f["Tabelle"] == "AKTEN"}
    zfw = collections.defaultdict(dict)
    for w in db["ZUSATZFELDWERTE"]:
        if w["FeldID"] in zf:
            zfw[str(w["Schluessel"])][zf[w["FeldID"]]] = w["Wert"]

    # Documents per matter, and which matters are kept electronically (a file Knovas can read)
    docs = collections.defaultdict(list)
    for d in db["DOKUMENTE"]:
        if not d["Geloescht"]:
            docs[d["AkteNr"]].append(d)
    def readable(d): return Path(d["Dateiname"]).suffix.lower() in READABLE
    # Kept electronically: written documents in AutoDoc (Word, PDF, text, mail). The spreadsheets the practice
    # software attaches to every matter (DokTyp Tabelle) do not make a matter electronic.
    electronic = {n for n, ds in docs.items() if any(Path(d["Dateiname"]).suffix.lower() in ELECTRONIC for d in ds)}

    # Money and time per matter
    lei = collections.defaultdict(lambda: {"h": decimal.Decimal(0), "chf": decimal.Decimal(0), "open": decimal.Decimal(0), "last": None})
    for l in db["LEISTUNGEN"]:
        s = lei[l["AkteNr"]]
        s["h"] += l["Stunden"] or 0
        s["chf"] += l["Betrag"] or 0
        if l["RechNr"] is None and verrechenbar.get(l["LeistArt"], True):
            s["open"] += l["Betrag"] or 0
        d = day(l["Datum"])
        s["last"] = max(filter(None, [s["last"], d]), default=None)
    rech = collections.defaultdict(list)
    for r in db["RECHNUNGEN"]:
        rech[r["AkteNr"]].append(r)
    fristen = collections.defaultdict(list)
    for f in db["FRISTEN"]:
        fristen[f["AkteNr"]].append(f)

    # Parties: roles per matter, counts, same-name groups
    roles = collections.defaultdict(set)        # AkteNr -> {(relation, ADR-key)}
    for a in akten.values():
        if a["MandantAdrNr"]:
            roles[a["AkteNr"]].add(("mandant", f"ADR-{a['MandantAdrNr']}"))
    for b in db["AKTENBETEILIGTE"]:
        rel = ROLLE_REL.get(b["RolleID"])
        if rel and b["AkteNr"] in akten:
            roles[b["AkteNr"]].add((rel, f"ADR-{b['AdrNr']}"))
    party_matters = collections.defaultdict(lambda: collections.Counter())
    for n, rs in roles.items():
        for rel, adr in rs:
            party_matters[adr][rel] += 1
    rights = collections.defaultdict(list)
    for r in db["AKTENRECHTE"]:
        rights[r["AkteNr"]].append(r)

    def party_name(p):
        if p["Firma"]:
            return p["Firma"]
        return " ".join(x for x in [p["Vorname"], p["Name"]] if x) or f"Adresse {p['AdrNr']}"
    groups = collections.defaultdict(list)
    for p in db["ADRESSEN"]:
        nf = normal_form(party_name(p))
        if nf:
            groups[nf].append(p["AdrNr"])
    group_of = {}
    for nf, members in groups.items():
        if len(members) >= 2:
            key = "NAME-" + nf[:80]
            rec("namensgruppe", key, {"normalform": nf.replace("-", " "), "anzahl_adressen": len(members)})
            for m in members:
                group_of[m] = key

    # The firm, its fields of law, its people
    rec("kanzlei", "KANZLEI", {"name": "Quarzfels Advokatur", "adresse": "Rämistrasse 14, 8001 Zürich"})
    for g in db["RECHTSGEBIETE"]:
        rec("rechtsgebiet", f"RG-{g['RgID']}", {"name": g["Bezeichnung"]}, links=[("gehoert_zu", "KANZLEI")])
    open_by_ma = collections.Counter(a["VerantwAnwalt"] for a in akten.values() if a["Status"] == "O")
    fr_by_ma = collections.Counter(f["Verantwortlich"] for f in db["FRISTEN"] if not f["Erledigt"])
    for m in db["MITARBEITER"]:
        fn = m["Funktion"] or ""
        rolle = "Partner" if "Partner" in fn else ("Anwalt" if "anw" in fn.lower() else "Sekretariat")
        rec("mitarbeiter", f"MA-{m['Kuerzel']}",
            {"name": m["Name"], "kuerzel": m["Kuerzel"], "funktion": fn, "rolle": rolle,
             "aktiv": ja(m["Aktiv"] != 0), "eintritt": day(m["Eintritt"]), "austritt": day(m["Austritt"]),
             "stundenansatz": m["Stundenansatz"], "offene_akten": open_by_ma[m["Kuerzel"]],
             "offene_fristen": fr_by_ma[m["Kuerzel"]]},
            computed=["offene_akten", "offene_fristen"], links=[("gehoert_zu", "KANZLEI")])

    for p in db["ADRESSEN"]:
        key = f"ADR-{p['AdrNr']}"
        c = party_matters.get(key, collections.Counter())
        rec("partei", key,
            {"name": party_name(p), "adresstyp": ADRTYP.get(p["AdrTyp"], p["AdrTyp"]), "anrede": p["Anrede"],
             "titel": p["Titel"], "vorname": p["Vorname"], "nachname": p["Name"], "firma": p["Firma"],
             "strasse": p["Strasse"], "plz": p["PLZ"], "ort": p["Ort"], "telefon_geschaeft": p["Telefon"],
             "email": p["EMail"], "inaktiv": ja(p["Inaktiv"]), "bemerkung": p["Bemerkung"],
             "erfasst_am": day(p["ErfasstAm"]), "anzahl_akten": sum(c.values()), "als_mandant": c["mandant"],
             "als_gegenpartei": c["gegenpartei"], "stammkunde": ja(c["mandant"] > 3)},
            computed=["anzahl_akten", "als_mandant", "als_gegenpartei", "stammkunde"],
            links=[("gleicher_name", group_of.get(p["AdrNr"]))])

    # Matters
    for n, a in akten.items():
        key = a["Aktenzeichen"]
        s = lei.get(n, {"h": 0, "chf": 0, "open": 0, "last": None})
        rs = rech.get(n, [])
        hn = [r for r in rs if r["Art"] == "R"]
        offen = sum((r["Total"] or 0) for r in rs if r["Status"] in ("offen", "gemahnt"))
        open_fr = sorted((f for f in fristen.get(n, []) if not f["Erledigt"]), key=lambda f: f["Datum"])
        nxt = open_fr[0] if open_fr else None
        nxt_day = nxt["Datum"].date() if nxt else None
        if nxt_day is None:
            ampel = "grün"
        elif nxt_day <= today + dt.timedelta(days=10):
            ampel = "rot"
        else:
            ampel = "gelb"
        ds = docs.get(n, [])
        belege = {d["DokTyp"] for d in ds} | {"RE-" + r["Art"] for r in rs}
        last_erl = max((d["ErstelltAm"] for d in ds if d["DokTyp"] in ERLEDIGUNG), default=None)
        if last_erl and any(d["DokTyp"] == "Brief-Mandant" and d["ErstelltAm"] > last_erl for d in ds):
            belege.add("Abschlussmitteilung")
        belege = sorted(belege)
        z = zfw.get(str(n), {})
        rr = rights.get(n, [])
        bem = a["Bemerkung"] or ""
        links = list(roles.get(n, set()))
        links += [("verantwortlich", mkey(a["VerantwAnwalt"])), ("sachbearbeitung", mkey(a["Sachbearbeiter"])),
                  ("rechtsgebiet", f"RG-{a['RgID']}" if a["RgID"] else None)]
        links += [("mandatsteam", mkey(r["Kuerzel"])) for r in rr if r["Recht"] == "Z"]
        links += [("ausgeschlossen", mkey(r["Kuerzel"])) for r in rr if r["Recht"] == "X"]
        rec("akte", key,
            {"name": f"{key} {a['Bezeichnung']}", "aktenzeichen": key, "bezeichnung": a["Bezeichnung"],
             "status": STATUS.get(a["Status"], a["Status"]), "eroeffnet_am": day(a["EroeffnetAm"]),
             "abgeschlossen_am": day(a["AbgeschlossenAm"]), "streitwert": a["Streitwert"],
             "honoraransatz": a["Honoraransatz"], "honorarform": "Pauschalhonorar" if "Pauschal" in bem else None,
             "sperrakte": ja(a["Gesperrt"]), "schranke": ja(any(r["Recht"] == "X" for r in rr)),
             "archiv_nr": a["ArchivNr"], "bemerkung": bem or None,
             "geschaeftsnummer_gericht": z.get("Geschäfts-Nr. Gericht"), "referenz_mandant": z.get("Referenz Mandant"),
             "rechtsschutz_police": z.get("Policen-Nr. Rechtsschutz"), "archivschachtel": z.get("Archivschachtel"),
             "akquise_durch": z.get("Akquise durch"),
             "stunden_total": s["h"], "leistungen_chf": s["chf"], "nicht_verrechnet_chf": s["open"],
             "verrechnet_chf": sum((r["Total"] or 0) for r in hn), "offen_chf": offen,
             "gemahnt": sum(1 for r in rs if r["Status"] == "gemahnt"), "offene_fristen": len(open_fr),
             "naechste_frist": nxt_day, "naechste_frist_art": fristart.get(nxt["FristArtID"]) if nxt else None,
             "ampel": ampel, "letzte_leistung": s["last"], "anzahl_dokumente": len(ds),
             "elektronisch": ja(n in electronic), "belege": belege},
            computed=["stunden_total", "leistungen_chf", "nicht_verrechnet_chf", "verrechnet_chf", "offen_chf",
                      "gemahnt", "offene_fristen", "naechste_frist", "naechste_frist_art", "ampel", "letzte_leistung",
                      "anzahl_dokumente", "elektronisch", "belege", "schranke"],
            links=links)

    # Deadlines: Weisung 2 asks for a Wiedervorlage five working days before the deadline
    wv = collections.defaultdict(list)
    for f in db["FRISTEN"]:
        if f["FristArtID"] == 5:
            wv[f["AkteNr"]].append(f["Datum"].date())
    for f in db["FRISTEN"]:
        if f["AkteNr"] not in akten:
            continue
        d = f["Datum"].date()
        due_wv = workdays_before(d, 5)
        wv_set = f["FristArtID"] != 5 and any(x <= due_wv for x in wv.get(f["AkteNr"], []) if x <= d)
        rec("frist", f"FR-{f['FristNr']}",
            {"name": f"{fristart.get(f['FristArtID'], 'Frist')} {d.isoformat()} ({az[f['AkteNr']]})",
             "datum": d, "fristart": fristart.get(f["FristArtID"]), "text": f["FristText"],
             "erledigt": ja(f["Erledigt"]), "erledigt_am": day(f["ErledigtAm"]), "erfasst_am": day(f["ErfasstAm"]),
             "rot": ja(not f["Erledigt"] and today <= d <= today + dt.timedelta(days=10)),
             "verpasst": ja(not f["Erledigt"] and d < today), "wiedervorlage": ja(wv_set),
             "ist_wiedervorlage": ja(f["FristArtID"] == 5), "hat_verantwortung": ja(bool(mkey(f["Verantwortlich"])))},
            computed=["rot", "verpasst", "wiedervorlage", "ist_wiedervorlage", "hat_verantwortung"],
            links=[("frist_von", az[f["AkteNr"]]), ("verantwortlich", mkey(f["Verantwortlich"]))])

    for t in db["TERMINE"]:
        if t["AkteNr"] not in akten:
            continue
        b = t["Beginn"]
        rec("termin", f"TE-{t['TerminNr']}",
            {"name": f"{TERMINART.get(t['Art'], 'Termin')} {b.date().isoformat()} {t['Betreff'] or ''}".strip(),
             "beginn": b.date(), "zeit": b.strftime("%H:%M"), "ende": t["Ende"].strftime("%H:%M") if t["Ende"] else None,
             "terminart": TERMINART.get(t["Art"], t["Art"]), "betreff": t["Betreff"], "ort": t["Ort"]},
            links=[("termin_von", az[t["AkteNr"]]), ("teilnehmer", mkey(t["Kuerzel"]))])

    for r in db["RECHNUNGEN"]:
        if r["AkteNr"] not in akten:
            continue
        d = r["Datum"].date()
        art = RECHNUNGSART.get(r["Art"], r["Art"])
        open_ = r["Status"] in ("offen", "gemahnt")
        rel = "honorarnote_zu" if r["Art"] == "R" else "kostenvorschuss_zu"
        rec("rechnung", f"RE-{r['RechNr']}",
            {"name": f"{art} {r['RechNr']} ({az[r['AkteNr']]})", "rechnungsart": art, "datum": d,
             "honorar": r["Honorar"], "auslagen": r["Auslagen"], "mwst_satz": r["MwStSatz"], "total": r["Total"],
             "zahlstatus": r["Status"], "bezahlt_am": day(r["BezahltAm"]),
             "tage_offen": (today - d).days if open_ else None},
            computed=["tage_offen"], links=[("rechnung_von", az[r["AkteNr"]]), (rel, az[r["AkteNr"]])])

    # Documents of the electronic matters (the others are records with placeholder files)
    for n in sorted(electronic):
        ds = sorted(docs[n], key=lambda d: d["ErstelltAm"])
        last_erl = max((d["ErstelltAm"] for d in ds if d["DokTyp"] in ERLEDIGUNG), default=None)
        for d in ds:
            rel = EVIDENCE.get(d["DokTyp"])
            if d["DokTyp"] == "Brief-Mandant" and last_erl and d["ErstelltAm"] > last_erl:
                rel = "abschlussmitteilung_zu"
            path = f"Quarzfels/Praxis/Autodoc/{d['Ordner']}/{d['Dateiname']}"
            rec("dokument", f"DOK-{d['DokNr']}",
                {"name": d["Bezeichnung"] or d["Dateiname"], "doktyp": d["DokTyp"], "bezeichnung": d["Bezeichnung"],
                 "dateiname": d["Dateiname"], "pfad": path, "erstellt_am": day(d["ErstelltAm"]),
                 "groesse": d["Groesse"], "lesbar": ja(readable(d))},
                computed=["lesbar"],
                links=[("dokument_von", az[n]), (rel, az[n]) if rel else ("", ""), ("erstellt_von", mkey(d["ErstelltVon"]))])

    stand, terms = abc
    seen = set()
    for term, meaning in terms:
        key = "BEGRIFF-" + slug(term)
        if key in seen or key == "BEGRIFF-":
            continue
        seen.add(key)
        rec("begriff", key, {"name": term, "begriff": term, "bedeutung": meaning, "stand": stand},
            links=[("begriff_von", "KANZLEI")])

    # Drop the placeholder link of documents without evidence relation
    for r in recs:
        links = r["payload"].get("links")
        if links:
            r["payload"]["links"] = [l for l in links if l["relation"]]
            if not r["payload"]["links"]:
                del r["payload"]["links"]
    return recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, type=Path)
    ap.add_argument("--share", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--today", default=dt.date.today().isoformat())
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.today)
    db = read_db(a.env.expanduser())
    recs = build(db, read_abc(a.share.expanduser()), today)
    keys = [r["external_id"] for r in recs]
    dup = [k for k, c in collections.Counter(keys).items() if c > 1]
    if dup:
        sys.exit(f"{len(dup)} duplicate keys")
    gen_id = hashlib.sha256("\n".join(sorted(f"{r['external_id']}@{r['version']}" for r in recs)).encode()).hexdigest()[:24]
    msgs = recs + [
        {"type": "generation", "source": SOURCE, "payload": {"id": gen_id, "complete": True, "counts": {"records": len(recs), "tombstones": 0}}},
        {"type": "state", "source": SOURCE, "state": {"read_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "today": today.isoformat(), "generation": gen_id}},
    ]
    with a.out.expanduser().open("w", encoding="utf-8") as fh:
        for m in msgs:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    kinds = collections.Counter(r["payload"]["record_kind"] for r in recs)
    print(json.dumps({"records": len(recs), "by_kind": dict(sorted(kinds.items())), "today": today.isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
