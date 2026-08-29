from __future__ import annotations

import json

from lingjing_harness.algorithms import (
    RecommendationEngine,
    SearchEngine,
    evolve_recommend,
    evolve_search,
)
from lingjing_harness.runtime import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def _summary(result: dict) -> dict:
    evolution = result.get("evolution") or {}
    candidate_count = int(result.get("candidate_count", 0))
    surface_count = len(evolution.get("response_surface") or [])
    return {
        "evaluation_ready": bool(result.get("evaluation_ready")),
        "safe_to_try": bool(result.get("safe_to_try")),
        "trusted": bool(result.get("trusted")),
        "objective_delta": float(result.get("objective_delta", 0.0)),
        "candidate_count": candidate_count,
        "response_surface_candidates": surface_count,
        "loop_distinct_candidates": max(0, candidate_count - surface_count),
        "optimizer_backend": evolution.get("optimizer_backend") or "native",
        "optimizer_library": evolution.get("optimizer_library"),
        "optimizer_version": evolution.get("optimizer_version"),
        "method": evolution.get("method"),
    }


def main() -> None:
    catalog = build_sample_catalog()

    native_search = evolve_search(catalog, SearchEngine(catalog))
    native_recommend = evolve_recommend(catalog, RecommendationEngine(catalog))

    search_first = evolve_search(
        catalog,
        SearchEngine(catalog),
        optimizer_backend="optuna",
    )
    search_second = evolve_search(
        catalog,
        SearchEngine(catalog),
        optimizer_backend="optuna",
    )
    recommend = evolve_recommend(
        catalog,
        RecommendationEngine(catalog),
        optimizer_backend="optuna",
    )

    for result in (search_first, search_second, recommend):
        evolution = result.get("evolution") or {}
        assert result.get("evaluation_ready") is True
        assert int(result.get("candidate_count", 0)) > 0
        assert evolution.get("optimizer_backend") == "optuna"
        assert evolution.get("optimizer_library") == "optuna"
        assert evolution.get("method") == "optuna_tpe_with_evidence_response_surface"

    # Seeded TPE + serial trial execution should preserve reproducibility for the
    # same catalog/evaluator contract.
    assert search_first["candidate_config"] == search_second["candidate_config"]
    assert search_first["objective_delta"] == search_second["objective_delta"]

    registry = OptimizerToolRegistry(catalog, optimizer_backend="optuna")
    registry_result = registry.search_evolve(activate=False)
    assert (registry_result.get("evolution") or {}).get("optimizer_backend") == "optuna"
    assert registry_result.get("activated") is False

    print(
        json.dumps(
            {
                "native_search": _summary(native_search),
                "native_recommend": _summary(native_recommend),
                "optuna_search": _summary(search_first),
                "optuna_recommend": _summary(recommend),
                "registry_search": _summary(registry_result),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
