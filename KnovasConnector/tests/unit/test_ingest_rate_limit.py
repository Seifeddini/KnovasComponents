from sync.ingest_rate_limit import acquire, acquire_chars, configure


def test_acquire_within_burst():
    configure(max_per_minute=60, burst=5)
    assert acquire(max_wait_seconds=1.0)


def test_fail_closed_when_not_configured(monkeypatch):
    import sync.ingest_rate_limit as mod

    mod._requests_limiter = None
    mod._chars_limiter = None
    assert not acquire(max_wait_seconds=0.1)


def test_acquire_chars_cost():
    configure(max_per_minute=600, burst=10, max_chars_per_minute=6000, burst_chars=1000)
    assert acquire_chars(500, max_wait_seconds=1.0)
    assert acquire_chars(2000, max_wait_seconds=0.1) is False
