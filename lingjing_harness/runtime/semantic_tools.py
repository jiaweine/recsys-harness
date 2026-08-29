from __future__ import annotations

from typing import Any

from lingjing_harness.integrations import FlagEmbeddingHybridSearchEngine, FlagEmbeddingSearchAdapter
from lingjing_harness.serving import normalize_serving_limit
from .optimizer_tools import OptimizerToolRegistry as _ToolRegistry


SUPPORTED_SEARCH_BACKENDS = ("reference", "flag_embedding")


class SearchBackendToolRegistry(_ToolRegistry):
    """Tool registry with an explicit search retrieval backend.

    The default remains the dependency-light reference engine. Selecting
    ``flag_embedding`` is explicit and composes the mature dense retriever with
    the existing Harness ranking/evidence lifecycle; merely installing the
    semantic extra never changes runtime behavior.
    """

    def __init__(
        self,
        *args: Any,
        search_backend: str = "reference",
        search_backend_kwargs: dict[str, Any] | None = None,
        search_backend_adapter: FlagEmbeddingSearchAdapter | None = None,
        **kwargs: Any,
    ) -> None:
        backend = str(search_backend).strip().lower()
        if backend not in SUPPORTED_SEARCH_BACKENDS:
            raise ValueError(
                f"unknown search backend: {backend}; expected one of "
                f"{', '.join(SUPPORTED_SEARCH_BACKENDS)}"
            )
        backend_kwargs = dict(search_backend_kwargs or {})
        if backend == "reference" and backend_kwargs:
            raise ValueError("search_backend_kwargs require a non-reference search backend")
        if search_backend_adapter is not None and backend != "flag_embedding":
            raise ValueError("search_backend_adapter requires search_backend='flag_embedding'")

        self.search_backend = backend
        self.search_backend_kwargs = backend_kwargs
        # The inherited constructor validates active strategies and builds the
        # segment portfolio. For a semantic backend those checks must happen only
        # after the reference engine has been wrapped, otherwise a reference-only
        # validation could mark the semantic strategy fresh for the shared TTL.
        self._search_backend_initializing = backend != "reference"
        super().__init__(*args, **kwargs)
        self._search_backend_initializing = False

        if backend != "reference":
            self._install_search_backend(adapter=search_backend_adapter)
            self._validate_active_strategies()
            self._refresh_portfolio()
            self._validate_active_portfolio()
            self._specs = self._build_specs()

    def _validate_active_strategies(self) -> None:
        if getattr(self, "_search_backend_initializing", False):
            return
        super()._validate_active_strategies()

    def _refresh_portfolio(self) -> None:
        if getattr(self, "_search_backend_initializing", False):
            return
        super()._refresh_portfolio()

    def _validate_active_portfolio(self) -> None:
        if getattr(self, "_search_backend_initializing", False):
            return
        super()._validate_active_portfolio()

    def _semantic_options(self) -> tuple[int, dict[str, Any]]:
        options = dict(self.search_backend_kwargs)
        dense_limit = normalize_serving_limit(options.pop("dense_limit", 48))
        if dense_limit == 0:
            raise ValueError("dense_limit must be greater than zero")
        return dense_limit, options

    def _install_search_backend(self, adapter: FlagEmbeddingSearchAdapter | None = None) -> None:
        if self.search_backend == "reference":
            return
        if self.search_backend != "flag_embedding":
            raise AssertionError(f"unhandled search backend: {self.search_backend}")
        if isinstance(self.search, FlagEmbeddingHybridSearchEngine):
            return
        dense_limit, adapter_kwargs = self._semantic_options()
        semantic_adapter = adapter or FlagEmbeddingSearchAdapter(self.catalog, **adapter_kwargs)
        if semantic_adapter.catalog is not self.catalog:
            raise ValueError("semantic adapter must be rebound to the current catalog")
        self.search = FlagEmbeddingHybridSearchEngine(
            self.search,
            semantic_adapter,
            dense_limit=dense_limit,
        )

    def fork(self) -> "SearchBackendToolRegistry":
        clone = super().fork()
        clone.search_backend = self.search_backend
        clone.search_backend_kwargs = dict(self.search_backend_kwargs)
        clone._search_backend_initializing = False
        return clone

    def replace_catalog(self, catalog: Any) -> None:
        adapter = (
            self.search.adapter.for_catalog(catalog)
            if isinstance(self.search, FlagEmbeddingHybridSearchEngine)
            else None
        )
        self.__init__(
            catalog,
            self.memory,
            self.network,
            optimizer_backend=self.optimizer_backend,
            search_backend=self.search_backend,
            search_backend_kwargs=self.search_backend_kwargs,
            search_backend_adapter=adapter,
        )

    def inspect_data(self) -> dict[str, Any]:
        result = super().inspect_data()
        if isinstance(self.search, FlagEmbeddingHybridSearchEngine):
            backend_manifest = self.search.backend_manifest()
        else:
            backend_manifest = {
                "backend": "reference",
                "ranking_owner": "harness",
                "semantic_owner": "harness",
            }
        return {
            **result,
            "search_backend": backend_manifest,
            "search_backends": list(SUPPORTED_SEARCH_BACKENDS),
        }

    def search_evolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        adapter = (
            self.search.adapter
            if isinstance(self.search, FlagEmbeddingHybridSearchEngine)
            else None
        )
        result = super().search_evolve(*args, **kwargs)
        # The inherited activation path intentionally reconstructs the owned
        # SearchEngine from the trusted config. Re-wrap that new reference engine
        # with the already-loaded semantic adapter instead of reloading the model.
        if self.search_backend != "reference" and not isinstance(
            self.search, FlagEmbeddingHybridSearchEngine
        ):
            self._install_search_backend(adapter=adapter)
            self._refresh_portfolio()
        return result


__all__ = ["SUPPORTED_SEARCH_BACKENDS", "SearchBackendToolRegistry"]
