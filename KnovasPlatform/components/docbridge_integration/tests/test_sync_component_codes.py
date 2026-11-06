"""Every result code the sync component can answer is in the shared contract and has a German text in the Builder
(connection layer MVP-1, MVP-21, MVP-22, MVP-28), and so has every code of the contract, Knovas Link's
``install-certificate`` refusals among them (DEC-0056, MVP-23).

The codes are read from the component's source: the second argument of every ``Outcome(...)`` and ``result(...)`` call
that is a string literal. A code added to the component without the contract (``KnovasLink/contracts/codes.json``) or
the Builder (``builder/codes.py``) fails here, so a consultant never sees «Unbekannter Code» for something the
component says. The ``github.http_<status>`` family is a pattern in the contract, not a list, and stays one.
"""
import ast
import json
import re
from pathlib import Path

from builder import codes

COMPONENTS = Path(__file__).resolve().parents[2]
COMPONENT_SRC = COMPONENTS / "sync_component" / "src" / "sync_component"
CONTRACT = Path(__file__).resolve().parents[4] / "KnovasLink" / "contracts" / "codes.json"
FAMILY = re.compile(r"^github\.http_[1-5][0-9]{2}$")
EMITTERS = {"Outcome", "result"}


def emitted_codes() -> dict:
    found: dict = {}
    for path in sorted(COMPONENT_SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            code = node.args[1]
            if name in EMITTERS and isinstance(code, ast.Constant) and isinstance(code.value, str):
                found.setdefault(code.value, set()).add(path.name)
    return found


def test_the_scan_finds_the_component_s_codes():
    found = emitted_codes()
    assert {"not_found", "conflict", "github.unreachable", "github.branch_left", "internal"} <= set(found), sorted(found)


def test_every_code_the_component_answers_is_in_the_contract():
    listed = {c["code"] for c in json.loads(CONTRACT.read_text(encoding="utf-8"))["codes"]}
    missing = {c: sorted(f) for c, f in emitted_codes().items() if c not in listed and not FAMILY.fullmatch(c)}
    assert missing == {}, f"add these to KnovasLink/contracts/codes.json and local-api.md: {missing}"


def test_every_code_the_component_answers_has_a_german_text():
    unknown = sorted(c for c in emitted_codes() if codes.describe(c)["title"] == "Unbekannter Code")
    assert unknown == [], f"add these to builder/codes.py: {unknown}"


def test_every_code_of_the_contract_has_a_german_text():
    listed = [c["code"] for c in json.loads(CONTRACT.read_text(encoding="utf-8"))["codes"]]
    assert {"waiting_for_certificate", "certificate.wrong_key", "certificate.not_client_auth"} <= set(listed)
    unknown = [c for c in listed if codes.describe(c)["title"] == "Unbekannter Code"]
    assert unknown == [], f"add these to builder/codes.py: {unknown}"
    for c in listed:
        found = codes.describe(c)
        assert found["explanation"].strip() and found["fix"].strip(), c
