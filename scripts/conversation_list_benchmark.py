from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from lingjing_harness.store import WorkspaceStore


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _seed(store: WorkspaceStore, conversations: int, active_runs: int) -> None:
    now = 1_000_000.0
    conversation_rows = [
        (
            f"cv-bench-{index:07d}",
            f"Conversation {index}",
            "audit",
            now + index,
            now + index,
        )
        for index in range(conversations)
    ]
    run_rows = []
    for index in range(active_runs):
        conversation_id = f"cv-bench-{conversations - 1 - index:07d}"
        run_id = f"job-bench-{index:07d}"
        snapshot = {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "goal": "benchmark",
            "status": "running",
            "events": [],
            "created_at": now + conversations + index,
            "updated_at": now + conversations + index,
        }
        run_rows.append(
            (
                run_id,
                conversation_id,
                "benchmark",
                "running",
                json.dumps(snapshot),
                now + conversations + index,
                now + conversations + index,
                "bench-owner",
                now + conversations + index + 30,
            )
        )
    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            "insert into conversations(id,title,scene,created_at,updated_at) values(?,?,?,?,?)",
            conversation_rows,
        )
        connection.executemany(
            """
            insert into runs(
              run_id,conversation_id,goal,status,snapshot,created_at,updated_at,owner_id,lease_until
            ) values(?,?,?,?,?,?,?,?,?)
            """,
            run_rows,
        )
        connection.commit()


def run_benchmark(*, conversations: int, active_runs: int, repeats: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xushu-conversation-list-") as directory:
        store = WorkspaceStore(Path(directory) / "workspace.db")
        _seed(store, conversations, active_runs)

        # Warm page cache.
        rows = store.list_conversations()
        active = store.active_conversation_ids()
        if not rows or len(active) != active_runs:
            raise AssertionError("benchmark seed failed")

        combined = _timed(
            lambda: (
                store.list_conversations(),
                store.active_conversation_ids(),
            ),
            repeats,
        )
        list_only = _timed(store.list_conversations, repeats)
        active_only = _timed(store.active_conversation_ids, repeats)

        with sqlite3.connect(store.path) as connection:
            indexes = [
                row[1]
                for row in connection.execute(
                    "pragma index_list(conversations)"
                ).fetchall()
            ]

        return {
            "conversations": conversations,
            "active_runs": active_runs,
            "list_plus_active": _summary(combined),
            "list_only": _summary(list_only),
            "active_only": _summary(active_only),
            "conversation_indexes": indexes,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the conversation-list read path at long history."
    )
    parser.add_argument("--conversations", type=int, default=100_000)
    parser.add_argument("--active-runs", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--max-list-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-combined-p50-ms", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        conversations=max(1_000, args.conversations),
        active_runs=max(1, min(args.active_runs, args.conversations)),
        repeats=max(5, args.repeats),
    )
    print(json.dumps(result, sort_keys=True))

    failures: list[str] = []
    list_p50 = float(result["list_only"]["p50_ms"])
    combined_p50 = float(result["list_plus_active"]["p50_ms"])
    if args.max_list_p50_ms > 0 and list_p50 > args.max_list_p50_ms:
        failures.append(
            f"conversation list p50={list_p50} > {args.max_list_p50_ms}"
        )
    if args.max_combined_p50_ms > 0 and combined_p50 > args.max_combined_p50_ms:
        failures.append(
            f"conversation list+active p50={combined_p50} > {args.max_combined_p50_ms}"
        )
    if "idx_conversations_updated_at" not in result["conversation_indexes"]:
        failures.append("idx_conversations_updated_at is missing")
    if failures:
        raise SystemExit(
            "conversation-list performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
