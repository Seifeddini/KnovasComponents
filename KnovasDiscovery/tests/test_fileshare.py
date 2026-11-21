import builtins
import io
import os

import pytest

from knovas_discovery.probes import Context, fileshare as fs
from knovas_discovery.secrets import Secrets
from knovas_discovery.util import ProbeError

from conftest import NOW, set_age

GUID = "7b9859a2-70f9-5199-ab0b-906686e0be31"


def write(path, size=10, days=1.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    set_age(path, days)
    return path


@pytest.fixture
def share(tmp_path):
    root = tmp_path / "share" / "Firm" / "Store"
    write(root / "000001-000500" / f"{GUID}_2020-001_Brief.docx", 100, days=10)
    write(root / "000001-000500" / f"{GUID}_2020-001_Notiz.pdf", 0, days=400)          # empty, 1-5y
    write(root / "000001-000500" / "~$Brief.docx", 162, days=2)                         # lock file, readable ext
    write(root / "000001-000500" / "Diktat.DSS", 50, days=2000)                         # 5-10y
    write(root / "000501-001000" / "Scan_20240101_0800.pdf", 70, days=4000)             # >10y
    write(root / "000501-001000" / "IMG-1234.jpg", 80, days=3)
    write(root / "000501-001000" / "README", 5, days=3)                                 # no extension
    write(root / "000501-001000" / "Archiv.pst", 900, days=30)
    write(root / "Nested" / "deeper" / "deepest" / "note.txt", 20, days=5)
    write(root / "root-file.msg", 30, days=6)
    return tmp_path / "share", "Firm/Store"


def test_facts_of_a_tree(share):
    path, root = share
    facts, detail, status, error = fs.scan_root(str(path), root, NOW)
    assert (status, error) == ("ok", None)
    assert facts["files"] == 10
    assert facts["bytes"] == 100 + 0 + 162 + 50 + 70 + 80 + 5 + 900 + 20 + 30
    assert facts["folders"] == 5          # two ranges, Nested, deeper, deepest
    assert facts["max_depth"] == 3
    assert facts["extensions"] == {"": 1, ".docx": 2, ".dss": 1, ".jpg": 1, ".msg": 1, ".pdf": 2, ".pst": 1, ".txt": 1}
    assert facts["connector_readable_files"] == 6   # 2 docx (one a lock file), 2 pdf, txt, msg
    assert facts["connector_readable_share"] == 0.6
    assert facts["age"] == {"newest_days": 2, "oldest_days": 4000,
                            "bands": {"<1y": 7, "1-5y": 1, "5-10y": 1, ">10y": 1}}
    assert facts["refused_folders"] == 0
    assert facts["patterns"] == {"autodoc_names_share": 0.2, "range_folders": 2, "office_lock_files": 1,
                                 "outlook_archives": 1, "dictation_files": 1, "scanner_files": 2,
                                 "no_extension_files": 1, "long_paths": 0, "empty_files": 1}
    assert facts["largest_file_bytes"] == 900
    assert facts["first_read_forecast"] == {"files_to_upload": 6, "hours_at_4_per_minute": 0.0}
    assert set(facts["listing"]) == {"seconds", "entries_per_second"}
    assert detail["top_folders"] == {"(files in the root)": 1, "000001-000500": 4, "000501-001000": 4, "Nested": 1}
    assert detail["largest_files"][0] == {"path": "Firm/Store/000501-001000/Archiv.pst", "bytes": 900}
    assert len(detail["largest_files"]) == 10


def test_long_paths_count_the_path_below_the_share(share, monkeypatch):
    path, root = share
    longest = f"Firm/Store/000001-000500/{GUID}_2020-001_Brief.docx"
    monkeypatch.setattr(fs, "LONG_PATH", len(longest) - 1)
    facts, *_ = fs.scan_root(str(path), root, NOW)
    assert facts["patterns"]["long_paths"] == 1


def test_never_opens_a_file(share, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the fileshare probe opened a file")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(io, "open", refuse)
    monkeypatch.setattr(os, "open", refuse)
    path, root = share
    facts, *_ = fs.scan_root(str(path), root, NOW)
    assert facts["files"] == 10


def test_refused_folder(share, refused_dir):
    path, root = share
    locked = path / "Firm" / "Store" / "Personal"
    write(locked / "salary.pdf", 10)
    if not refused_dir(locked):
        pytest.skip("this OS or account cannot make a folder unlistable")
    facts, detail, status, _ = fs.scan_root(str(path), root, NOW)
    assert facts["refused_folders"] == 1
    assert facts["folders"] == 6           # the refused folder is seen in its parent
    assert facts["files"] == 10            # its file is not
    assert detail["refused_folders"] == ["Firm/Store/Personal"]
    assert status == "ok"


def test_links_are_not_followed(share):
    path, root = share
    loop = path / "Firm" / "Store" / "Nested" / "back-to-store"
    try:
        os.symlink(path / "Firm" / "Store", loop, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this OS or account cannot create a symbolic link")
    facts, detail, status, _ = fs.scan_root(str(path), root, NOW)
    assert facts["files"] == 10 and facts["folders"] == 5 and status == "ok"
    assert detail["links_not_followed"] == ["Firm/Store/Nested/back-to-store"]


def test_missing_and_refused_root(tmp_path, refused_dir):
    with pytest.raises(ProbeError) as e:
        fs.scan_root(str(tmp_path), "nope", NOW)
    assert e.value.code == "root_not_found"
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "inside.pdf").write_bytes(b"x")
    if refused_dir(locked):
        # A root that refuses the account is a finding: one refused folder, nothing seen, status ok.
        facts, detail, status, error = fs.scan_root(str(tmp_path), "locked", NOW)
        assert (status, error) == ("ok", None)
        assert (facts["files"], facts["folders"], facts["refused_folders"]) == (0, 0, 1)
        assert facts["age"]["newest_days"] is None
        assert detail["refused_folders"] == ["locked"]


def test_run_gives_one_result_per_root_and_survives_a_bad_root(share):
    path, root = share
    ctx = Context(now=NOW, secrets=Secrets(environ={}, interactive=False))
    outs = fs.run({"key": "s", "path": str(path), "roots": [root, "Firm/Missing", "Firm\\Store\\Nested"]}, ctx)
    assert [o.result["key"] for o in outs] == ["s:Firm/Store", "s:Firm/Missing", "s:Firm/Store/Nested"]
    assert [o.result["status"] for o in outs] == ["ok", "failed", "ok"]
    assert outs[1].result["error"] == "root_not_found"
    assert outs[2].result["facts"]["files"] == 1
    whole = fs.run({"key": "s", "path": str(path)}, ctx)
    assert whole[0].result["key"] == "s" and whole[0].result["facts"]["files"] == 10
