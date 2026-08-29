from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lingjing_harness.algorithms import SearchConfig, SearchEngine
from lingjing_harness.algorithms.text import tokenize
from lingjing_harness.domain import Catalog
from lingjing_harness.serving import normalize_serving_limit, normalize_serving_score


DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
DEFAULT_DENSE_LIMIT = 48


def _load_flag_model():
    try:
        from FlagEmbedding import FlagModel
    except ImportError as exc:
        raise RuntimeError(
            "FlagEmbeddingSearchAdapter requires the optional semantic dependencies; "
            "install with `pip install -e '.[semantic]'`"
        ) from exc
    return FlagModel


@dataclass(slots=True)
class FlagEmbeddingSearchAdapter:
    """Thin SearchServingAdapter backed directly by FlagOpen/FlagEmbedding.

    FlagEmbedding owns model loading and embedding inference. Harness owns only
    the eligible corpus boundary and the serving-row contract around dense
    retrieval results.
    """

    catalog: Catalog
    model_name: str = DEFAULT_MODEL
    query_instruction: str = DEFAULT_QUERY_INSTRUCTION
    use_fp16: bool = False
    model_kwargs: dict[str, Any] = field(default_factory=dict)
    _model: Any = field(init=False, repr=False)
    _items: list[Any] = field(init=False, repr=False)
    _corpus_embeddings: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        FlagModel = _load_flag_model()
        self._items = [item for item in self.catalog.items if item.eligible]

        kwargs = dict(self.model_kwargs)
        kwargs.setdefault("query_instruction_for_retrieval", self.query_instruction)
        kwargs.setdefault("use_fp16", self.use_fp16)
        self._model = FlagModel(self.model_name, **kwargs)

        corpus = [
            " ".join(
                part
                for part in (
                    item.title,
                    item.text,
                    " ".join(item.categories),
                )
                if part
            )
            for item in self._items
        ]
        self._corpus_embeddings = self._model.encode_corpus(corpus) if corpus else None

    def capability_manifest(self) -> dict[str, Any]:
        return {
            "backend": "flag_embedding",
            "model": self.model_name,
            "corpus_items": len(self._items),
            "query_instruction": self.query_instruction,
        }

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        limit = normalize_serving_limit(limit)
        query = str(query or "").strip()
        if not query or limit == 0 or not self._items or self._corpus_embeddings is None:
            return []

        query_embeddings = self._model.encode_queries([query])
        query_embedding = query_embeddings[0]
        scores = self._corpus_embeddings @ query_embedding

        ranked: list[tuple[float, int]] = []
        for index, raw_score in enumerate(scores):
            if index >= len(self._items):
                break
            try:
                score = normalize_serving_score(raw_score)
            except ValueError:
                continue
            ranked.append((score, index))
        ranked.sort(key=lambda row: (-row[0], row[1]))

        rows: list[dict[str, Any]] = []
        for score, index in ranked:
            item = self._items[index]
            rows.append(
                {
                    **item.public_dict(),
                    "score": score,
                    "backend": "flag_embedding",
                    "model": self.model_name,
                }
            )
            if len(rows) >= limit:
                break
        return rows


class FlagEmbeddingHybridSearchEngine:
    """SearchEngine-compatible façade using FlagEmbedding for semantic retrieval.

    Dense retrieval and semantic similarity come directly from FlagEmbedding.
    The project-owned SearchEngine continues to own lexical evidence, business
    signals, ranking weights, reranking, evolution, temporal evaluation and
    activation semantics. ``with_config`` reuses the loaded model and corpus
    embeddings so one evolution run never reloads the mature backend per trial.
    """

    def __init__(
        self,
        reference: SearchEngine,
        adapter: FlagEmbeddingSearchAdapter,
        *,
        dense_limit: int = DEFAULT_DENSE_LIMIT,
    ) -> None:
        if reference.catalog is not adapter.catalog:
            raise ValueError("reference search engine and semantic adapter must share one catalog")
        dense_limit = normalize_serving_limit(dense_limit)
        if dense_limit == 0:
            raise ValueError("dense_limit must be greater than zero")
        self.catalog = reference.catalog
        self.config = reference.config
        self.reference = reference
        self.adapter = adapter
        self.dense_limit = dense_limit

    def with_config(self, config: SearchConfig) -> "FlagEmbeddingHybridSearchEngine":
        clone = object.__new__(type(self))
        clone.catalog = self.catalog
        clone.config = config
        clone.reference = self.reference.with_config(config)
        clone.adapter = self.adapter
        clone.dense_limit = self.dense_limit
        return clone

    def capability_manifest(self) -> dict[str, list[dict]]:
        return self.reference.capability_manifest()

    def backend_manifest(self) -> dict[str, Any]:
        return {
            **self.adapter.capability_manifest(),
            "mode": "hybrid_candidates",
            "dense_limit": self.dense_limit,
            "ranking_owner": "harness",
            "semantic_owner": "flag_embedding",
        }

    def routing_prepare(self, query: str) -> list[dict]:
        """Preserve reference pathologies for SegmentRouter classification."""

        return self.reference.prepare(query)

    @staticmethod
    def _semantic_score(raw: Any) -> float:
        try:
            score = normalize_serving_score(raw)
        except ValueError:
            return 0.0
        return max(0.0, min(1.0, score))

    def prepare(self, query: str) -> list[dict]:
        query = str(query or "").strip()
        if not query:
            return []

        # When this backend is selected, FlagEmbedding owns the semantic signal.
        # Keep lexical/expanded reference candidates, but discard the reference
        # hash-vector semantic-rescue candidates and zero their semantic feature.
        prepared: dict[str, dict[str, Any]] = {}
        for raw in self.reference.prepare(query):
            item = raw["item"]
            if raw.get("candidate_source") == "semantic":
                continue
            prepared[item.item_id] = {**raw, "sem": 0.0}

        qtokens = list(dict.fromkeys(tokenize(query)))
        qset = set(qtokens)
        dense_limit = min(
            self.dense_limit,
            sum(1 for item in self.catalog.items if item.eligible),
        )
        for dense in self.adapter.search(query, limit=dense_limit):
            item_id = str(dense.get("id") or "")
            item = self.catalog.item_by_id.get(item_id)
            if item is None or not item.eligible:
                continue
            semantic = self._semantic_score(dense.get("score"))
            if semantic <= 0.0:
                continue
            existing = prepared.get(item_id)
            if existing is not None:
                existing["sem"] = semantic
                existing["candidate_source"] = "hybrid"
                continue

            title_tokens = set(tokenize(item.title))
            overlap = len(qset & title_tokens) / max(1, len(qset))
            exact = 1.0 if query.lower() in item.title.lower() else 0.0
            prepared[item_id] = {
                "item": item,
                "lex_raw": 0.0,
                "lex": 0.0,
                "sem": semantic,
                "title": min(1.0, overlap * 0.7 + exact * 0.55),
                "candidate_source": "semantic",
                "pop": self.catalog.popularity_norm(item),
            }
        return list(prepared.values())

    def rank_prepared(
        self,
        prepared: list[dict],
        *,
        config: SearchConfig | None = None,
        limit: int = 10,
    ) -> list[dict]:
        rows = self.reference.rank_prepared(prepared, config=config, limit=limit)
        model = self.adapter.model_name
        return [
            {
                **row,
                "backend": "hybrid_flag_embedding",
                "semantic_model": model,
            }
            for row in rows
        ]

    def search(self, query: str, *, limit: int = 10) -> list[dict]:
        limit = normalize_serving_limit(limit)
        if limit == 0:
            return []
        return self.rank_prepared(self.prepare(query), limit=limit)


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_QUERY_INSTRUCTION",
    "DEFAULT_DENSE_LIMIT",
    "FlagEmbeddingSearchAdapter",
    "FlagEmbeddingHybridSearchEngine",
]
