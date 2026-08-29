from __future__ import annotations

from lingjing_harness.runtime import AgentHarness, AgentMemory
from lingjing_harness.runtime.credit_assignment import (
    apply_semantic_trajectory_credit,
    semantic_requirement_mass,
    trajectory_policy_credits,
)
from lingjing_harness.sample_data import build_sample_catalog


def _synthetic_result() -> dict:
    ns = "https://xushu.ai/ontology/recsys#"
    return {
        "run_id": "run-credit-test",
        "plan": {"mode": "search"},
        "events": [
            {
                "phase": "complete",
                "payload": {"reward": 0.8},
            }
        ],
        "actions": [
            {
                "tool": "root.tool",
                "status": "completed",
                "decision": {"cycle": 1, "requirement": "root"},
            },
            {
                "tool": "leaf.tool",
                "status": "completed",
                "decision": {"cycle": 2, "requirement": "leaf"},
            },
            {
                "tool": "noop.tool",
                "status": "completed",
                "decision": {"cycle": 3, "requirement": "noop"},
            },
        ],
        "deliberation": {
            "mission": {
                "requirements": {
                    "root": {
                        "priority": "critical",
                        "status": "satisfied",
                        "optional": False,
                        "prerequisites": [],
                    },
                    "leaf": {
                        "priority": "high",
                        "status": "satisfied",
                        "optional": False,
                        "prerequisites": ["root"],
                    },
                    "noop": {
                        "priority": "medium",
                        "status": "satisfied",
                        "optional": True,
                        "prerequisites": [],
                    },
                },
                "semantic_governance": {
                    "jsonld": {
                        "@graph": [
                            {
                                "@id": f"{ns}requirement:root",
                                "@type": "xushu:EvidenceRequirement",
                            },
                            {
                                "@id": f"{ns}requirement:leaf",
                                "@type": "xushu:EvidenceRequirement",
                                "xushu:dependsOn": [
                                    {"@id": f"{ns}requirement:root"}
                                ],
                            },
                            {
                                "@id": f"{ns}requirement:noop",
                                "@type": "xushu:EvidenceRequirement",
                            },
                        ]
                    }
                },
            },
            "reflections": [
                {"cycle": 1, "requirements_changed": ["root"], "new_contradictions": []},
                {"cycle": 2, "requirements_changed": ["leaf"], "new_contradictions": []},
                # noop completed but did not create mission progress.
                {"cycle": 3, "requirements_changed": [], "new_contradictions": []},
            ],
        },
    }


def _collateral_damage_result() -> dict:
    return {
        "run_id": "run-credit-collateral",
        "plan": {"mode": "search"},
        "events": [{"phase": "complete", "payload": {"reward": 0.9}}],
        "actions": [
            {
                "tool": "clean.tool",
                "status": "completed",
                "decision": {"cycle": 1, "requirement": "clean"},
            },
            {
                "tool": "mixed.tool",
                "status": "completed",
                "decision": {"cycle": 2, "requirement": "target"},
            },
        ],
        "deliberation": {
            "mission": {
                "requirements": {
                    "clean": {
                        "priority": "high",
                        "status": "satisfied",
                        "optional": False,
                        "prerequisites": [],
                    },
                    "target": {
                        "priority": "critical",
                        "status": "satisfied",
                        "optional": False,
                        "prerequisites": [],
                    },
                    "collateral": {
                        "priority": "critical",
                        "status": "blocked",
                        "optional": False,
                        "prerequisites": [],
                    },
                },
            },
            "reflections": [
                {
                    "cycle": 1,
                    "requirements_changed": ["clean"],
                    "new_contradictions": [],
                },
                {
                    "cycle": 2,
                    "requirements_changed": ["target", "collateral"],
                    "new_contradictions": ["search:quality_vs_recall"],
                },
            ],
        },
    }


def test_semantic_mass_rewards_high_priority_upstream_evidence() -> None:
    mission = _synthetic_result()["deliberation"]["mission"]
    masses = semantic_requirement_mass(mission)

    assert masses["root"] > masses["leaf"] > masses["noop"]


def test_trajectory_credit_is_nonuniform_and_horizon_aware() -> None:
    credit = trajectory_policy_credits(_synthetic_result())

    assert credit["method"] == "semantic_influence_transition_credit"
    assert 0.0 < credit["terminal_weight"] < 1.0
    assert credit["process_weight"] > 0.0
    assert credit["tool_credits"]["root.tool"] > credit["tool_credits"]["leaf.tool"]
    assert credit["tool_credits"]["leaf.tool"] > credit["tool_credits"]["noop.tool"]


def test_credit_uses_all_changed_requirements_and_penalizes_collateral_damage() -> None:
    credit = trajectory_policy_credits(_collateral_damage_result())
    rows = {row["tool"]: row for row in credit["action_credits"]}

    mixed = rows["mixed.tool"]
    assert mixed["target_requirement"] == "target"
    assert mixed["satisfied_requirements"] == ["target"]
    assert mixed["blocked_requirements"] == ["collateral"]
    assert mixed["new_contradictions"] == ["search:quality_vs_recall"]
    assert mixed["blocked_penalty"] > 0.0
    assert mixed["contradiction_penalty"] > 0.0
    assert mixed["process_score"] < rows["clean.tool"]["process_score"]
    # A high-priority target cannot hide a simultaneous high-priority regression.
    assert credit["tool_credits"]["mixed.tool"] < credit["tool_credits"]["clean.tool"]


def test_policy_credit_correction_replaces_equal_terminal_contribution_once() -> None:
    memory = AgentMemory()
    result = _synthetic_result()
    terminal = 0.8
    tools = ["root.tool", "leaf.tool", "noop.tool"]
    memory.update_policy("search", [f"search|{tool}" for tool in tools], terminal)

    applied = apply_semantic_trajectory_credit(memory, result)
    assert applied["applied"] is True
    assert applied["adjusted_policy_rows"] == 3

    with memory._lock:
        conn = memory._connect()
        try:
            rows = conn.execute(
                "select action_key,trials,reward_sum from agent_policy_stats where context_key='search'"
            ).fetchall()
        finally:
            memory._close(conn)
    stats = {row["action_key"]: (int(row["trials"]), float(row["reward_sum"])) for row in rows}
    expected = applied["tool_credits"]
    for tool in tools:
        assert stats[f"search|{tool}"][0] == 1
        assert abs(stats[f"search|{tool}"][1] - expected[tool]) < 1e-9

    second = apply_semantic_trajectory_credit(memory, result)
    assert second["applied"] is False
    assert second["deduplicated"] is True


def test_public_harness_exposes_process_credit_provenance() -> None:
    catalog = build_sample_catalog()
    memory = AgentMemory()
    result = AgentHarness(catalog, memory=memory).run("做一次全局体检")

    credit = result["policy_credit"]
    autonomy = result["autonomy"]["policy_credit_assignment"]
    assert credit["method"] == "semantic_influence_transition_credit"
    assert autonomy["method"] == "semantic_influence_transition_credit"
    assert autonomy["horizon"] >= 1
    assert autonomy["applied"] is True
