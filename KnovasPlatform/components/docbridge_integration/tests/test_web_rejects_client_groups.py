"""A browser that names its own access groups is refused, not corrected.

Silently dropping the field would let a caller believe a scope applied when
it never did, and would make a future merging bug invisible: the field would
sit there unused until someone "fixed" it by honouring it.

Plan: docs/superpowers/plans/2026-09-02-auth-assertion-end-to-end.md, Task 5.
The hook runs before the login gate, so it does not need a signed-in user and
these tests run without PostgreSQL.
"""

from __future__ import annotations

import pytest

flask = pytest.importorskip("flask")


@pytest.fixture
def client(docbridge_app):
    return docbridge_app.test_client()


def test_body_supplied_access_groups_are_rejected(client):
    r = client.post("/api/search", json={"query": "x", "access_groups": ["litigation"]})
    assert r.status_code == 400
    assert "access_groups" in r.get_json()["error"]


def test_empty_access_groups_list_is_also_rejected(client):
    """Empty is not "none". An explicit empty list would read as
    'deliberately unrestricted' if it were ever honoured."""
    r = client.post("/api/search", json={"query": "x", "access_groups": []})
    assert r.status_code == 400


def test_the_field_is_refused_on_every_json_route_not_just_search(client):
    r = client.post("/api/anything-at-all", json={"access_groups": ["g"]})
    assert r.status_code == 400


def test_a_normal_request_is_unaffected(client):
    """Whatever the login gate does with an anonymous caller, it is not 400."""
    r = client.post("/api/search", json={"query": "x"})
    assert r.status_code != 400


def test_non_json_bodies_are_left_to_the_route(client):
    r = client.post("/api/search", data={"access_groups": "g"})
    assert r.status_code != 400
