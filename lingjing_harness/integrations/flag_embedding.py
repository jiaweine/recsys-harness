from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lingjing_harness.domain import Catalog


DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


@dataclass(slots=True)
class FlagEmbeddingSearchAdapter:
    """SearchServingAdapter backed directly by FlagOpen/FlagEmbedding.

    The integration intentionally delegates embedding inference to FlagEmbedding
    instead of reimplementing encoders or similarity models in the harness.
    Model loading is lazy with respect to the core package: users install the
    optional ``semantic`` extra only when they choose this backend.
    """

    catalog: Catalog
    model_name: str = DEFAULT_MODEL
    query_instruction: str = DEFAULT_QUERY_INSTRUCTION
    use_fp16: bool = False
    _model: Any = field(init=False, repr=False)
    _items: list[Any] = field(init=False, repr=False)
    _corpus_embeddings: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            from FlagEmbedding import FlagModel
        except ImportError as exc:
            raise RuntimeError(
                "FlagEmbedding backend requires the optional semantic dependencies. "
                "Install the project with the semantic extra before using this adapter."
            ) from exc

        self._items = [item for item in self.catalog.items if item.eligible]
        self._model = FlagModel(
            self.model_name,
            query_instruction_for_retrieval=self.query_instruction,
            use_fp16=self.use_fp16,
        )
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
        self._corpus_embeddings = self._model.encode_corpus(corpus)

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query or limit <= 0 or not self._items:
            return []

        import numpy as np

        query_embedding = np.asarray(self._model.encode_queries([query]))[0]
        corpus_embeddings = np.asarray(self._corpus_embeddings)
        scores = corpus_embeddings @ query_embedding
        order = np.argsort(scores)[::-1][: min(int(limit), len(self._items))]

        rows: list[dict[str, Any]] = []
        for index in order:
            item = self._items[int(index)]
            rows.append(
                {
                    **item.public_dict(),
                    "score": float(scores[int(index)]),
                    "backend": "flag_embedding",
                    "model": self.model_name,
                }
            )
        return rows


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_QUERY_INSTRUCTION",
    "FlagEmbeddingSearchAdapter",
]
