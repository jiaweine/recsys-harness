from __future__ import annotations

from typing import Any

from lingjing_harness.algorithms.optimizer_backends import (
    SUPPORTED_OPTIMIZER_BACKENDS,
    optimizer_backend as select_optimizer_backend,
)
from .tools import ToolRegistry as _ToolRegistry


class OptimizerToolRegistry(_ToolRegistry):
    """Tool registry with an explicit per-registry evolution optimizer.

    The default production registry remains dependency-light and native. Projects
    that install the optional optimizer extra can choose Optuna explicitly; the
    same backend then applies to global and segment evolution within that registry.
    """

    def __init__(self, *args: Any, optimizer_backend: str = "native", **kwargs: Any) -> None:
        with select_optimizer_backend(optimizer_backend) as backend:
            self.optimizer_backend = backend
        super().__init__(*args, **kwargs)

    def fork(self) -> "OptimizerToolRegistry":
        clone = super().fork()
        clone.optimizer_backend = self.optimizer_backend
        return clone

    def inspect_data(self) -> dict[str, Any]:
        result = super().inspect_data()
        return {
            **result,
            "optimizer_backend": self.optimizer_backend,
            "optimizer_backends": list(SUPPORTED_OPTIMIZER_BACKENDS),
        }

    def search_evolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with select_optimizer_backend(self.optimizer_backend):
            return super().search_evolve(*args, **kwargs)

    def recommend_evolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with select_optimizer_backend(self.optimizer_backend):
            return super().recommend_evolve(*args, **kwargs)


__all__ = ["OptimizerToolRegistry"]
