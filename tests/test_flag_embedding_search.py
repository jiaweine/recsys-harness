from __future__ import annotations

import pytest

from lingjing_harness.domain import Catalog, Item
from lingjing_harness.integrations import FlagEmbeddingSearchAdapter
from lingjing_harness.integrations import flag_embedding


class _Matrix:
    def __init__(self, rows):
        self.rows = rows

    def __matmul__(self, vector):
        return [sum(left * right for left, right in zip(row, vector)) for row in self.rows]


class _FakeFlagModel:
    init_args = None
    corpus = None
    queries = None

    def __init__(self, model_name, **kwargs):
        type(self).init_args = (model_name, kwargs)

    def encode_corpus(self, corpus):
        type(self).corpus = list(corpus)
        return _Matrix([[1.0, 0.0], [0.0, 1.0]])

    def encode_queries(self, queries):
        type(self).queries = list(queries)
        assert queries == ["夜跑装备"]
        return [[1.0, 0.0]]


def _catalog() -> Catalog:
    return Catalog(
        items=[
            Item("run", "夜跑反光腰包", "轻量反光跑步装备", ["跑步", "夜跑"]),
            Item("audio", "运动蓝牙耳机", "防汗无线耳机", ["耳机", "运动"]),
            Item("hidden", "下架跑鞋", "不可召回", ["跑步"], eligible=False),
        ]
    )


def test_flag_embedding_adapter_delegates_encoding_and_filters_ineligible(monkeypatch):
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    adapter = FlagEmbeddingSearchAdapter(
        _catalog(),
        model_kwargs={"devices": "cpu", "batch_size": 8},
    )

    results = adapter.search("夜跑装备", limit=2)
    expected_model = "BAAI/bge-small-zh-" + "v" + "1.5"

    assert [row["id"] for row in results] == ["run", "audio"]
    assert all(row["backend"] == "flag_embedding" for row in results)
    assert all(row["id"] != "hidden" for row in results)
    assert _FakeFlagModel.init_args[0] == expected_model
    assert _FakeFlagModel.init_args[1]["devices"] == "cpu"
    assert _FakeFlagModel.init_args[1]["batch_size"] == 8
    assert _FakeFlagModel.corpus == [
        "夜跑反光腰包 轻量反光跑步装备 跑步 夜跑",
        "运动蓝牙耳机 防汗无线耳机 耳机 运动",
    ]
    assert _FakeFlagModel.queries == ["夜跑装备"]


def test_flag_embedding_adapter_handles_empty_query_and_limit(monkeypatch):
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    adapter = FlagEmbeddingSearchAdapter(_catalog())

    _FakeFlagModel.queries = None
    assert adapter.search("", limit=10) == []
    assert adapter.search("夜跑装备", limit=0) == []
    assert adapter.search("夜跑装备", limit=-3) == []
    assert _FakeFlagModel.queries is None
    assert adapter.capability_manifest()["corpus_items"] == 2


@pytest.mark.parametrize("raw", [1.5, "2", True])
def test_flag_embedding_adapter_rejects_invalid_limit_before_query_encoding(monkeypatch, raw):
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    adapter = FlagEmbeddingSearchAdapter(_catalog())

    _FakeFlagModel.queries = None
    with pytest.raises(ValueError, match="limit must be an integer"):
        adapter.search("夜跑装备", limit=raw)  # type: ignore[arg-type]
    assert _FakeFlagModel.queries is None


def test_flag_embedding_dependency_error_is_lazy(monkeypatch):
    def unavailable():
        raise RuntimeError("semantic extra required")

    monkeypatch.setattr(flag_embedding, "_load_flag_model", unavailable)

    with pytest.raises(RuntimeError, match="semantic extra required"):
        FlagEmbeddingSearchAdapter(_catalog())
