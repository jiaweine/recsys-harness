from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lingjing_harness.api_security import install_api_security_boundary


def test_unauthenticated_protected_requests_do_not_reach_inner_quota(monkeypatch):
    monkeypatch.delenv("LINGJING_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("LINGJING_ALLOWED_ORIGINS", raising=False)

    app = FastAPI()
    inner_requests: list[str] = []
    durable_quota_calls: list[tuple[str, int, int]] = []

    # Model api_core's historical access middleware, which owns the existing
    # task/import/attachment rate rules. The stable security boundary is installed
    # later and must sit outside this layer.
    @app.middleware("http")
    async def inner_access_probe(request, call_next):
        inner_requests.append(request.url.path)
        return await call_next(request)

    @app.post("/api/conversations")
    def create_conversation():
        return {"ok": True}

    @app.post("/api/attachments")
    def upload_attachment():
        return {"ok": True}

    @app.post("/api/auth/login")
    def login():
        return {"ok": True}

    def consume_rate_limit(scope_key: str, *, limit: int, window_seconds: int):
        durable_quota_calls.append((scope_key, limit, window_seconds))
        return True

    core = SimpleNamespace(
        app=app,
        AUTH_REQUIRED=True,
        _session_valid=lambda request: False,
        _client_key=lambda request: "shared-nat-client",
        store=SimpleNamespace(consume_rate_limit=consume_rate_limit),
    )
    install_api_security_boundary(core)
    client = TestClient(app)

    conversation = client.post("/api/conversations", json={})
    attachment = client.post("/api/attachments")

    assert conversation.status_code == 401
    assert attachment.status_code == 401
    assert inner_requests == []
    assert durable_quota_calls == []

    # Login remains deliberately open to the auth-before-quota boundary; the
    # historical access middleware still owns its brute-force rate limit.
    login_response = client.post("/api/auth/login", json={"access_key": "bad"})
    assert login_response.status_code == 200
    assert inner_requests == ["/api/auth/login"]
    assert durable_quota_calls == []
