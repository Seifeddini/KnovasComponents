"""Erzeugt fiktive Kanzlei-PDFs fuer die Wissensnetz-Demo (einmalig, committet).

Wichtig: Die quote-Felder der Fixture muessen woertlich im PDF stehen --
der Beleg-Klick zeigt die Seite, und der Satz muss dort auffindbar sein.

Story: Mandantin Mueller Bau AG, Dossier 2024-001, Werkvertrag Neubau Ost
gegen die Gegenpartei Immo Invest GmbH vor Bezirksgericht Zuerich; dazu
zwei weitere (schlanker dokumentierte) Mandate der Kanzlei als Kontext
fuer zusaetzliche Mandant-/Dossier-/Gegenpartei-/Gericht-Entitaeten.

Alle Namen, Firmen und Aktenzeichen sind frei erfunden.
"""
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parents[1].parent.parent / "docbridge_test_data" / "AutoDoc" / "wissensnetz"

DOCS = {
    "werkvertrag_neubau_ost.pdf": [
        ("Werkvertrag", [
            "Werkvertrag betreffend Neubau Ost",
            "zwischen der Müller Bau AG als Auftraggeberin",
            "und der Immo Invest GmbH als Bestellerin.",
            "Dokumenttyp: Werkvertrag",
        ]),
        ("Vertragsgegenstand", [
            "Die Parteien vereinbaren die Erstellung des Rohbaus",
            "gemäss Baubeschrieb vom 12. März 2024.",
        ]),
        ("Fristen", [
            "Die Kündigungsfrist beträgt drei Monate per Monatsende.",
            "Baubeginn ist der 1. Mai 2024.",
        ]),
        ("Nachtrag", [
            "Nachtrag Nr. 1 zum Werkvertrag vom 12. März 2024.",
            "Der Fertigstellungstermin wird auf den 30. November 2024 festgelegt.",
            "Dokumenttyp: Nachtrag",
        ]),
    ],
    "schreiben_gericht_2024_001.pdf": [
        ("Bezirksgericht Zürich", [
            "In Sachen Müller Bau AG gegen Immo Invest GmbH",
            "betreffend Forderung aus Werkvertrag",
            "wird die Frist zur Klageantwort auf den 30. September 2024 angesetzt.",
            "Dokumenttyp: Gerichtsschreiben",
        ]),
        ("Obergericht des Kantons Zürich", [
            "In Sachen Sarah Vogel Architektur GmbH gegen Bauconsult Ost AG",
            "betreffend Dossier 2024-014",
            "Berufung gegen den Entscheid des Bezirksgerichts Winterthur.",
        ]),
    ],
    "mandatsvereinbarung_mueller.pdf": [
        ("Mandatsvereinbarung", [
            "Die Müller Bau AG erteilt der Kanzlei das Mandat",
            "zur Vertretung im Dossier 2024-001.",
            "Dokumenttyp: Mandatsvereinbarung",
        ]),
        ("Honorar", [
            "Es gilt ein Stundenansatz von 350 Franken.",
            "Ein Kostenvorschuss von 5000 Franken ist bei Mandatsbeginn zu leisten.",
        ]),
        ("Weitere Mandate", [
            "Weitere Mandate der Kanzlei umfassen:",
            "Sarah Vogel Architektur GmbH, Dossier 2024-014.",
            "Bäckerei Hofer AG, Dossier 2024-022.",
        ]),
    ],
}

OUT.mkdir(parents=True, exist_ok=True)
for filename, pages in DOCS.items():
    doc = pymupdf.open()
    for heading, lines in pages:
        page = doc.new_page()
        page.insert_text((72, 96), heading, fontsize=16, fontname="hebo")
        for i, line in enumerate(lines):
            page.insert_text((72, 140 + 22 * i), line, fontsize=11, fontname="helv")
    doc.save(OUT / filename)
    print(f"geschrieben: {OUT / filename}")
