from .capabilities import (
    CapabilityContract,
    CapabilityGate,
    CapabilityHypothesis,
    CapabilityRegistry,
    RUNTIME_CAPABILITIES,
)
from .collaborative_tools import RecommendationBackendToolRegistry
from .contracts import RunBudget
from .deliberation import DeliberationEngine, TrajectoryCritic
from .harness import AgentHarness as _BaseAgentHarness, RunCancelled
from .invocation_maintenance import discard_completed_run_invocations
from .memory import AgentMemory, catalog_fingerprint
from .mission_compiler import MissionCompiler
from .network import NetworkResearch
from .optimizer_tools import OptimizerToolRegistry
from .perception import PerceptionEngine
from .policy import OwnedPolicy
from .semantic_tools import SearchBackendToolRegistry
from .tools import ToolRegistry
from .verifier import ResultVerifier
from .backend_config import RuntimeBackendConfig, build_runtime_tools


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
        """Run normally, then release replay-only invocation rows on success."""

        result = super().run(*args, **kwargs)
        discard_completed_run_invocations(self.memory, str(result.get("run_id") or ""))
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
]
