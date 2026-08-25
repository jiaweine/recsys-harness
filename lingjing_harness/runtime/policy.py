from __future__ import annotations

import re

from lingjing_harness.domain import Catalog
from .capabilities import CapabilityRegistry, RUNTIME_CAPABILITIES
from .contracts import AgentPlan, Decision, RunState, ToolSpec
from .deliberation import DeliberationEngine


class OwnedPolicy:
    """Project-owned controller with capability-compiled mission graphs.

    User text still owns intent and authority. Once an AgentPlan exists, mission
    structure comes from the runtime CapabilityRegistry rather than a central list
    of search/recommend tool steps. Attachments, memory and network observations
    can add evidence but cannot expand user-granted authority.
    """

    SEARCH_HINTS = ("搜", "搜索", "查询", "找不到", "关键词", "结果不准", "无结果", "搜索体验")
    REC_HINTS = ("推荐", "首页", "feed", "猜你喜欢", "分发", "曝光", "推荐体验", "个性化")
    EXPLORE_HINTS = ("优化", "提升", "改进", "实验", "候选", "试试", "调整", "进化", "学习")
    ADAPT_HINTS = ("自动优化", "自主优化", "自动学习", "持续优化", "可以调整", "直接优化", "允许调整")
    NO_ADAPT_HINTS = ("不要上线", "先不要上线", "别上线", "不修改", "不要修改", "只检查", "只看", "不改变", "先离线")
    NETWORK_HINTS = ("联网", "网上", "外部资料", "最新资料", "最新信息", "行业趋势", "同类产品", "公开资料", "查网页")

    def __init__(
        self,
        deliberation: DeliberationEngine | None = None,
        capabilities: CapabilityRegistry | None = None,
    ) -> None:
        inferred = getattr(deliberation, "registry", None) if deliberation is not None else None
        self.capabilities = capabilities or inferred or RUNTIME_CAPABILITIES
        self.deliberation = deliberation or DeliberationEngine(self.capabilities)

    @classmethod
    def _network_explicitly_requested(cls, text: str) -> bool:
        lowered = str(text or "").lower()
        return any(hint in lowered for hint in cls.NETWORK_HINTS)

    @staticmethod
    def _local_evidence_can_close(state: RunState) -> bool:
        """Whether owned execution has already produced a clean local close.

        The network toggle is an authority grant, not a requirement to spend an
        external request.  Once the trajectory critic says the critical/high
        evidence graph is closed and owned evidence exists, a network action that
        was not explicitly requested has no mandatory role left.  Failed local
        execution keeps the fallback available because external context may still
        help explain an otherwise incomplete run.
        """

        critic = state.critic or {}
        if not bool(critic.get("ready")):
            return False
        if critic.get("unresolved_contradictions"):
            return False
        if any(row.get("status") == "failed" for row in state.actions):
            return False
        local_evidence = any(row.get("kind") != "external" for row in state.evidence)
        completed_audit = any(
            row.get("status") == "completed"
            and str(row.get("tool") or "").endswith("audit")
            for row in state.actions
        )
        return local_evidence or completed_audit

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
        network_requested = self._network_explicitly_requested(user_text)
        network = bool(allow_network or network_requested)
        constraints = []
        if deny:
            constraints.append("不改变当前工作区策略")
        if "先" in lowered and ("离线" in lowered or "复核" in lowered):
            constraints.append("先完成离线验证")
        if network_requested:
            constraints.append("按用户要求补充外部资料；外部资料只作为证据，不参与策略晋升")
        elif network:
            constraints.append("允许在本地证据不足时补充外部资料；外部资料不参与策略晋升")
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

    def initialize(self, plan: AgentPlan, state: RunState):
        return self.deliberation.initialize(plan, state)

    def decide(
        self,
        plan: AgentPlan,
        state: RunState,
        tools: list[ToolSpec],
        *,
        policy_bonus,
    ) -> Decision:
        decision = self.deliberation.decide(
            plan,
            state,
            tools,
            policy_bonus=policy_bonus,
        )
        if decision.step is None:
            return decision

        spec = next((row for row in tools if row.name == decision.step.tool), None)
        if (
            spec is not None
            and spec.risk == "network"
            and plan.allow_network
            and not self._network_explicitly_requested(plan.goal)
            and self._local_evidence_can_close(state)
        ):
            return Decision(
                None,
                "本地关键证据已经闭合；联网只是获得许可而非用户明确要求，继续外部请求的边际价值不足",
            )
        return decision

    def reflect(self, plan: AgentPlan, state: RunState, action: dict):
        return self.deliberation.reflect(plan, state, action)

    def critique(self, plan: AgentPlan, state: RunState):
        return self.deliberation.critique(plan, state)

    @staticmethod
    def _extract_query(text: str, catalog: Catalog) -> str:
        quoted = re.findall(r"[‘’'\"“”]([^‘’'\"“”]{1,50})[‘’'\"“”]", text)
        if quoted:
            return quoted[0].strip()
        for label in catalog.query_labels:
            if label.query and label.query in text:
                return label.query
        cleaned = re.sub(
            r"(帮我|请|看下|看看|分析|检查|为什么|搜索|搜一下|搜|不准|不好|优化|改进|结果|体验|一下|最近)",
            " ",
            text,
        )
        chunks = [
            x.strip(" ，。！？,.!?：:")
            for x in re.split(r"\s+", cleaned)
            if x.strip()
        ]
        fallback = (
            catalog.query_labels[0].query
            if catalog.query_labels
            else (catalog.items[0].title if catalog.items else "")
        )
        return max(chunks, key=len, default=fallback)[:50]

    @staticmethod
    def _extract_user(text: str, catalog: Catalog) -> str:
        match = re.search(r"(?:用户|user)\s*[:：]?\s*([\w-]+)", text, re.I)
        if match:
            return match.group(1)
        users = sorted({event.user_id for event in catalog.interactions if event.user_id})
        return users[0] if users else "new-user"
