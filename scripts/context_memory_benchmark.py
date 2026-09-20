from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import lingjing_harness.runtime.context_memory as context_memory_module
from lingjing_harness.runtime.context_memory import (
    build_governed_context,
    context_query_terms,
)
from lingjing_harness.store import WorkspaceStore


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def _timed_ms(fn: Callable[[], object], repeats: int) -> list[float]:
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _summary(samples: list[float]) -> dict[str, float]:
    return {
        "count": float(len(samples)),
        "mean_ms": round(statistics.fmean(samples), 3) if samples else 0.0,
        "p50_ms": round(_percentile(samples, 0.50), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
        "max_ms": round(max(samples), 3) if samples else 0.0,
    }


def _seed(
    store: WorkspaceStore,
    conversation_id: str,
    *,
    messages: int,
    memory_items: int,
) -> None:
    rows = []
    for index in range(messages):
        role = "user" if index % 2 == 0 else "assistant"
        technical = f"exp-{index % 997}-ranker/r{index % 17}"
        topic = "露营灯" if index % 11 == 0 else ("咖啡机" if index % 7 == 0 else "常规研发记录")
        content = (
            f"turn={index} {technical} {topic} "
            f"feature_{index % 31} latency-{index % 13} "
            "context memory retrieval performance regression analysis"
        )
        rows.append(
            (
                f"bench-msg-{index}",
                conversation_id,
                role,
                content,
                "{}",
                float(index + 1),
            )
        )

    # One old, rare anchor must remain retrievable even with a large recent tail.
    rows.append(
        (
            "bench-anchor",
            conversation_id,
            "user",
            "关键分支 alpha-rare-731：继续优化搜索“露营灯”，保留这个实验锚点。",
            "{}",
            0.5,
        )
    )

    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            "insert into messages(id,conversation_id,role,content,payload,created_at) "
            "values(?,?,?,?,?,?)",
            rows,
        )
        connection.commit()

    for index in range(memory_items):
        store.remember_context_item(
            conversation_id,
            source_id=f"bench-att-{index}",
            source_kind="attachment_text",
            content=(
                f"attachment {index} 露营灯 exp-{index % 97} "
                f"multimodal observation feature_{index % 23}"
            ),
            catalog_revision="rev-bench",
            created_at=float(messages + index + 10),
        )


def run_benchmark(
    *,
    messages: int,
    memory_items: int,
    repeats: int,
    workers: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="lingjing-context-bench-") as directory:
        database = Path(directory) / "workspace.db"
        store = WorkspaceStore(database)
        cid = store.create_conversation("context-benchmark", "search")["id"]
        _seed(
            store,
            cid,
            messages=messages,
            memory_items=memory_items,
        )

        queries = [
            "继续 alpha-rare-731 的实验",
            "检查搜索露营灯 feature_17",
            "继续 exp-311-ranker/r5",
            "检查 context memory retrieval performance",
            "继续 latency-9 feature_7",
        ]

        # Warm SQLite page cache and Python code paths.
        for query in queries:
            store.context_snapshot(
                cid,
                query_terms=context_query_terms(query),
                recent_limit=96,
                search_limit=96,
                anchor_limit=16,
                memory_limit=72,
            )

        read_samples: list[float] = []
        snapshots = []
        for index in range(max(1, repeats)):
            query = queries[index % len(queries)]
            started = time.perf_counter()
            snapshot = store.context_snapshot(
                cid,
                query_terms=context_query_terms(query),
                recent_limit=96,
                search_limit=96,
                anchor_limit=16,
                memory_limit=72,
            )
            read_samples.append((time.perf_counter() - started) * 1000.0)
            snapshots.append(snapshot)

        anchor_snapshot = store.context_snapshot(
            cid,
            query_terms=context_query_terms("继续 alpha-rare-731 的实验"),
            recent_limit=96,
            search_limit=96,
            anchor_limit=16,
            memory_limit=72,
        )
        anchor_found = any(
            row.get("id") == "bench-anchor"
            for row in anchor_snapshot["messages"]
        )
        if not anchor_found:
            raise AssertionError("rare long-history technical anchor was not retrieved")

        build_messages = snapshots[-1]["messages"]
        build_memories = snapshots[-1]["memory_items"]
        build_samples = _timed_ms(
            lambda: build_governed_context(
                "继续检查露营灯 exp-311 feature_17",
                messages=build_messages,
                multimodal_items=build_memories,
                catalog_revision="rev-bench",
            ),
            max(20, repeats),
        )
        build_warm_samples = build_samples[1:] or build_samples

        concurrency_repeats = max(4, repeats // max(1, workers))
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = []
            for worker in range(max(1, workers)):
                for index in range(concurrency_repeats):
                    query = queries[(worker + index) % len(queries)]
                    futures.append(
                        executor.submit(
                            store.context_snapshot,
                            cid,
                            query_terms=context_query_terms(query),
                            recent_limit=96,
                            search_limit=96,
                            anchor_limit=16,
                            memory_limit=72,
                        )
                    )
            for future in futures:
                result = future.result()
                if not result["messages"]:
                    raise AssertionError("concurrent context read returned no messages")
        concurrent_elapsed = max(time.perf_counter() - started, 1e-9)
        concurrent_reads = len(futures)

        batch_samples: list[float] = []
        for batch_index in range(24):
            batch = [
                {
                    "source_id": f"bench-batch-{batch_index}-{item_index}",
                    "source_kind": "attachment_text",
                    "content": (
                        f"batch {batch_index} item {item_index} 露营灯 "
                        f"feature_{item_index}"
                    ),
                    "trust": 0.52,
                    "catalog_revision": "rev-batch",
                    "created_at": float(
                        messages + memory_items + 10_000
                        + batch_index * 8 + item_index
                    ),
                }
                for item_index in range(8)
            ]
            started = time.perf_counter()
            store.remember_context_items(cid, batch)
            batch_samples.append((time.perf_counter() - started) * 1000.0)

        write_samples: list[float] = []
        write_count = min(192, max(64, memory_items))
        for index in range(write_count):
            started = time.perf_counter()
            store.remember_context_item(
                cid,
                source_id=f"bench-write-{index}",
                source_kind="attachment_text",
                content=f"benchmark write {index} 露营灯 feature_{index % 17}",
                catalog_revision="rev-write",
                created_at=float(messages + memory_items + index + 1000),
            )
            write_samples.append((time.perf_counter() - started) * 1000.0)

        return {
            "messages": messages,
            "memory_items": memory_items,
            "workers": workers,
            "anchor_found": anchor_found,
            "database_bytes": os.path.getsize(database),
            "context_snapshot": _summary(read_samples),
            "context_build": _summary(build_samples),
            "context_build_cold_ms": round(build_samples[0], 3),
            "context_build_warm": _summary(build_warm_samples),
            "memory_write": _summary(write_samples),
            "memory_batch_8": _summary(batch_samples),
            "concurrent_reads": {
                "count": concurrent_reads,
                "elapsed_ms": round(concurrent_elapsed * 1000.0, 3),
                "reads_per_second": round(concurrent_reads / concurrent_elapsed, 1),
            },
            "vector_cache": {
                "maxsize": context_memory_module._context_vector.cache_info().maxsize,
                "currsize": context_memory_module._context_vector.cache_info().currsize,
                "hits": context_memory_module._context_vector.cache_info().hits,
                "misses": context_memory_module._context_vector.cache_info().misses,
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark long-horizon governed context-memory hot paths."
    )
    parser.add_argument("--messages", type=int, default=50_000)
    parser.add_argument("--memory-items", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=40)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-snapshot-p95-ms", type=float, default=0.0)
    parser.add_argument("--max-build-warm-p95-ms", type=float, default=0.0)
    parser.add_argument("--min-concurrent-rps", type=float, default=0.0)
    parser.add_argument("--max-batch-p50-ms", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        messages=max(1_000, args.messages),
        memory_items=max(0, args.memory_items),
        repeats=max(5, args.repeats),
        workers=max(1, args.workers),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    checks = [
        (
            args.max_snapshot_p95_ms <= 0
            or result["context_snapshot"]["p95_ms"] <= args.max_snapshot_p95_ms,
            "context_snapshot p95",
            result["context_snapshot"]["p95_ms"],
            args.max_snapshot_p95_ms,
        ),
        (
            args.max_build_warm_p95_ms <= 0
            or result["context_build_warm"]["p95_ms"] <= args.max_build_warm_p95_ms,
            "context_build warm p95",
            result["context_build_warm"]["p95_ms"],
            args.max_build_warm_p95_ms,
        ),
        (
            args.min_concurrent_rps <= 0
            or result["concurrent_reads"]["reads_per_second"] >= args.min_concurrent_rps,
            "concurrent reads/s",
            result["concurrent_reads"]["reads_per_second"],
            args.min_concurrent_rps,
        ),
        (
            args.max_batch_p50_ms <= 0
            or result["memory_batch_8"]["p50_ms"] <= args.max_batch_p50_ms,
            "batch-8 p50",
            result["memory_batch_8"]["p50_ms"],
            args.max_batch_p50_ms,
        ),
    ]
    failures = [
        f"{name}: observed={observed} threshold={threshold}"
        for ok, name, observed, threshold in checks
        if not ok
    ]
    if failures:
        raise SystemExit("performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
