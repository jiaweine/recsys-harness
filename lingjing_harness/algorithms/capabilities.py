from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from math import isfinite
from typing import Any, Callable, Mapping, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """One typed vertical capability implementation.

    The registry describes *what implementations exist*. It deliberately does not
    encode which implementation is preferred; selection is left to the evolution
    loop and its domain evaluation / holdout gates.
    """

    group: str
    name: str
    description: str
    handler: Callable[..., Any]
    complexity: float = 1.0


class CapabilityRegistry:
    """Deterministic registry for search/recommendation structural genes."""

    def __init__(self) -> None:
        self._groups: dict[str, dict[str, CapabilitySpec]] = {}
        self._defaults: dict[str, str] = {}

    def register(
        self,
        group: str,
        name: str,
        description: str,
        handler: Callable[..., Any],
        *,
        default: bool = False,
        complexity: float = 1.0,
    ) -> None:
        group = str(group)
        name = str(name)
        bucket = self._groups.setdefault(group, {})
        if name in bucket:
            raise ValueError(f"duplicate capability: {group}/{name}")
        complexity = float(complexity)
        if not isfinite(complexity) or complexity <= 0:
            raise ValueError("capability complexity must be a positive finite number")
        bucket[name] = CapabilitySpec(
            group=group,
            name=name,
            description=description,
            handler=handler,
            complexity=complexity,
        )
        if default or group not in self._defaults:
            self._defaults[group] = name

    def names(self, group: str) -> tuple[str, ...]:
        return tuple(self._groups.get(group, {}).keys())

    def specs(self, group: str) -> tuple[CapabilitySpec, ...]:
        return tuple(self._groups.get(group, {}).values())

    def default(self, group: str) -> str:
        if group not in self._defaults:
            raise KeyError(f"unknown capability group: {group}")
        return self._defaults[group]

    def resolve(self, group: str, name: str | None) -> CapabilitySpec:
        bucket = self._groups.get(group)
        if not bucket:
            raise KeyError(f"unknown capability group: {group}")
        requested = str(name or "")
        if requested in bucket:
            return bucket[requested]
        # Persisted strategies from older versions may contain a removed or
        # unknown choice. Runtime execution fails closed to the owned default;
        # the evolver itself only emits registered choices.
        return bucket[self.default(group)]

    def call(self, group: str, name: str | None, *args: Any, **kwargs: Any) -> Any:
        return self.resolve(group, name).handler(*args, **kwargs)

    def manifest(self) -> dict[str, list[dict[str, Any]]]:
        return {
            group: [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "complexity": spec.complexity,
                    "default": self._defaults.get(group) == spec.name,
                }
                for spec in specs.values()
            ]
            for group, specs in self._groups.items()
        }


CAPABILITIES = CapabilityRegistry()


def capability_field(group: str, default: str):
    """Declare a categorical structural gene on a config dataclass field."""

    return field(
        default=default,
        metadata={
            "evolve_kind": "capability",
            "capability_group": str(group),
        },
    )


def normalize_strategy_config(config: T) -> T:
    """Validate one typed strategy config and canonicalize capability choices.

    Numeric genes are accepted only when finite and inside their declared schema
    bounds. Unknown/removed capability choices fail closed to the registry default.
    The returned dataclass is therefore the *effective* runtime strategy, which
    keeps persisted configuration, diagnostics and execution semantics aligned.
    """

    if not is_dataclass(config):
        raise TypeError("strategy config must be a dataclass instance")

    updates: dict[str, Any] = {}
    for config_field in fields(config):
        metadata = config_field.metadata
        capability_group = str(metadata.get("capability_group") or "")
        evolve_group = str(metadata.get("evolve_group") or "")
        value = getattr(config, config_field.name)

        if capability_group:
            updates[config_field.name] = CAPABILITIES.resolve(
                capability_group,
                str(value or ""),
            ).name
            continue

        if evolve_group:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid numeric strategy gene: {config_field.name}") from exc
            if not isfinite(number):
                raise ValueError(f"non-finite strategy gene: {config_field.name}")
            low = float(metadata.get("min", float("-inf")))
            high = float(metadata.get("max", float("inf")))
            if number < low or number > high:
                raise ValueError(
                    f"strategy gene out of bounds: {config_field.name}={number:g} not in [{low:g}, {high:g}]"
                )
            updates[config_field.name] = number

    return replace(config, **updates)


def config_from_mapping(cls: type[T], raw: Mapping[str, Any]) -> T:
    """Build and validate a strategy config from durable/untrusted mapping data."""

    if not isinstance(raw, Mapping):
        raise TypeError("strategy config payload must be a mapping")
    return normalize_strategy_config(cls(**dict(raw)))
