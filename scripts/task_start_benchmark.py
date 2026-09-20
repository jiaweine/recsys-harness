from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
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


def _snapshot(run_id: str, conversation_id: str, goal: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": goal,
        "status": "running",
        "events": [],
        "result": None,
        "attachment_ids": [],
        "attachments": [],
        "allow_network": False,
        "catalog_revision": "bench-revision",
        "created_at": now,
        "updated_at": now,
    }


def _store_start_benchmark(repeats: int) -> dict[str, float]:
    with tempfile.TemporaryDirectory(prefix="xushu-task-start-store-") as directory:
        store = WorkspaceStore(Path(directory) / "workspace.db")
        cid = store.create_conversation("task-start", "search")["id"]
        store.add_message(cid, "assistant", "seed", {"seed": True})
        owner = "task-start-bench-owner"
        samples: list[float] = []

        for index in range(repeats):
            run_id = f"task-start-{index}"
            goal = f"检查搜索露营灯 #{index}"
            snapshot = _snapshot(run_id, cid, goal)

            started = time.perf_counter()
            if not store.reserve_run(
                run_id,
                cid,
                goal,
                snapshot,
                owner_id=owner,
                lease_seconds=30,
            ):
                raise AssertionError("failed to reserve benchmark run")
            user = store.add_message(
                cid,
                "user",
                goal,
                {"attachments": [], "allow_network": False},
            )
            snapshot["user_message_id"] = user["id"]
            status, authorized = store.save_run_fenced(
                run_id,
                cid,
                goal,
                "running",
                snapshot,
                owner_id=owner,
                lease_seconds=30,
            )
            samples.append((time.perf_counter() - started) * 1000.0)

            if status != "running" or not authorized:
                raise AssertionError("initial persistence lost ownership")
            durable = store.get_run(run_id)
            if durable.get("user_message_id") != user["id"]:
                raise AssertionError("durable run did not capture user message id")
            store.delete_run(run_id, owner_id=owner)

        return _summary(samples)


async def _api_start_benchmark(repeats: int) -> dict[str, float]:
    from httpx import ASGITransport, AsyncClient
    import lingjing_harness.api as api_module

    async def no_execute(*args, **kwargs):
        await asyncio.sleep(0)

    original_execute = api_module._execute
    api_module._execute = no_execute
    samples: list[float] = []
    transport = ASGITransport(app=api_module.app)

    try:
        async with api_module.lifespan(api_module.app):
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                created = await client.post(
                    "/api/conversations",
                    json={"scene": "search", "title": "task-start-api-benchmark"},
                )
                if created.status_code != 200:
                    raise AssertionError(created.text)
                cid = created.json()["id"]

                for index in range(repeats):
                    started = time.perf_counter()
                    response = await client.post(
                        f"/api/conversations/{cid}/messages",
                        json={
                            "content": f"检查搜索露营灯 #{index}",
                            "attachments": [],
                            "allow_network": False,
                        },
                    )
                    samples.append((time.perf_counter() - started) * 1000.0)
                    if response.status_code != 200:
                        raise AssertionError(
                            f"task start failed: {response.status_code} {response.text}"
                        )
                    run_id = response.json()["run_id"]
                    with api_module.RUN_LOCK:
                        api_module.RUNS.pop(run_id, None)
                    api_module._PERSIST_META.pop(run_id, None)
                    api_module.store.delete_run(
                        run_id,
                        owner_id=api_module.WORKER_ID,
                    )
    finally:
        api_module._execute = original_execute

    return _summary(samples)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark task-start durable write amplification."
    )
    parser.add_argument("--repeats", type=int, default=40)
    args = parser.parse_args()
    repeats = max(10, min(50, args.repeats))

    with tempfile.TemporaryDirectory(prefix="xushu-task-start-api-") as directory:
        os.environ["LINGJING_DATA_DIR"] = str(Path(directory))
        os.environ["LINGJING_ENV"] = "development"
        os.environ["LINGJING_TRUST_PROXY_IP"] = "0"
        result = {
            "repeats": repeats,
            "store_three_write_start": _store_start_benchmark(repeats),
            "api_task_start": asyncio.run(_api_start_benchmark(repeats)),
        }

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
