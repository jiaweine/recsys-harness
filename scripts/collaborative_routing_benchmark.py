from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
from implicit.datasets.movielens import get_movielens
from implicit.evaluation import leave_k_out_split, ranking_metrics_at_k

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.integrations import ImplicitRecommendationAdapter


TOP_K = 10
MIN_POSITIVE_RATING = 4.0
MIN_USER_POSITIVES = 3
THRESHOLDS = (1, 3, 5, 8, 12, 20, 32, 64)


def _positive_user_items():
    movies, movie_user_ratings = get_movielens("100k")
    titled = np.asarray([bool(str(title or "").strip()) for title in movies])
    item_source_indices = np.flatnonzero(titled)

    ratings = movie_user_ratings.T.tocsr()[:, item_source_indices].astype(np.float32)
    ratings.data = (ratings.data >= MIN_POSITIVE_RATING).astype(np.float32)
    ratings.eliminate_zeros()

    positive_counts = np.asarray(ratings.getnnz(axis=1)).ravel()
    user_source_indices = np.flatnonzero(positive_counts >= MIN_USER_POSITIVES)
    ratings = ratings[user_source_indices].tocsr()

    titles = [str(movies[index]).strip() for index in item_source_indices]
    return titles, ratings


def _catalog_from_train(titles: list[str], train) -> tuple[Catalog, list[str]]:
    item_counts = np.asarray(train.getnnz(axis=0)).ravel().astype(np.float64)
    max_count = max(1.0, float(item_counts.max(initial=0)))
    items = [
        Item(
            item_id=f"m{item_index:04d}",
            title=title,
            popularity=float(item_counts[item_index] / max_count),
            quality=0.5,
            freshness=0.5,
        )
        for item_index, title in enumerate(titles)
    ]
    user_ids = [f"u{user_index:04d}" for user_index in range(train.shape[0])]

    coo = train.tocoo()
    interactions = [
        Interaction(
            user_id=user_ids[int(user_index)],
            item_id=items[int(item_index)].item_id,
            event="like",
            weight=float(weight),
            timestamp=1.0,
        )
        for user_index, item_index, weight in zip(coo.row, coo.col, coo.data)
    ]
    return Catalog(items=items, interactions=interactions, name="MovieLens 100K positive train"), user_ids


def _rank_arrays(rows: list[dict], item_index: dict[str, int], *, k: int) -> tuple[np.ndarray, np.ndarray]:
    if len(rows) < k:
        raise RuntimeError(f"backend returned only {len(rows)} rows for top-{k} evaluation")
    ids = np.asarray([item_index[str(row["id"])] for row in rows[:k]], dtype=np.int32)
    scores = np.asarray(
        [float(row.get("score", k - rank)) for rank, row in enumerate(rows[:k])],
        dtype=np.float32,
    )
    return ids, scores


@dataclass
class CachedRoutingModel:
    collaborative_ids: np.ndarray
    collaborative_scores: np.ndarray
    reference_ids: np.ndarray
    reference_scores: np.ndarray
    history_counts: np.ndarray
    threshold: int

    def recommend(self, userid, user_items, N: int = 10):
        del user_items
        userids = np.asarray(userid, dtype=np.int32).reshape(-1)
        if N > self.collaborative_ids.shape[1]:
            raise ValueError("requested N exceeds cached recommendation width")
        use_collaborative = self.history_counts[userids] >= self.threshold
        ids = np.where(
            use_collaborative[:, None],
            self.collaborative_ids[userids, :N],
            self.reference_ids[userids, :N],
        )
        scores = np.where(
            use_collaborative[:, None],
            self.collaborative_scores[userids, :N],
            self.reference_scores[userids, :N],
        )
        return ids.astype(np.int32, copy=False), scores.astype(np.float32, copy=False)


def _metrics(model, train, test) -> dict[str, float]:
    values = ranking_metrics_at_k(
        model,
        train,
        test,
        K=TOP_K,
        show_progress=False,
        num_threads=1,
    )
    return {key: round(float(value), 6) for key, value in values.items()}


def _masked_test(test, history_counts: np.ndarray, lower: int, upper: int | None):
    mask = history_counts >= lower
    if upper is not None:
        mask &= history_counts <= upper
    return test.multiply(mask.astype(np.float32)[:, None]).tocsr()


def main() -> None:
    titles, positives = _positive_user_items()
    train, test = leave_k_out_split(positives, K=1, random_state=42)
    train = train.tocsr()
    test = test.tocsr()

    catalog, user_ids = _catalog_from_train(titles, train)
    item_index = {item.item_id: index for index, item in enumerate(catalog.items)}
    history_counts = np.asarray(train.getnnz(axis=1)).ravel().astype(np.int32)
    evaluation_users = np.flatnonzero(np.asarray(test.getnnz(axis=1)).ravel() > 0)
    if not evaluation_users.size:
        raise RuntimeError("MovieLens split produced no evaluation users")

    reference = RecommendationEngine(catalog)
    collaborative = ImplicitRecommendationAdapter(
        catalog,
        model="bpr",
        min_history=1,
        fallback=reference,
    )

    user_count = train.shape[0]
    collaborative_ids = np.full((user_count, TOP_K), -1, dtype=np.int32)
    collaborative_scores = np.zeros((user_count, TOP_K), dtype=np.float32)
    reference_ids = np.full((user_count, TOP_K), -1, dtype=np.int32)
    reference_scores = np.zeros((user_count, TOP_K), dtype=np.float32)

    for user_index in evaluation_users:
        user_id = user_ids[int(user_index)]
        ref_ids, ref_scores = _rank_arrays(reference.recommend(user_id, limit=TOP_K), item_index, k=TOP_K)
        cf_ids, cf_scores = _rank_arrays(collaborative.recommend(user_id, limit=TOP_K), item_index, k=TOP_K)
        reference_ids[user_index] = ref_ids
        reference_scores[user_index] = ref_scores
        collaborative_ids[user_index] = cf_ids
        collaborative_scores[user_index] = cf_scores

    max_history = int(history_counts[evaluation_users].max())
    thresholds = sorted(set(THRESHOLDS + (max_history + 1,)))
    sweep = []
    for threshold in thresholds:
        model = CachedRoutingModel(
            collaborative_ids,
            collaborative_scores,
            reference_ids,
            reference_scores,
            history_counts,
            threshold,
        )
        route_share = float(np.mean(history_counts[evaluation_users] >= threshold))
        sweep.append(
            {
                "threshold": threshold,
                "collaborative_user_share": round(route_share, 6),
                **_metrics(model, train, test),
            }
        )

    pure_bpr = CachedRoutingModel(
        collaborative_ids,
        collaborative_scores,
        reference_ids,
        reference_scores,
        history_counts,
        1,
    )
    pure_reference = CachedRoutingModel(
        collaborative_ids,
        collaborative_scores,
        reference_ids,
        reference_scores,
        history_counts,
        max_history + 1,
    )
    buckets = []
    for label, lower, upper in (
        ("2-4", 2, 4),
        ("5-9", 5, 9),
        ("10-19", 10, 19),
        ("20+", 20, None),
    ):
        bucket_test = _masked_test(test, history_counts, lower, upper)
        users = int(np.count_nonzero(np.asarray(bucket_test.getnnz(axis=1)).ravel()))
        if not users:
            continue
        buckets.append(
            {
                "history": label,
                "users": users,
                "bpr": _metrics(pure_bpr, train, bucket_test),
                "reference": _metrics(pure_reference, train, bucket_test),
            }
        )

    best = max(sweep, key=lambda row: (row["ndcg"], row["map"], row["auc"]))
    payload = {
        "dataset": "implicit.datasets.movielens.get_movielens('100k')",
        "protocol": "implicit.evaluation.leave_k_out_split(K=1, random_state=42)",
        "metrics": "implicit.evaluation.ranking_metrics_at_k(K=10)",
        "positive_rating_threshold": MIN_POSITIVE_RATING,
        "users": int(train.shape[0]),
        "items": int(train.shape[1]),
        "train_interactions": int(train.nnz),
        "evaluation_users": int(evaluation_users.size),
        "history": {
            "min": int(history_counts[evaluation_users].min()),
            "median": float(np.median(history_counts[evaluation_users])),
            "max": max_history,
        },
        "sweep": sweep,
        "history_buckets": buckets,
        "best_by_ndcg_then_map_auc": best,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
