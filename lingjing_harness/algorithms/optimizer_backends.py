from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from . import evolution_core as core


SUPPORTED_OPTIMIZER_BACKENDS = ("native", "optuna")
_OPTIMIZER_BACKEND: ContextVar[str] = ContextVar("xushu_optimizer_backend", default="native")
_ORIGINAL_EVOLUTION_LOOP = core._evolution_loop
_INSTALLED = False
_NATIVE_ARCHIVE_PARENT_LIMIT = 5


def _load_optuna():
    try:
        import optuna
    except ImportError as exc:
        raise RuntimeError(
            "optimizer_backend='optuna' requires the optional optimizer dependencies; "
            "install with `pip install -e '.[optimizer]'`"
        ) from exc
    return optuna


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
    if backend == "optuna":
        # Fail before spending any response-surface evaluation budget.
        _load_optuna()
    token = _OPTIMIZER_BACKEND.set(backend)
    try:
        yield backend
    finally:
        _OPTIMIZER_BACKEND.reset(token)


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
    """Delegate black-box population search to Optuna's official TPE sampler.

    Harness keeps the typed genome, exact projection, response-surface evidence,
    trusted-memory warm starts, evaluator, holdout, trust gates, and evaluation
    budget. Optuna owns generic trial suggestion and study optimization.
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
    study = optuna.create_study(direction="maximize", sampler=sampler)

    warm_starts = core._unique_configs([dict(base_config), *population])
    for config in warm_starts:
        try:
            projected = core._project(config, dimensions, group_totals)
            study.enqueue_trial(
                _trial_params(projected, dimensions),
                skip_if_exists=True,
            )
        except (TypeError, ValueError, KeyError):
            continue

    # Cached/enqueued trials are cheap evidence reuse and must not consume the
    # black-box evaluation budget. Allow extra sampler attempts for projected
    # duplicates, but stop as soon as the native-equivalent distinct budget is hit.
    trial_budget = len(warm_starts) + max(
        core.POPULATION_SIZE * core.MAX_GENERATIONS,
        evaluation_budget * 4,
    )
    new_evaluations = 0

    def objective(trial: Any) -> float:
        nonlocal new_evaluations
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
        config = core._project(raw, dimensions, group_totals)
        key = core._config_key(config)
        if key not in evaluated:
            if new_evaluations >= evaluation_budget:
                raise optuna.TrialPruned("distinct evaluation budget exhausted")
            report, robust, score = evaluate(config)
            evaluated[key] = {
                "config": config,
                "report": report,
                "robustness": robust,
                "objective": round(float(score), 7),
                "generation": 0,
                "source": "optuna_tpe",
            }
            new_evaluations += 1
        return float(evaluated[key]["objective"])

    def stop_at_budget(study_: Any, trial: Any) -> None:
        del trial
        if new_evaluations >= evaluation_budget:
            study_.stop()

    study.optimize(
        objective,
        n_trials=trial_budget,
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


def _routed_evolution_loop(**kwargs: Any):
    backend = current_optimizer_backend()
    if backend == "native":
        return _ORIGINAL_EVOLUTION_LOOP(**kwargs)
    if backend == "optuna":
        return _optuna_evolution_loop(**kwargs)
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

    optuna = _load_optuna()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            evolution = node.get("evolution")
            if isinstance(evolution, dict) and evolution.get("method") == "mixed_genome_response_surface":
                updated = dict(evolution)
                surface_count = len(updated.get("response_surface") or [])
                candidate_count = int(node.get("candidate_count", 0) or 0)
                updated.update(
                    {
                        "method": "optuna_tpe_with_evidence_response_surface",
                        "router": "optuna.samplers.TPESampler",
                        "optimizer_backend": "optuna",
                        "optimizer_library": "optuna",
                        "optimizer_version": str(getattr(optuna, "__version__", "unknown")),
                        "optimizer_warm_start": "current+response_surface+trusted_memory",
                        "optimizer_budget_contract": "native_distinct_evaluator_calls",
                        "optimizer_new_evaluations": max(0, candidate_count - surface_count),
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
