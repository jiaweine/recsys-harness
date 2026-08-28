from __future__ import annotations

from hashlib import blake2b
from math import log2
from statistics import mean
from typing import TypeVar

from lingjing_harness.domain import Catalog
from lingjing_harness.production import evaluate_logged_policy, request_groups
from .search import SearchEngine
from .recommend import RecommendationEngine
from .recommend_validation import audit_recommend_relevance

MAX_AUDIT_QUERIES = 32
MAX_AUDIT_USERS = 32
T = TypeVar("T")


def _stable_sample(rows: list[T], limit: int, key) -> list[T]:
    if len(rows) <= limit:
        return rows
    return sorted(
        rows,
        key=lambda row: blake2b(str(key(row)).encode("utf-8"), digest_size=8).digest(),
    )[:limit]


def recall_at_k(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    for i, item_id in enumerate(ranked[:k], 1):
        if item_id in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    if not relevant:
        return 0.0
    dcg = sum(1 / log2(i + 2) for i, item_id in enumerate(ranked[:k]) if item_id in relevant)
    idcg = sum(1 / log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / max(1e-9, idcg)


def _business_search(catalog: Catalog, engine: SearchEngine) -> dict | None:
    if catalog.reward_spec is None or not request_groups(catalog.events, surface="search"):
        return None
    return evaluate_logged_policy(
        catalog.events,
        surface="search",
        reward_spec=catalog.reward_spec,
        search_engine=engine,
    )


def _business_recommend(catalog: Catalog, engine: RecommendationEngine) -> dict | None:
    if catalog.reward_spec is None or not request_groups(catalog.events, surface="recommend"):
        return None
    return evaluate_logged_policy(
        catalog.events,
        surface="recommend",
        reward_spec=catalog.reward_spec,
        recommend_engine=engine,
    )


def audit_search(catalog: Catalog, engine: SearchEngine, *, labels_override=None) -> dict:
    source_labels = list(catalog.query_labels) if labels_override is None else list(labels_override)
    labels = _stable_sample(source_labels, MAX_AUDIT_QUERIES, lambda row: row.query)
    rows = []
    for label in labels:
        ranked = [row["id"] for row in engine.search(label.query, limit=10)]
        relevant = set(label.relevant)
        rows.append(
            {
                "query": label.query,
                "recall": recall_at_k(ranked, relevant),
                "mrr": reciprocal_rank(ranked, relevant),
                "ndcg": ndcg_at_k(ranked, relevant),
                "top": ranked[:3],
            }
        )
    business = _business_search(catalog, engine)
    if not rows:
        result = {
            "queries": 0,
            "available_queries": 0,
            "sampled": False,
            "quality": 0.0,
            "proxy_quality": 0.0,
            "empty_rate": round(
                sum(1 for item in catalog.items[: min(12, len(catalog.items))] if not engine.search(item.title, limit=3))
                / max(1, min(12, len(catalog.items))),
                4,
            ),
            "details": [],
        }
    else:
        proxy_quality = round(mean(row["ndcg"] for row in rows), 4)
        result = {
            "queries": len(rows),
            "available_queries": len(source_labels),
            "sampled": len(rows) < len(source_labels),
            "quality": proxy_quality,
            "proxy_quality": proxy_quality,
            "recall": round(mean(row["recall"] for row in rows), 4),
            "mrr": round(mean(row["mrr"] for row in rows), 4),
            "details": rows,
        }
    result["business_reward_available"] = business is not None
    if business is not None:
        result.update(
            {
                "business_reward": business["reward"],
                "business_reward_coverage": business["reward_coverage"],
                "business_requests": business["requests"],
                "business_estimator": business["estimator"],
            }
        )
    return result


def _category_diversity(result: list[dict]) -> float:
    categories = [category for row in result for category in row.get("categories", [])]
    return len(set(categories)) / max(1, len(categories))


def _cold_eval_ids(engine: RecommendationEngine, slice_key: str, samples: int) -> tuple[list[str], int]:
    """Generate deterministic cold identities that cannot collide with real users."""

    known = set(engine.known_users())
    identities: list[str] = []
    collisions = 0
    for index in range(max(0, samples)):
        stem = f"__harness_cold_eval__:{slice_key}:{index}"
        candidate = stem
        suffix = 0
        while candidate in known or candidate in identities:
            collisions += 1
            suffix += 1
            candidate = f"{stem}:{suffix}"
        identities.append(candidate)
    return identities, collisions


def audit_cold_start(
    catalog: Catalog,
    engine: RecommendationEngine,
    *,
    slice_key: str = "audit",
    samples: int = 3,
) -> dict:
    """Evaluate the behavior that only cold-start users can exercise.

    Discovery and holdout callers use different ``slice_key`` values, creating
    deterministic but disjoint identities. Real-user collisions are explicitly
    avoided so a synthetic cold probe can never accidentally inherit history.
    """

    identities, collisions = _cold_eval_ids(engine, slice_key, samples)
    scores: list[float] = []
    details: list[dict] = []
    for user_id in identities:
        result = engine.recommend(user_id, limit=8)
        if not result:
            details.append({"user_id": user_id, "quality": 0.0, "top": []})
            continue
        diversity = _category_diversity(result)
        freshness = mean(row["freshness"] for row in result)
        novelty = mean(row["signals"]["novelty"] for row in result)
        quality = mean(row["quality"] for row in result)
        score = (
            0.35 * quality
            + 0.25 * freshness
            + 0.20 * novelty
            + 0.20 * diversity
        )
        scores.append(score)
        details.append(
            {
                "user_id": user_id,
                "quality": round(score, 4),
                "top": [row["id"] for row in result[:4]],
            }
        )
    return {
        "samples": len(identities),
        "scored_samples": len(scores),
        "quality": round(mean(scores), 4) if scores else 0.0,
        "collision_avoided": collisions,
        "details": details,
    }


def audit_recommend(catalog: Catalog, engine: RecommendationEngine, *, users_override=None) -> dict:
    known_users = engine.known_users()
    known_set = set(known_users)
    all_users = known_users if users_override is None else [user for user in users_override if user in known_set]
    users = _stable_sample(all_users, MAX_AUDIT_USERS, lambda user: user)
    cold = audit_cold_start(catalog, engine, slice_key="audit", samples=3)
    relevance = audit_recommend_relevance(catalog, engine, users_override=users)
    business = _business_recommend(catalog, engine)

    if not users:
        result = {
            "users": 0,
            "available_users": 0,
            "sampled": False,
            "quality": 0.0,
            "proxy_quality": 0.0,
            "coverage": 0.0,
            "diversity": 0.0,
            "freshness": 0.0,
            "novelty": 0.0,
            "cold_start_quality": cold["quality"],
            "cold_start_samples": cold["samples"],
            "details": [],
        }
    else:
        exposed: set[str] = set()
        diversities: list[float] = []
        freshness_values: list[float] = []
        novelty_values: list[float] = []
        details = []
        for user in users:
            slate = engine.recommend(user, limit=8)
            exposed.update(row["id"] for row in slate)
            diversity = _category_diversity(slate)
            freshness = mean([row["freshness"] for row in slate]) if slate else 0.0
            novelty = mean([row["signals"]["novelty"] for row in slate]) if slate else 0.0
            diversities.append(diversity)
            freshness_values.append(freshness)
            novelty_values.append(novelty)
            details.append(
                {
                    "user_id": user,
                    "diversity": round(diversity, 4),
                    "freshness": round(freshness, 4),
                    "top": [row["id"] for row in slate[:4]],
                }
            )

        eligible_count = sum(1 for item in catalog.items if item.eligible)
        coverage = len(exposed) / max(1, eligible_count)
        diversity = mean(diversities)
        freshness = mean(freshness_values)
        novelty = mean(novelty_values)
        # This remains a descriptive offline guardrail composite for backward
        # compatibility. It is intentionally exposed as proxy_quality and is not
        # treated as business value when RewardSpec + production logs exist.
        proxy_quality = (
            0.41 * coverage
            + 0.23 * diversity
            + 0.18 * freshness
            + 0.08 * novelty
            + 0.10 * float(cold["quality"])
        )
        result = {
            "users": len(users),
            "available_users": len(all_users),
            "sampled": len(users) < len(all_users),
            "quality": round(proxy_quality, 4),
            "proxy_quality": round(proxy_quality, 4),
            "coverage": round(coverage, 4),
            "diversity": round(diversity, 4),
            "freshness": round(freshness, 4),
            "novelty": round(novelty, 4),
            "cold_start_quality": cold["quality"],
            "cold_start_samples": cold["samples"],
            "details": details,
        }

    model_relevance = relevance["model"]
    popularity_relevance = relevance["popularity_baseline"]
    result.update(
        {
            "relevance_available": relevance["available"],
            "relevance_users": relevance["users"],
            "relevance_protocol": relevance["protocol"],
            "relevance_k": relevance["k"],
            "relevance_hit_rate": model_relevance["hit_rate"],
            "relevance_recall": model_relevance["recall"],
            "relevance_mrr": model_relevance["mrr"],
            "relevance_ndcg": model_relevance["ndcg"],
            "popularity_relevance_ndcg": popularity_relevance["ndcg"],
            "relevance_ndcg_delta_vs_popularity": relevance["delta_vs_popularity"]["ndcg"],
            "relevance": relevance,
        }
    )
    result["business_reward_available"] = business is not None
    if business is not None:
        result.update(
            {
                "business_reward": business["reward"],
                "business_reward_coverage": business["reward_coverage"],
                "business_requests": business["requests"],
                "business_estimator": business["estimator"],
            }
        )
    return result
