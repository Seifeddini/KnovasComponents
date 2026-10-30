"""Ansichten, KC-1: each tenant's definitions bundle (KnowledgeBase KB-7) instead of a copy in the code.

Both fixtures load (Knovas: the files vendored until KC-1; the law firm of case study 1, built by
KnowledgeBase's runner); a wrong hash or format version is refused with its reason on the page; a
github: source reads the bundle and its reports through the change requests' client; bundles are kept
by content hash for the definitions TTL.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest
from flask import Flask

from graph_views import bundle, definitions as defs, descriptions, doc_pages, edit_map, keys
from graph_views import service as views
from graph_views.settings import ViewSettings
from views_fixture import KNOVAS_BUNDLE, KNOVAS_SOURCE, LAWFIRM_BUNDLE, FakeViewsClient, FixtureGraph, client_zero_graph
from web_interface.views_routes import create_views_blueprint

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"


class _NoWait:
    def acquire(self):
        pass


def _doc(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(tmp_path: Path, doc: dict, name: str = "bundle.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _rehash(doc: dict) -> dict:
    doc["header"]["content_hash"] = bundle.content_hash(doc["sections"])
    return doc


class _FakeGitHub:
    """GitHubRepo.get_raw over a dict of path -> text, recording the reads."""

    def __init__(self, files: dict):
        self.files, self.reads = files, []

    def get_raw(self, path: str, ref: str):
        self.reads.append((path, ref))
        return self.files.get(path)


# -- the two fixtures ---------------------------------------------------------------------------


class TestFixtures:
    def test_the_knovas_bundle_loads_with_its_reports(self):
        b = bundle.load_file(KNOVAS_BUNDLE)
        assert (b.label, b.header["format_version"]) == ("KNOVAS", 1)
        assert b.content_hash == bundle.content_hash(b.sections)
        assert set(b.sections) == set(bundle.SECTIONS)
        assert len(b.processes["processes"]) == 7 and len(b.spines["spines"]) == 4
        assert {"process_completeness", "docs_completeness", "suggestions"} <= set(b.reports)
        assert b.report("process_completeness")["instances"] and b.report_errors == []
        assert "tenant" not in b.types                        # never derived from the bundle (DEC-0039)

    def test_the_law_firm_bundle_loads_with_empty_sections_for_what_it_lacks(self):
        b = bundle.load_file(LAWFIRM_BUNDLE)
        assert (b.label, b.header["name"], b.header["kind"]) == ("KANZLEI-X", "Kanzlei X", "law firm")
        assert [p["key"] for p in b.processes["processes"]] == ["PROC-contract-review"]
        assert b.spines == {"version": 1, "spines": []} and b.descriptions == {}
        assert [p["type"] for p in b.pages["pages"]] == ["Matter"]
        assert b.reports == {} and b.report_paths == {}

    def test_the_views_read_the_law_firm_from_its_bundle(self):
        token = bundle.use(bundle.load_file(LAWFIRM_BUNDLE))
        try:
            assert descriptions.type_entry("Contract")["label"] == "Contract"     # no descriptions: technical name
            assert descriptions.hop_fits("Contract", {"relation": "belongs_to_matter", "direction": "out"})
            assert keys.key_patterns() == {}
            assert edit_map.entry("Contract.status")["allowed_values"] == ["draft", "in review", "signed"]
            assert edit_map.suggestion_items() == []
            assert [p["key"] for p in doc_pages.definitions()] == ["matter"]
            proc, spine = defs.from_bundle()
            assert defs.validate(proc, "process-definition.schema.json") == []
            assert defs.validate(spine, "spine-definition.schema.json") == []
        finally:
            bundle.reset(token)

    def test_a_law_firm_tenant_without_definition_nodes_gets_its_bundle(self):
        g = FixtureGraph()
        g.node("Contract", "Mietvertrag Muster")
        svc = views.ViewService(lambda: FakeViewsClient(g), ViewSettings(config_source=f"file:{LAWFIRM_BUNDLE}"),
                                bucket=_NoWait(), markers=lambda: set())
        ctx = svc.context()
        d = svc.definitions(ctx)
        assert d.source == "bundle" and [p["key"] for p in d.processes] == ["PROC-contract-review"]
        assert d.spines == [] and d.pinned["label"] == "KANZLEI-X"


# -- refusals --------------------------------------------------------------------------------------


class TestRefusals:
    def test_a_wrong_hash_is_refused(self, tmp_path):
        doc = _doc(KNOVAS_BUNDLE)
        doc["sections"]["processes"]["processes"][0]["owner_role"] = "ROLE-EDITED-BY-HAND"
        with pytest.raises(bundle.BundleError) as exc:
            bundle.load_file(_write(tmp_path, doc))
        assert "Prüfsumme stimmt nicht" in str(exc.value) and "python -m brain_runner bundle" in str(exc.value)

    def test_an_unknown_format_version_is_refused(self, tmp_path):
        doc = _doc(LAWFIRM_BUNDLE)
        doc["header"]["format_version"] = 2
        with pytest.raises(bundle.BundleError) as exc:
            bundle.load_file(_write(tmp_path, doc))
        assert "Formatversion 2 wird nicht unterstützt (diese Plattform liest Version 1)" in str(exc.value)

    def test_another_format_is_refused(self, tmp_path):
        doc = _doc(LAWFIRM_BUNDLE)
        doc["header"]["format"] = "something-else"
        with pytest.raises(bundle.BundleError, match="ist kein brain-definitions-bundle"):
            bundle.load_file(_write(tmp_path, doc))

    def test_a_file_that_is_not_a_bundle_is_refused(self, tmp_path):
        (tmp_path / "bundle.json").write_text('{"processes": []}', encoding="utf-8")
        with pytest.raises(bundle.BundleError, match="kein Definitionsbündel"):
            bundle.load_file(tmp_path / "bundle.json")

    @pytest.mark.parametrize("spec, says", [
        ("", "Kein Definitionsbündel konfiguriert (views.config_source)"),
        ("https://example.org/bundle.json", "ist ungültig"),
        ("github:Seifeddini/KnowledgeBase:docs/bundle.json", "ist ungültig"),
    ])
    def test_a_missing_or_malformed_source_is_named(self, spec, says):
        with pytest.raises(bundle.BundleError, match=says.replace("(", r"\(").replace(")", r"\)")):
            bundle.parse_source(spec)

    def test_a_missing_file_is_named(self, tmp_path):
        with pytest.raises(bundle.BundleError, match="nicht gefunden"):
            bundle.read(f"file:{tmp_path / 'nope.json'}")


def _app(source: str, roles=("admin",), graph=None):
    class _Gate:
        user = type("U", (), {"id": uuid.uuid4(), "roles": frozenset(roles)})()

        def current_user(self):
            return self.user

    client = FakeViewsClient(graph or client_zero_graph())
    svc = views.ViewService(lambda: client, ViewSettings(config_source=source), bucket=_NoWait(), markers=lambda: set())
    app = Flask(__name__, template_folder=str(TEMPLATES))
    app.add_url_rule("/ontology", "ontology_page", lambda: "")
    app.register_blueprint(create_views_blueprint(_Gate(), svc, graph_mode=lambda: True,
                                                  page_context=lambda: {"csrf_token": "t", "asset_version": "1"}))
    return app.test_client(), client


class TestOnThePage:
    def test_a_wrong_hash_is_refused_with_its_reason_by_every_view(self, tmp_path):
        doc = _doc(KNOVAS_BUNDLE)
        doc["sections"]["spines"]["spines"] = []
        app, client = _app(f"file:{_write(tmp_path, doc)}")
        for path in ("/api/views/definitions", "/api/views/start", "/api/views/board?process=PROC-feature-lifecycle"):
            r = app.get(path)
            assert r.status_code == 503, path
            body = r.get_json()
            assert body["bundle_error"] is True and body["error"].startswith("Definitionsbündel: ")
            assert "Prüfsumme stimmt nicht" in body["error"]
        assert client.calls == []                             # refused before the brain is read

    def test_the_page_names_a_refused_format_version(self, tmp_path):
        doc = _rehash(_doc(LAWFIRM_BUNDLE))
        doc["header"]["format_version"] = 7
        app, _ = _app(f"file:{_write(tmp_path, doc)}")
        page = app.get("/views").get_data(as_text=True)
        assert 'id="viewsBundleError"' in page and "Formatversion 7 wird nicht unterstützt" in page
        assert 'id="viewsToolbar"' not in page
        doc_page = app.get("/views/doc/L2-KNOWLEDGE-GRAPH").get_data(as_text=True)
        assert "Formatversion 7 wird nicht unterstützt" in doc_page

    def test_no_source_configured_says_so(self):
        app, _ = _app("")
        body = app.get("/api/views/definitions").get_json()
        assert body["error"] == "Definitionsbündel: Kein Definitionsbündel konfiguriert (views.config_source)."
        assert "Kein Definitionsbündel konfiguriert" in app.get("/views").get_data(as_text=True)

    def test_a_good_bundle_draws_the_page(self):
        app, _ = _app(KNOVAS_SOURCE)
        page = app.get("/views").get_data(as_text=True)
        assert 'id="viewsBundleError"' not in page and 'id="viewsToolbar"' in page
        assert app.get("/api/views/definitions").status_code == 200


# -- github: sources, reports, caching -----------------------------------------------------------------

KB_PATH = "docs/ModernDocs/client-zero/generated/bundle.json"


def _kb_files(doc: dict) -> dict:
    """The Knovas fixture laid out as in KnowledgeBase: reports under ../ontology/generated/."""
    doc = copy.deepcopy(doc)
    fixture = KNOVAS_BUNDLE.parent
    files = {}
    for name, rel in list(doc["header"]["reports"].items()):
        doc["header"]["reports"][name] = f"../ontology/generated/{rel}"
        files[f"docs/ModernDocs/client-zero/ontology/generated/{rel}"] = (fixture / rel).read_text(encoding="utf-8")
    files[KB_PATH] = json.dumps(doc)
    return files


class TestGitHubSource:
    def test_parse(self):
        s = bundle.parse_source(f"github:Seifeddini/KnowledgeBase@master:{KB_PATH}")
        assert (s.kind, s.repo, s.ref, s.path) == ("github", "Seifeddini/KnowledgeBase", "master", KB_PATH)
        assert s.report_path("../ontology/generated/suggestions.json") == \
            "docs/ModernDocs/client-zero/ontology/generated/suggestions.json"
        with pytest.raises(bundle.BundleError, match="ausserhalb des Repositorys"):
            s.report_path("../../../../../etc/passwd")

    def test_the_bundle_and_its_reports_are_read_through_the_github_client(self):
        gh = _FakeGitHub(_kb_files(_doc(KNOVAS_BUNDLE)))
        asked = []
        b = bundle.read(f"github:Seifeddini/KnowledgeBase@master:{KB_PATH}",
                        lambda repo: asked.append(repo) or gh)
        assert asked and set(asked) == {"Seifeddini/KnowledgeBase"}
        assert b.label == "KNOVAS" and b.report("suggestions")["items"]
        assert gh.reads[0] == (KB_PATH, "master") and len(gh.reads) == 4

    def test_a_missing_report_is_listed_and_the_bundle_still_serves(self):
        files = _kb_files(_doc(KNOVAS_BUNDLE))
        files.pop("docs/ModernDocs/client-zero/ontology/generated/suggestions.json")
        b = bundle.read(f"github:Seifeddini/KnowledgeBase@master:{KB_PATH}", lambda repo: _FakeGitHub(files))
        assert b.report("suggestions") == {} and b.report_errors == ["Bericht suggestions fehlt (../ontology/generated/suggestions.json)."]

    def test_a_github_failure_names_the_token_setting(self):
        from graph_views.github_repo import GitHubError

        class Denied:
            def get_raw(self, path, ref):
                raise GitHubError("read file", 401)

        with pytest.raises(bundle.BundleError) as exc:
            bundle.read(f"github:Seifeddini/KnowledgeBase@master:{KB_PATH}", lambda repo: Denied())
        assert "HTTP 401" in str(exc.value) and "KB_GITHUB_TOKEN" in str(exc.value)

    def test_get_raw_asks_for_the_raw_media_type_and_sends_no_empty_token(self):
        from graph_views.github_repo import GitHubRepo

        class Session:
            def __init__(self):
                self.seen = []

            def get(self, url, headers, timeout):
                self.seen.append((url, headers))
                return type("R", (), {"status_code": 200, "content": "{}".encode("utf-8")})()

        s = Session()
        assert GitHubRepo("o/r", "", session=s).get_raw("a b/bundle.json", "master") == "{}"
        url, headers = s.seen[0]
        assert url == "https://api.github.com/repos/o/r/contents/a%20b/bundle.json?ref=master"
        assert headers["Accept"] == "application/vnd.github.raw+json" and "Authorization" not in headers


class TestCache:
    def test_a_bundle_is_read_again_after_the_ttl_and_kept_by_its_hash(self):
        now = [0.0]
        gh = _FakeGitHub(_kb_files(_doc(KNOVAS_BUNDLE)))
        store = bundle.BundleStore(600, github=lambda repo: gh, now=lambda: now[0])
        spec = f"github:Seifeddini/KnowledgeBase@master:{KB_PATH}"
        first = store.get("t1", spec)
        first.memo("probe", lambda: object())
        reads = len(gh.reads)
        assert store.get("t1", spec) is first and len(gh.reads) == reads          # within the TTL: no read
        now[0] = 601
        again = store.get("t1", spec)
        assert len(gh.reads) == 2 * reads                                          # read again after the TTL
        assert again.content_hash == first.content_hash
        assert again.memo("probe", lambda: None) is first.memo("probe", lambda: None)   # same hash: same derived values
        changed = _doc(KNOVAS_BUNDLE)
        changed["sections"]["owners"] = {"aliases": {}}
        gh.files.update(_kb_files(_rehash(changed)))
        assert store.get("t1", spec, refresh=True).content_hash != first.content_hash

    def test_each_tenant_gets_its_own_source(self):
        store = bundle.BundleStore(600)
        assert store.get("knovas", KNOVAS_SOURCE).label == "KNOVAS"
        assert store.get("kanzlei", f"file:{LAWFIRM_BUNDLE}").label == "KANZLEI-X"

    def test_a_refused_bundle_is_not_read_per_request(self, tmp_path):
        doc = _doc(LAWFIRM_BUNDLE)
        doc["header"]["format_version"] = 9
        path = _write(tmp_path, doc)
        store = bundle.BundleStore(600)
        with pytest.raises(bundle.BundleError):
            store.get("t", f"file:{path}")
        _write(tmp_path, _doc(LAWFIRM_BUNDLE))                # fixed on disk
        with pytest.raises(bundle.BundleError):
            store.get("t", f"file:{path}")                    # still the remembered refusal
        assert store.get("t", f"file:{path}", refresh=True).label == "KANZLEI-X"

    def test_definitions_are_cached_per_bundle(self, tmp_path):
        path = _write(tmp_path, _doc(KNOVAS_BUNDLE))
        svc = views.ViewService(lambda: FakeViewsClient(client_zero_graph()), ViewSettings(config_source=f"file:{path}"),
                                bucket=_NoWait(), markers=lambda: set())
        first = svc.definitions(svc.context())
        assert svc.definitions(svc.context()) is first
        changed = _doc(KNOVAS_BUNDLE)
        changed["sections"]["owners"] = {"aliases": {}}
        _write(tmp_path, _rehash(changed))
        assert svc.definitions(svc.context(refresh=True)) is not first
