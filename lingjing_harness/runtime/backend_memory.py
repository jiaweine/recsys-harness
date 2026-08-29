from __future__ import annotations

from hashlib import blake2b
import json
from typing import Any


_SCOPED_CATALOG_METHODS = frozenset(
    {
        "remember_strategy",
        "record_strategy_credit",
        "strategy_credits",
        "evolution_memory",
        "record_evolution_result",
        "strategies",
        "active_skill",
        "retire_active",
        "mark_skill_validation",
        "active_config",
    }
)


def backend_strategy_scope(surface: str, backend: str, kwargs: dict[str, Any]) -> str:
    """Return a stable strategy-memory scope for one serving surface.

    The dependency-light reference backend intentionally keeps the historical
    unscoped catalog key so existing trusted/active strategies remain readable.
    Non-reference backends include their explicit runtime options because those
    options can change the evidence and serving semantics behind the same config.
    """

    surface = str(surface).strip().lower()
    backend = str(backend).strip().lower()
    if surface not in {"search", "recommend"}:
        raise ValueError("surface must be search or recommend")
    if backend == "reference" and not kwargs:
        return ""
    payload = {
        "surface": surface,
        "backend": backend,
        "kwargs": kwargs,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = blake2b(raw.encode("utf-8"), digest_size=10).hexdigest()
    return f"{surface}-{digest}"


def runtime_invocation_scope(search_scope: str, recommend_scope: str) -> str:
    """Scope adaptive invocation replay when any serving backend is non-reference."""

    if not search_scope and not recommend_scope:
        return ""
    raw = json.dumps(
        {"search": search_scope, "recommend": recommend_scope},
        sort_keys=True,
        separators=(",", ":"),
    )
    return blake2b(raw.encode("utf-8"), digest_size=8).hexdigest()


class BackendScopedMemory:
    """Transparent strategy/credit namespace over a shared AgentMemory.

    Episodes, policy statistics, and general recall remain shared across serving
    backends. Only durable strategy state, validation timestamps, arm credit, and
    adaptive invocation replay are namespaced. Search and recommendation scopes
    are independent so changing one backend does not invalidate the other surface.
    """

    def __init__(
        self,
        base_memory: Any,
        *,
        search_scope: str = "",
        recommend_scope: str = "",
        invocation_scope: str = "",
    ) -> None:
        self.base_memory = base_memory
        self.search_scope = str(search_scope or "")
        self.recommend_scope = str(recommend_scope or "")
        self.invocation_scope = str(invocation_scope or "")

    def _scope_for_domain(self, domain: str) -> str:
        domain = str(domain or "")
        if domain == "search" or domain.startswith("search.segment."):
            return self.search_scope
        if domain == "recommend" or domain.startswith("recommend.segment."):
            return self.recommend_scope
        return ""

    def scoped_catalog_key(self, catalog_key: str, domain: str) -> str:
        scope = self._scope_for_domain(domain)
        if not scope:
            return str(catalog_key)
        return f"{catalog_key}:backend:{scope}"

    def _scoped_invocation_id(self, invocation_id: str) -> str:
        invocation_id = str(invocation_id or "")
        if not invocation_id or not self.invocation_scope:
            return invocation_id
        return f"backend:{self.invocation_scope}:{invocation_id}"

    def _scoped_event_key(self, event_key: str) -> str:
        event_key = str(event_key or "")
        if not event_key or not self.invocation_scope:
            return event_key
        return f"backend:{self.invocation_scope}:{event_key}"

    def invocation_result(self, invocation_id: str) -> dict[str, Any] | None:
        return self.base_memory.invocation_result(self._scoped_invocation_id(invocation_id))

    @staticmethod
    def _strategy_domains() -> list[str]:
        """Return every durable global/segment strategy domain owned by the runtime."""

        from lingjing_harness.algorithms.segments import (
            RECOMMEND_SEGMENTS,
            SEARCH_SEGMENTS,
            strategy_domain,
        )

        return [
            "search",
            *(strategy_domain("search", segment) for segment in SEARCH_SEGMENTS),
            "recommend",
            *(strategy_domain("recommend", segment) for segment in RECOMMEND_SEGMENTS),
        ]

    def stats(self, catalog_key: str | None = None) -> dict[str, Any]:
        """Report shared episodes plus strategy state visible to this runtime.

        Episodes deliberately remain shared at the stable workspace key. Procedural
        strategy state and arm credit do not: search and recommendation may each use
        a distinct backend namespace. Aggregate the current runtime's global and
        segment domains instead of reporting an inactive reference or alternate
        mature backend.
        """

        if not catalog_key:
            return self.base_memory.stats(catalog_key)

        key = str(catalog_key)
        result = dict(self.base_memory.stats(key))
        skills = 0
        active = 0
        for domain in self._strategy_domains():
            rows = self.strategies(key, domain, limit=512)
            skills += len(rows)
            active += sum(1 for row in rows if row.get("status") == "active")

        credits = [
            *self.strategy_credits(key, "search", include_segments=True, limit=512),
            *self.strategy_credits(key, "recommend", include_segments=True, limit=512),
        ]
        credit_rows = [
            row.get("credit") or {}
            for row in credits
            if isinstance(row, dict)
        ]
        result.update(
            {
                "skills": skills,
                "active_strategies": active,
                "credit_arms": len(credit_rows),
                "negative_credit_arms": sum(
                    1
                    for row in credit_rows
                    if int(row.get("negative", 0) or 0) > int(row.get("positive", 0) or 0)
                ),
                "credit_events": sum(int(row.get("trials", 0) or 0) for row in credit_rows),
            }
        )
        return result

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.base_memory, name)
        if name not in _SCOPED_CATALOG_METHODS or not callable(attribute):
            return attribute

        def scoped(*args: Any, **kwargs: Any) -> Any:
            positional = list(args)
            domain = ""
            if len(positional) >= 2:
                domain = str(positional[1] or "")
            else:
                domain = str(kwargs.get("domain") or kwargs.get("surface") or "")

            if positional:
                positional[0] = self.scoped_catalog_key(str(positional[0]), domain)
            elif "catalog_key" in kwargs:
                kwargs = dict(kwargs)
                kwargs["catalog_key"] = self.scoped_catalog_key(
                    str(kwargs["catalog_key"]),
                    domain,
                )

            if name == "remember_strategy" and kwargs.get("invocation_id"):
                kwargs = dict(kwargs)
                kwargs["invocation_id"] = self._scoped_invocation_id(
                    str(kwargs["invocation_id"])
                )
            if name == "record_strategy_credit" and kwargs.get("event_key"):
                kwargs = dict(kwargs)
                kwargs["event_key"] = self._scoped_event_key(str(kwargs["event_key"]))
            return attribute(*positional, **kwargs)

        return scoped


__all__ = [
    "BackendScopedMemory",
    "backend_strategy_scope",
    "runtime_invocation_scope",
]
