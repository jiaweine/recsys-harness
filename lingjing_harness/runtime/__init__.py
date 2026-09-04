from .capabilities import (
    CapabilityContract,
    CapabilityGate,
    CapabilityHypothesis,
    CapabilityRegistry,
    RUNTIME_CAPABILITIES,
)
from .collaborative_tools import RecommendationBackendToolRegistry
from .contracts import RunBudget
from .credit_assignment import apply_semantic_trajectory_credit
from .deliberation import DeliberationEngine, TrajectoryCritic
from .harness import AgentHarness as _BaseAgentHarness, RunCancelled
from .invocation_maintenance import discard_completed_run_invocations
from .memory import AgentMemory, catalog_fingerprint
from .mechanism_graph import (
    mechanism_graph_snapshot,
    mechanism_stats,
    record_mechanism_evidence,
    validate_mechanism_graph,
    validate_mechanism_graph_with_shacl,
)
from .mechanism_transfer import (
    install_mechanism_transfer,
    mechanism_pair_priors,
    record_runtime_mechanism_evidence,
)
from .mission_compiler import MissionCompiler
from .network import NetworkResearch
from .optimizer_tools import OptimizerToolRegistry
from .optimizer_observation_drift import install_optimizer_observation_drift_guard
from .optimizer_observation_drift_confidence import (
    install_optimizer_observation_drift_confidence,
)
from .optimizer_observation_legacy_recency_anchor import (
    install_optimizer_observation_legacy_recency_anchor,
)
from .optimizer_observation_memory import install_optimizer_observation_runtime
from .optimizer_observation_recency_anchor import (
    install_optimizer_observation_recency_anchor,
)
from .optimizer_observation_snapshot import install_optimizer_observation_snapshot
from .optimizer_observation_weighting import install_optimizer_observation_weighting
from .optimizer_routing_checkpoint import install_optimizer_routing_checkpoint
from .optimizer_routing_epoch import install_optimizer_routing_epoch_counts
from .optimizer_routing_epoch_fence import install_optimizer_routing_epoch_fence
from .perception import PerceptionEngine
from .policy import OwnedPolicy
from .semantic_tools import SearchBackendToolRegistry
from .skill_retention import prune_retired_strategy_history
from .tools import ToolRegistry
from .verifier import ResultVerifier
from .backend_config import RuntimeBackendConfig, build_runtime_tools


# Public runtimes enrich evolution memory with second-order mechanism interaction
# priors. The original single-arm credit path remains authoritative for first-order
# routing; the bridge appends only co-occurrence pair evidence.
install_mechanism_transfer()
# Preserve the pre-observation router so later confidence gates can reject weak
# durable geometry all the way back to trusted-strategy / legacy routing instead
# of silently falling through to the unweighted observation wrapper.
OptimizerToolRegistry._routing_context_without_optimizer_observations = (
    OptimizerToolRegistry._routing_context
)
# Keep evaluator-paid search geometry in a separate routing-only durable ledger.
# This installs after optimizer/backend composition so one capture wrapper observes
# native, Optuna, MOTPE, and qLog rows without changing their evaluation budgets.
install_optimizer_observation_runtime(AgentMemory, OptimizerToolRegistry)
# Weight only the durable routing geometry. Weights are transient, bounded, and
# derived from recency plus repeated evidence; they never affect warm starts or
# downstream holdout/promotion authority.
install_optimizer_observation_weighting(OptimizerToolRegistry)
# Detect structural changes only from already-paid durable observations. When the
# newest time cohort independently clears the existing entry-confidence gate, old
# geometry is quarantined instead of forcing legacy fallback; otherwise fallback
# remains authoritative until enough fresh evidence exists.
install_optimizer_observation_drift_guard(OptimizerToolRegistry)
# Keep each candidate change-point geometry local to its boundary and require a
# leave-one-match-out stability check before contrast geometry can change routing.
# Order inversion keeps its existing authority; this layer still spends no new
# evaluator budget and never supplies warm-start or promotion evidence.
install_optimizer_observation_drift_confidence(OptimizerToolRegistry)
# Preserve only the final hysteresis regime across restarts. The checkpoint wraps
# weighting plus drift handling, revalidates current evidence on restore, and
# carries no optimizer credit, warm-start, activation, or promotion authority.
install_optimizer_routing_checkpoint(OptimizerToolRegistry)
# Materialize latest routing rows and bounded paid history from one SQLite read
# transaction per routing decision. Drift confidence, weighting, epoch-count and
# checkpoint layers then reuse one evaluator-paid evidence cohort.
install_optimizer_observation_snapshot(AgentMemory, OptimizerToolRegistry)
# Future-skewed paid observation clocks stay fresh when first seen locally, then
# age from one durable per-commit routing anchor across restarts. Raw ledger clocks
# and all evaluator/warm-start/promotion authority remain unchanged.
install_optimizer_observation_recency_anchor(OptimizerToolRegistry)
# Databases predating paid-history commit IDs may still contain latest-only rows.
# Anchor only those legacy future clocks by exact durable config/timestamp identity;
# do not synthesize paid history or rewrite the observation ledger.
install_optimizer_observation_legacy_recency_anchor(OptimizerToolRegistry)
# Repeated-evidence counts are lifetime values in the durable latest-row ledger.
# Scope only the routing-time view to the active/pending change-point epoch so old
# regimes cannot inflate weighting, drift entry confidence, or checkpoint fencing.
install_optimizer_routing_epoch_counts(AgentMemory, OptimizerToolRegistry)
# Fence one routing call to the durable epoch token it observed at entry. A stale
# process cannot overwrite a newer epoch checkpoint, and a concurrent epoch advance
# observed before return fails closed to the pre-observation router for that call.
install_optimizer_routing_epoch_fence(OptimizerToolRegistry)


class AgentHarness(_BaseAgentHarness):
    """Public harness with explicit process-level backend configuration.

    The dependency-light reference/native stack remains the default. When no
    registry is supplied, API, CLI, and library callers using this public import
    can select mature optional backends through ``RuntimeBackendConfig`` or the
    corresponding environment variables. Supplying ``tools`` preserves the
    original fully-injected runtime contract.
    """

    def __init__(
        self,
        catalog,
        max_tools: int = 14,
        *,
        memory=None,
        budget=None,
        tools=None,
        backend_config: RuntimeBackendConfig | None = None,
    ) -> None:
        if tools is None:
            shared_memory = memory or AgentMemory()
            tools = build_runtime_tools(
                catalog,
                shared_memory,
                config=backend_config,
            )
            # The tool registry owns the effective procedural-memory facade. For
            # reference backends this is the shared AgentMemory itself; mature
            # backends wrap it with backend-scoped strategy/credit namespaces
            # while still delegating episodes, recall and policy learning.
            memory = tools.memory
        super().__init__(
            catalog,
            max_tools=max_tools,
            memory=memory,
            budget=budget,
            tools=tools,
        )

    def fork(self) -> "AgentHarness":
        """Fork the public runtime while preserving prepared backend state."""

        return AgentHarness(
            self.catalog,
            memory=self.memory,
            budget=RunBudget(
                max_tools=self.budget.max_tools,
                max_cost=self.budget.max_cost,
                max_seconds=self.budget.max_seconds,
            ),
            tools=self.tools.fork(),
        )

    def run(self, *args, **kwargs):
        """Run, apply process credit, persist mechanism evidence, then prune state."""

        result = super().run(*args, **kwargs)

        credit = apply_semantic_trajectory_credit(self.memory, result)
        autonomy = result.setdefault("autonomy", {})
        autonomy["policy_credit_assignment"] = {
            "method": credit.get("method"),
            "applied": bool(credit.get("applied")),
            "horizon": int(credit.get("horizon", 0) or 0),
            "terminal_weight": credit.get("terminal_weight"),
            "process_weight": credit.get("process_weight"),
            "adjusted_policy_rows": int(credit.get("adjusted_policy_rows", 0) or 0),
        }
        result["policy_credit"] = credit

        mechanisms = record_runtime_mechanism_evidence(self.memory, self.catalog_key, result)
        autonomy["mechanism_evidence_graph"] = {
            "method": mechanisms.get("method"),
            "recorded": int(mechanisms.get("recorded", 0) or 0),
            "deduplicated": int(mechanisms.get("deduplicated", 0) or 0),
            "mechanisms": int(mechanisms.get("mechanisms", 0) or 0),
            "contexts": int(mechanisms.get("contexts", 0) or 0),
        }
        result["mechanism_evidence"] = mechanisms

        discard_completed_run_invocations(self.memory, str(result.get("run_id") or ""))
        prune_retired_strategy_history(self.memory)
        return result


__all__ = [
    "AgentHarness", "RunCancelled",
    "AgentMemory",
    "catalog_fingerprint",
    "CapabilityContract", "CapabilityGate", "CapabilityHypothesis", "CapabilityRegistry",
    "RUNTIME_CAPABILITIES", "MissionCompiler",
    "DeliberationEngine", "TrajectoryCritic",
    "NetworkResearch",
    "OptimizerToolRegistry",
    "SearchBackendToolRegistry",
    "RecommendationBackendToolRegistry",
    "RuntimeBackendConfig", "build_runtime_tools",
    "PerceptionEngine",
    "OwnedPolicy",
    "ToolRegistry",
    "ResultVerifier",
    "record_mechanism_evidence", "record_runtime_mechanism_evidence",
    "mechanism_stats", "mechanism_graph_snapshot", "mechanism_pair_priors",
    "validate_mechanism_graph", "validate_mechanism_graph_with_shacl",
]
