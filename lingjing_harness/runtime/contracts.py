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
class Decision:
    step: PlanStep | None
    rationale: str
    score: float = 0.0
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    learned_bonus: float = 0.0


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
