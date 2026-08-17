from __future__ import annotations

from hashlib import blake2b
from math import log2
from statistics import mean
from typing import TypeVar

from lingjing_harness.domain import Catalog
from .search import SearchEngine
from .recommend import RecommendationEngine

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
    if not rows:
        probes = catalog.items[: min(12, len(catalog.items))]
        empty = sum(1 for item in probes if not engine.search(item.title, limit=3))
        return {
            "queries": 0,
            "available_queries": 0,
            "sampled": False,
            "quality": 0.0,
            "empty_rate": round(empty / max(1, len(probes)), 4),
            "details": [],
        }
    return {
        "queries": len(rows),
        "available_queries": len(source_labels),
        "sampled": len(rows) < len(source_labels),
        "quality": round(mean(row["ndcg"] for row in rows), 4),
        "recall": round(mean(row["recall"] for row in rows), 4),
        "mrr": round(mean(row["mrr"] for row in rows), 4),
        "details": rows,
    }


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

    if not users:
        return {
            "users": 0,
            "available_users": 0,
            "sampled": False,
            "quality": 0.0,
            "coverage": 0.0,
            "diversity": 0.0,
            "freshness": 0.0,
            "novelty": 0.0,
            "cold_start_quality": cold["quality"],
            "cold_start_samples": cold["samples"],
            "details": [],
        }

    exposed: set[str] = set()
    diversities: list[float] = []
    freshness_values: list[float] = []
    novelty_values: list[float] = []
    details = []
    for user in users:
        result = engine.recommend(user, limit=8)
        exposed.update(row["id"] for row in result)
        diversity = _category_diversity(result)
        freshness = mean([row["freshness"] for row in result]) if result else 0.0
        novelty = mean([row["signals"]["novelty"] for row in result]) if result else 0.0
        diversities.append(diversity)
        freshness_values.append(freshness)
        novelty_values.append(novelty)
        details.append(
            {
                "user_id": user,
                "diversity": round(diversity, 4),
                "freshness": round(freshness, 4),
                "top": [row["id"] for row in result[:4]],
            }
        )

    eligible_count = sum(1 for item in catalog.items if item.eligible)
    coverage = len(exposed) / max(1, eligible_count)
    diversity = mean(diversities)
    freshness = mean(freshness_values)
    novelty = mean(novelty_values)
    quality = (
        0.41 * coverage
        + 0.23 * diversity
        + 0.18 * freshness
        + 0.08 * novelty
        + 0.10 * float(cold["quality"])
    )
    return {
        "users": len(users),
        "available_users": len(all_users),
        "sampled": len(users) < len(all_users),
        "quality": round(quality, 4),
        "coverage": round(coverage, 4),
        "diversity": round(diversity, 4),
        "freshness": round(freshness, 4),
        "novelty": round(novelty, 4),
        "cold_start_quality": cold["quality"],
        "cold_start_samples": cold["samples"],
        "details": details,
    }
