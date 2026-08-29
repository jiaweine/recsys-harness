from __future__ import annotations

from math import isfinite
from operator import index
from typing import Any


def normalize_serving_limit(limit: Any) -> int:
    """Return one strict, non-negative serving limit for every ranking backend.

    Ranking APIs accept integer-like values that implement ``__index__`` (for
    example Python and NumPy integer scalars). Floats, strings and booleans are
    rejected rather than silently truncated/coerced. Non-positive integers mean
    an empty slate and allow callers to short-circuit before retrieval/model IO.
    """

    if isinstance(limit, bool):
        raise ValueError("limit must be an integer")
    try:
        value = index(limit)
    except TypeError as exc:
        raise ValueError("limit must be an integer") from exc
    return max(0, value)


def normalize_serving_score(score: Any) -> float:
    """Return a finite numeric ranking score or reject the row as malformed."""

    if isinstance(score, bool):
        raise ValueError("score must be a finite number")
    try:
        value = float(score)
    except (TypeError, ValueError) as exc:
        raise ValueError("score must be a finite number") from exc
    if not isfinite(value):
        raise ValueError("score must be a finite number")
    return value


__all__ = ["normalize_serving_limit", "normalize_serving_score"]
