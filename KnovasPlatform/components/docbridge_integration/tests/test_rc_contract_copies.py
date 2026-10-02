"""The contract files the Platform ships are byte copies of RemoteController's.

The Platform image contains ``docbridge_integration/src`` only, so it carries
its own copies of what RemoteController defines: the sync-request schema
(checked in test_ingestion_compiler.py) and the path-template golden
vectors the Platform's template implementation is held to. A copy that
drifted would let the two implementations disagree while both pass their
own tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from identity import field_templates

CHECKOUT_VECTORS = (Path(__file__).resolve().parents[4] / "RemoteController" / "contracts"
                    / "vectors" / "field_templates.json")


def test_the_platform_ships_the_vectors():
    assert field_templates.VECTORS_PATH.is_file()
    cases = json.loads(field_templates.VECTORS_PATH.read_text(encoding="utf-8"))
    assert isinstance(cases, list) and len(cases) >= 25


def test_the_vectors_are_a_byte_copy_of_remote_controllers():
    if not CHECKOUT_VECTORS.is_file():
        pytest.skip("RemoteController is not in this checkout")
    assert field_templates.VECTORS_PATH.read_bytes() == CHECKOUT_VECTORS.read_bytes()


def test_the_vectors_sit_outside_the_schema_glob():
    """CI checks every ``contracts/*.json`` as a JSON Schema and an array is
    not one; the copy keeps the same ``vectors/`` sub-folder layout."""
    assert field_templates.VECTORS_PATH.parent.name == "vectors"
    assert field_templates.VECTORS_PATH.parent.parent.name == "rc_contracts"
