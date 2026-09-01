from scripts.optimizer_drift_calibration import run_calibration


def _case(report, name):
    return next(row for row in report["cases"] if row["name"] == name)


def test_drift_calibration_preserves_structural_invariants_without_evaluator_calls():
    report = run_calibration(seeds=128)

    assert report["new_evaluator_calls"] == 0
    assert report["case_count"] == 9
    assert report["max_false_positive_rate"] == 0.0

    for name in (
        "stationary_low_noise",
        "stationary_moderate_noise",
        "stationary_high_noise",
        "pure_level_shift",
        "level_shift_low_noise",
        "exploration_region_shift",
    ):
        case = _case(report, name)
        assert case["expected_change"] is False
        assert case["detection_rate"] == 0.0
        assert case["false_positive_rate"] == 0.0
        assert case["new_evaluator_calls"] == 0

    for name in ("order_inversion", "contrast_shift", "sequential_latest_break"):
        case = _case(report, name)
        assert case["detection_rate"] == 1.0
        assert case["cutoff_accuracy_given_detection"] == 1.0
        assert case["new_evaluator_calls"] == 0
