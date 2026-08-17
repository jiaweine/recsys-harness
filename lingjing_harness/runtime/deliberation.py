from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .contracts import (
    AgentPlan,
    Decision,
    EvidenceRequirement,
    Hypothesis,
    MissionGraph,
    PlanStep,
    RunState,
    ToolSpec,
)


_PRIORITY = {"critical": 1.0, "high": 0.82, "medium": 0.62, "low": 0.42}
_INFORMATION_GAIN = {
    "data.inspect": 1.00,
    "search.run": 0.95,
    "recommend.run": 0.94,
    "search.diagnose": 0.88,
    "recommend.diagnose": 0.88,
    "search.audit": 0.80,
    "recommend.audit": 0.80,
    "web.research": 0.76,
    "search.evolve": 0.70,
    "recommend.evolve": 0.70,
}
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

        active = [req for req in mission.requirements.values() if req.status != "dormant" and not req.optional]
        satisfied = [req for req in active if req.status == "satisfied"]
        terminal = [req for req in active if req.status in {"satisfied", "blocked"}]
        unresolved = [
            req.key
            for req in active
            if req.status == "open" and req.priority in {"critical", "high"}
        ]
        blocked = [req.key for req in active if req.status == "blocked"]

        unresolved_contradictions = []
        for contradiction in state.contradictions:
            domain = contradiction.split(":", 1)[0]
            diagnosis = mission.requirements.get(f"{domain}_diagnosis")
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
    """Mission-driven controller for evidence gathering, reflection and replanning.

    The engine deliberately separates *what evidence is still needed* from *which
    tool should run next*. This keeps the controller inspectable without reducing
    it to a fixed tool sequence.
    """

    def __init__(self) -> None:
        self.critic = TrajectoryCritic()

    def initialize(self, plan: AgentPlan, state: RunState) -> MissionGraph:
        if state.mission is not None:
            return state.mission

        requirements: dict[str, EvidenceRequirement] = {}
        hypotheses: dict[str, Hypothesis] = {}

        def require(
            key: str,
            label: str,
            domain: str,
            tool: str,
            priority: str,
            *,
            prerequisites: tuple[str, ...] = (),
            status: str = "open",
            optional: bool = False,
        ) -> None:
            requirements[key] = EvidenceRequirement(
                key=key,
                label=label,
                domain=domain,
                tool=tool,
                priority=priority,
                status=status,
                prerequisites=prerequisites,
                optional=optional,
            )

        def hypothesize(key: str, label: str, domain: str, confidence: float) -> None:
            hypotheses[key] = Hypothesis(key, label, domain, confidence=confidence)

        require("workspace_facts", "建立工作区事实边界", "general", "data.inspect", "critical")

        if plan.allow_network:
            require(
                "external_context",
                "补充带来源的外部时效证据",
                "external",
                "web.research",
                "medium",
                prerequisites=("workspace_facts",),
            )

        search_scope = plan.mode in {"search", "both"}
        recommend_scope = plan.mode in {"recommend", "both"}
        audit_scope = plan.mode == "audit"

        if search_scope:
            require(
                "search_reproduction",
                "复现当前搜索结果",
                "search",
                "search.run",
                "critical",
                prerequisites=("workspace_facts",),
            )
            require(
                "search_diagnosis",
                "解释搜索异常与证据缺口",
                "search",
                "search.diagnose",
                "high",
                prerequisites=("search_reproduction",),
                status="dormant",
            )
            hypothesize("search_local_mismatch", "当前查询存在匹配或候选覆盖缺口", "search", 0.42)

        if recommend_scope:
            require(
                "recommend_reproduction",
                "复现当前推荐首屏",
                "recommend",
                "recommend.run",
                "critical",
                prerequisites=("workspace_facts",),
            )
            require(
                "recommend_diagnosis",
                "解释推荐异常、冷启动或展示约束",
                "recommend",
                "recommend.diagnose",
                "high",
                prerequisites=("recommend_reproduction",),
                status="dormant",
            )
            hypothesize("recommend_cold_start", "当前用户缺少足够行为或可展示证据", "recommend", 0.34)

        need_search_audit = audit_scope or plan.mode == "both" or (plan.explore and search_scope)
        need_recommend_audit = audit_scope or plan.mode == "both" or (plan.explore and recommend_scope)

        if need_search_audit:
            require(
                "search_global_quality",
                "检查搜索整体稳定性与回退风险",
                "search",
                "search.audit",
                "high",
                prerequisites=("workspace_facts",),
            )
            hypothesize("search_systemic_gap", "问题可能是整体搜索质量缺口而非单点异常", "search", 0.32)

        if need_recommend_audit:
            require(
                "recommend_global_quality",
                "检查推荐覆盖、新鲜度与分散度",
                "recommend",
                "recommend.audit",
                "high",
                prerequisites=("workspace_facts",),
            )
            hypothesize("recommend_systemic_gap", "问题可能是整体推荐质量缺口而非单用户异常", "recommend", 0.32)

        if plan.explore and (search_scope or audit_scope):
            require(
                "search_candidate_validation",
                "探索并验证搜索候选策略",
                "search",
                "search.evolve",
                "high",
                prerequisites=("search_global_quality",),
            )
        if plan.explore and (recommend_scope or audit_scope):
            require(
                "recommend_candidate_validation",
                "探索并验证推荐候选策略",
                "recommend",
                "recommend.evolve",
                "high",
                prerequisites=("recommend_global_quality",),
            )

        state.mission = MissionGraph(
            objective=plan.goal,
            mode=plan.mode,
            requirements=requirements,
            hypotheses=hypotheses,
            exit_criteria=(
                "critical/high evidence requirements are terminal",
                "material contradictions are investigated",
                "tool and permission budgets remain respected",
                "learning is independently verified before trust",
            ),
        )
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
        self._refresh_blocked(plan, state, available)

        completed = {row.get("tool") for row in state.actions if row.get("status") == "completed"}
        failed = {row.get("tool") for row in state.actions if row.get("status") == "failed"}
        candidates: list[Candidate] = []

        for req in mission.requirements.values():
            if req.status != "open":
                continue
            if any(mission.requirements.get(key) is None or mission.requirements[key].status != "satisfied" for key in req.prerequisites):
                continue
            spec = available.get(req.tool)
            if spec is None:
                continue
            if req.tool in completed and not spec.repeatable:
                continue

            learned = float(policy_bonus(f"{plan.mode}|{req.tool}"))
            priority = _PRIORITY.get(req.priority, 0.62)
            information_gain = _INFORMATION_GAIN.get(req.tool, 0.60)
            evidence_gap = 1.0 if req.priority == "critical" else 0.82 if req.priority == "high" else 0.62
            relevant_hypotheses = tuple(
                hyp.key
                for hyp in mission.hypotheses.values()
                if hyp.domain == req.domain and hyp.status not in {"dismissed", "resolved"}
            )
            hypothesis_pressure = max(
                (mission.hypotheses[key].confidence for key in relevant_hypotheses),
                default=0.20,
            )
            contradiction_pressure = 1.0 if any(row.startswith(f"{req.domain}:") for row in state.contradictions) else 0.0
            cost_pressure = min(1.0, max(0.0, spec.cost / 6.5))
            risk_pressure = _RISK_PRESSURE.get(spec.risk, 0.08)
            failure_pressure = 1.0 if req.tool in failed else 0.0
            domain_novelty = 0.0
            if state.actions:
                previous = str(state.actions[-1].get("tool") or "")
                if previous.split(".", 1)[0] != req.tool.split(".", 1)[0]:
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
            step = self._step_for_requirement(plan, req)
            rationale = self._rationale(req, utility, mission)
            candidates.append(
                Candidate(req, step, score, learned, utility, relevant_hypotheses, rationale)
            )

        if not candidates:
            state.critic = self.critic.assess(state)
            unresolved = state.critic.get("unresolved") or []
            if unresolved:
                reason = "当前仍有证据缺口，但没有满足前置条件或权限边界的可执行动作：" + "、".join(unresolved)
            else:
                reason = "当前任务图中的关键证据已闭合，继续调用工具的边际价值不足"
            return Decision(None, reason)

        candidates.sort(key=lambda row: (-row.score, row.requirement.key, row.step.tool))
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

    def reflect(self, plan: AgentPlan, state: RunState, action: dict[str, Any]) -> dict[str, Any]:
        mission = self.initialize(plan, state)
        tool = str(action.get("tool") or "")
        status = str(action.get("status") or "failed")
        result = action.get("result") if isinstance(action.get("result"), dict) else {}
        before_status = {key: req.status for key, req in mission.requirements.items()}
        before_hyp = {key: (hyp.status, round(hyp.confidence, 4)) for key, hyp in mission.hypotheses.items()}
        before_contradictions = set(state.contradictions)

        matching = [req for req in mission.requirements.values() if req.tool == tool and req.status == "open"]
        for req in matching:
            if status != "completed":
                req.status = "blocked"
                req.reason = str(action.get("error") or "tool execution failed")
                continue
            if tool in {"search.evolve", "recommend.evolve"} and not result.get("evaluation_ready", True):
                req.status = "blocked"
                req.reason = "insufficient independent evaluation evidence"
            else:
                req.status = "satisfied"
                req.satisfied_by.append(str(action.get("invocation_id") or tool))
                req.reason = "tool completed and evidence was consumed"

        if status == "completed":
            if tool == "search.run":
                weak = self._search_is_weak(result)
                diagnosis = mission.requirements.get("search_diagnosis")
                if weak:
                    if diagnosis and diagnosis.status == "dormant":
                        diagnosis.status = "open"
                        diagnosis.reason = "reproduction exposed weak or missing match evidence"
                    self._support(mission, "search_local_mismatch", 0.84, tool)
                else:
                    self._dismiss(mission, "search_local_mismatch", 0.18, tool)
            elif tool == "recommend.run":
                cold = self._recommend_is_cold(result)
                diagnosis = mission.requirements.get("recommend_diagnosis")
                if cold:
                    if diagnosis and diagnosis.status == "dormant":
                        diagnosis.status = "open"
                        diagnosis.reason = "reproduction exposed cold-start or empty-slate evidence"
                    self._support(mission, "recommend_cold_start", 0.86, tool)
                else:
                    self._dismiss(mission, "recommend_cold_start", 0.20, tool)
            elif tool == "search.audit":
                quality = float(result.get("quality", 0.0) or 0.0)
                if quality < 0.65:
                    self._support(mission, "search_systemic_gap", min(0.92, 0.58 + (0.65 - quality)), tool)
                else:
                    self._dismiss(mission, "search_systemic_gap", 0.24, tool)
                search_run = state.observations.get("search.run") or {}
                if search_run and not self._search_is_weak(search_run) and quality < 0.45:
                    self._contradiction(state, "search: 单点复现正常，但全局搜索审计显著偏低")
                    diagnosis = mission.requirements.get("search_diagnosis")
                    if diagnosis and diagnosis.status == "dormant":
                        diagnosis.status = "open"
                        diagnosis.reason = "single-query evidence conflicts with global audit"
            elif tool == "recommend.audit":
                coverage = float(result.get("coverage", 0.0) or 0.0)
                quality = float(result.get("quality", coverage) or coverage)
                if coverage < 0.45 or quality < 0.55:
                    self._support(mission, "recommend_systemic_gap", 0.78, tool)
                else:
                    self._dismiss(mission, "recommend_systemic_gap", 0.24, tool)
                rec_run = state.observations.get("recommend.run") or {}
                if rec_run and not self._recommend_is_cold(rec_run) and coverage < 0.25:
                    self._contradiction(state, "recommend: 单用户首屏可用，但全局推荐覆盖显著偏低")
                    diagnosis = mission.requirements.get("recommend_diagnosis")
                    if diagnosis and diagnosis.status == "dormant":
                        diagnosis.status = "open"
                        diagnosis.reason = "single-user evidence conflicts with global audit"
            elif tool == "search.diagnose":
                self._resolve_domain_contradictions(state, "search")
            elif tool == "recommend.diagnose":
                self._resolve_domain_contradictions(state, "recommend")

        changed_requirements = [
            key for key, req in mission.requirements.items() if before_status.get(key) != req.status
        ]
        changed_hypotheses = [
            key
            for key, hyp in mission.hypotheses.items()
            if before_hyp.get(key) != (hyp.status, round(hyp.confidence, 4))
        ]
        new_contradictions = [row for row in state.contradictions if row not in before_contradictions]
        progress = bool(changed_requirements or changed_hypotheses or new_contradictions)
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
            "summary": self._reflection_summary(changed_requirements, changed_hypotheses, new_contradictions, next_gaps),
        }
        state.reflections.append(reflection)
        return reflection

    def critique(self, plan: AgentPlan, state: RunState) -> dict[str, Any]:
        self.initialize(plan, state)
        state.critic = self.critic.assess(state)
        return dict(state.critic)

    def _refresh_blocked(self, plan: AgentPlan, state: RunState, available: dict[str, ToolSpec]) -> None:
        mission = state.mission
        if mission is None:
            return
        for req in mission.requirements.values():
            if req.status != "open":
                continue
            if req.tool not in available:
                req.status = "blocked"
                req.reason = "required capability is not configured in this runtime"
                continue
            if req.tool == "search.evolve":
                audit_req = mission.requirements.get("search_global_quality")
                if audit_req and audit_req.status == "satisfied":
                    audit = state.observations.get("search.audit") or {}
                    if int(audit.get("queries", 0) or 0) < 3:
                        req.status = "blocked"
                        req.reason = "fewer than 3 searchable evaluation queries"
            if req.tool == "recommend.evolve":
                audit_req = mission.requirements.get("recommend_global_quality")
                if audit_req and audit_req.status == "satisfied":
                    audit = state.observations.get("recommend.audit") or {}
                    if int(audit.get("users", 0) or 0) < 3:
                        req.status = "blocked"
                        req.reason = "fewer than 3 evaluable recommendation users"

    @staticmethod
    def _step_for_requirement(plan: AgentPlan, req: EvidenceRequirement) -> PlanStep:
        titles = {
            "data.inspect": ("读取当前工作区", "确认数据、反馈、记忆与可复核证据边界"),
            "web.research": ("补充外部公开证据", "检索与当前目标直接相关的公开资料并保留来源"),
            "search.run": ("复现搜索体验", f"真实运行“{plan.query or '当前查询'}”并保存结果证据"),
            "search.diagnose": ("定位搜索失配", "检查查询证据、候选覆盖与低相关原因"),
            "search.audit": ("检查搜索稳定性", "用可复核查询检查整体表现和回退风险"),
            "search.evolve": ("探索搜索候选策略", "生成候选并经过留出验证、回归与稳健门槛筛选"),
            "recommend.run": ("复现推荐体验", f"生成用户 {plan.user_id or '当前用户'} 的一屏真实结果"),
            "recommend.diagnose": ("定位推荐约束", "检查用户历史、可展示池与冷启动状态"),
            "recommend.audit": ("检查推荐稳定性", "复核覆盖、新鲜度、质量与结果分散度"),
            "recommend.evolve": ("探索推荐候选策略", "生成候选并经过留出验证、回归与稳健门槛筛选"),
        }
        title, detail = titles.get(req.tool, (req.label, req.label))
        args: dict[str, Any] = {}
        if req.tool in {"search.run", "search.diagnose"}:
            args["query"] = plan.query
        elif req.tool in {"recommend.run", "recommend.diagnose"}:
            args["user_id"] = plan.user_id
        elif req.tool in {"search.evolve", "recommend.evolve"}:
            args["activate"] = plan.allow_adaptation
        elif req.tool == "web.research":
            args["query"] = plan.goal
        return PlanStep(req.tool, title, detail, args)

    @staticmethod
    def _rationale(req: EvidenceRequirement, utility: dict[str, float], mission: MissionGraph) -> str:
        hypothesis = [
            hyp.label
            for hyp in mission.hypotheses.values()
            if hyp.domain == req.domain and hyp.status not in {"dismissed", "resolved"} and hyp.confidence >= 0.55
        ]
        why = f"任务图仍缺少“{req.label}”"
        if hypothesis:
            why += "；当前高置信假设：" + "、".join(hypothesis[:2])
        if utility.get("contradiction_pressure", 0.0) > 0:
            why += "；现有证据存在冲突，需要优先消解"
        return why

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
    def _support(mission: MissionGraph, key: str, confidence: float, evidence: str) -> None:
        hyp = mission.hypotheses.get(key)
        if hyp is None:
            return
        hyp.confidence = max(hyp.confidence, min(0.99, confidence))
        hyp.status = "supported"
        if evidence not in hyp.supporting_evidence:
            hyp.supporting_evidence.append(evidence)

    @staticmethod
    def _dismiss(mission: MissionGraph, key: str, confidence: float, evidence: str) -> None:
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
        state.contradictions[:] = [row for row in state.contradictions if not row.startswith(f"{domain}:")]

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
        return "；".join(parts) if parts else "本轮没有形成新的高价值状态变化，控制器将降低重复路径优先级"
