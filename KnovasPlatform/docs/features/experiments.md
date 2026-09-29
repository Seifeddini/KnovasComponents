# Experimente

Das Modul **Experimente** hält fest, was ein Team ausprobiert, woran es den
Erfolg misst, was dabei herauskam und was es daraus gelernt hat – für
Suchqualität und Technik ebenso wie für Marketing, Vertrieb und Produkt, und für
jeden Bereich, den jemand selbst anlegt. Hypothesen, Messwerte, Auswertungen,
Entscheidungen und Erkenntnisse liegen in der Plattform-Datenbank und werden
zusätzlich als Dokumente in Knovas indexiert: die Frage «Haben wir das schon
einmal versucht, und was kam heraus?» beantwortet die normale Knovas-Suche.

Das Modul ist **standardmässig ausgeschaltet** und bleibt auch eingeschaltet für
alle unsichtbar, die keine Experimente-Rolle haben: kein Menüpunkt, keine
Treffer in der Suche, jede Adresse des Moduls antwortet mit «Nicht gefunden».

- Design: [docs/superpowers/specs/2026-09-28-experiment-platform-design.md](../../../docs/superpowers/specs/2026-09-28-experiment-platform-design.md)
- Umsetzungsplan (Schnittstellen, Datenmodell): [docs/superpowers/plans/2026-09-28-experiments-module.md](../../../docs/superpowers/plans/2026-09-28-experiments-module.md)
- Python-Client: [experiments-sdk/python](../../experiments-sdk/python/README.md) · Julia-Client: [experiments-sdk/julia](../../experiments-sdk/julia/README.md)

## Inhalt

1. [Begriffe](#begriffe)
2. [Einschalten, Schritt für Schritt](#einschalten-schritt-für-schritt)
3. [Rollen und Sichtbarkeit](#rollen-und-sichtbarkeit)
4. [Die Knovas-Zugriffsgruppe](#die-knovas-zugriffsgruppe)
5. [Python und Julia: das Profil «experiments»](#python-und-julia-das-profil-experiments)
6. [Bereiche, Typen, Metriken, Auswerter](#bereiche-typen-metriken-auswerter)
7. [Messarten](#messarten)
8. [Echte Fälle erfassen](#echte-fälle-erfassen)
9. [CSV-Formate](#csv-formate)
10. [Auswertungen und Scope](#auswertungen-und-scope)
11. [Entscheidungen und Erkenntnisse](#entscheidungen-und-erkenntnisse)
12. [Suchen mit Knovas](#suchen-mit-knovas)
13. [Zugangsschlüssel, API und SDK](#zugangsschlüssel-api-und-sdk)
14. [Betrieb](#betrieb)
15. [Sicherheitsmodell der Rechenumgebung](#sicherheitsmodell-der-rechenumgebung)
16. [Grenzen](#grenzen)
17. [Wenn etwas nicht geht](#wenn-etwas-nicht-geht)

## Begriffe

| Begriff | Bedeutung |
|---|---|
| **Bereich** | Ein Arbeitsgebiet mit eigenem Kürzel, z. B. Engineering (`ENG`), Marketing (`MKT`), Vertrieb (`SAL`), Produkt (`PRD`) – oder ein selbst angelegter. Experimente heissen nach dem Kürzel: `MKT-58`. |
| **Typ** | Die Vorlage eines Experiments: Angaben (Felder), Status und erlaubte Wechsel, Varianten, Metriken, Auswertungen. Versioniert; ein Experiment bleibt bei der Version, mit der es angelegt wurde. |
| **Experiment** | Eine Hypothese mit Titel, Angaben, Varianten, zugeordneten Metriken, Status und Verlauf. |
| **Variante** | Ein Arm des Experiments, z. B. `A` (Kontrolle) und `B`. Ein Typ kann auch ohne Varianten auskommen (Kampagne, Nutzertest). |
| **Metrik** | Eine wiederverwendbare Messgrösse mit Messart, Einheit und Richtung («höher ist besser»). |
| **Messwert** | Eine Zeile: Metrik, Variante, Wert und je nach Messart Anzahl, Nenner oder Quadratsumme, Zeitpunkt und Merkmale (`dims`). |
| **Lauf** | Eine Durchführung, die Messwerte erzeugt: ein CI-Benchmark, eine Kampagnenwoche. Mit Parametern, Commit und Umgebung. |
| **Auswerter** | Eine Rechnung über die Messwerte: eingebaut (in der Plattform) oder eigener Python- bzw. Julia-Code (in der Rechenumgebung). |
| **Auswertung** | Ein Ergebnis eines Auswerters: Urteil (besser, schlechter, offen), Kopfzeile, Vergleiche mit Intervallen, Warnungen. |
| **Entscheidung** | Übernehmen, Weiterentwickeln, Verwerfen oder Ohne klares Ergebnis – mit Begründung und **Erkenntnis**. |
| **Notiz** | Freier Text: Notiz, Beobachtung, Interview, Rückmeldung; Statuswechsel mit Grund werden ebenfalls als Notiz festgehalten. |
| **Paket** | Bereich, Metriken, Typen und Auswerter als eine YAML-Datei – mitgeliefert oder selbst exportiert. |

## Einschalten, Schritt für Schritt

### Voraussetzungen

- **Persönliche Konten** (`IDENTITY_ENABLED=true`, die Vorgabe). Mit dem
  gemeinsamen Firmenpasswort bleibt das Modul aus, was immer
  `EXPERIMENTS_ENABLED` sagt; das Protokoll von `docbridge-web` nennt den Grund.
- Die Plattform-Datenbank (`platform-db`) läuft ohnehin mit. Die Tabellen des
  Moduls legt die Migration `0003_experiments.sql` beim Start an – auch bei
  ausgeschaltetem Modul, damit das spätere Einschalten keinen eigenen Schritt
  braucht.

### Schritte

1. **Zugriffsgruppe anlegen.** Unter **Verwaltung → Zugriffsgruppen → Gruppe
   anlegen** eine Gruppe für Experimente anlegen, z. B. `experimente`. Warum:
   [Die Knovas-Zugriffsgruppe](#die-knovas-zugriffsgruppe).
2. **`knovas.env` ergänzen:**

   ```bash
   EXPERIMENTS_ENABLED=true
   EXPERIMENTS_ACCESS_GROUPS=experimente
   # nur für Python- und Julia-Auswerter (siehe unten):
   # COMPOSE_PROFILES=experiments
   # EXPERIMENTS_RUNNER_URL=unix:///run/experiments-runner/runner.sock
   ```

3. **Übernehmen und starten:**

   ```bash
   ./scripts/setup.sh && ./scripts/start.sh
   ./scripts/doctor.sh        # Abschnitt «Experimente»
   ```

   `setup.sh` reicht jede `EXPERIMENTS_*`-Zeile aus `knovas.env` an die
   Plattform weiter.
4. **Rollen vergeben.** Unter **Verwaltung → Personen → Verwalten → Rollen**
   `experimenter` für alle, die mit Experimenten arbeiten, und
   `experiments_manager` für die, die Bereiche, Typen, Metriken und Auswerter
   pflegen. Siehe [Rollen und Sichtbarkeit](#rollen-und-sichtbarkeit).
5. **Zugriffsgruppe geben.** Unter **Verwaltung → Personen → Verwalten →
   Zugriffsgruppen** jeder Person mit einer Experimente-Rolle die Gruppe
   `experimente` **zusätzlich** zu ihren bisherigen Gruppen eintragen. Das Feld
   ersetzt die ganze Liste: wer nur `experimente` hineinschreibt, nimmt der
   Person ihre anderen Gruppen weg.
6. **Pakete installieren.** Unter **Experimente → Verwaltung → Bereiche →
   Pakete** die gewünschten Pakete installieren (Engineering, Marketing,
   Vertrieb, Produkt) – oder auf der Kommandozeile:

   ```bash
   docker compose --env-file knovas.env exec docbridge-web \
     python -m experiments install-pack engineering --as max@firma.ch
   ```

   Das Grundpaket `core` (Typ «Allgemeine Hypothese», zwei Beispiel-Auswerter)
   ist immer da.
7. **Prüfen.** **Experimente → Verwaltung → Index** zeigt, ob jemandem die
   Zugriffsgruppe fehlt («Personen ohne Zugriffsgruppe») und ob die Rechenumgebung
   erreichbar ist. Ein erstes Experiment anlegen: **Experimente → Neues
   Experiment**. Nach etwa einer Minute steht es in Knovas («In Knovas:
   aktuell»).

### Einstellungen

Alle in `knovas.env`, danach `./scripts/setup.sh && ./scripts/start.sh`. Eine
gesetzte, aber leere Variable (`EXPERIMENTS_INDEX_ENABLED=`) gilt als «Vorgabe».

| Einstellung | Vorgabe | Wirkung |
|---|---|---|
| `EXPERIMENTS_ENABLED` | `false` | Schaltet das Modul ein. |
| `EXPERIMENTS_ACCESS_GROUPS` | leer | Knovas-Zugriffsgruppen jedes Experiment-Dokuments, durch Komma getrennt. Leer: es wird **nichts** in Knovas hochgeladen. |
| `EXPERIMENTS_INDEX_UNRESTRICTED` | `false` | Hochladen ohne Zugriffsgruppe erlauben – nur zusammen mit einer Ordnerregel auf `experiments/`, siehe unten. |
| `EXPERIMENTS_INDEX_ENABLED` | `true` | `false`: Experimente werden nicht mehr hochgeladen (Löschungen laufen weiter). |
| `EXPERIMENTS_INDEX_PER_MINUTE` | `2` | Uploads pro Minute (1–60), über alle Prozesse zusammen. Der Mandant erlaubt nur wenige Dokument-Uploads pro Minute und teilt sie mit RemoteController. |
| `EXPERIMENTS_INDEX_DEBOUNCE_SECONDS` | `60` | Änderungen innerhalb dieses Fensters werden einmal hochgeladen (0–3600). |
| `EXPERIMENTS_POINTER_PREFIX` | `experiments` | Erstes Pfadstück der Knovas-Dokumente (`experiments/<bereich>/<SCHLÜSSEL>`). Darf nicht mit `KNOVAS_IDENTIFIER_PREFIX` übereinstimmen. Nach einer Änderung: `purge-index` mit dem alten Wert, dann `reindex --all`. |
| `EXPERIMENTS_RUNNER_URL` | leer | Adresse der Rechenumgebung, mit dem Profil `unix:///run/experiments-runner/runner.sock`. Leer: nur eingebaute Auswerter. |
| `EXPERIMENTS_RUNNER_TIMEOUT` | `90` | Sekunden, die eine Python- oder Julia-Auswertung höchstens rechnet (5–3600). Gilt auch als Obergrenze in der Rechenumgebung. |
| `EXPERIMENTS_WORKER_ENABLED` | `true` | Hintergrundarbeit (Indexieren, Auswerten) in den Web-Prozessen. `false` nur, wenn sie anders läuft (`python -m experiments worker`). |
| `EXPERIMENTS_WORKER_POLL_SECONDS` | `5` | Wie oft ein untätiger Hintergrund-Thread nach Arbeit sieht. |
| `EXPERIMENTS_MAX_CSV_ROWS` | `200000` | Zeilen je CSV-Import (1000–1'000'000). |
| `COMPOSE_PROFILES` | – | `experiments` baut und startet die Rechenumgebung `experiments-runner`. |
| `EXPERIMENTS_RUNNER_MEMORY` | `3g` | Speichergrenze der Rechenumgebung. |
| `EXPERIMENTS_RUNNER_CPUS` | `2` | CPU-Grenze der Rechenumgebung. |
| `EXPERIMENTS_RUNNER_MAX_CONCURRENT` | `2` | Gleichzeitige Python-/Julia-Auswertungen. |

### Ausschalten und entfernen

- **`EXPERIMENTS_ENABLED=false`** (und `setup.sh` + `start.sh`): Menüpunkt weg,
  jede Seite des Moduls leitet auf die Suche um, jede API-Adresse antwortet
  404 «Experimente sind nicht eingeschaltet.», keine Hintergrundarbeit.
  Experiment-Treffer werden aus **jeder** Suchantwort entfernt, auch wenn die
  Dokumente noch in Knovas liegen. Die Daten bleiben in der Plattform-Datenbank;
  wieder einschalten stellt alles her.
- **Die Knovas-Kopien löschen** (geht auch bei ausgeschaltetem Modul):

  ```bash
  docker compose --env-file knovas.env exec docbridge-web python -m experiments purge-index        # zeigt, was gelöscht würde
  docker compose --env-file knovas.env exec docbridge-web python -m experiments purge-index --yes
  ```

  Bei eingeschaltetem Modul vorher `EXPERIMENTS_INDEX_ENABLED=false` setzen,
  sonst kann ein gerade laufender Auftrag ein Dokument wieder hochladen.

## Rollen und Sichtbarkeit

| Rolle | Sieht | Darf |
|---|---|---|
| ohne Experimente-Rolle | nichts: kein Menüpunkt, keine Treffer, 404 auf jeder Adresse | – |
| `experimenter` (Anzeige «Experimente») | alle Experimente aller Bereiche | Experimente anlegen und bearbeiten, Varianten und Metriken zuordnen, Messwerte, CSV, Läufe, Notizen, Auswertungen starten, Status wechseln, entscheiden, eigene Zugangsschlüssel |
| `experiments_manager` (Anzeige «Experimente verwalten») | dasselbe | zusätzlich: Bereiche, Typen, Metriken, Auswerter und Pakete pflegen, Experimente löschen, Index betreiben, Experimente global aus der normalen Suche nehmen |
| `admin` | dasselbe | alles, was `experiments_manager` darf |

- Es gibt **keine Rechte je Experiment oder je Bereich**. Wer eine
  Experimente-Rolle hat, sieht alles im Modul. Schreiben Sie deshalb keine
  Mandatsdaten, Namen von Kundinnen oder Interviewpartnern oder andere
  vertrauliche Angaben in Experimente.
- Ein Typ kann einzelne Statuswechsel auf Rollen beschränken (z. B.
  «Freigeben» nur für `experiments_manager`, siehe Beispiel unten).
- Notizen löschen die Verfasserin oder der Verfasser und die Verantwortlichen.
- Jede Änderung steht im Audit-Protokoll und im Abschnitt **Aktivität** des
  Experiments – mit Person und Zeitpunkt, ohne Inhalte.

## Die Knovas-Zugriffsgruppe

Jedes Experiment wird als ein Dokument in Knovas geschrieben. Knovas muss
wissen, wer es sehen darf – sonst stünden Hypothesen, Zahlen und Erkenntnisse
jedem offen, der den Mandanten durchsucht. Deshalb:

- Jedes Dokument trägt die Gruppen aus `EXPERIMENTS_ACCESS_GROUPS`.
- **Ohne Gruppe wird nichts hochgeladen** (Grundsatz «im Zweifel geschlossen»).
  Das Experiment zeigt dann «In Knovas: Fehler: Für Experimente ist keine
  Knovas-Zugriffsgruppe festgelegt (EXPERIMENTS_ACCESS_GROUPS).», und
  `doctor.sh` warnt.
- **Jede Person mit einer Experimente-Rolle braucht die Gruppe** (Schritt 5
  oben). Wem sie fehlt, dem liefert Knovas die Experiment-Dokumente nicht; die
  Suche im Modul fällt dann auf die Datenbanksuche zurück und sagt das
  («Ihnen fehlt die Knovas-Zugriffsgruppe für Experimente; gezeigt werden
  Datenbanktreffer.»). **Experimente → Verwaltung → Index** listet unter
  «Personen ohne Zugriffsgruppe» alle, denen eine Gruppe fehlt.

**Alternative ohne Gruppe am Dokument:** eine Ordnerregel unter **Verwaltung →
Zugriffsgruppen → Ordnerregeln** auf den Ordner `experiments/` (bzw. den Wert
von `EXPERIMENTS_POINTER_PREFIX` mit `/`) und `EXPERIMENTS_INDEX_UNRESTRICTED=true`.
Dann regelt die Ordnerregel die Sichtbarkeit. Ohne diese Regel wären die
Dokumente für alle Personen des Mandanten sichtbar – `doctor.sh` warnt in
diesem Fall («Experimente sind in Knovas für alle Nutzer des Mandanten
sichtbar»).

**Zwei Schutzschichten.** Die Plattform entfernt Experiment-Treffer aus jeder
Suchantwort und gibt sie nur Personen mit Experimente-Rolle zurück – unabhängig
davon, was Knovas liefert. Die Zugriffsgruppe schützt zusätzlich gegen jeden
anderen Weg in den Mandanten. Ob Knovas die Gruppen bereits durchsetzt, hängt
vom Stand der Backend-Anbindung ab; siehe «What is not enforced yet» in
[document-administration.md](document-administration.md#what-is-not-enforced-yet).
Experiment-Dokumente werden nie als Datei ausgeliefert: Vorschau und «Öffnen»
lehnen jeden Zeiger unter `experiments/` ab.

## Python und Julia: das Profil «experiments»

Die eingebauten Auswerter (Tabelle unten) rechnen in der Plattform selbst und
brauchen nichts weiter. Für **eigene Auswerter in Python oder Julia** gibt es
den Dienst `experiments-runner`, eine abgeschottete Rechenumgebung:

```bash
# knovas.env
COMPOSE_PROFILES=experiments
EXPERIMENTS_RUNNER_URL=unix:///run/experiments-runner/runner.sock
```

```bash
./scripts/setup.sh && ./scripts/start.sh
```

Der erste Bau dauert einige Minuten (die Julia-Pakete werden vorkompiliert).
Danach zeigt **Experimente → Verwaltung → Auswerter** «Rechenumgebung:
erreichbar» mit den Sprachversionen; `doctor.sh` prüft die Verbindung
ebenfalls.

| Sprache | Version | Pakete |
|---|---|---|
| Python | 3.11 | numpy, scipy, pandas, statsmodels (feste Versionen, siehe `KnovasPlatform/components/experiments_runner/requirements.txt`) und die Standardbibliothek |
| Julia | 1.11 | JSON3, Distributions, HypothesisTests, StatsBase, DataFrames und die Standardbibliothek (Random, Statistics, LinearAlgebra …) |

Die Rechenumgebung hat **kein Netzwerk**. Pakete lassen sich zur Laufzeit nicht
nachladen; wer ein weiteres braucht, ergänzt das Image
(`KnovasPlatform/components/experiments_runner/`). Wie sie abgeschottet ist,
steht unter [Sicherheitsmodell](#sicherheitsmodell-der-rechenumgebung).

Ohne das Profil bleiben Python- und Julia-Auswerter in der Oberfläche
ausgegraut («Python- und Julia-Auswerter brauchen die Rechenumgebung (Profil
experiments).»); Typen, die solche Auswerter vorsehen, überspringen sie mit
einer Warnung.

## Bereiche, Typen, Metriken, Auswerter

Alles unter **Experimente → Verwaltung** (Reiter Bereiche, Typen, Metriken,
Auswerter; nur `experiments_manager` und `admin`). Alles ist Konfiguration,
keine Programmierung: neue Bereiche und Typen brauchen kein neues Release.

### Bereiche und Pakete

| Paket | Bereich | Kürzel | Typen |
|---|---|---|---|
| `core` | (global) | – | Allgemeine Hypothese; Beispiel-Auswerter in Python und Julia |
| `engineering` | Engineering | `ENG` | Offline-Evaluation, Performance-Änderung, Feature-Rollout |
| `marketing` | Marketing | `MKT` | A/B-Test, Kampagne, Content-Test |
| `sales` | Vertrieb | `SAL` | Playbook-Test, Preis-Test |
| `product` | Produkt | `PRD` | Nutzertest, Feature-Rollout |

Ein **eigener Bereich** entsteht unter **Bereiche → Neuer Bereich** mit
Schlüssel (`^[a-z][a-z0-9-]{1,31}$`), Name, Kürzel (2–8 Grossbuchstaben oder
Ziffern, beginnt mit einem Buchstaben) und Farbe. Er startet mit dem Typ
«Allgemeine Hypothese»; eigene Typen und Metriken kommen dazu. **Exportieren**
lädt den Bereich mit seinen Metriken, Typen und den benutzten eigenen
Auswertern als YAML-Paket herunter, **Importieren** liest ein solches Paket
wieder ein – so wandert eine Konfiguration zwischen Installationen oder in ein
Git-Repository. Ein erneutes Installieren eines Pakets legt nur an, was fehlt;
Bestehendes bleibt unverändert.

Ein vollständiges Paket für einen neuen Bereich «Veranstaltungen»:

```yaml
pack: events
title: Veranstaltungen
description: Webinare und Anlässe für Kanzleien und Rechtsabteilungen.
version: 1

domain:
  key: events
  name: Veranstaltungen
  id_prefix: EVT
  color: "#8a5a00"
  description: Einladungen, Programme und Formate von Webinaren und Anlässen.

metrics:
  - key: open_rate
    name: Öffnungsrate
    kind: proportion
    unit: "%"
    direction: higher
    description: Wert = geöffnete E-Mails, Anzahl = zugestellte E-Mails.
    definition: {decimals: 1}
  - key: registration_rate
    name: Anmeldequote
    kind: proportion
    unit: "%"
    direction: higher
    description: Wert = Anmeldungen, Anzahl = zugestellte E-Mails.
    definition: {decimals: 2}
  - key: unsubscribe_rate
    name: Abmelderate
    kind: proportion
    unit: "%"
    direction: lower
    description: Wert = Abmeldungen, Anzahl = zugestellte E-Mails.
    definition: {decimals: 2}
  - key: rating
    name: Bewertung
    kind: ordinal
    unit: ""
    direction: higher
    description: Wert = Stufe, Anzahl = Personen mit dieser Stufe.
    definition:
      levels: {"1": schlecht, "2": mässig, "3": gut, "4": sehr gut}

types:
  - key: webinar_invite
    name: Webinar-Einladung
    description: Zwei oder mehr Einladungen (Betreff, Text) an gleich grosse Teile der Liste.
    definition:
      fields:
        - {key: webinar_date, label: Webinar-Datum, type: date, required: true}
        - {key: list_size, label: Empfänger je Variante, type: integer, min: 1,
           help: "Geplante Stichprobe; «Zur Auswertung» wartet, bis sie erreicht ist."}
        - {key: tool, label: Versandwerkzeug, type: enum, options: [Mailchimp, HubSpot, Outlook]}
        - {key: landing_page, label: Anmeldeseite, type: url}
      states:
        - {key: draft, label: Entwurf}
        - {key: review, label: Freigabe}
        - {key: running, label: Läuft, phase: running}
        - {key: analysis, label: Auswertung}
        - {key: decided, label: Entschieden, phase: decided}
        - {key: stopped, label: Abgebrochen, phase: stopped}
      initial: draft
      transitions:
        - {from: draft, to: review, label: Zur Freigabe,
           requires: [hypothesis, primary_metric, "variants:2", "field:webinar_date"]}
        - {from: review, to: running, label: Freigeben, roles: [experiments_manager]}
        - {from: review, to: draft, label: Zurück an Entwurf}
        - {from: running, to: analysis, label: Zur Auswertung,
           requires: [measurements, "n_planned:list_size"]}
        - {from: analysis, to: decided, label: Entscheiden, requires: [evaluation, decision]}
        - {from: analysis, to: running, label: Weiterlaufen lassen}
        - {from: "*", to: stopped, label: Abbrechen}
      variants:
        min: 2
        max: 4
        defaults:
          - {key: A, name: Bisheriger Betreff, is_control: true}
          - {key: B, name: Neuer Betreff}
      metrics:
        - {metric: open_rate, role: primary}
        - {metric: registration_rate, role: secondary}
        - {metric: rating, role: secondary}
        - {metric: unsubscribe_rate, role: guardrail, op: max, value: 0.01}
      evaluation:
        - {evaluator: builtin.describe, metric: all}
        - {evaluator: builtin.bayes_proportion, metric: primary, params: {threshold: 0.95}}
        - {evaluator: builtin.two_proportion, metric: registration_rate, params: {alpha: 0.05}}
        - {evaluator: builtin.chi_square, metric: rating}
      decision: {require_learning: true}

evaluators: []
requires_metrics: []
```

YAML und JSON werden sicher gelesen: Anker und Verweise (`&`, `*`) und
Python-Tags sind nicht erlaubt; ein Paket darf höchstens 2 MB, eine
Typdefinition höchstens 200 KB gross sein. In Flow-Schreibweise (`{...}`)
Texte mit Komma oder Doppelpunkt in Anführungszeichen setzen, wie beim
`help` oben.

### Typen

Unter **Typen** steht jeder Typ als YAML- oder JSON-Text. **Prüfen** meldet
Fehler je Feld; **Als neue Version speichern** legt eine neue Version an
(laufende Experimente behalten ihre); **Kopieren nach …** übernimmt einen Typ in
einen anderen Bereich. Eine Typdefinition hat diese Teile:

**`fields`** – Angaben des Experiments (höchstens 40). `key`
(`^[a-z][a-z0-9_]{0,39}$`), `label`, `type`, `required`, `help`:

| `type` | Wert |
|---|---|
| `text` | bis 500 Zeichen |
| `longtext` | bis 20'000 Zeichen |
| `number`, `integer` | Zahl; optional `min`, `max` |
| `enum` | eine aus `options` (1–50 Einträge) |
| `multi_enum` | mehrere aus `options` |
| `date` | Datum (`JJJJ-MM-TT`) |
| `url` | http- oder https-Adresse |
| `boolean` | ja/nein |

**`states`** – 2 bis 12 Status. Höchstens ein Status je `phase`: `running`
(das erste Betreten setzt «gestartet am»), `decided` (setzt «entschieden am»
und «beendet am»; dorthin führt nur das Entscheidungsformular), `stopped`
(setzt «beendet am»; verlangt einen Grund, der als Notiz gespeichert wird).
**`initial`** ist der Anfangsstatus.

**`transitions`** – erlaubte Wechsel mit Knopfbeschriftung. `from: "*"` heisst
«aus jedem Status». `roles` beschränkt einen Wechsel auf `experimenter` oder
`experiments_manager` (`admin` darf immer). `requires` nennt Bedingungen; die
Oberfläche zeigt einen gesperrten Knopf mit den fehlenden Punkten:

| Bedingung | Erfüllt, wenn | Meldung, wenn nicht |
|---|---|---|
| `hypothesis` | eine Hypothese dasteht | Die Hypothese fehlt. |
| `primary_metric` | eine primäre Metrik zugeordnet ist | Es ist keine primäre Metrik festgelegt. |
| `variants:N` | mindestens N Varianten bestehen | Es braucht mindestens N Varianten. |
| `measurements` | es Messwerte gibt | Es gibt noch keine Messwerte. |
| `evaluation` | eine Auswertung abgeschlossen ist | Es gibt noch keine abgeschlossene Auswertung. |
| `decision` | eine Entscheidung festgehalten ist | Es ist noch keine Entscheidung festgehalten. |
| `learning` | eine Erkenntnis festgehalten ist | Die Erkenntnis fehlt. |
| `field:<key>` | das Feld ausgefüllt ist | Das Feld «…» ist leer. |
| `n_planned:<key>` | jede Variante in der primären Metrik mindestens so viele Einheiten hat, wie das Zahlenfeld `<key>` nennt | Die geplante Stichprobe ist noch nicht erreicht (… von … je Variante). |

**`variants`** – `min`, `max` und die vorgeschlagenen `defaults` (Schlüssel,
Name, höchstens eine Kontrolle, optional Anteil `allocation` 0–1).

**`metrics`** – vorgeschlagene Metriken mit Rolle `primary` (höchstens eine),
`secondary` oder `guardrail` (Leitplanke mit `op: max` oder `min` und `value`
in den natürlichen Einheiten der Metrik, also `0.01` für 1 %). Metriken werden
zuerst im Bereich, dann global gesucht.

**`evaluation`** – die Auswertungen, die «Alle Auswertungen des Typs
ausführen» und die automatische Auswertung nach neuen Daten starten: je
`evaluator`, `metric` (`primary`, `all` = jede passende zugeordnete Metrik,
oder ein Metrik-Schlüssel), `params` und optional `scope`.

**`decision.require_learning`** – ob eine Entscheidung eine Erkenntnis braucht.

### Metriken

| Feld | Bedeutung |
|---|---|
| `key` | `^[a-z][a-z0-9_]{1,47}$`, eindeutig im Bereich (oder global) |
| `name` | Anzeigename |
| `kind` | Messart, siehe [Messarten](#messarten). Lässt sich nicht mehr ändern, sobald es Messwerte gibt. |
| `unit` | Einheit (`CHF`, `ms`, `Punkte` …); für Anteile `%` |
| `direction` | `higher` (höher ist besser), `lower` (tiefer ist besser), `none` (ohne Richtung – Auswertungen nennen Zahlen, aber kein Urteil) |
| `definition.decimals` | Nachkommastellen in der Anzeige (0–6) |
| `definition.min`, `max` | erlaubter Wertebereich (Mittelwert, Dauer, Geldbetrag, Skala), z. B. `0` und `100` für SUS |
| `definition.levels` | Stufen oder Kategorien mit Beschriftung (2–50); Pflicht für `categorical`, möglich für `ordinal` |

```yaml
- {key: sus_score, name: SUS-Wert, kind: mean, unit: Punkte, direction: higher,
   description: "Ein Wert je Person (0–100).", definition: {decimals: 1, min: 0, max: 100}}
- key: satisfaction
  name: Zufriedenheit
  kind: ordinal
  direction: higher
  definition:
    levels: {"1": sehr unzufrieden, "2": unzufrieden, "3": neutral, "4": zufrieden, "5": sehr zufrieden}
```

### Auswerter

**Eingebaut** (rechnen sofort in der Plattform, reines Python):

| Schlüssel | Name | Messarten | Parameter | Was er rechnet |
|---|---|---|---|---|
| `builtin.describe` | Beschreibung je Variante | alle | `target` | n, Schätzwert und 95 %-Intervall je Variante (Wilson für Anteile, t-Intervall für Mittelwerte, exakt für Raten); prüft Leitplanken; mit `target`: «besser», wenn das ganze Intervall auf der guten Seite des Ziels liegt |
| `builtin.two_proportion` | Zwei-Anteile-Test | Anteil | `alpha`, `correction` | Differenz in Prozentpunkten mit Intervall und z-Test |
| `builtin.bayes_proportion` | Bayes-Vergleich (Anteile) | Anteil | `threshold`, `prior_a`, `prior_b` | Wahrscheinlichkeit, dass eine Variante besser ist als die Kontrolle, erwarteter Verlust |
| `builtin.welch_t` | Welch-t-Test | Mittelwert, Dauer, Geldbetrag, Skala | `alpha`, `correction` | Mittelwert-Differenz mit Intervall; braucht Einzelwerte oder die Quadratsumme |
| `builtin.paired_t` | Gepaarter t-Test | Mittelwert, Dauer, Geldbetrag, Skala | `alpha`, `correction`, `pair_by` | Paare mit gleichem Merkmal (`dims.query`) in Kontrolle und Variante |
| `builtin.poisson_rate` | Raten-Vergleich | Rate | `alpha`, `correction` | Verhältnis der Ereignisraten mit exaktem Intervall |
| `builtin.ratio_delta` | Verhältnis-Vergleich | Verhältnis | `alpha`, `correction` | Differenz von Verhältnissen (Delta-Methode, jede Zeile eine Einheit) |
| `builtin.chi_square` | Chi-Quadrat-Test | Kategorie, Skala | `alpha`, `correction`, `expected` | Verteilungen zwischen Varianten; mit einer Gruppe gegen gleiche oder erwartete Anteile |

`alpha` 0,001–0,2 (Vorgabe 0,05); `correction` `holm` (Vorgabe) oder `none`;
`threshold` 0,5–0,999 (Vorgabe 0,95).

**Eigene Auswerter** (Python oder Julia) legen Verantwortliche unter
**Auswerter → Neuer Auswerter** an. Das Formular ist mit einer Vorlage
vorbelegt, die jeden Schlüssel des Ausgabevertrags setzt; die Beispiele
`example.bootstrap_mean_py` und `example.beta_binomial_jl` aus dem Grundpaket
sind ausführlich kommentierte Vorlagen. **Testen** führt den (auch noch
ungespeicherten) Code gegen ein Experiment und eine Metrik aus und zeigt
Ausgabe und Protokoll. Jede Änderung ist eine neue Version; eine Auswertung
merkt sich die Version, mit der sie gerechnet wurde.

Der Code definiert `evaluate(data)` und gibt ein Objekt zurück. `data`:

```json
{
  "experiment": {"key": "ENG-12", "title": "…", "hypothesis": "…", "domain": "engineering",
                 "type": "offline_eval", "status": "running", "fields": {}, "tags": []},
  "metric": {"key": "ndcg_at_10", "name": "NDCG@10", "kind": "mean", "unit": "",
             "direction": "higher", "role": "primary", "definition": {"decimals": 3},
             "guardrail": null},
  "variants": [{"key": "baseline", "name": "Ausgangsstand", "is_control": true},
               {"key": "candidate", "name": "Kandidat", "is_control": false}],
  "aggregates": [{"variant": "baseline", "rows": 500, "n": 500, "value_sum": 251.3,
                  "denominator_sum": null, "sum_sq": 140.2, "estimate": 0.5026, "levels": null}],
  "rows": [{"variant": "baseline", "run": "…", "value": 0.61, "count": 1, "denominator": null,
            "sum_sq": 0.3721, "observed_at": "2026-09-28T02:14:00+00:00", "dims": {"query": "q17"}}],
  "rows_truncated": false,
  "scope": {"runs": "latest"},
  "params": {"pair_by": "query"}
}
```

`rows` enthält höchstens 100'000 Zeilen (die neuesten; dann ist
`rows_truncated` wahr); `aggregates` sind immer vollständig. Rückgabe:

```json
{
  "verdict": "better | worse | inconclusive | n/a",
  "headline": "Eine Zeile, höchstens 200 Zeichen",
  "summary": "Markdown, höchstens 20'000 Zeichen – wird in Knovas indexiert",
  "comparisons": [{"variant": "candidate", "baseline": "baseline", "label": "Differenz",
                   "estimate": 0.021, "ci_low": 0.004, "ci_high": 0.038, "p_value": 0.014,
                   "prob_better": null, "relative": 0.042, "unit": "", "verdict": "better"}],
  "variants": [{"variant": "baseline", "n": 500, "value": 0.5026, "sd": null, "sum": 251.3,
                "ci_low": null, "ci_high": null}],
  "values": {"pairs": 500},
  "table": {"headers": ["Variante", "…"], "rows": [["candidate", "…"]]},
  "warnings": []
}
```

Die Plattform prüft jede Ausgabe: unbekannte Felder mit einfachen Werten
wandern nach `values`, andere werden verworfen (mit Warnung), Texte und Listen
werden gekürzt, `NaN` und unendliche Werte werden zu `null`, unbekannte Urteile
zu `n/a`. Was der Code auf die Konsole schreibt, steht im **Protokoll** der
Auswertung (höchstens 64 KB).

Beispiel in Python – ein Vorzeichentest über gepaarte Anfragen:

```python
"""Vorzeichentest: Bei wie vielen Anfragen schneidet die Variante besser ab als die Kontrolle?"""

import math
from collections import defaultdict


def _two_sided_binomial(k, n):
    """Exakter zweiseitiger Binomialtest gegen p = 0,5."""
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def evaluate(data):
    params = data.get("params") or {}
    pair_by = params.get("pair_by", "query")
    alpha = float(params.get("alpha", 0.05))
    metric = data["metric"]
    variants = data["variants"]
    if not variants:
        return {"verdict": "n/a", "headline": "Keine Varianten.", "warnings": []}
    control = next((v["key"] for v in variants if v["is_control"]), variants[0]["key"])
    sign = -1.0 if metric["direction"] == "lower" else 1.0

    # Mittel je Variante und Anfrage (eine Anfrage kann mehrere Zeilen haben).
    sums = defaultdict(lambda: [0.0, 0])
    for row in data["rows"]:
        key = (row.get("dims") or {}).get(pair_by)
        if key is None or row["variant"] is None:
            continue
        cell = sums[(row["variant"], key)]
        cell[0] += row["value"]
        cell[1] += row["count"]
    means = {k: s / n for k, (s, n) in sums.items() if n}

    comparisons, table_rows, warnings = [], [], []
    for variant in variants:
        if variant["key"] == control:
            continue
        wins = losses = ties = 0
        for (vkey, pair), value in means.items():
            if vkey != variant["key"] or (control, pair) not in means:
                continue
            diff = sign * (value - means[(control, pair)])
            wins, losses, ties = wins + (diff > 0), losses + (diff < 0), ties + (diff == 0)
        n = wins + losses
        if n == 0:
            warnings.append(f"Variante {variant['key']}: keine Paare mit Unterschied.")
            continue
        p_value = _two_sided_binomial(wins, n)
        verdict = "inconclusive"
        if metric["direction"] == "none":
            verdict = "n/a"
        elif p_value < alpha:
            verdict = "better" if wins > losses else "worse"
        comparisons.append({
            "variant": variant["key"], "baseline": control, "label": "Anteil gewonnener Paare",
            "estimate": wins / n, "ci_low": None, "ci_high": None, "p_value": p_value,
            "prob_better": None, "relative": None, "unit": "", "verdict": verdict,
        })
        table_rows.append([variant["key"], wins, losses, ties, f"{p_value:.4f}".replace(".", ",")])

    verdicts = [c["verdict"] for c in comparisons]
    overall = ("better" if "better" in verdicts else "worse" if "worse" in verdicts
               else "inconclusive" if verdicts else "n/a")
    headline = "; ".join(f"{c['variant']}: {c['estimate'] * 100:.0f} % der Paare besser"
                         for c in comparisons) or "Keine Paare."
    alpha_text = f"{alpha:g}".replace(".", ",")
    return {
        "verdict": overall,
        "headline": headline,
        "summary": f"Vorzeichentest je `{pair_by}`, zweiseitig, alpha = {alpha_text}.",
        "comparisons": comparisons,
        "variants": [],
        "values": {"pairs": sum(r[1] + r[2] + r[3] for r in table_rows)},
        "table": {"headers": ["Variante", "besser", "schlechter", "gleich", "p-Wert"],
                  "rows": table_rows},
        "warnings": warnings,
    }
```

Mit `input_kinds: [mean, duration, currency, ordinal]` und dem Parameterschema

```yaml
type: object
additionalProperties: false
properties:
  pair_by: {type: string, pattern: "^[A-Za-z0-9_.-]{1,40}$"}
  alpha: {type: number, minimum: 0.001, maximum: 0.2}
```

Beispiel in Julia – Welch-t-Test mit HypothesisTests.jl (`data` ist ein
`Dict{String,Any}`):

```julia
using HypothesisTests, Statistics

function evaluate(data)
    metric = data["metric"]
    variants = data["variants"]
    isempty(variants) && return Dict("verdict" => "n/a", "headline" => "Keine Varianten.")
    i = findfirst(v -> v["is_control"] === true, variants)
    control = variants[something(i, 1)]["key"]
    # Ein Wert je Zeile: Summe durch Anzahl (vorab zusammengefasste Zeilen zählen richtig).
    per_row(key) = [r["value"] / r["count"] for r in data["rows"] if r["variant"] == key]
    base = per_row(control)
    flip = metric["direction"] == "lower" ? -1 : 1
    comparisons = Any[]
    warnings = String[]
    for v in variants
        v["key"] == control && continue
        x = per_row(v["key"])
        if length(x) < 2 || length(base) < 2
            push!(warnings, "Variante $(v["key"]): zu wenige Zeilen.")
            continue
        end
        test = UnequalVarianceTTest(x, base)
        lo, hi = confint(test)
        p = pvalue(test)
        diff = mean(x) - mean(base)
        verdict = metric["direction"] == "none" ? "n/a" :
                  p >= 0.05 ? "inconclusive" : (flip * diff > 0 ? "better" : "worse")
        push!(comparisons, Dict("variant" => v["key"], "baseline" => control,
                                "label" => "Differenz", "estimate" => diff,
                                "ci_low" => lo, "ci_high" => hi, "p_value" => p,
                                "unit" => metric["unit"], "verdict" => verdict))
    end
    verdicts = [c["verdict"] for c in comparisons]
    overall = "better" in verdicts ? "better" :
              "worse" in verdicts ? "worse" :
              isempty(verdicts) ? "n/a" : "inconclusive"
    return Dict("verdict" => overall,
                "headline" => "Welch-t-Test über $(length(data["rows"])) Zeilen",
                "comparisons" => comparisons, "warnings" => warnings)
end
```

Wirft der Code eine Ausnahme, schlägt die Auswertung mit «Der Auswerter ist mit
einem Fehler abgebrochen.» fehl; der Stacktrace steht im Protokoll.

## Messarten

Jede Messwert-Zeile trägt `value` und je nach Messart `count`, `denominator`
und `sum_sq`. Eine Zeile kann eine einzelne Beobachtung sein (eine Anfrage,
eine Person) oder ein vorab zusammengefasster Block (ein Tag einer Kampagne,
ein Lauf) – das Modul speichert **hinreichende Statistiken** und rechnet
daraus.

| Messart (`kind`) | Anzeige | `value` | `count` | `denominator` | `sum_sq` | Schätzwert je Variante | Beispiel | Vorgesehene Auswerter |
|---|---|---|---|---|---|---|---|---|
| `proportion` | Anteil | Erfolge (ganze Zahl, ≤ `count`) | Versuche | – | – | Σ Erfolge / Σ Versuche | Klicks von Impressionen, Antworten von Anschreiben, gelöste von gestellten Aufgaben | describe, bayes_proportion, two_proportion |
| `mean` | Mittelwert | Summe der Werte | Anzahl Werte | – | Summe der Quadrate (bei `count` 1 automatisch) | Σ Werte / Σ Anzahl | NDCG@10 je Anfrage, SUS-Wert je Person | describe, welch_t, paired_t |
| `duration` | Dauer | Summe der Dauern | Anzahl | – | wie `mean` | wie `mean` | Latenz p95 je Lauf, Zeit bis Ergebnis | describe, welch_t, paired_t |
| `currency` | Geldbetrag | Summe der Beträge | Anzahl | – | wie `mean` | wie `mean` | Pipeline-Wert je Kontakt (auch 0) | describe, welch_t |
| `count` | Rate | Ereignisse (ganze Zahl ≥ 0) | Einheiten (Exposition) | – | – | Σ Ereignisse / Σ Einheiten | Fehler je 1000 Anfragen, Anmeldungen je Woche | describe, poisson_rate |
| `ratio` | Verhältnis | Zähler (Kosten ≥ 0) | Einheiten (meist 1 je Zeile) | Nenner (≥ 0, auch 0) | – | Σ Zähler / Σ Nenner (leer, solange Σ Nenner = 0) | Kosten pro Klick, Kosten pro Lead | describe, ratio_delta |
| `ordinal` | Skala | die Stufe | Personen auf dieser Stufe | – | – | Σ Stufe × Anzahl / Σ Anzahl, dazu die Verteilung | Zufriedenheit 1–5 | describe, welch_t, chi_square |
| `categorical` | Kategorie | der Kategorie-Code | Personen in dieser Kategorie | – | – | nur die Verteilung | bevorzugte Variante | describe, chi_square |

Weitere Regeln:

- **Quadratsumme.** Bei `count` 1 setzt das Modul `sum_sq` selbst (Wert²).
  Bei vorab zusammengefassten Zeilen (`count` > 1) ist sie optional; ohne sie
  kennt das Modul die Streuung nicht, und `welch_t` meldet «offen» mit einer
  Warnung. Wer Mittelwerte vergleichen will, liefert deshalb Einzelwerte
  (`count` 1) oder die Quadratsumme dazu.
- **Merkmale (`dims`)**: bis 20 Schlüssel (`^[A-Za-z0-9_.-]{1,40}$`) mit Texten
  bis 200 Zeichen, z. B. `{"query": "q17", "segment": "Kanzlei gross"}`. Sie
  dienen dem Paaren (`paired_t`), dem Eingrenzen (Scope `dims`) und eigenen
  Auswertern.
- **Zeitpunkt (`observed_at`)**: wann der Wert entstand, nicht wann er erfasst
  wurde. Ohne Angabe gilt «jetzt». Wochenzahlen tragen den Wochenanfang.
- `NaN` und unendliche Werte werden abgewiesen. Stufen und Kategorien müssen in
  der Metrik definiert sein; `min`/`max` der Metrik werden geprüft.
- Jede **Erfassung** – ein Formular, ein CSV-Import, ein API-Aufruf, ein Lauf –
  lässt sich unter **Messwerte → Erfassungen → Rückgängig** als Ganzes
  zurücknehmen. Einzelne Zeilen werden nie überschrieben.

## Echte Fälle erfassen

### Ranking-Qualität je Anfrage aus CI (Engineering, «Offline-Evaluation»)

Ziel: Ist der Kandidat (z. B. ein neuer Stemmer) auf dem festen Anfragesatz
besser als der Ausgangsstand?

- Experiment vom Typ **Offline-Evaluation**, Varianten `baseline` (Kontrolle)
  und `candidate`; Metriken NDCG@10 (primär), Recall@20, MRR, Leitplanke
  Latenz p95 ≤ 250 ms.
- CI meldet je Variante **einen Lauf** mit **einer Zeile je Anfrage und
  Metrik** (`value` = Wert der Anfrage, `count` 1, `dims.query` = Anfrage-ID)
  und der Latenz p95 als einem Wert je Lauf.
- Der Typ wertet mit Scope «neuester Lauf je Variante» und gepaartem t-Test je
  Anfrage aus: jede Anfrage wird mit sich selbst verglichen, das macht auch
  kleine Verbesserungen sichtbar.

```python
with client.run("ENG-12", variant="candidate", commit=sha, params={"stemmer": "v2"}) as run:
    for r in results:   # eine Zeile je Anfrage
        run.add_row("ndcg_at_10", r["ndcg"], dims={"query": r["query_id"]})
        run.add_row("recall_at_20", r["recall"], dims={"query": r["query_id"]})
    run.log(latency_p95_ms=p95_of_this_run)
```

Ein vollständiger GitHub-Actions-Ablauf steht unter [Zugangsschlüssel, API und
SDK](#github-actions). **Latenz p95** ist ein Wert je Lauf; die Schätzung ist
das Mittel über die Läufe, und `welch_t` (Typ «Performance-Änderung») braucht
mehrere Läufe je Variante. Für Latenzen je Anfrage die Metrik `latency_ms`
nehmen.

### Wöchentliche LinkedIn-Daten als breite CSV (Marketing, «A/B-Test»)

Ziel: Zwei Anzeigen-Varianten derselben Kampagne vergleichen, Klickrate und
Kosten pro Klick.

1. In LinkedIn Campaign Manager den Bericht der Kampagne mit Zeitraster
   **wöchentlich** und Aufschlüsselung nach **Anzeige** exportieren.
2. Je Anzeige und Woche eine Zeile: Anzeige → `variant`, Wochenbeginn →
   `observed_at`, Klicks → `ctr`, Impressionen → `ctr.count`, Ausgaben →
   `cost_per_click`, Klicks → `cost_per_click.denominator`.
3. Unter **Messwerte → CSV importieren** hochladen.

```csv
variant;observed_at;ctr;ctr.count;cost_per_click;cost_per_click.denominator
A;15.09.2026;129;10'688;412,80;129
B;15.09.2026;175;10'714;418,35;175
A;22.09.2026;141;11'020;425,10;141
B;22.09.2026;198;10'955;431,60;198
```

Die Klickrate ist ein **Anteil**: Klicks (Erfolge) von Impressionen
(Versuchen). Bayes-Vergleich und Zwei-Anteile-Test rechnen über die Summen
aller Wochen. Wochen nicht mitteln – eine Woche mit wenig Impressionen zählte
sonst so viel wie eine starke.

### Kosten als Verhältnis (Kosten pro Klick, Kosten pro Lead)

Kosten pro Lead sind ein **Verhältnis**: `value` = Kosten in CHF,
`denominator` = Leads, eine Zeile je Woche (oder Tag). Der Schätzwert ist
**Summe der Kosten durch Summe der Leads**, nicht das Mittel der
Wochenwerte:

| Woche | Kosten | Leads | Kosten/Lead der Woche |
|---|---|---|---|
| 1 | 400 | 2 | 200 |
| 2 | 400 | 8 | 50 |
| **Gesamt** | **800** | **10** | **80** (das Mittel der Wochen, 125, wäre falsch) |

Eine Woche **ohne Lead** ist erlaubt (`denominator` 0): ihre Kosten zählen
mit, ein Wochenwert «Kosten pro Lead» wäre dort gar nicht definiert. Der
Verhältnis-Vergleich (`ratio_delta`) behandelt jede Zeile als Einheit; für ein
belastbares Intervall braucht es daher mehrere Wochen (oder Tage) je Variante.

```csv
variant;observed_at;cost_per_lead;cost_per_lead.denominator
A;15.09.2026;400;2
A;22.09.2026;400;8
A;29.09.2026;380;0
```

### Interviews als Notizen (Produkt, «Nutzertest»)

Ein Nutzertest liefert wenige Zahlen und viel Text. Je Interview eine Notiz
der Art **Interview** (Beobachtungen während einer Sitzung als **Beobachtung**,
Rückmeldungen per Mail als **Rückmeldung**):

```markdown
**P4** – Associate, mittelgrosse Kanzlei, nutzt Knovas seit 3 Monaten

- Aufgabe 2 (Urteil zu Mietzinserhöhung finden): gelöst nach 95 s, suchte zuerst im Filter «Rechtsgebiet».
- «Ich hätte erwartet, dass die Treffer nach Datum sortiert sind.»
- Folge: Sortierung nach Datum als Option testen (→ PRD-9).
```

- **Pseudonyme statt Namen** (`P4`), keine Kanzleinamen, keine Mandatsdaten:
  Notizen werden mit dem Experiment in Knovas indexiert und sind für alle mit
  Experimente-Rolle lesbar.
- Notizen sind Markdown und in Knovas durchsuchbar – «Sortierung nach Datum»
  findet später alle Interviews, in denen das vorkam.
- Die Zahlen desselben Tests (Aufgabenerfolg, SUS, Zufriedenheit) gehören als
  Messwerte daneben, siehe nächster Abschnitt.

### SUS-Werte, Aufgabenerfolg und Zufriedenheit

**SUS** (System Usability Scale): zehn Aussagen, je 1–5. Wert je Person =
2,5 × (Σ (ungerade Aussagen − 1) + Σ (5 − gerade Aussagen)), also 0–100. Das
Modul erwartet den fertigen Wert: **eine Zeile je Person**, `count` 1, Metrik
`sus_score` (Mittelwert, 0–100).

**Aufgabenerfolg** ist ein Anteil: je Person `value` = gelöste Aufgaben,
`count` = gestellte Aufgaben. **Zufriedenheit** ist eine Skala: `value` = Stufe
1–5, `count` 1 je Person (oder die Zahl der Personen auf dieser Stufe).

```csv
metric;variant;value;count;observed_at;dim.participant
sus_score;;72,5;1;24.09.2026;P1
sus_score;;85;1;24.09.2026;P2
task_success;;4;5;24.09.2026;P1
task_success;;3;5;24.09.2026;P2
satisfaction;;4;1;24.09.2026;P1
satisfaction;;5;1;24.09.2026;P2
```

Der Typ «Nutzertest» hat keine Varianten (Spalte `variant` leer) und prüft den
Aufgabenerfolg gegen das Ziel 80 %: die Kopfzeile lautet dann etwa
«Aufgabenerfolg 75,0 % (95 %-KI 40,9–92,9 %) – Ziel 80,0 % nicht belegt.» Wer
den SUS-Wert gegen den üblichen Richtwert 68 prüfen will, ergänzt im Typ die
Zeile `{evaluator: builtin.describe, metric: sus_score, params: {target: 68}}`
und speichert eine neue Version. Mit Varianten (zwei Prototypen) vergleicht
`welch_t` die SUS-Mittel, `chi_square` die Verteilung der Zufriedenheit.

### Vertrieb: Sequenzen mit geplanter Stichprobe («Playbook-Test»)

Je Woche und Variante: angeschriebene Kontakte, Antworten, Termine,
Abmeldungen.

```csv
variant;observed_at;reply_rate;reply_rate.count;meeting_rate;meeting_rate.count;unsubscribe_rate;unsubscribe_rate.count
A;22.09.2026;14;120;4;120;1;120
B;22.09.2026;22;118;7;118;0;118
```

Das Feld «Geplante Kontakte je Variante» (`planned_n`) sperrt «Zur
Auswertung», bis jede Variante so viele Kontakte hat (`n_planned:planned_n`). Der
Stichprobenrechner unter **Metriken** schätzt die nötige Zahl aus Basisrate und
kleinstem relevantem Unterschied. Beträge (`pipeline_value`, `deal_value`) je
Kontakt oder Angebot erfassen, **auch 0** – sonst wäre das Mittel zu
optimistisch.

## CSV-Formate

UTF-8 (mit oder ohne BOM), Trennzeichen `;`, `,` oder Tab (wird erkannt), eine
Kopfzeile. Höchstens `EXPERIMENTS_MAX_CSV_ROWS` Zeilen (Vorgabe 200'000) und
20 MB; grössere Dateien aufteilen. Ein Import ist eine Erfassung: alle Zeilen
oder keine.

**Langes Format** – die Kopfzeile enthält `metric`, eine Zeile je Messwert:

| Spalte | Pflicht | Inhalt |
|---|---|---|
| `metric` | ja | Metrik-Schlüssel |
| `value` | ja | Wert |
| `variant` | wenn das Experiment Varianten hat | Varianten-Schlüssel; leer = ohne Variante |
| `count`, `denominator`, `sum_sq` | je nach Messart | siehe [Messarten](#messarten) |
| `observed_at` | nein | Zeitpunkt |
| `run` | nein | ID eines erfassten Laufs |
| `dim.<name>` | nein | Merkmal, z. B. `dim.query` (höchstens 20 Spalten) |

**Breites Format** – ohne Spalte `metric`: eine Spalte je Metrik, benannt nach
ihrem Schlüssel (`ctr`), dazu `<schlüssel>.count`, `<schlüssel>.denominator`,
`<schlüssel>.sum_sq`, und `variant`, `observed_at`, `run`, `dim.<name>`. Aus
jeder Zelle einer Metrik-Spalte mit Wert wird eine Messwert-Zeile.

- **Zahlen**: `'`, `’`, Leerzeichen und geschützte Leerzeichen als
  Tausendertrennzeichen werden entfernt (`10'688`); ein einzelnes Komma ohne
  Punkt ist ein Dezimalkomma (`412,80`). Beim Trennzeichen `,` deshalb
  Dezimalpunkte verwenden – oder `;` als Trennzeichen.
- **Daten**: ISO 8601 (`2026-09-15`, `2026-09-15T08:00:00+02:00`) oder
  `TT.MM.JJJJ` (gilt als Mitternacht UTC).
- **Spaltennamen** aus Buchstaben, Ziffern, `_ . -` und Leerzeichen (bis 60
  Zeichen); Zellen bis 200 Zeichen.
- Spalten, die das Modul keiner Metrik und keinem Feld zuordnen kann, werden
  übersprungen und nach dem Import genannt. Ordnen Sie jede Metrik **vor** dem
  Import dem Experiment zu (Abschnitt **Metriken**), sonst erscheint sie weder in
  den Karten noch in den Auswertungen.
- Fehler nennen die Zeile: «Zeile 5: Erfolge müssen eine ganze Zahl sein.» (bis
  zu 20 Fehler auf einmal).

## Auswertungen und Scope

- **Eingebaute Auswerter** rechnen sofort; die Karte ist gleich fertig.
- **Python- und Julia-Auswerter** warten auf die Rechenumgebung («wartet»,
  «läuft»); die Karte aktualisiert sich selbst. Ist die Rechenumgebung
  30 Minuten lang nicht erreichbar, schlägt die Auswertung fehl.
- **Automatisch nach neuen Daten**: 30 Sekunden nach jeder Erfassung laufen die
  Auswertungen des Typs («Pipeline»). Hat sich an den Eingaben nichts geändert
  (gleiche Daten, gleiche Auswerter-Version, gleiche Parameter und gleicher
  Scope), wird die bestehende Auswertung wiederverwendet statt neu gerechnet.
  Von den automatischen Auswertungen je Auswerter, Metrik, Parameter und Scope
  bleiben die neuesten 10; von Hand gestartete bleiben alle.
- **Von Hand**: **Auswertung starten** (Auswerter, Metrik, Scope, Parameter
  als JSON) oder **Alle Auswertungen des Typs ausführen**.

### Scope: welche Messwerte zählen

| Scope | JSON | Wann |
|---|---|---|
| Alle Daten | `{}` | Vorgabe: jede Zeile |
| Neuester Lauf je Variante | `{"runs": "latest"}` | CI-Benchmarks: je Variante der neueste **abgeschlossene** Lauf mit Werten dieser Metrik; dazu Zeilen ohne Lauf für Varianten ohne solchen Lauf |
| Bestimmte Läufe | `{"runs": ["<lauf-id>", "…"]}` | einen alten Stand nachrechnen |
| Zeitraum | `{"since": "2026-09-01T00:00:00+02:00", "until": "2026-10-01T00:00:00+02:00"}` | Kampagnen: nur die Laufzeit, ohne Nachzügler |
| Merkmal | `{"dims": {"segment": "Kanzlei gross"}}` | ein Segment für sich |

Die Angaben lassen sich kombinieren. Jede Auswertung zeigt ihren Scope.

### Urteile lesen

- **besser / schlechter / offen**, gemessen an der Richtung der Metrik
  («tiefer ist besser» dreht das Urteil); Metriken ohne Richtung ergeben
  Zahlen, aber kein Urteil (`n/a`, angezeigt als «–»).
- Frequentistische Tests: «besser» oder «schlechter», wenn der p-Wert unter
  `alpha` (0,05) liegt. Bei mehr als einem Vergleich (drei und mehr Varianten)
  werden die p-Werte nach Holm korrigiert («p-Werte nach Holm korrigiert.»).
- Bayes-Vergleich: «besser» ab P(besser) ≥ 95 %, «schlechter» ab ≤ 5 %.
- Das Gesamturteil ist «besser», wenn ein Vergleich besser ist (die Kopfzeile
  nennt den besten), sonst «schlechter», wenn einer schlechter ist, sonst
  «offen».
- **Leitplanken**: die Beschreibung prüft jede Variante gegen die Leitplanke
  der Metrik («Leitplanke verletzt: Variante B 78,0 % > 70,0 %.») und urteilt
  dann «schlechter»; Liste und Experiment zeigen «Leitplanke verletzt».
- Zu wenige Daten (n < 2, keine Paare, unbekannte Streuung) ergeben «offen» mit
  einer Warnung, nie einen Fehler.

Vorsicht bei der Deutung: Wer täglich nachsieht und beim ersten «besser» stoppt,
findet häufiger Unterschiede, die es nicht gibt. Die Stichprobe vorher planen
(Stichprobenrechner, Bedingung `n_planned`) und erst dann entscheiden. Ein
Ungleichgewicht der Zuteilung (Sample Ratio Mismatch) prüft das Modul nicht
selbst; ein Blick auf n je Variante in der Beschreibung genügt meist.

## Entscheidungen und Erkenntnisse

Das Formular **Entscheidung** erscheint, sobald der Status einen Wechsel in den
Status «Entschieden» erlaubt. Es zeigt verletzte Leitplanken zuerst und hält
fest:

| Urteil | Bedeutung |
|---|---|
| Übernehmen (`ship`) | Die Variante wird eingeführt. |
| Weiterentwickeln (`iterate`) | Die Richtung stimmt; ein Folgeexperiment. |
| Verwerfen (`stop`) | Die Idee wird nicht weiterverfolgt. |
| Ohne klares Ergebnis (`inconclusive`) | Die Daten entscheiden nicht. |

Dazu **Begründung** (warum, mit Verweis auf die Auswertung) und **Erkenntnis**
(was das Team daraus für künftige Vorhaben mitnimmt; Pflicht, wenn der Typ es
verlangt – in allen mitgelieferten Typen). Das Speichern hält die Entscheidung
fest und wechselt in einem Schritt in den Status «Entschieden». Frühere
Entscheidungen bleiben als Verlauf sichtbar.

Eine Erkenntnis ist dann nützlich, wenn sie ohne das Experiment verständlich
ist und in einem Jahr noch gefunden wird:

> Bei Partnerinnen und Partnern in Kanzleien wirken LinkedIn-Karussells mit
> einem konkreten Fallbeispiel besser als reine Textposts (Klickrate +0,42 Pp.,
> drei Wochen, je rund 21'000 Impressionen). Kosten pro Klick unverändert.

**Abbrechen** verlangt einen Grund; er steht als Notiz der Art «Statuswechsel»
im Experiment und ist durchsuchbar wie jede Notiz.

## Suchen mit Knovas

**Im Modul**: das Suchfeld «In Knovas suchen …» auf der Übersicht fragt Knovas
und die Datenbank und führt die Treffer zusammen. Die Zeile über den Treffern
sagt, woher sie kommen: «Gefunden mit Knovas», «Knovas und Datenbank» oder
«Datenbanksuche» – letzteres, wenn Knovas nicht erreichbar ist, die
Indexierung aus ist oder Ihnen die Zugriffsgruppe fehlt (mit Hinweis).

**In der normalen Suche** erscheinen Experimente für Personen mit
Experimente-Rolle als eigene Karten (Kolben-Symbol, «Experiment · Bereich ·
Status»); ein Klick öffnet die Experiment-Seite, nie eine Datei. Titel, Status
und Bereich kommen dabei aus der Datenbank, nicht aus der Knovas-Kopie – sie
sind also immer aktuell.

- Jede Person kann Experiment-Treffer für sich ausschalten: **Experimente →
  Verwaltung → Zugangsschlüssel → «Experimente in meiner normalen Suche
  zeigen»**.
- Verantwortliche können sie für alle ausschalten: **Experimente → Verwaltung
  → Index → «Experimente in der normalen Suche zeigen»**.
- Für alle ohne Experimente-Rolle sind Experiment-Treffer nie sichtbar – die
  Plattform entfernt sie aus jeder Antwort, aus der Trefferliste ebenso wie aus
  der Zeigerliste, die Knovas mitliefert.

**Was in Knovas steht**: ein Dokument je Experiment, Zeiger
`experiments/<bereich>/<SCHLÜSSEL>`, Pfad
`/Experimente/<Bereich>/<Typ>/<SCHLÜSSEL> <Titel>`, Titel «SCHLÜSSEL · Titel»,
Beschreibung = Hypothese. Der Text enthält Hypothese, Beschreibung, Angaben,
Varianten, Metriken mit Schätzwerten je Variante, die neueste Auswertung je
Auswerter und Metrik (Kopfzeile und Zusammenfassung), Läufe (bis 50), Notizen
und Entscheidungen mit Begründung und Erkenntnis. **Namen und E-Mail-Adressen
von Konten stehen nicht darin** – freier Text aber so, wie er geschrieben wurde.
Sehr lange Experimente werden gekürzt (ältere Auswertungen, dann Läufe, dann
Notizen zuerst).

**Wann**: etwa eine Minute nach der letzten Änderung (Änderungen innerhalb
dieses Fensters werden einmal hochgeladen), höchstens
`EXPERIMENTS_INDEX_PER_MINUTE` Uploads pro Minute für alle Experimente
zusammen. Nach einer Massenänderung (Paket, Umbenennung eines Bereichs) kann das
eine Weile dauern; Änderungen von Personen gehen vor. Der Stand steht im Kopf
jedes Experiments: «In Knovas: aktuell / ausstehend / Fehler: … / aus»;
**Neu indexieren** stellt ein Experiment sofort in die Warteschlange.

Gute Fragen an die Suche: «Karussell LinkedIn Partner», «Stemmer Recall»,
«Preis Kanzlei gross», «Sortierung nach Datum Interview».

## Zugangsschlüssel, API und SDK

### Zugangsschlüssel

Unter **Experimente → Verwaltung → Zugangsschlüssel** legt jede Person mit
Experimente-Rolle eigene Schlüssel an: Name (z. B. «CI Suchqualität») und
Ablauf in Tagen (1–365, Vorgabe 90). Der Schlüssel (`kxp_…`) wird **einmal**
angezeigt; gespeichert wird nur sein SHA-256-Hash und ein Hinweis auf die
letzten vier Zeichen.

- Ein Schlüssel handelt als seine Person, **mit deren Rollen zum Zeitpunkt
  der Anfrage**: wird die Rolle entzogen, das Konto gesperrt oder deaktiviert
  oder muss es sein Passwort ändern, gilt der Schlüssel nicht mehr (HTTP 401).
- Er gilt nur für `/api/experiments/v1` – nicht für die Suche, die Verwaltung
  oder andere Teile der Plattform. Ein Sitzungs-Cookie wird dort nie gelesen.
- **Widerrufen** wirkt sofort. Jede Änderung über einen Schlüssel steht mit
  dessen ID im Audit-Protokoll; «zuletzt benutzt» steht in der Liste.
- Für CI am besten ein eigenes Konto (z. B. `ci-experimente@…`) nur mit der
  Rolle `experimenter` anlegen und dessen Schlüssel als Secret hinterlegen.
  Rechtzeitig vor Ablauf einen neuen anlegen und den alten widerrufen.

### Maschinen-API

`Authorization: Bearer kxp_…`, JSON. Antwort `{"success": true, <schlüssel>: …}`
oder `{"success": false, "error": "…", "fields": {…}}`.

| Methode, Pfad (unter `/api/experiments/v1`) | Zweck | Antwort |
|---|---|---|
| `GET /ping` | wer bin ich | `user` |
| `POST /experiments` | Experiment anlegen (`domain`, `type`, `title`, `hypothesis`, `fields`) | `experiment` (201) |
| `GET /experiments/<KEY>` | Kopf, Varianten, Metriken | `experiment` |
| `POST /experiments/<KEY>/runs` | Lauf mit Messwerten | `run` (201) |
| `POST /experiments/<KEY>/measurements` | Messwerte (`rows`) | `result` (201) |
| `POST /experiments/<KEY>/notes` | Notiz (`body`, `kind`) | `note` (201) |
| `POST /experiments/<KEY>/pipeline` | Auswertungen des Typs (optional `scope`) | `evaluations` |
| `GET /experiments/<KEY>/evaluations?metric=&limit=20` | neueste Auswertungen | `evaluations` |

```bash
curl -sS -H "Authorization: Bearer $KNOVAS_EXPERIMENTS_TOKEN" \
  https://knovas.example.ch/api/experiments/v1/ping

curl -sS -X POST -H "Authorization: Bearer $KNOVAS_EXPERIMENTS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"variant": "candidate", "commit": "3f2a91c", "metrics": {"latency_p95_ms": 212}}' \
  https://knovas.example.ch/api/experiments/v1/experiments/ENG-12/runs
```

Ist das Modul ausgeschaltet, antwortet jede Adresse mit 404 «Experimente sind
nicht eingeschaltet.».

### Python- und Julia-Client

`KnovasPlatform/experiments-sdk/python/knovas_experiments.py` (eine Datei, nur
Standardbibliothek) und `KnovasPlatform/experiments-sdk/julia/KnovasExperiments.jl`
(HTTP.jl, JSON3.jl). Beide folgen Umleitungen nie, zeigen den Schlüssel in
keiner Meldung, prüfen TLS und wiederholen nur lesende Anfragen. Einzelheiten:
[Python](../../experiments-sdk/python/README.md), [Julia](../../experiments-sdk/julia/README.md).

```python
from knovas_experiments import Client

client = Client()   # KNOVAS_URL, KNOVAS_EXPERIMENTS_TOKEN
with client.run("ENG-12", variant="candidate", commit=sha) as run:
    for r in results:
        run.add_row("ndcg_at_10", r["ndcg"], dims={"query": r["query_id"]})
    run.log(latency_p95_ms=212.0)
worse = [e for e in client.evaluate("ENG-12") if e.get("verdict") == "worse"]
```

Endet der `with`-Block mit einer Ausnahme, wird der Lauf als «fehlgeschlagen»
und ohne Messwerte gemeldet.

### GitHub Actions

Secret `KNOVAS_EXPERIMENTS_TOKEN` und Variable `KNOVAS_URL` im Repository
anlegen; `tools/knovas_experiments.py` ins Repository übernehmen. Der Job misst
Ausgangsstand und Kandidat, meldet beide Läufe und schlägt fehl, wenn eine
Auswertung «schlechter» ergibt (auch bei verletzter Leitplanke):

```yaml
name: Suchqualität
on:
  pull_request:
    paths: ["search/**", "bench/**"]
permissions:
  contents: read
jobs:
  offline-eval:
    runs-on: ubuntu-latest
    env:
      KNOVAS_URL: ${{ vars.KNOVAS_URL }}
      KNOVAS_EXPERIMENTS_TOKEN: ${{ secrets.KNOVAS_EXPERIMENTS_TOKEN }}
      EXPERIMENT: ENG-12
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -r requirements.txt
      - run: python tools/knovas_experiments.py ping
      - name: Ausgangsstand und Kandidat messen
        run: |
          git worktree add ../baseline "${{ github.event.pull_request.base.sha }}"
          python bench/run_queries.py --code ../baseline --out baseline.jsonl
          python bench/run_queries.py --code . --out candidate.jsonl
      - name: Melden und auswerten
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: python bench/report_to_knovas.py
```

Das Skript `bench/report_to_knovas.py` steht vollständig im
[README des Python-Clients](../../experiments-sdk/python/README.md#github-actions-suchqualität-je-pull-request).
Kurz: je Variante `client.run(...)` mit einer Zeile je Anfrage, danach
`client.wait_for(KEY, client.evaluate(KEY))` und Exit-Code 1 bei `worse`.

## Betrieb

### Hintergrundarbeit

Indexieren, Löschen in Knovas, automatische Auswertungen und Python-/Julia-
Auswertungen laufen als Aufträge in einer Warteschlange in der
Plattform-Datenbank. Jeder Web-Prozess (gunicorn-Worker) hat zwei
Hintergrund-Threads: einen für Index und Pipeline, einen für Auswertungen in
der Rechenumgebung. Aufträge werden mit Lease vergeben (ein abgestürzter
Prozess gibt sie nach Ablauf frei), mit wachsendem Abstand wiederholt (bis zu
8 Versuche) und danach als «gescheitert» markiert. Gleiche Aufträge werden
zusammengefasst: zehn Änderungen in einer Minute ergeben einen Upload. Etwa alle
zehn Minuten stellt die Wartung liegengebliebene Experimente («ausstehend»,
«Fehler») erneut ein und räumt erledigte Aufträge nach sieben Tagen weg.

### Index

**Experimente → Verwaltung → Index** (Verantwortliche) zeigt:

- Anzahl Experimente je Stand (aktuell, ausstehend, Fehler, aus),
- Aufträge je Status und die letzten Fehler,
- **Personen ohne Zugriffsgruppe**: Personen mit Experimente-Rolle, denen eine
  Gruppe aus `EXPERIMENTS_ACCESS_GROUPS` fehlt,
- den Zustand der Rechenumgebung,
- **Alles neu indexieren** (mit niedriger Priorität; Änderungen von Personen
  gehen vor).

| Stand im Experiment | Bedeutung, nächster Schritt |
|---|---|
| aktuell | Die Knovas-Kopie entspricht dem letzten Stand. |
| ausstehend | Ein Upload ist eingeplant (Entprellung, Uploadrate). |
| Fehler: Für Experimente ist keine Knovas-Zugriffsgruppe festgelegt (EXPERIMENTS_ACCESS_GROUPS). | Gruppe setzen, dann **Alles neu indexieren**. |
| Fehler: Knovas war nicht erreichbar. | Alle Versuche sind gescheitert. Verbindung prüfen (`doctor.sh`); die Wartung versucht es wieder. |
| Fehler: Knovas hat das Dokument abgelehnt (HTTP …). | Knovas weist das Dokument ab; Protokoll von `docbridge-web` ansehen. |
| aus | Indexierung ausgeschaltet (`EXPERIMENTS_INDEX_ENABLED=false`) oder nach `purge-index`. |

### doctor.sh

`./scripts/doctor.sh` hat einen Abschnitt **Experimente**. Bei ausgeschaltetem
Modul meldet er nur «aus». Eingeschaltet prüft er: persönliche Konten an,
Tabellen vorhanden, Aufträge und gescheiterte Aufträge (Warnung), Experimente
mit Index-Fehler (Warnung), leere `EXPERIMENTS_ACCESS_GROUPS` ohne
`EXPERIMENTS_INDEX_UNRESTRICTED` (Warnung «Experimente werden nicht in Knovas
indexiert: keine Zugriffsgruppe»), `EXPERIMENTS_INDEX_UNRESTRICTED` ohne Gruppe
(Warnung «Experimente sind in Knovas für alle Nutzer des Mandanten sichtbar»)
und – wenn `EXPERIMENTS_RUNNER_URL` gesetzt ist – ob die Rechenumgebung
erreichbar ist.

### Kommandozeile

Im Container `docbridge-web`:

```bash
DC="docker compose --env-file knovas.env"
$DC exec docbridge-web python -m experiments status            # Einstellungen, Aufträge, Indexstand, Rechenumgebung
$DC exec docbridge-web python -m experiments status --json
$DC exec docbridge-web python -m experiments reindex MKT-12 ENG-3
$DC exec docbridge-web python -m experiments reindex --all
$DC exec docbridge-web python -m experiments purge-index [--yes]
$DC exec docbridge-web python -m experiments install-pack sales --as max@firma.ch
$DC exec docbridge-web python -m experiments worker --once     # fällige Aufträge abarbeiten, dann beenden
```

| Befehl | Wirkung |
|---|---|
| `status` | Schalter, Zugriffsgruppen, Aufträge je Status, Indexstand, erfasste Knovas-Dokumente, letzte Fehler, Rechenumgebung |
| `reindex KEY … \| --all` | Experimente zum Hochladen einreihen (einzeln mit hoher, alle mit niedriger Priorität) |
| `purge-index [--yes]` | Alle Experiment-Dokumente aus Knovas löschen – jedes erfasste und alles, was Knovas unter dem Präfix noch führt; ohne `--yes` nur anzeigen. Geht auch bei ausgeschaltetem Modul. |
| `install-pack NAME [--as E-MAIL]` | Paket installieren, als ein Konto mit Verwalter-Rolle (Vorgabe `PLATFORM_ADMIN_EMAIL`) |
| `worker [--once] [--max-jobs N]` | Hintergrundarbeit im Vordergrund; mit `--once` nur die fälligen Aufträge |

### Sicherung und Wiederherstellung

Alles, was das Modul weiss, liegt in der Plattform-Datenbank (Volume
`platform_db_data`): Bereiche, Typen, Metriken, Auswerter mit allen Versionen,
Experimente, Messwerte, Läufe, Notizen, Auswertungen, Entscheidungen,
Zugangsschlüssel (als Hash) und die Warteschlange. Die Knovas-Kopien lassen sich
daraus jederzeit neu erzeugen; die Rechenumgebung hält keinen Zustand.

```bash
DC="docker compose --env-file knovas.env"
# Sicherung der ganzen Plattform-Datenbank (Konten, Rollen, Audit, Experimente)
$DC exec -T platform-db pg_dump -U platform -d knovas_platform --format=custom \
  > platform-db-$(date +%F).dump

# Wiederherstellung
$DC stop docbridge-web
$DC exec -T platform-db pg_restore -U platform -d knovas_platform --clean --if-exists \
  < platform-db-2026-09-28.dump
$DC start docbridge-web
$DC exec docbridge-web python -m experiments reindex --all
```

(`-U` und `-d` entsprechen `PLATFORM_DB_USER` und `PLATFORM_DB_NAME`.) Nur die
Experiment-Tabellen, etwa für eine Auswertung ausserhalb:
`pg_dump -U platform -d knovas_platform -t 'exp_*' --data-only`. Eine
wiederhergestellte Sicherung bringt auch widerrufene oder abgelaufene
Zugangsschlüssel in den Stand von damals zurück – nach einem Restore die Liste
unter **Zugangsschlüssel** prüfen.

Konfiguration lässt sich zusätzlich als Code sichern: **Experimente → Verwaltung
→ Bereiche → Exportieren** je Bereich ins Git-Repository legen.

## Sicherheitsmodell der Rechenumgebung

Eigene Auswerter sind Code, den Verantwortliche schreiben. Er läuft **nie** in
der Plattform, sondern nur im Dienst `experiments-runner`:

| Schicht | Massnahme |
|---|---|
| Netzwerk | `network_mode: none`: kein Netzwerk, auch nicht das interne. Die Plattform erreicht den Dienst nur über einen Unix-Socket auf einem kleinen tmpfs-Volume (1 MB, `noexec`); `docbridge-web` bindet es nur lesend ein. Der Code erreicht nichts – weder Knovas noch die Datenbank noch das Internet. |
| Container | eigener Benutzer (uid 10001), Dateisystem nur lesbar, alle Capabilities entzogen, `no-new-privileges`, Grenzen für Speicher (`EXPERIMENTS_RUNNER_MEMORY`), CPU (`EXPERIMENTS_RUNNER_CPUS`) und Prozesse (256); `/tmp` als tmpfs mit 1 GB. Kein Secret, keine `env_file`, kein weiteres Volume. |
| Auftrag | eigener Prozess in eigener Sitzung, frisches Verzeichnis (0700), Umgebung nur mit wenigen festen Variablen; Limits für CPU-Zeit, Adressraum (Python 1,5 GB, Julia 6 GB), Dateigrösse (64 MB), offene Dateien, Prozesse, keine Core-Dumps; hoher OOM-Wert, damit der Kernel zuerst den Auftrag beendet. |
| Aufräumen | Nach Zeitablauf wird die ganze Prozessgruppe beendet; nach jedem Auftrag jeder Prozess, der weder zum Dienst noch zu einem laufenden Auftrag gehört (auch doppelt abgespaltene); das Verzeichnis wird gelöscht. |
| Ausgabe | nur eine reguläre Datei bis 8 MB, ohne Symlinks gelesen; Protokoll bis 64 KB; feste deutsche Fehlermeldungen. |
| Plattform | behandelt jede Ausgabe als fremde Daten: prüft und kürzt sie (höchstens 256 KB), zeigt Texte nur maskiert bzw. über den Markdown-Renderer, speichert und indexiert nur die geprüfte Fassung. Höchstens `EXPERIMENTS_RUNNER_MAX_CONCURRENT` Aufträge gleichzeitig; Zeitlimit `EXPERIMENTS_RUNNER_TIMEOUT`. |
| Wer darf | Nur `experiments_manager` und `admin` legen Auswerter an oder ändern sie; jede Version bleibt erhalten, jede Änderung und jeder Test steht im Audit-Protokoll (mit SHA-256 des getesteten Codes). |

Die eingebauten Auswerter sind Teil der Plattform (reines Python, nur Zahlen
als Eingabe) und laufen ohne Rechenumgebung.

### Bewusst getragene Risiken

- **Container, keine virtuelle Maschine.** Die Rechenumgebung teilt den Kernel
  mit dem Host. Eine Kernel-Lücke könnte aus dem Container herausführen; die
  Schichten oben machen das schwer, nicht unmöglich. Wer stärkere Trennung
  braucht, betreibt den Dienst auf einem Host ohne andere Dienste oder mit einer
  Sandbox-Laufzeit wie gVisor – beides ist nicht Teil der Auslieferung und nicht
  getestet.
- **Gleicher Benutzer für Dienst und Aufträge.** Auswerter-Code läuft unter
  derselben uid wie der Runner-Dienst. Ein böswilliger Auswerter kann den Dienst
  stören, gleichzeitig laufende Auswertungen lesen oder deren Ergebnis
  verfälschen und den Socket ersetzen, bis der Dienst neu startet. Er erreicht
  dabei nur Experimentdaten – die Verantwortliche ohnehin alle sehen – und
  nichts ausserhalb des Containers. Deshalb dürfen nur Verantwortliche
  Auswerter schreiben, und jede Version ist nachvollziehbar.
- **Auswerter sehen die Daten ihres Experiments** und können sie in ihre
  Ausgabe schreiben; die Ausgabe landet im Experiment und in Knovas. Das ist der
  Zweck eines Auswerters.
- **Auslastung.** Ein langsamer Auswerter belegt einen Platz bis zum Zeitlimit;
  andere Python-/Julia-Auswertungen warten so lange. Eingebaute Auswerter sind
  davon nicht betroffen.
- **Julia-Pakete** werden beim Bau des Images aufgelöst und sind – anders als
  die Python-Pakete – nicht auf Versionen festgelegt; zwei Builds können
  verschiedene Versionen enthalten.
- **Zugangsschlüssel** gelten bis zu 365 Tage, wenn niemand sie widerruft. Ein
  entwendeter Schlüssel erlaubt, was seine Person im Modul darf – nicht mehr,
  und nur bis zum Widerruf.
- **Knovas-Sichtbarkeit.** Was in Experimenten steht, steht auch in Knovas,
  geschützt durch die Zugriffsgruppe. Ob Knovas sie schon durchsetzt, siehe
  [Die Knovas-Zugriffsgruppe](#die-knovas-zugriffsgruppe).

## Grenzen

| Was | Grenze |
|---|---|
| Messwerte | ausgelegt auf 10⁸ Zeilen (Aggregation über einen deckenden Index) |
| Zeilen je API-Aufruf oder Lauf | 10'000 |
| Anfrage | 32 MB |
| CSV-Import | `EXPERIMENTS_MAX_CSV_ROWS` (200'000) Zeilen, 20 MB |
| Zeilen für einen eigenen Auswerter | 100'000 (die neuesten; Aggregate immer vollständig) |
| Laufzeit eines Python-/Julia-Auswerters | `EXPERIMENTS_RUNNER_TIMEOUT` (90 s); Test in der Verwaltung höchstens 60 s |
| Auswerter-Code | 200'000 Zeichen |
| Auswerter-Ausgabe | 256 KB (Kopfzeile 200, Zusammenfassung 20'000 Zeichen, 50 Vergleiche, Tabelle 200 × 20); Protokoll 64 KB |
| Titel / Hypothese / Beschreibung | 300 / 20'000 / 50'000 Zeichen |
| Notiz | 50'000 Zeichen |
| Schlagwörter | 20 je Experiment, je 50 Zeichen |
| Varianten | 20 je Experiment (der Typ kann weniger festlegen) |
| Typ | 40 Felder, 2–12 Status, 40 Wechsel, 30 Metriken, 20 Auswertungsschritte |
| Merkmale (`dims`) | 20 je Zeile, Werte bis 200 Zeichen |
| Paket / Typdefinition | 2 MB / 200 KB |
| Knovas-Dokument | 400'000 Zeichen in Teilen bis 40'000 |
| Uploads nach Knovas | `EXPERIMENTS_INDEX_PER_MINUTE` (2) pro Minute |
| Zugangsschlüssel | Ablauf 1–365 Tage |
| Experiment-Seite | die neuesten 60 Auswertungen, 200 Notizen, 100 Läufe |

Nicht enthalten (bewusst): Anbindungen an Werbe- oder CRM-Systeme (Daten kommen
über Formular, CSV oder API), Dateianhänge, Rechte je Experiment oder Bereich,
Notebooks, sequentielle Tests und eine automatische SRM-Prüfung,
KI-Zusammenfassungen.

## Wenn etwas nicht geht

| Was Sie sehen | Was zu tun ist |
|---|---|
| Kein Menüpunkt «Experimente» | Modul aus (`EXPERIMENTS_ENABLED`), persönliche Konten aus, oder Ihnen fehlt die Rolle `experimenter`/`experiments_manager`. |
| API antwortet 404 «Experimente sind nicht eingeschaltet.» | `EXPERIMENTS_ENABLED=true` in `knovas.env`, dann `setup.sh` und `start.sh`. |
| API antwortet 401 | Schlüssel abgelaufen, widerrufen oder vertippt; Konto gesperrt, deaktiviert, mit offenem Passwortwechsel oder ohne Experimente-Rolle. |
| Der Client meldet eine Umleitung | Die Adresse stimmt nicht (meist `http://` statt `https://`, oder ein Pfad fehlt). |
| «In Knovas: Fehler: … keine Knovas-Zugriffsgruppe …» | `EXPERIMENTS_ACCESS_GROUPS` setzen (oder Ordnerregel + `EXPERIMENTS_INDEX_UNRESTRICTED`), dann **Alles neu indexieren**. |
| Suche im Modul sagt «Datenbanksuche» mit Hinweis auf die Zugriffsgruppe | Ihnen fehlt die Gruppe: **Verwaltung → Personen → Zugriffsgruppen** ergänzen. |
| Experimente stehen lange auf «ausstehend» | Uploadrate (2 pro Minute) nach einer Massenänderung; `python -m experiments status` zeigt die Warteschlange. Sind Aufträge «gescheitert», `doctor.sh`. |
| Python-/Julia-Auswerter ausgegraut | Profil `experiments` und `EXPERIMENTS_RUNNER_URL` setzen; `docker compose --env-file knovas.env ps experiments-runner` muss «healthy» zeigen. |
| Auswertung bleibt auf «wartet» | Rechenumgebung nicht erreichbar oder ausgelastet; nach 30 Minuten schlägt sie fehl. |
| «Zeitlimit überschritten.» | Auswerter zu langsam für `EXPERIMENTS_RUNNER_TIMEOUT`; Code beschleunigen oder Zeilen per Scope eingrenzen. |
| CSV: «Zeile 5: …» | Die Meldung nennt das Problem; die Datei wurde nicht übernommen (alles oder nichts). |
| CSV: Spalten «ignoriert» | Metrik nicht zugeordnet oder Spaltenname weicht vom Metrik-Schlüssel ab. |
| «Das Experiment wurde inzwischen geändert. Bitte neu laden.» | Jemand anderes hat gleichzeitig gespeichert; neu laden und die Änderung wiederholen. |
| Knopf für einen Statuswechsel gesperrt | Die Zeile darunter nennt, was fehlt (Hypothese, Messwerte, Stichprobe …). |
| `welch_t` meldet «offen» mit Hinweis auf die Streuung | Zusammengefasste Zeilen ohne Quadratsumme: Einzelwerte oder `sum_sq` liefern. |
