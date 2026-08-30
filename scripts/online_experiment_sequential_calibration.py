from __future__ import annotations

import argparse
import json
from random import Random
from typing import Any

from lingjing_harness import online_experiments as online


def _null_spec(*, outcome_alpha: float, srm_alpha: float) -> online.OnlineExperimentSpec:
    return online.OnlineExperimentSpec(
        experiment_id="null-calibration",
        control_arm="control",
        candidate_arm="candidate",
        metrics=(
            online.OnlineMetricSpec(
                name="conversion",
                role="primary",
                kind="bernoulli",
                direction="higher_is_better",
                advance_threshold=0.0,
                rollback_threshold=-0.2,
                minimum_samples_per_arm=1,
            ),
        ),
        stages=(online.RampStage(0, 0.5, 2),),
        outcome_alpha=outcome_alpha,
        srm_alpha=srm_alpha,
    )


def _effect_false_ramp_trial(
    rng: Random,
    *,
    probability: float,
    max_per_arm: int,
    look_every: int,
    outcome_alpha: float,
) -> bool:
    control: list[float] = []
    candidate: list[float] = []
    metric = _null_spec(outcome_alpha=outcome_alpha, srm_alpha=0.01).primary_metric
    for index in range(max_per_arm):
        control.append(1.0 if rng.random() < probability else 0.0)
        candidate.append(1.0 if rng.random() < probability else 0.0)
        n = index + 1
        if n < 5 or n % look_every:
            continue
        effect = online._effect_confidence_sequence(
            control,
            candidate,
            metric,
            alpha=outcome_alpha,
        )
        if float(effect["benefit_lower"]) >= 0.0:
            return True
    return False


def _srm_false_alarm_trial(
    rng: Random,
    *,
    units: int,
    srm_alpha: float,
) -> bool:
    spec = _null_spec(outcome_alpha=0.05, srm_alpha=srm_alpha)
    epoch = online.AllocationEpoch("e0", 0, 0.5)
    rows = [
        online.OnlineObservation(
            unit_id=f"u-{index}",
            sequence=index,
            epoch_id="e0",
            arm="candidate" if rng.random() < 0.5 else "control",
        )
        for index in range(units)
    ]
    evidence = online._srm_evidence(rows, spec, (epoch,))
    return bool(evidence["failed_anytime"])


def run_calibration(
    *,
    trials: int = 300,
    seed: int = 20260830,
    max_per_arm: int = 400,
    srm_units: int = 600,
    look_every: int = 10,
    outcome_alpha: float = 0.05,
    srm_alpha: float = 0.01,
) -> dict[str, Any]:
    if trials < 1:
        raise ValueError("trials must be >= 1")
    rng = Random(seed)
    effect_false = sum(
        _effect_false_ramp_trial(
            rng,
            probability=0.30,
            max_per_arm=max_per_arm,
            look_every=look_every,
            outcome_alpha=outcome_alpha,
        )
        for _ in range(trials)
    )
    srm_false = sum(
        _srm_false_alarm_trial(rng, units=srm_units, srm_alpha=srm_alpha)
        for _ in range(trials)
    )
    return {
        "trials": trials,
        "seed": seed,
        "continuous_looks": max_per_arm // look_every,
        "outcome_alpha": outcome_alpha,
        "srm_alpha": srm_alpha,
        "primary_false_ramps": effect_false,
        "primary_false_ramp_rate": effect_false / trials,
        "srm_false_alarms": srm_false,
        "srm_false_alarm_rate": srm_false / trials,
        "calibration_role": "implementation_smoke_not_theorem_replacement",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260830)
    args = parser.parse_args()
    report = run_calibration(trials=args.trials, seed=args.seed)
    print(json.dumps(report, indent=2, sort_keys=True))
    # These are deliberately wider than the theoretical alpha levels: finite Monte
    # Carlo is a regression smoke that should catch implementation bugs, not a noisy
    # test that rejects a mathematically valid method because of sampling variation.
    if report["primary_false_ramp_rate"] > 0.10:
        raise SystemExit("primary sequential false-ramp smoke exceeded 10%")
    if report["srm_false_alarm_rate"] > 0.05:
        raise SystemExit("SRM sequential false-alarm smoke exceeded 5%")


if __name__ == "__main__":
    main()
