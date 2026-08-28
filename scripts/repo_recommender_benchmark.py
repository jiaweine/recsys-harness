from __future__ import annotations

from collections import defaultdict

import numpy as np
from scipy.sparse import csr_matrix

from implicit.evaluation import ranking_metrics_at_k

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction


class XushuImplicitEvaluationAdapter:
    """Expose the repo-owned recommender through implicit's evaluation API."""

    def __init__(self, catalog: Catalog, user_ids: list[str], item_ids: list[str]) -> None:
        self.catalog = catalog
        self.user_ids = user_ids
        self.item_ids = item_ids
        self.item_index = {item_id: index for index, item_id in enumerate(item_ids)}
        self.engine = RecommendationEngine(catalog)

    def recommend(self, userid, user_items, N=10, **_kwargs):
        user_array = np.atleast_1d(userid)
        rows = []
        scores = []
        for raw_user in user_array:
            user_id = self.user_ids[int(raw_user)]
            ranked = self.engine.recommend(user_id, limit=N)
            ids = np.array(
                [self.item_index[row["id"]] for row in ranked if row["id"] in self.item_index],
                dtype=np.int32,
            )
            values = np.array(
                [float(row.get("score", 0.0)) for row in ranked if row["id"] in self.item_index],
                dtype=np.float64,
            )
            if len(ids) < N:
                pad = N - len(ids)
                ids = np.pad(ids, (0, pad), constant_values=-1)
                values = np.pad(values, (0, pad), constant_values=-np.inf)
            rows.append(ids[:N])
            scores.append(values[:N])
        if np.isscalar(userid):
            return rows[0], scores[0]
        return np.vstack(rows), np.vstack(scores)


def training_catalog(
    source: Catalog,
    train: csr_matrix,
    user_ids: list[str],
    item_ids: list[str],
) -> Catalog:
    original = defaultdict(list)
    for event in source.interactions:
        original[(event.user_id, event.item_id)].append(event)

    interactions: list[Interaction] = []
    coo = train.tocoo()
    for user_idx, item_idx, weight in zip(coo.row, coo.col, coo.data):
        user_id = user_ids[int(user_idx)]
        item_id = item_ids[int(item_idx)]
        source_rows = original.get((user_id, item_id), [])
        timestamp = max((row.timestamp for row in source_rows), default=0.0)
        event_name = source_rows[-1].event if source_rows else "positive"
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


def ranking_metrics(model, train, test, *, k: int = 10) -> dict[str, float]:
    report = ranking_metrics_at_k(
        model,
        train,
        test,
        K=k,
        show_progress=False,
        num_threads=1,
    )
    return {key: round(float(value), 6) for key, value in report.items()}


__all__ = ["XushuImplicitEvaluationAdapter", "training_catalog", "ranking_metrics"]
