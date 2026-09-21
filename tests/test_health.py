from fastapi.testclient import TestClient

import lingjing_harness.api as api_module
from lingjing_harness.api import app


def test_health_probes_are_public_even_when_api_auth_is_required(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "AUTH_REQUIRED", True)
    client = TestClient(app)

    live = client.get("/health/live")
    ready = client.get("/health/ready")
    protected = client.get("/api/status")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert protected.status_code == 401


def test_liveness_does_not_depend_on_durable_store(monkeypatch) -> None:
    client = TestClient(app)

    def unavailable(*args, **kwargs):
        raise RuntimeError("sqlite unavailable")

    monkeypatch.setattr(api_module.store, "workspace_readiness_snapshot", unavailable)

    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["detail"] == "durable store unavailable"


def test_readiness_fails_closed_on_workspace_revision_mismatch(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(
        api_module.store,
        "workspace_readiness_snapshot",
        lambda *args, **kwargs: {
            "catalog_revision": "stale-revision",
            "publication_revision": "",
            "update_active": False,
        },
    )

    ready = client.get("/health/ready")

    assert ready.status_code == 503
    assert ready.json()["detail"] == "workspace revision not ready"


def test_readiness_fails_while_workspace_update_lease_is_active(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(
        api_module.store,
        "workspace_readiness_snapshot",
        lambda *args, **kwargs: {
            "catalog_revision": api_module.CATALOG_REVISION,
            "publication_revision": "",
            "update_active": True,
        },
    )

    ready = client.get("/health/ready")

    assert ready.status_code == 503
    assert ready.json()["detail"] == "workspace update in progress"
