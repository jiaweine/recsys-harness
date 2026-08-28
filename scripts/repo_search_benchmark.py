from __future__ import annotations

import json

from sentence_transformers import SentenceTransformer, util

from lingjing_harness.algorithms import ndcg_at_k, recall_at_k, reciprocal_rank
from lingjing_harness.sample_data import build_sample_catalog


MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


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

    model = SentenceTransformer(MODEL_NAME)
    corpus_embeddings = model.encode(
        corpus,
        convert_to_tensor=True,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    query_embeddings = model.encode(
        queries,
        convert_to_tensor=True,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    hits = util.semantic_search(
        query_embeddings,
        corpus_embeddings,
        top_k=min(10, len(items)),
    )

    details = []
    for label, query_hits in zip(catalog.query_labels, hits):
        ranked = [items[int(hit["corpus_id"])].item_id for hit in query_hits]
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
        "retrieval_api": "sentence_transformers.util.semantic_search",
        "queries": len(details),
        "ndcg": round(sum(row["ndcg"] for row in details) / count, 6),
        "recall": round(sum(row["recall"] for row in details) / count, 6),
        "mrr": round(sum(row["mrr"] for row in details) / count, 6),
        "details": details,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
