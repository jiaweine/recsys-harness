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
    return sorted(rows, key=lambda row: blake2b(str(key(row)).encode("utf-8"), digest_size=8).digest())[:limit]


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
    dcg = sum(1 / log2(i + 2) for i, x in enumerate(ranked[:k]) if x in relevant)
    idcg = sum(1 / log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / max(1e-9, idcg)


def audit_search(catalog: Catalog, engine: SearchEngine, *, labels_override=None) -> dict:
    source_labels = list(catalog.query_labels) if labels_override is None else list(labels_override)
    labels = _stable_sample(source_labels, MAX_AUDIT_QUERIES, lambda x: x.query)
    rows = []
    for label in labels:
        ranked = [x["id"] for x in engine.search(label.query, limit=10)]
        rel = set(label.relevant)
        rows.append({
            "query": label.query,
            "recall": recall_at_k(ranked, rel),
            "mrr": reciprocal_rank(ranked, rel),
            "ndcg": ndcg_at_k(ranked, rel),
            "top": ranked[:3],
        })
    if not rows:
        probes = catalog.items[:min(12, len(catalog.items))]
        empty = sum(1 for x in probes if not engine.search(x.title, limit=3))
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
        "quality": round(mean(x["ndcg"] for x in rows), 4),
        "recall": round(mean(x["recall"] for x in rows), 4),
        "mrr": round(mean(x["mrr"] for x in rows), 4),
        "details": rows,
    }


def _category_diversity(result: list[dict]) -> float:
    cats = [c for x in result for c in x.get("categories", [])]
    return len(set(cats)) / max(1, len(cats))


def audit_recommend(catalog: Catalog, engine: RecommendationEngine, *, users_override=None) -> dict:
    all_users = engine.known_users() if users_override is None else [user for user in users_override if user in set(engine.known_users())]
    users = _stable_sample(all_users, MAX_AUDIT_USERS, lambda x: x)
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
            "details": [],
        }
    exposed = set()
    diversities = []
    freshness = []
    novelty = []
    details = []
    for user in users:
        result = engine.recommend(user, limit=8)
        exposed.update(x["id"] for x in result)
        div = _category_diversity(result)
        fresh = mean([x["freshness"] for x in result]) if result else 0.0
        nov = mean([x["signals"]["novelty"] for x in result]) if result else 0.0
        diversities.append(div)
        freshness.append(fresh)
        novelty.append(nov)
        details.append({"user_id": user, "diversity": round(div, 4), "freshness": round(fresh, 4), "top": [x["id"] for x in result[:4]]})
    eligible_count = sum(1 for item in catalog.items if item.eligible)
    coverage = len(exposed) / max(1, eligible_count)
    quality = .45*coverage + .25*mean(diversities) + .20*mean(freshness) + .10*mean(novelty)
    return {
        "users": len(users),
        "available_users": len(all_users),
        "sampled": len(users) < len(all_users),
        "quality": round(quality, 4),
        "coverage": round(coverage, 4),
        "diversity": round(mean(diversities), 4),
        "freshness": round(mean(freshness), 4),
        "novelty": round(mean(novelty), 4),
        "details": details,
    }
