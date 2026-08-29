from __future__ import annotations

from dataclasses import asdict

import pytest

from lingjing_harness.algorithms import SearchConfig
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.mechanism_graph import (
    mechanism_graph_snapshot,
    mechanism_stats,
    record_mechanism_evidence,
    validate_mechanism_graph,
    validate_mechanism_graph_with_shacl,
)
from lingjing_harness.runtime.mechanism_transfer import mechanism_pair_priors


def _search_configs() -> tuple[dict, dict]:
    global_config = asdict(SearchConfig())
    segment_config = dict(global_config)
    # Preserve the search blend mass while creating a legal local mechanism.
    shift = min(0.02, float(segment_config["semantic"]) / 2.0)
    segment_config["lexical"] = float(segment_config["lexical"]) + shift
    segment_config["semantic"] = float(segment_config["semantic"]) - shift
    return global_config, segment_config


def _synthetic_result() -> dict:
    global_config, segment_config = _search_configs()
    return {
        "run_id": "run-mechanism-test",
        "data": {"items": 120, "interactions": 480, "query_labels": 20},
        "actions": [
            {
                "tool": "search.evolve",
                "status": "completed",
                "invocation_id": "run-mechanism-test:1:search.evolve",
                "result": {
                    "evaluation_ready": True,
                    "evaluation_basis": "business_reward+relevance_guardrails",
                    "trusted": False,
                    "safe_to_try": False,
                    "objective_delta": -0.02,
                    "candidate_config": global_config,
                    "trust_blocked_by": ["domain_guardrail_holdout_regressed"],
                    "business_validation": {
                        "available": True,
                        "holdout_reward_delta": -0.03,
                        "full_reward_delta": -0.01,
                        "confidence": {
                            "samples": 6,
                            "probability_positive": 0.18,
                        },
                    },
                    "validation": {
                        "holdout": {
                            "samples": 8,
                            "independent": True,
                        }
                    },
                    "evolution": {
                        "method": "optuna_motpe_with_evidence_response_surface",
                        "optimizer_backend": "optuna_motpe",
                        "selected_signature": [
                            "query_strategy:literal->expanded",
                            "semantic:+",
                        ],
                    },
                    "segment_portfolio": {
                        "available": True,
                        "entries": [
                            {
                                "segment": "head",
                                "trusted": True,
                                "safe_to_try": True,
                                "candidate_config": segment_config,
                                "discovery_requests": 7,
                                "holdout_requests": 4,
                                "holdout_reward_delta": 0.025,
                                "full_reward_delta": 0.02,
                                "confidence": {
                                    "samples": 4,
                                    "probability_positive": 0.82,
                                },
                                "guardrail": {
                                    "available": True,
                                    "quality_delta": 0.01,
                                },
                                "trust_blocked_by": [],
                            }
                        ],
                    },
                },
            }
        ],
    }


def test_mechanism_memory_records_global_and_segment_evidence_idempotently() -> None:
    memory = AgentMemory()
    result = _synthetic_result()

    first = record_mechanism_evidence(memory, "catalog-a", result)
    assert first["recorded"] >= 3
    assert first["mechanisms"] >= 3
    assert first["contexts"] == 2

    second = record_mechanism_evidence(memory, "catalog-a", result)
    assert second["recorded"] == 0
    assert second["deduplicated"] == first["recorded"]

    stats = mechanism_stats(memory, "catalog-a", domain="search")
    assert stats
    assert sum(int(row["rejected"]) for row in stats) >= 2
    assert sum(int(row["accepted"]) for row in stats) >= 1


def test_mechanism_graph_exports_closed_world_context_mechanism_evidence_outcome() -> None:
    memory = AgentMemory()
    record_mechanism_evidence(memory, "catalog-a", _synthetic_result())

    snapshot = mechanism_graph_snapshot(memory, "catalog-a", domain="search")
    assert snapshot["valid"] is True
    assert snapshot["violations"] == []
    assert snapshot["events"] >= 3
    assert validate_mechanism_graph(snapshot) == []

    types = {
        row.get("@type")
        for row in snapshot["jsonld"]["@graph"]
        if isinstance(row, dict)
    }
    assert {
        "xushu:Context",
        "xushu:Mechanism",
        "xushu:Experiment",
        "xushu:Evidence",
        "xushu:Outcome",
        "xushu:FailureMode",
    }.issubset(types)


def test_rejected_mechanism_retains_named_failure_modes() -> None:
    memory = AgentMemory()
    record_mechanism_evidence(memory, "catalog-a", _synthetic_result())
    snapshot = mechanism_graph_snapshot(memory, "catalog-a")

    failures = [
        row.get("xushu:failureKey")
        for row in snapshot["jsonld"]["@graph"]
        if row.get("@type") == "xushu:FailureMode"
    ]
    assert "domain_guardrail_holdout_regressed" in failures


def test_mechanism_pair_memory_is_second_order_and_visible_to_evolution() -> None:
    memory = AgentMemory()
    record_mechanism_evidence(memory, "catalog-a", _synthetic_result())

    pairs = mechanism_pair_priors(memory, "catalog-a", "search")
    assert pairs
    assert all(row["status"] == "mechanism_pair" for row in pairs)
    assert all(len(row["pair"]["arms"]) == 2 for row in pairs)
    assert any(int(row["pair"]["negative"]) == 1 for row in pairs)

    remembered = memory.evolution_memory("catalog-a", "search", limit=5)
    assert any(row.get("status") == "mechanism_pair" for row in remembered)
    # Pair memory must not masquerade as first-order strategy-arm credit.
    pair_rows = [row for row in remembered if row.get("status") == "mechanism_pair"]
    assert all("credit" not in row for row in pair_rows)


def test_mechanism_graph_passes_packaged_shacl_when_ontology_extra_is_installed() -> None:
    pytest.importorskip("rdflib")
    pytest.importorskip("pyshacl")

    memory = AgentMemory()
    record_mechanism_evidence(memory, "catalog-a", _synthetic_result())
    snapshot = mechanism_graph_snapshot(memory, "catalog-a")
    result = validate_mechanism_graph_with_shacl(snapshot)

    assert result["conforms"] is True, result["results_text"]
