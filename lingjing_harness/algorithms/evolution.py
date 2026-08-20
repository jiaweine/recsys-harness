"""Stable public surface for vertical evolution.

The structural/search machinery remains in ``evolution_core``. Public evolution
routes through ``production_evolution`` so a project-provided RewardSpec and
production exposure log become the primary objective when available, while the
legacy proxy path remains available for local/demo datasets.
"""

from .evolution_core import (
    EvolutionDimension,
    _evolution_schema,
    _history_posteriors,
    _perturb,
    _project,
    _recommend_gates,
    _stable_split,
)
from .production_evolution import evolve_recommend, evolve_search

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
