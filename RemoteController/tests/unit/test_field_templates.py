"""Path templates for document fields (spec section 3.4).

The golden vectors in ``contracts/vectors/field_templates.json`` are the
contract the Platform's preview is checked against; every case below that
is not in the file covers the multi-template API on top of it.
"""
import json
import pickle
from pathlib import Path

import pytest

from sync.field_templates import (
    SYSTEM_KEYS,
    TEMPLATE_ERROR_CODES,
    CompiledTemplate,
    TemplateError,
    captures,
    compile_template,
    compile_templates,
    directory_segments,
    match_template,
)

VECTORS_PATH = Path(__file__).resolve().parents[2] / "contracts" / "vectors" / "field_templates.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def _vector_id(vector):
    template = vector["template"]
    return (template[:40] + "...") if len(template) > 40 else (template or "<empty>")


def test_vectors_file_shape():
    assert isinstance(VECTORS, list)
    assert len(VECTORS) >= 25
    for vector in VECTORS:
        assert set(vector) == {"template", "path", "captures", "error"}
        assert isinstance(vector["template"], str)
        assert isinstance(vector["path"], str)
        assert vector["error"] is None or vector["error"] in TEMPLATE_ERROR_CODES
        if vector["error"] is not None:
            assert vector["captures"] is None
        if vector["captures"] is not None:
            assert all(isinstance(k, str) and isinstance(v, str) for k, v in vector["captures"].items())


def test_vectors_file_is_not_a_schema_and_lives_outside_contracts_root():
    # CI runs Draft202012Validator.check_schema on contracts/*.json (not
    # recursive); a JSON array there would fail it.
    assert VECTORS_PATH.parent.name == "vectors"
    assert VECTORS_PATH.parent.parent.name == "contracts"


def test_vectors_cover_the_required_cases():
    nfd = "\u0308"

    def has(pred):
        return any(pred(v) for v in VECTORS)

    assert has(lambda v: "\\" in v["path"] and v["captures"])
    assert has(lambda v: nfd in v["path"] and v["captures"] is not None)
    assert has(lambda v: nfd in v["template"] and v["captures"] is not None)
    assert has(lambda v: "*/" in v["template"] and v["captures"] is not None)
    # /** with zero deeper levels, and with several
    assert has(lambda v: v["template"] == "{mandant}/**" and v["path"].count("/") == 1 and v["captures"])
    assert has(lambda v: v["template"].endswith("/**") and v["path"].count("/") >= 4 and v["captures"])
    # depth mismatch without /**
    assert has(lambda v: not v["template"].endswith("/**") and v["captures"] is None and v["error"] is None)
    for code in TEMPLATE_ERROR_CODES:
        assert has(lambda v, c=code: v["error"] == c), code
    # empty segment gives no value
    assert has(lambda v: "//" in v["path"] or "/   /" in v["path"])
    # case-insensitive literal
    assert has(lambda v: v["template"].startswith("Kunden/") and v["path"].startswith("kunden/"))


@pytest.mark.parametrize("vector", VECTORS, ids=_vector_id)
def test_golden_vector(vector):
    if vector["error"] is not None:
        with pytest.raises(TemplateError) as exc:
            compile_template(vector["template"])
        assert exc.value.code == vector["error"]
        return
    assert match_template(vector["template"], vector["path"]) == vector["captures"]


def test_first_matching_template_wins():
    templates = compile_templates(["Kunden/{mandant}/**", "{mandant}/{period}/**", "{mandant}/**"])
    assert captures("Kunden/Muster AG/Brief.pdf", templates) == {"mandant": "Muster AG"}
    assert captures("Muster AG/GJ 2024/Brief.pdf", templates) == {"mandant": "Muster AG", "period": "GJ 2024"}
    assert captures("Muster AG/Brief.pdf", templates) == {"mandant": "Muster AG"}


def test_a_match_without_captures_still_stops_the_search():
    templates = compile_templates(["Archiv/**", "{mandant}/**"])
    assert captures("Archiv/Brief.pdf", templates) == {}
    assert captures("Muster AG/Brief.pdf", templates) == {"mandant": "Muster AG"}


def test_no_template_or_no_match_gives_no_captures():
    assert captures("Muster AG/Brief.pdf", ()) == {}
    assert captures("Brief.pdf", compile_templates(["{mandant}/**"])) == {}


def test_captures_are_never_type_converted():
    found = match_template("{period}/{amount}/{flag}", "2024/1234.50/true/Brief.pdf")
    assert found == {"period": "2024", "amount": "1234.50", "flag": "true"}
    assert all(isinstance(v, str) for v in found.values())


def test_capture_is_nfc_normalised():
    found = match_template("{mandant}/**", "Mu\u0308ster AG/Brief.pdf")
    assert found == {"mandant": "M\u00fcster AG"}


def test_every_system_key_is_refused():
    for key in SYSTEM_KEYS:
        with pytest.raises(TemplateError) as exc:
            compile_template("{%s}/**" % key)
        assert exc.value.code == "system_key"


def test_compile_templates_raises_on_the_first_bad_template():
    with pytest.raises(TemplateError) as exc:
        compile_templates(["{mandant}/**", "{title}/**", "{a}/{a}"])
    assert exc.value.code == "system_key"


def test_template_error_message_never_repeats_the_template():
    with pytest.raises(TemplateError) as exc:
        compile_template("Muster AG {mandant}")
    assert "Muster" not in str(exc.value)
    assert exc.value.code == "syntax"


def test_non_string_template_is_a_syntax_error():
    with pytest.raises(TemplateError) as exc:
        compile_template(None)  # type: ignore[arg-type]
    assert exc.value.code == "syntax"


def test_directory_segments_drop_the_file_name_and_split_backslashes():
    assert directory_segments("Muster AG\\GJ 2024/Brief.pdf") == ("Muster AG", "GJ 2024")
    assert directory_segments("Brief.pdf") == ()
    assert directory_segments("") == ()


def test_compiled_template_is_hashable_and_picklable():
    template = compile_template("Kunden/{mandant}/*/**")
    assert isinstance(template, CompiledTemplate)
    assert hash(template) == hash(compile_template("Kunden/{mandant}/*/**"))
    clone = pickle.loads(pickle.dumps(template))
    assert clone == template
    assert clone.match("kunden/Muster AG/x/Brief.pdf") == {"mandant": "Muster AG"}
    assert template.keys == ("mandant",)
