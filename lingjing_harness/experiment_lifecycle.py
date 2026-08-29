from __future__ import annotations

from hashlib import blake2b
import json
from typing import Any, Mapping, TYPE_CHECKING

from .experiments import evaluate_counterfactual_experiment

if TYPE_CHECKING:
    from .domain import Catalog


def strategy_policy_id(
    surface: str,
    config: Mapping[str, Any],
    *,
    backend_scope: str = "",
) -> str:
    """Create a stable identity for one executable candidate policy.

    Policy identity includes the serving-backend scope because the same Harness
    weights evaluated behind different retrieval/collaborative backends are not
    the same target policy. The dependency-light reference runtime uses an empty
    scope, preserving a stable identity for existing integrations.
    """

    normalized_surface = str(surface or "").strip().lower()
    if normalized_surface not in {"search", "recommend"}:
        raise ValueError("surface must be search or recommend")
    if not isinstance(config, Mapping):
        raise ValueError("strategy config must be an object")
    payload = {
        "surface": normalized_surface,
        "config": {str(key): value for key, value in config.items()},
        "backend_scope": str(backend_scope or ""),
    }
    try:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("strategy config must contain JSON-compatible values") from exc
    digest = blake2b(raw.encode("utf-8"), digest_size=12).hexdigest()
    return f"{normalized_surface}-strategy-{digest}"


def evaluate_candidate_experiment(
    catalog: "Catalog",
    surface: str,
    config: Mapping[str, Any],
    *,
    backend_scope: str = "",
) -> dict[str, Any]:
    """Evaluate explicit OPE evidence for exactly one candidate policy.

    No propensity is synthesized from production exposures, rank, score, or model
    output. A candidate participates only when the imported workspace contains an
    ExperimentSpec whose candidate_policy_id exactly matches the deterministic
    policy identity below. Passing OPE advances only to controlled-online-test
    eligibility; activation authority is handled separately by the runtime.
    """

    candidate_policy_id = strategy_policy_id(
        surface,
        config,
        backend_scope=backend_scope,
    )
    experiments = [
        spec
        for spec in getattr(catalog, "experiments", [])
        if spec.surface == surface and spec.candidate_policy_id == candidate_policy_id
    ]
    if not experiments:
        return {
            "contract_present": False,
            "candidate_policy_id": candidate_policy_id,
            "available": False,
            "reason": "no_matching_experiment_contract",
        }
    if len(experiments) != 1:
        raise ValueError("candidate policy must have exactly one experiment contract")

    spec = experiments[0]
    records = [
        row
        for row in getattr(catalog, "counterfactual_records", [])
        if row.surface == spec.surface
        and row.logging_policy_id == spec.logging_policy_id
        and row.target_policy_id == spec.candidate_policy_id
    ]
    evaluation = evaluate_counterfactual_experiment(records, spec)
    return {
        "contract_present": True,
        "candidate_policy_id": candidate_policy_id,
        "experiment_id": spec.experiment_id,
        "logging_policy_id": spec.logging_policy_id,
        "primary_estimator": spec.primary_estimator,
        "records": len(records),
        "available": bool(
            (evaluation.get("counterfactual_evaluation") or {}).get("available")
        ),
        "evaluation": evaluation,
    }


__all__ = ["strategy_policy_id", "evaluate_candidate_experiment"]
