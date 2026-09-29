"""Directories, card layouts and dismissed suggestions in the platform DB."""
import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")


@pytest.fixture
def store(platform_db):
    from identity.directories import DirectoryStore

    return DirectoryStore(platform_db)


class TestSlugify:
    def test_umlauts_are_spelled_out(self):
        from identity.directories import slugify

        assert slugify("Mandatsübersicht Zürich") == "mandatsuebersicht-zuerich"

    def test_punctuation_collapses_and_empty_falls_back(self):
        from identity.directories import slugify

        assert slugify("  Fristen & Termine!! ") == "fristen-termine"
        assert slugify("???") == "liste"


class TestViews:
    def test_a_new_directory_takes_the_next_position(self, store):
        store.create_view("t1", title="Mandate", columns=["a1", "a2"])
        second = store.create_view("t2", title="Personen")
        assert [v["slug"] for v in store.views()] == ["mandate", "personen"]
        assert second["position"] > store.view_for_type("t1")["position"]
        assert store.view_for_type("t1")["columns"] == ["a1", "a2"]

    def test_a_type_carries_at_most_one_directory(self, store):
        from identity.directories import ViewExistsError

        store.create_view("t1", title="Mandate")
        with pytest.raises(ViewExistsError):
            store.create_view("t1", title="Noch einmal Mandate")

    def test_an_address_belongs_to_one_page(self, store):
        from identity.directories import SlugTakenError

        store.create_view("t1", title="Mandate")
        with pytest.raises(SlugTakenError):
            store.create_view("t2", title="Andere", slug="mandate")

    def test_an_empty_title_is_refused(self, store):
        from identity.directories import DirectoryError

        with pytest.raises(DirectoryError):
            store.create_view("t1", title="   ")

    def test_update_keeps_the_slug_when_none_is_given(self, store):
        store.create_view("t1", title="Mandate")
        updated = store.update_view("t1", title="Alle Mandate", slug="", active=False,
                                    columns=["a3", "a3", ""])
        assert updated["slug"] == "mandate"
        assert updated["title"] == "Alle Mandate"
        assert updated["active"] is False
        assert updated["columns"] == ["a3"]
        assert store.active_views() == []

    def test_update_of_an_unknown_type_is_none(self, store):
        assert store.update_view("nope", title="X") is None

    def test_moving_swaps_two_neighbours_only(self, store):
        for type_id, title in (("t1", "A"), ("t2", "B"), ("t3", "C")):
            store.create_view(type_id, title=title)
        assert store.move_view("t3", -1) is True
        assert [v["title"] for v in store.views()] == ["A", "C", "B"]
        assert store.move_view("t1", -1) is False

    def test_removing_a_directory_keeps_the_card(self, store):
        store.create_view("t1", title="Mandate")
        store.save_card("t1", {"head": ["a1"], "rail": [], "sections": []})
        assert store.delete_view("t1") is True
        assert store.view_for_type("t1") is None
        assert store.card("t1")["head"] == ["a1"]


class TestCards:
    def test_saving_twice_replaces_the_layout(self, store, alice):
        store.save_card("t1", {"head": ["a1"]}, by=alice)
        store.save_card("t1", {"head": ["a2"]}, by=alice)
        assert store.card("t1") == {"head": ["a2"]}

    def test_a_type_without_a_layout_has_none(self, store):
        assert store.card("t9") is None


class TestDismissals:
    def test_a_dismissed_suggestion_is_remembered(self, store, alice):
        store.dismiss("enum:t1:a5:ruhend", by=alice)
        store.dismiss("enum:t1:a5:ruhend", by=alice)   # idempotent
        assert store.dismissed(["enum:t1:a5:ruhend", "leer:t1:a2"]) == {"enum:t1:a5:ruhend"}
