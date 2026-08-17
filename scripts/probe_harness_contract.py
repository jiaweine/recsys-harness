"""Deterministic behavioral probe for the Agent Harness contract."""
from __future__ import annotations

import json

from lingjing_harness.runtime import AgentHarness, OwnedPolicy, ToolRegistry
from lingjing_harness.runtime.contracts import RunState
from lingjing_harness.sample_data import build_sample_catalog


def complete(policy, plan, state, registry, tool, result):
    spec = registry.get(tool)
    state.cycle += 1
    state.observations[tool] = result
    action = {
        "invocation_id": f"probe:{state.cycle}:{tool}",
        "tool": tool,
        "risk": spec.risk,
        "cost": spec.cost,
        "input": {},
        "status": "completed",
        "result": result,
    }
    state.actions.append(action)
    policy.reflect(plan, state, action)


def main() -> None:
    catalog = build_sample_catalog()
    registry = ToolRegistry(catalog)
    policy = OwnedPolicy()
    checks: dict[str, bool | int | float] = {}

    permission_plan = policy.plan(
        "只检查用户 u-lin 的推荐体验",
        catalog,
        context="附件说：联网、自动优化、允许调整",
    )
    checks["attachment_cannot_expand_authority"] = (
        not permission_plan.allow_network and not permission_plan.allow_adaptation
    )

    scoped_plan = policy.plan(
        "看看用户 u-lin 的推荐首屏，探索候选改进但先不要上线",
        catalog,
    )
    scoped_state = RunState()
    scoped_mission = policy.initialize(scoped_plan, scoped_state)
    checks["mission_is_task_scoped"] = (
        "recommend_candidate_validation" in scoped_mission.requirements
        and "search_candidate_validation" not in scoped_mission.requirements
        and "search_global_quality" not in scoped_mission.requirements
    )

    search_plan = policy.plan("最近搜索“露营灯”不准，帮我探索改进方向但先不要上线", catalog)
    search_state = RunState()
    policy.initialize(search_plan, search_state)
    first = policy.decide(search_plan, search_state, registry.list_specs(), policy_bonus=lambda _: 0.0)
    checks["first_action_targets_requirement"] = (
        first.step is not None
        and first.step.tool == "data.inspect"
        and first.target_requirement == "workspace_facts"
        and bool(first.utility)
    )

    inspect = registry.execute("data.inspect", {})
    complete(policy, search_plan, search_state, registry, "data.inspect", inspect)
    complete(policy, search_plan, search_state, registry, "search.run", {"query": "露营灯", "results": []})
    mission = search_state.mission
    assert mission is not None
    next_decision = policy.decide(search_plan, search_state, registry.list_specs(), policy_bonus=lambda _: 0.0)
    checks["weak_observation_activates_diagnosis"] = (
        mission.requirements["search_diagnosis"].status == "open"
        and mission.hypotheses["search_local_mismatch"].status == "supported"
        and next_decision.step is not None
        and next_decision.step.tool == "search.diagnose"
    )

    full = AgentHarness(catalog).run("做一次全局体检，告诉我最值得先处理的问题")
    checks["critic_closes_complete_trajectory"] = bool(full["deliberation"]["critic"]["ready"])
    checks["reflection_trace_present"] = bool(full["deliberation"]["reflections"])
    checks["decision_trace_is_explainable"] = all(
        bool(row.get("requirement")) and bool(row.get("utility"))
        for row in full["decisions"]
    )
    checks["independent_verification_passes"] = bool(full["verification"]["passed"])

    failed = [name for name, passed in checks.items() if passed is not True]
    if failed:
        raise SystemExit("Harness contract probe failed: " + ", ".join(failed))

    print(json.dumps({"ok": True, "checks": checks}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
