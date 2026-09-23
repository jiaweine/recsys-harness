from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator, MutableMapping

from lingjing_harness.domain import Catalog
from .recommend import RecommendationEngine


class SeedGraphSnapshot:
    """Independent temporal graph rows required by one relevance user's seeds."""

    __slots__ = ("rows", "absent")

    def __init__(
        self,
        rows: dict[str, Counter[str]],
        absent: set[str],
    ) -> None:
        self.rows = rows
        self.absent = absent


class TemporalGraphSnapshot(MutableMapping[str, Counter[str]]):
    """Seed-fast graph view with exact lazy fallback to the full temporal graph.

    Built-in recommendation graph scoring only asks ``get(seed)`` for the active
    user's history items, so those independent rows can be snapshotted cheaply.
    Any broader/private graph access materializes the exact full graph from the
    temporal catalog before continuing. This keeps custom capability behavior and
    per-slice mutation isolation equivalent to a fully copied defaultdict.
    """

    default_factory = Counter

    def __init__(
        self,
        catalog: Catalog,
        source_engine: RecommendationEngine,
        snapshot: SeedGraphSnapshot,
    ) -> None:
        self._catalog = catalog
        self._source_engine = source_engine
        self._seed_rows = snapshot.rows
        self._known_absent = snapshot.absent
        self._full: defaultdict[str, Counter[str]] | None = None

    @property
    def materialized(self) -> bool:
        return self._full is not None

    def _materialize(self) -> defaultdict[str, Counter[str]]:
        if self._full is not None:
            return self._full

        rebuilt = RecommendationEngine(
            self._catalog,
            config=self._source_engine.config,
            item_vectors=self._source_engine._vectors,
            popularity_norms=self._source_engine._popularity,
        )
        full = rebuilt._co

        # Preserve any mutations made through an already-exposed seed Counter
        # before a later custom/private operation forced full graph materialization.
        for item_id, counts in self._seed_rows.items():
            full[item_id] = counts
        for item_id in self._known_absent:
            full.pop(item_id, None)

        self._full = full
        self._seed_rows = {}
        self._known_absent = set()
        return full

    def get(self, key: str, default=None):
        if self._full is not None:
            return self._full.get(key, default)
        if key in self._seed_rows:
            return self._seed_rows[key]
        if key in self._known_absent:
            return default
        return self._materialize().get(key, default)

    def __getitem__(self, key: str) -> Counter[str]:
        if self._full is not None:
            return self._full[key]
        if key in self._seed_rows:
            return self._seed_rows[key]
        if key in self._known_absent:
            counts: Counter[str] = Counter()
            self._seed_rows[key] = counts
            self._known_absent.remove(key)
            return counts
        return self._materialize()[key]

    def __setitem__(self, key: str, value: Counter[str]) -> None:
        self._materialize()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._materialize()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._materialize())

    def __len__(self) -> int:
        return len(self._materialize())


__all__ = ["SeedGraphSnapshot", "TemporalGraphSnapshot"]
