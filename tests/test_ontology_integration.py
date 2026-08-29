from __future__ import annotations

import pytest

from lingjing_harness.runtime.contracts import AgentPlan
from lingjing_harness.runtime.mission_compiler import MissionCompiler
from lingjing_harness.runtime.semantic_governance import validate_with_pyshacl


pytest.importorskip("rdflib")
pytest.importorskip("pyshacl")


def test_compiled_search_mission_conforms_to_packaged_shacl_shapes() -> None:
    mission = MissionCompiler().compile(
        AgentPlan(
            mode="search",
            goal="联网检查并优化搜索，但不要上线",
            query="无线耳机",
            explore=True,
            allow_adaptation=False,
            allow_network=True,
        )
    )

    semantic = mission.semantic_governance
    assert semantic["valid"] is True
    result = validate_with_pyshacl(semantic["jsonld"])

    assert result["conforms"] is True, result["results_text"]
    assert result["validator"] == "pyshacl+rdfs"


def test_compiled_recommend_mission_conforms_to_packaged_shacl_shapes() -> None:
    mission = MissionCompiler().compile(
        AgentPlan(
            mode="recommend",
            goal="检查推荐并探索候选策略",
            user_id="user-1",
            explore=True,
            allow_adaptation=False,
            allow_network=False,
        )
    )

    result = validate_with_pyshacl(mission.semantic_governance["jsonld"])
    assert result["conforms"] is True, result["results_text"]
