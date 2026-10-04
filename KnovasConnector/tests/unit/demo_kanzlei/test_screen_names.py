from demo_kanzlei.screen_names import NameCollision, screen_names


class FakeClient:
    def __init__(self, hits: dict[str, list[str]]):
        self.hits = hits
        self.queries: list[str] = []

    def search_exact(self, name: str) -> list[str]:
        self.queries.append(name)
        return list(self.hits.get(name, []))


def test_screen_rejects_exact_active_collision():
    client = FakeClient({"Steinbach Partner AG": ["Steinbach Partner AG"]})
    try:
        screen_names(["Steinbach Partner AG", "Quarzfels Advokatur"], client=client)
        assert False, "expected NameCollision"
    except NameCollision as exc:
        assert "Steinbach Partner AG" in str(exc)


def test_screen_accepts_names_with_no_exact_hit():
    client = FakeClient({})
    screen_names(["Quarzfels Advokatur", "Nebelmeer Treuhand GmbH"], client=client)
    assert client.queries == ["Quarzfels Advokatur", "Nebelmeer Treuhand GmbH"]


def test_screen_is_case_insensitive_on_exact_match():
    client = FakeClient({"Quarzfels Advokatur": ["QUARZFELS ADVOKATUR"]})
    try:
        screen_names(["Quarzfels Advokatur"], client=client)
        assert False, "expected NameCollision"
    except NameCollision:
        pass
