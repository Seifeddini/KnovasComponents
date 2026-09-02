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
