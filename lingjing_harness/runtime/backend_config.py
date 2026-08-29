from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, ClassVar, Mapping

from lingjing_harness.algorithms.optimizer_backends import SUPPORTED_OPTIMIZER_BACKENDS
from lingjing_harness.domain import Catalog

from .backend_memory import (
    BackendScopedMemory,
    backend_strategy_scope,
    runtime_invocation_scope,
)
from .collaborative_tools import (
    SUPPORTED_RECOMMEND_BACKENDS,
    RecommendationBackendToolRegistry,
)
from .memory import AgentMemory
from .semantic_tools import SUPPORTED_SEARCH_BACKENDS
from .tools import ToolRegistry


SEARCH_BACKEND_ENV = "LINGJING_SEARCH_BACKEND"
RECOMMEND_BACKEND_ENV = "LINGJING_RECOMMEND_BACKEND"
OPTIMIZER_BACKEND_ENV = "LINGJING_OPTIMIZER_BACKEND"
SEARCH_BACKEND_KWARGS_ENV = "LINGJING_SEARCH_BACKEND_KWARGS"
RECOMMEND_BACKEND_KWARGS_ENV = "LINGJING_RECOMMEND_BACKEND_KWARGS"


def _backend_name(
    value: Any,
    *,
    field_name: str,
    supported: tuple[str, ...],
    default: str,
) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in supported:
        raise ValueError(
            f"unknown {field_name}: {normalized}; expected one of {', '.join(supported)}"
        )
    return normalized


def _validate_json_mapping(value: Mapping[Any, Any], *, field_name: str) -> dict[str, Any]:
    normalized = {str(key): item for key, item in value.items()}
    try:
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain JSON-compatible values") from exc
    return normalized


def _backend_kwargs(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return _validate_json_mapping(value, field_name=field_name)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a JSON object")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return _validate_json_mapping(decoded, field_name=field_name)


@dataclass(slots=True)
class RuntimeBackendConfig:
    """Explicit process-level serving/evolution backend selection.

    Optional dependencies remain lazy. Merely installing FlagEmbedding, implicit,
    or Optuna never changes runtime behavior; a backend must be selected through
    this config, environment variables, or CLI flags.
    """

    SEARCH_BACKENDS: ClassVar[tuple[str, ...]] = tuple(SUPPORTED_SEARCH_BACKENDS)
    RECOMMEND_BACKENDS: ClassVar[tuple[str, ...]] = tuple(SUPPORTED_RECOMMEND_BACKENDS)
    OPTIMIZER_BACKENDS: ClassVar[tuple[str, ...]] = tuple(SUPPORTED_OPTIMIZER_BACKENDS)

    search_backend: str = "reference"
    recommend_backend: str = "reference"
    optimizer_backend: str = "native"
    search_backend_kwargs: dict[str, Any] = field(default_factory=dict)
    recommend_backend_kwargs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.search_backend = _backend_name(
            self.search_backend,
            field_name="search backend",
            supported=self.SEARCH_BACKENDS,
            default="reference",
        )
        self.recommend_backend = _backend_name(
            self.recommend_backend,
            field_name="recommendation backend",
            supported=self.RECOMMEND_BACKENDS,
            default="reference",
        )
        self.optimizer_backend = _backend_name(
            self.optimizer_backend,
            field_name="optimizer backend",
            supported=self.OPTIMIZER_BACKENDS,
            default="native",
        )
        self.search_backend_kwargs = _backend_kwargs(
            self.search_backend_kwargs,
            field_name="search backend kwargs",
        )
        self.recommend_backend_kwargs = _backend_kwargs(
            self.recommend_backend_kwargs,
            field_name="recommendation backend kwargs",
        )
        if self.search_backend == "reference" and self.search_backend_kwargs:
            raise ValueError("search backend kwargs require a non-reference search backend")
        if self.recommend_backend == "reference" and self.recommend_backend_kwargs:
            raise ValueError(
                "recommendation backend kwargs require a non-reference recommendation backend"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimeBackendConfig":
        source: Mapping[str, str] = os.environ if env is None else env
        return cls(
            search_backend=source.get(SEARCH_BACKEND_ENV, "reference"),
            recommend_backend=source.get(RECOMMEND_BACKEND_ENV, "reference"),
            optimizer_backend=source.get(OPTIMIZER_BACKEND_ENV, "native"),
            search_backend_kwargs=_backend_kwargs(
                source.get(SEARCH_BACKEND_KWARGS_ENV),
                field_name=SEARCH_BACKEND_KWARGS_ENV,
            ),
            recommend_backend_kwargs=_backend_kwargs(
                source.get(RECOMMEND_BACKEND_KWARGS_ENV),
                field_name=RECOMMEND_BACKEND_KWARGS_ENV,
            ),
        )

    @property
    def is_dependency_light_default(self) -> bool:
        return (
            self.search_backend == "reference"
            and self.recommend_backend == "reference"
            and self.optimizer_backend == "native"
            and not self.search_backend_kwargs
            and not self.recommend_backend_kwargs
        )

    @property
    def strategy_scopes(self) -> dict[str, str]:
        return {
            "search": backend_strategy_scope(
                "search",
                self.search_backend,
                self.search_backend_kwargs,
            ),
            "recommend": backend_strategy_scope(
                "recommend",
                self.recommend_backend,
                self.recommend_backend_kwargs,
            ),
        }

    def manifest(self) -> dict[str, Any]:
        return {
            "search_backend": self.search_backend,
            "recommend_backend": self.recommend_backend,
            "optimizer_backend": self.optimizer_backend,
            "search_backend_kwargs": dict(self.search_backend_kwargs),
            "recommend_backend_kwargs": dict(self.recommend_backend_kwargs),
            "strategy_scopes": dict(self.strategy_scopes),
        }


def build_runtime_tools(
    catalog: Catalog,
    memory: Any = None,
    network: Any = None,
    *,
    config: RuntimeBackendConfig | None = None,
    env: Mapping[str, str] | None = None,
):
    """Build one registry for the selected process-level backend contract."""

    resolved = config or RuntimeBackendConfig.from_env(env)
    if resolved.is_dependency_light_default:
        return ToolRegistry(catalog, memory, network)

    base_memory = memory
    if isinstance(base_memory, BackendScopedMemory):
        base_memory = base_memory.base_memory
    if base_memory is None:
        base_memory = AgentMemory()

    scopes = resolved.strategy_scopes
    invocation_scope = runtime_invocation_scope(scopes["search"], scopes["recommend"])
    tool_memory: Any = base_memory
    if scopes["search"] or scopes["recommend"]:
        tool_memory = BackendScopedMemory(
            base_memory,
            search_scope=scopes["search"],
            recommend_scope=scopes["recommend"],
            invocation_scope=invocation_scope,
        )

    registry = RecommendationBackendToolRegistry(
        catalog,
        tool_memory,
        network,
        optimizer_backend=resolved.optimizer_backend,
        search_backend=resolved.search_backend,
        search_backend_kwargs=resolved.search_backend_kwargs,
        recommend_backend=resolved.recommend_backend,
        recommend_backend_kwargs=resolved.recommend_backend_kwargs,
    )
    registry.runtime_backend_config = resolved.manifest()
    return registry


__all__ = [
    "RuntimeBackendConfig",
    "build_runtime_tools",
    "SEARCH_BACKEND_ENV",
    "RECOMMEND_BACKEND_ENV",
    "OPTIMIZER_BACKEND_ENV",
    "SEARCH_BACKEND_KWARGS_ENV",
    "RECOMMEND_BACKEND_KWARGS_ENV",
]
