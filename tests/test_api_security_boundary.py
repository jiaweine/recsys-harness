from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lingjing_harness.api_security import install_api_security_boundary


def _secured_app(monkeypatch, *, hosts=None, origins=None):
    if hosts is None:
        monkeypatch.delenv("LINGJING_ALLOWED_HOSTS", raising=False)
    else:
        monkeypatch.setenv("LINGJING_ALLOWED_HOSTS", hosts)
    if origins is None:
        monkeypatch.delenv("LINGJING_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("LINGJING_ALLOWED_ORIGINS", origins)

    app = FastAPI()

    @app.get("/api/status")
    def status():
        return {"ok": True}

    @app.post("/api/mutate")
    def mutate():
        return {"ok": True}

    core = SimpleNamespace(app=app)
    install_api_security_boundary(core)
    return app, core


def test_default_boundary_rejects_untrusted_host_and_cross_origin(monkeypatch):
    app, _core = _secured_app(monkeypatch)
    client = TestClient(app)

    assert client.get("/api/status").status_code == 200
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 400
    denied = client.post(
        "/api/mutate",
        headers={"origin": "https://evil.example"},
    )
    assert denied.status_code == 403
    assert client.post(
        "/api/mutate",
        headers={"origin": "http://testserver"},
    ).status_code == 200


def test_security_headers_override_weaker_inner_defaults(monkeypatch):
    app, _core = _secured_app(monkeypatch)
    response = TestClient(app).get("/api/status")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_public_hosts_and_explicit_cross_origin_are_configurable(monkeypatch):
    app, core = _secured_app(
        monkeypatch,
        hosts="*.example.com",
        origins="https://admin.example.net",
    )
    client = TestClient(app)

    assert client.get(
        "/api/status",
        headers={"host": "api.example.com"},
    ).status_code == 200
    assert client.get(
        "/api/status",
        headers={"host": "example.com"},
    ).status_code == 400
    assert client.post(
        "/api/mutate",
        headers={
            "host": "api.example.com",
            "origin": "https://admin.example.net",
        },
    ).status_code == 200
    assert core.ALLOWED_HOSTS == ("*.example.com",)
    assert core.ALLOWED_ORIGINS == ("https://admin.example.net",)


def test_ip_literal_hosts_remain_available_for_container_health_paths(monkeypatch):
    app, _core = _secured_app(monkeypatch)
    client = TestClient(app)

    assert client.get("/api/status", headers={"host": "10.20.30.40:8080"}).status_code == 200
    assert client.get("/api/status", headers={"host": "[::1]:8080"}).status_code == 200
