from __future__ import annotations

from collections import defaultdict
from math import log2
from statistics import mean
from typing import Iterable

from lingjing_harness.domain import Catalog, Interaction
from .recommend import RecommendationEngine


DEFAULT_RELEVANCE_K = 10
MAX_RELEVANCE_USERS = 32


def _evaluation_users(
    catalog: Catalog,
    users: Iterable[str] | None,
) -> list[str]:
    allowed = set(users) if users is not None else None
    by_user: dict[str, list[Interaction]] = defaultdict(list)
    for event in catalog.interactions:
        if allowed is not None and event.user_id not in allowed:
            continue
        by_user[event.user_id].append(event)

    eligible: list[str] = []
    for user_id, events in by_user.items():
        if len(events) < 2:
            continue
        ordered = sorted(events, key=lambda row: (row.timestamp, row.item_id))
        seen: set[str] = set()
        has_temporal_target = False
        for event in ordered:
            if event.item_id not in seen and seen:
                has_temporal_target = True
            seen.add(event.item_id)
        if has_temporal_target:
            eligible.append(user_id)
    return sorted(eligible)[:MAX_RELEVANCE_USERS]


def _latest_novel_target(events: list[Interaction]) -> Interaction | None:
    ordered = sorted(events, key=lambda row: (row.timestamp, row.item_id))
    earlier: set[str] = set()
    candidates: list[Interaction] = []
    for event in ordered:
        if earlier and event.item_id not in earlier:
            candidates.append(event)
        earlier.add(event.item_id)
    return candidates[-1] if candidates else None


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


def audit_recommend_relevance(
    catalog: Catalog,
    engine: RecommendationEngine,
    *,
    users_override: Iterable[str] | None = None,
    k: int = DEFAULT_RELEVANCE_K,
) -> dict:
    """Temporal leave-one-out relevance benchmark for warm recommendation.

    For every eligible user we hold out the latest item that was novel at the
    moment it occurred, rebuild the recommender from interactions strictly before
    that timestamp, and ask whether the held-out item is recovered. This prevents
    the target interaction from entering the user profile or collaborative graph.

    A popularity baseline is evaluated on the exact same temporal split so the
    report can answer a more useful question than "does the recommender return a
    diverse slate?": does personalization beat a non-personalized ranking?
    """

    k = max(1, int(k))
    users = _evaluation_users(catalog, users_override)
    by_user: dict[str, list[Interaction]] = defaultdict(list)
    for event in catalog.interactions:
        by_user[event.user_id].append(event)

    model_rows: list[dict[str, float]] = []
    popularity_rows: list[dict[str, float]] = []
    details: list[dict] = []

    for user_id in users:
        target = _latest_novel_target(by_user[user_id])
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
        candidate_engine = RecommendationEngine(training_catalog, config=engine.config)
        model_ranked = [
            row["id"]
            for row in candidate_engine.recommend(user_id, limit=k)
        ]
        popularity_ranked = _popularity_rank(training_catalog, seen, k=k)

        model_metric = _single_target_metrics(model_ranked, target.item_id, k)
        popularity_metric = _single_target_metrics(popularity_ranked, target.item_id, k)
        model_rows.append(model_metric)
        popularity_rows.append(popularity_metric)
        details.append(
            {
                "user_id": user_id,
                "target": target.item_id,
                "target_timestamp": target.timestamp,
                "history": len(user_history),
                "model_rank": (
                    model_ranked.index(target.item_id) + 1
                    if target.item_id in model_ranked
                    else None
                ),
                "popularity_rank": (
                    popularity_ranked.index(target.item_id) + 1
                    if target.item_id in popularity_ranked
                    else None
                ),
            }
        )

    def aggregate(rows: list[dict[str, float]]) -> dict[str, float]:
        if not rows:
            return {"hit_rate": 0.0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
        return {
            key: round(mean(row[key] for row in rows), 4)
            for key in ("hit_rate", "recall", "mrr", "ndcg")
        }

    model = aggregate(model_rows)
    popularity = aggregate(popularity_rows)
    return {
        "available": bool(model_rows),
        "users": len(model_rows),
        "k": k,
        "protocol": "strict_temporal_leave_one_out",
        "model": model,
        "popularity_baseline": popularity,
        "delta_vs_popularity": {
            key: round(model[key] - popularity[key], 4)
            for key in ("hit_rate", "recall", "mrr", "ndcg")
        },
        "details": details,
    }


__all__ = ["audit_recommend_relevance"]
