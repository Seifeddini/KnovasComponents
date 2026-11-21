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


class _Response:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        import json

        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, response: _Response):
        self.response = response
        self.headers: dict = {}

    def post(self, *_args, **_kwargs):
        return self.response


def test_zefix_no_result_is_an_empty_answer_not_a_failed_request():
    from demo_kanzlei.screen_names import ZefixClient

    body = '{"error":{"code":"API.ZFR.SEARCH.NORESULT","title":"API.SEARCH.TITLE.NORESULT"}}'
    client = ZefixClient(session=_Session(_Response(404, body)))
    assert client.search_exact("Quarzfels Advokatur") == []
    screen_names(["Quarzfels Advokatur"], client=client)


def test_zefix_other_errors_still_fail():
    from demo_kanzlei.screen_names import ZefixClient

    client = ZefixClient(session=_Session(_Response(404, "<html>not found</html>")))
    try:
        client.search_exact("Quarzfels Advokatur")
        assert False, "expected the request error"
    except RuntimeError:
        pass


def test_zefix_hit_is_returned():
    from demo_kanzlei.screen_names import ZefixClient

    client = ZefixClient(session=_Session(_Response(200, '{"list":[{"name":"Steinbach Partner AG"}]}')))
    assert client.search_exact("Steinbach Partner AG") == ["Steinbach Partner AG"]
