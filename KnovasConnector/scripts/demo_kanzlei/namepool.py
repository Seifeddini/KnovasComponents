"""Names, places and topics the firm's records are drawn from.

People carry common Swiss names: a list of invented people cannot avoid them.
Companies are different. A company name that exists in the commercial register
would look like a real client, so every company is built from an invented stem
(two nature words joined, in the manner of the firm's own name) and the full
list is screened against Zefix (``cli.py screen``).
"""
from __future__ import annotations

FIRST_NAMES_F = [
    "Anna", "Barbara", "Brigitte", "Carla", "Chiara", "Claudia", "Corinne", "Daniela", "Doris", "Elena",
    "Elisabeth", "Esther", "Eva", "Fabienne", "Franziska", "Gabriela", "Heidi", "Irene", "Jacqueline", "Jasmin",
    "Julia", "Karin", "Katharina", "Laura", "Lea", "Livia", "Manuela", "Margrit", "Maria", "Marianne",
    "Martina", "Melanie", "Michelle", "Monika", "Nadine", "Nicole", "Nina", "Patricia", "Petra", "Priska",
    "Rahel", "Regula", "Rita", "Ruth", "Sabine", "Sandra", "Sarah", "Silvia", "Simone", "Sonja",
    "Stefanie", "Susanne", "Tamara", "Tanja", "Ursula", "Vanessa", "Verena", "Vreni", "Yvonne", "Zoe",
]

FIRST_NAMES_M = [
    "Adrian", "Alexander", "Andreas", "Beat", "Benjamin", "Bruno", "Christian", "Christoph", "Daniel", "David",
    "Dominik", "Erich", "Fabian", "Felix", "Florian", "Franz", "Georg", "Hans", "Heinz", "Jan",
    "Jonas", "Jürg", "Kevin", "Kurt", "Lars", "Luca", "Lukas", "Manuel", "Marc", "Marcel",
    "Marco", "Markus", "Martin", "Matthias", "Michael", "Nico", "Oliver", "Pascal", "Patrick", "Paul",
    "Peter", "Philipp", "Raphael", "René", "Reto", "Roger", "Roland", "Rolf", "Samuel", "Sandro",
    "Simon", "Stefan", "Thomas", "Tobias", "Urs", "Walter", "Werner", "Willi", "Yves", "Zeno",
]

SURNAMES = [
    "Aebi", "Ammann", "Arnold", "Bachmann", "Baumann", "Baumgartner", "Berger", "Bieri", "Blaser", "Bolliger",
    "Brunner", "Bucher", "Bühler", "Burri", "Christen", "Egger", "Egli", "Eichenberger", "Fankhauser", "Fehr",
    "Fischer", "Flückiger", "Frei", "Frey", "Furrer", "Gasser", "Gerber", "Giger", "Graf", "Gut",
    "Haas", "Häfliger", "Hartmann", "Hauser", "Herzog", "Hess", "Hofer", "Hofmann", "Huber", "Hug",
    "Hunziker", "Imhof", "Jenni", "Jost", "Kaufmann", "Keller", "Kern", "Koch", "Kohler", "Koller",
    "Krebs", "Kuhn", "Küng", "Kunz", "Kurz", "Lehmann", "Leuenberger", "Lienhard", "Lüthi", "Maurer",
    "Meier", "Merz", "Meyer", "Moser", "Müller", "Näf", "Nussbaumer", "Odermatt", "Peter", "Pfister",
    "Portmann", "Rohner", "Roth", "Ruckstuhl", "Rüegg", "Schär", "Schaub", "Scherrer", "Schmid", "Schneider",
    "Schnyder", "Schuler", "Schwarz", "Siegenthaler", "Sigrist", "Stalder", "Staub", "Steiner", "Stocker", "Studer",
    "Suter", "Sutter", "Tanner", "Thommen", "Vogel", "Vogt", "Walser", "Weber", "Wenger", "Widmer",
    "Wirth", "Wüthrich", "Wyss", "Zaugg", "Zbinden", "Zehnder", "Ziegler", "Zimmermann", "Zingg", "Zürcher",
    "Bernasconi", "Bianchi", "Colombo", "Ferrari", "Lombardi", "Pedrazzini", "Rossi", "Favre", "Dubois", "Rochat",
    "Krasniqi", "Berisha", "Jovanovic", "Petrovic", "Da Silva", "Pereira", "Yilmaz", "Kaya", "Nguyen", "Tran",
]

#: (postcode, town) in the firm's catchment area.
TOWNS = [
    ("8001", "Zürich"), ("8002", "Zürich"), ("8004", "Zürich"), ("8005", "Zürich"), ("8006", "Zürich"),
    ("8008", "Zürich"), ("8032", "Zürich"), ("8037", "Zürich"), ("8045", "Zürich"), ("8048", "Zürich"),
    ("8050", "Zürich"), ("8057", "Zürich"), ("8400", "Winterthur"), ("8404", "Winterthur"), ("8406", "Winterthur"),
    ("8610", "Uster"), ("8620", "Wetzikon"), ("8600", "Dübendorf"), ("8952", "Schlieren"), ("8953", "Dietikon"),
    ("8820", "Wädenswil"), ("8810", "Horgen"), ("8800", "Thalwil"), ("8700", "Küsnacht"), ("8706", "Meilen"),
    ("8712", "Stäfa"), ("8302", "Kloten"), ("8180", "Bülach"), ("8105", "Regensdorf"), ("8134", "Adliswil"),
    ("8340", "Hinwil"), ("8630", "Rüti"), ("8640", "Rapperswil"), ("8200", "Schaffhausen"), ("8500", "Frauenfeld"),
    ("5400", "Baden"), ("5000", "Aarau"), ("6300", "Zug"), ("6340", "Baar"), ("9000", "St. Gallen"),
    ("6003", "Luzern"), ("3011", "Bern"), ("4051", "Basel"), ("8808", "Pfäffikon SZ"), ("8853", "Lachen"),
]

STREETS = [
    "Bahnhofstrasse", "Hauptstrasse", "Dorfstrasse", "Seestrasse", "Zürcherstrasse", "Kirchgasse", "Schulstrasse",
    "Industriestrasse", "Poststrasse", "Birkenweg", "Rosenweg", "Lindenstrasse", "Bergstrasse", "Wiesenstrasse",
    "Mühleweg", "Oberdorfstrasse", "Unterdorfstrasse", "Feldstrasse", "Gartenstrasse", "Sonnenhalde",
    "Im Grund", "Rebbergstrasse", "Talacker", "Am Bach", "Schützenstrasse", "Tödistrasse", "Albisstrasse",
    "Winterthurerstrasse", "Rämistrasse", "Limmatquai", "Badenerstrasse", "Hardstrasse", "Neugasse", "Alte Landstrasse",
]

#: Invented stems: any STEM_A joined with any STEM_B ("Firn" + "tobel").
STEM_A = [
    "Firn", "Quarz", "Torf", "Nebel", "Lärch", "Gletsch", "Moos", "Schiefer", "Ginster", "Flechten",
    "Arven", "Tuff", "Kiesel", "Wacholder", "Farn", "Bims", "Glimmer", "Mergel", "Schilf", "Dohlen",
    "Krähen", "Enzian", "Silberdistel", "Wollgras", "Sandstein", "Ried", "Eisvogel", "Murmel", "Gämsen", "Föhren",
]

STEM_B = [
    "tobel", "halde", "ried", "grat", "matt", "egg", "rain", "kamm", "moos", "bühl",
    "flue", "wang", "furt", "acker", "weid", "klus", "schwand", "loch", "boden", "horn",
]

#: (sector word in the name, practice areas such a client typically brings)
SECTORS = [
    ("Bau", "Bau-/Planungsrecht"), ("Holzbau", "Bau-/Planungsrecht"), ("Haustechnik", "Bau-/Planungsrecht"),
    ("Immobilien", "Mietrecht"), ("Verwaltungen", "Mietrecht"), ("Informatik", "IT-Recht"),
    ("Software", "IT-Recht"), ("Treuhand", "Gesellschaftsrecht"), ("Beteiligungen", "Gesellschaftsrecht"),
    ("Gastro", "Vertragsrecht"), ("Logistik", "Vertragsrecht"), ("Transporte", "Haftpflicht-/Versicherungsrecht"),
    ("Maschinenbau", "Vertragsrecht"), ("Elektro", "Bau-/Planungsrecht"), ("Medizintechnik", "Vertragsrecht"),
    ("Handel", "Vertragsrecht"), ("Gartenbau", "Bau-/Planungsrecht"), ("Metallbau", "Bau-/Planungsrecht"),
    ("Druck", "Arbeitsrecht"), ("Reinigungen", "Arbeitsrecht"), ("Personal", "Arbeitsrecht"),
    ("Architektur", "Bau-/Planungsrecht"), ("Energie", "Verwaltungsrecht"), ("Pharma", "Datenschutz"),
]

LEGAL_FORMS = ["AG", "AG", "AG", "GmbH", "GmbH", "Genossenschaft", "Stiftung"]

#: Practice areas with their weight in the firm's work and typical matter topics.
PRACTICE_AREAS: list[tuple[str, int, list[str]]] = [
    ("Arbeitsrecht", 16, ["Kündigung", "Arbeitszeugnis", "Lohnforderung", "Konkurrenzverbot", "Fristlose Entlassung", "Bonusanspruch", "Überstunden", "Aufhebungsvereinbarung"]),
    ("Mietrecht", 13, ["Mietzinserhöhung", "Kündigung Mietverhältnis", "Mängel der Mietsache", "Nebenkostenabrechnung", "Erstreckung", "Ausweisung", "Kaution"]),
    ("Bau-/Planungsrecht", 12, ["Werklohn", "Mängelrüge", "Bauhandwerkerpfandrecht", "Baueinsprache", "Honorar Architekt", "Bauverzögerung", "Nachbarrecht"]),
    ("Vertragsrecht", 14, ["Kaufpreisforderung", "Lieferverzug", "Gewährleistung", "Auftrag Honorar", "Darlehen", "Agenturvertrag", "Vertragsauflösung"]),
    ("Gesellschaftsrecht", 9, ["Gründung", "Aktionärbindungsvertrag", "Kapitalerhöhung", "Verantwortlichkeit", "Unternehmenskauf", "Generalversammlung", "Liquidation"]),
    ("Erb-/Familienrecht", 10, ["Erbteilung", "Testament", "Ehevertrag", "Scheidung", "Unterhalt", "Willensvollstreckung", "Vorsorgeauftrag"]),
    ("Haftpflicht-/Versicherungsrecht", 7, ["Verkehrsunfall", "Taggeld", "Invalidenrente", "Haftung Werkeigentümer", "Regress"]),
    ("SchKG", 7, ["Betreibung", "Rechtsöffnung", "Arrest", "Konkurseingabe", "Kollokation", "Nachlassstundung"]),
    ("Strafrecht", 3, ["Strafbefehl", "Privatklage", "Einvernahme", "Verkehrsdelikt"]),
    ("Verwaltungsrecht", 3, ["Baubewilligung", "Rekurs", "Submission", "Ausländerrecht"]),
    ("IT-Recht", 3, ["Softwarevertrag", "Lizenzstreit", "Projektabbruch", "SLA-Verletzung"]),
    ("Datenschutz", 2, ["Auskunftsbegehren", "Datenschutz-Folgenabschätzung", "Auftragsbearbeitung"]),
    ("Immaterialgüterrecht", 2, ["Markeneintragung", "Markenverletzung", "Urheberrecht"]),
    ("Steuerrecht", 1, ["Einsprache Veranlagung", "Grundstückgewinnsteuer", "Nachsteuer"]),
    ("Finanzmarkt", 1, ["GwG-Abklärung", "Bewilligung", "Anlegerklage"]),
    ("Kartellrecht", 1, ["Vertriebsvertrag", "Auskunftsbegehren"]),
]

#: Public bodies. These are real institutions; their addresses here are generic.
AUTHORITIES = [
    "Bezirksgericht Zürich", "Bezirksgericht Winterthur", "Bezirksgericht Uster", "Bezirksgericht Horgen",
    "Bezirksgericht Meilen", "Bezirksgericht Hinwil", "Bezirksgericht Dietikon", "Bezirksgericht Bülach",
    "Obergericht des Kantons Zürich", "Handelsgericht des Kantons Zürich", "Verwaltungsgericht des Kantons Zürich",
    "Sozialversicherungsgericht des Kantons Zürich", "Baurekursgericht des Kantons Zürich", "Bundesgericht",
    "Bundesverwaltungsgericht", "Arbeitsgericht Zürich", "Mietgericht Zürich",
    "Schlichtungsbehörde in Miet- und Pachtsachen Zürich", "Friedensrichteramt Zürich Kreise 1 und 2",
    "Friedensrichteramt Winterthur", "Friedensrichteramt Uster", "Betreibungsamt Zürich 1",
    "Betreibungsamt Winterthur-Stadt", "Konkursamt Zürich-Altstadt", "Staatsanwaltschaft Zürich-Limmat",
    "Staatsanwaltschaft Winterthur/Unterland", "Kantonales Steueramt Zürich", "Handelsregisteramt des Kantons Zürich",
    "Grundbuchamt Zürich-Altstadt", "Notariat Zürich-Altstadt", "Notariat Winterthur-Altstadt",
    "Eidgenössische Finanzmarktaufsicht FINMA", "Wettbewerbskommission WEKO",
    "Eidgenössischer Datenschutz- und Öffentlichkeitsbeauftragter EDÖB", "Meldestelle für Geldwäscherei MROS",
    "SVA Zürich", "Amt für Wirtschaft und Arbeit Zürich", "Migrationsamt des Kantons Zürich",
    "Eidgenössisches Institut für Geistiges Eigentum", "KESB Stadt Zürich",
]

#: What a lawyer writes in a time entry. ``{x}`` is a party, court or statute.
TIME_TEXTS = [
    "Telefon mit Mandant", "Telefon mit Gegenanwalt", "E-Mail an Mandant", "E-Mail von Gegenanwalt geprüft",
    "Studium Akten", "Studium Beilagen", "Rechtsabklärung", "Entwurf Schreiben an Gegenpartei",
    "Entwurf Rechtsschrift", "Überarbeitung Rechtsschrift", "Besprechung mit Mandant", "Besprechung intern",
    "Verhandlung inkl. Weg", "Vorbereitung Verhandlung", "Aktennotiz", "Vergleichsgespräch",
    "Durchsicht Entscheid", "Fristenkontrolle", "Korrespondenz Gericht", "Kostennote erstellt",
]

SECRETARY_TEXTS = ["Kopien und Versand", "Aktenanlage", "Terminkoordination", "Beilagenverzeichnis erstellt", "Schreibarbeiten"]

DEADLINE_KINDS = ["Gerichtsfrist", "Rechtsmittelfrist", "Verjährung", "Zahlungsfrist", "Wiedervorlage", "Vertragsfrist"]

#: Document kinds of the practice software (the ``Typ`` field of an AutoDoc name).
FILLER_DOC_TYPES = [
    "Brief-Mandant", "Brief-Gegenanwalt", "Brief-Gericht", "Aktennotiz", "Telefonnotiz", "Vollmacht",
    "Mandatsvereinbarung", "Eingabe", "Klage", "Klageantwort", "Vertrag", "Beilage", "Fax", "Honorarnote",
    "Kostenvorschuss", "Posteingang", "Verfuegung", "Entscheid", "Tabelle", "Foto", "Diktat", "Entwurf",
]
