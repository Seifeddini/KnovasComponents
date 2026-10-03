"""doc_fields_capability: learning what the tenant supports, and per-user caches.

Spec 2.2: the probe classification, the TTLs, the four signals and the
needs_calibration hold that a probe must not lift; D1/D13: nothing turns the
feature on, and legacy mode is off without a request. Registry, entity
names and node names (F3) are cached per user and never fetched with ``q``
(D10). A registry read Knovas does not answer is remembered per user, and
the last registry is used meanwhile.
"""

from __future__ import annotations

import threading
import time

import pytest

import doc_fields_capability as cap
from doc_fields_capability import Capability, CapabilityCache
from doc_fields_fakes import FakeDocFieldsApi
from knovas_client import DocFieldsError, DocFieldsUnavailable, QueryRejected
from test_knovas_client_hardening import StubConfig, make_client


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class ProbeClient:
    """Answers the probe from a script; counts the probes."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.probes = 0

    def doc_fields_probe(self):
        self.probes += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def clock():
    return Clock()


def _cache(clock, **kw):
    kw.setdefault("ttl", 300)
    kw.setdefault("unknown_ttl", 30)
    kw.setdefault("calibration_recheck", cap.LISTING_ONLY_HOLD_SECONDS)
    return CapabilityCache(clock=clock, **kw)


class TestClassify:
    @pytest.mark.parametrize("answer, capability", [
        ("off", Capability.off), ("values", Capability.values),
        ("filters", Capability.filters), ("unknown", Capability.unknown),
        ("listing_only", Capability.unknown), (None, Capability.unknown), ("", Capability.unknown),
    ])
    def test_probe_answers(self, answer, capability):
        assert cap.classify_probe(answer) is capability

    def test_what_each_capability_shows(self):
        assert [c.value for c in Capability if c.shows_values] == [
            "values", "listing_only", "filters"]
        assert [c.value for c in Capability if c.shows_listing] == ["listing_only", "filters"]
        assert [c.value for c in Capability if c.shows_filters] == ["filters"]
        assert [c.value for c in Capability if c.sends_return_fields] == [
            "listing_only", "filters"]

    def test_capability_serialises_as_its_name(self):
        import json

        assert json.dumps({"capability": Capability.listing_only}) == \
            '{"capability": "listing_only"}'


class TestCacheTtl:
    def test_cached_for_the_ttl_then_probed_again(self, clock):
        client, cache = ProbeClient("filters"), _cache(clock)
        assert cache.get(client) is Capability.filters
        clock.advance(299)
        assert cache.get(client) is Capability.filters
        assert client.probes == 1
        clock.advance(2)
        cache.get(client)
        assert client.probes == 2

    def test_unknown_is_cached_for_30_seconds_only(self, clock):
        client, cache = ProbeClient("unknown", "values"), _cache(clock)
        assert cache.get(client) is Capability.unknown
        clock.advance(29)
        assert cache.get(client) is Capability.unknown and client.probes == 1
        clock.advance(2)
        assert cache.get(client) is Capability.values and client.probes == 2

    def test_a_probe_that_raises_is_unknown(self, clock):
        client, cache = ProbeClient(RuntimeError("boom"), "off"), _cache(clock)
        assert cache.get(client) is Capability.unknown
        assert cache.get(client) is Capability.unknown and client.probes == 1
        clock.advance(31)
        assert cache.get(client) is Capability.off

    def test_nobody_signed_in_caches_nothing(self, clock):
        """A broker refusing to send is not the tenant's answer: the next
        signed-in request must probe instead of inheriting 'unknown'."""
        client, cache = ProbeClient(PermissionError("no user"), "filters"), _cache(clock)
        assert cache.get(client) is Capability.unknown
        assert cache.peek() is None
        assert cache.get(client) is Capability.filters and client.probes == 2

    def test_peek_never_probes(self, clock):
        client, cache = ProbeClient("values"), _cache(clock)
        assert cache.peek() is None
        cache.get(client)
        assert cache.peek() is Capability.values and client.probes == 1


class TestSignals:
    def test_where_unsupported_means_values(self, clock):
        client, cache = ProbeClient("filters"), _cache(clock)
        cache.get(client)
        cache.observe("where_unsupported")
        assert cache.get(client) is Capability.values and client.probes == 1

    def test_feature_off_means_off(self, clock):
        client, cache = ProbeClient("values"), _cache(clock)
        cache.get(client)
        cache.observe("feature_off")
        assert cache.get(client) is Capability.off and client.probes == 1

    def test_echo_missing_makes_the_next_call_probe(self, clock):
        client, cache = ProbeClient("filters"), _cache(clock)
        cache.get(client)
        cache.observe("echo_missing")
        assert cache.peek() is None
        assert cache.get(client) is Capability.filters and client.probes == 2

    def test_needs_calibration_is_held_past_a_probe(self, clock):
        """The probe cannot see calibration (find never checks it); if it
        could lift the hold, every TTL would offer the filter rail again and
        the next filtered search would meet the same 503."""
        client, cache = ProbeClient("filters"), _cache(clock, ttl=60)
        cache.get(client)
        cache.observe("needs_calibration")
        for _ in range(4):
            clock.advance(70)  # past the 60 s capability TTL, inside the hold
            assert cache.get(client) is Capability.listing_only
        assert client.probes == 1
        clock.advance(21)  # 301 s after the signal
        assert cache.get(client) is Capability.filters and client.probes == 2

    def test_the_hold_lasts_five_minutes(self, clock):
        """F6: Knovas 1.5.0 calls 503 where_requires_calibration "a problem on
        the Knovas side. Try again later." -- five minutes, not an hour."""
        assert cap.LISTING_ONLY_HOLD_SECONDS == 300
        client, cache = ProbeClient("filters"), CapabilityCache(clock=clock)
        cache.get(client)
        cache.observe("needs_calibration")
        clock.advance(299)
        assert cache.get(client) is Capability.listing_only and client.probes == 1
        clock.advance(2)
        assert cache.get(client) is Capability.filters and client.probes == 2

    def test_a_hold_observed_while_a_probe_is_out_wins(self, clock):
        cache = _cache(clock)

        class Racing:
            def doc_fields_probe(self):
                cache.observe("needs_calibration")
                return "filters"

        assert cache.get(Racing()) is Capability.listing_only

    def test_feature_off_and_where_unsupported_lift_the_hold(self, clock):
        client, cache = ProbeClient("filters"), _cache(clock)
        cache.observe("needs_calibration")
        cache.observe("feature_off")
        assert cache.get(client) is Capability.off
        cache.observe("needs_calibration")
        cache.observe("where_unsupported")
        assert cache.get(client) is Capability.values
        assert client.probes == 0

    def test_an_unknown_signal_is_a_programming_error(self, clock):
        with pytest.raises(ValueError):
            _cache(clock).observe("filters_please")

    @pytest.mark.parametrize("exc, signal", [
        (DocFieldsUnavailable(), "feature_off"),
        (DocFieldsError(400, "where_unsupported", "x"), "where_unsupported"),
        (QueryRejected(400, "where_unsupported"), "where_unsupported"),
        (QueryRejected(503, "where_requires_calibration"), "needs_calibration"),
        (QueryRejected(400, "unknown_field"), None),
        (DocFieldsError(409, "version_conflict", "x"), None),
        (RuntimeError("x"), None),
    ])
    def test_signal_for(self, exc, signal):
        assert cap.signal_for(exc) == signal

    def test_observe_exception_reaches_the_shared_cache(self):
        client = FakeDocFieldsApi("filters")
        assert cap.capability_for(client) is Capability.filters
        assert cap.observe_exception(QueryRejected(503, "where_requires_calibration")) \
            == "needs_calibration"
        assert cap.capability_for(client) is Capability.listing_only
        assert cap.observe_exception(RuntimeError()) is None


class TestCapabilityFor:
    def test_a_client_outside_secured_mode_is_off_without_a_request(self):
        client = make_client(use_secured_api=True)  # no mTLS paths configured

        def refuse(*a, **kw):
            raise AssertionError("no request may be sent")

        client._session.request = refuse
        assert cap.capability_for(client) is Capability.off

    def test_the_dummy_client_defaults_to_off(self):
        from conftest import DummyKnovasClient

        assert cap.capability_for(DummyKnovasClient(StubConfig({}))) is Capability.off

    @pytest.mark.parametrize("ui", ["off", "OFF", "false", "0", "no"])
    def test_ui_off_means_off_without_a_probe(self, ui):
        client = FakeDocFieldsApi(StubConfig({"web.doc_fields.ui": ui}), "filters")
        assert cap.capability_for(client) is Capability.off
        assert client.probe_calls == 0

    def test_ui_can_only_turn_it_off(self):
        """D1: no setting makes a server-off tenant look on."""
        client = FakeDocFieldsApi(StubConfig({"web.doc_fields.ui": "on"}), "off")
        assert cap.capability_for(client) is Capability.off
        assert client.probe_calls == 1

    def test_secured_clients_share_one_process_cache(self):
        first = FakeDocFieldsApi("values")
        assert cap.capability_for(first) is Capability.values
        second = FakeDocFieldsApi("filters")
        assert cap.capability_for(second) is Capability.values  # one tenant, one answer
        assert second.probe_calls == 0
        cap.reset_for_tests()
        assert cap.capability_for(second) is Capability.filters

    def test_the_shared_cache_takes_its_ttls_from_config(self):
        config = StubConfig({"web.doc_fields.capability_ttl_seconds": 7,
                             "web.doc_fields.calibration_recheck_seconds": 11})
        shared = cap.shared_cache(config)
        assert shared._ttl == 7 and shared._recheck == 11
        assert cap.shared_cache() is shared

    def test_a_cache_created_before_any_config_adopts_the_first_one(self):
        cap.observe("feature_off")  # e.g. a route observes before anyone probed
        shared = cap.shared_cache(StubConfig({"web.doc_fields.capability_ttl_seconds": 9}))
        assert shared._ttl == 9
        cap.shared_cache(StubConfig({"web.doc_fields.capability_ttl_seconds": 99}))
        assert shared._ttl == 9  # the first config wins, as documented

    def test_is_secured_reads_the_real_client(self):
        assert cap.is_secured(make_client()) is False
        from test_knovas_client_hardening import make_secured_client

        assert cap.is_secured(make_secured_client()) is True


class TestSettings:
    def test_defaults(self):
        s = cap.settings(StubConfig({}))
        assert s.ui_enabled is True
        assert (s.capability_ttl, s.unknown_ttl, s.calibration_recheck,
                s.registry_cache_seconds, s.find_page_size) == (300, 30, 300, 300, 50)
        assert s.edit_roles == frozenset({"admin"})
        assert cap.settings(None).edit_roles == frozenset({"admin"})

    def test_the_shipped_config_holds_listing_only_for_five_minutes(self):
        """config/config.yaml sets the hold explicitly; it must say five
        minutes too, or the code default never applies."""
        import pathlib

        from config_loader import ConfigLoader

        path = pathlib.Path(__file__).resolve().parents[1] / "config" / "config.yaml"
        assert cap.settings(ConfigLoader(str(path))).calibration_recheck == 300

    def test_edit_roles(self):
        assert cap.parse_edit_roles("admin, Member ,") == frozenset({"admin", "member"})
        assert cap.parse_edit_roles("superuser") == frozenset({"admin"})
        assert cap.parse_edit_roles("") == frozenset({"admin"})
        assert cap.parse_edit_roles(["approver"]) == frozenset({"approver"})

    def test_bounds(self):
        s = cap.settings(StubConfig({"web.doc_fields.find_page_size": 5000,
                                     "web.doc_fields.capability_ttl_seconds": "x"}))
        assert s.find_page_size == 200 and s.capability_ttl == 300


class TestRegistryFor:
    def test_cached_per_user(self, monkeypatch):
        client = FakeDocFieldsApi("values")
        alice = cap.registry_for(client, "alice")
        cap.registry_for(client, "alice")
        assert [c for c, _ in client.doc_calls].count("doc_fields") == 1
        cap.registry_for(client, "bob")
        assert [c for c, _ in client.doc_calls].count("doc_fields") == 2
        assert {s["key"] for s in alice} >= {"doc_type", "mandant", "patient"}

    def test_the_ttl_expires(self, monkeypatch):
        now = [0.0]
        monkeypatch.setattr(cap, "_now", lambda: now[0])
        client = FakeDocFieldsApi(StubConfig({"web.doc_fields.registry_cache_seconds": 10}),
                                  "values")
        cap.registry_for(client, "alice")
        now[0] = 9.9
        cap.registry_for(client, "alice")
        now[0] = 10.1
        cap.registry_for(client, "alice")
        assert [c for c, _ in client.doc_calls].count("doc_fields") == 2

    def test_invalidate_one_user_or_everyone(self):
        client = FakeDocFieldsApi("values")
        for who in ("alice", "bob"):
            cap.registry_for(client, who)
        cap.invalidate("alice")
        cap.registry_for(client, "alice")
        cap.registry_for(client, "bob")
        assert [c for c, _ in client.doc_calls].count("doc_fields") == 3
        cap.invalidate()
        cap.registry_for(client, "bob")
        assert [c for c, _ in client.doc_calls].count("doc_fields") == 4

    def test_a_refusal_is_raised_and_not_cached(self):
        """An answer is raised and the next call asks again; a read Knovas
        does not answer is remembered instead (TestRegistryOutage)."""
        client = FakeDocFieldsApi("values")
        client.fail_call("doc_fields", 403, "assertion_rejected")
        with pytest.raises(DocFieldsError):
            cap.registry_for(client, "alice")
        assert cap.registry_for(client, "alice")
        with pytest.raises(DocFieldsUnavailable):
            cap.registry_for(FakeDocFieldsApi("off"), "carol")

    def test_callers_cannot_change_the_cache(self):
        client = FakeDocFieldsApi("values")
        first = cap.registry_for(client, "alice")
        first[0]["label"] = "changed"
        first.clear()
        assert cap.registry_for(client, "alice")[0]["label"] != "changed"


class TestRegistryTargetsFor:
    def test_target_ids_come_from_the_cached_entry(self):
        client = FakeDocFieldsApi("values")
        targets = cap.registry_targets_for(client, "alice")
        cap.registry_for(client, "alice")
        assert [c for c, _ in client.doc_calls].count("doc_fields") == 1
        assert targets.get("mandant")
        # The sanitized registry carries no target id at all.
        assert all("target_node_type_id" not in spec for spec in cap.registry_for(client, "alice"))

    def test_per_user_and_a_copy(self):
        client = FakeDocFieldsApi("values")
        mine = cap.registry_targets_for(client, "alice")
        mine.clear()
        assert cap.registry_targets_for(client, "alice")
        cap.registry_targets_for(client, "bob")
        assert [c for c, _ in client.doc_calls].count("doc_fields") == 2

    def test_a_refusal_is_raised_and_not_cached(self):
        client = FakeDocFieldsApi("values")
        client.fail_call("doc_fields", 403, "assertion_rejected")
        with pytest.raises(DocFieldsError):
            cap.registry_targets_for(client, "alice")
        assert cap.registry_targets_for(client, "alice")


def _reads(client):
    """How often the fake was asked for the registry."""
    return [c for c, _ in client.doc_calls].count("doc_fields")


def _unanswered(message="ReadTimeout"):
    """What KnovasAPIClient.doc_fields raises when Knovas does not answer
    at all (a timeout or no connection), after the client's retries."""
    return DocFieldsError(503, "transport_error", message)


class TestRegistryOutage:
    """GET /secured/graph/doc-fields hangs or fails while search works.
    Each read costs the client's whole retry cycle (three tries, each with
    the 120 s read timeout), so a read Knovas does not answer -- no
    connection, a timeout, a 5xx, a 429 -- is remembered per person for
    unknown_ttl (30 s): until then their registry is not read again, the
    last one read is used (on the failing call too), and without one the
    error is raised as before. One read per person at a time. An answer --
    the feature off, a refusal -- is raised and not remembered, as before."""

    @pytest.fixture
    def now(self, monkeypatch):
        now = [0.0]
        monkeypatch.setattr(cap, "_now", lambda: now[0])
        return now

    @pytest.fixture
    def client(self):
        return FakeDocFieldsApi(StubConfig({"web.doc_fields.registry_cache_seconds": 10}),
                                "values")

    def test_a_failed_read_is_remembered_for_unknown_ttl(self, now, client):
        client.fail_call("doc_fields", _unanswered())
        with pytest.raises(DocFieldsError):
            cap.registry_for(client, "alice")
        now[0] = 29.0
        for read in (cap.registry_for, cap.registry_targets_for):
            with pytest.raises(DocFieldsError) as again:
                read(client, "alice")
            assert (again.value.status, again.value.error_code) == (503, "transport_error")
        assert _reads(client) == 1, "no Knovas call while the failure is remembered"
        assert cap.registry_for(client, "bob") and _reads(client) == 2, "per person"

    def test_after_unknown_ttl_a_new_read_happens(self, now, client):
        client.fail_call("doc_fields", _unanswered())
        with pytest.raises(DocFieldsError):
            cap.registry_for(client, "alice")
        now[0] = 29.9
        with pytest.raises(DocFieldsError):
            cap.registry_for(client, "alice")
        assert _reads(client) == 1
        now[0] = 30.1
        assert {s["key"] for s in cap.registry_for(client, "alice")} >= {"doc_type", "mandant"}
        assert _reads(client) == 2
        now[0] = 39.0
        cap.registry_for(client, "alice")
        assert _reads(client) == 2, "the new answer is cached as before"

    def test_the_last_good_registry_is_served_while_knovas_fails(self, now, client):
        good = cap.registry_for(client, "alice")
        targets = cap.registry_targets_for(client, "alice")
        now[0] = 11.0  # expired
        client.fail_call("doc_fields", _unanswered())
        assert cap.registry_for(client, "alice") == good, "on the failing call itself"
        now[0] = 40.0  # 29 s after the failure
        assert cap.registry_for(client, "alice") == good
        assert cap.registry_targets_for(client, "alice") == targets
        assert cap.entity_names_for(client, "alice", "mandant") == ["Beispiel GmbH", "Muster AG"]
        served = cap.registry_for(client, "alice")
        served[0]["label"] = "changed"
        assert cap.registry_for(client, "alice") == good, "callers still get copies"
        assert _reads(client) == 2, "Knovas is not asked while the failure is remembered"
        now[0] = 41.5
        cap.registry_for(client, "alice")
        assert _reads(client) == 3

    def test_a_feature_off_answer_is_not_remembered(self, now, client):
        """DocFieldsUnavailable is Knovas's answer, not an outage: it is
        raised (the capability cache turns the feature off from it), never
        hidden behind the last registry, and the next call asks again --
        unlike a read Knovas did not answer."""
        good = cap.registry_for(client, "alice")
        now[0] = 11.0
        client.fail_call("doc_fields", DocFieldsUnavailable())
        with pytest.raises(DocFieldsUnavailable):
            cap.registry_for(client, "alice")
        assert cap.registry_for(client, "alice") == good
        assert _reads(client) == 3, "asked again at once"
        now[0] = 22.0
        client.fail_call("doc_fields", _unanswered())
        assert cap.registry_for(client, "alice") == good
        assert cap.registry_for(client, "alice") == good
        assert _reads(client) == 4, "the unanswered read is remembered"

    @pytest.mark.parametrize("failure", [
        DocFieldsError(503, "doc_fields_unavailable", "x"),
        DocFieldsError(500, None, "x"),
        DocFieldsError(502, "invalid_response", "x"),
        DocFieldsError(504, None, "x"),
        DocFieldsError(429, "too_many_requests", "x"),
    ], ids=lambda e: f"{e.status}-{e.error_code}")
    def test_5xx_and_429_count_as_no_answer(self, now, client, failure):
        good = cap.registry_for(client, "alice")
        now[0] = 11.0
        client.fail_call("doc_fields", failure)
        assert cap.registry_for(client, "alice") == good
        assert cap.registry_for(client, "alice") == good
        assert _reads(client) == 2

    @pytest.mark.parametrize("failure", [
        DocFieldsError(403, "assertion_rejected", "x"),
        DocFieldsError(401, None, "x"),
        DocFieldsError(400, "invalid_value", "x"),
        PermissionError("nobody signed in, nothing sent"),
    ], ids=lambda e: str(getattr(e, "status", type(e).__name__)))
    def test_a_refusal_is_raised_and_not_remembered(self, now, client, failure):
        """A refusal says the person may not read the registry (now): the
        last one is not served instead, and the next call asks again."""
        cap.registry_for(client, "alice")
        now[0] = 11.0
        client.fail_call("doc_fields", failure)
        with pytest.raises(type(failure)):
            cap.registry_for(client, "alice")
        cap.registry_for(client, "alice")
        assert _reads(client) == 3

    def test_invalidate_forgets_a_remembered_failure(self, now, client):
        client.fail_call("doc_fields", _unanswered())
        with pytest.raises(DocFieldsError):
            cap.registry_for(client, "alice")
        cap.invalidate("alice")
        assert cap.registry_for(client, "alice") and _reads(client) == 2

    def test_a_read_out_during_invalidate_does_not_refill_the_cache(self, now, client):
        """invalidate() after a registry write: a read that was already out
        may carry the registry from before the write. It answers its own
        caller but is not kept; the next call reads again."""
        read = client.doc_fields

        def racing():
            raw = read()
            cap.invalidate("alice")
            return raw

        client.doc_fields = racing
        assert cap.registry_for(client, "alice")
        client.doc_fields = read
        cap.registry_for(client, "alice")
        assert _reads(client) == 2

    @staticmethod
    def _held(client, outcome=None):
        """``client.doc_fields`` held until released, then answering (or
        raising ``outcome``); returns (entered, release, calls)."""
        entered, release, calls = threading.Event(), threading.Event(), []
        read = client.doc_fields

        def held():
            calls.append(1)
            entered.set()
            release.wait(5)
            if outcome is not None:
                raise outcome
            return read()

        client.doc_fields = held
        return entered, release, calls

    def test_while_a_read_is_out_the_others_get_the_last_registry(self, now, client):
        """A hung Knovas holds the one read for minutes; the person's other
        requests meanwhile get the last registry at once, without reads of
        their own."""
        good = cap.registry_for(client, "alice")
        now[0] = 11.0
        entered, release, calls = self._held(client)
        out = []
        reader = threading.Thread(target=lambda: out.append(cap.registry_for(client, "alice")))
        reader.start()
        try:
            assert entered.wait(5)
            assert cap.registry_for(client, "alice") == good
            assert cap.registry_targets_for(client, "alice")
            assert len(calls) == 1, "no second read while one is out"
        finally:
            release.set()
            reader.join(5)
        assert out == [good] and len(calls) == 1

    @pytest.mark.parametrize("outcome", [None, _unanswered()], ids=["answered", "unanswered"])
    def test_without_a_last_registry_the_others_wait_for_the_one_read(self, now, client,
                                                                      outcome):
        entered, release, calls = self._held(client, outcome)
        results = []

        def ask():
            try:
                results.append(len(cap.registry_for(client, "alice")))
            except DocFieldsError as exc:
                results.append(exc.error_code)

        threads = [threading.Thread(target=ask) for _ in range(3)]
        threads[0].start()
        try:
            assert entered.wait(5)
            for thread in threads[1:]:
                thread.start()
            time.sleep(0.2)  # the others reach the read that is out
        finally:
            release.set()
            for thread in threads:
                thread.join(5)
        assert len(calls) == 1
        assert len(results) == 3 and len(set(results)) == 1
        assert results[0] == ("transport_error" if outcome else len(client.registry))

    def test_no_value_in_a_log_line(self, now, client, caplog):
        import logging

        sentinel = "Sentinel-Feld-AG"
        client.registry[0]["labels"]["de"] = sentinel
        good = cap.registry_for(client, "alice")
        assert any(spec["label"] == sentinel for spec in good)
        now[0] = 11.0
        with caplog.at_level(logging.DEBUG):
            client.fail_call("doc_fields", _unanswered(sentinel))
            assert cap.registry_for(client, "alice") == good
            cap.invalidate()
            client.fail_call("doc_fields", DocFieldsError(502, "invalid_response", sentinel))
            with pytest.raises(DocFieldsError):
                cap.registry_for(client, "alice")
        assert caplog.records and sentinel not in caplog.text


class TestEntityNamesFor:
    def test_names_of_the_target_type_without_q(self):
        client = FakeDocFieldsApi("values")
        names = cap.entity_names_for(client, "alice", "mandant")
        assert names == ["Beispiel GmbH", "Muster AG"]
        assert client.graph_nodes_calls == [{"node_type_id": "t-mandant", "q": None}]

    def test_cached_per_user(self):
        client = FakeDocFieldsApi("values")
        cap.entity_names_for(client, "alice", "mandant")
        cap.entity_names_for(client, "alice", {"key": "mandant"})
        assert len(client.graph_nodes_calls) == 1
        cap.entity_names_for(client, "bob", "mandant")
        assert len(client.graph_nodes_calls) == 2

    @pytest.mark.parametrize("key", ["patient", "party", "doc_type", "nope", "", None])
    def test_free_text_only(self, key):
        """special (patient), no target (party), not an entity, unknown."""
        client = FakeDocFieldsApi("values")
        assert cap.entity_names_for(client, "alice", key) is None
        assert client.graph_nodes_calls == []

    def test_more_than_5000_nodes_is_free_text(self):
        client = FakeDocFieldsApi("values")
        for i in range(5001):
            client.nodes[f"x{i}"] = {"id": f"x{i}", "name": f"Firma {i}",
                                     "node_type_id": "t-mandant"}
        assert cap.entity_names_for(client, "alice", "mandant") is None
        assert cap.entity_names_for(client, "alice", "mandant") is None
        assert len(client.graph_nodes_calls) == 1  # the verdict is cached too

    def test_exactly_5000_still_suggests(self):
        client = FakeDocFieldsApi("values")
        for i in range(4998):
            client.nodes[f"x{i}"] = {"id": f"x{i}", "name": f"Firma {i}",
                                     "node_type_id": "t-mandant"}
        assert len(cap.entity_names_for(client, "alice", "mandant")) == 5000

    def test_a_graph_failure_is_free_text_and_not_cached(self):
        client = FakeDocFieldsApi("values")
        original = client.graph_nodes

        def broken(**kw):
            raise RuntimeError("graph down")

        client.graph_nodes = broken
        assert cap.entity_names_for(client, "alice", "mandant") is None
        client.graph_nodes = original
        assert cap.entity_names_for(client, "alice", "mandant") == ["Beispiel GmbH", "Muster AG"]

    def test_a_hidden_target_type_means_no_suggestions(self):
        client = FakeDocFieldsApi("values")
        next(s for s in client.registry if s["key"] == "mandant")["target_node_type_id"] = None
        assert cap.entity_names_for(client, "alice", "mandant") is None


class TestNodeNamesFor:
    """F3: the names of auto-scope nodes, each read on its own as the person
    (``graph_node_name``: GET /secured/graph/nodes/<id>), never the whole
    node list and never with q; a node Knovas does not show them (404) is
    only counted. The reads are optional: few, short, never retried, and a
    failed one is remembered briefly, so a notice cannot hold a search up."""

    def test_names_in_order_hidden_counted_one_read_per_id(self):
        client = FakeDocFieldsApi("filters")
        names, hidden = cap.node_names_for(client, "alice", ["m2", "gone", "m1", "m2"])
        assert names == ["Beispiel GmbH", "Muster AG"] and hidden == 1
        assert client.graph_node_name_calls == ["m2", "gone", "m1"]
        assert client.graph_nodes_calls == [], "never the whole node list"

    def test_a_big_graph_still_names_what_the_person_may_see(self):
        """Above 5000 nodes the person's own node is named all the same:
        nothing reads the node list, whatever its size."""
        client = FakeDocFieldsApi("filters")
        for i in range(6000):
            client.nodes[f"x{i}"] = {"id": f"x{i}", "name": f"Firma {i}",
                                     "node_type_id": "t-mandant"}
        assert cap.node_names_for(client, "alice", ["m1", "hidden"]) == (["Muster AG"], 1)
        assert client.graph_node_name_calls == ["m1", "hidden"]
        assert client.graph_nodes_calls == []

    def test_cached_per_person_and_node_a_404_too(self):
        client = FakeDocFieldsApi("filters")
        cap.node_names_for(client, "alice", ["m1", "gone"])
        assert cap.node_names_for(client, "alice", ["gone", "m1"]) == (["Muster AG"], 1)
        assert client.graph_node_name_calls == ["m1", "gone"]
        cap.node_names_for(client, "alice", ["m2"])
        cap.node_names_for(client, "bob", ["m1"])
        assert client.graph_node_name_calls == ["m1", "gone", "m2", "m1"]
        cap.invalidate("alice")
        cap.node_names_for(client, "alice", ["m1"])
        assert client.graph_node_name_calls == ["m1", "gone", "m2", "m1", "m1"]

    def test_the_cache_expires(self, monkeypatch):
        now = [0.0]
        monkeypatch.setattr(cap, "_now", lambda: now[0])
        client = FakeDocFieldsApi(StubConfig({"web.doc_fields.registry_cache_seconds": 10}),
                                  "filters")
        cap.node_names_for(client, "alice", ["m1"])
        now[0] = 9.9
        cap.node_names_for(client, "alice", ["m1"])
        now[0] = 10.1
        cap.node_names_for(client, "alice", ["m1"])
        assert client.graph_node_name_calls == ["m1", "m1"]

    def test_the_reads_are_bounded(self):
        """Knovas reports up to 200 nodes: one read more than the five names
        a notice shows, every other node counted without a request."""
        client = FakeDocFieldsApi("filters")
        ids = [f"hidden-{i}" for i in range(200)]
        assert cap.node_names_for(client, "alice", ids) == ([], 200)
        assert len(client.graph_node_name_calls) == cap.NODE_NAME_READS_MAX == 6

    def test_no_read_after_five_names(self):
        client = FakeDocFieldsApi("filters")
        for i in range(7):
            client.nodes[f"x{i}"] = {"id": f"x{i}", "name": f"Firma {i}",
                                     "node_type_id": "t-mandant"}
        names, hidden = cap.node_names_for(client, "alice", [f"x{i}" for i in range(7)])
        assert names == [f"Firma {i}" for i in range(5)] and hidden == 2
        assert client.graph_node_name_calls == [f"x{i}" for i in range(5)]

    def test_each_read_is_short_and_none_starts_late(self, monkeypatch):
        """A slow graph: each read gets the short timeout, and none starts
        once that much time has passed since the first."""
        now = [0.0]
        monkeypatch.setattr(cap, "_now", lambda: now[0])
        client = FakeDocFieldsApi("filters")
        answer = client.graph_node_name
        timeouts = []

        def slow(node_id, timeout):
            timeouts.append(timeout)
            now[0] += 1.5
            return answer(node_id, timeout)

        client.graph_node_name = slow
        assert cap.node_names_for(client, "alice", ["m1", "m2", "p1"]) == (
            ["Muster AG", "Beispiel GmbH"], 1)
        assert timeouts == [cap.NODE_NAME_TIMEOUT] * 2
        assert cap.NODE_NAME_TIMEOUT <= 2

    def test_a_failure_counts_stops_and_is_remembered_briefly(self, monkeypatch):
        """A graph that times out costs one search one short read -- not
        three retried ones, and not every search: for unknown_ttl (30 s)
        this person's notices count without asking, then names come back."""
        import requests

        now = [0.0]
        monkeypatch.setattr(cap, "_now", lambda: now[0])
        client = FakeDocFieldsApi("filters")
        answer = client.graph_node_name
        tried = []

        def timing_out(node_id, timeout):
            tried.append(node_id)
            raise requests.exceptions.ReadTimeout("read timed out")

        client.graph_node_name = timing_out
        assert cap.node_names_for(client, "alice", ["m1", "m2"]) == ([], 2)
        assert tried == ["m1"], "the first failure stops the other reads"
        now[0] = 29.0
        assert cap.node_names_for(client, "alice", ["m1", "m2"]) == ([], 2)
        assert tried == ["m1"], "remembered for 30 s: no read at all"
        assert cap.node_names_for(client, "bob", ["m1"]) == ([], 1)
        assert tried == ["m1", "m1"], "per person"
        client.graph_node_name = answer
        now[0] = 30.5
        assert cap.node_names_for(client, "alice", ["m1"]) == (["Muster AG"], 0)

    def test_the_cache_stays_bounded(self, monkeypatch):
        monkeypatch.setattr(cap, "NODE_NAMES_KEPT_MAX", 4)
        client = FakeDocFieldsApi("filters")
        for i in range(10):
            cap.node_names_for(client, "alice", [f"n{i}"])
            assert len(cap._NODE_NAMES) <= 4
        assert cap.node_names_for(client, "alice", ["m1"]) == (["Muster AG"], 0)

    def test_nothing_is_asked_without_ids(self):
        client = FakeDocFieldsApi("filters")
        assert cap.node_names_for(client, "alice", []) == ([], 0)
        assert client.graph_node_name_calls == [] and client.graph_nodes_calls == []

    def test_no_name_in_a_log_line(self, caplog):
        import logging

        sentinel = "Sentinel-Knoten-AG"
        client = FakeDocFieldsApi("filters")
        client.nodes["s1"] = {"id": "s1", "name": sentinel, "node_type_id": "t-mandant"}

        def broken(node_id, timeout):
            raise RuntimeError(sentinel)

        with caplog.at_level(logging.DEBUG):
            assert cap.node_names_for(client, "alice", ["s1"]) == ([sentinel], 0)
            cap.invalidate()
            client.graph_node_name = broken
            assert cap.node_names_for(client, "alice", ["s1"]) == ([], 1)
        assert caplog.records and sentinel not in caplog.text


def test_the_conftest_resets_the_shared_state_between_tests_part_1():
    cap.shared_cache().observe("feature_off")
    cap._REGISTRY["someone"] = object()


def test_the_conftest_resets_the_shared_state_between_tests_part_2():
    assert cap.shared_cache().peek() is None
    assert "someone" not in cap._REGISTRY


def test_no_value_in_any_log_line(caplog):
    """D6: capability, registry and suggestions log states and exception
    class names, never a name, a pointer or an exception's text."""
    import logging

    sentinel = "Muster-Sentinel-AG"
    client = FakeDocFieldsApi("filters")
    client.nodes["s1"] = {"id": "s1", "name": sentinel, "node_type_id": "t-mandant"}

    class Probe:
        def doc_fields_probe(self):
            raise RuntimeError(sentinel)

    def broken(**kw):
        raise RuntimeError(sentinel)

    with caplog.at_level(logging.DEBUG):
        cap.capability_for(client)
        cap.observe("needs_calibration")
        cap.observe("echo_missing")
        cap.registry_for(client, "alice")
        assert sentinel in cap.entity_names_for(client, "alice", "mandant")
        cap.invalidate()
        client.graph_nodes = broken
        assert cap.entity_names_for(client, "alice", "mandant") is None
        CapabilityCache().get(Probe())
    assert caplog.records  # the paths above do log
    assert sentinel not in caplog.text


def _db_reachable():
    from conftest import platform_db_reachable

    return platform_db_reachable()


@pytest.mark.skipif(not _db_reachable(), reason="No PostgreSQL for the identity app")
def test_the_fake_boots_the_identity_app(platform_db, tmp_path, monkeypatch):
    """What wave-2 route tests do: build the real app around the fake."""
    from conftest import _identity_app

    app = _identity_app(platform_db, tmp_path, monkeypatch,
                        client_cls=FakeDocFieldsApi.bind("listing_only"))
    api = FakeDocFieldsApi.current
    assert app is not None and api.config is not None
    assert (api.mode, api.calibrated) == ("filters", False)
    assert cap.capability_for(api) is Capability.filters  # the probe cannot see calibration
    cap.observe("needs_calibration")
    assert cap.capability_for(api) is Capability.listing_only
