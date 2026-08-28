from __future__ import annotations

import json

from lingjing_harness.algorithms import (
    RecommendationEngine,
    SearchConfig,
    SearchEngine,
    audit_recommend,
    audit_search,
)
from lingjing_harness.sample_data import build_sample_catalog


def main() -> None:
    catalog = build_sample_catalog()

    search = audit_search(catalog, SearchEngine(catalog))
    lexical_config = SearchConfig(
        lexical=0.75,
        semantic=0.005,
        title=0.15,
        quality=0.03,
        popularity=0.02,
        freshness=0.045,
        diversity=0.0,
        query_strategy="literal",
        candidate_strategy="postings_union",
        rerank_strategy="category_mmr",
    )
    lexical = audit_search(catalog, SearchEngine(catalog, lexical_config))

    recommend = audit_recommend(catalog, RecommendationEngine(catalog))
    result = {
        "search": {
            "hybrid": {
                "ndcg": search.get("quality", 0.0),
                "recall": search.get("recall", 0.0),
                "mrr": search.get("mrr", 0.0),
            },
            "lexical_baseline": {
                "ndcg": lexical.get("quality", 0.0),
                "recall": lexical.get("recall", 0.0),
                "mrr": lexical.get("mrr", 0.0),
            },
            "ndcg_delta": round(
                float(search.get("quality", 0.0)) - float(lexical.get("quality", 0.0)),
                4,
            ),
        },
        "recommend": {
            "temporal_protocol": recommend.get("relevance_protocol"),
            "users": recommend.get("relevance_users", 0),
            "model": {
                "hit_rate": recommend.get("relevance_hit_rate", 0.0),
                "recall": recommend.get("relevance_recall", 0.0),
                "mrr": recommend.get("relevance_mrr", 0.0),
                "ndcg": recommend.get("relevance_ndcg", 0.0),
            },
            "popularity_baseline": {
                "ndcg": recommend.get("popularity_relevance_ndcg", 0.0),
            },
            "ndcg_delta_vs_popularity": recommend.get(
                "relevance_ndcg_delta_vs_popularity", 0.0
            ),
            "descriptive_guardrails": {
                "coverage": recommend.get("coverage", 0.0),
                "diversity": recommend.get("diversity", 0.0),
                "freshness": recommend.get("freshness", 0.0),
                "novelty": recommend.get("novelty", 0.0),
            },
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
