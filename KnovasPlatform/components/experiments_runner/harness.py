"""Runs one Python evaluator job inside the experiments-runner sandbox.

runner.py starts this file once per job, as its own process in its own
session, with rlimits and a scrubbed environment already applied:

    /opt/venv/bin/python3 -I harness.py <job dir>

The job directory holds two files the runner wrote: ``code`` (the
evaluator source) and ``input.json`` (the evaluator input contract). This
harness executes the code in a fresh namespace, calls ``evaluate(data)`` and
writes the envelope the runner reads back to ``output.json``:

    {"ok": true,  "result": {...}}
    {"ok": false, "error_code": "exception" | "no_evaluate" | "not_dict"
                                | "not_serializable" | "bad_input"}

The envelope, not stdout, is the protocol: whatever the user code prints
lands in the job log and never has to be parsed. Tracebacks go to stderr,
which the runner captures into the same log. The user code runs in this very
process, so it could write output.json itself -- that is harmless, because it
could equally return whatever it likes; the runner validates the envelope
either way.
"""

from __future__ import annotations

import builtins
import decimal
import json
import linecache
import math
import numbers
import os
import sys
import traceback

#: The filename tracebacks show for the user's code.
CODE_FILENAME = "evaluator.py"
#: Nesting limit for the result; deeper structures are refused, which also
#: catches self-referencing containers.
MAX_DEPTH = 100

PR_SET_DUMPABLE = 4


class NotSerializable(Exception):
    """The result holds a value that has no JSON form."""


def _harden() -> None:
    """Make this process non-dumpable.

    Every job runs under the same uid, so without this a concurrently running
    job could ptrace this one or read its memory through /proc/<pid>/mem.
    Best effort: the runner's other limits stand without it.
    """
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except Exception:  # noqa: BLE001 - hardening only
        pass


def _clean_str(value: str) -> str:
    """A string that encodes as UTF-8: lone surrogates become U+FFFD."""
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _plain(value, depth: int = 0):
    """``value`` as plain JSON types; NaN and Infinity become None.

    numpy arrays and scalars (and anything else with ``tolist``/``item``)
    are converted, so an evaluator may return them directly.
    """
    if depth > MAX_DEPTH:
        raise NotSerializable("nesting deeper than %d levels" % MAX_DEPTH)
    if isinstance(value, str):
        return _clean_str(value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, (numbers.Real, decimal.Decimal)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, dict):
        return {
            _clean_str(key if isinstance(key, str) else str(_plain(key, depth + 1))): _plain(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in value]
    for converter in ("tolist", "item"):
        method = getattr(value, converter, None)
        if callable(method):
            return _plain(method(), depth + 1)
    raise NotSerializable("value of type %s" % type(value).__name__)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _write_envelope(job_dir: str, envelope: dict) -> None:
    """Write output.json atomically, so the runner never reads half a file.

    UTF-8 rather than ASCII escapes, so the 8 MB output limit means the same
    for Python and Julia evaluators (_plain removed lone surrogates).
    """
    text = json.dumps(envelope, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    tmp = os.path.join(job_dir, "output.json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, os.path.join(job_dir, "output.json"))


def _print_exception(skip_harness_frame: bool = False) -> None:
    """Print the current exception; optionally without this file's own frame.

    The user reads the traceback in the job log: it should start at their
    code, not at the exec/call inside the harness.
    """
    exc_type, exc, tb = sys.exc_info()
    if skip_harness_frame and tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    traceback.print_exception(exc_type, exc, tb, file=sys.stderr)


def run(code: str, data: dict) -> dict:
    """Execute ``code`` and call its ``evaluate(data)``; returns the envelope."""
    # Tracebacks show the user's own lines, although the code never exists
    # as a file under CODE_FILENAME.
    linecache.cache[CODE_FILENAME] = (len(code), None, code.splitlines(True), CODE_FILENAME)
    namespace = {"__name__": "evaluator", "__builtins__": builtins}
    try:
        exec(compile(code, CODE_FILENAME, "exec"), namespace)  # noqa: S102 - the sandbox's purpose
    except SyntaxError:
        # No frame of the user's code exists; the harness frame is all there is.
        _print_exception()
        return {"ok": False, "error_code": "exception"}
    except BaseException:  # noqa: BLE001 - SystemExit from user code is an error too
        _print_exception(skip_harness_frame=True)
        return {"ok": False, "error_code": "exception"}
    evaluate = namespace.get("evaluate")
    if not callable(evaluate):
        print("evaluator.py defines no function evaluate(data).", file=sys.stderr)
        return {"ok": False, "error_code": "no_evaluate"}
    try:
        result = evaluate(data)
    except BaseException:  # noqa: BLE001
        _print_exception(skip_harness_frame=True)
        return {"ok": False, "error_code": "exception"}
    if not isinstance(result, dict):
        print("evaluate(data) returned %s, not a dict." % type(result).__name__, file=sys.stderr)
        return {"ok": False, "error_code": "not_dict"}
    try:
        return {"ok": True, "result": _plain(result)}
    except (NotSerializable, RecursionError, ValueError, TypeError, OverflowError) as exc:
        print("The result cannot be written as JSON: %s" % exc, file=sys.stderr)
        return {"ok": False, "error_code": "not_serializable"}
    except BaseException:  # noqa: BLE001 - tolist() of a user object may raise anything
        _print_exception()
        return {"ok": False, "error_code": "not_serializable"}


def main(argv: list) -> int:
    if len(argv) != 2:
        print("usage: harness.py <job dir>", file=sys.stderr)
        return 2
    job_dir = argv[1]
    _harden()
    # stdout and stderr share one pipe: line buffering keeps prints and a
    # traceback in the order they happened.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    code_path = os.path.join(job_dir, "code")
    input_path = os.path.join(job_dir, "input.json")
    try:
        code = _read_text(code_path)
        with open(input_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("input is not an object")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print("Cannot read the job input: %s" % exc, file=sys.stderr)
        envelope = {"ok": False, "error_code": "bad_input"}
    else:
        # Gone before any user code runs: another job under the same uid
        # finds nothing to read here.
        for path in (code_path, input_path):
            try:
                os.unlink(path)
            except OSError:
                pass
        envelope = run(code, data)
    try:
        _write_envelope(job_dir, envelope)
    except BaseException:  # noqa: BLE001
        _print_exception()
        status = 1
    else:
        status = 0 if envelope.get("ok") else 1
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        # os._exit: threads the user code left running, and atexit handlers
        # it registered, must not keep the job alive or change its result.
        os._exit(status)
    return status  # pragma: no cover - unreachable


if __name__ == "__main__":
    main(sys.argv)
