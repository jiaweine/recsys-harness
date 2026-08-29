from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import log

from lingjing_harness.domain import Catalog, Item
from lingjing_harness.serving import normalize_serving_limit
from .capabilities import CAPABILITIES, capability_field
from .text import cosine, hashed_vector, tokenize


_BLEND = {"evolve_group": "blend", "min": 0.005, "max": 0.75, "relative_step": 0.16}
_INDEPENDENT = {"evolve_group": "independent", "min": 0.0, "max": 0.32, "relative_step": 0.18}


@dataclass(frozen=True)
class SearchConfig:
    """Search strategy genome.

    Continuous fields are response-surface genes. Categorical fields are typed
    capability genes whose choices come from the vertical CapabilityRegistry.
    Adding a capability does not require a branch in the central evolver.
    """

    lexical: float = field(default=0.47, metadata=_BLEND)
    semantic: float = field(default=0.25, metadata=_BLEND)
    title: float = field(default=0.10, metadata=_BLEND)
    quality: float = field(default=0.07, metadata=_BLEND)
    popularity: float = field(default=0.04, metadata=_BLEND)
    freshness: float = field(default=0.07, metadata=_BLEND)
    diversity: float = field(default=0.08, metadata=_INDEPENDENT)

    query_strategy: str = capability_field("search.query", "rare_focus")
    candidate_strategy: str = capability_field("search.candidate", "postings_union")
    rerank_strategy: str = capability_field("search.rerank", "category_mmr")


class SearchEngine:
    """Project-owned search with evolvable query, retrieval and rerank stages."""

    GENERIC_QUERY_TOKENS = {"装备", "用品", "商品", "产品", "东西", "好物"}

    def __init__(self, catalog: Catalog, config: SearchConfig | None = None) -> None:
        self.catalog = catalog
        self.config = config or SearchConfig()
        self._doc_tokens: dict[str, list[str]] = {}
        self._field_tokens: dict[str, tuple[list[str], list[str], list[str]]] = {}
        self._title_token_sets: dict[str, set[str]] = {}
        self._title_lower: dict[str, str] = {}
        self._postings: dict[str, list[str]] = {}
        self._df: Counter[str] = Counter()
        self._vectors: dict[str, dict[int, float]] = {}
        for item in catalog.items:
            body = " ".join([item.title, item.text, *item.categories])
            title_tokens = tokenize(item.title)
            text_tokens = tokenize(item.text)
            category_tokens = tokenize(" ".join(item.categories))
            toks = [*title_tokens, *text_tokens, *category_tokens]
            self._field_tokens[item.item_id] = (title_tokens, text_tokens, category_tokens)
            self._title_token_sets[item.item_id] = set(title_tokens)
            self._title_lower[item.item_id] = item.title.lower()
            self._doc_tokens[item.item_id] = toks
            unique_tokens = set(toks)
            self._df.update(unique_tokens)
            if item.eligible:
                for token in unique_tokens:
                    self._postings.setdefault(token, []).append(item.item_id)
            self._vectors[item.item_id] = hashed_vector(body)
        self._avg_len = sum(map(len, self._doc_tokens.values())) / max(1, len(self._doc_tokens))

    def with_config(self, config: SearchConfig) -> "SearchEngine":
        clone = object.__new__(SearchEngine)
        clone.catalog = self.catalog
        clone.config = config
        clone._doc_tokens = self._doc_tokens
        clone._field_tokens = self._field_tokens
        clone._title_token_sets = self._title_token_sets
        clone._title_lower = self._title_lower
        clone._postings = self._postings
        clone._df = self._df
        clone._vectors = self._vectors
        clone._avg_len = self._avg_len
        return clone

    def capability_manifest(self) -> dict[str, list[dict]]:
        return {
            group: rows
            for group, rows in CAPABILITIES.manifest().items()
            if group.startswith("search.")
        }

    def _idf(self, token: str) -> float:
        n = max(1, len(self.catalog.items))
        df = self._df.get(token, 0)
        return log(1 + (n - df + 0.5) / (df + 0.5))

    def _bm25(self, item: Item, qtokens: list[str]) -> float:
        toks = self._doc_tokens[item.item_id]
        title_tokens, text_tokens, category_tokens = self._field_tokens[item.item_id]
        title_tf, text_tf, category_tf = Counter(title_tokens), Counter(text_tokens), Counter(category_tokens)
        dl = max(1, len(toks))
        score = 0.0
        k1, b = 1.45, 0.72
        for token in qtokens:
            f = 2.1 * title_tf.get(token, 0) + text_tf.get(token, 0) + 0.75 * category_tf.get(token, 0)
            if f <= 0:
                continue
            query_weight = 0.45 if token in self.GENERIC_QUERY_TOKENS else 1.0
            score += query_weight * self._idf(token) * (f * (k1 + 1)) / (
                f + k1 * (1 - b + b * dl / max(1.0, self._avg_len))
            )
        return score

    def prepare(self, query: str) -> list[dict]:
        query = query.strip()
        qtokens = list(dict.fromkeys(tokenize(query)))
        if not qtokens:
            return []
        qvec = hashed_vector(query)
        retrieval_tokens = CAPABILITIES.call(
            "search.query",
            self.config.query_strategy,
            self,
            query,
            qtokens,
        )
        retrieval_tokens = list(dict.fromkeys(str(x) for x in retrieval_tokens if str(x)))
        provenance = CAPABILITIES.call(
            "search.candidate",
            self.config.candidate_strategy,
            self,
            query,
            qtokens,
            retrieval_tokens,
            qvec,
        )
        if not provenance:
            return []

        qset = set(qtokens)
        rows: list[dict] = []
        max_lex = 1e-9
        for item_id, source in provenance.items():
            item = self.catalog.item_by_id.get(item_id)
            if item is None or not item.eligible:
                continue
            lex = self._bm25(item, qtokens)
            max_lex = max(max_lex, lex)
            title_tokens = self._title_token_sets[item.item_id]
            overlap = len(qset & title_tokens) / max(1, len(qset))
            exact = 1.0 if query.lower() in self._title_lower[item.item_id] else 0.0
            sem = max(0.0, cosine(qvec, self._vectors[item.item_id]))
            rows.append(
                {
                    "item": item,
                    "lex_raw": lex,
                    "sem": sem,
                    "title": min(1.0, overlap * 0.7 + exact * 0.55),
                    "candidate_source": source,
                }
            )
        for row in rows:
            row["lex"] = row["lex_raw"] / max_lex
            row["pop"] = self.catalog.popularity_norm(row["item"])
        return [
            row
            for row in rows
            if row["lex"] > 0
            or row["title"] > 0
            or (row["candidate_source"] in {"expanded", "semantic"} and row["sem"] >= 0.16)
        ]

    def rank_prepared(
        self,
        prepared: list[dict],
        *,
        config: SearchConfig | None = None,
        limit: int = 10,
    ) -> list[dict]:
        limit = normalize_serving_limit(limit)
        if limit == 0:
            return []
        cfg = config or self.config
        rows: list[dict] = []
        for raw in prepared:
            item = raw["item"]
            base = (
                cfg.lexical * raw["lex"]
                + cfg.semantic * raw["sem"]
                + cfg.title * raw["title"]
                + cfg.quality * item.quality
                + cfg.popularity * raw["pop"]
                + cfg.freshness * item.freshness
            )
            rows.append(
                {
                    **raw,
                    "base": base,
                    "signals": {
                        "match": round(0.65 * raw["lex"] + 0.35 * raw["sem"], 4),
                        "quality": round(item.quality, 4),
                        "freshness": round(item.freshness, 4),
                        "popularity": round(raw["pop"], 4),
                    },
                }
            )
        rows.sort(key=lambda x: (-x["base"], x["item"].item_id))
        pool = rows[: max(30, limit * 6)]
        selected: list[dict] = []
        while pool and len(selected) < limit:
            best = None
            best_score = float("-inf")
            for row in pool:
                redundancy = max(
                    (
                        CAPABILITIES.call(
                            "search.rerank",
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
                "rank": i + 1,
                **row["item"].public_dict(),
                "score": round(row["adjusted"], 5),
                "signals": row["signals"],
            }
            for i, row in enumerate(selected)
        ]

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        limit = normalize_serving_limit(limit)
        if limit == 0:
            return []
        return self.rank_prepared(self.prepare(query), limit=limit)


def _query_literal(engine: SearchEngine, query: str, qtokens: list[str]) -> list[str]:
    return list(qtokens)


def _query_rare_focus(engine: SearchEngine, query: str, qtokens: list[str]) -> list[str]:
    n = max(1, len(engine.catalog.items))
    concrete = [
        token
        for token in qtokens
        if engine._df.get(token, 0) <= max(64, int(n * 0.35))
    ]
    return concrete or list(qtokens)


def _query_catalog_expand(engine: SearchEngine, query: str, qtokens: list[str]) -> list[str]:
    base = _query_rare_focus(engine, query, qtokens)
    anchor_ids: set[str] = set()
    for token in qtokens:
        anchor_ids.update(engine._postings.get(token, ()))
    if not anchor_ids:
        return base
    scores: Counter[str] = Counter()
    qset = set(qtokens)
    for item_id in anchor_ids:
        for token in set(engine._doc_tokens.get(item_id, ())):
            if token in qset or token in engine.GENERIC_QUERY_TOKENS:
                continue
            if engine._df.get(token, 0) > max(96, int(len(engine.catalog.items) * 0.6)):
                continue
            scores[token] += max(0.1, engine._idf(token))
    expansion = [token for token, _ in scores.most_common(2)]
    return list(dict.fromkeys([*base, *expansion]))


def _candidate_postings_union(
    engine: SearchEngine,
    query: str,
    qtokens: list[str],
    retrieval_tokens: list[str],
    qvec: dict[int, float],
) -> dict[str, str]:
    original = set(qtokens)
    out: dict[str, str] = {}
    for token in retrieval_tokens:
        source = "lexical" if token in original else "expanded"
        for item_id in engine._postings.get(token, ()):
            previous = out.get(item_id)
            if previous != "lexical":
                out[item_id] = source
    return out


def _candidate_semantic_rescue(
    engine: SearchEngine,
    query: str,
    qtokens: list[str],
    retrieval_tokens: list[str],
    qvec: dict[int, float],
) -> dict[str, str]:
    out = _candidate_postings_union(engine, query, qtokens, retrieval_tokens, qvec)
    if not out:
        return out
    semantic = []
    for item in engine.catalog.items:
        if not item.eligible or item.item_id in out:
            continue
        score = max(0.0, cosine(qvec, engine._vectors[item.item_id]))
        semantic.append((score, item.item_id))
    semantic.sort(key=lambda row: (-row[0], row[1]))
    budget = min(24, max(6, len(out)))
    for score, item_id in semantic[:budget]:
        if score >= 0.16:
            out[item_id] = "semantic"
    return out


def _category_similarity(engine: SearchEngine, a: Item, b: Item) -> float:
    aa, bb = set(a.categories), set(b.categories)
    return len(aa & bb) / max(1, len(aa | bb))


def _semantic_similarity(engine: SearchEngine, a: Item, b: Item) -> float:
    return max(0.0, cosine(engine._vectors[a.item_id], engine._vectors[b.item_id]))


def _hybrid_similarity(engine: SearchEngine, a: Item, b: Item) -> float:
    return 0.55 * _category_similarity(engine, a, b) + 0.45 * _semantic_similarity(engine, a, b)


CAPABILITIES.register(
    "search.query",
    "rare_focus",
    "Prefer concrete, discriminative query evidence for candidate retrieval.",
    _query_rare_focus,
    default=True,
)
CAPABILITIES.register(
    "search.query",
    "literal",
    "Use the tokenized user query without retrieval rewriting.",
    _query_literal,
)
CAPABILITIES.register(
    "search.query",
    "catalog_expand",
    "Expand retrieval evidence with high-IDF tokens learned from the current catalog.",
    _query_catalog_expand,
    complexity=1.15,
)

CAPABILITIES.register(
    "search.candidate",
    "postings_union",
    "Generate candidates only from owned lexical/category postings.",
    _candidate_postings_union,
    default=True,
)
CAPABILITIES.register(
    "search.candidate",
    "semantic_rescue",
    "Union anchored postings with bounded semantic rescue candidates.",
    _candidate_semantic_rescue,
    complexity=1.35,
)

CAPABILITIES.register(
    "search.rerank",
    "category_mmr",
    "Diversify the slate by category overlap.",
    _category_similarity,
    default=True,
)
CAPABILITIES.register(
    "search.rerank",
    "semantic_mmr",
    "Diversify the slate by owned semantic-vector similarity.",
    _semantic_similarity,
    complexity=1.1,
)
CAPABILITIES.register(
    "search.rerank",
    "hybrid_mmr",
    "Blend category and semantic redundancy during slate selection.",
    _hybrid_similarity,
    complexity=1.15,
)
