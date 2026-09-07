"""Per-document German bodies. Same facts, different genres — not one skeleton."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from demo_kanzlei.filenames import letterhead_block
from demo_kanzlei.models import PlanRow, World
from demo_kanzlei.plan_documents import plan_hash

TEMPLATE_VERSION = "akten-v3"


def write_prose(row: PlanRow, world: World, cache_dir: Path) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{TEMPLATE_VERSION}:{plan_hash(row)}".encode()).hexdigest()
    cached = cache_dir / f"{digest}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    text = template_prose(row, world)
    cached.write_text(text, encoding="utf-8")
    return text


def template_prose(row: PlanRow, world: World) -> str:
    if row.tier == "scan" or row.doc_type in {"Klageantwort", "Eingang-Behoerde"}:
        return _incoming(row, world)
    if row.doc_type.startswith("E-Mail"):
        return _email(row, world)
    builders = {
        "Mandatsvereinbarung": _mandatsvereinbarung,
        "Vollmacht": _vollmacht,
        "Konfliktpruefung": _konflikt,
        "Aktennotiz": _aktennotiz,
        "Telefonnotiz": _telefonnotiz,
        "Pendenzenliste": _pendenzen,
        "Abmahnung": _abmahnung,
        "Brief-Gegenanwalt": _brief_gegenanwalt,
        "Brief-Mandant": _brief_mandant,
        "Klage": _klage,
        "Replik": _replik,
        "Stellungnahme": _stellungnahme,
        "Vorladung": _vorladung,
        "Verhandlungsprotokoll": _protokoll,
        "Urteil": _urteil,
        "Vergleich": _vergleich,
        "Sistierungsvermerk": _sistierung,
        "Beilage-Vertrag": _beilage,
        "Rechtsgutachten": _gutachten,
        "Honorarnote": _honorarnote,
        "Leistungserfassung": _leistung,
        "Kostenvorschuss": _kostenvorschuss,
        "Mandatsvereinbarung-Muster": _muster,
        "Vollmacht-Muster": _muster,
        "Partnersitzung-Protokoll": _partnersitzung,
        "GwG-Weisung": _gwg,
        "Merkblatt-Datenschutz": _merkblatt,
        "Honorarordnung": _honorarordnung,
    }
    fn = builders.get(row.doc_type, _brief_mandant)
    return fn(row, world)


def write_all(rows: list[PlanRow], world: World, cache_dir: Path) -> dict[str, str]:
    bodies = {}
    for row in rows:
        bodies[row.id] = write_prose(row, world, cache_dir)
    (cache_dir / "index.json").write_text(
        json.dumps({row_id: hashlib.sha256(text.encode()).hexdigest() for row_id, text in bodies.items()}),
        encoding="utf-8",
    )
    return bodies


def _f(row: PlanRow) -> dict[str, str]:
    return row.facts


def _ref(row: PlanRow) -> str:
    facts = _f(row)
    return letterhead_block(
        unser_zeichen=facts.get("unser_zeichen", row.matter_aktenzeichen or ""),
        aktenzeichen=facts.get("aktenzeichen", row.matter_aktenzeichen or ""),
        in_sachen=facts.get("in_sachen", ""),
    )


def _voice(row: PlanRow) -> str:
    return _f(row).get("voice", "practical")


def _plot(row: PlanRow) -> str:
    """Same bible on every document of the Akte — not a new random dispute."""
    facts = _f(row)
    hook = facts.get("hook") or facts.get("summary") or ""
    place = facts.get("place") or ""
    trigger = facts.get("trigger") or ""
    parts = [hook] if hook else []
    if place:
        parts.append(f"Streitort ist {place}.")
    if trigger:
        parts.append(f"Auslöser: {trigger}.")
    return " ".join(parts)


def _extra_verbose(row: PlanRow) -> str:
    if _voice(row) != "verbose":
        return ""
    return (
        "\nNach nochmaliger Durchsicht der Belege und der Korrespondenz der letzten "
        "Wochen erscheint mir angezeigt, den Sachverhalt hier etwas breiter zu fassen, "
        "auch soweit er für den unmittelbaren nächsten Schritt nur mittelbar relevant ist. "
        f"{_plot(row)}\n"
    )


def _statute(row: PlanRow) -> str:
    cited = _f(row).get("statute") or ""
    if cited:
        return f"\nMassgeblich ist {cited}.\n"
    if _voice(row) in {"precise", "academic"}:
        return "\nRechtlich stütze ich mich namentlich auf Art. 18 OR sowie Art. 221 ff. ZPO.\n"
    return ""


def _firm_block(world: World) -> str:
    f = world.firm
    return f"{f.name}\n{f.street}, {f.zip_code} {f.seat}\nTel. {f.phone}\n"


def _sign(row: PlanRow, world: World) -> str:
    return f"{_f(row).get('author', '')}\n{world.firm.name}\n"


def _mandatsvereinbarung(row: PlanRow, world: World) -> str:
    facts = _f(row)
    ansatz = facts.get("honorar_ansatz", "CHF 380")
    clauses = [
        f"1. Gegenstand. Die Mandantin {facts['client']} beauftragt die Kanzlei in Sachen {facts['in_sachen']}. {_plot(row)}",
        "2. Umfang. Umfasst sind aussergerichtliche Korrespondenz, Strategie und, soweit nötig, Prozessführung.",
        f"3. Honorar. Abrechnung nach Zeitaufwand, Ansatz {ansatz} zzgl. Auslagen und MwSt.",
        "4. Kostenvorschuss. Ein angemessener Vorschuss ist vor Arbeitsbeginn geschuldet.",
        "5. Vollmacht. Eine separate Prozessvollmacht wird eingeholt.",
    ]
    if _voice(row) == "terse":
        clauses = clauses[:3]
    return (
        "MANDATSVEREINBARUNG\n\n"
        f"{_firm_block(world)}\n{_ref(row)}\n{row.date}\n\n"
        + "\n".join(clauses)
        + "\n\nDie Mandantin bestätigt den Auftrag mit Unterschrift.\n\n"
        + _sign(row, world)
    )


def _vollmacht(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        "PROZESSVOLLMACHT\n\n"
        f"{_ref(row)}\n{row.date}\n\n"
        f"Die Unterzeichnende, {facts['client']}, bevollmächtigt "
        f"{facts.get('author', 'den unterzeichnenden Anwalt')} der {world.firm.name}, "
        f"sie in Sachen {facts['in_sachen']} vor allen Behörden und Gerichten zu vertreten, "
        "Vergleiche zu schliessen und Zustellungen entgegenzunehmen.\n\n"
        "Diese Vollmacht gilt bis auf Widerruf.\n\n"
        "___________________________\nUnterschrift Mandantschaft\n"
    )


def _konflikt(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        "KONFLIKTPRÜFUNG (intern)\n\n"
        f"{_ref(row)}\nVerfasst: {facts.get('author')}  |  {row.date}\n\n"
        f"Geprüft gegen die Mandanten- und Gegenparteienliste. Mandantin {facts['client']}, "
        f"Gegenpartei {facts['opposing']}. Sache: {_plot(row)} "
        "Keine laufende Vertretung der Gegenseite, "
        "kein wirtschaftlicher Interessenkonflikt ersichtlich.\n\n"
        "Ergebnis: Mandat kann angenommen werden.\n"
        + _extra_verbose(row)
    )


def _aktennotiz(row: PlanRow, world: World) -> str:
    facts = _f(row)
    core = (
        f"Besprochen wurde: {_plot(row)} "
        f"Mandantin {facts['client']} erwartet einen konkreten nächsten Schritt."
    )
    if _voice(row) == "terse":
        body = f"- {core}\n- Nächster Schritt intern klären.\n"
    elif _voice(row) == "verbose":
        body = (
            f"{core}\n\n"
            "Im Einzelnen: Aktenlage, Beweismittel und Fristen wurden durchgegangen. "
            "Offen bleiben noch Beilagen der Mandantschaft. Ich habe die Pendenzen angepasst "
            "und um eine kurze Rückmeldung bis Ende Woche gebeten. "
            "Sollte die Gegenseite nicht reagieren, ist der Klageweg vorzubereiten.\n"
            + _extra_verbose(row)
        )
    else:
        body = f"{core}\n\nNächster Schritt: Korrespondenz an die Gegenseite bzw. Eingabe, je nach Lage.\n"
    return (
        "AKTENNOTIZ intern — nicht an die Mandantschaft\n\n"
        f"{_ref(row)}\nVerfasst von {facts.get('author')} am {row.date}\n\n"
        f"{body}"
    )


def _telefonnotiz(row: PlanRow, world: World) -> str:
    facts = _f(row)
    who = facts.get("contact_name", facts["client"])
    return (
        "TELEFONNOTIZ\n\n"
        f"{_ref(row)}\n{row.date}, mit {who}\n\n"
        f"Gesprächsinhalt: {_plot(row)} "
        "Die Mandantschaft bleibt bei ihrem Antrag. Rückruf nicht nötig, "
        "solange die nächste Eingabe planmässig erfolgt.\n"
    )


def _pendenzen(row: PlanRow, world: World) -> str:
    facts = _f(row)
    frist = facts.get("frist", "offen")
    return (
        "PENDENZENLISTE\n\n"
        f"{_ref(row)}\nStand {row.date}\n\n"
        f"1. Beilagen der Mandantschaft {facts['client']} nachfassen\n"
        f"2. Frist {frist} überwachen\n"
        f"3. Entwurf nächste Eingabe ({facts.get('place', facts.get('summary', 'Sache'))})\n"
        "4. Honorarstand der Buchhaltung melden\n"
    )


def _abmahnung(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        f"{_firm_block(world)}\nEinschreiben\n\n"
        f"{facts['opposing']}\n\n{row.date}\n\n"
        f"{_ref(row)}\n"
        f"Abmahnung\n\n"
        f"Sehr geehrte Damen und Herren\n\n"
        f"Wir vertreten {facts['client']}. {_plot(row)} "
        "Wir setzen Ihnen Frist von zehn Tagen, die Forderung anzuerkennen "
        "und die Mängel zu beheben, widrigenfalls wir ohne weitere Anzeige klagen.\n"
        f"{_statute(row)}\n"
        "Mit vorzüglicher Hochachtung\n"
        + _sign(row, world)
    )


def _brief_gegenanwalt(row: PlanRow, world: World) -> str:
    facts = _f(row)
    opening = "Sehr geehrter Herr Kollege, sehr geehrte Frau Kollegin"
    if _voice(row) == "formal":
        opening = "Hochgeachtete Frau Kollegin, hochgeachteter Herr Kollege"
    return (
        f"{_firm_block(world)}\n{row.date}\n\n{_ref(row)}\n"
        f"{opening}\n\n"
        f"In vorbezeichneter Sache vertreten wir {facts['client']}. "
        f"{_plot(row) or 'Wir halten an unserem Standpunkt fest.'} "
        "Wir ersuchen um Stellungnahme innert zehn Tagen.\n"
        f"{_statute(row)}{_extra_verbose(row)}\n"
        "Mit kollegialer Begrüssung\n"
        + _sign(row, world)
    )


def _brief_mandant(row: PlanRow, world: World) -> str:
    facts = _f(row)
    who = facts.get("contact_name", "Damen und Herren")
    if _voice(row) == "terse":
        body = f"Kurz der Stand: {_plot(row) or 'Die Sache läuft.'} Wir geben Nachricht, sobald etwas ansteht.\n"
    else:
        body = (
            f"anbei der Stand in Ihrer Sache {facts['in_sachen']}. "
            f"{_plot(row)} "
            "Bitte liefern Sie uns noch die offenen Belege; ohne sie können wir die "
            "nächste Eingabe nicht sauber begründen.\n"
        )
    return (
        f"{_firm_block(world)}\n{row.date}\n\n{_ref(row)}\n"
        f"Guten Tag {who}\n\n{body}{_extra_verbose(row)}\n"
        "Freundliche Grüsse\n"
        + _sign(row, world)
    )


def _klage(row: PlanRow, world: World) -> str:
    facts = _f(row)
    streitwert = facts.get("streitwert", "CHF 50'000")
    frist = facts.get("frist", "gemäss richterlicher Ansetzung")
    begehren = (
        f"1. Die Beklagte sei zu verpflichten, der Klägerin {streitwert} zzgl. Zins zu 5% seit {row.date} zu bezahlen.\n"
        "2. Unter Kosten- und Entschädigungsfolge.\n"
    )
    sachverhalt = (
        f"Die Klägerin {facts['client']} und die Beklagte {facts['opposing']} "
        f"stritten in Sachen {facts['in_sachen']}. {_plot(row)}"
    )
    if _voice(row) == "terse":
        begruendung = "Die Forderung ist fällig und unbestritten geblieben."
    else:
        begruendung = (
            "Die Klägerin hat ihre Leistungen erbracht. Die Beklagte ist im Verzug. "
            f"Eine aussergerichtliche Lösung ist gescheitert. Klagefrist bzw. nächste Frist: {frist}."
        )
    return (
        f"An das {facts.get('court', 'zuständige Gericht')}\n\n"
        f"Klage\n\n{_ref(row)}\n{row.date}\n\n"
        f"Klägerin: {facts['client']}\nBeklagte: {facts['opposing']}\n"
        f"Streitwert: {streitwert}\n\n"
        f"Rechtsbegehren\n{begehren}\n"
        f"Sachverhalt\n{sachverhalt}\n\n"
        f"Begründung\n{begruendung}\n{_statute(row)}{_extra_verbose(row)}\n"
        + _sign(row, world)
    )


def _replik(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        f"An das {facts.get('court', 'Gericht')}\n\nReplik\n\n{_ref(row)}\n{row.date}\n\n"
        "Die Klageantwort wird bestritten, soweit sie nicht ausdrücklich anerkannt wird. "
        f"{_plot(row)} Die Klägerin hält an den Rechtsbegehren fest.\n"
        f"{_statute(row)}{_extra_verbose(row)}\n"
        + _sign(row, world)
    )


def _stellungnahme(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        f"{_firm_block(world)}\n{row.date}\n\n{_ref(row)}\n"
        "Stellungnahme\n\nSehr geehrte Damen und Herren\n\n"
        f"Namens {facts['client']} nehmen wir innert Frist Stellung. "
        f"{_plot(row)} Eine Verletzung wird bestritten; "
        "eventualiter wird um angesetzte Nachfrist ersucht.\n"
        f"{_statute(row)}\nHochachtungsvoll\n"
        + _sign(row, world)
    )


def _vorladung(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        f"{facts.get('court', 'Gericht')}\nVorladung zur Hauptverhandlung\n\n"
        f"Ihr Zeichen: {facts['aktenzeichen']}\nIn Sachen: {facts['in_sachen']}\n"
        f"Aktenzeichen: {facts['aktenzeichen']}\n\n"
        f"{row.date}\n\n"
        "Die Parteien werden auf den in der Verfügung genannten Termin vorgeladen. "
        "Schriftliche Eingaben sind im Doppel einzureichen.\n"
        f"{_plot(row)}\n"
    )


def _protokoll(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        f"Verhandlungsprotokoll — {facts.get('court', 'Gericht')}\n\n"
        f"{_ref(row)}\n{row.date}\n\n"
        f"Anwesend: Vertreter/in {facts['client']}; Vertreter/in {facts['opposing']}.\n"
        f"Verhandlung: {_plot(row) or 'Hauptverhandlung'}. "
        "Das Gericht stellt Fragen zum Sachverhalt. Entscheid bleibt vorbehalten.\n"
    )


def _urteil(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        f"{facts.get('court', 'Gericht')}\n\nUrteil\n\n"
        f"Aktenzeichen: {facts['aktenzeichen']}\nIn Sachen: {facts['in_sachen']}\n"
        f"{row.date}\n\n"
        "Das Gericht erkennt:\n"
        f"1. Die Klage der {facts['client']} wird im Sinne der Erwägungen gutgeheissen bzw. teilweise gutgeheissen.\n"
        "2. Die Kosten werden nach Obsiegen und Unterliegen verlegt.\n"
        f"{facts.get('outcome') or _plot(row)}\n"
    )


def _vergleich(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        f"VERGLEICH\n\n{_ref(row)}\n{row.date}\n\n"
        f"Zwischen {facts['client']} und {facts['opposing']} wird vereinbart: "
        "Die Parteien erklären die Angelegenheit als erledigt. "
        f"{_plot(row)} Jede Partei trägt ihre Kosten, soweit nichts anderes bestimmt ist.\n"
    )


def _sistierung(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        "SISTIERUNGSVERMERK intern\n\n"
        f"{_ref(row)}\n{row.date}\n\n"
        f"Die Akte wird sistiert. {_plot(row)} "
        "Fristenkalender bleibt aktiv; Wiedervorlage in drei Monaten.\n"
    )


def _beilage(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        f"VERTRAG / BEILAGE (Abschrift für die Akte)\n\n"
        f"Aktenzeichen: {facts['aktenzeichen']}\nIn Sachen: {facts['in_sachen']}\n"
        f"{row.date}\n\n"
        f"Vertrag zwischen {facts['client']} und {facts['opposing']}. "
        f"Urkunde: {facts.get('beilage', 'Vertragsabschrift')}. {_plot(row)}\n\n"
        "§1 Gegenstand  §2 Leistung  §3 Vergütung  §4 Mängel  §5 Gerichtsstand Zürich.\n"
    )


def _gutachten(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        "RECHTSGUTACHTEN intern\n\n"
        f"{_ref(row)}\n{facts.get('author')}  |  {row.date}\n\n"
        f"Frage: {_plot(row) or 'rechtliche Beurteilung'}\n\n"
        "Kurzantwort: Ein Anspruch ist vertretbar, aber nicht risikofrei. "
        "Ich empfehle, die Beweislage zu verdichten, bevor ein weitergehender Schritt erfolgt.\n"
        f"{_statute(row)}{_extra_verbose(row)}"
    )


def _honorarnote(row: PlanRow, world: World) -> str:
    facts = _f(row)
    betrag = facts.get("betrag") or facts.get("honorar_total") or "CHF 9'800"
    ansatz = facts.get("honorar_ansatz", "CHF 420")
    return (
        f"HONORARNOTE\n{world.firm.name} — Buchhaltung\n\n"
        f"{_ref(row)}\nDatum {row.date}\nRechnungsempfängerin: {facts['client']}\n\n"
        "Pos.  Beschreibung                         Stunden  Ansatz\n"
        f"1     Aktenstudium und Korrespondenz        6.00    {ansatz}\n"
        "2     Eingaben / Verhandlung                8.00    dito\n"
        "3     Auslagen Porto und Kopien             pauschal\n\n"
        f"Total {betrag} zzgl. MwSt., zahlbar innert 30 Tagen.\n"
        f"Vermerk: Honorar in der Sache {facts.get('place', facts['in_sachen'])}. {_plot(row)}\n"
    )


def _leistung(row: PlanRow, world: World) -> str:
    facts = _f(row)
    return (
        "LEISTUNGSERFASSUNG (intern)\n\n"
        f"{_ref(row)}\nPeriode bis {row.date}  |  Sachbearbeiter {facts.get('author')}\n\n"
        f"{row.date}   1.5 h   Aktenstudium\n"
        f"{row.date}   0.8 h   Telefon {facts.get('contact_name', facts['client'])}\n"
        f"{row.date}   2.0 h   Entwurf Eingabe\n\n"
        f"{_plot(row)}\n"
    )


def _kostenvorschuss(row: PlanRow, world: World) -> str:
    facts = _f(row)
    betrag = facts.get("betrag") or facts.get("vorschuss") or "CHF 4'500"
    return (
        f"KOSTENVORSCHUSSRECHNUNG\n{world.firm.name}\n\n"
        f"{_ref(row)}\n{row.date}\n\n"
        f"An {facts['client']}\n\n"
        f"Gemäss Mandatsvereinbarung stellen wir einen Kostenvorschuss von {betrag} in Rechnung. "
        f"Mandat: {_plot(row)} Die Arbeit an der Sache beginnt nach Eingang.\n"
        "Zahlbar innert 10 Tagen.\n"
    )


def _email(row: PlanRow, world: World) -> str:
    facts = _f(row)
    to_name = facts.get("contact_name", facts.get("client", ""))
    kind = facts.get("kind", "")
    if "gegenpartei" in kind or row.doc_type.endswith("Gegenpartei"):
        greet = f"Guten Tag, in Sachen {facts['in_sachen']} — kurz und ohne Umschweife"
        if _voice(row) == "formal":
            greet = "Sehr geehrte Frau Kollegin, sehr geehrter Herr Kollege"
        body = (
            f"anbei unser Schreiben. {_plot(row)} "
            "Wir erwarten Ihre Stellungnahme innert der angesetzten Frist."
        )
        if _voice(row) == "practical":
            body += " Bitte bestätigen Sie den Empfang. (falls das Mail in den Spam rutscht: kurz Bescheid)"
    elif "gericht" in kind or row.doc_type.endswith("Gericht"):
        greet = f"Sehr geehrte Damen und Herren des {facts.get('court', 'Gerichts')}"
        body = (
            f"hiermit reicht die Klägerin die beiliegende Eingabe ein. "
            f"Akte {facts['aktenzeichen']}. {_plot(row)}"
        )
    elif "behoerde" in kind or row.doc_type.endswith("Behoerde"):
        greet = "Sehr geehrte Damen und Herren"
        body = (
            f"Namens {facts['client']} übermitteln wir die Stellungnahme innert Frist. "
            f"{_plot(row)}"
        )
    else:
        greet = f"Guten Tag {to_name}"
        if _voice(row) == "terse":
            body = f"Stand heute: {_plot(row) or 'wir sind dran.'} Mehr sobald es etwas zu entscheiden gibt."
        else:
            body = (
                f"kurz der Stand in Ihrer Sache {facts['aktenzeichen']}. "
                f"{_plot(row)} Wenn die Belege bis Freitag da sind, können wir weiterfahren."
            )
    return (
        f"{greet}\n\n"
        f"Aktenzeichen: {facts['aktenzeichen']}\n"
        f"In Sachen: {facts['in_sachen']}\n"
        f"Unser Zeichen: {facts.get('unser_zeichen', '')}\n\n"
        f"{body}\n{_extra_verbose(row)}\n"
        f"Freundliche Grüsse\n{facts.get('author')}\n{world.firm.name}\n"
    )


def _incoming(row: PlanRow, world: World) -> str:
    facts = _f(row)
    if row.doc_type == "Eingang-Behoerde":
        sender = "Aufsichtsbehörde — Eingangsstelle"
        body = (
            f"Ihre Eingabe in Sachen {facts['in_sachen']} ist eingegangen. "
            f"{_plot(row)} Ein Dossier wurde eröffnet. Sie erhalten die Verfügung unaufgefordert."
        )
    else:
        sender = f"Rechtsvertretung {facts['opposing']}"
        body = (
            f"Namens {facts['opposing']} bestreiten wir die Klage der {facts['client']} vollumfänglich. "
            f"Zum Vorwurf ({facts.get('place', '')}): {_plot(row)} "
            "Kosten- und Entschädigungsfolge zulasten der Klägerin."
        )
    return (
        f"{sender}\n{row.date}\n\n"
        f"Ihr Zeichen: {facts.get('unser_zeichen', facts['aktenzeichen'])}\n"
        f"Aktenzeichen: {facts['aktenzeichen']}\n"
        f"In Sachen: {facts['in_sachen']}\n\n"
        f"{body}\n"
    )


def _muster(row: PlanRow, world: World) -> str:
    return (
        f"VORLAGE — {row.doc_type}\n{world.firm.name}\n\n"
        f"Aktenzeichen: Kanzlei  |  Stand {row.date}\n\n"
        "Platzhalter für Name, Aktenzeichen, Honoraransatz. Nicht in Mandatsakten verwenden, "
        "bevor die Felder ersetzt sind.\n"
    )


def _partnersitzung(row: PlanRow, world: World) -> str:
    return (
        f"PROTOKOLL PARTNERSITZUNG\n{world.firm.name}, {world.firm.seat}\n{row.date}\n\n"
        f"Aktenzeichen: Kanzlei\n\n"
        "Traktanden: Auslastung, Risikoakten, Honorare, IT. "
        "Beschluss: Demo-Mandat 2024-017 bleibt bei Bär / Zimmermann.\n"
    )


def _gwg(row: PlanRow, world: World) -> str:
    return (
        f"GwG-WEISUNG intern\n{world.firm.name}\n\nAktenzeichen: Kanzlei\n{row.date}\n\n"
        "1. Identifikation der Vertragspartei vor Mandatsannahme.\n"
        "2. Verdachtsmeldungen über die GwG-Fachstelle.\n"
        "3. Keine Annahme von Mandaten in bar über den Schwellenwert ohne Dokumentation.\n"
    )


def _merkblatt(row: PlanRow, world: World) -> str:
    return (
        f"MERKBLATT DATENSCHUTZ\n{world.firm.name}\n\nAktenzeichen: Kanzlei\n{row.date}\n\n"
        "Mandantendaten nur zweckgebunden, Zugriff nach Akte, keine private Cloud. "
        "Auskunftsbegehren an das Sekretariat weiterleiten.\n"
    )


def _honorarordnung(row: PlanRow, world: World) -> str:
    return (
        f"HONORARORDNUNG\n{world.firm.name}\n\nAktenzeichen: Kanzlei\n{row.date}\n\n"
        "Partner CHF 450–550, Associates CHF 320–420, Substitut CHF 220. "
        "Auslagen zum Selbstkostenpreis. Semestrale Abrechnung, sonst nach Aufwandsschwelle.\n"
    )
