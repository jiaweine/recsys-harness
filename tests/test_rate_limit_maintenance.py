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


def test_install_is_idempotent(tmp_path):
    store = WorkspaceStore(tmp_path / "rate-limit-idempotent.db")
    install_rate_limit_maintenance(store)
    first = store.consume_rate_limit
    install_rate_limit_maintenance(store)

    assert store.consume_rate_limit is first
