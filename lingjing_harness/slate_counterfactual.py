"""Explicit off-policy evaluation for ordered search / recommendation slates.

Single-action contextual-bandit propensities are not sufficient to evaluate an
ordered list. This module provides three complementary estimators with explicit
position-level logging contracts:

- IIPS: item-position propensity ratios under the item-position reward model;
- RIPS: prefix / cascade propensity ratios under sequential reward interaction;
- Cascade-DR: cascade importance correction plus a position-conditional reward
  model control variate.

The implementation is dependency-light and does not infer probabilities from rank,
score, logits, or deterministic output. Integrators must supply exact logging and
target probabilities. Cascade-DR additionally verifies that target prefix
propensities are mathematically consistent with the supplied target conditional
action distributions.

Bootstrap resampling is performed at the *whole-slate* identity level so positions
from one request are never split across resamples.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import blake2b
from math import isfinite, sqrt
from random import Random
from statistics import mean
from typing import Any, Iterable, Mapping


DEFAULT_SLATE_BOOTSTRAP_ITERATIONS = 600
_PROBABILITY_TOLERANCE = 1e-6


def _probability(value: Any, *, name: str, allow_zero: bool) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    lower_ok = number >= 0.0 if allow_zero else number > 0.0
    if not lower_ok or number > 1.0:
        interval = "[0, 1]" if allow_zero else "(0, 1]"
        raise ValueError(f"{name} must be within {interval}")
    return number


def _finite(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class SlatePositionRecord:
    """One logged position inside an ordered slate.

    ``logging_item_position_propensity`` and ``target_item_position_propensity``
    are marginal probabilities for the logged item at this exact position.

    ``logging_cascade_propensity`` and ``target_cascade_propensity`` are joint
    probabilities of the logged action prefix from position zero through this
    position. They must be non-increasing inside a slate.

    Cascade-DR additionally requires ``q_values`` for every action represented by
    ``target_action_distribution``. The latter is the target policy's conditional
    action distribution at this position given the observed prefix. Its probability
    for the logged action must reproduce the supplied target cascade propensity.
    """

    slate_id: str
    surface: str
    position: int
    action_id: str
    reward: float
    logging_policy_id: str
    target_policy_id: str
    logging_item_position_propensity: float
    target_item_position_propensity: float
    logging_cascade_propensity: float
    target_cascade_propensity: float
    q_values: dict[str, float] | None = None
    target_action_distribution: dict[str, float] | None = None
    segment: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.slate_id).strip():
            raise ValueError("slate_id must not be empty")
        surface = str(self.surface or "").strip().lower()
        if surface not in {"search", "recommend"}:
            raise ValueError("surface must be search or recommend")
        object.__setattr__(self, "surface", surface)
        if isinstance(self.position, bool) or int(self.position) != self.position or int(self.position) < 0:
            raise ValueError("position must be an integer >= 0")
        if not str(self.action_id).strip():
            raise ValueError("action_id must not be empty")
        if not str(self.logging_policy_id).strip() or not str(self.target_policy_id).strip():
            raise ValueError("logging_policy_id and target_policy_id are required")

        object.__setattr__(self, "reward", _finite(self.reward, name="reward"))
        for field_name, allow_zero in (
            ("logging_item_position_propensity", False),
            ("target_item_position_propensity", True),
            ("logging_cascade_propensity", False),
            ("target_cascade_propensity", True),
        ):
            object.__setattr__(
                self,
                field_name,
                _probability(getattr(self, field_name), name=field_name, allow_zero=allow_zero),
            )

        has_q = self.q_values is not None
        has_target_dist = self.target_action_distribution is not None
        if has_q != has_target_dist:
            raise ValueError("Cascade-DR requires both q_values and target_action_distribution")
        if has_q:
            if not isinstance(self.q_values, Mapping) or not self.q_values:
                raise ValueError("q_values must be a non-empty object")
            if not isinstance(self.target_action_distribution, Mapping) or not self.target_action_distribution:
                raise ValueError("target_action_distribution must be a non-empty object")
            q_values = {
                str(key): _finite(value, name=f"q_values[{key}]")
                for key, value in self.q_values.items()
                if str(key)
            }
            target_dist = {
                str(key): _probability(
                    value,
                    name=f"target_action_distribution[{key}]",
                    allow_zero=True,
                )
                for key, value in self.target_action_distribution.items()
                if str(key)
            }
            if self.action_id not in q_values:
                raise ValueError("q_values must include the logged action_id")
            if set(target_dist) - set(q_values):
                raise ValueError("q_values must cover every action in target_action_distribution")
            if abs(sum(target_dist.values()) - 1.0) > _PROBABILITY_TOLERANCE:
                raise ValueError("target_action_distribution probabilities must sum to 1")
            object.__setattr__(self, "q_values", q_values)
            object.__setattr__(self, "target_action_distribution", target_dist)

        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be an object")
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "position", int(self.position))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SlatePositionRecord":
        if not isinstance(raw, Mapping):
            raise ValueError("slate position record must be an object")
        required = (
            "slate_id",
            "surface",
            "position",
            "action_id",
            "reward",
            "logging_policy_id",
            "target_policy_id",
            "logging_item_position_propensity",
            "target_item_position_propensity",
            "logging_cascade_propensity",
            "target_cascade_propensity",
        )
        missing = [name for name in required if raw.get(name) in (None, "")]
        if missing:
            raise ValueError(
                "slate position record is missing explicit fields: " + ", ".join(missing)
            )
        try:
            position = int(raw["position"])
        except (TypeError, ValueError) as exc:
            raise ValueError("position must be an integer >= 0") from exc
        return cls(
            slate_id=str(raw["slate_id"]).strip(),
            surface=str(raw["surface"]).strip().lower(),
            position=position,
            action_id=str(raw["action_id"]).strip(),
            reward=raw["reward"],
            logging_policy_id=str(raw["logging_policy_id"]).strip(),
            target_policy_id=str(raw["target_policy_id"]).strip(),
            logging_item_position_propensity=raw["logging_item_position_propensity"],
            target_item_position_propensity=raw["target_item_position_propensity"],
            logging_cascade_propensity=raw["logging_cascade_propensity"],
            target_cascade_propensity=raw["target_cascade_propensity"],
            q_values=(dict(raw["q_values"]) if isinstance(raw.get("q_values"), Mapping) else None),
            target_action_distribution=(
                dict(raw["target_action_distribution"])
                if isinstance(raw.get("target_action_distribution"), Mapping)
                else None
            ),
            segment=str(raw.get("segment") or "").strip(),
            metadata=dict(raw.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "slate_id": self.slate_id,
            "surface": self.surface,
            "position": self.position,
            "action_id": self.action_id,
            "reward": self.reward,
            "logging_policy_id": self.logging_policy_id,
            "target_policy_id": self.target_policy_id,
            "logging_item_position_propensity": self.logging_item_position_propensity,
            "target_item_position_propensity": self.target_item_position_propensity,
            "logging_cascade_propensity": self.logging_cascade_propensity,
            "target_cascade_propensity": self.target_cascade_propensity,
        }
        if self.q_values is not None:
            row["q_values"] = dict(self.q_values)
            row["target_action_distribution"] = dict(self.target_action_distribution or {})
        if self.segment:
            row["segment"] = self.segment
        if self.metadata:
            row["metadata"] = dict(self.metadata)
        return row


def _group_slates(records: Iterable[SlatePositionRecord]) -> dict[str, list[SlatePositionRecord]]:
    grouped: dict[str, list[SlatePositionRecord]] = {}
    logging_policy = ""
    target_policy = ""
    surface = ""
    segment: str | None = None
    for row in records:
        if logging_policy and row.logging_policy_id != logging_policy:
            raise ValueError("slate OPE report must contain exactly one logging_policy_id")
        if target_policy and row.target_policy_id != target_policy:
            raise ValueError("slate OPE report must contain exactly one target_policy_id")
        if surface and row.surface != surface:
            raise ValueError("slate OPE report must contain exactly one surface")
        if segment is not None and row.segment != segment:
            raise ValueError("slate OPE report must contain exactly one segment scope")
        logging_policy = row.logging_policy_id
        target_policy = row.target_policy_id
        surface = row.surface
        segment = row.segment
        grouped.setdefault(row.slate_id, []).append(row)

    for slate_id, rows in grouped.items():
        rows.sort(key=lambda row: row.position)
        positions = [row.position for row in rows]
        if positions != list(range(len(rows))):
            raise ValueError(f"slate {slate_id} positions must be contiguous and start at zero")
        actions = [row.action_id for row in rows]
        if len(actions) != len(set(actions)):
            raise ValueError(f"slate {slate_id} must not repeat an action_id")

        previous_logging = 1.0
        previous_target = 1.0
        previous_actions: set[str] = set()
        for row in rows:
            if row.logging_cascade_propensity > previous_logging + 1e-12:
                raise ValueError(
                    f"slate {slate_id} logging_cascade_propensity must be non-increasing"
                )
            if row.target_cascade_propensity > previous_target + 1e-12:
                raise ValueError(
                    f"slate {slate_id} target_cascade_propensity must be non-increasing"
                )
            if row.target_action_distribution is not None:
                target_dist = row.target_action_distribution
                repeated_mass = sum(float(target_dist.get(action, 0.0)) for action in previous_actions)
                if repeated_mass > _PROBABILITY_TOLERANCE:
                    raise ValueError(
                        f"slate {slate_id} target_action_distribution assigns mass to a prior action"
                    )
                conditional = float(target_dist.get(row.action_id, 0.0))
                expected_prefix = previous_target * conditional
                if abs(expected_prefix - row.target_cascade_propensity) > _PROBABILITY_TOLERANCE:
                    raise ValueError(
                        f"slate {slate_id} target cascade propensity is inconsistent with target_action_distribution"
                    )
            previous_logging = row.logging_cascade_propensity
            previous_target = row.target_cascade_propensity
            previous_actions.add(row.action_id)
    return grouped


def _iips_slate_value(rows: list[SlatePositionRecord]) -> float:
    return sum(
        row.reward * row.target_item_position_propensity / row.logging_item_position_propensity
        for row in rows
    )


def _rips_slate_value(rows: list[SlatePositionRecord]) -> float:
    return sum(
        row.reward * row.target_cascade_propensity / row.logging_cascade_propensity
        for row in rows
    )


def _cascade_dr_slate_value(rows: list[SlatePositionRecord]) -> float:
    if not all(row.q_values is not None for row in rows):
        raise ValueError("Cascade-DR requires reward-model inputs at every position")
    total = 0.0
    previous_weight = 1.0
    for row in rows:
        weight = row.target_cascade_propensity / row.logging_cascade_propensity
        q_values = row.q_values or {}
        target_dist = row.target_action_distribution or {}
        q_observed = float(q_values[row.action_id])
        expected_q = sum(
            float(target_dist[action]) * float(q_values[action])
            for action in target_dist
        )
        total += weight * (row.reward - q_observed) + previous_weight * expected_q
        previous_weight = weight
    return total


def _estimator_values(grouped: dict[str, list[SlatePositionRecord]]) -> dict[str, dict[str, float]]:
    values: dict[str, dict[str, float]] = {
        "logged": {},
        "iips": {},
        "rips": {},
        "cascade_dr": {},
    }
    complete_model = all(
        row.q_values is not None
        for rows in grouped.values()
        for row in rows
    )
    for slate_id, rows in grouped.items():
        values["logged"][slate_id] = sum(row.reward for row in rows)
        values["iips"][slate_id] = _iips_slate_value(rows)
        values["rips"][slate_id] = _rips_slate_value(rows)
        if complete_model:
            values["cascade_dr"][slate_id] = _cascade_dr_slate_value(rows)
    return values


def _effective_sample_ratio(weights: list[float]) -> float:
    if not weights:
        return 0.0
    total = sum(weights)
    squares = sum(value * value for value in weights)
    if squares <= 0.0:
        return 0.0
    return (total * total / squares) / len(weights)


def _coefficient_of_variation(values: list[float]) -> float:
    if not values:
        return 0.0
    avg = mean(values)
    if avg == 0.0:
        return 0.0
    variance = mean((value - avg) ** 2 for value in values)
    return sqrt(max(0.0, variance)) / abs(avg)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0.0, min(1.0, float(fraction))) * (len(ordered) - 1)
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    mix = position - low
    return ordered[low] * (1.0 - mix) + ordered[high] * mix


def _position_weight_diagnostics(
    grouped: dict[str, list[SlatePositionRecord]],
    *,
    kind: str,
) -> tuple[list[float], dict[int, float]]:
    all_weights: list[float] = []
    by_position: dict[int, list[float]] = {}
    for rows in grouped.values():
        for row in rows:
            if kind == "item":
                weight = row.target_item_position_propensity / row.logging_item_position_propensity
            else:
                weight = row.target_cascade_propensity / row.logging_cascade_propensity
            all_weights.append(weight)
            by_position.setdefault(row.position, []).append(weight)
    ess = {
        position: _effective_sample_ratio(weights)
        for position, weights in sorted(by_position.items())
    }
    return all_weights, ess


def _final_cascade_weights(grouped: dict[str, list[SlatePositionRecord]]) -> list[float]:
    weights = []
    for rows in grouped.values():
        last = rows[-1]
        weights.append(last.target_cascade_propensity / last.logging_cascade_propensity)
    return weights


def _stable_seed(grouped: dict[str, list[SlatePositionRecord]], label: str) -> int:
    parts = []
    for slate_id in sorted(grouped):
        for row in grouped[slate_id]:
            parts.append(
                f"{slate_id}:{row.position}:{row.action_id}:{row.reward:.12g}:"
                f"{row.logging_item_position_propensity:.12g}:"
                f"{row.target_item_position_propensity:.12g}:"
                f"{row.logging_cascade_propensity:.12g}:"
                f"{row.target_cascade_propensity:.12g}"
            )
    raw = f"{label}|" + "|".join(parts)
    return int.from_bytes(blake2b(raw.encode("utf-8"), digest_size=8).digest(), "little")


def _bootstrap_delta(
    grouped: dict[str, list[SlatePositionRecord]],
    estimator: str,
    *,
    iterations: int,
) -> dict[str, Any]:
    slate_ids = sorted(grouped)
    if not slate_ids:
        return {
            "available": False,
            "samples": 0,
            "delta": 0.0,
            "ci95": None,
            "probability_positive": None,
            "bootstrap_unit": "whole_slate",
        }
    per_slate = _estimator_values(grouped)
    logged = per_slate["logged"]
    target = per_slate[estimator]
    common = [key for key in slate_ids if key in target]
    observed = mean(target[key] - logged[key] for key in common) if common else 0.0
    if len(common) < 2:
        return {
            "available": False,
            "samples": len(common),
            "delta": round(observed, 6),
            "ci95": None,
            "probability_positive": None,
            "bootstrap_unit": "whole_slate",
        }
    rng = Random(_stable_seed(grouped, estimator))
    count = len(common)
    draws: list[float] = []
    for _ in range(max(100, min(5000, int(iterations)))):
        sample = [common[rng.randrange(count)] for _ in range(count)]
        draws.append(mean(target[key] - logged[key] for key in sample))
    draws.sort()
    low = _percentile(draws, 0.025)
    high = _percentile(draws, 0.975)
    positive = sum(1 for value in draws if value > 0.0) / len(draws)
    return {
        "available": True,
        "samples": len(common),
        "delta": round(observed, 6),
        "ci95": [round(low, 6), round(high, 6)],
        "probability_positive": round(positive, 4),
        "bootstrap_unit": "whole_slate",
    }


def evaluate_slate_off_policy(
    records: Iterable[SlatePositionRecord],
    *,
    bootstrap_iterations: int = DEFAULT_SLATE_BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    """Evaluate IIPS, RIPS, and Cascade-DR on explicit slate logging evidence."""

    grouped = _group_slates(records)
    if not grouped:
        return {
            "available": False,
            "slates": 0,
            "positions": 0,
            "estimators": {},
            "confidence": {},
            "diagnostics": {},
        }

    values = _estimator_values(grouped)
    logged_mean = mean(values["logged"].values())
    complete_model = len(values["cascade_dr"]) == len(grouped)

    def estimator_row(name: str) -> dict[str, Any]:
        rows = values[name]
        if not rows:
            return {"available": False, "value": None, "delta_vs_logged": None}
        value = mean(rows.values())
        return {
            "available": True,
            "value": round(value, 6),
            "delta_vs_logged": round(value - logged_mean, 6),
        }

    item_weights, item_ess_by_position = _position_weight_diagnostics(grouped, kind="item")
    cascade_weights, cascade_ess_by_position = _position_weight_diagnostics(grouped, kind="cascade")
    final_cascade_weights = _final_cascade_weights(grouped)
    total_positions = sum(len(rows) for rows in grouped.values())
    first = next(iter(grouped.values()))[0]

    estimators = {
        "logged_mean": {
            "available": True,
            "value": round(logged_mean, 6),
            "delta_vs_logged": 0.0,
        },
        "iips": estimator_row("iips"),
        "rips": estimator_row("rips"),
        "cascade_dr": estimator_row("cascade_dr"),
    }
    if not complete_model:
        estimators["cascade_dr"]["reason"] = (
            "complete position-level q_values and target_action_distribution are required"
        )

    confidence = {
        "iips": _bootstrap_delta(grouped, "iips", iterations=bootstrap_iterations),
        "rips": _bootstrap_delta(grouped, "rips", iterations=bootstrap_iterations),
        "cascade_dr": (
            _bootstrap_delta(grouped, "cascade_dr", iterations=bootstrap_iterations)
            if complete_model
            else {
                "available": False,
                "samples": 0,
                "delta": None,
                "ci95": None,
                "probability_positive": None,
                "reason": "reward_model_incomplete",
                "bootstrap_unit": "whole_slate",
            }
        ),
    }

    available_values = [
        float(estimators[name]["value"])
        for name in ("iips", "rips", "cascade_dr")
        if estimators[name].get("available")
    ]
    spread = max(available_values) - min(available_values) if len(available_values) >= 2 else 0.0
    value_scale = max(1.0, abs(logged_mean), *(abs(value) for value in available_values))
    normalized_spread = spread / value_scale
    minimum_item_ess = min(item_ess_by_position.values(), default=0.0)
    minimum_cascade_ess = min(cascade_ess_by_position.values(), default=0.0)
    heavy_tail = bool(
        max(item_weights, default=0.0) >= 20.0
        or max(cascade_weights, default=0.0) >= 20.0
        or minimum_item_ess < 0.35
        or minimum_cascade_ess < 0.35
        or _coefficient_of_variation(cascade_weights) >= 1.5
    )

    return {
        "available": True,
        "surface": first.surface,
        "slates": len(grouped),
        "positions": total_positions,
        "slate_lengths": sorted({len(rows) for rows in grouped.values()}),
        "logging_policy_id": first.logging_policy_id,
        "target_policy_id": first.target_policy_id,
        "segment": first.segment,
        "estimators": estimators,
        "confidence": confidence,
        "diagnostics": {
            "reward_model_coverage": round(
                sum(
                    1
                    for rows in grouped.values()
                    for row in rows
                    if row.q_values is not None
                )
                / total_positions,
                6,
            ),
            "item_position_weight_max": round(max(item_weights, default=0.0), 6),
            "item_position_weight_p95": round(_percentile(item_weights, 0.95), 6),
            "item_position_effective_sample_ratio": round(
                _effective_sample_ratio(item_weights), 6
            ),
            "item_position_effective_sample_ratio_by_position": {
                str(position): round(value, 6)
                for position, value in item_ess_by_position.items()
            },
            "minimum_item_position_effective_sample_ratio": round(minimum_item_ess, 6),
            "cascade_weight_max": round(max(cascade_weights, default=0.0), 6),
            "cascade_weight_p95": round(_percentile(cascade_weights, 0.95), 6),
            "cascade_weight_cv": round(_coefficient_of_variation(cascade_weights), 6),
            "cascade_effective_sample_ratio": round(
                _effective_sample_ratio(cascade_weights), 6
            ),
            "cascade_effective_sample_ratio_by_position": {
                str(position): round(value, 6)
                for position, value in cascade_ess_by_position.items()
            },
            "minimum_cascade_effective_sample_ratio": round(minimum_cascade_ess, 6),
            "final_prefix_effective_sample_ratio": round(
                _effective_sample_ratio(final_cascade_weights), 6
            ),
            "target_zero_item_position_share": round(
                sum(1 for value in item_weights if value == 0.0) / total_positions, 6
            ),
            "target_zero_cascade_share": round(
                sum(1 for value in cascade_weights if value == 0.0) / total_positions, 6
            ),
            "heavy_tail_detected": heavy_tail,
            "estimator_spread": round(spread, 6),
            "estimator_normalized_spread": round(normalized_spread, 6),
            "estimator_agreement": round(1.0 / (1.0 + normalized_spread), 6),
            "bootstrap_unit": "whole_slate",
            "support_semantics": (
                "logged-action ratios only; full target-policy support cannot be inferred from logged slates"
            ),
            "assumptions": {
                "iips": "item_position_reward_independence",
                "rips": "cascade_sequential_reward_interaction",
                "cascade_dr": "cascade_interaction_plus_position_conditional_reward_model",
            },
        },
    }


__all__ = [
    "SlatePositionRecord",
    "evaluate_slate_off_policy",
]
