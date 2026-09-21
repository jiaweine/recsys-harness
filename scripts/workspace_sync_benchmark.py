from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import tempfile
import time
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


def _catalog_payload(*, items: int, interactions: int, events: int) -> dict:
    return {
        "items": [
            {
                "item_id": f"item-{index}",
                "title": f"Item {index}",
                "text": f"benchmark item {index}",
                "categories": [f"category-{index % 80}", f"group-{index % 17}"],
                "popularity": float(index % 100),
                "quality": 0.7,
                "freshness": 0.8,
                "eligible": True,
                "metadata": {},
            }
            for index in range(items)
        ],
        "interactions": [
            {
                "user_id": f"user-{index % 25000}",
                "item_id": f"item-{index % items}",
                "event": "click",
                "weight": 1.0,
                "timestamp": float(index),
            }
            for index in range(interactions)
        ],
        "query_labels": [],
        "events": [
            {
                "request_id": f"request-{index // 5}",
                "timestamp": float(index),
                "surface": "search" if index % 2 == 0 else "recommend",
                "item_id": f"item-{index % items}",
                "event": "impression",
                "value": 1.0,
                "position": (index % 10) + 1,
            }
            for index in range(events)
        ],
        "reward_spec": {"weights": {"click": 1.0}},
    }


def run_benchmark(*, items: int, interactions: int, events: int, repeats: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xushu-workspace-sync-") as directory:
        root = Path(directory)
        payload = _catalog_payload(
            items=items,
            interactions=interactions,
            events=events,
        )
        catalog_file = root / "catalog.json"
        catalog_file.write_text(
            json.dumps(
                {"name": "workspace-sync-benchmark", "data": payload},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

        os.environ["LINGJING_DATA_DIR"] = str(root)
        os.environ["LINGJING_ENV"] = "development"
        os.environ["LINGJING_TRUST_PROXY_IP"] = "0"

        import lingjing_harness.api as api_module

        # First sync initializes the durable workspace revision. All measured
        # samples below are steady-state reads of an unchanged workspace.
        if not api_module._sync_workspace():
            raise AssertionError("initial workspace sync failed")

        revision_reads = _timed(api_module.store.workspace_revision, repeats * 4)
        file_stats = _timed(catalog_file.stat, repeats * 8)

        counters = {"begin": 0, "abort": 0, "finish_publication": 0}
        original_begin = api_module.store.begin_workspace_update
        original_abort = api_module.store.abort_workspace_update
        original_finish = api_module.store.finish_workspace_publication

        def counted_begin(*args, **kwargs):
            counters["begin"] += 1
            return original_begin(*args, **kwargs)

        def counted_abort(*args, **kwargs):
            counters["abort"] += 1
            return original_abort(*args, **kwargs)

        def counted_finish(*args, **kwargs):
            counters["finish_publication"] += 1
            return original_finish(*args, **kwargs)

        api_module.store.begin_workspace_update = counted_begin
        api_module.store.abort_workspace_update = counted_abort
        api_module.store.finish_workspace_publication = counted_finish
        try:
            sync_samples = _timed(
                lambda: (
                    api_module._sync_workspace()
                    or (_ for _ in ()).throw(AssertionError("steady sync failed"))
                ),
                repeats,
            )
        finally:
            api_module.store.begin_workspace_update = original_begin
            api_module.store.abort_workspace_update = original_abort
            api_module.store.finish_workspace_publication = original_finish

        forced_drift_samples: list[float] = []
        for index in range(3):
            stat = catalog_file.stat()
            os.utime(
                catalog_file,
                ns=(
                    stat.st_atime_ns,
                    stat.st_mtime_ns + 1_000_000 + index,
                ),
            )
            started = time.perf_counter()
            if not api_module._sync_workspace():
                raise AssertionError("file-drift workspace sync failed")
            forced_drift_samples.append((time.perf_counter() - started) * 1000.0)

        return {
            "items": items,
            "interactions": interactions,
            "events": events,
            "catalog_bytes": catalog_file.stat().st_size,
            "workspace_sync": _summary(sync_samples),
            "forced_file_drift_sync": _summary(forced_drift_samples),
            "workspace_revision": _summary(revision_reads),
            "catalog_stat": _summary(file_stats),
            "sync_side_effect_calls": counters,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark steady-state workspace synchronization."
    )
    parser.add_argument("--items", type=int, default=5000)
    parser.add_argument("--interactions", type=int, default=50000)
    parser.add_argument("--events", type=int, default=50000)
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--max-steady-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-drift-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(100, args.items),
        interactions=max(1000, args.interactions),
        events=max(1000, args.events),
        repeats=max(3, args.repeats),
    )
    steady_p50 = float(result["workspace_sync"]["p50_ms"])
    drift_p50 = float(result["forced_file_drift_sync"]["p50_ms"])
    speedup = drift_p50 / max(steady_p50, 1e-9)
    result["steady_vs_drift_speedup_p50"] = round(speedup, 2)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    if args.max_steady_p50_ms > 0 and steady_p50 > args.max_steady_p50_ms:
        failures.append(
            f"steady workspace sync p50={steady_p50} > {args.max_steady_p50_ms}"
        )
    if args.min_drift_speedup > 0 and speedup < args.min_drift_speedup:
        failures.append(
            f"steady/drift speedup={speedup:.2f} < {args.min_drift_speedup}"
        )
    side_effects = result["sync_side_effect_calls"]
    if any(int(value) != 0 for value in side_effects.values()):
        failures.append(f"steady sync side effects are not zero: {side_effects}")
    if failures:
        raise SystemExit(
            "workspace sync performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
