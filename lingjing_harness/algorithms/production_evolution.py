from __future__ import annotations

from dataclasses import asdict
from random import Random
from typing import Any

from lingjing_harness.domain import Catalog
from lingjing_harness.production import (
    evaluate_logged_policy,
    paired_bootstrap_delta,
    request_groups,
    temporal_request_split,
)
from .capabilities import normalize_strategy_config
from .recommend import RecommendConfig, RecommendationEngine
from .search import SearchConfig, SearchEngine
from . import evolution_core as core


MIN_BUSINESS_REQUESTS = 4


def _business_events_ready(catalog: Catalog, surface: str) -> bool:
    if catalog.reward_spec is None:
        return False
    return len(request_groups(catalog.events, surface=surface)) >= MIN_BUSINESS_REQUESTS


def _annotate_proxy(result: dict[str, Any], surface: str) -> dict[str, Any]:
    result = dict(result)
    result["evaluation_basis"] = "proxy_metrics"
    result["business_trusted"] = False
    result["business_validation"] = {
        "available": False,
        "surface": surface,
        "reason": "production_reward_evidence_unavailable",
        "requests": len(request_groups(result.get("_catalog_events", []), surface=surface))
        if result.get("_catalog_events")
        else 0,
    }
    result.pop("_catalog_events", None)
    return result


def _attach_business(report: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    return {
        **report,
        "business_reward": float(replay.get("reward", 0.0)),
        "business_reward_coverage": float(replay.get("reward_coverage", 0.0)),
        "business_requests": int(replay.get("requests", 0)),
        "business_estimator": replay.get("estimator"),
    }


def _search_business_objective(report: dict[str, Any], robust: dict[str, float]) -> float:
    # Business reward is the primary signal. Relevance and robustness remain a
    # bounded safety/tie-break component rather than pretending to be revenue.
    return 0.82 * float(report.get("business_reward", 0.0)) + 0.18 * core._search_objective(report, robust)


def _recommend_business_objective(report: dict[str, Any], robust: dict[str, float]) -> float:
    return 0.82 * float(report.get("business_reward", 0.0)) + 0.18 * core._recommend_objective(report, robust)


def evolve_search(
    catalog: Catalog,
    current: SearchEngine,
    *,
    remembered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not _business_events_ready(catalog, "search"):
        return _annotate_proxy(core.evolve_search(catalog, current, remembered=remembered), "search")

    remembered = remembered or []
    reward_spec = catalog.reward_spec
    assert reward_spec is not None
    labels = core._stable_limit(list(catalog.query_labels), lambda row: row.query)
    discovery_labels, holdout_labels = core._stable_split(labels, lambda row: row.query)
    if len(labels) < core.MIN_SEARCH_EVIDENCE:
        result = core.evolve_search(catalog, current, remembered=remembered)
        result = _annotate_proxy(result, "search")
        result["business_validation"] = {
            "available": True,
            "surface": "search",
            "reason": "relevance_guardrail_evidence_insufficient",
            "requests": len(request_groups(catalog.events, surface="search")),
        }
        return result

    discovery_events, holdout_events = temporal_request_split(catalog.events, surface="search")
    if not holdout_events:
        return _annotate_proxy(core.evolve_search(catalog, current, remembered=remembered), "search")

    base_config = asdict(current.config)
    dimensions, group_totals = core._evolution_schema(current.config)
    reference = core._audit_search_config(catalog, current, labels, current.config)
    reference_discovery = core._audit_search_config(catalog, current, discovery_labels, current.config)
    reference_holdout = core._audit_search_config(catalog, current, holdout_labels, current.config) if holdout_labels else None
    reference_business = evaluate_logged_policy(
        discovery_events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=current,
    )
    reference_discovery = _attach_business(reference_discovery, reference_business)

    def evaluate(config: dict[str, Any]):
        cfg = normalize_strategy_config(SearchConfig(**config))
        engine = current.with_config(cfg)
        report = core._audit_search_config(catalog, current, discovery_labels, cfg)
        replay = evaluate_logged_policy(
            discovery_events,
            surface="search",
            reward_spec=reward_spec,
            search_engine=engine,
        )
        report = _attach_business(report, replay)
        robust = core._search_robustness(reference_discovery, report)
        return report, robust, _search_business_objective(report, robust)

    rng = Random(core._seed(catalog, "search-business"))
    base_objective = _search_business_objective(reference_discovery, {"worse_share": 0.0, "worst_delta": 0.0})
    surface, cache = core._response_surface(
        base_config=base_config,
        dimensions=dimensions,
        group_totals=group_totals,
        remembered=remembered,
        evaluate=evaluate,
        base_objective=base_objective,
        rng=rng,
    )
    population, basin_jump = core._surface_seeds(
        base_config=base_config,
        surface=surface,
        remembered=remembered,
        dimensions=dimensions,
        group_totals=group_totals,
        rng=rng,
    )
    rows, archive = core._evolution_loop(
        base_config=base_config,
        population=population,
        dimensions=dimensions,
        group_totals=group_totals,
        evaluate=evaluate,
        rng=rng,
        cache=cache,
    )
    if not rows:
        return _annotate_proxy(core._not_ready_search(reference, base_config, dimensions), "search")

    best = rows[0]
    candidate_config = normalize_strategy_config(SearchConfig(**best["config"]))
    best["config"] = asdict(candidate_config)
    candidate_engine = current.with_config(candidate_config)
    trial = core._audit_search_config(catalog, current, labels, candidate_config)
    robust = core._search_robustness(reference, trial)
    holdout = core._audit_search_config(catalog, current, holdout_labels, candidate_config) if holdout_labels else None
    holdout_robust = core._search_robustness(reference_holdout, holdout) if holdout and reference_holdout else None

    full_reference_business = evaluate_logged_policy(
        catalog.events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=current,
    )
    full_candidate_business = evaluate_logged_policy(
        catalog.events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=candidate_engine,
    )
    holdout_reference_business = evaluate_logged_policy(
        holdout_events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=current,
    )
    holdout_candidate_business = evaluate_logged_policy(
        holdout_events,
        surface="search",
        reward_spec=reward_spec,
        search_engine=candidate_engine,
    )
    confidence = paired_bootstrap_delta(
        holdout_reference_business["request_scores"],
        holdout_candidate_business["request_scores"],
    )

    quality_delta = float(trial.get("quality", 0.0)) - float(reference.get("quality", 0.0))
    recall_delta = float(trial.get("recall", 0.0)) - float(reference.get("recall", 0.0))
    holdout_quality_delta = (
        float(holdout.get("quality", 0.0)) - float(reference_holdout.get("quality", 0.0))
        if holdout and reference_holdout else 0.0
    )
    holdout_recall_delta = (
        float(holdout.get("recall", 0.0)) - float(reference_holdout.get("recall", 0.0))
        if holdout and reference_holdout else 0.0
    )
    business_delta = float(full_candidate_business["reward"]) - float(full_reference_business["reward"])
    discovery_business_delta = float(best["report"].get("business_reward", 0.0)) - float(reference_business["reward"])
    holdout_business_delta = float(holdout_candidate_business["reward"]) - float(holdout_reference_business["reward"])
    discovery_delta = float(best["objective"]) - base_objective

    safe = (
        trial.get("queries", 0) >= core.MIN_SEARCH_EVIDENCE
        and quality_delta >= -0.01
        and recall_delta >= -0.03
        and robust["worse_share"] <= 0.40
        and robust["worst_delta"] >= -0.40
        and holdout_quality_delta >= -0.015
        and holdout_recall_delta >= -0.04
        and holdout_business_delta >= -0.02
        and (not holdout_robust or holdout_robust["worst_delta"] >= -0.50)
    )
    trusted = (
        safe
        and business_delta > 0.0
        and discovery_business_delta > 0.001
        and holdout_business_delta >= -0.003
        and float(confidence["probability_positive"]) >= 0.65
    )
    trial = _attach_business(trial, full_candidate_business)
    reference = _attach_business(reference, full_reference_business)
    metadata = core._evolution_metadata(
        dimensions=dimensions,
        surface=surface,
        best=best,
        base_config=base_config,
        archive=archive,
        basin_jump=basin_jump,
        remembered=remembered,
    )
    metadata.update({"business_reward_routed": True, "temporal_holdout": True})
    return {
        "reference": reference,
        "candidate": trial,
        "delta": {
            "quality": round(quality_delta, 4),
            "recall": round(recall_delta, 4),
            "business_reward": round(business_delta, 6),
        },
        "evaluation_ready": True,
        "evaluation_basis": "business_reward+relevance_guardrails",
        "safe_to_try": safe,
        "trusted": trusted,
        "business_trusted": trusted,
        "candidate_config": best["config"],
        "candidate_count": len(rows),
        "generations": core.MAX_GENERATIONS,
        "robustness": robust,
        "objective_delta": round(discovery_delta, 5),
        "business_validation": {
            "available": True,
            "surface": "search",
            "temporal": True,
            "discovery_requests": len(request_groups(discovery_events, surface="search")),
            "holdout_requests": len(request_groups(holdout_events, surface="search")),
            "discovery_reward_delta": round(discovery_business_delta, 6),
            "holdout_reward_delta": round(holdout_business_delta, 6),
            "full_reward_delta": round(business_delta, 6),
            "confidence": confidence,
            "estimator": holdout_candidate_business["estimator"],
        },
        "validation": {
            "discovery": {"samples": len(discovery_labels), "objective_delta": round(discovery_delta, 5)},
            "holdout": {
                "samples": len(holdout_labels),
                "independent": bool(holdout_labels),
                "quality_delta": round(holdout_quality_delta, 4),
                "recall_delta": round(holdout_recall_delta, 4),
                "robustness": holdout_robust,
            },
        },
        "top_candidates": [
            {
                "objective": row["objective"],
                "business_reward": row["report"].get("business_reward", 0.0),
                "quality": row["report"].get("quality", 0.0),
                "recall": row["report"].get("recall", 0.0),
                "signature": list(core._config_signature(base_config, row["config"], dimensions)),
            }
            for row in rows[:3]
        ],
        "evolution": metadata,
    }


def evolve_recommend(
    catalog: Catalog,
    current: RecommendationEngine,
    *,
    remembered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not _business_events_ready(catalog, "recommend"):
        return _annotate_proxy(core.evolve_recommend(catalog, current, remembered=remembered), "recommend")

    remembered = remembered or []
    reward_spec = catalog.reward_spec
    assert reward_spec is not None
    users = core._stable_limit(current.known_users(), lambda user: user)
    discovery_users, holdout_users = core._stable_split(users, lambda user: user)
    if len(users) < core.MIN_RECOMMEND_EVIDENCE:
        result = core.evolve_recommend(catalog, current, remembered=remembered)
        result = _annotate_proxy(result, "recommend")
        result["business_validation"] = {
            "available": True,
            "surface": "recommend",
            "reason": "warm_user_guardrail_evidence_insufficient",
            "requests": len(request_groups(catalog.events, surface="recommend")),
        }
        return result

    discovery_events, holdout_events = temporal_request_split(catalog.events, surface="recommend")
    if not holdout_events:
        return _annotate_proxy(core.evolve_recommend(catalog, current, remembered=remembered), "recommend")

    base_config = asdict(current.config)
    dimensions, group_totals = core._evolution_schema(current.config)
    reference = core._audit_recommend_config(catalog, current, users, current.config, slice_key="full")
    reference_discovery = core._audit_recommend_config(
        catalog, current, discovery_users, current.config, slice_key="discovery"
    )
    reference_holdout = (
        core._audit_recommend_config(catalog, current, holdout_users, current.config, slice_key="holdout")
        if holdout_users else None
    )
    reference_business = evaluate_logged_policy(
        discovery_events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=current,
    )
    reference_discovery = _attach_business(reference_discovery, reference_business)

    def evaluate(config: dict[str, Any]):
        cfg = normalize_strategy_config(RecommendConfig(**config))
        engine = current.with_config(cfg)
        report = core._audit_recommend_config(
            catalog, current, discovery_users, cfg, slice_key="discovery"
        )
        replay = evaluate_logged_policy(
            discovery_events,
            surface="recommend",
            reward_spec=reward_spec,
            recommend_engine=engine,
        )
        report = _attach_business(report, replay)
        robust = core._recommend_robustness(reference_discovery, report)
        return report, robust, _recommend_business_objective(report, robust)

    rng = Random(core._seed(catalog, "recommend-business"))
    base_objective = _recommend_business_objective(reference_discovery, {"worse_share": 0.0, "worst_delta": 0.0})
    surface, cache = core._response_surface(
        base_config=base_config,
        dimensions=dimensions,
        group_totals=group_totals,
        remembered=remembered,
        evaluate=evaluate,
        base_objective=base_objective,
        rng=rng,
    )
    population, basin_jump = core._surface_seeds(
        base_config=base_config,
        surface=surface,
        remembered=remembered,
        dimensions=dimensions,
        group_totals=group_totals,
        rng=rng,
    )
    rows, archive = core._evolution_loop(
        base_config=base_config,
        population=population,
        dimensions=dimensions,
        group_totals=group_totals,
        evaluate=evaluate,
        rng=rng,
        cache=cache,
    )
    if not rows:
        return _annotate_proxy(core._not_ready_recommend(reference, base_config, dimensions), "recommend")

    best = rows[0]
    candidate_config = normalize_strategy_config(RecommendConfig(**best["config"]))
    best["config"] = asdict(candidate_config)
    candidate_engine = current.with_config(candidate_config)
    trial = core._audit_recommend_config(catalog, current, users, candidate_config, slice_key="full")
    robust = core._recommend_robustness(reference, trial)
    holdout = (
        core._audit_recommend_config(catalog, current, holdout_users, candidate_config, slice_key="holdout")
        if holdout_users else None
    )
    holdout_robust = (
        core._recommend_robustness(reference_holdout, holdout)
        if holdout and reference_holdout else None
    )

    full_reference_business = evaluate_logged_policy(
        catalog.events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=current,
    )
    full_candidate_business = evaluate_logged_policy(
        catalog.events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=candidate_engine,
    )
    holdout_reference_business = evaluate_logged_policy(
        holdout_events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=current,
    )
    holdout_candidate_business = evaluate_logged_policy(
        holdout_events,
        surface="recommend",
        reward_spec=reward_spec,
        recommend_engine=candidate_engine,
    )
    confidence = paired_bootstrap_delta(
        holdout_reference_business["request_scores"],
        holdout_candidate_business["request_scores"],
    )

    q_delta = float(trial.get("quality", 0.0)) - float(reference.get("quality", 0.0))
    fresh_delta = float(trial.get("freshness", 0.0)) - float(reference.get("freshness", 0.0))
    cov_delta = float(trial.get("coverage", 0.0)) - float(reference.get("coverage", 0.0))
    div_delta = float(trial.get("diversity", 0.0)) - float(reference.get("diversity", 0.0))
    cold_delta = float(trial.get("cold_start_quality", 0.0)) - float(reference.get("cold_start_quality", 0.0))
    holdout_q_delta = (
        float(holdout.get("quality", 0.0)) - float(reference_holdout.get("quality", 0.0))
        if holdout and reference_holdout else 0.0
    )
    holdout_cov_delta = (
        float(holdout.get("coverage", 0.0)) - float(reference_holdout.get("coverage", 0.0))
        if holdout and reference_holdout else 0.0
    )
    holdout_cold_delta = (
        float(holdout.get("cold_start_quality", 0.0)) - float(reference_holdout.get("cold_start_quality", 0.0))
        if holdout and reference_holdout else 0.0
    )
    business_delta = float(full_candidate_business["reward"]) - float(full_reference_business["reward"])
    discovery_business_delta = float(best["report"].get("business_reward", 0.0)) - float(reference_business["reward"])
    holdout_business_delta = float(holdout_candidate_business["reward"]) - float(holdout_reference_business["reward"])
    discovery_delta = float(best["objective"]) - base_objective

    proxy_safe, _ = core._recommend_gates(
        users=int(trial.get("users", 0)),
        q_delta=q_delta,
        cov_delta=cov_delta,
        fresh_delta=fresh_delta,
        div_delta=div_delta,
        cold_delta=cold_delta,
        discovery_delta=discovery_delta,
        robust=robust,
        holdout_available=bool(holdout_users),
        holdout_q_delta=holdout_q_delta,
        holdout_cov_delta=holdout_cov_delta,
        holdout_cold_delta=holdout_cold_delta,
        holdout_robust=holdout_robust,
    )
    safe = proxy_safe and holdout_business_delta >= -0.02
    trusted = (
        safe
        and business_delta > 0.0
        and discovery_business_delta > 0.001
        and holdout_business_delta >= -0.003
        and float(confidence["probability_positive"]) >= 0.65
    )
    trial = _attach_business(trial, full_candidate_business)
    reference = _attach_business(reference, full_reference_business)
    metadata = core._evolution_metadata(
        dimensions=dimensions,
        surface=surface,
        best=best,
        base_config=base_config,
        archive=archive,
        basin_jump=basin_jump,
        remembered=remembered,
    )
    metadata.update({"business_reward_routed": True, "temporal_holdout": True})
    return {
        "reference": reference,
        "candidate": trial,
        "delta": {
            "quality": round(q_delta, 4),
            "freshness": round(fresh_delta, 4),
            "coverage": round(cov_delta, 4),
            "diversity": round(div_delta, 4),
            "cold_start_quality": round(cold_delta, 4),
            "business_reward": round(business_delta, 6),
        },
        "evaluation_ready": True,
        "evaluation_basis": "business_reward+recommendation_guardrails",
        "safe_to_try": safe,
        "trusted": trusted,
        "business_trusted": trusted,
        "candidate_config": best["config"],
        "candidate_count": len(rows),
        "generations": core.MAX_GENERATIONS,
        "robustness": robust,
        "objective_delta": round(discovery_delta, 5),
        "business_validation": {
            "available": True,
            "surface": "recommend",
            "temporal": True,
            "discovery_requests": len(request_groups(discovery_events, surface="recommend")),
            "holdout_requests": len(request_groups(holdout_events, surface="recommend")),
            "discovery_reward_delta": round(discovery_business_delta, 6),
            "holdout_reward_delta": round(holdout_business_delta, 6),
            "full_reward_delta": round(business_delta, 6),
            "confidence": confidence,
            "estimator": holdout_candidate_business["estimator"],
        },
        "validation": {
            "discovery": {
                "samples": len(discovery_users),
                "objective_delta": round(discovery_delta, 5),
                "cold_start_samples": int(reference_discovery.get("cold_start_samples", 0)),
            },
            "holdout": {
                "samples": len(holdout_users),
                "independent": bool(holdout_users),
                "quality_delta": round(holdout_q_delta, 4),
                "coverage_delta": round(holdout_cov_delta, 4),
                "cold_start_quality_delta": round(holdout_cold_delta, 4),
                "cold_start_samples": int((holdout or {}).get("cold_start_samples", 0)),
                "robustness": holdout_robust,
            },
        },
        "top_candidates": [
            {
                "objective": row["objective"],
                "business_reward": row["report"].get("business_reward", 0.0),
                "quality": row["report"].get("quality", 0.0),
                "coverage": row["report"].get("coverage", 0.0),
                "freshness": row["report"].get("freshness", 0.0),
                "cold_start_quality": row["report"].get("cold_start_quality", 0.0),
                "signature": list(core._config_signature(base_config, row["config"], dimensions)),
            }
            for row in rows[:3]
        ],
        "evolution": metadata,
    }


__all__ = ["evolve_search", "evolve_recommend"]
