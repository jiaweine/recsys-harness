from __future__ import annotations

import argparse
import copy
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
        "detail": "runtime polling benchmark",
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
    events: int,
    payload_bytes: int,
    repeats: int,
    workers: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xushu-run-poll-") as directory:
        os.environ["LINGJING_DATA_DIR"] = str(Path(directory))
        os.environ["LINGJING_ENV"] = "development"
        os.environ["LINGJING_TRUST_PROXY_IP"] = "0"

        import lingjing_harness.api as api

        conversation = api.store.create_conversation("poll benchmark", "audit")
        run_id = "job-pollbench"
        now = time.time()
        row = {
            "run_id": run_id,
            "conversation_id": conversation["id"],
            "goal": "benchmark",
            "status": "running",
            "events": [_event(index, payload_bytes) for index in range(events)],
            "result": None,
            "attachment_ids": [],
            "attachments": [],
            "allow_network": False,
            "catalog_revision": api.CATALOG_REVISION,
            "checkpoint": {
                "cycle": events,
                "status": "running",
                "result": None,
            },
            "created_at": now,
            "updated_at": now,
        }
        accepted = api.store.reserve_run(
            run_id,
            conversation["id"],
            "benchmark",
            row,
            owner_id=api.WORKER_ID,
            lease_seconds=api.RUN_LEASE_SECONDS,
        )
        if not accepted:
            raise AssertionError("benchmark run reservation failed")
        with api.RUN_LOCK:
            api.RUNS[run_id] = copy.deepcopy(row)

        expected_status = api.store.run_status(run_id)
        expected_poll = api.get_run(run_id)
        if expected_status != "running" or expected_poll.get("status") != "running":
            raise AssertionError("benchmark run did not remain active")

        def baseline_snapshot_copy():
            with api.RUN_LOCK:
                current = api.RUNS.get(run_id)
                return copy.deepcopy(current) if current is not None else None

        def baseline_status_read():
            with api.store._connect() as connection:
                status_row = connection.execute(
                    "select status from runs where run_id=?",
                    (run_id,),
                ).fetchone()
            return str(status_row["status"]) if status_row else None

        def specialized_snapshot_copy():
            with api.RUN_LOCK:
                current = api.RUNS.get(run_id)
                return api._clone_run_value(current) if current is not None else None

        baseline_status_samples = _timed(baseline_status_read, repeats)
        status_samples = _timed(lambda: api.store.run_status(run_id), repeats)
        snapshot_samples = _timed(baseline_snapshot_copy, repeats)
        specialized_snapshot_samples = _timed(specialized_snapshot_copy, repeats)
        poll_samples = _timed(lambda: api.get_run(run_id), repeats)

        def poll_many(iterations: int) -> int:
            completed = 0
            for _ in range(iterations):
                row_value = api.get_run(run_id)
                if row_value.get("status") != "running":
                    raise AssertionError("active poll changed status")
                completed += 1
            return completed

        per_worker = max(4, repeats // max(1, workers))
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            completed = sum(
                executor.map(poll_many, [per_worker] * max(1, workers))
            )
        elapsed = max(time.perf_counter() - started, 1e-9)

        snapshot_bytes = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        return {
            "events": events,
            "payload_bytes": payload_bytes,
            "snapshot_bytes": snapshot_bytes,
            "run_status_baseline": _summary(baseline_status_samples),
            "run_status": _summary(status_samples),
            "snapshot_copy_baseline": _summary(snapshot_samples),
            "snapshot_copy": _summary(specialized_snapshot_samples),
            "active_poll": _summary(poll_samples),
            "concurrent": {
                "workers": workers,
                "polls": completed,
                "elapsed_seconds": round(elapsed, 4),
                "polls_per_second": round(completed / elapsed, 2),
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark active run polling and durable-status read hot paths."
    )
    parser.add_argument("--events", type=int, default=80)
    parser.add_argument("--payload-bytes", type=int, default=4096)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-status-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-poll-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-status-speedup", type=float, default=0.0)
    parser.add_argument("--min-copy-speedup", type=float, default=0.0)
    parser.add_argument("--min-concurrent-rps", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        events=max(1, args.events),
        payload_bytes=max(0, args.payload_bytes),
        repeats=max(10, args.repeats),
        workers=max(1, args.workers),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    status_baseline_p50 = float(result["run_status_baseline"]["p50_ms"])
    status_p50 = float(result["run_status"]["p50_ms"])
    copy_baseline_p50 = float(result["snapshot_copy_baseline"]["p50_ms"])
    copy_p50 = float(result["snapshot_copy"]["p50_ms"])
    poll_p50 = float(result["active_poll"]["p50_ms"])
    status_speedup = status_baseline_p50 / max(status_p50, 1e-9)
    copy_speedup = copy_baseline_p50 / max(copy_p50, 1e-9)
    result["status_speedup_p50"] = round(status_speedup, 2)
    result["copy_speedup_p50"] = round(copy_speedup, 2)
    rps = float(result["concurrent"]["polls_per_second"])
    if args.max_status_p50_ms > 0 and status_p50 > args.max_status_p50_ms:
        failures.append(f"run_status p50={status_p50} > {args.max_status_p50_ms}")
    if args.max_poll_p50_ms > 0 and poll_p50 > args.max_poll_p50_ms:
        failures.append(f"active poll p50={poll_p50} > {args.max_poll_p50_ms}")
    if args.min_status_speedup > 0 and status_speedup < args.min_status_speedup:
        failures.append(
            f"run_status speedup={status_speedup:.2f} < {args.min_status_speedup}"
        )
    if args.min_copy_speedup > 0 and copy_speedup < args.min_copy_speedup:
        failures.append(
            f"snapshot copy speedup={copy_speedup:.2f} < {args.min_copy_speedup}"
        )
    if args.min_concurrent_rps > 0 and rps < args.min_concurrent_rps:
        failures.append(f"concurrent rps={rps} < {args.min_concurrent_rps}")
    if failures:
        raise SystemExit("run poll performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
