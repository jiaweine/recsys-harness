from .capabilities import (
    CapabilityContract,
    CapabilityGate,
    CapabilityHypothesis,
    CapabilityRegistry,
    RUNTIME_CAPABILITIES,
)
from .collaborative_tools import RecommendationBackendToolRegistry
from .deliberation import DeliberationEngine, TrajectoryCritic
from .harness import AgentHarness as _BaseAgentHarness, RunCancelled
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
            memory = shared_memory
        super().__init__(
            catalog,
            max_tools=max_tools,
            memory=memory,
            budget=budget,
            tools=tools,
        )


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
