"""RBAC client methods.

Before this task `knovas_client.py` made zero calls to any of the four shipped
RBAC endpoints -- the engine existed in KnowledgeBase and was unreachable from
the product.
"""

from __future__ import annotations

import pytest


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _client(monkeypatch, payload, status_code=200):
    from knovas_client import KnovasAPIClient

    client = KnovasAPIClient.__new__(KnovasAPIClient)
    calls = []

    def _fake(self, method, endpoint, data=None, params=None):
        calls.append({"method": method, "endpoint": endpoint,
                      "data": data, "params": params})
        return _Resp(payload, status_code)

    monkeypatch.setattr(KnovasAPIClient, "_make_request", _fake)
    return client, calls


class TestAccessGroups:
    def test_access_groups_hits_the_collection_endpoint(self, monkeypatch):
        client, calls = _client(monkeypatch, {"groups": [{"group_id": "g1"}],
                                              "epoch": 3})
        groups = client.access_groups()
        assert calls[0]["endpoint"] == "/secured/access_groups"
        assert calls[0]["method"] == "GET"
        assert groups == [{"group_id": "g1"}]

    def test_create_access_group_posts_name_and_parent(self, monkeypatch):
        client, calls = _client(monkeypatch, {"group_id": "g2"}, 201)
        client.create_access_group("Litigation", parent="g1")
        assert calls[0]["method"] == "POST"
        assert calls[0]["data"] == {"name": "Litigation", "parent": "g1"}


class TestDocumentAccess:
    def test_read_passes_the_pointer_as_a_query_param(self, monkeypatch):
        client, calls = _client(
            monkeypatch,
            {"pointer": "rc-sync/a.docx", "access_groups": ["g1"], "acl_epoch": 2},
        )
        got = client.document_access("rc-sync/a.docx")
        assert calls[0]["params"] == {"pointer": "rc-sync/a.docx"}
        assert got["access_groups"] == ["g1"]

    def test_write_sends_the_complete_desired_set(self, monkeypatch):
        client, calls = _client(
            monkeypatch, {"pointer": "p", "access_groups": ["g1"], "acl_epoch": 3}
        )
        client.set_document_access("p", ["g1"])
        assert calls[0]["method"] == "PUT"
        assert calls[0]["data"]["access_groups"] == ["g1"]

    def test_acting_as_is_sent_separately_from_the_assignment(self, monkeypatch):
        """The endpoint's two group fields mean different things.

        `access_groups` is the assignment; `acting_as` is the caller's
        clearance. Conflating them would let a caller widen their own
        domination check.
        """
        client, calls = _client(monkeypatch, {"pointer": "p",
                                              "access_groups": [], "acl_epoch": 1})
        client.set_document_access("p", ["g-hr"], acting_as=["g-all"])
        assert calls[0]["data"]["access_groups"] == ["g-hr"]
        assert calls[0]["data"]["acting_as"] == ["g-all"]


class TestDocumentInventory:
    def test_documents_forwards_the_cursor_and_filters(self, monkeypatch):
        client, calls = _client(
            monkeypatch,
            {"documents": [], "next_after": None, "total_count": 0},
        )
        client.documents(after="rc-sync/m/a.docx", limit=250,
                         prefix="rc-sync/m/", unrestricted=True)
        params = calls[0]["params"]
        assert calls[0]["endpoint"] == "/secured/documents"
        assert params["after"] == "rc-sync/m/a.docx"
        assert params["limit"] == 250
        assert params["prefix"] == "rc-sync/m/"
        assert params["unrestricted"] == "true"

    def test_omitted_filters_are_not_sent_as_none(self, monkeypatch):
        client, calls = _client(
            monkeypatch, {"documents": [], "next_after": None, "total_count": 0}
        )
        client.documents()
        assert "prefix" not in calls[0]["params"]
        assert "group" not in calls[0]["params"]

    def test_iter_documents_follows_the_cursor_to_the_end(self, monkeypatch):
        from knovas_client import KnovasAPIClient

        client = KnovasAPIClient.__new__(KnovasAPIClient)
        pages = [
            {"documents": [{"pointer": "a"}, {"pointer": "b"}],
             "next_after": "b", "total_count": 3},
            {"documents": [{"pointer": "c"}], "next_after": None, "total_count": 3},
        ]
        seen_after = []

        def _fake(self, method, endpoint, data=None, params=None):
            seen_after.append((params or {}).get("after"))
            return _Resp(pages.pop(0))

        monkeypatch.setattr(KnovasAPIClient, "_make_request", _fake)
        got = [d["pointer"] for d in client.iter_documents()]
        assert got == ["a", "b", "c"]
        assert seen_after == [None, "b"]

    def test_iter_documents_stops_on_a_repeated_cursor(self, monkeypatch):
        """A server that returns the same cursor must not spin us forever."""
        from knovas_client import KnovasAPIClient

        client = KnovasAPIClient.__new__(KnovasAPIClient)

        def _fake(self, method, endpoint, data=None, params=None):
            return _Resp({"documents": [{"pointer": "a"}],
                          "next_after": "a", "total_count": 1})

        monkeypatch.setattr(KnovasAPIClient, "_make_request", _fake)
        got = list(client.iter_documents(max_pages=5))
        assert len(got) <= 5


class TestFolderRules:
    def test_create_sends_prefix_and_groups(self, monkeypatch):
        client, calls = _client(monkeypatch, {"rule_id": "r1"}, 201)
        client.create_folder_rule("rc-sync/matters/A/", ["g-lit"])
        assert calls[0]["endpoint"] == "/secured/folder_rules"
        assert calls[0]["data"]["pointer_prefix"] == "rc-sync/matters/A/"
        assert calls[0]["data"]["access_groups"] == ["g-lit"]

    def test_update_targets_the_rule_id(self, monkeypatch):
        client, calls = _client(monkeypatch, {"rule_id": "r1", "version": 2})
        client.update_folder_rule("r1", [])
        assert calls[0]["method"] == "PATCH"
        assert calls[0]["endpoint"] == "/secured/folder_rules/r1"
