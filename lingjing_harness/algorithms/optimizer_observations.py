from __future__ import annotations

from contextvars import ContextVar
from math import isfinite
from typing import Any, Iterable

from . import evolution_core as core


_CAPTURED_ROWS: ContextVar[tuple[dict[str, Any], ...]] = ContextVar(
    "xushu_optimizer_observation_rows",
    default=(),
)
_ORIGINAL_EVOLUTION_LOOP: Any = None
_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _discovery_feasibility(row: dict[str, Any]) -> tuple[bool, str] | None:
    """Label only constraints already evaluated on the discovery slice.

    This deliberately does not claim holdout safety or promotion authority. The
    label is a geometry signal for the meta-router: enough domain evidence plus
    the same per-identity robustness limits already owned by core evolution.
    """

    report = row.get("report") if isinstance(row.get("report"), dict) else {}
    robust = row.get("robustness") if isinstance(row.get("robustness"), dict) else {}
    worse_share = _finite_float(robust.get("worse_share"))
    worst_delta = _finite_float(robust.get("worst_delta"))
    if worse_share is None or worst_delta is None:
        return None

    if "queries" in report:
        try:
            evidence = int(report.get("queries", 0) or 0)
        except (TypeError, ValueError):
            return None
        return (
            evidence >= core.MIN_SEARCH_EVIDENCE
            and worse_share <= 0.34
            and worst_delta >= -0.35,
            "search_discovery_robustness_guardrails_v1",
        )

    if "users" in report:
        try:
            evidence = int(report.get("users", 0) or 0)
        except (TypeError, ValueError):
            return None
        return (
            evidence >= core.MIN_RECOMMEND_EVIDENCE
            and worse_share <= 0.40
            and worst_delta >= -0.30,
            "recommend_discovery_robustness_guardrails_v1",
        )
    return None


def evaluated_optimizer_observations(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a compact trace of rows whose expensive evaluator already ran."""

    observations: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        config = row.get("config")
        objective = _finite_float(row.get("objective"))
        feasibility = _discovery_feasibility(row)
        if not isinstance(config, dict) or objective is None or feasibility is None:
            continue
        feasible, basis = feasibility
        robust = row.get("robustness") if isinstance(row.get("robustness"), dict) else {}
        observations.append(
            {
                "config": dict(config),
                "objective": round(objective, 7),
                "feasible": bool(feasible),
                "source": str(row.get("source") or "optimizer_evaluator"),
                "generation": max(0, int(row.get("generation", 0) or 0)),
                "feasibility_basis": basis,
                "constraints": {
                    "worse_share": _finite_float(robust.get("worse_share")),
                    "worst_delta": _finite_float(robust.get("worst_delta")),
                },
            }
        )
    return observations


def captured_optimizer_observations() -> list[dict[str, Any]]:
    return [dict(row) for row in _CAPTURED_ROWS.get()]


def consume_optimizer_observations() -> list[dict[str, Any]]:
    rows = captured_optimizer_observations()
    _CAPTURED_ROWS.set(())
    return rows


def install_optimizer_observation_capture() -> None:
    """Wrap the already-routed optimizer loop once, without adding evaluations."""

    global _INSTALLED, _ORIGINAL_EVOLUTION_LOOP
    if _INSTALLED:
        return
    _ORIGINAL_EVOLUTION_LOOP = core._evolution_loop

    def captured_loop(*args: Any, **kwargs: Any):
        rows, archive = _ORIGINAL_EVOLUTION_LOOP(*args, **kwargs)
        _CAPTURED_ROWS.set(tuple(evaluated_optimizer_observations(rows)))
        return rows, archive

    core._evolution_loop = captured_loop
    _INSTALLED = True


__all__ = [
    "captured_optimizer_observations",
    "consume_optimizer_observations",
    "evaluated_optimizer_observations",
    "install_optimizer_observation_capture",
]
