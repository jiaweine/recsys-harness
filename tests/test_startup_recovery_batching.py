import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest

from lingjing_harness.api_recovery import (
    install_startup_recovery_batching,
    run_lease_heartbeat_iteration,
)
from lingjing_harness.store import WorkspaceStore


def _snapshot(run_id, conversation_id):
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "recover",
        "status": "running",
        "events": [],
    }


def test_startup_recovery_expands_past_sixteen_without_stealing_live_foreign_lease(tmp_path):
    store = WorkspaceStore(tmp_path / "recovery-batching.db")

    expected = []
    for index in range(37):
        conversation = store.create_conversation(title=f"recover-{index}")
        run_id = f"recover-{index:02d}"
        assert store.reserve_run(
            run_id,
            conversation["id"],
            "recover",
            _snapshot(run_id, conversation["id"]),
            owner_id="worker-a:run:old-session",
            lease_seconds=60,
        )
        expected.append(run_id)

    # Model a crashed predecessor deterministically: all of its durable rows are
    # still active but their leases have expired before the successor recovers.
    with store._lock, store._connect() as connection:  # noqa: SLF001 - crash fixture
        connection.execute(
            "update runs set lease_until=0 where run_id like 'recover-%'"
        )
        connection.commit()

    foreign_conversation = store.create_conversation(title="foreign")
    assert store.reserve_run(
        "foreign-live",
        foreign_conversation["id"],
        "foreign",
        _snapshot("foreign-live", foreign_conversation["id"]),
        owner_id="worker-b:run:live-session",
        lease_seconds=60,
    )

    recovered = []

    async def original_recover():
        claimed = store.claim_recoverable_runs(
            owner_id="worker-a:run:new-session",
            lease_seconds=30,
            limit=16,
        )
        assert len(claimed) <= 1
        recovered.extend(row["run_id"] for row in claimed)

    core = SimpleNamespace(store=store, _recover_on_startup=original_recover)
    install_startup_recovery_batching(core)
    asyncio.run(core._recover_on_startup())

    assert len(recovered) == 37
    assert len(set(recovered)) == 37
    assert set(recovered) == set(expected)
    assert store.get_run("foreign-live")["owner_id"] == "worker-b:run:live-session"


def test_startup_recovery_isolates_poison_run_and_continues_with_healthy_peer(tmp_path, caplog):
    store = WorkspaceStore(tmp_path / "recovery-poison.db")
    healthy_conversation = store.create_conversation(title="healthy")
    poison_conversation = store.create_conversation(title="poison")

    assert store.reserve_run(
        "healthy-run",
        healthy_conversation["id"],
        "recover healthy",
        _snapshot("healthy-run", healthy_conversation["id"]),
        owner_id="worker-a:run:old-session",
        lease_seconds=60,
    )
    assert store.reserve_run(
        "poison-run",
        poison_conversation["id"],
        "recover poison",
        _snapshot("poison-run", poison_conversation["id"]),
        owner_id="worker-a:run:old-session",
        lease_seconds=60,
    )

    # Both predecessor leases are expired, and poison is deliberately first in
    # the durable recovery order.  A batch claim would lease both rows before the
    # poison failure could abort processing of the healthy one.
    with store._lock, store._connect() as connection:  # noqa: SLF001 - crash fixture
        connection.execute(
            """
            update runs
            set lease_until=0,
                updated_at=case run_id when 'poison-run' then 200.0 else 100.0 end
            where run_id in ('poison-run','healthy-run')
            """
        )
        connection.commit()

    attempts = []
    recovered = []
    core = SimpleNamespace(
        store=store,
        RUN_LOCK=threading.RLock(),
        RUNS={},
        _PERSIST_META={},
    )

    async def original_recover():
        claimed = store.claim_recoverable_runs(
            owner_id="worker-a:run:new-session",
            lease_seconds=30,
            limit=16,
        )
        if not claimed:
            return
        assert len(claimed) == 1
        saved = claimed[0]
        run_id = saved["run_id"]
        attempts.append(run_id)
        if run_id == "poison-run":
            # Model a failure after recovery has started populating process-local
            # state but before an executor task is successfully scheduled.
            with core.RUN_LOCK:
                core.RUNS[run_id] = {"status": "running"}
            core._PERSIST_META[run_id] = ("partial",)
            raise RuntimeError("malformed recovered checkpoint")

        snapshot = dict(saved["snapshot"])
        snapshot["status"] = "completed"
        assert store.save_run(
            run_id,
            saved["conversation_id"],
            saved["goal"],
            "completed",
            snapshot,
            owner_id="worker-a:run:new-session",
        ) == "completed"
        recovered.append(run_id)

    core._recover_on_startup = original_recover
    install_startup_recovery_batching(core)

    with caplog.at_level(logging.ERROR, logger="lingjing_harness.api_recovery"):
        asyncio.run(core._recover_on_startup())

    assert attempts == ["poison-run", "healthy-run"]
    assert recovered == ["healthy-run"]
    assert store.get_run("healthy-run")["status"] == "completed"
    poison = store.get_run("poison-run")
    assert poison["status"] == "running"
    assert poison["owner_id"] == "worker-a:run:new-session"
    assert poison["lease_until"] > 0
    assert "poison-run" not in core.RUNS
    assert "poison-run" not in core._PERSIST_META
    assert "durable run recovery failed for poison-run" in caplog.text


def test_startup_recovery_still_propagates_claim_infrastructure_failure():
    class BrokenClaimStore:
        def claim_recoverable_runs(self, **_kwargs):
            raise RuntimeError("database unavailable")

    store = BrokenClaimStore()

    async def original_recover():
        store.claim_recoverable_runs(
            owner_id="worker-a:run:new-session",
            lease_seconds=30,
            limit=16,
        )

    core = SimpleNamespace(store=store, _recover_on_startup=original_recover)
    install_startup_recovery_batching(core)

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(core._recover_on_startup())


def test_startup_recovery_batching_is_idempotent(tmp_path):
    store = WorkspaceStore(tmp_path / "recovery-idempotent.db")
    calls = []

    async def original_recover():
        calls.append("recover")

    core = SimpleNamespace(store=store, _recover_on_startup=original_recover)
    install_startup_recovery_batching(core)
    installed = core._recover_on_startup
    install_startup_recovery_batching(core)

    assert core._recover_on_startup is installed
    asyncio.run(core._recover_on_startup())
    assert calls == ["recover"]


def test_heartbeat_iteration_renews_local_runs_before_recovery_sweep():
    class RecordingStore:
        def __init__(self):
            self.renewals = []

        def renew_run_lease(self, run_id, owner_id, lease_seconds):
            self.renewals.append((run_id, owner_id, lease_seconds))
            return True

    store = RecordingStore()
    recoveries = []

    async def recover():
        # The local owner must be renewed before this process competes to claim
        # any expired peer work from the same durable database.
        assert store.renewals == [("local-run", "worker-a:run:new", 30.0)]
        recoveries.append("sweep")

    core = SimpleNamespace(
        store=store,
        RUN_LOCK=threading.RLock(),
        RUNS={
            "local-run": {"status": "running"},
            "finished-run": {"status": "completed"},
        },
        ACTIVE_RUN_STATUSES={"running", "interrupted", "cancel_requested"},
        WORKER_ID="worker-a:run:new",
        RUN_LEASE_SECONDS=30.0,
        _recover_on_startup=recover,
    )

    assert asyncio.run(run_lease_heartbeat_iteration(core)) is True
    assert recoveries == ["sweep"]


def test_recovery_sweep_failure_does_not_break_future_lease_heartbeats(caplog):
    class RecordingStore:
        def __init__(self):
            self.renewals = []

        def renew_run_lease(self, run_id, owner_id, lease_seconds):
            self.renewals.append((run_id, owner_id, lease_seconds))
            return True

    store = RecordingStore()
    recovery_attempts = []

    async def recover():
        recovery_attempts.append(len(recovery_attempts) + 1)
        if len(recovery_attempts) == 1:
            raise RuntimeError("malformed recovered checkpoint")

    core = SimpleNamespace(
        store=store,
        RUN_LOCK=threading.RLock(),
        RUNS={"local-run": {"status": "running"}},
        ACTIVE_RUN_STATUSES={"running", "interrupted", "cancel_requested"},
        WORKER_ID="worker-a:run:new",
        RUN_LEASE_SECONDS=30.0,
        _recover_on_startup=recover,
    )

    with caplog.at_level(logging.ERROR, logger="lingjing_harness.api_recovery"):
        assert asyncio.run(run_lease_heartbeat_iteration(core)) is False
        assert asyncio.run(run_lease_heartbeat_iteration(core)) is True

    assert recovery_attempts == [1, 2]
    assert store.renewals == [
        ("local-run", "worker-a:run:new", 30.0),
        ("local-run", "worker-a:run:new", 30.0),
    ]
    assert "expired run recovery sweep failed" in caplog.text
