from __future__ import annotations

from hashlib import blake2b
import json

import numpy as np
from scipy.sparse import csr_matrix

from implicit.als import AlternatingLeastSquares
from implicit.bpr import BayesianPersonalizedRanking
from implicit.datasets.movielens import get_movielens
from implicit.evaluation import leave_k_out_split
from implicit.nearest_neighbours import BM25Recommender

from lingjing_harness.domain import Catalog, Interaction, Item
from repo_recommender_benchmark import (
    XushuImplicitEvaluationAdapter,
    ranking_metrics,
    training_catalog,
)


MAX_USERS = 200
MIN_POSITIVE_HISTORY = 8
SPLIT_SEED = 42


def _fingerprint_rows(rows) -> str:
    payload = "|".join(str(row) for row in rows)
    return blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()


def _canonicalize_matrix(matrix) -> csr_matrix:
    coo = matrix.tocoo()
    rows = sorted(
        (int(user), int(item), float(value))
        for user, item, value in zip(coo.row, coo.col, coo.data)
    )
    canonical = csr_matrix(
        (
            np.asarray([value for _, _, value in rows], dtype=np.float32),
            (
                np.asarray([user for user, _, _ in rows], dtype=np.int32),
                np.asarray([item for _, item, _ in rows], dtype=np.int32),
            ),
        ),
        shape=matrix.shape,
        dtype=np.float32,
    )
    canonical.sort_indices()
    return canonical


def _matrix_fingerprint(matrix) -> str:
    coo = _canonicalize_matrix(matrix).tocoo()
    return _fingerprint_rows(
        f"{int(user)}:{int(item)}:{float(value):.6f}"
        for user, item, value in zip(coo.row, coo.col, coo.data)
    )


def _dataset() -> tuple[Catalog, csr_matrix, list[str], list[str], dict]:
    movies, movie_user = get_movielens("100k")
    user_item = movie_user.T.tocsr().astype(np.float32)
    user_item.data = np.where(user_item.data >= 4.0, 1.0, 0.0)
    user_item.eliminate_zeros()
    user_item.sort_indices()

    counts = np.asarray(user_item.getnnz(axis=1)).ravel()
    eligible_users = [int(index) for index in np.where(counts >= MIN_POSITIVE_HISTORY)[0]]
    ranked_users = sorted(
        eligible_users,
        key=lambda user_index: (-int(counts[user_index]), user_index),
    )[:MAX_USERS]
    selected = user_item[np.asarray(ranked_users, dtype=np.int32)].tocsr()

    active_items = np.asarray(selected.getnnz(axis=0)).ravel() > 0
    active_items &= np.array([bool(str(title or "").strip()) for title in movies])
    item_indices = [int(index) for index in np.where(active_items)[0]]
    selected = _canonicalize_matrix(selected[:, np.asarray(item_indices, dtype=np.int32)])

    user_ids = [f"ml-user-{index}" for index in ranked_users]
    item_ids = [f"ml-movie-{index}" for index in item_indices]
    items = [
        Item(
            item_id=item_id,
            title=str(movies[movie_index]),
            text=str(movies[movie_index]),
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
    identity = {
        "selected_users_fingerprint": _fingerprint_rows(user_ids),
        "selected_items_fingerprint": _fingerprint_rows(item_ids),
        "input_matrix_fingerprint": _matrix_fingerprint(selected),
    }
    return catalog, selected, user_ids, item_ids, identity


def main() -> None:
    source, interactions, user_ids, item_ids, identity = _dataset()
    train, test = leave_k_out_split(interactions, K=1, random_state=SPLIT_SEED)
    train = _canonicalize_matrix(train)
    test = _canonicalize_matrix(test)
    reference_catalog = training_catalog(source, train, user_ids, item_ids)

    models = {
        "xushu_reference": XushuImplicitEvaluationAdapter(reference_catalog, user_ids, item_ids),
        "implicit_als": AlternatingLeastSquares(
            factors=32,
            regularization=0.05,
            alpha=1.0,
            iterations=20,
            use_gpu=False,
            random_state=SPLIT_SEED,
        ),
        "implicit_bpr": BayesianPersonalizedRanking(
            factors=32,
            learning_rate=0.01,
            regularization=0.01,
            iterations=80,
            num_threads=1,
            use_gpu=False,
            verify_negative_samples=True,
            random_state=SPLIT_SEED,
        ),
        "implicit_bm25_item_item": BM25Recommender(K=40, K1=1.2, B=0.75),
    }

    results = {}
    for name, model in models.items():
        if name != "xushu_reference":
            model.fit(train, show_progress=False)
        results[name] = ranking_metrics(model, train, test, k=10)

    print(
        json.dumps(
            {
                "dataset": "implicit.datasets.movielens.get_movielens('100k')",
                "positive_rule": "rating>=4 converted to binary implicit feedback",
                "selection": {
                    "max_users": MAX_USERS,
                    "minimum_positive_history": MIN_POSITIVE_HISTORY,
                    "tie_break": "positive_count_desc_then_original_user_id_asc",
                    **identity,
                },
                "protocol": "implicit.leave_k_out_split(K=1, random_state=42) + implicit.ranking_metrics_at_k(K=10)",
                "users": len(user_ids),
                "items": len(item_ids),
                "train_events": int(train.nnz),
                "test_events": int(test.nnz),
                "train_fingerprint": _matrix_fingerprint(train),
                "test_fingerprint": _matrix_fingerprint(test),
                "metrics_at_10": results,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
