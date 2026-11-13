import copy
import json

import pytest

from custom_views.manifest import Manifest, ManifestError, parse_manifest

VALID = {
    "format": 1,
    "title": {"de": "Übersicht", "en": "Overview"},
    "description": {"de": "Beschreibung", "en": "Description"},
    "order": 2.5,
    "roles": ["admin"],
    "entry": "index.html",
    "scripts": ["app.js"],
    "styles": ["app.css"],
    "reads": {"project": ["status", "owner"]},
    "links": {"hosts": ["example.atlassian.net"]},
}


def raw(**changes):
    data = copy.deepcopy(VALID)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return json.dumps(data).encode("utf-8")


def test_valid_manifest_round_trips():
    m = parse_manifest("my-view", raw())
    assert isinstance(m, Manifest)
    assert m.id == "my-view"
    assert m.title["de"] == "Übersicht"
    assert m.description["en"] == "Description"
    assert m.order == 2.5
    assert m.roles == ("admin",)
    assert m.entry == "index.html"
    assert m.scripts == ("app.js",)
    assert m.styles == ("app.css",)
    assert m.reads == {"project": ("status", "owner")}
    assert m.link_hosts == ("example.atlassian.net",)
    with pytest.raises(TypeError):
        m.title["de"] = "x"


def test_optional_parts_may_be_absent():
    m = parse_manifest("v", raw(description=None, order=None, scripts=None, styles=None, links=None))
    assert m.description is None and m.order is None
    assert m.scripts == () and m.styles == () and m.link_hosts == ()


@pytest.mark.parametrize(
    "data,field",
    [
        (raw(roles=None), "roles"),
        (raw(reads=None), "reads"),
        (raw(reads={}), "reads"),
        (raw(bogus=1), "bogus"),
        (raw(entry="../x.html"), "entry"),
        (raw(scripts=["a.js.map"]), "scripts"),
        (raw(links={"hosts": ["https://example.com"]}), "links"),
        (raw(reads={"a.b": ["x"]}), "reads"),
        (raw(reads={"a": ["x.y"]}), "reads"),
    ],
)
def test_invalid_manifest_is_refused_naming_the_field(data, field):
    with pytest.raises(ManifestError) as exc:
        parse_manifest("v", data)
    assert field in str(exc.value)


def test_error_never_echoes_content():
    with pytest.raises(ManifestError) as exc:
        parse_manifest("v", raw(entry="SECRET/../x.html"))
    assert "SECRET" not in str(exc.value)


def test_invalid_json_refused():
    with pytest.raises(ManifestError):
        parse_manifest("v", b"{not json")


def test_non_utf8_refused():
    with pytest.raises(ManifestError):
        parse_manifest("v", b"\xff\xfe{}")


def test_too_large_refused():
    big = json.dumps({"x": "a" * 70_000}).encode()
    with pytest.raises(ManifestError):
        parse_manifest("v", big)


def test_allows():
    def m(roles):
        return parse_manifest("v", raw(roles=roles))

    assert m(["*"]).allows(frozenset()) is True
    assert m([]).allows(frozenset({"admin"})) is False
    assert m(["admin"]).allows(frozenset({"member"})) is False
    assert m(["admin"]).allows(frozenset({"admin", "member"})) is True
