from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.runtime import AgentHarness, OwnedPolicy


def test_owned_policy_routes_search_and_extracts_query():
    catalog=build_sample_catalog(); plan=OwnedPolicy().plan('最近搜索“露营灯”不准，帮我优化但先不要上线',catalog)
    assert plan.mode == "search"
    assert plan.query == "露营灯"
    assert plan.compare is True


def test_harness_executes_tools_and_verifies():
    result=AgentHarness(build_sample_catalog()).run('最近搜索“露营灯”不准，帮我优化但先不要上线')
    assert result["owned_policy"] is True
    assert result["events"][-1]["progress"] == 100
    assert any(a["tool"]=="search.run" for a in result["actions"])
    assert any(a["tool"]=="search.compare" for a in result["actions"])
    assert result["evidence"]
    assert "### 结论" in result["answer"]


def test_global_audit_runs_both_paths():
    result=AgentHarness(build_sample_catalog()).run('做一次全局体检，告诉我最值得先处理的问题')
    tools={x["tool"] for x in result["actions"]}
    assert {"search.audit","recommend.audit"} <= tools
