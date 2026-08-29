from __future__ import annotations

import json
from statistics import mean, pstdev

import numpy as np
from implicit.als import AlternatingLeastSquares
from implicit.bpr import BayesianPersonalizedRanking
from implicit.evaluation import leave_k_out_split, ranking_metrics_at_k
from implicit.nearest_neighbours import BM25Recommender

from scripts.collaborative_routing_benchmark import (
    MIN_POSITIVE_RATING,
    SPLIT_SEEDS,
    TOP_K,
    _masked_test,
    _positive_user_items,
)


MODEL_ORDER = ("bpr", "als", "bm25")
HISTORY_BUCKETS = (
    ("2-4", 2, 4),
    ("5-9", 5, 9),
    ("10-19", 10, 19),
    ("20+", 20, None),
)


def _build_model(name: str):
    if name == "bpr":
        return BayesianPersonalizedRanking(
            factors=32,
            learning_rate=0.01,
            regularization=0.01,
            iterations=80,
            num_threads=1,
            use_gpu=False,
            verify_negative_samples=True,
            random_state=42,
        )
    if name == "als":
        return AlternatingLeastSquares(
            factors=32,
            regularization=0.05,
            alpha=1.0,
            iterations=20,
            use_gpu=False,
            random_state=42,
        )
    if name == "bm25":
        return BM25Recommender(K=40, K1=1.2, B=0.75)
    raise ValueError(f"unsupported model: {name}")


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


def _run_split(positives, seed: int) -> dict:
    train, test = leave_k_out_split(positives, K=1, random_state=seed)
    train = train.tocsr()
    test = test.tocsr()
    history_counts = np.asarray(train.getnnz(axis=1)).ravel().astype(np.int32)
    evaluation_users = np.flatnonzero(np.asarray(test.getnnz(axis=1)).ravel() > 0)

    models = {}
    for name in MODEL_ORDER:
        model = _build_model(name)
        model.fit(train, show_progress=False)
        models[name] = model

    overall = {name: _metrics(model, train, test) for name, model in models.items()}
    buckets = []
    for label, lower, upper in HISTORY_BUCKETS:
        bucket_test = _masked_test(test, history_counts, lower, upper)
        users = int(np.count_nonzero(np.asarray(bucket_test.getnnz(axis=1)).ravel()))
        if not users:
            continue
        buckets.append(
            {
                "history": label,
                "users": users,
                "models": {
                    name: _metrics(model, train, bucket_test)
                    for name, model in models.items()
                },
            }
        )

    return {
        "seed": seed,
        "train_interactions": int(train.nnz),
        "evaluation_users": int(evaluation_users.size),
        "history": {
            "min": int(history_counts[evaluation_users].min()),
            "median": float(np.median(history_counts[evaluation_users])),
            "max": int(history_counts[evaluation_users].max()),
        },
        "overall": overall,
        "history_buckets": buckets,
    }


def _aggregate(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(mean(values), 6),
        "std": round(pstdev(values), 6),
    }


def _aggregate_models(runs: list[dict]) -> dict[str, dict[str, dict[str, float]]]:
    return {
        model: {
            metric: _aggregate([run["overall"][model][metric] for run in runs])
            for metric in ("precision", "map", "ndcg", "auc")
        }
        for model in MODEL_ORDER
    }


def _aggregate_buckets(runs: list[dict]) -> list[dict]:
    rows = []
    for label, _, _ in HISTORY_BUCKETS:
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
                "users": _aggregate([float(row["users"]) for row in matches]),
                "models": {
                    model: {
                        metric: _aggregate(
                            [row["models"][model][metric] for row in matches]
                        )
                        for metric in ("precision", "map", "ndcg", "auc")
                    }
                    for model in MODEL_ORDER
                },
            }
        )
    return rows


def _winner(models: dict[str, dict[str, dict[str, float]]]) -> dict[str, str]:
    return {
        metric: max(
            MODEL_ORDER,
            key=lambda model: (
                models[model][metric]["mean"],
                models[model]["ndcg"]["mean"],
                models[model]["map"]["mean"],
            ),
        )
        for metric in ("precision", "map", "ndcg", "auc")
    }


def main() -> None:
    _, positives = _positive_user_items()
    runs = [_run_split(positives, seed) for seed in SPLIT_SEEDS]
    aggregate = _aggregate_models(runs)
    buckets = _aggregate_buckets(runs)

    payload = {
        "dataset": "implicit.datasets.movielens.get_movielens('100k')",
        "protocol": "implicit.evaluation.leave_k_out_split(K=1) over fixed split seeds",
        "split_seeds": list(SPLIT_SEEDS),
        "metrics": "implicit.evaluation.ranking_metrics_at_k(K=10)",
        "positive_rating_threshold": MIN_POSITIVE_RATING,
        "users": int(positives.shape[0]),
        "items": int(positives.shape[1]),
        "positive_interactions": int(positives.nnz),
        "model_parameters": {
            "bpr": {
                "factors": 32,
                "learning_rate": 0.01,
                "regularization": 0.01,
                "iterations": 80,
                "random_state": 42,
            },
            "als": {
                "factors": 32,
                "regularization": 0.05,
                "alpha": 1.0,
                "iterations": 20,
                "random_state": 42,
            },
            "bm25": {"K": 40, "K1": 1.2, "B": 0.75},
        },
        "runs": runs,
        "aggregate_models": aggregate,
        "overall_winner_by_metric": _winner(aggregate),
        "aggregate_history_buckets": buckets,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
