"""The Ingestion tab: one form, one write, and a preview before either."""

from __future__ import annotations

import inspect
import pathlib

import pytest

flask = pytest.importorskip("flask")

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"


class TestShape:
    def test_routes_are_gated_and_posts_check_csrf_first(self):
        from web_interface import admin_ingestion

        src = inspect.getsource(admin_ingestion)
        assert src.count("@bp.route") == src.count("@require_ingestion")
        for fn in ("def preview(", "def save(", "def restore(", "def start(", "def stop("):
            body = src[src.index(fn):src.index(fn) + 700]
            assert "csrf_ok" in body

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
