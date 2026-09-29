"""The README examples: people copy them into CI pipelines, so they are tested.

The CI script (bench/report_to_knovas.py) runs as it is written in the README
against the fake Platform; the other examples are checked for what the
Platform would refuse.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from kx_fake_platform import TOKEN

HERE = os.path.dirname(os.path.abspath(__file__))
SDK_DIR = os.path.dirname(HERE)
README = os.path.join(SDK_DIR, "README.md")
JULIA_README = os.path.join(SDK_DIR, "..", "julia", "README.md")
DOCS = os.path.join(SDK_DIR, "..", "..", "docs", "features", "experiments.md")
API = "/api/experiments/v1"

#: The metrics of the type "Offline-Evaluation" (engineering pack) that the
#: ENG-12 examples report into. The Platform refuses a whole run that names
#: any other metric ("ist diesem Experiment nicht zugeordnet").
OFFLINE_EVAL_METRICS = {"ndcg_at_10", "recall_at_20", "mrr", "latency_p95_ms"}


def _blocks(language: str, path: str = README) -> list:
    with open(path, encoding="utf-8") as handle:
        return re.findall(r"```%s\n(.*?)```" % language, handle.read(), re.S)


def _ci_script() -> str:
    return next(b for b in _blocks("python") if "def report(client, variant" in b)


def test_python_examples_compile():
    for path in (README, DOCS):
        blocks = _blocks("python", path)
        assert blocks, path
        for block in blocks:
            compile(block, path, "exec")


def _top_level_keys(text: str, start: int) -> set:
    """The quoted keys at the outermost level of the bracket opened at start."""
    depth, keys, i = 0, set(), start
    while i < len(text):
        char = text[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return keys
        elif char == '"':                      # a string: its brackets do not count
            match = re.match(r'"([a-z0-9_]+)"\s*(?:=>|:)', text[i:])
            if match and depth == 1:
                keys.add(match.group(1))
            i = text.index('"', i + 1)
        i += 1
    return keys


def _eng12_metrics(paragraph: str) -> set:
    patterns = (r'add_row\("([a-z0-9_]+)"', r'\.log\(([a-z0-9_]+)=',
                r'"metric"\s*=>\s*"([a-z0-9_]+)"', r'"metric":\s*"([a-z0-9_]+)"')
    names = {name for pattern in patterns for name in re.findall(pattern, paragraph)}
    # metrics=Dict("a" => 1, "b" => Dict(...)) and metrics={"a": 1, ...}
    for match in re.finditer(r'metrics\s*=\s*(?:Dict)?([({])', paragraph):
        names |= _top_level_keys(paragraph, match.start(1))
    return names


@pytest.mark.parametrize("path, language", [
    (README, "python"), (JULIA_README, "julia"), (DOCS, "python"),
])
def test_offline_eval_examples_log_only_the_types_metrics(path, language):
    # e2e-api-3: error_rate belongs to the rollout and performance types.
    seen = set()
    for block in _blocks(language, path):
        for paragraph in block.split("\n\n"):
            if '"ENG-12"' in paragraph:        # the key in code, not in a comment
                seen |= _eng12_metrics(paragraph)
    assert seen, "no ENG-12 example found in %s" % path
    assert seen <= OFFLINE_EVAL_METRICS, seen - OFFLINE_EVAL_METRICS


def _write_bench(tmp_path):
    (tmp_path / "bench").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "bench" / "report_to_knovas.py").write_text(_ci_script(), encoding="utf-8")
    shutil.copy(os.path.join(SDK_DIR, "knovas_experiments.py"), tmp_path / "tools")
    for name, score in (("baseline.jsonl", 0.50), ("candidate.jsonl", 0.55)):
        with open(tmp_path / name, "w", encoding="utf-8") as handle:
            for q in range(5):
                handle.write(json.dumps({"query_id": "q%d" % q, "ndcg_at_10": score,
                                         "recall_at_20": score, "mrr": score,
                                         "latency_ms": 100 + q}) + "\n")


DONE_BETTER = [
    {"id": "d", "status": "done", "verdict": "n/a", "evaluator_name": "builtin.describe",
     "metric_key": "latency_p95_ms", "headline": "Latenz p95: baseline 104 ms, candidate 104 ms"},
    {"id": "p", "status": "done", "verdict": "better", "evaluator_name": "builtin.paired_t",
     "metric_key": "ndcg_at_10", "headline": "+0,05"},
]


@pytest.mark.parametrize("returned, exit_code", [
    (DONE_BETTER, 0),
    (DONE_BETTER + [{"id": "g", "status": "done", "verdict": "worse",
                     "evaluator_name": "builtin.describe", "metric_key": "latency_p95_ms",
                     "headline": "Leitplanke verletzt: Variante candidate 320 ms > 250 ms"}], 1),
    (DONE_BETTER + [{"id": "c", "status": "failed", "verdict": None,
                     "evaluator_name": "example.bootstrap_mean_py"}], 1),
])
def test_ci_script_judges_the_newest_run_of_this_push(fake, tmp_path, returned, exit_code):
    # e2e-api-1 and e2e-api-2: the script asks for the newest run per variant
    # in every step (the guardrail included) and gates on what this push's
    # evaluate() returned, never on the experiment's history.
    _write_bench(tmp_path)
    fake.ok("POST", f"{API}/experiments/ENG-12/runs", "run", {"id": "r1"}, status=201)
    fake.ok("POST", f"{API}/experiments/ENG-12/pipeline", "evaluations", returned)
    fake.ok("GET", f"{API}/experiments/ENG-12/evaluations", "evaluations",
            [{"id": "old", "status": "done", "verdict": "worse"}])
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "KNOVAS_URL": fake.url,
           "KNOVAS_EXPERIMENTS_TOKEN": TOKEN, "EXPERIMENT": "ENG-12",
           "BASE_SHA": "b" * 40, "HEAD_SHA": "c" * 40, "PR_NUMBER": "481"}

    done = subprocess.run([sys.executable, os.path.join("bench", "report_to_knovas.py")],
                          cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60)

    assert done.returncode == exit_code, done.stdout + done.stderr
    runs = [r for r in fake.requests if r.path.endswith("/runs")]
    assert [r.json["variant"] for r in runs] == ["baseline", "candidate"]
    for run in runs:
        names = {row["metric"] for row in run.json["rows"]} | set(run.json["metrics"])
        assert names <= OFFLINE_EVAL_METRICS
        assert run.json["metrics"] == {"latency_p95_ms": 104}
    pipeline = [r for r in fake.requests if r.path.endswith("/pipeline")]
    assert [r.json for r in pipeline] == [{"scope": {"runs": "latest"}}]
    assert [r for r in fake.requests if r.method == "GET"] == []


def _sign_test_example():
    block = next(b for b in _blocks("python", DOCS) if "_two_sided_binomial" in b)
    namespace = {}
    exec(compile(block, DOCS, "exec"), namespace)
    return namespace["evaluate"]


def _sign_test_input(kind, rows):
    return {"metric": {"kind": kind, "direction": "higher"}, "params": {"pair_by": "query"},
            "variants": [{"key": "A", "is_control": True}, {"key": "B", "is_control": False}],
            "rows": [{"variant": v, "value": x, "count": c, "dims": {"query": q}}
                     for v, q, x, c in rows]}


def test_docs_sign_test_example_reads_a_scale_row_as_answers_on_a_level():
    # review-stats-2: an ordinal row is a level (value) with count answers on
    # it, not a sum; the documented example is declared for ordinal metrics.
    evaluate = _sign_test_example()
    aggregated = [("A", "q1", 4, 1), ("B", "q1", 5, 2), ("A", "q2", 2, 3), ("B", "q2", 3, 1),
                  ("B", "q2", 2, 1)]
    one_per_answer = [(v, q, x, 1) for v, q, x, c in aggregated for _ in range(c)]
    assert evaluate(_sign_test_input("ordinal", aggregated)) == \
        evaluate(_sign_test_input("ordinal", one_per_answer))
    result = evaluate(_sign_test_input("ordinal", aggregated))
    # q1: B 5 > A 4; q2: B 2.5 > A 2 -- two pairs won. Read as sums, B's two
    # answers of 5 would be a mean of 2.5 and lose q1.
    assert result["table"]["rows"] == [["B", 2, 0, 0, "0,5000"]]
    # For a mean metric, value stays the sum of count values.
    mean = evaluate(_sign_test_input("mean", [("A", "q1", 8.0, 2), ("B", "q1", 5.0, 1)]))
    assert mean["table"]["rows"] == [["B", 1, 0, 0, "1,0000"]]
