from scripts.visual_qa_geometry import meets_min_touch_target


def test_touch_target_accepts_negligible_browser_geometry_noise():
    assert meets_min_touch_target(
        {
            "x": 253.0,
            "y": 4.5,
            "width": 44.0,
            "height": 43.99999952316284,
        }
    )


def test_touch_target_still_rejects_materially_undersized_dimensions():
    assert not meets_min_touch_target(
        {"x": 0.0, "y": 0.0, "width": 43.99, "height": 44.0}
    )
    assert not meets_min_touch_target(
        {"x": 0.0, "y": 0.0, "width": 44.0, "height": 43.99}
    )
    assert not meets_min_touch_target(None)
