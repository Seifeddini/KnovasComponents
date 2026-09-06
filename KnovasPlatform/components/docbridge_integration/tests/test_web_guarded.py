"""run_guarded: queue it, or do it and say that you did it alone."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from web_interface.guarded import GuardOutcome, run_guarded


class _Service:
    def __init__(self, requires: bool):
        self._requires = requires
        self.requests: list[tuple] = []
        self.bypasses: list[tuple] = []

    def requires_approval(self, kind, actor):
        return self._requires

    def request(self, actor, *, kind, target_ref, payload, ttl=None):
        self.requests.append((kind, target_ref, dict(payload)))
        return SimpleNamespace(id="r-1", kind=kind, target_ref=target_ref)

    def record_bypass(self, actor, *, kind, target_ref, detail=None):
        self.bypasses.append((kind, target_ref, dict(detail or {})))


ACTOR = SimpleNamespace(id="u-1", roles=frozenset({"admin"}))


def test_when_approval_is_required_the_action_is_queued_and_not_run():
    service = _Service(requires=True)
    ran = []
    outcome = run_guarded(service, ACTOR, kind="acl_change", target_ref="doc-1",
                          payload={"pointers": ["doc-1"]},
                          execute=lambda: ran.append(1) or {"changed": 1})
    assert outcome.queued is True
    assert outcome.request.id == "r-1"
    assert ran == [], "a queued action must not execute"
    assert service.requests == [("acl_change", "doc-1", {"pointers": ["doc-1"]})]
    assert service.bypasses == []


def test_when_no_approval_is_required_it_runs_once_and_the_bypass_is_recorded():
    service = _Service(requires=False)
    calls = []
    outcome = run_guarded(service, ACTOR, kind="acl_change", target_ref="doc-1",
                          payload={}, execute=lambda: calls.append(1) or {"changed": 1})
    assert outcome == GuardOutcome(queued=False, result={"changed": 1})
    assert calls == [1]
    assert service.bypasses == [("acl_change", "doc-1", {"result": {"changed": 1}})]
    assert service.requests == []


def test_execute_returning_none_still_records_a_bypass():
    service = _Service(requires=False)
    outcome = run_guarded(service, ACTOR, kind="acl_change", target_ref="x",
                          payload={}, execute=lambda: None)
    assert outcome.result == {}
    assert service.bypasses[0][2] == {"result": {}}


def test_an_unguarded_kind_is_refused_before_touching_the_service():
    service = _Service(requires=False)
    with pytest.raises(ValueError):
        run_guarded(service, ACTOR, kind="rename_folder", target_ref="x",
                    payload={}, execute=lambda: {"ok": True})
    assert service.bypasses == [] and service.requests == []


def test_an_exception_in_execute_records_no_bypass():
    """No action happened, so there is nothing to record as done alone."""
    service = _Service(requires=False)

    def boom():
        raise RuntimeError("backend down")

    with pytest.raises(RuntimeError):
        run_guarded(service, ACTOR, kind="acl_change", target_ref="x",
                    payload={}, execute=boom)
    assert service.bypasses == []
