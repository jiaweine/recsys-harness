from __future__ import annotations

import asyncio
from pathlib import Path
import tomllib

from fastapi.testclient import TestClient

import lingjing_harness.api as api_module
from lingjing_harness.api_request_body_limit import RequestBodyLimitMiddleware


def test_project_dependency_declares_starlette_body_limit_floor() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = set(pyproject["project"]["dependencies"])
    assert "starlette>=1.6,<2" in dependencies


def test_installed_limits_bound_public_auth_and_import_before_model_parsing() -> None:
    client = TestClient(api_module.app)

    assert api_module.MAX_AUTH_REQUEST_BODY_BYTES == 16 * 1024
    assert api_module.MAX_INLINE_IMPORT_BODY_BYTES == api_module.MAX_IMPORT_BYTES + 64 * 1024
    assert api_module.REQUEST_BODY_PATH_LIMITS["/api/attachments"] > api_module.MAX_ATTACHMENT_BYTES
    assert api_module.REQUEST_BODY_PATH_LIMITS["/api/data/import-file"] > api_module.MAX_IMPORT_BYTES

    wrapped = {
        str(getattr(route, "path", "")): getattr(route, "_xushu_max_body_size", None)
        for route in api_module.app.router.routes
        if str(getattr(route, "path", "")) in api_module.REQUEST_BODY_PATH_LIMITS
    }
    assert wrapped == api_module.REQUEST_BODY_PATH_LIMITS

    # A declared oversize is rejected before JSON/Pydantic parsing. The tiny
    # physical body keeps this regression fast while proving the installed route
    # policy is consulted before the endpoint sees LoginRequest/ImportPayload.
    login = client.post(
        "/api/auth/login",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "content-length": str(api_module.MAX_AUTH_REQUEST_BODY_BYTES + 1),
        },
    )
    assert login.status_code == 413
    assert login.text == "Content Too Large"

    inline_import = client.post(
        "/api/data/import",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "content-length": str(api_module.MAX_INLINE_IMPORT_BODY_BYTES + 1),
        },
    )
    assert inline_import.status_code == 413
    assert inline_import.text == "Content Too Large"

    # Ordinary bounded requests retain their previous endpoint semantics.
    normal_login = client.post("/api/auth/login", json={"access_key": "x"})
    assert normal_login.status_code == 200


def _run_stream_case(headers: list[tuple[bytes, bytes]]) -> tuple[list[dict], list[bytes]]:
    seen_by_downstream: list[bytes] = []

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            seen_by_downstream.append(message.get("body") or b"")
            if not message.get("more_body", False):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"2")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = RequestBodyLimitMiddleware(downstream, max_body_size=8)
    frames = iter(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"6789", "more_body": False},
        ]
    )
    sent: list[dict] = []

    async def receive():
        return next(frames)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/stream",
        "headers": headers,
    }
    asyncio.run(middleware(scope, receive, send))
    return sent, seen_by_downstream


def test_streaming_limit_rejects_chunked_body_without_content_length() -> None:
    sent, seen = _run_stream_case([])
    assert seen == [b"12345"]
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert sent[1]["body"] == b"Content Too Large"


def test_streaming_limit_does_not_trust_dishonest_content_length() -> None:
    sent, seen = _run_stream_case([(b"content-length", b"1")])
    assert seen == [b"12345"]
    assert sent[0]["status"] == 413


def test_bodyless_get_remains_unaffected() -> None:
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(downstream, max_body_size=1)
    sent: list[dict] = []

    async def receive():
        raise AssertionError("bodyless GET should not need to consume receive")

    async def send(message):
        sent.append(message)

    asyncio.run(
        middleware(
            {"type": "http", "method": "GET", "path": "/health/live", "headers": []},
            receive,
            send,
        )
    )
    assert sent[0]["status"] == 204
