from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from lingjing_harness.domain import Catalog
from lingjing_harness.production import (
    evaluate_logged_policy,
    paired_bootstrap_delta,
    request_groups,
    temporal_request_split,
)
from .capabilities import normalize_strategy_config
from .production_evolution import _business_confidence_supports_trust
from .recommend import RecommendConfig, RecommendationEngine
from .search import SearchConfig, SearchEngine
from .segments import SegmentRouter
from . import evolution_core as core


MIN_SEGMENT_DISCOVERY_REQUESTS = 3
MIN_SEGMENT_HOLDOUT_REQUESTS = 2


def _candidate_pool(
    base_config: dict[str, Any],
    seed_config: dict[str, Any],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
) -> list[dict[str, Any]]:
    """Build a typed local neighborhood around the globally discovered basin."""

    try:
        center = core._project(seed_config, dimensions, group_totals)
    except (TypeError, ValueError, KeyError):
        center = core._project(base_config, dimensions, group_totals)
    rows = [dict(base_config), dict(center)]
    for dimension in dimensions:
        try:
            rows.extend(
                candidate
                for _, _, candidate in core._neighbors(
                    center,
                    dimension,
                    dimensions,
                    group_totals,
                )
            )
        except (TypeError, ValueError, KeyError):
            continue
    return core._unique_configs(rows)


def _request_count(events: Iterable[Any], surface: str) -> int:
    return len(request_groups(events, surface=surface))


def _search_labels_for_events(catalog: Catalog, events: Iterable[Any]) -> list[Any]:
    queries = {
        row.query
        for row in events
        if getattr(row, "surface", "") == "search" and getattr(row, "query", "")
    }
    return [label for label in catalog.query_labels if label.query in queries]


def _recommend_users_for_events(current: RecommendationEngine, events: Iterable[Any]) -> list[str]:
    known = set(current.known_users())
    return sorted(
        {
            row.user_id
            for row in events
            if getattr(row, "surface", "") == "recommend"
            and getattr(row, "user_id", "") in known
        }
    )


def _search_entry(
    *,
    catalog: Catalog,
    current: SearchEngine,
    segment: str,
    discovery_events: list[Any],
    holdout_events: list[Any],
    full_events: list[Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    reward_spec = catalog.reward_spec
    assert reward_spec is not None
    discovery_requests = _request_count(discovery_events, "search")
    holdout_requests = _request_count(holdout_events, "search")
    labels = _search_labels_for_events(catalog, full_events)
    reference_discovery = evaluate_logged_policy(
        discovery_events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=current,
    )
    reference_holdout = evaluate_logged_policy(
        holdout_events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=current,
    )
    reference_full = evaluate_logged_policy(
        full_events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=current,
    )

    best_config = asdict(current.config)
    best_discovery = reference_discovery
    best_engine = current
    for raw in candidates:
        try:
            cfg = normalize_strategy_config(SearchConfig(**raw))
        except (TypeError, ValueError, KeyError):
            continue
        engine = current.with_config(cfg)
        replay = evaluate_logged_policy(
            discovery_events,
            surface="search",
            reward_spec=reward_spec,
            search_engine=engine,
        )
        if float(replay.get("reward", 0.0)) > float(best_discovery.get("reward", 0.0)) + 1e-12:
            best_config = asdict(cfg)
            best_discovery = replay
            best_engine = engine

    candidate_holdout = evaluate_logged_policy(
        holdout_events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=best_engine,
    )
    candidate_full = evaluate_logged_policy(
        full_events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=best_engine,
    )
    confidence = paired_bootstrap_delta(
        reference_holdout.get("request_scores", {}),
        candidate_holdout.get("request_scores", {}),
    )
    confidence_supports_trust = _business_confidence_supports_trust(confidence)
    discovery_delta = float(best_discovery.get("reward", 0.0)) - float(reference_discovery.get("reward", 0.0))
    holdout_delta = float(candidate_holdout.get("reward", 0.0)) - float(reference_holdout.get("reward", 0.0))
    full_delta = float(candidate_full.get("reward", 0.0)) - float(reference_full.get("reward", 0.0))

    guardrail_ready = len(labels) >= core.MIN_SEARCH_EVIDENCE
    quality_delta = 0.0
    recall_delta = 0.0
    robust = None
    safe = False
    if guardrail_ready:
        reference_guard = core._audit_search_config(catalog, current, labels, current.config)
        candidate_guard = core._audit_search_config(
            catalog,
            current,
            labels,
            normalize_strategy_config(SearchConfig(**best_config)),
        )
        robust = core._search_robustness(reference_guard, candidate_guard)
        quality_delta = float(candidate_guard.get("quality", 0.0)) - float(reference_guard.get("quality", 0.0))
        recall_delta = float(candidate_guard.get("recall", 0.0)) - float(reference_guard.get("recall", 0.0))
        safe = (
            quality_delta >= -0.01
            and recall_delta >= -0.03
            and robust["worse_share"] <= 0.40
            and robust["worst_delta"] >= -0.40
        )

    enough = (
        discovery_requests >= MIN_SEGMENT_DISCOVERY_REQUESTS
        and holdout_requests >= MIN_SEGMENT_HOLDOUT_REQUESTS
    )
    trusted = (
        enough
        and guardrail_ready
        and safe
        and discovery_delta > 0.001
        and holdout_delta >= -0.003
        and confidence_supports_trust
    )
    return {
        "segment": segment,
        "trusted": trusted,
        "safe_to_try": bool(enough and guardrail_ready and safe),
        "candidate_config": best_config,
        "discovery_requests": discovery_requests,
        "holdout_requests": holdout_requests,
        "full_requests": _request_count(full_events, "search"),
        "discovery_reward_delta": round(discovery_delta, 6),
        "holdout_reward_delta": round(holdout_delta, 6),
        "full_reward_delta": round(full_delta, 6),
        "candidate_reward": float(candidate_full.get("reward", 0.0)),
        "guardrail": {
            "available": guardrail_ready,
            "samples": len(labels),
            "quality_delta": round(quality_delta, 4),
            "recall_delta": round(recall_delta, 4),
            "robustness": robust,
        },
        "confidence": confidence,
        "trust_blocked_by": [
            *(["segment_discovery_requests<3"] if discovery_requests < MIN_SEGMENT_DISCOVERY_REQUESTS else []),
            *(["segment_holdout_requests<2"] if holdout_requests < MIN_SEGMENT_HOLDOUT_REQUESTS else []),
            *([] if guardrail_ready else ["segment_relevance_guardrail_unavailable"]),
            *([] if safe or not guardrail_ready else ["segment_guardrail_regression"]),
            *([] if discovery_delta > 0.001 else ["segment_discovery_reward_not_improved"]),
            *([] if holdout_delta >= -0.003 else ["segment_holdout_reward_regressed"]),
            *([] if confidence_supports_trust else ["segment_confidence_insufficient"]),
        ],
    }


def _recommend_entry(
    *,
    catalog: Catalog,
    current: RecommendationEngine,
    segment: str,
    discovery_events: list[Any],
    holdout_events: list[Any],
    full_events: list[Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    reward_spec = catalog.reward_spec
    assert reward_spec is not None
    discovery_requests = _request_count(discovery_events, "recommend")
    holdout_requests = _request_count(holdout_events, "recommend")
    users = _recommend_users_for_events(current, full_events)
    reference_discovery = evaluate_logged_policy(
        discovery_events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=current,
    )
    reference_holdout = evaluate_logged_policy(
        holdout_events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=current,
    )
    reference_full = evaluate_logged_policy(
        full_events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=current,
    )

    best_config = asdict(current.config)
    best_discovery = reference_discovery
    best_engine = current
    for raw in candidates:
        try:
            cfg = normalize_strategy_config(RecommendConfig(**raw))
        except (TypeError, ValueError, KeyError):
            continue
        engine = current.with_config(cfg)
        replay = evaluate_logged_policy(
            discovery_events,
            surface="recommend",
            reward_spec=reward_spec,
            recommend_engine=engine,
        )
        if float(replay.get("reward", 0.0)) > float(best_discovery.get("reward", 0.0)) + 1e-12:
            best_config = asdict(cfg)
            best_discovery = replay
            best_engine = engine

    candidate_holdout = evaluate_logged_policy(
        holdout_events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=best_engine,
    )
    candidate_full = evaluate_logged_policy(
        full_events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=best_engine,
    )
    confidence = paired_bootstrap_delta(
        reference_holdout.get("request_scores", {}),
        candidate_holdout.get("request_scores", {}),
    )
    confidence_supports_trust = _business_confidence_supports_trust(confidence)
    discovery_delta = float(best_discovery.get("reward", 0.0)) - float(reference_discovery.get("reward", 0.0))
    holdout_delta = float(candidate_holdout.get("reward", 0.0)) - float(reference_holdout.get("reward", 0.0))
    full_delta = float(candidate_full.get("reward", 0.0)) - float(reference_full.get("reward", 0.0))

    cfg = normalize_strategy_config(RecommendConfig(**best_config))
    slice_key = "segment-" + segment.replace("/", "-")
    reference_guard = core._audit_recommend_config(catalog, current, users, current.config, slice_key=slice_key)
    candidate_guard = core._audit_recommend_config(catalog, current, users, cfg, slice_key=slice_key)
    cold_delta = float(candidate_guard.get("cold_start_quality", 0.0)) - float(reference_guard.get("cold_start_quality", 0.0))
    q_delta = float(candidate_guard.get("quality", 0.0)) - float(reference_guard.get("quality", 0.0))
    cov_delta = float(candidate_guard.get("coverage", 0.0)) - float(reference_guard.get("coverage", 0.0))
    fresh_delta = float(candidate_guard.get("freshness", 0.0)) - float(reference_guard.get("freshness", 0.0))
    div_delta = float(candidate_guard.get("diversity", 0.0)) - float(reference_guard.get("diversity", 0.0))
    robust = core._recommend_robustness(reference_guard, candidate_guard) if users else None

    cold_segment = segment == "recommend/cold-start"
    if cold_segment:
        guardrail_ready = int(candidate_guard.get("cold_start_samples", 0)) >= 2
        safe = guardrail_ready and cold_delta >= -0.02
    else:
        guardrail_ready = len(users) >= core.MIN_RECOMMEND_EVIDENCE
        safe = bool(
            guardrail_ready
            and q_delta >= -0.006
            and cov_delta >= -0.04
            and fresh_delta >= -0.02
            and cold_delta >= -0.03
            and robust
            and robust["worse_share"] <= 0.45
            and robust["worst_delta"] >= -0.35
        )

    enough = (
        discovery_requests >= MIN_SEGMENT_DISCOVERY_REQUESTS
        and holdout_requests >= MIN_SEGMENT_HOLDOUT_REQUESTS
    )
    trusted = (
        enough
        and guardrail_ready
        and safe
        and discovery_delta > 0.001
        and holdout_delta >= -0.003
        and confidence_supports_trust
    )
    return {
        "segment": segment,
        "trusted": trusted,
        "safe_to_try": bool(enough and guardrail_ready and safe),
        "candidate_config": best_config,
        "discovery_requests": discovery_requests,
        "holdout_requests": holdout_requests,
        "full_requests": _request_count(full_events, "recommend"),
        "discovery_reward_delta": round(discovery_delta, 6),
        "holdout_reward_delta": round(holdout_delta, 6),
        "full_reward_delta": round(full_delta, 6),
        "candidate_reward": float(candidate_full.get("reward", 0.0)),
        "guardrail": {
            "available": guardrail_ready,
            "users": len(users),
            "quality_delta": round(q_delta, 4),
            "coverage_delta": round(cov_delta, 4),
            "freshness_delta": round(fresh_delta, 4),
            "diversity_delta": round(div_delta, 4),
            "cold_start_quality_delta": round(cold_delta, 4),
            "robustness": robust,
        },
        "confidence": confidence,
        "trust_blocked_by": [
            *(["segment_discovery_requests<3"] if discovery_requests < MIN_SEGMENT_DISCOVERY_REQUESTS else []),
            *(["segment_holdout_requests<2"] if holdout_requests < MIN_SEGMENT_HOLDOUT_REQUESTS else []),
            *([] if guardrail_ready else ["segment_domain_guardrail_unavailable"]),
            *([] if safe or not guardrail_ready else ["segment_guardrail_regression"]),
            *([] if discovery_delta > 0.001 else ["segment_discovery_reward_not_improved"]),
            *([] if holdout_delta >= -0.003 else ["segment_holdout_reward_regressed"]),
            *([] if confidence_supports_trust else ["segment_confidence_insufficient"]),
        ],
    }


def attach_search_portfolio(
    catalog: Catalog,
    current: SearchEngine,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not catalog.reward_spec or not (result.get("business_validation") or {}).get("available"):
        return result
    discovery_events, holdout_events = temporal_request_split(catalog.events, surface="search")
    if not holdout_events:
        return result
    router = SegmentRouter(catalog, current, RecommendationEngine(catalog))
    discovery = router.partition_events(discovery_events, surface="search")
    holdout = router.partition_events(holdout_events, surface="search")
    full = router.partition_events(catalog.events, surface="search")
    base_config = asdict(current.config)
    seed_config = result.get("candidate_config") if isinstance(result.get("candidate_config"), dict) else base_config
    dimensions, group_totals = core._evolution_schema(current.config)
    candidates = _candidate_pool(base_config, seed_config, dimensions, group_totals)
    entries = []
    for segment in sorted(full):
        entries.append(
            _search_entry(
                catalog=catalog,
                current=current,
                segment=segment,
                discovery_events=discovery.get(segment, []),
                holdout_events=holdout.get(segment, []),
                full_events=full.get(segment, []),
                candidates=candidates,
            )
        )
    enriched = dict(result)
    enriched["segment_portfolio"] = {
        "available": True,
        "surface": "search",
        "routing": router.manifest("search"),
        "candidate_basis": "typed_local_neighborhood_of_global_basin",
        "candidate_count": len(candidates),
        "trusted_segments": sum(1 for entry in entries if entry["trusted"]),
        "entries": entries,
    }
    return enriched


def attach_recommend_portfolio(
    catalog: Catalog,
    current: RecommendationEngine,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not catalog.reward_spec or not (result.get("business_validation") or {}).get("available"):
        return result
    discovery_events, holdout_events = temporal_request_split(catalog.events, surface="recommend")
    if not holdout_events:
        return result
    router = SegmentRouter(catalog, SearchEngine(catalog), current)
    discovery = router.partition_events(discovery_events, surface="recommend")
    holdout = router.partition_events(holdout_events, surface="recommend")
    full = router.partition_events(catalog.events, surface="recommend")
    base_config = asdict(current.config)
    seed_config = result.get("candidate_config") if isinstance(result.get("candidate_config"), dict) else base_config
    dimensions, group_totals = core._evolution_schema(current.config)
    candidates = _candidate_pool(base_config, seed_config, dimensions, group_totals)
    entries = []
    for segment in sorted(full):
        entries.append(
            _recommend_entry(
                catalog=catalog,
                current=current,
                segment=segment,
                discovery_events=discovery.get(segment, []),
                holdout_events=holdout.get(segment, []),
                full_events=full.get(segment, []),
                candidates=candidates,
            )
        )
    enriched = dict(result)
    enriched["segment_portfolio"] = {
        "available": True,
        "surface": "recommend",
        "routing": router.manifest("recommend"),
        "candidate_basis": "typed_local_neighborhood_of_global_basin",
        "candidate_count": len(candidates),
        "trusted_segments": sum(1 for entry in entries if entry["trusted"]),
        "entries": entries,
    }
    return enriched


__all__ = [
    "MIN_SEGMENT_DISCOVERY_REQUESTS",
    "MIN_SEGMENT_HOLDOUT_REQUESTS",
    "attach_search_portfolio",
    "attach_recommend_portfolio",
]
