from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

import lingjing_harness.api as api_module
from lingjing_harness.api_request_body_limit import RequestBodyLimitMiddleware


def test_installed_limits_bound_public_auth_and_import_before_model_parsing() -> None:
    client = TestClient(api_module.app)

    assert api_module.MAX_AUTH_REQUEST_BODY_BYTES == 16 * 1024
    assert api_module.MAX_INLINE_IMPORT_BODY_BYTES == api_module.MAX_IMPORT_BYTES + 64 * 1024
    assert api_module.REQUEST_BODY_PATH_LIMITS["/api/attachments"] > api_module.MAX_ATTACHMENT_BYTES
    assert api_module.REQUEST_BODY_PATH_LIMITS["/api/data/import-file"] > api_module.MAX_IMPORT_BYTES

    # A declared oversize is rejected before JSON/Pydantic parsing. The tiny
    # physical body keeps this regression fast while proving the installed path
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
    assert login.json()["detail"] == "请求体过大"

    inline_import = client.post(
        "/api/data/import",
        content=b"{}",
        headers={
            "content-type": "application/json",
            "content-length": str(api_module.MAX_INLINE_IMPORT_BODY_BYTES + 1),
        },
    )
    assert inline_import.status_code == 413
    assert inline_import.json()["detail"] == "请求体过大"

    # Ordinary bounded requests retain their previous endpoint semantics.
    normal_login = client.post("/api/auth/login", json={"access_key": "x"})
    assert normal_login.status_code == 200


def _run_stream_case(headers: list[tuple[bytes, bytes]]) -> list[dict]:
    seen_by_downstream: list[bytes] = []

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            seen_by_downstream.append(message.get("body") or b"")
            if not message.get("more_body", False):
                break
        body = json.dumps({"ok": True}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(body)).encode("ascii"))],
            }
        )
        await send({"type": "http.response.body", "body": body})

    middleware = RequestBodyLimitMiddleware(downstream, default_limit=8)
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

    # The first bounded chunk may be delivered; the chunk crossing the limit is
    # stopped before downstream code can observe or parse it.
    assert seen_by_downstream == [b"12345"]
    return sent


def test_streaming_limit_rejects_chunked_body_without_content_length() -> None:
    sent = _run_stream_case([])
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["detail"] == "请求体过大"


def test_streaming_limit_does_not_trust_dishonest_content_length() -> None:
    sent = _run_stream_case([(b"content-length", b"1")])
    assert sent[0]["status"] == 413


def test_non_body_methods_are_not_subject_to_request_body_accounting() -> None:
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(downstream, default_limit=1)
    sent: list[dict] = []

    async def receive():
        raise AssertionError("GET body should not be consumed by this boundary")

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
