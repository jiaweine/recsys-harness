from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

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


def _spec(experiment_id: str = "concurrent-exp") -> OnlineExperimentSpec:
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
        stages=(
            RampStage(0, 0.25, 200),
            RampStage(1, 0.5, 300),
        ),
        outcome_alpha=0.05,
        srm_alpha=0.01,
    )


def _binary(index: int, rate: float) -> float:
    return 1.0 if index % 100 < round(rate * 100) else 0.0


def _winning_stage_zero_rows() -> list[OnlineObservation]:
    control_count = 300
    candidate_count = 100
    total = control_count + candidate_count
    rows = []
    c_used = 0
    t_used = 0
    for sequence in range(total):
        desired_t = round((sequence + 1) * candidate_count / total)
        candidate = desired_t > t_used
        if candidate:
            index = t_used
            t_used += 1
            arm = "candidate"
            unit_id = f"win-t-{index}"
            conversion = 0.90
            error = 0.01
        else:
            index = c_used
            c_used += 1
            arm = "control"
            unit_id = f"win-c-{index}"
            conversion = 0.10
            error = 0.30
        rows.append(
            OnlineObservation(
                unit_id=unit_id,
                sequence=sequence,
                epoch_id="e0",
                arm=arm,
                metrics={
                    "conversion": _binary(index, conversion),
                    "error": _binary(index, error),
                },
            )
        )
    return rows


def test_multiple_store_instances_serialize_unique_ingest_batches(tmp_path: Path):
    path = tmp_path / "workspace.db"
    DurableOnlineExperimentStore(path).create_experiment(_spec(), initial_epoch_id="e0")
    workers = 8
    per_worker = 50
    barrier = Barrier(workers)

    def ingest(worker: int):
        store = DurableOnlineExperimentStore(path)
        rows = [
            OnlineObservation(
                unit_id=f"w{worker}-u{index}",
                sequence=worker * per_worker + index,
                epoch_id="e0",
                arm="control" if (worker * per_worker + index) % 4 else "candidate",
            )
            for index in range(per_worker)
        ]
        barrier.wait()
        return store.ingest_observations("concurrent-exp", rows)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(ingest, range(workers)))

    final = DurableOnlineExperimentStore(path).get_experiment("concurrent-exp")
    assert sum(result["inserted_units"] for result in results) == workers * per_worker
    assert final["observation_count"] == workers * per_worker
    assert final["version"] == 1 + workers


def test_concurrent_monotonic_maturation_preserves_both_new_fields(tmp_path: Path):
    path = tmp_path / "workspace.db"
    base = DurableOnlineExperimentStore(path)
    base.create_experiment(_spec(), initial_epoch_id="e0")
    base.ingest_observations(
        "concurrent-exp",
        [OnlineObservation("u-1", 0, "e0", "control")],
    )
    barrier = Barrier(2)

    def mature(metrics):
        store = DurableOnlineExperimentStore(path)
        barrier.wait()
        return store.ingest_observations(
            "concurrent-exp",
            [OnlineObservation("u-1", 0, "e0", "control", metrics=metrics)],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(mature, ({"conversion": 1.0}, {"error": 0.0})))

    # Both transactions observed the other commit after SQLite serialization and
    # monotonically extended the same durable unit rather than overwriting it.
    assert sorted(result["matured_units"] for result in results) == [1, 1]
    assert DurableOnlineExperimentStore(path).get_experiment("concurrent-exp")["version"] == 4


def test_two_workers_racing_same_transition_have_single_cas_winner(tmp_path: Path):
    path = tmp_path / "workspace.db"
    setup = DurableOnlineExperimentStore(path)
    setup.create_experiment(_spec(), initial_epoch_id="e0")
    ingest = setup.ingest_observations("concurrent-exp", _winning_stage_zero_rows())
    assert setup.evaluate("concurrent-exp")["decision"]["action"] == "advance_ramp"
    expected_version = ingest["version"]
    barrier = Barrier(2)

    def transition(epoch_id: str):
        store = DurableOnlineExperimentStore(path)
        barrier.wait()
        try:
            result = store.apply_recommendation(
                "concurrent-exp",
                expected_version=expected_version,
                action="advance_ramp",
                new_epoch_id=epoch_id,
            )
            return ("success", result["experiment"]["current_epoch_id"])
        except ExperimentConflict as exc:
            return ("conflict", exc.current_version)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(transition, ("e1-a", "e1-b")))

    successes = [row for row in outcomes if row[0] == "success"]
    conflicts = [row for row in outcomes if row[0] == "conflict"]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0][1] == expected_version + 1

    final = DurableOnlineExperimentStore(path).get_experiment("concurrent-exp")
    assert final["version"] == expected_version + 1
    assert final["current_stage_index"] == 1
    assert final["current_epoch_id"] in {"e1-a", "e1-b"}
    assert len(final["epochs"]) == 2
