from __future__ import annotations

import json
import os
from dataclasses import dataclass
from statistics import mean, pstdev

import numpy as np
from implicit.datasets.movielens import get_movielens
from implicit.evaluation import leave_k_out_split, ranking_metrics_at_k

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.integrations import ImplicitRecommendationAdapter


TOP_K = 10
MIN_POSITIVE_RATING = 4.0
MIN_USER_POSITIVES = 3
SPLIT_SEEDS = (17, 42, 91)
THRESHOLDS = (1, 2, 3, 4, 5, 6, 8, 12, 20, 32, 64)
SUPPORTED_MODELS = ("bpr", "als", "bm25")


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
    return {key: float(value) for key, value in values.items()}


def _masked_test(test, history_counts: np.ndarray, lower: int, upper: int | None):
    mask = history_counts >= lower
    if upper is not None:
        mask &= history_counts <= upper
    masked = test.multiply(mask.astype(np.float32)[:, None]).tocsr()
    masked.eliminate_zeros()
    return masked


def _run_split(titles: list[str], positives, seed: int, model_name: str) -> dict:
    train, test = leave_k_out_split(positives, K=1, random_state=seed)
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
        model=model_name,
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
    sweep = []
    for threshold in THRESHOLDS:
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
                "collaborative_user_share": route_share,
                **_metrics(model, train, test),
            }
        )

    pure_collaborative = CachedRoutingModel(
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
                "collaborative": _metrics(pure_collaborative, train, bucket_test),
                "reference": _metrics(pure_reference, train, bucket_test),
            }
        )

    return {
        "seed": seed,
        "train_interactions": int(train.nnz),
        "evaluation_users": int(evaluation_users.size),
        "history": {
            "min": int(history_counts[evaluation_users].min()),
            "median": float(np.median(history_counts[evaluation_users])),
            "max": max_history,
        },
        "sweep": sweep,
        "history_buckets": buckets,
    }


def _aggregate_metric(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(mean(values), 6),
        "std": round(pstdev(values), 6),
    }


def _aggregate_sweeps(runs: list[dict]) -> list[dict]:
    rows = []
    for threshold in THRESHOLDS:
        matches = [next(row for row in run["sweep"] if row["threshold"] == threshold) for run in runs]
        rows.append(
            {
                "threshold": threshold,
                "collaborative_user_share": _aggregate_metric(
                    [row["collaborative_user_share"] for row in matches]
                ),
                "precision": _aggregate_metric([row["precision"] for row in matches]),
                "map": _aggregate_metric([row["map"] for row in matches]),
                "ndcg": _aggregate_metric([row["ndcg"] for row in matches]),
                "auc": _aggregate_metric([row["auc"] for row in matches]),
            }
        )
    return rows


def _aggregate_buckets(runs: list[dict]) -> list[dict]:
    labels = ("2-4", "5-9", "10-19", "20+")
    rows = []
    for label in labels:
        matches = [
            next((row for row in run["history_buckets"] if row["history"] == label), None)
            for run in runs
        ]
        matches = [row for row in matches if row is not None]
        if not matches:
            continue
        rows.append(
            {
                "history": label,
                "users": _aggregate_metric([float(row["users"]) for row in matches]),
                "collaborative": {
                    metric: _aggregate_metric([row["collaborative"][metric] for row in matches])
                    for metric in ("precision", "map", "ndcg", "auc")
                },
                "reference": {
                    metric: _aggregate_metric([row["reference"][metric] for row in matches])
                    for metric in ("precision", "map", "ndcg", "auc")
                },
            }
        )
    return rows


def main() -> None:
    model_name = str(os.environ.get("COLLABORATIVE_MODEL", "bpr")).strip().lower()
    if model_name not in SUPPORTED_MODELS:
        raise SystemExit(
            f"COLLABORATIVE_MODEL must be one of {', '.join(SUPPORTED_MODELS)}; got {model_name!r}"
        )

    titles, positives = _positive_user_items()
    runs = [_run_split(titles, positives, seed, model_name) for seed in SPLIT_SEEDS]
    sweep = _aggregate_sweeps(runs)
    buckets = _aggregate_buckets(runs)
    best = max(
        sweep,
        key=lambda row: (row["ndcg"]["mean"], row["map"]["mean"], row["auc"]["mean"]),
    )

    payload = {
        "dataset": "implicit.datasets.movielens.get_movielens('100k')",
        "protocol": "implicit.evaluation.leave_k_out_split(K=1) over fixed split seeds",
        "split_seeds": list(SPLIT_SEEDS),
        "metrics": "implicit.evaluation.ranking_metrics_at_k(K=10)",
        "collaborative_model": model_name,
        "positive_rating_threshold": MIN_POSITIVE_RATING,
        "users": int(positives.shape[0]),
        "items": int(positives.shape[1]),
        "positive_interactions": int(positives.nnz),
        "runs": runs,
        "aggregate_sweep": sweep,
        "aggregate_history_buckets": buckets,
        "best_by_mean_ndcg_then_map_auc": best,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
