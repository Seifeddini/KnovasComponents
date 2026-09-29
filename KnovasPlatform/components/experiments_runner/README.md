# experiments-runner

The sandbox in which user-written **Python and Julia evaluators** of the
Experiments module (UI: *Experimente*) run. Experiments managers can write
such evaluators in the Platform; the Platform never executes that code
itself. It sends the code and the evaluator input to this service and stores
what comes back. Built-in evaluators run in the Platform and do not need the
runner.

Plan: [`docs/superpowers/plans/2026-09-28-experiments-module.md`](../../../docs/superpowers/plans/2026-09-28-experiments-module.md), section 15.

```
docbridge-web (Platform)                      experiments-runner (network_mode: none)
  experiments.runner_client  --HTTP over-->   runner.py  (PID 1, uid 10001)
                             unix socket        |- job: python3 -I harness.py <job dir>
  /run/experiments-runner (ro)  <- tmpfs vol -> |- job: julia ... harness.jl <job dir>
```

## Switching it on

In `knovas.env`, then `./scripts/setup.sh && ./scripts/start.sh`:

```
COMPOSE_PROFILES=experiments
EXPERIMENTS_RUNNER_URL=unix:///run/experiments-runner/runner.sock
```

Optional: `EXPERIMENTS_RUNNER_MEMORY` (default `3g`) and `EXPERIMENTS_RUNNER_CPUS`
(default `2`) for the container, `EXPERIMENTS_RUNNER_MAX_CONCURRENT` (default 2)
for jobs at once, `EXPERIMENTS_RUNNER_TIMEOUT` (default 90 s) for the time limit
of one evaluation -- the Platform asks for it and the runner caps every job at
it. `./scripts/doctor.sh` reports whether the Platform reaches the runner.

The first build takes several minutes: it installs and precompiles the Julia
packages.

## Writing an evaluator

The code defines `evaluate(data)` and returns an object; input and output
follow the evaluator contract in the plan (section 7). The Platform passes
the result through `sanitize_output`, so unknown keys and oversized values
are handled there.

Python (`numpy`, `scipy`, `pandas`, `statsmodels`, pinned in `requirements.txt`):

```python
import numpy as np

def evaluate(data):
    values = [row["value"] for row in data["rows"]]
    return {"verdict": "n/a", "headline": "Mittel %.2f" % np.mean(values),
            "values": {"mean": np.mean(values)}}
```

numpy scalars and arrays are converted; NaN and Infinity become `null`.

Julia (`JSON3`, `Distributions`, `HypothesisTests`, `StatsBase`, `DataFrames`):

```julia
using Distributions

function evaluate(data)           # data::Dict{String,Any}
    n = length(data["rows"])
    Dict("verdict" => "n/a", "headline" => "n = $n",
         "values" => Dict("p" => cdf(Normal(), 0.0)))
end
```

The harness reads and writes the protocol with its own small JSON reader and
writer, so no package is needed for it. A `Dict`, `NamedTuple`, vectors,
tuples, matrices (as nested lists), numbers, strings, `Symbol`, `nothing` and
`missing` are written; anything else is refused.

What the code prints goes to the log the Platform shows under *Protokoll*
(at most 64 KB: the start and the end are kept). An exception gives the
traceback there.

| Outcome | `error` |
|---|---|
| exception (also while loading the code) | Der Auswerter ist mit einem Fehler abgebrochen. |
| wall-clock limit reached | Zeitlimit überschritten. |
| no `evaluate` | Der Code definiert keine Funktion evaluate(data). |
| result is not an object | Der Auswerter hat kein Objekt zurückgegeben. |
| result has no JSON form | Das Ergebnis des Auswerters lässt sich nicht als JSON schreiben. |
| output larger than 8 MB | Die Ausgabe des Auswerters ist grösser als 8 MB. |
| output planted or malformed | Der Auswerter hat keine gültige Ausgabe hinterlassen. |
| process ended without output | Der Auswerter hat kein Ergebnis geliefert. |
| killed by a CPU, memory or file-size limit | Der Auswerter hat ein Rechenzeit- oder Speicherlimit überschritten. |
| other signal | Der Auswerter ist abgestürzt. |
| language not installed | Julia ist in dieser Rechenumgebung nicht verfügbar. |

## Protocol

Plain HTTP/1.0 over the unix socket (`RUNNER_LISTEN`), JSON bodies.

* `GET /health` -> `{"ok", "languages": {"python": "3.11.2 (numpy ...)", "julia": "1.11.9 (JSON3 ...)"}, "busy", "max_concurrent", "version"}`.
  `ok` is false without any interpreter or with less than 256 MB free job space.
* `POST /v1/run` `{"language": "python"|"julia", "code" (<= 200000 chars), "data" (object), "timeout_seconds"}`
  (body <= 64 MB, `Content-Length` required) -> 200
  `{"ok", "output", "error", "logs", "duration_ms"}`. A timeout above
  `RUNNER_MAX_SECONDS` is lowered to it.
* 400 malformed request, 411 no `Content-Length`, 413 body over 64 MB,
  503 when all `RUNNER_MAX_CONCURRENT` slots stay busy for 10 s or the job
  space has less than 256 MB free (nothing ran; the Platform retries later).

## Security model

Evaluator code is written by experiments managers, runs with the data of
experiments, and must not reach anything else. The layers, outside in:

**Container** (`docker-compose.yml`): `network_mode: none` (no interface but
loopback -- the code can reach neither the Platform, nor Knovas, nor the
internet, and nothing can reach it), non-root uid 10001, read-only root
filesystem, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit: 256`,
memory and CPU limits, `/tmp` a 1 GB tmpfs, no secret, no `env_file`, no
volume but the socket. The socket volume is a 1 MB tmpfs owned by uid 10001,
so code in the sandbox cannot fill the host disk through it; docbridge-web
mounts it read-only (connecting to a socket needs no write access).

**Server** (`runner.py`, standard library only): PID 1 of the container and a
child subreaper, so every process a job starts stays its descendant however it
detaches; non-dumpable, so jobs (same uid) cannot ptrace it or read its memory.
It exits -- and Docker restarts the container, ending every process in it --
when the socket file disappears or is replaced (a job could otherwise unlink
it and listen in its place), and when the job space stays full while no job
runs. At most 32 connections, and at most `RUNNER_MAX_CONCURRENT + 2` request
bodies in memory at once.

**Per job:**

* its own process in its own session (`start_new_session`), started with
  `python3 -I harness.py` or `julia --startup-file=no --history-file=no
  --heap-size-hint=1G harness.jl`;
* rlimits before exec: CPU = time limit + 5 s (SIGXCPU, then SIGKILL a second
  later), address space 1.5 GB (Python) or 6 GB (Julia, which reserves a large
  address range at start-up; real memory is bounded by the container limit),
  file size 64 MB, 256 open files, 128 processes, no core files;
  `oom_score_adj` 1000, so under memory pressure the kernel kills a job, not
  the server;
* an environment with nothing inherited but `PATH`: `HOME` and `TMPDIR` in the
  job directory, `LANG=C.UTF-8`, one BLAS/OpenMP/Julia thread,
  `JULIA_DEPOT_PATH=<job dir>/depot:/opt/julia-depot:` (anything Julia writes
  lands in the job's own depot; the packages come from the read-only depot's
  default environment through `JULIA_LOAD_PATH=@:@v#.#:@stdlib`);
* a fresh 0700 directory under `/tmp` with `code` and `input.json`, which the
  harness deletes before any user code runs;
* at the time limit, SIGKILL to its process group and the process itself;
  after every job, SIGKILL to every descendant of the server whose session is
  neither the server's nor a still running job's -- a daemon that
  double-forked, called `setsid` or changed its process group dies with its
  job. Kills go through a pidfd after re-checking the process's start time,
  so a reused pid is never hit;
* stdout and stderr captured together, at most 64 KB kept (head and tail),
  NUL bytes and lone surrogates replaced (PostgreSQL accepts neither);
* `output.json` opened relative to the job directory's own descriptor with
  `O_NOFOLLOW|O_NONBLOCK`, accepted only as a regular file of at most 8 MB
  holding the harness envelope -- a planted symlink, FIFO or directory is
  refused, never followed or waited on;
* the job directory removed afterwards.

**Accepted risks.**

* All jobs run under the same uid. A job can see and alter the files of a job
  running at the same time, and signal its processes; the harness makes each
  job non-dumpable (no ptrace, no `/proc/<pid>/mem`) and deletes its input
  before user code runs, which narrows but does not close this. The authors
  are experiments managers, who can read every experiment in the module
  anyway, and with no network the only way out for data is the job's own
  result.
* A job can send the server SIGTERM (a restart of the container: running
  evaluations fail and are retried by the Platform) and can use its time and
  memory limits to the full. That is a denial of service of the runner, not of
  the Platform.
* `RLIMIT_NPROC` counts every process of uid 10001 on the host, including
  RemoteController's, which runs under the same uid. 128 leaves ample room in
  practice; the container's `pids_limit` is the real bound.
* `RUNNER_LISTEN=tcp:...` (development) has no authentication at all: anyone
  who reaches the port can run code. Never publish it.

## Operations

* Health: `docker compose --env-file knovas.env --profile experiments ps experiments-runner`
  (the healthcheck probes `/health` over the socket with
  `runner.py --healthcheck`).
* Smoke test of an image, with the settings compose applies:
  `docker run --rm --network none --read-only --tmpfs /tmp:size=1g,mode=1777 --cap-drop ALL
  --security-opt no-new-privileges:true knovas-experiments-runner:0.1.0
  /opt/venv/bin/python3 -I /app/runner.py --self-test` runs one evaluator per
  language and fails if the precompiled Julia packages are not used (every job
  would then compile for minutes).
* The log lists each job's language, duration and outcome, and the processes
  a job left behind. It never contains code, data or results.
* The image compiles Julia packages for the CPU of the machine that builds
  it. To run it elsewhere, build with
  `--build-arg JULIA_CPU_TARGET="generic;sandybridge,-xsaveopt,clone_all;haswell,-rdrnd,base(1)"`.

Environment of the server (compose sets the first two):

| Variable | Default | |
|---|---|---|
| `RUNNER_MAX_CONCURRENT` | 2 | jobs at once (1-16) |
| `RUNNER_MAX_SECONDS` | 600 (compose: `EXPERIMENTS_RUNNER_TIMEOUT`, 90) | cap on a job's time limit (5-3600) |
| `RUNNER_LISTEN` | `unix:/run/experiments-runner/runner.sock` | or `tcp:127.0.0.1:8090` for development |
| `RUNNER_TMP_DIR` | `/tmp` | where job directories are made |
| `RUNNER_PYTHON` | the server's interpreter | Python for jobs |
| `RUNNER_JULIA` | `julia` on `PATH` | Julia for jobs |
| `RUNNER_JULIA_DEPOT` | `/opt/julia-depot` | read-only depot with the packages |
| `RUNNER_MIN_FREE_MB` | 256 | job space below which jobs are refused |
| `RUNNER_SLOT_WAIT_SECONDS` | 10 | how long a request waits for a free slot before 503 |

## Development

```
cd KnovasPlatform/components/experiments_runner
pip install pytest -r requirements.txt     # numpy etc. only for the numpy tests
pytest
```

The tests start the real server on a unix socket in a subprocess and run
jobs through it. Julia tests skip without `julia` on `PATH`, numpy tests
without numpy; `RUNNER_TESTS_REQUIRE_ALL=true` (set in CI) turns a skip into a
failure. `tests/test_runner_platform_client.py` runs the Platform's own
`RunnerClient` against the server when `../docbridge_integration/src` is
present.
