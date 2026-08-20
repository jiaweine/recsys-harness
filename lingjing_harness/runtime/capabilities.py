"""Declarative capability contracts for mission compilation.

Capabilities describe what an agent action can provide and what evidence it
requires.  The mission planner can use these contracts instead of matching
hard-coded tool names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet


@dataclass(frozen=True)
class CapabilityContract:
    name: str
    provides: FrozenSet[str] = field(default_factory=frozenset)
    requires: FrozenSet[str] = field(default_factory=frozenset)
    diagnoses: FrozenSet[str] = field(default_factory=frozenset)
    risks: FrozenSet[str] = field(default_factory=frozenset)
    side_effects: FrozenSet[str] = field(default_factory=frozenset)
    cost: str = "normal"

    def can_help(self, goal: set[str]) -> bool:
        """Return whether this capability covers any requested outcome."""
        return bool(self.provides & goal or self.diagnoses & goal)


DEFAULT_CAPABILITIES = (
    CapabilityContract(
        name="search_observation",
        provides=frozenset({"retrieval_evidence", "coverage_signal"}),
        diagnoses=frozenset({"retrieval_gap", "query_failure"}),
        risks=frozenset({"stale_index"}),
    ),
    CapabilityContract(
        name="recommend_evolution",
        provides=frozenset({"candidate_strategy", "ranking_improvement"}),
        requires=frozenset({"validated_evidence"}),
        diagnoses=frozenset({"ranking_regression", "segment_pathology"}),
        risks=frozenset({"overfit"}),
        side_effects=frozenset({"strategy_memory_write"}),
    ),
)
