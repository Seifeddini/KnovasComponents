# KnovasExperiments.jl – Julia-Client für Experimente

Meldet Läufe, Messwerte und Notizen aus Julia an das Modul **Experimente** der
Knovas Platform und führt die Auswertungen eines Experiments aus. Gleiche
Schnittstelle (`/api/experiments/v1`) und gleiche Regeln wie der
[Python-Client](../python/README.md); Hintergrund und Messarten:
[Experimente](../../docs/features/experiments.md).

Eine Datei. Braucht [HTTP.jl](https://github.com/JuliaWeb/HTTP.jl) (1.x) und
[JSON3.jl](https://github.com/quinnj/JSON3.jl); Julia 1.9 oder neuer.

Nicht zu verwechseln mit den **Julia-Auswertern**: die laufen in der
Rechenumgebung der Platform (`experiments-runner`) und brauchen diesen Client
nicht. Er ist für Julia-Code ausserhalb der Platform – Benchmarks,
Simulationen, Notebooks –, der Messwerte liefert.

## Einrichten

```julia
using Pkg
Pkg.add(["HTTP", "JSON3"])

include("KnovasExperiments.jl")
using .KnovasExperiments
```

Einen Zugangsschlüssel legt man in der Platform unter **Experimente →
Verwaltung → Zugangsschlüssel** an (beginnt mit `kxp_`, wird einmal
angezeigt). Adresse und Schlüssel kommen als Argumente oder aus
`KNOVAS_URL` und `KNOVAS_EXPERIMENTS_TOKEN`:

```julia
client = Client()                                    # aus der Umgebung
client = Client("https://knovas.example.ch", token)  # ausdrücklich
ping(client)   # Dict("display_name" => "Eva Muster", "roles" => ["experimenter"])
```

Für eine eigene Zertifizierungsstelle `JULIA_SSL_CA_ROOTS_PATH=/pfad/ca.pem`
setzen (HTTP.jl liest sie über NetworkOptions); `Client(...; verify=false)`
schaltet die TLS-Prüfung aus und ist nur für Testsysteme gedacht.

## Beispiele

```julia
# Ein Lauf mit einem Messwert je Anfrage (Offline-Evaluation) und einem Wert je Lauf
rows = [Dict("metric" => "ndcg_at_10", "value" => s, "dims" => Dict("query" => q))
        for (q, s) in ndcg_by_query]
run = log_run(client, "ENG-12"; variant="candidate", name="Nachtlauf",
              commit=readchomp(`git rev-parse HEAD`),
              params=Dict("k1" => 1.2, "b" => 0.75),
              metrics=Dict("latency_p95_ms" => 212.0),
              rows=rows, started_at=t0, ended_at=now(UTC))

# Ein Anteil (Fehler von Anfragen) in einem Feature-Rollout: dort ist die
# Fehlerrate Primärmetrik; die Offline-Evaluation ENG-12 hat sie nicht
log_run(client, "ENG-20"; variant="an", name="Rollout 10 %",
        metrics=Dict("error_rate" => Dict("value" => 3, "count" => 1200)))

# Messwerte ohne Lauf, z. B. aus einem DataFrame (Spalten metric, variant, value, count)
add_measurements(client, "MKT-7", [Dict(pairs(r)) for r in eachrow(df)])

# Notiz (wird mit dem Experiment in Knovas indexiert: keine Namen von Personen)
add_note(client, "PRD-3", "P4 brauchte 40 s bis zum Filter."; kind="observation")

# Auswertungen des Typs ausführen, auf Python-/Julia-Auswerter warten und den
# CI-Job bei «worse» oder einer gescheiterten Auswertung fehlschlagen lassen.
# Geprüft wird die Liste dieses Aufrufs, nicht evaluations(client, "ENG-12"):
# das ist der Verlauf mit den Urteilen früherer Pushes.
evs = wait_for(client, "ENG-12",
               evaluate(client, "ENG-12"; scope=Dict("runs" => "latest")); timeout=900)
for e in evs
    println(get(e, "evaluator_name", "?"), ": ", get(e, "headline", ""), " [",
            something(get(e, "verdict", nothing), get(e, "status", nothing), ""), "]")
end
any(e -> get(e, "verdict", nothing) == "worse" || get(e, "status", nothing) == "failed", evs) && exit(1)
```

Jede Metrik in `metrics` und `rows` muss dem Experiment zugeordnet sein (die
Metriken seines Typs oder unter **Experiment → Metriken** ergänzte). Sonst
weist die Platform den ganzen Aufruf mit HTTP 400 ab («Die Metrik «…» ist
diesem Experiment nicht zugeordnet.», `kind == :validation`) und speichert
nichts.

`scope=Dict("runs" => "latest")` wertet in **jedem** Schritt nur den neuesten
abgeschlossenen Lauf je Variante aus – auch die Beschreibung mit der
Leitplanke (Latenz p95). Ohne diese Angabe gilt der Scope des Typs; bei
Experimenten, die mit einer älteren Typ-Version angelegt wurden, mittelt die
Beschreibung dann über alle Läufe aller Pushes. Eingebaute Auswerter sind
fertig, wenn `evaluate` zurückkommt; Python- und Julia-Auswerter kommen als
`queued` zurück, `wait_for` wartet auf sie.

## Funktionen

| Funktion | HTTP |
|---|---|
| `ping(client)` | `GET /api/experiments/v1/ping` |
| `experiment(client, key)` | `GET …/experiments/<KEY>` |
| `create_experiment(client, domain, type_key, title; hypothesis="", fields=nothing)` | `POST …/experiments` |
| `log_run(client, key; name, variant, params, metrics, rows, environment, commit, status="finished", started_at, ended_at, note)` | `POST …/experiments/<KEY>/runs` |
| `add_measurements(client, key, rows)` | `POST …/experiments/<KEY>/measurements` |
| `add_note(client, key, body; kind="note")` | `POST …/experiments/<KEY>/notes` |
| `evaluate(client, key; scope=nothing)` | `POST …/experiments/<KEY>/pipeline` |
| `evaluations(client, key; metric=nothing, limit=20)` | `GET …/experiments/<KEY>/evaluations` (Verlauf: die neuesten, auch früherer Pushes) |
| `wait_for(client, key, evaluations; timeout=600, interval=5)` | wiederholt `evaluations`, bis keine der übergebenen Auswertungen mehr `queued` oder `running` ist; gibt sie in derselben Reihenfolge zurück |

Ergebnisse sind `Dict{String,Any}` und `Vector{Any}` (wie JSON).

**Werte**: `Dict`, `NamedTuple` (auch `DataFrameRow` über `Dict(pairs(r))`),
Vektoren, Zahlen, Strings, `Symbol`, `missing`/`nothing` (→ `null`), `Date`
und `DateTime`. Ein `DateTime` trägt keine Zeitzone und wird als **UTC**
gesendet (`now(UTC)` verwenden). `NaN` und `Inf` weist der Client vor dem
Senden ab und nennt die Stelle (`rows.3.value`, von 0 gezählt).

**Grenzen**: höchstens 10'000 Zeilen je Aufruf, 32 MB je Anfrage.

## Fehler

Alle Fehler sind `ExperimentsError` mit `kind`, `status` (0 ohne Antwort),
`message` (deutsch, wo vorhanden wörtlich von der Platform) und `fields`:

| `kind` | Wann |
|---|---|
| `:config` | Adresse oder Schlüssel fehlen oder sind ungültig |
| `:transport` | keine Antwort (Verbindung, DNS, TLS, Zeitüberschreitung) |
| `:protocol` | Umleitung oder kein JSON der Platform |
| `:validation` | Eingabe abgewiesen (400, 413 oder vor dem Senden) |
| `:auth` | 401: Schlüssel ungültig, abgelaufen oder widerrufen; Konto gesperrt oder ohne Experimente-Rolle |
| `:forbidden`, `:not_found`, `:conflict`, `:unavailable`, `:server`, `:http` | 403, 404, 409, 503, andere 5xx, übrige |
| `:disabled` | Experimente sind auf dieser Platform ausgeschaltet (`EXPERIMENTS_ENABLED`) |
| `:timeout` | `wait_for` hat aufgegeben: Auswertungen warten noch auf die Rechenumgebung |

```julia
try
    log_run(client, "ENG-12"; variant="candidate", rows=rows)
catch e
    e isa ExperimentsError && e.kind == :validation && foreach(println, e.fields)
    rethrow()
end
```

## Sicherheit

Wie beim Python-Client: Umleitungen werden nie befolgt (der Schlüssel reiste
sonst mit), der Schlüssel erscheint in keiner Fehlermeldung und keiner
Ausgabe von `show`, TLS wird geprüft, `http://` zu einem anderen Rechner warnt,
und nur `GET` wird nach einem Fehler wiederholt – ein wiederholter `POST`
könnte einen Lauf doppelt speichern.

## Tests

```bash
julia --startup-file=no KnovasPlatform/experiments-sdk/julia/test/runtests.jl
```

Die Tests brauchen weder eine Platform noch Netz. Sind HTTP.jl und JSON3.jl
installiert, werden sie geladen; sonst lädt der Test kleine Platzhalter aus
`test/stubs` (die geprüfte Logik spricht kein HTTP). Die CI installiert beide
Pakete und prüft so auch, dass der Client mit ihnen lädt.
