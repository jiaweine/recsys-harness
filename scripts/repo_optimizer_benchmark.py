from __future__ import annotations

import json
from dataclasses import asdict

import optuna

from lingjing_harness.algorithms import SearchConfig, SearchEngine, evolve_search
from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.capabilities import normalize_strategy_config
from lingjing_harness.sample_data import build_sample_catalog


def main() -> None:
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    catalog = build_sample_catalog()
    current = SearchEngine(catalog)

    native = evolve_search(catalog, current)
    budget = max(1, int(native.get("candidate_count", 1)))

    labels = core._stable_limit(list(catalog.query_labels), lambda row: row.query)
    discovery_labels, holdout_labels = core._stable_split(labels, lambda row: row.query)
    base_config = asdict(current.config)
    dimensions, group_totals = core._evolution_schema(current.config)
    reference_discovery = core._audit_search_config(
        catalog,
        current,
        discovery_labels,
        current.config,
    )

    def evaluate_config(raw: dict) -> float:
        projected = core._project(raw, dimensions, group_totals)
        config = normalize_strategy_config(SearchConfig(**projected))
        report = core._audit_search_config(catalog, current, discovery_labels, config)
        robust = core._search_robustness(reference_discovery, report)
        return core._search_objective(report, robust)

    def objective(trial: optuna.Trial) -> float:
        raw = dict(base_config)
        for dimension in dimensions:
            if dimension.kind == "continuous":
                raw[dimension.name] = trial.suggest_float(
                    dimension.name,
                    dimension.low,
                    dimension.high,
                )
            else:
                raw[dimension.name] = trial.suggest_categorical(
                    dimension.name,
                    list(dimension.choices),
                )
        return evaluate_config(raw)

    sampler = optuna.samplers.TPESampler(seed=42, multivariate=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=budget, show_progress_bar=False)

    optuna_raw = dict(base_config)
    optuna_raw.update(study.best_trial.params)
    optuna_config = normalize_strategy_config(
        SearchConfig(**core._project(optuna_raw, dimensions, group_totals))
    )
    optuna_full = core._audit_search_config(catalog, current, labels, optuna_config)
    optuna_holdout = core._audit_search_config(
        catalog,
        current,
        holdout_labels,
        optuna_config,
    ) if holdout_labels else None

    native_config = normalize_strategy_config(SearchConfig(**native["candidate_config"]))
    native_full = core._audit_search_config(catalog, current, labels, native_config)
    native_holdout = core._audit_search_config(
        catalog,
        current,
        holdout_labels,
        native_config,
    ) if holdout_labels else None

    report = {
        "evaluation_budget": budget,
        "native": {
            "method": native.get("evolution", {}).get("method"),
            "objective_delta": native.get("objective_delta", 0.0),
            "full_ndcg": native_full.get("quality", 0.0),
            "full_recall": native_full.get("recall", 0.0),
            "holdout_ndcg": (native_holdout or {}).get("quality", 0.0),
            "holdout_recall": (native_holdout or {}).get("recall", 0.0),
            "config": asdict(native_config),
        },
        "optuna_tpe": {
            "version": optuna.__version__,
            "best_discovery_objective": round(float(study.best_value), 7),
            "full_ndcg": optuna_full.get("quality", 0.0),
            "full_recall": optuna_full.get("recall", 0.0),
            "holdout_ndcg": (optuna_holdout or {}).get("quality", 0.0),
            "holdout_recall": (optuna_holdout or {}).get("recall", 0.0),
            "config": asdict(optuna_config),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
