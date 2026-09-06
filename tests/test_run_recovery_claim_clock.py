import asyncio
import time
from types import SimpleNamespace

import lingjing_harness.api_recovery as recovery_module
from lingjing_harness.api_recovery import install_startup_recovery_batching
from lingjing_harness.store import WorkspaceStore


def _snapshot(run_id: str, conversation_id: str, started: float) -> dict:
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "recover",
        "status": "running",
        "events": [],
        "created_at": started,
        "updated_at": started,
    }


def test_recovery_claim_separates_eligibility_from_fresh_lease_clock(tmp_path):
    store = WorkspaceStore(tmp_path / "recovery-claim-clock.db")
    started = time.time()

    expired_conversation = store.create_conversation(title="expired")
    live_conversation = store.create_conversation(title="live")
    assert store.reserve_run(
        "expired-run",
        expired_conversation["id"],
        "recover expired",
        _snapshot("expired-run", expired_conversation["id"], started),
        owner_id="old-worker",
        lease_seconds=1,
    )
    assert store.reserve_run(
        "live-run",
        live_conversation["id"],
        "keep live",
        _snapshot("live-run", live_conversation["id"], started),
        owner_id="live-worker",
        lease_seconds=50,
    )

    eligibility_now = started + 2.0
    lease_now = started + 100.0
    claimed = store.claim_recoverable_runs(
        owner_id="new-worker",
        lease_seconds=30,
        now=eligibility_now,
        lease_now=lease_now,
    )

    assert [row["run_id"] for row in claimed] == ["expired-run"]
    expired = store.get_run("expired-run")
    assert expired["owner_id"] == "new-worker"
    assert expired["lease_until"] == lease_now + 30.0

    # The fresh lease clock must not broaden the sweep's eligibility boundary.
    live = store.get_run("live-run")
    assert live["owner_id"] == "live-worker"
    assert live["lease_until"] > eligibility_now


def test_batching_freezes_eligibility_but_refreshes_each_claim_lease(monkeypatch):
    class RecordingStore:
        def __init__(self):
            self.calls = []

        def claim_recoverable_runs(
            self,
            *,
            owner_id,
            lease_seconds,
            limit=20,
            now=None,
            lease_now=None,
        ):
            self.calls.append(
                {
                    "owner_id": owner_id,
                    "lease_seconds": lease_seconds,
                    "limit": limit,
                    "now": now,
                    "lease_now": lease_now,
                }
            )
            if len(self.calls) == 1:
                return [{"run_id": "recover-me"}]
            return []

    clock = iter([100.0, 200.0, 300.0])
    monkeypatch.setattr(recovery_module.time, "time", lambda: next(clock))
    store = RecordingStore()

    async def original_recover():
        store.claim_recoverable_runs(
            owner_id="worker-a:run:new-session",
            lease_seconds=30.0,
            limit=16,
        )

    core = SimpleNamespace(store=store, _recover_on_startup=original_recover)
    install_startup_recovery_batching(core)
    asyncio.run(core._recover_on_startup())

    assert store.calls == [
        {
            "owner_id": "worker-a:run:new-session",
            "lease_seconds": 30.0,
            "limit": 1,
            "now": 100.0,
            "lease_now": 200.0,
        },
        {
            "owner_id": "worker-a:run:new-session",
            "lease_seconds": 30.0,
            "limit": 1,
            "now": 100.0,
            "lease_now": 300.0,
        },
    ]
