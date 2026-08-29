from __future__ import annotations

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


__all__ = ["normalize_serving_limit"]
