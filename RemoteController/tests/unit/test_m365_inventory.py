"""The inventory tracks items by id, so renames and moves need no special cases."""
from __future__ import annotations

import json

import pytest

from m365.inventory import DELTA_SELECT, DriveInventory, InventoryError
from onedrive_mirror.graph import DeltaTokenInvalid, GraphRequestError

T = "2026-01-02T03:04:05Z"


def folder(item_id, name, parent):
    return {"id": item_id, "name": name, "folder": {}, "parentReference": {"id": parent},
            "lastModifiedDateTime": T}


def file(item_id, name, parent, size=10, modified=T, url=None):
    return {"id": item_id, "name": name, "file": {}, "size": size, "lastModifiedDateTime": modified,
            "parentReference": {"id": parent}, "webUrl": url or f"https://x/{name}"}


def deleted(item_id):
    # What OneDrive for Business sends: no name, no path.
    return {"id": item_id, "deleted": {"state": "deleted"}, "parentReference": {"id": "whatever"}}


ROOT = {"id": "root", "name": "root", "root": {}, "folder": {}}


class FakeDelta:
    def __init__(self):
        self.pages = {}
        self.calls = []
        self.invalid = set()
        self.fail = set()

    def delta_pages(self, drive_id, delta_url=None, *, select=None):
        self.calls.append((delta_url, select))
        key = delta_url or "__initial__"
        if key in self.invalid:
            raise DeltaTokenInvalid("410")
        if key in self.fail:
            raise GraphRequestError("boom")
        yield from self.pages[key]


def make(tmp_path, graph, folder_id="akten", folder_is_root=False):
    return DriveInventory(graph, drive_id="drv", folder_id=folder_id, folder_is_root=folder_is_root,
                          state_path=tmp_path / "inventory.json")


def initial_library():
    return [
        ROOT,
        folder("akten", "Akten", "root"),
        folder("other", "Privat", "root"),
        folder("y24", "2024", "akten"),
        file("f1", "a.pdf", "y24"),
        file("f2", "b.docx", "akten"),
        file("f3", "geheim.pdf", "other"),
    ]


def test_initial_enumeration_lists_only_the_configured_folder(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library()[:3], None), (initial_library()[3:], "L1")]
    inv = make(tmp_path, g)
    inv.refresh()
    assert sorted(inv.files()) == ["2024/a.pdf", "b.docx"]
    assert sorted(inv.folders()) == ["2024"]
    assert g.calls == [(None, DELTA_SELECT)]
    f = inv.files()["2024/a.pdf"]
    assert (f.item_id, f.drive_id, f.size, f.modified_iso, f.web_url) == ("f1", "drv", 10, T, "https://x/a.pdf")


def test_children_before_parents_still_resolve(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(list(reversed(initial_library())), "L1")]
    inv = make(tmp_path, g)
    inv.refresh()
    assert sorted(inv.files()) == ["2024/a.pdf", "b.docx"]


def test_incremental_changes_rename_move_delete(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    g.pages["L1"] = [([
        folder("y24", "2024-abgeschlossen", "akten"),  # rename: subtree follows
        file("f3", "geheim.pdf", "akten"),             # moved INTO scope
        deleted("f2"),                                  # deleted, reported without a name
        file("f4", "neu.txt", "y24"),                   # added
    ], "L2")]
    inv = make(tmp_path, g)
    inv.refresh()
    inv.refresh()
    assert sorted(inv.files()) == ["2024-abgeschlossen/a.pdf", "2024-abgeschlossen/neu.txt", "geheim.pdf"]
    assert g.calls[-1][0] == "L1"


def test_folder_moved_into_scope_brings_unreported_children(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    # Only the folder is reported when it moves; its file is not.
    g.pages["L1"] = [([folder("other", "Privat", "akten")], "L2")]
    inv = make(tmp_path, g)
    inv.refresh()
    inv.refresh()
    assert "Privat/geheim.pdf" in inv.files()


def test_state_survives_restart_and_resumes_from_the_saved_link(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    make(tmp_path, g).refresh()
    saved = json.loads((tmp_path / "inventory.json").read_text())
    assert saved["delta_link"] == "L1" and "f1" in saved["items"]
    assert (tmp_path / "inventory.json").stat().st_mode & 0o777 == 0o600

    g2 = FakeDelta()
    g2.pages["L1"] = [([], "L2")]
    inv = make(tmp_path, g2)
    assert sorted(inv.files()) == ["2024/a.pdf", "b.docx"]  # usable before any call
    inv.refresh()
    assert g2.calls == [("L1", DELTA_SELECT)]


def test_state_of_another_library_is_ignored(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    make(tmp_path, g).refresh()
    other = DriveInventory(FakeDelta(), drive_id="other-drive", folder_id="akten", folder_is_root=False,
                           state_path=tmp_path / "inventory.json")
    with pytest.raises(InventoryError):
        other.files()


def test_expired_position_rereads_everything_and_drops_vanished_items(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    inv = make(tmp_path, g)
    inv.refresh()
    g.invalid.add("L1")
    g.pages["__initial__"] = [([ROOT, folder("akten", "Akten", "root"), file("f2", "b.docx", "akten")], "L9")]
    inv.refresh()
    assert sorted(inv.files()) == ["b.docx"]


def test_failed_refresh_keeps_last_good_view_and_link(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    inv = make(tmp_path, g)
    inv.refresh()
    g.fail.add("L1")
    with pytest.raises(InventoryError):
        inv.refresh()
    assert sorted(inv.files()) == ["2024/a.pdf", "b.docx"]
    g.fail.clear()
    g.pages["L1"] = [([], "L2")]
    inv.refresh()
    assert g.calls[-1][0] == "L1"


def test_feed_without_final_link_is_not_trusted(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), None)]
    inv = make(tmp_path, g)
    with pytest.raises(InventoryError):
        inv.refresh()
    with pytest.raises(InventoryError, match="not been read"):
        inv.files()


def test_deleting_the_configured_folder_refuses_instead_of_emptying(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    g.pages["L1"] = [([deleted("akten")], "L2")]
    inv = make(tmp_path, g)
    inv.refresh()
    with pytest.raises(InventoryError, match="no longer exists"):
        inv.refresh()
    assert sorted(inv.files()) == ["2024/a.pdf", "b.docx"]  # nothing was dropped


def test_library_root_as_folder(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    inv = make(tmp_path, g, folder_id="root", folder_is_root=True)
    inv.refresh()
    assert sorted(inv.files()) == ["Akten/2024/a.pdf", "Akten/b.docx", "Privat/geheim.pdf"]


def test_unsafe_names_and_packages_are_skipped(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [([
        ROOT, folder("akten", "Akten", "root"),
        file("bad", "..", "akten"),
        {"id": "pkg", "name": "Notizbuch", "package": {"type": "oneNote"}, "parentReference": {"id": "akten"}},
        folder("loop1", "L1", "loop2"), folder("loop2", "L2", "loop1"), file("inloop", "x.pdf", "loop1"),
    ], "L1")]
    inv = make(tmp_path, g)
    inv.refresh()
    assert inv.files() == {}


def test_an_unchanged_cycle_does_not_rewrite_the_inventory(tmp_path):
    g = FakeDelta()
    g.pages["__initial__"] = [(initial_library(), "L1")]
    g.pages["L1"] = [([], "L2")]
    inv = make(tmp_path, g)
    inv.refresh()
    before = (tmp_path / "inventory.json").stat().st_mtime_ns
    inv.refresh()
    assert (tmp_path / "inventory.json").stat().st_mtime_ns == before
    assert json.loads((tmp_path / "inventory.json").read_text())["delta_link"] == "L1"
