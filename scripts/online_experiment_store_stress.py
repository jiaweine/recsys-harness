from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from typing import Any

from lingjing_harness.online_experiment_store import (
    DurableOnlineExperimentStore,
    ExperimentConflict,
)
from lingjing_harness.online_experiments import (
    OnlineExperimentSpec,
    OnlineMetricSpec,
    OnlineObservation,
    RampStage,
)


def _spec(experiment_id: str, *, final_only: bool = False) -> OnlineExperimentSpec:
    stages = (
        (RampStage(0, 0.5, 200),)
        if final_only
        else (RampStage(0, 0.25, 200), RampStage(1, 0.5, 300))
    )
    return OnlineExperimentSpec(
        experiment_id=experiment_id,
        control_arm="control",
        candidate_arm="candidate",
        metrics=(
            OnlineMetricSpec(
                name="conversion",
                role="primary",
                kind="bernoulli",
                direction="higher_is_better",
                advance_threshold=0.05,
                rollback_threshold=-0.05,
                minimum_samples_per_arm=50,
            ),
            OnlineMetricSpec(
                name="error",
                role="guardrail",
                kind="bernoulli",
                direction="lower_is_better",
                advance_threshold=-0.03,
                rollback_threshold=-0.08,
                minimum_samples_per_arm=50,
            ),
        ),
        stages=stages,
        outcome_alpha=0.05,
        srm_alpha=0.01,
    )


def _binary(index: int, rate: float) -> float:
    return 1.0 if index % 100 < round(rate * 100) else 0.0


def _winning_rows() -> list[OnlineObservation]:
    rows = []
    control = 0
    candidate = 0
    for sequence in range(400):
        desired_candidate = round((sequence + 1) * 0.25)
        treatment = desired_candidate > candidate
        if treatment:
            index = candidate
            candidate += 1
            rows.append(
                OnlineObservation(
                    f"win-t-{index}",
                    sequence,
                    "e0",
                    "candidate",
                    {"conversion": _binary(index, 0.9), "error": _binary(index, 0.01)},
                )
            )
        else:
            index = control
            control += 1
            rows.append(
                OnlineObservation(
                    f"win-c-{index}",
                    sequence,
                    "e0",
                    "control",
                    {"conversion": _binary(index, 0.1), "error": _binary(index, 0.3)},
                )
            )
    return rows


def run_stress(*, workers: int = 8, units: int = 2048) -> dict[str, Any]:
    if workers < 2:
        raise ValueError("workers must be >= 2")
    if units < workers or units % workers:
        raise ValueError("units must be divisible by workers")
    with TemporaryDirectory(prefix="xushu-online-exp-") as directory:
        path = Path(directory) / "workspace.db"
        root = DurableOnlineExperimentStore(path)
        root.create_experiment(_spec("ingest"), initial_epoch_id="e0")
        per_worker = units // workers
        start = Barrier(workers)

        def ingest(worker: int):
            store = DurableOnlineExperimentStore(path)
            begin = worker * per_worker
            rows = [
                OnlineObservation(
                    unit_id=f"w{worker}-u{offset}",
                    sequence=begin + offset,
                    epoch_id="e0",
                    arm="candidate" if (begin + offset) % 4 == 0 else "control",
                )
                for offset in range(per_worker)
            ]
            start.wait()
            return store.ingest_observations("ingest", rows)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            ingest_results = list(pool.map(ingest, range(workers)))

        after_ingest = DurableOnlineExperimentStore(path).get_experiment("ingest")
        if after_ingest["observation_count"] != units:
            raise AssertionError(after_ingest)
        if after_ingest["version"] != workers + 1:
            raise AssertionError(after_ingest)

        maturation_barrier = Barrier(workers)

        def mature(worker: int):
            store = DurableOnlineExperimentStore(path)
            begin = worker * per_worker
            rows = [
                OnlineObservation(
                    unit_id=f"w{worker}-u{offset}",
                    sequence=begin + offset,
                    epoch_id="e0",
                    arm="candidate" if (begin + offset) % 4 == 0 else "control",
                    metrics={
                        "conversion": float((begin + offset) % 7 == 0),
                        "error": float((begin + offset) % 19 == 0),
                    },
                )
                for offset in range(per_worker)
            ]
            maturation_barrier.wait()
            return store.ingest_observations("ingest", rows)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            mature_results = list(pool.map(mature, range(workers)))

        after_maturation = DurableOnlineExperimentStore(path).get_experiment("ingest")
        if after_maturation["version"] != 2 * workers + 1:
            raise AssertionError(after_maturation)

        root.create_experiment(_spec("cas"), initial_epoch_id="e0")
        evidence_version = root.ingest_observations("cas", _winning_rows())["version"]
        if root.evaluate("cas")["decision"]["action"] != "advance_ramp":
            raise AssertionError("stress fixture did not produce ramp evidence")
        cas_barrier = Barrier(workers)

        def transition(worker: int):
            store = DurableOnlineExperimentStore(path)
            cas_barrier.wait()
            try:
                store.apply_recommendation(
                    "cas",
                    expected_version=evidence_version,
                    action="advance_ramp",
                    new_epoch_id=f"e1-worker-{worker}",
                )
                return "success"
            except ExperimentConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(transition, range(workers)))
        if outcomes.count("success") != 1 or outcomes.count("conflict") != workers - 1:
            raise AssertionError(outcomes)
        cas_final = DurableOnlineExperimentStore(path).get_experiment("cas")
        if cas_final["version"] != evidence_version + 1:
            raise AssertionError(cas_final)
        if len(cas_final["epochs"]) != 2:
            raise AssertionError(cas_final)

        return {
            "workers": workers,
            "units": units,
            "ingest_batches": len(ingest_results),
            "maturation_batches": len(mature_results),
            "final_ingest_version": after_maturation["version"],
            "cas_successes": outcomes.count("success"),
            "cas_conflicts": outcomes.count("conflict"),
            "cas_final_version": cas_final["version"],
            "production_activation": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--units", type=int, default=2048)
    args = parser.parse_args()
    report = run_stress(workers=args.workers, units=args.units)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
