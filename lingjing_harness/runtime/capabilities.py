"""Declarative runtime capability contracts.

The registry is the source of mission-planning metadata. Execution remains owned
by ``ToolSpec`` handlers, while capabilities declare what evidence an action can
provide, which evidence it needs, its expected information value, and the gates
that must hold before it is a valid next step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


_ALL_MODES = frozenset({"search", "recommend", "both", "audit"})


@dataclass(frozen=True, slots=True)
class CapabilityGate:
    """A declarative readiness gate evaluated against a prior observation."""

    observation: str
    metric: str
    minimum: float
    reason: str

    def failure(self, observations: dict[str, Any]) -> str | None:
        row = observations.get(self.observation)
        if not isinstance(row, dict):
            return None
        try:
            value = float(row.get(self.metric, 0.0) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        return self.reason if value < self.minimum else None


@dataclass(frozen=True, slots=True)
class CapabilityHypothesis:
    key: str
    label: str
    confidence: float


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    """Self-description consumed by mission compilation and deliberation."""

    name: str
    requirement_key: str
    label: str
    domain: str
    description: str
    risk: str
    cost: float
    priority: str = "medium"
    information_gain: float = 0.60
    side_effect: str = "none"
    repeatable: bool = False
    provides: frozenset[str] = field(default_factory=frozenset)
    requires: frozenset[str] = field(default_factory=frozenset)
    diagnoses: frozenset[str] = field(default_factory=frozenset)
    base_modes: frozenset[str] = field(default_factory=frozenset)
    explore_modes: frozenset[str] = field(default_factory=frozenset)
    network_required: bool = False
    initial_status: str = "open"
    optional: bool = False
    title: str = ""
    detail: str = ""
    argument_bindings: tuple[tuple[str, str], ...] = ()
    completion_truthy: tuple[str, ...] = ()
    gates: tuple[CapabilityGate, ...] = ()
    hypotheses: tuple[CapabilityHypothesis, ...] = ()
    reflection_profile: str = ""
    order: int = 100

    def enabled_for(self, plan: Any) -> bool:
        mode = str(getattr(plan, "mode", "audit") or "audit")
        if self.network_required and not bool(getattr(plan, "allow_network", False)):
            return False
        return mode in self.base_modes or (
            bool(getattr(plan, "explore", False)) and mode in self.explore_modes
        )

    def can_help(self, goal: set[str]) -> bool:
        """Compatibility helper retained from the initial capability prototype."""
        return bool(self.provides & goal or self.diagnoses & goal)

    @property
    def risks(self) -> frozenset[str]:
        return frozenset({self.risk}) if self.risk else frozenset()

    @property
    def side_effects(self) -> frozenset[str]:
        return frozenset({self.side_effect}) if self.side_effect != "none" else frozenset()

    def dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "requirement_key": self.requirement_key,
            "label": self.label,
            "domain": self.domain,
            "description": self.description,
            "risk": self.risk,
            "cost": self.cost,
            "priority": self.priority,
            "information_gain": self.information_gain,
            "side_effect": self.side_effect,
            "repeatable": self.repeatable,
            "provides": sorted(self.provides),
            "requires": sorted(self.requires),
            "diagnoses": sorted(self.diagnoses),
            "base_modes": sorted(self.base_modes),
            "explore_modes": sorted(self.explore_modes),
            "network_required": self.network_required,
            "initial_status": self.initial_status,
            "optional": self.optional,
            "gates": [
                {
                    "observation": gate.observation,
                    "metric": gate.metric,
                    "minimum": gate.minimum,
                    "reason": gate.reason,
                }
                for gate in self.gates
            ],
            "reflection_profile": self.reflection_profile,
        }


class CapabilityRegistry:
    """Validated registry of runtime capability contracts.

    Multiple implementations may satisfy the same ``requirement_key``. They must
    agree on the evidence dependency and requirement semantics; deliberation can
    then choose among them using current cost/risk/information signals without a
    central tool-name branch.
    """

    def __init__(self, contracts: Iterable[CapabilityContract]) -> None:
        rows = tuple(contracts)
        by_name: dict[str, CapabilityContract] = {}
        by_requirement: dict[str, list[CapabilityContract]] = {}
        for contract in rows:
            if not contract.name or not contract.requirement_key:
                raise ValueError("capability name and requirement_key are required")
            if contract.name in by_name:
                raise ValueError(f"duplicate capability: {contract.name}")
            if contract.risk not in {"read", "simulation", "network", "adaptive"}:
                raise ValueError(f"unsupported capability risk: {contract.risk}")
            if not (0.0 <= float(contract.information_gain) <= 1.0):
                raise ValueError("information_gain must be within [0, 1]")
            if float(contract.cost) < 0.0:
                raise ValueError("capability cost must be non-negative")
            by_name[contract.name] = contract
            by_requirement.setdefault(contract.requirement_key, []).append(contract)

        for key, alternatives in by_requirement.items():
            anchor = alternatives[0]
            for alternative in alternatives[1:]:
                if (
                    alternative.domain != anchor.domain
                    or alternative.priority != anchor.priority
                    or alternative.requires != anchor.requires
                    or alternative.initial_status != anchor.initial_status
                    or alternative.optional != anchor.optional
                ):
                    raise ValueError(
                        f"capability alternatives for {key} must share requirement semantics"
                    )

        self._contracts = rows
        self._by_name = by_name
        self._by_requirement = {
            key: tuple(sorted(value, key=lambda row: (-row.information_gain, row.cost, row.name)))
            for key, value in by_requirement.items()
        }

    def get(self, name: str) -> CapabilityContract:
        if name not in self._by_name:
            raise KeyError(f"unknown capability: {name}")
        return self._by_name[name]

    def maybe_get(self, name: str) -> CapabilityContract | None:
        return self._by_name.get(name)

    def for_plan(self, plan: Any) -> tuple[CapabilityContract, ...]:
        return tuple(
            sorted(
                (row for row in self._contracts if row.enabled_for(plan)),
                key=lambda row: (row.order, row.requirement_key, row.name),
            )
        )

    def alternatives(self, requirement_key: str, plan: Any) -> tuple[CapabilityContract, ...]:
        return tuple(
            row for row in self._by_requirement.get(requirement_key, ()) if row.enabled_for(plan)
        )

    def validate_tool_specs(self, specs: Iterable[Any]) -> list[str]:
        """Return metadata drift between execution specs and capability contracts."""
        errors: list[str] = []
        for spec in specs:
            contract = self.maybe_get(str(getattr(spec, "name", "")))
            if contract is None:
                continue
            if str(getattr(spec, "risk", "")) != contract.risk:
                errors.append(f"{contract.name}:risk")
            try:
                cost = float(getattr(spec, "cost", 0.0))
            except (TypeError, ValueError):
                cost = -1.0
            if abs(cost - float(contract.cost)) > 1e-9:
                errors.append(f"{contract.name}:cost")
            if str(getattr(spec, "side_effect", "none")) != contract.side_effect:
                errors.append(f"{contract.name}:side_effect")
            if bool(getattr(spec, "repeatable", False)) != contract.repeatable:
                errors.append(f"{contract.name}:repeatable")
        return errors

    def manifest(self) -> list[dict[str, Any]]:
        return [row.dict() for row in sorted(self._contracts, key=lambda item: (item.order, item.name))]


def _capability(
    name: str,
    requirement_key: str,
    label: str,
    domain: str,
    description: str,
    risk: str,
    cost: float,
    **kwargs: Any,
) -> CapabilityContract:
    return CapabilityContract(
        name=name,
        requirement_key=requirement_key,
        label=label,
        domain=domain,
        description=description,
        risk=risk,
        cost=cost,
        **kwargs,
    )


DEFAULT_CAPABILITIES = (
    _capability(
        "data.inspect",
        "workspace_facts",
        "建立工作区事实边界",
        "general",
        "Inspect catalog and evaluation readiness",
        "read",
        0.35,
        priority="critical",
        information_gain=1.00,
        provides=frozenset({"workspace_facts", "evaluation_readiness"}),
        base_modes=_ALL_MODES,
        title="读取当前工作区",
        detail="确认数据、反馈、记忆与可复核证据边界",
        order=10,
    ),
    _capability(
        "web.research",
        "external_context",
        "补充带来源的外部时效证据",
        "external",
        "Search current public web evidence",
        "network",
        1.8,
        priority="medium",
        information_gain=0.76,
        side_effect="external_request",
        provides=frozenset({"external_context"}),
        requires=frozenset({"workspace_facts"}),
        base_modes=_ALL_MODES,
        network_required=True,
        title="补充外部公开证据",
        detail="检索与当前目标直接相关的公开资料并保留来源",
        argument_bindings=(("query", "goal"),),
        order=15,
    ),
    _capability(
        "search.run",
        "search_reproduction",
        "复现当前搜索结果",
        "search",
        "Run the current search experience",
        "read",
        0.9,
        priority="critical",
        information_gain=0.95,
        provides=frozenset({"search_reproduction", "retrieval_evidence"}),
        requires=frozenset({"workspace_facts"}),
        diagnoses=frozenset({"retrieval_gap", "query_failure"}),
        base_modes=frozenset({"search", "both"}),
        title="复现搜索体验",
        detail="真实运行“{query}”并保存结果证据",
        argument_bindings=(("query", "query"),),
        hypotheses=(
            CapabilityHypothesis(
                "search_local_mismatch",
                "当前查询存在匹配或候选覆盖缺口",
                0.42,
            ),
        ),
        reflection_profile="search_reproduction",
        order=20,
    ),
    _capability(
        "recommend.run",
        "recommend_reproduction",
        "复现当前推荐首屏",
        "recommend",
        "Generate a recommendation slate",
        "read",
        1.0,
        priority="critical",
        information_gain=0.94,
        provides=frozenset({"recommend_reproduction", "slate_evidence"}),
        requires=frozenset({"workspace_facts"}),
        diagnoses=frozenset({"cold_start", "empty_slate"}),
        base_modes=frozenset({"recommend", "both"}),
        title="复现推荐体验",
        detail="生成用户 {user_id} 的一屏真实结果",
        argument_bindings=(("user_id", "user_id"),),
        hypotheses=(
            CapabilityHypothesis(
                "recommend_cold_start",
                "当前用户缺少足够行为或可展示证据",
                0.34,
            ),
        ),
        reflection_profile="recommend_reproduction",
        order=21,
    ),
    _capability(
        "search.diagnose",
        "search_diagnosis",
        "解释搜索异常与证据缺口",
        "search",
        "Diagnose query evidence and candidate coverage",
        "read",
        0.7,
        priority="high",
        information_gain=0.88,
        provides=frozenset({"search_diagnosis"}),
        requires=frozenset({"search_reproduction"}),
        diagnoses=frozenset({"retrieval_gap", "query_failure", "candidate_coverage"}),
        base_modes=frozenset({"search", "both"}),
        initial_status="dormant",
        title="定位搜索失配",
        detail="检查查询证据、候选覆盖与低相关原因",
        argument_bindings=(("query", "query"),),
        reflection_profile="search_diagnosis",
        order=30,
    ),
    _capability(
        "recommend.diagnose",
        "recommend_diagnosis",
        "解释推荐异常、冷启动或展示约束",
        "recommend",
        "Diagnose user evidence, cold start and eligible pool",
        "read",
        0.7,
        priority="high",
        information_gain=0.88,
        provides=frozenset({"recommend_diagnosis"}),
        requires=frozenset({"recommend_reproduction"}),
        diagnoses=frozenset({"cold_start", "candidate_scarcity", "history_sparsity"}),
        base_modes=frozenset({"recommend", "both"}),
        initial_status="dormant",
        title="定位推荐约束",
        detail="检查用户历史、可展示池与冷启动状态",
        argument_bindings=(("user_id", "user_id"),),
        reflection_profile="recommend_diagnosis",
        order=31,
    ),
    _capability(
        "search.audit",
        "search_global_quality",
        "检查搜索整体稳定性与回退风险",
        "search",
        "Evaluate search on labeled queries",
        "simulation",
        2.0,
        priority="high",
        information_gain=0.80,
        provides=frozenset({"search_global_quality", "search_guardrail"}),
        requires=frozenset({"workspace_facts"}),
        diagnoses=frozenset({"systemic_search_gap", "retrieval_regression"}),
        base_modes=frozenset({"audit", "both"}),
        explore_modes=frozenset({"search"}),
        title="检查搜索稳定性",
        detail="用可复核查询检查整体表现和回退风险",
        hypotheses=(
            CapabilityHypothesis(
                "search_systemic_gap",
                "问题可能是整体搜索质量缺口而非单点异常",
                0.32,
            ),
        ),
        reflection_profile="search_audit",
        order=40,
    ),
    _capability(
        "recommend.audit",
        "recommend_global_quality",
        "检查推荐覆盖、新鲜度与分散度",
        "recommend",
        "Evaluate recommendation coverage, freshness, diversity and cold start",
        "simulation",
        2.2,
        priority="high",
        information_gain=0.80,
        provides=frozenset({"recommend_global_quality", "recommend_guardrail"}),
        requires=frozenset({"workspace_facts"}),
        diagnoses=frozenset({"systemic_recommend_gap", "cold_start_regression"}),
        base_modes=frozenset({"audit", "both"}),
        explore_modes=frozenset({"recommend"}),
        title="检查推荐稳定性",
        detail="复核覆盖、新鲜度、质量与结果分散度",
        hypotheses=(
            CapabilityHypothesis(
                "recommend_systemic_gap",
                "问题可能是整体推荐质量缺口而非单用户异常",
                0.32,
            ),
        ),
        reflection_profile="recommend_audit",
        order=41,
    ),
    _capability(
        "search.evolve",
        "search_candidate_validation",
        "探索并验证搜索候选策略",
        "search",
        "Generate and robustly evaluate evolved search strategies",
        "adaptive",
        6.0,
        priority="high",
        information_gain=0.70,
        side_effect="internal_strategy_memory",
        provides=frozenset({"search_candidate_validation", "validated_search_strategy"}),
        requires=frozenset({"search_global_quality"}),
        diagnoses=frozenset({"search_strategy_regression"}),
        explore_modes=frozenset({"search", "both", "audit"}),
        title="探索搜索候选策略",
        detail="生成候选并经过留出验证、回归与稳健门槛筛选",
        argument_bindings=(("activate", "allow_adaptation"),),
        completion_truthy=("evaluation_ready",),
        gates=(
            CapabilityGate(
                "search.audit",
                "queries",
                3.0,
                "fewer than 3 searchable evaluation queries",
            ),
        ),
        order=50,
    ),
    _capability(
        "recommend.evolve",
        "recommend_candidate_validation",
        "探索并验证推荐候选策略",
        "recommend",
        "Generate and robustly evaluate evolved recommendation strategies",
        "adaptive",
        6.5,
        priority="high",
        information_gain=0.70,
        side_effect="internal_strategy_memory",
        provides=frozenset({"recommend_candidate_validation", "validated_recommend_strategy"}),
        requires=frozenset({"recommend_global_quality"}),
        diagnoses=frozenset({"recommend_strategy_regression", "segment_pathology"}),
        explore_modes=frozenset({"recommend", "both", "audit"}),
        title="探索推荐候选策略",
        detail="生成候选并经过留出验证、回归与稳健门槛筛选",
        argument_bindings=(("activate", "allow_adaptation"),),
        completion_truthy=("evaluation_ready",),
        gates=(
            CapabilityGate(
                "recommend.audit",
                "users",
                3.0,
                "fewer than 3 evaluable recommendation users",
            ),
        ),
        order=51,
    ),
)

RUNTIME_CAPABILITIES = CapabilityRegistry(DEFAULT_CAPABILITIES)


__all__ = [
    "CapabilityGate",
    "CapabilityHypothesis",
    "CapabilityContract",
    "CapabilityRegistry",
    "DEFAULT_CAPABILITIES",
    "RUNTIME_CAPABILITIES",
]
