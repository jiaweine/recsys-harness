from __future__ import annotations

from typing import Any

from lingjing_harness.integrations import (
    ImplicitHybridRecommendationEngine,
    ImplicitRecommendationAdapter,
)
from lingjing_harness.serving import normalize_serving_limit

from .semantic_tools import SearchBackendToolRegistry as _ToolRegistry


SUPPORTED_RECOMMEND_BACKENDS = ("reference", "implicit_als")


class RecommendationBackendToolRegistry(_ToolRegistry):
    """Tool registry with explicit mature recommendation backend selection.

    ``reference`` remains the dependency-light default. Selecting ``implicit_als``
    composes a trained mature collaborative signal with the existing Harness
    ranking/evidence lifecycle. Installing the collaborative extra alone never
    changes runtime behavior.

    This class extends ``SearchBackendToolRegistry`` so one registry can select
    search and recommendation backends independently while sharing the same
    optimizer, strategy memory, segment routing and activation lifecycle.
    """

    def __init__(
        self,
        *args: Any,
        recommend_backend: str = "reference",
        recommend_backend_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        backend = str(recommend_backend).strip().lower()
        if backend not in SUPPORTED_RECOMMEND_BACKENDS:
            raise ValueError(
                f"unknown recommendation backend: {backend}; expected one of "
                f"{', '.join(SUPPORTED_RECOMMEND_BACKENDS)}"
            )
        backend_kwargs = dict(recommend_backend_kwargs or {})
        if backend == "reference" and backend_kwargs:
            raise ValueError("recommend_backend_kwargs require a non-reference recommendation backend")

        self.recommend_backend = backend
        self.recommend_backend_kwargs = backend_kwargs
        # Base constructors validate active strategies and segment portfolios.
        # Delay those checks until the selected recommendation backend is wrapped,
        # otherwise a reference-only check could mark a hybrid strategy fresh.
        self._recommend_backend_initializing = backend != "reference"
        super().__init__(*args, **kwargs)
        self._recommend_backend_initializing = False

        if backend != "reference":
            self._install_recommend_backend()
            self._validate_active_strategies()
            self._refresh_portfolio()
            self._validate_active_portfolio()
            self._specs = self._build_specs()

    def _validate_active_strategies(self) -> None:
        if getattr(self, "_recommend_backend_initializing", False):
            return
        super()._validate_active_strategies()

    def _refresh_portfolio(self) -> None:
        if getattr(self, "_recommend_backend_initializing", False):
            return
        super()._refresh_portfolio()

    def _validate_active_portfolio(self) -> None:
        if getattr(self, "_recommend_backend_initializing", False):
            return
        super()._validate_active_portfolio()

    def _collaborative_options(self) -> tuple[int, dict[str, Any]]:
        options = dict(self.recommend_backend_kwargs)
        collaborative_limit = normalize_serving_limit(options.pop("collaborative_limit", 48))
        if collaborative_limit == 0:
            raise ValueError("collaborative_limit must be greater than zero")
        requested_model = str(options.pop("model", "als")).strip().lower()
        if requested_model != "als":
            raise ValueError("implicit_als backend requires model='als'")
        return collaborative_limit, {"model": "als", **options}

    def _install_recommend_backend(
        self,
        adapter: ImplicitRecommendationAdapter | None = None,
    ) -> None:
        if self.recommend_backend == "reference":
            return
        if self.recommend_backend != "implicit_als":
            raise AssertionError(f"unhandled recommendation backend: {self.recommend_backend}")
        if isinstance(self.recommend, ImplicitHybridRecommendationEngine):
            return

        collaborative_limit, adapter_options = self._collaborative_options()
        collaborative = adapter or ImplicitRecommendationAdapter(
            self.catalog,
            fallback=self.recommend,
            **adapter_options,
        )
        self.recommend = ImplicitHybridRecommendationEngine(
            self.recommend,
            collaborative,
            collaborative_limit=collaborative_limit,
            adapter_options=adapter_options,
        )

    def fork(self) -> "RecommendationBackendToolRegistry":
        clone = super().fork()
        clone.recommend_backend = self.recommend_backend
        clone.recommend_backend_kwargs = dict(self.recommend_backend_kwargs)
        clone._recommend_backend_initializing = False
        return clone

    def replace_catalog(self, catalog: Any) -> None:
        search_adapter = getattr(self.search, "adapter", None)
        rebind_search = getattr(search_adapter, "for_catalog", None)
        rebound_search_adapter = rebind_search(catalog) if callable(rebind_search) else None
        self.__init__(
            catalog,
            self.memory,
            self.network,
            optimizer_backend=self.optimizer_backend,
            search_backend=self.search_backend,
            search_backend_kwargs=self.search_backend_kwargs,
            search_backend_adapter=rebound_search_adapter,
            recommend_backend=self.recommend_backend,
            recommend_backend_kwargs=self.recommend_backend_kwargs,
        )

    def inspect_data(self) -> dict[str, Any]:
        result = super().inspect_data()
        if isinstance(self.recommend, ImplicitHybridRecommendationEngine):
            backend_manifest = self.recommend.backend_manifest()
        else:
            backend_manifest = {
                "backend": "reference",
                "mode": "owned_ranking",
                "ranking_owner": "harness",
                "collaborative_owner": "harness",
                "cold_start_owner": "harness",
            }
        return {
            **result,
            "recommend_backend": backend_manifest,
            "recommend_backends": list(SUPPORTED_RECOMMEND_BACKENDS),
        }

    def recommend_evolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        adapter = (
            self.recommend.adapter
            if isinstance(self.recommend, ImplicitHybridRecommendationEngine)
            else None
        )
        result = super().recommend_evolve(*args, **kwargs)
        # The inherited activation path may reconstruct the owned engine from the
        # trusted config. Re-wrap it with the already-trained collaborative model
        # rather than retraining or changing runtime semantics after activation.
        if self.recommend_backend != "reference" and not isinstance(
            self.recommend, ImplicitHybridRecommendationEngine
        ):
            self._install_recommend_backend(adapter=adapter)
            self._refresh_portfolio()
        return result


__all__ = ["SUPPORTED_RECOMMEND_BACKENDS", "RecommendationBackendToolRegistry"]
