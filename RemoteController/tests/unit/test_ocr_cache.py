"""OCR disk cache (GI-EXTRACT-04): hit/miss, LRU under the cap, 0600, purge
on delete/unsync, off-switch, and a connection never shared across fork."""
from __future__ import annotations

import multiprocessing as mp
import os
import stat

import pytest

from sync.ocr_cache import (
    CACHE_FILENAME,
    MemoryOcrCache,
    OcrDiskCache,
    ocr_cache_max_mb,
    ocr_cache_path_for_state,
)


@pytest.fixture
def cache(tmp_path) -> OcrDiskCache:
    c = OcrDiskCache(tmp_path / CACHE_FILENAME, max_bytes=1024)
    yield c
    c.close()


def test_miss_then_hit_and_counters(cache):
    assert cache.get("k") is None
    cache.put("k", "Seite 1 OCR")
    assert cache.get("k") == "Seite 1 OCR"
    assert (cache.hits, cache.misses) == (1, 1)
    assert cache.stats()["entries"] == 1


def test_created_with_mode_0600(cache):
    cache.put("k", "v")
    mode = stat.S_IMODE(os.stat(cache.path).st_mode)
    assert mode == 0o600, oct(mode)


def test_lru_eviction_under_the_cap(tmp_path):
    cache = OcrDiskCache(tmp_path / CACHE_FILENAME, max_bytes=30)
    cache.put("a", "x" * 10)
    cache.put("b", "y" * 10)
    cache.put("c", "z" * 10)
    assert cache.stats()["bytes"] == 30
    assert cache.get("a") == "x" * 10, "touch a: it is now the most recently used"
    cache.put("d", "w" * 10)
    assert cache.get("b") is None, "b was the least recently used"
    assert cache.get("a") == "x" * 10
    assert cache.get("c") == "z" * 10 and cache.get("d") == "w" * 10
    assert cache.stats()["bytes"] <= 30
    cache.put("huge", "h" * 31)
    assert cache.get("huge") is None, "an entry above the cap is never stored"
    cache.close()


def test_cap_zero_disables_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
    assert ocr_cache_max_mb() == 0
    disabled = OcrDiskCache.beside_state_path(tmp_path / "state.json")
    assert disabled.enabled is False
    disabled.put("k", "v")
    assert disabled.get("k") is None
    assert disabled.misses == 1
    assert not disabled.path.exists(), "no file is created when the cache is off"
    assert disabled.purge_document("x.pdf") == 0


def test_env_cap_and_path(tmp_path, monkeypatch):
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "7")
    c = OcrDiskCache.beside_state_path(tmp_path / "sub" / "state.json")
    assert c.max_bytes == 7 * 1024 * 1024
    assert c.path == tmp_path / "sub" / CACHE_FILENAME
    assert ocr_cache_path_for_state(tmp_path / "s.json") == tmp_path / CACHE_FILENAME
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "lots")
    assert ocr_cache_max_mb() == 512
    monkeypatch.delenv("RC_OCR_CACHE_MAX_MB")
    assert ocr_cache_max_mb() == 512


def test_purge_document_removes_only_that_documents_entries(cache):
    cache.for_document("a.pdf").put("k-a", "text a")
    cache.for_document("b.pdf").put("k-b", "text b")
    cache.for_document("a.pdf").put("k-shared", "same scan")
    cache.for_document("b.pdf").put("k-shared", "same scan")
    assert cache.purge_document("a.pdf") == 1
    assert cache.get("k-a") is None
    assert cache.get("k-b") == "text b"
    assert cache.get("k-shared") == "same scan", "still referenced by b.pdf"
    assert cache.purge_document("b.pdf") == 2
    assert cache.get("k-shared") is None and cache.get("k-b") is None


def test_purge_all_removes_the_file(cache):
    cache.put("k", "v")
    assert cache.path.exists()
    cache.purge_all()
    assert not cache.path.exists()
    assert cache.get("k") is None, "reopens empty"


def test_put_ignores_non_text_and_eviction_drops_document_rows(cache):
    cache.for_document("a.pdf").put("k", b"bytes")  # type: ignore[arg-type]
    assert cache.get("k") is None
    small = OcrDiskCache(cache.path.with_name("small.db"), max_bytes=4)
    small.for_document("a.pdf").put("k1", "aaaa")
    small.for_document("a.pdf").put("k2", "bbbb")
    assert small.get("k1") is None
    assert small.purge_document("a.pdf") == 1, "only k2 is left to purge"
    small.close()


def _child_reads(conn, cache: OcrDiskCache, key: str) -> None:
    value = cache.get(key)
    conn.send((os.getpid(), cache.connection_pid, value))
    conn.close()


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="fork only")
def test_connection_is_opened_after_fork_never_shared(cache):
    """A connection inherited from the parent is never used in the child:
    the child opens its own, and reads what the parent wrote."""
    cache.put("k", "parent wrote this")
    assert cache.connection_pid == os.getpid()
    ctx = mp.get_context("fork")
    receiver, sender = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_reads, args=(sender, cache, "k"))
    proc.start()
    sender.close()
    child_pid, child_conn_pid, value = receiver.recv()
    proc.join(10)
    assert value == "parent wrote this"
    assert child_conn_pid == child_pid != os.getpid()
    assert cache.connection_pid == os.getpid(), "the parent's connection is untouched"
    assert cache.get("k") == "parent wrote this"


def test_memory_cache_counts_hits_and_misses():
    m = MemoryOcrCache()
    assert m.get("k") is None
    m.put("k", "v")
    assert m.get("k") == "v"
    assert (m.hits, m.misses) == (1, 1)


def test_cache_is_usable_from_worker_threads(tmp_path, caplog):
    """The library's OCR scheduler calls get/put from its worker threads: a
    connection opened on the main thread must serve them (no SQLite
    same-thread ProgrammingError turned into silent misses)."""
    import logging
    from concurrent.futures import ThreadPoolExecutor

    from sync.ocr_cache import OcrDiskCache

    cache = OcrDiskCache(tmp_path / "ocr-cache.db", 1024 * 1024)
    cache.put("warm", "x" * 10)  # opens the connection on this thread

    def worker(i: int) -> str | None:
        cache.put(f"k{i}", f"v{i}")
        return cache.get(f"k{i}")

    with caplog.at_level(logging.WARNING, logger="sync.ocr_cache"):
        with ThreadPoolExecutor(max_workers=4) as pool:
            got = list(pool.map(worker, range(16)))
    assert got == [f"v{i}" for i in range(16)]
    assert cache.hits == 16 and cache.misses == 0
    assert not [r for r in caplog.records if "OCR cache" in r.getMessage()]
