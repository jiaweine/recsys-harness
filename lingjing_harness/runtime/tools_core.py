from __future__ import annotations

from dataclasses import asdict
import time
from typing import Any

from lingjing_harness.algorithms import (
    RecommendConfig,
    RecommendationEngine,
    SearchConfig,
    SearchEngine,
    audit_recommend,
    audit_search,
    evolve_recommend,
    evolve_search,
)
from lingjing_harness.algorithms.capabilities import config_from_mapping
from lingjing_harness.algorithms.text import tokenize
from lingjing_harness.domain import Catalog
from .contracts import ToolSpec
from .memory import AgentMemory, catalog_fingerprint
from .network import NetworkResearch


class ToolRegistry:
    """Capability registry with risk guards and durable strategy lifecycle."""

    ACTIVE_VALIDATION_TTL = 300.0

    def __init__(
        self,
        catalog: Catalog,
        memory: AgentMemory | None = None,
        network: NetworkResearch | None = None,
    ) -> None:
        self.catalog = catalog
        self.memory = memory or AgentMemory()
        self.network = network or NetworkResearch()
        self.catalog_key = catalog_fingerprint(catalog)
        self.rollback_events: list[dict[str, Any]] = []

        search_cfg = self._load_config("search", SearchConfig)
        recommend_cfg = self._load_config("recommend", RecommendConfig)
        self.search = SearchEngine(catalog, search_cfg)
        self.recommend = RecommendationEngine(catalog, recommend_cfg)
        self._validate_active_strategies()
        self._specs = self._build_specs()

    def _build_specs(self) -> dict[str, ToolSpec]:
        specs = {
            "data.inspect": ToolSpec(
                "data.inspect",
                "Inspect catalog and evaluation readiness",
                "read",
                self.inspect_data,
                cost=0.35,
                input_schema={"type": "object", "properties": {}},
            ),
            "search.run": ToolSpec(
                "search.run",
                "Run the current search experience",
                "read",
                self.run_search,
                cost=0.9,
                input_schema={"type": "object", "properties": {"query": {"type": "string", "maxLength": 50}}},
            ),
            "search.diagnose": ToolSpec(
                "search.diagnose",
                "Diagnose query evidence and candidate coverage",
                "read",
                self.search_diagnose,
                cost=0.7,
                input_schema={"type": "object", "properties": {"query": {"type": "string", "maxLength": 50}}},
            ),
            "search.audit": ToolSpec(
                "search.audit",
                "Evaluate search on labeled queries",
                "simulation",
                self.search_audit,
                cost=2.0,
                input_schema={"type": "object", "properties": {}},
            ),
            "search.evolve": ToolSpec(
                "search.evolve",
                "Generate and robustly evaluate evolved search strategies",
                "adaptive",
                self.search_evolve,
                cost=6.0,
                side_effect="internal_strategy_memory",
                input_schema={"type": "object", "properties": {"activate": {"type": "boolean"}}},
            ),
            "recommend.run": ToolSpec(
                "recommend.run",
                "Generate a recommendation slate",
                "read",
                self.run_recommend,
                cost=1.0,
                input_schema={"type": "object", "properties": {"user_id": {"type": "string", "maxLength": 120}}},
            ),
            "recommend.diagnose": ToolSpec(
                "recommend.diagnose",
                "Diagnose user evidence, cold start and eligible pool",
                "read",
                self.recommend_diagnose,
                cost=0.7,
                input_schema={"type": "object", "properties": {"user_id": {"type": "string", "maxLength": 120}}},
            ),
            "recommend.audit": ToolSpec(
                "recommend.audit",
                "Evaluate recommendation coverage, freshness, diversity and cold start",
                "simulation",
                self.recommend_audit,
                cost=2.2,
                input_schema={"type": "object", "properties": {}},
            ),
            "recommend.evolve": ToolSpec(
                "recommend.evolve",
                "Generate and robustly evaluate evolved recommendation strategies",
                "adaptive",
                self.recommend_evolve,
                cost=6.5,
                side_effect="internal_strategy_memory",
                input_schema={"type": "object", "properties": {"activate": {"type": "boolean"}}},
            ),
        }
        if self.network.configured:
            specs["web.research"] = ToolSpec(
                "web.research",
                "Search current public web evidence",
                "network",
                self.web_research,
                cost=1.8,
                side_effect="external_request",
                input_schema={"type": "object", "properties": {"query": {"type": "string", "maxLength": 320}}},
            )
        return specs

    def _retire_invalid_active(self, domain: str, reason: str) -> None:
        retired = self.memory.retire_active(self.catalog_key, domain, reason=reason)
        if retired:
            self.rollback_events.append({"domain": domain, **retired})

    def _load_config(self, domain: str, cls):
        raw = self.memory.active_config(self.catalog_key, domain)
        if not raw:
            return cls()
        try:
            config = config_from_mapping(cls, raw)
        except (TypeError, ValueError, KeyError) as exc:
            self._retire_invalid_active(
                domain,
                f"persisted strategy is invalid for the current schema: {type(exc).__name__}",
            )
            return cls()

        # A removed capability may canonicalize to the safe default. That is safe
        # for execution, but the old active fingerprint no longer describes what
        # would actually run, so retire it and require re-evaluation.
        if asdict(config) != raw:
            self._retire_invalid_active(
                domain,
                "persisted strategy required canonicalization against the current schema",
            )
            return cls()
        return config

    def fork(self) -> "ToolRegistry":
        clone = object.__new__(ToolRegistry)
        clone.catalog = self.catalog
        clone.memory = self.memory
        clone.network = self.network
        clone.catalog_key = self.catalog_key
        clone.rollback_events = []
        clone.search = self.search.with_config(clone._load_config("search", SearchConfig))
        clone.recommend = self.recommend.with_config(clone._load_config("recommend", RecommendConfig))
        clone._specs = clone._build_specs()
        return clone

    def _validate_active_strategies(self) -> None:
        search_skill = self.memory.active_skill(self.catalog_key, "search")
        if search_skill and not self._validation_is_fresh(search_skill):
            active_report = audit_search(self.catalog, self.search)
            default_engine = self.search.with_config(SearchConfig())
            default_report = audit_search(self.catalog, default_engine)
            enough = active_report.get("queries", 0) >= 3 and default_report.get("queries", 0) >= 3
            regression = (
                enough
                and (
                    active_report.get("quality", 0.0) < default_report.get("quality", 0.0) - 0.01
                    or active_report.get("recall", 0.0) < default_report.get("recall", 0.0) - 0.02
                )
            )
            if regression:
                retired = self.memory.retire_active(
                    self.catalog_key,
                    "search",
                    reason="active strategy regressed against the owned default",
                )
                self.search = default_engine
                if retired:
                    self.rollback_events.append({"domain": "search", **retired})
            else:
                self.memory.mark_skill_validation(
                    self.catalog_key,
                    "search",
                    search_skill["fingerprint"],
                    metrics={
                        "quality": active_report.get("quality", 0.0),
                        "recall": active_report.get("recall", 0.0),
                    },
                )

        recommend_skill = self.memory.active_skill(self.catalog_key, "recommend")
        if recommend_skill and not self._validation_is_fresh(recommend_skill):
            active_report = audit_recommend(self.catalog, self.recommend)
            default_engine = self.recommend.with_config(RecommendConfig())
            default_report = audit_recommend(self.catalog, default_engine)
            enough = active_report.get("users", 0) >= 3 and default_report.get("users", 0) >= 3
            regression = (
                enough
                and (
                    active_report.get("quality", 0.0) < default_report.get("quality", 0.0) - 0.01
                    or active_report.get("coverage", 0.0) < default_report.get("coverage", 0.0) - 0.03
                    or active_report.get("cold_start_quality", 0.0)
                    < default_report.get("cold_start_quality", 0.0) - 0.035
                )
            )
            if regression:
                retired = self.memory.retire_active(
                    self.catalog_key,
                    "recommend",
                    reason="active strategy regressed against the owned default, including cold-start slice",
                )
                self.recommend = default_engine
                if retired:
                    self.rollback_events.append({"domain": "recommend", **retired})
            else:
                self.memory.mark_skill_validation(
                    self.catalog_key,
                    "recommend",
                    recommend_skill["fingerprint"],
                    metrics={
                        "quality": active_report.get("quality", 0.0),
                        "coverage": active_report.get("coverage", 0.0),
                        "cold_start_quality": active_report.get("cold_start_quality", 0.0),
                    },
                )

    def _validation_is_fresh(self, skill: dict[str, Any]) -> bool:
        validated_at = float((skill.get("payload") or {}).get("validated_at", 0.0) or 0.0)
        return validated_at > 0 and time.time() - validated_at < self.ACTIVE_VALIDATION_TTL

    def replace_catalog(self, catalog: Catalog) -> None:
        self.__init__(catalog, self.memory, self.network)

    def get(self, name: str) -> ToolSpec:
        if name not in self._specs:
            raise KeyError(f"unknown tool: {name}")
        return self._specs[name]

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "risk": spec.risk,
                "cost": spec.cost,
                "side_effect": spec.side_effect,
                "input_schema": spec.input_schema,
            }
            for spec in self._specs.values()
        ]

    def execute(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        allow_adaptation: bool = False,
        allow_network: bool = False,
        invocation_id: str | None = None,
    ) -> dict[str, Any]:
        spec = self.get(name)
        args = dict(args or {})
        if "query" in args and args["query"] is not None:
            limit = 320 if name == "web.research" else 50
            args["query"] = str(args["query"]).strip()[:limit]
        if "user_id" in args and args["user_id"] is not None:
            args["user_id"] = str(args["user_id"]).strip()[:120]
        if spec.risk == "adaptive" and args.get("activate") and not allow_adaptation:
            raise PermissionError("当前目标没有授权改变工作区策略")
        if spec.risk == "network" and not allow_network:
            raise PermissionError("当前目标没有授权联网研究")
        if invocation_id and spec.risk == "adaptive":
            args["_invocation_id"] = invocation_id
        return spec.handler(**args)

    def inspect_data(self) -> dict[str, Any]:
        summary = self.catalog.summary()
        issues = []
        if summary["interactions"] == 0:
            issues.append("缺少用户行为记录，个性化结果会更多依赖内容本身")
        elif summary["users"] < 3:
            issues.append("可复核用户太少，推荐策略暂时不会进入自主激活")
        if summary["queries"] == 0:
            issues.append("缺少人工复核查询，搜索只能做结构性检查")
        elif summary["queries"] < 3:
            issues.append("人工复核查询太少，搜索策略暂时不会进入自主激活")
        if summary["items"] < 12:
            issues.append("内容规模较小，离线结论的稳定性有限")
        duplicates = len(self.catalog.items) - len({item.title.strip().lower() for item in self.catalog.items})
        if duplicates:
            issues.append(f"发现 {duplicates} 条重复标题")
        unavailable = sum(1 for item in self.catalog.items if not item.eligible)
        if unavailable:
            issues.append(f"有 {unavailable} 条内容当前不可展示")
        return {
            "summary": summary,
            "issues": issues,
            "memory": self.memory.stats(self.catalog_key),
            "active": {
                "search": asdict(self.search.config),
                "recommend": asdict(self.recommend.config),
            },
            "network_available": self.network.configured,
            "rollbacks": list(self.rollback_events),
        }

    def web_research(self, query: str | None = None, **_: Any) -> dict[str, Any]:
        return self.network.search(query or "", limit=6)

    def run_search(self, query: str | None = None, **_: Any) -> dict[str, Any]:
        query = query or ""
        return {"query": query, "results": self.search.search(query, limit=8)}

    def search_diagnose(self, query: str | None = None, **_: Any) -> dict[str, Any]:
        query = (query or "").strip()
        query_tokens = list(dict.fromkeys(tokenize(query)))
        result = self.search.search(query, limit=8)
        covered = set()
        for row in result:
            title = next(
                (item.title for item in self.catalog.items if item.item_id == row["id"]),
                row["title"],
            )
            covered.update(set(query_tokens) & set(tokenize(title)))
        generic = [token for token in query_tokens if token in SearchEngine.GENERIC_QUERY_TOKENS]
        return {
            "query": query,
            "query_tokens": query_tokens,
            "covered_tokens": sorted(covered),
            "generic_tokens": generic,
            "result_count": len(result),
            "top_match": result[0]["signals"]["match"] if result else 0.0,
            "diagnosis": (
                "没有候选包含可验证的查询词证据"
                if not result
                else "查询包含较宽泛词，排序需要更多依赖具体词证据"
                if generic
                else "当前候选存在但首位匹配证据偏弱"
                if result[0]["signals"]["match"] < 0.42
                else "当前查询的直接词项证据基本完整"
            ),
        }

    def search_audit(self, **_: Any) -> dict[str, Any]:
        return audit_search(self.catalog, self.search)

    def search_evolve(
        self,
        activate: bool = False,
        _invocation_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if _invocation_id:
            replay = self.memory.invocation_result(_invocation_id)
            if replay:
                result = dict(replay["result"])
                result.update(
                    {
                        "skill": replay["skill"],
                        "learned": True,
                        "activated": replay["skill"]["status"] == "active",
                        "replayed": True,
                    }
                )
                if result["activated"]:
                    self.search = self.search.with_config(
                        config_from_mapping(SearchConfig, result["candidate_config"])
                    )
                return result

        remembered = self.memory.strategies(self.catalog_key, "search", limit=5)
        result = evolve_search(self.catalog, self.search, remembered=remembered)
        result["activated"] = False
        result["learned"] = False
        if result.get("trusted"):
            status = "active" if activate else "trusted"
            skill = self.memory.remember_strategy(
                self.catalog_key,
                "search",
                result["candidate_config"],
                score=float(result.get("candidate", {}).get("quality", 0.0)),
                evidence=int(result.get("candidate", {}).get("queries", 0)),
                status=status,
                payload={
                    "delta": result.get("delta", {}),
                    "robustness": result.get("robustness", {}),
                    **(
                        {
                            "validated_at": time.time(),
                            "validation": result.get("candidate", {}),
                        }
                        if activate
                        else {}
                    ),
                },
                invocation_id=_invocation_id,
                tool_result=result,
            )
            result["skill"] = skill
            result["learned"] = True
            if activate:
                self.search = SearchEngine(
                    self.catalog,
                    config_from_mapping(SearchConfig, result["candidate_config"]),
                )
                result["activated"] = True
        return result

    def run_recommend(self, user_id: str | None = None, **_: Any) -> dict[str, Any]:
        user_id = user_id or "new-user"
        history_events = len(self.recommend._by_user.get(user_id, []))
        return {
            "user_id": user_id,
            "history_events": history_events,
            "results": self.recommend.recommend(user_id, limit=8),
        }

    def recommend_diagnose(self, user_id: str | None = None, **_: Any) -> dict[str, Any]:
        user_id = user_id or "new-user"
        events = self.recommend._by_user.get(user_id, [])
        seen = {event.item_id for event in events}
        eligible = [
            item
            for item in self.catalog.items
            if item.eligible and item.item_id not in seen
        ]
        categories = sorted(
            {
                category
                for event in events
                for category in self.catalog.item_by_id[event.item_id].categories
            }
        )
        return {
            "user_id": user_id,
            "history_events": len(events),
            "seen_items": len(seen),
            "eligible_unseen": len(eligible),
            "known_categories": categories[:12],
            "cold_start": len(events) == 0,
            "diagnosis": (
                "这是冷启动用户，当前结果主要依赖内容质量、新鲜度和稳定探索"
                if not events
                else "可展示未看内容不足，推荐空间受到候选池限制"
                if len(eligible) < 8
                else "用户行为和可展示候选都足以支持个性化复核"
            ),
        }

    def recommend_audit(self, **_: Any) -> dict[str, Any]:
        return audit_recommend(self.catalog, self.recommend)

    def recommend_evolve(
        self,
        activate: bool = False,
        _invocation_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if _invocation_id:
            replay = self.memory.invocation_result(_invocation_id)
            if replay:
                result = dict(replay["result"])
                result.update(
                    {
                        "skill": replay["skill"],
                        "learned": True,
                        "activated": replay["skill"]["status"] == "active",
                        "replayed": True,
                    }
                )
                if result["activated"]:
                    self.recommend = self.recommend.with_config(
                        config_from_mapping(RecommendConfig, result["candidate_config"])
                    )
                return result

        remembered = self.memory.strategies(self.catalog_key, "recommend", limit=5)
        result = evolve_recommend(self.catalog, self.recommend, remembered=remembered)
        result["activated"] = False
        result["learned"] = False
        if result.get("trusted"):
            status = "active" if activate else "trusted"
            skill = self.memory.remember_strategy(
                self.catalog_key,
                "recommend",
                result["candidate_config"],
                score=float(result.get("candidate", {}).get("quality", 0.0)),
                evidence=int(result.get("candidate", {}).get("users", 0)),
                status=status,
                payload={
                    "delta": result.get("delta", {}),
                    "robustness": result.get("robustness", {}),
                    **(
                        {
                            "validated_at": time.time(),
                            "validation": result.get("candidate", {}),
                        }
                        if activate
                        else {}
                    ),
                },
                invocation_id=_invocation_id,
                tool_result=result,
            )
            result["skill"] = skill
            result["learned"] = True
            if activate:
                self.recommend = RecommendationEngine(
                    self.catalog,
                    config_from_mapping(RecommendConfig, result["candidate_config"]),
                )
                result["activated"] = True
        return result


__all__ = ["ToolRegistry"]
