# Demo-Korpus für Kanzlei-Demos

Baut ~8.800 lizenzsaubere Rechtsdokumente als Einzeldateien und legt sie so ab,
dass der Remote Controller sie als Watch-Root einliest.

## Drei getrennte Schritte

| Was | Werkzeug | Berührt Certs/Docker? |
|-----|----------|------------------------|
| **Dokumente herunterladen** | `fetch_demo_corpus.py build` | Nein |
| **Korpus prüfen** | `fetch_demo_corpus.py verify` | Nein |
| **Korpus auf anderen Server kopieren** | `fetch_demo_corpus.py upload` (rsync) | Nein |
| **Remote Controller starten** | `setup_server_corpus.sh` | Ja — mTLS, `.env`, Docker |

Nur Dokumente holen → `build`. Nicht `setup_server_corpus.sh` und nicht `upload`
(außer der Korpus wurde auf einer **anderen** Maschine gebaut und muss per rsync
übertragen werden).

## Dokumente herunterladen (nur Dateien)

Vom Monorepo-Root (`KnovasComponents/` bzw. `KnovasInternal/`):

```bash
# Ubuntu 24.04: venv nötig (PEP 668 blockiert system-weites pip)
sudo apt install python3.12-venv python3-full   # einmalig
python3 -m venv .venv-demo-corpus
source .venv-demo-corpus/bin/activate

pip install -r RemoteController/scripts/demo_corpus/requirements.txt

python RemoteController/scripts/demo_corpus/fetch_demo_corpus.py build --out corpus/
python RemoteController/scripts/demo_corpus/fetch_demo_corpus.py verify --out corpus/
```

`corpus/` ist in `.gitignore` — ~8.800 Dateien gehören nicht in dieses Repo.

Danach optional RC hochfahren (separater Schritt — Zertifikate, Docker):

```bash
cd RemoteController && bash scripts/setup_server_corpus.sh
```

## Korpus von anderer Maschine übertragen

Nur wenn `build` bereits auf Laptop/CI lief und `manifest.jsonl` existiert:

```bash
python RemoteController/scripts/demo_corpus/fetch_demo_corpus.py upload --out corpus/ \
  --host <rc-host> --user master --remote-path /home/master/KnovasInternal/corpus   # Probelauf
python RemoteController/scripts/demo_corpus/fetch_demo_corpus.py upload --out corpus/ \
  --host <rc-host> --user master --remote-path /home/master/KnovasInternal/corpus --execute
```

`upload` ohne `--execute` zeigt nur den rsync-Probelauf. Ohne `--host` gilt
`--remote-path` als lokaler Zielpfad (selten nötig).

## Zusammenstellung

| Slice | Inhalt | Dokumente | Lizenz |
|---|---|---:|---|
| `caselaw` | Schweizer Entscheide: BGer, BGE, BVGer, BStGer, EDÖB, WEKO, FINMA, ZH, GE, TI, VD | 5.300 | CC0-1.0 |
| `fedlex` | Bundesgesetzgebung, deutsche Fassung, inkl. 60 echter Versionsketten | ~1.740 | Bundes-OGD |
| `slds` | Leitentscheide mit amtlichem Regeste als Goldreferenz | 500 | CC BY 4.0 |
| `contracts` | CUAD 510 + MAUD 100 + ContractNLI 607 | 1.217 | CC BY 4.0 |

Bewusst **nicht** enthalten: Open Legal Data (ODbL — Share-alike greift beim
Ausliefern eines vorbefüllten Tenants), GDPRhub (Freiwilligen-Wiki ohne klare
Weiterverbreitungslizenz), Enron (echte private Korrespondenz nicht
einwilligender Personen).

## Verwendung mit dem Remote Controller

`docker-compose.corpus.yml` mountet `KnovasComponents/corpus/` nach
`/data/corpus`, und `setup_server_corpus.sh` setzt `RC_WATCH_ROOTS=/data/corpus`.

Einzelne Slices: `build --only caselaw,slds`. Ein abgebrochener Lauf lässt sich
erneut starten; vorhandene Dateien werden übersprungen.

## Ergebnis auf der Platte

```
corpus/
  manifest.jsonl          eine Zeile je Dokument: Pfad, Quelle, Quell-URL, Lizenz, sha256, Metadaten
  LICENSES.md             Lizenz und Namensnennung je Slice
  01_rechtsprechung/<gericht>/<decision_id>.txt + .meta.json
  02_gesetzgebung/SR-<nummer>/SR-<nummer>_<datum>.pdf
  03_leitentscheide/<id>.txt + <id>.regeste.txt
  04_vertraege/{cuad,maud,contractnli}/<vertrag>.txt
```

Entscheide und Verträge liegen als Text vor, Bundesgesetzgebung als Original-PDF
von Fedlex. Damit wird sowohl der reine Textpfad als auch die echte
Extraktionsstrecke über `knovas-extract` bedient.

Die Entscheid-Volltexte stammen aus den OpenCaseLaw-Parquets. Die PDFs einzeln
von den Gerichtsportalen zu ziehen hiesse tausende Requests gegen fremde Server;
die Parquets liefern denselben Text mitsamt `source_url` und `pdf_url` in
`.meta.json`, falls die Originale später doch gebraucht werden.

## Aufwand

Der Lauf lädt ~7 GB und schreibt 1–2 GB Korpus. Die Entscheide werden per
HTTP-Range direkt aus den Parquets gelesen, statt alle 7,8 GB Rohdaten zu holen.
Rechne mit 30–60 Minuten, je nach Anbindung.

## Konfiguration

`slices.toml` steuert Stückzahlen, Gerichte und die Fedlex-Auswahl. Die Liste
`priority_sr` bestimmt, welche Erlasse zuerst geholt werden — sie beginnt mit BV,
ZGB, OR, ZPO, StGB, StPO, DSG. `version_chain_acts` legt fest, für wie viele
Erlasse alle Fassungen statt nur der aktuellen geholt werden,
`max_versions_per_act` deckelt die Kettenlänge.

## Was beim Sampling zu beachten ist

Die Entscheide werden über die Row-Groups einer Gerichtsdatei verteilt gezogen,
nicht vom Anfang. Ohne das kämen bei BGer sämtliche Dokumente aus dem Jahr 2000
und der Datumsfilter der Demo liefe ins Leere. Ein Lauf mit 300 BGer-Entscheiden
verteilt sich über 1999–2026 bei DE/FR/IT im Verhältnis 55/37/8, was der
Verteilung des Gesamtkorpus entspricht.

Fedlex liefert unter „in Kraft" auch Fassungen mit künftigem
Anwendbarkeitsdatum. Das ist echtes Fedlex-Verhalten und kein Fehler, fällt aber
auf, wenn in der Demo ein Erlass mit Datum 2029 auftaucht.

## Rechtliches für die Vorführung

`LICENSES.md` im Korpus trägt die Namensnennung für CC-BY-Slices — eine
Credits-Zeile im Demo-Deck erfüllt die Auflage. Schweizer Entscheide sind von den
Gerichten bezüglich natürlicher Personen anonymisiert, Firmennamen bleiben
regelmässig sichtbar. In der Demo also „gerichtsanonymisiert" sagen, nicht
„vollständig anonym".
