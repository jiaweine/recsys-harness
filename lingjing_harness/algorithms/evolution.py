"""Compatibility surface for the hardened vertical evolution engine."""

from .evolution_core import (
    EvolutionDimension,
    _evolution_schema,
    _history_posteriors,
    _perturb,
    _project,
    _recommend_gates,
    _stable_split,
    evolve_recommend,
    evolve_search,
)

__all__ = [
    "EvolutionDimension",
    "_evolution_schema",
    "_history_posteriors",
    "_perturb",
    "_project",
    "_recommend_gates",
    "_stable_split",
    "evolve_search",
    "evolve_recommend",
]
