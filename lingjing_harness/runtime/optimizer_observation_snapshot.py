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
                "config_key": str(row["config_key"]),
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
        decoded = {
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
        try:
            keys = row.keys()
        except AttributeError:
            keys = ()
        if "id" in keys:
            decoded["observation_commit_id"] = max(0, int(row["id"]))
        observations.append(decoded)
    return observations


def _same_paid_observation_version(
    latest: dict[str, Any],
    history: dict[str, Any],
) -> bool:
    """Require the current row to match the exact history version claiming its ID."""

    latest_at = _finite_float(latest.get("updated_at"))
    history_at = _finite_float(history.get("observed_at"))
    latest_objective = _finite_float(latest.get("objective"))
    history_objective = _finite_float(history.get("objective"))
    if (
        latest_at is None
        or history_at is None
        or latest_objective is None
        or history_objective is None
    ):
        return False
    if abs(latest_at - history_at) > 1e-12:
        return False
    if abs(latest_objective - history_objective) > 1e-12:
        return False
    return (
        str(latest.get("config_key") or "") == str(history.get("config_key") or "")
        and bool(latest.get("feasible")) == bool(history.get("feasible"))
        and str(latest.get("source") or "") == str(history.get("source") or "")
        and int(latest.get("generation", 0) or 0)
        == int(history.get("generation", 0) or 0)
        and str(latest.get("feasibility_basis") or "")
        == str(history.get("feasibility_basis") or "")
        and dict(latest.get("constraints") or {})
        == dict(history.get("constraints") or {})
    )


def _read_atomic_snapshot(
    memory: Any,
    catalog_key: str,
    domain: str,
) -> dict[str, Any]:
    """Materialize one current-set-anchored latest/history SQLite snapshot."""

    domain = str(domain or "").strip()
    if domain not in {"search", "recommend"}:
        return {
            "observations": [],
            "history": [],
            "history_clock_rows": [],
            "history_rows_read": 0,
            "history_filtered_rows": 0,
            "latest_newest_at": 0.0,
            "history_newest_at": 0.0,
        }

    observation_memory._ensure_optimizer_observation_table(memory)
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            connection.execute("begin")
            latest_rows = observation_memory._latest_observation_rows(
                connection,
                catalog_key=catalog_key,
                domain=domain,
                limit=observation_memory.OPTIMIZER_OBSERVATION_READ_BUDGET,
            )
            history_rows = connection.execute(
                """
                select id,config_key,config,score,feasible,source,generation,feasibility_basis,
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
    raw_history = _decode_history_rows(list(history_rows))
    latest_history_by_config: dict[str, dict[str, Any]] = {}
    history_clock_rows: list[dict[str, Any]] = []
    for row in raw_history:
        observation_id = max(0, int(row.get("observation_commit_id", 0) or 0))
        config_key = str(row.get("config_key") or "")
        if observation_id > 0:
            current = latest_history_by_config.get(config_key)
            current_id = max(
                0,
                int((current or {}).get("observation_commit_id", 0) or 0),
            )
            if observation_id > current_id:
                latest_history_by_config[config_key] = row
            history_clock_rows.append(
                {
                    "observation_id": observation_id,
                    "config_key": config_key,
                    "observed_at": float(row.get("observed_at", 0.0) or 0.0),
                }
            )
    for row in observations:
        history_version = latest_history_by_config.get(
            str(row.get("config_key") or "")
        )
        if history_version is None or not _same_paid_observation_version(
            row,
            history_version,
        ):
            continue
        observation_id = max(
            0,
            int(history_version.get("observation_commit_id", 0) or 0),
        )
        if observation_id > 0:
            row["observation_commit_id"] = observation_id

    latest_config_keys = {
        str(row.get("config_key") or "").strip()
        for row in observations
        if str(row.get("config_key") or "").strip()
    }
    history = [
        row
        for row in raw_history
        if str(row.get("config_key") or "").strip() in latest_config_keys
    ]
    return {
        "observations": observations,
        "history": history,
        "history_clock_rows": history_clock_rows,
        "history_rows_read": len(raw_history),
        "history_filtered_rows": max(0, len(raw_history) - len(history)),
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
                "history_rows_read": int(snapshot.get("history_rows_read", 0) or 0),
                "history_filtered_rows": int(
                    snapshot.get("history_filtered_rows", 0) or 0
                ),
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
                "optimizer_observation_snapshot_history_scope": "current_latest_config_set",
                "optimizer_observation_snapshot_history_match": "exact_durable_config_key",
                "optimizer_observation_snapshot_commit_identity": "history_autoincrement_id_in_same_read_transaction",
                "optimizer_observation_snapshot_latest_commit_match": "exact_config_key_timestamp_and_evaluator_payload",
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
