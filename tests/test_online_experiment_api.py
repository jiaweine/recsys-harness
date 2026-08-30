from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lingjing_harness.online_experiment_api import install_online_experiment_routes


def _create_payload(experiment_id: str = "api-exp", *, final_only: bool = False):
    stages = (
        [{"stage_index": 0, "candidate_fraction": 0.5, "minimum_randomized_units": 200}]
        if final_only
        else [
            {"stage_index": 0, "candidate_fraction": 0.25, "minimum_randomized_units": 200},
            {"stage_index": 1, "candidate_fraction": 0.5, "minimum_randomized_units": 300},
        ]
    )
    return {
        "experiment_id": experiment_id,
        "control_arm": "control",
        "candidate_arm": "candidate",
        "metrics": [
            {
                "name": "conversion",
                "role": "primary",
                "kind": "bernoulli",
                "direction": "higher_is_better",
                "advance_threshold": 0.05,
                "rollback_threshold": -0.05,
                "minimum_samples_per_arm": 50,
                "cuped_covariate": "pre_conversion",
            },
            {
                "name": "error",
                "role": "guardrail",
                "kind": "bernoulli",
                "direction": "lower_is_better",
                "advance_threshold": -0.03,
                "rollback_threshold": -0.08,
                "minimum_samples_per_arm": 50,
            },
        ],
        "stages": stages,
        "outcome_alpha": 0.05,
        "srm_alpha": 0.01,
        "initial_epoch_id": "e0",
    }


def _binary(index: int, rate: float) -> float:
    return 1.0 if index % 100 < round(rate * 100) else 0.0


def _rows(
    *,
    epoch_id: str,
    control_count: int,
    candidate_count: int,
    control_conversion: float = 0.10,
    candidate_conversion: float = 0.90,
    control_error: float = 0.30,
    candidate_error: float = 0.01,
    sequence_start: int = 0,
):
    total = control_count + candidate_count
    result = []
    c_used = 0
    t_used = 0
    for offset in range(total):
        desired_t = round((offset + 1) * candidate_count / total)
        candidate = desired_t > t_used
        if candidate:
            index = t_used
            t_used += 1
            arm = "candidate"
            conversion = candidate_conversion
            error = candidate_error
            unit_id = f"{epoch_id}-t-{index}"
        else:
            index = c_used
            c_used += 1
            arm = "control"
            conversion = control_conversion
            error = control_error
            unit_id = f"{epoch_id}-c-{index}"
        result.append(
            {
                "unit_id": unit_id,
                "sequence": sequence_start + offset,
                "epoch_id": epoch_id,
                "arm": arm,
                "metrics": {
                    "conversion": _binary(index, conversion),
                    "error": _binary(index, error),
                },
                "pre_exposure": {"pre_conversion": (index % 10) / 10.0},
            }
        )
    return result


def _app(tmp_path: Path):
    app = FastAPI()
    registry = install_online_experiment_routes(
        app, database_path=tmp_path / "workspace.db"
    )
    return app, registry


def test_installer_is_idempotent_for_same_app_and_database(tmp_path: Path):
    app, first = _app(tmp_path)
    route_count = len(app.router.routes)

    second = install_online_experiment_routes(
        app, database_path=tmp_path / "workspace.db"
    )

    assert first is second
    assert len(app.router.routes) == route_count
    with pytest.raises(RuntimeError, match="different database"):
        install_online_experiment_routes(app, database_path=tmp_path / "other.db")


def test_api_create_ingest_evaluate_and_list(tmp_path: Path):
    app, _ = _app(tmp_path)
    client = TestClient(app)

    created = client.post("/api/online-experiments", json=_create_payload()).json()
    assert created["version"] == 1
    assert created["automatic_activation"] is False

    batch = _rows(epoch_id="e0", control_count=300, candidate_count=100)
    ingested = client.post(
        "/api/online-experiments/api-exp/observations",
        json={"observations": batch},
    )
    assert ingested.status_code == 200
    assert ingested.json()["inserted_units"] == 400
    assert ingested.json()["version"] == 2

    evaluation = client.get("/api/online-experiments/api-exp/evaluation")
    assert evaluation.status_code == 200
    assert evaluation.json()["decision"]["action"] == "advance_ramp"
    assert evaluation.json()["registry"]["version"] == 2

    listed = client.get("/api/online-experiments").json()
    assert len(listed) == 1
    assert listed[0]["observation_count"] == 400


def test_api_delayed_maturation_is_idempotent_and_conflict_is_409(tmp_path: Path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    client.post("/api/online-experiments", json=_create_payload(final_only=True))

    assignment = {
        "unit_id": "u-1",
        "sequence": 0,
        "epoch_id": "e0",
        "arm": "control",
        "metrics": {},
        "pre_exposure": {"pre_conversion": 0.2},
    }
    assert client.post(
        "/api/online-experiments/api-exp/observations",
        json={"observations": [assignment]},
    ).json()["version"] == 2

    matured = {
        **assignment,
        "metrics": {"conversion": 1.0, "error": 0.0},
    }
    response = client.post(
        "/api/online-experiments/api-exp/observations",
        json={"observations": [matured]},
    )
    assert response.json()["version"] == 3
    assert response.json()["matured_units"] == 1

    duplicate = client.post(
        "/api/online-experiments/api-exp/observations",
        json={"observations": [matured]},
    ).json()
    assert duplicate["version"] == 3
    assert duplicate["idempotent_units"] == 1

    conflict = client.post(
        "/api/online-experiments/api-exp/observations",
        json={"observations": [{**assignment, "metrics": {"conversion": 0.0}}]},
    )
    assert conflict.status_code == 409
    assert "conflicting metric" in conflict.json()["detail"]["message"]


def test_api_recommendation_requires_fresh_version_and_never_applies_traffic(tmp_path: Path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    client.post("/api/online-experiments", json=_create_payload())
    rows = _rows(epoch_id="e0", control_count=300, candidate_count=100)
    version = client.post(
        "/api/online-experiments/api-exp/observations",
        json={"observations": rows},
    ).json()["version"]

    applied = client.post(
        "/api/online-experiments/api-exp/recommendation",
        json={
            "expected_version": version,
            "action": "advance_ramp",
            "new_epoch_id": "e1",
        },
    )
    assert applied.status_code == 200
    body = applied.json()
    assert body["experiment"]["current_stage_index"] == 1
    assert body["traffic_directive"]["automatic_apply"] is False
    assert body["traffic_directive"]["production_activation"] is False

    stale = client.post(
        "/api/online-experiments/api-exp/recommendation",
        json={
            "expected_version": version,
            "action": "advance_ramp",
            "new_epoch_id": "e2",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == version + 1


def test_api_final_success_stops_at_promotion_review(tmp_path: Path):
    app, _ = _app(tmp_path)
    client = TestClient(app)
    client.post("/api/online-experiments", json=_create_payload(final_only=True))
    rows = _rows(epoch_id="e0", control_count=200, candidate_count=200)
    version = client.post(
        "/api/online-experiments/api-exp/observations",
        json={"observations": rows},
    ).json()["version"]

    response = client.post(
        "/api/online-experiments/api-exp/recommendation",
        json={
            "expected_version": version,
            "action": "eligible_for_promotion_review",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["experiment"]["status"] == "promotion_review"
    assert body["automatic_activation"] is False

    directive = client.get(
        "/api/online-experiments/api-exp/traffic-directive"
    ).json()
    assert directive["recommendation"] == "hold_controlled_allocation_pending_promotion_review"
    assert directive["recommended_candidate_fraction"] == pytest.approx(0.5)
    assert directive["production_activation"] is False


def test_api_rate_limit_uses_supplied_hardened_identity(tmp_path: Path):
    app = FastAPI()
    calls = []

    def limiter(scope_key: str, *, limit: int, window_seconds: float):
        calls.append((scope_key, limit, window_seconds))
        return True

    install_online_experiment_routes(
        app,
        database_path=tmp_path / "workspace.db",
        rate_limiter=limiter,
        client_key=lambda request: "hardened-client",
    )
    client = TestClient(app)
    response = client.post("/api/online-experiments", json=_create_payload())

    assert response.status_code == 201
    assert calls == [("online-experiment:create:hardened-client", 30, 60)]


def test_stable_api_exports_online_experiment_routes_and_shared_database():
    import lingjing_harness.api as stable_api

    paths = {getattr(route, "path", "") for route in stable_api.app.router.routes}
    assert "/api/online-experiments" in paths
    assert "/api/online-experiments/{experiment_id}/evaluation" in paths
    assert "/api/online-experiments/{experiment_id}/traffic-directive" in paths
    assert stable_api.online_experiment_store.path == stable_api.store.path
