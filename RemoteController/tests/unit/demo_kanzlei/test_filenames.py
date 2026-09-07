from demo_kanzlei.filenames import (
    LETTERHEAD_LABELS,
    broken_filename,
    letterhead_block,
    make_filename,
    parse_autodoc_filename,
    validate_generated_filename,
)


def test_make_filename_uses_exactly_two_separator_underscores():
    name = make_filename(
        guid="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        akten_id="2024-017",
        typ="Brief-Mandant",
        ext="docx",
    )
    assert name == "a1b2c3d4-e5f6-7890-abcd-ef1234567890_2024-017_Brief-Mandant.docx"
    assert name.count("_") == 2


def test_typ_may_contain_underscores():
    name = make_filename(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        akten_id="2024-017",
        typ="Brief_Gegenanwalt",
        ext="pdf",
    )
    parsed = parse_autodoc_filename(name)
    assert parsed["akten_id"] == "2024-017"
    assert parsed["doc_type"] == "Brief_Gegenanwalt"


def test_rejects_underscore_in_guid_or_akten_id():
    try:
        make_filename("bad_guid", "2024-017", "Brief", "pdf")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "underscore" in str(exc).lower()
    try:
        make_filename("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "2024_017", "Brief", "pdf")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "underscore" in str(exc).lower()


def test_letterhead_block_matches_spec():
    block = letterhead_block(
        unser_zeichen="2024-017 / MB-lz",
        aktenzeichen="2024-017",
        in_sachen="Meierhans Bau AG ./. Rüegg",
    )
    assert "Unser Zeichen:   2024-017 / MB-lz" in block
    assert "Aktenzeichen:    2024-017" in block
    assert "In Sachen:       Meierhans Bau AG ./. Rüegg" in block
    for label in LETTERHEAD_LABELS:
        assert label in block


def test_broken_filename_parses_to_missing_akten_id():
    name = broken_filename("Eingang-Post-2024-03.pdf")
    assert "_" not in name.split(".")[0] or name.count("_") < 2
    parsed = parse_autodoc_filename(name)
    assert parsed["akten_id"] is None


def test_validate_generated_filename_accepts_hyphen_version_suffix_in_typ():
    name = make_filename(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        akten_id="2024-017",
        typ="Klage-v2",
        ext="docx",
    )
    validate_generated_filename(name)
