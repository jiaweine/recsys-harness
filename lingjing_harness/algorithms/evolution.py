from __future__ import annotations

from dataclasses import asdict
from hashlib import blake2b
from random import Random
from statistics import mean
from typing import Any, Callable, Iterable, TypeVar

from lingjing_harness.domain import Catalog
from .evaluation import audit_recommend, audit_search, ndcg_at_k, recall_at_k, reciprocal_rank
from .recommend import RecommendConfig, RecommendationEngine
from .search import SearchConfig, SearchEngine

MIN_SEARCH_EVIDENCE = 3
MIN_RECOMMEND_EVIDENCE = 3
MAX_GENERATIONS = 2
POPULATION_SIZE = 7
MAX_EVOLUTION_SAMPLES = 36
T = TypeVar("T")


def _seed(catalog: Catalog, domain: str) -> int:
    raw = f"{domain}|{catalog.name}|{len(catalog.items)}|{len(catalog.interactions)}|{len(catalog.query_labels)}"
    for item in catalog.items[:32]:
        raw += f"|{item.item_id}:{item.title}"
    return int.from_bytes(blake2b(raw.encode("utf-8"), digest_size=8).digest(), "little")


def _stable_split(rows: list[T], key: Callable[[T], str]) -> tuple[list[T], list[T]]:
    if len(rows) < 4:
        return rows, []
    ranked = sorted(rows, key=lambda row: blake2b(key(row).encode("utf-8"), digest_size=8).digest())
    holdout_size = max(1, min(len(ranked) // 3, len(ranked) - 2))
    return ranked[holdout_size:], ranked[:holdout_size]




def _stable_limit(rows: list[T], key: Callable[[T], str], limit: int = MAX_EVOLUTION_SAMPLES) -> list[T]:
    if len(rows) <= limit:
        return list(rows)
    return sorted(rows, key=lambda row: blake2b(key(row).encode("utf-8"), digest_size=8).digest())[:limit]


def _audit_search_cached(catalog: Catalog, engine: SearchEngine, labels, prepared: dict[str, list[dict]], config: SearchConfig) -> dict[str, Any]:
    rows = []
    for label in labels:
        ranked = [row["id"] for row in engine.rank_prepared(prepared[label.query], config=config, limit=10)]
        relevant = set(label.relevant)
        rows.append({
            "query": label.query,
            "recall": recall_at_k(ranked, relevant),
            "mrr": reciprocal_rank(ranked, relevant),
            "ndcg": ndcg_at_k(ranked, relevant),
            "top": ranked[:3],
        })
    if not rows:
        return {"queries": 0, "available_queries": 0, "sampled": False, "quality": 0.0, "recall": 0.0, "mrr": 0.0, "details": []}
    return {
        "queries": len(rows),
        "available_queries": len(labels),
        "sampled": False,
        "quality": round(mean(row["ndcg"] for row in rows), 4),
        "recall": round(mean(row["recall"] for row in rows), 4),
        "mrr": round(mean(row["mrr"] for row in rows), 4),
        "details": rows,
    }


def _audit_recommend_cached(catalog: Catalog, engine: RecommendationEngine, users: list[str], prepared: dict[str, list[dict]], config: RecommendConfig) -> dict[str, Any]:
    if not users:
        return {"users": 0, "available_users": 0, "sampled": False, "quality": 0.0, "coverage": 0.0, "diversity": 0.0, "freshness": 0.0, "novelty": 0.0, "details": []}
    exposed=set(); diversities=[]; freshness=[]; novelty=[]; details=[]
    for user in users:
        result = engine.rank_prepared(prepared[user], config=config, limit=8)
        exposed.update(row["id"] for row in result)
        cats=[category for row in result for category in row.get("categories", [])]
        diversity=len(set(cats))/max(1,len(cats))
        fresh=mean([row["freshness"] for row in result]) if result else 0.0
        nov=mean([row["signals"]["novelty"] for row in result]) if result else 0.0
        diversities.append(diversity); freshness.append(fresh); novelty.append(nov)
        details.append({"user_id":user,"diversity":round(diversity,4),"freshness":round(fresh,4),"top":[row["id"] for row in result[:4]]})
    eligible_count=sum(1 for item in catalog.items if item.eligible)
    coverage=len(exposed)/max(1,eligible_count)
    quality=.45*coverage+.25*mean(diversities)+.20*mean(freshness)+.10*mean(novelty)
    return {
        "users":len(users),"available_users":len(users),"sampled":False,
        "quality":round(quality,4),"coverage":round(coverage,4),"diversity":round(mean(diversities),4),
        "freshness":round(mean(freshness),4),"novelty":round(mean(novelty),4),"details":details,
    }


def _normalize(values: dict[str, float], keys: tuple[str, ...]) -> dict[str, float]:
    clipped = {key: max(0.005, min(0.75, float(values[key]))) for key in keys}
    total = sum(clipped.values()) or 1.0
    return {**values, **{key: clipped[key] / total for key in keys}}


def _mutate_config(
    base: dict[str, float],
    *,
    keys: tuple[str, ...],
    diversity_key: str,
    rng: Random,
    scale: float,
) -> dict[str, float]:
    row = dict(base)
    for key in keys:
        row[key] = row[key] * (1.0 + rng.uniform(-scale, scale))
    row = _normalize(row, keys)
    row[diversity_key] = max(0.0, min(0.32, float(base[diversity_key]) + rng.uniform(-0.06, 0.06)))
    return row


def _unique_configs(rows: Iterable[dict[str, float]]) -> list[dict[str, float]]:
    seen: set[tuple[tuple[str, float], ...]] = set()
    out = []
    for row in rows:
        try:
            rounded = {key: round(float(value), 6) for key, value in row.items()}
        except (TypeError, ValueError):
            continue
        sig = tuple(sorted(rounded.items()))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(rounded)
    return out


def _search_robustness(reference: dict[str, Any], trial: dict[str, Any]) -> dict[str, float]:
    base = {row["query"]: row for row in reference.get("details", [])}
    deltas = []
    for row in trial.get("details", []):
        if row["query"] in base:
            deltas.append(float(row["ndcg"]) - float(base[row["query"]]["ndcg"]))
    if not deltas:
        return {"worse_share": 1.0, "worst_delta": -1.0, "mean_delta": 0.0}
    return {
        "worse_share": round(sum(1 for value in deltas if value < -0.02) / len(deltas), 4),
        "worst_delta": round(min(deltas), 4),
        "mean_delta": round(mean(deltas), 4),
    }


def _recommend_robustness(reference: dict[str, Any], trial: dict[str, Any]) -> dict[str, float]:
    base = {row["user_id"]: row for row in reference.get("details", [])}
    deltas = []
    for row in trial.get("details", []):
        previous = base.get(row["user_id"])
        if previous:
            utility = 0.55 * float(row["diversity"]) + 0.45 * float(row["freshness"])
            old = 0.55 * float(previous["diversity"]) + 0.45 * float(previous["freshness"])
            deltas.append(utility - old)
    if not deltas:
        return {"worse_share": 1.0, "worst_delta": -1.0, "mean_delta": 0.0}
    return {
        "worse_share": round(sum(1 for value in deltas if value < -0.03) / len(deltas), 4),
        "worst_delta": round(min(deltas), 4),
        "mean_delta": round(mean(deltas), 4),
    }


def _search_objective(report: dict[str, Any], robust: dict[str, float] | None = None) -> float:
    robust = robust or {"worse_share": 0.0, "worst_delta": 0.0}
    return (
        float(report.get("quality", 0.0))
        + 0.08 * float(report.get("recall", 0.0))
        - 0.035 * float(robust.get("worse_share", 0.0))
        + 0.015 * min(0.0, float(robust.get("worst_delta", 0.0)))
    )


def _recommend_objective(report: dict[str, Any], robust: dict[str, float] | None = None) -> float:
    robust = robust or {"worse_share": 0.0, "worst_delta": 0.0}
    return (
        float(report.get("quality", 0.0))
        + 0.05 * float(report.get("freshness", 0.0))
        + 0.03 * float(report.get("diversity", 0.0))
        - 0.03 * float(robust.get("worse_share", 0.0))
        + 0.01 * min(0.0, float(robust.get("worst_delta", 0.0)))
    )


def _search_targeted(base: dict[str, float]) -> list[dict[str, float]]:
    keys = ("lexical", "semantic", "title", "quality", "popularity", "freshness")
    rows = []
    deltas = (
        {"lexical": 0.06, "semantic": -0.035, "title": 0.025},
        {"title": 0.07, "lexical": -0.035, "semantic": -0.035},
        {"semantic": 0.055, "lexical": -0.035, "title": -0.02},
        {"freshness": 0.045, "popularity": -0.025, "quality": 0.015},
    )
    for delta in deltas:
        row = dict(base)
        for key, value in delta.items():
            row[key] = max(0.005, row[key] + value)
        row = _normalize(row, keys)
        if "freshness" in delta:
            row["diversity"] = min(0.30, row["diversity"] + 0.025)
        rows.append(row)
    return rows


def _recommend_targeted(base: dict[str, float]) -> list[dict[str, float]]:
    keys = ("profile", "graph", "category", "quality", "freshness", "popularity", "novelty", "exploration")
    rows = []
    deltas = (
        {"profile": 0.045, "graph": 0.02, "popularity": -0.025},
        {"freshness": 0.055, "novelty": 0.025, "popularity": -0.035},
        {"quality": 0.035, "category": 0.02, "exploration": -0.015},
        {"exploration": 0.035, "novelty": 0.025, "profile": -0.025},
    )
    for delta in deltas:
        row = dict(base)
        for key, value in delta.items():
            row[key] = max(0.005, row[key] + value)
        row = _normalize(row, keys)
        if "freshness" in delta or "novelty" in delta:
            row["diversity"] = min(0.30, row["diversity"] + 0.025)
        rows.append(row)
    return rows


def _evolution_loop(
    *,
    base_config: dict[str, float],
    remembered: list[dict[str, Any]],
    targeted: list[dict[str, float]],
    keys: tuple[str, ...],
    diversity_key: str,
    evaluate: Callable[[dict[str, float]], tuple[dict[str, Any], dict[str, float], float]],
    rng: Random,
) -> list[dict[str, Any]]:
    remembered_configs = [row["config"] for row in remembered if isinstance(row.get("config"), dict)]
    population = [*targeted, *remembered_configs]
    while len(population) < POPULATION_SIZE:
        population.append(_mutate_config(base_config, keys=keys, diversity_key=diversity_key, rng=rng, scale=0.16))
    population = _unique_configs(population)[:POPULATION_SIZE]
    evaluated: dict[tuple[tuple[str, float], ...], dict[str, Any]] = {}
    elite = dict(base_config)
    for generation in range(MAX_GENERATIONS):
        generation_rows = []
        for config in population:
            try:
                sig = tuple(sorted((key, round(float(value), 6)) for key, value in config.items()))
                if sig not in evaluated:
                    report, robust, objective = evaluate(config)
                    evaluated[sig] = {
                        "config": config,
                        "report": report,
                        "robustness": robust,
                        "objective": round(objective, 6),
                        "generation": generation + 1,
                    }
                generation_rows.append(evaluated[sig])
            except (TypeError, ValueError, KeyError):
                continue
        if not generation_rows:
            break
        generation_rows.sort(key=lambda row: (-row["objective"], tuple(sorted(row["config"].items()))))
        elite = dict(generation_rows[0]["config"])
        if generation + 1 >= MAX_GENERATIONS:
            break
        population = [elite]
        while len(population) < POPULATION_SIZE:
            population.append(_mutate_config(elite, keys=keys, diversity_key=diversity_key, rng=rng, scale=0.10))
        population = _unique_configs(population)
    return sorted(evaluated.values(), key=lambda row: (-row["objective"], tuple(sorted(row["config"].items()))))


def evolve_search(
    catalog: Catalog,
    current: SearchEngine,
    *,
    remembered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    remembered = remembered or []
    labels = _stable_limit(list(catalog.query_labels), lambda row: row.query)
    discovery_labels, holdout_labels = _stable_split(labels, lambda row: row.query)
    base_config = asdict(current.config)
    prepared = {label.query: current.prepare(label.query) for label in labels}
    reference = _audit_search_cached(catalog, current, labels, prepared, current.config)
    reference_discovery = _audit_search_cached(catalog, current, discovery_labels, prepared, current.config)
    reference_holdout = _audit_search_cached(catalog, current, holdout_labels, prepared, current.config) if holdout_labels else None
    evidence = int(reference.get("queries", 0))
    if evidence < MIN_SEARCH_EVIDENCE:
        return _not_ready_search(reference, base_config)

    def evaluate(config: dict[str, float]):
        cfg = SearchConfig(**config)
        report = _audit_search_cached(catalog, current, discovery_labels, prepared, cfg)
        robust = _search_robustness(reference_discovery, report)
        return report, robust, _search_objective(report, robust)

    rows = _evolution_loop(
        base_config=base_config,
        remembered=remembered,
        targeted=_search_targeted(base_config),
        keys=("lexical", "semantic", "title", "quality", "popularity", "freshness"),
        diversity_key="diversity",
        evaluate=evaluate,
        rng=Random(_seed(catalog, "search")),
    )
    if not rows:
        return _not_ready_search(reference, base_config)
    best = rows[0]
    candidate_config = SearchConfig(**best["config"])
    trial = _audit_search_cached(catalog, current, labels, prepared, candidate_config)
    robust = _search_robustness(reference, trial)
    holdout = _audit_search_cached(catalog, current, holdout_labels, prepared, candidate_config) if holdout_labels else None
    holdout_robust = _search_robustness(reference_holdout, holdout) if holdout and reference_holdout else None
    quality_delta = float(trial.get("quality", 0.0)) - float(reference.get("quality", 0.0))
    recall_delta = float(trial.get("recall", 0.0)) - float(reference.get("recall", 0.0))
    discovery_delta = float(best["objective"]) - _search_objective(reference_discovery)
    holdout_quality_delta = (
        float(holdout.get("quality", 0.0)) - float(reference_holdout.get("quality", 0.0))
        if holdout and reference_holdout else 0.0
    )
    holdout_recall_delta = (
        float(holdout.get("recall", 0.0)) - float(reference_holdout.get("recall", 0.0))
        if holdout and reference_holdout else 0.0
    )
    safe = (
        trial.get("queries", 0) >= MIN_SEARCH_EVIDENCE
        and quality_delta >= -0.002
        and recall_delta >= -0.01
        and robust["worse_share"] <= 0.34
        and robust["worst_delta"] >= -0.35
        and (not holdout or (holdout_quality_delta >= -0.005 and holdout_recall_delta >= -0.02))
        and (not holdout_robust or holdout_robust["worst_delta"] >= -0.45)
    )
    trusted = safe and discovery_delta >= 0.001 and (quality_delta > 0.0005 or recall_delta > 0.002)
    return {
        "reference": reference,
        "candidate": trial,
        "delta": {"quality": round(quality_delta, 4), "recall": round(recall_delta, 4)},
        "evaluation_ready": True,
        "safe_to_try": safe,
        "trusted": trusted,
        "candidate_config": best["config"],
        "candidate_count": len(rows),
        "generations": MAX_GENERATIONS,
        "robustness": robust,
        "objective_delta": round(discovery_delta, 5),
        "validation": {
            "discovery": {"samples": len(discovery_labels), "objective_delta": round(discovery_delta, 5)},
            "holdout": {
                "samples": len(holdout_labels),
                "quality_delta": round(holdout_quality_delta, 4),
                "recall_delta": round(holdout_recall_delta, 4),
                "robustness": holdout_robust,
            },
        },
        "top_candidates": [
            {
                "objective": row["objective"],
                "quality": row["report"].get("quality", 0.0),
                "recall": row["report"].get("recall", 0.0),
                "worse_share": row["robustness"].get("worse_share", 0.0),
            }
            for row in rows[:3]
        ],
    }


def evolve_recommend(
    catalog: Catalog,
    current: RecommendationEngine,
    *,
    remembered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    remembered = remembered or []
    users = _stable_limit(current.known_users(), lambda user: user)
    discovery_users, holdout_users = _stable_split(users, lambda user: user)
    base_config = asdict(current.config)
    prepared = {user: current.prepare(user) for user in users}
    reference = _audit_recommend_cached(catalog, current, users, prepared, current.config)
    reference_discovery = _audit_recommend_cached(catalog, current, discovery_users, prepared, current.config)
    reference_holdout = _audit_recommend_cached(catalog, current, holdout_users, prepared, current.config) if holdout_users else None
    evidence = int(reference.get("users", 0))
    if evidence < MIN_RECOMMEND_EVIDENCE:
        return _not_ready_recommend(reference, base_config)

    def evaluate(config: dict[str, float]):
        cfg = RecommendConfig(**config)
        report = _audit_recommend_cached(catalog, current, discovery_users, prepared, cfg)
        robust = _recommend_robustness(reference_discovery, report)
        return report, robust, _recommend_objective(report, robust)

    rows = _evolution_loop(
        base_config=base_config,
        remembered=remembered,
        targeted=_recommend_targeted(base_config),
        keys=("profile", "graph", "category", "quality", "freshness", "popularity", "novelty", "exploration"),
        diversity_key="diversity",
        evaluate=evaluate,
        rng=Random(_seed(catalog, "recommend")),
    )
    if not rows:
        return _not_ready_recommend(reference, base_config)
    best = rows[0]
    candidate_config = RecommendConfig(**best["config"])
    trial = _audit_recommend_cached(catalog, current, users, prepared, candidate_config)
    robust = _recommend_robustness(reference, trial)
    holdout = _audit_recommend_cached(catalog, current, holdout_users, prepared, candidate_config) if holdout_users else None
    holdout_robust = _recommend_robustness(reference_holdout, holdout) if holdout and reference_holdout else None
    q_delta = float(trial.get("quality", 0.0)) - float(reference.get("quality", 0.0))
    fresh_delta = float(trial.get("freshness", 0.0)) - float(reference.get("freshness", 0.0))
    cov_delta = float(trial.get("coverage", 0.0)) - float(reference.get("coverage", 0.0))
    div_delta = float(trial.get("diversity", 0.0)) - float(reference.get("diversity", 0.0))
    discovery_delta = float(best["objective"]) - _recommend_objective(reference_discovery)
    holdout_q_delta = (
        float(holdout.get("quality", 0.0)) - float(reference_holdout.get("quality", 0.0))
        if holdout and reference_holdout else 0.0
    )
    holdout_cov_delta = (
        float(holdout.get("coverage", 0.0)) - float(reference_holdout.get("coverage", 0.0))
        if holdout and reference_holdout else 0.0
    )
    safe = (
        trial.get("users", 0) >= MIN_RECOMMEND_EVIDENCE
        and q_delta >= -0.003
        and cov_delta >= -0.02
        and fresh_delta >= -0.012
        and robust["worse_share"] <= 0.40
        and robust["worst_delta"] >= -0.30
        and (not holdout or (holdout_q_delta >= -0.008 and holdout_cov_delta >= -0.06))
        and (not holdout_robust or holdout_robust["worst_delta"] >= -0.35)
    )
    trusted = safe and discovery_delta >= 0.001 and (q_delta > 0.0005 or fresh_delta > 0.002 or div_delta > 0.002)
    return {
        "reference": reference,
        "candidate": trial,
        "delta": {
            "quality": round(q_delta, 4),
            "freshness": round(fresh_delta, 4),
            "coverage": round(cov_delta, 4),
            "diversity": round(div_delta, 4),
        },
        "evaluation_ready": True,
        "safe_to_try": safe,
        "trusted": trusted,
        "candidate_config": best["config"],
        "candidate_count": len(rows),
        "generations": MAX_GENERATIONS,
        "robustness": robust,
        "objective_delta": round(discovery_delta, 5),
        "validation": {
            "discovery": {"samples": len(discovery_users), "objective_delta": round(discovery_delta, 5)},
            "holdout": {
                "samples": len(holdout_users),
                "quality_delta": round(holdout_q_delta, 4),
                "coverage_delta": round(holdout_cov_delta, 4),
                "robustness": holdout_robust,
            },
        },
        "top_candidates": [
            {
                "objective": row["objective"],
                "quality": row["report"].get("quality", 0.0),
                "coverage": row["report"].get("coverage", 0.0),
                "freshness": row["report"].get("freshness", 0.0),
                "worse_share": row["robustness"].get("worse_share", 0.0),
            }
            for row in rows[:3]
        ],
    }

def _not_ready_search(reference: dict[str, Any], base_config: dict[str, float]) -> dict[str, Any]:
    return {
        "reference": reference,
        "candidate": reference,
        "delta": {"quality": 0.0, "recall": 0.0},
        "evaluation_ready": False,
        "safe_to_try": False,
        "trusted": False,
        "candidate_config": base_config,
        "candidate_count": 0,
        "generations": 0,
        "robustness": {"worse_share": 0.0, "worst_delta": 0.0, "mean_delta": 0.0},
        "objective_delta": 0.0,
        "validation": {"discovery": {"samples": 0}, "holdout": {"samples": 0}},
    }


def _not_ready_recommend(reference: dict[str, Any], base_config: dict[str, float]) -> dict[str, Any]:
    return {
        "reference": reference,
        "candidate": reference,
        "delta": {"quality": 0.0, "freshness": 0.0, "coverage": 0.0, "diversity": 0.0},
        "evaluation_ready": False,
        "safe_to_try": False,
        "trusted": False,
        "candidate_config": base_config,
        "candidate_count": 0,
        "generations": 0,
        "robustness": {"worse_share": 0.0, "worst_delta": 0.0, "mean_delta": 0.0},
        "objective_delta": 0.0,
        "validation": {"discovery": {"samples": 0}, "holdout": {"samples": 0}},
    }
