import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


class TestWorkbenchPage:
    def test_the_page_requires_a_session(self, anon_client):
        assert anon_client.get("/workbench").status_code in (302, 401)

    def test_the_page_renders_the_three_panes(self, member_client):
        html = member_client.get("/workbench").get_data(as_text=True)
        assert 'id="nodeList"' in html
        assert 'id="neighbourhoodGraph"' in html
        assert 'id="fieldReader"' in html

    def test_the_page_carries_a_csrf_token(self, member_client):
        html = member_client.get("/workbench").get_data(as_text=True)
        assert 'name="csrf-token"' in html

    def test_fixture_mode_says_so_instead_of_rendering_panes(self, fixture_mode_client):
        html = fixture_mode_client.get("/workbench").get_data(as_text=True)
        assert "Wissensnetz-Modus erforderlich" in html
