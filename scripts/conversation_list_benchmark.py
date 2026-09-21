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
    rows = sorted(values)
    return rows[min(len(rows) - 1, max(0, math.ceil(q * len(rows)) - 1))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 4) if values else 0.0,
        "p50_ms": round(_percentile(values, 0.50), 4),
        "p95_ms": round(_percentile(values, 0.95), 4),
        "max_ms": round(max(values), 4) if values else 0.0,
    }


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    out: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return out


def _seed(store: WorkspaceStore, conversations: int, active_runs: int) -> None:
    with sqlite3.connect(store.path) as connection:
        rows = [
            (
                f"cv-bench-{index:08d}",
                f"Conversation {index}",
                "audit",
                float(index),
                float(index),
            )
            for index in range(conversations)
        ]
        connection.executemany(
            "insert into conversations(id,title,scene,created_at,updated_at) values(?,?,?,?,?)",
            rows,
        )
        run_rows = []
        for index in range(active_runs):
            cid = f"cv-bench-{conversations - 1 - index:08d}"
            run_id = f"job-bench-{index:06d}"
            snapshot = json.dumps(
                {
                    "run_id": run_id,
                    "conversation_id": cid,
                    "status": "running",
                    "events": [],
                    "created_at": float(conversations + index),
                    "updated_at": float(conversations + index),
                },
                separators=(",", ":"),
            )
            run_rows.append(
                (
                    run_id,
                    cid,
                    "benchmark",
                    "running",
                    snapshot,
                    float(conversations + index),
                    float(conversations + index),
                    "bench-owner",
                    float(conversations + index + 30),
                )
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


def _query_plan(store: WorkspaceStore) -> list[str]:
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute(
            "explain query plan select * from conversations order by updated_at desc limit 40"
        ).fetchall()
    return [str(row[-1]) for row in rows]


def run_benchmark(
    *,
    conversations: int,
    active_runs: int,
    repeats: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xushu-conversation-list-") as directory:
        store = WorkspaceStore(Path(directory) / "workspace.db")
        _seed(store, conversations, active_runs)

        # Warm filesystem/page cache before timing steady reads.
        expected = store.list_conversations()
        active = store.active_conversation_ids()
        if len(expected) != 40:
            raise AssertionError(f"unexpected list size: {len(expected)}")
        expected_active = {
            f"cv-bench-{conversations - 1 - index:08d}"
            for index in range(active_runs)
        }
        if active != expected_active:
            raise AssertionError("active conversation set mismatch")

        list_samples = _timed(store.list_conversations, repeats)
        active_samples = _timed(store.active_conversation_ids, repeats)
        combined_samples = _timed(
            lambda: [
                {**row, "active": row["id"] in store.active_conversation_ids()}
                for row in store.list_conversations()
            ],
            repeats,
        )

        return {
            "conversations": conversations,
            "active_runs": active_runs,
            "query_plan": _query_plan(store),
            "list_conversations": _summary(list_samples),
            "active_conversation_ids": _summary(active_samples),
            "combined_endpoint_shape": _summary(combined_samples),
            "top_id": expected[0]["id"],
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark conversation-list reads at large workspace scale."
    )
    parser.add_argument("--conversations", type=int, default=100_000)
    parser.add_argument("--active-runs", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=80)
    args = parser.parse_args()

    result = run_benchmark(
        conversations=max(10_000, args.conversations),
        active_runs=max(0, min(200, args.active_runs)),
        repeats=max(10, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
