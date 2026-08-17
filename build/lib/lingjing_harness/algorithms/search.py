from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import log

from lingjing_harness.domain import Catalog, Item
from .text import cosine, hashed_vector, tokenize


@dataclass(frozen=True)
class SearchConfig:
    lexical: float = 0.47
    semantic: float = 0.25
    title: float = 0.10
    quality: float = 0.07
    popularity: float = 0.04
    freshness: float = 0.07
    diversity: float = 0.08


class SearchEngine:
    """Project-owned hybrid search: field-aware lexical + hashed semantic + slate rerank."""

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

    def _idf(self, token: str) -> float:
        n = max(1, len(self.catalog.items)); df = self._df.get(token, 0)
        return log(1 + (n - df + 0.5) / (df + 0.5))

    def _bm25(self, item: Item, qtokens: list[str]) -> float:
        toks = self._doc_tokens[item.item_id]
        title_tokens, text_tokens, category_tokens = self._field_tokens[item.item_id]
        title_tf, text_tf, category_tf = Counter(title_tokens), Counter(text_tokens), Counter(category_tokens)
        dl = max(1, len(toks)); score = 0.0
        k1, b = 1.45, 0.72
        for token in qtokens:
            # Field-aware term frequency: title > body text > broad category tags.
            f = 2.1*title_tf.get(token, 0) + text_tf.get(token, 0) + .75*category_tf.get(token, 0)
            if f <= 0: continue
            query_weight = .45 if token in self.GENERIC_QUERY_TOKENS else 1.0
            score += query_weight * self._idf(token) * (f*(k1+1)) / (f + k1*(1-b+b*dl/max(1.0, self._avg_len)))
        return score

    @staticmethod
    def _cat_similarity(a: Item, b: Item) -> float:
        aa, bb = set(a.categories), set(b.categories)
        return len(aa & bb) / max(1, len(aa | bb))

    def prepare(self, query: str) -> list[dict]:
        query = query.strip(); qtokens = list(dict.fromkeys(tokenize(query)))
        if not qtokens: return []
        qvec = hashed_vector(query)
        qset = set(qtokens)
        rows: list[dict] = []
        max_lex = 1e-9
        n = max(1, len(self.catalog.items))
        # Retrieval uses the rarest concrete query evidence first. This avoids scanning the
        # full catalog for every request while preserving the same lexical eligibility rule.
        concrete = [token for token in qtokens if self._df.get(token, 0) <= max(64, int(n * .35))]
        retrieval_tokens = concrete or qtokens
        candidate_ids = set()
        for token in retrieval_tokens:
            candidate_ids.update(self._postings.get(token, ()))
        if not candidate_ids:
            return []
        for item_id in candidate_ids:
            item = self.catalog.item_by_id[item_id]
            lex = self._bm25(item, qtokens); max_lex = max(max_lex, lex)
            title_tokens = self._title_token_sets[item.item_id]; overlap = len(qset & title_tokens)/max(1, len(qset))
            exact = 1.0 if query.lower() in self._title_lower[item.item_id] else 0.0
            sem = max(0.0, cosine(qvec, self._vectors[item.item_id]))
            rows.append({"item": item, "lex_raw": lex, "sem": sem, "title": min(1.0, overlap*.7+exact*.55)})
        for row in rows:
            row["lex"] = row["lex_raw"] / max_lex
            row["pop"] = self.catalog.popularity_norm(row["item"])
        return [row for row in rows if row["lex"] > 0 or row["title"] > 0]

    def rank_prepared(self, prepared: list[dict], *, config: SearchConfig | None = None, limit: int = 10) -> list[dict]:
        cfg = config or self.config
        rows: list[dict] = []
        for raw in prepared:
            item = raw["item"]
            base = cfg.lexical*raw["lex"] + cfg.semantic*raw["sem"] + cfg.title*raw["title"] + cfg.quality*item.quality + cfg.popularity*raw["pop"] + cfg.freshness*item.freshness
            rows.append({
                **raw,
                "base": base,
                "signals": {"match": round(.65*raw["lex"]+.35*raw["sem"],4), "quality": round(item.quality,4), "freshness": round(item.freshness,4), "popularity": round(raw["pop"],4)},
            })
        rows.sort(key=lambda x: (-x["base"], x["item"].item_id))
        pool = rows[:max(30, limit*6)]; selected: list[dict] = []
        while pool and len(selected) < limit:
            best = None; best_score = float("-inf")
            for row in pool:
                redundancy = max((self._cat_similarity(row["item"], chosen["item"]) for chosen in selected), default=0.0)
                adjusted = row["base"] - cfg.diversity*redundancy
                if adjusted > best_score:
                    best_score, best = adjusted, row
            assert best is not None
            selected.append({**best, "adjusted": best_score}); pool.remove(best)
        return [{"rank": i+1, **row["item"].public_dict(), "score": round(row["adjusted"],5), "signals": row["signals"]} for i,row in enumerate(selected)]

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        return self.rank_prepared(self.prepare(query), limit=limit)
