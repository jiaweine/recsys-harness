from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from . import evolution_core as core


SUPPORTED_OPTIMIZER_BACKENDS = ("native", "optuna", "optuna_motpe", "qlognehvi")
_OPTIMIZER_BACKEND: ContextVar[str] = ContextVar("xushu_optimizer_backend", default="native")
_QLOG_TELEMETRY: ContextVar[dict[str, Any] | None] = ContextVar(
    "xushu_qlog_telemetry", default=None
)
_ORIGINAL_EVOLUTION_LOOP = core._evolution_loop
_INSTALLED = False
_NATIVE_ARCHIVE_PARENT_LIMIT = 5


def _load_optuna():
    try:
        import optuna
    except ImportError as exc:
        raise RuntimeError(
            "Optuna optimizer backends require the optional optimizer dependencies; "
            "install with `pip install -e '.[optimizer]'`"
        ) from exc
    return optuna


def _load_qlog():
    from .qlog_mobo import load_botorch_stack

    return load_botorch_stack()


def _normalize_backend(name: str | None) -> str:
    backend = str(name or "native").strip().lower()
    if backend not in SUPPORTED_OPTIMIZER_BACKENDS:
        raise ValueError(
            f"unknown optimizer backend: {backend}; expected one of "
            f"{', '.join(SUPPORTED_OPTIMIZER_BACKENDS)}"
        )
    return backend


@contextmanager
def optimizer_backend(name: str | None) -> Iterator[str]:
    """Select one optimizer for this evolution call without global cross-talk."""

    backend = _normalize_backend(name)
    if backend.startswith("optuna"):
        # Fail before spending any response-surface evaluation budget.
        _load_optuna()
    elif backend == "qlognehvi":
        # The research stack is optional and must fail before any evaluator work.
        _load_qlog()
    backend_token = _OPTIMIZER_BACKEND.set(backend)
    telemetry_token = _QLOG_TELEMETRY.set(None) if backend == "qlognehvi" else None
    try:
        yield backend
    finally:
        _OPTIMIZER_BACKEND.reset(backend_token)
        if telemetry_token is not None:
            _QLOG_TELEMETRY.reset(telemetry_token)


def current_optimizer_backend() -> str:
    return _OPTIMIZER_BACKEND.get()


def _trial_params(config: dict[str, Any], dimensions: list[core.EvolutionDimension]) -> dict[str, Any]:
    return {dimension.name: config[dimension.name] for dimension in dimensions}


def _native_distinct_evaluation_budget(
    population: list[dict[str, Any]],
    cache: dict[tuple[tuple[str, Any], ...], dict[str, Any]] | None,
) -> int:
    """Mirror the native loop's upper bound on new black-box evaluations.

    The first generation reuses cached response-surface seeds. Later generations
    keep up to five quality-diversity archive parents and refill the remaining
    population slots. The budget is therefore expressed in *new distinct evaluator
    calls*, not sampler trials, so an external optimizer cannot gain quality by
    silently spending more expensive evaluations than the native loop contract.
    """

    if core.MAX_GENERATIONS <= 0 or core.POPULATION_SIZE <= 0:
        return 0
    cached = set((cache or {}).keys())
    first_population = core._unique_configs(population)[: core.POPULATION_SIZE]
    first_generation_new = sum(
        1 for config in first_population if core._config_key(config) not in cached
    )
    retained_parents = min(_NATIVE_ARCHIVE_PARENT_LIMIT, core.POPULATION_SIZE)
    refill_slots = max(0, core.POPULATION_SIZE - retained_parents)
    return first_generation_new + max(0, core.MAX_GENERATIONS - 1) * refill_slots


def _suggest_config(
    trial: Any,
    *,
    base_config: dict[str, Any],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
) -> dict[str, Any]:
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
    return core._project(raw, dimensions, group_totals)


def _evaluate_once(
    config: dict[str, Any],
    *,
    evaluated: dict[tuple[tuple[str, Any], ...], dict[str, Any]],
    evaluate: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, float], float]],
    source: str,
) -> tuple[dict[str, Any], bool]:
    key = core._config_key(config)
    if key in evaluated:
        return evaluated[key], False
    report, robust, score = evaluate(config)
    row = {
        "config": config,
        "report": report,
        "robustness": robust,
        "objective": round(float(score), 7),
        "generation": 0,
        "source": source,
    }
    evaluated[key] = row
    return row, True


def _motpe_values(row: dict[str, Any]) -> tuple[float, float, float]:
    """Return generic Pareto signals without changing final promotion semantics.

    The first objective remains the harness-owned scalar routing objective so the
    selected candidate and trust lifecycle stay backward compatible. The second
    preserves domain quality as an independent optimization signal instead of
    letting business/proxy scalarization erase it. The third rewards robustness
    by minimizing the fraction of materially worse evaluation identities.
    """

    report = row.get("report") if isinstance(row.get("report"), dict) else {}
    robust = row.get("robustness") if isinstance(row.get("robustness"), dict) else {}
    primary = float(row.get("objective", 0.0) or 0.0)
    domain_quality = float(
        report.get(
            "quality",
            report.get("business_reward", report.get("recall", report.get("coverage", 0.0))),
        )
        or 0.0
    )
    negative_worse_share = -float(robust.get("worse_share", 0.0) or 0.0)
    return primary, domain_quality, negative_worse_share


def _warm_start_study(
    study: Any,
    *,
    base_config: dict[str, Any],
    population: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
) -> list[dict[str, Any]]:
    warm_starts = core._unique_configs([dict(base_config), *population])
    accepted: list[dict[str, Any]] = []
    for config in warm_starts:
        try:
            projected = core._project(config, dimensions, group_totals)
            study.enqueue_trial(
                _trial_params(projected, dimensions),
                skip_if_exists=True,
            )
            accepted.append(projected)
        except (TypeError, ValueError, KeyError):
            continue
    return accepted


def _trial_budget(warm_start_count: int, evaluation_budget: int) -> int:
    # Cached/enqueued trials are cheap evidence reuse and must not consume the
    # black-box evaluation budget. Allow extra sampler attempts for projected
    # duplicates, but stop as soon as the native-equivalent distinct budget is hit.
    return warm_start_count + max(
        core.POPULATION_SIZE * core.MAX_GENERATIONS,
        evaluation_budget * 4,
    )


def _optuna_evolution_loop(
    *,
    base_config: dict[str, Any],
    population: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
    evaluate: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, float], float]],
    rng: Any,
    cache: dict[tuple[tuple[str, Any], ...], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Delegate scalar black-box population search to Optuna's official TPE."""

    optuna = _load_optuna()
    evaluated = dict(cache or {})
    evaluation_budget = _native_distinct_evaluation_budget(population, evaluated)
    if evaluation_budget <= 0:
        rows = sorted(
            evaluated.values(),
            key=lambda row: (-float(row["objective"]), core._config_key(row["config"])),
        )
        return rows, core._quality_diversity_archive(base_config, rows, dimensions)

    seed = int(rng.randrange(0, 2**31 - 1))
    sampler = optuna.samplers.TPESampler(seed=seed, multivariate=True)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    warm_starts = _warm_start_study(
        study,
        base_config=base_config,
        population=population,
        dimensions=dimensions,
        group_totals=group_totals,
    )
    new_evaluations = 0

    def objective(trial: Any) -> float:
        nonlocal new_evaluations
        config = _suggest_config(
            trial,
            base_config=base_config,
            dimensions=dimensions,
            group_totals=group_totals,
        )
        key = core._config_key(config)
        if key not in evaluated and new_evaluations >= evaluation_budget:
            raise optuna.TrialPruned("distinct evaluation budget exhausted")
        row, is_new = _evaluate_once(
            config,
            evaluated=evaluated,
            evaluate=evaluate,
            source="optuna_tpe",
        )
        if is_new:
            new_evaluations += 1
        return float(row["objective"])

    def stop_at_budget(study_: Any, trial: Any) -> None:
        del trial
        if new_evaluations >= evaluation_budget:
            study_.stop()

    study.optimize(
        objective,
        n_trials=_trial_budget(len(warm_starts), evaluation_budget),
        n_jobs=1,
        show_progress_bar=False,
        catch=(TypeError, ValueError, KeyError),
        callbacks=[stop_at_budget],
    )

    rows = sorted(
        evaluated.values(),
        key=lambda row: (-float(row["objective"]), core._config_key(row["config"])),
    )
    return rows, core._quality_diversity_archive(base_config, rows, dimensions)


def _optuna_motpe_evolution_loop(
    *,
    base_config: dict[str, Any],
    population: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
    evaluate: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, float], float]],
    rng: Any,
    cache: dict[tuple[tuple[str, Any], ...], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use Optuna MOTPE to propose Pareto-aware candidates under the same budget.

    Optuna's multi-objective TPE uses hypervolume contribution when constructing
    the good set. Xushu deliberately keeps final candidate ordering by the original
    harness objective, and keeps holdout/trust gates outside the optimizer. MOTPE
    therefore improves *proposal search* without granting the optimizer promotion
    authority or changing the expensive-evaluation budget contract.
    """

    optuna = _load_optuna()
    evaluated = dict(cache or {})
    evaluation_budget = _native_distinct_evaluation_budget(population, evaluated)
    if evaluation_budget <= 0:
        rows = sorted(
            evaluated.values(),
            key=lambda row: (-float(row["objective"]), core._config_key(row["config"])),
        )
        return rows, core._quality_diversity_archive(base_config, rows, dimensions)

    seed = int(rng.randrange(0, 2**31 - 1))
    sampler = optuna.samplers.TPESampler(seed=seed, multivariate=True)
    study = optuna.create_study(
        directions=["maximize", "maximize", "maximize"],
        sampler=sampler,
    )
    warm_starts = _warm_start_study(
        study,
        base_config=base_config,
        population=population,
        dimensions=dimensions,
        group_totals=group_totals,
    )
    new_evaluations = 0

    def objective(trial: Any) -> tuple[float, float, float]:
        nonlocal new_evaluations
        config = _suggest_config(
            trial,
            base_config=base_config,
            dimensions=dimensions,
            group_totals=group_totals,
        )
        key = core._config_key(config)
        if key not in evaluated and new_evaluations >= evaluation_budget:
            raise optuna.TrialPruned("distinct evaluation budget exhausted")
        row, is_new = _evaluate_once(
            config,
            evaluated=evaluated,
            evaluate=evaluate,
            source="optuna_motpe",
        )
        if is_new:
            new_evaluations += 1
        return _motpe_values(row)

    def stop_at_budget(study_: Any, trial: Any) -> None:
        del trial
        if new_evaluations >= evaluation_budget:
            study_.stop()

    study.optimize(
        objective,
        n_trials=_trial_budget(len(warm_starts), evaluation_budget),
        n_jobs=1,
        show_progress_bar=False,
        catch=(TypeError, ValueError, KeyError),
        callbacks=[stop_at_budget],
    )

    # Search is Pareto-aware, but the harness-owned scalar remains the incumbent
    # selection contract so historical comparisons and trust semantics do not drift.
    rows = sorted(
        evaluated.values(),
        key=lambda row: (-float(row["objective"]), core._config_key(row["config"])),
    )
    return rows, core._quality_diversity_archive(base_config, rows, dimensions)


def _qlog_evolution_loop(**kwargs: Any):
    from .qlog_mobo import qlognehvi_evolution_loop

    evaluated = dict(kwargs.get("cache") or {})
    evaluation_budget = _native_distinct_evaluation_budget(
        kwargs.get("population") or [], evaluated
    )
    rows, archive = qlognehvi_evolution_loop(
        **kwargs,
        evaluation_budget=evaluation_budget,
        stack=_load_qlog(),
    )
    telemetry = None
    if rows and isinstance(rows[0], dict):
        raw = rows[0].get("optimizer_provenance")
        if isinstance(raw, dict):
            telemetry = dict(raw)
    _QLOG_TELEMETRY.set(telemetry)
    return rows, archive


def _routed_evolution_loop(**kwargs: Any):
    backend = current_optimizer_backend()
    if backend == "native":
        return _ORIGINAL_EVOLUTION_LOOP(**kwargs)
    if backend == "optuna":
        return _optuna_evolution_loop(**kwargs)
    if backend == "optuna_motpe":
        return _optuna_motpe_evolution_loop(**kwargs)
    if backend == "qlognehvi":
        return _qlog_evolution_loop(**kwargs)
    raise AssertionError(f"unsupported optimizer backend after validation: {backend}")


def install_optimizer_router() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    core._evolution_loop = _routed_evolution_loop
    _INSTALLED = True


def annotate_optimizer_backend(result: dict[str, Any], backend: str) -> dict[str, Any]:
    """Make optimizer provenance explicit without changing trust semantics."""

    backend = _normalize_backend(backend)
    if backend == "native":
        return result

    if backend == "qlognehvi":
        stack = _load_qlog()
        qlog_telemetry = dict(_QLOG_TELEMETRY.get() or {})
        library_name = "botorch"
        library_version = str(getattr(stack.botorch, "__version__", "unknown"))
    else:
        optuna = _load_optuna()
        qlog_telemetry = {}
        library_name = "optuna"
        library_version = str(getattr(optuna, "__version__", "unknown"))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            evolution = node.get("evolution")
            if isinstance(evolution, dict) and evolution.get("method") == "mixed_genome_response_surface":
                updated = dict(evolution)
                surface_count = len(updated.get("response_surface") or [])
                candidate_count = int(node.get("candidate_count", 0) or 0)
                if backend == "optuna_motpe":
                    method = "optuna_motpe_with_evidence_response_surface"
                    router = "optuna.samplers.TPESampler(multi-objective)"
                    extra = {
                        "optimizer_objectives": [
                            "primary_objective",
                            "domain_quality",
                            "negative_worse_share",
                        ],
                        "pareto_search": True,
                        "final_selection": "harness_primary_objective",
                    }
                    new_evaluations = max(0, candidate_count - surface_count)
                elif backend == "qlognehvi":
                    method = "constrained_qlognehvi_with_evidence_response_surface"
                    router = "botorch.qLogNoisyExpectedHypervolumeImprovement"
                    contract = qlog_telemetry.get("contract") or {}
                    extra = {
                        "optimizer_objectives": list(contract.get("objectives") or [
                            "primary_objective",
                            "domain_quality",
                        ]),
                        "optimizer_outcome_constraints": list(
                            contract.get("constraints") or []
                        ),
                        "optimizer_evidence_route": contract.get("evidence_route"),
                        "pareto_search": True,
                        "noisy_multiobjective": True,
                        "mixed_space_model": qlog_telemetry.get("model"),
                        "acquisition": qlog_telemetry.get("acquisition"),
                        "final_selection": "harness_primary_objective",
                        "optimizer_provenance": qlog_telemetry,
                    }
                    new_evaluations = int(
                        qlog_telemetry.get(
                            "new_evaluations",
                            max(0, candidate_count - surface_count),
                        )
                        or 0
                    )
                else:
                    method = "optuna_tpe_with_evidence_response_surface"
                    router = "optuna.samplers.TPESampler"
                    extra = {"pareto_search": False}
                    new_evaluations = max(0, candidate_count - surface_count)
                updated.update(
                    {
                        "method": method,
                        "router": router,
                        "optimizer_backend": backend,
                        "optimizer_library": library_name,
                        "optimizer_version": library_version,
                        "optimizer_warm_start": "current+response_surface+trusted_memory",
                        "optimizer_budget_contract": "native_distinct_evaluator_calls",
                        "optimizer_new_evaluations": new_evaluations,
                        **extra,
                    }
                )
                node["evolution"] = updated
                if "generations" in node:
                    node["generations"] = 0
            for key, value in list(node.items()):
                if key != "evolution":
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


install_optimizer_router()


__all__ = [
    "SUPPORTED_OPTIMIZER_BACKENDS",
    "optimizer_backend",
    "current_optimizer_backend",
    "install_optimizer_router",
    "annotate_optimizer_backend",
]
