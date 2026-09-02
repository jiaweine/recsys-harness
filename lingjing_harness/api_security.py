from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse


_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1", "testserver")
_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; "
    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)


def _csv_values(name: str, defaults: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return defaults
    return tuple(value.strip() for value in raw.split(",") if value.strip())


def _host_parts(raw: str) -> tuple[str, int | None] | None:
    value = str(raw or "").strip()
    if not value or any(char in value for char in "\r\n/\\"):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        if parsed.username is not None or parsed.password is not None or not parsed.hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    return parsed.hostname.rstrip(".").lower(), port


def _host_allowed(hostname: str, allowed_hosts: tuple[str, ...]) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    for raw in allowed_hosts:
        pattern = raw.rstrip(".").lower()
        if pattern == "*" or hostname == pattern:
            return True
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if hostname.endswith(suffix) and hostname != suffix[1:]:
                return True
    return False


def _normalized_origin(raw: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(str(raw or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port
    except ValueError:
        return None
    return parsed.scheme.lower(), parsed.hostname.rstrip(".").lower(), port


def _origin_allowed(
    raw_origin: str,
    request_host: tuple[str, int | None],
    allowed_origins: tuple[str, ...],
) -> bool:
    normalized = _normalized_origin(raw_origin)
    if normalized is None:
        return False
    _scheme, hostname, port = normalized
    if (hostname, port) == request_host:
        return True
    for raw in allowed_origins:
        if raw == "*":
            return True
        candidate = _normalized_origin(raw)
        if candidate == normalized:
            return True
    return False


def install_api_security_boundary(core: Any) -> None:
    """Install fail-closed request identity and browser response boundaries."""

    if getattr(core, "_API_SECURITY_BOUNDARY_INSTALLED", False):
        return

    allowed_hosts = _csv_values("LINGJING_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS)
    allowed_origins = _csv_values("LINGJING_ALLOWED_ORIGINS")

    @core.app.middleware("http")
    async def api_security_boundary(request: Request, call_next):
        host = _host_parts(request.headers.get("host", ""))
        if host is None or not _host_allowed(host[0], allowed_hosts):
            return JSONResponse({"detail": "Host header is not allowed"}, status_code=400)

        origin = request.headers.get("origin")
        if (
            origin
            and request.method.upper() in _MUTATION_METHODS
            and not _origin_allowed(origin, host, allowed_origins)
        ):
            return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path != "/docs" and not request.url.path.startswith("/docs/"):
            response.headers["Content-Security-Policy"] = _CSP
        return response

    core.ALLOWED_HOSTS = allowed_hosts
    core.ALLOWED_ORIGINS = allowed_origins
    core._API_SECURITY_BOUNDARY_INSTALLED = True
