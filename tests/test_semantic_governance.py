from __future__ import annotations

from lingjing_harness.runtime.capabilities import RUNTIME_CAPABILITIES
from lingjing_harness.runtime.contracts import AgentPlan, EvidenceRequirement, MissionGraph
from lingjing_harness.runtime.mission_compiler import MissionCompiler
from lingjing_harness.runtime.semantic_governance import SemanticGovernanceCompiler


def test_mission_compiler_persists_valid_semantic_governance() -> None:
    mission = MissionCompiler().compile(
        AgentPlan(
            mode="search",
            goal="检查当前搜索结果",
            query="耳机",
        )
    )

    semantic = mission.semantic_governance
    assert semantic["valid"] is True
    assert semantic["fingerprint"]
    assert semantic["type_counts"]["Objective"] == 1
    assert semantic["type_counts"]["EvidenceRequirement"] >= 2
    assert semantic["type_counts"]["Capability"] >= 2
    assert semantic["jsonld"]["@graph"]

    restored = MissionGraph.from_dict(mission.dict())
    assert restored.semantic_governance["fingerprint"] == semantic["fingerprint"]


def test_network_capability_requires_explicit_network_authority() -> None:
    denied = MissionCompiler().compile(
        AgentPlan(mode="audit", goal="全局体检", allow_network=False)
    )
    denied_rows = denied.semantic_governance["jsonld"]["@graph"]
    assert not any(
        str(row.get("@id", "")).endswith("capability:web.research")
        for row in denied_rows
    )

    allowed = MissionCompiler().compile(
        AgentPlan(mode="audit", goal="联网做全局体检", allow_network=True)
    )
    rows = allowed.semantic_governance["jsonld"]["@graph"]
    network = next(
        row
        for row in rows
        if str(row.get("@id", "")).endswith("authority:network")
    )
    web = next(
        row
        for row in rows
        if str(row.get("@id", "")).endswith("capability:web.research")
    )
    assert network["granted"] is True
    assert web["xushu:requiresAuthority"]


def test_semantic_governance_detects_requirement_cycles() -> None:
    mission = MissionGraph(
        objective="invalid cycle",
        mode="audit",
        requirements={
            "a": EvidenceRequirement(
                key="a",
                label="a",
                domain="general",
                tool="data.inspect",
                capabilities=("data.inspect",),
                prerequisites=("b",),
            ),
            "b": EvidenceRequirement(
                key="b",
                label="b",
                domain="general",
                tool="data.inspect",
                capabilities=("data.inspect",),
                prerequisites=("a",),
            ),
        },
    )
    graph = SemanticGovernanceCompiler(RUNTIME_CAPABILITIES).compile(
        AgentPlan(mode="audit", goal="invalid cycle"),
        mission,
    )

    assert graph.valid is False
    assert any(
        row.shape == "AcyclicEvidenceDependencyShape"
        for row in graph.violations
    )
