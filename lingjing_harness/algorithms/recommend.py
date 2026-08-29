"""Stable recommendation surface with evolvable serving backends."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
from importlib.util import find_spec
from threading import RLock
from typing import Any

from lingjing_harness.domain import Catalog
from lingjing_harness.serving import normalize_serving_limit

from .capabilities import CAPABILITIES, capability_field, normalize_strategy_config
from .recommend_core import (
    RecommendConfig as _CoreRecommendConfig,
    RecommendationEngine as _CoreRecommendationEngine,
)


def _implicit_available() -> bool:
    """Return whether the optional mature collaborative stack is importable."""

    try:
        return all(find_spec(name) is not None for name in ("implicit", "numpy", "scipy"))
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


@dataclass(frozen=True)
class RecommendConfig(_CoreRecommendConfig):
    """Recommendation genome including the serving implementation choice.

    ``reference`` remains the safe default. Optional mature backends are only
    registered when their dependencies are installed, so unavailable persisted
    choices canonicalize back to the owned implementation and are retired by the
    existing active-strategy schema guard before execution.
    """

    serving_strategy: str = capability_field("recommend.serving", "reference")


class RecommendationEngine(_CoreRecommendationEngine):
    """Owned recommendation engine with a typed, evidence-evaluated serving route.

    Feature preparation and the reference ranker stay in ``recommend_core``. This
    stable public layer owns only serving implementation routing. Heavy optional
    models are trained lazily once per Catalog-backed engine family and shared by
    ``with_config`` clones, while each request gets a fallback engine carrying the
    candidate config being evaluated.
    """

    def __init__(self, catalog: Catalog, config: RecommendConfig | None = None) -> None:
        super().__init__(catalog, normalize_strategy_config(config or RecommendConfig()))
        self._serving_backend_cache: dict[str, Any] = {}
        self._serving_backend_lock = RLock()

    def with_config(self, config: RecommendConfig) -> "RecommendationEngine":
        clone = object.__new__(type(self))
        clone.catalog = self.catalog
        clone.config = normalize_strategy_config(config)
        clone._vectors = self._vectors
        clone._popularity = self._popularity
        clone._by_user = self._by_user
        clone._co = self._co
        clone._serving_backend_cache = self._serving_backend_cache
        clone._serving_backend_lock = self._serving_backend_lock
        return clone

    def _reference_engine(self) -> "RecommendationEngine":
        return self.with_config(replace(self.config, serving_strategy="reference"))

    def _implicit_adapter(self, model: str):
        cache_key = f"implicit:{model}"
        with self._serving_backend_lock:
            cached = self._serving_backend_cache.get(cache_key)
            if cached is None:
                from lingjing_harness.integrations.implicit_recommendation import (
                    ImplicitRecommendationAdapter,
                )

                cached = ImplicitRecommendationAdapter(
                    self.catalog,
                    model=model,
                    fallback=self._reference_engine(),
                )
                self._serving_backend_cache[cache_key] = cached

        # The trained collaborative state is immutable for serving. Shallow-copy
        # only the adapter shell so fallback behavior can remain candidate-config
        # specific without retraining or mutating a shared object concurrently.
        adapter = copy(cached)
        adapter.fallback = self._reference_engine()
        return adapter

    def _enrich_backend_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach Harness-owned guardrail signals required by shared evaluation."""

        enriched: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            item_id = str(row.get("id") or "")
            item = self.catalog.item_by_id.get(item_id)
            signals = dict(row.get("signals") or {})
            if item is not None and "novelty" not in signals:
                signals["novelty"] = round(1.0 - self.catalog.popularity_norm(item), 4)
            row["signals"] = signals
            enriched.append(row)
        return enriched

    def recommend(self, user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        limit = normalize_serving_limit(limit)
        if limit == 0:
            return []
        return CAPABILITIES.call(
            "recommend.serving",
            self.config.serving_strategy,
            self,
            user_id,
            limit,
        )


def _serve_reference(
    engine: RecommendationEngine,
    user_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    # Call the owned implementation directly so the serving capability does not
    # recurse back through the router.
    return _CoreRecommendationEngine.recommend(engine, user_id, limit=limit)


def _serve_implicit_als(
    engine: RecommendationEngine,
    user_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = engine._implicit_adapter("als").recommend(user_id, limit=limit)
    return engine._enrich_backend_rows(rows)


CAPABILITIES.register(
    "recommend.serving",
    "reference",
    "Use the owned recommendation ranker as the serving implementation.",
    _serve_reference,
    default=True,
)
if _implicit_available():
    CAPABILITIES.register(
        "recommend.serving",
        "implicit_als",
        "Use mature implicit ALS for warm-user serving with the owned strategy as fallback.",
        _serve_implicit_als,
        complexity=1.35,
    )


__all__ = ["RecommendConfig", "RecommendationEngine"]
