"""What every code of the connection layer means, in German, and what to do about it (MVP-28, KC-7b).

A component, Knovas Link and the Brain answer with codes (``source_unreachable``, ``conflict``, …), never with a
sentence: a sentence could carry a name or a value, a code cannot (GI-DATA-02/04). The Builder turns a code into a
title, an explanation and the fix, here and only here, so the same code reads the same on every page. A code without an
entry is shown as itself under «Unbekannter Code»; ``tests/test_builder_monitoring_mvp.py`` fails on a code the MVP
emits that has none.
"""
from __future__ import annotations

from typing import Any, Mapping

#: code -> title, explanation, fix. The codes are the contracts' (MVP-1, MVP-5, MVP-7, MVP-8, MVP-13, MVP-17, MVP-22)
#: and the Builder's own alerts.
CODES: Mapping[str, Mapping[str, str]] = {
    # Streams and jobs (MVP-1, MVP-18)
    "unsupported_type": {
        "title": "Nachrichtenart nicht unterstützt",
        "explanation": "Eine Komponente hat eine Art von Nachricht geschickt, die diese Version noch nicht verarbeitet "
                       "(zum Beispiel Datensätze oder Berechtigungen, die erst mit einer späteren Stufe kommen).",
        "fix": "Nichts zu tun, solange die Quelle nur Dokumente liefert. Sonst die Plattform und Knovas Link auf die "
               "Version bringen, die diese Art kennt.",
    },
    "unsupported": {
        "title": "Keine Komponente für diesen Auftrag",
        "explanation": "Knovas Link hat keine Komponente, die diese Art von Auftrag für diese Quelle ausführt.",
        "fix": "Prüfen, ob die Komponente der Quelle im Installationspaket ist und läuft (Betrieb › Verbindung); "
               "sonst das Paket mit der Komponente neu herunterladen und starten.",
    },
    "unreachable": {
        "title": "Komponente nicht erreichbar",
        "explanation": "Der Auftrag kam bis Knovas Link, aber die Komponente hat bis zur Frist nicht geantwortet.",
        "fix": "Am Standort «docker compose ps» und «docker compose logs» der Komponente prüfen und sie neu starten.",
    },
    "lease_lost": {
        "title": "Auftrag verfallen",
        "explanation": "Die Antwort kam, nachdem der Auftrag schon an eine andere Stelle vergeben oder abgelaufen war; "
                       "sie wurde verworfen und gezählt.",
        "fix": "Einzelne Fälle sind harmlos. Häufen sie sich, ist der Standort zu langsam oder die Verbindung "
               "unterbrochen: die Umlaufzeit der Probe ansehen.",
    },
    # Fetching originals (MVP-7)
    "source_unreachable": {
        "title": "Quelle nicht erreichbar",
        "explanation": "Das Original konnte nicht abgerufen werden: Knovas Link, die Komponente oder die Quelle hat "
                       "nicht rechtzeitig geantwortet. Der indexierte Text wird nie an seiner Stelle geliefert.",
        "fix": "Betrieb › Verbindung: Ist Knovas Link online und die Komponente gemeldet? Ist die Quelle lesbar "
               "(«Verbindung prüfen»)?",
    },
    "source_refused": {
        "title": "Quelle hat abgelehnt",
        "explanation": "Die Komponente oder die Quelle hat den Abruf abgelehnt; der genaue Grund steht als eigener Code dabei.",
        "fix": "Den genannten Code nachschlagen, oft ist der Abruf an der Quelle ausgeschaltet oder das Token reicht nicht.",
    },
    "no_source_ref": {
        "title": "Herkunft unbekannt",
        "explanation": "Für dieses Dokument weiss das Brain nicht, aus welcher Quelle und Version es stammt; ein Original "
                       "lässt sich deshalb nicht abrufen.",
        "fix": "Die Zuordnung der Quelle einmal anwenden: der erste Lauf mit dieser Version trägt die Herkunft für "
               "jedes Dokument nach, ohne es neu einzulesen.",
    },
    "not_found": {
        "title": "Nicht gefunden",
        "explanation": "Die Quelle kennt die Datei in dieser Version nicht (mehr).",
        "fix": "Neu synchronisieren; ist die Datei gelöscht, verschwindet der Treffer mit dem nächsten Lauf.",
    },
    # Writing back (MVP-8, MVP-22)
    "conflict": {
        "title": "Konflikt",
        "explanation": "Die Datei hat sich seit dem Vorschlag in der Quelle geändert; geschrieben wurde nichts.",
        "fix": "Nach der nächsten Synchronisation den Vorschlag neu stellen lassen, auf Grundlage der neuen Fassung.",
    },
    "mode_unsupported": {
        "title": "Schreibart nicht erlaubt",
        "explanation": "Verlangt wurde ein Schreiben ohne Prüfung durch die Quelle. In dieser Stufe wird nur als "
                       "Vorschlag geschrieben (bei GitHub als Pull Request).",
        "fix": "Den Vorschlag als Vorschlag stellen; selbständiges Schreiben kommt erst mit einer späteren Stufe.",
    },
    "token_cannot_write": {
        "title": "Token darf nicht schreiben",
        "explanation": "Das Token der Quelle darf keine Änderungen vorschlagen.",
        "fix": "Bei GitHub ein Token mit «Contents: write» und «Pull requests: write» erstellen, die Zeile "
               "SYNC_CREDENTIAL_… in knovas-sources.env ersetzen und neu starten.",
    },
    "fetch_disabled": {
        "title": "Abruf ausgeschaltet",
        "explanation": "Für diese Quelle ist «Agenten dürfen Originale abrufen» aus.",
        "fix": "Quellen & Zuordnung › Quelle: den Schalter einschalten, prüfen lassen und veröffentlichen.",
    },
    "write_disabled": {
        "title": "Zurückschreiben ausgeschaltet",
        "explanation": "Für diese Quelle ist «Änderungen dürfen zurückgeschrieben werden» aus.",
        "fix": "Quellen & Zuordnung › Quelle: den Schalter einschalten, prüfen lassen und veröffentlichen.",
    },
    "not_writable": {
        "title": "Nicht beschreibbar",
        "explanation": "Die Zuordnung erlaubt für dieses Attribut kein Zurückschreiben.",
        "fix": "In der Zuordnung unter «Zurückschreiben» das Attribut einschalten, wenn es ein einfaches Feld ist.",
    },
    "computed_attribute": {
        "title": "Berechnetes Attribut",
        "explanation": "Das Attribut wird aus anderen Angaben berechnet und nie in die Quelle geschrieben.",
        "fix": "Nichts zu tun: stattdessen die Angaben ändern, aus denen es berechnet wird.",
    },
    "value_not_allowed": {
        "title": "Wert nicht erlaubt",
        "explanation": "Der vorgeschlagene Wert erfüllt die Regeln der Quelle nicht (etwa mehr als ein Satz, ein Datum, "
                       "das keines ist, oder ein Wert ausserhalb der erlaubten Liste).",
        "fix": "Den Vorschlag mit einem gültigen Wert neu stellen lassen.",
    },
    "not_invertible": {
        "title": "Nicht umkehrbar",
        "explanation": "Der Wert entsteht in der Zuordnung mit Schritten oder einem Ausdruck; aus dem Brain lässt sich "
                       "nicht eindeutig sagen, was in der Quelle stehen müsste.",
        "fix": "Nur Attribute, die ein einfaches Feld der Quelle sind, lassen sich zurückschreiben.",
    },
    "unknown_mapping": {
        "title": "Zuordnung unbekannt",
        "explanation": "Die Komponente kennt die Zuordnung des Vorschlags nicht (mehr).",
        "fix": "Prüfen, ob die Zuordnung veröffentlicht ist und die Komponente ihr Paket neu geladen hat.",
    },
    "previously_rejected": {
        "title": "Schon abgelehnt",
        "explanation": "Genau diese Änderung wurde für diesen Knoten schon einmal abgelehnt; sie wird nicht erneut gestellt.",
        "fix": "Nichts zu tun. Soll sie doch gelten, die Änderung in der Quelle selbst machen.",
    },
    # Knovas Link (MVP-15, MVP-17)
    "waiting_for_certificate": {
        "title": "Wartet auf das Zertifikat",
        "explanation": "Knovas Link ist angemeldet, hat aber noch kein Zertifikat für das Brain. Bis dahin nimmt es "
                       "keine Aufträge an und schreibt nichts in den Mandanten.",
        "fix": "Den Zertifikatsschritt ausführen, wie in DEC-0056 entschieden; bis dahin wartet Knovas Link "
               "(waiting_for_certificate).",
    },
    "not_relayed": {
        "title": "Nicht weitergeleitet",
        "explanation": "Eine Komponente hat über Knovas Link eine Adresse des Brains verlangt, die Link nicht "
                       "weiterleitet.",
        "fix": "Die Komponente auf eine Version bringen, die zu dieser Version von Knovas Link passt.",
    },
    # The Builder's own alerts (MVP-28)
    "link_unreachable": {
        "title": "Knovas Link nicht erreichbar",
        "explanation": "Seit mehr als 10 Minuten ist keine Probe über den Arbeitskanal gelungen. Abrufe von Originalen "
                       "und Schreibaufträge dieses Standorts scheitern.",
        "fix": "Am Standort «docker compose ps» prüfen und Knovas Link neu starten; erreicht der Rechner Knovas "
               "ausgehend (Firewall)? Danach «Jetzt prüfen».",
    },
    "work_channel_down": {
        "title": "Arbeitskanal unterbrochen",
        "explanation": "Knovas Link meldet sich, aber seine Verbindung für Aufträge zu Knovas steht nicht.",
        "fix": "Die ausgehende Verbindung des Standorts zu Knovas prüfen (Firewall, Proxy); danach «Jetzt prüfen».",
    },
    "source_check_failed": {
        "title": "Quelle nicht lesbar",
        "explanation": "Die Komponente meldet, dass sie eine ihrer Quellen nicht lesen kann.",
        "fix": "Die Seite der Komponente nennt je Quelle, was zu tun ist; danach «Verbindung prüfen».",
    },
    "mapping_stale": {
        "title": "Zuordnung veraltet",
        "explanation": "Die Quelle wurde länger nicht gelesen, als die Frische der Zuordnung erlaubt.",
        "fix": "Läuft die Komponente, und ist ihr Zeitplan aktiv? Sonst einen Probelauf und das Anwenden anstossen.",
    },
    "proposal_waiting": {
        "title": "Vorschlag wartet",
        "explanation": "Ein Änderungsvorschlag wartet seit mehr als 48 Stunden auf eine Entscheidung.",
        "fix": "Betrieb › Freigaben: annehmen oder mit Begründung ablehnen.",
    },
    "write_conflict": {
        "title": "Schreiben endete im Konflikt",
        "explanation": "Ein angenommener Vorschlag wurde nicht geschrieben, weil sich die Datei inzwischen geändert hat.",
        "fix": "Nach der nächsten Synchronisation den Vorschlag neu stellen lassen.",
    },
    "write_refused": {
        "title": "Schreiben abgelehnt",
        "explanation": "Ein angenommener Vorschlag wurde von der Komponente oder der Quelle abgelehnt.",
        "fix": "Den Code beim Vorschlag nachschlagen (Betrieb › Freigaben).",
    },
}


#: Why a fact is not invertible (``not_invertible``), as brain-runner's inverse mapping (MVP-13) says it per fact. The
#: shared title stays «Nicht umkehrbar»; the sub-reason gives the explanation and the fix.
NOT_INVERTIBLE: Mapping[str, Mapping[str, str]] = {
    "not_a_plain_path": {
        "explanation": "Der Wert entsteht mit Schritten oder einem Ausdruck, oder er wird aus dem Dokument statt aus dem "
                       "Eintrag gelesen; aus dem Brain lässt sich nicht eindeutig sagen, was in der Quelle stehen müsste.",
        "fix": "Das Attribut ohne Schritte direkt an ein Feld des Eintrags binden, wenn es zurückgeschrieben werden soll.",
    },
    "not_a_stored_value": {
        "explanation": "Das Feld steht nicht in der Datei selbst, sondern wird beim Lesen abgeleitet (etwa Titel, Pfad "
                       "oder erster Absatz); in eine solche Angabe kann nichts geschrieben werden.",
        "fix": "An ein Feld binden, das in der Datei steht: bei Markdown im Front Matter, bei YAML oder JSON in den Daten.",
    },
    "stated_differently": {
        "explanation": "Mehrere Zieltypen-Einträge der Zuordnung setzen dieses Attribut mit unterschiedlichen Ausdrücken; "
                       "welcher zurückgeschrieben würde, ist nicht eindeutig.",
        "fix": "Das Attribut überall mit demselben Feld setzen, oder nur an einer Stelle.",
    },
    "combined_items": {
        "explanation": "Die Zuordnung führt mehrere Einträge zu einem Knoten zusammen; eine Datei hält dann nur einen Teil "
                       "des Knotens.",
        "fix": "Zurückschreiben geht nur bei Zuordnungen, die je Eintrag einen eigenen Knoten machen.",
    },
    "grouped_items": {
        "explanation": "Die Quelle der Zuordnung gruppiert Einträge; ein Wert gehört dann zu keiner einzelnen Stelle "
                       "einer Datei.",
        "fix": "Zurückschreiben geht nur bei Zuordnungen ohne Gruppierung.",
    },
    "several_items_per_file": {
        "explanation": "Die Auswahl der Zuordnung liest mehrere Einträge aus einer Datei; welcher Eintrag zu ändern wäre, "
                       "ist nicht eindeutig.",
        "fix": "Zurückschreiben geht nur, wenn je Datei ein Eintrag gelesen wird (Auswahl «@» oder ein einfacher Pfad).",
    },
    "several_file_sets": {
        "explanation": "Die Zuordnung liest mehrere Dateimengen zusammen; ein Wert gehört dann zu keiner einzelnen Datei.",
        "fix": "Zurückschreiben geht nur bei Zuordnungen, die eine Dateimenge lesen.",
    },
    "extractor_not_writable": {
        "explanation": "Diese Art von Datei kann nicht beschrieben werden; zurückgeschrieben wird in Markdown mit Front "
                       "Matter, YAML und JSON.",
        "fix": "Nichts zu tun, solange die Quelle in dieser Form gelesen wird.",
    },
    "computed_attribute": {
        "explanation": "Das Attribut wird aus anderen Angaben berechnet und nie in die Quelle geschrieben.",
        "fix": "Stattdessen die Angaben ändern, aus denen es berechnet wird.",
    },
}


def not_invertible(reason: Any) -> dict:
    """«Nicht umkehrbar» with the sub-reason's explanation and fix; an unknown sub-reason is named as itself."""
    found = NOT_INVERTIBLE.get(str(reason or ""))
    explanation = found["explanation"] if found else f"Grund «{reason}»." if reason else CODES["not_invertible"]["explanation"]
    return {"code": "not_invertible", "reason": str(reason or "not_invertible"), "title": CODES["not_invertible"]["title"],
            "explanation": explanation, "fix": found["fix"] if found else CODES["not_invertible"]["fix"],
            "text": f"{CODES['not_invertible']['title']}: {explanation}"}


def describe(code: Any) -> dict:
    """The title, explanation and fix of ``code``; a code without an entry is named as itself."""
    found = CODES.get(str(code or ""))
    if found is None:
        return {"code": str(code or ""), "title": "Unbekannter Code", "explanation": f"Code «{code}».",
                "fix": "Die Seite der Komponente und ihr Protokoll ansehen."}
    return {"code": str(code), **found}
