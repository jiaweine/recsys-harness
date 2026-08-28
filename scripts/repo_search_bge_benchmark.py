from __future__ import annotations

import json

import numpy as np
from FlagEmbedding import FlagModel

from lingjing_harness.algorithms import ndcg_at_k, recall_at_k, reciprocal_rank
from lingjing_harness.sample_data import build_sample_catalog


MODEL_NAME = "BAAI/bge-small-zh-v1.5"
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def main() -> None:
    catalog = build_sample_catalog()
    items = [item for item in catalog.items if item.eligible]
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
        for item in items
    ]
    queries = [label.query for label in catalog.query_labels]

    model = FlagModel(
        MODEL_NAME,
        query_instruction_for_retrieval=QUERY_INSTRUCTION,
        use_fp16=False,
    )
    query_embeddings = model.encode_queries(queries)
    corpus_embeddings = model.encode_corpus(corpus)
    scores = np.asarray(query_embeddings) @ np.asarray(corpus_embeddings).T

    details = []
    for label, query_scores in zip(catalog.query_labels, scores):
        order = np.argsort(query_scores)[::-1][: min(10, len(items))]
        ranked = [items[int(index)].item_id for index in order]
        relevant = set(label.relevant)
        details.append(
            {
                "query": label.query,
                "recall": recall_at_k(ranked, relevant),
                "mrr": reciprocal_rank(ranked, relevant),
                "ndcg": ndcg_at_k(ranked, relevant),
                "top": ranked[:5],
            }
        )

    count = max(1, len(details))
    report = {
        "model": MODEL_NAME,
        "library": "FlagOpen/FlagEmbedding",
        "api": "FlagModel.encode_queries + encode_corpus",
        "queries": len(details),
        "ndcg": round(sum(row["ndcg"] for row in details) / count, 6),
        "recall": round(sum(row["recall"] for row in details) / count, 6),
        "mrr": round(sum(row["mrr"] for row in details) / count, 6),
        "details": details,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
