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


def run_benchmark(*, repeats: int, workers: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xushu-readiness-") as directory:
        os.environ["LINGJING_DATA_DIR"] = str(Path(directory))
        os.environ["LINGJING_ENV"] = "development"
        os.environ["LINGJING_TRUST_PROXY_IP"] = "0"

        import lingjing_harness.api as api

        revision = str(api.CATALOG_REVISION)
        observed = api.store.ensure_workspace_revision(revision)
        if observed != revision:
            raise AssertionError(f"workspace revision did not initialize: {observed!r}")

        expected = api.health_ready()
        if expected != {"status": "ready"}:
            raise AssertionError(f"unexpected readiness payload: {expected!r}")

        def legacy_sync_workspace() -> bool:
            shared = api.store.ensure_workspace_revision(api.CATALOG_REVISION)
            if not shared:
                return True
            with api.WORKSPACE_LOCK:
                shared = api.store.workspace_revision() or shared
                active = api._load_catalog()
                active_revision = api.catalog_fingerprint(active)
                if active_revision != shared:
                    return False

                pending = getattr(api.store, "workspace_publication_pending", None)
                if callable(pending) and pending(shared):
                    finish = getattr(api.store, "finish_workspace_publication", None)
                    if callable(finish):
                        finish(shared)
                    return True

                begin = getattr(api.store, "begin_workspace_update", None)
                abort = getattr(api.store, "abort_workspace_update", None)
                owner = "readiness-benchmark-legacy"
                if callable(begin) and callable(abort):
                    if begin(
                        owner,
                        lease_seconds=api.WORKSPACE_UPDATE_LEASE_SECONDS,
                    ):
                        abort(owner)
                finish = getattr(api.store, "finish_workspace_publication", None)
                if callable(finish):
                    finish(shared)
                return True

        def legacy_health_ready() -> dict[str, str]:
            if not legacy_sync_workspace():
                raise AssertionError("legacy sync unexpectedly failed")
            durable_revision = api.store.workspace_revision()
            updating = api.store.workspace_update_active()
            if durable_revision != api.CATALOG_REVISION or updating:
                raise AssertionError("legacy readiness unexpectedly failed")
            return {"status": "ready"}

        legacy_ready_samples = _timed(legacy_health_ready, repeats)
        sync_samples = _timed(api._sync_workspace, repeats)
        revision_samples = _timed(api.store.workspace_revision, repeats)
        update_samples = _timed(api.store.workspace_update_active, repeats)
        ready_samples = _timed(api.health_ready, repeats)

        def probe_many(iterations: int) -> int:
            completed = 0
            for _ in range(iterations):
                if api.health_ready() != {"status": "ready"}:
                    raise AssertionError("readiness changed during benchmark")
                completed += 1
            return completed

        per_worker = max(8, repeats // max(1, workers))
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            completed = sum(executor.map(probe_many, [per_worker] * max(1, workers)))
        elapsed = max(time.perf_counter() - started, 1e-9)

        return {
            "health_ready_baseline": _summary(legacy_ready_samples),
            "sync_workspace": _summary(sync_samples),
            "workspace_revision": _summary(revision_samples),
            "workspace_update_active": _summary(update_samples),
            "health_ready": _summary(ready_samples),
            "concurrent": {
                "workers": workers,
                "probes": completed,
                "elapsed_seconds": round(elapsed, 4),
                "probes_per_second": round(completed / elapsed, 2),
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark steady-state workspace readiness probes."
    )
    parser.add_argument("--repeats", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-ready-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-ready-speedup", type=float, default=0.0)
    parser.add_argument("--min-concurrent-rps", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        repeats=max(20, args.repeats),
        workers=max(1, args.workers),
    )
    failures: list[str] = []
    baseline_p50 = float(result["health_ready_baseline"]["p50_ms"])
    ready_p50 = float(result["health_ready"]["p50_ms"])
    ready_speedup = baseline_p50 / max(ready_p50, 1e-9)
    result["ready_speedup_p50"] = round(ready_speedup, 2)
    rps = float(result["concurrent"]["probes_per_second"])
    if args.max_ready_p50_ms > 0 and ready_p50 > args.max_ready_p50_ms:
        failures.append(f"health_ready p50={ready_p50} > {args.max_ready_p50_ms}")
    if args.min_ready_speedup > 0 and ready_speedup < args.min_ready_speedup:
        failures.append(
            f"readiness speedup={ready_speedup:.2f} < {args.min_ready_speedup}"
        )
    if args.min_concurrent_rps > 0 and rps < args.min_concurrent_rps:
        failures.append(f"concurrent rps={rps} < {args.min_concurrent_rps}")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if failures:
        raise SystemExit("readiness performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
