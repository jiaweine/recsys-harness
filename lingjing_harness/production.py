from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import blake2b
from math import isfinite, log2
from random import Random
from statistics import mean
from typing import Any, Iterable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class RewardSpec:
    """Business reward contract owned by the integrating product team.

    Sequence quality, freshness and diversity remain useful guardrail metrics, but
    they are not treated as business value when this contract and production
    exposure logs are available.
    """

    weights: dict[str, float]
    inverse_propensity_cap: float = 20.0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RewardSpec":
        if not isinstance(raw, Mapping):
            raise ValueError("reward_spec 必须是对象")
        weights_raw = raw.get("weights") or raw.get("events") or {}
        if not isinstance(weights_raw, Mapping) or not weights_raw:
            raise ValueError("reward_spec.weights 至少需要一个事件权重")
        weights: dict[str, float] = {}
        for name, value in weights_raw.items():
            event = str(name).strip().lower()
            if not event:
                continue
            try:
                weight = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"reward weight 必须是数值: {event}") from exc
            if not isfinite(weight):
                raise ValueError(f"reward weight 必须是有限数值: {event}")
            weights[event] = weight
        if not weights:
            raise ValueError("reward_spec.weights 至少需要一个有效事件权重")
        try:
            cap = float(raw.get("inverse_propensity_cap", 20.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("inverse_propensity_cap 必须是数值") from exc
        if not isfinite(cap) or cap < 1.0 or cap > 100.0:
            raise ValueError("inverse_propensity_cap 必须在 [1, 100]")
        return cls(weights=weights, inverse_propensity_cap=cap)

    def reward(self, event: str, value: float = 1.0) -> float:
        weight = self.weights.get(str(event).strip().lower(), 0.0)
        number = float(value)
        if not isfinite(number):
            raise ValueError("reward event value 必须是有限数值")
        return weight * number

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": dict(self.weights),
            "inverse_propensity_cap": self.inverse_propensity_cap,
        }


@dataclass(frozen=True, slots=True)
class ExposureEvent:
    """One production exposure/outcome row.

    Rows are intentionally flat so they can be emitted directly from ranking
    services or warehouses. Multiple rows with the same ``request_id`` describe
    one request and are kept on the same side of temporal evaluation splits.
    """

    request_id: str
    timestamp: float
    surface: str
    item_id: str
    event: str = "impression"
    value: float = 1.0
    propensity: float | None = None
    position: int | None = None
    user_id: str = ""
    query: str = ""
    policy_id: str = ""
    model_version: str = ""
    experiment_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "ExposureEvent":
        if not isinstance(row, Mapping):
            raise ValueError("events 中的每一项都必须是对象")
        request_id = str(row.get("request_id") or row.get("request") or "").strip()
        surface = str(row.get("surface") or row.get("domain") or "").strip().lower()
        item_id = str(row.get("item_id") or row.get("item") or "").strip()
        if not request_id:
            raise ValueError("event.request_id 不能为空")
        if surface not in {"search", "recommend"}:
            raise ValueError("event.surface 必须是 search 或 recommend")
        if not item_id:
            raise ValueError("event.item_id 不能为空")
        try:
            timestamp = float(row.get("timestamp", 0.0))
            value = float(row.get("value", 1.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("event.timestamp/value 必须是数值") from exc
        if not isfinite(timestamp) or not isfinite(value):
            raise ValueError("event.timestamp/value 必须是有限数值")
        propensity_raw = row.get("propensity")
        propensity: float | None = None
        if propensity_raw not in (None, ""):
            try:
                propensity = float(propensity_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("event.propensity 必须是数值") from exc
            if not isfinite(propensity) or propensity <= 0.0 or propensity > 1.0:
                raise ValueError("event.propensity 必须在 (0, 1]")
        position_raw = row.get("position")
        position: int | None = None
        if position_raw not in (None, ""):
            try:
                position = int(position_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("event.position 必须是整数") from exc
            if position < 1:
                raise ValueError("event.position 必须 >= 1")
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise ValueError("event.metadata 必须是对象")
        return cls(
            request_id=request_id,
            timestamp=timestamp,
            surface=surface,
            item_id=item_id,
            event=str(row.get("event") or "impression").strip().lower(),
            value=value,
            propensity=propensity,
            position=position,
            user_id=str(row.get("user_id") or row.get("user") or "").strip(),
            query=str(row.get("query") or "").strip(),
            policy_id=str(row.get("policy_id") or "").strip(),
            model_version=str(row.get("model_version") or "").strip(),
            experiment_id=str(row.get("experiment_id") or "").strip(),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "surface": self.surface,
            "item_id": self.item_id,
            "event": self.event,
            "value": self.value,
        }
        if self.propensity is not None:
            row["propensity"] = self.propensity
        if self.position is not None:
            row["position"] = self.position
        if self.user_id:
            row["user_id"] = self.user_id
        if self.query:
            row["query"] = self.query
        if self.policy_id:
            row["policy_id"] = self.policy_id
        if self.model_version:
            row["model_version"] = self.model_version
        if self.experiment_id:
            row["experiment_id"] = self.experiment_id
        if self.metadata:
            row["metadata"] = dict(self.metadata)
        return row


class SearchReplayEngine(Protocol):
    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]: ...


class RecommendReplayEngine(Protocol):
    def recommend(self, user_id: str, *, limit: int = 10) -> list[dict[str, Any]]: ...


def request_groups(
    events: Iterable[ExposureEvent],
    *,
    surface: str | None = None,
) -> dict[str, list[ExposureEvent]]:
    grouped: dict[str, list[ExposureEvent]] = {}
    for event in events:
        if surface and event.surface != surface:
            continue
        grouped.setdefault(event.request_id, []).append(event)
    for rows in grouped.values():
        rows.sort(key=lambda row: (row.timestamp, row.position or 10**9, row.item_id, row.event))
    return grouped


def temporal_request_split(
    events: Iterable[ExposureEvent],
    *,
    surface: str,
    holdout_fraction: float = 0.25,
    minimum_requests: int = 4,
) -> tuple[list[ExposureEvent], list[ExposureEvent]]:
    """Create a future holdout split without splitting one request identity."""

    grouped = request_groups(events, surface=surface)
    if len(grouped) < minimum_requests:
        return [event for rows in grouped.values() for event in rows], []
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            max(row.timestamp for row in item[1]),
            item[0],
        ),
    )
    holdout_size = max(1, min(len(ranked) - 2, round(len(ranked) * holdout_fraction)))
    cut = len(ranked) - holdout_size
    discovery_ids = {request_id for request_id, _ in ranked[:cut]}
    holdout_ids = {request_id for request_id, _ in ranked[cut:]}
    discovery = [event for request_id, rows in grouped.items() if request_id in discovery_ids for event in rows]
    holdout = [event for request_id, rows in grouped.items() if request_id in holdout_ids for event in rows]
    return discovery, holdout


def _rank_discount(rank: int | None) -> float:
    if rank is None or rank < 1:
        return 0.0
    return 1.0 / log2(rank + 1.0)


def _request_score(
    rows: list[ExposureEvent],
    ranked_ids: list[str],
    reward_spec: RewardSpec,
) -> tuple[float, float, float]:
    rank_by_id = {item_id: index + 1 for index, item_id in enumerate(ranked_ids)}
    numerator = 0.0
    denominator = 0.0
    ranked_mass = 0.0
    for event in rows:
        reward = reward_spec.reward(event.event, event.value)
        if reward == 0.0:
            continue
        inverse = 1.0
        if event.propensity is not None:
            inverse = min(reward_spec.inverse_propensity_cap, 1.0 / event.propensity)
        mass = abs(reward) * inverse
        denominator += mass
        rank = rank_by_id.get(event.item_id)
        if rank is not None:
            ranked_mass += mass
            numerator += reward * inverse * _rank_discount(rank)
    if denominator <= 0.0:
        return 0.0, 0.0, 0.0
    return numerator / denominator, ranked_mass / denominator, denominator


def evaluate_logged_policy(
    events: Iterable[ExposureEvent],
    *,
    surface: str,
    reward_spec: RewardSpec,
    search_engine: SearchReplayEngine | None = None,
    recommend_engine: RecommendReplayEngine | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Replay a policy on logged requests using the team's reward contract.

    This is deliberately called *logged replay*, not a magically unbiased OPE.
    Propensity is used when present, but unobserved outcomes remain unobserved.
    The result is therefore suitable for routing and safety evidence, while a
    production adapter can later add richer IPS/SNIPS/DR estimators.
    """

    if surface not in {"search", "recommend"}:
        raise ValueError("surface must be search or recommend")
    if surface == "search" and search_engine is None:
        raise ValueError("search_engine is required for search replay")
    if surface == "recommend" and recommend_engine is None:
        raise ValueError("recommend_engine is required for recommendation replay")

    grouped = request_groups(events, surface=surface)
    scores: dict[str, float] = {}
    coverages: list[float] = []
    reward_masses: list[float] = []
    skipped = 0
    propensity_rows = 0
    for request_id, rows in sorted(grouped.items()):
        if any(row.propensity is not None for row in rows):
            propensity_rows += sum(1 for row in rows if row.propensity is not None)
        if surface == "search":
            query = next((row.query for row in rows if row.query), "")
            if not query:
                skipped += 1
                continue
            ranked_ids = [row["id"] for row in search_engine.search(query, limit=limit)]  # type: ignore[union-attr]
        else:
            user_id = next((row.user_id for row in rows if row.user_id), "")
            if not user_id:
                skipped += 1
                continue
            ranked_ids = [row["id"] for row in recommend_engine.recommend(user_id, limit=limit)]  # type: ignore[union-attr]
        score, coverage, mass = _request_score(rows, ranked_ids, reward_spec)
        if mass <= 0.0:
            skipped += 1
            continue
        scores[request_id] = score
        coverages.append(coverage)
        reward_masses.append(mass)

    return {
        "surface": surface,
        "estimator": "propensity_weighted_logged_replay" if propensity_rows else "logged_replay",
        "requests": len(scores),
        "available_requests": len(grouped),
        "skipped_requests": skipped,
        "reward": round(mean(scores.values()), 6) if scores else 0.0,
        "reward_coverage": round(mean(coverages), 6) if coverages else 0.0,
        "reward_mass": round(sum(reward_masses), 6),
        "propensity_rows": propensity_rows,
        "request_scores": scores,
    }


def paired_bootstrap_delta(
    reference_scores: Mapping[str, float],
    candidate_scores: Mapping[str, float],
    *,
    iterations: int = 600,
) -> dict[str, Any]:
    """Estimate a paired request-level delta without fabricating singleton uncertainty."""

    common = sorted(set(reference_scores) & set(candidate_scores))
    if not common:
        return {
            "available": False,
            "samples": 0,
            "delta": 0.0,
            "ci95": None,
            "probability_positive": None,
            "reason": "no common request identities are available for paired confidence",
        }
    deltas = [float(candidate_scores[key]) - float(reference_scores[key]) for key in common]
    observed = mean(deltas)
    if len(deltas) == 1:
        return {
            "available": False,
            "samples": 1,
            "delta": round(observed, 6),
            "ci95": None,
            "probability_positive": None,
            "reason": "at least two paired requests are required for bootstrap uncertainty",
        }
    raw = "|".join(f"{key}:{reference_scores[key]:.8f}:{candidate_scores[key]:.8f}" for key in common)
    seed = int.from_bytes(blake2b(raw.encode("utf-8"), digest_size=8).digest(), "little")
    rng = Random(seed)
    draws: list[float] = []
    count = len(deltas)
    draw_count = max(100, min(10000, int(iterations)))
    for _ in range(draw_count):
        draws.append(mean(deltas[rng.randrange(count)] for _ in range(count)))
    draws.sort()
    low = draws[max(0, int(len(draws) * 0.025) - 1)]
    high = draws[min(len(draws) - 1, int(len(draws) * 0.975))]
    positive = sum(1 for value in draws if value > 0.0) / len(draws)
    return {
        "available": True,
        "samples": len(common),
        "delta": round(observed, 6),
        "ci95": [round(low, 6), round(high, 6)],
        "probability_positive": round(positive, 4),
    }
