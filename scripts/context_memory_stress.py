from __future__ import annotations

import argparse
import json
import math
import sqlite3
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import lingjing_harness.runtime.context_memory as context_memory_module
from lingjing_harness.runtime.context_memory import (
    build_governed_context,
    context_query_terms,
)
from lingjing_harness.store import WorkspaceStore


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]


def _seed(database: str, conversation_id: str, messages: int) -> None:
    rows = []
    for index in range(messages):
        role = "user" if index % 2 == 0 else "assistant"
        content = (
            f"stress turn={index} exp-{index % 1009}-ranker/r{index % 19} "
            f"feature_{index % 47} "
            + ("露营灯" if index % 13 == 0 else "常规研发记录")
        )
        rows.append(
            (
                f"stress-msg-{index}",
                conversation_id,
                role,
                content,
                "{}",
                float(index + 1),
            )
        )
    rows.append(
        (
            "stress-anchor",
            conversation_id,
            "user",
            "关键旧锚点 alpha-stress-991：继续搜索“露营灯”。",
            "{}",
            0.25,
        )
    )
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "insert into messages(id,conversation_id,role,content,payload,created_at) "
            "values(?,?,?,?,?,?)",
            rows,
        )
        connection.commit()


def _reader(
    database: str,
    conversation_id: str,
    worker: int,
    iterations: int,
) -> list[float]:
    store = WorkspaceStore(database)
    queries = (
        "继续 alpha-stress-991",
        "检查露营灯 feature_17",
        "继续 exp-311-ranker/r7",
        "检查 feature_29 latency",
    )
    timings: list[float] = []
    for index in range(iterations):
        query = queries[(worker + index) % len(queries)]
        started = time.perf_counter()
        snapshot = store.context_snapshot(
            conversation_id,
            query_terms=context_query_terms(query),
            recent_limit=96,
            search_limit=96,
            anchor_limit=16,
            memory_limit=72,
        )
        timings.append((time.perf_counter() - started) * 1000.0)
        if not snapshot["messages"]:
            raise AssertionError("reader lost conversation candidates")
        if "alpha-stress-991" in query and not any(
            row["id"] == "stress-anchor" for row in snapshot["messages"]
        ):
            raise AssertionError("reader lost old technical anchor")
    return timings


def _writer(
    database: str,
    conversation_id: str,
    worker: int,
    iterations: int,
    base_time: float,
) -> list[float]:
    store = WorkspaceStore(database)
    timings: list[float] = []
    for iteration in range(iterations):
        batch = [
            {
                "source_id": f"stress-att-{worker}-{iteration}-{item}",
                "source_kind": "attachment_text",
                "content": (
                    f"worker {worker} iteration {iteration} item {item} "
                    f"露营灯 feature_{item}"
                ),
                "trust": 0.52,
                "catalog_revision": "stress-rev",
                "created_at": base_time + worker * 100_000 + iteration * 8 + item,
            }
            for item in range(8)
        ]
        started = time.perf_counter()
        store.remember_context_items(conversation_id, batch)
        timings.append((time.perf_counter() - started) * 1000.0)
    return timings


def _process_same_source_write(
    database: str,
    conversation_id: str,
    round_id: int,
    timestamp: float,
) -> float:
    store = WorkspaceStore(database)
    source_id = f"race-source-{round_id}"
    store.remember_context_item(
        conversation_id,
        source_id=source_id,
        source_kind="attachment_text",
        content=f"value-{int(timestamp)}",
        catalog_revision=f"rev-{int(timestamp)}",
        created_at=timestamp,
    )
    return timestamp


def run_stress(
    *,
    messages: int,
    readers: int,
    writers: int,
    read_iterations: int,
    write_iterations: int,
    processes: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="lingjing-context-stress-") as directory:
        database = str(Path(directory) / "workspace.db")
        store = WorkspaceStore(database)
        cid = store.create_conversation("context-stress", "audit")["id"]
        _seed(database, cid, messages)

        started = time.perf_counter()
        read_samples: list[float] = []
        write_samples: list[float] = []
        with ThreadPoolExecutor(max_workers=readers + writers) as executor:
            read_futures = [
                executor.submit(
                    _reader,
                    database,
                    cid,
                    worker,
                    read_iterations,
                )
                for worker in range(readers)
            ]
            write_futures = [
                executor.submit(
                    _writer,
                    database,
                    cid,
                    worker,
                    write_iterations,
                    float(messages + 10_000),
                )
                for worker in range(writers)
            ]
            for future in read_futures:
                read_samples.extend(future.result())
            for future in write_futures:
                write_samples.extend(future.result())
        mixed_elapsed = max(time.perf_counter() - started, 1e-9)

        # Cross-process writes intentionally race older and newer observations for
        # the same source. The latest timestamp must win regardless of commit order.
        rounds = 8
        race_base = float(messages + 10_000_000)
        stamp_offsets = (10.0, 50.0, 20.0, 80.0, 30.0, 70.0, 40.0, 60.0)
        with ProcessPoolExecutor(max_workers=processes) as executor:
            for round_id in range(rounds):
                round_base = race_base + round_id * 1_000.0
                futures = [
                    executor.submit(
                        _process_same_source_write,
                        database,
                        cid,
                        round_id,
                        round_base + offset,
                    )
                    for offset in stamp_offsets
                ]
                for future in futures:
                    future.result()

        final_store = WorkspaceStore(database)
        snapshot = final_store.context_snapshot(
            cid,
            query_terms=context_query_terms("value-80"),
            recent_limit=8,
            search_limit=8,
            anchor_limit=0,
            memory_limit=192,
        )
        race_rows = {
            row["source_id"]: row
            for row in snapshot["memory_items"]
            if row["source_id"].startswith("race-source-")
        }
        if len(race_rows) != rounds:
            raise AssertionError(
                f"cross-process race sources lost: {len(race_rows)} != {rounds}"
            )
        for round_id in range(rounds):
            row = race_rows[f"race-source-{round_id}"]
            expected = race_base + round_id * 1_000.0 + 80.0
            if (
                float(row["created_at"]) != expected
                or row["content"] != f"value-{int(expected)}"
            ):
                raise AssertionError(
                    f"temporal rollback detected for race-source-{round_id}: {dict(row)}"
                )

        # Churn more unique vectors than the cache capacity. This checks bounded
        # retention without relying on process RSS noise from the runner.
        context_memory_module._context_vector.cache_clear()
        for index in range(5_000):
            build_governed_context(
                f"继续 stress-vector-{index}",
                messages=[
                    {
                        "id": f"cache-msg-{index}",
                        "role": "user",
                        "content": (
                            f"stress-vector-{index} 露营灯 "
                            f"feature_{index % 97}"
                        ),
                        "created_at": float(index),
                    }
                ],
                max_selected=1,
            )
        cache_info = context_memory_module._context_vector.cache_info()
        if cache_info.currsize > cache_info.maxsize:
            raise AssertionError(
                f"context vector cache exceeded bound: {cache_info}"
            )

        return {
            "messages": messages,
            "readers": readers,
            "writers": writers,
            "processes": processes,
            "read_operations": len(read_samples),
            "write_batches": len(write_samples),
            "mixed_elapsed_seconds": round(mixed_elapsed, 3),
            "read_p95_ms": round(_p95(read_samples), 3),
            "write_batch_p95_ms": round(_p95(write_samples), 3),
            "mixed_operations_per_second": round(
                (len(read_samples) + len(write_samples)) / mixed_elapsed,
                1,
            ),
            "race_rounds": rounds,
            "cache_currsize": cache_info.currsize,
            "cache_maxsize": cache_info.maxsize,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Concurrent long-context read/write/recovery stress test."
    )
    parser.add_argument("--messages", type=int, default=100_000)
    parser.add_argument("--readers", type=int, default=12)
    parser.add_argument("--writers", type=int, default=4)
    parser.add_argument("--read-iterations", type=int, default=24)
    parser.add_argument("--write-iterations", type=int, default=16)
    parser.add_argument("--processes", type=int, default=8)
    args = parser.parse_args()

    result = run_stress(
        messages=max(10_000, args.messages),
        readers=max(1, args.readers),
        writers=max(1, args.writers),
        read_iterations=max(4, args.read_iterations),
        write_iterations=max(4, args.write_iterations),
        processes=max(2, args.processes),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    if result["read_p95_ms"] > 600:
        raise SystemExit(
            f"context read p95 too slow under mixed load: {result['read_p95_ms']} ms"
        )
    if result["write_batch_p95_ms"] > 800:
        raise SystemExit(
            "context batch write p95 too slow under mixed load: "
            f"{result['write_batch_p95_ms']} ms"
        )


if __name__ == "__main__":
    main()
