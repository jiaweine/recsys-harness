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
    prepare_recommend_relevance,
)
from lingjing_harness.algorithms.capabilities import config_from_mapping
from lingjing_harness.production import request_groups
from .tools_core import ToolRegistry as CoreToolRegistry


class ToolRegistry(CoreToolRegistry):
    """Production-aware ToolRegistry.

    The core registry owns execution and compatibility behavior. This layer makes
    business reward evidence part of strategy memory and active rollback whenever
    a project supplied RewardSpec + exposure/outcome log is available. Evolution
    memory includes durable positive/negative arm credit so independently failed
    mutations alter later routing rather than disappearing after one run.
    """

    def _business_ready(self, domain: str) -> bool:
        return bool(
            self.catalog.reward_spec
            and request_groups(self.catalog.events, surface=domain)
        )

    def inspect_data(self) -> dict[str, Any]:
        result = super().inspect_data()
        summary = result["summary"]
        issues = list(result["issues"])
        if not self.catalog.reward_spec:
            issues.append("未配置业务 RewardSpec；策略只能以离线 relevance/coverage 等 proxy 指标评估")
        elif not self.catalog.events:
            issues.append("已配置 RewardSpec，但缺少 production events；还不能进行业务 reward replay")
        else:
            if summary.get("search_replay_requests", 0) < 8:
                issues.append("搜索 production request 太少，暂不足以形成稳定的时间 holdout")
            if summary.get("recommend_replay_requests", 0) < 8:
                issues.append("推荐 production request 太少，暂不足以形成稳定的时间 holdout")
        return {**result, "issues": issues}

    def fork(self) -> "ToolRegistry":
        """Fork without falling back to the compatibility-only core class."""

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
        # Capture whether recommendation validation was due before the core layer
        # refreshes validated_at. Relevance follows the same TTL as every other
        # active-strategy guardrail instead of being recomputed on every registry
        # construction.
        recommend_before = self.memory.active_skill(self.catalog_key, "recommend")
        recommend_validation_due = bool(
            recommend_before and not self._validation_is_fresh(recommend_before)
        )

        # Business regression is checked *before* the core validation can mark a
        # strategy fresh. Otherwise a proxy-only refresh could accidentally hide
        # a production-reward regression for the whole validation TTL.
        business_pass: dict[str, tuple[str, float, float]] = {}
        for domain, engine, default_engine in (
            ("search", self.search, self.search.with_config(SearchConfig())),
            ("recommend", self.recommend, self.recommend.with_config(RecommendConfig())),
        ):
            if not self._business_ready(domain):
                continue
            skill = self.memory.active_skill(self.catalog_key, domain)
            if not skill or self._validation_is_fresh(skill):
                continue
            active_report = audit_search(self.catalog, engine) if domain == "search" else audit_recommend(self.catalog, engine)
            default_report = audit_search(self.catalog, default_engine) if domain == "search" else audit_recommend(self.catalog, default_engine)
            active_reward = active_report.get("business_reward")
            default_reward = default_report.get("business_reward")
            if active_reward is None or default_reward is None:
                continue
            if float(active_reward) < float(default_reward) - 0.015:
                retired = self.memory.retire_active(
                    self.catalog_key,
                    domain,
                    reason="active strategy regressed on production business reward",
                )
                if domain == "search":
                    self.search = default_engine
                else:
                    self.recommend = default_engine
                if retired:
                    self.rollback_events.append({"domain": domain, **retired})
            else:
                business_pass[domain] = (
                    str(skill["fingerprint"]),
                    float(active_reward),
                    float(active_report.get("quality", 0.0)),
                )

        # Existing search relevance plus recommendation proxy/coverage/cold-start
        # checks remain mandatory and own the shared validation TTL.
        super()._validate_active_strategies()

        # Recommendation promotion carries an interaction-temporal relevance
        # guardrail. Revalidation enforces the same invariant on the exact same
        # prepared temporal slices, but only when the normal validation cycle is
        # due. This avoids rebuilding historical slices on every ToolRegistry fork.
        relevance_validation: tuple[str, dict[str, Any]] | None = None
        recommend_skill = self.memory.active_skill(self.catalog_key, "recommend")
        if recommend_validation_due and recommend_skill:
            prepared = prepare_recommend_relevance(
                self.catalog,
                self.recommend,
                users_override=self.recommend.known_users(),
                k=10,
            )
            active_relevance = prepared.evaluate(self.recommend.config)
            default_relevance = prepared.evaluate(RecommendConfig())
            samples = min(
                int(active_relevance.get("users", 0) or 0),
                int(default_relevance.get("users", 0) or 0),
            )
            relevance_available = bool(
                active_relevance.get("available")
                and default_relevance.get("available")
                and samples >= 3
            )
            ndcg_delta = float(active_relevance.get("model", {}).get("ndcg", 0.0)) - float(
                default_relevance.get("model", {}).get("ndcg", 0.0)
            )
            mrr_delta = float(active_relevance.get("model", {}).get("mrr", 0.0)) - float(
                default_relevance.get("model", {}).get("mrr", 0.0)
            )
            regression = relevance_available and (
                ndcg_delta < -0.01 or mrr_delta < -0.015
            )
            if regression:
                retired = self.memory.retire_active(
                    self.catalog_key,
                    "recommend",
                    reason="active recommendation strategy regressed on interaction-temporal relevance",
                )
                self.recommend = self.recommend.with_config(RecommendConfig())
                if retired:
                    self.rollback_events.append({"domain": "recommend", **retired})
            else:
                relevance_validation = (
                    str(recommend_skill["fingerprint"]),
                    {
                        "relevance_available": relevance_available,
                        "relevance_users": samples,
                        "relevance_protocol": active_relevance.get("protocol"),
                        "relevance_temporal_scope": active_relevance.get("temporal_scope"),
                        "point_in_time_item_features": active_relevance.get("point_in_time_item_features"),
                        "relevance_ndcg": float(active_relevance.get("model", {}).get("ndcg", 0.0)),
                        "default_relevance_ndcg": float(default_relevance.get("model", {}).get("ndcg", 0.0)),
                        "relevance_ndcg_delta": round(ndcg_delta, 4),
                        "relevance_mrr": float(active_relevance.get("model", {}).get("mrr", 0.0)),
                        "default_relevance_mrr": float(default_relevance.get("model", {}).get("mrr", 0.0)),
                        "relevance_mrr_delta": round(mrr_delta, 4),
                    },
                )
                if "recommend" not in business_pass:
                    existing = dict((recommend_skill.get("payload") or {}).get("validation") or {})
                    existing.update(relevance_validation[1])
                    self.memory.mark_skill_validation(
                        self.catalog_key,
                        "recommend",
                        str(recommend_skill["fingerprint"]),
                        metrics=existing,
                    )

        # If a strategy survived business plus domain guardrails, persist one
        # validation record that merges the core metrics with business/relevance
        # evidence instead of replacing one evidence family with another.
        for domain, (fingerprint, reward, proxy_quality) in business_pass.items():
            skill = self.memory.active_skill(self.catalog_key, domain)
            if not skill or str(skill.get("fingerprint")) != fingerprint:
                continue
            metrics: dict[str, Any] = dict((skill.get("payload") or {}).get("validation") or {})
            metrics.update(
                {
                    "business_reward": reward,
                    "proxy_quality": proxy_quality,
                }
            )
            if (
                domain == "recommend"
                and relevance_validation is not None
                and relevance_validation[0] == fingerprint
            ):
                metrics.update(relevance_validation[1])
            self.memory.mark_skill_validation(
                self.catalog_key,
                domain,
                fingerprint,
                metrics=metrics,
            )

    @staticmethod
    def _strategy_score(result: dict[str, Any]) -> float:
        candidate = result.get("candidate") or {}
        if candidate.get("business_reward") is not None:
            return float(candidate["business_reward"])
        return float(candidate.get("quality", 0.0))

    @staticmethod
    def _strategy_evidence(result: dict[str, Any], fallback_key: str) -> int:
        candidate = result.get("candidate") or {}
        if candidate.get("business_requests") is not None:
            return int(candidate["business_requests"])
        return int(candidate.get(fallback_key, 0))

    @staticmethod
    def _strategy_payload(result: dict[str, Any], *, activate: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "delta": result.get("delta", {}),
            "robustness": result.get("robustness", {}),
            "evaluation_basis": result.get("evaluation_basis", "proxy_metrics"),
            "business_validation": result.get("business_validation", {}),
            "relevance_validation": result.get("relevance_validation", {}),
            "credit_learning": result.get("credit_learning", {}),
        }
        if activate:
            payload.update(
                {
                    "validated_at": time.time(),
                    "validation": result.get("candidate", {}),
                }
            )
        return payload

    def _evolution_memory(self, domain: str) -> list[dict[str, Any]]:
        reader = getattr(self.memory, "evolution_memory", None)
        if callable(reader):
            return reader(self.catalog_key, domain, limit=5)
        return self.memory.strategies(self.catalog_key, domain, limit=5)

    def _record_credit(
        self,
        surface: str,
        current_config: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        recorder = getattr(self.memory, "record_evolution_result", None)
        if not callable(recorder):
            return {"available": False, "reason": "credit_memory_unavailable"}
        return recorder(
            self.catalog_key,
            surface,
            current_config=current_config,
            result=result,
        )

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

        remembered = self._evolution_memory("search")
        current_config = asdict(self.search.config)
        result = evolve_search(self.catalog, self.search, remembered=remembered)
        result["credit_learning"] = self._record_credit("search", current_config, result)
        result["activated"] = False
        result["learned"] = False
        if result.get("trusted"):
            status = "active" if activate else "trusted"
            skill = self.memory.remember_strategy(
                self.catalog_key,
                "search",
                result["candidate_config"],
                score=self._strategy_score(result),
                evidence=self._strategy_evidence(result, "queries"),
                status=status,
                payload=self._strategy_payload(result, activate=activate),
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

        remembered = self._evolution_memory("recommend")
        current_config = asdict(self.recommend.config)
        result = evolve_recommend(self.catalog, self.recommend, remembered=remembered)
        result["credit_learning"] = self._record_credit("recommend", current_config, result)
        result["activated"] = False
        result["learned"] = False
        if result.get("trusted"):
            status = "active" if activate else "trusted"
            skill = self.memory.remember_strategy(
                self.catalog_key,
                "recommend",
                result["candidate_config"],
                score=self._strategy_score(result),
                evidence=self._strategy_evidence(result, "users"),
                status=status,
                payload=self._strategy_payload(result, activate=activate),
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
