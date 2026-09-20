from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from lingjing_harness.rate_limit_maintenance import install_rate_limit_maintenance
from lingjing_harness.store import WorkspaceStore


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


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


def _saturate(store: WorkspaceStore, scope: str, limit: int, now: float) -> None:
    for offset in range(limit):
        allowed = store.consume_rate_limit(
            scope,
            limit=limit,
            window_seconds=60,
            now=now + offset * 0.001,
        )
        if not allowed:
            raise AssertionError("rate limit saturated too early")
    if store.consume_rate_limit(
        scope,
        limit=limit,
        window_seconds=60,
        now=now + 0.5,
    ):
        raise AssertionError("rate limit did not saturate")


def run_benchmark(*, sequential: int, concurrent_ops: int, workers: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xushu-rate-limit-") as directory:
        path = Path(directory) / "workspace.db"
        store = WorkspaceStore(path)
        install_rate_limit_maintenance(store)
        scope = "task:benchmark-client"
        limit = 5
        now = 1_000_000.0
        _saturate(store, scope, limit, now)

        # Warm maintenance and SQLite page cache. Keep all denied probes within
        # the same unexpired window so the benchmark isolates the saturated path.
        for _ in range(8):
            assert store.consume_rate_limit(
                scope,
                limit=limit,
                window_seconds=60,
                now=now + 1.0,
            ) is False

        denied_seq = _timed(
            lambda: store.consume_rate_limit(
                scope,
                limit=limit,
                window_seconds=60,
                now=now + 1.0,
            ),
            sequential,
        )

        fresh_index = 0

        def fresh_allowed() -> bool:
            nonlocal fresh_index
            fresh_index += 1
            return store.consume_rate_limit(
                f"task:fresh-{fresh_index}",
                limit=limit,
                window_seconds=60,
                now=now + 2.0,
            )

        fresh_allowed_samples = _timed(fresh_allowed, max(50, sequential // 2))

        stores = [WorkspaceStore(path) for _ in range(max(1, workers))]
        for candidate in stores:
            install_rate_limit_maintenance(candidate)
            # Avoid measuring each process-like store's one-time maintenance.
            candidate._rate_limit_maintenance_cleanup(now + 1.0)

        def denied(index: int) -> float:
            candidate = stores[index % len(stores)]
            started = time.perf_counter()
            allowed = candidate.consume_rate_limit(
                scope,
                limit=limit,
                window_seconds=60,
                now=now + 1.0,
            )
            if allowed:
                raise AssertionError("saturated concurrent request was allowed")
            return (time.perf_counter() - started) * 1000.0

        batch_started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            concurrent_samples = list(executor.map(denied, range(concurrent_ops)))
        concurrent_elapsed = time.perf_counter() - batch_started

        # Expiry must still be able to reopen the window after a denial flood.
        reopened = store.consume_rate_limit(
            scope,
            limit=limit,
            window_seconds=60,
            now=now + 61.0,
        )
        if not reopened:
            raise AssertionError("expired rate-limit window did not reopen")

        return {
            "sequential_denied": _summary(denied_seq),
            "fresh_allowed": _summary(fresh_allowed_samples),
            "concurrent_denied": _summary(concurrent_samples),
            "concurrent_ops": concurrent_ops,
            "workers": workers,
            "concurrent_rps": round(concurrent_ops / max(concurrent_elapsed, 1e-9), 2),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark saturated durable rate-limit denial hot path."
    )
    parser.add_argument("--sequential", type=int, default=300)
    parser.add_argument("--concurrent-ops", type=int, default=240)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-sequential-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-concurrent-p95-ms", type=float, default=0.0)
    parser.add_argument("--min-concurrent-rps", type=float, default=0.0)
    parser.add_argument("--max-fresh-allowed-p50-ms", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        sequential=max(50, args.sequential),
        concurrent_ops=max(50, args.concurrent_ops),
        workers=max(1, args.workers),
    )
    print(json.dumps(result, sort_keys=True))

    failures: list[str] = []
    sequential_p50 = float(result["sequential_denied"]["p50_ms"])
    concurrent_p95 = float(result["concurrent_denied"]["p95_ms"])
    concurrent_rps = float(result["concurrent_rps"])
    fresh_allowed_p50 = float(result["fresh_allowed"]["p50_ms"])
    if args.max_sequential_p50_ms > 0 and sequential_p50 > args.max_sequential_p50_ms:
        failures.append(
            f"sequential denied p50={sequential_p50} > {args.max_sequential_p50_ms}"
        )
    if args.max_concurrent_p95_ms > 0 and concurrent_p95 > args.max_concurrent_p95_ms:
        failures.append(
            f"concurrent denied p95={concurrent_p95} > {args.max_concurrent_p95_ms}"
        )
    if args.min_concurrent_rps > 0 and concurrent_rps < args.min_concurrent_rps:
        failures.append(
            f"concurrent denied rps={concurrent_rps} < {args.min_concurrent_rps}"
        )
    if (
        args.max_fresh_allowed_p50_ms > 0
        and fresh_allowed_p50 > args.max_fresh_allowed_p50_ms
    ):
        failures.append(
            f"fresh allowed p50={fresh_allowed_p50} > {args.max_fresh_allowed_p50_ms}"
        )
    if failures:
        raise SystemExit("rate-limit performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
