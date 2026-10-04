"""The Platform reads a path template exactly as Knovas Connector will.

The Ingestion tab compiles templates when a profile is saved and previews
their captures over the paths Knovas Connector reports (spec 3.4, 4.8). A
preview that disagrees with the sync shows a person values their documents
never get, so the Platform's implementation is pinned three ways: against
the golden vectors (a byte copy of Knovas Connector's), against
Knovas Connector's own module on every pairing of the vectors' templates
and paths, and through the preview function the page actually calls.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import pickle
import sys
from pathlib import Path

import pytest

from identity import field_templates as ft

RC_MODULE = (Path(__file__).resolve().parents[4] / "KnovasConnector" / "src" / "sync"
             / "field_templates.py")


def _vectors() -> list[dict]:
    return json.loads(ft.VECTORS_PATH.read_text(encoding="utf-8"))


VECTORS = _vectors()


def _outcome(module, template, path):
    """(captures or None, error code or None), the vectors' shape."""
    try:
        return module.match_template(template, path), None
    except module.TemplateError as exc:
        return None, exc.code


@pytest.fixture(scope="module")
def rc_templates():
    """Knovas Connector's module, loaded from the checkout by file path (it
    lives in a package the Platform cannot import)."""
    if not RC_MODULE.is_file():
        pytest.skip("Knovas Connector is not in this checkout")
    spec = importlib.util.spec_from_file_location("_rc_field_templates", RC_MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class TestGoldenVectors:
    def test_there_are_enough_vectors_and_every_one_has_the_shape(self):
        assert len(VECTORS) >= 25
        for case in VECTORS:
            assert set(case) == {"template", "path", "captures", "error"}
            assert case["error"] is None or case["captures"] is None
            assert case["error"] is None or case["error"] in ft.TEMPLATE_ERROR_CODES

    @pytest.mark.parametrize("case", VECTORS, ids=lambda c: f"{c['template'][:30]}|{c['path'][:30]}")
    def test_every_vector(self, case):
        assert _outcome(ft, case["template"], case["path"]) == (case["captures"], case["error"])

    def test_the_vectors_cover_what_spec_3_4_lists(self):
        templates = [c["template"] for c in VECTORS]
        paths = [c["path"] for c in VECTORS]
        errors = {c["error"] for c in VECTORS}
        assert any("\\" in p for p in paths), "backslash separators"
        assert any(t.endswith("/**") for t in templates), "deep suffix"
        assert any(t.startswith("*/") or "/*/" in t for t in templates), "single wildcard"
        assert {"syntax", "duplicate_key", "system_key", "too_long"} <= errors
        assert any(c["captures"] == {} for c in VECTORS), "a match that captures nothing"


class TestSameAnswersAsKnovasConnector:
    def test_every_vector_template_against_every_vector_path(self, rc_templates):
        templates = sorted({c["template"] for c in VECTORS})
        paths = sorted({c["path"] for c in VECTORS}) + [
            "", "a.pdf", "/abs/a.pdf", "x//y/a.pdf", "K\u00fcndigungen/Muster AG/a.pdf",
            "Ku\u0308ndigungen/Muster AG/a.pdf", "Stra\u00dfe/Muster AG/a.pdf",
        ]
        for template, path in itertools.product(templates, paths):
            assert _outcome(ft, template, path) == _outcome(rc_templates, template, path), (
                template[:40], path[:40])

    def test_first_match_wins_in_both(self, rc_templates):
        texts = ["Archiv/**", "{mandant}/{period}/**", "{mandant}/**"]
        mine = ft.compile_templates(texts)
        theirs = rc_templates.compile_templates(texts)
        for path in ("Archiv/2019/Brief.pdf", "Muster AG/GJ 2024/a.pdf", "Muster AG/a.pdf",
                     "a.pdf"):
            assert ft.captures(path, mine) == rc_templates.captures(path, theirs), path

    def test_the_constants_agree(self, rc_templates):
        assert ft.SYSTEM_KEYS == rc_templates.SYSTEM_KEYS
        assert ft.TEMPLATE_ERROR_CODES == rc_templates.TEMPLATE_ERROR_CODES
        assert (ft.MAX_TEMPLATE_CHARS, ft.MAX_LITERAL_CHARS, ft.MAX_KEY_CHARS) == (
            rc_templates.MAX_TEMPLATE_CHARS, rc_templates.MAX_LITERAL_CHARS,
            rc_templates.MAX_KEY_CHARS)


class TestThePreviewThePageShows:
    """``admin_ingestion.template_preview`` is what the Vorschau and the
    "Vorlagen testen" button render. Fed one file per vector, it must show
    exactly the vector's captures (spec 7, WP-P4 acceptance)."""

    @pytest.mark.parametrize("case", [c for c in VECTORS if c["error"] is None],
                             ids=lambda c: f"{c['template'][:30]}|{c['path'][:30]}")
    def test_preview_matches_every_vector(self, case):
        from web_interface.admin_ingestion import template_preview

        out = template_preview([case["template"]], [{"type": "file", "path": case["path"]}])
        assert out["files"] == 1 and out["errors"] == []
        (row,) = out["rows"]
        if case["captures"] is None:
            assert row["template"] is None and row["captures"] == [] and out["matched"] == 0
        else:
            assert row["template"] == 1 and out["matched"] == 1
            assert {c["key"]: c["value"] for c in row["captures"]} == case["captures"]

    @pytest.mark.parametrize("case", [c for c in VECTORS if c["error"] is not None],
                             ids=lambda c: f"{c['template'][:30]}|{c['error']}")
    def test_a_bad_template_captures_nothing_and_names_its_code(self, case):
        """Knovas Connector skips the whole folder on a bad template, so the
        preview must not pretend another template still applies."""
        from web_interface.admin_ingestion import template_preview

        out = template_preview(["{mandant}/**", case["template"]],
                               [{"type": "file", "path": "Muster AG/a.pdf"}])
        assert [e["code"] for e in out["errors"]] == [case["error"]]
        assert out["errors"][0]["index"] == 2
        assert out["matched"] == 0 and out["rows"][0]["captures"] == []

    def test_the_preview_never_repeats_a_bad_template(self):
        from web_interface.admin_ingestion import template_preview

        out = template_preview(["Muster AG*/{x}"], [])
        assert "Muster AG" not in json.dumps(out)


class TestCompiledTemplates:
    def test_a_compiled_template_is_hashable_and_picklable(self):
        compiled = ft.compile_template("{mandant}/*/Belege/**")
        assert hash(compiled) == hash(ft.compile_template("{mandant}/*/Belege/**"))
        assert pickle.loads(pickle.dumps(compiled)) == compiled
        assert compiled.keys == ("mandant",)

    def test_first_match_names_the_template(self):
        templates = ft.compile_templates(["Archiv/**", "{mandant}/**"])
        assert ft.first_match("Muster AG/a.pdf", templates) == (1, {"mandant": "Muster AG"})
        assert ft.first_match("a.pdf", templates) == (None, {})

    def test_an_error_message_never_repeats_the_template(self):
        with pytest.raises(ft.TemplateError) as excinfo:
            ft.compile_template("Muster AG/{mandant}/{mandant}")
        assert excinfo.value.code == "duplicate_key"
        assert "Muster" not in str(excinfo.value)

    def test_the_module_is_ascii_only(self):
        """New .py files stay ASCII (scripts/check_ascii_py.py)."""
        for path in (Path(ft.__file__), Path(__file__)):
            path.read_bytes().decode("ascii")
