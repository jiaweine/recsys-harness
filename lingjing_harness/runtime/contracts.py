from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    handler: Callable[..., dict[str, Any]]


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
    compare: bool = False
    steps: list[PlanStep] = field(default_factory=list)
