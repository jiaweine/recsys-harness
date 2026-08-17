from __future__ import annotations

import re
from typing import Any

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog
from .contracts import AgentPlan, Decision, PlanStep, RunState, ToolSpec


class OwnedPolicy:
    """Adaptive project-owned decision policy with dynamic replanning and learned action utility."""

    SEARCH_HINTS = ("搜", "搜索", "查询", "找不到", "关键词", "结果不准", "无结果", "搜索体验")
    REC_HINTS = ("推荐", "首页", "feed", "猜你喜欢", "分发", "曝光", "推荐体验", "个性化")
    COMPARE_HINTS = ("优化", "提升", "改进", "对比", "方案", "实验", "候选", "试试", "调整")
    ADAPT_HINTS = ("自动优化", "自主优化", "自动学习", "持续优化", "可以调整", "直接优化", "允许调整")
    NO_ADAPT_HINTS = ("不要上线", "先不要上线", "别上线", "不修改", "不要修改", "只检查", "只看", "不改变", "先离线")

    def plan(self, text: str, catalog: Catalog) -> AgentPlan:
        lowered = text.lower()
        search = any(k in lowered for k in self.SEARCH_HINTS)
        rec = any(k in lowered for k in self.REC_HINTS)
        if search and rec:
            mode = "both"
        elif search:
            mode = "search"
        elif rec:
            mode = "recommend"
        else:
            mode = "audit"
        compare = any(k in lowered for k in self.COMPARE_HINTS)
        query = self._extract_query(text, catalog) if mode in {"search", "both"} else None
        user = self._extract_user(text, catalog) if mode in {"recommend", "both"} else None
        deny = any(k in lowered for k in self.NO_ADAPT_HINTS)
        allow = any(k in lowered for k in self.ADAPT_HINTS) and not deny
        constraints = []
        if deny:
            constraints.append("不改变当前工作区策略")
        if "先" in lowered and ("离线" in lowered or "复核" in lowered):
            constraints.append("先完成离线验证")
        return AgentPlan(
            mode=mode,
            goal=text.strip(),
            query=query,
            user_id=user,
            compare=compare,
            allow_adaptation=allow,
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
        done = {row["tool"] for row in state.actions}
        observations = state.observations
        candidates: list[tuple[float, PlanStep, str, float]] = []

        def add(tool: str, base: float, title: str, detail: str, rationale: str, args: dict[str, Any] | None = None) -> None:
            if tool not in available:
                return
            if tool in done and not available[tool].repeatable:
                return
            bonus = float(policy_bonus(f"{plan.mode}|{tool}"))
            candidates.append((base + bonus, PlanStep(tool, title, detail, args or {}), rationale, bonus))

        if "data.inspect" not in done:
            add("data.inspect", 1.00, "读取当前工作区", "确认数据、反馈与可复核证据是否足够", "所有决策先建立数据边界")
        else:
            search_needed = plan.mode in {"search", "both"}
            rec_needed = plan.mode in {"recommend", "both"}
            audit_only = plan.mode == "audit"

            if search_needed and "search.run" not in done:
                add("search.run", 0.96, "复现搜索体验", f"真实运行“{plan.query or '当前查询'}”并保存结果证据", "先复现用户指出的搜索问题", {"query": plan.query})
            if rec_needed and "recommend.run" not in done:
                add("recommend.run", 0.95, "复现推荐体验", f"生成用户 {plan.user_id or '当前用户'} 的一屏结果", "先复现用户指出的推荐问题", {"user_id": plan.user_id})

            search_run = observations.get("search.run")
            if search_run is not None and "search.diagnose" not in done:
                rows = search_run.get("results", [])
                if not rows or float(rows[0].get("signals", {}).get("match", 0.0)) < 0.42:
                    add("search.diagnose", 0.91, "定位搜索失配", "检查查询词证据、候选覆盖与低相关原因", "当前复现结果存在空结果或弱匹配，需要先诊断再实验", {"query": plan.query})

            rec_run = observations.get("recommend.run")
            if rec_run is not None and "recommend.diagnose" not in done:
                rows = rec_run.get("results", [])
                if not rows or rec_run.get("history_events", 0) == 0:
                    add("recommend.diagnose", 0.90, "定位推荐约束", "检查用户历史、可展示池和冷启动状态", "当前用户缺少结果或行为证据，需要先诊断", {"user_id": plan.user_id})

            if (search_needed or audit_only) and "search.audit" not in done:
                base = 0.88 if search_run is not None or audit_only else 0.72
                add("search.audit", base, "检查搜索稳定性", "用可复核查询检查整体表现和回退风险", "单点结果不足以支撑策略判断")
            if (rec_needed or audit_only) and "recommend.audit" not in done:
                base = 0.87 if rec_run is not None or audit_only else 0.71
                add("recommend.audit", base, "检查推荐稳定性", "复核不同用户的覆盖、新鲜度和结果分散度", "单个用户不足以支撑策略判断")

            search_audit = observations.get("search.audit")
            rec_audit = observations.get("recommend.audit")
            should_evolve_search = search_needed and plan.compare
            should_evolve_rec = rec_needed and plan.compare
            if plan.mode == "audit" and plan.compare:
                if search_audit and rec_audit:
                    # Spend the next experiment on the weaker side first.
                    should_evolve_search = float(search_audit.get("quality", 0.0)) <= float(rec_audit.get("quality", 0.0))
                    should_evolve_rec = not should_evolve_search

            if should_evolve_search and search_audit is not None and "search.evolve" not in done:
                if search_audit.get("queries", 0) >= 3:
                    add(
                        "search.evolve",
                        0.84,
                        "自主生成并验证改进策略",
                        "生成多组候选并经过稳健性门槛筛选",
                        "已有足够搜索证据，可以进入受控策略进化",
                        {"activate": plan.allow_adaptation},
                    )
            if should_evolve_rec and rec_audit is not None and "recommend.evolve" not in done:
                if rec_audit.get("users", 0) >= 3:
                    add(
                        "recommend.evolve",
                        0.83,
                        "自主生成并验证改进策略",
                        "生成多组候选并经过稳健性门槛筛选",
                        "已有足够推荐证据，可以进入受控策略进化",
                        {"activate": plan.allow_adaptation},
                    )

        if not candidates:
            return Decision(None, "目标所需证据已经齐全，继续调用工具不会增加有效信息")
        candidates.sort(key=lambda row: (-row[0], row[1].tool))
        score, step, rationale, bonus = candidates[0]
        alternatives = [
            {"tool": row[1].tool, "score": round(row[0], 4), "reason": row[2]}
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
        users = RecommendationEngine(catalog).known_users()
        match = re.search(r"(?:用户|user)\s*[:：]?\s*([\w-]+)", text, re.I)
        if match:
            return match.group(1)
        return users[0] if users else "new-user"
