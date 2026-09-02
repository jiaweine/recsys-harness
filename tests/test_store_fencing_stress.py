from __future__ import annotations

import pytest

import lingjing_harness.store as store_module
from lingjing_harness.store import WorkspaceStore


def _snapshot(run_id: str, conversation_id: str, *, now: float) -> dict:
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "stress",
        "status": "running",
        "events": [],
        "created_at": now,
        "updated_at": now,
    }


def test_expired_workspace_update_cannot_commit_after_new_run_enters(tmp_path, monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: clock["now"])
    path = tmp_path / "workspace-expired-fence.db"
    updater = WorkspaceStore(path)
    runner = WorkspaceStore(path)
    assert updater.ensure_workspace_revision("rev-a") == "rev-a"
    conversation = runner.create_conversation()

    assert updater.begin_workspace_update("updater", lease_seconds=5.0) is True
    clock["now"] = 1_006.0
    snapshot = _snapshot("run-after-expiry", conversation["id"], now=clock["now"])
    assert runner.reserve_run(
        "run-after-expiry",
        conversation["id"],
        "stress",
        snapshot,
        owner_id="runner",
        lease_seconds=30.0,
    ) is True

    assert updater.commit_workspace_revision("updater", "rev-b") is False
    assert updater.workspace_revision() == "rev-a"
    assert runner.run_status("run-after-expiry") == "running"


def test_old_run_owner_never_regains_write_authority_after_takeover_lease_expires(
    tmp_path, monkeypatch
):
    clock = {"now": 2_000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: clock["now"])
    path = tmp_path / "owner-fence-expiry.db"
    old = WorkspaceStore(path)
    new = WorkspaceStore(path)
    conversation = old.create_conversation()
    snapshot = _snapshot("run-owner-fence", conversation["id"], now=clock["now"])
    assert old.reserve_run(
        "run-owner-fence",
        conversation["id"],
        "stress",
        snapshot,
        owner_id="old-owner",
        lease_seconds=2.0,
    )

    clock["now"] = 2_003.0
    claimed = new.claim_recoverable_runs(
        owner_id="new-owner", lease_seconds=2.0, now=clock["now"]
    )
    assert [row["run_id"] for row in claimed] == ["run-owner-fence"]

    clock["now"] = 2_006.0
    stale = {**snapshot, "status": "completed", "answer": "stale", "updated_at": 2_006.0}
    assert old.save_run(
        "run-owner-fence",
        conversation["id"],
        "stress",
        "completed",
        stale,
        owner_id="old-owner",
        lease_seconds=30.0,
    ) == "running"
    current = new.get_run("run-owner-fence")
    assert current["status"] == "running"
    assert current["owner_id"] == "new-owner"


def test_ownerless_stale_write_cannot_bypass_owned_active_run(tmp_path, monkeypatch):
    clock = {"now": 3_000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: clock["now"])
    store = WorkspaceStore(tmp_path / "ownerless-fence.db")
    conversation = store.create_conversation()
    snapshot = _snapshot("run-ownerless", conversation["id"], now=clock["now"])
    assert store.reserve_run(
        "run-ownerless",
        conversation["id"],
        "stress",
        snapshot,
        owner_id="real-owner",
        lease_seconds=2.0,
    )

    clock["now"] = 3_010.0
    assert store.save_run(
        "run-ownerless",
        conversation["id"],
        "stress",
        "failed",
        {**snapshot, "status": "failed", "updated_at": clock["now"]},
        owner_id=None,
    ) == "running"
    current = store.get_run("run-ownerless")
    assert current["status"] == "running"
    assert current["owner_id"] == "real-owner"


def test_future_snapshot_timestamp_cannot_poison_run_lease_or_recovery(tmp_path, monkeypatch):
    clock = {"now": 4_000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: clock["now"])
    path = tmp_path / "future-run-clock.db"
    owner = WorkspaceStore(path)
    recovery = WorkspaceStore(path)
    conversation = owner.create_conversation()
    snapshot = _snapshot("run-future-clock", conversation["id"], now=clock["now"])
    assert owner.reserve_run(
        "run-future-clock",
        conversation["id"],
        "stress",
        snapshot,
        owner_id="owner",
        lease_seconds=5.0,
    )

    clock["now"] = 4_001.0
    poisoned = {**snapshot, "updated_at": 4_001.0 + 365.0 * 86400.0}
    assert owner.save_run(
        "run-future-clock",
        conversation["id"],
        "stress",
        "running",
        poisoned,
        owner_id="owner",
        lease_seconds=5.0,
    ) == "running"
    durable = owner.get_run("run-future-clock")
    assert durable["updated_at"] == pytest.approx(4_001.0)
    assert durable["lease_until"] == pytest.approx(4_006.0)

    clock["now"] = 4_007.0
    claimed = recovery.claim_recoverable_runs(
        owner_id="recovery", lease_seconds=5.0, now=clock["now"]
    )
    assert [row["run_id"] for row in claimed] == ["run-future-clock"]


def test_heartbeat_repairs_legacy_future_skewed_row_clock(tmp_path, monkeypatch):
    clock = {"now": 5_000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: clock["now"])
    store = WorkspaceStore(tmp_path / "legacy-future-lease.db")
    conversation = store.create_conversation()
    snapshot = _snapshot("run-legacy-future", conversation["id"], now=clock["now"])
    assert store.reserve_run(
        "run-legacy-future",
        conversation["id"],
        "stress",
        snapshot,
        owner_id="owner",
        lease_seconds=5.0,
    )

    legacy_future = 5_000.0 + 365.0 * 86400.0
    with store._lock, store._connect() as connection:  # noqa: SLF001 - corruption fixture
        connection.execute(
            "update runs set updated_at=?,lease_until=? where run_id=?",
            (legacy_future, legacy_future + 5.0, "run-legacy-future"),
        )
        connection.commit()

    clock["now"] = 5_001.0
    assert store.renew_run_lease("run-legacy-future", "owner", 5.0) is True
    with store._connect() as connection:  # noqa: SLF001 - contract inspection
        row = connection.execute(
            "select updated_at,lease_until from runs where run_id=?",
            ("run-legacy-future",),
        ).fetchone()
    assert float(row["updated_at"]) == pytest.approx(5_001.0)
    assert float(row["lease_until"]) == pytest.approx(5_006.0)


def test_recovery_reanchors_dead_legacy_future_lease_before_claiming(tmp_path, monkeypatch):
    clock = {"now": 7_000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: clock["now"])
    path = tmp_path / "legacy-dead-owner-future-lease.db"
    owner = WorkspaceStore(path)
    recovery = WorkspaceStore(path)
    conversation = owner.create_conversation()
    snapshot = _snapshot("run-dead-future", conversation["id"], now=clock["now"])
    assert owner.reserve_run(
        "run-dead-future",
        conversation["id"],
        "stress",
        snapshot,
        owner_id="dead-owner",
        lease_seconds=5.0,
    )

    future = 7_000.0 + 365.0 * 86400.0
    with owner._lock, owner._connect() as connection:  # noqa: SLF001 - corruption fixture
        connection.execute(
            "update runs set updated_at=?,lease_until=? where run_id=?",
            (future, future + 5.0, "run-dead-future"),
        )
        connection.commit()

    clock["now"] = 7_001.0
    assert recovery.claim_recoverable_runs(
        owner_id="recovery", lease_seconds=5.0, now=clock["now"]
    ) == []
    with recovery._connect() as connection:  # noqa: SLF001 - contract inspection
        row = connection.execute(
            "select updated_at,lease_until,owner_id from runs where run_id=?",
            ("run-dead-future",),
        ).fetchone()
    assert float(row["updated_at"]) == pytest.approx(7_001.0)
    assert float(row["lease_until"]) == pytest.approx(7_006.0)
    assert row["owner_id"] == "dead-owner"

    clock["now"] = 7_007.0
    claimed = recovery.claim_recoverable_runs(
        owner_id="recovery", lease_seconds=5.0, now=clock["now"]
    )
    assert [row["run_id"] for row in claimed] == ["run-dead-future"]
    assert recovery.get_run("run-dead-future")["owner_id"] == "recovery"


def test_workspace_update_clock_rollback_preserves_lease_duration_then_recovers(
    tmp_path, monkeypatch
):
    clock = {"now": 8_000.0}
    monkeypatch.setattr(store_module.time, "time", lambda: clock["now"])
    path = tmp_path / "workspace-future-lease.db"
    updater = WorkspaceStore(path)
    runner = WorkspaceStore(path)
    assert updater.ensure_workspace_revision("rev-a") == "rev-a"
    conversation = runner.create_conversation()

    future = 8_000.0 + 365.0 * 86400.0
    clock["now"] = future
    assert updater.begin_workspace_update("updater", lease_seconds=5.0) is True

    clock["now"] = 8_000.0
    assert updater.workspace_update_active(now=clock["now"]) is True
    with updater._connect() as connection:  # noqa: SLF001 - contract inspection
        row = connection.execute(
            "select update_until,updated_at from workspace_state where id=1"
        ).fetchone()
    assert float(row["updated_at"]) == pytest.approx(8_000.0)
    assert float(row["update_until"]) == pytest.approx(8_005.0)

    clock["now"] = 8_001.0
    assert runner.reserve_run(
        "run-during-reanchored-update",
        conversation["id"],
        "stress",
        _snapshot("run-during-reanchored-update", conversation["id"], now=clock["now"]),
        owner_id="runner",
        lease_seconds=30.0,
    ) is False

    clock["now"] = 8_006.0
    assert runner.reserve_run(
        "run-after-reanchored-update",
        conversation["id"],
        "stress",
        _snapshot("run-after-reanchored-update", conversation["id"], now=clock["now"]),
        owner_id="runner",
        lease_seconds=30.0,
    ) is True
    assert updater.commit_workspace_revision("updater", "rev-b") is False
    assert updater.workspace_revision() == "rev-a"


def test_rate_limit_clock_rollback_repairs_window_without_resetting_count(tmp_path):
    store = WorkspaceStore(tmp_path / "rate-clock-rollback.db")
    key = "task:clock-skew"
    assert store.consume_rate_limit(key, limit=2, window_seconds=60.0, now=6_000.0) is True
    assert store.consume_rate_limit(key, limit=2, window_seconds=60.0, now=6_001.0) is True
    assert store.consume_rate_limit(key, limit=2, window_seconds=60.0, now=6_002.0) is False

    future = 6_000.0 + 365.0 * 86400.0
    assert store.consume_rate_limit(key, limit=2, window_seconds=60.0, now=future) is True

    assert store.consume_rate_limit(key, limit=2, window_seconds=60.0, now=6_010.0) is True
    assert store.consume_rate_limit(key, limit=2, window_seconds=60.0, now=6_011.0) is False
    with store._connect() as connection:  # noqa: SLF001 - contract inspection
        row = connection.execute(
            "select window_start,count from rate_limits where scope_key=?", (key,)
        ).fetchone()
    assert float(row["window_start"]) == pytest.approx(6_010.0)
    assert int(row["count"]) == 2
    assert store.consume_rate_limit(key, limit=2, window_seconds=60.0, now=6_071.0) is True
