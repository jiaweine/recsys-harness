from __future__ import annotations

import json

import numpy as np

from implicit.als import AlternatingLeastSquares
from implicit.bpr import BayesianPersonalizedRanking
from implicit.datasets.movielens import get_movielens
from implicit.evaluation import leave_k_out_split

from lingjing_harness.domain import Catalog, Interaction, Item
from repo_recommender_benchmark import (
    XushuImplicitEvaluationAdapter,
    _metrics,
    _training_catalog,
)


MAX_USERS = 200
MIN_POSITIVE_HISTORY = 8


def _dataset() -> tuple[Catalog, object, list[str], list[str]]:
    movies, movie_user = get_movielens("100k")
    user_item = movie_user.T.tocsr().astype(np.float32)
    user_item.data = np.where(user_item.data >= 4.0, user_item.data, 0.0)
    user_item.eliminate_zeros()

    counts = np.asarray(user_item.getnnz(axis=1)).ravel()
    eligible_users = np.where(counts >= MIN_POSITIVE_HISTORY)[0]
    ranked_users = eligible_users[np.argsort(counts[eligible_users])[::-1]][:MAX_USERS]
    selected = user_item[ranked_users].tocsr()

    active_items = np.asarray(selected.getnnz(axis=0)).ravel() > 0
    active_items &= np.array([bool(str(title or "").strip()) for title in movies])
    item_indices = np.where(active_items)[0]
    selected = selected[:, item_indices].tocsr()

    user_ids = [f"ml-user-{int(index)}" for index in ranked_users]
    item_ids = [f"ml-movie-{int(index)}" for index in item_indices]
    items = [
        Item(
            item_id=item_id,
            title=str(movies[int(movie_index)]),
            text=str(movies[int(movie_index)]),
        )
        for item_id, movie_index in zip(item_ids, item_indices)
    ]

    coo = selected.tocoo()
    interactions = [
        Interaction(
            user_id=user_ids[int(user_index)],
            item_id=item_ids[int(item_index)],
            event="positive",
            weight=float(weight),
            timestamp=0.0,
        )
        for user_index, item_index, weight in zip(coo.row, coo.col, coo.data)
    ]
    catalog = Catalog(
        items=items,
        interactions=interactions,
        name="MovieLens 100K positive-preference slice",
    )
    return catalog, selected, user_ids, item_ids


def main() -> None:
    source, interactions, user_ids, item_ids = _dataset()
    train, test = leave_k_out_split(interactions, K=1, random_state=42)
    reference_catalog = _training_catalog(source, train, user_ids, item_ids)

    models = {
        "xushu_reference": XushuImplicitEvaluationAdapter(reference_catalog, user_ids, item_ids),
        "implicit_als": AlternatingLeastSquares(
            factors=32,
            regularization=0.05,
            alpha=1.0,
            iterations=20,
            use_gpu=False,
            random_state=42,
        ),
        "implicit_bpr": BayesianPersonalizedRanking(
            factors=32,
            learning_rate=0.01,
            regularization=0.01,
            iterations=80,
            use_gpu=False,
            verify_negative_samples=True,
            random_state=42,
        ),
    }

    results = {}
    for name, model in models.items():
        if name != "xushu_reference":
            model.fit(train, show_progress=False)
        results[name] = _metrics(model, train, test)

    print(
        json.dumps(
            {
                "dataset": "implicit.datasets.movielens.get_movielens('100k')",
                "positive_rule": "rating>=4",
                "selection": {
                    "max_users": MAX_USERS,
                    "minimum_positive_history": MIN_POSITIVE_HISTORY,
                },
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
