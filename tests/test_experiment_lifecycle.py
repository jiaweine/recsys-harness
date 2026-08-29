from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from lingjing_harness.algorithms import SearchConfig
from lingjing_harness.counterfactual import CounterfactualRecord
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.experiment_lifecycle import strategy_policy_id
from lingjing_harness.experiments import ExperimentCriteria, ExperimentSpec
from lingjing_harness.production import ExposureEvent
from lingjing_harness.runtime import AgentMemory, ToolRegistry
from lingjing_harness.workspace_identity import workspace_fingerprint


def _criteria() -> ExperimentCriteria:
    return ExperimentCriteria(
        minimum_samples=8,
        minimum_effective_sample_ratio=0.5,
        maximum_clipped_share=0.0,
        minimum_support_coverage=1.0,
        minimum_probability_positive=0.5,
        minimum_estimated_delta=0.1,
    )


def _records(candidate_policy_id: str) -> list[CounterfactualRecord]:
    rows: list[CounterfactualRecord] = []
    for index in range(8):
        good = index < 4
        rows.append(
            CounterfactualRecord(
                decision_id=f"d-{index}",
                surface="search",
                action_id=f"action-{index}",
                reward=1.0 if good else 0.0,
                logging_propensity=0.5,
                target_propensity=1.0 if good else 0.1,
                logging_policy_id="prod-search",
                target_policy_id=candidate_policy_id,
            )
        )
    return rows


def _experiment(candidate_policy_id: str) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="exp-search-candidate",
        surface="search",
        hypothesis="candidate improves controlled search reward",
        logging_policy_id="prod-search",
        candidate_policy_id=candidate_policy_id,
        primary_estimator="snips",
        criteria=_criteria(),
    )


def _catalog(*, candidate_policy_id: str | None = None, records: bool = True) -> Catalog:
    experiments = []
    counterfactual_records = []
    if candidate_policy_id:
        experiments = [_experiment(candidate_policy_id)]
        counterfactual_records = _records(candidate_policy_id) if records else []
    return Catalog(
        items=[Item("i1", "Alpha"), Item("i2", "Beta")],
        experiments=experiments,
        counterfactual_records=counterfactual_records,
    )


def _trusted_result(config: SearchConfig, *, business_trusted: bool = False) -> dict:
    return {
        "reference": {"quality": 0.7},
        "candidate": {"quality": 0.8, "queries": 8},
        "delta": {"quality": 0.1},
        "evaluation_ready": True,
        "evaluation_basis": "business_reward+relevance_guardrails" if business_trusted else "proxy_metrics",
        "safe_to_try": True,
        "trusted": True,
        "business_trusted": business_trusted,
        "candidate_config": asdict(config),
        "candidate_count": 1,
        "generations": 1,
        "robustness": {},
        "objective_delta": 0.1,
        "business_validation": {"available": business_trusted},
        "relevance_validation": {},
        "validation": {"holdout": {"independent": False, "samples": 0}},
        "evolution": {"selected_signature": []},
    }


def test_catalog_round_trips_explicit_counterfactual_and_experiment_contracts():
    candidate = strategy_policy_id("search", asdict(SearchConfig()))
    catalog = _catalog(candidate_policy_id=candidate)

    payload = catalog.to_payload()
    restored = Catalog.from_payload(payload)

    assert len(restored.counterfactual_records) == 8
    assert len(restored.experiments) == 1
    assert restored.experiments[0].candidate_policy_id == candidate
    assert restored.summary()["counterfactual_records"] == 8
    assert restored.summary()["experiment_specs"] == 1


def test_production_exposures_never_synthesize_counterfactual_target_probabilities():
    catalog = Catalog(
        items=[Item("i1", "Alpha")],
        events=[
            ExposureEvent(
                request_id="r1",
                timestamp=1.0,
                surface="search",
                item_id="i1",
                propensity=0.5,
                policy_id="prod-search",
            )
        ],
    )

    payload = catalog.to_payload()
    restored = Catalog.from_payload(payload)

    assert restored.counterfactual_records == []
    assert restored.experiments == []
    assert "counterfactual_records" not in payload
    assert "experiments" not in payload


def test_catalog_rejects_ambiguous_experiment_contracts_for_one_candidate():
    candidate = strategy_policy_id("search", asdict(SearchConfig()))
    first = _experiment(candidate)
    second = ExperimentSpec(
        experiment_id="exp-second",
        surface="search",
        hypothesis="second contract",
        logging_policy_id="other-control",
        candidate_policy_id=candidate,
        primary_estimator="ips",
        criteria=_criteria(),
    )
    with pytest.raises(ValueError, match="only one experiment contract"):
        Catalog(items=[Item("i1", "Alpha")], experiments=[first, second])


def test_policy_identity_is_deterministic_and_backend_aware():
    config = asdict(replace(SearchConfig(), diversity=0.19))

    reference = strategy_policy_id("search", config)
    same = strategy_policy_id("search", dict(reversed(list(config.items()))))
    semantic = strategy_policy_id("search", config, backend_scope="search-semantic")
    changed = strategy_policy_id(
        "search",
        asdict(replace(SearchConfig(), diversity=0.21)),
    )

    assert reference == same
    assert reference != semantic
    assert reference != changed


def test_workspace_revision_changes_when_ope_evidence_or_contract_changes():
    candidate = strategy_policy_id("search", asdict(SearchConfig()))
    base = _catalog()
    with_contract = _catalog(candidate_policy_id=candidate, records=False)
    with_evidence = _catalog(candidate_policy_id=candidate, records=True)

    assert workspace_fingerprint(base) != workspace_fingerprint(with_contract)
    assert workspace_fingerprint(with_contract) != workspace_fingerprint(with_evidence)


def test_ope_can_qualify_online_test_but_cannot_activate_without_business_validation(monkeypatch):
    candidate_config = replace(SearchConfig(), diversity=0.19)
    candidate_policy_id = strategy_policy_id("search", asdict(candidate_config))
    catalog = _catalog(candidate_policy_id=candidate_policy_id)
    registry = ToolRegistry(catalog, AgentMemory())

    import lingjing_harness.runtime.experiment_tools as experiment_tools

    monkeypatch.setattr(
        experiment_tools,
        "evolve_search",
        lambda *args, **kwargs: _trusted_result(candidate_config),
    )

    result = registry.search_evolve(activate=True)

    assert result["trusted"] is True
    assert result["online_test_eligible"] is True
    assert result["activation_requested"] is True
    assert result["activation_eligible"] is False
    assert result["activated"] is False
    assert result["skill"]["status"] == "trusted"
    assert result["activation_blocked_reason"] == "explicit_experiment_requires_business_validation"
    assert result["experiment_validation"]["evaluation"]["decision"]["automatic_activation"] is False
    assert result["portfolio_activated"] is False


def test_missing_ope_rows_fail_closed_for_online_test_and_activation(monkeypatch):
    candidate_config = replace(SearchConfig(), diversity=0.19)
    candidate_policy_id = strategy_policy_id("search", asdict(candidate_config))
    catalog = _catalog(candidate_policy_id=candidate_policy_id, records=False)
    registry = ToolRegistry(catalog, AgentMemory())

    import lingjing_harness.runtime.experiment_tools as experiment_tools

    monkeypatch.setattr(
        experiment_tools,
        "evolve_search",
        lambda *args, **kwargs: _trusted_result(candidate_config),
    )
    result = registry.search_evolve(activate=True)

    assert result["online_test_eligible"] is False
    assert result["activation_eligible"] is False
    assert result["activated"] is False
    assert result["skill"]["status"] == "trusted"


def test_workspace_without_matching_experiment_preserves_existing_activation_behavior(monkeypatch):
    candidate_config = replace(SearchConfig(), diversity=0.19)
    registry = ToolRegistry(_catalog(), AgentMemory())

    import lingjing_harness.runtime.experiment_tools as experiment_tools

    monkeypatch.setattr(
        experiment_tools,
        "evolve_search",
        lambda *args, **kwargs: _trusted_result(candidate_config),
    )
    result = registry.search_evolve(activate=True)

    assert result["experiment_validation"]["contract_present"] is False
    assert result["activation_eligible"] is True
    assert result["activated"] is True
    assert result["skill"]["status"] == "active"


def test_business_trusted_candidate_can_activate_even_with_explicit_experiment(monkeypatch):
    candidate_config = replace(SearchConfig(), diversity=0.19)
    candidate_policy_id = strategy_policy_id("search", asdict(candidate_config))
    catalog = _catalog(candidate_policy_id=candidate_policy_id, records=False)
    registry = ToolRegistry(catalog, AgentMemory())

    import lingjing_harness.runtime.experiment_tools as experiment_tools

    monkeypatch.setattr(
        experiment_tools,
        "evolve_search",
        lambda *args, **kwargs: _trusted_result(candidate_config, business_trusted=True),
    )
    result = registry.search_evolve(activate=True)

    assert result["business_trusted"] is True
    assert result["online_test_eligible"] is False
    assert result["activation_eligible"] is True
    assert result["activated"] is True
    assert result["skill"]["status"] == "active"
