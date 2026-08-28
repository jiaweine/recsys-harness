from __future__ import annotations

from hashlib import blake2b
import json

import numpy as np
from scipy.sparse import csr_matrix

from implicit.als import AlternatingLeastSquares
from implicit.bpr import BayesianPersonalizedRanking
from implicit.datasets.movielens import get_movielens
from implicit.evaluation import train_test_split
from implicit.nearest_neighbours import BM25Recommender

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item
from repo_recommender_benchmark import (
    XushuImplicitEvaluationAdapter,
    _metrics,
    _training_catalog,
)


MAX_USERS = 200
MIN_POSITIVE_HISTORY = 8
SPLIT_SEED = 42
TRAIN_PERCENTAGE = 0.8


def _fingerprint_rows(rows) -> str:
    payload = "|".join(str(row) for row in rows)
    return blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()


def _matrix_fingerprint(matrix) -> str:
    coo = matrix.tocoo()
    rows = sorted(
        (int(user), int(item), float(value))
        for user, item, value in zip(coo.row, coo.col, coo.data)
    )
    return _fingerprint_rows(f"{user}:{item}:{value:.6f}" for user, item, value in rows)


def _canonicalize_matrix(matrix) -> csr_matrix:
    """Return a CSR whose row/item traversal order is explicit and stable."""

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


def _dataset() -> tuple[Catalog, object, list[str], list[str], dict]:
    movies, movie_user = get_movielens("100k")
    user_item = movie_user.T.tocsr().astype(np.float32)
    user_item.data = np.where(user_item.data >= 4.0, user_item.data, 0.0)
    user_item.eliminate_zeros()
    user_item.sort_indices()

    counts = np.asarray(user_item.getnnz(axis=1)).ravel()
    eligible_users = [
        int(user_index)
        for user_index in np.where(counts >= MIN_POSITIVE_HISTORY)[0]
    ]
    # Make ties part of the benchmark contract instead of inheriting NumPy's
    # unstable argsort ordering for users with identical interaction counts.
    ranked_users = sorted(
        eligible_users,
        key=lambda user_index: (-int(counts[user_index]), user_index),
    )[:MAX_USERS]
    selected = user_item[np.asarray(ranked_users, dtype=np.int32)].tocsr()
    selected.sort_indices()

    active_items = np.asarray(selected.getnnz(axis=0)).ravel() > 0
    active_items &= np.array([bool(str(title or "").strip()) for title in movies])
    item_indices = [int(index) for index in np.where(active_items)[0]]
    selected = selected[:, np.asarray(item_indices, dtype=np.int32)].tocsr()
    selected = _canonicalize_matrix(selected)

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


def _mean(values: list[float]) -> float:
    return round(float(np.mean(values)), 6) if values else 0.0


def _reference_diagnostics(
    catalog: Catalog,
    test,
    user_ids: list[str],
    item_ids: list[str],
) -> dict:
    """Measure the existing engine's owned signals without changing its algorithm."""

    engine = RecommendationEngine(catalog)
    target_graph: list[float] = []
    target_profile: list[float] = []
    graph_positive_share: list[float] = []
    graph_saturated_share: list[float] = []
    profile_positive_share: list[float] = []
    target_candidates = 0
    target_events = 0

    for user_index, user_id in enumerate(user_ids):
        target_indices = [int(index) for index in test.getrow(user_index).indices]
        if not target_indices:
            continue
        target_events += len(target_indices)
        prepared = engine.prepare(user_id)
        if not prepared:
            continue

        graph_values = [float(row["graph"]) for row in prepared]
        profile_values = [float(row["profile_fit"]) for row in prepared]
        graph_positive_share.append(
            sum(value > 0.0 for value in graph_values) / len(graph_values)
        )
        graph_saturated_share.append(
            sum(value >= 0.999999 for value in graph_values) / len(graph_values)
        )
        profile_positive_share.append(
            sum(value > 0.0 for value in profile_values) / len(profile_values)
        )

        prepared_by_id = {row["item"].item_id: row for row in prepared}
        for target_index in target_indices:
            target_id = item_ids[target_index]
            target = prepared_by_id.get(target_id)
            if target is None:
                continue
            target_candidates += 1
            target_graph.append(float(target["graph"]))
            target_profile.append(float(target["profile_fit"]))

    return {
        "target_events": target_events,
        "target_candidate_coverage": round(target_candidates / max(1, target_events), 6),
        "target_graph_nonzero_share": round(
            sum(value > 0.0 for value in target_graph) / max(1, len(target_graph)),
            6,
        ),
        "target_graph_saturated_share": round(
            sum(value >= 0.999999 for value in target_graph) / max(1, len(target_graph)),
            6,
        ),
        "target_graph_mean": _mean(target_graph),
        "target_profile_mean": _mean(target_profile),
        "candidate_graph_positive_share_mean": _mean(graph_positive_share),
        "candidate_graph_saturated_share_mean": _mean(graph_saturated_share),
        "candidate_profile_positive_share_mean": _mean(profile_positive_share),
    }


def main() -> None:
    source, interactions, user_ids, item_ids, identity = _dataset()
    train, test = train_test_split(
        interactions,
        train_percentage=TRAIN_PERCENTAGE,
        random_state=SPLIT_SEED,
    )
    train = _canonicalize_matrix(train)
    test = _canonicalize_matrix(test)
    reference_catalog = _training_catalog(source, train, user_ids, item_ids)

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
        results[name] = _metrics(model, train, test)

    print(
        json.dumps(
            {
                "dataset": "implicit.datasets.movielens.get_movielens('100k')",
                "positive_rule": "rating>=4",
                "selection": {
                    "max_users": MAX_USERS,
                    "minimum_positive_history": MIN_POSITIVE_HISTORY,
                    "tie_break": "interaction_count_desc_then_original_user_id_asc",
                    **identity,
                },
                "protocol": "canonicalized implicit.train_test_split(train_percentage=0.8, random_state=42); BPR num_threads=1",
                "split_seed": SPLIT_SEED,
                "train_fingerprint": _matrix_fingerprint(train),
                "split_fingerprint": _matrix_fingerprint(test),
                "users": len(user_ids),
                "items": len(item_ids),
                "train_events": int(train.nnz),
                "test_events": int(test.nnz),
                "metrics_at_10": results,
                "reference_signal_diagnostics": _reference_diagnostics(
                    reference_catalog,
                    test,
                    user_ids,
                    item_ids,
                ),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
