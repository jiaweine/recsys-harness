from __future__ import annotations

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
from lingjing_harness.production import request_groups
from .tools_core import ToolRegistry as CoreToolRegistry


class ToolRegistry(CoreToolRegistry):
    """Production-aware ToolRegistry.

    The core registry owns execution and compatibility behavior. This layer makes
    business reward evidence part of strategy memory and active rollback whenever
    a project supplied RewardSpec + exposure/outcome log is available.
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

    def _validate_active_strategies(self) -> None:
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

        # Existing relevance/coverage/cold-start checks remain mandatory.
        super()._validate_active_strategies()

        # If the strategy survived both business and proxy checks, persist one
        # validation record that states both values explicitly.
        for domain, (fingerprint, reward, proxy_quality) in business_pass.items():
            skill = self.memory.active_skill(self.catalog_key, domain)
            if not skill or str(skill.get("fingerprint")) != fingerprint:
                continue
            self.memory.mark_skill_validation(
                self.catalog_key,
                domain,
                fingerprint,
                metrics={
                    "business_reward": reward,
                    "proxy_quality": proxy_quality,
                },
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
        }
        if activate:
            payload.update(
                {
                    "validated_at": time.time(),
                    "validation": result.get("candidate", {}),
                }
            )
        return payload

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
