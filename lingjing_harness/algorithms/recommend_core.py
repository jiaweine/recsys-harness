from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import blake2b
from math import exp, sqrt

from lingjing_harness.domain import Catalog, Item
from .capabilities import CAPABILITIES, capability_field, normalize_strategy_config
from .text import cosine, hashed_vector


_BLEND = {"evolve_group": "blend", "min": 0.005, "max": 0.75, "relative_step": 0.16}
_INDEPENDENT = {"evolve_group": "independent", "min": 0.0, "max": 0.32, "relative_step": 0.18}
_COLD_START = {"evolve_group": "independent", "min": 0.0, "max": 0.28, "relative_step": 0.20}


@dataclass(frozen=True)
class RecommendConfig:
    """Recommendation strategy genome with continuous and structural genes.

    Warm ranking weights share one normalized blend. Cold-start pressure is an
    independent gene because it is inactive for warm users; mutating it must not
    silently rescale profile/graph/freshness weights.
    """

    profile: float = field(default=0.34, metadata=_BLEND)
    graph: float = field(default=0.20, metadata=_BLEND)
    category: float = field(default=0.10, metadata=_BLEND)
    quality: float = field(default=0.12, metadata=_BLEND)
    freshness: float = field(default=0.13, metadata=_BLEND)
    popularity: float = field(default=0.05, metadata=_BLEND)
    novelty: float = field(default=0.06, metadata=_BLEND)
    exploration: float = field(default=0.04, metadata=_BLEND)
    cold_start: float = field(default=0.06, metadata=_COLD_START)
    diversity: float = field(default=0.14, metadata=_INDEPENDENT)

    profile_strategy: str = capability_field("recommend.profile", "recency_balanced")
    candidate_strategy: str = capability_field("recommend.candidate", "full_pool")
    cold_start_strategy: str = capability_field("recommend.cold_start", "quality_freshness")
    exploration_strategy: str = capability_field("recommend.exploration", "stable_fresh")
    rerank_strategy: str = capability_field("recommend.rerank", "category_mmr")


class RecommendationEngine:
    """Owned implicit-feedback recommender with evolvable vertical stages."""

    MAX_GRAPH_HISTORY = 120

    def __init__(self, catalog: Catalog, config: RecommendConfig | None = None) -> None:
        self.catalog = catalog
        self.config = normalize_strategy_config(config or RecommendConfig())
        self._vectors = {
            item.item_id: hashed_vector(" ".join([item.title, item.text, *item.categories]))
            for item in catalog.items
        }
        self._popularity = {item.item_id: catalog.popularity_norm(item) for item in catalog.items}
        self._by_user: dict[str, list] = defaultdict(list)
        for event in catalog.interactions:
            self._by_user[event.user_id].append(event)
        self._co: dict[str, Counter[str]] = defaultdict(Counter)
        for events in self._by_user.values():
            ids = list(dict.fromkeys(event.item_id for event in reversed(events)))[: self.MAX_GRAPH_HISTORY]
            for i, left in enumerate(ids):
                for right in ids[i + 1 :]:
                    self._co[left][right] += 1
                    self._co[right][left] += 1

    def with_config(self, config: RecommendConfig) -> "RecommendationEngine":
        clone = object.__new__(RecommendationEngine)
        clone.catalog = self.catalog
        clone.config = normalize_strategy_config(config)
        clone._vectors = self._vectors
        clone._popularity = self._popularity
        clone._by_user = self._by_user
        clone._co = self._co
        return clone

    def capability_manifest(self) -> dict[str, list[dict]]:
        return {
            group: rows
            for group, rows in CAPABILITIES.manifest().items()
            if group.startswith("recommend.")
        }

    def known_users(self) -> list[str]:
        return sorted(self._by_user)

    def _profile(self, user_id: str) -> tuple[dict[int, float], Counter[str], set[str], Counter[str]]:
        return CAPABILITIES.call(
            "recommend.profile",
            self.config.profile_strategy,
            self,
            user_id,
        )

    def _profile_with_decay(
        self,
        user_id: str,
        *,
        horizon: float | None,
    ) -> tuple[dict[int, float], Counter[str], set[str], Counter[str]]:
        events = self._by_user.get(user_id, [])
        seen = {event.item_id for event in events}
        cats: Counter[str] = Counter()
        seeds: Counter[str] = Counter()
        vec: dict[int, float] = {}
        max_ts = max((event.timestamp for event in events), default=0.0)
        for event in events:
            item = self.catalog.item_by_id[event.item_id]
            age = max(0.0, max_ts - event.timestamp)
            if horizon is None or max_ts <= 0:
                recency = 1.0
            else:
                recency = exp(-age / max(1.0, horizon))
            weight = event.weight * (0.55 + 0.45 * recency)
            seeds[event.item_id] += weight
            for key, value in self._vectors[event.item_id].items():
                vec[key] = vec.get(key, 0.0) + value * weight
            for category in item.categories:
                cats[category] += weight
        norm = sqrt(sum(value * value for value in vec.values())) or 1.0
        return {key: value / norm for key, value in vec.items()}, cats, seen, seeds

    def _graph_scores(self, seeds: Counter[str]) -> dict[str, float]:
        denom = sum(seeds.values()) or 1.0
        raw: dict[str, float] = defaultdict(float)
        for seed, weight in seeds.items():
            for item_id, count in self._co.get(seed, {}).items():
                raw[item_id] += weight * count
        return {item_id: min(1.0, value / denom) for item_id, value in raw.items()}

    @staticmethod
    def _stable_hash(user_id: str, item_id: str) -> float:
        value = int.from_bytes(
            blake2b(f"{user_id}:{item_id}".encode(), digest_size=4).digest(),
            "little",
        )
        return (value % 1000) / 1000.0

    def prepare(self, user_id: str) -> list[dict]:
        profile, cats, seen, seeds = self._profile(user_id)
        cat_total = sum(cats.values()) or 1.0
        graph_scores = self._graph_scores(seeds)
        candidate_ids = CAPABILITIES.call(
            "recommend.candidate",
            self.config.candidate_strategy,
            self,
            user_id,
            profile,
            cats,
            seen,
            seeds,
            graph_scores,
        )
        cold = len(self._by_user.get(user_id, [])) == 0
        rows = []
        # A capability is allowed to return any iterable; normalize it here so
        # duplicate IDs cannot create duplicate candidates in the final slate.
        for item_id in dict.fromkeys(str(value) for value in candidate_ids):
            item = self.catalog.item_by_id.get(item_id)
            if item is None or not item.eligible or item.item_id in seen:
                continue
            profile_fit = max(0.0, cosine(profile, self._vectors[item.item_id])) if profile else 0.0
            cat_fit = sum(cats.get(category, 0.0) for category in item.categories) / cat_total
            graph = graph_scores.get(item.item_id, 0.0)
            popularity = self._popularity[item.item_id]
            novelty = 1.0 - popularity
            explore = CAPABILITIES.call(
                "recommend.exploration",
                self.config.exploration_strategy,
                self,
                user_id,
                item,
                popularity,
            )
            cold_prior = (
                CAPABILITIES.call(
                    "recommend.cold_start",
                    self.config.cold_start_strategy,
                    self,
                    item,
                    popularity,
                    explore,
                )
                if cold
                else 0.0
            )
            rows.append(
                {
                    "item": item,
                    "profile_fit": profile_fit,
                    "cat_fit": cat_fit,
                    "graph": graph,
                    "pop": popularity,
                    "novelty": novelty,
                    "explore": explore,
                    "cold_prior": cold_prior,
                }
            )
        return rows

    def rank_prepared(
        self,
        prepared: list[dict],
        *,
        config: RecommendConfig | None = None,
        limit: int = 10,
    ) -> list[dict]:
        cfg = normalize_strategy_config(config or self.config)
        rows = []
        for raw in prepared:
            item = raw["item"]
            base = (
                cfg.profile * raw["profile_fit"]
                + cfg.graph * raw["graph"]
                + cfg.category * raw["cat_fit"]
                + cfg.quality * item.quality
                + cfg.freshness * item.freshness
                + cfg.popularity * raw["pop"]
                + cfg.novelty * raw["novelty"]
                + cfg.exploration * raw["explore"]
                + cfg.cold_start * raw.get("cold_prior", 0.0)
            )
            rows.append(
                {
                    "item": item,
                    "base": base,
                    "signals": {
                        "fit": round(
                            min(
                                1.0,
                                0.55 * raw["profile_fit"]
                                + 0.30 * raw["cat_fit"]
                                + 0.15 * raw["graph"],
                            ),
                            4,
                        ),
                        "quality": round(item.quality, 4),
                        "freshness": round(item.freshness, 4),
                        "novelty": round(raw["novelty"], 4),
                    },
                }
            )
        rows.sort(key=lambda row: (-row["base"], row["item"].item_id))
        pool = rows[: max(40, limit * 6)]
        selected = []
        while pool and len(selected) < limit:
            best = None
            best_score = float("-inf")
            for row in pool:
                redundancy = max(
                    (
                        CAPABILITIES.call(
                            "recommend.rerank",
                            cfg.rerank_strategy,
                            self,
                            row["item"],
                            chosen["item"],
                        )
                        for chosen in selected
                    ),
                    default=0.0,
                )
                adjusted = row["base"] - cfg.diversity * redundancy
                if adjusted > best_score:
                    best_score, best = adjusted, row
            assert best is not None
            selected.append({**best, "adjusted": best_score})
            pool.remove(best)
        return [
            {
                "rank": index + 1,
                **row["item"].public_dict(),
                "score": round(row["adjusted"], 5),
                "signals": row["signals"],
            }
            for index, row in enumerate(selected)
        ]

    def recommend(self, user_id: str, *, limit: int = 10) -> list[dict]:
        return self.rank_prepared(self.prepare(user_id), limit=limit)


def _profile_recency_balanced(engine: RecommendationEngine, user_id: str):
    return engine._profile_with_decay(user_id, horizon=30.0)


def _profile_recent_intent(engine: RecommendationEngine, user_id: str):
    return engine._profile_with_decay(user_id, horizon=10.0)


def _profile_long_horizon(engine: RecommendationEngine, user_id: str):
    return engine._profile_with_decay(user_id, horizon=90.0)


def _candidate_full_pool(
    engine: RecommendationEngine,
    user_id: str,
    profile: dict[int, float],
    cats: Counter[str],
    seen: set[str],
    seeds: Counter[str],
    graph_scores: dict[str, float],
) -> list[str]:
    return [
        item.item_id
        for item in engine.catalog.items
        if item.eligible and item.item_id not in seen
    ]


def _candidate_evidence_union(
    engine: RecommendationEngine,
    user_id: str,
    profile: dict[int, float],
    cats: Counter[str],
    seen: set[str],
    seeds: Counter[str],
    graph_scores: dict[str, float],
) -> list[str]:
    eligible = [
        item
        for item in engine.catalog.items
        if item.eligible and item.item_id not in seen
    ]
    if not seeds or not eligible:
        return [item.item_id for item in eligible]

    selected: set[str] = set(graph_scores)
    if cats:
        category_keys = set(cats)
        for item in eligible:
            if set(item.categories) & category_keys:
                selected.add(item.item_id)

    semantic = []
    if profile:
        for item in eligible:
            semantic.append(
                (max(0.0, cosine(profile, engine._vectors[item.item_id])), item.item_id)
            )
        semantic.sort(key=lambda row: (-row[0], row[1]))
        selected.update(item_id for _, item_id in semantic[:24])

    target = min(len(eligible), max(24, int(len(eligible) * 0.55)))
    if len(selected) < target:
        fallback = sorted(
            eligible,
            key=lambda item: (
                -(
                    0.45 * item.quality
                    + 0.35 * item.freshness
                    + 0.20 * engine._popularity[item.item_id]
                ),
                item.item_id,
            ),
        )
        for item in fallback:
            selected.add(item.item_id)
            if len(selected) >= target:
                break
    return sorted(selected)


def _cold_quality_freshness(
    engine: RecommendationEngine,
    item: Item,
    popularity: float,
    explore: float,
) -> float:
    return 0.45 * item.quality + 0.35 * item.freshness + 0.20 * popularity


def _cold_discovery_prior(
    engine: RecommendationEngine,
    item: Item,
    popularity: float,
    explore: float,
) -> float:
    novelty = 1.0 - popularity
    return 0.40 * item.quality + 0.35 * item.freshness + 0.25 * novelty


def _cold_fresh_explore(
    engine: RecommendationEngine,
    item: Item,
    popularity: float,
    explore: float,
) -> float:
    return 0.35 * item.quality + 0.40 * item.freshness + 0.25 * explore


def _explore_stable_fresh(
    engine: RecommendationEngine,
    user_id: str,
    item: Item,
    popularity: float,
) -> float:
    return engine._stable_hash(user_id, item.item_id) * item.freshness


def _explore_novelty_seek(
    engine: RecommendationEngine,
    user_id: str,
    item: Item,
    popularity: float,
) -> float:
    novelty = 1.0 - popularity
    return engine._stable_hash(user_id, item.item_id) * (0.45 + 0.55 * novelty) * item.freshness


def _explore_coverage_seek(
    engine: RecommendationEngine,
    user_id: str,
    item: Item,
    popularity: float,
) -> float:
    novelty = 1.0 - popularity
    stable = engine._stable_hash(user_id, item.item_id)
    return (0.60 * novelty + 0.40 * stable) * (0.40 + 0.60 * item.freshness)


def _category_similarity(engine: RecommendationEngine, left: Item, right: Item) -> float:
    left_categories, right_categories = set(left.categories), set(right.categories)
    return len(left_categories & right_categories) / max(1, len(left_categories | right_categories))


def _semantic_similarity(engine: RecommendationEngine, left: Item, right: Item) -> float:
    return max(0.0, cosine(engine._vectors[left.item_id], engine._vectors[right.item_id]))


def _hybrid_similarity(engine: RecommendationEngine, left: Item, right: Item) -> float:
    return 0.55 * _category_similarity(engine, left, right) + 0.45 * _semantic_similarity(engine, left, right)


CAPABILITIES.register(
    "recommend.profile",
    "recency_balanced",
    "Build the user profile from weighted behavior with a balanced recency horizon.",
    _profile_recency_balanced,
    default=True,
)
CAPABILITIES.register(
    "recommend.profile",
    "recent_intent",
    "Emphasize recent user intent when behavior is drifting quickly.",
    _profile_recent_intent,
)
CAPABILITIES.register(
    "recommend.profile",
    "long_horizon",
    "Use a longer preference horizon for stable-interest domains.",
    _profile_long_horizon,
)

CAPABILITIES.register(
    "recommend.candidate",
    "full_pool",
    "Score every eligible unseen item in the owned catalog.",
    _candidate_full_pool,
    default=True,
)
CAPABILITIES.register(
    "recommend.candidate",
    "evidence_union",
    "Focus candidates using graph, category and profile evidence with a safe global fallback.",
    _candidate_evidence_union,
    complexity=1.2,
)

CAPABILITIES.register(
    "recommend.cold_start",
    "quality_freshness",
    "Use quality, freshness and popularity as the cold-start prior.",
    _cold_quality_freshness,
    default=True,
)
CAPABILITIES.register(
    "recommend.cold_start",
    "discovery_prior",
    "Prefer quality, freshness and novelty for cold-start discovery.",
    _cold_discovery_prior,
)
CAPABILITIES.register(
    "recommend.cold_start",
    "fresh_explore",
    "Use freshness and stable exploration as stronger cold-start evidence.",
    _cold_fresh_explore,
)

CAPABILITIES.register(
    "recommend.exploration",
    "stable_fresh",
    "Deterministic per-user exploration scaled by item freshness.",
    _explore_stable_fresh,
    default=True,
)
CAPABILITIES.register(
    "recommend.exploration",
    "novelty_seek",
    "Allocate more exploration to novel items while remaining deterministic.",
    _explore_novelty_seek,
)
CAPABILITIES.register(
    "recommend.exploration",
    "coverage_seek",
    "Favor low-popularity coverage while retaining stable user-item exploration.",
    _explore_coverage_seek,
)

CAPABILITIES.register(
    "recommend.rerank",
    "category_mmr",
    "Diversify the slate by category overlap.",
    _category_similarity,
    default=True,
)
CAPABILITIES.register(
    "recommend.rerank",
    "semantic_mmr",
    "Diversify the slate by owned semantic-vector similarity.",
    _semantic_similarity,
    complexity=1.1,
)
CAPABILITIES.register(
    "recommend.rerank",
    "hybrid_mmr",
    "Blend category and semantic redundancy during slate selection.",
    _hybrid_similarity,
    complexity=1.15,
)
