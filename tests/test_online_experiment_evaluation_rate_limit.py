from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lingjing_harness.online_experiment_api import install_online_experiment_routes


def test_full_evaluation_uses_shared_hardened_rate_limit(tmp_path: Path):
    app = FastAPI()
    calls = []

    def limiter(scope_key: str, *, limit: int, window_seconds: float):
        calls.append((scope_key, limit, window_seconds))
        return False

    install_online_experiment_routes(
        app,
        database_path=tmp_path / "workspace.db",
        rate_limiter=limiter,
        client_key=lambda request: "hardened-client",
    )

    response = TestClient(app).get(
        "/api/online-experiments/large-experiment/evaluation"
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "online experiment request rate exceeded"
    assert calls == [("online-experiment:evaluate:hardened-client", 30, 60)]
