"""The Ingestion tab: one form, one write, and a preview before either."""

from __future__ import annotations

import inspect
import pathlib

import pytest

flask = pytest.importorskip("flask")

from conftest import platform_db_reachable

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"


class FakeRemoteControllerClient:
    """A recording RemoteController, as DummyKnovasClient is a recording
    Knovas. The tab writes another service's configuration as the signed-in
    person, so the route tests have to drive that seam, not mock past it."""

    last_instance = None
    #: Document fields (spec 3.8). None leaves the key out of status(), the
    #: way an older RemoteController answers. Tests set these with
    #: monkeypatch on the class, so every test starts from the old shape.
    capabilities_advertised = None
    doc_fields_block = None
    document_sync = None
    requeue_answer = 0
    #: Extra /discover answers by root, for the template preview.
    extra_entries: dict = {}

    def __init__(self, base_url, *, principal_broker=None, session=None, timeout=20.0):
        self.base_url = base_url
        self.principal_broker = principal_broker
        self.calls: list[str] = []
        self.pushed: list = []
        self.discover_args: list[dict] = []
        self.discover_error = None
        self.requeued: list[str] = []
        FakeRemoteControllerClient.last_instance = self

    def count(self, name: str) -> int:
        return self.calls.count(name)

    def discover(self, root=None, max_depth=3):
        self.calls.append("discover")
        self.discover_args.append({"root": root, "max_depth": max_depth})
        if self.discover_error:
            from remote_controller_client import RemoteControllerError
            raise RemoteControllerError(self.discover_error)
        scan_root = root or "/data/corpus"
        by_root = {
            "/data/corpus": [
                {"type": "directory", "name": "Mandate", "path": "Mandate"},
                {"type": "file", "name": "a.docx", "path": "a.docx"},
                {"type": "directory", "name": "Allgemein", "path": "Allgemein"},
            ],
            "/data/corpus/Mandate": [
                {"type": "directory", "name": "2024-017", "path": "2024-017"},
            ],
        }
        by_root.update(self.extra_entries)
        return {
            "root": scan_root,
            "truncated": False,
            "entries": by_root.get(scan_root, []),
        }

    def status(self):
        self.calls.append("status")
        out = {"scheduler_state": "not_running", "files_synced_local": 0}
        if self.capabilities_advertised is not None:
            out["capabilities"] = list(self.capabilities_advertised)
        if self.doc_fields_block is not None:
            out["doc_fields"] = self.doc_fields_block
        if self.document_sync is not None:
            out["document_sync"] = self.document_sync
        return out

    def capabilities(self):
        self.calls.append("capabilities")
        return frozenset(self.capabilities_advertised or ())

    def requeue_doc_fields(self, outcome):
        self.calls.append("requeue_doc_fields")
        self.requeued.append(outcome)
        return self.requeue_answer

    def start(self):
        self.calls.append("start")
        return {"scheduler_status": "running"}

    def stop(self):
        self.calls.append("stop")
        return {"scheduler_status": "not_running"}

    def get_sync_config(self):
        self.calls.append("get_sync_config")
        return {"mode": "continuous"}

    def push(self, compiled):
        self.calls.append("push")
        self.pushed.append(compiled)
        return {"applied": "started"}


def _logout(client):
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
    client.post("/logout", data={"csrf_token": token})


SAVE_FORM = {
    "identifier_prefix": "kanzlei",
    "schedule": "nightly",
    "throughput": "normal",
    "file_types": "documents",
    "folder-0-path": "/mnt/autodoc/mandate",
    "folder-0-recursive": "1",
}


class TestShape:
    def test_routes_are_gated_and_posts_check_csrf_first(self):
        """Order, not presence: a CSRF check after the state change is not a
        CSRF check. Mirrors the Freigaben shape test."""
        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        assert src.count("@bp.route") == src.count("@require_ingestion")
        first_state_change = {
            "def preview(": "rc_client_factory()",
            "def save(": "run_guarded(",
            "def restore(": "run_guarded(",
            "def start(": "rc_client_factory().start()",
            "def stop(": "run_guarded(",
            "def requeue_doc_fields(": "rc_client_factory()",
            "def template_preview_json(": "rc_client_factory()",
        }
        for fn, marker in first_state_change.items():
            start = src.index(fn)
            end = src.find("@bp.route", start)
            body = src[start:end if end != -1 else len(src)]
            assert body.index("csrf_ok") < body.index(marker), fn

    def test_save_and_stop_are_guarded_start_and_preview_are_not(self):
        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        for fn in ("def save(", "def restore(", "def stop("):
            assert "run_guarded(" in src[src.index(fn):src.index(fn) + 1800], fn
        for fn in ("def start(", "def preview("):
            assert "run_guarded(" not in src[src.index(fn):src.index(fn) + 900], fn

    def test_the_compiler_is_the_only_writer(self):
        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        assert "compile_profile(" in src
        assert "sync_request.schema" not in src and "remote_controller_sync" not in src

    def test_the_folder_tree_is_a_get_under_the_same_gate(self):
        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        assert '@bp.route("/ingestion/folders")' in src
        start = src.index("def folders(")
        end = src.find("@bp.route", start)
        body = src[start:end if end != -1 else len(src)]
        assert "csrf_ok" not in body
        assert "child_folders(" in body


class TestFormParsing:
    def test_folders_rows_become_source_folders(self):
        from web_interface.admin_ingestion import profile_from_form

        form = {
            "identifier_prefix": "kanzlei", "schedule": "nightly", "throughput": "normal",
            "folder-0-path": "/mnt/autodoc/mandate", "folder-0-recursive": "1",
            "folder-1-path": "   ",
            "folder-2-path": "/mnt/autodoc/allgemein",
        }
        lists = {"file_types": ["documents", "email"], "folder-0-groups": ["g-lit"],
                 "folder-2-groups": []}
        p = profile_from_form(form, lists)
        assert [s.path for s in p.sources] == ["/mnt/autodoc/mandate", "/mnt/autodoc/allgemein"]
        assert p.sources[0].access_groups == ("g-lit",) and p.sources[0].recursive is True
        assert p.sources[1].recursive is False
        assert p.file_types == ["documents", "email"] and p.max_document_age_days is None

    def test_a_bad_preset_is_a_form_error_not_a_crash(self):
        from identity.ingestion_compiler import ProfileError
        from web_interface.admin_ingestion import profile_from_form

        with pytest.raises(ProfileError):
            profile_from_form({"identifier_prefix": "k", "schedule": "whenever",
                               "throughput": "normal", "folder-0-path": "/x"}, {})

    def test_a_non_numeric_age_limit_is_a_form_error_not_a_crash(self):
        from identity.ingestion_compiler import ProfileError
        from web_interface.admin_ingestion import profile_from_form

        with pytest.raises(ProfileError):
            profile_from_form({"identifier_prefix": "k", "schedule": "nightly",
                               "throughput": "normal", "folder-0-path": "/x",
                               "max_document_age_days": "dreissig"}, {})

    def test_form_from_request_keeps_input_when_validation_fails(self):
        # A ProfileError re-render must not lose what the person typed --
        # dict(request.form) would keep only the first folder/file type.
        from web_interface.admin_ingestion import form_from_request

        form = {
            "identifier_prefix": "kanzlei", "schedule": "whenever", "throughput": "normal",
            "folder-0-path": "/mnt/autodoc/mandate", "folder-0-recursive": "1",
            "folder-1-path": "/mnt/autodoc/allgemein",
        }
        lists = {"file_types": ["documents", "email"], "folder-0-groups": ["g-lit"]}
        rebuilt = form_from_request(form, lists)
        assert [f["path"] for f in rebuilt["folders"]] == [
            "/mnt/autodoc/mandate", "/mnt/autodoc/allgemein",
        ]
        assert rebuilt["folders"][0]["recursive"] is True
        assert rebuilt["folders"][0]["groups"] == ["g-lit"]
        assert rebuilt["folders"][1]["recursive"] is False
        assert rebuilt["file_types"] == ["documents", "email"]


class TestFoldersFromDiscover:
    """The tree picker lists immediate child folders, never files, and never
    a typed path. RemoteController /discover returns both; the console
    keeps only directories and joins them onto the scanned root."""

    def test_keeps_directories_and_drops_files(self):
        from web_interface.admin_ingestion import folders_from_discover

        out = folders_from_discover({
            "root": "/data/corpus",
            "truncated": False,
            "entries": [
                {"type": "directory", "name": "Mandate", "path": "Mandate"},
                {"type": "file", "name": "readme.txt", "path": "readme.txt"},
                {"type": "directory", "name": "Allgemein", "path": "Allgemein"},
            ],
        })
        assert out["root"] == "/data/corpus"
        assert out["folders"] == [
            {"name": "Mandate", "path": "/data/corpus/Mandate"},
            {"name": "Allgemein", "path": "/data/corpus/Allgemein"},
        ]
        assert out["truncated"] is False

    def test_skips_nested_paths_so_expand_is_one_level(self):
        from web_interface.admin_ingestion import folders_from_discover

        out = folders_from_discover({
            "root": "/data/corpus",
            "entries": [
                {"type": "directory", "name": "2024-017", "path": "Mandate/2024-017"},
                {"type": "directory", "name": "Mandate", "path": "Mandate"},
            ],
        })
        assert [f["path"] for f in out["folders"]] == ["/data/corpus/Mandate"]

    def test_joins_under_a_nested_root(self):
        from web_interface.admin_ingestion import folders_from_discover

        out = folders_from_discover({
            "root": "/data/corpus/Mandate",
            "entries": [
                {"type": "directory", "name": "2024-017", "path": "2024-017"},
            ],
        })
        assert out["folders"] == [
            {"name": "2024-017", "path": "/data/corpus/Mandate/2024-017"},
        ]

    def test_child_folders_asks_discover_for_one_level(self):
        from web_interface.admin_ingestion import child_folders

        class RC:
            def __init__(self):
                self.kwargs = None

            def discover(self, root=None, max_depth=3):
                self.kwargs = {"root": root, "max_depth": max_depth}
                return {"root": "/data/corpus", "entries": [
                    {"type": "directory", "name": "Mandate", "path": "Mandate"},
                ]}

        rc = RC()
        out = child_folders(rc, root=None)
        assert rc.kwargs == {"root": None, "max_depth": 1}
        assert out["folders"][0]["path"] == "/data/corpus/Mandate"


class TestApplyProfile:
    """apply_profile must not duplicate a version when a retried push
    follows a failed one for the same profile (a plan defect, fix round 1)."""

    def test_a_failed_push_leaves_one_unpushed_version_and_a_retry_reuses_it(self, monkeypatch):
        from identity.ingestion_compiler import IngestionProfile, SourceFolder
        from identity.ingestion_profiles import profile_to_json
        from remote_controller_client import RemoteControllerError
        from web_interface import admin_ingestion

        class _FakeVersion:
            def __init__(self, id_, version, profile):
                self.id = id_
                self.version = version
                self.profile = profile
                self.pushed_at = None

        class _FakeRepo:
            def __init__(self):
                self._current = None
                self._next_version = 1
                self.save_calls = 0
                self.mark_pushed_calls = []

            def current(self, name="default"):
                return self._current

            def save_new_version(self, profile, *, name="default", by, approved_by=None):
                self.save_calls += 1
                v = _FakeVersion(f"v{self._next_version}", self._next_version, profile)
                self._next_version += 1
                self._current = v
                return v

            def mark_pushed(self, version_id):
                self.mark_pushed_calls.append(version_id)
                if self._current is not None and self._current.id == version_id:
                    self._current.pushed_at = "2026-09-02T00:00:00"

        class _FailThenSucceedClient:
            def __init__(self):
                self.calls = 0

            def push(self, compiled):
                self.calls += 1
                if self.calls == 1:
                    raise RemoteControllerError("RemoteController nicht erreichbar")
                return {"applied": "started"}

        fake_repo = _FakeRepo()
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: fake_repo)

        profile = IngestionProfile(identifier_prefix="kanzlei",
                                    sources=[SourceFolder(path="/mnt/autodoc/mandate")])
        payload = {"profile": profile_to_json(profile)}
        rc = _FailThenSucceedClient()

        with pytest.raises(RemoteControllerError):
            admin_ingestion.apply_profile(payload, actor=object(), conn=None, rc_client=rc)

        assert fake_repo.save_calls == 1
        first = fake_repo.current()
        assert first is not None and first.pushed_at is None

        result = admin_ingestion.apply_profile(payload, actor=object(), conn=None, rc_client=rc)

        assert fake_repo.save_calls == 1, "the retry must not insert a second version"
        assert rc.calls == 2
        assert fake_repo.mark_pushed_calls[-1] == first.id
        assert result["version"] == first.version
        assert result["applied"] == "started"

    def test_it_carries_the_push_outcome_out_and_into_the_audit_row(self, monkeypatch):
        """C2: "gespeichert und uebertragen" is not the same claim as
        "running". apply_profile must hand the route what push found out."""
        from identity.ingestion_compiler import IngestionProfile, SourceFolder
        from identity.ingestion_profiles import profile_to_json
        from web_interface import admin_ingestion

        class _Version:
            id, version, pushed_at = "v1", 1, None
            profile = None

        class _Repo:
            def current(self, name="default"):
                return None

            def save_new_version(self, profile, *, name="default", by, approved_by=None):
                return _Version()

            def mark_pushed(self, version_id):
                pass

        recorded = []
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: _Repo())
        monkeypatch.setattr(admin_ingestion.audit, "record",
                            lambda conn, **kw: recorded.append(kw))

        class _Client:
            def push(self, compiled):
                return {"applied": "stored", "start_error": "RemoteController nicht erreichbar"}

        profile = IngestionProfile(identifier_prefix="kanzlei",
                                   sources=[SourceFolder(path="/mnt/autodoc/mandate")])
        out = admin_ingestion.apply_profile({"profile": profile_to_json(profile)},
                                            actor=object(), conn=None, rc_client=_Client())
        assert out["applied"] == "stored"
        assert out["start_error"] == "RemoteController nicht erreichbar"
        assert recorded[-1]["detail"]["applied"] == "stored"


class TestTheNoticeSaysWhatHappened:
    """C2: three outcomes, three sentences. "uebertragen" alone told an
    administrator the folder list was live when the running worker had not
    picked it up, or when a manual profile was only stored."""

    def test_started_next_cycle_and_stored_read_differently(self):
        from web_interface.admin_ingestion import _applied_clause

        assert _applied_clause({"applied": "started"}) == "; Abgleich gestartet."
        assert _applied_clause({"applied": "next_cycle"}) == (
            "; wird beim naechsten Durchlauf wirksam.")
        assert _applied_clause({"applied": "stored"}) == (
            "; der Abgleich wird von Hand gestartet.")

    def test_a_failed_start_is_named_in_the_notice(self):
        from web_interface.admin_ingestion import _applied_clause

        text = _applied_clause({"applied": "stored", "start_error": "HTTP 500"})
        assert text.endswith(" Start fehlgeschlagen: HTTP 500")
        assert "der Abgleich wird von Hand gestartet." in text

    def test_a_result_without_an_outcome_does_not_claim_a_start(self):
        from web_interface.admin_ingestion import _applied_clause

        assert _applied_clause({}) == "; der Abgleich wird von Hand gestartet."


class TestTemplate:
    def test_exists_and_every_post_form_has_csrf(self):
        html = (TEMPLATES / "admin_ingestion.html").read_text(encoding="utf-8")
        assert html.count('method="post"') >= 4
        assert html.count('name="csrf_token"') >= html.count('method="post"')

    def test_presets_are_offered_as_choices_not_free_text(self):
        # R-I1: the template renders `value="{{ key }}"` for each preset, so a
        # source-level assertion can't tell a real select from free text --
        # only rendered HTML, built from the real preset tables through the
        # module's own _labelled() helper, proves the ids reach the page.
        import jinja2

        from identity import ingestion_presets as presets
        from web_interface.admin_ingestion import _labelled

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        html = env.get_template("admin_ingestion.html").render(
            app_title="Knovas", company_name="Kanzlei", feedback_url=None,
            console_url="/admin/people", active_nav="admin", csrf_token="t",
            error=None, notice=None, me=None, asset_version="1",
            ingestion_enabled=True,
            form={"identifier_prefix": "kanzlei", "description": "", "schedule": "nightly",
                  "throughput": "normal", "file_types": ["documents"], "max_document_age_days": "",
                  "folders": [{"path": "/mnt/autodoc/mandate", "recursive": True, "groups": ["g-lit"]}]},
            schedules=_labelled(presets.SCHEDULE_PRESETS),
            throughputs=_labelled(presets.THROUGHPUT_PRESETS),
            file_types=_labelled(presets.FILE_TYPE_PRESETS),
            groups=[{"group_id": "g-lit", "name": "Litigation"}],
            status={"scheduler_state": "idle", "files_synced_local": 0}, current=None, versions=[], preview=None,
            support_json=None,
        )
        for preset in ("continuous", "nightly", "manual", "gentle", "normal", "fast"):
            assert f'value="{preset}"' in html

    def test_the_strip_knows_the_tab(self):
        assert "admin.ingestion" in (TEMPLATES / "_admin_tabs.html").read_text(encoding="utf-8")

    def test_it_renders_with_stub_data(self):
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        html = env.get_template("admin_ingestion.html").render(
            app_title="Knovas", company_name="Kanzlei", feedback_url=None,
            console_url="/admin/people", active_nav="admin", csrf_token="t",
            error=None, notice=None, me=None, asset_version="1",
            # R-I2: the Ingestion tab anchor is only drawn when this is true.
            ingestion_enabled=True,
            form={"identifier_prefix": "kanzlei", "description": "", "schedule": "nightly",
                  "throughput": "normal", "file_types": ["documents"], "max_document_age_days": "",
                  "folders": [{"path": "/mnt/autodoc/mandate", "recursive": True, "groups": ["g-lit"]}]},
            schedules={"nightly": {"label": "Nachts", "description": "..."}},
            throughputs={"normal": {"label": "Normal", "description": "..."}},
            file_types={"documents": {"label": "Dokumente", "description": "..."}},
            groups=[{"group_id": "g-lit", "name": "Litigation"}],
            status={"scheduler_state": "idle", "files_synced_local": 0}, current=None, versions=[], preview=None,
            support_json=None,
        )
        assert "/mnt/autodoc/mandate" in html

    def test_the_page_picks_folders_from_a_tree_not_typed_paths(self):
        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        html = env.get_template("admin_ingestion.html").render(
            app_title="Knovas", company_name="Kanzlei", feedback_url=None,
            console_url="/admin/people", active_nav="admin", csrf_token="t",
            error=None, notice=None, me=None, asset_version="1",
            ingestion_enabled=True,
            form={"identifier_prefix": "kanzlei", "description": "", "schedule": "nightly",
                  "throughput": "normal", "file_types": ["documents"], "max_document_age_days": "",
                  "folders": [{"path": "/mnt/autodoc/mandate", "recursive": True, "groups": ["g-lit"]}]},
            schedules={"nightly": {"label": "Nachts", "description": "..."}},
            throughputs={"normal": {"label": "Normal", "description": "..."}},
            file_types={"documents": {"label": "Dokumente", "description": "..."}},
            groups=[{"group_id": "g-lit", "name": "Litigation"}],
            status={"scheduler_state": "idle", "files_synced_local": 0}, current=None, versions=[], preview=None,
            support_json=None,
        )
        assert 'id="folder-tree"' in html
        assert 'id="folder-rows"' in html
        assert 'id="folder-row-template"' in html
        assert "Hinzuf" in html
        assert "Entfernen" in html
        assert 'name="folder-0-path"' in html
        assert "readonly" in html
        assert 'placeholder="/mnt/autodoc' not in html
        source = (TEMPLATES / "admin_ingestion.html").read_text(encoding="utf-8")
        assert "admin_ingestion.js" in source


class TestTabStripVisibility:
    """Fix round 1, item 3: an ingestion_manager without 'admin' must see the
    Ingestion tab (it was wrongly nested inside the admin-only block)."""

    def test_ingestion_manager_without_admin_sees_ingestion_not_people(self):
        import types

        import jinja2

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                                 autoescape=True, undefined=jinja2.StrictUndefined)
        env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/")
        me = types.SimpleNamespace(roles={"ingestion_manager"})
        html = env.get_template("_admin_tabs.html").render(
            admin_tab="ingestion", me=me, ingestion_enabled=True)
        assert "/admin/ingestion" in html
        assert "/admin/people" not in html


class TestExecuteIngestionChange:
    """I1/I2: the executor an approved request runs. The approver clicks, but
    the profile row must name the person who asked, and a pure approver must
    not get as far as inserting one."""

    @staticmethod
    def _profile_payload():
        from identity.ingestion_compiler import IngestionProfile, SourceFolder
        from identity.ingestion_profiles import profile_to_json

        return profile_to_json(IngestionProfile(
            identifier_prefix="kanzlei",
            sources=[SourceFolder(path="/mnt/autodoc/mandate", access_groups=("g-lit",))]))

    class _Version:
        id, version, pushed_at, profile = "v1", 1, None, None

    class _Repo:
        def __init__(self):
            self.saved = []

        def current(self, name="default"):
            return None

        def save_new_version(self, profile, *, name="default", by, approved_by=None):
            self.saved.append((by, approved_by))
            return TestExecuteIngestionChange._Version()

        def mark_pushed(self, version_id):
            pass

    class _Client:
        def __init__(self):
            self.calls = []

        def push(self, compiled):
            self.calls.append("push")
            return {"applied": "started"}

        def stop(self):
            self.calls.append("stop")
            return {"scheduler_status": "not_running"}

    class _Actor:
        def __init__(self, id_, roles):
            self.id, self.roles = id_, roles

    def test_the_version_records_the_requester_and_the_approver(self, monkeypatch):
        import uuid

        from web_interface import admin_ingestion

        repo = self._Repo()
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: repo)
        monkeypatch.setattr(admin_ingestion.audit, "record", lambda conn, **kw: None)

        requester_id = uuid.uuid4()
        approver = self._Actor(uuid.uuid4(), {"admin"})
        payload = {"profile": self._profile_payload(), "requested_by": str(requester_id)}
        admin_ingestion.execute_ingestion_change(payload, approver, conn=None,
                                                 rc_client=self._Client())
        (by, approved_by), = repo.saved
        assert str(by.id) == str(requester_id), "created_by is the person who asked"
        assert approved_by is approver, "approved_by is the person who confirmed"

    def test_acting_alone_leaves_approved_by_empty(self, monkeypatch):
        import uuid

        from web_interface import admin_ingestion

        repo = self._Repo()
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: repo)
        monkeypatch.setattr(admin_ingestion.audit, "record", lambda conn, **kw: None)

        me = self._Actor(uuid.uuid4(), {"admin"})
        payload = {"profile": self._profile_payload(), "requested_by": str(me.id)}
        admin_ingestion.execute_ingestion_change(payload, me, conn=None, rc_client=self._Client())
        (by, approved_by), = repo.saved
        assert by is me and approved_by is None

    def test_a_pure_approver_is_refused_before_a_version_row_exists(self, monkeypatch):
        import uuid

        import pytest as _pytest
        from remote_controller_client import RemoteControllerError
        from web_interface import admin_ingestion

        repo = self._Repo()
        client = self._Client()
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: repo)
        monkeypatch.setattr(admin_ingestion.audit, "record", lambda conn, **kw: None)

        pruefer = self._Actor(uuid.uuid4(), {"approver"})
        payload = {"profile": self._profile_payload(), "requested_by": str(uuid.uuid4())}
        with _pytest.raises(RemoteControllerError) as excinfo:
            admin_ingestion.execute_ingestion_change(payload, pruefer, conn=None, rc_client=client)
        assert "admin oder ingestion_manager" in str(excinfo.value)
        assert repo.saved == [], "no version row for an execution that cannot happen"
        assert client.calls == [], "and nothing reaches RemoteController"

    def test_an_ingestion_manager_may_execute(self, monkeypatch):
        import uuid

        from web_interface import admin_ingestion

        repo = self._Repo()
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: repo)
        monkeypatch.setattr(admin_ingestion.audit, "record", lambda conn, **kw: None)

        actor = self._Actor(uuid.uuid4(), {"ingestion_manager", "approver"})
        payload = {"profile": self._profile_payload(), "requested_by": str(uuid.uuid4())}
        out = admin_ingestion.execute_ingestion_change(payload, actor, conn=None,
                                                       rc_client=self._Client())
        assert out["version"] == 1

    def test_the_approved_stop_writes_the_same_audit_row_as_the_direct_one(self, monkeypatch):
        """I2: the registry lambda called stop() and wrote nothing, so an
        approved halt was invisible under ingestion.stopped."""
        import uuid

        from web_interface import admin_ingestion

        recorded = []
        monkeypatch.setattr(admin_ingestion.audit, "record",
                            lambda conn, **kw: recorded.append(kw))
        client = self._Client()
        out = admin_ingestion.execute_ingestion_change(
            {"action": "stop"}, self._Actor(uuid.uuid4(), {"approver"}),
            conn=None, rc_client=client)
        assert out == {"stopped": True}
        assert client.calls == ["stop"]
        assert [r["action"] for r in recorded] == ["ingestion.stopped"]

    def test_the_route_and_the_registry_share_one_stop(self):
        """Two call sites, one implementation -- what the registry exists for."""
        import inspect

        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        assert src.count("def execute_stop(") == 1
        assert "execute_stop(" in src[src.index("def stop("):]


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestLive:
    """I5: nothing drove /admin/ingestion* through the app. C1 and C2 are
    exactly the kind of thing a route test with a fake RemoteController
    surfaces, and the Freigaben tab got one while this tab did not."""

    @pytest.fixture
    def rc(self, monkeypatch):
        """Substituted before create_app: app.py does `from
        remote_controller_client import RemoteControllerClient` inside the
        factory, so patching the module attribute is what reaches it."""
        import remote_controller_client

        FakeRemoteControllerClient.last_instance = None
        monkeypatch.setattr(remote_controller_client, "RemoteControllerClient",
                            FakeRemoteControllerClient)
        return FakeRemoteControllerClient

    @pytest.fixture
    def client(self, rc, identity_app):
        return identity_app.test_client()

    @pytest.fixture
    def people(self, identity_repo):
        from _console import PASSWORD

        out = {}
        for email, role in (("chef@kanzlei.ch", "admin"),
                            ("chef2@kanzlei.ch", "admin"),
                            ("ingest@kanzlei.ch", "ingestion_manager"),
                            ("anwalt@kanzlei.ch", "member")):
            u = identity_repo.create(email=email, display_name=email.split("@")[0],
                                     password=PASSWORD)
            identity_repo.grant_role(u.id, role)
            out[email] = identity_repo.get(u.id)
        return out

    def test_who_may_open_it(self, client, people):
        from _console import sign_in

        assert client.get("/admin/ingestion").status_code in (302, 303)
        sign_in(client, "anwalt@kanzlei.ch")
        assert client.get("/admin/ingestion").status_code == 403
        _logout(client)
        sign_in(client, "ingest@kanzlei.ch")
        assert client.get("/admin/ingestion").status_code == 200

    def test_a_post_without_the_csrf_token_changes_nothing(self, client, people, rc):
        from _console import sign_in

        sign_in(client, "chef@kanzlei.ch")
        client.get("/admin/ingestion")
        before = rc.last_instance.count("push")
        r = client.post("/admin/ingestion/save", data=dict(SAVE_FORM))
        assert r.status_code == 400
        assert rc.last_instance.count("push") == before

    def test_a_member_is_refused_the_post_not_only_the_link(self, client, people, rc):
        """Hiding the tab is presentation; refusing the POST is the control.
        The token is read out of the session, since which pages this persona
        may render is not what this test is about."""
        from _console import sign_in

        sign_in(client, "anwalt@kanzlei.ch")
        with client.session_transaction() as sess:
            token = sess.get("csrf_token")
        r = client.post("/admin/ingestion/start", data={"csrf_token": token})
        assert r.status_code == 403
        assert rc.last_instance.count("start") == 0

    def test_saving_in_strict_mode_queues_and_pushes_nothing(
        self, client, people, rc, platform_db, identity_repo
    ):
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        ApprovalService(platform_db, identity_repo).set_admin_bypass(
            False, by=people["chef@kanzlei.ch"])
        sign_in(client, "chef@kanzlei.ch")
        r = post_form(client, "/admin/ingestion/save", page="/admin/ingestion", **SAVE_FORM)
        assert r.status_code == 200
        assert rc.last_instance.count("push") == 0, "queued means not sent"
        (req,) = ApprovalService(platform_db, identity_repo).pending()
        assert req.kind == "ingestion_profile_change"
        assert req.payload["requested_by"] == str(people["chef@kanzlei.ch"].id)
        assert platform_db.execute(
            "SELECT count(*) FROM ingestion_profiles").fetchone()[0] == 0

    def test_an_approving_admin_carries_the_change_out(
        self, client, people, rc, platform_db, identity_repo
    ):
        from _console import post_form, sign_in
        from identity.approvals import ApprovalService

        ApprovalService(platform_db, identity_repo).set_admin_bypass(
            False, by=people["chef@kanzlei.ch"])
        sign_in(client, "ingest@kanzlei.ch")
        post_form(client, "/admin/ingestion/save", page="/admin/ingestion", **SAVE_FORM)
        _logout(client)
        (req,) = ApprovalService(platform_db, identity_repo).pending()

        sign_in(client, "chef@kanzlei.ch")
        r = post_form(client, f"/admin/approvals/{req.id}/approve", page="/admin/approvals")
        assert r.status_code == 200
        assert rc.last_instance.count("push") == 1
        row = platform_db.execute(
            "SELECT created_by, approved_by, pushed_at FROM ingestion_profiles "
            "WHERE is_current").fetchone()
        assert str(row[0]) == str(people["ingest@kanzlei.ch"].id), "the requester authored it"
        assert str(row[1]) == str(people["chef@kanzlei.ch"].id), "the approver confirmed it"
        assert row[2] is not None, "and it is marked pushed"

    def test_save_restore_start_and_stop_each_reach_remote_controller_once(
        self, client, people, rc, platform_db
    ):
        from _console import post_form, sign_in

        sign_in(client, "chef@kanzlei.ch")
        assert post_form(client, "/admin/ingestion/save", page="/admin/ingestion",
                         **SAVE_FORM).status_code == 200
        assert rc.last_instance.count("push") == 1

        assert post_form(client, "/admin/ingestion/restore/1",
                         page="/admin/ingestion").status_code == 200
        assert rc.last_instance.count("push") == 2
        assert [v[0] for v in platform_db.execute(
            "SELECT version FROM ingestion_profiles ORDER BY version").fetchall()] == [1, 2]

        assert post_form(client, "/admin/ingestion/start",
                         page="/admin/ingestion").status_code == 200
        assert rc.last_instance.count("start") == 1

        assert post_form(client, "/admin/ingestion/stop",
                         page="/admin/ingestion").status_code == 200
        assert rc.last_instance.count("stop") == 1
        actions = [row[0] for row in platform_db.execute(
            "SELECT action FROM audit_log").fetchall()]
        assert actions.count("ingestion.stopped") == 1

    def test_preview_asks_remote_controller_per_folder_and_saves_nothing(
        self, client, people, rc, platform_db
    ):
        from _console import post_form, sign_in

        sign_in(client, "chef@kanzlei.ch")
        form = dict(SAVE_FORM)
        form["folder-1-path"] = "/mnt/autodoc/allgemein"
        r = post_form(client, "/admin/ingestion/preview", page="/admin/ingestion", **form)
        assert r.status_code == 200
        assert rc.last_instance.count("discover") == 2
        assert rc.last_instance.count("push") == 0
        assert platform_db.execute(
            "SELECT count(*) FROM ingestion_profiles").fetchone()[0] == 0

    def test_folders_lists_child_directories_under_the_watch_root(self, client, people, rc):
        from _console import sign_in

        sign_in(client, "ingest@kanzlei.ch")
        r = client.get("/admin/ingestion/folders")
        assert r.status_code == 200
        body = r.get_json()
        assert body["root"] == "/data/corpus"
        assert body["folders"] == [
            {"name": "Mandate", "path": "/data/corpus/Mandate"},
            {"name": "Allgemein", "path": "/data/corpus/Allgemein"},
        ]
        assert rc.last_instance.discover_args[-1] == {"root": None, "max_depth": 1}

    def test_folders_passes_the_expanded_root(self, client, people, rc):
        from _console import sign_in

        sign_in(client, "chef@kanzlei.ch")
        r = client.get("/admin/ingestion/folders",
                       query_string={"root": "/data/corpus/Mandate"})
        assert r.status_code == 200
        assert r.get_json()["folders"] == [
            {"name": "2024-017", "path": "/data/corpus/Mandate/2024-017"},
        ]
        assert rc.last_instance.discover_args[-1] == {
            "root": "/data/corpus/Mandate", "max_depth": 1,
        }

    def test_a_member_cannot_list_folders(self, client, people, rc):
        from _console import sign_in

        sign_in(client, "anwalt@kanzlei.ch")
        assert client.get("/admin/ingestion/folders").status_code == 403
        assert rc.last_instance.count("discover") == 0

    def test_folders_names_a_remote_controller_failure(self, client, people, rc):
        from _console import sign_in

        sign_in(client, "chef@kanzlei.ch")
        rc.last_instance.discover_error = "RemoteController nicht erreichbar"
        r = client.get("/admin/ingestion/folders")
        assert r.status_code == 502
        body = r.get_json()
        assert body["folders"] == []
        assert "nicht erreichbar" in body["error"]


# --- Erneute Übernahme -------------------------------------------------------
#
# RemoteController überspringt, was sein Zustandsspeicher als übertragen führt.
# Das ist richtig, solange beide Seiten dasselbe glauben. Wurde der Bestand bei
# Knovas neu aufgesetzt, stehen die Dateien dort weiter als "synced", der Zyklus
# meldet "uploaded=0 scanned=207 errors=0" und lädt nie wieder etwas hoch --
# von aussen eine Übernahme, die läuft, und eine Suche, die nichts findet.
#
# IngestionProfile.full_rescan kompiliert zu "mode": "full", das genau das löst.
# Nur setzen konnte es niemand: das Formular kannte das Feld nicht.

class TestFullRescanIsReachable:
    def test_the_form_offers_it(self):
        html = (TEMPLATES / "admin_ingestion.html").read_text(encoding="utf-8")
        assert 'name="full_rescan"' in html

    def test_a_ticked_box_becomes_a_full_profile(self):
        from web_interface.admin_ingestion import profile_from_form

        profile = profile_from_form(
            {"identifier_prefix": "Mandanten Sync", "schedule": "nightly",
             "throughput": "normal", "folder-0-path": "/mnt/documents",
             "full_rescan": "1"},
            {"file_types": ["documents"]},
        )
        assert profile.full_rescan is True

    def test_an_unticked_box_stays_incremental(self):
        """Der Normalbetrieb bleibt der Normalbetrieb."""
        from web_interface.admin_ingestion import profile_from_form

        profile = profile_from_form(
            {"identifier_prefix": "Mandanten Sync", "schedule": "nightly",
             "throughput": "normal", "folder-0-path": "/mnt/documents"},
            {"file_types": ["documents"]},
        )
        assert profile.full_rescan is False

    def test_it_reaches_the_sync_request_as_full_mode(self):
        from identity.ingestion_compiler import IngestionProfile, SourceFolder, compile_profile

        compiled = compile_profile(IngestionProfile(
            identifier_prefix="Mandanten Sync",
            sources=[SourceFolder(path="/mnt/documents")],
            file_types=["documents"], schedule="nightly", throughput="normal",
            full_rescan=True,
        ))
        assert compiled.sync_request["mode"] == "full"

    def test_a_saved_profile_shows_the_box_still_ticked(self):
        """Sonst verliert ein Bearbeiten des Profils die Einstellung stillschweigend."""
        from identity.ingestion_compiler import IngestionProfile, SourceFolder
        from web_interface.admin_ingestion import form_from_profile

        form = form_from_profile(IngestionProfile(
            identifier_prefix="p", sources=[SourceFolder(path="/mnt/documents")],
            file_types=["documents"], schedule="nightly", throughput="normal",
            full_rescan=True,
        ))
        assert form["full_rescan"] is True


# --- Document fields per folder (spec 4.8) -----------------------------------
#
# Placeholder names only ("Muster AG", "Beispiel GmbH"). SENTINEL stands for a
# client name inside a value or a template: it may appear on the admin's own
# page and nowhere else -- not in a log line, the audit row, the support JSON
# or the approvals summary.

SENTINEL = "Sentinel-Muster-AG-7781"
ALL_CAPS = ("source_fields_v1", "field_templates_v1", "metadata_fields_v1", "fields_requeue_v1")


class _RC:
    """A minimal RemoteController for the pure helpers."""

    def __init__(self, caps=ALL_CAPS):
        self.caps = frozenset(caps)
        self.calls: list[str] = []

    def capabilities(self):
        self.calls.append("capabilities")
        return self.caps


def _knovas(mode="values", **knobs):
    from doc_fields_fakes import FakeDocFieldsApi

    api = FakeDocFieldsApi(None, mode)
    for name, value in knobs.items():
        setattr(api, name, value)
    return api


def _profile(*sources, prefix="rc-sync", schedule="nightly"):
    from identity.ingestion_compiler import IngestionProfile

    return IngestionProfile(identifier_prefix=prefix, sources=list(sources), schedule=schedule)


def _folder(path="/mnt/mandate", **kw):
    from identity.ingestion_compiler import SourceFolder

    return SourceFolder(path=path, **kw)


class TestFieldInputsParse:
    def test_static_values_one_per_line_several_with_semicolons(self):
        from web_interface.admin_ingestion import format_static_fields, parse_static_fields

        pairs = parse_static_fields("doc_type = Rechnung\n\n  keywords= Beleg ; Kreditor;\n", 1)
        assert pairs == (("doc_type", "Rechnung"), ("keywords", ("Beleg", "Kreditor")))
        assert parse_static_fields(format_static_fields(pairs), 1) == pairs

    @pytest.mark.parametrize("text,expected", [
        (f"{SENTINEL}", "Zeile 1: erwartet"),
        (f"Mandant = {SENTINEL}", "Zeile 1: ung\u00fcltiger Feldschl\u00fcssel"),
        (f"doc_type = a\ndoc_type = {SENTINEL}", "Zeile 2: Feld \u201edoc_type\u201c steht doppelt"),
        ("doc_type = ;", "Zeile 1: kein Wert"),
    ])
    def test_a_bad_line_is_named_by_number_never_by_content(self, text, expected):
        from identity.ingestion_compiler import ProfileError
        from web_interface.admin_ingestion import parse_static_fields

        with pytest.raises(ProfileError) as excinfo:
            parse_static_fields(text, 2)
        assert "Ordner 2" in str(excinfo.value) and expected in str(excinfo.value)
        assert SENTINEL not in str(excinfo.value)

    def test_templates_and_metadata(self):
        from identity.ingestion_compiler import ProfileError
        from web_interface.admin_ingestion import parse_metadata_items, parse_templates

        assert parse_templates("  {mandant}/**  \n\n*/{period}\n") == ("{mandant}/**", "*/{period}")
        assert parse_metadata_items(["email_date", "language", "email_date"], 1) == (
            "language", "email_date")
        with pytest.raises(ProfileError):
            parse_metadata_items(["email_message_id"], 1)

    def test_the_form_round_trips_a_folder_with_fields(self):
        from web_interface.admin_ingestion import form_from_profile, profile_from_form

        form = {"identifier_prefix": "kanzlei", "schedule": "nightly", "throughput": "normal",
                "folder-0-path": "/mnt/mandate", "folder-0-recursive": "1",
                "folder-0-fields": "doc_type = invoice\nkeywords = a; b",
                "folder-0-templates": "{mandant}/{period}/**",
                "folder-1-path": "/mnt/allgemein"}
        lists = {"file_types": ["documents"], "folder-0-metadata": ["email_date"]}
        profile = profile_from_form(form, lists)
        first, second = profile.sources
        assert dict(first.fields) == {"doc_type": "invoice", "keywords": ("a", "b")}
        assert first.field_templates == ("{mandant}/{period}/**",)
        assert first.metadata_fields == ("email_date",)
        assert not second.has_fields
        row = form_from_profile(profile)["folders"][0]
        assert row["fields_text"] == "doc_type = invoice\nkeywords = a; b"
        assert row["templates_text"] == "{mandant}/{period}/**"
        assert row["metadata"] == ["email_date"]

    def test_a_rejected_form_keeps_the_field_inputs(self):
        from web_interface.admin_ingestion import form_from_request

        rebuilt = form_from_request(
            {"folder-0-path": "/mnt/mandate", "folder-0-fields": "doc_type = x",
             "folder-0-templates": "{mandant}/**"},
            {"folder-0-metadata": ["language"]})
        row = rebuilt["folders"][0]
        assert (row["fields_text"], row["templates_text"], row["metadata"]) == (
            "doc_type = x", "{mandant}/**", ["language"])


class TestReuploadCost:
    @pytest.mark.parametrize("count,per_cycle,schedule,throughput,expected", [
        # 200 cycles of 500 s (5 min interval + 100 uploads at 30/min) in an
        # 11-hour window: the spec's example profile.
        (20000, 100, "nightly", "normal", "ca. 3 N\u00e4chte"),
        (100, 100, "nightly", "normal", "ca. 1 Nacht"),
        (1000, 100, "continuous", "normal", "ca. 54 Minuten"),
        (100000, 100, "continuous", "fast", "ca. 48 Stunden"),
        (1000000, 100, "continuous", "normal", "ca. 38 Tage"),
        (250, 100, "manual", "normal", "3-mal Start"),
        (50, 100, "manual", "normal", "einmal Start"),
        # gentle uploads at most 100 files a cycle, whatever the RC bound
        (1000, 500, "manual", "gentle", "10-mal Start"),
        (0, 100, "nightly", "normal", "keine"),
    ])
    def test_eta(self, count, per_cycle, schedule, throughput, expected):
        from web_interface.admin_ingestion import reupload_eta

        assert reupload_eta(count, per_cycle, schedule, throughput) == expected

    def test_the_sentence_names_the_folders_the_cost_and_the_bound(self):
        from web_interface.admin_ingestion import reupload_text

        text = reupload_text(["/mnt/mandate"], 20000, 100, "nightly", "normal")
        assert text.startswith("Alle Dokumente der Quelle(n) /mnt/mandate werden erneut gesendet")
        assert "verrechneter Upload mit erneuter Texterkennung" in text
        assert "H\u00f6chstens 20000 Dokumente; bei 100 pro Durchlauf" in text
        assert "ca. 3 N\u00e4chte" in text

    def test_the_bound_is_what_the_speed_preset_lets_through(self):
        from web_interface.admin_ingestion import reupload_text

        text = reupload_text(["/a"], 1000, 500, "manual", "gentle")
        assert "bei 100 pro Durchlauf" in text and "10-mal Start" in text

    def test_an_unknown_total_is_said_not_guessed(self):
        from web_interface.admin_ingestion import reupload_info

        info = reupload_info(["/a"], {"scheduler_state": "x"}, "nightly", "normal")
        assert "H\u00f6chstens" not in info["text"] and "erst nach einem Abgleich" in info["text"]
        assert "100 pro Durchlauf" in info["text"]
        assert "Ordnervorgabe" in info["advice"]
        assert reupload_info([], {}, "nightly", "normal") is None


class TestStatusBar:
    BLOCK = {
        "enabled": True, "server": "not_accepted", "per_cycle": 100,
        "documents": {"with_fields": 1234, "pending_reupload": 56, "refused": 3,
                      "not_accepted": 140, "reupload_failed": 1},
        "last_cycle": {"staged": 40, "refused": {"unknown_field": 2, SENTINEL: 9},
                       "rel_collisions": 4},
        "warnings": {"unresolved_entity": 12, "ambiguous_date": 1},
        "unknown_keys": ["mandat", SENTINEL],
        "suggest": {"mandat": ["mandant"]},
        "template_errors": {"field_template_invalid": 2},
    }

    def _status(self, caps=ALL_CAPS, block=None, **extra):
        status = {"scheduler_state": "idle_between_cycles", "capabilities": list(caps),
                  "doc_fields": self.BLOCK if block is None else block,
                  "document_sync": {"total": 5000, "fields_changed": 7}}
        status.update(extra)
        return status

    def test_an_older_remote_controller_shows_nothing(self):
        from doc_fields_capability import Capability
        from web_interface.admin_ingestion import doc_fields_status

        assert doc_fields_status({"scheduler_state": "x"}, capability=Capability.values) is None
        assert doc_fields_status(None, capability=Capability.values) is None

    def test_every_count_the_spec_lists_is_shown(self):
        from doc_fields_capability import Capability
        from web_interface.admin_ingestion import doc_fields_status

        out = doc_fields_status(self._status(), capability=Capability.values)
        text = "\n".join(line["text"] for line in out["lines"])
        assert "Felder bei 140 Uploads nicht \u00fcbernommen: Funktion bei Knovas aus." in text
        assert "unknown_field 2\u00d7" in text
        assert "unresolved_entity 12\u00d7 (nicht verkn\u00fcpft)" in text
        assert "mandat (Vorschlag: mandant)" in text
        assert "7 Dokumente mit ge\u00e4nderten Feldeinstellungen" in text
        assert "56 Dokumente warten auf erneutes Senden (100 pro Durchlauf, ca. 1 Nacht)" in text
        assert "4 Dateien liegen unter gleichem relativem Pfad" in text
        assert "1 Dokumente nach wiederholten Fehlern" in text
        assert "2\u00d7 Ordner wegen ung\u00fcltiger Pfadvorlage" in text

    def test_it_never_says_saved_and_never_repeats_something_that_is_not_a_code(self):
        from doc_fields_capability import Capability
        from web_interface.admin_ingestion import doc_fields_status

        out = doc_fields_status(self._status(), capability=Capability.values)
        text = "\n".join(line["text"] for line in out["lines"])
        assert "gespeichert" not in text  # H7
        assert SENTINEL not in text

    def test_requeue_offers_per_outcome(self):
        from doc_fields_capability import Capability
        from web_interface.admin_ingestion import doc_fields_status

        offers = lambda cap, **kw: [r["outcome"] for r in doc_fields_status(  # noqa: E731
            self._status(**kw), capability=cap)["requeue"]]
        assert offers(Capability.values) == ["not_accepted", "refused", "reupload_failed"]
        # not_accepted only once Knovas offers fields again
        assert offers(Capability.off) == ["refused", "reupload_failed"]
        assert offers(Capability.unknown) == ["refused", "reupload_failed"]
        # and nothing from a RemoteController that cannot requeue
        assert offers(Capability.values, caps=("source_fields_v1",)) == []

    def test_a_quiet_block(self):
        from doc_fields_capability import Capability
        from web_interface.admin_ingestion import doc_fields_status

        out = doc_fields_status(self._status(block={"enabled": False, "server": "unknown"},
                                             document_sync={"total": 10}),
                                capability=Capability.off)
        assert [line["text"] for line in out["lines"]] == [
            "Dokumentfelder sind im Knovas Connector ausgeschaltet (RC_DOC_FIELDS=off); "
            "es werden keine Felder gesendet.",
            "Noch keine R\u00fcckmeldung von Knovas zu Dokumentfeldern.",
        ]
        assert out["requeue"] == []


class TestTemplatePreviewEntries:
    ENTRIES = [
        {"type": "directory", "path": "Muster AG"},
        {"type": "file", "path": "Muster AG/GJ 2024/Rechnung_17.pdf"},
        {"type": "file", "path": "Muster AG\\GJ 2023\\Rechnung_3.pdf"},
        {"type": "file", "path": "Notiz.pdf"},
    ]

    def test_files_only_first_match_and_counts(self):
        from web_interface.admin_ingestion import template_preview

        out = template_preview(["{mandant}/{period}/**", "{mandant}/**"], self.ENTRIES)
        assert (out["files"], out["matched"], out["truncated"]) == (3, 2, False)
        assert [r["template"] for r in out["rows"]] == [1, 1, None]
        assert out["rows"][1]["captures"] == [{"key": "mandant", "value": "Muster AG"},
                                              {"key": "period", "value": "GJ 2023"}]

    def test_a_folder_without_subfolders_sees_its_top_level_only(self):
        from web_interface.admin_ingestion import template_preview

        out = template_preview(["{mandant}/**"], self.ENTRIES, recursive=False)
        assert [r["path"] for r in out["rows"]] == ["Notiz.pdf"]

    def test_the_listing_is_capped(self):
        from web_interface.admin_ingestion import template_preview

        entries = [{"type": "file", "path": f"Muster AG/{i}.pdf"} for i in range(30)]
        out = template_preview(["{mandant}/**"], entries, limit=5)
        assert len(out["rows"]) == 5 and out["files"] == 30 and out["truncated"] is True
        assert out["matched"] == 30


class TestCheckProfileFields:
    """What a save checks (spec 4.8), against FakeDocFieldsApi."""

    def _check(self, profile, current=None, rc=None, knovas=None, **kw):
        from web_interface.admin_ingestion import check_profile_fields

        return check_profile_fields(profile, current, rc_client=rc or _RC(),
                                    knovas_client=knovas or _knovas(), user_key="u-1", **kw)

    def test_a_profile_without_fields_asks_nobody(self):
        rc, knovas = _RC(caps=()), _knovas()
        check = self._check(_profile(_folder()), rc=rc, knovas=knovas)
        assert check.changed == () and check.warnings == () and check.notes == ()
        assert rc.calls == [] and knovas.doc_calls == [] and knovas.probe_calls == 0

    @pytest.mark.parametrize("caps,source_kw", [
        ((), {"fields": {"doc_type": "invoice"}}),
        (("source_fields_v1",), {"field_templates": ("{mandant}/**",)}),
        (("source_fields_v1",), {"metadata_fields": ("language",)}),
        (("field_templates_v1",), {"field_templates": ("{mandant}/**",)}),
    ])
    def test_a_remote_controller_without_the_capability_refuses_the_save(self, caps, source_kw):
        from identity.ingestion_compiler import RC_TOO_OLD, ProfileError

        with pytest.raises(ProfileError) as excinfo:
            self._check(_profile(_folder(**source_kw)), rc=_RC(caps=caps))
        assert str(excinfo.value) == RC_TOO_OLD
        assert "Der Knovas Connector ist zu alt \u2013 bitte aktualisieren" in RC_TOO_OLD

    def test_an_unreachable_remote_controller_is_not_called_too_old(self):
        from identity.ingestion_compiler import RC_UNREACHABLE, ProfileError
        from remote_controller_client import RemoteControllerError
        from web_interface.admin_ingestion import _require_rc_support

        class _Down(_RC):
            def reachable_capabilities(self):
                self.calls.append("reachable_capabilities")
                return None

        with pytest.raises(ProfileError) as excinfo:
            self._check(_profile(_folder(fields={"doc_type": "invoice"})), rc=_Down())
        assert str(excinfo.value) == RC_UNREACHABLE
        assert "nicht erreichbar" in RC_UNREACHABLE and "zu alt" not in RC_UNREACHABLE
        with pytest.raises(RemoteControllerError) as pushed:
            _require_rc_support(_Down(), {"sources": [{"path": "/a", "fields": {"doc_type": "x"}}]})
        assert "nicht erreichbar" in str(pushed.value)
        # A body without fields asks nobody, reachable or not.
        _require_rc_support(_Down(), {"sources": [{"path": "/a"}]})

    def test_keys_are_validated_and_enum_labels_stored_as_codes(self):
        check = self._check(_profile(_folder(fields={"doc_type": "Rechnung"},
                                              field_templates=("{mandant}/**",))))
        assert dict(check.profile.sources[0].fields) == {"doc_type": "invoice"}

    def test_an_unknown_key_is_refused_naming_the_key(self):
        from identity.ingestion_compiler import ProfileError

        with pytest.raises(ProfileError) as excinfo:
            self._check(_profile(_folder(fields={"mandat": SENTINEL})))
        assert "mandat" in str(excinfo.value) and SENTINEL not in str(excinfo.value)

    def test_without_a_registry_a_changed_config_is_refused_an_unchanged_one_kept(self):
        from conftest import DummyKnovasClient
        from identity.ingestion_compiler import ProfileError
        from web_interface.admin_ingestion import NOT_RECHECKED, UNVERIFIABLE

        off = DummyKnovasClient(None)  # not secured: capability off, no request
        current = _profile(_folder(fields={"doc_type": "invoice"}))
        with pytest.raises(ProfileError) as excinfo:
            self._check(_profile(_folder(fields={"doc_type": "contract"})), current, knovas=off)
        assert str(excinfo.value) == UNVERIFIABLE
        same = self._check(_profile(_folder(fields={"doc_type": "invoice"})), current, knovas=off)
        assert same.notes == (NOT_RECHECKED,) and same.changed == ()

    def test_a_registry_that_cannot_be_read_counts_as_unavailable(self):
        from identity.ingestion_compiler import ProfileError
        from web_interface.admin_ingestion import UNVERIFIABLE

        knovas = _knovas()
        knovas.fail_call("doc_fields", 503, "transport_error")
        with pytest.raises(ProfileError) as excinfo:
            self._check(_profile(_folder(fields={"doc_type": "invoice"})), knovas=knovas)
        assert str(excinfo.value) == UNVERIFIABLE

    def test_a_folder_rule_on_the_same_key_is_a_warning_with_counts_only(self):
        knovas = _knovas()
        knovas.rules = {
            "rc-sync/Muster AG/": {"id": "r1", "pointer_prefix": "rc-sync/Muster AG/",
                                   "set": {"mandant": SENTINEL}, "version": 1, "status": "live"},
            "rc-sync/": {"id": "r2", "pointer_prefix": "rc-sync/",
                         "set": {"doc_type": "contract"}, "version": 1, "status": "live"},
        }
        check = self._check(_profile(_folder(field_templates=("{mandant}/**",))), knovas=knovas)
        (warning,) = check.warnings
        assert "\u201eMandant\u201c (mandant)" in warning and "1 Regel" in warning
        assert SENTINEL not in warning and "Muster AG" not in warning
        assert "Vorrang" in warning

    def test_rules_behind_the_admin_group_are_skipped_with_a_note(self):
        from web_interface.admin_ingestion import RULES_FORBIDDEN

        check = self._check(_profile(_folder(fields={"doc_type": "invoice"})),
                            knovas=_knovas(rules_denied=True))
        assert check.notes == (RULES_FORBIDDEN,) and check.warnings == ()
        assert RULES_FORBIDDEN == ("Ordnervorgaben nicht pr\u00fcfbar "
                                   "(nur Knovas-Administratorgruppe).")

    def test_metadata_only_needs_no_rules(self):
        knovas = _knovas()
        self._check(_profile(_folder(metadata_fields=("language",))), knovas=knovas)
        assert "doc_field_rules" not in [name for name, _ in knovas.doc_calls]

    def test_what_changes_is_reported_after_normalising(self):
        current = _profile(_folder(fields={"doc_type": "invoice"}), _folder("/b"))
        same = self._check(_profile(_folder(fields={"doc_type": "Rechnung"}), _folder("/b")),
                           current)
        assert same.changed == ()
        changed = self._check(_profile(_folder(), _folder("/b", metadata_fields=("language",))),
                              current)
        assert changed.changed == ("/mnt/mandate", "/b")

    def test_the_preview_gets_the_problem_instead_of_an_exception(self):
        check = self._check(_profile(_folder(fields={"doc_type": "invoice"})), rc=_RC(caps=()),
                            strict=False)
        assert "Der Knovas Connector ist zu alt" in check.error


class TestTheExecutorRefusesAnOldRemoteController:
    """An approved change can run long after it was asked for; the executor
    checks again, before any version row (spec 4.8, 2.5)."""

    class _Repo:
        def __init__(self):
            self.saved = []

        def current(self, name="default"):
            return None

        def save_new_version(self, profile, *, name="default", by, approved_by=None):
            self.saved.append(profile)
            return type("V", (), {"id": "v1", "version": 1, "pushed_at": None})()

        def mark_pushed(self, version_id):
            pass

    class _Client:
        def __init__(self, caps):
            self.caps, self.pushed = frozenset(caps), []

        def capabilities(self):
            return self.caps

        def push(self, compiled):
            self.pushed.append(compiled)
            return {"applied": "started"}

    def _payload(self):
        from identity.ingestion_profiles import profile_to_json

        return {"profile": profile_to_json(_profile(_folder(
            fields={"mandant": SENTINEL}, field_templates=(f"{SENTINEL}/{{period}}/**",))))}

    def test_old_remote_controller(self, monkeypatch):
        from remote_controller_client import RemoteControllerError
        from web_interface import admin_ingestion

        repo, client = self._Repo(), self._Client(caps=("source_fields_v1",))
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: repo)
        with pytest.raises(RemoteControllerError) as excinfo:
            admin_ingestion.execute_ingestion_change(
                self._payload(), TestExecuteIngestionChange._Actor("a", {"admin"}),
                conn=None, rc_client=client)
        assert "zu alt" in str(excinfo.value)
        assert repo.saved == [] and client.pushed == []

    def test_a_current_one_gets_the_fields_and_the_audit_row_counts_them(self, monkeypatch):
        from web_interface import admin_ingestion

        repo, client = self._Repo(), self._Client(caps=ALL_CAPS)
        recorded = []
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: repo)
        monkeypatch.setattr(admin_ingestion.audit, "record", lambda conn, **kw: recorded.append(kw))
        admin_ingestion.execute_ingestion_change(
            self._payload(), TestExecuteIngestionChange._Actor("a", {"admin"}),
            conn=None, rc_client=client)
        (compiled,) = client.pushed
        assert compiled.sync_request["sources"][0]["fields"] == {"mandant": SENTINEL}
        detail = recorded[-1]["detail"]
        assert detail["folders_with_fields"] == 1 and detail["field_templates"] == 1
        assert SENTINEL not in repr(recorded) and "mandant" not in repr(detail)

    def test_a_profile_without_fields_never_asks(self, monkeypatch):
        from identity.ingestion_profiles import profile_to_json
        from web_interface import admin_ingestion

        class _NoCaps(self._Client):
            def capabilities(self):
                raise AssertionError("an old RemoteController is never asked")

        repo, client = self._Repo(), _NoCaps(caps=())
        monkeypatch.setattr(admin_ingestion, "IngestionProfileRepository", lambda conn: repo)
        monkeypatch.setattr(admin_ingestion.audit, "record", lambda conn, **kw: None)
        admin_ingestion.execute_ingestion_change(
            {"profile": profile_to_json(_profile(_folder()))},
            TestExecuteIngestionChange._Actor("a", {"admin"}), conn=None, rc_client=client)
        assert len(client.pushed) == 1


class TestApprovalsSummaryCountsFields:
    def test_counts_only(self):
        from identity.ingestion_profiles import profile_to_json
        from web_interface.admin_approvals import _summary

        payload = {"profile": profile_to_json(_profile(
            _folder(fields={"mandant": SENTINEL}, field_templates=(f"{SENTINEL}/{{x}}/**",)),
            _folder("/b", metadata_fields=("language",)), _folder("/c")))}
        text = _summary("ingestion_profile_change", payload)
        assert text.endswith(", 2 Ordner mit Feldern, 1 Pfadvorlage")
        assert SENTINEL not in text and "mandant" not in text

    def test_the_approver_sees_the_re_upload_the_requester_confirmed(self):
        from identity.ingestion_profiles import profile_to_json
        from web_interface.admin_approvals import _summary

        payload = {"profile": profile_to_json(_profile(_folder(fields={"doc_type": "invoice"}))),
                   "reupload_folders": 1}
        assert _summary("ingestion_profile_change", payload).endswith(
            "; erneutes Senden aller Dokumente von 1 Ordner(n)")

    def test_a_profile_without_fields_reads_as_before(self):
        from identity.ingestion_profiles import profile_to_json
        from web_interface.admin_approvals import _summary

        text = _summary("ingestion_profile_change",
                        {"profile": profile_to_json(_profile(_folder()))})
        assert text == "1 Ordner (0 mit Zugriffsgruppen), nightly, normal, documents"


class TestRemoteControllerClientDocFields:
    """``health``, ``capabilities`` and ``requeue_doc_fields`` (spec 4.8)."""

    class _Broker:
        def __init__(self, user="u-1"):
            self.user = user

        def current_user(self):
            return self.user

        def assertion_for(self, user):
            return f"token-for-{user}"

    class _Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            return self._body

    class _Session:
        def __init__(self, answer):
            self.answer, self.calls = answer, []

        def request(self, method, url, **kw):
            self.calls.append((method, url, kw.get("json")))
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    def _client(self, answer, user="u-1"):
        from remote_controller_client import RemoteControllerClient

        session = self._Session(answer)
        return RemoteControllerClient("http://rc:5001", principal_broker=self._Broker(user),
                                      session=session), session

    def test_health_is_status(self):
        client, session = self._client(self._Resp(200, {"scheduler_state": "idle"}))
        assert client.health() == {"scheduler_state": "idle"}
        assert session.calls == [("GET", "http://rc:5001/sync/status", None)]

    def test_capabilities_of_a_current_an_old_and_an_unreachable_one(self):
        import requests

        current, _ = self._client(self._Resp(200, {"capabilities": list(ALL_CAPS) + [7]}))
        assert current.capabilities() == frozenset(ALL_CAPS)
        old, _ = self._client(self._Resp(200, {"scheduler_state": "idle"}))
        assert old.capabilities() == frozenset()
        broken, _ = self._client(self._Resp(500, {"error": "boom"}))
        assert broken.capabilities() == frozenset()
        down, _ = self._client(requests.ConnectionError("refused"))
        assert down.capabilities() == frozenset()
        nobody, session = self._client(self._Resp(200, {"capabilities": list(ALL_CAPS)}), user=None)
        assert nobody.capabilities() == frozenset() and session.calls == []
        # reachable_capabilities tells "cannot be asked" (None) from "too old".
        assert old.reachable_capabilities() == frozenset()
        assert current.reachable_capabilities() == frozenset(ALL_CAPS)
        assert broken.reachable_capabilities() is None
        assert down.reachable_capabilities() is None
        assert nobody.reachable_capabilities() is None

    def test_requeue_posts_the_outcome_in_the_body(self):
        client, session = self._client(self._Resp(200, {"requeued": 12}))
        assert client.requeue_doc_fields("refused") == 12
        assert session.calls == [("POST", "http://rc:5001/sync/doc-fields/requeue",
                                  {"outcome": "refused"})]
        with pytest.raises(ValueError):
            client.requeue_doc_fields("everything")

    def test_requeue_on_an_old_remote_controller_is_an_error(self):
        from remote_controller_client import RemoteControllerError

        client, _ = self._client(self._Resp(404, {"error": "Not Found"}))
        with pytest.raises(RemoteControllerError):
            client.requeue_doc_fields("all")

    def test_required_capabilities(self):
        from remote_controller_client import required_capabilities

        assert required_capabilities({"sources": [{"path": "/a"}]}) == frozenset()
        assert required_capabilities({"sources": [
            {"path": "/a", "fields": {"doc_type": "invoice"}},
            {"path": "/b", "metadata_fields": ["language"]},
        ]}) == {"source_fields_v1", "metadata_fields_v1"}
        assert required_capabilities({"sources": [
            {"path": "/a", "field_templates": ["{x}/**"]}]}) == {
            "source_fields_v1", "field_templates_v1"}


def _render(**overrides):
    import jinja2

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                             autoescape=True, undefined=jinja2.StrictUndefined)
    env.globals["url_for"] = lambda endpoint, **kw: "/" + endpoint.replace(".", "/") + (
        "/" + "/".join(str(v) for v in kw.values()) if kw else "")
    context = dict(
        app_title="Knovas", company_name="Kanzlei", feedback_url=None,
        console_url="/admin/people", active_nav="admin", csrf_token="t",
        error=None, notice=None, me=None, asset_version="1", ingestion_enabled=True,
        form={"identifier_prefix": "kanzlei", "description": "", "schedule": "nightly",
              "throughput": "normal", "file_types": ["documents"], "max_document_age_days": "",
              "folders": [{"path": "/mnt/autodoc/mandate", "recursive": True, "groups": [],
                           "fields_text": "doc_type = invoice",
                           "templates_text": "{mandant}/**", "metadata": ["language"]}]},
        schedules={"nightly": {"label": "Nachts", "description": "..."}},
        throughputs={"normal": {"label": "Normal", "description": "..."}},
        file_types={"documents": {"label": "Dokumente", "description": "..."}},
        groups=[], status={"scheduler_state": "idle"}, current=None, versions=[],
        preview=None, support_json=None,
    )
    context.update(overrides)
    return env.get_template("admin_ingestion.html").render(**context)


def _df(**overrides):
    from web_interface.admin_ingestion import METADATA_LABELS

    df = {"inputs": True, "available": True, "registry": [], "rc_supports": True,
          "metadata_items": [{"key": k, "label": v} for k, v in METADATA_LABELS.items()],
          "status": None, "reupload": None, "restore_version": None, "warnings": [],
          "notes": [], "multi_source": False}
    df.update(overrides)
    return df


class TestTemplateFields:
    def test_without_the_feature_the_page_is_as_before(self):
        html = _render()
        assert "folder-0-fields" not in html and "Dokumentfelder" not in html
        html = _render(doc_fields=_df(inputs=False))
        assert "folder-0-fields" not in html and "data-template-preview" not in html

    def test_the_inputs_per_folder_and_in_the_row_template(self):
        html = _render(doc_fields=_df())
        for name in ("folder-0-fields", "folder-0-templates", "folder-0-metadata",
                     "folder-__n__-fields", "folder-__n__-templates", "folder-__n__-metadata"):
            assert f'name="{name}"' in html, name
        assert "doc_type = invoice</textarea>" in html
        assert 'value="language" checked' in html
        assert 'data-template-preview="/admin/template_preview_json"' in html
        assert "Vorlagen testen" in html

    def test_notes_when_knovas_or_remote_controller_cannot_take_them(self):
        html = _render(doc_fields=_df(available=False, rc_supports=False, multi_source=True))
        assert "Knovas stellt Dokumentfelder derzeit nicht bereit" in html
        assert "Der Knovas Connector meldet keine Unterst\u00fctzung" in html
        assert "der erste Ordner" in html

    def test_an_unreachable_remote_controller_is_not_called_too_old(self):
        html = _render(doc_fields=_df(rc_supports=None))
        assert "Der Knovas Connector ist nicht erreichbar" in html
        assert "Der Knovas Connector meldet keine Unterst\u00fctzung" not in html

    def test_the_confirmation_sits_inside_the_profile_form(self):
        html = _render(doc_fields=_df(reupload={"paths": ["/a"], "text": "Alle Dokumente X",
                                                "advice": "Ordnervorgabe Y"}))
        form = html[html.index('id="profile-form"'):html.index("</form>", html.index('id="profile-form"'))]
        assert 'name="confirm_reupload" value="1"' in form and "Alle Dokumente X" in form

    def test_a_restore_is_confirmed_by_its_own_form(self):
        html = _render(doc_fields=_df(restore_version=3, reupload={
            "paths": ["/a"], "text": "Alle Dokumente X", "advice": "Y"}))
        assert 'action="/admin/restore/3"' in html
        assert "Version 3 wiederherstellen und erneut senden" in html
        assert html.count('name="csrf_token"') >= html.count('method="post"')

    def test_the_status_block_and_its_buttons(self):
        status = {"lines": [{"text": "Knovas \u00fcbernimmt Dokumentfelder.", "level": "info"},
                            {"text": "Felder bei 3 Uploads abgelehnt", "level": "warn"}],
                  "requeue": [{"outcome": "refused", "label": "Abgelehnte erneut senden"}]}
        html = _render(doc_fields=_df(status=status))
        assert "<strong>Felder bei 3 Uploads abgelehnt</strong>" in html
        assert 'action="/admin/requeue_doc_fields"' in html
        assert 'name="outcome" value="refused"' in html
        assert html.count('name="csrf_token"') >= html.count('method="post"')

    def test_preview_rows_show_captures(self):
        preview = [{"path": "/mnt/mandate", "files": 2, "folders": 1, "truncated": False,
                    "error": None, "templates": {
                        "rows": [{"path": "Muster AG/a.pdf", "template": 1,
                                  "captures": [{"key": "mandant", "value": "Muster AG"}]}],
                        "files": 2, "matched": 1, "errors": [], "truncated": False}}]
        html = _render(doc_fields=_df(), preview=preview)
        assert "1 von 2 Dateien im Ausschnitt passen" in html
        assert "mandant = Muster AG" in html

    def test_the_description_label_says_what_it_does(self):
        assert "wird jedem Dokument als Beschreibung mitgegeben" in _render()


class TestFrontendStaysTextOnly:
    def test_the_template_preview_script_sets_text_and_posts(self):
        js = (TEMPLATES.parent / "static" / "js" / "admin_ingestion.js").read_text(encoding="utf-8")
        assert "innerHTML" not in js
        assert "data-template-preview" in js and "X-CSRF-Token" in js
        assert "method: 'POST'" in js


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestLiveDocumentFields:
    """The tab against FakeDocFieldsApi in ``values`` mode (secured) and the
    recording RemoteController, through the real app and login."""

    FORM = dict(SAVE_FORM, **{
        "folder-0-fields": "doc_type = Rechnung",
        "folder-0-templates": "{mandant}/{period}/**",
        "folder-0-metadata": "email_date",
    })

    @pytest.fixture
    def rc(self, monkeypatch):
        import remote_controller_client

        FakeRemoteControllerClient.last_instance = None
        monkeypatch.setattr(remote_controller_client, "RemoteControllerClient",
                            FakeRemoteControllerClient)
        monkeypatch.setattr(FakeRemoteControllerClient, "capabilities_advertised", list(ALL_CAPS))
        monkeypatch.setattr(FakeRemoteControllerClient, "extra_entries", {
            "/mnt/autodoc/mandate": [
                {"type": "file", "path": "Muster AG/GJ 2024/Rechnung_17.pdf"},
                {"type": "file", "path": "Beispiel GmbH/GJ 2023/Belege/Brief.pdf"},
                {"type": "file", "path": "Notiz.pdf"},
            ]})
        return FakeRemoteControllerClient

    @pytest.fixture
    def knovas_mode(self):
        return "values"

    @pytest.fixture
    def app(self, rc, knovas_mode, platform_db, tmp_path, monkeypatch):
        from conftest import _identity_app
        from doc_fields_fakes import FakeDocFieldsApi

        return _identity_app(platform_db, tmp_path, monkeypatch,
                             client_cls=FakeDocFieldsApi.bind(knovas_mode))

    @pytest.fixture
    def client(self, app, identity_repo):
        from _console import PASSWORD, sign_in

        u = identity_repo.create(email="chef@kanzlei.ch", display_name="chef", password=PASSWORD)
        identity_repo.grant_role(u.id, "admin")
        client = app.test_client()
        sign_in(client, "chef@kanzlei.ch")
        return client

    def _save(self, client, **form):
        from _console import post_form

        return post_form(client, "/admin/ingestion/save", page="/admin/ingestion", **form)

    def test_the_page_offers_the_inputs_and_the_registry(self, client):
        html = client.get("/admin/ingestion").data.decode("utf-8")
        assert 'name="folder-__n__-fields"' in html
        assert "<code>doc_type</code>" in html and "Rechnung" in html

    def test_saving_validates_normalises_and_pushes_the_fields(self, client, rc, platform_db):
        r = self._save(client, **self.FORM)
        assert r.status_code == 200, r.data.decode("utf-8")[:2000]
        (compiled,) = rc.last_instance.pushed
        entry = compiled.sync_request["sources"][0]
        assert entry["fields"] == {"doc_type": "invoice"}
        assert entry["field_templates"] == ["{mandant}/{period}/**"]
        assert entry["metadata_fields"] == ["email_date"]
        stored = platform_db.execute(
            "SELECT profile FROM ingestion_profiles WHERE is_current").fetchone()[0]
        assert stored["sources"][0]["fields"] == {"doc_type": "invoice"}
        assert set(stored) == {"identifier_prefix", "sources", "file_types", "schedule",
                               "throughput", "paused", "full_rescan", "max_document_age_days",
                               "max_file_megabytes", "exclude_globs", "delete_on_remove",
                               "description"}

    def test_an_old_remote_controller_gets_nothing(self, client, rc, platform_db, monkeypatch):
        monkeypatch.setattr(FakeRemoteControllerClient, "capabilities_advertised", None)
        r = self._save(client, **self.FORM)
        assert r.status_code == 400
        assert "Der Knovas Connector ist zu alt" in r.data.decode("utf-8")
        assert rc.last_instance.count("push") == 0
        assert platform_db.execute("SELECT count(*) FROM ingestion_profiles").fetchone()[0] == 0

    def test_a_status_that_fails_is_unreachable_not_too_old(self, client, rc, monkeypatch,
                                                             platform_db, identity_repo):
        """platform-admin-ingestion-5: an RC that cannot be asked right now
        is never told to update."""
        from identity.ingestion_profiles import IngestionProfileRepository
        from remote_controller_client import RemoteControllerError

        IngestionProfileRepository(platform_db).save_new_version(
            _profile(_folder("/mnt/autodoc/mandate", fields={"doc_type": "invoice"}),
                     prefix="kanzlei"),
            by=identity_repo.get_by_email("chef@kanzlei.ch"))

        def timeout(self):
            raise RemoteControllerError("RemoteController nicht erreichbar: timeout", status=None)

        monkeypatch.setattr(FakeRemoteControllerClient, "status", timeout)
        html = client.get("/admin/ingestion").data.decode("utf-8")
        assert "Der Knovas Connector ist nicht erreichbar" in html
        assert "Der Knovas Connector meldet keine Unterst\u00fctzung" not in html

    def test_an_old_remote_controller_still_takes_a_profile_without_fields(
        self, client, rc, monkeypatch
    ):
        monkeypatch.setattr(FakeRemoteControllerClient, "capabilities_advertised", None)
        assert self._save(client, **SAVE_FORM).status_code == 200
        (compiled,) = rc.last_instance.pushed
        assert set(compiled.sync_request["sources"][0]) == {"path", "recursive"}

    def test_an_unknown_key_is_refused_before_anything_is_sent(self, client, rc):
        r = self._save(client, **dict(self.FORM, **{"folder-0-fields": f"mandat = {SENTINEL}"}))
        assert r.status_code == 400
        assert "mandat" in r.data.decode("utf-8")
        assert rc.last_instance.count("push") == 0

    def test_a_field_change_needs_the_confirmation_and_states_the_cost(
        self, client, rc, monkeypatch, platform_db
    ):
        monkeypatch.setattr(FakeRemoteControllerClient, "document_sync", {"total": 20000})
        monkeypatch.setattr(FakeRemoteControllerClient, "doc_fields_block",
                            {"enabled": True, "server": "accepted", "per_cycle": 100})
        assert self._save(client, **self.FORM).status_code == 200
        changed = dict(self.FORM, **{"folder-0-fields": "doc_type = Vertrag"})
        r = self._save(client, **changed)
        html = r.data.decode("utf-8")
        assert r.status_code == 400
        assert "Alle Dokumente der Quelle(n) /mnt/autodoc/mandate werden erneut gesendet" in html
        assert "H\u00f6chstens 20000 Dokumente; bei 100 pro Durchlauf" in html
        assert "ca. 3 N\u00e4chte" in html
        assert 'name="confirm_reupload"' in html
        assert "doc_type = Vertrag" in html, "the person's input survives"
        assert rc.last_instance.count("push") == 1
        r = self._save(client, **dict(changed, confirm_reupload="1"))
        assert r.status_code == 200
        assert rc.last_instance.count("push") == 2
        bypass = platform_db.execute(
            "SELECT detail FROM audit_log WHERE action = 'approval.bypassed' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert bypass is None or "Vertrag" not in repr(bypass[0])
        assert rc.last_instance.pushed[-1].sync_request["sources"][0]["fields"] == {
            "doc_type": "contract"}

    def test_an_unchanged_save_needs_no_confirmation(self, client, rc):
        assert self._save(client, **self.FORM).status_code == 200
        assert self._save(client, **dict(self.FORM, throughput="gentle")).status_code == 200
        assert rc.last_instance.count("push") == 2

    def test_restoring_a_version_with_other_fields_is_confirmed_first(self, client, rc):
        from _console import post_form

        assert self._save(client, **self.FORM).status_code == 200
        assert self._save(client, **dict(self.FORM, **{"folder-0-fields": "",
                                                       "confirm_reupload": "1"})).status_code == 200
        r = post_form(client, "/admin/ingestion/restore/1", page="/admin/ingestion")
        assert r.status_code == 400
        assert "Version 1 wiederherstellen und erneut senden" in r.data.decode("utf-8")
        assert rc.last_instance.count("push") == 2
        r = post_form(client, "/admin/ingestion/restore/1", page="/admin/ingestion",
                      confirm_reupload="1")
        assert r.status_code == 200
        assert rc.last_instance.count("push") == 3

    def test_preview_shows_the_captures_and_saves_nothing(self, client, rc, platform_db):
        from _console import post_form

        r = post_form(client, "/admin/ingestion/preview", page="/admin/ingestion", **self.FORM)
        html = r.data.decode("utf-8")
        assert r.status_code == 200
        assert "2 von 3 Dateien im Ausschnitt passen" in html
        assert "mandant = Muster AG; period = GJ 2024" in html
        assert "mandant = Beispiel GmbH; period = GJ 2023" in html
        assert rc.last_instance.count("push") == 0
        assert platform_db.execute("SELECT count(*) FROM ingestion_profiles").fetchone()[0] == 0
        support = html[html.index("ohne Pfade und Gruppen"):].split("</pre>")[0]
        assert "&#34;folders_with_fields&#34;: 1" in support  # autoescaped JSON
        assert "{mandant}" not in support and "doc_type" not in support

    def test_the_live_template_preview_is_a_post_with_a_csrf_header(self, client, rc):
        from _console import csrf_from

        body = {"path": "/mnt/autodoc/mandate", "templates": ["{mandant}/**"], "recursive": True}
        assert client.post("/admin/ingestion/template-preview", json=body).status_code == 400
        token = csrf_from(client.get("/admin/ingestion").data.decode("utf-8"))
        r = client.post("/admin/ingestion/template-preview", json=body,
                        headers={"X-CSRF-Token": token})
        assert r.status_code == 200
        out = r.get_json()
        assert out["files"] == 3 and out["matched"] == 2
        assert out["rows"][0]["captures"] == [{"key": "mandant", "value": "Muster AG"}]
        bad = client.post("/admin/ingestion/template-preview",
                          json={"path": "/x", "templates": ["a"] * 9},
                          headers={"X-CSRF-Token": token})
        assert bad.status_code == 400

    def test_the_status_bar_and_requeue(self, client, rc, platform_db, monkeypatch):
        from _console import post_form

        monkeypatch.setattr(FakeRemoteControllerClient, "doc_fields_block", {
            "enabled": True, "server": "accepted", "per_cycle": 100,
            "documents": {"refused": 3, "pending_reupload": 5}})
        monkeypatch.setattr(FakeRemoteControllerClient, "requeue_answer", 3)
        html = client.get("/admin/ingestion").data.decode("utf-8")
        assert "Felder bei 3 Uploads abgelehnt" in html
        assert 'name="outcome" value="refused"' in html
        r = post_form(client, "/admin/ingestion/doc-fields/requeue", page="/admin/ingestion",
                      outcome="refused")
        assert r.status_code == 200
        assert "3 Dokumente zum erneuten Senden vorgemerkt" in r.data.decode("utf-8")
        assert rc.last_instance.requeued == ["refused"]
        row = platform_db.execute(
            "SELECT detail FROM audit_log WHERE action = 'ingestion.doc_fields_requeued'"
        ).fetchone()
        assert row[0] == {"outcome": "refused", "requeued": 3}

    def test_requeue_checks_csrf_and_the_outcome(self, client, rc):
        from _console import post_form

        assert client.post("/admin/ingestion/doc-fields/requeue",
                           data={"outcome": "refused"}).status_code == 400
        r = post_form(client, "/admin/ingestion/doc-fields/requeue", page="/admin/ingestion",
                      outcome="everything")
        assert r.status_code == 400
        assert rc.last_instance.requeued == []

    def test_no_value_reaches_a_log_line_or_the_audit_row(self, client, rc, platform_db, caplog):
        import logging

        from _console import post_form

        caplog.set_level(logging.DEBUG)
        form = dict(self.FORM, **{
            "folder-0-fields": f"doc_type = Rechnung\nkeywords = {SENTINEL}",
            "folder-0-templates": f"{SENTINEL}/{{mandant}}/**"})
        post_form(client, "/admin/ingestion/preview", page="/admin/ingestion", **form)
        assert self._save(client, **form).status_code == 200
        assert SENTINEL not in caplog.text
        details = [r[0] for r in platform_db.execute("SELECT detail FROM audit_log").fetchall()]
        assert SENTINEL not in repr(details)


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="No PostgreSQL at the identity test DSN")
class TestLiveDocumentFieldsOff(TestLiveDocumentFields):
    """Knovas without document fields: no inputs for a profile without them,
    and a profile that has them keeps them (and cannot change them)."""

    @pytest.fixture
    def knovas_mode(self):
        return "off"

    def test_the_page_offers_the_inputs_and_the_registry(self, client):
        html = client.get("/admin/ingestion").data.decode("utf-8")
        assert "folder-__n__-fields" not in html and "Vorlagen testen" not in html

    def test_saving_validates_normalises_and_pushes_the_fields(self, client, rc):
        from web_interface.admin_ingestion import UNVERIFIABLE

        r = self._save(client, **self.FORM)
        assert r.status_code == 400
        assert UNVERIFIABLE.split(":")[0] in r.data.decode("utf-8")
        assert rc.last_instance.count("push") == 0

    def test_a_stored_field_config_is_kept_and_shown(self, client, rc, platform_db, identity_repo):
        from identity.ingestion_profiles import IngestionProfileRepository

        IngestionProfileRepository(platform_db).save_new_version(
            _profile(_folder("/mnt/autodoc/mandate", fields={"doc_type": "invoice"}),
                     prefix="kanzlei"),
            by=identity_repo.get_by_email("chef@kanzlei.ch"))
        html = client.get("/admin/ingestion").data.decode("utf-8")
        assert "doc_type = invoice</textarea>" in html
        assert "Knovas stellt Dokumentfelder derzeit nicht bereit" in html
        r = self._save(client, **dict(SAVE_FORM, **{"folder-0-fields": "doc_type = invoice"}))
        assert r.status_code == 200, "an unchanged field config saves without a registry"
        assert rc.last_instance.pushed[-1].sync_request["sources"][0]["fields"] == {
            "doc_type": "invoice"}

    # Tests of the base class that need Knovas to offer fields.
    test_a_field_change_needs_the_confirmation_and_states_the_cost = None
    test_an_unchanged_save_needs_no_confirmation = None
    test_restoring_a_version_with_other_fields_is_confirmed_first = None
    test_preview_shows_the_captures_and_saves_nothing = None
    test_an_unknown_key_is_refused_before_anything_is_sent = None
    test_no_value_reaches_a_log_line_or_the_audit_row = None


@pytest.mark.skipif(not platform_db_reachable(),
                    reason="the approval path needs a real PostgreSQL")
class TestStaleApprovalNeverResendsUnconfirmed:
    """platform-admin-ingestion-8: an approved request runs against the
    profile current at execution. When someone changed the field
    configuration in between, the push would revert it and re-send whole
    folders nobody confirmed and the approver was not shown -- refused."""

    class _RC:
        def __init__(self):
            self.pushed = []

        def capabilities(self):
            return frozenset(ALL_CAPS)

        def push(self, compiled):
            self.pushed.append(compiled)
            return {"applied": "stored"}

    @staticmethod
    def _profile(template, schedule):
        from identity.ingestion_compiler import IngestionProfile, SourceFolder

        return IngestionProfile(identifier_prefix="kanzlei", schedule=schedule, sources=[
            SourceFolder(path="/mnt/x", recursive=True, field_templates=(template,))])

    @pytest.fixture
    def people(self, identity_repo):
        from _console import PASSWORD

        out = {}
        for email, role in (("a@kanzlei.ch", "ingestion_manager"), ("b@kanzlei.ch", "admin"),
                            ("c@kanzlei.ch", "admin")):
            user = identity_repo.create(email=email, display_name=email, password=PASSWORD)
            identity_repo.grant_role(user.id, role)
            out[email[0]] = identity_repo.get(user.id)
        return out

    def _request(self, platform_db, identity_repo, requester, profile):
        from identity.approvals import ApprovalService
        from identity.ingestion_profiles import IngestionProfileRepository, profile_to_json
        from web_interface import admin_ingestion

        current = IngestionProfileRepository(platform_db).current()
        changed = admin_ingestion.field_config_changes(current.profile, profile)
        note = admin_ingestion._reupload_note(
            admin_ingestion.FieldCheck(profile, tuple(changed)), current)
        payload = {"profile": profile_to_json(profile), "requested_by": str(requester.id),
                   **note}
        return ApprovalService(platform_db, identity_repo).request(
            requester, kind="ingestion_profile_change", target_ref="ingestion_profile:default",
            payload=payload)

    def _direct(self, platform_db, actor, profile, rc, **extra):
        from identity.ingestion_profiles import profile_to_json
        from web_interface import admin_ingestion

        admin_ingestion.apply_profile({"profile": profile_to_json(profile), **extra}, actor,
                                      conn=platform_db, rc_client=rc)

    @pytest.mark.parametrize("requested_template", ["{mandant}/**", "{client}/**"])
    def test_a_field_change_in_between_refuses_the_approved_push(
            self, platform_db, identity_repo, people, requested_template):
        from identity.approvals import ApprovalService
        from identity.ingestion_compiler import ProfileError
        from identity.ingestion_profiles import IngestionProfileRepository
        from web_interface import admin_ingestion

        rc = self._RC()
        repo = IngestionProfileRepository(platform_db)
        self._direct(platform_db, people["b"], self._profile("{mandant}/**", "nightly"), rc)
        # A asks (needs approval): either a schedule-only change, or a field
        # change with its re-upload confirmed against the version current now.
        req = self._request(platform_db, identity_repo, people["a"],
                            self._profile(requested_template, "continuous"))
        # B changes the folder's fields directly in between.
        self._direct(platform_db, people["b"], self._profile("{mandant}/{period}/**", "nightly"),
                     rc, reupload_folders=1)
        pushes = len(rc.pushed)
        approved = ApprovalService(platform_db, identity_repo).approve(req.id, people["c"])
        with pytest.raises(ProfileError) as refused:
            admin_ingestion.execute_ingestion_change(approved.payload, people["c"],
                                                     conn=platform_db, rc_client=rc)
        assert str(refused.value) == admin_ingestion.STALE_REUPLOAD
        assert len(rc.pushed) == pushes, "nothing pushed"
        assert repo.current().profile.sources[0].field_templates == ("{mandant}/{period}/**",)

    def test_an_unchanged_base_executes_as_confirmed(self, platform_db, identity_repo, people):
        from identity.approvals import ApprovalService
        from identity.ingestion_profiles import IngestionProfileRepository
        from web_interface import admin_ingestion

        rc = self._RC()
        self._direct(platform_db, people["b"], self._profile("{mandant}/**", "nightly"), rc)
        req = self._request(platform_db, identity_repo, people["a"],
                            self._profile("{client}/**", "nightly"))
        assert req.payload["reupload_folders"] == 1 and isinstance(req.payload["base_version"], int)
        approved = ApprovalService(platform_db, identity_repo).approve(req.id, people["c"])
        admin_ingestion.execute_ingestion_change(approved.payload, people["c"],
                                                 conn=platform_db, rc_client=rc)
        assert IngestionProfileRepository(platform_db).current().profile.sources[0] \
            .field_templates == ("{client}/**",)

    def test_a_change_without_fields_in_between_still_executes(self, platform_db, identity_repo,
                                                               people):
        from identity.approvals import ApprovalService
        from identity.ingestion_profiles import IngestionProfileRepository
        from web_interface import admin_ingestion

        rc = self._RC()
        self._direct(platform_db, people["b"], self._profile("{mandant}/**", "nightly"), rc)
        req = self._request(platform_db, identity_repo, people["a"],
                            self._profile("{mandant}/**", "continuous"))
        self._direct(platform_db, people["b"], self._profile("{mandant}/**", "manual"), rc)
        approved = ApprovalService(platform_db, identity_repo).approve(req.id, people["c"])
        admin_ingestion.execute_ingestion_change(approved.payload, people["c"],
                                                 conn=platform_db, rc_client=rc)
        assert IngestionProfileRepository(platform_db).current().profile.schedule == "continuous"
