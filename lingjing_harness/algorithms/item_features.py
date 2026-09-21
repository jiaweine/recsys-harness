from __future__ import annotations

from lingjing_harness.domain import Item

from .text import hashed_vector


def build_item_vectors(items: list[Item]) -> dict[str, dict[int, float]]:
    """Build one immutable-by-convention semantic vector snapshot for a catalog."""

    return {
        item.item_id: hashed_vector(" ".join([item.title, item.text, *item.categories]))
        for item in items
    }


__all__ = ["build_item_vectors"]
