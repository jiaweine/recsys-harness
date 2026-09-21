from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable


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
    values: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        values.append((time.perf_counter() - started) * 1000.0)
    return values


def _event(index: int, payload_bytes: int) -> dict[str, object]:
    return {
        "phase": "execute" if index % 3 else "observe",
        "title": f"event {index}",
        "detail": "conversation detail benchmark",
        "progress": min(99, index),
        "payload": {
            "index": index,
            "blob": "x" * payload_bytes,
            "nested": {"values": list(range(16))},
        },
        "created_at": float(index),
    }


def run_benchmark(
    *,
    conversations: int,
    messages: int,
    events: int,
    payload_bytes: int,
    repeats: int,
    workers: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xushu-conversation-read-") as directory:
        os.environ["LINGJING_DATA_DIR"] = str(Path(directory))
        os.environ["LINGJING_ENV"] = "development"
        os.environ["LINGJING_TRUST_PROXY_IP"] = "0"

        import lingjing_harness.api as api

        rows = [
            api.store.create_conversation(f"conversation {index}", "audit")
            for index in range(max(2, conversations))
        ]
        target = rows[0]

        message_blob = "m" * 1024
        for index in range(max(1, messages)):
            role = "user" if index % 2 == 0 else "assistant"
            api.store.add_message(
                target["id"],
                role,
                f"message {index}",
                {"index": index, "blob": message_blob},
            )

        now = time.time()
        snapshot = {
            "run_id": "job-conversation-detail-benchmark",
            "conversation_id": target["id"],
            "goal": "benchmark detail",
            "status": "running",
            "events": [_event(index, payload_bytes) for index in range(events)],
            "result": None,
            "checkpoint": {
                "cycle": events,
                "actions": [{"tool": "search.run", "blob": "a" * payload_bytes}],
                "observations": [{"blob": "o" * payload_bytes}],
            },
            "created_at": now,
            "updated_at": now,
        }
        assert api.store.reserve_run(
            snapshot["run_id"],
            target["id"],
            snapshot["goal"],
            snapshot,
            owner_id=api.WORKER_ID,
            lease_seconds=api.RUN_LEASE_SECONDS,
        )

        # Populate several other active conversations so list-view active marking
        # is not a one-row special case.
        active_count = min(len(rows) - 1, 16)
        for index in range(1, active_count + 1):
            row = {
                "run_id": f"job-list-{index}",
                "conversation_id": rows[index]["id"],
                "goal": "list benchmark",
                "status": "running",
                "events": [],
                "result": None,
                "created_at": now,
                "updated_at": now,
            }
            assert api.store.reserve_run(
                row["run_id"],
                row["conversation_id"],
                row["goal"],
                row,
                owner_id=api.WORKER_ID,
                lease_seconds=api.RUN_LEASE_SECONDS,
            )

        def list_current():
            listed = api.store.list_conversations()
            active = api.store.active_conversation_ids()
            return [{**row, "active": row["id"] in active} for row in listed]

        def detail_current():
            conversation = api.store.get_conversation(target["id"])
            active = api.store.active_run_for_conversation(target["id"])
            if active:
                conversation["active_run"] = {
                    "run_id": active["run_id"],
                    "status": active["status"],
                    "events": active.get("events", []),
                }
            else:
                conversation["active_run"] = None
            return conversation

        expected = detail_current()
        if expected["active_run"]["run_id"] != snapshot["run_id"]:
            raise AssertionError("active run fixture did not resolve")

        list_samples = _timed(list_current, repeats)
        conversation_samples = _timed(
            lambda: api.store.get_conversation(target["id"]), repeats
        )
        active_samples = _timed(
            lambda: api.store.active_run_for_conversation(target["id"]), repeats
        )
        detail_samples = _timed(detail_current, repeats)

        def detail_many(iterations: int) -> int:
            completed = 0
            for _ in range(iterations):
                visible = detail_current()
                if visible["active_run"]["run_id"] != snapshot["run_id"]:
                    raise AssertionError("detail view drifted")
                completed += 1
            return completed

        per_worker = max(4, repeats // max(1, workers))
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            completed = sum(
                executor.map(detail_many, [per_worker] * max(1, workers))
            )
        elapsed = max(time.perf_counter() - started, 1e-9)

        snapshot_bytes = len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8"))
        return {
            "conversations": conversations,
            "messages": messages,
            "events": events,
            "snapshot_bytes": snapshot_bytes,
            "conversation_list": _summary(list_samples),
            "conversation_messages": _summary(conversation_samples),
            "active_run_lookup": _summary(active_samples),
            "conversation_detail": _summary(detail_samples),
            "concurrent_detail": {
                "workers": workers,
                "reads": completed,
                "elapsed_seconds": round(elapsed, 4),
                "reads_per_second": round(completed / elapsed, 2),
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark conversation list/detail read hot paths."
    )
    parser.add_argument("--conversations", type=int, default=40)
    parser.add_argument("--messages", type=int, default=200)
    parser.add_argument("--events", type=int, default=160)
    parser.add_argument("--payload-bytes", type=int, default=8192)
    parser.add_argument("--repeats", type=int, default=80)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    result = run_benchmark(
        conversations=max(2, args.conversations),
        messages=max(1, args.messages),
        events=max(1, args.events),
        payload_bytes=max(0, args.payload_bytes),
        repeats=max(10, args.repeats),
        workers=max(1, args.workers),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
