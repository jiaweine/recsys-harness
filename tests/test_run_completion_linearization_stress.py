from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import lingjing_harness.api as api_module
from lingjing_harness.store import WorkspaceStore


_ROUNDS = 96


def _snapshot(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "cancel completion linearization stress",
        "status": "running",
        "events": [],
        "result": None,
        "attachment_ids": [],
        "attachments": [],
        "allow_network": False,
        "catalog_revision": api_module.CATALOG_REVISION,
        "created_at": now,
        "updated_at": now,
    }


def _publish(
    store: WorkspaceStore,
    conversation_id: str,
    run_id: str,
    gate: threading.Barrier,
    delay: float,
):
    gate.wait(timeout=5.0)
    if delay:
        time.sleep(delay)
    return store.publish_run_completion(
        conversation_id,
        "stress answer",
        {
            "job_id": run_id,
            "answer": "stress answer",
            "events": [],
            "catalog_revision": api_module.CATALOG_REVISION,
        },
        owner_id=api_module.WORKER_ID,
    )


def _cancel(
    remote: WorkspaceStore,
    run_id: str,
    gate: threading.Barrier,
    delay: float,
) -> tuple[str, str]:
    gate.wait(timeout=5.0)
    if delay:
        time.sleep(delay)
    try:
        return "returned", remote.request_cancel(run_id)
    except RuntimeError as exc:
        # Completion is the only legal terminal state that can beat this cancel.
        return "terminal", str(exc)


def test_cancel_and_assistant_publication_linearize_under_cross_connection_stress(tmp_path):
    store = WorkspaceStore(tmp_path / "run-completion-linearization-stress.db")
    remote = WorkspaceStore(store.path)
    outcomes = {"completed": 0, "cancel_requested": 0}

    # Alternate a small scheduling bias while still starting both contenders at
    # the same barrier.  The unbiased third of the rounds exercises natural lock
    # contention; the two biased thirds guarantee that both legal transaction
    # orderings are exercised instead of trusting the CI scheduler to vary.
    with ThreadPoolExecutor(max_workers=2) as executor:
        for index in range(_ROUNDS):
            conversation = store.create_conversation(f"linearization-{index}", "search")
            run_id = f"job-linearization-{index}"
            row = _snapshot(run_id, conversation["id"])
            assert store.reserve_run(
                run_id,
                conversation["id"],
                row["goal"],
                row,
                owner_id=api_module.WORKER_ID,
                lease_seconds=api_module.RUN_LEASE_SECONDS,
            )

            mode = index % 3
            publish_delay = 0.01 if mode == 0 else 0.0
            cancel_delay = 0.01 if mode == 1 else 0.0
            gate = threading.Barrier(3)
            publish_future = executor.submit(
                _publish,
                store,
                conversation["id"],
                run_id,
                gate,
                publish_delay,
            )
            cancel_future = executor.submit(
                _cancel,
                remote,
                run_id,
                gate,
                cancel_delay,
            )
            gate.wait(timeout=5.0)

            publish_status, published_message = publish_future.result(timeout=5.0)
            cancel_kind, cancel_value = cancel_future.result(timeout=5.0)
            durable = store.get_run(run_id)
            assistants = [
                message
                for message in store.list_messages(conversation["id"])
                if message["role"] == "assistant"
                and message.get("payload", {}).get("job_id") == run_id
            ]

            status = durable["status"]
            assert status in outcomes
            outcomes[status] += 1

            if status == "completed":
                assert publish_status == "completed"
                assert published_message is not None
                assert cancel_kind == "terminal"
                assert cancel_value == "completed"
                assert len(assistants) == 1
                assert assistants[0]["id"] == published_message["id"]
                assert durable["result"]["job_id"] == run_id
                assert durable["message"]["id"] == published_message["id"]
                assert durable["owner_id"] is None
                assert durable["lease_until"] is None
            else:
                assert status == "cancel_requested"
                assert cancel_kind == "returned"
                assert cancel_value == "cancel_requested"
                assert publish_status == "cancel_requested"
                assert published_message is None
                assert assistants == []
                assert durable["owner_id"] == api_module.WORKER_ID

            store.delete_run(run_id)

    assert outcomes["completed"] > 0
    assert outcomes["cancel_requested"] > 0
    assert sum(outcomes.values()) == _ROUNDS
