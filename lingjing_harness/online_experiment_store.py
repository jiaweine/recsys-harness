from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .online_experiments import (
    AllocationEpoch,
    OnlineExperimentSpec,
    OnlineMetricSpec,
    OnlineObservation,
    RampStage,
    evaluate_online_experiment,
)


EXPERIMENT_STATUSES = frozenset({"running", "rollback_required", "promotion_review"})
MUTATING_RECOMMENDATIONS = frozenset(
    {"advance_ramp", "rollback_recommended", "eligible_for_promotion_review"}
)


class ExperimentConflict(RuntimeError):
    def __init__(self, message: str, *, current_version: int | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version


class ExperimentStateError(RuntimeError):
    pass


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _metric_to_dict(metric: OnlineMetricSpec) -> dict[str, Any]:
    return {
        "name": metric.name,
        "role": metric.role,
        "kind": metric.kind,
        "direction": metric.direction,
        "advance_threshold": metric.advance_threshold,
        "rollback_threshold": metric.rollback_threshold,
        "minimum_samples_per_arm": metric.minimum_samples_per_arm,
        "cuped_covariate": metric.cuped_covariate,
    }


def _stage_to_dict(stage: RampStage) -> dict[str, Any]:
    return {
        "stage_index": stage.stage_index,
        "candidate_fraction": stage.candidate_fraction,
        "minimum_randomized_units": stage.minimum_randomized_units,
    }


def spec_to_dict(spec: OnlineExperimentSpec) -> dict[str, Any]:
    return {
        "experiment_id": spec.experiment_id,
        "control_arm": spec.control_arm,
        "candidate_arm": spec.candidate_arm,
        "metrics": [_metric_to_dict(metric) for metric in spec.metrics],
        "stages": [_stage_to_dict(stage) for stage in spec.stages],
        "outcome_alpha": spec.outcome_alpha,
        "srm_alpha": spec.srm_alpha,
        "srm_dirichlet_prior": spec.srm_dirichlet_prior,
    }


def spec_from_dict(raw: Mapping[str, Any]) -> OnlineExperimentSpec:
    metrics = tuple(
        OnlineMetricSpec(
            name=str(item["name"]),
            role=str(item["role"]),
            kind=str(item["kind"]),
            direction=str(item["direction"]),
            advance_threshold=float(item["advance_threshold"]),
            rollback_threshold=float(item["rollback_threshold"]),
            minimum_samples_per_arm=int(item.get("minimum_samples_per_arm", 50)),
            cuped_covariate=(
                str(item["cuped_covariate"])
                if item.get("cuped_covariate") not in (None, "")
                else None
            ),
        )
        for item in raw.get("metrics", [])
    )
    stages = tuple(
        RampStage(
            stage_index=int(item["stage_index"]),
            candidate_fraction=float(item["candidate_fraction"]),
            minimum_randomized_units=int(item["minimum_randomized_units"]),
        )
        for item in raw.get("stages", [])
    )
    return OnlineExperimentSpec(
        experiment_id=str(raw.get("experiment_id") or ""),
        control_arm=str(raw.get("control_arm") or ""),
        candidate_arm=str(raw.get("candidate_arm") or ""),
        metrics=metrics,
        stages=stages,
        outcome_alpha=float(raw.get("outcome_alpha", 0.05)),
        srm_alpha=float(raw.get("srm_alpha", 0.01)),
        srm_dirichlet_prior=float(raw.get("srm_dirichlet_prior", 0.5)),
    )


def _epoch_to_dict(epoch: AllocationEpoch, ordinal: int) -> dict[str, Any]:
    return {
        "epoch_id": epoch.epoch_id,
        "stage_index": epoch.stage_index,
        "candidate_fraction": epoch.candidate_fraction,
        "ordinal": ordinal,
    }


def _finite_unit_value(name: str, raw: Any) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _merge_monotonic(
    existing: dict[str, Any], incoming: Mapping[str, Any], *, label: str
) -> tuple[dict[str, Any], int]:
    merged = dict(existing)
    additions = 0
    for raw_key, raw_value in incoming.items():
        key = str(raw_key)
        value = _finite_unit_value(f"{label}.{key}", raw_value)
        if key in merged:
            if abs(float(merged[key]) - value) > 1e-12:
                raise ExperimentConflict(f"conflicting {label} value for {key}")
            continue
        merged[key] = value
        additions += 1
    return merged, additions


class DurableOnlineExperimentStore:
    """SQLite-backed experiment registry with evidence-version fencing.

    Assignment identity is immutable. Delayed metrics and pre-exposure covariates may
    only be added monotonically. Every evidence-changing write increments one shared
    experiment version, so a transition can atomically prove it acted on the same
    evidence snapshot that produced its recommendation.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma busy_timeout=10000")
        connection.execute("pragma foreign_keys=on")
        return connection

    def _init(self) -> None:
        sql = """
        create table if not exists online_experiments(
          experiment_id text primary key,
          spec_json text not null,
          status text not null,
          current_epoch_id text not null,
          version integer not null,
          created_at real not null,
          updated_at real not null
        );
        create table if not exists online_experiment_epochs(
          experiment_id text not null,
          epoch_id text not null,
          ordinal integer not null,
          stage_index integer not null,
          candidate_fraction real not null,
          created_at real not null,
          primary key(experiment_id,epoch_id),
          unique(experiment_id,ordinal),
          foreign key(experiment_id) references online_experiments(experiment_id) on delete cascade
        );
        create table if not exists online_experiment_observations(
          experiment_id text not null,
          unit_id text not null,
          sequence integer not null,
          epoch_id text not null,
          arm text not null,
          metrics_json text not null,
          pre_exposure_json text not null,
          created_at real not null,
          updated_at real not null,
          primary key(experiment_id,unit_id),
          unique(experiment_id,sequence),
          foreign key(experiment_id,epoch_id)
            references online_experiment_epochs(experiment_id,epoch_id) on delete restrict
        );
        create index if not exists idx_online_observations_epoch_sequence
          on online_experiment_observations(experiment_id,epoch_id,sequence);
        create table if not exists online_experiment_events(
          event_id integer primary key autoincrement,
          experiment_id text not null,
          version integer not null,
          event_type text not null,
          payload_json text not null,
          created_at real not null,
          foreign key(experiment_id) references online_experiments(experiment_id) on delete cascade
        );
        create index if not exists idx_online_events_experiment
          on online_experiment_events(experiment_id,event_id);
        """
        with self._lock, self._connect() as connection:
            connection.execute("pragma journal_mode=wal")
            connection.execute("pragma synchronous=normal")
            connection.executescript(sql)
            connection.commit()

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        experiment_id: str,
        version: int,
        event_type: str,
        payload: Mapping[str, Any],
        now: float,
    ) -> None:
        connection.execute(
            """
            insert into online_experiment_events(
              experiment_id,version,event_type,payload_json,created_at
            ) values(?,?,?,?,?)
            """,
            (experiment_id, int(version), event_type, _json_dumps(dict(payload)), now),
        )

    @staticmethod
    def _experiment_row(
        connection: sqlite3.Connection, experiment_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "select * from online_experiments where experiment_id=?", (experiment_id,)
        ).fetchone()
        if not row:
            raise KeyError(experiment_id)
        return row

    @staticmethod
    def _epochs(
        connection: sqlite3.Connection, experiment_id: str
    ) -> tuple[AllocationEpoch, ...]:
        rows = connection.execute(
            """
            select epoch_id,stage_index,candidate_fraction
            from online_experiment_epochs
            where experiment_id=? order by ordinal
            """,
            (experiment_id,),
        ).fetchall()
        return tuple(
            AllocationEpoch(
                epoch_id=str(row["epoch_id"]),
                stage_index=int(row["stage_index"]),
                candidate_fraction=float(row["candidate_fraction"]),
            )
            for row in rows
        )

    @staticmethod
    def _observations(
        connection: sqlite3.Connection, experiment_id: str
    ) -> list[OnlineObservation]:
        rows = connection.execute(
            """
            select unit_id,sequence,epoch_id,arm,metrics_json,pre_exposure_json
            from online_experiment_observations
            where experiment_id=? order by sequence
            """,
            (experiment_id,),
        ).fetchall()
        return [
            OnlineObservation(
                unit_id=str(row["unit_id"]),
                sequence=int(row["sequence"]),
                epoch_id=str(row["epoch_id"]),
                arm=str(row["arm"]),
                metrics=_json_object(row["metrics_json"]),
                pre_exposure=_json_object(row["pre_exposure_json"]),
            )
            for row in rows
        ]

    @staticmethod
    def _validate_observation(
        observation: OnlineObservation,
        *,
        spec: OnlineExperimentSpec,
        epoch_ids: set[str],
    ) -> None:
        if observation.epoch_id not in epoch_ids:
            raise ExperimentConflict(f"unknown allocation epoch: {observation.epoch_id}")
        if observation.arm not in {spec.control_arm, spec.candidate_arm}:
            raise ExperimentConflict(f"unknown experiment arm: {observation.arm}")
        metric_map = {metric.name: metric for metric in spec.metrics}
        unknown = set(observation.metrics) - set(metric_map)
        if unknown:
            raise ExperimentConflict(f"unknown online metrics: {sorted(unknown)}")
        for name, raw in observation.metrics.items():
            value = _finite_unit_value(f"metric.{name}", raw)
            if value < 0.0 or value > 1.0:
                raise ExperimentConflict(f"metric {name} must be within [0,1]")
            if metric_map[name].kind == "bernoulli" and value not in {0.0, 1.0}:
                raise ExperimentConflict(f"bernoulli metric {name} must be 0 or 1")
        for name, raw in observation.pre_exposure.items():
            if not str(name).strip():
                raise ExperimentConflict("pre-exposure covariate name must not be empty")
            _finite_unit_value(f"pre_exposure.{name}", raw)

    def create_experiment(
        self, spec: OnlineExperimentSpec, *, initial_epoch_id: str = "epoch-0"
    ) -> dict[str, Any]:
        initial_epoch_id = str(initial_epoch_id).strip()
        if not initial_epoch_id:
            raise ValueError("initial_epoch_id must not be empty")
        now = time.time()
        spec_json = _json_dumps(spec_to_dict(spec))
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            existing = connection.execute(
                "select spec_json,current_epoch_id from online_experiments where experiment_id=?",
                (spec.experiment_id,),
            ).fetchone()
            if existing:
                if (
                    str(existing["spec_json"]) == spec_json
                    and str(existing["current_epoch_id"]) == initial_epoch_id
                ):
                    connection.rollback()
                    return self.get_experiment(spec.experiment_id)
                connection.rollback()
                raise ExperimentConflict("experiment_id already exists with different contract")
            stage = spec.stages[0]
            connection.execute(
                """
                insert into online_experiments(
                  experiment_id,spec_json,status,current_epoch_id,version,created_at,updated_at
                ) values(?,?,?,?,1,?,?)
                """,
                (
                    spec.experiment_id,
                    spec_json,
                    "running",
                    initial_epoch_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                insert into online_experiment_epochs(
                  experiment_id,epoch_id,ordinal,stage_index,candidate_fraction,created_at
                ) values(?,?,?,?,?,?)
                """,
                (
                    spec.experiment_id,
                    initial_epoch_id,
                    0,
                    stage.stage_index,
                    stage.candidate_fraction,
                    now,
                ),
            )
            self._event(
                connection,
                spec.experiment_id,
                1,
                "experiment_created",
                {
                    "initial_epoch": _epoch_to_dict(
                        AllocationEpoch(initial_epoch_id, 0, stage.candidate_fraction), 0
                    ),
                    "automatic_activation": False,
                },
                now,
            )
            connection.commit()
        return self.get_experiment(spec.experiment_id)

    def list_experiments(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                select experiment_id,status,current_epoch_id,version,created_at,updated_at
                from online_experiments order by updated_at desc limit ?
                """,
                (limit,),
            ).fetchall()
            result = []
            for row in rows:
                count = connection.execute(
                    "select count(*) from online_experiment_observations where experiment_id=?",
                    (row["experiment_id"],),
                ).fetchone()[0]
                result.append({**dict(row), "observation_count": int(count)})
            return result

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._experiment_row(connection, experiment_id)
            spec = spec_from_dict(_json_object(row["spec_json"]))
            epochs = self._epochs(connection, experiment_id)
            observation_count = int(
                connection.execute(
                    "select count(*) from online_experiment_observations where experiment_id=?",
                    (experiment_id,),
                ).fetchone()[0]
            )
        current = next(epoch for epoch in epochs if epoch.epoch_id == row["current_epoch_id"])
        return {
            "experiment_id": experiment_id,
            "status": str(row["status"]),
            "version": int(row["version"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "spec": spec_to_dict(spec),
            "epochs": [_epoch_to_dict(epoch, index) for index, epoch in enumerate(epochs)],
            "current_epoch_id": current.epoch_id,
            "current_stage_index": current.stage_index,
            "current_candidate_fraction": current.candidate_fraction,
            "observation_count": observation_count,
            "automatic_activation": False,
        }

    def ingest_observations(
        self, experiment_id: str, observations: Iterable[OnlineObservation]
    ) -> dict[str, Any]:
        incoming = list(observations)
        if not incoming:
            raise ValueError("observations must not be empty")
        now = time.time()
        inserted = 0
        matured = 0
        idempotent = 0
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            experiment = self._experiment_row(connection, experiment_id)
            spec = spec_from_dict(_json_object(experiment["spec_json"]))
            epochs = self._epochs(connection, experiment_id)
            epoch_ids = {epoch.epoch_id for epoch in epochs}
            current_epoch_id = str(experiment["current_epoch_id"])
            status = str(experiment["status"])
            existing_max_sequence = connection.execute(
                """
                select max(sequence) from online_experiment_observations
                where experiment_id=?
                """,
                (experiment_id,),
            ).fetchone()[0]
            for observation in incoming:
                self._validate_observation(observation, spec=spec, epoch_ids=epoch_ids)
                existing = connection.execute(
                    """
                    select sequence,epoch_id,arm,metrics_json,pre_exposure_json
                    from online_experiment_observations
                    where experiment_id=? and unit_id=?
                    """,
                    (experiment_id, observation.unit_id),
                ).fetchone()
                if not existing:
                    if status != "running":
                        connection.rollback()
                        raise ExperimentConflict(
                            f"new randomized assignments are disabled while experiment is {status}"
                        )
                    if observation.epoch_id != current_epoch_id:
                        connection.rollback()
                        raise ExperimentConflict(
                            "new randomized assignments must use the current allocation epoch"
                        )
                    if (
                        existing_max_sequence is not None
                        and int(observation.sequence) <= int(existing_max_sequence)
                    ):
                        connection.rollback()
                        raise ExperimentConflict(
                            "new assignment sequence must follow existing assignment history"
                        )
                    sequence_owner = connection.execute(
                        """
                        select unit_id from online_experiment_observations
                        where experiment_id=? and sequence=?
                        """,
                        (experiment_id, int(observation.sequence)),
                    ).fetchone()
                    if sequence_owner:
                        connection.rollback()
                        raise ExperimentConflict(
                            f"assignment sequence already owned by unit {sequence_owner['unit_id']}"
                        )
                    connection.execute(
                        """
                        insert into online_experiment_observations(
                          experiment_id,unit_id,sequence,epoch_id,arm,metrics_json,
                          pre_exposure_json,created_at,updated_at
                        ) values(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            experiment_id,
                            observation.unit_id,
                            int(observation.sequence),
                            observation.epoch_id,
                            observation.arm,
                            _json_dumps(dict(observation.metrics)),
                            _json_dumps(dict(observation.pre_exposure)),
                            now,
                            now,
                        ),
                    )
                    inserted += 1
                    continue
                if (
                    int(existing["sequence"]) != int(observation.sequence)
                    or str(existing["epoch_id"]) != observation.epoch_id
                    or str(existing["arm"]) != observation.arm
                ):
                    connection.rollback()
                    raise ExperimentConflict("randomized assignment identity is immutable")
                metrics, metric_additions = _merge_monotonic(
                    _json_object(existing["metrics_json"]),
                    observation.metrics,
                    label="metric",
                )
                covariates, covariate_additions = _merge_monotonic(
                    _json_object(existing["pre_exposure_json"]),
                    observation.pre_exposure,
                    label="pre_exposure",
                )
                additions = metric_additions + covariate_additions
                if additions:
                    connection.execute(
                        """
                        update online_experiment_observations
                        set metrics_json=?,pre_exposure_json=?,updated_at=?
                        where experiment_id=? and unit_id=?
                        """,
                        (
                            _json_dumps(metrics),
                            _json_dumps(covariates),
                            now,
                            experiment_id,
                            observation.unit_id,
                        ),
                    )
                    matured += 1
                else:
                    idempotent += 1
            changed = inserted + matured
            version = int(experiment["version"])
            if changed:
                version += 1
                connection.execute(
                    "update online_experiments set version=?,updated_at=? where experiment_id=?",
                    (version, now, experiment_id),
                )
                self._event(
                    connection,
                    experiment_id,
                    version,
                    "observations_ingested",
                    {
                        "batch_size": len(incoming),
                        "inserted_units": inserted,
                        "matured_units": matured,
                        "idempotent_units": idempotent,
                    },
                    now,
                )
            connection.commit()
        return {
            "experiment_id": experiment_id,
            "version": version,
            "batch_size": len(incoming),
            "inserted_units": inserted,
            "matured_units": matured,
            "idempotent_units": idempotent,
            "evidence_changed": bool(changed),
        }

    @staticmethod
    def _evaluate_from_connection(
        connection: sqlite3.Connection, experiment_id: str
    ) -> dict[str, Any]:
        experiment = DurableOnlineExperimentStore._experiment_row(connection, experiment_id)
        spec = spec_from_dict(_json_object(experiment["spec_json"]))
        epochs = DurableOnlineExperimentStore._epochs(connection, experiment_id)
        observations = DurableOnlineExperimentStore._observations(connection, experiment_id)
        evaluation = evaluate_online_experiment(
            observations,
            spec,
            epochs=epochs,
            current_epoch_id=str(experiment["current_epoch_id"]),
        )
        evaluation["registry"] = {
            "status": str(experiment["status"]),
            "version": int(experiment["version"]),
            "automatic_activation": False,
        }
        return evaluation

    def evaluate(self, experiment_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("begin")
            evaluation = self._evaluate_from_connection(connection, experiment_id)
            connection.commit()
        return evaluation

    def traffic_directive(self, experiment_id: str) -> dict[str, Any]:
        record = self.get_experiment(experiment_id)
        status = record["status"]
        current_fraction = float(record["current_candidate_fraction"])
        if status == "rollback_required":
            recommendation = "stop_candidate_traffic"
            recommended_fraction = 0.0
        elif status == "promotion_review":
            recommendation = "hold_controlled_allocation_pending_promotion_review"
            recommended_fraction = current_fraction
        else:
            recommendation = "serve_current_controlled_allocation"
            recommended_fraction = current_fraction
        return {
            "experiment_id": experiment_id,
            "version": record["version"],
            "current_epoch_id": record["current_epoch_id"],
            "approved_controlled_candidate_fraction": current_fraction,
            "recommended_candidate_fraction": recommended_fraction,
            "recommendation": recommendation,
            "automatic_apply": False,
            "production_activation": False,
        }

    def apply_recommendation(
        self,
        experiment_id: str,
        *,
        expected_version: int,
        action: str,
        new_epoch_id: str | None = None,
    ) -> dict[str, Any]:
        action = str(action).strip()
        if action not in MUTATING_RECOMMENDATIONS:
            raise ValueError("only mutating evidence recommendations can be applied")
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            experiment = self._experiment_row(connection, experiment_id)
            current_version = int(experiment["version"])
            if current_version != int(expected_version):
                connection.rollback()
                raise ExperimentConflict(
                    "stale experiment version", current_version=current_version
                )
            if str(experiment["status"]) != "running":
                connection.rollback()
                raise ExperimentStateError(
                    f"experiment is not transitionable from {experiment['status']}"
                )
            evaluation = self._evaluate_from_connection(connection, experiment_id)
            recommended = str(evaluation["decision"]["action"])
            if recommended != action:
                connection.rollback()
                raise ExperimentConflict(
                    f"fresh evidence recommends {recommended}, not {action}",
                    current_version=current_version,
                )
            next_version = current_version + 1
            event_payload: dict[str, Any] = {
                "evidence_version": current_version,
                "decision": evaluation["decision"],
                "srm": {
                    "failed_anytime": evaluation["srm"]["failed_anytime"],
                    "max_e_value": evaluation["srm"]["max_e_value"],
                    "first_crossing_sequence": evaluation["srm"]["first_crossing_sequence"],
                },
                "metric_statuses": {
                    name: row.get("status") for name, row in evaluation["metrics"].items()
                },
                "automatic_activation": False,
            }
            if action == "advance_ramp":
                next_stage = evaluation["decision"].get("next_stage_index")
                if next_stage is None:
                    connection.rollback()
                    raise ExperimentStateError("advance recommendation has no next stage")
                epoch_id = str(new_epoch_id or "").strip()
                if not epoch_id:
                    connection.rollback()
                    raise ValueError("new_epoch_id is required to advance the ramp")
                spec = spec_from_dict(_json_object(experiment["spec_json"]))
                stage = spec.stages[int(next_stage)]
                ordinal = int(
                    connection.execute(
                        "select count(*) from online_experiment_epochs where experiment_id=?",
                        (experiment_id,),
                    ).fetchone()[0]
                )
                try:
                    connection.execute(
                        """
                        insert into online_experiment_epochs(
                          experiment_id,epoch_id,ordinal,stage_index,candidate_fraction,created_at
                        ) values(?,?,?,?,?,?)
                        """,
                        (
                            experiment_id,
                            epoch_id,
                            ordinal,
                            stage.stage_index,
                            stage.candidate_fraction,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise ExperimentConflict("new allocation epoch id already exists") from exc
                connection.execute(
                    """
                    update online_experiments
                    set current_epoch_id=?,version=?,updated_at=? where experiment_id=?
                    """,
                    (epoch_id, next_version, now, experiment_id),
                )
                event_type = "ramp_advanced"
                event_payload["new_epoch"] = {
                    "epoch_id": epoch_id,
                    "ordinal": ordinal,
                    "stage_index": stage.stage_index,
                    "candidate_fraction": stage.candidate_fraction,
                }
            elif action == "rollback_recommended":
                connection.execute(
                    """
                    update online_experiments
                    set status='rollback_required',version=?,updated_at=? where experiment_id=?
                    """,
                    (next_version, now, experiment_id),
                )
                event_type = "rollback_marked"
            else:
                connection.execute(
                    """
                    update online_experiments
                    set status='promotion_review',version=?,updated_at=? where experiment_id=?
                    """,
                    (next_version, now, experiment_id),
                )
                event_type = "promotion_review_marked"
            self._event(
                connection,
                experiment_id,
                next_version,
                event_type,
                event_payload,
                now,
            )
            connection.commit()
        return {
            "experiment": self.get_experiment(experiment_id),
            "traffic_directive": self.traffic_directive(experiment_id),
            "applied_recommendation": action,
            "evidence_version": current_version,
            "automatic_activation": False,
        }

    def events(self, experiment_id: str, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        with self._connect() as connection:
            self._experiment_row(connection, experiment_id)
            rows = connection.execute(
                """
                select event_id,version,event_type,payload_json,created_at
                from online_experiment_events
                where experiment_id=? order by event_id desc limit ?
                """,
                (experiment_id, limit),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "version": int(row["version"]),
                "event_type": str(row["event_type"]),
                "payload": _json_object(row["payload_json"]),
                "created_at": float(row["created_at"]),
            }
            for row in reversed(rows)
        ]


__all__ = [
    "DurableOnlineExperimentStore",
    "ExperimentConflict",
    "ExperimentStateError",
    "EXPERIMENT_STATUSES",
    "MUTATING_RECOMMENDATIONS",
    "spec_from_dict",
    "spec_to_dict",
]
