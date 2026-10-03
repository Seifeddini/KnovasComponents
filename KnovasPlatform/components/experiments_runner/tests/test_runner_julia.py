"""Julia jobs end to end: the harness's JSON protocol, errors, limits, depots."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

import runner as R
from conftest import needs_julia, wait_dead, wait_for_file

pytestmark = needs_julia


def test_health_reports_julia(jl_runner):
    health = jl_runner.health()
    assert health["ok"] is True
    assert health["languages"]["julia"].startswith("1.")


def test_julia_happy_path_and_data_types(jl_runner):
    code = (
        'function evaluate(data)\n'
        '    println("rows: ", length(data["rows"]))\n'
        '    vals = [r["value"] for r in data["rows"]]\n'
        '    Dict("type" => string(typeof(data)),\n'
        '         "row_type" => string(typeof(data["rows"])),\n'
        '         "mean" => sum(vals) / length(vals),\n'
        '         "int_type" => string(typeof(data["n"])),\n'
        '         "float_type" => string(typeof(data["f"])),\n'
        '         "null" => data["missing"] === nothing,\n'
        '         "text" => data["text"],\n'
        '         "escapes" => data["escapes"],\n'
        '         "nested" => data["nested"])\n'
        'end\n'
    )
    data = {
        "rows": [{"value": 1.0}, {"value": 2}, {"value": 4.5}],
        "n": 12, "f": 1e-7, "missing": None,
        "text": "Gr\u00fcsse \u00e0 Z\u00fcrich \U0001F600",
        "escapes": "quote \" backslash \\ slash / tab \t newline \n ctrl \u0001",
        "nested": {"empty_list": [], "empty_obj": {}, "bools": [True, False],
                   "big": 12345678901234567890, "neg": -0.5, "exp": 2.5e+300},
    }
    result = jl_runner.run("julia", code, data)
    assert result["ok"] is True, result
    out = result["output"]
    assert out["type"] == "Dict{String, Any}"
    assert out["row_type"] == "Vector{Any}"
    assert out["mean"] == pytest.approx(7.5 / 3)
    assert out["int_type"] == "Int64"
    assert out["float_type"] == "Float64"
    assert out["null"] is True
    assert out["text"] == data["text"]
    assert out["escapes"] == data["escapes"]
    # 12345678901234567890 does not fit Int64: it arrives as a Float64.
    assert out["nested"] == {**data["nested"], "big": float(12345678901234567890)}
    assert result["logs"] == "rows: 3\n"


def test_julia_writer_handles_every_supported_type(jl_runner):
    code = (
        'evaluate(data) = Dict(\n'
        '    "nan" => NaN, "inf" => -Inf, "f32" => 1.5f0, "rational" => 1//4, "pi" => pi,\n'
        '    "nothing" => nothing, "missing" => missing, "symbol" => :abc, "char" => \'x\',\n'
        '    "tuple" => (1, "a"), "named" => (a = 1, b = [true]), "matrix" => [1 2; 3 4],\n'
        '    "int_keys" => Dict(1 => "eins"), "big" => big(2)^70, "tiny" => 1e-300,\n'
        '    "ctrl" => "a\\u0000b\\u001f", "set" => Set([7]), "range" => 1:3)\n'
    )
    result = jl_runner.run("julia", code)
    assert result["ok"] is True, result
    assert result["output"] == {
        "nan": None, "inf": None, "f32": 1.5, "rational": 0.25, "pi": pytest.approx(3.141592653589793),
        "nothing": None, "missing": None, "symbol": "abc", "char": "x",
        "tuple": [1, "a"], "named": {"a": 1, "b": [True]}, "matrix": [[1, 2], [3, 4]],
        "int_keys": {"1": "eins"}, "big": 2 ** 70, "tiny": 1e-300,
        "ctrl": "a\ufffdb\u001f", "set": [7], "range": [1, 2, 3],
    }


def test_julia_lone_surrogate_in_input_becomes_replacement_char(jl_runner):
    body = json.dumps({"language": "julia", "code": 'evaluate(data) = Dict("s" => data["s"])',
                       "data": {"s": "a\ud800b"}, "timeout_seconds": 60}).encode()
    status, payload = jl_runner.request("POST", "/v1/run", body)
    assert status == 200
    assert payload["ok"] is True, payload
    assert payload["output"] == {"s": "a\ufffdb"}


@pytest.mark.parametrize("code, message, needle", [
    ('evaluate(data) = error("kaputt")\n', R.MSG_EXCEPTION, "kaputt"),
    ('function evaluate(data)\n    x = [1, 2]\n    x[5]\nend\n', R.MSG_EXCEPTION, "BoundsError"),
    ('evaluate(data = \n', R.MSG_EXCEPTION, "ParseError"),
    ('error("beim Laden")\n', R.MSG_EXCEPTION, "beim Laden"),
    ('x = 1\n', R.MSG_NO_EVALUATE, "defines no function evaluate"),
    ('evaluate(data) = [1, 2]\n', R.MSG_NOT_DICT, "not a Dict"),
    ('evaluate(data) = Dict("f" => sin)\n', R.MSG_NOT_SERIALIZABLE, "not serializable"),
    ('function evaluate(data)\n    d = Dict{String,Any}()\n    d["self"] = d\n    d\nend\n',
     R.MSG_NOT_SERIALIZABLE, "nesting deeper"),
    ('evaluate(data) = exit(0)\n', R.MSG_NO_OUTPUT, "ohne Ergebnis"),
    ('evaluate(data) = ccall(:abort, Cvoid, ())\n', R.MSG_CRASHED, "SIGABRT"),
    ('evaluate(data) = Dict("x" => zeros(UInt8, 8 * 1024^3))\n', R.MSG_EXCEPTION, "OutOfMemoryError"),
])
def test_julia_failures_have_fixed_messages(jl_runner, code, message, needle):
    result = jl_runner.run("julia", code)
    assert result["ok"] is False
    assert result["output"] is None
    assert result["error"] == message
    assert needle in result["logs"], result["logs"]


def test_julia_exception_points_at_the_evaluator(jl_runner):
    result = jl_runner.run("julia", '\n\nevaluate(data) = error("in Zeile drei")\n')
    assert result["error"] == R.MSG_EXCEPTION
    assert "evaluator.jl:3" in result["logs"]


def test_julia_output_over_8_mb_is_refused(jl_runner):
    result = jl_runner.run("julia", 'evaluate(data) = Dict("x" => "a"^(9 * 1024 * 1024))\n')
    assert result["ok"] is False
    assert result["error"] == R.MSG_OUTPUT_TOO_LARGE


def test_julia_timeout(jl_runner):
    started = time.monotonic()
    result = jl_runner.run("julia", "function evaluate(data)\n    while true end\nend\n",
                           timeout_seconds=5)
    assert time.monotonic() - started < 20
    assert result["ok"] is False
    assert result["error"] == R.MSG_TIMEOUT


def test_julia_environment_and_limits(jl_runner):
    code = (
        'function evaluate(data)\n'
        '    limits = read("/proc/self/limits", String)\n'
        '    Dict("env" => Dict(k => v for (k, v) in ENV), "depot" => DEPOT_PATH,\n'
        '         "load" => LOAD_PATH, "threads" => Threads.nthreads(), "limits" => limits,\n'
        '         "oom" => strip(read("/proc/self/oom_score_adj", String)), "cwd" => pwd(),\n'
        '         "files" => sort(readdir(pwd())))\n'
        'end\n'
    )
    result = jl_runner.run("julia", code, timeout_seconds=30)
    assert result["ok"] is True, result
    out = result["output"]
    cwd = out["cwd"]
    assert os.path.dirname(cwd) == str(jl_runner.job_root)
    # OPENBLAS_MAIN_FREE is set by Julia itself at start-up, not by the runner.
    assert set(out["env"]) - {"OPENBLAS_MAIN_FREE"} == {
        "PATH", "HOME", "TMPDIR", "LANG", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "OMP_NUM_THREADS", "JULIA_NUM_THREADS", "JULIA_DEPOT_PATH", "JULIA_LOAD_PATH"}
    assert out["env"]["HOME"] == cwd
    assert out["depot"][0] == cwd + "/depot"
    assert out["depot"][1] == jl_runner.env["RUNNER_JULIA_DEPOT"]
    assert len(out["depot"]) >= 3           # Julia's bundled depots follow (trailing ':')
    assert out["load"] == ["@", "@v#.#", "@stdlib"]
    assert out["threads"] == 1
    assert out["oom"] == "1000"
    assert out["files"] == ["depot", "tmp"]
    limits = {line[:26].strip(): line[26:].split() for line in out["limits"].splitlines()[1:]}
    assert limits["Max address space"][:2] == [str(6 * 1024 ** 3)] * 2
    assert limits["Max cpu time"][:2] == ["35", "36"]
    assert limits["Max open files"][0] == "256"
    assert limits["Max processes"][0] == "128"
    assert limits["Max core file size"][0] == "0"
    assert limits["Max file size"][0] == str(64 * 1024 * 1024)
    assert not os.path.exists(cwd)


def test_julia_depot_is_per_job(jl_runner):
    write = (
        'function evaluate(data)\n'
        '    mkpath(DEPOT_PATH[1])\n'
        '    marker = joinpath(DEPOT_PATH[1], "compiled-by-job-one")\n'
        '    write(marker, "x")\n'
        '    Dict("depot" => DEPOT_PATH[1], "marker" => marker)\n'
        'end\n'
    )
    first = jl_runner.run("julia", write)
    assert first["ok"] is True, first
    look = (
        'evaluate(data) = Dict("depot" => DEPOT_PATH[1], "seen" => isfile(data["marker"]),\n'
        '                      "entries" => isdir(DEPOT_PATH[1]) ? readdir(DEPOT_PATH[1]) : String[])\n'
    )
    second = jl_runner.run("julia", look, {"marker": first["output"]["marker"]})
    assert second["ok"] is True, second
    assert second["output"]["depot"] != first["output"]["depot"]
    assert second["output"]["seen"] is False
    assert "compiled-by-job-one" not in second["output"]["entries"]
    assert not os.path.exists(first["output"]["depot"])


def test_julia_packages_of_the_shared_depot_load_precompiled(start_runner, tmp_path):
    # The image installs its packages into /opt/julia-depot's default
    # environment and precompiles them there at build time. A job must load
    # them through JULIA_LOAD_PATH and reuse those caches: compiling again
    # into the job's own depot would cost every job minutes.
    julia = R.Config.from_env().julia
    depot = tmp_path / "shared-depot"
    pkg = depot / "dev" / "KnovasProbe"
    (pkg / "src").mkdir(parents=True)
    uuid = "9a1c1a62-7e0f-4f4e-8a55-3f0d7e0d6a11"
    (pkg / "Project.toml").write_text('name = "KnovasProbe"\nuuid = "%s"\nversion = "0.1.0"\n' % uuid)
    (pkg / "src" / "KnovasProbe.jl").write_text(
        "module KnovasProbe\nanswer() = sum(x for x in 40:41) - 39\nend\n")
    env_dir = depot / "environments" / ("v" + ".".join(_julia_version().split(".")[:2]))
    env_dir.mkdir(parents=True)
    (env_dir / "Project.toml").write_text('[deps]\nKnovasProbe = "%s"\n' % uuid)
    (env_dir / "Manifest.toml").write_text(
        'manifest_format = "2.0"\n\n[[deps.KnovasProbe]]\npath = %s\nuuid = "%s"\nversion = "0.1.0"\n'
        % (json.dumps(str(pkg)), uuid))
    # Build time: precompile into the shared depot, as the Dockerfile does.
    build = subprocess.run(
        [julia, "--startup-file=no", "-e", "using KnovasProbe"],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "JULIA_DEPOT_PATH": str(depot)},
        capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stdout + build.stderr
    assert list(depot.glob("compiled/*/KnovasProbe/*.ji"))
    for path in depot.rglob("*"):
        path.chmod(path.stat().st_mode & ~0o222)
    runner = start_runner({"RUNNER_JULIA": julia, "RUNNER_JULIA_DEPOT": str(depot)})
    code = (
        "using KnovasProbe\n"
        "evaluate(data) = Dict(\"a\" => KnovasProbe.answer(),\n"
        "                      \"recompiled\" => isdir(joinpath(DEPOT_PATH[1], \"compiled\")))\n"
    )
    result = runner.run("julia", code, timeout_seconds=120)
    assert result["ok"] is True, result
    assert result["output"] == {"a": 42, "recompiled": False}


def _julia_version() -> str:
    out = subprocess.run([R.Config.from_env().julia, "--version"], capture_output=True, text=True)
    return out.stdout.split()[-1]


def test_julia_detached_process_is_killed_after_the_job(jl_runner, tmp_path):
    pidfile = tmp_path / "detached.pid"
    code = (
        'function evaluate(data)\n'
        '    p = run(detach(`sh -c "echo \\$\\$ > $(data["pidfile"]); exec sleep 300"`); wait=false)\n'
        '    while !isfile(data["pidfile"]) || isempty(read(data["pidfile"], String))\n'
        '        sleep(0.01)\n'
        '    end\n'
        '    Dict("pid" => parse(Int, strip(read(data["pidfile"], String))))\n'
        'end\n'
    )
    result = jl_runner.run("julia", code, {"pidfile": str(pidfile)})
    assert result["ok"] is True, result
    assert wait_dead(result["output"]["pid"])


def test_julia_output_limit_counts_utf8_bytes(jl_runner):
    ok = jl_runner.run("julia", 'evaluate(data) = Dict("x" => "\\u00fc"^(3 * 1024 * 1024))\n')
    assert ok["ok"] is True, ok["error"]
    assert ok["output"]["x"] == "\u00fc" * (3 * 1024 * 1024)
    too_big = jl_runner.run("julia", 'evaluate(data) = Dict("x" => "\\u00fc"^(5 * 1024 * 1024))\n')
    assert too_big["error"] == R.MSG_OUTPUT_TOO_LARGE
