"""Constrained mixed-space qLogNEHVI proposal search under the native eval budget.

This backend uses BoTorch only to choose *which legal configuration to evaluate
next*. It never owns the harness scalar incumbent, independent holdout, trust,
activation, or rollback decisions.

Continuous genes are normalized to [0, 1]. Categorical capability genes are integer
labels consumed only through BoTorch categorical kernels / mixed optimizers, so the
codes never imply ordinal distance. Blend-group mass constraints are transformed
algebraically into exact linear equality constraints in normalized coordinates.

Two maximization objectives and explicit signed outcome constraints are modeled by
independent single-output Gaussian processes collected in ``ModelListGP``. Keeping
one GP per output mirrors BoTorch's constrained-MOBO reference pattern and avoids
coupling the acquisition path to batched multi-output fantasizing behavior. Every
constraint output is defined so ``value <= 0`` means feasible.

qLogNEHVI receives a fixed reference point computed once from the initial design.
When feasible observations exist, only feasible objective rows define that point;
otherwise the initial design is used as an explicit fallback. Acquisition
optimization follows BoTorch's own mixed-space routing: continuous optimization for
continuous-only spaces, exact categorical-combination enumeration for small mixed
spaces, and alternating mixed optimization only when the categorical product is
large enough to justify approximation.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import isfinite
from types import SimpleNamespace
from typing import Any, Callable

from . import evolution_core as core
from .optimizer_contracts import optimizer_evidence_contract


MIN_MODEL_POINTS = 4
MODEL_FIT_MAXITER = 50
ACQUISITION_RAW_SAMPLES = 256
ACQUISITION_RESTARTS = 12
MAX_DUPLICATE_PROPOSALS = 8


def load_botorch_stack() -> SimpleNamespace:
    """Import the optional research optimizer stack only when explicitly selected."""

    try:
        import botorch
        import torch
        from botorch.acquisition.multi_objective.logei import (
            qLogNoisyExpectedHypervolumeImprovement,
        )
        from botorch.acquisition.multi_objective.objective import (
            IdentityMCMultiOutputObjective,
        )
        from botorch.fit import fit_gpytorch_mll
        from botorch.models.gp_regression import SingleTaskGP
        from botorch.models.gp_regression_mixed import MixedSingleTaskGP
        from botorch.models.model_list_gp_regression import ModelListGP
        from botorch.models.transforms.outcome import Standardize
        from botorch.optim.optimize import optimize_acqf, optimize_acqf_mixed
        from botorch.optim.optimize_mixed import (
            optimize_acqf_mixed_alternating,
            should_use_mixed_alternating_optimizer,
        )
        from gpytorch.mlls.sum_marginal_log_likelihood import SumMarginalLogLikelihood
    except ImportError as exc:
        raise RuntimeError(
            "qLogNEHVI optimizer requires the optional mobo dependencies; "
            "install with `pip install -e '.[mobo]'`"
        ) from exc
    return SimpleNamespace(
        botorch=botorch,
        torch=torch,
        qLogNoisyExpectedHypervolumeImprovement=qLogNoisyExpectedHypervolumeImprovement,
        IdentityMCMultiOutputObjective=IdentityMCMultiOutputObjective,
        fit_gpytorch_mll=fit_gpytorch_mll,
        SingleTaskGP=SingleTaskGP,
        MixedSingleTaskGP=MixedSingleTaskGP,
        ModelListGP=ModelListGP,
        Standardize=Standardize,
        optimize_acqf=optimize_acqf,
        optimize_acqf_mixed=optimize_acqf_mixed,
        optimize_acqf_mixed_alternating=optimize_acqf_mixed_alternating,
        should_use_mixed_alternating_optimizer=should_use_mixed_alternating_optimizer,
        SumMarginalLogLikelihood=SumMarginalLogLikelihood,
    )


@dataclass(frozen=True, slots=True)
class EncodedDimension:
    index: int
    name: str
    kind: str
    low: float
    high: float
    choices: tuple[str, ...]
    group: str

    @property
    def span(self) -> float:
        return max(0.0, float(self.high) - float(self.low))


@dataclass(frozen=True, slots=True)
class MixedSearchSpace:
    """Round-trip representation of the harness typed genome for BoTorch."""

    base_config: dict[str, Any]
    dimensions: tuple[EncodedDimension, ...]
    group_totals: dict[str, float]

    @classmethod
    def build(
        cls,
        *,
        base_config: dict[str, Any],
        dimensions: list[core.EvolutionDimension],
        group_totals: dict[str, float],
    ) -> "MixedSearchSpace":
        encoded = []
        for index, dimension in enumerate(dimensions):
            encoded.append(
                EncodedDimension(
                    index=index,
                    name=dimension.name,
                    kind=dimension.kind,
                    low=float(dimension.low),
                    high=float(dimension.high),
                    choices=tuple(str(choice) for choice in dimension.choices),
                    group=str(dimension.group),
                )
            )
        return cls(
            base_config=dict(base_config),
            dimensions=tuple(encoded),
            group_totals={str(key): float(value) for key, value in group_totals.items()},
        )

    @property
    def categorical_indices(self) -> list[int]:
        return [
            dimension.index
            for dimension in self.dimensions
            if dimension.kind == "capability"
        ]

    @property
    def continuous_indices(self) -> list[int]:
        return [
            dimension.index
            for dimension in self.dimensions
            if dimension.kind == "continuous"
        ]

    def encode(self, config: dict[str, Any]) -> list[float]:
        values: list[float] = []
        for dimension in self.dimensions:
            if dimension.name not in config:
                raise KeyError(dimension.name)
            if dimension.kind == "capability":
                choice = str(config[dimension.name])
                if choice not in dimension.choices:
                    raise ValueError(
                        f"unknown categorical choice for {dimension.name}: {choice}"
                    )
                values.append(float(dimension.choices.index(choice)))
                continue
            value = float(config[dimension.name])
            if not isfinite(value):
                raise ValueError(f"non-finite continuous gene: {dimension.name}")
            if dimension.span <= 0.0:
                values.append(0.0)
            else:
                normalized = (value - dimension.low) / dimension.span
                values.append(max(0.0, min(1.0, normalized)))
        return values

    def decode(self, values: list[float] | tuple[float, ...]) -> dict[str, Any]:
        if len(values) != len(self.dimensions):
            raise ValueError("encoded candidate dimensionality mismatch")
        raw = dict(self.base_config)
        for dimension, encoded in zip(self.dimensions, values):
            value = float(encoded)
            if not isfinite(value):
                raise ValueError("non-finite encoded candidate")
            if dimension.kind == "capability":
                if not dimension.choices:
                    raise ValueError(
                        f"categorical dimension has no choices: {dimension.name}"
                    )
                index = int(round(value))
                index = max(0, min(len(dimension.choices) - 1, index))
                raw[dimension.name] = dimension.choices[index]
            else:
                raw[dimension.name] = (
                    dimension.low
                    + max(0.0, min(1.0, value)) * dimension.span
                )

        original_dimensions = [
            core.EvolutionDimension(
                name=dimension.name,
                kind=dimension.kind,
                group=dimension.group,
                low=dimension.low,
                high=dimension.high,
                choices=dimension.choices,
            )
            for dimension in self.dimensions
        ]
        return core._project(raw, original_dimensions, self.group_totals)

    def bounds(self, torch: Any) -> Any:
        lower = []
        upper = []
        for dimension in self.dimensions:
            if dimension.kind == "capability":
                lower.append(0.0)
                upper.append(float(max(0, len(dimension.choices) - 1)))
            else:
                lower.append(0.0)
                upper.append(1.0)
        return torch.tensor([lower, upper], dtype=torch.double)

    def categorical_values(self) -> dict[int, list[float]]:
        return {
            dimension.index: [
                float(index) for index in range(len(dimension.choices))
            ]
            for dimension in self.dimensions
            if dimension.kind == "capability"
        }

    def fixed_features_list(self) -> list[dict[int, float]]:
        """Enumerate categorical assignments for small mixed spaces exactly."""

        categorical = self.categorical_values()
        if not categorical:
            return []
        indices = sorted(categorical)
        return [
            {index: float(value) for index, value in zip(indices, values)}
            for values in product(*(categorical[index] for index in indices))
        ]

    def equality_constraint_specs(
        self,
    ) -> list[tuple[list[int], list[float], float]]:
        """Translate actual-unit blend sums into normalized-coordinate equalities."""

        by_group: dict[str, list[EncodedDimension]] = {}
        for dimension in self.dimensions:
            if (
                dimension.kind == "continuous"
                and dimension.group != "independent"
            ):
                by_group.setdefault(dimension.group, []).append(dimension)

        constraints: list[tuple[list[int], list[float], float]] = []
        for group, members in sorted(by_group.items()):
            target = float(self.group_totals[group])
            indices = [member.index for member in members]
            coefficients = [member.span for member in members]
            rhs = target - sum(member.low for member in members)
            if not coefficients or all(
                abs(value) <= 1e-15 for value in coefficients
            ):
                continue
            constraints.append((indices, coefficients, rhs))
        return constraints

    def equality_constraints(self, torch: Any) -> list[tuple[Any, Any, float]]:
        return [
            (
                torch.tensor(indices, dtype=torch.long),
                torch.tensor(coefficients, dtype=torch.double),
                float(rhs),
            )
            for indices, coefficients, rhs in self.equality_constraint_specs()
        ]

    def provenance(self) -> dict[str, Any]:
        categorical = self.categorical_values()
        combination_count = 1
        for values in categorical.values():
            combination_count *= len(values)
        if not categorical:
            combination_count = 0
        return {
            "encoding": "continuous_unit_interval_plus_categorical_labels",
            "continuous_dimensions": self.continuous_indices,
            "categorical_dimensions": self.categorical_indices,
            "categorical_cardinalities": {
                dimension.name: len(dimension.choices)
                for dimension in self.dimensions
                if dimension.kind == "capability"
            },
            "categorical_combination_count": combination_count,
            "equality_constraints": [
                {
                    "indices": indices,
                    "coefficients": coefficients,
                    "rhs": rhs,
                    "semantics": "normalized_linear_equality",
                }
                for indices, coefficients, rhs in self.equality_constraint_specs()
            ],
        }


def _evaluate_once(
    config: dict[str, Any],
    *,
    evaluated: dict[tuple[tuple[str, Any], ...], dict[str, Any]],
    evaluate: Callable[
        [dict[str, Any]], tuple[dict[str, Any], dict[str, float], float]
    ],
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


def _ordered_training_rows(
    evaluated: dict[tuple[tuple[str, Any], ...], dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        evaluated.values(),
        key=lambda row: core._config_key(row["config"]),
    )


def _reference_point(
    outcome_rows: list[tuple[float, ...]],
) -> tuple[list[float], str, int]:
    """Build one fixed hypervolume reference from feasible initial evidence.

    The first two entries are objectives; all later entries are signed constraints
    with ``<= 0`` meaning feasible. If feasible initial observations exist, only
    those define the hypervolume reference. Otherwise all initial observations are
    used as an explicit fallback so BO can still search toward feasibility.
    """

    if not outcome_rows:
        raise ValueError("qLogNEHVI requires observed objective evidence")
    feasible = [
        row
        for row in outcome_rows
        if len(row) <= 2 or all(float(value) <= 0.0 for value in row[2:])
    ]
    selected = feasible or outcome_rows
    basis = "feasible_initial_design" if feasible else "all_initial_design_no_feasible"
    reference = []
    for column in range(2):
        values = [float(row[column]) for row in selected]
        minimum = min(values)
        maximum = max(values)
        span = maximum - minimum
        scale = max(1e-6, abs(minimum), abs(maximum))
        margin = max(1e-6, 0.10 * span, 0.01 * scale)
        reference.append(minimum - margin)
    return reference, basis, len(selected)


def _training_tensors(
    stack: SimpleNamespace,
    space: MixedSearchSpace,
    rows: list[dict[str, Any]],
    contract: Any,
) -> tuple[Any, Any]:
    torch = stack.torch
    train_x = torch.tensor(
        [space.encode(row["config"]) for row in rows],
        dtype=torch.double,
    )
    train_y = torch.tensor(
        [contract.outcome_values(row) for row in rows],
        dtype=torch.double,
    )
    return train_x, train_y


def _fit_model(
    stack: SimpleNamespace,
    *,
    train_x: Any,
    train_y: Any,
    categorical_indices: list[int],
) -> Any:
    """Fit independent GPs per objective/constraint and combine in ModelListGP."""

    models = []
    for output_index in range(int(train_y.shape[-1])):
        output = train_y[:, output_index : output_index + 1]
        kwargs = {
            "train_X": train_x,
            "train_Y": output,
            "outcome_transform": stack.Standardize(m=1),
        }
        if categorical_indices:
            model = stack.MixedSingleTaskGP(
                **kwargs,
                cat_dims=categorical_indices,
            )
        else:
            model = stack.SingleTaskGP(**kwargs)
        models.append(model)
    model = stack.ModelListGP(*models)
    mll = stack.SumMarginalLogLikelihood(model.likelihood, model)
    stack.fit_gpytorch_mll(
        mll,
        optimizer_kwargs={"options": {"maxiter": MODEL_FIT_MAXITER}},
    )
    return model


def _optimize_acquisition(
    stack: SimpleNamespace,
    *,
    acquisition: Any,
    space: MixedSearchSpace,
) -> tuple[Any, Any, str]:
    """Choose the strongest appropriate official acquisition optimizer."""

    bounds = space.bounds(stack.torch)
    equality_constraints = space.equality_constraints(stack.torch)
    categorical = space.categorical_values()
    common = {
        "acq_function": acquisition,
        "bounds": bounds,
        "q": 1,
        "raw_samples": ACQUISITION_RAW_SAMPLES,
        "num_restarts": ACQUISITION_RESTARTS,
        "equality_constraints": equality_constraints,
    }
    if not categorical:
        candidate, value = stack.optimize_acqf(
            **common,
            options={"batch_limit": 128, "maxiter": 100},
        )
        return candidate, value, "continuous_exact"

    use_alternating = stack.should_use_mixed_alternating_optimizer(
        cat_dims=categorical
    )
    if not use_alternating:
        candidate, value = stack.optimize_acqf_mixed(
            **common,
            fixed_features_list=space.fixed_features_list(),
            options={"batch_limit": 128, "maxiter": 100},
        )
        return candidate, value, "categorical_enumeration"

    candidate, value = stack.optimize_acqf_mixed_alternating(
        **common,
        cat_dims=categorical,
        options={
            "maxiter_alternating": 32,
            "maxiter_discrete": 4,
            "maxiter_continuous": 8,
            "batch_limit": 256,
            "init_batch_limit": 512,
        },
    )
    return candidate, value, "mixed_alternating"


def _candidate_from_acquisition(
    stack: SimpleNamespace,
    *,
    space: MixedSearchSpace,
    rows: list[dict[str, Any]],
    contract: Any,
    seed: int,
    reference_point: list[float],
    reference_point_basis: str,
    reference_point_rows: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    torch = stack.torch
    torch.manual_seed(int(seed))
    train_x, train_y = _training_tensors(stack, space, rows, contract)
    model = _fit_model(
        stack,
        train_x=train_x,
        train_y=train_y,
        categorical_indices=space.categorical_indices,
    )
    objective = stack.IdentityMCMultiOutputObjective(
        outcomes=[0, 1],
        num_outcomes=train_y.shape[-1],
    )
    constraint_indices = list(range(2, int(train_y.shape[-1])))
    constraints = [
        (lambda samples, index=index: samples[..., index])
        for index in constraint_indices
    ]
    acquisition = stack.qLogNoisyExpectedHypervolumeImprovement(
        model=model,
        ref_point=list(reference_point),
        X_baseline=train_x,
        objective=objective,
        constraints=constraints,
        prune_baseline=True,
        # ModelListGP does not need root caching here, and disabling it avoids
        # coupling correctness to batched multi-output/fantasy cache assumptions.
        cache_root=False,
    )
    candidate, acquisition_value, optimizer = _optimize_acquisition(
        stack,
        acquisition=acquisition,
        space=space,
    )
    encoded = [
        float(value)
        for value in candidate.detach().cpu().reshape(-1).tolist()
    ]
    config = space.decode(encoded)
    acq_value = None
    if acquisition_value is not None:
        flattened = acquisition_value.detach().cpu().reshape(-1).tolist()
        if flattened:
            acq_value = float(flattened[0])
    posterior = model.posterior(train_x[:1])
    posterior_mean_shape = list(posterior.mean.shape)
    return config, {
        "reference_point": list(reference_point),
        "reference_point_basis": reference_point_basis,
        "reference_point_rows": int(reference_point_rows),
        "training_points": len(rows),
        "constraint_outputs": constraint_indices,
        "acquisition_value": acq_value,
        "acquisition_optimizer": optimizer,
        "posterior_mean_shape": posterior_mean_shape,
        "model_outputs": int(getattr(model, "num_outputs", train_y.shape[-1])),
        "cache_root": False,
    }


def _unseen_population_candidate(
    population: list[dict[str, Any]],
    evaluated: dict[tuple[tuple[str, Any], ...], dict[str, Any]],
) -> dict[str, Any] | None:
    for candidate in core._unique_configs(population):
        if core._config_key(candidate) not in evaluated:
            return candidate
    return None


def _unseen_mutation_candidate(
    *,
    base_config: dict[str, Any],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
    evaluated: dict[tuple[tuple[str, Any], ...], dict[str, Any]],
    rng: Any,
) -> dict[str, Any] | None:
    for attempt in range(core.POPULATION_SIZE * 12):
        candidate = core._mutate_config(
            base_config,
            dimensions=dimensions,
            group_totals=group_totals,
            rng=rng,
            scale=0.85 + 0.08 * (attempt % 8),
        )
        if core._config_key(candidate) not in evaluated:
            return candidate
    return None


def qlognehvi_evolution_loop(
    *,
    base_config: dict[str, Any],
    population: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
    evaluate: Callable[
        [dict[str, Any]], tuple[dict[str, Any], dict[str, float], float]
    ],
    rng: Any,
    cache: dict[tuple[tuple[str, Any], ...], dict[str, Any]] | None = None,
    evaluation_budget: int,
    stack: SimpleNamespace | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run constrained qLogNEHVI while spending at most the native distinct budget."""

    stack = stack or load_botorch_stack()
    evaluated = dict(cache or {})
    contract = optimizer_evidence_contract(evaluate)
    space = MixedSearchSpace.build(
        base_config=base_config,
        dimensions=dimensions,
        group_totals=group_totals,
    )
    budget = max(0, int(evaluation_budget))
    new_evaluations = 0
    initial_design_evaluations = 0
    acquisition_evaluations = 0
    duplicate_proposals = 0
    acquisition_steps = 0
    last_acquisition: dict[str, Any] = {}

    # Cache/response-surface rows are free evidence. If they are sparse, spend from
    # the *same* native evaluator budget to reach the minimum model design.
    while len(evaluated) < MIN_MODEL_POINTS and new_evaluations < budget:
        candidate = _unseen_population_candidate(population, evaluated)
        if candidate is None:
            candidate = _unseen_mutation_candidate(
                base_config=base_config,
                dimensions=dimensions,
                group_totals=group_totals,
                evaluated=evaluated,
                rng=rng,
            )
        if candidate is None:
            break
        _, is_new = _evaluate_once(
            candidate,
            evaluated=evaluated,
            evaluate=evaluate,
            source="qlognehvi_initial_design",
        )
        if is_new:
            new_evaluations += 1
            initial_design_evaluations += 1

    if len(evaluated) < 2:
        raise RuntimeError(
            "qLogNEHVI requires at least two distinct evaluated configurations"
        )

    # Freeze the hypervolume reference for this optimization run. Recomputing it
    # after each observation would change the utility definition mid-search and make
    # acquisition values across iterations incomparable.
    initial_rows = _ordered_training_rows(evaluated)
    initial_outcomes = [contract.outcome_values(row) for row in initial_rows]
    reference_point, reference_basis, reference_rows = _reference_point(
        initial_outcomes
    )

    duplicate_streak = 0
    while new_evaluations < budget:
        rows = _ordered_training_rows(evaluated)
        acquisition_steps += 1
        seed = int(rng.randrange(0, 2**31 - 1))
        try:
            candidate, acquisition_meta = _candidate_from_acquisition(
                stack,
                space=space,
                rows=rows,
                contract=contract,
                seed=seed,
                reference_point=reference_point,
                reference_point_basis=reference_basis,
                reference_point_rows=reference_rows,
            )
        except Exception as exc:
            raise RuntimeError(
                "qLogNEHVI acquisition failed without expanding evaluator budget: "
                f"{exc}"
            ) from exc
        last_acquisition = acquisition_meta
        key = core._config_key(candidate)
        if key in evaluated:
            duplicate_proposals += 1
            duplicate_streak += 1
            if duplicate_streak <= MAX_DUPLICATE_PROPOSALS:
                # Retry from a new deterministic seed before falling back. Duplicate
                # posterior proposals never spend an expensive evaluator call.
                continue
            candidate = _unseen_population_candidate(population, evaluated)
            if candidate is None:
                candidate = _unseen_mutation_candidate(
                    base_config=base_config,
                    dimensions=dimensions,
                    group_totals=group_totals,
                    evaluated=evaluated,
                    rng=rng,
                )
            duplicate_streak = 0
            if candidate is None:
                break

        _, is_new = _evaluate_once(
            candidate,
            evaluated=evaluated,
            evaluate=evaluate,
            source="qlognehvi_acquisition",
        )
        if is_new:
            new_evaluations += 1
            acquisition_evaluations += 1
            duplicate_streak = 0

    rows = sorted(
        evaluated.values(),
        key=lambda row: (
            -float(row["objective"]),
            core._config_key(row["config"]),
        ),
    )
    telemetry = {
        "optimizer": "qlognehvi",
        "library": "botorch",
        "library_version": str(
            getattr(stack.botorch, "__version__", "unknown")
        ),
        "acquisition": "qLogNoisyExpectedHypervolumeImprovement",
        "model": "ModelListGP[independent_single_output_gp]",
        "per_output_model": (
            "MixedSingleTaskGP"
            if space.categorical_indices
            else "SingleTaskGP"
        ),
        "model_fit_maxiter": MODEL_FIT_MAXITER,
        "native_distinct_evaluation_budget": budget,
        "new_evaluations": new_evaluations,
        "initial_design_evaluations": initial_design_evaluations,
        "acquisition_evaluations": acquisition_evaluations,
        "acquisition_steps": acquisition_steps,
        "duplicate_proposals": duplicate_proposals,
        "reference_point": reference_point,
        "reference_point_basis": reference_basis,
        "reference_point_rows": reference_rows,
        "reference_point_frozen_after_initial_design": True,
        "final_selection": "harness_primary_objective",
        "contract": contract.dict(),
        "space": space.provenance(),
        "last_acquisition": last_acquisition,
    }
    if rows:
        rows[0] = dict(rows[0])
        rows[0]["optimizer_provenance"] = telemetry
    archive = core._quality_diversity_archive(base_config, rows, dimensions)
    return rows, archive


__all__ = [
    "MIN_MODEL_POINTS",
    "MixedSearchSpace",
    "load_botorch_stack",
    "qlognehvi_evolution_loop",
]
