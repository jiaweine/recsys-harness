from __future__ import annotations

import pytest

from lingjing_harness.algorithms import RecommendationEngine, SearchEngine
from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.serving import normalize_serving_limit, normalize_serving_score


class _IndexLike:
    def __index__(self) -> int:
        return 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (5, 5),
        (0, 0),
        (-4, 0),
        (_IndexLike(), 3),
    ],
)
def test_shared_serving_limit_normalization(raw, expected):
    assert normalize_serving_limit(raw) == expected


@pytest.mark.parametrize("raw", [True, False, 1.5, "3", None])
def test_shared_serving_limit_rejects_non_integer_values(raw):
    with pytest.raises(ValueError, match="limit must be an integer"):
        normalize_serving_limit(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, 1.0),
        (0.25, 0.25),
        ("0.75", 0.75),
        (-3, -3.0),
    ],
)
def test_shared_serving_score_normalizes_finite_numbers(raw, expected):
    assert normalize_serving_score(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [True, False, None, "not-a-score", float("nan"), float("inf"), float("-inf")],
)
def test_shared_serving_score_rejects_invalid_values(raw):
    with pytest.raises(ValueError, match="score must be a finite number"):
        normalize_serving_score(raw)


def test_owned_search_zero_limit_short_circuits_before_prepare(monkeypatch):
    engine = SearchEngine(build_sample_catalog())

    def should_not_prepare(query: str):
        pytest.fail("zero-limit search must not prepare candidates")

    monkeypatch.setattr(engine, "prepare", should_not_prepare)
    assert engine.search("露营灯", limit=0) == []
    assert engine.search("露营灯", limit=-2) == []


@pytest.mark.parametrize("raw", [1.5, "2", True])
def test_owned_search_rejects_invalid_limit_before_prepare(monkeypatch, raw):
    engine = SearchEngine(build_sample_catalog())

    def should_not_prepare(query: str):
        pytest.fail("invalid-limit search must fail before candidate preparation")

    monkeypatch.setattr(engine, "prepare", should_not_prepare)
    with pytest.raises(ValueError, match="limit must be an integer"):
        engine.search("露营灯", limit=raw)  # type: ignore[arg-type]


def test_owned_search_rank_prepared_keeps_limit_contract():
    engine = SearchEngine(build_sample_catalog())
    prepared = engine.prepare("露营灯")
    assert engine.rank_prepared(prepared, limit=0) == []
    with pytest.raises(ValueError, match="limit must be an integer"):
        engine.rank_prepared(prepared, limit=1.5)  # type: ignore[arg-type]


def test_owned_recommend_zero_limit_short_circuits_before_prepare(monkeypatch):
    engine = RecommendationEngine(build_sample_catalog())

    def should_not_prepare(user_id: str):
        pytest.fail("zero-limit recommend must not prepare candidates")

    monkeypatch.setattr(engine, "prepare", should_not_prepare)
    assert engine.recommend("u-lin", limit=0) == []
    assert engine.recommend("u-lin", limit=-2) == []


@pytest.mark.parametrize("raw", [1.5, "2", True])
def test_owned_recommend_rejects_invalid_limit_before_prepare(monkeypatch, raw):
    engine = RecommendationEngine(build_sample_catalog())

    def should_not_prepare(user_id: str):
        pytest.fail("invalid-limit recommend must fail before candidate preparation")

    monkeypatch.setattr(engine, "prepare", should_not_prepare)
    with pytest.raises(ValueError, match="limit must be an integer"):
        engine.recommend("u-lin", limit=raw)  # type: ignore[arg-type]


def test_owned_recommend_rank_prepared_keeps_limit_contract():
    engine = RecommendationEngine(build_sample_catalog())
    prepared = engine.prepare("u-lin")
    assert engine.rank_prepared(prepared, limit=0) == []
    with pytest.raises(ValueError, match="limit must be an integer"):
        engine.rank_prepared(prepared, limit=1.5)  # type: ignore[arg-type]
