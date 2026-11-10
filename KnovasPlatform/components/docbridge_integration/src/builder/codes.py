"""What every code of the connection layer means, in German, and what to do about it (MVP-28, KC-7b).

A component, Knovas Link and the Brain answer with codes (``source_unreachable``, ``conflict``, …), never with a
sentence: a sentence could carry a name or a value, a code cannot (GI-DATA-02/04). The Builder turns a code into a
title, an explanation and the fix, here and only here, so the same code reads the same on every page. A code without an
entry is shown as itself under «Unbekannter Code»; ``tests/test_builder_monitoring_mvp.py`` fails on a code the MVP
emits that has none.
"""
from __future__ import annotations

import re
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
    "source_ref_missing": {
        "title": "Dokument nicht mehr vorhanden",
        "explanation": "Kein Dokument des Knotens nennt die Datei des Vorschlags noch, etwa nach einer Löschung im "
                       "Brain; der Vorschlag wurde nicht angenommen und nichts geschrieben.",
        "fix": "Den Vorschlag ablehnen. Ist die Datei nach der nächsten Synchronisation wieder da, einen neuen stellen.",
    },
    # Fetching and writing at the component (MVP-21, MVP-22)
    "too_large": {
        "title": "Zu gross",
        "explanation": "Das Original ist grösser, als ein Abruf übertragen darf (höchstens 25 MiB); es wurde nicht geliefert.",
        "fix": "Nichts zu tun: so grosse Dateien werden nicht abgerufen. Den Inhalt im Brain über die Suche lesen.",
    },
    "version_mismatch": {
        "title": "Version passt nicht",
        "explanation": "Die gelesenen Bytes ergeben nicht die Version, die verlangt war; geliefert wurde nichts, denn ein "
                       "Original in einer anderen Fassung wird nie als das verlangte ausgegeben.",
        "fix": "Neu synchronisieren und erneut abrufen; bleibt es so, Knovas melden.",
    },
    "edit_unsupported": {
        "title": "Änderung nicht sicher möglich",
        "explanation": "Die Datei lässt sich an dieser Stelle nicht sicher ändern: sie ist nicht UTF-8, der Wert ist keine "
                       "einzelne Angabe (eine Liste, eine Zuordnung, ein Verweis), der Schlüssel steht zweimal, oder keine "
                       "Schreibweise liest sich als der neue Wert zurück. Geschrieben wurde nichts.",
        "fix": "Die Änderung in der Quelle von Hand machen, oder das Feld in der Datei als einfache Angabe schreiben.",
    },
    "front_matter_missing": {
        "title": "Front Matter fehlt",
        "explanation": "Die Datei hat keinen Front-Matter-Block, wie die Zuordnung ihn liest; geschrieben wurde nichts.",
        "fix": "Prüfen, ob die Datei noch die ist, die die Zuordnung liest; sonst neu synchronisieren.",
    },
    "no_change": {
        "title": "Keine Änderung",
        "explanation": "Die Datei enthält die vorgeschlagenen Werte schon; ein Pull Request wäre leer und wurde nicht erstellt.",
        "fix": "Nichts zu tun; nach der nächsten Synchronisation zeigt das Brain die Werte.",
    },
    "github.rate_limited": {
        "title": "GitHub begrenzt die Anfragen",
        "explanation": "GitHub hat die Anfrage wegen zu vieler Aufrufe mit diesem Token abgewiesen.",
        "fix": "Später erneut versuchen; häuft es sich, ein eigenes Token für diese Quelle setzen.",
    },
    "github.branch_left": {
        "title": "Zweig ist stehen geblieben",
        "explanation": "Ein Schritt nach dem Anlegen des Zweigs für den Pull Request ist gescheitert, und der Zweig liess "
                       "sich nicht wieder löschen; er steht noch im Repository (knovas/write-…).",
        "fix": "Den Zweig im Repository löschen, dann den Vorschlag erneut annehmen lassen.",
    },
    "github.branch_exists": {
        "title": "Zweig schon vorhanden",
        "explanation": "Ein Zweig dieses Namens hält schon eine andere Änderung; er wird nie überschrieben oder gelöscht.",
        "fix": "Den Zweig im Repository prüfen und, wenn er nicht mehr gebraucht wird, löschen.",
    },
    "github.unreachable": {
        "title": "GitHub nicht erreichbar",
        "explanation": "Die Komponente hat GitHub nicht erreicht.",
        "fix": "Die ausgehende Verbindung des Standorts zu GitHub prüfen (Netz knovas-egress, Firewall, Proxy).",
    },
    "github.bad_repository": {
        "title": "Repository nicht gültig",
        "explanation": "Repository oder Branch der Quelle sind nicht gültig oder für dieses Token nicht sichtbar.",
        "fix": "Repository (owner/name) und Branch der Quelle prüfen, und ob das Token dieses Repository lesen darf.",
    },
    "github.bad_answer": {
        "title": "Unerwartete Antwort von GitHub",
        "explanation": "GitHub hat anders geantwortet, als die Komponente es erwartet.",
        "fix": "Später erneut versuchen; bleibt es so, Knovas melden.",
    },
    "github.base_not_a_branch": {
        "title": "Kein Branch",
        "explanation": "Die Quelle liest einen Tag oder eine Version, keinen Branch; ein Pull Request braucht einen Branch.",
        "fix": "An der Quelle einen Branch (etwa main) eintragen, wenn zurückgeschrieben werden soll.",
    },
    "runner.bad_change": {
        "title": "Änderung passt nicht zum Auftrag",
        "explanation": "Die übersetzte Änderung nennt eine andere Datei oder Version als der Auftrag, oder sie enthält "
                       "keinen Wert; es wurde nichts geschrieben.",
        "fix": "Den Vorschlag ablehnen und neu stellen; bleibt es so, Knovas melden.",
    },
    "bad_field": {
        "title": "Auftrag nicht annehmbar",
        "explanation": "Ein Feld des Auftrags liegt ausserhalb dessen, was die Komponente annimmt (etwa die Grösse "
                       "eines Abrufs).",
        "fix": "Knovas melden; der Auftrag kommt so nicht vom Brain.",
    },
    "internal": {
        "title": "Fehler in der Komponente",
        "explanation": "Die Komponente ist bei diesem Auftrag gescheitert; ihr Protokoll nennt die Fehlerart.",
        "fix": "Das Protokoll der Komponente am Standort prüfen und den Auftrag erneut versuchen; bleibt es so, Knovas melden.",
    },
    # Knovas Link (MVP-15, MVP-17)
    "waiting_for_certificate": {
        "title": "Wartet auf das Zertifikat",
        "explanation": "Knovas Link ist angemeldet, hat aber noch kein Zertifikat für das Brain. Bis dahin nimmt es "
                       "keine Aufträge an und schreibt nichts in den Mandanten.",
        "fix": "Zertifikat beantragen: auf dem Rechner «docker compose exec -T knovas-link python -m knovas_link "
               "certificate-request > site.csr»; Knovas signiert den Antrag (ein Knovas-Admin bestätigt; später "
               "signiert der Ausstellungsjob mit dem Schlüssel im HSM); dann «docker compose exec -T knovas-link python -m knovas_link "
               "install-certificate < site.crt». Knovas Link übernimmt das Zertifikat beim nächsten Heartbeat, ohne "
               "Neustart (DEC-0056, DEC-0062). Vor dem Signieren den Fingerabdruck des Antrags mit dem auf der Seite "
               "des Standorts vergleichen und das signierte Zertifikat dort mit «Zertifikat erfassen» eintragen: erst "
               "dann nimmt die Plattform Knovas Link mit ihm an.",
    },
    # install-certificate on the site's machine (DEC-0056, A1): it writes nothing and names one of these
    "certificate.not_enrolled": {
        "title": "Knovas Link nicht angemeldet",
        "explanation": "Knovas Link hat sich noch nicht bei der Plattform angemeldet und hat darum keinen Schlüssel, für "
                       "den ein Zertifikat beantragt oder installiert werden könnte.",
        "fix": "Knovas Link mit dem Einmal-Token aus dem Installationspaket starten («docker compose up -d»), dann das "
               "Zertifikat beantragen.",
    },
    "certificate.unreadable": {
        "title": "Kein Zertifikat erhalten",
        "explanation": "«install-certificate» hat kein Zertifikat im PEM-Format erhalten; es wurde nichts geschrieben.",
        "fix": "Das von Knovas signierte Zertifikat (-----BEGIN CERTIFICATE-----) als site.crt übergeben: "
               "«docker compose exec -T knovas-link python -m knovas_link install-certificate < site.crt».",
    },
    "certificate.wrong_key": {
        "title": "Zertifikat für einen anderen Schlüssel",
        "explanation": "Das Zertifikat gehört nicht zum Schlüssel dieses Knovas Link, etwa weil es aus einem anderen "
                       "Antrag oder vor «Token erneuern» signiert wurde; es wurde nichts geschrieben.",
        "fix": "Den Antrag dieses Knovas Link neu erstellen («… certificate-request > site.csr») und von Knovas "
               "signieren lassen.",
    },
    "certificate.not_valid_now": {
        "title": "Zertifikat nicht gültig",
        "explanation": "Das Zertifikat ist abgelaufen oder nach der Uhr des Rechners noch nicht gültig; es wurde nichts "
                       "geschrieben.",
        "fix": "Die Uhr des Rechners prüfen; sonst den Antrag neu von Knovas signieren lassen.",
    },
    "certificate.not_client_auth": {
        "title": "Zertifikat nicht für Clients",
        "explanation": "Das Zertifikat ist nicht für die Anmeldung als Client ausgestellt (erweiterte "
                       "Schlüsselverwendung clientAuth fehlt); es wurde nichts geschrieben.",
        "fix": "Den Antrag von der Client-CA von Knovas signieren lassen (internal-api, «/sign_certificate»).",
    },
    "certificate.wrong_issuer": {
        "title": "Zertifikat einer anderen Stelle",
        "explanation": "Das Zertifikat ist nicht von der Client-CA von Knovas signiert, die Knovas Link kennt; es wurde "
                       "nichts geschrieben.",
        "fix": "Den Antrag von der Client-CA von Knovas signieren lassen (internal-api, «/sign_certificate»).",
    },
    "certificate.wrong_tenant": {
        "title": "Zertifikat für einen anderen Mandanten",
        "explanation": "Das Zertifikat nennt einen anderen Mandanten (OU Semantix-Customer-…) als den dieses Standorts; "
                       "es wurde nichts geschrieben.",
        "fix": "Das Zertifikat bei Knovas sperren lassen und den Antrag für den Mandanten dieses Standorts neu "
               "signieren lassen.",
    },
    "certificate.no_client_ca": {
        "title": "Client-CA unbekannt",
        "explanation": "Knovas Link kennt die Client-CA von Knovas nicht und installiert darum kein Zertifikat.",
        "fix": "Knovas bittet, BUILDER_CONNECTOR_CLIENT_CA zu setzen, dann «Token erneuern»; oder am Standort "
               "LINK_CLIENT_CA auf die Datei der Client-CA setzen.",
    },
    "certificate.no_tenant": {
        "title": "Mandant unbekannt",
        "explanation": "Knovas Link kennt den Mandanten seines Standorts nicht und installiert darum kein Zertifikat.",
        "fix": "«Token erneuern», damit sich Knovas Link neu anmeldet und den Mandanten erfährt; oder am Standort "
               "LINK_TENANT setzen.",
    },
    "invalid_job": {
        "title": "Auftrag ungültig",
        "explanation": "Knovas Link hat einen Auftrag erhalten, der nicht dem Vertrag entspricht (fehlende oder unbekannte "
                       "Angaben), und ihn nicht an eine Komponente gegeben.",
        "fix": "Knovas Link und das Brain auf zueinander passende Versionen bringen; häuft es sich, Knovas melden.",
    },
    "part_not_accepted": {
        "title": "Teil nicht angenommen",
        "explanation": "Knovas Link konnte einen Teil eines abgerufenen Originals nicht an das Brain übergeben; der Abruf "
                       "ist gescheitert, ein unvollständiges Original wird nie geliefert.",
        "fix": "Erneut abrufen. Bleibt es so: die ausgehende Verbindung des Standorts und die Grösse der Datei prüfen.",
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
    # Change sets and previews (write back v2, FEAT-2026-11-10-write-back C-2): the Brain's Change Sets API; the
    # connector's own preview codes (preview_inexact, computed_field, …) are protocol 2.0's, in V2_CODES below
    "preview_missing": {
        "title": "Keine Vorschau",
        "explanation": "Die Komponente am Standort hat für diese Aktion noch keine Vorschau berechnet; ohne sie wird "
                       "nichts freigegeben.",
        "fix": "Warten, bis Knovas Link und die Komponente online sind (Betrieb › Verbindung); die Vorschau kommt "
               "von selbst. Bleibt sie aus, den Änderungssatz ablehnen und neu stellen lassen.",
    },
    "preview_not_exact": {
        "title": "Vorschau nicht genau",
        "explanation": "Die Komponente konnte die Änderung nicht genau berechnen; freigegeben wird nur, was genau "
                       "berechnet ist.",
        "fix": "Den Änderungssatz ablehnen; die Änderung in der Quelle selbst machen.",
    },
    "connector_inexact": {
        "title": "Vorschau nicht genau",
        "explanation": "Die Komponente hat ihre Vorschau als nicht genau gekennzeichnet; freigegeben wird nur, was "
                       "genau berechnet ist.",
        "fix": "Den Änderungssatz ablehnen; die Änderung in der Quelle selbst machen.",
    },
    "preview_stale": {
        "title": "Vorschau veraltet",
        "explanation": "Die Vorschau wurde für andere Angaben berechnet als die der Aktion.",
        "fix": "Warten, bis die Komponente die Vorschau neu berechnet hat, dann die Seite neu laden.",
    },
    "params_differ": {
        "title": "Vorschau für andere Angaben",
        "explanation": "Die Komponente hat die Vorschau für einen anderen Schritt berechnet als den der Aktion.",
        "fix": "Den Änderungssatz ablehnen und neu stellen lassen.",
    },
    "version_moved": {
        "title": "Quelle geändert",
        "explanation": "Die Datei hat sich seit der Version geändert, auf der die Vorschau beruhen muss; eine spätere "
                       "Änderung wird nie überschrieben.",
        "fix": "Den Änderungssatz ablehnen; nach der nächsten Synchronisation neu stellen lassen.",
    },
    "not_approvable": {
        "title": "Nicht freigebbar",
        "explanation": "Mindestens eine Aktion hat keine genaue Vorschau für genau ihre Angaben.",
        "fix": "Die Gründe bei den Aktionen ansehen; ablehnen, was keine genaue Vorschau bekommt.",
    },
    "not_proposed": {
        "title": "Nicht mehr offen",
        "explanation": "Der Änderungssatz wurde schon entschieden oder zurückgezogen.",
        "fix": "Die Seite neu laden.",
    },
    "staging_not_declared": {
        "title": "Schreibweise nicht angeboten",
        "explanation": "Die Komponente der Quelle schreibt nicht auf die Weise, die für die Quelle gewählt ist.",
        "fix": "Quellen & Zuordnung › Quelle: eine Schreibweise wählen, die die Komponente anbietet, prüfen lassen "
               "und veröffentlichen.",
    },
    "action_type_held": {
        "title": "Aktionsart angehalten",
        "explanation": "Ein früheres Schreiben dieser Aktionsart ergab in der Quelle etwas anderes als freigegeben; "
                       "bis eine Person den Halt aufhebt, wird sie auf dieser Quelle nicht freigegeben.",
        "fix": "Die Abweichung in der Quelle ansehen und den Halt im Brain aufheben lassen.",
    },
    "rejected": {
        "title": "Abgelehnt",
        "explanation": "Eine Person hat den Änderungssatz abgelehnt; geschrieben wurde nichts.",
        "fix": "Nichts zu tun.",
    },
    "document_purged": {
        "title": "Dokumente gelöscht",
        "explanation": "Die Dokumente des Mandanten wurden gelöscht; offene Änderungssätze wurden dabei abgelehnt.",
        "fix": "Nach dem nächsten Einlesen neu stellen lassen.",
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


#: Connector protocol 2.0 (draft; KnovasLink/contracts/v2/codes.json): the write path's codes.
V2_CODES: Mapping[str, Mapping[str, str]] = {
    "protocol_unsupported": {
        "title": "Protokoll 2 nicht eingeschaltet",
        "explanation": "Der Standort oder die Komponente spricht das Verbindungsprotokoll 2 (noch) nicht.",
        "fix": "Knovas Link mit LINK_PROTOCOL_V2=true und die Komponente mit ihrem Schalter für Protokoll 2 starten.",
    },
    "site_not_declared": {
        "title": "Standort nicht angemeldet",
        "explanation": "Knovas Link hat sich beim Brain noch nicht mit Protokoll 2 angemeldet.",
        "fix": "Warten, bis Knovas Link das Brain erreicht; sonst Knovas Link neu starten.",
    },
    "action_not_declared": {
        "title": "Aktion nicht angeboten",
        "explanation": "Die Komponente der Quelle hat diese Aktion nicht angemeldet; nichts wurde geschrieben.",
        "fix": "Die Aktion der Komponente prüfen (Betrieb › Verbindung) oder die Abbildung im Ontologie-Typ anpassen.",
    },
    "params_invalid": {
        "title": "Angaben passen nicht",
        "explanation": "Eine Angabe der Aktion fehlt, ist unbekannt oder hat nicht die angemeldete Art.",
        "fix": "Die Änderung mit den angemeldeten Angaben neu vorschlagen.",
    },
    "staging_unsupported": {
        "title": "Art des Schreibens nicht angeboten",
        "explanation": "Die Komponente bietet für diese Aktion weder direktes Schreiben noch die Prüfung in der Quelle "
                       "in der verlangten Art an.",
        "fix": "Bei der Quelle eine angebotene Art des Schreibens wählen.",
    },
    "grouping_unsupported": {
        "title": "Nur einzeln schreibbar",
        "explanation": "Die Quelle kann mehrere Schritte nicht gemeinsam schreiben; der Auftrag hatte mehrere.",
        "fix": "Nichts zu tun: das Brain schickt die Schritte einzeln.",
    },
    "preview_inexact": {
        "title": "Vorschau nicht möglich",
        "explanation": "Die Komponente kann nicht genau sagen, was sich in der Quelle ändern würde; die Änderung kann "
                       "nicht freigegeben werden.",
        "fix": "Die Änderung ohne berechnete Felder neu vorschlagen.",
    },
    "computed_field": {
        "title": "Feld wird berechnet",
        "explanation": "Die Änderung würde ein Feld setzen, das die Quelle selbst berechnet.",
        "fix": "Dieses Feld nicht ändern; es folgt aus anderen Feldern der Quelle.",
    },
    "target_not_found": {
        "title": "Eintrag nicht gefunden",
        "explanation": "Der Eintrag, den die Aktion ändern soll, gibt es in der Quelle nicht (mehr).",
        "fix": "Den nächsten Abgleich abwarten; das Brain folgt der Quelle.",
    },
    "version_changed": {
        "title": "Quelle hat sich geändert",
        "explanation": "Der Eintrag wurde nach der Vorschau in der Quelle geändert; nichts wurde geschrieben.",
        "fix": "Die Änderung ansehen und neu vorschlagen; die Vorschau zeigt dann den aktuellen Stand.",
    },
    "permit_missing": {
        "title": "Freigabe fehlt",
        "explanation": "Ein Schritt kam ohne seine unterschriebene Freigabe; Knovas Link hat nichts geschrieben.",
        "fix": "Melden: das Brain schickt jede Aktion nur mit ihrer Freigabe.",
    },
    "permit_mismatch": {
        "title": "Freigabe passt nicht",
        "explanation": "Die Freigabe nennt eine andere Quelle, Aktion, Angabe, Fassung oder Art des Schreibens als der "
                       "Auftrag; Knovas Link hat nichts geschrieben.",
        "fix": "Die Änderung neu freigeben. Häuft sich das, melden.",
    },
    "permit_bad_signature": {
        "title": "Freigabe ungültig",
        "explanation": "Die Unterschrift der Freigabe ließ sich mit den Schlüsseln des Brains nicht prüfen; Knovas Link "
                       "hat nichts geschrieben.",
        "fix": "Knovas Link neu starten, damit es die Schlüssel des Brains neu liest. Bleibt es so, melden.",
    },
    "permit_expired": {
        "title": "Freigabe abgelaufen",
        "explanation": "Die Freigabe kam nach ihrem Ablauf (eine Stunde) an; nichts wurde geschrieben.",
        "fix": "Die Änderung neu vorschlagen und freigeben.",
    },
    "permit_wrong_site": {
        "title": "Freigabe für einen anderen Standort",
        "explanation": "Die Freigabe nennt einen anderen Standort; nur der Standort mit dem Zugang zur Quelle schreibt.",
        "fix": "Prüfen, welcher Standort die Quelle bedient (Betrieb › Verbindung).",
    },
    "permit_used": {
        "title": "Schon geschrieben",
        "explanation": "Diese Freigabe wurde schon ausgeführt; die Antwort ist die erste Quittung, nichts wurde "
                       "noch einmal geschrieben.",
        "fix": "Nichts zu tun.",
    },
    "partly_written": {
        "title": "Teilweise geschrieben",
        "explanation": "Einige Schritte wurden geschrieben, andere nicht; jede Quittung sagt, welche.",
        "fix": "Die Quittungen ansehen und die fehlenden Schritte neu vorschlagen.",
    },
    "not_staged": {
        "title": "Prüfung in der Quelle unbekannt",
        "explanation": "Die Quelle kennt die angefragte Prüfung (zum Beispiel einen Änderungsvorschlag) nicht.",
        "fix": "In der Quelle nachsehen, ob die Prüfung gelöscht wurde.",
    },
    "write_log_unavailable": {
        "title": "Schreibprotokoll nicht schreibbar",
        "explanation": "Knovas Link konnte die Freigabe nicht in sein Protokoll am Standort schreiben; nichts wurde "
                       "geschrieben.",
        "fix": "Den Speicherplatz und das Volume von Knovas Link am Standort prüfen.",
    },
}
CODES = {**CODES, **V2_CODES}


def not_invertible(reason: Any) -> dict:
    """«Nicht umkehrbar» with the sub-reason's explanation and fix; an unknown sub-reason is named as itself."""
    found = NOT_INVERTIBLE.get(str(reason or ""))
    explanation = found["explanation"] if found else f"Grund «{reason}»." if reason else CODES["not_invertible"]["explanation"]
    return {"code": "not_invertible", "reason": str(reason or "not_invertible"), "title": CODES["not_invertible"]["title"],
            "explanation": explanation, "fix": found["fix"] if found else CODES["not_invertible"]["fix"],
            "text": f"{CODES['not_invertible']['title']}: {explanation}"}


#: A code family: GitHub answered with an HTTP status the component has no own code for.
GITHUB_HTTP = re.compile(r"^github\.http_(\d{3})$")


def describe(code: Any) -> dict:
    """The title, explanation and fix of ``code``; a code without an entry is named as itself."""
    family = GITHUB_HTTP.match(str(code or ""))
    if family:
        status = family.group(1)
        return {"code": str(code), "title": f"GitHub antwortete mit {status}",
                "explanation": f"GitHub hat die Anfrage der Komponente mit dem Status {status} beantwortet.",
                "fix": "Token und Repository prüfen: Ist das Token gültig, darf es dieses Repository lesen (und für "
                       "Vorschläge schreiben), und stimmen owner/name und Branch der Quelle?"}
    found = CODES.get(str(code or ""))
    if found is None:
        return {"code": str(code or ""), "title": "Unbekannter Code", "explanation": f"Code «{code}».",
                "fix": "Die Seite der Komponente und ihr Protokoll ansehen."}
    return {"code": str(code), **found}
