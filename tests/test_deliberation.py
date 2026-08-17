from lingjing_harness.runtime import AgentHarness, OwnedPolicy, ToolRegistry
from lingjing_harness.runtime.contracts import RunState
from lingjing_harness.sample_data import build_sample_catalog


def _complete(policy, plan, state, registry, tool, result=None):
    spec = registry.get(tool)
    payload = result if result is not None else registry.execute(
        tool,
        {},
        allow_adaptation=plan.allow_adaptation,
        allow_network=plan.allow_network,
        invocation_id=f"test:{len(state.actions) + 1}:{tool}",
    )
    state.cycle += 1
    state.observations[tool] = payload
    action = {
        "invocation_id": f"test:{state.cycle}:{tool}",
        "tool": tool,
        "risk": spec.risk,
        "cost": spec.cost,
        "input": {},
        "status": "completed",
        "result": payload,
    }
    state.actions.append(action)
    policy.reflect(plan, state, action)
    return action


def test_mission_graph_is_task_scoped_instead_of_expanding_every_domain():
    catalog = build_sample_catalog()
    policy = OwnedPolicy()
    plan = policy.plan("看看用户 u-lin 的推荐首屏，探索一个候选改进方案但先不要上线", catalog)
    state = RunState()
    mission = policy.initialize(plan, state)

    assert plan.mode == "recommend"
    assert plan.explore is True
    assert "recommend_reproduction" in mission.requirements
    assert "recommend_global_quality" in mission.requirements
    assert "recommend_candidate_validation" in mission.requirements
    assert "search_global_quality" not in mission.requirements
    assert "search_candidate_validation" not in mission.requirements


def test_decision_targets_evidence_requirement_and_exposes_utility_decomposition():
    catalog = build_sample_catalog()
    registry = ToolRegistry(catalog)
    policy = OwnedPolicy()
    plan = policy.plan("检查搜索“露营灯”的当前体验", catalog)
    state = RunState()
    policy.initialize(plan, state)

    first = policy.decide(plan, state, registry.list_specs(), policy_bonus=lambda _: 0.0)
    assert first.step is not None
    assert first.step.tool == "data.inspect"
    assert first.target_requirement == "workspace_facts"
    assert {"priority", "information_gain", "evidence_gap", "cost_pressure", "risk_pressure"} <= first.utility.keys()

    inspect = registry.execute("data.inspect", {})
    _complete(policy, plan, state, registry, "data.inspect", inspect)
    second = policy.decide(plan, state, registry.list_specs(), policy_bonus=lambda _: 0.0)
    assert second.step is not None
    assert second.step.tool == "search.run"
    assert second.target_requirement == "search_reproduction"


def test_weak_observation_activates_hypothesis_and_diagnostic_requirement():
    catalog = build_sample_catalog()
    registry = ToolRegistry(catalog)
    policy = OwnedPolicy()
    plan = policy.plan("最近搜索“露营灯”不准，帮我探索改进方向但先不要上线", catalog)
    state = RunState()
    mission = policy.initialize(plan, state)

    _complete(policy, plan, state, registry, "data.inspect", registry.execute("data.inspect", {}))
    weak_result = {"query": "露营灯", "results": []}
    _complete(policy, plan, state, registry, "search.run", weak_result)

    assert mission.requirements["search_diagnosis"].status == "open"
    assert mission.hypotheses["search_local_mismatch"].status == "supported"
    assert mission.hypotheses["search_local_mismatch"].confidence >= 0.8

    next_decision = policy.decide(plan, state, registry.list_specs(), policy_bonus=lambda _: 0.0)
    assert next_decision.step is not None
    assert next_decision.step.tool == "search.diagnose"
    assert "search_local_mismatch" in next_decision.hypotheses


def test_trajectory_critic_blocks_early_close_and_allows_evidence_complete_close():
    catalog = build_sample_catalog()
    registry = ToolRegistry(catalog)
    policy = OwnedPolicy()
    plan = policy.plan("检查搜索“露营灯”的当前体验", catalog)
    state = RunState()
    policy.initialize(plan, state)

    before = policy.critique(plan, state)
    assert before["ready"] is False
    assert "workspace_facts" in before["unresolved"]

    _complete(policy, plan, state, registry, "data.inspect", registry.execute("data.inspect", {}))
    strong_result = {
        "query": "露营灯",
        "results": [{"id": "x", "title": "露营灯", "rank": 1, "score": 1.0, "signals": {"match": 0.9}}],
    }
    _complete(policy, plan, state, registry, "search.run", strong_result)

    after = policy.critique(plan, state)
    assert after["ready"] is True
    assert after["unresolved"] == []
    assert after["evidence_coverage"] == 1.0


def test_checkpoint_persists_mission_hypotheses_reflections_and_critic(tmp_path):
    catalog = build_sample_catalog()
    checkpoint = {}

    def interrupt_after_first(payload):
        checkpoint.clear()
        checkpoint.update(payload)
        if len(payload.get("actions", [])) == 1:
            raise RuntimeError("interrupt after first deliberative checkpoint")

    try:
        AgentHarness(catalog).run(
            "检查搜索“露营灯”的当前体验",
            checkpoint_sink=interrupt_after_first,
        )
    except RuntimeError as exc:
        assert "deliberative checkpoint" in str(exc)
    else:
        raise AssertionError("checkpoint interruption did not fire")

    assert checkpoint["mission"]["requirements"]["workspace_facts"]["status"] == "satisfied"
    assert checkpoint["reflections"]
    assert checkpoint["critic"]

    resumed = AgentHarness(catalog).run(
        "检查搜索“露营灯”的当前体验",
        resume=checkpoint,
    )
    assert resumed["durability"]["deliberation_state_persisted"] is True
    assert resumed["deliberation"]["mission"]["requirements"]["workspace_facts"]["status"] == "satisfied"
    assert resumed["deliberation"]["reflections"]
    assert resumed["verification"]["checks"]["mission_terminal"] is True


def test_full_run_returns_inspectable_deliberation_trace():
    result = AgentHarness(build_sample_catalog()).run("做一次全局体检，告诉我最值得先处理的问题")
    mission = result["deliberation"]["mission"]
    tools = {row["tool"] for row in result["actions"]}

    assert {"search.audit", "recommend.audit"} <= tools
    assert mission["requirements"]["search_global_quality"]["status"] == "satisfied"
    assert mission["requirements"]["recommend_global_quality"]["status"] == "satisfied"
    assert result["deliberation"]["critic"]["ready"] is True
    assert result["deliberation"]["reflections"]
    assert all("requirement" in row and "utility" in row for row in result["decisions"])
