from __future__ import annotations

from typing import Any

from starlette.middleware.body_limit import RequestBodyLimitMiddleware


def install_request_body_limit(core: Any) -> None:
    """Bound request bodies before FastAPI model or multipart parsing.

    Starlette 1.6 owns the byte accounting and response lifecycle.  One global
    responder provides a hard ceiling for every HTTP request, while nested route
    responders tighten the high-risk/public and multipart endpoints.  Starlette's
    nested responders share one counter, so route limits apply to both declared
    Content-Length and actual chunked ASGI receive frames without buffering a
    second copy of the body here.
    """

    if getattr(core, "_REQUEST_BODY_LIMIT_INSTALLED", False):
        return

    # The generic ceiling remains large enough for the bounded 1000-observation
    # experiment batch contract. High-risk/public or multipart endpoints receive
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

    # Install exact-path limits on the already-registered FastAPI/Starlette route
    # apps. install_shutdown_boundary runs after all product and experiment routes
    # are registered, so this sees the complete public HTTP surface.
    wrapped_paths: set[str] = set()
    for route in core.app.router.routes:
        path = str(getattr(route, "path", "") or "")
        limit = path_limits.get(path)
        route_app = getattr(route, "app", None)
        if limit is None or route_app is None:
            continue
        route.app = RequestBodyLimitMiddleware(route_app, max_body_size=limit)
        route._xushu_max_body_size = limit
        wrapped_paths.add(path)

    missing = sorted(set(path_limits) - wrapped_paths)
    if missing:
        raise RuntimeError(f"request body limit routes are missing: {', '.join(missing)}")

    # This responder stays outside routing and supplies the generic ceiling. A
    # nested route responder narrows its active max_body_size before body parsing.
    core.app.add_middleware(RequestBodyLimitMiddleware, max_body_size=default_limit)

    core.MAX_REQUEST_BODY_BYTES = default_limit
    core.MAX_AUTH_REQUEST_BODY_BYTES = auth_limit
    core.MAX_INLINE_IMPORT_BODY_BYTES = inline_import_limit
    core.REQUEST_BODY_PATH_LIMITS = dict(path_limits)
    core._REQUEST_BODY_LIMIT_INSTALLED = True


__all__ = ["RequestBodyLimitMiddleware", "install_request_body_limit"]
