import asyncio
import threading
from types import SimpleNamespace

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
        recovered.extend(row["run_id"] for row in claimed)

    core = SimpleNamespace(store=store, _recover_on_startup=original_recover)
    install_startup_recovery_batching(core)
    asyncio.run(core._recover_on_startup())

    assert len(recovered) == 37
    assert len(set(recovered)) == 37
    assert set(recovered) == set(expected)
    assert store.get_run("foreign-live")["owner_id"] == "worker-b:run:live-session"


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

    asyncio.run(run_lease_heartbeat_iteration(core))

    assert recoveries == ["sweep"]
