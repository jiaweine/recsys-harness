from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from lingjing_harness.domain import Catalog
from lingjing_harness.production import ExposureEvent, request_groups
from .recommend import RecommendationEngine
from .search import SearchEngine


MIN_ROUTING_CONTEXTS = 4
SEARCH_SEGMENTS = (
    "search/no-anchor",
    "search/candidate-scarce",
    "search/weak-anchor",
    "search/strong-anchor",
    "search/mixed",
    "search/general",
)
RECOMMEND_SEGMENTS = (
    "recommend/cold-start",
    "recommend/candidate-scarce",
    "recommend/sparse-history",
    "recommend/established",
    "recommend/mixed",
    "recommend/warm",
)


def strategy_domain(surface: str, segment: str) -> str:
    prefix = f"{surface}/"
    if surface not in {"search", "recommend"} or not segment.startswith(prefix):
        raise ValueError("segment does not belong to surface")
    return f"{surface}.segment.{segment[len(prefix):]}"


def _quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, float(fraction))) * (len(ordered) - 1)
    left = int(position)
    right = min(len(ordered) - 1, left + 1)
    weight = position - left
    return ordered[left] * (1.0 - weight) + ordered[right] * weight


def _spread(values: list[float]) -> bool:
    return bool(values) and max(values) - min(values) > 1e-9


@dataclass(frozen=True, slots=True)
class SearchRequestFeatures:
    candidate_count: int
    anchor_strength: float


@dataclass(frozen=True, slots=True)
class RecommendRequestFeatures:
    history_events: int
    eligible_unseen: int


class SegmentRouter:
    """Route requests by observable pathology using traffic-derived quantiles.

    The router deliberately does not encode an optimization preference. It only
    describes the request context using the current owned engines and the observed
    production-request distribution. Segment-specific strategies are validated
    elsewhere and always fall back to the global strategy when evidence is thin.
    """

    def __init__(
        self,
        catalog: Catalog,
        search: SearchEngine,
        recommend: RecommendationEngine,
    ) -> None:
        self.catalog = catalog
        self.search = search
        self.recommend = recommend
        self._eligible_item_ids = frozenset(
            item.item_id for item in catalog.items if item.eligible
        )

        search_rows = request_groups(catalog.events, surface="search")
        search_calibration = [
            (
                request_id,
                self.search_features(next((row.query for row in rows if row.query), "")),
            )
            for request_id, rows in sorted(search_rows.items())
        ]
        self._search_calibration = [features for _, features in search_calibration]
        search_candidates = [float(row.candidate_count) for row in self._search_calibration]
        search_anchors = [float(row.anchor_strength) for row in self._search_calibration]
        self.search_thresholds = {
            "candidate_low": _quantile(search_candidates, 1.0 / 3.0),
            "anchor_low": _quantile(search_anchors, 1.0 / 3.0),
            "anchor_high": _quantile(search_anchors, 2.0 / 3.0),
            "candidate_spread": _spread(search_candidates),
            "anchor_spread": _spread(search_anchors),
        }

        recommend_rows = request_groups(catalog.events, surface="recommend")
        recommend_calibration: list[tuple[str, RecommendRequestFeatures]] = []
        for request_id, rows in sorted(recommend_rows.items()):
            user_id = next((row.user_id for row in rows if row.user_id), "")
            recommend_calibration.append((request_id, self.recommend_features(user_id)))
        self._recommend_calibration = [
            features
            for _, features in recommend_calibration
            if features.history_events > 0
        ]
        histories = [float(row.history_events) for row in self._recommend_calibration]
        unseen = [float(row.eligible_unseen) for row in self._recommend_calibration]
        self.recommend_thresholds = {
            "history_low": _quantile(histories, 1.0 / 3.0),
            "history_high": _quantile(histories, 2.0 / 3.0),
            "unseen_low": _quantile(unseen, 1.0 / 3.0),
            "history_spread": _spread(histories),
            "unseen_spread": _spread(unseen),
        }

        self._catalog_request_segments = {
            "search": {
                request_id: self._search_segment_for_features(features)
                for request_id, features in search_calibration
            },
            "recommend": {
                request_id: self._recommend_segment_for_features(features)
                for request_id, features in recommend_calibration
            },
        }
        self._catalog_segment_counts = {
            surface: Counter(assignments.values())
            for surface, assignments in self._catalog_request_segments.items()
        }

    @staticmethod
    def _search_routing_signature(search: SearchEngine) -> tuple[str, str]:
        config = search.config
        return (str(config.query_strategy), str(config.candidate_strategy))

    def rebind(
        self,
        catalog: Catalog,
        search: SearchEngine,
        recommend: RecommendationEngine,
    ) -> "SegmentRouter":
        """Reuse traffic calibration when the routing basis is unchanged.

        Search routing features depend on the query/candidate strategies used by
        prepare; recommendation routing features depend only on catalog/user
        history. Forked engines share the same catalog features, so a fork can
        reuse the expensive production-traffic calibration unless one of the
        search routing strategies changed.
        """

        if (
            catalog is not self.catalog
            or self._search_routing_signature(search)
            != self._search_routing_signature(self.search)
        ):
            return type(self)(catalog, search, recommend)

        clone = object.__new__(type(self))
        clone.catalog = catalog
        clone.search = search
        clone.recommend = recommend
        clone._search_calibration = self._search_calibration
        clone.search_thresholds = self.search_thresholds
        clone._recommend_calibration = self._recommend_calibration
        clone.recommend_thresholds = self.recommend_thresholds
        clone._eligible_item_ids = self._eligible_item_ids
        clone._catalog_request_segments = self._catalog_request_segments
        clone._catalog_segment_counts = self._catalog_segment_counts
        return clone

    @staticmethod
    def search_features_from_prepared(prepared: list[dict[str, Any]]) -> SearchRequestFeatures:
        if not prepared:
            return SearchRequestFeatures(candidate_count=0, anchor_strength=0.0)
        anchor = max(
            0.65 * float(row.get("lex", 0.0))
            + 0.20 * float(row.get("title", 0.0))
            + 0.15 * float(row.get("sem", 0.0))
            for row in prepared
        )
        return SearchRequestFeatures(candidate_count=len(prepared), anchor_strength=anchor)

    def search_features(self, query: str) -> SearchRequestFeatures:
        prepare = getattr(self.search, "routing_prepare", None)
        if prepare is None:
            prepare = self.search.prepare
        prepared = prepare((query or "").strip())
        return self.search_features_from_prepared(prepared)

    def recommend_features(self, user_id: str) -> RecommendRequestFeatures:
        events = self.recommend._by_user.get(user_id or "", [])
        seen = {event.item_id for event in events}
        eligible_seen = sum(
            1 for item_id in seen if item_id in self._eligible_item_ids
        )
        return RecommendRequestFeatures(
            history_events=len(events),
            eligible_unseen=len(self._eligible_item_ids) - eligible_seen,
        )

    def _search_segment_for_features(self, features: SearchRequestFeatures) -> str:
        if features.candidate_count == 0:
            return "search/no-anchor"
        if len(self._search_calibration) < MIN_ROUTING_CONTEXTS:
            return "search/general"
        thresholds = self.search_thresholds
        if thresholds["candidate_spread"] and features.candidate_count <= float(thresholds["candidate_low"]):
            return "search/candidate-scarce"
        if thresholds["anchor_spread"] and features.anchor_strength <= float(thresholds["anchor_low"]):
            return "search/weak-anchor"
        if thresholds["anchor_spread"] and features.anchor_strength >= float(thresholds["anchor_high"]):
            return "search/strong-anchor"
        return "search/mixed"

    def _recommend_segment_for_features(self, features: RecommendRequestFeatures) -> str:
        if features.history_events == 0:
            return "recommend/cold-start"
        if len(self._recommend_calibration) < MIN_ROUTING_CONTEXTS:
            return "recommend/warm"
        thresholds = self.recommend_thresholds
        if thresholds["unseen_spread"] and features.eligible_unseen <= float(thresholds["unseen_low"]):
            return "recommend/candidate-scarce"
        if thresholds["history_spread"] and features.history_events <= float(thresholds["history_low"]):
            return "recommend/sparse-history"
        if thresholds["history_spread"] and features.history_events >= float(thresholds["history_high"]):
            return "recommend/established"
        return "recommend/mixed"

    def search_segment_from_prepared(self, prepared: list[dict[str, Any]]) -> str:
        return self._search_segment_for_features(
            self.search_features_from_prepared(prepared)
        )

    def search_segment(self, query: str) -> str:
        return self._search_segment_for_features(self.search_features(query))

    def recommend_segment(self, user_id: str) -> str:
        return self._recommend_segment_for_features(self.recommend_features(user_id))

    def partition_events(
        self,
        events: Iterable[ExposureEvent],
        *,
        surface: str,
    ) -> dict[str, list[ExposureEvent]]:
        if surface not in {"search", "recommend"}:
            raise ValueError("surface must be search or recommend")
        grouped = request_groups(events, surface=surface)
        catalog_segments = self._catalog_request_segments[surface]
        cached = (
            catalog_segments
            if events is self.catalog.events
            and grouped.keys() == catalog_segments.keys()
            else None
        )
        partitions: dict[str, list[ExposureEvent]] = {}
        for request_id, rows in sorted(grouped.items()):
            if cached is not None:
                segment = cached[request_id]
            elif surface == "search":
                query = next((row.query for row in rows if row.query), "")
                segment = self.search_segment(query)
            else:
                user_id = next((row.user_id for row in rows if row.user_id), "")
                segment = self.recommend_segment(user_id)
            partitions.setdefault(segment, []).extend(rows)
        return partitions

    def manifest(self, surface: str) -> dict[str, Any]:
        if surface == "search":
            thresholds = dict(self.search_thresholds)
        elif surface == "recommend":
            thresholds = dict(self.recommend_thresholds)
        else:
            raise ValueError("surface must be search or recommend")
        return {
            "surface": surface,
            "routing_basis": "production_traffic_quantiles",
            "contexts": len(self._catalog_request_segments[surface]),
            "thresholds": thresholds,
            "requests_by_segment": {
                segment: int(count)
                for segment, count in sorted(self._catalog_segment_counts[surface].items())
            },
        }

    @staticmethod
    def known_segments(surface: str) -> tuple[str, ...]:
        if surface == "search":
            return SEARCH_SEGMENTS
        if surface == "recommend":
            return RECOMMEND_SEGMENTS
        raise ValueError("surface must be search or recommend")


__all__ = [
    "MIN_ROUTING_CONTEXTS",
    "SEARCH_SEGMENTS",
    "RECOMMEND_SEGMENTS",
    "SearchRequestFeatures",
    "RecommendRequestFeatures",
    "SegmentRouter",
    "strategy_domain",
]
