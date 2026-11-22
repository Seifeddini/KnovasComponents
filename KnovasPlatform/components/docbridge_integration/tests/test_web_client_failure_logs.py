"""RB-FIX-2: a traceback app.py logs for a failed Knovas call holds no query value, no name and no path id.

The verifier's finding: ``app.py``'s Cortex routes log ``exc_info`` when the client fails, and the error ``requests``
raises for no connection quotes the whole URL - ``.../graph/nodes/Anna%20Zwiebelmeier?q=Anna+Zwiebelmeier`` - so the
traceback carried it, and the route's own line printed the id from the path as well. The real app and the real
client, whose transport fails the way ``requests`` does (its message quoting the URL with a query); nothing reaches
a Core.
"""
from __future__ import annotations

import logging
from urllib.parse import quote, urlencode

import pytest
import requests
import tenacity

import conftest
from conftest import platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from knovas_client import KnovasAPIClient  # noqa: E402
from test_knovas_client_hardening import FakeSession  # noqa: E402

NAME = "Anna Zwiebelmeier"
SECRETS = ("Anna", "Zwiebelmeier", "q=Anna", "%20", "example.test")


def _no_connection(method, url, **kw):
    """What ``requests`` raises when the Core is not reachable: the path, and a query, in its message."""
    params = dict(kw.get("params") or {})
    params.setdefault("q", NAME)
    path = url.split("://", 1)[-1].split("/", 1)[-1]
    raise requests.exceptions.ConnectionError(
        f"HTTPConnectionPool(host='example.test', port=80): Max retries exceeded with url: /{path}?"
        f"{urlencode(params)} (Caused by NewConnectionError('Failed to establish a new connection'))")


class UnreachableCore(KnovasAPIClient):
    """The real client; only its transport fails."""

    def __init__(self, config_loader=None):
        super().__init__(config_loader)
        self._session = FakeSession(_no_connection)


@pytest.fixture
def app(platform_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")
    monkeypatch.setattr(KnovasAPIClient._make_request.retry, "wait", tenacity.wait_none())
    return conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=UnreachableCore)


@pytest.fixture
def member(app, identity_repo):
    person = conftest._person(identity_repo, "mia@kanzlei.ch", "Mia", "member")
    return conftest._signed_in(app, person.email)


def _logged(caplog) -> str:
    """Every line and traceback the app wrote, as a handler would format them."""
    formatter = logging.Formatter("%(levelname)s [%(name)s] %(message)s")
    return "\n".join(formatter.format(r) for r in caplog.records)


def test_the_traceback_of_a_failed_detail_read_holds_no_name_query_or_path_id(member, caplog):
    caplog.set_level(logging.DEBUG)
    response = member.get(f"/api/ontology/entities/{quote(NAME)}")
    assert response.status_code == 500
    assert NAME not in response.get_data(as_text=True)
    records = [r for r in caplog.records if r.getMessage().startswith("Ontology entity detail error")]
    assert len(records) == 1 and records[0].exc_info is not None          # the traceback is still logged
    text = _logged(caplog)
    assert "Traceback (most recent call last)" in text
    assert "Knovas API GET /secured/graph/nodes/{id}: no answer (ConnectionError)" in text
    for secret in SECRETS:
        assert secret not in text, secret


def test_the_traceback_of_a_failed_summary_holds_no_query_value(member, caplog):
    caplog.set_level(logging.DEBUG)
    response = member.get("/api/ontology/summary")
    assert response.status_code == 500
    text = _logged(caplog)
    assert "Ontology summary error" in text and "Traceback (most recent call last)" in text
    assert "no answer (ConnectionError)" in text
    for secret in SECRETS:
        assert secret not in text, secret
