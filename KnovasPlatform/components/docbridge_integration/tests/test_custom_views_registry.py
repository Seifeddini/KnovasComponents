"""Custom views: the registry and safe file reads (SS-625)."""

import hashlib
import json
import logging
import os
import shutil
import threading

import pytest

from custom_views import registry
from custom_views.manifest import ManifestError
from custom_views.registry import Invalid, Registry, folder_refusal, read_view_file
from custom_views.settings import CustomViewsSettings

KB = 1024
MB = 1024 * KB


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


@pytest.fixture
def root(tmp_path, monkeypatch):
    """The views folder, not writable for the Platform (as a read-only mount on a server)."""
    folder = tmp_path / "views"
    folder.mkdir()
    real_access = os.access

    def access(path, mode, *args, **kwargs):
        if mode & os.W_OK and _same(path, folder):
            return False
        return real_access(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "access", access)
    return folder


def settings(folder, **changes):
    return CustomViewsSettings(enabled=True, dir=str(folder), **changes)


def make_view(parent, view_id, files=None, **manifest):
    """A view folder with a valid manifest; ``files`` maps names to str/bytes (default: one entry file)."""
    data = {
        "format": 1,
        "title": {"de": f"Ansicht {view_id}", "en": f"View {view_id}"},
        "roles": ["*"],
        "entry": "index.html",
        "reads": {"Project": ["status"]},
    }
    data.update(manifest)
    folder = parent / view_id
    folder.mkdir()
    (folder / "view.json").write_text(json.dumps(data), encoding="utf-8")
    for name, content in ({"index.html": "<p>Hallo</p>"} if files is None else files).items():
        (folder / name).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return folder


def ids(views):
    return [v.manifest.id for v in views]


def test_no_folder_means_no_views(tmp_path):
    reg = Registry(settings(tmp_path / "missing"))
    assert reg.views() == []
    assert reg.invalid() == []
    assert reg.get("anything") is None
    assert reg.for_roles(frozenset({"admin"})) == []
    assert reg.refusal() == "Ordner für eigene Ansichten fehlt."


def test_a_valid_view_is_listed_with_fingerprints(root):
    folder = make_view(
        root,
        "overview",
        files={"index.html": "<p>Übersicht</p>", "app.js": "knovas.ready(() => {});", "app.css": "p { margin: 0 }",
               "notes.txt": "not named in the manifest"},
        roles=["admin"],
        scripts=["app.js"],
        styles=["app.css"],
    )
    reg = Registry(settings(root))

    [view] = reg.views()
    assert view.manifest.id == "overview"
    assert set(view.files) == {"view.json", "index.html", "app.js", "app.css"}
    for name, file in view.files.items():
        data = (folder / name).read_bytes()
        assert file.name == name
        assert file.data == data
        assert file.sha256 == hashlib.sha256(data).hexdigest()
        assert file.mtime == os.stat(folder / name).st_mtime
    with pytest.raises(TypeError):
        view.files["other.js"] = view.files["app.js"]

    assert reg.get("overview") is view
    assert reg.get("other") is None
    assert reg.for_roles(frozenset({"admin"})) == [view]
    assert reg.for_roles(frozenset({"user"})) == []
    assert reg.invalid() == []
    assert reg.refusal() is None


def test_an_invalid_view_is_skipped_with_its_reason_and_never_hides_a_valid_one(root, caplog):
    caplog.set_level(logging.INFO, logger="custom_views.registry")
    broken = make_view(root, "a-broken")
    (broken / "view.json").write_bytes(b"{not json")
    make_view(root, "b-good")
    (root / "c-no-manifest").mkdir()

    reg = Registry(settings(root))

    assert ids(reg.views()) == ["b-good"]
    assert reg.invalid() == [
        Invalid("a-broken", "Die Datei view.json ist kein gültiges JSON."),
        Invalid("c-no-manifest", "Datei „view.json“ fehlt."),
    ]
    assert reg.get("a-broken") is None
    assert "custom_views: a-broken invalid: Die Datei view.json ist kein gültiges JSON." in caplog.messages


def test_ids_outside_the_pattern_are_ignored(root):
    for name in ("Upper", "-lead", "under_score", "dot.ted", "a" * 41, ".hidden"):
        make_view(root, name)
    (root / "loose").write_text("a file, not a folder", encoding="utf-8")
    make_view(root, "a" * 40)
    make_view(root, "ok-1")

    reg = Registry(settings(root))

    assert ids(reg.views()) == ["a" * 40, "ok-1"]
    assert reg.invalid() == []


def test_a_file_over_512_kb_refuses_the_view(root):
    make_view(root, "big", files={"index.html": b"x" * (512 * KB + 1)})
    make_view(root, "edge", files={"index.html": b"x" * (512 * KB)})

    reg = Registry(settings(root))

    assert ids(reg.views()) == ["edge"]
    assert reg.invalid() == [Invalid("big", "Datei „index.html“ ist grösser als 512 KB.")]


def test_a_view_over_2_mb_is_refused(root):
    scripts = ["a.js", "b.js", "c.js", "d.js"]

    def build(view_id, extra):
        folder = make_view(root, view_id, files={}, scripts=scripts)
        manifest_size = (folder / "view.json").stat().st_size
        (folder / "index.html").write_bytes(b"<p></p>")
        for name in scripts[:3]:
            (folder / name).write_bytes(b"x" * (512 * KB))
        # view.json + every named file add up to exactly 2 MB + extra.
        (folder / "d.js").write_bytes(b"x" * (2 * MB - manifest_size - 7 - 3 * 512 * KB + extra))

    build("at-limit", 0)
    build("over-limit", 1)  # over only because view.json counts too

    reg = Registry(settings(root))

    assert ids(reg.views()) == ["at-limit"]
    assert reg.invalid() == [Invalid("over-limit", "Die Ansicht ist grösser als 2 MB.")]


def test_more_than_50_views_keeps_the_first_50_by_id(root, caplog):
    caplog.set_level(logging.INFO, logger="custom_views.registry")
    for n in range(51):
        order = {50: -1, 1: 2, 2: 1}.get(n)
        make_view(root, f"v{n:02d}", **({"order": order} if order is not None else {}))

    reg = Registry(settings(root))

    expected = ["v02", "v01", "v00"] + [f"v{n:02d}" for n in range(3, 50)]
    assert ids(reg.views()) == expected
    assert reg.get("v50") is None
    assert reg.invalid() == []
    assert "custom_views: more than 50 views; skipped 1 folder(s) from v50 on" in caplog.messages


def test_a_symlinked_file_or_folder_is_refused(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("<p>secret</p>", encoding="utf-8")
    elsewhere = make_view(outside, "elsewhere")
    linked_file = make_view(root, "linked-file", files={})
    try:
        os.symlink(outside / "index.html", linked_file / "index.html")
    except OSError:
        pytest.skip("symbolic links are not permitted here")
    os.symlink(elsewhere, root / "linked-folder", target_is_directory=True)
    make_view(root, "plain")

    reg = Registry(settings(root))

    assert ids(reg.views()) == ["plain"]
    assert reg.invalid() == [
        Invalid("linked-file", "Datei „index.html“ ist ein Verweis."),
        Invalid("linked-folder", "Ordner der Ansicht ist ein Verweis."),
    ]
    with pytest.raises(ManifestError):
        read_view_file(str(linked_file), "index.html", MB)

    linked_root = tmp_path / "linked-root"
    os.symlink(root, linked_root, target_is_directory=True)
    assert folder_refusal(str(linked_root), None) == "Ordner für eigene Ansichten ist ein Verweis."
    assert folder_refusal(str(linked_root) + os.sep, None) == "Ordner für eigene Ansichten ist ein Verweis."


@pytest.mark.skipif(os.name != "nt", reason="junctions exist on Windows only")
def test_a_junction_is_refused_on_windows(root, tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    elsewhere = make_view(outside, "elsewhere")
    _winapi.CreateJunction(str(elsewhere), str(root / "joined"))
    make_view(root, "plain")

    reg = Registry(settings(root))

    assert ids(reg.views()) == ["plain"]
    assert reg.invalid() == [Invalid("joined", "Ordner der Ansicht ist ein Verweis.")]

    joined_root = tmp_path / "joined-root"
    _winapi.CreateJunction(str(root), str(joined_root))
    assert folder_refusal(str(joined_root), None) == "Ordner für eigene Ansichten ist ein Verweis."
    joined_parent = tmp_path / "joined-parent"
    _winapi.CreateJunction(str(tmp_path / "outside"), str(joined_parent))
    assert folder_refusal(str(joined_parent / "elsewhere"), None) == "Ordner für eigene Ansichten ist ein Verweis."


def test_a_file_named_in_the_manifest_but_missing_refuses_the_view(root):
    make_view(root, "missing", scripts=["gone.js"])
    folder = make_view(root, "not-a-file", styles=["dir.css"])
    (folder / "dir.css").mkdir()

    reg = Registry(settings(root))

    assert reg.views() == []
    assert reg.invalid() == [
        Invalid("missing", "Datei „gone.js“ fehlt."),
        Invalid("not-a-file", "Datei „dir.css“ ist keine gewöhnliche Datei."),
    ]


def test_read_view_file_takes_only_plain_names_directly_in_the_folder(root, tmp_path):
    folder = make_view(root, "plain")
    (tmp_path / "outside.html").write_text("<p>secret</p>", encoding="utf-8")
    (folder / "sub").mkdir()
    (folder / "sub" / "inner.html").write_text("<p>inner</p>", encoding="utf-8")

    for name in ("../../outside.html", str(tmp_path / "outside.html"), "sub/inner.html", "sub\\inner.html",
                 "..", ".", "", ".hidden"):
        with pytest.raises(ManifestError):
            read_view_file(str(folder), name, MB)

    file = read_view_file(str(folder), "index.html", MB)
    assert file.data == b"<p>Hallo</p>"
    with pytest.raises(ManifestError, match="grösser als"):
        read_view_file(str(folder), "index.html", 5)


def test_a_rescan_happens_only_after_30_seconds(root):
    clock = Clock()
    make_view(root, "first")
    reg = Registry(settings(root), clock=clock)
    assert ids(reg.views()) == ["first"]

    make_view(root, "second")
    clock.now = 29.9
    assert ids(reg.views()) == ["first"]
    clock.now = 30.0
    assert ids(reg.views()) == ["first", "second"]


def test_a_changed_file_shows_a_new_fingerprint_and_is_logged(root, caplog):
    caplog.set_level(logging.INFO, logger="custom_views.registry")
    clock = Clock()
    folder = make_view(root, "board", files={"index.html": "<p>alt</p>"})
    reg = Registry(settings(root), clock=clock)
    before = reg.get("board")

    (folder / "index.html").write_text("<p>NEU-INHALT</p>", encoding="utf-8")
    clock.now = 10.0
    assert reg.get("board") is before  # one snapshot until the next rescan

    caplog.clear()
    clock.now = 30.0
    after = reg.get("board")
    new_sha = hashlib.sha256(b"<p>NEU-INHALT</p>").hexdigest()
    assert after.files["index.html"].sha256 == new_sha
    assert after.files["index.html"].data == b"<p>NEU-INHALT</p>"
    assert before.files["index.html"].data == b"<p>alt</p>"
    assert caplog.messages == [f"custom_views: board/index.html changed sha256={new_sha[:12]}"]
    assert "NEU-INHALT" not in caplog.text

    shutil.rmtree(folder)
    clock.now = 60.0
    assert reg.views() == []
    assert "custom_views: board removed" in caplog.messages


def test_a_reader_during_a_rescan_gets_the_last_complete_scan(root, monkeypatch):
    clock = Clock()
    make_view(root, "steady")
    reg = Registry(settings(root), clock=clock)
    first = reg.get("steady")

    entered, release = threading.Event(), threading.Event()
    real_parse = registry.parse_manifest

    def slow_parse(view_id, raw):
        entered.set()
        assert release.wait(5)
        return real_parse(view_id, raw)

    monkeypatch.setattr(registry, "parse_manifest", slow_parse)
    clock.now = 30.0
    result = {}
    scanner = threading.Thread(target=lambda: result.update(views=reg.views()))
    scanner.start()
    try:
        assert entered.wait(5)
        assert reg.get("steady") is first  # neither waits for the running scan nor starts a second one
    finally:
        release.set()
        scanner.join(5)
    [view] = result["views"]
    assert view is not first and view.manifest.id == "steady"
    assert reg.get("steady") is view


def test_folder_refusal_inside_documents_and_writable(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_CAN_CHECK_WRITE", True)  # the POSIX path, on any OS
    documents = tmp_path / "documents"
    inside = documents / "views"
    inside.mkdir(parents=True)
    in_documents = "Ordner für eigene Ansichten liegt im Dokumentenordner."
    assert folder_refusal(str(inside), str(documents)) == in_documents
    assert folder_refusal(str(documents), str(documents)) == in_documents
    assert folder_refusal(str(tmp_path), str(documents)) == "Ordner für eigene Ansichten enthält den Dokumentenordner."

    # A real folder the test's user may write (on Windows os.access says so for every folder).
    writable = tmp_path / "writable"
    writable.mkdir()
    make_view(writable, "valid")
    is_writable = "Ordner für eigene Ansichten ist für die Plattform beschreibbar."
    assert folder_refusal(str(writable), str(documents)) == is_writable
    reg = Registry(settings(writable))
    assert reg.views() == []
    assert reg.get("valid") is None
    assert reg.refusal() == is_writable

    # The same folder once the Platform may not write it (a read-only mount on a server).
    real_access = os.access
    monkeypatch.setattr(os, "access", lambda path, mode, *a, **k: False if mode & os.W_OK else real_access(path, mode))
    assert folder_refusal(str(writable), str(documents)) is None
    reg = Registry(settings(writable))
    assert ids(reg.views()) == ["valid"]
    assert reg.refusal() is None


def test_an_unsafe_or_undecodable_view_is_invalid(root):
    """The document's checks (SS-626) run in the scan: an unsafe entry or a non-UTF-8 file makes the view invalid."""
    make_view(root, "a-script", files={"index.html": "<p>x</p><script>alert(1)</script>"})
    make_view(root, "b-handler", files={"index.html": '<img src="data:image/png," onerror="f()">'})
    make_view(root, "c-latin1", files={"index.html": "<p>Grüsse</p>".encode("latin-1")})
    make_view(root, "d-script-latin1", files={"index.html": "<p>x</p>", "app.js": "var s = 'ä';".encode("latin-1")},
              scripts=["app.js"])
    make_view(root, "e-good", files={"index.html": "<p>x</p>", "app.js": "var s = '</script>';"}, scripts=["app.js"])

    reg = Registry(settings(root))

    assert ids(reg.views()) == ["e-good"]
    assert reg.invalid() == [
        Invalid("a-script", "Die Einstiegsdatei (entry) darf kein Element „script“ enthalten."),
        Invalid("b-handler", "Die Einstiegsdatei (entry) darf keine Ereignis-Attribute („on…“) enthalten."),
        Invalid("c-latin1", "Datei „index.html“ ist nicht UTF-8-kodiert."),
        Invalid("d-script-latin1", "Datei „app.js“ ist nicht UTF-8-kodiert."),
    ]


def test_on_windows_the_write_protection_is_not_checked_and_the_log_says_so_once(tmp_path, monkeypatch, caplog):
    """os.access says every folder is writable on Windows; the check would refuse every development folder."""
    caplog.set_level(logging.INFO, logger="custom_views.registry")
    monkeypatch.setattr(registry, "_CAN_CHECK_WRITE", False)  # the Windows path, on any OS
    monkeypatch.setattr(os, "access", lambda path, mode, *a, **k: True)
    folder = tmp_path / "views"
    folder.mkdir()
    make_view(folder, "valid")

    assert folder_refusal(str(folder), None) is None
    # Every other refusal still holds.
    documents = tmp_path / "documents"
    (documents / "inner").mkdir(parents=True)
    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    assert folder_refusal(str(tmp_path / "missing"), None) == "Ordner für eigene Ansichten fehlt."
    assert folder_refusal(str(tmp_path / "a-file"), None) == "Ordner für eigene Ansichten ist kein Ordner."
    assert folder_refusal(str(documents / "inner"), str(documents)) == "Ordner für eigene Ansichten liegt im Dokumentenordner."
    assert folder_refusal(str(tmp_path), str(documents)) == "Ordner für eigene Ansichten enthält den Dokumentenordner."

    clock = Clock()
    reg = Registry(settings(folder), clock=clock)
    assert ids(reg.views()) == ["valid"]
    assert reg.refusal() is None
    clock.now = 30.0
    assert ids(reg.views()) == ["valid"]  # a second scan

    def warnings():
        return [r for r in caplog.records if r.levelno == logging.WARNING and "write protection" in r.getMessage()]

    assert [r.getMessage() for r in warnings()] == [
        "custom_views: the views folder's write protection cannot be checked on Windows; "
        "make sure the Platform cannot write it"
    ]
    Registry(settings(folder)).views()
    assert len(warnings()) == 2  # once per Registry
