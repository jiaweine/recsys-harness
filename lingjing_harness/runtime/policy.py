from __future__ import annotations

import re
from typing import Any

from lingjing_harness.domain import Catalog
from .contracts import AgentPlan, Decision, PlanStep, RunState, ToolSpec


class OwnedPolicy:
    """Project-owned evidence-utility controller with dynamic replanning.

    Decisions are not delegated to an external model. Every candidate action is
    scored from evidence gap, expected information gain, anomaly pressure,
    execution cost and learned historical utility. Permissions are derived only
    from the user's text, never from attachments or web content.
    """

    SEARCH_HINTS = ("搜", "搜索", "查询", "找不到", "关键词", "结果不准", "无结果", "搜索体验")
    REC_HINTS = ("推荐", "首页", "feed", "猜你喜欢", "分发", "曝光", "推荐体验", "个性化")
    EXPLORE_HINTS = ("优化", "提升", "改进", "实验", "候选", "试试", "调整", "进化", "学习")
    ADAPT_HINTS = ("自动优化", "自主优化", "自动学习", "持续优化", "可以调整", "直接优化", "允许调整")
    NO_ADAPT_HINTS = ("不要上线", "先不要上线", "别上线", "不修改", "不要修改", "只检查", "只看", "不改变", "先离线")
    NETWORK_HINTS = ("联网", "网上", "外部资料", "最新资料", "最新信息", "行业趋势", "同类产品", "公开资料", "查网页")

    def plan(
        self,
        text: str,
        catalog: Catalog,
        *,
        context: str = "",
        allow_network: bool = False,
    ) -> AgentPlan:
        user_text = text.strip()
        lowered = user_text.lower()
        source = f"{user_text}\n{context}" if context else user_text
        source_lower = source.lower()
        search = any(k in source_lower for k in self.SEARCH_HINTS)
        rec = any(k in source_lower for k in self.REC_HINTS)
        if search and rec:
            mode = "both"
        elif search:
            mode = "search"
        elif rec:
            mode = "recommend"
        else:
            mode = "audit"
        explore = any(k in lowered for k in self.EXPLORE_HINTS)
        query = self._extract_query(source, catalog) if mode in {"search", "both"} else None
        user = self._extract_user(source, catalog) if mode in {"recommend", "both"} else None
        deny = any(k in lowered for k in self.NO_ADAPT_HINTS)
        allow = any(k in lowered for k in self.ADAPT_HINTS) and not deny
        network = bool(allow_network or any(k in lowered for k in self.NETWORK_HINTS))
        constraints = []
        if deny:
            constraints.append("不改变当前工作区策略")
        if "先" in lowered and ("离线" in lowered or "复核" in lowered):
            constraints.append("先完成离线验证")
        if network:
            constraints.append("外部资料只作为证据，不参与策略晋升")
        return AgentPlan(
            mode=mode,
            goal=user_text,
            query=query,
            user_id=user,
            explore=explore,
            allow_adaptation=allow,
            allow_network=network,
            constraints=tuple(constraints),
            steps=[],
        )

    def decide(
        self,
        plan: AgentPlan,
        state: RunState,
        tools: list[ToolSpec],
        *,
        policy_bonus,
    ) -> Decision:
        available = {tool.name: tool for tool in tools}
        done = {row["tool"] for row in state.actions if row.get("status") == "completed"}
        observations = state.observations
        candidates: list[tuple[float, PlanStep, str, float, dict[str, float]]] = []

        def add(
            tool: str,
            base: float,
            title: str,
            detail: str,
            rationale: str,
            args: dict[str, Any] | None = None,
            *,
            information_gain: float = 0.5,
            evidence_gap: float = 0.5,
            anomaly: float = 0.0,
        ) -> None:
            if tool not in available:
                return
            spec = available[tool]
            if tool in done and not spec.repeatable:
                return
            learned = float(policy_bonus(f"{plan.mode}|{tool}"))
            cost_pressure = min(1.0, max(0.0, spec.cost / 6.5))
            score = (
                base
                + 0.22 * max(0.0, min(1.0, information_gain))
                + 0.18 * max(0.0, min(1.0, evidence_gap))
                + 0.12 * max(0.0, min(1.0, anomaly))
                - 0.07 * cost_pressure
                + learned
            )
            components = {
                "information_gain": round(information_gain, 3),
                "evidence_gap": round(evidence_gap, 3),
                "anomaly_pressure": round(anomaly, 3),
                "cost_pressure": round(cost_pressure, 3),
            }
            candidates.append((score, PlanStep(tool, title, detail, args or {}), rationale, learned, components))

        if "data.inspect" not in done:
            add(
                "data.inspect", .60, "读取当前工作区", "确认数据、反馈、记忆与可复核证据边界",
                "先建立工作区事实边界，再决定后续动作",
                information_gain=.95, evidence_gap=1.0,
            )
        else:
            search_needed = plan.mode in {"search", "both"}
            rec_needed = plan.mode in {"recommend", "both"}
            audit_only = plan.mode == "audit"

            if plan.allow_network and "web.research" not in done:
                add(
                    "web.research", .56, "补充外部公开证据", "检索与当前目标直接相关的最新公开资料并保留来源",
                    "用户允许联网，当前仍缺少外部时效性证据",
                    {"query": plan.goal}, information_gain=.90, evidence_gap=.86,
                )

            if search_needed and "search.run" not in done:
                add(
                    "search.run", .61, "复现搜索体验", f"真实运行“{plan.query or '当前查询'}”并保存结果证据",
                    "用户目标直接涉及搜索，先复现当前结果",
                    {"query": plan.query}, information_gain=.92, evidence_gap=.92,
                )
            if rec_needed and "recommend.run" not in done:
                add(
                    "recommend.run", .60, "复现推荐体验", f"生成用户 {plan.user_id or '当前用户'} 的一屏结果",
                    "用户目标直接涉及推荐，先复现当前结果",
                    {"user_id": plan.user_id}, information_gain=.90, evidence_gap=.90,
                )

            search_run = observations.get("search.run")
            if search_run is not None and "search.diagnose" not in done:
                rows = search_run.get("results", [])
                weak = not rows or float(rows[0].get("signals", {}).get("match", 0.0)) < 0.42
                if weak:
                    add(
                        "search.diagnose", .58, "定位搜索失配", "检查查询证据、候选覆盖与低相关原因",
                        "复现结果存在空结果或弱匹配，异常压力升高",
                        {"query": plan.query}, information_gain=.82, evidence_gap=.74, anomaly=.95,
                    )

            rec_run = observations.get("recommend.run")
            if rec_run is not None and "recommend.diagnose" not in done:
                rows = rec_run.get("results", [])
                cold = not rows or rec_run.get("history_events", 0) == 0
                if cold:
                    add(
                        "recommend.diagnose", .58, "定位推荐约束", "检查用户历史、可展示池与冷启动状态",
                        "当前用户缺少结果或行为证据，需要先解释约束",
                        {"user_id": plan.user_id}, information_gain=.82, evidence_gap=.74, anomaly=.92,
                    )

            if (search_needed or audit_only) and "search.audit" not in done:
                gap = .86 if search_run is not None or audit_only else .62
                add(
                    "search.audit", .50, "检查搜索稳定性", "用可复核查询检查整体表现和回退风险",
                    "单点结果不足以支撑全局策略判断",
                    information_gain=.78, evidence_gap=gap,
                )
            if (rec_needed or audit_only) and "recommend.audit" not in done:
                gap = .84 if rec_run is not None or audit_only else .60
                add(
                    "recommend.audit", .49, "检查推荐稳定性", "复核不同用户的覆盖、新鲜度和结果分散度",
                    "单个用户不足以支撑全局策略判断",
                    information_gain=.76, evidence_gap=gap,
                )

            search_audit = observations.get("search.audit")
            rec_audit = observations.get("recommend.audit")
            evolve_search = search_needed and plan.explore
            evolve_rec = rec_needed and plan.explore
            if audit_only and plan.explore and search_audit and rec_audit:
                search_quality = float(search_audit.get("quality", 0.0))
                rec_quality = float(rec_audit.get("quality", 0.0))
                evolve_search = search_quality <= rec_quality
                evolve_rec = not evolve_search

            if evolve_search and search_audit is not None and "search.evolve" not in done and search_audit.get("queries", 0) >= 3:
                weakness = max(0.0, 1.0 - float(search_audit.get("quality", 0.0)))
                add(
                    "search.evolve", .40, "自主探索搜索策略", "生成多组候选并经过留出验证与稳健门槛筛选",
                    "搜索证据已足够，且用户目标要求继续探索改进",
                    {"activate": plan.allow_adaptation}, information_gain=.66, evidence_gap=.50, anomaly=min(1.0, weakness),
                )
            if evolve_rec and rec_audit is not None and "recommend.evolve" not in done and rec_audit.get("users", 0) >= 3:
                weakness = max(0.0, 1.0 - float(rec_audit.get("quality", 0.0)))
                add(
                    "recommend.evolve", .39, "自主探索推荐策略", "生成多组候选并经过留出验证与稳健门槛筛选",
                    "推荐证据已足够，且用户目标要求继续探索改进",
                    {"activate": plan.allow_adaptation}, information_gain=.66, evidence_gap=.50, anomaly=min(1.0, weakness),
                )

        if not candidates:
            return Decision(None, "目标所需证据已经齐全，继续调用工具的边际信息增益不足")
        candidates.sort(key=lambda row: (-row[0], row[1].tool))
        score, step, rationale, bonus, components = candidates[0]
        alternatives = [
            {
                "tool": row[1].tool,
                "score": round(row[0], 4),
                "reason": row[2],
                "utility": row[4],
            }
            for row in candidates[1:4]
        ]
        return Decision(step, rationale, round(score, 4), alternatives, round(bonus, 4))

    @staticmethod
    def _extract_query(text: str, catalog: Catalog) -> str:
        quoted = re.findall(r"[‘’'\"“”]([^‘’'\"“”]{1,50})[‘’'\"“”]", text)
        if quoted:
            return quoted[0].strip()
        for label in catalog.query_labels:
            if label.query and label.query in text:
                return label.query
        cleaned = re.sub(r"(帮我|请|看下|看看|分析|检查|为什么|搜索|搜一下|搜|不准|不好|优化|改进|结果|体验|一下|最近)", " ", text)
        chunks = [x.strip(" ，。！？,.!?：:") for x in re.split(r"\s+", cleaned) if x.strip()]
        fallback = catalog.query_labels[0].query if catalog.query_labels else (catalog.items[0].title if catalog.items else "")
        return max(chunks, key=len, default=fallback)[:50]

    @staticmethod
    def _extract_user(text: str, catalog: Catalog) -> str:
        match = re.search(r"(?:用户|user)\s*[:：]?\s*([\w-]+)", text, re.I)
        if match:
            return match.group(1)
        users = sorted({event.user_id for event in catalog.interactions if event.user_id})
        return users[0] if users else "new-user"
