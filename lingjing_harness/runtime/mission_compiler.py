"""Compile task-specific Mission Graphs from declarative capability contracts."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .capabilities import CapabilityContract, CapabilityRegistry, RUNTIME_CAPABILITIES
from .contracts import AgentPlan, EvidenceRequirement, Hypothesis, MissionGraph
from .semantic_governance import compile_semantic_governance


class MissionCompiler:
    """Turn an AgentPlan into an evidence DAG without tool-name branches.

    Capability contracts decide when they participate, what evidence they require,
    and which requirement they can satisfy. The compiler only checks dependency
    closure and groups interchangeable capability implementations.  The resulting
    mission is then projected into the semantic governance graph and must satisfy
    its closed-world ontology shapes before it can execute.
    """

    EXIT_CRITERIA = (
        "critical/high evidence requirements are terminal",
        "material contradictions are investigated",
        "tool and permission budgets remain respected",
        "learning is independently verified before trust",
    )

    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self.registry = registry or RUNTIME_CAPABILITIES

    def compile(self, plan: AgentPlan) -> MissionGraph:
        enabled = self.registry.for_plan(plan)
        grouped: dict[str, list[CapabilityContract]] = defaultdict(list)
        for contract in enabled:
            grouped[contract.requirement_key].append(contract)

        requirements: dict[str, EvidenceRequirement] = {}
        hypotheses: dict[str, Hypothesis] = {}
        available_keys = set(grouped)

        for key in self._ordered_requirement_keys(grouped):
            alternatives = tuple(
                sorted(
                    grouped[key],
                    key=lambda row: (-row.information_gain, row.cost, row.order, row.name),
                )
            )
            primary = alternatives[0]
            missing = tuple(sorted(dep for dep in primary.requires if dep not in available_keys))
            status = primary.initial_status
            reason = ""
            if missing and status != "dormant":
                status = "blocked"
                reason = "capability dependency is unavailable: " + ", ".join(missing)

            requirements[key] = EvidenceRequirement(
                key=key,
                label=primary.label,
                domain=primary.domain,
                tool=primary.name,
                capabilities=tuple(row.name for row in alternatives),
                priority=primary.priority,
                status=status,
                prerequisites=tuple(sorted(primary.requires)),
                optional=primary.optional,
                reason=reason,
            )

            for contract in alternatives:
                for template in contract.hypotheses:
                    hypotheses.setdefault(
                        template.key,
                        Hypothesis(
                            template.key,
                            template.label,
                            contract.domain,
                            confidence=max(0.0, min(1.0, float(template.confidence))),
                        ),
                    )

        mission = MissionGraph(
            objective=plan.goal,
            mode=plan.mode,
            requirements=requirements,
            hypotheses=hypotheses,
            exit_criteria=self.EXIT_CRITERIA,
            capability_snapshot=tuple(row.name for row in enabled),
        )
        semantic = compile_semantic_governance(plan, mission, self.registry)
        if not bool(semantic.get("valid")):
            violations = semantic.get("violations") or []
            detail = "; ".join(
                str(row.get("message") or row.get("shape") or "semantic violation")
                for row in violations[:4]
                if isinstance(row, dict)
            )
            raise ValueError(
                "mission semantic governance validation failed"
                + (f": {detail}" if detail else "")
            )
        mission.semantic_governance = semantic
        return mission

    @staticmethod
    def _ordered_requirement_keys(
        grouped: dict[str, list[CapabilityContract]],
    ) -> list[str]:
        def sort_key(key: str) -> tuple[int, str]:
            return (min(row.order for row in grouped[key]), key)

        return sorted(grouped, key=sort_key)

    def capability_dependencies(self, plan: AgentPlan) -> dict[str, tuple[str, ...]]:
        """Inspectable dependency map used by tests and architecture tooling."""
        mission = self.compile(plan)
        return {
            key: tuple(req.prerequisites)
            for key, req in mission.requirements.items()
        }


__all__ = ["MissionCompiler"]
