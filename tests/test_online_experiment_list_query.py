from __future__ import annotations

from lingjing_harness.online_experiment_store import DurableOnlineExperimentStore
from lingjing_harness.online_experiments import (
    OnlineExperimentSpec,
    OnlineMetricSpec,
    OnlineObservation,
    RampStage,
)


def _spec(experiment_id: str) -> OnlineExperimentSpec:
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
                advance_threshold=0.1,
                rollback_threshold=-0.1,
                minimum_samples_per_arm=1,
            ),
        ),
        stages=(
            RampStage(
                stage_index=0,
                candidate_fraction=0.5,
                minimum_randomized_units=2,
            ),
        ),
    )


def test_experiment_list_counts_observations_with_one_select(tmp_path, monkeypatch):
    store = DurableOnlineExperimentStore(tmp_path / "workspace.db")
    store.create_experiment(_spec("exp-empty"), initial_epoch_id="e0")
    store.create_experiment(_spec("exp-two"), initial_epoch_id="e0")
    store.ingest_observations(
        "exp-two",
        [
            OnlineObservation(
                unit_id="u-control",
                sequence=0,
                epoch_id="e0",
                arm="control",
                metrics={"conversion": 0.0},
            ),
            OnlineObservation(
                unit_id="u-candidate",
                sequence=1,
                epoch_id="e0",
                arm="candidate",
                metrics={"conversion": 1.0},
            ),
        ],
    )

    original_connect = store._connect
    statements: list[str] = []

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_connect", traced_connect)
    rows = store.list_experiments(500)

    assert len(statements) == 1
    counts = {row["experiment_id"]: row["observation_count"] for row in rows}
    assert counts == {"exp-empty": 0, "exp-two": 2}
    assert rows[0]["experiment_id"] == "exp-two"
