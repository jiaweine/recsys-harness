from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    handler: Callable[..., dict[str, Any]]
    cost: float = 1.0
    repeatable: bool = False
    side_effect: str = "none"
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlanStep:
    tool: str
    title: str
    detail: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentPlan:
    mode: str
    goal: str
    query: str | None = None
    user_id: str | None = None
    explore: bool = False
    allow_adaptation: bool = False
    allow_network: bool = False
    constraints: tuple[str, ...] = ()
    steps: list[PlanStep] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceRequirement:
    """One piece of evidence the mission needs before the critic can close it."""

    key: str
    label: str
    domain: str
    tool: str
    priority: str = "medium"
    status: str = "open"
    prerequisites: tuple[str, ...] = ()
    optional: bool = False
    satisfied_by: list[str] = field(default_factory=list)
    reason: str = ""
    capabilities: tuple[str, ...] = ()

    def dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "domain": self.domain,
            "tool": self.tool,
            "capabilities": list(self.capabilities or ((self.tool,) if self.tool else ())),
            "priority": self.priority,
            "status": self.status,
            "prerequisites": list(self.prerequisites),
            "optional": self.optional,
            "satisfied_by": list(self.satisfied_by),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "EvidenceRequirement":
        tool = str(row.get("tool") or "")
        capabilities = tuple(str(x) for x in row.get("capabilities") or ())
        if not capabilities and tool:
            capabilities = (tool,)
        return cls(
            key=str(row.get("key") or "requirement"),
            label=str(row.get("label") or "Evidence requirement"),
            domain=str(row.get("domain") or "general"),
            tool=tool,
            priority=str(row.get("priority") or "medium"),
            status=str(row.get("status") or "open"),
            prerequisites=tuple(str(x) for x in row.get("prerequisites") or ()),
            optional=bool(row.get("optional")),
            satisfied_by=[str(x) for x in row.get("satisfied_by") or []],
            reason=str(row.get("reason") or ""),
            capabilities=capabilities,
        )


@dataclass(slots=True)
class Hypothesis:
    """An inspectable explanation the harness can strengthen, weaken or retire."""

    key: str
    label: str
    domain: str
    confidence: float = 0.5
    status: str = "open"
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)

    def dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "domain": self.domain,
            "confidence": round(float(self.confidence), 4),
            "status": self.status,
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "Hypothesis":
        return cls(
            key=str(row.get("key") or "hypothesis"),
            label=str(row.get("label") or "Hypothesis"),
            domain=str(row.get("domain") or "general"),
            confidence=max(0.0, min(1.0, float(row.get("confidence", 0.5) or 0.5))),
            status=str(row.get("status") or "open"),
            supporting_evidence=[str(x) for x in row.get("supporting_evidence") or []],
            contradicting_evidence=[str(x) for x in row.get("contradicting_evidence") or []],
        )


@dataclass(slots=True)
class MissionGraph:
    """Task-specific evidence graph compiled before the first tool call.

    ``semantic_governance`` is a stable, typed projection used to validate the
    mission's evidence/capability/authority semantics.  It is persisted with the
    mission so resumed runs keep the same governance contract.
    """

    objective: str
    mode: str
    requirements: dict[str, EvidenceRequirement] = field(default_factory=dict)
    hypotheses: dict[str, Hypothesis] = field(default_factory=dict)
    exit_criteria: tuple[str, ...] = ()
    capability_snapshot: tuple[str, ...] = ()
    semantic_governance: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "mode": self.mode,
            "requirements": {key: req.dict() for key, req in self.requirements.items()},
            "hypotheses": {key: hyp.dict() for key, hyp in self.hypotheses.items()},
            "exit_criteria": list(self.exit_criteria),
            "capability_snapshot": list(self.capability_snapshot),
            "semantic_governance": dict(self.semantic_governance),
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "MissionGraph":
        requirements = {
            str(key): EvidenceRequirement.from_dict(value)
            for key, value in (row.get("requirements") or {}).items()
            if isinstance(value, dict)
        }
        hypotheses = {
            str(key): Hypothesis.from_dict(value)
            for key, value in (row.get("hypotheses") or {}).items()
            if isinstance(value, dict)
        }
        semantic = row.get("semantic_governance") or {}
        return cls(
            objective=str(row.get("objective") or ""),
            mode=str(row.get("mode") or "audit"),
            requirements=requirements,
            hypotheses=hypotheses,
            exit_criteria=tuple(str(x) for x in row.get("exit_criteria") or ()),
            capability_snapshot=tuple(str(x) for x in row.get("capability_snapshot") or ()),
            semantic_governance=dict(semantic) if isinstance(semantic, dict) else {},
        )


@dataclass(slots=True)
class Decision:
    step: PlanStep | None
    rationale: str
    score: float = 0.0
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    learned_bonus: float = 0.0
    target_requirement: str | None = None
    utility: dict[str, float] = field(default_factory=dict)
    hypotheses: tuple[str, ...] = ()


@dataclass(slots=True)
class RunBudget:
    max_tools: int = 14
    max_cost: float = 32.0
    max_seconds: float = 45.0


@dataclass(slots=True)
class RunState:
    cycle: int = 0
    spent_cost: float = 0.0
    actions: list[dict[str, Any]] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    mission: MissionGraph | None = None
    reflections: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    stagnation: int = 0
    last_progress_cycle: int = 0
    critic: dict[str, Any] = field(default_factory=dict)
