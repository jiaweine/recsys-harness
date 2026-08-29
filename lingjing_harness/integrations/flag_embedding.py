from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lingjing_harness.domain import Catalog
from lingjing_harness.serving import normalize_serving_limit, normalize_serving_score


DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


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


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_QUERY_INSTRUCTION",
    "FlagEmbeddingSearchAdapter",
]
