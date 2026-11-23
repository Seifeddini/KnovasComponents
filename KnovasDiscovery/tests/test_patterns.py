from knovas_discovery.probes import fileshare as fs

GUID = "7b9859a2-70f9-5199-ab0b-906686e0be31"


def test_autodoc_name():
    assert fs.is_autodoc_name(f"{GUID}_2008-013_Entwurf.docx")
    assert fs.is_autodoc_name(f"{{{GUID}}}_123_Brief.pdf")          # with braces
    assert fs.is_autodoc_name(f"{GUID.upper()}_7_a_b.doc")           # upper case, more parts
    assert not fs.is_autodoc_name(f"{GUID}_2008-013.docx")           # one part only
    assert not fs.is_autodoc_name("Vertrag final (2).docx")
    assert not fs.is_autodoc_name(f"x{GUID}_1_2.doc")


def test_range_folder():
    assert fs.is_range_folder("000501-001000")
    assert not fs.is_range_folder("0501-1000")
    assert not fs.is_range_folder("000501-001000 alt")


def test_lock_outlook_dictation():
    assert fs.is_lock_file("~$rienplan 2026.xlsx")
    assert not fs.is_lock_file("Ferienplan.xlsx")
    assert fs.is_outlook_archive("Archiv bis 2024.PST") and fs.is_outlook_archive("x.ost")
    assert fs.is_dictation("d.DSS") and fs.is_dictation("d.ds2") and fs.is_dictation("d.dvf")
    assert not fs.is_dictation("d.wav")


def test_scanner_file():
    assert fs.is_scanner_file("Scan_20261116_083412.pdf")
    assert fs.is_scanner_file("IMG-4471.pdf")
    assert fs.is_scanner_file("scn0001.TIFF")
    assert fs.is_scanner_file("img_2202.jpg")
    assert not fs.is_scanner_file("Scan_20261116.docx")       # not a scanner format
    assert not fs.is_scanner_file("Scanner-Treiber.exe")
    assert not fs.is_scanner_file("Image of the office.jpg")    # no number after the prefix


def test_extension_and_readable():
    assert fs.extension("A.DOCX") == ".docx"
    assert fs.extension("noext") == ""
    assert fs.extension("archive.tar.gz") == ".gz"
    assert all(fs.is_connector_readable(f"x{e}") for e in (".md", ".txt", ".docx", ".pdf", ".eml", ".msg"))
    assert not fs.is_connector_readable("x.doc")


def test_age_band():
    assert fs.age_band(0) == "<1y"
    assert fs.age_band(364) == "<1y"
    assert fs.age_band(365) == "1-5y"
    assert fs.age_band(5 * 365) == "5-10y"
    assert fs.age_band(10 * 365) == ">10y"
