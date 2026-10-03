"""What brings an experiment back to Knovas after purge-index.

Only an explicit reindex (all, or the one experiment) or a change to the
experiment itself while indexing is on. Renames of domains and metrics,
changes made while indexing is off, and the maintenance leave it out.
"""

import dataclasses

import pytest

from conftest import platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)

from experiments import store, tasks  # noqa: E402
from test_experiments_service import SETTINGS, World, measurements  # noqa: E402

OFF = dataclasses.replace(SETTINGS, index_enabled=False)


@pytest.fixture
def w(platform_db, identity_repo):
    return World(platform_db, identity_repo)


def purge(w, key):
    eid = w.conn.execute("SELECT id::text FROM exp_experiments WHERE key = %s", (key,)).fetchone()[0]
    w.conn.execute("DELETE FROM exp_jobs")
    store.set_index_state(w.conn, eid, "off", store.INDEX_OFF_PURGED)
    return eid


def state(w, key):
    return w.conn.execute("SELECT index_state, index_error FROM exp_experiments WHERE key = %s",
                          (key,)).fetchone()


def index_jobs(w):
    return [j for j in w.jobs("index") if j["status"] == "pending"]


def test_a_domain_rename_does_not_upload_purged_experiments(w):
    purged = w.create()["key"]
    kept = w.create(title="Zweites")["key"]
    purge(w, purged)
    w.manager.update_domain("marketing", {"name": "Marketing & Kommunikation"})
    assert state(w, purged) == ("off", store.INDEX_OFF_PURGED)
    assert state(w, kept)[0] == "pending"
    queued = [j["payload"]["experiment_id"] for j in index_jobs(w)]
    assert len(queued) == 1


def test_a_metric_rename_does_not_upload_purged_experiments(w):
    purged = w.create()["key"]
    purge(w, purged)
    ctr = next(m for m in w.manager.list_metrics("marketing") if m["key"] == "ctr")
    w.manager.update_metric(ctr["id"], {"name": "Klickrate (neu)"})
    assert state(w, purged) == ("off", store.INDEX_OFF_PURGED)
    assert index_jobs(w) == []


def test_a_change_while_indexing_is_off_keeps_the_purge_marker(w):
    purged = w.create()["key"]
    purge(w, purged)
    w.svc(w.eva, settings=OFF).add_measurements(purged, measurements(A=(3, 30), B=(5, 30)))
    assert state(w, purged) == ("off", store.INDEX_OFF_PURGED)
    # ...so the maintenance leaves it out once indexing is back on.
    assert tasks.requeue_switched_off(w.conn) == 0
    assert index_jobs(w) == []


def test_indexing_switched_off_still_marks_unpurged_experiments_for_reupload(w):
    key = w.create()["key"]
    w.svc(w.eva, settings=OFF).add_measurements(key, measurements(A=(3, 30), B=(5, 30)))
    assert state(w, key) == ("off", None)
    assert tasks.requeue_switched_off(w.conn) == 1


def test_a_change_to_the_experiment_itself_while_indexing_is_on_uploads_it(w):
    purged = w.create()["key"]
    purge(w, purged)
    w.experimenter.add_measurements(purged, measurements(A=(3, 30), B=(5, 30)))
    assert state(w, purged) == ("pending", None)
    assert len(index_jobs(w)) == 1


def test_reindex_all_brings_purged_experiments_back(w):
    purged = w.create()["key"]
    purge(w, purged)
    assert w.manager.reindex_all()["queued"] == 1
    assert state(w, purged) == ("pending", None)


def test_purged_experiments_are_counted_in_their_state(w):
    purged = w.create()["key"]
    eid = purge(w, purged)
    assert store.experiments_for_reindex(w.conn, states=("off",)) == []
    assert store.experiments_for_reindex(w.conn, states=("off",), include_purged=True) == [eid]
    assert store.index_state_counts(w.conn)["off"] == 1
