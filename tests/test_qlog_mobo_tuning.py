from __future__ import annotations

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms import qlog_mobo


def _space(choices=("a", "b", "c")):
    return qlog_mobo.MixedSearchSpace.build(
        base_config={"x": 0.5, "y": 0.5, "capability": choices[0]},
        dimensions=[
            core.EvolutionDimension(
                name="x",
                kind="continuous",
                group="independent",
                low=0.0,
                high=1.0,
            ),
            core.EvolutionDimension(
                name="y",
                kind="continuous",
                group="independent",
                low=0.0,
                high=1.0,
            ),
            core.EvolutionDimension(
                name="capability",
                kind="capability",
                group="candidate",
                choices=choices,
            ),
        ],
        group_totals={},
    )


def test_small_mixed_space_uses_bounded_acquisition_search_effort():
    raw_samples, restarts, maxiter = qlog_mobo._acquisition_search_budget(
        _space(),
        training_points=14,
    )

    assert raw_samples == 64
    assert restarts == 4
    assert maxiter == 60
    assert raw_samples < qlog_mobo.ACQUISITION_RAW_SAMPLES
    assert restarts < qlog_mobo.ACQUISITION_RESTARTS


def test_acquisition_search_budget_respects_global_caps(monkeypatch):
    monkeypatch.setattr(qlog_mobo, "ACQUISITION_RAW_SAMPLES", 32)
    monkeypatch.setattr(qlog_mobo, "ACQUISITION_RESTARTS", 3)

    raw_samples, restarts, _ = qlog_mobo._acquisition_search_budget(
        _space(),
        training_points=8,
    )

    assert raw_samples == 32
    assert restarts == 3


def test_ten_call_acquisition_budget_reserves_last_three_for_primary_exploitation():
    modes = [
        qlog_mobo._acquisition_mode(
            acquisition_evaluations=index,
            acquisition_budget=10,
        )
        for index in range(10)
    ]

    assert modes == [
        "pareto",
        "pareto",
        "pareto",
        "pareto",
        "pareto",
        "pareto",
        "pareto",
        "primary",
        "primary",
        "primary",
    ]


def test_single_remaining_acquisition_is_primary_exploitation():
    assert qlog_mobo._acquisition_mode(
        acquisition_evaluations=0,
        acquisition_budget=1,
    ) == "primary"
