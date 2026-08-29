from __future__ import annotations

from dataclasses import asdict

import pytest

from lingjing_harness.algorithms import RecommendConfig
from lingjing_harness.runtime import AgentHarness, AgentMemory
from lingjing_harness.runtime.backend_memory import BackendScopedMemory
from lingjing_harness.runtime.invocation_maintenance import discard_completed_run_invocations
from lingjing_harness.runtime.memory import catalog_fingerprint
from lingjing_harness.sample_data import build_sample_catalog


_ADAPTIVE_PROMPT = "帮我看看用户 u-lin 的推荐首屏，给我一个候选改进方案，先离线不要上线。"


def _evolve_action(payload: dict) -> dict:
    return next(row for row in payload.get("actions", []) if row.get("tool") == "recommend.evolve")


def test_completed_run_discards_replay_rows_after_final_checkpoint(tmp_path) -> None:
    memory = AgentMemory(tmp_path / "memory.db")
    checkpoints: list[dict] = []

    result = AgentHarness(build_sample_catalog(), memory=memory).run(
        _ADAPTIVE_PROMPT,
        checkpoint_sink=lambda payload: checkpoints.append(dict(payload)),
    )
    evolve = _evolve_action(result)

    assert checkpoints[-1]["status"] == "completed"
    assert evolve["result"]["learned"] is True
    assert memory.invocation_result(evolve["invocation_id"]) is None
    assert memory.strategies(catalog_fingerprint(build_sample_catalog()), "recommend")


def test_checkpoint_failure_keeps_replay_rows_for_recovery(tmp_path) -> None:
    memory = AgentMemory(tmp_path / "memory.db")
    failed_checkpoint: dict = {}

    def fail_after_adaptation(payload: dict) -> None:
        if any(row.get("tool") == "recommend.evolve" for row in payload.get("actions", [])):
            failed_checkpoint.clear()
            failed_checkpoint.update(payload)
            raise RuntimeError("simulated checkpoint failure")

    with pytest.raises(RuntimeError, match="simulated checkpoint failure"):
        AgentHarness(build_sample_catalog(), memory=memory).run(
            _ADAPTIVE_PROMPT,
            checkpoint_sink=fail_after_adaptation,
        )

    evolve = _evolve_action(failed_checkpoint)
    replay = memory.invocation_result(evolve["invocation_id"])
    assert replay is not None
    assert replay["skill"]["wins"] == 1


def test_backend_cleanup_is_scoped_to_one_runtime_namespace(tmp_path) -> None:
    base = AgentMemory(tmp_path / "memory.db")
    catalog = build_sample_catalog()
    key = catalog_fingerprint(catalog)
    first = BackendScopedMemory(
        base,
        recommend_scope="recommend-first",
        invocation_scope="runtime-first",
    )
    second = BackendScopedMemory(
        base,
        recommend_scope="recommend-second",
        invocation_scope="runtime-second",
    )
    invocation_id = "run-shared:1:recommend.evolve"
    config = asdict(RecommendConfig())

    first.remember_strategy(
        key,
        "recommend",
        config,
        score=0.8,
        evidence=8,
        invocation_id=invocation_id,
        tool_result={"marker": "first"},
    )
    second.remember_strategy(
        key,
        "recommend",
        config,
        score=0.8,
        evidence=8,
        invocation_id=invocation_id,
        tool_result={"marker": "second"},
    )

    assert first.invocation_result(invocation_id)["result"]["marker"] == "first"
    assert second.invocation_result(invocation_id)["result"]["marker"] == "second"
    assert discard_completed_run_invocations(first, "run-shared") == 1
    assert first.invocation_result(invocation_id) is None
    assert second.invocation_result(invocation_id)["result"]["marker"] == "second"


def test_public_fork_preserves_public_completion_boundary() -> None:
    harness = AgentHarness(build_sample_catalog())
    child = harness.fork()

    assert type(child) is AgentHarness
    assert child.memory is harness.memory
    assert child.tools.search._vectors is harness.tools.search._vectors
    assert child.tools.recommend._co is harness.tools.recommend._co
