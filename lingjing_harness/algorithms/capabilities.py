from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


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
        bucket[name] = CapabilitySpec(
            group=group,
            name=name,
            description=description,
            handler=handler,
            complexity=float(complexity),
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
