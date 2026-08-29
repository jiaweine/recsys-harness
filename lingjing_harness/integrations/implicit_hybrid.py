from __future__ import annotations

from dataclasses import replace
from math import log2
from typing import Any

from lingjing_harness.algorithms.recommend import RecommendConfig, RecommendationEngine
from lingjing_harness.domain import Catalog
from lingjing_harness.serving import normalize_serving_limit

from .implicit_recommendation import ImplicitRecommendationAdapter


DEFAULT_COLLABORATIVE_LIMIT = 48


class ImplicitHybridRecommendationEngine:
    """RecommendationEngine-compatible façade using ``implicit`` as a warm signal.

    The mature backend owns collaborative retrieval/ranking evidence for warm
    users. Harness keeps final scoring weights, candidate guardrails, diversity,
    cold-start behavior, evolution, temporal evaluation and activation semantics.
    ``with_config`` therefore evaluates every candidate against the same trained
    collaborative model instead of retraining or silently switching runtimes.
    """

    def __init__(
        self,
        reference: RecommendationEngine,
        adapter: ImplicitRecommendationAdapter,
        *,
        collaborative_limit: int = DEFAULT_COLLABORATIVE_LIMIT,
        adapter_options: dict[str, Any] | None = None,
    ) -> None:
        if reference.catalog is not adapter.catalog:
            raise ValueError("reference recommendation engine and collaborative adapter must share one catalog")
        collaborative_limit = normalize_serving_limit(collaborative_limit)
        if collaborative_limit == 0:
            raise ValueError("collaborative_limit must be greater than zero")

        self.catalog = reference.catalog
        self.config = reference.config
        self.reference = reference
        self.adapter = adapter
        self.collaborative_limit = collaborative_limit
        self.adapter_options = dict(
            adapter_options
            or {
                "model": adapter.model_name,
                "min_history": adapter.min_history,
            }
        )
        # SegmentRouter and runtime diagnostics intentionally inspect the owned
        # interaction index directly. Preserve that stable compatibility surface.
        self._by_user = reference._by_user

    def with_config(self, config: RecommendConfig) -> "ImplicitHybridRecommendationEngine":
        clone = object.__new__(type(self))
        clone.catalog = self.catalog
        clone.reference = self.reference.with_config(config)
        clone.config = clone.reference.config
        clone.adapter = self.adapter
        clone.collaborative_limit = self.collaborative_limit
        clone.adapter_options = dict(self.adapter_options)
        clone._by_user = clone.reference._by_user
        return clone

    def for_catalog(self, catalog: Catalog) -> "ImplicitHybridRecommendationEngine":
        """Rebuild the same runtime on a point-in-time evaluation Catalog.

        Temporal relevance must train collaborative state only from interactions
        available before the target event. This hook lets the prepared evaluator
        preserve the selected runtime without leaking the full-catalog model.
        """

        reference = RecommendationEngine(catalog, self.config)
        adapter = ImplicitRecommendationAdapter(
            catalog,
            fallback=reference,
            **self.adapter_options,
        )
        return type(self)(
            reference,
            adapter,
            collaborative_limit=self.collaborative_limit,
            adapter_options=self.adapter_options,
        )

    def capability_manifest(self) -> dict[str, list[dict]]:
        return self.reference.capability_manifest()

    def backend_manifest(self) -> dict[str, Any]:
        return {
            **self.adapter.capability_manifest(),
            "mode": "hybrid_collaborative_signal",
            "collaborative_limit": self.collaborative_limit,
            "ranking_owner": "harness",
            "collaborative_owner": "implicit",
            "cold_start_owner": "harness",
        }

    def known_users(self) -> list[str]:
        return self.reference.known_users()

    @staticmethod
    def _rank_signal(rank: int) -> float:
        return 1.0 / log2(max(2, rank + 1))

    def _history_count(self, user_id: str) -> int:
        history_count = getattr(self.adapter, "history_count", None)
        if callable(history_count):
            return int(history_count(user_id))
        return sum(
            1
            for event in self._by_user.get(user_id, [])
            if float(getattr(event, "weight", 0.0)) > 0.0
        )

    def _collaborative_signals(self, user_id: str) -> dict[str, float]:
        if self._history_count(user_id) < self.adapter.min_history:
            return {}
        seen = {event.item_id for event in self._by_user.get(user_id, [])}
        eligible_unseen = sum(
            1
            for item in self.catalog.items
            if item.eligible and item.item_id not in seen
        )
        request_limit = min(self.collaborative_limit, eligible_unseen)
        if request_limit <= 0:
            return {}

        rows = self.adapter.recommend(user_id, limit=request_limit)
        backend = f"implicit_{self.adapter.model_name}"
        collaborative = [row for row in rows if row.get("backend") == backend]
        return {
            str(row["id"]): self._rank_signal(rank)
            for rank, row in enumerate(collaborative, start=1)
            if row.get("id")
        }

    def prepare(self, user_id: str) -> list[dict]:
        reference_rows = self.reference.prepare(user_id)
        collaborative = self._collaborative_signals(user_id)
        if not collaborative:
            # Sparse/unknown users follow the exact owned cold/sparse path.
            return reference_rows

        prepared = {
            row["item"].item_id: {
                **row,
                "graph": collaborative.get(row["item"].item_id, 0.0),
                "_collaborative": collaborative.get(row["item"].item_id, 0.0),
            }
            for row in reference_rows
        }

        missing = set(collaborative) - set(prepared)
        if missing:
            full_pool = self.reference.with_config(
                replace(self.config, candidate_strategy="full_pool")
            ).prepare(user_id)
            for raw in full_pool:
                item_id = raw["item"].item_id
                if item_id not in missing:
                    continue
                signal = collaborative[item_id]
                prepared[item_id] = {
                    **raw,
                    "graph": signal,
                    "_collaborative": signal,
                }
                missing.remove(item_id)
                if not missing:
                    break
        return list(prepared.values())

    def rank_prepared(
        self,
        prepared: list[dict],
        *,
        config: RecommendConfig | None = None,
        limit: int = 10,
    ) -> list[dict]:
        rows = self.reference.rank_prepared(prepared, config=config, limit=limit)
        collaborative = {
            raw["item"].item_id: float(raw.get("_collaborative", 0.0))
            for raw in prepared
        }
        if not any(value > 0.0 for value in collaborative.values()):
            return rows
        return [
            {
                **row,
                "backend": f"hybrid_implicit_{self.adapter.model_name}",
                "collaborative_model": self.adapter.model_name,
                "signals": {
                    **dict(row.get("signals") or {}),
                    "collaborative": round(collaborative.get(str(row.get("id") or ""), 0.0), 4),
                },
            }
            for row in rows
        ]

    def recommend(self, user_id: str, *, limit: int = 10) -> list[dict]:
        limit = normalize_serving_limit(limit)
        if limit == 0:
            return []
        return self.rank_prepared(self.prepare(user_id), limit=limit)


__all__ = ["DEFAULT_COLLABORATIVE_LIMIT", "ImplicitHybridRecommendationEngine"]
