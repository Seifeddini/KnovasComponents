"""The Platform's RunnerClient against the real runner: the wire contract.

Skipped when the Platform source is not next to this component (the runner
is also tested on its own). Nothing here needs a database or Flask.
"""

from __future__ import annotations

import importlib
import sys
import threading
import time

import pytest

from conftest import RUNNER_DIR

PLATFORM_SRC = RUNNER_DIR.parent / "docbridge_integration" / "src"


@pytest.fixture(scope="module")
def client_module():
    if not (PLATFORM_SRC / "experiments" / "runner_client.py").exists():
        pytest.skip("Platform source with experiments.runner_client is not available")
    sys.path.insert(0, str(PLATFORM_SRC))
    try:
        module = importlib.import_module("experiments.runner_client")
        errors = importlib.import_module("experiments.errors")
    finally:
        sys.path.remove(str(PLATFORM_SRC))
    return module, errors


def test_health_and_run_through_the_platform_client(client_module, py_runner):
    module, _errors = client_module
    client = module.RunnerClient("unix://" + py_runner.socket_path, timeout_seconds=30)
    health = client.health()
    assert health["configured"] is True
    assert health["ok"] is True
    assert health["busy"] == 0
    assert "python" in health["languages"]

    result = client.run(language="python",
                        code="def evaluate(data):\n    return {'n': len(data['rows'])}\n",
                        data={"rows": [1, 2, 3]}, timeout_seconds=20)
    assert result["ok"] is True
    assert result["output"] == {"n": 3}
    assert result["error"] is None

    failed = client.run(language="python", code="def evaluate(data):\n    raise KeyError('x')\n",
                        data={}, timeout_seconds=20)
    assert failed["ok"] is False
    assert failed["error"] == "Der Auswerter ist mit einem Fehler abgebrochen."
    assert "KeyError" in failed["logs"]


def test_timeout_reaches_the_platform_as_a_failed_result(client_module, py_runner):
    module, _errors = client_module
    client = module.RunnerClient("unix://" + py_runner.socket_path, timeout_seconds=30)
    result = client.run(language="python", code="import time\ndef evaluate(data):\n    time.sleep(60)\n",
                        data={}, timeout_seconds=2)
    assert result["ok"] is False
    assert result["error"] == "Zeitlimit \u00fcberschritten."


def test_busy_runner_is_unavailable_to_the_platform(client_module, start_runner):
    module, errors = client_module
    runner = start_runner({"RUNNER_MAX_CONCURRENT": "1", "RUNNER_SLOT_WAIT_SECONDS": "1"})
    client = module.RunnerClient("unix://" + runner.socket_path, timeout_seconds=30)
    code = "import time\ndef evaluate(data):\n    time.sleep(4)\n    return {}\n"
    worker = threading.Thread(target=client.run, kwargs={
        "language": "python", "code": code, "data": {}, "timeout_seconds": 20})
    worker.start()
    try:
        deadline = time.monotonic() + 20
        while runner.health()["busy"] != 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        with pytest.raises(errors.Unavailable):
            client.run(language="python", code="def evaluate(d):\n    return {}\n", data={},
                       timeout_seconds=5)
    finally:
        worker.join()


def test_stopped_runner_is_unavailable_to_the_platform(client_module, py_runner):
    module, errors = client_module
    client = module.RunnerClient("unix://" + py_runner.socket_path, timeout_seconds=30)
    py_runner.stop()
    assert client.health()["ok"] is False
    with pytest.raises(errors.Unavailable):
        client.run(language="python", code="def evaluate(d):\n    return {}\n", data={},
                   timeout_seconds=5)


def test_large_non_ascii_output_fits_the_platform_clients_answer_limit(client_module, py_runner):
    # 7 MB of umlauts as UTF-8: within the 8 MB output limit, and the answer
    # must stay within the client's 12 MB (ASCII escapes would make it 21 MB).
    module, _errors = client_module
    client = module.RunnerClient("unix://" + py_runner.socket_path, timeout_seconds=60)
    result = client.run(language="python",
                        code="def evaluate(data):\n    return {'x': '\\u00fc' * (3584 * 1024)}\n",
                        data={}, timeout_seconds=60)
    assert result["ok"] is True, result["error"]
    assert result["output"]["x"] == "\u00fc" * (3584 * 1024)
