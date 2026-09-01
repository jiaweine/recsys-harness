from __future__ import annotations

from contextvars import ContextVar
import json
from math import isfinite
from typing import Any

from . import optimizer_observation_memory as observation_memory


_ROUTING_SNAPSHOT_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "optimizer_observation_snapshot_context",
    default=None,
)
_SNAPSHOT_STATE_ATTR = "_optimizer_observation_snapshot_states"
_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _decode_latest_rows(rows: list[Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row["config"] or "{}")
            constraints = json.loads(row["constraints"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(config, dict):
            continue
        observations.append(
            {
                "status": "optimizer_observation",
                "config": config,
                "objective": float(row["score"]),
                "feasible": bool(row["feasible"]),
                "source": row["source"],
                "generation": int(row["generation"]),
                "feasibility_basis": row["feasibility_basis"],
                "constraints": constraints if isinstance(constraints, dict) else {},
                "seen_count": int(row["seen_count"]),
                "updated_at": float(row["updated_at"]),
            }
        )
    return observations


def _decode_history_rows(rows: list[Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for row in rows:
        try:
            config = json.loads(row["config"] or "{}")
            constraints = json.loads(row["constraints"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(config, dict):
            continue
        observations.append(
            {
                "status": "optimizer_observation_history",
                "config_key": str(row["config_key"]),
                "config": config,
                "objective": float(row["score"]),
                "feasible": bool(row["feasible"]),
                "source": row["source"],
                "generation": int(row["generation"]),
                "feasibility_basis": row["feasibility_basis"],
                "constraints": constraints if isinstance(constraints, dict) else {},
                "observed_at": float(row["observed_at"]),
            }
        )
    return observations


def _read_atomic_snapshot(
    memory: Any,
    catalog_key: str,
    domain: str,
) -> dict[str, Any]:
    """Materialize latest rows and paid history from one SQLite read snapshot."""

    domain = str(domain or "").strip()
    if domain not in {"search", "recommend"}:
        return {
            "observations": [],
            "history": [],
            "latest_newest_at": 0.0,
            "history_newest_at": 0.0,
        }

    observation_memory._ensure_optimizer_observation_table(memory)
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            connection.execute("begin")
            latest_rows = connection.execute(
                """
                select config,score,feasible,source,generation,feasibility_basis,
                       constraints,seen_count,updated_at
                from agent_optimizer_observations
                where catalog_key=? and domain=?
                order by updated_at desc, config_key asc limit ?
                """,
                (
                    catalog_key,
                    domain,
                    observation_memory.OPTIMIZER_OBSERVATION_READ_BUDGET,
                ),
            ).fetchall()
            history_rows = connection.execute(
                """
                select config_key,config,score,feasible,source,generation,feasibility_basis,
                       constraints,observed_at
                from agent_optimizer_observation_history
                where catalog_key=? and domain=?
                order by observed_at desc, id desc limit ?
                """,
                (
                    catalog_key,
                    domain,
                    observation_memory.OPTIMIZER_OBSERVATION_HISTORY_READ_BUDGET,
                ),
            ).fetchall()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            memory._close(connection)

    observations = _decode_latest_rows(list(latest_rows))
    history = _decode_history_rows(list(history_rows))
    return {
        "observations": observations,
        "history": history,
        "latest_newest_at": max(
            (_finite_float(row.get("updated_at")) or 0.0 for row in observations),
            default=0.0,
        ),
        "history_newest_at": max(
            (_finite_float(row.get("observed_at")) or 0.0 for row in history),
            default=0.0,
        ),
    }


def _snapshot_states(registry: Any) -> dict[str, dict[str, Any]]:
    states = getattr(registry, _SNAPSHOT_STATE_ATTR, None)
    if not isinstance(states, dict):
        states = {}
        setattr(registry, _SNAPSHOT_STATE_ATTR, states)
    return states


def _snapshot_for(
    memory: Any,
    catalog_key: str,
    domain: str,
) -> dict[str, Any] | None:
    context = _ROUTING_SNAPSHOT_CONTEXT.get()
    if not isinstance(context, dict):
        return None
    surface = str(context.get("surface") or "")
    if str(domain) != surface:
        return None

    snapshots = context.setdefault("snapshots", {})
    key = (str(catalog_key), str(domain))
    snapshot = snapshots.get(key)
    if not isinstance(snapshot, dict):
        snapshot = _read_atomic_snapshot(memory, str(catalog_key), str(domain))
        snapshots[key] = snapshot
        registry = context.get("registry")
        if registry is not None:
            _snapshot_states(registry)[surface] = {
                "status": "coherent_snapshot",
                "latest_rows": len(snapshot.get("observations") or []),
                "history_rows": len(snapshot.get("history") or []),
                "latest_newest_at": float(snapshot.get("latest_newest_at", 0.0) or 0.0),
                "history_newest_at": float(snapshot.get("history_newest_at", 0.0) or 0.0),
                "new_evaluator_calls": 0,
            }
    return snapshot


def _bounded_limit(value: Any, *, upper: int) -> int:
    if isinstance(value, bool):
        return 1
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = 1
    return max(1, min(int(upper), limit))


def install_optimizer_observation_snapshot(
    agent_memory_cls: type,
    optimizer_registry_cls: type,
) -> None:
    """Give one routing decision a coherent latest/history observation snapshot."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_observations = agent_memory_cls.optimizer_observations
    original_history = agent_memory_cls.optimizer_observation_history

    def optimizer_observations_with_snapshot(
        self: Any,
        catalog_key: str,
        domain: str,
        *,
        limit: int = observation_memory.OPTIMIZER_OBSERVATION_READ_BUDGET,
    ) -> list[dict[str, Any]]:
        snapshot = _snapshot_for(self, catalog_key, domain)
        if snapshot is None:
            return original_observations(
                self,
                catalog_key,
                domain,
                limit=limit,
            )
        bounded = _bounded_limit(
            limit,
            upper=observation_memory.OPTIMIZER_OBSERVATION_READ_BUDGET,
        )
        return [dict(row) for row in list(snapshot.get("observations") or [])[:bounded]]

    def optimizer_observation_history_with_snapshot(
        self: Any,
        catalog_key: str,
        domain: str,
        *,
        limit: int = observation_memory.OPTIMIZER_OBSERVATION_HISTORY_READ_BUDGET,
    ) -> list[dict[str, Any]]:
        snapshot = _snapshot_for(self, catalog_key, domain)
        if snapshot is None:
            return original_history(
                self,
                catalog_key,
                domain,
                limit=limit,
            )
        bounded = _bounded_limit(
            limit,
            upper=observation_memory.OPTIMIZER_OBSERVATION_HISTORY_READ_BUDGET,
        )
        return [dict(row) for row in list(snapshot.get("history") or [])[:bounded]]

    agent_memory_cls.optimizer_observations = optimizer_observations_with_snapshot
    agent_memory_cls.optimizer_observation_history = optimizer_observation_history_with_snapshot

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_observation_snapshot(self: Any, surface: str):
        token = _ROUTING_SNAPSHOT_CONTEXT.set(
            {
                "registry": self,
                "surface": str(surface),
                "snapshots": {},
            }
        )
        try:
            return original_routing_context(self, surface)
        finally:
            _ROUTING_SNAPSHOT_CONTEXT.reset(token)

    optimizer_registry_cls._routing_context = routing_context_with_observation_snapshot

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_observation_snapshot(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        states = getattr(self, _SNAPSHOT_STATE_ATTR, None)
        router.update(
            {
                "optimizer_observation_snapshot": "single_sqlite_read_transaction",
                "optimizer_observation_snapshot_scope": "one_routing_decision",
                "optimizer_observation_snapshot_latest_budget": observation_memory.OPTIMIZER_OBSERVATION_READ_BUDGET,
                "optimizer_observation_snapshot_history_budget": observation_memory.OPTIMIZER_OBSERVATION_HISTORY_READ_BUDGET,
                "optimizer_observation_snapshot_states": (
                    dict(states) if isinstance(states, dict) else {}
                ),
                "optimizer_observation_snapshot_authority": "routing_descriptor_only",
                "optimizer_observation_snapshot_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_observation_snapshot
    _INSTALLED = True


__all__ = [
    "install_optimizer_observation_snapshot",
]
