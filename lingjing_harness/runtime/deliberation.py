from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .capabilities import CapabilityContract, CapabilityRegistry, RUNTIME_CAPABILITIES
from .contracts import (
    AgentPlan,
    Decision,
    EvidenceRequirement,
    MissionGraph,
    PlanStep,
    RunState,
    ToolSpec,
)
from .mission_compiler import MissionCompiler


_PRIORITY = {"critical": 1.0, "high": 0.82, "medium": 0.62, "low": 0.42}
_RISK_PRESSURE = {"read": 0.00, "simulation": 0.05, "network": 0.10, "adaptive": 0.16}


@dataclass(slots=True)
class Candidate:
    requirement: EvidenceRequirement
    step: PlanStep
    score: float
    learned_bonus: float
    utility: dict[str, float]
    hypotheses: tuple[str, ...]
    rationale: str


class TrajectoryCritic:
    """Checks whether the current trajectory is evidence-complete enough to close."""

    def assess(self, state: RunState) -> dict[str, Any]:
        mission = state.mission
        if mission is None:
            return {
                "ready": False,
                "evidence_coverage": 0.0,
                "terminal_coverage": 0.0,
                "unresolved": ["mission_not_initialized"],
                "blocked": [],
                "contradictions": list(state.contradictions),
                "confidence": 0.0,
            }

        active = [
            req
            for req in mission.requirements.values()
            if req.status != "dormant" and not req.optional
        ]
        satisfied = [req for req in active if req.status == "satisfied"]
        terminal = [req for req in active if req.status in {"satisfied", "blocked"}]
        unresolved = [
            req.key
            for req in active
            if req.status == "open" and req.priority in {"critical", "high"}
        ]
        blocked = [req.key for req in active if req.status == "blocked"]

        unresolved_contradictions: list[str] = []
        for contradiction in state.contradictions:
            domain = contradiction.split(":", 1)[0]
            diagnosis = next(
                (
                    req
                    for req in mission.requirements.values()
                    if req.domain == domain and "diagnosis" in req.key
                ),
                None,
            )
            if diagnosis is None or diagnosis.status not in {"satisfied", "blocked"}:
                unresolved_contradictions.append(contradiction)

        denom = max(1, len(active))
        evidence_coverage = len(satisfied) / denom
        terminal_coverage = len(terminal) / denom
        ready = not unresolved and not unresolved_contradictions
        confidence = 0.38 + 0.46 * evidence_coverage + 0.10 * terminal_coverage
        confidence -= 0.10 * min(2, len(blocked))
        confidence -= 0.12 * min(2, len(unresolved_contradictions))
        confidence = max(0.0, min(0.99, confidence))

        return {
            "ready": ready,
            "evidence_coverage": round(evidence_coverage, 4),
            "terminal_coverage": round(terminal_coverage, 4),
            "unresolved": unresolved,
            "blocked": blocked,
            "contradictions": list(state.contradictions),
            "unresolved_contradictions": unresolved_contradictions,
            "stagnation": state.stagnation,
            "confidence": round(confidence, 4),
        }


class DeliberationEngine:
    """Capability-driven controller for evidence gathering and replanning.

    Mission structure and action-planning metadata come from the runtime
    CapabilityRegistry. This class owns generic utility scoring plus the vertical
    interpretation of observations; adding a capability that satisfies an
    existing or new evidence requirement does not require editing mission routing.
    """

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
        compiler: MissionCompiler | None = None,
    ) -> None:
        self.registry = registry or RUNTIME_CAPABILITIES
        self.compiler = compiler or MissionCompiler(self.registry)
        self.critic = TrajectoryCritic()

    def initialize(self, plan: AgentPlan, state: RunState) -> MissionGraph:
        if state.mission is None:
            state.mission = self.compiler.compile(plan)
        state.critic = self.critic.assess(state)
        return state.mission

    def decide(
        self,
        plan: AgentPlan,
        state: RunState,
        tools: list[ToolSpec],
        *,
        policy_bonus: Callable[[str], float],
    ) -> Decision:
        mission = self.initialize(plan, state)
        available = {tool.name: tool for tool in tools}
        self._refresh_blocked(state, available)

        completed = {
            str(row.get("tool") or "")
            for row in state.actions
            if row.get("status") == "completed"
        }
        failed = {
            str(row.get("tool") or "")
            for row in state.actions
            if row.get("status") == "failed"
        }
        candidates: list[Candidate] = []

        for req in mission.requirements.values():
            if req.status != "open":
                continue
            if any(
                mission.requirements.get(key) is None
                or mission.requirements[key].status != "satisfied"
                for key in req.prerequisites
            ):
                continue

            for tool_name in self._requirement_capabilities(req):
                spec = available.get(tool_name)
                if spec is None:
                    continue
                contract = self.registry.maybe_get(tool_name)
                if contract is not None and self._gate_failure(contract, state):
                    continue
                if tool_name in completed and not spec.repeatable:
                    continue

                learned = float(policy_bonus(f"{plan.mode}|{tool_name}"))
                priority = _PRIORITY.get(req.priority, 0.62)
                information_gain = (
                    float(contract.information_gain) if contract is not None else 0.60
                )
                evidence_gap = (
                    1.0
                    if req.priority == "critical"
                    else 0.82
                    if req.priority == "high"
                    else 0.62
                )
                relevant_hypotheses = tuple(
                    hyp.key
                    for hyp in mission.hypotheses.values()
                    if hyp.domain == req.domain
                    and hyp.status not in {"dismissed", "resolved"}
                )
                hypothesis_pressure = max(
                    (
                        mission.hypotheses[key].confidence
                        for key in relevant_hypotheses
                    ),
                    default=0.20,
                )
                contradiction_pressure = (
                    1.0
                    if any(
                        row.startswith(f"{req.domain}:")
                        for row in state.contradictions
                    )
                    else 0.0
                )
                cost_pressure = min(1.0, max(0.0, spec.cost / 6.5))
                risk_pressure = _RISK_PRESSURE.get(spec.risk, 0.08)
                failure_pressure = 1.0 if tool_name in failed else 0.0
                domain_novelty = 0.0
                if state.actions:
                    previous = str(state.actions[-1].get("tool") or "")
                    if previous.split(".", 1)[0] != tool_name.split(".", 1)[0]:
                        domain_novelty = 0.05
                stagnation_pressure = min(0.12, 0.04 * state.stagnation)

                score = (
                    0.30 * priority
                    + 0.22 * information_gain
                    + 0.18 * evidence_gap
                    + 0.12 * hypothesis_pressure
                    + 0.08 * contradiction_pressure
                    + domain_novelty
                    + learned
                    - 0.07 * cost_pressure
                    - 0.05 * risk_pressure
                    - 0.16 * failure_pressure
                    - stagnation_pressure * (0.5 if domain_novelty else 1.0)
                )
                utility = {
                    "priority": round(priority, 4),
                    "information_gain": round(information_gain, 4),
                    "evidence_gap": round(evidence_gap, 4),
                    "hypothesis_pressure": round(hypothesis_pressure, 4),
                    "contradiction_pressure": round(contradiction_pressure, 4),
                    "cost_pressure": round(cost_pressure, 4),
                    "risk_pressure": round(risk_pressure, 4),
                    "domain_novelty": round(domain_novelty, 4),
                    "stagnation_pressure": round(stagnation_pressure, 4),
                    "learned_bonus": round(learned, 4),
                }
                step = self._step_for_requirement(plan, req, tool_name)
                rationale = self._rationale(req, utility, mission, tool_name)
                candidates.append(
                    Candidate(
                        req,
                        step,
                        score,
                        learned,
                        utility,
                        relevant_hypotheses,
                        rationale,
                    )
                )

        if not candidates:
            self._refresh_blocked(state, available)
            state.critic = self.critic.assess(state)
            unresolved = state.critic.get("unresolved") or []
            if unresolved:
                reason = (
                    "当前仍有证据缺口，但没有满足前置条件、能力门槛或权限边界的可执行动作："
                    + "、".join(unresolved)
                )
            else:
                reason = "当前任务图中的关键证据已闭合，继续调用工具的边际价值不足"
            return Decision(None, reason)

        candidates.sort(
            key=lambda row: (-row.score, row.requirement.key, row.step.tool)
        )
        winner = candidates[0]
        alternatives = [
            {
                "tool": row.step.tool,
                "requirement": row.requirement.key,
                "score": round(row.score, 4),
                "reason": row.rationale,
                "utility": row.utility,
            }
            for row in candidates[1:4]
        ]
        return Decision(
            winner.step,
            winner.rationale,
            round(winner.score, 4),
            alternatives,
            round(winner.learned_bonus, 4),
            target_requirement=winner.requirement.key,
            utility=winner.utility,
            hypotheses=winner.hypotheses,
        )

    def reflect(
        self,
        plan: AgentPlan,
        state: RunState,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        mission = self.initialize(plan, state)
        tool = str(action.get("tool") or "")
        status = str(action.get("status") or "failed")
        result = action.get("result") if isinstance(action.get("result"), dict) else {}
        before_status = {key: req.status for key, req in mission.requirements.items()}
        before_hyp = {
            key: (hyp.status, round(hyp.confidence, 4))
            for key, hyp in mission.hypotheses.items()
        }
        before_contradictions = set(state.contradictions)

        matching = [
            req
            for req in mission.requirements.values()
            if tool in self._requirement_capabilities(req) and req.status == "open"
        ]
        contract = self.registry.maybe_get(tool)
        for req in matching:
            if status != "completed":
                req.status = "blocked"
                req.reason = str(action.get("error") or "tool execution failed")
                continue
            unmet = [
                key
                for key in (contract.completion_truthy if contract is not None else ())
                if not bool(result.get(key))
            ]
            if unmet:
                req.status = "blocked"
                req.reason = "required completion evidence is unavailable: " + ", ".join(unmet)
            else:
                req.status = "satisfied"
                req.satisfied_by.append(str(action.get("invocation_id") or tool))
                req.reason = "capability completed and declared evidence was consumed"

        if status == "completed" and contract is not None:
            self._interpret_observation(contract, mission, state, result, tool)

        changed_requirements = [
            key
            for key, req in mission.requirements.items()
            if before_status.get(key) != req.status
        ]
        changed_hypotheses = [
            key
            for key, hyp in mission.hypotheses.items()
            if before_hyp.get(key) != (hyp.status, round(hyp.confidence, 4))
        ]
        new_contradictions = [
            row for row in state.contradictions if row not in before_contradictions
        ]
        progress = bool(
            changed_requirements or changed_hypotheses or new_contradictions
        )
        if progress:
            state.stagnation = 0
            state.last_progress_cycle = state.cycle
        else:
            state.stagnation += 1

        state.critic = self.critic.assess(state)
        next_gaps = [
            req.key
            for req in mission.requirements.values()
            if req.status == "open" and not req.optional
        ][:5]
        reflection = {
            "cycle": state.cycle,
            "tool": tool,
            "progress": progress,
            "requirements_changed": changed_requirements,
            "hypotheses_changed": changed_hypotheses,
            "new_contradictions": new_contradictions,
            "next_gaps": next_gaps,
            "critic": dict(state.critic),
            "summary": self._reflection_summary(
                changed_requirements,
                changed_hypotheses,
                new_contradictions,
                next_gaps,
            ),
        }
        state.reflections.append(reflection)
        return reflection

    def critique(self, plan: AgentPlan, state: RunState) -> dict[str, Any]:
        self.initialize(plan, state)
        state.critic = self.critic.assess(state)
        return dict(state.critic)

    def _refresh_blocked(
        self,
        state: RunState,
        available: dict[str, ToolSpec],
    ) -> None:
        mission = state.mission
        if mission is None:
            return
        for req in mission.requirements.values():
            if req.status != "open":
                continue
            options = self._requirement_capabilities(req)
            configured = [name for name in options if name in available]
            if not configured:
                req.status = "blocked"
                req.reason = "required capability is not configured in this runtime"
                continue
            if any(
                mission.requirements.get(key) is None
                or mission.requirements[key].status != "satisfied"
                for key in req.prerequisites
            ):
                continue
            failures = []
            for name in configured:
                contract = self.registry.maybe_get(name)
                failure = self._gate_failure(contract, state) if contract else None
                if failure is None:
                    break
                failures.append(failure)
            else:
                if failures:
                    req.status = "blocked"
                    req.reason = "; ".join(sorted(set(failures)))

    @staticmethod
    def _requirement_capabilities(req: EvidenceRequirement) -> tuple[str, ...]:
        if req.capabilities:
            return req.capabilities
        return (req.tool,) if req.tool else ()

    @staticmethod
    def _gate_failure(
        contract: CapabilityContract | None,
        state: RunState,
    ) -> str | None:
        if contract is None:
            return None
        for gate in contract.gates:
            failure = gate.failure(state.observations)
            if failure:
                return failure
        return None

    def _step_for_requirement(
        self,
        plan: AgentPlan,
        req: EvidenceRequirement,
        tool_name: str,
    ) -> PlanStep:
        contract = self.registry.maybe_get(tool_name)
        if contract is None:
            return PlanStep(tool_name, req.label, req.label, {})

        values = {
            "query": plan.query or "当前查询",
            "user_id": plan.user_id or "当前用户",
            "goal": plan.goal,
            "allow_adaptation": plan.allow_adaptation,
            "allow_network": plan.allow_network,
            "mode": plan.mode,
        }
        detail = contract.detail or req.label
        try:
            detail = detail.format_map(values)
        except (KeyError, ValueError):
            detail = contract.detail or req.label
        args: dict[str, Any] = {}
        for argument, attribute in contract.argument_bindings:
            args[argument] = getattr(plan, attribute, None)
        return PlanStep(
            tool_name,
            contract.title or req.label,
            detail,
            args,
        )

    @staticmethod
    def _rationale(
        req: EvidenceRequirement,
        utility: dict[str, float],
        mission: MissionGraph,
        tool_name: str,
    ) -> str:
        hypothesis = [
            hyp.label
            for hyp in mission.hypotheses.values()
            if hyp.domain == req.domain
            and hyp.status not in {"dismissed", "resolved"}
            and hyp.confidence >= 0.55
        ]
        why = f"任务图仍缺少“{req.label}”"
        if len(req.capabilities) > 1:
            why += f"；当前选择能力 {tool_name}"
        if hypothesis:
            why += "；当前高置信假设：" + "、".join(hypothesis[:2])
        if utility.get("contradiction_pressure", 0.0) > 0:
            why += "；现有证据存在冲突，需要优先消解"
        return why

    def _interpret_observation(
        self,
        contract: CapabilityContract,
        mission: MissionGraph,
        state: RunState,
        result: dict[str, Any],
        tool: str,
    ) -> None:
        profile = contract.reflection_profile
        if profile == "search_reproduction":
            weak = self._search_is_weak(result)
            diagnosis = mission.requirements.get("search_diagnosis")
            if weak:
                if diagnosis and diagnosis.status == "dormant":
                    diagnosis.status = "open"
                    diagnosis.reason = "reproduction exposed weak or missing match evidence"
                self._support(mission, "search_local_mismatch", 0.84, tool)
            else:
                self._dismiss(mission, "search_local_mismatch", 0.18, tool)
            return

        if profile == "recommend_reproduction":
            cold = self._recommend_is_cold(result)
            diagnosis = mission.requirements.get("recommend_diagnosis")
            if cold:
                if diagnosis and diagnosis.status == "dormant":
                    diagnosis.status = "open"
                    diagnosis.reason = "reproduction exposed cold-start or empty-slate evidence"
                self._support(mission, "recommend_cold_start", 0.86, tool)
            else:
                self._dismiss(mission, "recommend_cold_start", 0.20, tool)
            return

        if profile == "search_audit":
            quality = float(result.get("quality", 0.0) or 0.0)
            if quality < 0.65:
                self._support(
                    mission,
                    "search_systemic_gap",
                    min(0.92, 0.58 + (0.65 - quality)),
                    tool,
                )
            else:
                self._dismiss(mission, "search_systemic_gap", 0.24, tool)
            search_run = state.observations.get("search.run") or {}
            if search_run and not self._search_is_weak(search_run) and quality < 0.45:
                self._contradiction(
                    state,
                    "search: 单点复现正常，但全局搜索审计显著偏低",
                )
                diagnosis = mission.requirements.get("search_diagnosis")
                if diagnosis and diagnosis.status == "dormant":
                    diagnosis.status = "open"
                    diagnosis.reason = "single-query evidence conflicts with global audit"
            return

        if profile == "recommend_audit":
            coverage = float(result.get("coverage", 0.0) or 0.0)
            quality = float(result.get("quality", coverage) or coverage)
            if coverage < 0.45 or quality < 0.55:
                self._support(mission, "recommend_systemic_gap", 0.78, tool)
            else:
                self._dismiss(mission, "recommend_systemic_gap", 0.24, tool)
            rec_run = state.observations.get("recommend.run") or {}
            if rec_run and not self._recommend_is_cold(rec_run) and coverage < 0.25:
                self._contradiction(
                    state,
                    "recommend: 单用户首屏可用，但全局推荐覆盖显著偏低",
                )
                diagnosis = mission.requirements.get("recommend_diagnosis")
                if diagnosis and diagnosis.status == "dormant":
                    diagnosis.status = "open"
                    diagnosis.reason = "single-user evidence conflicts with global audit"
            return

        if profile == "search_diagnosis":
            self._resolve_domain_contradictions(state, "search")
        elif profile == "recommend_diagnosis":
            self._resolve_domain_contradictions(state, "recommend")

    @staticmethod
    def _search_is_weak(result: dict[str, Any]) -> bool:
        rows = result.get("results") or []
        if not rows:
            return True
        signals = rows[0].get("signals") if isinstance(rows[0], dict) else {}
        try:
            return float((signals or {}).get("match", 1.0)) < 0.42
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _recommend_is_cold(result: dict[str, Any]) -> bool:
        rows = result.get("results") or []
        return not rows or int(result.get("history_events", 0) or 0) == 0

    @staticmethod
    def _support(
        mission: MissionGraph,
        key: str,
        confidence: float,
        evidence: str,
    ) -> None:
        hyp = mission.hypotheses.get(key)
        if hyp is None:
            return
        hyp.confidence = max(hyp.confidence, min(0.99, confidence))
        hyp.status = "supported"
        if evidence not in hyp.supporting_evidence:
            hyp.supporting_evidence.append(evidence)

    @staticmethod
    def _dismiss(
        mission: MissionGraph,
        key: str,
        confidence: float,
        evidence: str,
    ) -> None:
        hyp = mission.hypotheses.get(key)
        if hyp is None:
            return
        hyp.confidence = min(hyp.confidence, max(0.01, confidence))
        hyp.status = "dismissed"
        if evidence not in hyp.contradicting_evidence:
            hyp.contradicting_evidence.append(evidence)

    @staticmethod
    def _contradiction(state: RunState, text: str) -> None:
        if text not in state.contradictions:
            state.contradictions.append(text)

    @staticmethod
    def _resolve_domain_contradictions(state: RunState, domain: str) -> None:
        state.contradictions[:] = [
            row for row in state.contradictions if not row.startswith(f"{domain}:")
        ]

    @staticmethod
    def _reflection_summary(
        changed_requirements: list[str],
        changed_hypotheses: list[str],
        contradictions: list[str],
        next_gaps: list[str],
    ) -> str:
        parts = []
        if changed_requirements:
            parts.append("证据需求更新：" + "、".join(changed_requirements[:3]))
        if changed_hypotheses:
            parts.append("假设更新：" + "、".join(changed_hypotheses[:3]))
        if contradictions:
            parts.append("发现冲突：" + "；".join(contradictions[:2]))
        if next_gaps:
            parts.append("下一批缺口：" + "、".join(next_gaps[:3]))
        return (
            "；".join(parts)
            if parts
            else "本轮没有形成新的高价值状态变化，控制器将降低重复路径优先级"
        )


__all__ = ["DeliberationEngine", "TrajectoryCritic"]
