import pytest

from lingjing_harness.runtime import (
    CapabilityContract,
    CapabilityRegistry,
    DeliberationEngine,
    OwnedPolicy,
    RUNTIME_CAPABILITIES,
    ToolRegistry,
)
from lingjing_harness.runtime.capabilities import DEFAULT_CAPABILITIES
from lingjing_harness.runtime.contracts import MissionGraph, RunState, ToolSpec
from lingjing_harness.sample_data import build_sample_catalog


def test_runtime_tool_metadata_matches_capability_contracts():
    specs = ToolRegistry(build_sample_catalog()).list_specs()
    assert RUNTIME_CAPABILITIES.validate_tool_specs(specs) == []
    names = {spec.name for spec in specs}
    assert names <= {row["name"] for row in RUNTIME_CAPABILITIES.manifest()}


def test_capability_compiler_preserves_vertical_scope_and_is_deterministic():
    catalog = build_sample_catalog()
    policy = OwnedPolicy()
    plan = policy.plan(
        "看看用户 u-lin 的推荐首屏，探索一个候选改进方案但先不要上线",
        catalog,
    )

    first = policy.deliberation.compiler.compile(plan)
    second = policy.deliberation.compiler.compile(plan)
    assert first.dict() == second.dict()
    assert set(first.requirements) >= {
        "workspace_facts",
        "recommend_reproduction",
        "recommend_global_quality",
        "recommend_candidate_validation",
    }
    assert "search_global_quality" not in first.requirements
    assert "search_candidate_validation" not in first.requirements
    assert "recommend.evolve" in first.capability_snapshot


def test_new_capability_extends_mission_without_deliberation_tool_branch():
    custom = CapabilityContract(
        name="search.trace",
        requirement_key="search_trace_evidence",
        label="补充搜索链路追踪证据",
        domain="search",
        description="Trace search evidence through an external implementation",
        risk="read",
        cost=0.2,
        priority="high",
        information_gain=0.99,
        provides=frozenset({"search_trace_evidence"}),
        requires=frozenset({"workspace_facts"}),
        base_modes=frozenset({"search"}),
        title="追踪搜索证据",
        detail="补充当前查询的链路级证据",
        argument_bindings=(("query", "query"),),
        order=25,
    )
    registry = CapabilityRegistry((*DEFAULT_CAPABILITIES, custom))
    policy = OwnedPolicy(capabilities=registry)
    catalog = build_sample_catalog()
    plan = policy.plan("检查搜索“露营灯”的当前体验", catalog)
    state = RunState()
    mission = policy.initialize(plan, state)

    assert "search_trace_evidence" in mission.requirements
    assert mission.requirements["search_trace_evidence"].capabilities == ("search.trace",)

    mission.requirements["workspace_facts"].status = "satisfied"
    mission.requirements["search_reproduction"].status = "satisfied"
    specs = ToolRegistry(catalog).list_specs()
    specs.append(
        ToolSpec(
            "search.trace",
            custom.description,
            custom.risk,
            lambda **_: {"traced": True},
            cost=custom.cost,
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        )
    )
    decision = policy.decide(plan, state, specs, policy_bonus=lambda _: 0.0)
    assert decision.step is not None
    assert decision.step.tool == "search.trace"
    assert decision.target_requirement == "search_trace_evidence"
    assert decision.step.args["query"] == "露营灯"
    assert decision.utility["information_gain"] == 0.99


def test_declarative_evolution_gate_blocks_thin_evidence_without_tool_name_logic():
    catalog = build_sample_catalog()
    policy = OwnedPolicy()
    plan = policy.plan("搜索“露营灯”优化一下，先不要上线", catalog)
    state = RunState()
    mission = policy.initialize(plan, state)

    mission.requirements["workspace_facts"].status = "satisfied"
    mission.requirements["search_reproduction"].status = "satisfied"
    mission.requirements["search_global_quality"].status = "satisfied"
    state.observations["search.audit"] = {"queries": 2, "quality": 0.9}

    policy.decide(
        plan,
        state,
        ToolRegistry(catalog).list_specs(),
        policy_bonus=lambda _: 0.0,
    )
    candidate = mission.requirements["search_candidate_validation"]
    assert candidate.status == "blocked"
    assert "fewer than 3 searchable evaluation queries" in candidate.reason


def test_mission_roundtrip_preserves_capability_snapshot_and_alternatives():
    catalog = build_sample_catalog()
    policy = OwnedPolicy()
    plan = policy.plan("检查搜索“露营灯”的当前体验", catalog)
    mission = policy.initialize(plan, RunState())
    restored = MissionGraph.from_dict(mission.dict())
    assert restored.capability_snapshot == mission.capability_snapshot
    assert restored.requirements["search_reproduction"].capabilities == ("search.run",)


def test_registry_rejects_conflicting_implementations_for_same_evidence_requirement():
    incompatible = CapabilityContract(
        name="search.other",
        requirement_key="search_reproduction",
        label="alternative",
        domain="search",
        description="bad alternative contract",
        risk="read",
        cost=0.1,
        priority="critical",
        information_gain=0.9,
        provides=frozenset({"search_reproduction"}),
        requires=frozenset(),
        base_modes=frozenset({"search"}),
    )
    with pytest.raises(ValueError, match="share requirement semantics"):
        CapabilityRegistry((*DEFAULT_CAPABILITIES, incompatible))
