from __future__ import annotations

from dataclasses import asdict
from typing import Any

from lingjing_harness.domain import Catalog
from lingjing_harness.production import temporal_request_split
from .credit_routing import filter_segment_candidates
from .recommend import RecommendationEngine
from .search import SearchEngine
from .segments import SegmentRouter, strategy_domain
from . import evolution_core as core
from . import segment_evolution as segment_core


def attach_search_portfolio(
    catalog: Catalog,
    current: SearchEngine,
    result: dict[str, Any],
    *,
    remembered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not catalog.reward_spec or not (result.get("business_validation") or {}).get("available"):
        return result
    discovery_events, holdout_events = temporal_request_split(catalog.events, surface="search")
    if not holdout_events:
        return result

    remembered = remembered or []
    router = SegmentRouter(catalog, current, RecommendationEngine(catalog))
    discovery = router.partition_events(discovery_events, surface="search")
    holdout = router.partition_events(holdout_events, surface="search")
    full = router.partition_events(catalog.events, surface="search")
    base_config = asdict(current.config)
    seed_config = result.get("candidate_config") if isinstance(result.get("candidate_config"), dict) else base_config
    dimensions, group_totals = core._evolution_schema(current.config)
    candidate_pool = segment_core._candidate_pool(base_config, seed_config, dimensions, group_totals)

    entries = []
    routing_credit: dict[str, Any] = {}
    for segment in sorted(full):
        domain = strategy_domain("search", segment)
        candidates, credit = filter_segment_candidates(
            base_config=base_config,
            candidates=candidate_pool,
            dimensions=dimensions,
            remembered=remembered,
            domain=domain,
        )
        routing_credit[segment] = credit
        entry = segment_core._search_entry(
            catalog=catalog,
            current=current,
            segment=segment,
            discovery_events=discovery.get(segment, []),
            holdout_events=holdout.get(segment, []),
            full_events=full.get(segment, []),
            candidates=candidates,
        )
        entry["credit_routing"] = credit
        entries.append(entry)

    enriched = dict(result)
    enriched["segment_portfolio"] = {
        "available": True,
        "surface": "search",
        "routing": router.manifest("search"),
        "candidate_basis": "typed_local_neighborhood_with_durable_failure_credit",
        "candidate_count": len(candidate_pool),
        "trusted_segments": sum(1 for entry in entries if entry["trusted"]),
        "negative_credit_routed": True,
        "credit_routing": routing_credit,
        "entries": entries,
    }
    return enriched


def attach_recommend_portfolio(
    catalog: Catalog,
    current: RecommendationEngine,
    result: dict[str, Any],
    *,
    remembered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not catalog.reward_spec or not (result.get("business_validation") or {}).get("available"):
        return result
    discovery_events, holdout_events = temporal_request_split(catalog.events, surface="recommend")
    if not holdout_events:
        return result

    remembered = remembered or []
    router = SegmentRouter(catalog, SearchEngine(catalog), current)
    discovery = router.partition_events(discovery_events, surface="recommend")
    holdout = router.partition_events(holdout_events, surface="recommend")
    full = router.partition_events(catalog.events, surface="recommend")
    base_config = asdict(current.config)
    seed_config = result.get("candidate_config") if isinstance(result.get("candidate_config"), dict) else base_config
    dimensions, group_totals = core._evolution_schema(current.config)
    candidate_pool = segment_core._candidate_pool(base_config, seed_config, dimensions, group_totals)

    entries = []
    routing_credit: dict[str, Any] = {}
    for segment in sorted(full):
        domain = strategy_domain("recommend", segment)
        candidates, credit = filter_segment_candidates(
            base_config=base_config,
            candidates=candidate_pool,
            dimensions=dimensions,
            remembered=remembered,
            domain=domain,
        )
        routing_credit[segment] = credit
        entry = segment_core._recommend_entry(
            catalog=catalog,
            current=current,
            segment=segment,
            discovery_events=discovery.get(segment, []),
            holdout_events=holdout.get(segment, []),
            full_events=full.get(segment, []),
            candidates=candidates,
        )
        entry["credit_routing"] = credit
        entries.append(entry)

    enriched = dict(result)
    enriched["segment_portfolio"] = {
        "available": True,
        "surface": "recommend",
        "routing": router.manifest("recommend"),
        "candidate_basis": "typed_local_neighborhood_with_durable_failure_credit",
        "candidate_count": len(candidate_pool),
        "trusted_segments": sum(1 for entry in entries if entry["trusted"]),
        "negative_credit_routed": True,
        "credit_routing": routing_credit,
        "entries": entries,
    }
    return enriched


__all__ = ["attach_search_portfolio", "attach_recommend_portfolio"]
