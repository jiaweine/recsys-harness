from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Mapping, Protocol, Sequence

from .serving import normalize_serving_limit


class SearchServingAdapter(Protocol):
    """Minimal read-only contract for an existing production search service."""

    def search(self, query: str, *, limit: int = 10) -> Sequence[Mapping[str, Any]]: ...


class RecommendServingAdapter(Protocol):
    """Minimal read-only contract for an existing production recommender."""

    def recommend(self, user_id: str, *, limit: int = 10) -> Sequence[Mapping[str, Any]]: ...


def normalize_ranked_rows(rows: Sequence[Mapping[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Fail closed on malformed external ranking rows and deduplicate IDs."""

    normalized_limit = normalize_serving_limit(limit)
    if normalized_limit == 0:
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        item_id = str(raw.get("id") or raw.get("item_id") or "").strip()
        if not item_id or item_id in seen:
            continue
        score = raw.get("score")
        normalized_score: float | None = None
        if score is not None:
            try:
                normalized_score = float(score)
            except (TypeError, ValueError):
                continue
            if not isfinite(normalized_score):
                continue
        seen.add(item_id)
        row = dict(raw)
        row["id"] = item_id
        if normalized_score is not None:
            row["score"] = normalized_score
        row["rank"] = len(out) + 1
        out.append(row)
        if len(out) >= normalized_limit:
            break
    return out


@dataclass(slots=True)
class AdapterSearchEngine:
    """Replay-compatible facade around a real search serving adapter."""

    adapter: SearchServingAdapter

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        normalized_limit = normalize_serving_limit(limit)
        if normalized_limit == 0:
            return []
        return normalize_ranked_rows(
            self.adapter.search(query, limit=normalized_limit),
            limit=normalized_limit,
        )


@dataclass(slots=True)
class AdapterRecommendationEngine:
    """Replay-compatible facade around a real recommendation serving adapter."""

    adapter: RecommendServingAdapter

    def recommend(self, user_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        normalized_limit = normalize_serving_limit(limit)
        if normalized_limit == 0:
            return []
        return normalize_ranked_rows(
            self.adapter.recommend(user_id, limit=normalized_limit),
            limit=normalized_limit,
        )


@dataclass(slots=True)
class CallableSearchAdapter:
    handler: Callable[[str, int], Sequence[Mapping[str, Any]]]

    def search(self, query: str, *, limit: int = 10) -> Sequence[Mapping[str, Any]]:
        return self.handler(query, limit)


@dataclass(slots=True)
class CallableRecommendAdapter:
    handler: Callable[[str, int], Sequence[Mapping[str, Any]]]

    def recommend(self, user_id: str, *, limit: int = 10) -> Sequence[Mapping[str, Any]]:
        return self.handler(user_id, limit)


__all__ = [
    "SearchServingAdapter",
    "RecommendServingAdapter",
    "AdapterSearchEngine",
    "AdapterRecommendationEngine",
    "CallableSearchAdapter",
    "CallableRecommendAdapter",
    "normalize_ranked_rows",
]
