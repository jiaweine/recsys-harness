from __future__ import annotations

import argparse
import copy
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
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    out: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return out


def _snapshot(run_id: str, cid: str, event_count: int, payload_bytes: int) -> dict:
    detail = "x" * max(0, payload_bytes)
    events = [
        {
            "phase": "execute" if index % 3 == 0 else "observe",
            "progress": min(99, index),
            "detail": detail,
            "payload": {"index": index},
            "created_at": float(index),
        }
        for index in range(event_count)
    ]
    return {
        "run_id": run_id,
        "conversation_id": cid,
        "goal": "runtime persistence benchmark",
        "status": "running",
        "events": events,
        "result": None,
        "created_at": 1.0,
        "updated_at": 1.0,
    }


def run_benchmark(
    *,
    repeats: int,
    event_count: int,
    event_payload_bytes: int,
    messages: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="lingjing-runtime-persist-") as directory:
        database = str(Path(directory) / "workspace.db")
        store = WorkspaceStore(database)
        cid = store.create_conversation("persistence-benchmark", "audit")["id"]
        run_id = "runtime-persistence-benchmark"
        owner = "bench-owner"
        snapshot = _snapshot(run_id, cid, event_count, event_payload_bytes)

        if not store.reserve_run(
            run_id,
            cid,
            snapshot["goal"],
            snapshot,
            owner_id=owner,
            lease_seconds=30,
        ):
            raise AssertionError("failed to reserve benchmark run")

        # Baseline production path: save_run already refreshes owner + lease,
        # followed immediately by a second durable renew_run_lease write.
        save_plus_renew = _timed(
            lambda: (
                store.save_run(
                    run_id,
                    cid,
                    snapshot["goal"],
                    "running",
                    snapshot,
                    owner_id=owner,
                    lease_seconds=30,
                ),
                store.renew_run_lease(run_id, owner, 30),
            ),
            repeats,
        )
        save_only = _timed(
            lambda: store.save_run(
                run_id,
                cid,
                snapshot["goal"],
                "running",
                snapshot,
                owner_id=owner,
                lease_seconds=30,
            ),
            repeats,
        )
        save_fenced = _timed(
            lambda: store.save_run_fenced(
                run_id,
                cid,
                snapshot["goal"],
                "running",
                snapshot,
                owner_id=owner,
                lease_seconds=30,
            ),
            repeats,
        )
        run_status = _timed(lambda: store.run_status(run_id), repeats * 4)
        full_run = _timed(lambda: store.get_run(run_id), repeats)

        rows = [
            (
                f"bench-msg-{index}",
                cid,
                "user" if index % 2 == 0 else "assistant",
                f"message {index}",
                json.dumps({"blob": "y" * 512, "index": index}),
                float(index + 10),
            )
            for index in range(messages)
        ]
        with sqlite3.connect(database) as connection:
            connection.executemany(
                "insert into messages(id,conversation_id,role,content,payload,created_at) "
                "values(?,?,?,?,?,?)",
                rows,
            )
            connection.commit()

        conversation_detail = _timed(lambda: store.get_conversation(cid), max(5, repeats // 4))
        store.add_message(
            cid,
            "assistant",
            "idempotency lookup target",
            {"job_id": "benchmark-job-id", "blob": "z" * 512},
        )
        assistant_lookup_hit = _timed(
            lambda: store.assistant_for_job(cid, "benchmark-job-id"),
            repeats,
        )
        assistant_lookup_miss = _timed(
            lambda: store.assistant_for_job(cid, "missing-benchmark-job-id"),
            repeats,
        )
        list_plus_active = _timed(
            lambda: (store.list_conversations(), store.active_conversation_ids()),
            repeats * 2,
        )

        copy_source = store.get_run(run_id)
        deep_copy = _timed(lambda: copy.deepcopy(copy_source), repeats * 2)

        heartbeat_run_ids: list[str] = []
        for index in range(100):
            heartbeat_cid = store.create_conversation(
                f"heartbeat-{index}",
                "audit",
            )["id"]
            heartbeat_run_id = f"heartbeat-run-{index}"
            heartbeat_snapshot = _snapshot(
                heartbeat_run_id,
                heartbeat_cid,
                1,
                0,
            )
            if not store.reserve_run(
                heartbeat_run_id,
                heartbeat_cid,
                heartbeat_snapshot["goal"],
                heartbeat_snapshot,
                owner_id=owner,
                lease_seconds=30,
            ):
                raise AssertionError("failed to reserve heartbeat benchmark run")
            heartbeat_run_ids.append(heartbeat_run_id)

        heartbeat_individual = _timed(
            lambda: [
                store.renew_run_lease(heartbeat_run_id, owner, 30)
                for heartbeat_run_id in heartbeat_run_ids
            ],
            max(5, repeats // 5),
        )
        heartbeat_batch = _timed(
            lambda: store.renew_run_leases(heartbeat_run_ids, owner, 30),
            max(5, repeats // 5),
        )

        return {
            "event_count": event_count,
            "event_payload_bytes": event_payload_bytes,
            "messages": messages,
            "snapshot_json_bytes": len(
                json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
            ),
            "save_plus_renew": _summary(save_plus_renew),
            "save_only": _summary(save_only),
            "save_fenced": _summary(save_fenced),
            "run_status": _summary(run_status),
            "get_run": _summary(full_run),
            "deepcopy_run": _summary(deep_copy),
            "conversation_detail": _summary(conversation_detail),
            "assistant_lookup_hit": _summary(assistant_lookup_hit),
            "assistant_lookup_miss": _summary(assistant_lookup_miss),
            "list_plus_active": _summary(list_plus_active),
            "heartbeat_individual_100": _summary(heartbeat_individual),
            "heartbeat_batch_100": _summary(heartbeat_batch),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark durable run-persistence and conversation read hot paths."
    )
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--event-count", type=int, default=80)
    parser.add_argument("--event-payload-bytes", type=int, default=1024)
    parser.add_argument("--messages", type=int, default=10000)
    parser.add_argument("--max-save-fenced-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-assistant-lookup-miss-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-heartbeat-batch-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-heartbeat-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        repeats=max(10, args.repeats),
        event_count=max(1, args.event_count),
        event_payload_bytes=max(0, args.event_payload_bytes),
        messages=max(1000, args.messages),
    )
    heartbeat_batch_p50 = float(result["heartbeat_batch_100"]["p50_ms"])
    heartbeat_individual_p50 = float(result["heartbeat_individual_100"]["p50_ms"])
    heartbeat_speedup = (
        heartbeat_individual_p50 / max(heartbeat_batch_p50, 1e-9)
    )
    result["heartbeat_speedup_p50"] = round(heartbeat_speedup, 2)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    checks = [
        (
            args.max_save_fenced_p50_ms <= 0
            or result["save_fenced"]["p50_ms"] <= args.max_save_fenced_p50_ms,
            "save_fenced p50",
            result["save_fenced"]["p50_ms"],
            args.max_save_fenced_p50_ms,
        ),
        (
            args.max_assistant_lookup_miss_p50_ms <= 0
            or result["assistant_lookup_miss"]["p50_ms"]
            <= args.max_assistant_lookup_miss_p50_ms,
            "assistant lookup miss p50",
            result["assistant_lookup_miss"]["p50_ms"],
            args.max_assistant_lookup_miss_p50_ms,
        ),
        (
            args.max_heartbeat_batch_p50_ms <= 0
            or heartbeat_batch_p50 <= args.max_heartbeat_batch_p50_ms,
            "heartbeat batch p50",
            heartbeat_batch_p50,
            args.max_heartbeat_batch_p50_ms,
        ),
        (
            args.min_heartbeat_speedup <= 0
            or heartbeat_speedup >= args.min_heartbeat_speedup,
            "heartbeat p50 speedup",
            round(heartbeat_speedup, 2),
            args.min_heartbeat_speedup,
        ),
    ]
    for ok, name, observed, threshold in checks:
        if not ok:
            failures.append(
                f"{name}: observed={observed} threshold={threshold}"
            )
    if failures:
        raise SystemExit(
            "runtime persistence performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
