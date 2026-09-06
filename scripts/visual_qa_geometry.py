from __future__ import annotations

import math


MIN_TOUCH_TARGET_PX = 44.0
LAYOUT_EPSILON_PX = 1e-4


def meets_min_touch_target(box: dict[str, float] | None) -> bool:
    """Require a 44px box while tolerating negligible browser float noise."""

    if not box:
        return False
    return all(
        float(box.get(dimension, 0.0)) >= MIN_TOUCH_TARGET_PX
        or math.isclose(
            float(box.get(dimension, 0.0)),
            MIN_TOUCH_TARGET_PX,
            rel_tol=0.0,
            abs_tol=LAYOUT_EPSILON_PX,
        )
        for dimension in ("width", "height")
    )
