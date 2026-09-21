from lingjing_harness.algorithms import RecommendationEngine, SearchEngine
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry


def _catalog() -> Catalog:
    return Catalog(
        items=[
            Item(
                item_id=f"item-{index}",
                title=f"Item {index}",
                text=f"shared semantic text {index}",
                categories=["common", f"group-{index % 3}"],
                popularity=float(index + 1),
                quality=0.8,
                freshness=0.7,
            )
            for index in range(12)
        ],
        name="shared-item-vector-test",
    )


def test_tool_registry_shares_exact_item_vector_snapshot():
    catalog = _catalog()
    registry = ToolRegistry(catalog, AgentMemory(":memory:"))

    assert registry.search._vectors is registry.recommend._vectors  # noqa: SLF001
    assert set(registry.search._vectors) == {item.item_id for item in catalog.items}  # noqa: SLF001


def test_direct_engine_construction_remains_self_contained():
    catalog = _catalog()
    search = SearchEngine(catalog)
    recommend = RecommendationEngine(catalog)

    assert search._vectors is not recommend._vectors  # noqa: SLF001
    assert search._vectors == recommend._vectors  # noqa: SLF001


def test_config_clones_keep_shared_vector_snapshot():
    catalog = _catalog()
    registry = ToolRegistry(catalog, AgentMemory(":memory:"))

    search_clone = registry.search.with_config(registry.search.config)
    recommend_clone = registry.recommend.with_config(registry.recommend.config)

    assert search_clone._vectors is registry.search._vectors  # noqa: SLF001
    assert recommend_clone._vectors is registry.recommend._vectors  # noqa: SLF001
