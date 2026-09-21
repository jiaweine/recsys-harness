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


ACTIVE = ("running", "interrupted", "cancel_requested")


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


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
    now = 10_000_000.0
    conversation_rows = [
        (
            f"cv-{index:08d}",
            f"Conversation {index}",
            "audit",
            now + index,
            now + index,
        )
        for index in range(conversations)
    ]
    run_rows = []
    for index in range(active_runs):
        conversation_id = f"cv-{index % conversations:08d}"
        run_id = f"run-{index:08d}"
        status = ACTIVE[index % len(ACTIVE)]
        snapshot = {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "status": status,
            "events": [],
        }
        run_rows.append(
            (
                run_id,
                conversation_id,
                "benchmark",
                status,
                json.dumps(snapshot),
                now + index,
                now + index,
                "bench",
                now + index + 60.0,
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


def _legacy(store: WorkspaceStore, limit: int) -> list[dict]:
    rows = store.list_conversations(limit)
    active = store.active_conversation_ids()
    return [{**row, "active": row["id"] in active} for row in rows]


def _candidate(store: WorkspaceStore, limit: int) -> list[dict]:
    return store.list_conversations_with_activity(limit)


def run_benchmark(
    *,
    conversations: int,
    active_runs: int,
    repeats: int,
    limit: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xushu-read-api-") as directory:
        store = WorkspaceStore(Path(directory) / "workspace.db")
        _seed(store, conversations, active_runs)

        # WorkspaceStore now creates the production ordering index during schema
        # initialization. Drop it temporarily to reconstruct the old production
        # plan, then recreate it below for the optimized measurement.
        with sqlite3.connect(store.path) as connection:
            connection.execute("drop index if exists idx_conversations_updated_at")
            connection.commit()

        expected = _legacy(store, limit)
        candidate = _candidate(store, limit)
        if candidate != expected:
            raise AssertionError("candidate conversation query changed API semantics")

        # Warm SQLite page cache before measuring.
        _legacy(store, limit)
        _candidate(store, limit)

        legacy_samples = _timed(lambda: _legacy(store, limit), repeats)
        candidate_samples = _timed(lambda: _candidate(store, limit), repeats)

        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "create index if not exists idx_conversations_updated_at "
                "on conversations(updated_at desc)"
            )
            connection.commit()

        _legacy(store, limit)
        _candidate(store, limit)
        indexed_legacy_samples = _timed(lambda: _legacy(store, limit), repeats)
        indexed_candidate_samples = _timed(lambda: _candidate(store, limit), repeats * 2)

        legacy_p50 = _summary(legacy_samples)["p50_ms"]
        indexed_legacy_p50 = _summary(indexed_legacy_samples)["p50_ms"]
        indexed_candidate_p50 = _summary(indexed_candidate_samples)["p50_ms"]
        return {
            "conversations": conversations,
            "active_runs": active_runs,
            "limit": limit,
            "legacy_two_query": _summary(legacy_samples),
            "candidate_single_query": _summary(candidate_samples),
            "indexed_legacy_two_query": _summary(indexed_legacy_samples),
            "indexed_candidate_single_query": _summary(indexed_candidate_samples),
            "index_only_speedup": round(
                legacy_p50 / max(indexed_legacy_p50, 1e-9),
                2,
            ),
            "indexed_single_query_speedup": round(
                legacy_p50 / max(indexed_candidate_p50, 1e-9),
                2,
            ),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark conversation-list API read amplification."
    )
    parser.add_argument("--conversations", type=int, default=50_000)
    parser.add_argument("--active-runs", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        conversations=max(1000, args.conversations),
        active_runs=max(0, args.active_runs),
        repeats=max(10, args.repeats),
        limit=max(1, args.limit),
    )
    print(json.dumps(result, sort_keys=True))

    optimized_p50 = float(result["indexed_candidate_single_query"]["p50_ms"])
    speedup = float(result["indexed_single_query_speedup"])
    failures: list[str] = []
    if args.max_optimized_p50_ms > 0 and optimized_p50 > args.max_optimized_p50_ms:
        failures.append(
            f"optimized p50={optimized_p50} > {args.max_optimized_p50_ms}"
        )
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(f"speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit("read API performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
