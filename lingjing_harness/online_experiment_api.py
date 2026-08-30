from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .online_experiment_store import (
    DurableOnlineExperimentStore,
    ExperimentConflict,
    ExperimentStateError,
)
from .online_experiments import (
    OnlineExperimentSpec,
    OnlineMetricSpec,
    OnlineObservation,
    RampStage,
)


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def _identifier(name: str, value: str) -> str:
    value = str(value or "").strip()
    if not _ID.fullmatch(value):
        raise ValueError(f"{name} contains unsupported characters")
    return value


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


class MetricContractRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: Literal["primary", "guardrail"]
    kind: Literal["bernoulli", "bounded"]
    direction: Literal["higher_is_better", "lower_is_better"]
    advance_threshold: float
    rollback_threshold: float
    minimum_samples_per_arm: int = Field(default=50, ge=1, le=10_000_000)
    cuped_covariate: str | None = Field(default=None, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("metric name must not be empty")
        return value

    @field_validator("advance_threshold", "rollback_threshold")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        return _finite("metric threshold", value)

    @field_validator("cuped_covariate")
    @classmethod
    def normalize_covariate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    def contract(self) -> OnlineMetricSpec:
        return OnlineMetricSpec(**self.model_dump())


class RampStageRequest(BaseModel):
    stage_index: int = Field(ge=0, le=100)
    candidate_fraction: float = Field(gt=0.0, lt=1.0)
    minimum_randomized_units: int = Field(ge=2, le=1_000_000_000)

    @field_validator("candidate_fraction")
    @classmethod
    def finite_fraction(cls, value: float) -> float:
        return _finite("candidate_fraction", value)

    def contract(self) -> RampStage:
        return RampStage(**self.model_dump())


class ExperimentCreateRequest(BaseModel):
    experiment_id: str = Field(min_length=1, max_length=200)
    control_arm: str = Field(min_length=1, max_length=120)
    candidate_arm: str = Field(min_length=1, max_length=120)
    metrics: list[MetricContractRequest] = Field(min_length=1, max_length=16)
    stages: list[RampStageRequest] = Field(min_length=1, max_length=16)
    outcome_alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    srm_alpha: float = Field(default=0.01, gt=0.0, lt=1.0)
    srm_dirichlet_prior: float = Field(default=0.5, gt=0.0, le=1000.0)
    initial_epoch_id: str = Field(default="epoch-0", min_length=1, max_length=200)

    @field_validator("experiment_id", "initial_epoch_id")
    @classmethod
    def validate_identifier(cls, value: str, info) -> str:
        return _identifier(info.field_name, value)

    @field_validator("control_arm", "candidate_arm")
    @classmethod
    def normalize_arm(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("arm must not be empty")
        return value

    @field_validator("outcome_alpha", "srm_alpha", "srm_dirichlet_prior")
    @classmethod
    def finite_probability_parameter(cls, value: float) -> float:
        return _finite("experiment probability parameter", value)

    def contract(self) -> OnlineExperimentSpec:
        return OnlineExperimentSpec(
            experiment_id=self.experiment_id,
            control_arm=self.control_arm,
            candidate_arm=self.candidate_arm,
            metrics=tuple(metric.contract() for metric in self.metrics),
            stages=tuple(stage.contract() for stage in self.stages),
            outcome_alpha=self.outcome_alpha,
            srm_alpha=self.srm_alpha,
            srm_dirichlet_prior=self.srm_dirichlet_prior,
        )


class ObservationRequest(BaseModel):
    unit_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=0, le=9_000_000_000_000_000_000)
    epoch_id: str = Field(min_length=1, max_length=200)
    arm: str = Field(min_length=1, max_length=120)
    metrics: dict[str, float] = Field(default_factory=dict)
    pre_exposure: dict[str, float] = Field(default_factory=dict)

    @field_validator("unit_id", "epoch_id")
    @classmethod
    def validate_identifier(cls, value: str, info) -> str:
        return _identifier(info.field_name, value)

    @field_validator("arm")
    @classmethod
    def normalize_arm(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("arm must not be empty")
        return value

    @field_validator("metrics", "pre_exposure")
    @classmethod
    def validate_numeric_map(cls, value: dict[str, float], info) -> dict[str, float]:
        if len(value) > 64:
            raise ValueError(f"{info.field_name} supports at most 64 fields")
        result: dict[str, float] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key or len(key) > 120:
                raise ValueError(f"invalid {info.field_name} field name")
            result[key] = _finite(f"{info.field_name}.{key}", raw_value)
        return result

    def contract(self) -> OnlineObservation:
        return OnlineObservation(
            unit_id=self.unit_id,
            sequence=self.sequence,
            epoch_id=self.epoch_id,
            arm=self.arm,
            metrics=self.metrics,
            pre_exposure=self.pre_exposure,
        )


class ObservationBatchRequest(BaseModel):
    observations: list[ObservationRequest] = Field(min_length=1, max_length=1000)


class RecommendationApplyRequest(BaseModel):
    expected_version: int = Field(ge=1)
    action: Literal[
        "advance_ramp", "rollback_recommended", "eligible_for_promotion_review"
    ]
    new_epoch_id: str | None = Field(default=None, max_length=200)

    @field_validator("new_epoch_id")
    @classmethod
    def normalize_new_epoch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _identifier("new_epoch_id", value)


def install_online_experiment_routes(
    app: FastAPI,
    *,
    database_path: str | Path,
    rate_limiter: Callable[..., bool] | None = None,
    client_key: Callable[[Request], str] | None = None,
) -> DurableOnlineExperimentStore:
    """Install version-fenced online experiment routes on the stable API app.

    Route registration is idempotent, while request-identity and rate-limit hooks
    are intentionally refreshable.  The stable API wrapper can therefore replace
    a bootstrap identity resolver with its hardened proxy-aware resolver without
    duplicating routes or leaving the original closure captured forever.
    """

    resolved_path = str(Path(database_path).resolve())
    installed = getattr(app.state, "online_experiment_store", None)
    if installed is not None:
        if not isinstance(installed, DurableOnlineExperimentStore):
            raise RuntimeError("online experiment app state is owned by another component")
        if str(Path(installed.path).resolve()) != resolved_path:
            raise RuntimeError("online experiment routes already use a different database")
        app.state.online_experiment_rate_limiter = rate_limiter
        app.state.online_experiment_client_key = client_key
        return installed

    registry = DurableOnlineExperimentStore(database_path)
    app.state.online_experiment_rate_limiter = rate_limiter
    app.state.online_experiment_client_key = client_key
    router = APIRouter(prefix="/api/online-experiments", tags=["online-experiments"])

    def limit(request: Request, scope: str, *, requests: int, window: int) -> None:
        limiter = getattr(request.app.state, "online_experiment_rate_limiter", None)
        if limiter is None:
            return
        resolver = getattr(request.app.state, "online_experiment_client_key", None)
        identity = resolver(request) if resolver is not None else "unknown"
        if not limiter(
            f"online-experiment:{scope}:{identity}",
            limit=requests,
            window_seconds=window,
        ):
            raise HTTPException(429, "online experiment request rate exceeded")

    def conflict(exc: ExperimentConflict) -> HTTPException:
        detail: dict[str, Any] = {"message": str(exc)}
        if exc.current_version is not None:
            detail["current_version"] = exc.current_version
        return HTTPException(409, detail)

    @router.post("", status_code=201)
    def create_experiment(req: ExperimentCreateRequest, request: Request):
        limit(request, "create", requests=30, window=60)
        try:
            return registry.create_experiment(
                req.contract(), initial_epoch_id=req.initial_epoch_id
            )
        except ExperimentConflict as exc:
            raise conflict(exc) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("")
    def list_experiments(limit_rows: int = 100):
        return registry.list_experiments(limit_rows)

    @router.get("/{experiment_id}")
    def get_experiment(experiment_id: str):
        try:
            return registry.get_experiment(experiment_id)
        except KeyError as exc:
            raise HTTPException(404, "online experiment not found") from exc

    @router.post("/{experiment_id}/observations")
    def ingest_observations(
        experiment_id: str, req: ObservationBatchRequest, request: Request
    ):
        limit(request, "ingest", requests=120, window=60)
        try:
            result = registry.ingest_observations(
                experiment_id, (row.contract() for row in req.observations)
            )
            result["evaluation_path"] = f"/api/online-experiments/{experiment_id}/evaluation"
            return result
        except KeyError as exc:
            raise HTTPException(404, "online experiment not found") from exc
        except ExperimentConflict as exc:
            raise conflict(exc) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/{experiment_id}/evaluation")
    def evaluate(experiment_id: str):
        try:
            return registry.evaluate(experiment_id)
        except KeyError as exc:
            raise HTTPException(404, "online experiment not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{experiment_id}/traffic-directive")
    def traffic_directive(experiment_id: str):
        try:
            return registry.traffic_directive(experiment_id)
        except KeyError as exc:
            raise HTTPException(404, "online experiment not found") from exc

    @router.post("/{experiment_id}/recommendation")
    def apply_recommendation(
        experiment_id: str, req: RecommendationApplyRequest, request: Request
    ):
        limit(request, "transition", requests=60, window=60)
        try:
            return registry.apply_recommendation(
                experiment_id,
                expected_version=req.expected_version,
                action=req.action,
                new_epoch_id=req.new_epoch_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "online experiment not found") from exc
        except ExperimentConflict as exc:
            raise conflict(exc) from exc
        except ExperimentStateError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/{experiment_id}/events")
    def events(experiment_id: str, limit_rows: int = 200):
        try:
            return registry.events(experiment_id, limit_rows)
        except KeyError as exc:
            raise HTTPException(404, "online experiment not found") from exc

    app.include_router(router)
    app.state.online_experiment_store = registry
    return registry


__all__ = [
    "ExperimentCreateRequest",
    "MetricContractRequest",
    "ObservationBatchRequest",
    "ObservationRequest",
    "RampStageRequest",
    "RecommendationApplyRequest",
    "install_online_experiment_routes",
]
