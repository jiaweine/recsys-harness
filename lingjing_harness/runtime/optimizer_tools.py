from __future__ import annotations

from hashlib import blake2b
import json
import time
from typing import Any, Callable

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_backends import (
    SUPPORTED_OPTIMIZER_BACKENDS,
    optimizer_backend as select_optimizer_backend,
)
from lingjing_harness.algorithms.optimizer_meta import (
    build_routing_context,
    optimizer_dependency_availability,
    optimizer_run_utility,
    rank_optimizer_backends,
)
from .optimizer_meta_memory import OptimizerMetaMemory
from .tools import ToolRegistry as _ToolRegistry


AUTO_OPTIMIZER_BACKEND = "auto"
AVAILABLE_OPTIMIZER_BACKENDS = (*SUPPORTED_OPTIMIZER_BACKENDS, AUTO_OPTIMIZER_BACKEND)
_NATIVE_ARCHIVE_PARENT_LIMIT = 5


def _normalize_registry_backend(raw: str) -> str:
    backend = str(raw or "native").strip().lower()
    if backend not in AVAILABLE_OPTIMIZER_BACKENDS:
        raise ValueError(
            f"unknown optimizer backend: {backend}; expected one of "
            f"{', '.join(AVAILABLE_OPTIMIZER_BACKENDS)}"
        )
    return backend


def _response_surface_capacity(dimensions: list[Any]) -> int:
    total = 0
    for dimension in dimensions:
        if str(getattr(dimension, "kind", "")) == "continuous":
            total += 2
        else:
            total += max(0, len(tuple(getattr(dimension, "choices", ()) or ())) - 1)
    return total


def _estimated_native_evaluation_budget(response_surface_rows: int) -> int:
    """Estimate fixed-backend new-call budget after response-surface reuse.

    Native seeds its first population with up to six already-evaluated response
    surface neighbors, then keeps at most five archive parents in later generations.
    The estimate therefore mirrors the budget shape without running an evaluator.
    """

    if core.MAX_GENERATIONS <= 0 or core.POPULATION_SIZE <= 0:
        return 0
    cached_seed_slots = min(6, max(0, int(response_surface_rows)), core.POPULATION_SIZE)
    first_generation_new = max(0, core.POPULATION_SIZE - cached_seed_slots)
    retained = min(_NATIVE_ARCHIVE_PARENT_LIMIT, core.POPULATION_SIZE)
    refill = max(0, core.POPULATION_SIZE - retained)
    return first_generation_new + max(0, core.MAX_GENERATIONS - 1) * refill


def _stable_event_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


class OptimizerToolRegistry(_ToolRegistry):
    """Tool registry with explicit or cost-aware per-registry optimizer selection.

    ``native`` remains the dependency-light default. ``auto`` is opt-in and only
    chooses which proposal optimizer runs. It cannot change the harness-owned final
    candidate ordering, independent holdout, statistical trust, activation, or
    rollback authority.
    """

    def __init__(self, *args: Any, optimizer_backend: str = "native", **kwargs: Any) -> None:
        requested = _normalize_registry_backend(optimizer_backend)
        if requested == AUTO_OPTIMIZER_BACKEND:
            self.optimizer_backend = requested
        else:
            with select_optimizer_backend(requested) as backend:
                self.optimizer_backend = backend
        super().__init__(*args, **kwargs)
        self.optimizer_meta_memory = OptimizerMetaMemory(self.memory)

    def fork(self) -> "OptimizerToolRegistry":
        clone = super().fork()
        clone.optimizer_backend = self.optimizer_backend
        clone.optimizer_meta_memory = self.optimizer_meta_memory
        return clone

    def inspect_data(self) -> dict[str, Any]:
        result = super().inspect_data()
        return {
            **result,
            "optimizer_backend": self.optimizer_backend,
            "optimizer_backends": list(SUPPORTED_OPTIMIZER_BACKENDS),
            "optimizer_meta_router": {
                "enabled": self.optimizer_backend == AUTO_OPTIMIZER_BACKEND,
                "policies": [AUTO_OPTIMIZER_BACKEND],
                "default_backend": "native",
                "authority": "optimizer_selection_only",
            },
        }

    def _routing_context(self, surface: str):
        engine = self.search if surface == "search" else self.recommend
        dimensions, _ = core._evolution_schema(engine.config)
        evidence_route = "production" if self._business_ready(surface) else "proxy"
        trusted_memory = self._evolution_memory(surface)
        response_surface_rows = _response_surface_capacity(dimensions)
        warm_rows = response_surface_rows + min(len(trusted_memory), core.POPULATION_SIZE)
        return build_routing_context(
            surface=surface,
            evidence_route=evidence_route,
            evaluation_budget=_estimated_native_evaluation_budget(response_surface_rows),
            dimensions=dimensions,
            cache={index: None for index in range(warm_rows)},
            objective_count=2,
            constraint_count=2,
        )

    def _select_auto_backend(self, surface: str):
        history = self.optimizer_meta_memory.read(self.catalog_key, surface)
        context = self._routing_context(surface)
        availability = optimizer_dependency_availability()
        decision = rank_optimizer_backends(
            context,
            history=history,
            availability=availability,
        )
        preflight_failures: dict[str, str] = {}
        selected: str | None = None
        for backend in decision.ranked_backends:
            try:
                # Fixed backend contexts load optional dependencies before any
                # expensive evaluator can be called. A broken optional install can
                # therefore fall through without spending evaluation budget.
                with select_optimizer_backend(backend):
                    pass
            except RuntimeError as exc:
                availability[backend] = False
                preflight_failures[backend] = type(exc).__name__
                continue
            selected = backend
            break
        if selected is None:
            selected = "native"
            availability["native"] = True
        if selected != decision.selected_backend or preflight_failures:
            decision = rank_optimizer_backends(
                context,
                history=history,
                availability=availability,
            )
            selected = decision.selected_backend
        return selected, decision, preflight_failures

    @staticmethod
    def _new_evaluations(result: dict[str, Any]) -> int:
        evolution = result.get("evolution") if isinstance(result.get("evolution"), dict) else {}
        explicit = evolution.get("optimizer_new_evaluations")
        if explicit is not None:
            try:
                return max(0, int(explicit))
            except (TypeError, ValueError):
                pass
        try:
            candidate_count = max(0, int(result.get("candidate_count", 0) or 0))
        except (TypeError, ValueError):
            candidate_count = 0
        surface_count = len(evolution.get("response_surface") or [])
        return max(0, candidate_count - surface_count)

    def _evidence_marker(self) -> dict[str, Any]:
        production_events = list(getattr(self.catalog, "events", ()) or ())
        latest_event = 0.0
        for event in production_events:
            try:
                latest_event = max(latest_event, float(getattr(event, "timestamp", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        return {
            "interactions": len(getattr(self.catalog, "interactions", ()) or ()),
            "query_labels": len(getattr(self.catalog, "query_labels", ()) or ()),
            "production_events": len(production_events),
            "latest_production_timestamp": round(latest_event, 6),
        }

    def _run_auto(
        self,
        surface: str,
        runner: Callable[..., dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        selected, decision, preflight_failures = self._select_auto_backend(surface)
        started = time.perf_counter()
        with select_optimizer_backend(selected):
            result = runner(*args, **kwargs)
        wall_seconds = max(0.0, time.perf_counter() - started)

        new_evaluations = self._new_evaluations(result)
        try:
            objective_delta = float(result.get("objective_delta", 0.0) or 0.0)
        except (TypeError, ValueError):
            objective_delta = 0.0
        utility = optimizer_run_utility(
            initial_best_objective=1.0,
            final_best_objective=1.0 + objective_delta,
            new_evaluations=new_evaluations,
            wall_seconds=wall_seconds,
            evidence_route=decision.context.evidence_route,
        )
        meta = decision.to_dict()
        meta.update(
            {
                "requested_backend": AUTO_OPTIMIZER_BACKEND,
                "selected_backend": selected,
                "preflight_failures": preflight_failures,
                "wall_seconds": round(wall_seconds, 6),
                "new_evaluations": new_evaluations,
                "objective_delta": objective_delta,
                "credit": utility,
                "final_selection": "harness_primary_objective",
                "promotion_authority": "downstream_holdout_and_trust",
            }
        )
        evolution = result.get("evolution")
        if isinstance(evolution, dict):
            updated = dict(evolution)
            updated["optimizer_requested_backend"] = AUTO_OPTIMIZER_BACKEND
            updated["optimizer_selected_backend"] = selected
            updated["optimizer_meta_router"] = meta
            result["evolution"] = updated
        else:
            result["optimizer_meta_router"] = meta

        invocation_id = str(kwargs.get("_invocation_id") or "").strip()
        if (
            utility.get("credit_eligible")
            and not result.get("replayed")
            and not result.get("optimizer_meta_credit")
        ):
            event_payload = {
                "catalog_key": self.catalog_key,
                "surface": surface,
                "context_key": decision.context.context_key,
                "backend": selected,
                "objective_delta": round(objective_delta, 8),
                "candidate_config": result.get("candidate_config"),
                "evidence": self._evidence_marker(),
                "invocation_id": invocation_id or None,
            }
            event_key = (
                f"optimizer-invocation:{self.catalog_key}:{invocation_id}:{surface}"
                if invocation_id
                else _stable_event_key(event_payload)
            )
            result["optimizer_meta_credit"] = self.optimizer_meta_memory.record(
                self.catalog_key,
                surface,
                context_key=decision.context.context_key,
                backend=selected,
                context=decision.context.to_dict(),
                utility=float(utility["utility"]),
                objective_gain=objective_delta,
                evaluator_calls=new_evaluations,
                wall_seconds=wall_seconds,
                event_key=event_key,
                payload={
                    "routing": meta,
                    "evidence": event_payload["evidence"],
                },
            )
        else:
            result["optimizer_meta_credit"] = {
                "recorded": False,
                "reason": "replayed_or_insufficient_optimizer_evidence",
            }
        return result

    def search_evolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.optimizer_backend == AUTO_OPTIMIZER_BACKEND:
            return self._run_auto("search", super().search_evolve, *args, **kwargs)
        with select_optimizer_backend(self.optimizer_backend):
            return super().search_evolve(*args, **kwargs)

    def recommend_evolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.optimizer_backend == AUTO_OPTIMIZER_BACKEND:
            return self._run_auto("recommend", super().recommend_evolve, *args, **kwargs)
        with select_optimizer_backend(self.optimizer_backend):
            return super().recommend_evolve(*args, **kwargs)


__all__ = [
    "OptimizerToolRegistry",
    "AUTO_OPTIMIZER_BACKEND",
    "AVAILABLE_OPTIMIZER_BACKENDS",
]
