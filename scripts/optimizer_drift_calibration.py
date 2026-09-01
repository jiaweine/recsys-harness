from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lingjing_harness.algorithms import evolution_core as core  # noqa: E402
from lingjing_harness.runtime.optimizer_observation_drift import (  # noqa: E402
    detect_optimizer_observation_drift,
)


BASE_X = (0.10, 0.30, 0.60, 0.90)
BASE_SCORES = (0.10, 0.30, 0.60, 0.90)


def _dimension() -> Any:
    return core.EvolutionDimension(
        name="x",
        kind="continuous",
        group="independent",
        low=0.0,
        high=1.0,
    )


def _row(x: float, score: float, *, updated_at: float) -> dict[str, Any]:
    return {
        "config": {"x": float(x)},
        "objective": float(score),
        "feasible": True,
        "updated_at": float(updated_at),
        "seen_count": 1,
    }


def _cohort(
    *,
    xs: Sequence[float],
    scores: Sequence[float],
    updated_at: float,
    rng: random.Random,
    score_noise: float = 0.0,
    x_jitter: float = 0.006,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for x, score in zip(xs, scores, strict=True):
        jittered_x = min(1.0, max(0.0, float(x) + rng.uniform(-x_jitter, x_jitter)))
        noisy_score = float(score) + (rng.gauss(0.0, score_noise) if score_noise else 0.0)
        rows.append(_row(jittered_x, noisy_score, updated_at=updated_at))
    return rows


def _stationary_case(rng: random.Random, noise: float) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for cohort_index in range(4):
        observations.extend(
            _cohort(
                xs=BASE_X,
                scores=BASE_SCORES,
                updated_at=float(4_000 - 1_000 * cohort_index),
                rng=rng,
                score_noise=noise,
            )
        )
    return observations


def _level_shift_case(rng: random.Random, noise: float = 0.0) -> list[dict[str, Any]]:
    observations = _cohort(
        xs=BASE_X,
        scores=tuple(score + 0.75 for score in BASE_SCORES),
        updated_at=4_000.0,
        rng=rng,
        score_noise=noise,
    )
    for cohort_index in range(1, 4):
        observations.extend(
            _cohort(
                xs=BASE_X,
                scores=BASE_SCORES,
                updated_at=float(4_000 - 1_000 * cohort_index),
                rng=rng,
                score_noise=noise,
            )
        )
    return observations


def _region_shift_case(rng: random.Random) -> list[dict[str, Any]]:
    observations = _cohort(
        xs=(0.02, 0.07, 0.12, 0.17),
        scores=BASE_SCORES,
        updated_at=4_000.0,
        rng=rng,
        score_noise=0.01,
        x_jitter=0.003,
    )
    for cohort_index in range(1, 4):
        observations.extend(
            _cohort(
                xs=(0.80, 0.86, 0.92, 0.98),
                scores=BASE_SCORES,
                updated_at=float(4_000 - 1_000 * cohort_index),
                rng=rng,
                score_noise=0.01,
                x_jitter=0.003,
            )
        )
    return observations


def _order_inversion_case(rng: random.Random) -> list[dict[str, Any]]:
    observations = _cohort(
        xs=BASE_X,
        scores=tuple(reversed(BASE_SCORES)),
        updated_at=4_000.0,
        rng=rng,
        score_noise=0.01,
    )
    for cohort_index in range(1, 4):
        observations.extend(
            _cohort(
                xs=BASE_X,
                scores=BASE_SCORES,
                updated_at=float(4_000 - 1_000 * cohort_index),
                rng=rng,
                score_noise=0.01,
            )
        )
    return observations


def _contrast_shift_case(rng: random.Random) -> list[dict[str, Any]]:
    observations = _cohort(
        xs=BASE_X,
        scores=(0.10, 0.80, 1.80, 3.00),
        updated_at=4_000.0,
        rng=rng,
        score_noise=0.01,
    )
    for cohort_index in range(1, 4):
        observations.extend(
            _cohort(
                xs=BASE_X,
                scores=BASE_SCORES,
                updated_at=float(4_000 - 1_000 * cohort_index),
                rng=rng,
                score_noise=0.01,
            )
        )
    return observations


def _sequential_case(rng: random.Random) -> list[dict[str, Any]]:
    return [
        *_cohort(
            xs=(0.13, 0.33, 0.63, 0.93),
            scores=(0.60, 0.10, 0.30, 0.90),
            updated_at=3_000.0,
            rng=rng,
            score_noise=0.002,
            x_jitter=0.001,
        ),
        *_cohort(
            xs=(0.12, 0.32, 0.62, 0.92),
            scores=(0.90, 0.60, 0.30, 0.10),
            updated_at=2_000.0,
            rng=rng,
            score_noise=0.002,
            x_jitter=0.001,
        ),
        *_cohort(
            xs=BASE_X,
            scores=BASE_SCORES,
            updated_at=1_000.0,
            rng=rng,
            score_noise=0.002,
            x_jitter=0.001,
        ),
    ]


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def _evaluate_case(
    *,
    name: str,
    seeds: int,
    generator: Callable[[random.Random], list[dict[str, Any]]],
    expected_change: bool,
    expected_recent_rows: int | None = None,
) -> dict[str, Any]:
    detected = 0
    cutoff_matches = 0
    wall_ms: list[float] = []
    candidate_splits: list[int] = []
    severities: list[float] = []
    for seed in range(seeds):
        observations = generator(random.Random(seed))
        started = time.perf_counter()
        result = detect_optimizer_observation_drift(
            dimensions=[_dimension()],
            observations=observations,
        )
        wall_ms.append((time.perf_counter() - started) * 1_000.0)
        if int(result.get("new_evaluator_calls", 0) or 0) != 0:
            raise AssertionError("drift calibration must never spend evaluator calls")
        changed = bool(result.get("change_detected"))
        detected += int(changed)
        candidate_splits.append(int(result.get("candidate_splits", 0) or 0))
        severities.append(float(result.get("severity", 0.0) or 0.0))
        if expected_recent_rows is not None and changed:
            cutoff_matches += int(int(result.get("recent_rows", 0) or 0) == expected_recent_rows)

    detection_rate = detected / seeds
    cutoff_accuracy = cutoff_matches / detected if detected else 0.0
    return {
        "name": name,
        "seeds": seeds,
        "expected_change": expected_change,
        "detection_rate": detection_rate,
        "false_positive_rate": detection_rate if not expected_change else 0.0,
        "cutoff_accuracy_given_detection": cutoff_accuracy if expected_recent_rows is not None else None,
        "mean_candidate_splits": mean(candidate_splits),
        "mean_severity": mean(severities),
        "mean_wall_ms": mean(wall_ms),
        "p95_wall_ms": _percentile(wall_ms, 0.95),
        "new_evaluator_calls": 0,
    }


def run_calibration(*, seeds: int = 128) -> dict[str, Any]:
    if seeds < 1:
        raise ValueError("seeds must be >= 1")
    cases = [
        _evaluate_case(
            name="stationary_low_noise",
            seeds=seeds,
            generator=lambda rng: _stationary_case(rng, 0.01),
            expected_change=False,
        ),
        _evaluate_case(
            name="stationary_moderate_noise",
            seeds=seeds,
            generator=lambda rng: _stationary_case(rng, 0.06),
            expected_change=False,
        ),
        _evaluate_case(
            name="stationary_high_noise",
            seeds=seeds,
            generator=lambda rng: _stationary_case(rng, 0.12),
            expected_change=False,
        ),
        _evaluate_case(
            name="pure_level_shift",
            seeds=seeds,
            generator=lambda rng: _level_shift_case(rng, 0.0),
            expected_change=False,
        ),
        _evaluate_case(
            name="level_shift_low_noise",
            seeds=seeds,
            generator=lambda rng: _level_shift_case(rng, 0.01),
            expected_change=False,
        ),
        _evaluate_case(
            name="exploration_region_shift",
            seeds=seeds,
            generator=_region_shift_case,
            expected_change=False,
        ),
        _evaluate_case(
            name="order_inversion",
            seeds=seeds,
            generator=_order_inversion_case,
            expected_change=True,
            expected_recent_rows=4,
        ),
        _evaluate_case(
            name="contrast_shift",
            seeds=seeds,
            generator=_contrast_shift_case,
            expected_change=True,
            expected_recent_rows=4,
        ),
        _evaluate_case(
            name="sequential_latest_break",
            seeds=seeds,
            generator=_sequential_case,
            expected_change=True,
            expected_recent_rows=4,
        ),
    ]
    false_positive_cases = [case for case in cases if not case["expected_change"]]
    drift_cases = [case for case in cases if case["expected_change"]]
    return {
        "method": "deterministic_paid-observation_drift_calibration",
        "seeds_per_case": seeds,
        "case_count": len(cases),
        "cases": cases,
        "max_false_positive_rate": max(
            float(case["false_positive_rate"]) for case in false_positive_cases
        ),
        "min_detection_rate": min(float(case["detection_rate"]) for case in drift_cases),
        "min_cutoff_accuracy_given_detection": min(
            float(case["cutoff_accuracy_given_detection"] or 0.0) for case in drift_cases
        ),
        "max_p95_wall_ms": max(float(case["p95_wall_ms"]) for case in cases),
        "new_evaluator_calls": 0,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate optimizer drift detection on deterministic synthetic regimes.")
    parser.add_argument("--seeds", type=int, default=128)
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_calibration(seeds=args.seeds)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
