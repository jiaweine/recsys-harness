"""Stable ToolRegistry import surface with production portfolio lifecycle."""

from __future__ import annotations

from dataclasses import asdict
import time
from typing import Any

from lingjing_harness.algorithms import RecommendConfig, RecommendationEngine, SearchConfig, SearchEngine
from lingjing_harness.algorithms.capabilities import config_from_mapping
from lingjing_harness.algorithms.segments import SegmentRouter, strategy_domain
from lingjing_harness.production import evaluate_logged_policy, request_groups
from .experiment_tools import ToolRegistry as _ProductionToolRegistry


class ToolRegistry(_ProductionToolRegistry):
    """Production registry with conservative global and segment activation.

    A validated segment strategy can override the global strategy only for the
    request contexts assigned to that segment. Thin or stale segments fall back
    to the global engine, and active segment strategies are rechecked against the
    current production evidence before they are used after the validation TTL.
    """

    MIN_ACTIVE_BUSINESS_REQUESTS = 8
    MIN_ACTIVE_SEGMENT_REQUESTS = 5

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._refresh_portfolio()
        self._validate_active_portfolio()

    def _business_ready(self, domain: str) -> bool:
        return bool(
            self.catalog.reward_spec
            and len(request_groups(self.catalog.events, surface=domain))
            >= self.MIN_ACTIVE_BUSINESS_REQUESTS
        )

    def _activation_allowed(self, surface: str, requested: bool) -> bool:
        if not requested:
            return False
        requests = request_groups(self.catalog.events, surface=surface)
        if self.catalog.reward_spec and requests:
            return self._business_ready(surface)
        return True

    def _load_segment_config(self, surface: str, segment: str, cls: Any) -> Any | None:
        domain = strategy_domain(surface, segment)
        raw = self.memory.active_config(self.catalog_key, domain)
        if not raw:
            return None
        try:
            config = config_from_mapping(cls, raw)
        except (TypeError, ValueError, KeyError) as exc:
            retired = self.memory.retire_active(
                self.catalog_key,
                domain,
                reason=f"segment strategy is invalid for the current schema: {type(exc).__name__}",
            )
            if retired:
                self.rollback_events.append({"domain": domain, **retired})
            return None
        if asdict(config) != raw:
            retired = self.memory.retire_active(
                self.catalog_key,
                domain,
                reason="segment strategy required canonicalization against the current schema",
            )
            if retired:
                self.rollback_events.append({"domain": domain, **retired})
            return None
        return config

    def _refresh_portfolio(self) -> None:
        self.segment_router = SegmentRouter(self.catalog, self.search, self.recommend)
        self.search_portfolio = {
            segment: config
            for segment in self.segment_router.known_segments("search")
            if (config := self._load_segment_config("search", segment, SearchConfig)) is not None
        }
        self.recommend_portfolio = {
            segment: config
            for segment in self.segment_router.known_segments("recommend")
            if (config := self._load_segment_config("recommend", segment, RecommendConfig)) is not None
        }

    def _validate_active_portfolio(self) -> None:
        if not self.catalog.reward_spec:
            return
        reward_spec = self.catalog.reward_spec
        assert reward_spec is not None
        for surface, portfolio, fallback in (
            ("search", self.search_portfolio, self.search),
            ("recommend", self.recommend_portfolio, self.recommend),
        ):
            partitions = self.segment_router.partition_events(self.catalog.events, surface=surface)
            for segment, config in list(portfolio.items()):
                domain = strategy_domain(surface, segment)
                skill = self.memory.active_skill(self.catalog_key, domain)
                if not skill or self._validation_is_fresh(skill):
                    continue
                events = partitions.get(segment, [])
                requests = len(request_groups(events, surface=surface))
                if requests < self.MIN_ACTIVE_SEGMENT_REQUESTS:
                    retired = self.memory.retire_active(
                        self.catalog_key,
                        domain,
                        reason="segment no longer has enough production requests for active routing",
                    )
                    portfolio.pop(segment, None)
                    if retired:
                        self.rollback_events.append({"domain": domain, **retired})
                    continue
                if surface == "search":
                    candidate = fallback.with_config(config)
                    current_report = evaluate_logged_policy(
                        events,
                        surface="search",
                        reward_spec=reward_spec,
                        search_engine=fallback,
                    )
                    candidate_report = evaluate_logged_policy(
                        events,
                        surface="search",
                        reward_spec=reward_spec,
                        search_engine=candidate,
                    )
                else:
                    candidate = fallback.with_config(config)
                    current_report = evaluate_logged_policy(
                        events,
                        surface="recommend",
                        reward_spec=reward_spec,
                        recommend_engine=fallback,
                    )
                    candidate_report = evaluate_logged_policy(
                        events,
                        surface="recommend",
                        reward_spec=reward_spec,
                        recommend_engine=candidate,
                    )
                current_reward = float(current_report.get("reward", 0.0))
                candidate_reward = float(candidate_report.get("reward", 0.0))
                if candidate_reward < current_reward - 0.015:
                    retired = self.memory.retire_active(
                        self.catalog_key,
                        domain,
                        reason="active segment strategy regressed against the current global fallback",
                    )
                    portfolio.pop(segment, None)
                    if retired:
                        self.rollback_events.append({"domain": domain, **retired})
                    continue
                self.memory.mark_skill_validation(
                    self.catalog_key,
                    domain,
                    str(skill["fingerprint"]),
                    metrics={
                        "segment": segment,
                        "business_reward": candidate_reward,
                        "global_fallback_reward": current_reward,
                        "requests": requests,
                    },
                )

    def fork(self) -> "ToolRegistry":
        """Pick up durable strategies while reusing immutable/heavy features."""

        clone = object.__new__(type(self))
        clone.catalog = self.catalog
        clone.memory = self.memory
        clone.network = self.network
        clone.catalog_key = self.catalog_key
        clone.rollback_events = []
        clone.search = self.search.with_config(clone._load_config("search", SearchConfig))
        clone.recommend = self.recommend.with_config(clone._load_config("recommend", RecommendConfig))
        clone._specs = clone._build_specs()
        clone._refresh_portfolio()
        clone._validate_active_portfolio()
        return clone

    def inspect_data(self) -> dict[str, Any]:
        result = super().inspect_data()
        active = dict(result.get("active") or {})
        active["search_portfolio"] = {
            segment: asdict(config) for segment, config in sorted(self.search_portfolio.items())
        }
        active["recommend_portfolio"] = {
            segment: asdict(config) for segment, config in sorted(self.recommend_portfolio.items())
        }
        return {
            **result,
            "active": active,
            "segment_routing": {
                "search": self.segment_router.manifest("search"),
                "recommend": self.segment_router.manifest("recommend"),
            },
        }

    def run_search(self, query: str | None = None, **_: Any) -> dict[str, Any]:
        query = query or ""
        segment = self.segment_router.search_segment(query)
        config = self.search_portfolio.get(segment)
        engine = self.search.with_config(config) if config is not None else self.search
        return {
            "query": query,
            "segment": segment,
            "strategy_scope": "segment" if config is not None else "global",
            "results": engine.search(query, limit=8),
        }

    def search_diagnose(self, query: str | None = None, **kwargs: Any) -> dict[str, Any]:
        result = super().search_diagnose(query=query, **kwargs)
        segment = self.segment_router.search_segment(query or "")
        return {
            **result,
            "segment": segment,
            "strategy_scope": "segment" if segment in self.search_portfolio else "global",
        }

    def run_recommend(self, user_id: str | None = None, **_: Any) -> dict[str, Any]:
        user_id = user_id or "new-user"
        segment = self.segment_router.recommend_segment(user_id)
        config = self.recommend_portfolio.get(segment)
        engine = self.recommend.with_config(config) if config is not None else self.recommend
        return {
            "user_id": user_id,
            "history_events": len(self.recommend._by_user.get(user_id, [])),
            "segment": segment,
            "strategy_scope": "segment" if config is not None else "global",
            "results": engine.recommend(user_id, limit=8),
        }

    def recommend_diagnose(self, user_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        user_id = user_id or "new-user"
        result = super().recommend_diagnose(user_id=user_id, **kwargs)
        segment = self.segment_router.recommend_segment(user_id)
        return {
            **result,
            "segment": segment,
            "strategy_scope": "segment" if segment in self.recommend_portfolio else "global",
        }

    def _remember_segment_portfolio(
        self,
        result: dict[str, Any],
        *,
        surface: str,
        activate: bool,
        invocation_id: str | None,
    ) -> list[dict[str, Any]]:
        portfolio = result.get("segment_portfolio") or {}
        if not portfolio.get("available"):
            return []
        learned: list[dict[str, Any]] = []
        for entry in portfolio.get("entries") or []:
            if not isinstance(entry, dict) or not entry.get("trusted"):
                continue
            segment = str(entry.get("segment") or "")
            config = entry.get("candidate_config")
            if not segment or not isinstance(config, dict):
                continue
            domain = strategy_domain(surface, segment)
            status = "active" if activate else "trusted"
            payload: dict[str, Any] = {
                "segment": segment,
                "routing_basis": (portfolio.get("routing") or {}).get("routing_basis"),
                "discovery_reward_delta": entry.get("discovery_reward_delta", 0.0),
                "holdout_reward_delta": entry.get("holdout_reward_delta", 0.0),
                "full_reward_delta": entry.get("full_reward_delta", 0.0),
                "confidence": entry.get("confidence", {}),
                "guardrail": entry.get("guardrail", {}),
            }
            if activate:
                payload.update(
                    {
                        "validated_at": time.time(),
                        "validation": {
                            "business_reward": entry.get("candidate_reward", 0.0),
                            "requests": entry.get("full_requests", 0),
                            "segment": segment,
                        },
                    }
                )
            skill = self.memory.remember_strategy(
                self.catalog_key,
                domain,
                config,
                score=float(entry.get("candidate_reward", 0.0)),
                evidence=int(entry.get("full_requests", 0)),
                status=status,
                payload=payload,
                invocation_id=(f"{invocation_id}:{domain}" if invocation_id else None),
                tool_result={"segment_portfolio_entry": entry},
            )
            learned.append({"segment": segment, "domain": domain, **skill})
        if activate and learned:
            self._refresh_portfolio()
        return learned

    def search_evolve(
        self,
        activate: bool = False,
        _invocation_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        effective_activate = self._activation_allowed("search", activate)
        result = super().search_evolve(
            activate=effective_activate,
            _invocation_id=_invocation_id,
            **kwargs,
        )
        global_activated = bool(result.get("activated"))
        portfolio_activate = (
            effective_activate
            and bool(result.get("activation_eligible", True))
            and not global_activated
        )
        portfolio_skills = self._remember_segment_portfolio(
            result,
            surface="search",
            activate=portfolio_activate,
            invocation_id=_invocation_id,
        )
        if global_activated:
            # Segment boundaries were measured against the pre-activation global
            # strategy. Rebuild routing and require another independent segment
            # validation before any segment override can become active.
            self._refresh_portfolio()
        if portfolio_skills:
            result["learned"] = True
        result["portfolio_skills"] = portfolio_skills
        result["portfolio_activated"] = bool(portfolio_activate and portfolio_skills)
        if effective_activate and global_activated and portfolio_skills:
            result["portfolio_activation_blocked_by"] = "global_strategy_changed_requires_segment_revalidation"
        if activate and not effective_activate:
            result["activation_blocked_by"] = "production_request_floor<8"
        return result

    def recommend_evolve(
        self,
        activate: bool = False,
        _invocation_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        effective_activate = self._activation_allowed("recommend", activate)
        result = super().recommend_evolve(
            activate=effective_activate,
            _invocation_id=_invocation_id,
            **kwargs,
        )
        global_activated = bool(result.get("activated"))
        portfolio_activate = (
            effective_activate
            and bool(result.get("activation_eligible", True))
            and not global_activated
        )
        portfolio_skills = self._remember_segment_portfolio(
            result,
            surface="recommend",
            activate=portfolio_activate,
            invocation_id=_invocation_id,
        )
        if global_activated:
            self._refresh_portfolio()
        if portfolio_skills:
            result["learned"] = True
        result["portfolio_skills"] = portfolio_skills
        result["portfolio_activated"] = bool(portfolio_activate and portfolio_skills)
        if effective_activate and global_activated and portfolio_skills:
            result["portfolio_activation_blocked_by"] = "global_strategy_changed_requires_segment_revalidation"
        if activate and not effective_activate:
            result["activation_blocked_by"] = "production_request_floor<8"
        return result


__all__ = ["ToolRegistry"]
