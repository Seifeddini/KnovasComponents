"""The capability the file routes are gated on.

These run without PostgreSQL on purpose. The wall's end-to-end tests need the
identity database and skip without it; the property that decides whether a
document is reachable should not skip with them.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from document_grants import DocumentGrantStore, normalize_pointer  # noqa: E402

POINTER = "Mandanten Sync/kanzlei/2024-017/brief.pdf"


@pytest.fixture
def store(tmp_path):
    # A subdirectory that does not exist yet: the production path lives under
    # the app data volume and the store has to create it.
    return DocumentGrantStore(str(tmp_path / "state" / "grants.sqlite3"), ttl_seconds=60)


class TestWhatRetrievalReturnedIsReachable:
    def test_a_granted_pointer_is_allowed(self, store):
        store.grant("u1", [POINTER])
        assert store.granted("u1", POINTER) is True

    def test_a_pointer_nobody_searched_for_is_refused(self, store):
        store.grant("u1", [POINTER])
        assert store.granted("u1", "Mandanten Sync/kanzlei/other.pdf") is False

    def test_a_grant_belongs_to_one_person(self, store):
        store.grant("u1", [POINTER])
        assert store.granted("u2", POINTER) is False

    def test_an_unauthenticated_subject_is_refused(self, store):
        store.grant("u1", [POINTER])
        assert store.granted("", POINTER) is False

    def test_granting_nothing_is_a_no_op(self, store):
        assert store.grant("u1", []) == 0
        assert store.grant("", [POINTER]) == 0


class TestOneDocumentHasSeveralSpellings:
    """Search grants the raw Knovas pointer; a caller may name the same file by
    its path under the mount. Both must reach the same grant, or the wall
    refuses documents it did grant."""

    def test_separators_and_leading_slashes_agree(self, store):
        store.grant("u1", [POINTER])
        assert store.granted("u1", POINTER.replace("/", "\\")) is True
        assert store.granted("u1", "/" + POINTER) is True

    def test_any_offered_spelling_matching_is_enough(self, store):
        store.grant("u1", [POINTER])
        assert store.granted("u1", "kanzlei/2024-017/brief.pdf", POINTER) is True

    def test_no_offered_spelling_matching_is_a_refusal(self, store):
        store.grant("u1", [POINTER])
        assert store.granted("u1", "kanzlei/2024-017/brief.pdf", "x/y.pdf") is False

    def test_case_is_not_folded(self, store):
        """The pointer becomes a path on a case-sensitive filesystem, so
        folding it would let one grant cover two different files."""
        store.grant("u1", [POINTER])
        assert store.granted("u1", POINTER.lower()) is False

    def test_normalize_is_the_one_rule_both_sides_use(self):
        assert normalize_pointer("  /a\\b/c.pdf ") == "a/b/c.pdf"
        assert normalize_pointer(None) == ""


class TestGrantsAgeOut:
    def test_an_expired_grant_is_refused(self, tmp_path):
        store = DocumentGrantStore(str(tmp_path / "g.sqlite3"), ttl_seconds=60)
        store.grant("u1", [POINTER])
        conn = sqlite3.connect(store._store_path)
        conn.execute("UPDATE document_grants SET ts = ?", (time.time() - 3600,))
        conn.commit()
        conn.close()
        assert store.granted("u1", POINTER) is False

    def test_searching_again_refreshes_the_grant(self, tmp_path):
        store = DocumentGrantStore(str(tmp_path / "g.sqlite3"), ttl_seconds=60)
        store.grant("u1", [POINTER])
        conn = sqlite3.connect(store._store_path)
        conn.execute("UPDATE document_grants SET ts = ?", (time.time() - 3600,))
        conn.commit()
        conn.close()
        store.grant("u1", [POINTER])
        assert store.granted("u1", POINTER) is True

    def test_a_ttl_below_the_floor_is_raised(self, tmp_path):
        """A tiny TTL would expire grants inside one page load."""
        store = DocumentGrantStore(str(tmp_path / "g.sqlite3"), ttl_seconds=1)
        assert store._ttl >= 60


class TestItSurvivesMoreThanOneWorker:
    def test_a_second_process_sees_the_first_ones_grant(self, tmp_path):
        """gunicorn runs several workers: the one serving the thumbnail is not
        the one that served the search."""
        path = str(tmp_path / "shared.sqlite3")
        DocumentGrantStore(path, ttl_seconds=60).grant("u1", [POINTER])
        assert DocumentGrantStore(path, ttl_seconds=60).granted("u1", POINTER) is True

    def test_the_shared_store_is_the_default_backend(self, store):
        assert store.backend == "sqlite"


class TestItFailsClosed:
    def test_an_unusable_path_degrades_instead_of_raising(self):
        store = DocumentGrantStore("/proc/nope/grants.sqlite3", ttl_seconds=60)
        assert store.backend == "memory"

    def test_the_degraded_store_still_refuses_what_it_never_granted(self):
        store = DocumentGrantStore("/proc/nope/grants.sqlite3", ttl_seconds=60)
        store.grant("u1", [POINTER])
        assert store.granted("u1", POINTER) is True
        assert store.granted("u1", "other.pdf") is False

    def test_an_unreadable_store_refuses_rather_than_serves(self, store, monkeypatch):
        """A store we cannot read is not permission to hand over bytes."""
        store.grant("u1", [POINTER])

        def explode(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(store, "_connect", explode)
        assert store.granted("u1", POINTER) is False

    def test_an_unwritable_store_does_not_break_the_search(self, store, monkeypatch):
        """Recording a grant is decoration on the search. If it cannot be done,
        the search still has to answer -- not 500."""

        def explode(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(store, "_connect", explode)
        assert store.grant("u1", [POINTER]) == 0
