"""Two Low findings of the BB-07 pre-review (FEAT-2026-11-05-knovas-brain-v1, S-5).

B6: YAML aliases were expanded on import. ``_Loader`` resolves them by reference, so a package of a few kilobytes with
nested aliases loaded small and grew exponentially once the model was serialised into ``builder_version``. Packages do
use aliases (a rule's condition named once and reused), so they are counted with every alias written out and refused
over a limit, as is an alias that refers to a node containing it.

B7: an export hands out the whole workspace (sources and their settings, access design, role mapping, mappings) and left
no access-log row when a role holder made it. Every export is now logged.
"""
import io

import pytest

from builder import package
from builder_harness import SAMPLE_PACKAGE, FakeEngine, MemoryWorkspaceStore, StandInUser, make_app


def _bomb(levels=8, width=10) -> str:
    lines = ["l0: &l0 [" + ", ".join(["x"] * width) + "]"]
    for n in range(1, levels + 1):
        lines.append(f"l{n}: &l{n} [" + ", ".join([f"*l{n - 1}"] * width) + "]")
    return "\n".join(lines) + "\n"


class TestAliases:
    def test_a_billion_laughs_is_refused_before_it_grows(self):
        text = _bomb()
        assert len(text) < 1024
        with pytest.raises(package.PackageError) as refused:
            package.load_yaml(text, "ontology/terms.yaml")
        assert "YAML-Aliasen" in refused.value.message and "ontology/terms.yaml" in refused.value.message

    def test_an_alias_that_contains_itself_is_refused(self):
        with pytest.raises(package.PackageError) as refused:
            package.load_yaml("a: &a [1, *a]\n", "x.yaml")
        assert "auf sich selbst" in refused.value.message

    def test_a_reused_condition_still_loads(self):
        text = ("rules:\n  - {when: &approved {fact: status, in: [approved, released]}, message: a}\n"
                "  - {when: *approved, message: b}\n")
        loaded = package.load_yaml(text, "processes.yaml")
        assert loaded["rules"][1]["when"] == {"fact": "status", "in": ["approved", "released"]}

    def test_the_count_writes_nothing_out(self):
        assert package.expanded_size(package.load_yaml(_bomb(levels=3), "x"), limit=10**9) > 1000
        # The mapping, its two keys, the list and its two items, and "c".
        assert package.expanded_size({"a": [1, 2], "b": "c"}) == 1 + 1 + 3 + 1 + 1

    def test_an_upload_with_one_is_refused_and_nothing_made(self):
        admin = StandInUser({"admin"}, "Ada Admin")
        store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
        http = make_app(admin, store, tenant="t-b6", engine=FakeEngine()).test_client()
        files = package.files_from_directory(SAMPLE_PACKAGE)
        files["mappings/bomb.yaml"] = _bomb().encode()
        data = package.zip_files(files)
        refused = http.post("/builder/api/import", content_type="multipart/form-data",
                            data={"name": "Bombe", "package": (io.BytesIO(data), "p.zip")})
        assert refused.status_code == 422 and "YAML-Aliasen" in refused.get_json()["error"]
        assert store.list() == [], "nothing made"


class TestTheExportIsLogged:
    def test_a_role_holder_s_export_leaves_a_row(self):
        admin = StandInUser({"admin"}, "Ada Admin")
        store = MemoryWorkspaceStore({str(admin.id): admin.display_name})
        http = make_app(admin, store, tenant="t-b7", engine=FakeEngine()).test_client()
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        workspace = store.create(name="Muster AG", model=model, actor_id=admin.id, source={"kind": "blank"})
        assert http.get(f"/builder/w/{workspace}").status_code == 200
        assert http.get(f"/builder/w/{workspace}/export").status_code == 200
        rows = store.access_log(workspace)
        assert [(r["action"], r["method"], r["status"], r["via"]) for r in rows] == [("export", "GET", 200, "role")], \
            "the export is logged; other reads of a role holder are not"
