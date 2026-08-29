from __future__ import annotations

from typing import Any

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog
from lingjing_harness.serving import normalize_serving_limit


SUPPORTED_IMPLICIT_MODELS = ("bpr", "als", "bm25")
DEFAULT_IMPLICIT_MODEL = "als"
DEFAULT_MIN_HISTORY = 3


def _load_implicit_dependencies():
    try:
        import numpy as np
        from scipy.sparse import csr_matrix
        from implicit.als import AlternatingLeastSquares
        from implicit.bpr import BayesianPersonalizedRanking
        from implicit.nearest_neighbours import BM25Recommender
    except ImportError as exc:
        raise RuntimeError(
            "ImplicitRecommendationAdapter requires the optional collaborative dependencies; "
            "install with `pip install -e '.[collaborative]'`"
        ) from exc
    return np, csr_matrix, AlternatingLeastSquares, BayesianPersonalizedRanking, BM25Recommender


class ImplicitRecommendationAdapter:
    """Thin serving adapter around the mature ``implicit`` recommenders.

    Collaborative ranking is used only for users with enough positive interaction
    history. Unknown and sparse users retain the owned reference recommender so
    cold-start behavior does not depend on a factor model with no user evidence.
    Eligibility and already-consumed filtering remain Harness-owned contracts;
    the warm-user ranking itself is delegated to ``implicit``.
    """

    def __init__(
        self,
        catalog: Catalog,
        *,
        model: str = DEFAULT_IMPLICIT_MODEL,
        min_history: int = DEFAULT_MIN_HISTORY,
        model_kwargs: dict[str, Any] | None = None,
        fallback: RecommendationEngine | None = None,
    ) -> None:
        model_name = str(model).strip().lower()
        if model_name not in SUPPORTED_IMPLICIT_MODELS:
            raise ValueError(
                f"unknown implicit recommendation model: {model_name}; expected one of "
                f"{', '.join(SUPPORTED_IMPLICIT_MODELS)}"
            )
        if int(min_history) < 1:
            raise ValueError("min_history must be at least 1")

        np, csr_matrix, ALS, BPR, BM25 = _load_implicit_dependencies()
        self._np = np
        self.catalog = catalog
        self.model_name = model_name
        self.min_history = int(min_history)
        self.fallback = fallback or RecommendationEngine(catalog)

        self.user_ids = sorted({event.user_id for event in catalog.interactions if event.weight > 0.0})
        self.item_ids = [item.item_id for item in catalog.items]
        self.user_index = {user_id: index for index, user_id in enumerate(self.user_ids)}
        self.item_index = {item_id: index for index, item_id in enumerate(self.item_ids)}

        rows: list[int] = []
        cols: list[int] = []
        values: list[float] = []
        for event in catalog.interactions:
            if event.weight <= 0.0 or event.user_id not in self.user_index:
                continue
            item_index = self.item_index.get(event.item_id)
            if item_index is None:
                continue
            rows.append(self.user_index[event.user_id])
            cols.append(item_index)
            values.append(float(event.weight))

        self.user_items = csr_matrix(
            (values, (rows, cols)),
            shape=(len(self.user_ids), len(self.item_ids)),
            dtype=np.float32,
        )
        self.user_items.sum_duplicates()
        self.user_items.sort_indices()
        self._history_counts = self._np.asarray(self.user_items.getnnz(axis=1)).ravel()
        self._ineligible_indices = self._np.asarray(
            [index for index, item in enumerate(catalog.items) if not item.eligible],
            dtype=self._np.int32,
        )

        kwargs = dict(model_kwargs or {})
        if model_name == "bpr":
            defaults = {
                "factors": 32,
                "learning_rate": 0.01,
                "regularization": 0.01,
                "iterations": 80,
                "num_threads": 1,
                "use_gpu": False,
                "verify_negative_samples": True,
                "random_state": 42,
            }
            defaults.update(kwargs)
            self.model = BPR(**defaults)
        elif model_name == "als":
            defaults = {
                "factors": 32,
                "regularization": 0.05,
                "alpha": 1.0,
                "iterations": 20,
                "use_gpu": False,
                "random_state": 42,
            }
            defaults.update(kwargs)
            self.model = ALS(**defaults)
        else:
            defaults = {"K": 40, "K1": 1.2, "B": 0.75}
            defaults.update(kwargs)
            self.model = BM25(**defaults)

        if self.user_items.nnz:
            self.model.fit(self.user_items, show_progress=False)

    def known_users(self) -> list[str]:
        return list(self.user_ids)

    def history_count(self, user_id: str) -> int:
        user_index = self.user_index.get(user_id)
        if user_index is None:
            return 0
        return int(self._history_counts[user_index])

    def capability_manifest(self) -> dict[str, Any]:
        return {
            "backend": "implicit",
            "model": self.model_name,
            "min_history": self.min_history,
            "training_users": len(self.user_ids),
            "training_items": len(self.item_ids),
            "training_interactions": int(self.user_items.nnz),
            "fallback": "reference",
        }

    def _fallback(self, user_id: str, *, limit: int, reason: str) -> list[dict[str, Any]]:
        return [
            {
                **row,
                "backend": "reference",
                "backend_reason": reason,
            }
            for row in self.fallback.recommend(user_id, limit=limit)
        ]

    def recommend(self, user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        limit = normalize_serving_limit(limit)
        if limit == 0:
            return []
        user_index = self.user_index.get(user_id)
        if user_index is None:
            return self._fallback(user_id, limit=limit, reason="unknown_user")
        history = int(self._history_counts[user_index])
        if history < self.min_history:
            return self._fallback(user_id, limit=limit, reason="history_below_collaborative_threshold")
        if not self.user_items.nnz:
            return self._fallback(user_id, limit=limit, reason="collaborative_training_data_unavailable")

        user_row = self.user_items[user_index]
        seen_indices = {int(index) for index in user_row.indices}
        request_limit = min(
            len(self.item_ids),
            max(limit, limit * 2 + len(seen_indices) + int(self._ineligible_indices.size)),
        )
        kwargs: dict[str, Any] = {
            "N": request_limit,
            "filter_already_liked_items": True,
        }
        if self._ineligible_indices.size:
            kwargs["filter_items"] = self._ineligible_indices
        ids, scores = self.model.recommend(
            user_index,
            user_row,
            **kwargs,
        )

        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        for raw_index, raw_score in zip(self._np.asarray(ids).ravel(), self._np.asarray(scores).ravel()):
            item_index = int(raw_index)
            if item_index < 0 or item_index >= len(self.item_ids) or item_index in seen_indices:
                continue
            item_id = self.item_ids[item_index]
            item = self.catalog.item_by_id[item_id]
            if not item.eligible or item_id in selected_ids:
                continue
            score = float(raw_score)
            selected_ids.add(item_id)
            selected.append(
                {
                    "rank": len(selected) + 1,
                    **item.public_dict(),
                    "score": round(score, 6),
                    "backend": f"implicit_{self.model_name}",
                    "signals": {
                        "collaborative": round(score, 6),
                        "quality": round(item.quality, 4),
                        "freshness": round(item.freshness, 4),
                    },
                }
            )
            if len(selected) >= limit:
                break

        if len(selected) < limit:
            for row in self.fallback.recommend(user_id, limit=limit * 2):
                item_id = str(row.get("id") or "")
                if not item_id or item_id in selected_ids:
                    continue
                selected_ids.add(item_id)
                selected.append(
                    {
                        **row,
                        "rank": len(selected) + 1,
                        "backend": "reference_fill",
                        "backend_reason": "collaborative_slate_shortfall",
                    }
                )
                if len(selected) >= limit:
                    break
        return selected


__all__ = [
    "SUPPORTED_IMPLICIT_MODELS",
    "DEFAULT_IMPLICIT_MODEL",
    "DEFAULT_MIN_HISTORY",
    "ImplicitRecommendationAdapter",
]
