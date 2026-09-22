from __future__ import annotations

from lingjing_harness.domain import Item

from .text import hashed_vector


class ItemVectorSnapshot(dict[str, dict[int, float]]):
    """Owned semantic-vector snapshot with a known dense coordinate space."""

    def __init__(
        self,
        values: dict[str, dict[int, float]],
        *,
        dense_dims: int,
    ) -> None:
        super().__init__(values)
        self.dense_dims = int(dense_dims)


def build_item_vectors(items: list[Item]) -> dict[str, dict[int, float]]:
    """Build one immutable-by-convention semantic vector snapshot for a catalog."""

    return ItemVectorSnapshot(
        {
            item.item_id: hashed_vector(
                " ".join([item.title, item.text, *item.categories])
            )
            for item in items
        },
        dense_dims=256,
    )


__all__ = ["ItemVectorSnapshot", "build_item_vectors"]
