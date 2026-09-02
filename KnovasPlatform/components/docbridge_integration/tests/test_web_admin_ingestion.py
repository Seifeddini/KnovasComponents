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

    def __init__(self, base_url, *, principal_broker=None, session=None, timeout=20.0):
        self.base_url = base_url
        self.principal_broker = principal_broker
        self.calls: list[str] = []
        self.pushed: list = []
        FakeRemoteControllerClient.last_instance = self

    def count(self, name: str) -> int:
        return self.calls.count(name)

    def discover(self, root=None, max_depth=3):
        self.calls.append("discover")
        return {"entries": [{"type": "file", "path": "a.docx"}], "truncated": False}

    def status(self):
        self.calls.append("status")
        return {"scheduler_state": "not_running", "files_synced_local": 0}

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
