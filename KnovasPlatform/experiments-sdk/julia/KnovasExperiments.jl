"""
    KnovasExperiments

Julia client for the machine API of the Knovas Platform's Experiments module
("Experimente"): report runs, measurements and notes, run the evaluations of
an experiment's type and read the verdicts back.

```julia
include("KnovasExperiments.jl")
using .KnovasExperiments

client = Client("https://knovas.example.ch", ENV["KNOVAS_EXPERIMENTS_TOKEN"])
log_run(client, "ENG-12"; variant="candidate", commit=sha,
        rows=[Dict("metric" => "ndcg_at_10", "value" => s, "dims" => Dict("query" => q))
              for (q, s) in scores])
for e in evaluate(client, "ENG-12")
    println(e["headline"], " -> ", e["verdict"])
end
```

Needs HTTP.jl and JSON3.jl. The API is `/api/experiments/v1`, authenticated
with a personal access token (`kxp_...`, created under Experimente ->
Verwaltung -> Zugangsschluessel) that acts as its owner.

Deliberate choices, the same as in the Python SDK: redirects are never
followed (the token would travel along), the token appears in no error
message and no `show` output, TLS is verified unless `verify=false`, and
only GET requests are retried (a repeated POST would store a run twice).
Error messages are German, like every message the Platform returns.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (sections 10, 16)
"""
module KnovasExperiments

using Dates
using HTTP
using JSON3

export Client, ExperimentsError, ping, experiment, create_experiment, log_run,
       add_measurements, add_note, evaluate, evaluations, wait_for

const SDK_VERSION = "1.0.0"
const API_PREFIX = "/api/experiments/v1"
const ENV_URL = "KNOVAS_URL"
const ENV_TOKEN = "KNOVAS_EXPERIMENTS_TOKEN"
const TOKEN_RE = r"^kxp_[A-Za-z0-9_-]{16,200}$"
const KEY_RE = r"^[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}$"
const RUN_STATUSES = ("finished", "failed", "cancelled")
const MAX_EVALUATIONS_LIMIT = 60
# Evaluations that still wait for the runner (Python and Julia evaluators).
const PENDING_EVALUATION_STATUSES = ("queued", "running")
# The Platform's answer on every module path while EXPERIMENTS_ENABLED is off.
const MSG_SWITCHED_OFF = "Experimente sind nicht eingeschaltet."

"""
    ExperimentsError(kind, status, message, fields)

Every error this client throws. `kind` says what went wrong:

- `:config` the client cannot be used as configured (address, token)
- `:transport` no answer (connection, DNS, TLS, timeout); `status == 0`
- `:protocol` an answer that is not the Platform's JSON (a redirect, a page)
- `:validation` input refused (HTTP 400/413, or before sending)
- `:auth` 401, `:forbidden` 403, `:not_found` 404, `:conflict` 409,
  `:unavailable` 503, `:server` other 5xx, `:http` any other status
- `:disabled` 404 because the module is switched off on that Platform
- `:timeout` `wait_for` gave up while evaluations were still queued or running

`fields` holds per-field messages of a refused input.
"""
struct ExperimentsError <: Exception
    kind::Symbol
    status::Int
    message::String
    fields::Dict{String,String}
end

ExperimentsError(kind::Symbol, message::AbstractString; status::Integer=0,
                 fields=Dict{String,String}()) =
    ExperimentsError(kind, Int(status), String(message),
                     Dict{String,String}(string(k) => string(v) for (k, v) in fields))

function Base.showerror(io::IO, e::ExperimentsError)
    print(io, "ExperimentsError(", e.kind, "): ", e.message)
    e.status != 0 && print(io, " (HTTP ", e.status, ")")
    if !isempty(e.fields)
        items = sort!(collect(e.fields); by=first)
        print(io, " [", join(("$(k): $(v)" for (k, v) in items), "; "), "]")
    end
end

"""
    Client(base_url=ENV["KNOVAS_URL"], token=ENV["KNOVAS_EXPERIMENTS_TOKEN"];
           timeout=30, verify=true)

`base_url` is the Platform's address as people open it in the browser.
For a Platform behind a private CA set `JULIA_SSL_CA_ROOTS_PATH` to the CA
file; `verify=false` switches TLS verification off (test systems only).
"""
struct Client
    base_url::String
    token::String
    timeout::Int
    verify::Bool

    function Client(base_url::AbstractString=get(ENV, ENV_URL, ""),
                    token::AbstractString=get(ENV, ENV_TOKEN, "");
                    timeout::Real=30, verify::Bool=true)
        url = _check_url(base_url)
        tok = _check_token(token)
        (isfinite(timeout) && timeout > 0) ||
            throw(ExperimentsError(:config, "timeout muss eine positive Zahl (Sekunden) sein."))
        if startswith(url, "http://") && !_is_loopback(url)
            @warn "$(url) ist unverschl\u00fcsselt (http): der Zugangsschl\u00fcssel geht im Klartext \u00fcber das Netz. Bitte https verwenden."
        end
        verify || @warn "TLS-Pr\u00fcfung ausgeschaltet (verify=false): nur f\u00fcr Testsysteme."
        return new(url, tok, max(1, ceil(Int, timeout)), verify)
    end
end

Base.show(io::IO, c::Client) =
    print(io, "KnovasExperiments.Client(\"", c.base_url, "\", token=\"kxp_...\")")

# -- configuration --------------------------------------------------------------

function _check_url(raw::AbstractString)
    text = strip(String(raw))
    isempty(text) && throw(ExperimentsError(:config,
        "Keine Adresse der Knovas Platform: base_url angeben oder $(ENV_URL) setzen."))
    any(c -> isspace(c) || iscntrl(c), text) && throw(ExperimentsError(:config,
        "Die Adresse enth\u00e4lt Leer- oder Steuerzeichen."))
    uri = try
        HTTP.URI(text)
    catch
        throw(ExperimentsError(:config, "$(repr(text)) ist keine http(s)-Adresse."))
    end
    (uri.scheme in ("http", "https") && !isempty(uri.host)) ||
        throw(ExperimentsError(:config,
            "$(repr(text)) ist keine http(s)-Adresse (erwartet z. B. https://knovas.example.ch)."))
    isempty(uri.userinfo) || throw(ExperimentsError(:config,
        "Die Adresse darf keine Zugangsdaten enthalten; der Zugangsschl\u00fcssel geh\u00f6rt in token."))
    (isempty(uri.query) && isempty(uri.fragment) && !occursin('#', text)) ||
        throw(ExperimentsError(:config, "Die Adresse darf weder ? noch # enthalten."))
    base = rstrip(text, '/')
    if endswith(base, API_PREFIX)
        base = rstrip(base[1:end-length(API_PREFIX)], '/')
    end
    return String(base)
end

function _check_token(raw::AbstractString)
    text = strip(String(raw))
    isempty(text) && throw(ExperimentsError(:config,
        "Kein Zugangsschl\u00fcssel: token angeben oder $(ENV_TOKEN) setzen " *
        "(Experimente -> Verwaltung -> Zugangsschl\u00fcssel)."))
    # The value itself is never repeated: it may be some other secret.
    occursin(TOKEN_RE, text) || throw(ExperimentsError(:config,
        "Das ist kein Zugangsschl\u00fcssel f\u00fcr Experimente (sie beginnen mit kxp_ " *
        "und enthalten nur Buchstaben, Ziffern, - und _)."))
    return String(text)
end

function _is_loopback(url::AbstractString)
    host = lowercase(HTTP.URI(url).host)
    return host == "localhost" || endswith(host, ".localhost") || host == "::1" ||
           host == "[::1]" || startswith(host, "127.")
end

function _key(key)
    text = uppercase(strip(string(key)))
    occursin(KEY_RE, text) || throw(ExperimentsError(:validation,
        "$(repr(string(key))) ist kein Experiment-Schl\u00fcssel (erwartet z. B. ENG-12)."))
    return text
end

# -- JSON ------------------------------------------------------------------------

_join(path::AbstractString, k) = isempty(path) ? string(k) : string(path, ".", k)

_non_finite(path) = ExperimentsError(:validation,
    "$(isempty(path) ? "Wert" : path): Der Wert muss eine endliche Zahl sein (NaN oder unendlich).";
    fields=Dict((isempty(path) ? "Wert" : path) => "Der Wert muss eine endliche Zahl sein."))

# Plain JSON values from what Julia code hands over: numbers are checked for
# NaN and infinity, dates become ISO strings (a DateTime is read as UTC,
# because it carries no zone), Symbols and NamedTuples become strings and
# objects, missing becomes null.
_prepare(x::Nothing, path) = nothing
_prepare(x::Missing, path) = nothing
_prepare(x::Bool, path) = x
_prepare(x::Integer, path) = x
function _prepare(x::AbstractFloat, path)
    isfinite(x) || throw(_non_finite(path))
    return Float64(x)
end
_prepare(x::Real, path) = _prepare(float(x), path)
_prepare(x::AbstractString, path) = String(x)
_prepare(x::Symbol, path) = String(x)
_prepare(x::DateTime, path) = string(x) * "Z"
_prepare(x::Date, path) = string(x)
_prepare(x::AbstractDict, path) =
    Dict{String,Any}(string(k) => _prepare(v, _join(path, k)) for (k, v) in x)
_prepare(x::NamedTuple, path) =
    Dict{String,Any}(string(k) => _prepare(v, _join(path, k)) for (k, v) in pairs(x))
_prepare(x::Union{AbstractVector,Tuple}, path) =
    Any[_prepare(v, _join(path, i - 1)) for (i, v) in enumerate(x)]
_prepare(x, path) = throw(ExperimentsError(:validation,
    "$(isempty(path) ? "Wert" : path): $(typeof(x)) ist nicht als JSON darstellbar."))

# JSON3 objects and arrays as Dict{String,Any} and Vector{Any}, so results
# read like the Python SDK's: e["headline"].
_plain(x::JSON3.Object) = Dict{String,Any}(string(k) => _plain(v) for (k, v) in pairs(x))
_plain(x::JSON3.Array) = Any[_plain(v) for v in x]
_plain(x) = x

function _mapping(x, name)
    (x isa AbstractDict || x isa NamedTuple) ||
        throw(ExperimentsError(:validation, "$(name) muss ein Objekt (Dict) sein."))
    return x
end

function _rows(rows, name="rows")
    (rows isa AbstractVector || rows isa Tuple) ||
        throw(ExperimentsError(:validation, "$(name) muss eine Liste von Zeilen (Dicts) sein."))
    for (i, row) in enumerate(rows)
        (row isa AbstractDict || row isa NamedTuple) ||
            throw(ExperimentsError(:validation, "$(name).$(i - 1) ist keine Zeile (Dict)."))
    end
    return rows
end

# -- transport ---------------------------------------------------------------------

function _transport_text(err)
    timeout_type = isdefined(HTTP.Exceptions, :TimeoutError) ? HTTP.Exceptions.TimeoutError : Nothing
    inner = hasproperty(err, :error) ? getproperty(err, :error) : nothing
    if err isa timeout_type || inner isa timeout_type
        return "Zeit\u00fcberschreitung"
    end
    # Type names only: some HTTP.jl errors print the request, and the
    # request holds the token.
    name = string(nameof(typeof(err)))
    return inner isa Exception ? string(name, ": ", nameof(typeof(inner))) : name
end

function _status_error(status::Int, message::AbstractString, fields)
    if status == 404 && message == MSG_SWITCHED_OFF
        return ExperimentsError(:disabled,
            "Experimente sind auf dieser Knovas Platform nicht eingeschaltet " *
            "(EXPERIMENTS_ENABLED). Bitte die Betreiber fragen oder die Adresse pr\u00fcfen.";
            status=status)
    end
    kind = status in (400, 413) ? :validation :
           status == 401 ? :auth :
           status == 403 ? :forbidden :
           status == 404 ? :not_found :
           status == 409 ? :conflict :
           status == 503 ? :unavailable :
           status >= 500 ? :server : :http
    if isempty(message)
        message = status == 401 ? "Ung\u00fcltiger oder abgelaufener Zugangsschl\u00fcssel." :
                  status == 404 ? "Nicht gefunden. Stimmt die Adresse, und hat diese Platform das Modul Experimente?" :
                  status == 413 ? "Die Anfrage ist zu gross. Bitte die Zeilen auf mehrere Aufrufe verteilen." :
                  "Die Platform hat mit einem Fehler geantwortet."
    end
    return ExperimentsError(kind, message; status=status, fields=fields)
end

function _interpret(resp, key::String)
    status = Int(resp.status)
    if 300 <= status < 400
        location = HTTP.header(resp, "Location", "")
        target = isempty(location) ? "eine andere Adresse" : location
        throw(ExperimentsError(:protocol,
            "Die Platform leitet auf $(target) um; der Client folgt Umleitungen nicht, damit " *
            "der Zugangsschl\u00fcssel bei der Platform bleibt. Stimmt die Adresse (https statt http, Pfad)?";
            status=status))
    end
    payload = nothing
    if !isempty(resp.body)
        payload = try
            _plain(JSON3.read(resp.body))
        catch
            nothing
        end
    end
    if !(payload isa Dict{String,Any})
        status >= 400 && throw(_status_error(status, "", Dict{String,String}()))
        throw(ExperimentsError(:protocol,
            "Die Antwort ist kein JSON der Knovas Platform. Stimmt die Adresse?"; status=status))
    end
    success = get(payload, "success", nothing)
    if status >= 400 || success === false
        message = strip(string(something(get(payload, "error", nothing), "")))
        raw_fields = get(payload, "fields", nothing)
        fields = raw_fields isa AbstractDict ? raw_fields : Dict{String,String}()
        status < 400 && throw(ExperimentsError(:protocol,
            isempty(message) ? "Die Platform meldet einen Fehler." : message;
            status=status, fields=fields))
        throw(_status_error(status, message, fields))
    end
    (success === true && haskey(payload, key)) || throw(ExperimentsError(:protocol,
        "Unerwartete Antwort der Platform (ohne \u00ab$(key)\u00bb)."; status=status))
    return payload[key]
end

function _request(c::Client, method::String, path::String; body=nothing, query=nothing,
                  key::String)
    url = c.base_url * API_PREFIX * path
    if query !== nothing
        parts = [HTTP.URIs.escapeuri(string(k)) * "=" * HTTP.URIs.escapeuri(string(v))
                 for (k, v) in query if v !== nothing]
        isempty(parts) || (url *= "?" * join(parts, "&"))
    end
    headers = Pair{String,String}[
        "Authorization" => "Bearer " * c.token,
        "Accept" => "application/json",
        "User-Agent" => "knovas-experiments-julia/" * SDK_VERSION,
    ]
    payload = UInt8[]
    if body !== nothing
        text = try
            JSON3.write(_prepare(body, ""))
        catch err
            err isa ExperimentsError && rethrow()
            throw(ExperimentsError(:validation, "Nicht als JSON darstellbar: $(nameof(typeof(err)))."))
        end
        payload = Vector{UInt8}(codeunits(text))
        push!(headers, "Content-Type" => "application/json; charset=utf-8")
    end
    resp = try
        HTTP.request(method, url, headers, payload;
                     redirect=false, status_exception=false,
                     retry=(method == "GET"), retries=2,
                     readtimeout=c.timeout, connect_timeout=min(c.timeout, 30),
                     require_ssl_verification=c.verify)
    catch err
        err isa ExperimentsError && rethrow()
        throw(ExperimentsError(:transport,
            "Keine Antwort von $(c.base_url): $(_transport_text(err))."))
    end
    return _interpret(resp, key)
end

_path(key) = "/experiments/" * _key(key)

# -- API ----------------------------------------------------------------------------

"""
    ping(client) -> Dict

Who the token acts as: `Dict("display_name" => ..., "roles" => [...])`.
"""
ping(c::Client) = _request(c, "GET", "/ping"; key="user")

"""
    experiment(client, key) -> Dict

Key, title, status, domain, type, variants, metrics and row_version.
"""
experiment(c::Client, key) = _request(c, "GET", _path(key); key="experiment")

"""
    create_experiment(client, domain, type_key, title; hypothesis="", fields=nothing) -> Dict

Creates an experiment of that type in that domain (both keys); variants and
metrics come from the type. The result's `"key"` is what the other calls take.
"""
function create_experiment(c::Client, domain::AbstractString, type_key::AbstractString,
                           title::AbstractString; hypothesis::AbstractString="", fields=nothing)
    body = Dict{String,Any}("domain" => domain, "type" => type_key, "title" => title,
                            "hypothesis" => hypothesis)
    fields === nothing || (body["fields"] = _mapping(fields, "fields"))
    return _request(c, "POST", "/experiments"; body=body, key="experiment")
end

"""
    log_run(client, key; name, variant, params, metrics, rows, environment, commit,
            status="finished", started_at, ended_at, note) -> Dict

Reports one run with its measurements in one transaction. `metrics` maps a
metric key to a number (mean, duration, currency) or to a Dict with
`value`, `count`, `denominator`, `sum_sq`; `rows` are measurement rows
(`Dict("metric" => "ndcg_at_10", "value" => 0.52, "dims" => Dict("query" => "q17"))`)
whose variant defaults to the run's. `status` is finished, failed or cancelled.
"""
function log_run(c::Client, key; name=nothing, variant=nothing, params=nothing,
                 metrics=nothing, rows=nothing, environment=nothing, commit=nothing,
                 status::AbstractString="finished", started_at=nothing, ended_at=nothing,
                 note=nothing)
    path = _path(key) * "/runs"
    status in RUN_STATUSES || throw(ExperimentsError(:validation,
        "status muss einer von $(join(RUN_STATUSES, ", ")) sein, nicht $(repr(status))."))
    body = Dict{String,Any}("status" => status)
    name === nothing || (body["name"] = string(name))
    variant === nothing || (body["variant"] = string(variant))
    params === nothing || (body["params"] = _mapping(params, "params"))
    environment === nothing || (body["environment"] = _mapping(environment, "environment"))
    commit === nothing || (body["commit"] = string(commit))
    started_at === nothing || (body["started_at"] = started_at)
    ended_at === nothing || (body["ended_at"] = ended_at)
    if metrics !== nothing && !isempty(_mapping(metrics, "metrics"))
        body["metrics"] = metrics
    end
    if rows !== nothing && !isempty(_rows(rows))
        body["rows"] = rows
    end
    if note !== nothing && !isempty(strip(string(note)))
        body["note"] = string(note)
    end
    return _request(c, "POST", path; body=body, key="run")
end

"""
    add_measurements(client, key, rows) -> Dict

Adds measurement rows without a run; all or nothing. Returns
`Dict("batch_id" => ..., "inserted" => n)`. At most 10'000 rows per call.
"""
function add_measurements(c::Client, key, rows)
    path = _path(key) * "/measurements"
    isempty(_rows(rows)) && throw(ExperimentsError(:validation,
        "rows ist leer: keine Messwerte zum Senden."))
    return _request(c, "POST", path; body=Dict{String,Any}("rows" => rows), key="result")
end

"""
    add_note(client, key, body; kind="note") -> Dict

Adds a note (kind note, observation, interview or feedback). Notes are
indexed into Knovas: write no names of interviewees or customers into them.
"""
function add_note(c::Client, key, body::AbstractString; kind::AbstractString="note")
    path = _path(key) * "/notes"
    isempty(strip(body)) && throw(ExperimentsError(:validation, "Die Notiz ist leer."))
    return _request(c, "POST", path; body=Dict{String,Any}("body" => body, "kind" => kind),
                    key="note")
end

"""
    evaluate(client, key; scope=nothing) -> Vector

Runs the evaluations the experiment's type defines. Built-in evaluators are
done when the call returns; Python and Julia evaluators are queued (wait for
them with `wait_for`). `scope` overrides the scope of every step of the type,
e.g. `Dict("runs" => "latest")`.
"""
function evaluate(c::Client, key; scope=nothing)
    path = _path(key) * "/pipeline"
    body = Dict{String,Any}()
    scope === nothing || (body["scope"] = _mapping(scope, "scope"))
    result = _request(c, "POST", path; body=body, key="evaluations")
    return result === nothing ? Any[] : result
end

"""
    evaluations(client, key; metric=nothing, limit=20) -> Vector

The newest evaluations (without logs), optionally of one metric.
"""
function evaluations(c::Client, key; metric=nothing, limit::Integer=20)
    path = _path(key) * "/evaluations"
    query = ["metric" => metric, "limit" => clamp(limit, 1, MAX_EVALUATIONS_LIMIT)]
    result = _request(c, "GET", path; query=query, key="evaluations")
    return result === nothing ? Any[] : result
end

"""
    wait_for(client, key, evaluations; timeout=600, interval=5) -> Vector

Polls until none of `evaluations` -- what `evaluate` returned -- is queued or
running any more, and returns them in the same order with the current state
of each. Entries without an id (a step that was skipped) come back unchanged.
Throws `ExperimentsError(:timeout, ...)` after `timeout` seconds.

A CI gate judges this list, the evaluations of this push, and not
`evaluations(client, key)`: that one is the experiment's history and still
holds the verdicts of earlier pushes.
"""
function wait_for(c::Client, key, evs; timeout::Real=600, interval::Real=5)
    k = _key(key)
    return _wait_for(() -> evaluations(c, k; limit=MAX_EVALUATIONS_LIMIT), evs;
                     timeout=timeout, interval=interval)
end

_has_id(e) = e isa AbstractDict && !isempty(string(something(get(e, "id", nothing), "")))
_is_pending(e) = _has_id(e) && get(e, "status", nothing) in PENDING_EVALUATION_STATUSES
_seconds(t::Real) = isinteger(t) ? string(Int(t)) : string(t)

# The polling itself, apart from HTTP: `fetch()` returns the experiment's
# newest evaluations; `pause` and `clock` are replaceable for tests.
function _wait_for(fetch, evs; timeout::Real=600, interval::Real=5, pause=sleep, clock=time)
    (isfinite(timeout) && isfinite(interval)) || throw(ExperimentsError(:validation,
        "timeout und interval m\u00fcssen endliche Zahlen (Sekunden) sein."))
    current = Any[e isa AbstractDict ? Dict{String,Any}(string(k) => v for (k, v) in e) : e
                  for e in evs]
    deadline = clock() + max(0.0, Float64(timeout))
    step = max(0.2, Float64(interval))
    while true
        waiting = count(_is_pending, current)
        waiting == 0 && return current
        clock() >= deadline && throw(ExperimentsError(:timeout,
            "$(waiting) Auswertung(en) nach $(_seconds(timeout)) s noch nicht fertig."))
        pause(step)
        latest = Dict{String,Any}()
        for e in fetch()
            _has_id(e) && (latest[string(e["id"])] = e)
        end
        current = Any[_has_id(e) ? get(latest, string(e["id"]), e) : e for e in current]
    end
end

end # module
