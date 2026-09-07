"""Hand-authored plots. One story bible per mandate; fillers pick a variant by seed."""
from __future__ import annotations

import hashlib

from dataclasses import replace
from demo_kanzlei.models import Story

NAMED: dict[str, Story] = {
    "2024-017": Story(
        plot_id="werklohn-schaffhauser",
        title="Werklohn und Mängelrüge Neubau Schaffhauserstrasse",
        hook="Rüegg hat den Restwerklohn für die Haustechnik am Neubau Schaffhauserstrasse 41 einbehalten.",
        place="Schaffhauserstrasse 41, Winterthur",
        opposing="Rüegg Haustechnik",
        streitwert="CHF 184'500",
        frist="2024-11-30",
        beilage="Werkvertrag vom 8. April 2022",
        trigger="Mängelrüge der Sanitärinstallation und Einbehalt des Restlohns",
        statute="Art. 82 und 368 OR",
        honorar_ansatz="CHF 420",
        honorar_total="CHF 21'840",
        vorschuss="CHF 8'000",
        outcome="Teilgutheissung erstinstanzlich",
    ),
    "2025-004": Story(
        plot_id="kuendigung-krankheit",
        title="Kündigung während Krankheit",
        hook="Rossi kündigte Reto Keller während ärztlich attestierter Arbeitsunfähigkeit.",
        place="Limmatstrasse 74, Zürich",
        opposing="Rossi",
        streitwert="CHF 48'600",
        frist="2025-09-15",
        beilage="Arbeitsvertrag vom 1. März 2019",
        trigger="Kündigung vom 12. Januar 2025 bei Krankheit seit 3. Januar 2025",
        statute="Art. 336c OR",
        honorar_ansatz="CHF 380",
        honorar_total="CHF 11'200",
        vorschuss="CHF 5'000",
    ),
    "2023-041": Story(
        plot_id="mietzins-linde",
        title="Mietzinsherabsetzung Gastraum",
        hook="Der Gastraum an der Seestrasse ist nach dem Umbau der Nachbarliegenschaft nicht mehr voll nutzbar.",
        place="Seestrasse 12, Wädenswil",
        opposing="Liegenschaften Hof AG",
        streitwert="CHF 36'000",
        frist="2023-08-31",
        beilage="Mietvertrag Gastrofläche vom 15. Mai 2018",
        trigger="Lärm und Sperrung des Gastgartens über zwei Saisons",
        statute="Art. 259d OR",
        honorar_ansatz="CHF 400",
        honorar_total="CHF 8'640",
        vorschuss="CHF 3'500",
        outcome="Herabsetzung um 18 Prozent, Akte geschlossen",
    ),
    "2024-112": Story(
        plot_id="gesellschafter-seehalde",
        title="Gesellschafterstreit Seehalde",
        hook="Brunner blockiert als Minderheit die Kapitalerhöhung der Seehalde Immobilien AG.",
        place="Zollikerstrasse 9, Zürich",
        opposing="Brunner",
        streitwert="CHF 250'000",
        frist="2025-02-28",
        beilage="Statuten und Aktionärbindungsvertrag 2016",
        trigger="Verweigerung der Zustimmung zur Kapitalerhöhung vom 4. Juni 2024",
        statute="Art. 706 OR",
        honorar_ansatz="CHF 450",
        honorar_total="CHF 16'400",
        vorschuss="CHF 10'000",
    ),
    "2025-019": Story(
        plot_id="edoeb-helvetiq",
        title="EDÖB-Stellungnahme Auftragsverarbeitung",
        hook="Ein Auftragsverarbeiter in Drittstaaten hat Patientendaten der Helvetiq ohne Auftragsvertrag verarbeitet.",
        place="Schlieren, Werk 2",
        opposing="Cloudbatch Ltd.",
        streitwert="n/a",
        frist="2025-06-30",
        beilage="Auftragsverarbeitungsvertrag-Entwurf und TOMs",
        trigger="EDÖB-Auskunftsbegehren nach Art. 24 DSG",
        statute="Art. 9 und 24 DSG",
        honorar_ansatz="CHF 400",
        honorar_total="CHF 9'100",
        vorschuss="CHF 4'000",
    ),
    "2022-088": Story(
        plot_id="weko-quorum",
        title="WEKO-Auskunftsbegehren Softwarelizenzen",
        hook="Die WEKO prüft, ob Quorum Soft AG den After-Sales-Markt für Lizenzen abgeschottet hat.",
        place="Sihlquai 5, Zürich",
        opposing="WEKO-Verfahren Dritte",
        streitwert="n/a",
        frist="2022-12-15",
        beilage="Lizenzverträge 2019–2022",
        trigger="Auskunftsbegehren der WEKO zu Exklusivbindungen",
        statute="Art. 7 KG",
        honorar_ansatz="CHF 480",
        honorar_total="CHF 14'800",
        vorschuss="CHF 7'500",
        outcome="Sistiert bis zum Abschluss des Parallelverfahrens",
    ),
    "2024-055": Story(
        plot_id="finma-forum",
        title="FINMA-Anzeige GwG-Verdacht",
        hook="Forum Revisions AG hat einen GwG-Verdacht bei einem Treuhandkunden gemeldet und braucht Begleitung.",
        place="Bahnhofstrasse 81, Zürich",
        opposing="FINMA / MROS",
        streitwert="n/a",
        frist="2024-07-01",
        beilage="Interne GwG-Akte Mandant X (anonymisiert)",
        trigger="Verdachtsmeldung nach Art. 9 GwG",
        statute="Art. 9 GwG und FINMA-RS 2016/7",
        honorar_ansatz="CHF 450",
        honorar_total="CHF 12'300",
        vorschuss="CHF 6'000",
    ),
    "2021-030": Story(
        plot_id="erbteilung-bachmann",
        title="Erbteilung Nachlass Bachmann",
        hook="Die Pflichtteilsberechnung im Nachlass Bachmann ist zwischen den drei Kindern strittig.",
        place="Uster, Einfamilienhaus Im Aabach",
        opposing="Miterben Bachmann",
        streitwert="CHF 410'000",
        frist="2021-11-01",
        beilage="Testament 2014 und Inventar",
        trigger="Uneinigkeit über die Anrechnung lebzeitiger Zuwendungen",
        statute="Art. 470 und 527 ZGB",
        honorar_ansatz="CHF 390",
        honorar_total="CHF 7'900",
        vorschuss="CHF 4'000",
        outcome="Teilungsvertrag unterzeichnet",
    ),
}

FILLERS: dict[str, list[Story]] = {
    "Bau": [
        Story("bau-dach", "Dachundichtigkeit Werkhof", "Das Flachdach des Werkhofs ist seit dem Winter undicht; der GU weist die Mängelrüge zurück.", "Dorfstrasse 2, Hinwil", "Gerüstbau Suter", "CHF 67'200", "2025-03-01", "GU-Vertrag 2021", "Wassereintritt Lagerhalle", "Art. 368 OR", "CHF 380", "CHF 6'400", "CHF 3'000"),
        Story("bau-kran", "Kranstillstand Industriequartier", "Der Kran stand drei Wochen, weil die Gegenpartei die Baustellenlogistik nicht koordinierte.", "Industriestrasse 18, Dübendorf", "Logistik Zollinger", "CHF 91'000", "2024-09-01", "Nachunternehmervertrag", "Stillstandskosten Kran", "Art. 97 OR", "CHF 400", "CHF 7'200", "CHF 3'500"),
        Story("bau-beton", "Betonfehler Turnhalle", "Die Bodenplatte der Turnhalle weist Risse über der Toleranz auf.", "Schulhausplatz 1, Wetzikon", "Betonwerk Töss", "CHF 112'400", "2025-01-15", "Werkvertrag Bodenplatte", "Rissbildung nach 8 Monaten", "Art. 368 OR", "CHF 410", "CHF 8'100", "CHF 4'000"),
    ],
    "IT": [
        Story("it-saas", "SaaS-Kündigung Wartung", "Der Anbieter stellte die Wartung ein, obwohl die Laufzeit noch 14 Monate betrug.", "Technopark Zürich", "Nimbus Hosting GmbH", "CHF 28'800", "2025-05-01", "SaaS-Vertrag 2022", "Einstellung des Support ohne Nachfrist", "Art. 97 OR", "CHF 360", "CHF 5'400", "CHF 2'500"),
        Story("it-lizenz", "Open-Source-Lizenzstreit", "In der ausgelieferten Software steckt eine Copyleft-Komponente ohne Hinweis.", "Sihlquai 55, Zürich", "Forge Apps AG", "CHF 55'000", "2024-12-01", "Entwicklungvertrag", "GPL-Komponente im Kundenprodukt", "UrhG und OR 97", "CHF 390", "CHF 6'800", "CHF 3'000"),
    ],
    "Gastro": [
        Story("gastro-liefer", "Lieferstopp Getränke", "Die Brauerei stoppte die Lieferung nach einem Streit um Pfandgebinde.", "Seestrasse 40, Wädenswil", "Seebräu AG", "CHF 19'400", "2024-08-15", "Rahmenliefervertrag", "Lieferstopp ohne Abmahnung", "Art. 107 OR", "CHF 340", "CHF 4'200", "CHF 2'000"),
        Story("gastro-pacht", "Pacht Inventar", "Beim Pachtende fehlt Inventar im Wert der Kaution.", "Niederdorfstrasse 7, Zürich", "Pächterkollektiv", "CHF 22'000", "2025-02-01", "Pachtvertrag", "fehlende Küchengeräte", "Art. 299 OR", "CHF 350", "CHF 4'800", "CHF 2'200"),
    ],
    "Immobilien": [
        Story("immo-stockwerk", "Stockwerkeigentum Heizung", "Die Erneuerung der Heizung wird von zwei Stockwerkeigentümern blockiert.", "Höschgasse 3, Zürich", "Stockwerkeigentümer Meier", "CHF 80'000", "2025-04-01", "Begründungsakt und Reglement", "Blockade der Heizungserneuerung", "Art. 647c ZGB", "CHF 400", "CHF 6'100", "CHF 3'000"),
        Story("immo-makler", "Maklerprovision", "Die Käuferin weigert sich, die vereinbarte Provision nach Handänderung zu zahlen.", "Weinbergstrasse 20, Zürich", "Käuferin Landolt", "CHF 41'250", "2024-10-01", "Maklerauftrag", "Provision nach beurkundetem Kauf", "Art. 412 OR", "CHF 380", "CHF 5'900", "CHF 2'800"),
    ],
    "Treuhand": [
        Story("treu-abschluss", "Streit Jahresabschluss", "Der Mitgesellschafter anerkennt den Jahresabschluss nicht und blockiert die Dividende.", "Bankstrasse 4, Uster", "Mitgesellschafter Vogt", "CHF 63'000", "2024-11-15", "Gesellschaftervertrag", "Verweigerung der Genehmigung", "Art. 804 OR", "CHF 370", "CHF 5'500", "CHF 2'500"),
        Story("treu-mandat", "Auskunft Mandatsende", "Nach Mandatsende verlangt die Nachfolgerin die vollständige Urkundenedition.", "Oberlandstrasse 11, Uster", "Nachfolgetreuhand", "CHF 12'000", "2025-03-20", "Treuhandvertrag", "Streit um Edition der Arbeitspapiere", "Art. 400 OR", "CHF 360", "CHF 3'800", "CHF 1'800"),
    ],
    "Pharma": [
        Story("pharma-liefer", "Charge zurückgerufen", "Eine Charge Wirkstoff wurde zurückgerufen; der Abnehmer fordert Deckungskauf.", "Werk Schlieren", "MediSource BV", "CHF 175'000", "2025-06-01", "Liefervertrag GMP", "Rückruf Charge 24-B", "Art. 197 OR", "CHF 430", "CHF 9'400", "CHF 5'000"),
    ],
}


def _index(key: str, n: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % n


def story_for(*, aktenzeichen: str, practice_area: str, seed: int, client_name: str) -> Story:
    if aktenzeichen in NAMED:
        return NAMED[aktenzeichen]
    sector = practice_area if practice_area in FILLERS else (
        "Bau" if "bau" in practice_area.casefold() else
        "IT" if practice_area.casefold() in {"it", "informatik"} else
        "Gastro" if "gastro" in practice_area.casefold() else
        "Immobilien" if "immobil" in practice_area.casefold() else
        "Treuhand" if "treuhand" in practice_area.casefold() else
        "Pharma" if "pharma" in practice_area.casefold() else
        "IT"
    )
    bank = FILLERS.get(sector) or FILLERS["IT"]
    chosen = bank[_index(f"{seed}:{aktenzeichen}:{client_name}", len(bank))]
    return replace(chosen)
