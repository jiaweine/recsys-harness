from __future__ import annotations

import argparse
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lingjing_harness.store import WorkspaceStore


def _exercise_worker(
    store: WorkspaceStore,
    worker: int,
    iterations: int,
    shared_conversation_id: str,
) -> int:
    conversation = store.create_conversation(f"stress-{worker}", "audit")
    operations = 1

    for index in range(iterations):
        role = "user" if index % 2 == 0 else "assistant"
        store.add_message(
            conversation["id"],
            role,
            f"worker={worker} iteration={index}",
            {"worker": worker, "iteration": index},
        )
        store.add_message(
            shared_conversation_id,
            "assistant",
            f"shared worker={worker} iteration={index}",
            {"worker": worker, "iteration": index},
        )
        operations += 2
        if index % 5 == 0:
            row = store.get_conversation(conversation["id"])
            if not row["messages"]:
                raise AssertionError("conversation read lost committed messages")
            operations += 1

    run_id = f"stress-run-{worker}"
    now = time.time()
    running = {
        "run_id": run_id,
        "conversation_id": conversation["id"],
        "goal": "stress",
        "status": "running",
        "events": [{"progress": 10}],
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    if not store.reserve_run(
        run_id,
        conversation["id"],
        "stress",
        running,
        owner_id=f"worker-{worker}",
        lease_seconds=30,
    ):
        raise AssertionError("failed to reserve an isolated stress run")
    operations += 1

    for _ in range(4):
        if not store.renew_run_lease(run_id, f"worker-{worker}", 30):
            raise AssertionError("run lease renewal failed under contention")
        if store.run_status(run_id) != "running":
            raise AssertionError("active run status changed unexpectedly")
        operations += 2

    completed = {
        **running,
        "status": "completed",
        "events": [{"progress": 100}],
        "result": {"worker": worker, "ok": True},
        "updated_at": time.time(),
    }
    status = store.save_run(
        run_id,
        conversation["id"],
        "stress",
        "completed",
        completed,
        owner_id=f"worker-{worker}",
    )
    if status != "completed":
        raise AssertionError(f"terminal save returned {status!r}")
    operations += 1

    late = store.save_run(
        run_id,
        conversation["id"],
        "stress",
        "running",
        {**running, "updated_at": time.time()},
        owner_id=f"worker-{worker}",
    )
    durable = store.get_run(run_id)
    if late != "completed" or durable["status"] != "completed":
        raise AssertionError("late active checkpoint resurrected a terminal run")
    if durable.get("result", {}).get("worker") != worker:
        raise AssertionError("terminal result was overwritten by a late checkpoint")
    operations += 2
    return operations


def run_stress(workers: int, iterations: int) -> dict[str, float | int]:
    workers = max(1, int(workers))
    iterations = max(1, int(iterations))
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="xushu-stress-") as directory:
        store = WorkspaceStore(Path(directory) / "workspace.db")
        shared = store.create_conversation("shared-contention", "audit")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _exercise_worker,
                    store,
                    worker,
                    iterations,
                    shared["id"],
                )
                for worker in range(workers)
            ]
            operations = 1 + sum(future.result() for future in futures)

        shared_messages = store.list_messages(shared["id"])
        expected_messages = workers * iterations
        if len(shared_messages) != expected_messages:
            raise AssertionError(
                f"shared write count mismatch: {len(shared_messages)} != {expected_messages}"
            )
        operations += 1

        attempts = max(workers * iterations, workers)
        limit = max(1, attempts // 3)
        fixed_now = 1_000_000.0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            decisions = list(
                executor.map(
                    lambda _: store.consume_rate_limit(
                        "stress:shared-client",
                        limit=limit,
                        window_seconds=60,
                        now=fixed_now,
                    ),
                    range(attempts),
                )
            )
        allowed = sum(bool(value) for value in decisions)
        if allowed != limit:
            raise AssertionError(f"rate limit lost atomicity: {allowed} != {limit}")
        operations += attempts

    elapsed = max(time.perf_counter() - started, 1e-9)
    return {
        "workers": workers,
        "iterations_per_worker": iterations,
        "shared_messages": workers * iterations,
        "rate_limit_attempts": attempts,
        "rate_limit_allowed": limit,
        "operations": operations,
        "elapsed_seconds": round(elapsed, 4),
        "operations_per_second": round(operations / elapsed, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic contention smoke test for Xushu durable workspace primitives."
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(run_stress(args.workers, args.iterations), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
