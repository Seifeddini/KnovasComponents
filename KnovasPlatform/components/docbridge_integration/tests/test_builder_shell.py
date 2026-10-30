"""The Brain Builder's shell (build plan step B0).

Authorisation is asserted on the route, not on the link: every stage and the
style guide refuse a signed-in person without a builder role, and send an
anonymous caller to the login. The design-system rules the plan sets are
checked on the files themselves: colours only in tokens.css, nothing loaded
from outside (the Platform runs offline at clients).
"""
import re

import pytest

from builder_harness import WEB, StandInUser, make_app
from web_interface.builder_routes import BUILDER_ROLES, STAGES

BUILDER_PATHS = ["/builder/", "/builder/_styleguide"] + [f"/builder/{s.slug}" for s in STAGES]


def _client(roles):
    user = None if roles is None else StandInUser(roles)
    return make_app(user).test_client()


class TestTheGate:
    @pytest.mark.parametrize("path", BUILDER_PATHS)
    def test_nobody_signed_in_goes_to_the_login(self, path):
        response = _client(None).get(path)
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login?next=")

    @pytest.mark.parametrize("path", BUILDER_PATHS)
    @pytest.mark.parametrize("roles", [set(), {"member"}, {"approver", "ingestion_manager"}])
    def test_a_person_without_a_builder_role_is_refused(self, path, roles):
        assert _client(roles).get(path).status_code == 403

    def test_an_unknown_stage_is_refused_before_it_is_looked_up(self):
        # The gate runs first, so the 404 below cannot tell an outsider which
        # stage names exist.
        assert _client({"member"}).get("/builder/nichts").status_code == 403
        assert _client({"admin"}).get("/builder/nichts").status_code == 404

    def test_admin_is_the_builder_role_until_the_builder_roles_exist(self):
        assert BUILDER_ROLES == frozenset({"admin"})


class TestTheShell:
    def test_the_entry_point_opens_the_first_stage(self):
        response = _client({"admin"}).get("/builder/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/builder/{STAGES[0].slug}")

    def test_the_rail_lists_the_eight_stages_in_order(self):
        html = _client({"admin"}).get("/builder/ontologie").get_data(as_text=True)
        labels = re.findall(r'<span class="b-stage-label">(.*?)</span>', html)
        assert labels == [
            "Arbeitsbereich", "Verbinden", "Ontologie", "Prozesse",
            "Quellen &amp; Zuordnung", "Ansichten &amp; Rollen",
            "Prüfen &amp; Veröffentlichen", "Betrieb",
        ]

    @pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.slug)
    def test_each_stage_marks_itself_current_and_shows_its_empty_state(self, stage):
        html = _client({"admin"}).get(f"/builder/{stage.slug}").get_data(as_text=True)
        current = re.findall(r'href="/builder/([a-z]+)"\s+aria-current="page"', html)
        assert current == [stage.slug]
        assert 'class="b-empty"' in html
        assert stage.empty_title in html

    def test_the_switcher_says_no_workspace_is_chosen(self):
        html = _client({"admin"}).get("/builder/arbeitsbereich").get_data(as_text=True)
        assert "Keiner gewählt" in html
        assert "Noch keiner angelegt." in html


class TestTheStyleGuide:
    def test_it_renders_every_component_in_both_themes(self):
        html = _client({"admin"}).get("/builder/_styleguide").get_data(as_text=True)
        for theme in ("light", "dark"):
            assert f'class="sg-theme" data-theme="{theme}"' in html
        for marker in (
            "b-button--primary", "b-button--secondary", "b-button--quiet", "b-button--danger",
            'class="b-input', 'class="b-select', 'class="b-textarea', 'class="b-check',
            'class="b-tag', 'class="b-card', 'data-panel', 'role="tablist"', 'class="b-table"',
            'class="b-toast ', 'class="b-empty"', "b-diff-line--added", "b-diff-line--changed",
            "b-diff-line--removed", 'class="b-toolbar"',
        ):
            assert html.count(marker) >= 2, marker

    def test_ids_stay_unique_with_both_themes_on_one_page(self):
        html = _client({"admin"}).get("/builder/_styleguide").get_data(as_text=True)
        ids = re.findall(r'\sid="([^"]+)"', html)
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("view", ["beide", "hell", "dunkel"])
    def test_a_view_can_be_chosen(self, view):
        html = _client({"admin"}).get(f"/builder/_styleguide?ansicht={view}").get_data(as_text=True)
        assert f'data-view="{view}"' in html

    def test_an_unknown_view_falls_back_to_both(self):
        html = _client({"admin"}).get("/builder/_styleguide?ansicht=<x>").get_data(as_text=True)
        assert 'data-view="beide"' in html


BUILDER_FILES = (
    sorted((WEB / "static" / "css" / "builder").glob("*.css"))
    + sorted((WEB / "static" / "js" / "builder").glob("*.js"))
    + sorted((WEB / "templates" / "builder").glob("*.html"))
)
HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")


class TestTheDesignSystemRules:
    def test_the_files_exist(self):
        names = {path.name for path in BUILDER_FILES}
        assert {"tokens.css", "builder.css", "builder.js", "_base.html"} <= names

    @pytest.mark.parametrize("path", [p for p in BUILDER_FILES if p.name != "tokens.css"], ids=lambda p: p.name)
    def test_no_colour_values_outside_the_token_file(self, path):
        text = path.read_text(encoding="utf-8")
        assert HEX_COLOUR.findall(text) == []
        assert not re.search(r"\b(rgba?|hsla?)\(", text)

    @pytest.mark.parametrize("path", [p for p in BUILDER_FILES if p.suffix == ".css"], ids=lambda p: p.name)
    def test_nothing_is_loaded_from_outside(self, path):
        text = path.read_text(encoding="utf-8")
        assert "@import" not in text
        for url in re.findall(r"url\(([^)]*)\)", text):
            assert not re.match(r"['\"]?(https?:)?//", url), url

    def test_both_themes_define_the_same_colour_tokens(self):
        text = (WEB / "static" / "css" / "builder" / "tokens.css").read_text(encoding="utf-8")
        blocks = re.findall(r"(?:\[data-theme=\"(?:light|dark)\"\]|:root:not\(\[data-theme=\"light\"\]\))\s*\{(.*?)\}", text, re.S)
        assert len(blocks) == 3
        names = [set(re.findall(r"(--color-[a-z-]+|--shadow-[a-z]+):", block)) for block in blocks]
        assert names[0] == names[1] == names[2]
        assert len(names[0]) > 20

    def test_every_token_builder_css_uses_is_defined(self):
        tokens = (WEB / "static" / "css" / "builder" / "tokens.css").read_text(encoding="utf-8")
        defined = set(re.findall(r"(--[a-z0-9-]+):", tokens)) | {"--panel-width"}
        used = set(re.findall(r"var\((--[a-z0-9-]+)", (WEB / "static" / "css" / "builder" / "builder.css").read_text(encoding="utf-8")))
        assert used - defined == set()
