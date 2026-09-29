"""experiments.store against the real PostgreSQL: key allocation under
concurrency, aggregates per kind (sum_sq rules, casts, scope "latest"),
evaluator rows, time series, batches, tokens, settings whitelist, index
bookkeeping, idempotent installs, evaluations and the snapshot's sizes."""

import dataclasses
import datetime as dt
import json
import threading
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from conftest import PLATFORM_DB_TEST_DSN, _person, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)

from experiments import evaluators, kinds, schema, store  # noqa: E402
from experiments.errors import Conflict, ValidationError  # noqa: E402
from experiments.jobs import JobQueue  # noqa: E402

UTC = dt.timezone.utc

DEFINITION = schema.validate_type_definition({
    "fields": [{"key": "channel", "label": "Kanal", "type": "enum", "options": ["LinkedIn", "E-Mail"]}],
    "states": [
        {"key": "draft", "label": "Entwurf"},
        {"key": "running", "label": "L\u00e4uft", "phase": "running"},
        {"key": "decided", "label": "Entschieden", "phase": "decided"},
        {"key": "stopped", "label": "Abgebrochen", "phase": "stopped"},
    ],
    "initial": "draft",
    "transitions": [
        {"from": "draft", "to": "running", "label": "Starten"},
        {"from": "running", "to": "decided", "label": "Entscheiden"},
        {"from": "*", "to": "stopped", "label": "Abbrechen"},
    ],
    "variants": {"min": 0, "max": 5, "defaults": []},
})

METRICS = [
    ("ctr", "proportion", {}),
    ("latency", "duration", {}),
    ("sus", "mean", {}),
    ("errors", "count", {}),
    ("cpc", "ratio", {}),
    ("satisfaction", "ordinal", {"levels": {"1": "schlecht", "2.5": "mittel", "4": "gut"}}),
    ("preferred", "categorical", {"levels": {"1": "A", "2": "B"}}),
]


# -- fixtures and helpers ----------------------------------------------------------


@pytest.fixture
def connect(platform_db):
    import psycopg

    schema_name = platform_db.execute("SELECT current_schema()").fetchone()[0]
    opened = []

    def _connect():
        conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True,
                               options=f"-c search_path={schema_name}")
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        if not conn.closed:
            conn.close()


def make_experiment(conn, w, *, title="LinkedIn Karussell", variants=("A", "B"),
                    hypothesis="Karussell-Posts erh\u00f6hen die Klickrate"):
    with conn.transaction():
        key = store.allocate_experiment_key(conn, w.domain_id)
        eid = store.insert_experiment(
            conn, key=key, domain_id=w.domain_id, type_id=w.type_id, type_version=1, title=title,
            hypothesis=hypothesis, description="", status="draft",
            fields={"channel": "LinkedIn"}, tags=["linkedin"], owner_id=w.uid, actor_id=w.uid,
            started=False)
        store.replace_variants(conn, eid, [
            {"key": v, "name": f"Variante {v}", "is_control": i == 0} for i, v in enumerate(variants)])
        store.replace_experiment_metrics(conn, eid, [
            {"metric_id": w.metrics["ctr"], "role": "primary"},
            {"metric_id": w.metrics["latency"], "role": "guardrail", "guardrail_op": "max",
             "guardrail_value": 200.0},
        ] + [{"metric_id": w.metrics[k], "role": "secondary"}
             for k, _, _ in METRICS if k not in ("ctr", "latency")])
    variant_ids = {v["key"]: v["id"] for v in store.list_variants(conn, eid)}
    return eid, key, variant_ids


@pytest.fixture
def w(platform_db, identity_repo):
    user = _person(identity_repo, "eva@knovas.ch", "Eva", "experimenter")
    conn = platform_db
    uid = str(user.id)
    domain_id = store.insert_domain(conn, key="marketing", name="Marketing", id_prefix="MKT",
                                    color="#eb6834", description="", pack=None, actor_id=uid)
    type_id = store.insert_type(conn, domain_id=domain_id, key="ab_test", name="A/B-Test",
                                description="", definition=DEFINITION, actor_id=uid)
    metrics = {}
    for key, kind, definition in METRICS:
        metrics[key] = store.insert_metric(
            conn, domain_id=domain_id, key=key, name=key.upper(), kind=kind, unit="",
            direction="higher", description="", definition=schema.validate_metric_definition(kind, definition),
            actor_id=uid)
    world = SimpleNamespace(conn=conn, user=user, uid=uid, domain_id=domain_id, type_id=type_id,
                            metrics=metrics, repo=identity_repo)
    world.eid, world.key, world.variants = make_experiment(conn, world)
    return world


def add(w, rows, *, eid=None, run_id=None, source="manual", conn=None):
    """Insert measurement rows the way the service does (batch + COPY)."""
    conn = conn or w.conn
    eid = eid or w.eid
    variants = {v["key"]: v["id"] for v in store.list_variants(conn, eid)}
    with conn.transaction():
        batch = store.insert_batch(conn, experiment_id=eid, run_id=run_id, source=source,
                                   rows=len(rows), metric_keys=sorted({r["metric"] for r in rows}),
                                   filename=None, actor_id=w.uid)
        now = store.transaction_now(conn)
        store.copy_measurements(conn, [
            (eid, w.metrics[r["metric"]], variants.get(r.get("variant")), r.get("run_id", run_id),
             batch, r.get("observed_at", now), float(r["value"]), int(r.get("count", 1)),
             r.get("denominator"), r.get("sum_sq"), json.dumps(r.get("dims", {})), source, w.uid)
            for r in rows])
    return batch


def run(w, *, variant=None, status="finished", eid=None):
    variants = {v["key"]: v["id"] for v in store.list_variants(w.conn, eid or w.eid)}
    run_id = store.insert_run(w.conn, experiment_id=eid or w.eid, variant_id=variants.get(variant),
                              name="lauf", status=status, params={}, environment={}, commit_ref="",
                              source="api", started_at=None, ended_at=None, actor_id=w.uid)
    time.sleep(0.002)  # distinct created_at, so "newest" is well defined
    return run_id


def agg(w, metric, kind, scope=None):
    return {a["variant"]: a for a in store.aggregates(w.conn, w.eid, w.metrics[metric], kind=kind,
                                                      scope=scope)}


def experiment_state(conn, eid):
    return conn.execute("SELECT updated_at, row_version, index_state, indexed_at, index_error "
                        "FROM exp_experiments WHERE id = %s", (eid,)).fetchone()


# -- keys ---------------------------------------------------------------------------


def test_keys_are_sequential_per_domain_and_a_rollback_leaves_no_gap(w):
    conn = w.conn
    assert w.key == "MKT-1"
    other = store.insert_domain(conn, key="sales", name="Vertrieb", id_prefix="SAL", color="#1baf7a",
                                description="", pack=None, actor_id=None)
    with conn.transaction():
        assert store.allocate_experiment_key(conn, other) == "SAL-1"
    with pytest.raises(RuntimeError):
        with conn.transaction():
            assert store.allocate_experiment_key(conn, w.domain_id) == "MKT-2"
            raise RuntimeError("abort")
    _, key, _ = make_experiment(conn, w)
    assert key == "MKT-2"
    with pytest.raises(ValidationError):
        store.allocate_experiment_key(conn, "00000000-0000-0000-0000-000000000001")


def test_key_allocation_under_concurrency(w, connect):
    """Eight writers on separate connections create 40 experiments in one
    domain at once: every key is handed out exactly once, without gaps."""
    workers, per_worker = 8, 5
    barrier = threading.Barrier(workers)
    keys, errors = [], []
    lock = threading.Lock()

    def create():
        conn = connect()
        try:
            barrier.wait()
            for _ in range(per_worker):
                _, key, _ = make_experiment(conn, w)
                with lock:
                    keys.append(key)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors
    numbers = sorted(int(k.split("-")[1]) for k in keys)
    assert numbers == list(range(2, 2 + workers * per_worker))
    assert w.conn.execute("SELECT next_seq FROM exp_domains WHERE id = %s",
                          (w.domain_id,)).fetchone()[0] == 2 + workers * per_worker


# -- aggregates -------------------------------------------------------------------------


def test_proportion_aggregates_per_variant_in_order_with_the_null_variant_last(w):
    add(w, [{"metric": "ctr", "variant": "B", "value": 175, "count": 10714},
            {"metric": "ctr", "value": 2, "count": 10},
            {"metric": "ctr", "variant": "A", "value": 100, "count": 8000},
            {"metric": "ctr", "variant": "A", "value": 29, "count": 2688}])
    result = store.aggregates(w.conn, w.eid, w.metrics["ctr"], kind="proportion")
    assert [a["variant"] for a in result] == ["A", "B", None]
    a = result[0]
    assert a == {"variant": "A", "rows": 2, "n": 10688, "value_sum": 129.0, "denominator_sum": None,
                 "sum_sq": None, "estimate": pytest.approx(129 / 10688), "levels": None}
    assert result[2]["estimate"] == pytest.approx(0.2)
    # Plain Python numbers only: no Decimal can reach the JSON encoder.
    for item in result:
        for value in item.values():
            assert not isinstance(value, Decimal)
        assert type(item["n"]) is int and type(item["rows"]) is int
        assert type(item["value_sum"]) is float
    assert store.aggregates(w.conn, w.eid, w.metrics["sus"], kind="mean") == []


def test_mean_like_sum_sq_rules(w):
    add(w, [{"metric": "latency", "variant": "A", "value": 10},  # count 1, no sum_sq stored
            {"metric": "latency", "variant": "A", "value": 30, "count": 2, "sum_sq": 500},
            {"metric": "latency", "variant": "B", "value": 12},
            {"metric": "latency", "variant": "B", "value": 40, "count": 4}])  # count > 1 without sum_sq
    result = agg(w, "latency", "duration")
    assert result["A"]["sum_sq"] == pytest.approx(100 + 500)
    assert result["A"]["n"] == 3 and result["A"]["estimate"] == pytest.approx(40 / 3)
    assert result["B"]["sum_sq"] is None
    assert result["B"]["estimate"] == pytest.approx(52 / 5)


def test_count_and_ratio_aggregates(w):
    add(w, [{"metric": "errors", "variant": "A", "value": 3, "count": 1000},
            {"metric": "errors", "variant": "A", "value": 1, "count": 1000},
            {"metric": "cpc", "variant": "A", "value": 120.0, "denominator": 40},
            {"metric": "cpc", "variant": "A", "value": 30.0, "denominator": 10},
            {"metric": "cpc", "variant": "B", "value": 50.0, "denominator": 0}])
    errors = agg(w, "errors", "count")["A"]
    assert errors["estimate"] == pytest.approx(0.002) and errors["denominator_sum"] is None
    cpc = agg(w, "cpc", "ratio")
    assert cpc["A"]["denominator_sum"] == 50.0 and cpc["A"]["estimate"] == pytest.approx(3.0)
    assert cpc["A"]["sum_sq"] is None
    # A week without clicks: the ratio is undefined, not infinite.
    assert cpc["B"]["denominator_sum"] == 0.0 and cpc["B"]["estimate"] is None


def test_ordinal_and_categorical_aggregates(w):
    add(w, [{"metric": "satisfaction", "variant": "A", "value": 4, "count": 3},
            {"metric": "satisfaction", "variant": "A", "value": 1, "count": 1},
            {"metric": "satisfaction", "variant": "A", "value": 2.5, "count": 2},
            {"metric": "satisfaction", "variant": "A", "value": 4, "count": 1},
            {"metric": "preferred", "variant": "B", "value": 1, "count": 7},
            {"metric": "preferred", "variant": "B", "value": 2, "count": 3}])
    sat = agg(w, "satisfaction", "ordinal")["A"]
    assert sat["n"] == 7 and sat["rows"] == 4
    assert sat["value_sum"] == pytest.approx(4 * 4 + 1 + 5)
    assert sat["sum_sq"] == pytest.approx(16 * 4 + 1 + 6.25 * 2)
    assert sat["estimate"] == pytest.approx(22 / 7)
    assert sat["levels"] == {"1": 1, "2.5": 2, "4": 4}
    pref = agg(w, "preferred", "categorical")["B"]
    assert pref["levels"] == {"1": 7, "2": 3} and pref["estimate"] is None
    assert pref["value_sum"] == 3.0 and pref["sum_sq"] is None


def test_scope_since_until_dims_and_run_list(w):
    r1 = run(w)
    add(w, [
        {"metric": "ctr", "variant": "A", "value": 1, "count": 10,
         "observed_at": dt.datetime(2026, 9, 1, tzinfo=UTC), "dims": {"query": "q1"}},
        {"metric": "ctr", "variant": "A", "value": 2, "count": 10,
         "observed_at": dt.datetime(2026, 9, 10, tzinfo=UTC), "dims": {"query": "q2"}},
        {"metric": "ctr", "variant": "A", "value": 3, "count": 10,
         "observed_at": dt.datetime(2026, 9, 20, tzinfo=UTC), "dims": {"query": "q1"}, "run_id": r1},
    ])
    n = lambda scope: agg(w, "ctr", "proportion", scope)["A"]["value_sum"]  # noqa: E731
    assert n({}) == 6.0
    assert n({"since": "2026-09-10"}) == 5.0
    assert n({"until": "10.09.2026"}) == 3.0  # inclusive: the row of that day counts
    assert n({"since": "2026-09-05", "until": "2026-09-15"}) == 2.0
    assert n({"dims": {"query": "q1"}}) == 4.0
    assert n({"runs": [r1]}) == 3.0
    assert agg(w, "ctr", "proportion", {"dims": {"query": "q9"}}) == {}
    with pytest.raises(ValidationError):
        store.aggregates(w.conn, w.eid, w.metrics["ctr"], kind="proportion", scope={"runs": "all"})
    with pytest.raises(ValidationError):
        store.aggregates(w.conn, w.eid, w.metrics["ctr"], kind="nope")
    assert store.aggregates(w.conn, "kein-uuid", w.metrics["ctr"], kind="proportion") == []


def test_scope_latest_takes_each_variants_newest_finished_run_with_rows_of_the_metric(w):
    old = run(w)
    add(w, [{"metric": "ctr", "variant": "A", "value": 1, "count": 10},
            {"metric": "ctr", "variant": "B", "value": 2, "count": 10}], run_id=old)
    newer = run(w)
    add(w, [{"metric": "ctr", "variant": "B", "value": 5, "count": 10},
            {"metric": "sus", "variant": "A", "value": 50}], run_id=newer)
    failed = run(w, status="failed")
    add(w, [{"metric": "ctr", "variant": "A", "value": 9, "count": 10}], run_id=failed)
    # Rows without a run: dropped for variants that have a finished run,
    # kept for the ones (and the null variant) that have none.
    add(w, [{"metric": "ctr", "variant": "A", "value": 7, "count": 10},
            {"metric": "ctr", "value": 3, "count": 10}])
    latest = agg(w, "ctr", "proportion", {"runs": "latest"})
    assert latest["A"]["value_sum"] == 1.0  # the failed run and the newer run (no A rows) do not count
    assert latest["B"]["value_sum"] == 5.0
    assert latest[None]["value_sum"] == 3.0
    assert agg(w, "sus", "mean", {"runs": "latest"})["A"]["value_sum"] == 50.0

    third = make_experiment(w.conn, w, variants=("X", "Y"))[0]
    add(w, [{"metric": "ctr", "variant": "X", "value": 4, "count": 10},
            {"metric": "ctr", "variant": "Y", "value": 6, "count": 10}], eid=third)
    by_variant = {a["variant"]: a["value_sum"] for a in store.aggregates(
        w.conn, third, w.metrics["ctr"], kind="proportion", scope={"runs": "latest"})}
    assert by_variant == {"X": 4.0, "Y": 6.0}  # no runs at all: every run-less row counts


def test_metric_aggregates_for_several_metrics_at_once(w):
    add(w, [{"metric": "ctr", "variant": "A", "value": 1, "count": 10},
            {"metric": "preferred", "variant": "A", "value": 2, "count": 4}])
    metrics = [{"id": w.metrics["ctr"], "kind": "proportion"},
               {"id": w.metrics["preferred"], "kind": "categorical"},
               {"id": w.metrics["sus"], "kind": "mean"}]
    result = store.metric_aggregates(w.conn, w.eid, metrics)
    assert result[w.metrics["ctr"]][0]["n"] == 10
    assert result[w.metrics["preferred"]][0]["levels"] == {"2": 4}
    assert result[w.metrics["sus"]] == []


def test_evaluator_rows_are_the_newest_in_ascending_order(w):
    r1 = run(w)
    add(w, [{"metric": "sus", "variant": "A", "value": float(i), "dims": {"query": f"q{i}"},
             "observed_at": dt.datetime(2026, 9, 1, tzinfo=UTC)} for i in range(5)], run_id=r1)
    rows, truncated = store.evaluator_rows(w.conn, w.eid, w.metrics["sus"], 3)
    assert truncated is True
    assert [r["value"] for r in rows] == [2.0, 3.0, 4.0]
    assert rows[0] == {"variant": "A", "run": r1, "value": 2.0, "count": 1, "denominator": None,
                       "sum_sq": None, "observed_at": "2026-09-01T00:00:00+00:00",
                       "dims": {"query": "q2"}}
    rows, truncated = store.evaluator_rows(w.conn, w.eid, w.metrics["sus"], 10,
                                           scope={"dims": {"query": "q1"}})
    assert truncated is False and [r["value"] for r in rows] == [1.0]
    assert store.evaluator_rows(w.conn, w.eid, w.metrics["sus"], 0) == ([], False)


def test_rows_fingerprint_follows_inserts_and_deletes(w):
    empty = store.rows_fingerprint(w.conn, w.eid, w.metrics["ctr"])
    assert empty == (0, None)
    batch = add(w, [{"metric": "ctr", "variant": "A", "value": 1, "count": 2}])
    first = store.rows_fingerprint(w.conn, w.eid, w.metrics["ctr"])
    assert first[0] == 1
    add(w, [{"metric": "ctr", "variant": "A", "value": 1, "count": 2}])
    second = store.rows_fingerprint(w.conn, w.eid, w.metrics["ctr"])
    assert second[0] == 2 and second[1] > first[1]
    store.delete_batch(w.conn, w.eid, batch)
    assert store.rows_fingerprint(w.conn, w.eid, w.metrics["ctr"]) == (1, second[1])


def test_timeseries_buckets_in_utc(w):
    add(w, [
        {"metric": "ctr", "variant": "A", "value": 1, "count": 10,
         "observed_at": dt.datetime(2026, 9, 1, 23, 30, tzinfo=UTC)},
        # 00:30 in Zurich is still the 1st in UTC.
        {"metric": "ctr", "variant": "A", "value": 3, "count": 10,
         "observed_at": dt.datetime(2026, 9, 2, 0, 30, tzinfo=dt.timezone(dt.timedelta(hours=2)))},
        {"metric": "ctr", "variant": "B", "value": 2, "count": 10,
         "observed_at": dt.datetime(2026, 9, 3, tzinfo=UTC)},
        {"metric": "ctr", "variant": "A", "value": 5, "count": 10,
         "observed_at": dt.datetime(2026, 10, 5, tzinfo=UTC)},
    ])
    days = store.timeseries(w.conn, w.eid, w.metrics["ctr"], kind="proportion", bucket="day")
    assert [(p["bucket_start"], p["variant"], p["n"]) for p in days] == [
        ("2026-09-01T00:00:00+00:00", "A", 20), ("2026-09-03T00:00:00+00:00", "B", 10),
        ("2026-10-05T00:00:00+00:00", "A", 10)]
    assert days[0]["estimate"] == pytest.approx(0.2) and days[0]["value_sum"] == 4.0
    weeks = store.timeseries(w.conn, w.eid, w.metrics["ctr"], kind="proportion", bucket="week")
    assert [p["bucket_start"][:10] for p in weeks] == ["2026-08-31", "2026-08-31", "2026-10-05"]
    assert [p["variant"] for p in weeks[:2]] == ["A", "B"]
    months = store.timeseries(w.conn, w.eid, w.metrics["ctr"], kind="proportion", bucket="month")
    assert [p["bucket_start"][:10] for p in months] == ["2026-09-01", "2026-09-01", "2026-10-01"]
    with pytest.raises(ValidationError):
        store.timeseries(w.conn, w.eid, w.metrics["ctr"], kind="proportion", bucket="year")


# -- batches ------------------------------------------------------------------------------


def test_batches_count_list_page_and_delete(w):
    first = add(w, [{"metric": "ctr", "variant": "A", "value": 1, "count": 2}] * 3)
    time.sleep(0.002)
    second = add(w, [{"metric": "sus", "variant": "B", "value": 1}] * 2, source="csv")
    assert store.batch_counts(w.conn, w.eid) == (2, 5)
    items, cursor = store.list_batches(w.conn, w.eid, limit=1)
    assert [i["batch_id"] for i in items] == [second] and cursor
    assert items[0]["source_label"] == "CSV" and items[0]["created_by"]["display_name"] == "Eva"
    assert set(items[0]) == {"batch_id", "source", "source_label", "rows", "metric_keys", "filename",
                             "created_at", "created_by"}
    items, cursor = store.list_batches(w.conn, w.eid, after=cursor, limit=1)
    assert [i["batch_id"] for i in items] == [first] and cursor is None
    with pytest.raises(ValidationError):
        store.list_batches(w.conn, w.eid, after="nicht-base64!")
    other = make_experiment(w.conn, w)[0]
    assert store.delete_batch(w.conn, other, first) is None
    assert store.delete_batch(w.conn, w.eid, "kaputt") is None
    assert store.delete_batch(w.conn, w.eid, first) == 3
    assert agg(w, "ctr", "proportion") == {}
    assert store.batch_counts(w.conn, w.eid) == (1, 2)


# -- tokens ---------------------------------------------------------------------------------


def test_tokens_resolve_only_while_valid(w):
    conn = w.conn
    plaintext = "kxp_" + "a" * 43
    token = store.insert_token(conn, user_id=w.uid, name="CI", token_hash=store.hash_token(plaintext),
                               hint="kxp_\u2026aaaa", expires_days=30)
    assert store.resolve_api_token(conn, plaintext) == {"token_id": token["id"], "user_id": w.uid}
    stored = conn.execute("SELECT token_hash, last_used_at FROM exp_api_tokens").fetchone()
    assert stored[0] != plaintext and len(stored[0]) == 64 and stored[1] is not None
    # last_used_at is written at most once a minute.
    conn.execute("UPDATE exp_api_tokens SET last_used_at = now() - interval '30 seconds'")
    before = conn.execute("SELECT last_used_at FROM exp_api_tokens").fetchone()[0]
    store.resolve_api_token(conn, plaintext)
    assert conn.execute("SELECT last_used_at FROM exp_api_tokens").fetchone()[0] == before
    conn.execute("UPDATE exp_api_tokens SET last_used_at = now() - interval '2 minutes'")
    store.resolve_api_token(conn, plaintext)
    assert conn.execute("SELECT last_used_at FROM exp_api_tokens").fetchone()[0] > before

    for bad in (None, "", "kxp_", "Bearer " + plaintext, plaintext.upper(), "x" * 500, 42,
                "kxp_\ud800"):
        assert store.resolve_api_token(conn, bad) is None
    conn.execute("UPDATE exp_api_tokens SET expires_at = now() - interval '1 second'")
    assert store.resolve_api_token(conn, plaintext) is None
    conn.execute("UPDATE exp_api_tokens SET expires_at = now() + interval '1 day'")
    assert store.revoke_token(conn, str(w.user.id), token["id"])["revoked"] is True
    assert store.resolve_api_token(conn, plaintext) is None
    other = _person(w.repo, "max@knovas.ch", "Max", "experimenter")
    assert store.revoke_token(conn, str(other.id), token["id"]) is None
    assert store.list_tokens(conn, str(other.id)) == []
    assert [t["name"] for t in store.list_tokens(conn, w.uid)] == ["CI"]


# -- settings ---------------------------------------------------------------------------------


def test_runtime_settings_are_whitelisted_and_typed(w):
    conn = w.conn
    assert store.get_runtime_setting(conn, "experiments.show_in_search") is True
    store.set_runtime_setting(conn, "experiments.show_in_search", False, w.user)
    assert store.get_runtime_setting(conn, "experiments.show_in_search") is False
    row = conn.execute("SELECT value, updated_by::text FROM settings "
                       "WHERE key = 'experiments.show_in_search'").fetchone()
    assert row == (False, w.uid)
    for key in ("approvals.admin_bypass", "experiments.other", "", None):
        with pytest.raises(ValidationError):
            store.get_runtime_setting(conn, key)
        with pytest.raises(ValidationError):
            store.set_runtime_setting(conn, key, True, w.user)
    for value in (1, "true", None, {"enabled": True}):
        with pytest.raises(ValidationError):
            store.set_runtime_setting(conn, "experiments.show_in_search", value, w.user)
    # A value of the wrong type in the table reads as the default.
    conn.execute("UPDATE settings SET value = '\"no\"'::jsonb WHERE key = 'experiments.show_in_search'")
    assert store.get_runtime_setting(conn, "experiments.show_in_search") is True


def test_user_show_in_search_preference(w):
    conn = w.conn
    assert store.get_user_show_in_search(conn, w.user.id) is True
    store.set_user_show_in_search(conn, w.user.id, False)
    assert store.get_user_show_in_search(conn, w.user.id) is False
    key = f"experiments.user.{w.uid}.show_in_search"
    assert conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()[0] is False
    assert store.get_user_show_in_search(conn, "kaputt") is True
    with pytest.raises(ValidationError):
        store.set_user_show_in_search(conn, w.user.id, "false")


# -- index bookkeeping --------------------------------------------------------------------------


def test_set_index_state_touches_neither_updated_at_nor_row_version(w):
    conn = w.conn
    before = experiment_state(conn, w.eid)
    store.set_index_state(conn, w.eid, "error", "x" * 900)
    after = experiment_state(conn, w.eid)
    assert after[0] == before[0] and after[1] == before[1]
    assert after[2] == "error" and len(after[4]) == 500
    # An edit after the snapshot was read keeps the state pending.
    store.set_index_state(conn, w.eid, "pending")
    store.set_index_state(conn, w.eid, "indexed", if_updated_at="2020-01-01T00:00:00+00:00")
    assert experiment_state(conn, w.eid)[2] == "pending"
    store.set_index_state(conn, w.eid, "indexed", if_updated_at=store.iso(before[0]))
    state = experiment_state(conn, w.eid)
    assert state[2] == "indexed" and state[3] is not None and state[4] is None
    store.set_index_state(conn, w.eid, "indexed", if_updated_at=before[0])  # a datetime works too
    with pytest.raises(ValueError):
        store.set_index_state(conn, w.eid, "done")
    store.set_index_state(conn, "kaputt", "off")  # ignored


def test_index_documents_and_reindex_lists(w):
    conn = w.conn
    for i in (3, 1, 2):
        store.record_index_document(conn, f"experiments/marketing/MKT-{i}", w.eid)
    store.record_index_document(conn, "experiments/marketing/MKT-1", None)  # upsert
    assert store.index_documents(conn, limit=2) == ["experiments/marketing/MKT-1",
                                                    "experiments/marketing/MKT-2"]
    assert store.index_documents(conn, after="experiments/marketing/MKT-2") == [
        "experiments/marketing/MKT-3"]
    store.forget_index_document(conn, "experiments/marketing/MKT-3")
    assert store.index_documents(conn) == ["experiments/marketing/MKT-1", "experiments/marketing/MKT-2"]

    second = make_experiment(conn, w)[0]
    store.set_index_state(conn, second, "error", "Fehler")
    assert set(store.experiments_for_reindex(conn)) == {w.eid, second}
    assert store.experiments_for_reindex(conn, states=("error",)) == [second]
    assert store.experiments_for_reindex(conn, domain_id=w.domain_id, type_id=w.type_id,
                                         states=("pending",)) == [w.eid]
    assert store.index_state_counts(conn) == {"pending": 1, "indexed": 0, "error": 1, "off": 0}
    assert store.set_all_index_states(conn, "off") == 2
    assert store.index_state_counts(conn)["off"] == 2


def test_delete_pending_jobs_only_touches_pending_jobs_with_those_keys(w):
    queue = JobQueue(w.conn)
    queue.enqueue("index", {"experiment_id": w.eid}, dedupe_key=f"index:{w.eid}")
    queue.enqueue("pipeline", {"experiment_id": w.eid}, dedupe_key=f"pipeline:{w.eid}")
    queue.enqueue("index", {"experiment_id": "x"}, dedupe_key="index:x")
    assert store.delete_pending_jobs(w.conn, [f"index:{w.eid}", f"pipeline:{w.eid}"]) == 2
    assert [r[0] for r in w.conn.execute("SELECT dedupe_key FROM exp_jobs").fetchall()] == ["index:x"]
    assert store.delete_pending_jobs(w.conn, []) == 0


# -- configuration ------------------------------------------------------------------------------


def test_builtin_evaluators_are_registered_once_and_versioned_on_change(w, monkeypatch):
    conn = w.conn
    store.ensure_builtin_evaluators(conn)
    store.ensure_builtin_evaluators(conn)
    rows = conn.execute("SELECT key, current_version, language FROM exp_evaluators ORDER BY key").fetchall()
    assert [r[0] for r in rows] == sorted(evaluators.BUILTINS)
    assert {r[1] for r in rows} == {1} and {r[2] for r in rows} == {"builtin"}
    describe = store.get_evaluator(conn, key="builtin.describe", with_code=True)
    assert describe["builtin"] and describe["code"] == "builtin.describe" and not describe["needs_rows"]
    assert store.get_evaluator(conn, key="builtin.paired_t")["needs_rows"] is True
    changed = dict(evaluators.BUILTINS)
    changed["builtin.describe"] = dataclasses.replace(changed["builtin.describe"],
                                                      description="Neu beschrieben.")
    monkeypatch.setattr(evaluators, "BUILTINS", changed)
    store.ensure_builtin_evaluators(conn)
    store.ensure_builtin_evaluators(conn)
    describe = store.get_evaluator(conn, key="builtin.describe")
    assert describe["current_version"] == 2 and describe["description"] == "Neu beschrieben."
    assert [v["version"] for v in store.evaluator_versions(conn, describe["id"])] == [2, 1]


def test_core_pack_install_is_idempotent(w):
    conn = w.conn
    store.ensure_builtin_evaluators(conn)
    store.ensure_core_pack(conn)
    count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    before = (count("SELECT count(*) FROM exp_types"), count("SELECT count(*) FROM exp_evaluators"),
              count("SELECT count(*) FROM exp_type_versions"))
    store.ensure_core_pack(conn)
    after = (count("SELECT count(*) FROM exp_types"), count("SELECT count(*) FROM exp_evaluators"),
             count("SELECT count(*) FROM exp_type_versions"))
    assert before == after
    hypothesis = store.find_type(conn, w.domain_id, "hypothesis")
    assert hypothesis["domain_id"] is None and hypothesis["name"] == "Allgemeine Hypothese"
    from experiments import packs

    assert store.pack_installed(conn, packs.load_pack("core"))
    assert not store.pack_installed(conn, packs.load_pack("sales"))


def test_pack_install_refuses_a_taken_prefix(w):
    from experiments import packs

    pack = packs.load_pack("marketing")
    pack["domain"]["key"] = "werbung"  # MKT is taken by the "marketing" domain of the fixture
    with pytest.raises(Conflict) as info:
        store.install_pack(w.conn, pack)
    assert info.value.message == "Das K\u00fcrzel \u00abMKT\u00bb ist schon vergeben."
    assert store.get_domain(w.conn, "werbung") is None


def test_unique_keys_become_german_conflicts(w):
    conn = w.conn
    with pytest.raises(Conflict) as info:
        store.insert_domain(conn, key="marketing", name="X", id_prefix="XYZ", color="#000000",
                            description="", pack=None, actor_id=None)
    assert info.value.message == "Der Schl\u00fcssel \u00abmarketing\u00bb ist schon vergeben."
    with pytest.raises(Conflict) as info:
        store.insert_domain(conn, key="werbung", name="X", id_prefix="MKT", color="#000000",
                            description="", pack=None, actor_id=None)
    assert "K\u00fcrzel" in info.value.message
    with pytest.raises(Conflict):
        store.insert_type(conn, domain_id=w.domain_id, key="ab_test", name="X", description="",
                          definition=DEFINITION, actor_id=None)
    with pytest.raises(Conflict):
        store.insert_metric(conn, domain_id=w.domain_id, key="ctr", name="X", kind="proportion",
                            unit="", direction="higher", description="", definition={}, actor_id=None)
    # The same key globally is a different metric; the domain's own one wins.
    global_ctr = store.insert_metric(conn, domain_id=None, key="ctr", name="Global", kind="proportion",
                                     unit="", direction="higher", description="", definition={},
                                     actor_id=None)
    assert store.resolve_metrics(conn, w.domain_id, ["ctr"])["ctr"]["id"] == w.metrics["ctr"]
    assert store.resolve_metrics(conn, None, ["ctr", "sus"]) == {
        "ctr": store.get_metric(conn, global_ctr)}


def test_type_versions_bump_and_identical_definitions_add_nothing(w, connect):
    conn = w.conn
    assert store.add_type_version(conn, w.type_id, DEFINITION, w.uid) == (1, False)
    changed = dict(DEFINITION, decision={"require_learning": True})
    assert store.add_type_version(conn, w.type_id, changed, w.uid) == (2, True)
    assert store.get_type(conn, w.type_id)["definition"]["decision"] == {"require_learning": True}
    # Concurrent editors each get their own version number.
    barrier = threading.Barrier(4)
    results, errors = [], []

    def edit(i):
        try:
            c = connect()
            barrier.wait()
            definition = dict(DEFINITION, fields=[{"key": f"f{i}", "label": f"F{i}", "type": "text",
                                                   "required": False, "help": ""}])
            results.append(store.add_type_version(c, w.type_id, definition, w.uid))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=edit, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert sorted(v for v, added in results) == [3, 4, 5, 6]
    assert [v["version"] for v in store.type_versions(conn, w.type_id)] == [6, 5, 4, 3, 2, 1]


def test_metric_update_bumps_the_version(w):
    assert store.update_metric(w.conn, w.metrics["ctr"], {"name": "Klickrate"}) == 2
    metric = store.get_metric(w.conn, w.metrics["ctr"])
    assert metric["name"] == "Klickrate" and metric["version"] == 2 and metric["in_use"] is True
    assert store.metric_has_measurements(w.conn, w.metrics["ctr"]) is False
    with pytest.raises(ValueError):
        store.update_metric(w.conn, w.metrics["ctr"], {"key": "x"})


# -- evaluations --------------------------------------------------------------------------------


def insert_eval(w, *, key="builtin.describe", status="done", trigger="manual", output=None,
                params=None, scope=None, metric="ctr", digest=None, eid=None):
    evaluator = store.get_evaluator(w.conn, key=key)
    return store.insert_evaluation(
        w.conn, experiment_id=eid or w.eid, evaluator_id=evaluator["id"],
        evaluator_version=evaluator["current_version"], metric_id=w.metrics[metric],
        params=params or {}, scope=scope or {}, trigger=trigger, status=status,
        output=output if output is not None else ({"verdict": "better", "headline": "H"}
                                                  if status == "done" else None),
        input_digest=digest, requested_by=w.uid, duration_ms=5)


def test_mark_evaluation_moves_states_clips_logs_and_touches_the_experiment(w):
    conn = w.conn
    store.ensure_builtin_evaluators(conn)
    evaluation = insert_eval(w, status="queued")
    before = experiment_state(conn, w.eid)
    store.mark_evaluation(conn, evaluation, status="running")
    row = conn.execute("SELECT status, started_at, finished_at FROM exp_evaluations").fetchone()
    assert row[0] == "running" and row[1] is not None and row[2] is None
    assert experiment_state(conn, w.eid)[0] == before[0]
    logs = "a\x00b" + "\u00e4" * 70_000
    store.mark_evaluation(conn, evaluation, status="failed", error="E" * 800, logs=logs,
                          duration_ms=float("nan"))
    row = conn.execute("SELECT status, error, logs, finished_at, duration_ms FROM exp_evaluations").fetchone()
    assert row[0] == "failed" and len(row[1]) == 500 and row[3] is not None and row[4] is None
    assert "\x00" not in row[2] and len(row[2].encode("utf-8")) <= 64 * 1024
    after = experiment_state(conn, w.eid)
    assert after[0] > before[0] and after[1] == before[1]
    first_start = conn.execute("SELECT started_at FROM exp_evaluations").fetchone()[0]
    store.mark_evaluation(conn, evaluation, status="queued")
    # started_at is kept: the runner give-up clock counts from the first attempt.
    assert conn.execute("SELECT status, started_at, finished_at FROM exp_evaluations").fetchone() == (
        "queued", first_start, None)
    store.mark_evaluation(conn, evaluation, status="running")
    assert conn.execute("SELECT started_at FROM exp_evaluations").fetchone()[0] == first_start
    assert 0 <= store.seconds_since_first_attempt(conn, evaluation) < 60
    store.mark_evaluation(conn, evaluation, status="done", output={"verdict": "n/a"}, duration_ms=12)
    assert store.get_evaluation(conn, w.eid, evaluation)["duration_ms"] == 12
    with pytest.raises(ValueError):
        store.mark_evaluation(conn, evaluation, status="lost")


def test_evaluation_record_for_the_worker(w):
    store.ensure_builtin_evaluators(w.conn)
    evaluation = insert_eval(w, status="queued", params={"alpha": 0.1}, scope={"runs": "latest"})
    record = store.get_evaluation_record(w.conn, evaluation)
    assert set(record) >= {"id", "experiment_id", "experiment_key", "evaluator_id", "evaluator_key",
                           "language", "code", "version", "params_schema", "metric_id", "params",
                           "scope", "status", "trigger", "created_at"}
    assert record["experiment_key"] == w.key and record["code"] == "builtin.describe"
    assert record["params"] == {"alpha": 0.1} and record["scope"] == {"runs": "latest"}
    assert store.get_evaluation_record(w.conn, "kaputt") is None
    assert store.get_evaluation_record(w.conn, "00000000-0000-0000-0000-000000000000") is None


def test_reuse_and_pruning_of_pipeline_evaluations(w):
    store.ensure_builtin_evaluators(w.conn)
    first = insert_eval(w, trigger="pipeline", digest="d1")
    assert store.find_reusable_evaluation(
        w.conn, experiment_id=w.eid, evaluator_id=store.get_evaluator(w.conn, key="builtin.describe")["id"],
        version=1, metric_id=w.metrics["ctr"], params={}, scope={}, input_digest="d1") == first
    for other in ({"params": {"target": 1}}, {"scope": {"runs": "latest"}}, {"digest": "d2"}):
        kwargs = {"params": {}, "scope": {}, "input_digest": "d1"}
        kwargs.update({"input_digest" if k == "digest" else k: v for k, v in other.items()})
        assert store.find_reusable_evaluation(
            w.conn, experiment_id=w.eid,
            evaluator_id=store.get_evaluator(w.conn, key="builtin.describe")["id"], version=1,
            metric_id=w.metrics["ctr"], **kwargs) is None
    for _ in range(12):
        insert_eval(w, trigger="pipeline")
    for _ in range(3):
        insert_eval(w, trigger="pipeline", params={"target": 0.5})
    manual = insert_eval(w, trigger="manual")
    assert store.prune_pipeline_evaluations(w.conn, w.eid, keep=10) == 3
    rows = w.conn.execute("SELECT trigger, params, count(*) FROM exp_evaluations "
                          "GROUP BY 1, 2 ORDER BY 1, 3").fetchall()
    assert sorted((r[0], r[2]) for r in rows) == [("manual", 1), ("pipeline", 3), ("pipeline", 10)]
    assert store.get_evaluation(w.conn, w.eid, manual) is not None
    assert store.get_evaluation(w.conn, w.eid, first) is None  # the oldest went


# -- snapshot and lists ------------------------------------------------------------------------


def test_snapshot_shape_counts_and_sizes(w):
    conn = w.conn
    store.ensure_builtin_evaluators(conn)
    manager = _person(w.repo, "max@knovas.ch", "Max", "experiments_manager")
    other = _person(w.repo, "ina@knovas.ch", "Ina", "experimenter")
    for i in range(65):
        insert_eval(w, output={"verdict": "better", "headline": f"H{i}", "summary": "S"})
    for i in range(205):
        store.insert_note(conn, experiment_id=w.eid, kind="note", body=f"Notiz {i}", variant_id=None,
                          run_id=None, actor_id=w.uid)
    conn.execute(
        "INSERT INTO exp_runs (experiment_id, name, created_at) "
        "SELECT %s, 'bulk', now() - interval '1 hour' FROM generate_series(1, 102)",
        (w.eid,))
    runs = [run(w, variant="B") for _ in range(3)]
    add(w, [{"metric": "ctr", "variant": "A", "value": 3, "count": 10},
            {"metric": "latency", "variant": "B", "value": 300},
            {"metric": "ctr", "variant": "B", "value": 1, "count": 5}], run_id=runs[-1])
    store.insert_decision(conn, experiment_id=w.eid, verdict="ship", rationale="R", learning="L",
                          actor_id=w.uid)

    snap = store.load_snapshot(conn, w.key, actor=w.user)
    assert set(snap) == {
        "id", "key", "title", "hypothesis", "description", "status", "status_label", "status_phase",
        "archived", "domain", "type", "fields", "field_values", "tags", "owner", "variants", "metrics",
        "evaluations", "decisions", "notes", "runs", "runs_next_after", "run_count",
        "measurement_count", "batch_count", "created_at", "updated_at", "started_at", "ended_at",
        "decided_at", "row_version", "index"}
    assert snap["status_label"] == "Entwurf" and snap["status_phase"] is None
    assert snap["domain"] == {"id": w.domain_id, "key": "marketing", "name": "Marketing",
                              "color": "#eb6834", "id_prefix": "MKT"}
    assert snap["type"] == {"id": w.type_id, "key": "ab_test", "name": "A/B-Test", "version": 1}
    assert snap["fields"] == [{"key": "channel", "label": "Kanal", "type": "enum", "value": "LinkedIn",
                               "display": "LinkedIn"}]
    assert snap["owner"] == {"id": w.uid, "display_name": "Eva"}
    assert [(v["key"], v["is_control"], v["has_data"]) for v in snap["variants"]] == [
        ("A", True, True), ("B", False, True)]
    assert set(snap["variants"][0]) == {"id", "key", "name", "description", "is_control", "allocation",
                                        "position", "has_data"}
    metrics = {m["key"]: m for m in snap["metrics"]}
    assert set(metrics["ctr"]) == {
        "id", "key", "name", "kind", "kind_label", "unit", "direction", "direction_label", "role",
        "role_label", "guardrail_op", "guardrail_value", "definition", "guardrail_status", "aggregates"}
    assert metrics["ctr"]["role_label"] == "prim\u00e4r" and metrics["ctr"]["guardrail_status"] is None
    assert metrics["latency"]["guardrail_status"] == "violated"
    assert metrics["sus"]["aggregates"] == []
    assert len(snap["evaluations"]) == 60
    assert [e["output"] is not None for e in snap["evaluations"]] == [True] * 20 + [False] * 40
    assert snap["evaluations"][0]["headline"] == "H64" and snap["evaluations"][59]["headline"] == "H5"
    assert all(e["verdict"] == "better" for e in snap["evaluations"])
    assert "logs" not in snap["evaluations"][0]
    assert snap["evaluations"][0]["requested_by"] == {"display_name": "Eva"}
    assert len(snap["notes"]) == 200 and snap["notes"][0]["body"] == "Notiz 204"
    assert all(n["can_delete"] for n in snap["notes"])
    assert len(snap["runs"]) == 100 and snap["run_count"] == 105
    newest_with_rows = next(r for r in snap["runs"] if r["id"] == runs[-1])
    assert newest_with_rows["metrics"] == {"ctr": pytest.approx(4 / 15), "latency": 300.0}
    assert snap["measurement_count"] == 3 and snap["batch_count"] == 1
    assert snap["decisions"][0]["verdict_label"] == "\u00dcbernehmen"
    assert snap["decisions"][0]["decided_by"] == {"id": w.uid, "display_name": "Eva"}
    assert snap["index"] == {"state": "pending", "state_label": "ausstehend", "indexed_at": None,
                             "error": None}
    json.dumps(snap, allow_nan=False)

    assert all(n["can_delete"] for n in store.load_snapshot(conn, w.key, actor=manager)["notes"])
    assert not any(n["can_delete"] for n in store.load_snapshot(conn, w.key, actor=other)["notes"])
    assert not any(n["can_delete"] for n in store.load_snapshot(conn, w.key)["notes"])
    assert store.load_snapshot(conn, w.eid)["key"] == w.key
    for missing in ("MKT-99", "mkt-1", "", None, "MKT-1; DROP TABLE x"):
        assert store.load_snapshot(conn, missing) is None


def test_lookup_by_keys(w):
    found = store.lookup_by_keys(w.conn, [w.key, "MKT-99", "kaputt", w.key])
    assert list(found) == [w.key]
    assert set(found[w.key]) == {"key", "title", "hypothesis", "status", "status_label", "status_phase",
                                 "archived", "domain_key", "domain_name", "domain_color", "type_name",
                                 "updated_at"}
    assert found[w.key]["status_phase"] is None  # draft has no phase
    assert found[w.key]["status_label"] == "Entwurf" and found[w.key]["type_name"] == "A/B-Test"
    assert store.lookup_by_keys(w.conn, []) == {}


def test_list_summaries_filters_pages_latest_result_and_guardrails(w):
    conn = w.conn
    store.ensure_builtin_evaluators(conn)
    second, second_key, _ = make_experiment(conn, w, title="Newsletter Betreff 50%_Rabatt",
                                            hypothesis="Rabatt im Betreff")
    third, third_key, _ = make_experiment(conn, w, title="Archiviert", hypothesis="")
    conn.execute("UPDATE exp_experiments SET archived = TRUE, tags = '{alt}' WHERE id = %s", (third,))
    add(w, [{"metric": "latency", "variant": "A", "value": 300}])
    insert_eval(w, output={"verdict": "better", "headline": "Besser"})
    time.sleep(0.002)
    # Another group (params differ): a newer evaluation of the same group would
    # supersede "Besser" instead.
    insert_eval(w, output={"verdict": "n/a", "headline": "Nur Zahlen"}, params={"target": 0.5})
    conn.execute("UPDATE exp_experiments SET updated_at = now() + interval '1 minute' WHERE id = %s",
                 (w.eid,))

    items, cursor, total = store.list_summaries(conn, limit=1)
    assert total == 2 and cursor
    first = items[0]
    assert first["key"] == w.key
    assert first["latest"]["headline"] == "Besser"  # a real verdict beats a newer n/a
    assert first["guardrail_violations"] == 1
    assert first["primary_metric"] == {"key": "ctr", "name": "CTR", "unit": "", "kind": "proportion"}
    assert first["owner"] == {"id": w.uid, "display_name": "Eva"}
    assert set(first) == {"key", "title", "status", "status_label", "status_phase", "archived", "tags",
                          "domain", "type", "owner", "primary_metric", "latest",
                          "guardrail_violations", "updated_at", "index_state"}
    items, cursor, _ = store.list_summaries(conn, after=cursor, limit=1)
    assert [i["key"] for i in items] == [second_key] and cursor is None
    assert items[0]["latest"] is None and items[0]["guardrail_violations"] == 0

    keys = lambda **kw: [i["key"] for i in store.list_summaries(conn, **kw)[0]]  # noqa: E731
    assert keys(include_archived=True, tag="alt") == [third_key]
    assert keys(words=["50%_"]) == [second_key]
    assert keys(words=["5_%"]) == []  # wildcards are taken literally
    assert keys(words=["betreff", "rabatt"]) == [second_key]
    assert keys(words=["karussell"]) == [w.key]  # the hypothesis matches
    assert keys(domain="sales") == [] and keys(status="running") == []
    assert keys(words=[w.key.lower()]) == [w.key]


def test_search_database_looks_into_notes_and_decisions(w):
    conn = w.conn
    other = make_experiment(conn, w, title="Anderes")[0]
    store.insert_note(conn, experiment_id=other, kind="interview", body="Die Kanzlei will Vorschau",
                      variant_id=None, run_id=None, actor_id=None)
    store.insert_decision(conn, experiment_id=w.eid, verdict="stop", rationale="zu teuer",
                          learning="Karussell wirkt nur bei Juristen", actor_id=None)
    hits = store.search_database(conn, ["vorschau"], 10)
    assert hits == [("MKT-2", "Die Kanzlei will Vorschau")]
    hits = store.search_database(conn, ["juristen"], 10)
    assert hits == [(w.key, "Karussell wirkt nur bei Juristen")]
    assert store.search_database(conn, ["%"], 10) == []
    assert store.search_database(conn, [], 10) == []


def test_activity_names_the_actor_or_a_removed_account(w):
    from identity import audit

    conn = w.conn
    gone = _person(w.repo, "weg@knovas.ch", "Weg", "experimenter")
    audit.record(conn, action="experiments.note.add", actor=w.user, target_type="experiment",
                 target_id=w.key, detail={"note_id": "x"})
    audit.record(conn, action="experiments.note.add", actor=gone, target_type="experiment",
                 target_id=w.key)
    audit.record(conn, action="experiments.pipeline.run", actor=None, target_type="experiment",
                 target_id=w.key)
    audit.record(conn, action="other", actor=w.user, target_type="experiment", target_id="MKT-2")
    conn.execute("DELETE FROM users WHERE id = %s", (str(gone.id),))
    rows = store.activity_rows(conn, w.key)
    assert [r["actor"] for r in rows] == [None, "Gel\u00f6schtes Konto", "Eva"]
    assert rows[2]["detail"] == {"note_id": "x"}


def test_runs_page_with_metrics(w):
    ids = [run(w, variant="A") for _ in range(3)]
    add(w, [{"metric": "ctr", "variant": "A", "value": 2, "count": 8}], run_id=ids[0])
    items, cursor = store.list_runs(w.conn, w.eid, limit=2)
    assert [i["id"] for i in items] == [ids[2], ids[1]] and cursor
    items, cursor = store.list_runs(w.conn, w.eid, after=cursor, limit=2)
    assert [i["id"] for i in items] == [ids[0]] and cursor is None
    assert items[0]["metrics"] == {"ctr": 0.25} and items[0]["variant"] == "A"
    assert items[0]["status_label"] == "abgeschlossen"
    assert set(items[0]) == {"id", "name", "variant", "status", "status_label", "params", "environment",
                             "commit", "source", "started_at", "ended_at", "created_at", "metrics"}


def test_viewers_without_the_access_group(w):
    conn = w.conn
    member = _person(w.repo, "mia@knovas.ch", "Mia", "member")
    manager = _person(w.repo, "max@knovas.ch", "Max", "experiments_manager")
    w.repo.set_access_groups(manager.id, ["g-exp"])
    assert member
    assert store.viewers_without_groups(conn, ["g-exp", "g-2"]) == [
        {"user": "Eva (eva@knovas.ch)", "missing_groups": ["g-exp", "g-2"]}]
    assert store.viewers_without_groups(conn, []) == []
    assert store.user_access_groups(conn, manager.id) == ("g-exp",)


# -- regressions (review fixes) --------------------------------------------------------------------


def describe_id(w):
    return store.get_evaluator(w.conn, key="builtin.describe")["id"]


def reusable(w, digest, **kwargs):
    return store.find_reusable_evaluation(
        w.conn, experiment_id=w.eid, evaluator_id=describe_id(w), version=1,
        metric_id=w.metrics["ctr"], params=kwargs.get("params", {}), scope={}, input_digest=digest)


def test_only_the_newest_of_a_group_is_reused_and_current(w):
    # review-backend-1 / e2e-ui-3: after an undo the digest matches an older
    # evaluation again; reusing it left the evaluation of the removed rows the
    # newest one everywhere.
    store.ensure_builtin_evaluators(w.conn)
    old = insert_eval(w, trigger="pipeline", digest="d1",
                      output={"verdict": "better", "headline": "Alt"})
    time.sleep(0.002)
    removed = insert_eval(w, trigger="pipeline", digest="d2",
                          output={"verdict": "worse", "headline": "Entfernt"})
    time.sleep(0.002)
    other_group = insert_eval(w, trigger="pipeline", digest="d1", params={"target": 0.5},
                              output={"verdict": "n/a", "headline": "Andere"})
    assert reusable(w, "d1") is None  # d1 is history now, a fresh evaluation is due
    assert reusable(w, "d2") == removed
    assert reusable(w, "d1", params={"target": 0.5}) == other_group
    evaluations = {e["id"]: e for e in store.load_snapshot(w.conn, w.key)["evaluations"]}
    assert evaluations[old]["superseded"] is True
    assert evaluations[removed]["superseded"] is False
    assert evaluations[other_group]["superseded"] is False
    assert store.get_evaluation(w.conn, w.eid, old)["superseded"] is True
    assert store.get_evaluation(w.conn, w.eid, removed)["superseded"] is False

    # A newer failed evaluation makes the done one history too and is not
    # reused itself: the next pipeline runs it again.
    time.sleep(0.002)
    failed = insert_eval(w, status="failed", digest="d2")
    assert reusable(w, "d2") is None
    assert store.get_evaluation(w.conn, w.eid, removed)["superseded"] is True
    assert store.get_evaluation(w.conn, w.eid, failed)["superseded"] is False
    # A queued one on the current input is reused (it is about to run).
    time.sleep(0.002)
    queued = insert_eval(w, status="queued", digest="d3")
    assert reusable(w, "d3") == queued
    # Another evaluator version never matches.
    assert store.find_reusable_evaluation(
        w.conn, experiment_id=w.eid, evaluator_id=describe_id(w), version=2,
        metric_id=w.metrics["ctr"], params={}, scope={}, input_digest="d3") is None


def test_list_latest_reads_only_current_done_evaluations(w):
    store.ensure_builtin_evaluators(w.conn)
    insert_eval(w, output={"verdict": "better", "headline": "Alt"})
    time.sleep(0.002)
    insert_eval(w, output={"verdict": "worse", "headline": "Neu"})
    latest = lambda: store.list_summaries(w.conn)[0][0]["latest"]  # noqa: E731
    assert latest()["headline"] == "Neu"  # the older "better" of the same group is superseded
    time.sleep(0.002)
    insert_eval(w, status="queued")  # the group is being recomputed
    assert latest() is None
    time.sleep(0.002)
    insert_eval(w, key="builtin.bayes_proportion", output={"verdict": "better", "headline": "Bayes"})
    assert latest()["headline"] == "Bayes"
    assert store.summaries_by_keys(w.conn, [w.key])[w.key]["latest"]["headline"] == "Bayes"


def test_aggregate_sums_cannot_overflow(w):
    # review-security-1 / review-backend-4: 1'025 rows of 2**53 - 1 (the old
    # per-row maximum) overflowed every sum(count)::bigint, and the experiment
    # could no longer be read, evaluated or indexed.
    run_id = run(w, variant="A")
    big = 2 ** 53 - 1
    add(w, [{"metric": "ctr", "variant": "A", "value": 0, "count": big}] * 1025)
    add(w, [{"metric": "satisfaction", "variant": "A", "value": 4, "count": big}] * 1025,
        run_id=run_id)
    snap = store.load_snapshot(w.conn, w.key)
    ctr = next(m for m in snap["metrics"] if m["key"] == "ctr")
    assert ctr["aggregates"][0]["n"] == pytest.approx(1025 * big, rel=1e-12)
    assert ctr["aggregates"][0]["estimate"] == 0.0
    sat = next(m for m in snap["metrics"] if m["key"] == "satisfaction")
    assert sat["aggregates"][0]["levels"]["4"] == pytest.approx(1025 * big, rel=1e-12)
    json.dumps(snap, allow_nan=False)
    assert store.timeseries(w.conn, w.eid, w.metrics["ctr"], kind="proportion", bucket="day")
    assert store.list_runs(w.conn, w.eid)[0][0]["metrics"] == {"satisfaction": pytest.approx(4.0)}
    assert store.aggregates(w.conn, w.eid, w.metrics["satisfaction"], kind="ordinal",
                            scope={"runs": "latest"})[0]["n"] > 2 ** 63
    # Below 2**53 counts stay exact.
    other = make_experiment(w.conn, w, title="Genau")[0]
    add(w, [{"metric": "ctr", "variant": "A", "value": 1, "count": 2 ** 52},
            {"metric": "ctr", "variant": "A", "value": 0, "count": 2 ** 52 - 1}], eid=other)
    n = store.aggregates(w.conn, other, w.metrics["ctr"], kind="proportion")[0]["n"]
    assert n == 2 ** 53 - 1 and isinstance(n, int)


def test_levels_of_an_ordinal_metric_without_defined_levels_are_bounded(w):
    # review-backend-10: every distinct value became a level of every snapshot.
    score = store.insert_metric(
        w.conn, domain_id=w.domain_id, key="score", name="Score", kind="ordinal", unit="",
        direction="higher", description="",
        definition=schema.validate_metric_definition("ordinal", {"min": 0, "max": 100}), actor_id=w.uid)
    w.metrics["score"] = score
    w.conn.execute("INSERT INTO exp_experiment_metrics (experiment_id, metric_id, role, position) "
                   "VALUES (%s, %s, 'secondary', 99)", (w.eid, score))
    add(w, [{"metric": "score", "variant": "A", "value": i / 4} for i in range(60)]
        + [{"metric": "score", "variant": "B", "value": v, "count": 2} for v in (1, 2, 2, 3)])
    aggs = {a["variant"]: a for a in store.aggregates(w.conn, w.eid, score, kind="ordinal")}
    assert aggs["A"]["levels"] is None and aggs["A"]["n"] == 60
    assert aggs["A"]["estimate"] == pytest.approx(sum(i / 4 for i in range(60)) / 60)
    assert aggs["B"]["levels"] == {"1": 2, "2": 4, "3": 2}
    assert kinds.MAX_AGGREGATE_LEVELS == 50
    exactly = make_experiment(w.conn, w, title="Genau 50")[0]
    w.conn.execute("INSERT INTO exp_experiment_metrics (experiment_id, metric_id, role, position) "
                   "VALUES (%s, %s, 'secondary', 99)", (exactly, score))
    add(w, [{"metric": "score", "variant": "A", "value": i} for i in range(50)], eid=exactly)
    assert len(store.aggregates(w.conn, exactly, score, kind="ordinal")[0]["levels"]) == 50


def test_summaries_carry_the_status_phase(w):
    # review-contract-frontend-5: the list guessed the phase from the key.
    w.conn.execute("UPDATE exp_experiments SET status = 'running' WHERE id = %s", (w.eid,))
    items = store.list_summaries(w.conn)[0]
    assert items[0]["status_phase"] == "running" and items[0]["status_label"] == "L\u00e4uft"
    assert store.summaries_by_keys(w.conn, [w.key])[w.key]["status_phase"] == "running"
    assert store.lookup_by_keys(w.conn, [w.key])[w.key]["status_phase"] == "running"


def test_list_pages_bring_experiments_that_moved_above_the_cursor(w):
    # review-contract-frontend-11: a change moved an experiment from a later
    # page above the cursor, and no page returned it any more.
    for i in range(4):
        make_experiment(w.conn, w, title=f"E{i}")
        time.sleep(0.002)
    everyone = {i["key"] for i in store.list_summaries(w.conn, limit=50)[0]}
    assert len(everyone) == 5
    page, cursor, total = store.list_summaries(w.conn, limit=2)
    seen = [i["key"] for i in page]
    oldest = store.list_summaries(w.conn, limit=50)[0][-1]["key"]
    assert oldest not in seen
    store.touch_experiment(w.conn, store.get_experiment_row(w.conn, oldest)["id"])
    moved = []
    while cursor:
        page, cursor, total = store.list_summaries(w.conn, after=cursor, limit=2)
        seen += [i["key"] for i in page]
        moved += [i["key"] for i in page if i.get("moved")]
    assert set(seen) == everyone and total == 5
    assert oldest in moved
    # Cursors without the as_of part (older pages, batches and runs) still work.
    legacy = store.encode_cursor(dt.datetime.now(UTC) + dt.timedelta(days=1),
                                 "ffffffff-ffff-ffff-ffff-ffffffffffff")
    assert len(store.list_summaries(w.conn, after=legacy, limit=50)[0]) == 5
    with pytest.raises(ValidationError):
        store.list_summaries(w.conn, after="a3x8", limit=2)


def test_prune_keeps_ten_per_group_of_ci_evaluations_too(w):
    # review-backend-7: CI's evaluations (trigger 'api') were never pruned.
    store.ensure_builtin_evaluators(w.conn)
    for _ in range(12):
        insert_eval(w, trigger="api")
    newest = insert_eval(w, trigger="api")
    manual = [insert_eval(w, trigger="manual", params={"target": 0.1}) for _ in range(12)]
    assert store.prune_pipeline_evaluations(w.conn, w.eid, keep=10) == 3
    counts = dict(w.conn.execute("SELECT trigger, count(*) FROM exp_evaluations GROUP BY 1").fetchall())
    assert counts == {"api": 10, "manual": 12}
    assert store.get_evaluation(w.conn, w.eid, newest) is not None
    assert all(store.get_evaluation(w.conn, w.eid, m) for m in manual)


def test_switched_off_experiments_are_found_for_reupload_but_purged_ones_are_not(w):
    # review-jobs-5: 'off' while indexing was switched off (index_error NULL)
    # vs 'off' after purge-index (marked).
    conn = w.conn
    purged = make_experiment(conn, w, title="Entfernt")[0]
    pending = make_experiment(conn, w, title="Wartet")[0]
    store.set_index_state(conn, w.eid, "off")
    store.set_index_state(conn, purged, "off", store.INDEX_OFF_PURGED)
    store.set_index_state(conn, pending, "pending")
    assert experiment_state(conn, purged)[4] == store.INDEX_OFF_PURGED
    assert experiment_state(conn, w.eid)[4] is None
    assert set(store.experiments_for_reindex(conn, states=("pending", "error"),
                                             switched_off=True)) == {w.eid, pending}
    assert store.experiments_for_reindex(conn, states=("pending", "error")) == [pending]
    assert store.experiments_for_reindex(conn, switched_off=True) == [w.eid]
    assert store.set_all_index_states(conn, "off", store.INDEX_OFF_PURGED) == 2
    assert store.experiments_for_reindex(conn, switched_off=True) == []


def test_owner_candidates_need_an_active_account_with_a_viewing_role(w):
    # e2e-api-5
    member = _person(w.repo, "mia@knovas.ch", "Mia", "member")
    admin = _person(w.repo, "chef@knovas.ch", "Chef", "admin")
    assert store.user_can_view_experiments(w.conn, w.uid)
    assert store.user_can_view_experiments(w.conn, admin.id)
    assert not store.user_can_view_experiments(w.conn, member.id)
    assert not store.user_can_view_experiments(w.conn, "kaputt")
    w.conn.execute("UPDATE users SET status = 'disabled' WHERE id = %s", (w.uid,))
    assert not store.user_can_view_experiments(w.conn, w.uid)


def test_assigned_metrics_can_lock_the_kinds_they_validate_against(w, connect):
    # review-backend-9: the insert holds KEY SHARE on its metrics, so a kind
    # change (FOR UPDATE) waits for it; creating experiments (KEY SHARE) and
    # pack imports (NO KEY UPDATE) do not.
    conn = w.conn
    other = connect()
    other.execute("SET lock_timeout = '300ms'")
    with conn.transaction():
        assert [m["key"] for m in store.assigned_metrics(conn, w.eid, lock=True)][0] == "ctr"
        import psycopg

        with pytest.raises(psycopg.errors.LockNotAvailable):
            with other.transaction():
                store.get_metric(other, w.metrics["ctr"], lock=True)
        with other.transaction():
            other.execute("SELECT 1 FROM exp_metrics WHERE id = %s FOR NO KEY UPDATE",
                          (w.metrics["ctr"],))
            other.execute("SELECT 1 FROM exp_metrics WHERE id = %s FOR KEY SHARE",
                          (w.metrics["ctr"],))
