"""Stable ToolRegistry import surface with production lifecycle policy."""

from lingjing_harness.algorithms import RecommendConfig, SearchConfig
from lingjing_harness.production import request_groups
from .tools_production import ToolRegistry as _ProductionToolRegistry


class ToolRegistry(_ProductionToolRegistry):
    """Production registry with a conservative automatic-action evidence floor."""

    MIN_ACTIVE_BUSINESS_REQUESTS = 8

    def _business_ready(self, domain: str) -> bool:
        return bool(
            self.catalog.reward_spec
            and len(request_groups(self.catalog.events, surface=domain))
            >= self.MIN_ACTIVE_BUSINESS_REQUESTS
        )

    def fork(self) -> "ToolRegistry":
        clone = object.__new__(ToolRegistry)
        clone.catalog = self.catalog
        clone.memory = self.memory
        clone.network = self.network
        clone.catalog_key = self.catalog_key
        clone.rollback_events = []
        clone.search = self.search.with_config(clone._load_config("search", SearchConfig))
        clone.recommend = self.recommend.with_config(clone._load_config("recommend", RecommendConfig))
        clone._specs = clone._build_specs()
        return clone


__all__ = ["ToolRegistry"]
