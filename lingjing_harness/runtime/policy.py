from __future__ import annotations

import re

from lingjing_harness.domain import Catalog
from .capabilities import CapabilityRegistry, RUNTIME_CAPABILITIES
from .contracts import AgentPlan, Decision, RunState, ToolSpec
from .context_memory import CONTINUATION_HINTS
from .deliberation import DeliberationEngine


class OwnedPolicy:
    """Project-owned controller with capability-compiled mission graphs.

    User text still owns intent and authority. Once an AgentPlan exists, mission
    structure comes from the runtime CapabilityRegistry rather than a central list
    of search/recommend tool steps. Attachments, memory and network observations
    can add evidence but cannot expand user-granted authority.
    """

    SEARCH_HINTS = ("搜", "搜索", "查询", "query", "找不到", "关键词", "结果不准", "无结果", "搜索体验")
    REC_HINTS = ("推荐", "recommend", "首页", "feed", "猜你喜欢", "分发", "曝光", "推荐体验", "个性化")
    EXPLORE_HINTS = ("优化", "提升", "改进", "实验", "候选", "试试", "调整", "进化", "学习")
    # ``allow_adaptation`` is retained in AgentPlan for checkpoint/API compatibility,
    # but its authority meaning is intentionally narrow: it authorizes changing the
    # active serving strategy. Exploring, validating and learning a candidate is
    # controlled by ``explore`` and never implies activation on its own.
    ACTIVATE_HINTS = (
        "上线",
        "激活",
        "应用策略",
        "应用这个策略",
        "直接生效",
        "立即生效",
        "设为当前",
        "作为当前策略",
        "切换策略",
        "部署策略",
    )
    NO_ACTIVATE_HINTS = (
        "不要上线",
        "先不要上线",
        "别上线",
        "不要激活",
        "先不要激活",
        "不修改",
        "不要修改",
        "只检查",
        "只看",
        "不改变",
        "先离线",
    )
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
        external request. Once the trajectory critic says the critical/high
        evidence graph is closed and owned evidence exists, a network action that
        was not explicitly requested has no mandatory role left. Failed local
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
        routing_context, user_memory_context = self._planning_contexts(
            context,
            prefer_attachment=any(
                hint in lowered
                for hint in ("附件", "图片", "截图", "文件", "这个图", "图里")
            ),
        )
        context_lower = routing_context.lower()
        direct_search = any(k in lowered for k in self.SEARCH_HINTS)
        direct_rec = any(k in lowered for k in self.REC_HINTS)
        inferred_search = any(k in context_lower for k in self.SEARCH_HINTS)
        inferred_rec = any(k in context_lower for k in self.REC_HINTS)
        search = direct_search or (not direct_search and not direct_rec and inferred_search)
        rec = direct_rec or (not direct_search and not direct_rec and inferred_rec)
        if search and rec:
            mode = "both"
        elif search:
            mode = "search"
        elif rec:
            mode = "recommend"
        else:
            mode = "audit"
        continuation = any(hint in lowered for hint in CONTINUATION_HINTS)
        user_memory_lower = user_memory_context.lower()
        explore = any(k in lowered for k in self.EXPLORE_HINTS) or (
            continuation
            and any(k in user_memory_lower for k in self.EXPLORE_HINTS)
        )
        query = None
        if mode in {"search", "both"}:
            query = self._extract_query(user_text, catalog, fallback=False)
            if not query:
                query = self._extract_query(routing_context, catalog, fallback=True)
        user = None
        if mode in {"recommend", "both"}:
            user = self._extract_user(user_text, catalog, fallback=False)
            if not user:
                user = self._extract_user(routing_context, catalog, fallback=True)
        deny_activation = any(k in lowered for k in self.NO_ACTIVATE_HINTS)
        allow_activation = any(k in lowered for k in self.ACTIVATE_HINTS) and not deny_activation
        network_requested = self._network_explicitly_requested(user_text)
        network = bool(allow_network or network_requested)
        constraints = []
        if deny_activation:
            constraints.append("不改变当前工作区策略")
        elif explore and not allow_activation:
            constraints.append("未授予激活权限；候选只验证和学习，不改变当前工作区策略")
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
            allow_adaptation=allow_activation,
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

    @classmethod
    def _planning_contexts(
        cls,
        context: str,
        *,
        prefer_attachment: bool = False,
    ) -> tuple[str, str]:
        """Parse the governed ledger once and derive routing/user-memory views."""

        if not context or "[CONTEXT_MEMORY" not in context:
            return context, ""

        allowed = {
            "direct_user",
            "current_attachment",
            "attachment_image",
            "attachment_text",
            "attachment_observation",
        }
        blocks: list[tuple[str, float, str]] = []
        source = ""
        created_at = 0.0
        lines: list[str] = []
        active = False

        def flush() -> None:
            nonlocal lines
            if active and source and lines:
                blocks.append((source, created_at, "\n".join(lines)))
            lines = []

        for line in context.splitlines():
            if line.startswith("[MEMORY "):
                flush()
                source_match = re.search(r"\bsource=([^\s\]]+)", line)
                stale_match = re.search(r"\bstale=([01])", line)
                created_match = re.search(r"\bcreated_at=([0-9.]+)", line)
                source = source_match.group(1) if source_match else ""
                stale = bool(stale_match and stale_match.group(1) == "1")
                created_at = float(created_match.group(1)) if created_match else 0.0
                active = source in allowed and not stale
                continue
            if line.startswith("[CONTEXT_MEMORY"):
                continue
            if active and line.startswith("> "):
                lines.append(line[2:])
        flush()

        direct = sorted(
            (block for block in blocks if block[0] == "direct_user"),
            key=lambda block: block[1],
            reverse=True,
        )
        user_memory = direct[0][2][:10_000] if direct else ""

        def has_domain_hint(value: str) -> bool:
            lowered = value.lower()
            return any(
                hint in lowered
                for hint in (*cls.SEARCH_HINTS, *cls.REC_HINTS)
            )

        current = [block for block in blocks if block[0] == "current_attachment"]
        historical = sorted(
            (
                block
                for block in blocks
                if block[0]
                in {
                    "attachment_image",
                    "attachment_text",
                    "attachment_observation",
                }
            ),
            key=lambda block: block[1],
            reverse=True,
        )

        if prefer_attachment:
            for _, _, value in current:
                if has_domain_hint(value):
                    return value[:10_000], user_memory
        for _, _, value in direct:
            if has_domain_hint(value):
                return value[:10_000], user_memory
        for _, _, value in current:
            if has_domain_hint(value):
                return value[:10_000], user_memory
        for _, _, value in historical:
            if has_domain_hint(value):
                return value[:10_000], user_memory

        ordered = (
            [*current, *direct, *historical]
            if prefer_attachment
            else [*direct, *current, *historical]
        )
        routing = "\n".join(value for _, _, value in ordered)[:10_000]
        return routing, user_memory

    @staticmethod
    def _extract_query(text: str, catalog: Catalog, *, fallback: bool = True) -> str:
        structured = re.search(
            r"""(?:["']?query["']?|查询词|关键词)\s*[:：=]\s*["'“]?([^"'“”\n,}]{1,50})""",
            text,
            re.I,
        )
        if structured:
            return structured.group(1).strip()
        quoted = [
            match.group(1).strip()
            for match in re.finditer(
                r"""[‘'“"]([^‘’'"“”]{1,50})[’'”"](?!\s*:)""",
                text,
            )
            if match.group(1).strip()
        ]
        if quoted:
            return quoted[0]
        for label in catalog.query_labels:
            if label.query and label.query in text:
                return label.query
        cleaned = re.sub(
            r"(帮我|请|看下|看看|分析|检查|为什么|搜索|搜一下|搜|不准|不好|优化|改进|结果|体验|一下|最近|继续|接着|沿用|刚才|之前|上次)",
            " ",
            text,
        )
        chunks = [
            x.strip(" ，。！？,.!?：:")
            for x in re.split(r"\s+", cleaned)
            if x.strip(" ，。！？,.!?：:")
        ]
        if chunks:
            return max(chunks, key=len)[:50]
        if not fallback:
            return ""
        default = (
            catalog.query_labels[0].query
            if catalog.query_labels
            else (catalog.items[0].title if catalog.items else "")
        )
        return default[:50]

    @staticmethod
    def _extract_user(text: str, catalog: Catalog, *, fallback: bool = True) -> str:
        users = sorted(
            {event.user_id for event in catalog.interactions if event.user_id},
            key=len,
            reverse=True,
        )
        for user_id in users:
            if re.search(
                rf"(?<![\w-]){re.escape(user_id)}(?![\w-])",
                text,
                re.I,
            ):
                return user_id

        match = re.search(
            r"""(?:
                ["']?user(?:_id)?["']?\s*[:=]\s*["']?
                |用户\s*[:：=]\s*["']?
                |用户\s+
            )([A-Za-z0-9_.-]+)""",
            text,
            re.I | re.X,
        )
        if match:
            return match.group(1)
        if not fallback:
            return ""
        return users[0] if users else "new-user"
