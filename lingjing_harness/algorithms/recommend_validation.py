from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import log2
from statistics import mean
from typing import Iterable

from lingjing_harness.domain import Catalog, Interaction
from .recommend import RecommendConfig, RecommendationEngine


DEFAULT_RELEVANCE_K = 10
MAX_RELEVANCE_USERS = 32
DEFAULT_MIN_TARGET_WEIGHT = 1.0


def _latest_novel_target(
    events: list[Interaction],
    *,
    minimum_target_weight: float,
) -> Interaction | None:
    ordered = sorted(events, key=lambda row: (row.timestamp, row.item_id))
    earlier: set[str] = set()
    candidates: list[Interaction] = []
    for event in ordered:
        if (
            earlier
            and event.item_id not in earlier
            and float(event.weight) >= minimum_target_weight
        ):
            candidates.append(event)
        earlier.add(event.item_id)
    return candidates[-1] if candidates else None


def _evaluation_users(
    catalog: Catalog,
    users: Iterable[str] | None,
    *,
    minimum_target_weight: float,
) -> list[str]:
    allowed = set(users) if users is not None else None
    by_user: dict[str, list[Interaction]] = defaultdict(list)
    for event in catalog.interactions:
        if allowed is not None and event.user_id not in allowed:
            continue
        by_user[event.user_id].append(event)

    eligible = [
        user_id
        for user_id, events in by_user.items()
        if len(events) >= 2
        and _latest_novel_target(
            events,
            minimum_target_weight=minimum_target_weight,
        )
        is not None
    ]
    return sorted(eligible)[:MAX_RELEVANCE_USERS]


def _single_target_metrics(ranked: list[str], target: str, k: int) -> dict[str, float]:
    top = ranked[:k]
    if target not in top:
        return {"hit_rate": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    rank = top.index(target) + 1
    return {
        "hit_rate": 1.0,
        "recall": 1.0,
        "mrr": 1.0 / rank,
        "ndcg": 1.0 / log2(rank + 1),
    }


def _popularity_rank(catalog: Catalog, seen: set[str], *, k: int) -> list[str]:
    candidates = [
        item
        for item in catalog.items
        if item.eligible and item.item_id not in seen
    ]
    candidates.sort(
        key=lambda item: (
            -catalog.popularity_norm(item),
            -item.quality,
            -item.freshness,
            item.item_id,
        )
    )
    return [item.item_id for item in candidates[:k]]


def _aggregate(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {"hit_rate": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    return {
        key: round(mean(row[key] for row in rows), 4)
        for key in ("hit_rate", "recall", "mrr", "ndcg")
    }


@dataclass(slots=True)
class _PreparedSlice:
    user_id: str
    target: str
    target_timestamp: float
    history: int
    engine: RecommendationEngine
    popularity_ranked: list[str]


@dataclass(slots=True)
class PreparedRecommendRelevance:
    """Reusable interaction-temporal relevance slices for candidate evaluation.

    Expensive temporal Catalog/RecommendationEngine construction happens once per
    user. Candidate strategies then reuse those immutable features via
    ``RecommendationEngine.with_config`` so an evolution run can regression-check
    many configs without rebuilding the same historical graph for every candidate.

    Only interaction history is point-in-time. Item-side popularity, freshness and
    quality are still the Catalog snapshot supplied by the caller, so reports make
    that evidence boundary explicit instead of presenting this as full historical
    feature reconstruction.
    """

    slices: list[_PreparedSlice]
    k: int
    minimum_target_weight: float

    def evaluate(self, config: RecommendConfig) -> dict:
        model_rows: list[dict[str, float]] = []
        popularity_rows: list[dict[str, float]] = []
        details: list[dict] = []

        for row in self.slices:
            candidate_engine = row.engine.with_config(config)
            model_ranked = [
                item["id"]
                for item in candidate_engine.recommend(row.user_id, limit=self.k)
            ]
            model_metric = _single_target_metrics(model_ranked, row.target, self.k)
            popularity_metric = _single_target_metrics(
                row.popularity_ranked,
                row.target,
                self.k,
            )
            model_rows.append(model_metric)
            popularity_rows.append(popularity_metric)
            details.append(
                {
                    "user_id": row.user_id,
                    "target": row.target,
                    "target_timestamp": row.target_timestamp,
                    "history": row.history,
                    "model_rank": (
                        model_ranked.index(row.target) + 1
                        if row.target in model_ranked
                        else None
                    ),
                    "popularity_rank": (
                        row.popularity_ranked.index(row.target) + 1
                        if row.target in row.popularity_ranked
                        else None
                    ),
                }
            )

        model = _aggregate(model_rows)
        popularity = _aggregate(popularity_rows)
        return {
            "available": bool(model_rows),
            "users": len(model_rows),
            "k": self.k,
            "protocol": "strict_temporal_leave_one_out",
            "temporal_scope": "interactions_only",
            "point_in_time_item_features": False,
            "minimum_target_weight": self.minimum_target_weight,
            "prepared_slices": len(self.slices),
            "model": model,
            "popularity_baseline": popularity,
            "delta_vs_popularity": {
                key: round(model[key] - popularity[key], 4)
                for key in ("hit_rate", "recall", "mrr", "ndcg")
            },
            "details": details,
        }


def prepare_recommend_relevance(
    catalog: Catalog,
    engine: RecommendationEngine,
    *,
    users_override: Iterable[str] | None = None,
    k: int = DEFAULT_RELEVANCE_K,
    minimum_target_weight: float = DEFAULT_MIN_TARGET_WEIGHT,
) -> PreparedRecommendRelevance:
    """Build reusable interaction-temporal slices for recommendation evaluation."""

    k = max(1, int(k))
    minimum_target_weight = max(0.0, float(minimum_target_weight))
    users = _evaluation_users(
        catalog,
        users_override,
        minimum_target_weight=minimum_target_weight,
    )
    by_user: dict[str, list[Interaction]] = defaultdict(list)
    for event in catalog.interactions:
        by_user[event.user_id].append(event)

    slices: list[_PreparedSlice] = []
    for user_id in users:
        target = _latest_novel_target(
            by_user[user_id],
            minimum_target_weight=minimum_target_weight,
        )
        if target is None:
            continue

        training_interactions = [
            event
            for event in catalog.interactions
            if event.timestamp < target.timestamp
        ]
        user_history = [
            event
            for event in training_interactions
            if event.user_id == user_id
        ]
        if not user_history:
            continue
        seen = {event.item_id for event in user_history}
        if target.item_id in seen:
            continue

        training_catalog = Catalog(
            items=list(catalog.items),
            interactions=training_interactions,
            query_labels=list(catalog.query_labels),
            events=[],
            reward_spec=None,
            name=f"{catalog.name}:temporal-relevance:{user_id}",
        )
        base_engine = RecommendationEngine(training_catalog, config=engine.config)
        slices.append(
            _PreparedSlice(
                user_id=user_id,
                target=target.item_id,
                target_timestamp=target.timestamp,
                history=len(user_history),
                engine=base_engine,
                popularity_ranked=_popularity_rank(training_catalog, seen, k=k),
            )
        )

    return PreparedRecommendRelevance(
        slices=slices,
        k=k,
        minimum_target_weight=minimum_target_weight,
    )


def audit_recommend_relevance(
    catalog: Catalog,
    engine: RecommendationEngine,
    *,
    users_override: Iterable[str] | None = None,
    k: int = DEFAULT_RELEVANCE_K,
    minimum_target_weight: float = DEFAULT_MIN_TARGET_WEIGHT,
) -> dict:
    """Interaction-temporal leave-one-out relevance benchmark for warm recommendation."""

    prepared = prepare_recommend_relevance(
        catalog,
        engine,
        users_override=users_override,
        k=k,
        minimum_target_weight=minimum_target_weight,
    )
    return prepared.evaluate(engine.config)


__all__ = [
    "PreparedRecommendRelevance",
    "audit_recommend_relevance",
    "prepare_recommend_relevance",
]
