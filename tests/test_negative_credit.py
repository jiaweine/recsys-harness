from __future__ import annotations

from dataclasses import asdict, replace

from lingjing_harness.algorithms import SearchConfig
from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.credit_routing import filter_segment_candidates
from lingjing_harness.algorithms.evolution import _evolution_schema, _history_posteriors
from lingjing_harness.runtime.memory import AgentMemory, catalog_fingerprint
from lingjing_harness.sample_data import build_sample_catalog


def test_credit_events_are_idempotent_and_count_failures_once(tmp_path):
    memory = AgentMemory(tmp_path / "credit.db")
    key = catalog_fingerprint(build_sample_catalog())
    first = memory.record_strategy_credit(
        key,
        "search",
        "query_strategy=literal",
        outcome="rejected",
        reward_delta=-0.2,
        evidence=3,
        reason="independent_holdout_regressed",
        event_key="same-independent-evidence",
    )
    second = memory.record_strategy_credit(
        key,
        "search",
        "query_strategy=literal",
        outcome="rejected",
        reward_delta=-0.2,
        evidence=3,
        reason="independent_holdout_regressed",
        event_key="same-independent-evidence",
    )
    rows = memory.strategy_credits(key, "search")
    assert first["recorded"] is True
    assert second["deduplicated"] is True
    assert len(rows) == 1
    assert rows[0]["credit"]["negative"] == 1
    assert rows[0]["credit"]["trials"] == 1
    assert memory.stats(key)["credit_events"] == 1


def test_negative_credit_changes_future_beta_prior_and_positive_credit_can_recover(tmp_path):
    memory = AgentMemory(tmp_path / "credit.db")
    key = catalog_fingerprint(build_sample_catalog())
    for index in range(2):
        memory.record_strategy_credit(
            key,
            "search",
            "query_strategy=literal",
            outcome="rejected",
            event_key=f"negative-{index}",
        )

    base = SearchConfig()
    dimensions, group_totals = _evolution_schema(base)
    before = _history_posteriors(asdict(base), [], dimensions, group_totals)["query_strategy=literal"]
    after = _history_posteriors(
        asdict(base),
        memory.evolution_memory(key, "search"),
        dimensions,
        group_totals,
    )["query_strategy=literal"]
    assert before == (1.0, 1.0)
    assert after == (1.0, 3.0)

    memory.record_strategy_credit(
        key,
        "search",
        "query_strategy=literal",
        outcome="accepted",
        event_key="later-success",
    )
    recovered = _history_posteriors(
        asdict(base),
        memory.evolution_memory(key, "search"),
        dimensions,
        group_totals,
    )["query_strategy=literal"]
    assert recovered == (2.0, 3.0)
    assert recovered[0] / sum(recovered) > after[0] / sum(after)


def test_segment_credit_prunes_only_repeatedly_failed_pure_mutations(tmp_path):
    memory = AgentMemory(tmp_path / "credit.db")
    key = catalog_fingerprint(build_sample_catalog())
    base = SearchConfig()
    base_config = asdict(base)
    dimensions, group_totals = core._evolution_schema(base)
    query_dimension = next(row for row in dimensions if row.name == "query_strategy")
    arm, _, failed_candidate = next(
        row
        for row in core._neighbors(base_config, query_dimension, dimensions, group_totals)
        if row[0] == "query_strategy=literal"
    )
    domain = "search.segment.weak-anchor"
    for index in range(2):
        memory.record_strategy_credit(
            key,
            domain,
            arm,
            outcome="rejected",
            event_key=f"segment-negative-{index}",
        )

    kept, metadata = filter_segment_candidates(
        base_config=base_config,
        candidates=[base_config, failed_candidate],
        dimensions=dimensions,
        remembered=memory.evolution_memory(key, "search"),
        domain=domain,
    )
    assert base_config in kept
    assert failed_candidate not in kept
    assert metadata["blocked_arms"] == ["query_strategy=literal"]
    assert metadata["pruned_candidates"] == 1


def test_inconclusive_validation_does_not_create_negative_credit(tmp_path):
    memory = AgentMemory(tmp_path / "credit.db")
    key = catalog_fingerprint(build_sample_catalog())
    candidate = asdict(replace(SearchConfig(), query_strategy="literal"))
    result = {
        "trusted": False,
        "safe_to_try": True,
        "candidate_config": candidate,
        "evolution": {"selected_signature": ["query_strategy=literal"]},
        "business_validation": {
            "available": True,
            "holdout_reward_delta": 0.02,
            "confidence": {"samples": 2, "probability_positive": 0.55},
        },
        "validation": {"holdout": {"independent": True, "samples": 2}},
    }
    summary = memory.record_evolution_result(
        key,
        "search",
        current_config=asdict(SearchConfig()),
        result=result,
    )
    assert summary["recorded_events"] == 0
    assert memory.strategy_credits(key, "search") == []


def test_rejected_global_and_segment_holdouts_write_separate_credit(tmp_path):
    memory = AgentMemory(tmp_path / "credit.db")
    key = catalog_fingerprint(build_sample_catalog())
    candidate = asdict(replace(SearchConfig(), query_strategy="literal"))
    result = {
        "trusted": False,
        "safe_to_try": True,
        "candidate_config": candidate,
        "evolution": {"selected_signature": ["query_strategy=literal"]},
        "business_validation": {
            "available": True,
            "holdout_reward_delta": -0.08,
            "confidence": {"samples": 3, "probability_positive": 0.1},
        },
        "validation": {"holdout": {"independent": True, "samples": 3}},
        "segment_portfolio": {
            "available": True,
            "entries": [
                {
                    "segment": "search/weak-anchor",
                    "candidate_config": candidate,
                    "trusted": False,
                    "safe_to_try": True,
                    "discovery_requests": 4,
                    "holdout_requests": 2,
                    "holdout_reward_delta": -0.12,
                    "guardrail": {"available": True},
                    "confidence": {"samples": 2, "probability_positive": 0.0},
                }
            ],
        },
    }
    first = memory.record_evolution_result(
        key,
        "search",
        current_config=asdict(SearchConfig()),
        result=result,
    )
    second = memory.record_evolution_result(
        key,
        "search",
        current_config=asdict(SearchConfig()),
        result=result,
    )
    global_credit = memory.strategy_credits(key, "search")[0]["credit"]
    segment_credit = memory.strategy_credits(key, "search.segment.weak-anchor")[0]["credit"]
    assert first["recorded_events"] == 2
    assert second["recorded_events"] == 0
    assert second["deduplicated_events"] == 2
    assert global_credit["negative"] == 1
    assert segment_credit["negative"] == 1
    assert global_credit["last_reason"] == "independent_business_holdout_regressed"
    assert segment_credit["last_reason"] == "independent_segment_holdout_regressed"


def test_active_rollback_becomes_negative_arm_credit(tmp_path):
    memory = AgentMemory(tmp_path / "credit.db")
    key = catalog_fingerprint(build_sample_catalog())
    config = asdict(replace(SearchConfig(), query_strategy="literal"))
    memory.remember_strategy(
        key,
        "search",
        config,
        score=0.8,
        evidence=5,
        status="active",
        payload={"selected_signature": ["query_strategy=literal"]},
    )
    retired = memory.retire_active(key, "search", reason="production reward regressed")
    rows = memory.strategy_credits(key, "search")
    assert retired is not None
    assert memory.active_config(key, "search") is None
    assert rows[0]["credit"]["arm"] == "query_strategy=literal"
    assert rows[0]["credit"]["negative"] == 1
    assert rows[0]["credit"]["last_outcome"] == "rollback"
