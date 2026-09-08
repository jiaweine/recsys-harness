from __future__ import annotations

import json
from typing import Any, Awaitable, Callable


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class _RequestBodyTooLarge(Exception):
    pass


def _json_response(status: int, detail: str) -> tuple[dict[str, Any], bytes]:
    body = json.dumps({"detail": detail}, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    start = {
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    }
    return start, body


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies before framework parsing allocates them.

    ``Content-Length`` is used only as a fast reject.  The receive stream is
    always counted as well, so a missing, malformed, or dishonest header cannot
    bypass the boundary when the server receives chunked request frames.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        default_limit: int,
        path_limits: dict[str, int] | None = None,
    ) -> None:
        self.app = app
        self.default_limit = max(1, int(default_limit))
        self.path_limits = {
            str(path): max(1, int(limit))
            for path, limit in (path_limits or {}).items()
        }

    def _limit(self, path: str) -> int:
        return self.path_limits.get(path, self.default_limit)

    @staticmethod
    def _declared_too_large(scope: dict[str, Any], limit: int) -> bool:
        for raw_name, raw_value in scope.get("headers") or []:
            if bytes(raw_name).lower() != b"content-length":
                continue
            try:
                declared = int(bytes(raw_value).decode("ascii").strip())
            except (UnicodeDecodeError, ValueError):
                # Never trust a malformed declaration; streaming accounting below
                # remains authoritative.
                continue
            if declared >= 0 and declared > limit:
                return True
        return False

    @staticmethod
    async def _reject(send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        start, body = _json_response(413, "请求体过大")
        await send(start)
        await send({"type": "http.response.body", "body": body})

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http" or str(scope.get("method") or "").upper() not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        limit = self._limit(str(scope.get("path") or ""))
        if self._declared_too_large(scope, limit):
            await self._reject(send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _RequestBodyTooLarge
            return message

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _RequestBodyTooLarge:
            # Body-consuming routes parse the request before starting a response.
            # If a custom downstream component violated that ordering, fail loudly
            # instead of emitting a second HTTP response on the same connection.
            if response_started:
                raise
            await self._reject(send)


def install_request_body_limit(core: Any) -> None:
    if getattr(core, "_REQUEST_BODY_LIMIT_INSTALLED", False):
        return

    # The generic ceiling remains large enough for the bounded 1000-observation
    # experiment batch contract.  High-risk/public or multipart endpoints receive
    # tighter limits that reflect their existing semantic payload budgets.
    default_limit = 32 * 1024 * 1024
    auth_limit = 16 * 1024
    multipart_overhead = 1024 * 1024
    inline_import_limit = core.MAX_IMPORT_BYTES + 64 * 1024

    path_limits = {
        "/api/auth/login": auth_limit,
        "/api/auth/logout": auth_limit,
        "/api/data/import": inline_import_limit,
        "/api/data/import-file": core.MAX_IMPORT_BYTES + multipart_overhead,
        "/api/attachments": core.MAX_ATTACHMENT_BYTES + multipart_overhead,
    }

    core.app.add_middleware(
        RequestBodyLimitMiddleware,
        default_limit=default_limit,
        path_limits=path_limits,
    )
    core.MAX_REQUEST_BODY_BYTES = default_limit
    core.MAX_AUTH_REQUEST_BODY_BYTES = auth_limit
    core.MAX_INLINE_IMPORT_BODY_BYTES = inline_import_limit
    core.REQUEST_BODY_PATH_LIMITS = dict(path_limits)
    core._REQUEST_BODY_LIMIT_INSTALLED = True


__all__ = ["RequestBodyLimitMiddleware", "install_request_body_limit"]
