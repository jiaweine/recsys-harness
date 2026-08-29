from __future__ import annotations

import json

from lingjing_harness.runtime import AgentHarness, AgentMemory
from lingjing_harness.runtime.backend_memory import BackendScopedMemory
from lingjing_harness.runtime.memory import catalog_fingerprint
from lingjing_harness.runtime.skill_retention import (
    RETIRED_SKILL_BUDGET,
    prune_retired_strategy_history,
)
from lingjing_harness.sample_data import build_sample_catalog


def _seed_skill(
    memory: AgentMemory,
    *,
    catalog_key: str,
    domain: str,
    index: int,
    status: str = "retired",
) -> str:
    fingerprint = f"skill-{index:04d}"
    updated_at = float(1000 + index)
    with memory._lock:
        conn = memory._connect()
        try:
            conn.execute(
                """
                insert into agent_skills(
                  catalog_key,domain,fingerprint,config,score,evidence,status,wins,
                  payload,created_at,updated_at
                ) values(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    catalog_key,
                    domain,
                    fingerprint,
                    json.dumps({"index": index}),
                    float(index),
                    index,
                    status,
                    1,
                    "{}",
                    updated_at,
                    updated_at,
                ),
            )
            conn.commit()
        finally:
            memory._close(conn)
    return fingerprint


def _status_count(memory: AgentMemory, catalog_key: str, domain: str, status: str) -> int:
    with memory._lock:
        conn = memory._connect()
        try:
            return int(
                conn.execute(
                    "select count(*) from agent_skills where catalog_key=? and domain=? and status=?",
                    (catalog_key, domain, status),
                ).fetchone()[0]
            )
        finally:
            memory._close(conn)


def test_retired_history_is_bounded_without_touching_live_strategies(tmp_path) -> None:
    memory = AgentMemory(tmp_path / "memory.db")
    key = "catalog-a"
    domain = "recommend"
    for index in range(36):
        _seed_skill(memory, catalog_key=key, domain=domain, index=index)
    _seed_skill(memory, catalog_key=key, domain=domain, index=100, status="trusted")
    _seed_skill(memory, catalog_key=key, domain=domain, index=101, status="active")

    removed = prune_retired_strategy_history(memory)

    assert removed == 36 - RETIRED_SKILL_BUDGET
    assert _status_count(memory, key, domain, "retired") == RETIRED_SKILL_BUDGET
    assert _status_count(memory, key, domain, "trusted") == 1
    assert _status_count(memory, key, domain, "active") == 1


def test_recovery_referenced_retired_skill_is_never_pruned(tmp_path) -> None:
    memory = AgentMemory(tmp_path / "memory.db")
    key = "catalog-recovery"
    domain = "recommend"
    referenced = ""
    for index in range(30):
        fingerprint = _seed_skill(memory, catalog_key=key, domain=domain, index=index)
        if index == 0:
            referenced = fingerprint

    invocation_id = "run-interrupted:1:recommend.evolve"
    with memory._lock:
        conn = memory._connect()
        try:
            conn.execute(
                """
                insert into agent_skill_events(
                  invocation_id,catalog_key,domain,fingerprint,result,created_at
                ) values(?,?,?,?,?,?)
                """,
                (invocation_id, key, domain, referenced, '{"marker":"recovery"}', 2000.0),
            )
            conn.commit()
        finally:
            memory._close(conn)

    removed = prune_retired_strategy_history(memory)

    assert removed == 5
    assert _status_count(memory, key, domain, "retired") == RETIRED_SKILL_BUDGET + 1
    replay = memory.invocation_result(invocation_id)
    assert replay is not None
    assert replay["fingerprint"] == referenced
    assert replay["result"]["marker"] == "recovery"


def test_retired_budget_is_independent_per_backend_namespace(tmp_path) -> None:
    base = AgentMemory(tmp_path / "memory.db")
    logical_key = "catalog-shared"
    first = BackendScopedMemory(base, recommend_scope="recommend-first")
    second = BackendScopedMemory(base, recommend_scope="recommend-second")
    first_key = first.scoped_catalog_key(logical_key, "recommend")
    second_key = second.scoped_catalog_key(logical_key, "recommend")

    for index in range(30):
        _seed_skill(base, catalog_key=first_key, domain="recommend", index=index)
        _seed_skill(base, catalog_key=second_key, domain="recommend", index=100 + index)

    assert prune_retired_strategy_history(first) == 12
    assert _status_count(base, first_key, "recommend", "retired") == RETIRED_SKILL_BUDGET
    assert _status_count(base, second_key, "recommend", "retired") == RETIRED_SKILL_BUDGET


def test_public_successful_run_applies_retired_history_maintenance(tmp_path) -> None:
    memory = AgentMemory(tmp_path / "memory.db")
    catalog = build_sample_catalog()
    key = catalog_fingerprint(catalog)
    for index in range(30):
        _seed_skill(memory, catalog_key=key, domain="recommend", index=index)

    result = AgentHarness(catalog, memory=memory, max_tools=1).run("检查推荐体验，不要修改策略")

    assert result["run_id"]
    assert _status_count(memory, key, "recommend", "retired") == RETIRED_SKILL_BUDGET
