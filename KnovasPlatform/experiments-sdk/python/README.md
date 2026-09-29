# knovas_experiments – Python-Client für Experimente

Meldet Läufe, Messwerte und Notizen aus CI, Benchmarks und Skripten an das
Modul **Experimente** der Knovas Platform und liest die Auswertungen zurück.
Eine Datei, nur Standardbibliothek (Python 3.8 oder neuer), keine
Abhängigkeiten.

Hintergrund, Messarten und Betrieb: [Experimente](../../docs/features/experiments.md).

## Einrichten

1. In der Platform unter **Experimente → Verwaltung → Zugangsschlüssel** einen
   Schlüssel anlegen (Name, Ablauf in Tagen). Er wird **einmal** angezeigt und
   beginnt mit `kxp_`. Er handelt als die Person, die ihn angelegt hat, mit
   ihren Rollen zum Zeitpunkt der Anfrage.
2. Die Datei `knovas_experiments.py` neben das eigene Skript kopieren (oder ins
   Repository übernehmen, z. B. `tools/knovas_experiments.py`).
3. Adresse und Schlüssel als Umgebungsvariablen setzen:

```bash
export KNOVAS_URL=https://knovas.example.ch
export KNOVAS_EXPERIMENTS_TOKEN=kxp_...
python knovas_experiments.py ping
# Verbunden als Eva Muster (Rollen: experimenter).
```

`ping` prüft in einem Schritt Adresse, Schlüssel und ob das Modul auf dieser
Platform eingeschaltet ist.

## Beispiele

```python
from knovas_experiments import Client

client = Client()   # KNOVAS_URL und KNOVAS_EXPERIMENTS_TOKEN
# oder: Client("https://knovas.example.ch", "kxp_...", cafile="/etc/ssl/firma-ca.pem")

# Ein Lauf je Variante, ein Messwert je Anfrage (Offline-Evaluation, Paket Engineering)
with client.run("ENG-12", variant="candidate", name="PR 481", commit=sha,
                params={"stemmer": "v2", "k1": 1.2}) as run:
    for query_id, scores in results.items():
        run.add_row("ndcg_at_10", scores["ndcg"], dims={"query": query_id})
        run.add_row("recall_at_20", scores["recall"], dims={"query": query_id})
    run.log(latency_p95_ms=212.0)                    # ein Wert je Lauf
    run.log(error_rate={"value": 3, "count": 1200})  # Anteil: Fehler von Anfragen

# Auswertungen des Typs ausführen und das Urteil lesen
for e in client.evaluate("ENG-12"):
    print(e.get("evaluator_name"), e.get("metric_key"), e.get("headline"), e.get("verdict"))

# Messwerte ohne Lauf (z. B. Wochenzahlen einer Kampagne)
client.add_measurements("MKT-7", [
    {"metric": "ctr", "variant": "A", "value": 129, "count": 10688, "observed_at": "2026-09-21"},
    {"metric": "ctr", "variant": "B", "value": 175, "count": 10714, "observed_at": "2026-09-21"},
])

# Notiz (wird mit dem Experiment in Knovas indexiert: keine Namen von Personen)
client.add_note("PRD-3", "P4 (Associate, mittelgrosse Kanzlei) fand den Filter erst nach 40 s.",
                kind="interview")
```

## Was der Client kann

| Aufruf | HTTP | Ergebnis |
|---|---|---|
| `ping()` | `GET /api/experiments/v1/ping` | `{"display_name", "roles"}` |
| `experiment(key)` | `GET …/experiments/<KEY>` | Schlüssel, Titel, Status, Bereich, Typ, Varianten, Metriken, `row_version` |
| `create_experiment(domain, type, title, hypothesis="", fields=None)` | `POST …/experiments` | das neue Experiment (Varianten und Metriken aus dem Typ) |
| `log_run(key, *, name, variant, params, metrics, rows, environment, commit, status="finished", started_at, ended_at, note)` | `POST …/experiments/<KEY>/runs` | der Lauf mit `metrics` als Schätzwerten |
| `add_measurements(key, rows)` | `POST …/experiments/<KEY>/measurements` | `{"batch_id", "inserted"}` |
| `add_note(key, body, kind="note")` | `POST …/experiments/<KEY>/notes` | die Notiz |
| `evaluate(key, scope=None)` | `POST …/experiments/<KEY>/pipeline` | Liste der Auswertungen des Typs |
| `evaluations(key, metric=None, *, limit=20)` | `GET …/experiments/<KEY>/evaluations` | die neuesten Auswertungen (ohne Protokoll) |
| `wait_for(key, evaluations, *, timeout=600, interval=5)` | wiederholt `evaluations` | dieselbe Liste, sobald nichts mehr `queued` oder `running` ist |
| `run(key, variant, name, params, commit, *, environment, note)` | – | Kontextmanager, siehe unten |

**Messzeilen** (`rows`, `add_row`) haben die Felder `metric`, `value` und je
nach Messart `count`, `denominator`, `sum_sq`, dazu `variant`, `observed_at`
und `dims` (bis 20 Schlüssel, z. B. `{"query": "q17"}`). Was die Felder je
Messart bedeuten, steht in der Tabelle «Messarten» der
[Dokumentation](../../docs/features/experiments.md#messarten).

**`metrics`** bei einem Lauf: für Mittelwert, Dauer und Geldbetrag genügt eine
Zahl (`latency_p95_ms=212`); die anderen Arten brauchen ein Objekt
(`{"value": 3, "count": 1200}` für einen Anteil, `{"value": 840.0,
"denominator": 12}` für ein Verhältnis).

**`run(...)`** sammelt mit `log(**metrics)`, `add_rows(rows)` und
`add_row(metric, value, ...)` und meldet den Lauf **einmal**, wenn der
`with`-Block endet:

- ohne Fehler mit Status `finished` und allen Messwerten;
- mit einer Ausnahme mit Status `failed` (Strg+C: `cancelled`) und **ohne**
  Messwerte, damit ein halber Benchmark nie in eine Auswertung gerät. Die
  Ausnahme selbst geht unverändert weiter; scheitert zusätzlich die Meldung,
  erscheint das nur als Warnung.

`started_at` und `ended_at` setzt der Kontextmanager selbst.

**Werte**: `datetime` (ohne Zeitzone gilt die lokale Zeit), `date`,
`Decimal`, `UUID`, numpy-Zahlen und -Arrays werden umgewandelt. `NaN` und
unendliche Werte weist der Client vor dem Senden ab und nennt die Stelle
(`rows.3.value`).

**Grenzen**: höchstens 10'000 Zeilen je Aufruf (`log_run` und
`add_measurements`), 32 MB je Anfrage. Grössere Mengen auf mehrere Aufrufe
verteilen – jeder Aufruf ist für sich «alles oder nichts».

## Fehler

Alle Fehler erben von `ExperimentsError` (`.status`, `.message`, `.fields`).
Die Meldungen sind deutsch und kommen, wo es sie gibt, wörtlich von der
Platform.

| Klasse | Wann |
|---|---|
| `ConfigurationError` | Adresse oder Schlüssel fehlen oder sind ungültig, `cafile` nicht lesbar |
| `TransportError` | keine Antwort: Verbindung, DNS, TLS, Zeitüberschreitung |
| `ProtocolError` | Umleitung, HTML statt JSON (falsche Adresse, Anmeldeseite, Proxy) |
| `ValidationError` | Eingabe abgewiesen (HTTP 400/413 oder vor dem Senden); `.fields` nennt die Felder |
| `AuthenticationError` | 401: Schlüssel unbekannt, widerrufen oder abgelaufen, oder das Konto ist gesperrt, deaktiviert, muss sein Passwort ändern oder hat keine Experimente-Rolle mehr |
| `ForbiddenError` | 403: die Rolle reicht nicht |
| `NotFoundError` | 404: das Experiment gibt es nicht |
| `ModuleDisabledError` | 404, weil Experimente auf dieser Platform ausgeschaltet sind (`EXPERIMENTS_ENABLED`) |
| `ConflictError` | 409 |
| `UnavailableError` | 503 |
| `ServerError` | 500 und andere 5xx |
| `WaitTimeout` | `wait_for` hat aufgegeben; `.evaluations` enthält den letzten Stand |

## Sicherheit

- **Umleitungen werden nie befolgt.** Eine Umleitung trüge den Schlüssel
  dorthin, wohin der `Location`-Kopf zeigt. Stattdessen schlägt der Aufruf
  fehl und nennt das Ziel – meist ist `http://` statt `https://` konfiguriert.
- Der Schlüssel steht nur im `Authorization`-Kopf, in keiner Fehlermeldung und
  keiner `repr`. Ein Wert, der nicht wie ein Zugangsschlüssel aussieht (etwa
  ein anderes Token), wird abgewiesen, ohne ihn zu wiederholen.
- TLS wird geprüft. `cafile=` für eine eigene Zertifizierungsstelle (die Datei
  ersetzt dann den Systemspeicher); `SSL_CERT_FILE` wirkt ebenfalls.
  `verify=False` nur auf Testsystemen – es warnt.
- `http://` zu einem anderen Rechner als diesem funktioniert, warnt aber: der
  Schlüssel ginge im Klartext über das Netz.
- Nur `GET` wird nach einem vorübergehenden Fehler (502, 503, 504, Verbindung)
  wiederholt, standardmässig zweimal (`Client(..., retries=2)`). Ein `POST`
  wird nur wiederholt, wenn die Verbindung abgewiesen wurde, die Anfrage also
  sicher nie ankam: die Platform kennt keinen Idempotenzschlüssel, und ein
  doppelt gemeldeter Lauf zählte seine Messwerte doppelt.
- Proxy-Variablen (`HTTPS_PROXY`, `NO_PROXY`) gelten wie bei `urllib`.

## GitHub Actions: Suchqualität je Pull Request

Das Beispiel misst Ausgangsstand und Kandidat auf demselben Anfragesatz, meldet
je einen Lauf mit einer Zeile je Anfrage und Metrik an das Experiment `ENG-12`
(Typ «Offline-Evaluation» aus dem Paket Engineering) und lässt den Job
fehlschlagen, sobald eine Auswertung `worse` meldet – auch eine verletzte
Leitplanke (z. B. Latenz p95 über 250 ms) ergibt `worse`.

Im Repository unter **Settings → Secrets and variables → Actions**:
Secret `KNOVAS_EXPERIMENTS_TOKEN`, Variable `KNOVAS_URL`. Den Schlüssel am
besten für ein eigenes Konto (z. B. `ci-experimente@…` mit nur der Rolle
`experimenter`) anlegen und vor Ablauf ersetzen.

`.github/workflows/search-quality.yml`:

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
    timeout-minutes: 60
    env:
      KNOVAS_URL: ${{ vars.KNOVAS_URL }}
      KNOVAS_EXPERIMENTS_TOKEN: ${{ secrets.KNOVAS_EXPERIMENTS_TOKEN }}
      EXPERIMENT: ENG-12
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install -r requirements.txt

      - name: Verbindung zu Knovas prüfen
        run: python tools/knovas_experiments.py ping

      - name: Ausgangsstand messen
        run: |
          git worktree add ../baseline "${{ github.event.pull_request.base.sha }}"
          python bench/run_queries.py --code ../baseline --queries bench/kanzlei-gold-v3.jsonl --out baseline.jsonl

      - name: Kandidat messen
        run: python bench/run_queries.py --code . --queries bench/kanzlei-gold-v3.jsonl --out candidate.jsonl

      - name: Melden und auswerten
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: python bench/report_to_knovas.py
```

`bench/report_to_knovas.py` (`bench/run_queries.py` schreibt eine JSON-Zeile je
Anfrage mit `query_id`, `ndcg_at_10`, `recall_at_20`, `mrr`, `latency_ms`):

```python
"""Meldet Ausgangsstand und Kandidat an Knovas und scheitert bei «worse»."""

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from knovas_experiments import Client, ExperimentsError  # noqa: E402

KEY = os.environ["EXPERIMENT"]
METRICS = ("ndcg_at_10", "recall_at_20", "mrr")


def p95(values):
    """95. Perzentil nach dem Nearest-Rank-Verfahren."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def report(client, variant, path, commit):
    with open(path, encoding="utf-8") as handle:
        results = [json.loads(line) for line in handle if line.strip()]
    with client.run(KEY, variant=variant, name=f"PR {os.environ['PR_NUMBER']}",
                    commit=commit, params={"queries": "kanzlei-gold-v3"},
                    environment={"runner": os.environ.get("RUNNER_OS", ""),
                                 "run_id": os.environ.get("GITHUB_RUN_ID", "")}) as run:
        for r in results:
            for metric in METRICS:
                run.add_row(metric, r[metric], dims={"query": r["query_id"]})
        run.log(latency_p95_ms=p95([r["latency_ms"] for r in results]))


def main():
    client = Client()
    report(client, "baseline", "baseline.jsonl", os.environ["BASE_SHA"])
    report(client, "candidate", "candidate.jsonl", os.environ["HEAD_SHA"])

    # Eingebaute Auswerter sind fertig, wenn evaluate() zurückkommt; Python- und
    # Julia-Auswerter laufen in der Rechenumgebung, wait_for wartet auf sie.
    evaluations = client.wait_for(KEY, client.evaluate(KEY), timeout=900)

    failed = False
    for e in evaluations:
        verdict = e.get("verdict") or e.get("status")
        print(f"{e.get('evaluator_name', '?')} / {e.get('metric_key', '-')}: "
              f"{e.get('headline', '')} [{verdict}]")
        if e.get("verdict") == "worse" or e.get("status") == "failed":
            failed = True
    print(f"Details: {client.base_url}/experiments/{KEY}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ExperimentsError as exc:
        print(f"Knovas Experimente: {exc}", file=sys.stderr)
        sys.exit(2)
```

Warum das so aufgebaut ist:

- **Eine Zeile je Anfrage** (`dims.query`) statt eines Mittelwerts: der
  gepaarte t-Test des Typs vergleicht Anfrage für Anfrage und erkennt so
  kleine Verbesserungen, die im Rauschen der Mittelwerte untergingen.
- **Scope «neuester Lauf je Variante»** (im Typ voreingestellt): jeder Push
  meldet neue Läufe, ausgewertet wird immer der neueste abgeschlossene Lauf
  jeder Variante. Ein abgebrochener Lauf (`failed`) zählt nicht.
- **Beide Varianten im selben Job**: Ausgangsstand und Kandidat laufen auf
  derselben Maschine gegen denselben Anfragesatz; die Latenz ist so
  vergleichbar.
- Ein Experiment gehört zu **einem** Vorhaben. Für die nächste Änderung ein
  neues Experiment anlegen (in der Oberfläche oder mit
  `client.create_experiment("engineering", "offline_eval", "Titel",
  hypothesis=...)`) und dessen Schlüssel in `EXPERIMENT` setzen.

## Tests

```bash
python -m pytest KnovasPlatform/experiments-sdk/python
```

Die Tests laufen gegen einen kleinen HTTP-Server im Testprozess (auch über TLS
mit einem Wegwerf-Zertifikat, wenn `openssl` vorhanden ist) und brauchen
nichts ausser `pytest`.
