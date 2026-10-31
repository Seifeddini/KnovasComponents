"""KC-0.2: "Sync from GitHub" — the Platform syncs the Brain from the knowledge base on demand.

Against an in-memory GitHub API over the law firm of case study 1 (tests/fixtures/lawfirm-bundle/config plus a few
files under Akten/) and an in-memory graph API: the first sync fetches every configuration file, the second only the
changed ones; Anwenden applies exactly the Probelauf's plans, then evaluates and bundles; outputs and history per
tenant; refusal when the commit or the tenant changed since the Probelauf; one sync per tenant at a time; admins only;
the token in no response and no log.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import pytest
from flask import Flask

import platform_settings as ps
from brain_sync import Busy, PackageRunner, Refused, SyncService, SyncStore
from fake_brain_api import FakeApi
from fake_github_api import FakeGitHub
from graph_views import bundle as bundles

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "lawfirm-bundle" / "config"
TOKEN = "github_pat_" + "s" * 40                    # made up; the fake GitHub refuses calls without it
REPO = "kanzlei/akten"
AKTEN = {"Akten/M-2026-001/vertrag-1.pdf": "%PDF-1.4 one", "Akten/M-2026-001/vertrag-2.pdf": "%PDF-1.4 two",
         "Akten/M-2026-002/vertrag-3.pdf": "%PDF-1.4 three"}
CONFIG_FILES = 13                                    # brain-config, one mapping, the ontology files, one profile, health


def _files() -> dict:
    out = {p.relative_to(FIXTURE).as_posix(): p.read_text(encoding="utf-8") for p in FIXTURE.rglob("*") if p.is_file()}
    out.update(AKTEN)
    return out


RAW = """brain_sync:
  repository: "${BRAIN_SYNC_REPOSITORY:-kanzlei/akten}"
  branch: "${BRAIN_SYNC_BRANCH:-main}"
  config_path: "${BRAIN_SYNC_CONFIG_PATH:-brain-config.yaml}"
  github_token_file: "${BRAIN_SYNC_GITHUB_TOKEN_FILE:-/nonexistent/token}"
"""


class _Config:
    def __init__(self, path: Path):
        self.config_path = str(path)

    def get(self, key, default=None):
        values = {"brain_sync.repository": REPO, "brain_sync.branch": "main", "brain_sync.config_path": "brain-config.yaml",
                  "brain_sync.github_token_file": "/nonexistent/token"}
        return values.get(key, default)

    def get_int(self, key, default=0):
        return default

    def get_float(self, key, default=0.0):
        return default


class _User:
    def __init__(self, email="chef@kanzlei.ch", roles=("admin",), uid=None):
        self.id, self.email, self.roles = uid or str(uuid.uuid4()), email, frozenset(roles)


class _Tenant:
    """The Platform's tenant and its certificate: a renewed or replaced certificate is another tenant reference."""

    def __init__(self):
        self.id, self.cert = "tenant-a", "cert-1"

    def ref(self) -> str:
        import hashlib

        return hashlib.sha256(f"{self.id}|{self.cert}".encode()).hexdigest()[:16]


@pytest.fixture
def world(tmp_path, monkeypatch):
    gh = FakeGitHub(repo=REPO, token=TOKEN)
    first = gh.commit(_files(), message="Akten und Konfiguration", date="2026-10-01T10:00:00Z")
    api = FakeApi()
    for t in ("Contract", "Matter", "Client"):
        api.post("secured/graph/node-types", {"name": t})
    (tmp_path / "config.yaml").write_text(RAW, encoding="utf-8")
    resolver = ps.Resolver(_Config(tmp_path / "config.yaml"), tenant="tenant-a",
                           environ={"BRAIN_SYNC_GITHUB_TOKEN": TOKEN}, box=ps.SecretBox(None))
    return {"gh": gh, "first": first, "api": api, "resolver": resolver, "storage": tmp_path / "sync", "tenant": _Tenant()}


def _service(world, store) -> SyncService:
    return SyncService(world["resolver"], tenant=lambda: world["tenant"].id, tenant_ref=world["tenant"].ref,
                       storage=world["storage"], store=lambda: store,
                       runner=PackageRunner(github_session=world["gh"], brain_api=lambda: world["api"]),
                       run_in_background=lambda job: job())


# -- registry ------------------------------------------------------------------------------------------------------


def test_the_sync_settings_are_registered():
    for key in ("brain_sync.repository", "brain_sync.branch", "brain_sync.config_path", "brain_sync.github_token"):
        assert key in ps.REGISTRY and ps.REGISTRY[key].scope == "tenant"
    assert ps.REGISTRY["brain_sync.github_token"].kind == "secret"
    with pytest.raises(ps.SettingError):
        ps.clean(ps.REGISTRY["brain_sync.repository"], "not a repo", roles=())
    with pytest.raises(ps.SettingError):
        ps.clean(ps.REGISTRY["brain_sync.config_path"], "../outside.yaml", roles=())


def test_bundle_sources_know_the_kind_sync():
    s = bundles.parse_source("sync")
    assert s.kind == "sync"


# -- the database ------------------------------------------------------------------------------------------------------

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable  # noqa: E402

needs_db = pytest.mark.skipif(not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


@pytest.fixture
def admin(identity_repo):
    u = identity_repo.create(email="chef@kanzlei.ch", display_name="Chef", password="korrektes-pferd-batterie")
    identity_repo.grant_role(u.id, "admin")
    return identity_repo.get(u.id)


@needs_db
class TestSync:
    def test_first_sync_fetches_every_configuration_file_then_applies_evaluates_and_bundles(self, world, platform_db, admin):
        svc = _service(world, SyncStore(platform_db))
        head = svc.check(admin)
        assert head["commit"] == world["first"] and head["message"] == "Akten und Konfiguration"
        assert head["changed_mapped_files"] is None                    # no sync yet: everything is new
        writes = world["api"].writes
        probe = svc.run(svc.probe(admin))
        assert probe["status"] == "ok" and probe["files_fetched"] == CONFIG_FILES
        counts = probe["counts"]["contracts"]
        assert counts["add"] == len(AKTEN) and counts["source_items"] == len(AKTEN) and not counts["blocked"]
        assert world["api"].writes == writes                             # a Probelauf writes nothing
        applied = svc.run(svc.apply(admin))
        assert applied["status"] == "ok" and applied["commit"] == world["first"]
        current = svc.current()
        assert current["commit"] == world["first"]
        bundle = json.loads(Path(current["bundle"]).read_text(encoding="utf-8"))
        label = bundle["header"]["label"]  # the key (brain-runner before 0.1.0) or the whole company (0.1.0 on)
        assert (label.get("key") if isinstance(label, dict) else label) == "KANZLEI-X"
        assert "partner" in bundle["sections"]["profiles"]
        evaluation = json.loads(Path(current["evaluation"]).read_text(encoding="utf-8"))
        assert [m["mapping"] for m in evaluation["mappings"]] == ["contracts"]

    def test_the_second_sync_fetches_only_the_changed_files(self, world, platform_db, admin):
        svc = _service(world, SyncStore(platform_db))
        svc.run(svc.probe(admin))
        svc.run(svc.apply(admin))
        blobs = world["gh"].count("blob")
        terms = (FIXTURE / "ontology/terms.yaml").read_text(encoding="utf-8")
        world["gh"].commit({"ontology/terms.yaml": terms + "\n# eine Ergänzung\n",
                            "Akten/M-2026-003/vertrag-4.pdf": "%PDF-1.4 four"}, message="ein neuer Vertrag",
                           date="2026-10-02T10:00:00Z")
        head = svc.check(admin)
        assert head["changed_mapped_files"] == 1                        # the new Akte; terms.yaml is configuration
        probe = svc.run(svc.probe(admin))
        # fetched: the changed terms.yaml (once, by "Neuen Stand holen") and, with brain-runner before 0.1.0, the new
        # Akte (by the plan; 0.1.0 and later take what the mapping reads of it without its content); nothing else
        assert probe["files_fetched"] in (1, 2) and world["gh"].count("blob") == blobs + probe["files_fetched"]
        assert probe["counts"]["contracts"]["add"] == 1
        applied = svc.run(svc.apply(admin))
        assert applied["status"] == "ok" and svc.current()["commit"] == head["commit"]

    def test_anwenden_is_refused_when_the_tenant_changed_since_the_probelauf(self, world, platform_db, admin):
        svc = _service(world, SyncStore(platform_db))
        svc.run(svc.probe(admin))
        world["tenant"].cert = "cert-2"                                  # the certificate was replaced
        with pytest.raises(Refused, match="Probelauf"):
            svc.apply(admin)
        assert svc.history()[0]["status"] == "refused" and svc.current() is None

    def test_anwenden_is_refused_when_the_commit_changed_since_the_probelauf(self, world, platform_db, admin):
        svc = _service(world, SyncStore(platform_db))
        svc.run(svc.probe(admin))
        world["gh"].commit({"Akten/M-2026-009/x.pdf": "%PDF x"}, date="2026-10-03T10:00:00Z")
        with pytest.raises(Refused, match="Probelauf"):
            svc.apply(admin)

    def test_anwenden_needs_a_probelauf(self, world, platform_db, admin):
        with pytest.raises(Refused, match="Probelauf"):
            _service(world, SyncStore(platform_db)).apply(admin)

    def test_one_sync_per_tenant_at_a_time(self, world, platform_db, admin):
        store = SyncStore(platform_db)
        held = store.start("tenant-a", "probe", admin, branch="main")
        with pytest.raises(Busy):
            _service(world, store).probe(admin)
        store.finish(held, "failed", result="test")
        assert _service(world, store).run(_service(world, store).probe(admin))["status"] == "ok"

    def test_history_holds_counts_and_hashes_and_is_audited(self, world, platform_db, admin):
        from identity import audit

        svc = _service(world, SyncStore(platform_db))
        svc.run(svc.probe(admin))
        svc.run(svc.apply(admin))
        rows = svc.history()
        assert [r["kind"] for r in rows] == ["apply", "probe"]
        for r in rows:
            assert r["commit"] == world["first"] and r["started_by"] == "chef@kanzlei.ch" and r["status"] == "ok"
            assert r["duration_ms"] is not None and r["counts"]["contracts"]["source_items"] == len(AKTEN)
        assert len(rows[1]["plan_sha256"]) == 64
        text = json.dumps(rows, default=str)
        assert "vertrag-1" not in text and "Akten/" not in text        # counts and hashes only, no file names
        actions = [r["action"] for r in audit.recent(platform_db) if r["action"].startswith("brain_sync.")]
        assert actions == ["brain_sync.apply", "brain_sync.probe"]

    def test_the_last_sync_becomes_the_views_source(self, world, platform_db, admin):
        svc = _service(world, SyncStore(platform_db))
        resolver = ps.Resolver(world["resolver"]._config, tenant="tenant-a", environ={"BRAIN_SYNC_GITHUB_TOKEN": TOKEN},
                               box=ps.SecretBox(None), defaults={"views.config_source": svc.default_bundle_source})
        assert resolver.effective("views.config_source").value == ""
        svc.run(svc.probe(admin))
        svc.run(svc.apply(admin))
        resolver.invalidate()
        e = resolver.effective("views.config_source")
        assert (e.value, e.source) == ("sync", "default")
        store = bundles.BundleStore(600, sync_path=lambda tenant: svc.bundle_path())
        assert store.get("tenant-a", "sync").label == "KANZLEI-X"

    def test_the_token_is_in_no_log(self, world, platform_db, admin, caplog):
        caplog.set_level(logging.DEBUG)
        svc = _service(world, SyncStore(platform_db))
        svc.check(admin)
        svc.run(svc.probe(admin))
        svc.run(svc.apply(admin))
        assert TOKEN not in caplog.text
        assert TOKEN not in json.dumps(svc.history(), default=str)


# -- the page ------------------------------------------------------------------------------------------------------------


def _app(world, store, user):
    from web_interface.sync_routes import create_sync_blueprint

    gate = type("G", (), {"current_user": lambda self: user})()
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
    app.register_blueprint(create_sync_blueprint(_service(world, store), gate=gate))
    return app.test_client()


@needs_db
class TestPage:
    def test_a_member_is_refused_everywhere(self, world, platform_db, identity_repo):
        member = identity_repo.create(email="ida@kanzlei.ch", display_name="Ida", password="korrektes-pferd-batterie")
        identity_repo.grant_role(member.id, "member")
        c = _app(world, SyncStore(platform_db), identity_repo.get(member.id))
        assert c.get("/api/sync").status_code == 403
        for path in ("/api/sync/check", "/api/sync/probe", "/api/sync/apply"):
            assert c.post(path).status_code == 403
        assert platform_db.execute("SELECT count(*) FROM brain_sync_runs").fetchone()[0] == 0

    def test_an_admin_runs_a_sync_and_the_token_never_comes_back(self, world, platform_db, admin, caplog):
        caplog.set_level(logging.DEBUG)
        c = _app(world, SyncStore(platform_db), admin)
        bodies = [c.post("/api/sync/check"), c.post("/api/sync/probe"), c.post("/api/sync/apply"), c.get("/api/sync")]
        assert [b.status_code for b in bodies] == [200, 202, 202, 200]
        state = bodies[-1].get_json()
        assert state["current"]["commit"] == world["first"] and state["settings"]["token"] == "gesetzt"
        assert [r["kind"] for r in state["history"]] == ["apply", "probe"]
        for b in bodies:
            assert TOKEN not in b.get_data(as_text=True)
        assert TOKEN not in caplog.text

    def test_a_refusal_is_a_409_with_the_reason(self, world, platform_db, admin):
        c = _app(world, SyncStore(platform_db), admin)
        r = c.post("/api/sync/apply")
        assert r.status_code == 409 and "Probelauf" in r.get_json()["error"]
