from __future__ import annotations

from lingjing_harness.domain import Catalog, Item
from lingjing_harness.integrations import flag_embedding
from lingjing_harness.runtime import AgentHarness, AgentMemory, RuntimeBackendConfig


class _Matrix:
    def __init__(self, rows):
        self.rows = rows

    def __matmul__(self, vector):
        return [sum(left * right for left, right in zip(row, vector)) for row in self.rows]


class _FakeFlagModel:
    init_count = 0
    corpora: list[list[str]] = []

    def __init__(self, model_name, **kwargs):
        type(self).init_count += 1
        self.model_name = model_name
        self.kwargs = kwargs

    def encode_corpus(self, corpus):
        rows = list(corpus)
        type(self).corpora.append(rows)
        assert len(rows) == 2
        return _Matrix([[1.0, 0.0], [0.0, 1.0]])

    def encode_queries(self, queries):
        return [[1.0, 0.0] for _ in queries]


class _FakeCollaborativeAdapter:
    init_count = 0

    def __init__(
        self,
        catalog,
        *,
        model="als",
        min_history=1,
        model_kwargs=None,
        fallback=None,
        **_,
    ):
        type(self).init_count += 1
        self.catalog = catalog
        self.model_name = model
        self.min_history = min_history
        self.model_kwargs = dict(model_kwargs or {})
        self.fallback = fallback

    def capability_manifest(self):
        return {
            "backend": "implicit",
            "model": self.model_name,
            "min_history": self.min_history,
            "training_users": 0,
            "training_items": len(self.catalog.items),
            "training_interactions": len(self.catalog.interactions),
            "fallback": "reference",
        }

    def recommend(self, user_id, *, limit=10):
        return []


def _catalog(prefix: str) -> Catalog:
    return Catalog(
        items=[
            Item(f"{prefix}-one", f"{prefix} 第一项", "语义语料一", ["测试", prefix]),
            Item(f"{prefix}-two", f"{prefix} 第二项", "语义语料二", ["测试", prefix]),
            Item(f"{prefix}-hidden", f"{prefix} 下架项", "不可展示", ["测试"], eligible=False),
        ],
        name=f"catalog-{prefix}",
    )


def _config(*, devices: str = "cpu", collaborative: bool = False) -> RuntimeBackendConfig:
    return RuntimeBackendConfig(
        search_backend="flag_embedding",
        search_backend_kwargs={
            "dense_limit": 2,
            "model_kwargs": {"devices": devices},
        },
        recommend_backend="implicit_als" if collaborative else "reference",
        recommend_backend_kwargs={"min_history": 1} if collaborative else {},
    )


def _reset_model() -> None:
    _FakeFlagModel.init_count = 0
    _FakeFlagModel.corpora = []
    _FakeCollaborativeAdapter.init_count = 0


def test_same_memory_harness_rebuild_reuses_semantic_model_and_reencodes_catalog(
    monkeypatch,
    tmp_path,
) -> None:
    _reset_model()
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    memory = AgentMemory(tmp_path / "memory.db")
    first_catalog = _catalog("old")
    second_catalog = _catalog("new")

    first = AgentHarness(first_catalog, memory=memory, backend_config=_config())
    first_adapter = first.tools.search.adapter
    model = first_adapter._model
    second = AgentHarness(second_catalog, memory=memory, backend_config=_config())

    assert _FakeFlagModel.init_count == 1
    assert len(_FakeFlagModel.corpora) == 2
    assert _FakeFlagModel.corpora[-1] == [
        "new 第一项 语义语料一 测试 new",
        "new 第二项 语义语料二 测试 new",
    ]
    assert second.tools.search.adapter is not first_adapter
    assert second.tools.search.adapter._model is model
    assert second.tools.search.adapter.catalog is second_catalog
    assert first_adapter.catalog is first_catalog


def test_semantic_model_cache_is_isolated_by_agent_memory(monkeypatch, tmp_path) -> None:
    _reset_model()
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)

    AgentHarness(
        _catalog("left"),
        memory=AgentMemory(tmp_path / "left.db"),
        backend_config=_config(),
    )
    AgentHarness(
        _catalog("right"),
        memory=AgentMemory(tmp_path / "right.db"),
        backend_config=_config(),
    )

    assert _FakeFlagModel.init_count == 2


def test_backend_scope_change_invalidates_same_memory_semantic_cache(monkeypatch, tmp_path) -> None:
    _reset_model()
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    memory = AgentMemory(tmp_path / "memory.db")

    first = AgentHarness(_catalog("cpu"), memory=memory, backend_config=_config(devices="cpu"))
    second = AgentHarness(_catalog("gpu"), memory=memory, backend_config=_config(devices="cuda:0"))

    assert _FakeFlagModel.init_count == 2
    assert first.tools.search.adapter._model is not second.tools.search.adapter._model


def test_same_memory_cache_keeps_only_one_backend_scope(monkeypatch, tmp_path) -> None:
    _reset_model()
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    memory = AgentMemory(tmp_path / "memory.db")

    AgentHarness(_catalog("first"), memory=memory, backend_config=_config(devices="cpu"))
    AgentHarness(_catalog("second"), memory=memory, backend_config=_config(devices="cuda:0"))
    AgentHarness(_catalog("third"), memory=memory, backend_config=_config(devices="cpu"))

    # The cache is intentionally a single slot. Switching scopes replaces it,
    # so switching back later reloads instead of retaining an unbounded model map.
    assert _FakeFlagModel.init_count == 3


def test_same_memory_combined_rebuild_reuses_search_but_rebuilds_collaborative(
    monkeypatch,
    tmp_path,
) -> None:
    _reset_model()
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    import lingjing_harness.runtime.collaborative_tools as collaborative_tools

    monkeypatch.setattr(
        collaborative_tools,
        "ImplicitRecommendationAdapter",
        _FakeCollaborativeAdapter,
    )
    memory = AgentMemory(tmp_path / "memory.db")
    first = AgentHarness(
        _catalog("old"),
        memory=memory,
        backend_config=_config(collaborative=True),
    )
    search_model = first.tools.search.adapter._model
    collaborative_adapter = first.tools.recommend.adapter

    second = AgentHarness(
        _catalog("new"),
        memory=memory,
        backend_config=_config(collaborative=True),
    )

    assert _FakeFlagModel.init_count == 1
    assert second.tools.search.adapter._model is search_model
    assert _FakeCollaborativeAdapter.init_count == 2
    assert second.tools.recommend.adapter is not collaborative_adapter
    assert second.tools.recommend.adapter.catalog is second.catalog
