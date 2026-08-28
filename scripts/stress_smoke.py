from __future__ import annotations

import argparse
import json
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
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


def _process_write(
    database: str,
    shared_conversation_id: str,
    worker: int,
    iterations: int,
) -> int:
    store = WorkspaceStore(database)
    for index in range(iterations):
        store.add_message(
            shared_conversation_id,
            "assistant",
            f"process={worker} iteration={index}",
            {"process": worker, "iteration": index},
        )
    return iterations


def _process_rate_limit(
    database: str,
    worker: int,
    attempts: int,
    limit: int,
    now: float,
) -> int:
    store = WorkspaceStore(database)
    return sum(
        store.consume_rate_limit(
            "stress:process-shared-client",
            limit=limit,
            window_seconds=60,
            now=now,
        )
        for _ in range(attempts)
    )


def _process_claim(database: str, worker: int, now: float) -> list[str]:
    store = WorkspaceStore(database)
    claimed = store.claim_recoverable_runs(
        owner_id=f"process-owner-{worker}",
        lease_seconds=60,
        limit=8,
        now=now,
    )
    return [str(row["run_id"]) for row in claimed]


def run_stress(workers: int, iterations: int, processes: int = 0) -> dict[str, float | int]:
    workers = max(1, int(workers))
    iterations = max(1, int(iterations))
    processes = max(0, int(processes))
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="xushu-stress-") as directory:
        database = str(Path(directory) / "workspace.db")
        store = WorkspaceStore(database)
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

        thread_shared_messages = workers * iterations
        shared_messages = store.list_messages(shared["id"])
        if len(shared_messages) != thread_shared_messages:
            raise AssertionError(
                f"shared thread write count mismatch: {len(shared_messages)} != {thread_shared_messages}"
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
            raise AssertionError(f"thread rate limit lost atomicity: {allowed} != {limit}")
        operations += attempts

        process_shared_messages = 0
        process_attempts = 0
        process_allowed = 0
        claim_winners = 0
        if processes:
            process_iterations = max(4, iterations // 2)
            with ProcessPoolExecutor(max_workers=processes) as executor:
                futures = [
                    executor.submit(
                        _process_write,
                        database,
                        shared["id"],
                        worker,
                        process_iterations,
                    )
                    for worker in range(processes)
                ]
                process_shared_messages = sum(future.result() for future in futures)
            operations += process_shared_messages

            expected_shared = thread_shared_messages + process_shared_messages
            durable_shared = store.list_messages(shared["id"])
            if len(durable_shared) != expected_shared:
                raise AssertionError(
                    f"cross-process write count mismatch: {len(durable_shared)} != {expected_shared}"
                )
            operations += 1

            process_attempts_each = max(8, iterations)
            process_attempts = processes * process_attempts_each
            process_limit = max(1, process_attempts // 3)
            with ProcessPoolExecutor(max_workers=processes) as executor:
                futures = [
                    executor.submit(
                        _process_rate_limit,
                        database,
                        worker,
                        process_attempts_each,
                        process_limit,
                        fixed_now + 120.0,
                    )
                    for worker in range(processes)
                ]
                process_allowed = sum(future.result() for future in futures)
            if process_allowed != process_limit:
                raise AssertionError(
                    f"process rate limit lost atomicity: {process_allowed} != {process_limit}"
                )
            operations += process_attempts

            claim_conversation = store.create_conversation("recoverable-claim", "audit")
            claim_run_id = "stress-cross-process-claim"
            claim_snapshot = {
                "run_id": claim_run_id,
                "conversation_id": claim_conversation["id"],
                "goal": "claim",
                "status": "running",
                "events": [],
                "result": None,
                "created_at": fixed_now,
                "updated_at": fixed_now,
            }
            store.save_run(
                claim_run_id,
                claim_conversation["id"],
                "claim",
                "running",
                claim_snapshot,
                owner_id=None,
            )
            with ProcessPoolExecutor(max_workers=processes) as executor:
                futures = [
                    executor.submit(_process_claim, database, worker, fixed_now + 240.0)
                    for worker in range(processes)
                ]
                claim_results = [future.result() for future in futures]
            claim_winners = sum(claim_run_id in rows for rows in claim_results)
            if claim_winners != 1:
                raise AssertionError(f"recoverable run had {claim_winners} concurrent owners")
            operations += processes + 2

    elapsed = max(time.perf_counter() - started, 1e-9)
    return {
        "workers": workers,
        "processes": processes,
        "iterations_per_worker": iterations,
        "thread_shared_messages": thread_shared_messages,
        "process_shared_messages": process_shared_messages,
        "thread_rate_limit_attempts": attempts,
        "thread_rate_limit_allowed": limit,
        "process_rate_limit_attempts": process_attempts,
        "process_rate_limit_allowed": process_allowed,
        "recoverable_claim_winners": claim_winners,
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
    parser.add_argument(
        "--processes",
        type=int,
        default=0,
        help="Also exercise independent process/store instances against the same SQLite database.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            run_stress(args.workers, args.iterations, args.processes),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
