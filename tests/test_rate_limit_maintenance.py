import concurrent.futures
import sqlite3

from lingjing_harness.rate_limit_maintenance import install_rate_limit_maintenance
from lingjing_harness.store import WorkspaceStore


def _rate_limit_keys(path):
    with sqlite3.connect(path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "select scope_key from rate_limits order by scope_key"
            ).fetchall()
        }


def test_stale_rate_limit_keys_are_cleaned_on_a_deterministic_schedule(tmp_path):
    path = tmp_path / "rate-limit-maintenance.db"
    store = WorkspaceStore(path)
    install_rate_limit_maintenance(
        store,
        interval_seconds=3600,
        retention_seconds=86400,
    )

    assert store.consume_rate_limit("login:stale", limit=10, window_seconds=60, now=100)
    # The next scheduled maintenance pass is at t=3700.  At t=4000 the first
    # key is still younger than the 24h retention horizon and must survive.
    assert store.consume_rate_limit("login:recent", limit=10, window_seconds=60, now=4000)
    assert _rate_limit_keys(path) == {"login:recent", "login:stale"}

    # t=90000 is deliberately unrelated to the old modulo-101 trigger.  The
    # stale key is older than the retention horizon, while the recent key is not.
    assert store.consume_rate_limit("login:current", limit=10, window_seconds=60, now=90000)
    assert _rate_limit_keys(path) == {"login:current", "login:recent"}


def test_rate_limit_maintenance_creates_cleanup_index(tmp_path):
    path = tmp_path / "rate-limit-index.db"
    store = WorkspaceStore(path)
    install_rate_limit_maintenance(store)

    assert store.consume_rate_limit("task:client", limit=2, window_seconds=60, now=100)

    with sqlite3.connect(path) as connection:
        indexes = {
            row[1]
            for row in connection.execute("pragma index_list(rate_limits)").fetchall()
        }
    assert "idx_rate_limits_updated_at" in indexes


def test_maintenance_preserves_shared_counter_semantics(tmp_path):
    path = tmp_path / "rate-limit-shared.db"
    one = WorkspaceStore(path)
    two = WorkspaceStore(path)
    install_rate_limit_maintenance(one)
    install_rate_limit_maintenance(two)

    assert one.consume_rate_limit("login:client", limit=2, window_seconds=60, now=100) is True
    assert two.consume_rate_limit("login:client", limit=2, window_seconds=60, now=101) is True
    assert one.consume_rate_limit("login:client", limit=2, window_seconds=60, now=102) is False
    assert two.consume_rate_limit("login:client", limit=2, window_seconds=60, now=161) is True


def test_maintenance_clock_rollback_rearms_future_gc_deadline(tmp_path):
    path = tmp_path / "rate-limit-maintenance-clock.db"
    store = WorkspaceStore(path)
    install_rate_limit_maintenance(
        store,
        interval_seconds=60,
        retention_seconds=120,
    )

    assert store.consume_rate_limit("task:normal", limit=10, window_seconds=60, now=100)
    # A host clock jump far into the future advances the process-local GC
    # deadline.  Without rollback repair, returning to the normal clock would
    # suppress maintenance until that future deadline was reached again.
    assert store.consume_rate_limit("task:future", limit=10, window_seconds=60, now=10_000)

    with sqlite3.connect(path) as connection:
        connection.execute(
            "insert or replace into rate_limits(scope_key,window_start,count,updated_at) values(?,?,?,?)",
            ("task:stale-after-jump", 0.0, 1, 0.0),
        )
        connection.commit()
    assert "task:stale-after-jump" in _rate_limit_keys(path)

    assert store.consume_rate_limit("task:recovered", limit=10, window_seconds=60, now=200)
    assert "task:stale-after-jump" not in _rate_limit_keys(path)


def test_install_is_idempotent(tmp_path):
    store = WorkspaceStore(tmp_path / "rate-limit-idempotent.db")
    install_rate_limit_maintenance(store)
    first = store.consume_rate_limit
    install_rate_limit_maintenance(store)

    assert store.consume_rate_limit is first



def test_saturated_rate_limit_denial_uses_read_only_fast_path(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "rate-limit-fast-deny.db")
    scope = "task:fast-deny"

    assert store.consume_rate_limit(scope, limit=2, window_seconds=60, now=100.0)
    assert store.consume_rate_limit(scope, limit=2, window_seconds=60, now=100.1)

    statements: list[str] = []
    original_connect = store._connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_connect", traced_connect)

    assert store.consume_rate_limit(
        scope,
        limit=2,
        window_seconds=60,
        now=100.2,
    ) is False

    normalized = [statement.lower().strip() for statement in statements]
    assert any(statement.startswith("select window_start,count,updated_at") for statement in normalized)
    assert not any(statement.startswith("begin immediate") for statement in normalized)
    assert not any(statement.startswith("update rate_limits") for statement in normalized)


def test_saturated_long_window_refreshes_retention_timestamp_periodically(tmp_path):
    path = tmp_path / "rate-limit-retention-touch.db"
    store = WorkspaceStore(path)
    scope = "task:long-window"

    assert store.consume_rate_limit(scope, limit=1, window_seconds=3600, now=100.0)
    assert store.consume_rate_limit(scope, limit=1, window_seconds=3600, now=120.0) is False

    with sqlite3.connect(path) as connection:
        before = connection.execute(
            "select updated_at from rate_limits where scope_key=?",
            (scope,),
        ).fetchone()[0]
    assert before == 100.0

    assert store.consume_rate_limit(scope, limit=1, window_seconds=3600, now=161.0) is False

    with sqlite3.connect(path) as connection:
        refreshed = connection.execute(
            "select updated_at from rate_limits where scope_key=?",
            (scope,),
        ).fetchone()[0]
    assert refreshed == 161.0

    assert store.consume_rate_limit(scope, limit=1, window_seconds=3600, now=162.0) is False
    with sqlite3.connect(path) as connection:
        unchanged = connection.execute(
            "select updated_at from rate_limits where scope_key=?",
            (scope,),
        ).fetchone()[0]
    assert unchanged == 161.0


def test_rate_limit_clock_rollback_bypasses_fast_deny_and_repairs_window(tmp_path):
    path = tmp_path / "rate-limit-clock-repair.db"
    store = WorkspaceStore(path)
    scope = "task:clock-repair"

    with sqlite3.connect(path) as connection:
        connection.execute(
            "insert into rate_limits(scope_key,window_start,count,updated_at) values(?,?,?,?)",
            (scope, 10_000.0, 5, 10_000.0),
        )
        connection.commit()

    assert store.consume_rate_limit(
        scope,
        limit=5,
        window_seconds=60,
        now=200.0,
    ) is False

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "select window_start,count,updated_at from rate_limits where scope_key=?",
            (scope,),
        ).fetchone()
    assert row == (200.0, 5, 200.0)


def test_cross_store_concurrent_rate_limit_allows_exact_shared_budget(tmp_path):
    path = tmp_path / "rate-limit-concurrent-budget.db"
    stores = [WorkspaceStore(path) for _ in range(8)]
    scope = "task:shared-budget"
    limit = 5

    def consume(index: int) -> bool:
        return stores[index % len(stores)].consume_rate_limit(
            scope,
            limit=limit,
            window_seconds=60,
            now=500.0,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(consume, range(24)))

    assert sum(bool(value) for value in results) == limit
    assert results.count(False) == 24 - limit
