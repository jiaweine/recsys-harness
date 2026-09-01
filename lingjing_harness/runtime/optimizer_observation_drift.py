from __future__ import annotations

from dataclasses import replace
from math import isfinite
from statistics import median
import time
from typing import Any, Iterable, Mapping, Sequence

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_meta import (
    _normalized_distance,
    _observation_score,
)
from lingjing_harness.algorithms.optimizer_observation_weighting import (
    describe_weighted_optimizer_landscape,
)

from . import optimizer_observation_weighting as weighting


OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS = 4
OPTIMIZER_OBSERVATION_DRIFT_MAX_WINDOW_ROWS = 24
OPTIMIZER_OBSERVATION_DRIFT_MAX_MATCH_DISTANCE = 0.45
OPTIMIZER_OBSERVATION_DRIFT_MIN_MATCH_COVERAGE = 0.75
OPTIMIZER_OBSERVATION_DRIFT_MAX_MEAN_MATCH_DISTANCE = 0.30
OPTIMIZER_OBSERVATION_DRIFT_ORDER_INVERSION_THRESHOLD = 2.0 / 3.0
OPTIMIZER_OBSERVATION_DRIFT_CONTRAST_SHIFT_THRESHOLD = 0.75
OPTIMIZER_OBSERVATION_DRIFT_FEASIBILITY_FLIP_THRESHOLD = 0.50
OPTIMIZER_OBSERVATION_DRIFT_FEASIBILITY_DENSITY_DELTA = 0.35
_DRIFT_STATE_ATTR = "_optimizer_observation_drift_states"
_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _usable_rows(
    observations: Iterable[Mapping[str, Any]],
    dimensions: Sequence[Any],
) -> list[dict[str, Any]]:
    names = tuple(str(getattr(dimension, "name", "") or "") for dimension in dimensions)
    rows: list[dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, Mapping):
            continue
        config = observation.get("config")
        score = _observation_score(observation)
        updated_at = _finite_float(observation.get("updated_at"))
        if (
            not isinstance(config, Mapping)
            or score is None
            or updated_at is None
            or any(not name or name not in config for name in names)
        ):
            continue
        row = dict(observation)
        row["config"] = dict(config)
        row["_drift_score"] = score
        row["_drift_updated_at"] = updated_at
        rows.append(row)
    rows.sort(
        key=lambda row: (
            -float(row["_drift_updated_at"]),
            repr(sorted(dict(row["config"]).items())),
        )
    )
    return rows


def _greedy_matches(
    recent: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    dimensions: Sequence[Any],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any], float]]:
    candidates: list[tuple[float, int, int]] = []
    for recent_index, recent_row in enumerate(recent):
        for history_index, history_row in enumerate(history):
            distance = _normalized_distance(
                recent_row["config"],
                history_row["config"],
                dimensions,
            )
            if (
                distance is not None
                and distance <= OPTIMIZER_OBSERVATION_DRIFT_MAX_MATCH_DISTANCE
            ):
                candidates.append((float(distance), recent_index, history_index))

    used_recent: set[int] = set()
    used_history: set[int] = set()
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any], float]] = []
    for distance, recent_index, history_index in sorted(candidates):
        if recent_index in used_recent or history_index in used_history:
            continue
        used_recent.add(recent_index)
        used_history.add(history_index)
        matches.append((recent[recent_index], history[history_index], distance))
    return matches


def _candidate_diagnostics(
    recent: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    dimensions: Sequence[Any],
) -> dict[str, Any]:
    matches = _greedy_matches(recent, history, dimensions)
    denominator = max(1, min(len(recent), len(history)))
    coverage = len(matches) / denominator
    mean_distance = (
        sum(distance for _, _, distance in matches) / len(matches)
        if matches
        else 1.0
    )
    local_overlap = bool(
        len(matches) >= OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS - 1
        and coverage + 1e-12 >= OPTIMIZER_OBSERVATION_DRIFT_MIN_MATCH_COVERAGE
        and mean_distance
        <= OPTIMIZER_OBSERVATION_DRIFT_MAX_MEAN_MATCH_DISTANCE + 1e-12
    )

    history_scores = [float(row["_drift_score"]) for row in history]
    score_scale = max(0.05, max(history_scores) - min(history_scores))
    epsilon = max(1e-9, 0.05 * score_scale)
    pairwise_checks = 0
    inversions = 0
    contrast_changes: list[float] = []
    for left_index in range(len(matches)):
        for right_index in range(left_index + 1, len(matches)):
            recent_difference = (
                float(matches[left_index][0]["_drift_score"])
                - float(matches[right_index][0]["_drift_score"])
            )
            history_difference = (
                float(matches[left_index][1]["_drift_score"])
                - float(matches[right_index][1]["_drift_score"])
            )
            if abs(history_difference) <= epsilon:
                continue
            pairwise_checks += 1
            if (
                abs(recent_difference) > epsilon
                and recent_difference * history_difference < 0.0
            ):
                inversions += 1
            contrast_changes.append(
                abs(abs(recent_difference) - abs(history_difference)) / score_scale
            )
    inversion_rate = inversions / pairwise_checks if pairwise_checks else 0.0
    contrast_shift = float(median(contrast_changes)) if contrast_changes else 0.0

    feasible_pairs = [
        (bool(recent_row["feasible"]), bool(history_row["feasible"]))
        for recent_row, history_row, _ in matches
        if isinstance(recent_row.get("feasible"), bool)
        and isinstance(history_row.get("feasible"), bool)
    ]
    flip_rate = (
        sum(
            1
            for recent_value, history_value in feasible_pairs
            if recent_value != history_value
        )
        / len(feasible_pairs)
        if feasible_pairs
        else 0.0
    )
    recent_feasible = [
        bool(row["feasible"])
        for row in recent
        if isinstance(row.get("feasible"), bool)
    ]
    history_feasible = [
        bool(row["feasible"])
        for row in history
        if isinstance(row.get("feasible"), bool)
    ]
    density_delta = (
        abs(
            sum(recent_feasible) / len(recent_feasible)
            - sum(history_feasible) / len(history_feasible)
        )
        if len(recent_feasible) == len(recent)
        and len(history_feasible) == len(history)
        else 0.0
    )

    order_shift = bool(
        local_overlap
        and pairwise_checks >= 3
        and inversion_rate + 1e-12
        >= OPTIMIZER_OBSERVATION_DRIFT_ORDER_INVERSION_THRESHOLD
    )
    contrast_shifted = bool(
        local_overlap
        and pairwise_checks >= 3
        and contrast_shift + 1e-12
        >= OPTIMIZER_OBSERVATION_DRIFT_CONTRAST_SHIFT_THRESHOLD
    )
    feasibility_shift = bool(
        local_overlap
        and len(feasible_pairs) >= OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS - 1
        and flip_rate + 1e-12
        >= OPTIMIZER_OBSERVATION_DRIFT_FEASIBILITY_FLIP_THRESHOLD
        and density_delta + 1e-12
        >= OPTIMIZER_OBSERVATION_DRIFT_FEASIBILITY_DENSITY_DELTA
    )

    primary_signals: list[str] = []
    if order_shift:
        primary_signals.append("local_order_inversion")
    if contrast_shifted:
        primary_signals.append("local_contrast_shift")
    signals = list(primary_signals)
    if feasibility_shift:
        signals.append("local_feasibility_shift")

    # Nearby different configs cannot prove that one constraint boundary changed.
    # Keep that evidence supporting-only. Direct feasibility authority is handled
    # separately from the bounded same-config evaluator history.
    severity = max(
        inversion_rate if order_shift else 0.0,
        min(1.0, contrast_shift) if contrast_shifted else 0.0,
    )
    return {
        "change_detected": bool(primary_signals),
        "signals": signals,
        "primary_signals": primary_signals,
        "supporting_signals": [
            signal for signal in signals if signal not in primary_signals
        ],
        "severity": severity,
        "matched_pairs": len(matches),
        "match_coverage": coverage,
        "mean_match_distance": mean_distance,
        "pairwise_checks": pairwise_checks,
        "order_inversion_rate": inversion_rate,
        "contrast_shift": contrast_shift,
        "feasibility_pairs": len(feasible_pairs),
        "feasibility_flip_rate": flip_rate,
        "feasibility_density_delta": density_delta,
        "new_evaluator_calls": 0,
    }


def _same_config_feasibility_history_diagnostics(
    observation_history: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for order, observation in enumerate(observation_history or []):
        if not isinstance(observation, Mapping):
            continue
        config_key = str(observation.get("config_key") or "").strip()
        feasible = observation.get("feasible")
        observed_at = _finite_float(observation.get("observed_at"))
        basis = str(observation.get("feasibility_basis") or "").strip()
        if not config_key or not isinstance(feasible, bool) or observed_at is None or not basis:
            continue
        rows.append(
            {
                "config_key": config_key,
                "feasible": feasible,
                "feasibility_basis": basis,
                "observed_at": observed_at,
                "_order": order,
            }
        )
    rows.sort(key=lambda row: (-float(row["observed_at"]), int(row["_order"])))

    recent: dict[str, dict[str, Any]] = {}
    cutoff: float | None = None
    index = 0
    while index < len(rows):
        cohort_at = float(rows[index]["observed_at"])
        cohort: list[dict[str, Any]] = []
        while index < len(rows) and float(rows[index]["observed_at"]) == cohort_at:
            cohort.append(rows[index])
            index += 1
        for row in cohort:
            recent.setdefault(str(row["config_key"]), row)
        if len(recent) >= OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS:
            cutoff = cohort_at
            break

    if cutoff is None:
        return {
            "change_detected": False,
            "same_config_feasibility_history_available": False,
            "same_config_feasibility_recent_configs": len(recent),
            "same_config_feasibility_pairs": 0,
            "same_config_feasibility_flips": 0,
            "same_config_feasibility_flip_rate": 0.0,
            "same_config_feasibility_basis_mismatches": 0,
            "new_evaluator_calls": 0,
        }

    prior: dict[str, dict[str, Any]] = {}
    basis_mismatches = 0
    for row in rows:
        if float(row["observed_at"]) >= cutoff:
            continue
        config_key = str(row["config_key"])
        recent_row = recent.get(config_key)
        if recent_row is None or config_key in prior:
            continue
        if str(row["feasibility_basis"]) != str(recent_row["feasibility_basis"]):
            basis_mismatches += 1
            continue
        prior[config_key] = row

    pair_keys = sorted(set(recent) & set(prior))
    flips = sum(
        1
        for config_key in pair_keys
        if bool(recent[config_key]["feasible"]) != bool(prior[config_key]["feasible"])
    )
    flip_rate = flips / len(pair_keys) if pair_keys else 0.0
    change_detected = bool(
        len(pair_keys) >= OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS
        and flip_rate + 1e-12 >= OPTIMIZER_OBSERVATION_DRIFT_FEASIBILITY_FLIP_THRESHOLD
    )
    return {
        "change_detected": change_detected,
        "same_config_feasibility_history_available": bool(
            len(pair_keys) >= OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS
        ),
        "same_config_feasibility_recent_configs": len(recent),
        "same_config_feasibility_pairs": len(pair_keys),
        "same_config_feasibility_flips": flips,
        "same_config_feasibility_flip_rate": flip_rate,
        "same_config_feasibility_basis_mismatches": basis_mismatches,
        "same_config_feasibility_recent_oldest_at": cutoff,
        "_recent_cutoff_at": cutoff,
        "new_evaluator_calls": 0,
    }


def _merge_same_config_feasibility_history(
    base: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    history_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(base)
    for key, value in history_diagnostics.items():
        if str(key).startswith("same_config_"):
            result[key] = value

    if not history_diagnostics.get("change_detected"):
        return result

    geometry_changed = bool(result.get("change_detected"))
    primary_signals = list(result.get("primary_signals") or [])
    signal = "same_config_feasibility_shift"
    if signal not in primary_signals:
        primary_signals.append(signal)
    supporting_signals = list(result.get("supporting_signals") or [])
    result["primary_signals"] = primary_signals
    result["supporting_signals"] = supporting_signals
    result["signals"] = list(dict.fromkeys([*primary_signals, *supporting_signals]))
    result["available"] = True
    result["change_detected"] = True
    result["reason"] = "change_detected"
    result["severity"] = max(
        float(result.get("severity", 0.0) or 0.0),
        float(history_diagnostics.get("same_config_feasibility_flip_rate", 0.0) or 0.0),
    )
    result["new_evaluator_calls"] = 0

    cutoff = _finite_float(history_diagnostics.get("_recent_cutoff_at"))
    geometry_cutoff = _finite_float(result.get("recent_oldest_at")) if geometry_changed else None
    if cutoff is not None and geometry_cutoff is not None:
        cutoff = max(cutoff, geometry_cutoff)
    elif cutoff is None:
        cutoff = geometry_cutoff

    recent_rows = [
        row
        for row in rows
        if cutoff is None or float(row["_drift_updated_at"]) >= cutoff
    ]
    result["recent_rows"] = len(recent_rows)
    result["history_rows"] = max(0, len(rows) - len(recent_rows))
    if recent_rows:
        result["recent_newest_at"] = float(recent_rows[0]["_drift_updated_at"])
        result["recent_oldest_at"] = float(recent_rows[-1]["_drift_updated_at"])
    result["_recent_observations"] = [
        {
            key: value
            for key, value in row.items()
            if not str(key).startswith("_drift_")
        }
        for row in recent_rows
    ]
    return result


def detect_optimizer_observation_drift(
    *,
    dimensions: Sequence[Any],
    observations: Iterable[Mapping[str, Any]],
    observation_history: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Find structural change using local score geometry and same-config history."""

    rows = _usable_rows(observations, dimensions)
    same_config_history = _same_config_feasibility_history_diagnostics(
        observation_history
    )
    minimum = 2 * OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS
    if len(rows) < minimum:
        return _merge_same_config_feasibility_history(
            {
                "available": False,
                "change_detected": False,
                "action": "none",
                "reason": "insufficient_rows",
                "usable_rows": len(rows),
                "candidate_splits": 0,
                "new_evaluator_calls": 0,
            },
            rows,
            same_config_history,
        )

    best: dict[str, Any] | None = None
    candidate_count = 0
    max_recent = min(
        OPTIMIZER_OBSERVATION_DRIFT_MAX_WINDOW_ROWS,
        len(rows) - OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS,
    )
    for split_index in range(
        OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS,
        max_recent + 1,
    ):
        if float(rows[split_index - 1]["_drift_updated_at"]) == float(
            rows[split_index]["_drift_updated_at"]
        ):
            continue
        history = rows[
            split_index : split_index + OPTIMIZER_OBSERVATION_DRIFT_MAX_WINDOW_ROWS
        ]
        if len(history) < OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS:
            continue

        recent = rows[:split_index]
        candidate_count += 1
        diagnostics = _candidate_diagnostics(recent, history, dimensions)
        candidate = {
            **diagnostics,
            "available": True,
            "reason": (
                "change_detected"
                if diagnostics["change_detected"]
                else "stable_geometry"
            ),
            "recent_rows": len(recent),
            "history_rows": len(history),
            "recent_newest_at": float(recent[0]["_drift_updated_at"]),
            "recent_oldest_at": float(recent[-1]["_drift_updated_at"]),
            "history_newest_at": float(history[0]["_drift_updated_at"]),
            "_recent_observations": [
                {
                    key: value
                    for key, value in row.items()
                    if not str(key).startswith("_drift_")
                }
                for row in recent
            ],
        }
        rank = (
            1 if candidate["change_detected"] else 0,
            float(candidate["severity"]),
            -len(recent),
            float(candidate["recent_oldest_at"]),
        )
        if best is None or rank > best["_rank"]:
            best = {**candidate, "_rank": rank}

    if best is None:
        return _merge_same_config_feasibility_history(
            {
                "available": False,
                "change_detected": False,
                "action": "none",
                "reason": "single_update_cohort",
                "usable_rows": len(rows),
                "candidate_splits": 0,
                "new_evaluator_calls": 0,
            },
            rows,
            same_config_history,
        )
    best["candidate_splits"] = candidate_count
    best["usable_rows"] = len(rows)
    best.pop("_rank", None)
    return _merge_same_config_feasibility_history(best, rows, same_config_history)


def _drift_states(registry: Any) -> dict[str, dict[str, Any]]:
    states = getattr(registry, _DRIFT_STATE_ATTR, None)
    if not isinstance(states, dict):
        states = {}
        setattr(registry, _DRIFT_STATE_ATTR, states)
    return states


def _public_diagnostics(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in diagnostics.items()
        if not str(key).startswith("_")
    }


def install_optimizer_observation_drift_guard(optimizer_registry_cls: type) -> None:
    """Quarantine stale routing geometry while preserving zero-evaluator authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_observation_drift_guard(self: Any, surface: str):
        context = original_routing_context(self, surface)
        states = _drift_states(self)
        reader = getattr(self.memory, "optimizer_observations", None)
        if not callable(reader):
            states[surface] = {
                "available": False,
                "change_detected": False,
                "action": "none",
                "reason": "observation_reader_unavailable",
                "new_evaluator_calls": 0,
            }
            return context

        observations = reader(self.catalog_key, surface)
        history_reader = getattr(self.memory, "optimizer_observation_history", None)
        observation_history = (
            history_reader(self.catalog_key, surface)
            if callable(history_reader)
            else []
        )
        engine = self.search if surface == "search" else self.recommend
        try:
            dimensions, _ = core._evolution_schema(engine.config)
            diagnostics = detect_optimizer_observation_drift(
                dimensions=dimensions,
                observations=observations,
                observation_history=observation_history,
            )
        except (TypeError, ValueError, KeyError):
            states[surface] = {
                "available": False,
                "change_detected": False,
                "action": "none",
                "reason": "drift_diagnostics_unavailable",
                "new_evaluator_calls": 0,
            }
            return context

        if not diagnostics.get("change_detected"):
            diagnostics["action"] = "none"
            states[surface] = _public_diagnostics(diagnostics)
            return context

        recent = list(diagnostics.get("_recent_observations") or [])
        recent_weighted = weighting.weight_optimizer_observations(
            recent,
            reference_time=time.time(),
        )
        recent_confidence = weighting.optimizer_observation_weight_diagnostics(
            recent_weighted
        )
        diagnostics["recent_confidence"] = recent_confidence

        recent_landscape = None
        if recent_confidence.get("enter_confident"):
            try:
                recent_landscape = describe_weighted_optimizer_landscape(
                    dimensions=dimensions,
                    observations=recent_weighted,
                )
            except (TypeError, ValueError, KeyError):
                recent_landscape = None

        regimes = weighting._routing_regimes(self)
        if recent_landscape is not None and recent_landscape.informative:
            regimes[surface] = weighting._ROUTING_REGIME_WEIGHTED
            diagnostics["action"] = "recent_only_weighted_geometry"
            states[surface] = _public_diagnostics(diagnostics)
            return replace(context, landscape=recent_landscape)

        regimes[surface] = weighting._ROUTING_REGIME_FALLBACK
        diagnostics["action"] = "pre_observation_fallback"
        states[surface] = _public_diagnostics(diagnostics)
        return weighting._pre_observation_context(self, surface, context)

    optimizer_registry_cls._routing_context = routing_context_with_observation_drift_guard

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_observation_drift(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        states = getattr(self, _DRIFT_STATE_ATTR, None)
        router.update(
            {
                "optimizer_observation_drift_detection": "temporal_change_point_local_geometry",
                "optimizer_observation_drift_min_window_rows": OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS,
                "optimizer_observation_drift_max_window_rows": OPTIMIZER_OBSERVATION_DRIFT_MAX_WINDOW_ROWS,
                "optimizer_observation_drift_max_match_distance": OPTIMIZER_OBSERVATION_DRIFT_MAX_MATCH_DISTANCE,
                "optimizer_observation_drift_min_match_coverage": OPTIMIZER_OBSERVATION_DRIFT_MIN_MATCH_COVERAGE,
                "optimizer_observation_drift_max_mean_match_distance": OPTIMIZER_OBSERVATION_DRIFT_MAX_MEAN_MATCH_DISTANCE,
                "optimizer_observation_drift_order_inversion_threshold": OPTIMIZER_OBSERVATION_DRIFT_ORDER_INVERSION_THRESHOLD,
                "optimizer_observation_drift_contrast_shift_threshold": OPTIMIZER_OBSERVATION_DRIFT_CONTRAST_SHIFT_THRESHOLD,
                "optimizer_observation_drift_feasibility_flip_threshold": OPTIMIZER_OBSERVATION_DRIFT_FEASIBILITY_FLIP_THRESHOLD,
                "optimizer_observation_drift_feasibility_density_delta": OPTIMIZER_OBSERVATION_DRIFT_FEASIBILITY_DENSITY_DELTA,
                "optimizer_observation_drift_primary_signals": "local_order_or_contrast",
                "optimizer_observation_drift_feasibility_role": "supporting_only_without_same_config_history",
                "optimizer_observation_drift_same_config_feasibility": "primary_with_basis_matched_history",
                "optimizer_observation_drift_same_config_min_pairs": OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS,
                "optimizer_observation_drift_action": "recent_only_if_entry_confident_else_pre_observation_fallback",
                "optimizer_observation_drift_states": (
                    dict(states) if isinstance(states, dict) else {}
                ),
                "optimizer_observation_drift_authority": "routing_descriptor_only",
                "optimizer_observation_drift_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_observation_drift
    _INSTALLED = True


__all__ = [
    "OPTIMIZER_OBSERVATION_DRIFT_CONTRAST_SHIFT_THRESHOLD",
    "OPTIMIZER_OBSERVATION_DRIFT_MAX_MATCH_DISTANCE",
    "OPTIMIZER_OBSERVATION_DRIFT_MAX_WINDOW_ROWS",
    "OPTIMIZER_OBSERVATION_DRIFT_MIN_MATCH_COVERAGE",
    "OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS",
    "detect_optimizer_observation_drift",
    "install_optimizer_observation_drift_guard",
]
