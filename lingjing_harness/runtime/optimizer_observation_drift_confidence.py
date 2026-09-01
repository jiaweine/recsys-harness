from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import optimizer_observation_drift as drift


OPTIMIZER_OBSERVATION_DRIFT_LOCAL_WINDOW_ROWS = (
    drift.OPTIMIZER_OBSERVATION_DRIFT_MIN_WINDOW_ROWS
)
OPTIMIZER_OBSERVATION_DRIFT_WINDOW_SCOPE = "candidate_boundary_local"
OPTIMIZER_OBSERVATION_DRIFT_CONTRAST_CONFIDENCE = (
    "leave_one_match_out_unanimous"
)
_INSTALLED = False


def _without_signal(values: Sequence[str], signal: str) -> list[str]:
    return [value for value in values if value != signal]


def _confirmed_contrast(
    *,
    matches: Sequence[tuple[Mapping[str, Any], Mapping[str, Any], float]],
    dimensions: Sequence[Any],
    original_diagnostics,
) -> tuple[int, int, float, bool]:
    required = OPTIMIZER_OBSERVATION_DRIFT_LOCAL_WINDOW_ROWS
    if len(matches) < required:
        return 0, 0, 0.0, False

    checks = 0
    supported = 0
    for dropped_index in range(len(matches)):
        recent = [
            recent_row
            for index, (recent_row, _, _) in enumerate(matches)
            if index != dropped_index
        ]
        history = [
            history_row
            for index, (_, history_row, _) in enumerate(matches)
            if index != dropped_index
        ]
        probe = original_diagnostics(recent, history, dimensions)
        checks += 1
        if "local_contrast_shift" in list(probe.get("primary_signals") or []):
            supported += 1

    support = supported / checks if checks else 0.0
    return checks, supported, support, bool(checks and supported == checks)


def install_optimizer_observation_drift_confidence(
    optimizer_registry_cls: type,
) -> None:
    """Localize change-point evidence and require stable contrast geometry."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_diagnostics = drift._candidate_diagnostics

    def candidate_diagnostics_with_confidence(
        recent: Sequence[Mapping[str, Any]],
        history: Sequence[Mapping[str, Any]],
        dimensions: Sequence[Any],
    ) -> dict[str, Any]:
        # A candidate split is a boundary, not a license to mix every newer cohort
        # into one matching pool. Compare the observations immediately on each side
        # so an older split cannot manufacture geometry by pairing rows from several
        # routing regimes.
        local_recent = list(recent[-OPTIMIZER_OBSERVATION_DRIFT_LOCAL_WINDOW_ROWS:])
        local_history = list(history[:OPTIMIZER_OBSERVATION_DRIFT_LOCAL_WINDOW_ROWS])
        result = dict(original_diagnostics(local_recent, local_history, dimensions))
        result.update(
            {
                "diagnostic_window_scope": OPTIMIZER_OBSERVATION_DRIFT_WINDOW_SCOPE,
                "diagnostic_recent_rows": len(local_recent),
                "diagnostic_history_rows": len(local_history),
                "contrast_confidence_method": OPTIMIZER_OBSERVATION_DRIFT_CONTRAST_CONFIDENCE,
            }
        )

        primary_signals = list(result.get("primary_signals") or [])
        if "local_contrast_shift" not in primary_signals:
            result["contrast_leave_one_out_checks"] = 0
            result["contrast_leave_one_out_supported"] = 0
            result["contrast_leave_one_out_support"] = None
            result["contrast_confident"] = None
            return result

        matches = drift._greedy_matches(local_recent, local_history, dimensions)
        checks, supported, support, confident = _confirmed_contrast(
            matches=matches,
            dimensions=dimensions,
            original_diagnostics=original_diagnostics,
        )
        result["contrast_leave_one_out_checks"] = checks
        result["contrast_leave_one_out_supported"] = supported
        result["contrast_leave_one_out_support"] = support
        result["contrast_confident"] = confident
        if confident:
            return result

        primary_signals = _without_signal(primary_signals, "local_contrast_shift")
        result["primary_signals"] = primary_signals
        result["signals"] = _without_signal(
            list(result.get("signals") or []),
            "local_contrast_shift",
        )
        result["change_detected"] = bool(primary_signals)
        result["severity"] = (
            float(result.get("order_inversion_rate", 0.0) or 0.0)
            if "local_order_inversion" in primary_signals
            else 0.0
        )
        return result

    drift._candidate_diagnostics = candidate_diagnostics_with_confidence

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_drift_confidence(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        router.update(
            {
                "optimizer_observation_drift_window_scope": OPTIMIZER_OBSERVATION_DRIFT_WINDOW_SCOPE,
                "optimizer_observation_drift_local_window_rows": OPTIMIZER_OBSERVATION_DRIFT_LOCAL_WINDOW_ROWS,
                "optimizer_observation_drift_contrast_confidence": OPTIMIZER_OBSERVATION_DRIFT_CONTRAST_CONFIDENCE,
                "optimizer_observation_drift_contrast_confidence_authority": "routing_descriptor_only",
                "optimizer_observation_drift_contrast_confidence_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_drift_confidence
    _INSTALLED = True


__all__ = [
    "OPTIMIZER_OBSERVATION_DRIFT_CONTRAST_CONFIDENCE",
    "OPTIMIZER_OBSERVATION_DRIFT_LOCAL_WINDOW_ROWS",
    "OPTIMIZER_OBSERVATION_DRIFT_WINDOW_SCOPE",
    "install_optimizer_observation_drift_confidence",
]
