from sync.ingest_rate_limit import acquire, configure


def test_acquire_within_burst():
    configure(max_per_minute=60, burst=5)
    assert acquire(max_wait_seconds=1.0)


def test_fail_closed_when_not_configured(monkeypatch):
    import sync.ingest_rate_limit as mod

    mod._limiter = None
    assert not acquire(max_wait_seconds=0.1)
