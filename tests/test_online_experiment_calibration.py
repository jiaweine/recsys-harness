from __future__ import annotations

from scripts.online_experiment_sequential_calibration import run_calibration


def test_small_deterministic_sequential_calibration_stays_bounded():
    report = run_calibration(
        trials=40,
        seed=7001,
        max_per_arm=120,
        srm_units=180,
        look_every=10,
    )

    assert report["calibration_role"] == "implementation_smoke_not_theorem_replacement"
    assert 0.0 <= report["primary_false_ramp_rate"] <= 0.15
    assert 0.0 <= report["srm_false_alarm_rate"] <= 0.10
    assert report["continuous_looks"] == 12
