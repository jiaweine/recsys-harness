from lingjing_harness.runtime import AgentHarness, AgentMemory, ToolRegistry
from lingjing_harness.runtime.contracts import Decision, PlanStep, RunState, ToolSpec
from lingjing_harness.runtime.policy import OwnedPolicy
from lingjing_harness.sample_data import build_sample_catalog


class CountingNetwork:
    configured = True

    def __init__(self):
        self.calls = 0

    def search(self, query, limit=6):
        self.calls += 1
        return {
            "query": query,
            "results": [
                {
                    "title": "public source",
                    "url": "https://example.com/source",
                    "snippet": "fresh external context",
                }
            ],
            "source": "network",
            "configured": True,
            "count": 1,
        }


def test_network_toggle_grants_permission_without_forcing_external_request(tmp_path):
    catalog = build_sample_catalog()
    network = CountingNetwork()
    memory = AgentMemory(tmp_path / "memory.db")
    registry = ToolRegistry(catalog, memory, network=network)

    result = AgentHarness(catalog, tools=registry).run(
        "检查搜索“露营灯”的结果是否正常",
        allow_network=True,
    )

    tools = [row["tool"] for row in result["actions"]]
    assert result["plan"]["allow_network"] is True
    assert "search.run" in tools
    assert "web.research" not in tools
    assert network.calls == 0
    assert result["network"]["used"] is False


def test_explicit_network_request_still_executes_external_research(tmp_path):
    catalog = build_sample_catalog()
    network = CountingNetwork()
    memory = AgentMemory(tmp_path / "memory.db")
    registry = ToolRegistry(catalog, memory, network=network)

    result = AgentHarness(catalog, tools=registry).run(
        "联网查一下公开资料，再检查搜索“露营灯”",
    )

    tools = [row["tool"] for row in result["actions"]]
    assert result["plan"]["allow_network"] is True
    assert "web.research" in tools
    assert network.calls == 1
    assert result["network"]["used"] is True


def test_permission_only_network_remains_available_when_local_evidence_is_incomplete():
    class NetworkWinner:
        registry = None

        def decide(self, plan, state, tools, *, policy_bonus):
            state.critic = {
                "ready": True,
                "unresolved_contradictions": [],
            }
            return Decision(
                PlanStep("web.research", "research", "fallback", {"query": plan.goal}),
                "network is the remaining useful fallback",
                score=0.4,
            )

    policy = OwnedPolicy(deliberation=NetworkWinner())
    catalog = build_sample_catalog()
    plan = policy.plan("检查搜索“露营灯”", catalog, allow_network=True)
    state = RunState()
    network_tool = ToolSpec(
        "web.research",
        "Search public evidence",
        "network",
        lambda **_: {},
        cost=1.8,
    )

    decision = policy.decide(plan, state, [network_tool], policy_bonus=lambda _: 0.0)
    assert decision.step is not None
    assert decision.step.tool == "web.research"

    state.evidence.append({"kind": "result", "title": "owned result"})
    decision = policy.decide(plan, state, [network_tool], policy_bonus=lambda _: 0.0)
    assert decision.step is None
    assert "边际价值不足" in decision.rationale
