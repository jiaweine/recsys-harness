from __future__ import annotations

from typing import Any

from lingjing_harness.algorithms import (
    RecommendConfig,
    RecommendationEngine,
    SearchConfig,
    SearchEngine,
    evolve_recommend,
    evolve_search,
)
from lingjing_harness.algorithms.capabilities import config_from_mapping
from lingjing_harness.experiment_lifecycle import evaluate_candidate_experiment

from .tools_production import ToolRegistry as _ProductionToolRegistry


class ToolRegistry(_ProductionToolRegistry):
    """Production registry with explicit counterfactual trust progression.

    Offline trust still owns strategy learning. An imported ExperimentSpec can
    additionally qualify that exact candidate for a controlled online test using
    explicit IPS/SNIPS/DR evidence, but OPE never grants production activation on
    its own. Existing workspaces without an experiment contract preserve the
    historical activation behavior exactly.
    """

    def _backend_strategy_scope(self, surface: str) -> str:
        config = getattr(self, "runtime_backend_config", None)
        if not isinstance(config, dict):
            return ""
        scopes = config.get("strategy_scopes") or {}
        if not isinstance(scopes, dict):
            return ""
        return str(scopes.get(surface) or "")

    def _apply_experiment_progression(
        self,
        result: dict[str, Any],
        *,
        surface: str,
        activation_requested: bool,
    ) -> dict[str, Any]:
        result = dict(result)
        candidate_config = result.get("candidate_config")
        if not isinstance(candidate_config, dict):
            result.update(
                {
                    "activation_requested": bool(activation_requested),
                    "activation_eligible": False,
                    "online_test_eligible": False,
                    "experiment_validation": {
                        "contract_present": False,
                        "available": False,
                        "reason": "candidate_config_unavailable",
                    },
                }
            )
            return result

        experiment = evaluate_candidate_experiment(
            self.catalog,
            surface,
            candidate_config,
            backend_scope=self._backend_strategy_scope(surface),
        )
        contract_present = bool(experiment.get("contract_present"))
        evaluation = experiment.get("evaluation") or {}
        decision = evaluation.get("decision") or {}
        online_test_eligible = bool(
            contract_present and decision.get("eligible_for_online_test")
        )
        offline_trusted = bool(result.get("trusted"))
        business_trusted = bool(result.get("business_trusted"))

        # An explicit experiment contract changes the progression semantics only
        # for its exact candidate policy. OPE may qualify a controlled online test,
        # but global activation still requires actual business validation. When no
        # contract matches, retain the established offline-trust activation path.
        activation_eligible = bool(
            offline_trusted and (not contract_present or business_trusted)
        )
        evidence_status = {
            "offline_trusted": offline_trusted,
            "online_test_eligible": online_test_eligible,
            "business_trusted": business_trusted,
            "activation_eligible": activation_eligible,
        }
        result.update(
            {
                "candidate_policy_id": experiment.get("candidate_policy_id"),
                "experiment_validation": experiment,
                "online_test_eligible": online_test_eligible,
                "activation_requested": bool(activation_requested),
                "activation_eligible": activation_eligible,
                "evidence_status": evidence_status,
            }
        )
        if (
            activation_requested
            and offline_trusted
            and contract_present
            and not business_trusted
        ):
            result["activation_blocked_reason"] = (
                "explicit_experiment_requires_business_validation"
            )
        return result

    @staticmethod
    def _strategy_payload(result: dict[str, Any], *, activate: bool) -> dict[str, Any]:
        payload = _ProductionToolRegistry._strategy_payload(result, activate=activate)
        payload.update(
            {
                "candidate_policy_id": result.get("candidate_policy_id"),
                "experiment_validation": result.get("experiment_validation", {}),
                "evidence_status": result.get("evidence_status", {}),
                "online_test_eligible": bool(result.get("online_test_eligible")),
                "activation_eligible": bool(result.get("activation_eligible")),
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

        remembered = self._evolution_memory("search")
        current_config = self.search.config
        result = evolve_search(self.catalog, self.search, remembered=remembered)
        result["credit_learning"] = self._record_credit(
            "search",
            vars(current_config) if hasattr(current_config, "__dict__") else {
                field: getattr(current_config, field)
                for field in current_config.__dataclass_fields__
            },
            result,
        )
        result = self._apply_experiment_progression(
            result,
            surface="search",
            activation_requested=activate,
        )
        should_activate = bool(activate and result.get("activation_eligible"))
        result["activated"] = False
        result["learned"] = False
        if result.get("trusted"):
            status = "active" if should_activate else "trusted"
            skill = self.memory.remember_strategy(
                self.catalog_key,
                "search",
                result["candidate_config"],
                score=self._strategy_score(result),
                evidence=self._strategy_evidence(result, "queries"),
                status=status,
                payload=self._strategy_payload(result, activate=should_activate),
                invocation_id=_invocation_id,
                tool_result=result,
            )
            result["skill"] = skill
            result["learned"] = True
            if should_activate:
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
        current_config = self.recommend.config
        result = evolve_recommend(self.catalog, self.recommend, remembered=remembered)
        result["credit_learning"] = self._record_credit(
            "recommend",
            vars(current_config) if hasattr(current_config, "__dict__") else {
                field: getattr(current_config, field)
                for field in current_config.__dataclass_fields__
            },
            result,
        )
        result = self._apply_experiment_progression(
            result,
            surface="recommend",
            activation_requested=activate,
        )
        should_activate = bool(activate and result.get("activation_eligible"))
        result["activated"] = False
        result["learned"] = False
        if result.get("trusted"):
            status = "active" if should_activate else "trusted"
            skill = self.memory.remember_strategy(
                self.catalog_key,
                "recommend",
                result["candidate_config"],
                score=self._strategy_score(result),
                evidence=self._strategy_evidence(result, "users"),
                status=status,
                payload=self._strategy_payload(result, activate=should_activate),
                invocation_id=_invocation_id,
                tool_result=result,
            )
            result["skill"] = skill
            result["learned"] = True
            if should_activate:
                self.recommend = RecommendationEngine(
                    self.catalog,
                    config_from_mapping(RecommendConfig, result["candidate_config"]),
                )
                result["activated"] = True
        return result


__all__ = ["ToolRegistry"]
