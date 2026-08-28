from __future__ import annotations

import json
from collections import defaultdict

import numpy as np
from scipy.sparse import csr_matrix

from implicit.als import AlternatingLeastSquares
from implicit.bpr import BayesianPersonalizedRanking
from implicit.evaluation import leave_k_out_split, ranking_metrics_at_k
from implicit.nearest_neighbours import BM25Recommender

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction
from lingjing_harness.sample_data import build_sample_catalog


class XushuImplicitEvaluationAdapter:
    """Expose the repo-owned reference recommender through implicit's metric API."""

    def __init__(self, catalog: Catalog, user_ids: list[str], item_ids: list[str]) -> None:
        self.catalog = catalog
        self.user_ids = user_ids
        self.item_ids = item_ids
        self.engine = RecommendationEngine(catalog)

    def recommend(self, userid, user_items, N=10, **_kwargs):
        user_array = np.atleast_1d(userid)
        rows = []
        scores = []
        for raw_user in user_array:
            user_id = self.user_ids[int(raw_user)]
            ranked = self.engine.recommend(user_id, limit=N)
            ids = np.array([self.item_ids.index(row["id"]) for row in ranked], dtype=np.int32)
            values = np.array([float(row.get("score", 0.0)) for row in ranked], dtype=np.float64)
            if len(ids) < N:
                pad = N - len(ids)
                ids = np.pad(ids, (0, pad), constant_values=-1)
                values = np.pad(values, (0, pad), constant_values=-np.inf)
            rows.append(ids)
            scores.append(values)
        if np.isscalar(userid):
            return rows[0], scores[0]
        return np.vstack(rows), np.vstack(scores)


def _matrix(catalog: Catalog):
    user_ids = sorted({event.user_id for event in catalog.interactions})
    item_ids = [item.item_id for item in catalog.items]
    user_index = {value: index for index, value in enumerate(user_ids)}
    item_index = {value: index for index, value in enumerate(item_ids)}
    rows = [user_index[event.user_id] for event in catalog.interactions]
    cols = [item_index[event.item_id] for event in catalog.interactions]
    data = [float(event.weight) for event in catalog.interactions]
    matrix = csr_matrix(
        (data, (rows, cols)),
        shape=(len(user_ids), len(item_ids)),
        dtype=np.float32,
    )
    return matrix, user_ids, item_ids


def _training_catalog(
    source: Catalog,
    train: csr_matrix,
    user_ids: list[str],
    item_ids: list[str],
) -> Catalog:
    original = defaultdict(list)
    for event in source.interactions:
        original[(event.user_id, event.item_id)].append(event)

    interactions: list[Interaction] = []
    train = train.tocoo()
    for user_idx, item_idx, weight in zip(train.row, train.col, train.data):
        user_id = user_ids[int(user_idx)]
        item_id = item_ids[int(item_idx)]
        source_rows = original.get((user_id, item_id), [])
        timestamp = max((row.timestamp for row in source_rows), default=0.0)
        event_name = source_rows[-1].event if source_rows else "click"
        interactions.append(
            Interaction(
                user_id=user_id,
                item_id=item_id,
                event=event_name,
                weight=float(weight),
                timestamp=float(timestamp),
            )
        )
    return Catalog(
        items=list(source.items),
        interactions=interactions,
        query_labels=list(source.query_labels),
        name=f"{source.name}:implicit-eval-train",
    )


def _metrics(model, train, test) -> dict[str, float]:
    report = ranking_metrics_at_k(
        model,
        train,
        test,
        K=10,
        show_progress=False,
        num_threads=1,
    )
    return {key: round(float(value), 6) for key, value in report.items()}


def main() -> None:
    source = build_sample_catalog()
    interactions, user_ids, item_ids = _matrix(source)
    train, test = leave_k_out_split(interactions, K=1, random_state=42)

    reference_catalog = _training_catalog(source, train, user_ids, item_ids)
    models = {
        "xushu_reference": XushuImplicitEvaluationAdapter(reference_catalog, user_ids, item_ids),
        "implicit_als": AlternatingLeastSquares(
            factors=16,
            regularization=0.05,
            alpha=1.0,
            iterations=20,
            use_gpu=False,
            random_state=42,
        ),
        "implicit_bpr": BayesianPersonalizedRanking(
            factors=16,
            learning_rate=0.01,
            regularization=0.01,
            iterations=60,
            use_gpu=False,
            verify_negative_samples=True,
            random_state=42,
        ),
        "implicit_bm25_item_item": BM25Recommender(K=20, K1=1.2, B=0.75),
    }

    results = {}
    for name, model in models.items():
        if name != "xushu_reference":
            model.fit(train, show_progress=False)
        results[name] = _metrics(model, train, test)

    print(
        json.dumps(
            {
                "protocol": "implicit.leave_k_out_split(K=1, random_state=42)",
                "users": len(user_ids),
                "items": len(item_ids),
                "train_events": int(train.nnz),
                "test_events": int(test.nnz),
                "metrics_at_10": results,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
