from __future__ import annotations

import json
import time
from unittest.mock import patch

from optimizer_equal_budget_benchmark import run_benchmark
from lingjing_harness.algorithms import qlog_mobo


def main() -> None:
    fit_seconds = 0.0
    fit_calls = 0
    optimize_seconds = 0.0
    optimize_calls = 0
    original_fit = qlog_mobo._fit_model
    original_optimize = qlog_mobo._optimize_acquisition

    def timed_fit(*args, **kwargs):
        nonlocal fit_seconds, fit_calls
        started = time.perf_counter()
        try:
            return original_fit(*args, **kwargs)
        finally:
            fit_seconds += time.perf_counter() - started
            fit_calls += 1

    def timed_optimize(*args, **kwargs):
        nonlocal optimize_seconds, optimize_calls
        started = time.perf_counter()
        try:
            return original_optimize(*args, **kwargs)
        finally:
            optimize_seconds += time.perf_counter() - started
            optimize_calls += 1

    with (
        patch.object(qlog_mobo, "_fit_model", timed_fit),
        patch.object(qlog_mobo, "_optimize_acquisition", timed_optimize),
    ):
        report = run_benchmark(backends=("qlognehvi",), seeds=(17,))

    wall_seconds = sum(float(row["wall_seconds"]) for row in report["runs"])
    measured_seconds = fit_seconds + optimize_seconds
    payload = {
        "runs": len(report["runs"]),
        "evaluator_calls": sum(int(row["evaluator_calls"]) for row in report["runs"]),
        "wall_seconds": wall_seconds,
        "fit": {
            "calls": fit_calls,
            "seconds": fit_seconds,
            "share_of_wall": fit_seconds / wall_seconds if wall_seconds else 0.0,
        },
        "acquisition_optimize": {
            "calls": optimize_calls,
            "seconds": optimize_seconds,
            "share_of_wall": optimize_seconds / wall_seconds if wall_seconds else 0.0,
        },
        "other_seconds": max(0.0, wall_seconds - measured_seconds),
        "benchmark_summary": report["summary"]["qlognehvi"],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
