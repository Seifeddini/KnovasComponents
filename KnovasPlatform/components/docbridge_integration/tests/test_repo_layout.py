"""The change requests' repository layout is a setting with empty defaults (V1-G4).

Empty means "not configured": the writer or the decision record that needs a path refuses with the setting's name,
and nothing falls back to a tenant's folder. Generic paths only here; the Knovas values are in views_fixture.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from graph_views import edit_map, repo_layout, writers
from graph_views.repo_layout import RepoLayout

CONFIG_YAML = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
GENERIC = RepoLayout(decisions_dir="handbook/decisions/", plan_links_path="/handbook/links/plan-links.yaml",
                     sample_edges_path="handbook\\links\\risks.yaml")


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_the_product_default_is_empty():
    assert repo_layout.current() == RepoLayout() and not any(vars(RepoLayout()).values())
    change_requests = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8"))["views"]["change_requests"]
    for name, (key, env) in repo_layout.SETTINGS.items():
        assert change_requests[key.rsplit(".", 1)[1]] == "${" + env + ":-}", name


def test_paths_are_read_from_the_config_and_normalised():
    layout = RepoLayout.from_config(_Config({"views.change_requests.decisions_dir": "handbook/decisions/",
                                             "views.change_requests.plan_links_path": " links/plan.yaml "}))
    assert layout == RepoLayout(decisions_dir="handbook/decisions", plan_links_path="links/plan.yaml")
    assert GENERIC.sample_edges_path == "handbook/links/risks.yaml" and GENERIC.plan_links_path.startswith("handbook/")
    with pytest.raises(ValueError, match="ohne"):
        RepoLayout(decisions_dir="handbook/../../etc")


def test_an_edit_map_source_names_a_file_by_its_tail_only():
    assert repo_layout.names_file("links/plan-links.yaml", "handbook/links/plan-links.yaml")
    assert repo_layout.names_file("handbook/links/plan-links.yaml", "handbook/links/plan-links.yaml")
    assert not repo_layout.names_file("ks/plan-links.yaml", "handbook/links/plan-links.yaml")
    assert not repo_layout.names_file("", "handbook/links/plan-links.yaml")
    assert not repo_layout.names_file("links/plan-links.yaml", "")


@pytest.mark.parametrize("edit_id,field", [("FeaturePlan serves Capability", "plan_links_path"),
                                           ("FeaturePlan motivated_by Goal", "plan_links_path"),
                                           ("PreMortem examines FeaturePlan", "sample_edges_path"),
                                           ("Risk mitigated_by FeaturePlan", "sample_edges_path")])
def test_an_edge_writer_without_its_file_refuses_and_names_the_setting(edit_id, field):
    w = writers.WRITERS[edit_id]
    key, env = repo_layout.SETTINGS[field]
    assert w.path == "" and key in w.routing_problem() and env in w.routing_problem()


def test_an_edge_writer_follows_the_configured_file(monkeypatch):
    repo_layout.configure(GENERIC)
    w = writers.WRITERS["FeaturePlan serves Capability"]
    assert w.files(writers.Plan("FEAT-x", "plans/x.md")) == ["handbook/links/plan-links.yaml"]
    entry = {"editable": True, "source": "links/plan-links.yaml", "key_path": w.key_path}
    monkeypatch.setattr(edit_map, "entry", lambda i: entry)
    assert w.routing_problem() is None
    entry["source"] = "links/elsewhere.yaml"
    assert "links/elsewhere.yaml" in w.routing_problem()


def test_sources_resolve_only_against_configured_files(monkeypatch):
    monkeypatch.setattr(edit_map, "entry", lambda i: {"source": "links/plan-links.yaml; other/notes.md"})
    assert edit_map.sources("x") == ["links/plan-links.yaml", "other/notes.md"]       # nothing guessed
    repo_layout.configure(GENERIC)
    assert edit_map.sources("x") == ["handbook/links/plan-links.yaml", "other/notes.md"]
