from __future__ import annotations

from dataclasses import replace
from hashlib import blake2b
import json
from math import isfinite
import time
from typing import Any, Iterable

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_meta import describe_optimizer_landscape
from lingjing_harness.algorithms.optimizer_observations import (
    consume_optimizer_observations,
    install_optimizer_observation_capture,
)

from . import backend_memory


OPTIMIZER_OBSERVATION_READ_BUDGET = 48
OPTIMIZER_OBSERVATION_RETENTION = 96
OPTIMIZER_OBSERVATION_HISTORY_RETENTION = 2 * OPTIMIZER_OBSERVATION_RETENTION
OPTIMIZER_OBSERVATION_HISTORY_READ_BUDGET = OPTIMIZER_OBSERVATION_HISTORY_RETENTION
OPTIMIZER_OBSERVATION_HISTORY_RETENTION_ORDER = "autoincrement_commit_order"
OPTIMIZER_OBSERVATION_LATEST_SELECTION_ORDER = "retained_history_commit_order_then_updated_at"
_INSTALLED = False


def _config_key(config: dict[str, Any]) -> str:
    raw = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _ensure_optimizer_observation_table(memory: Any) -> None:
    sql = """
    create table if not exists agent_optimizer_observations(
      catalog_key text not null,
      domain text not null,
      config_key text not null,
      config text not null,
      score real not null,
      feasible integer not null,
      source text not null,
      generation integer not null,
      feasibility_basis text not null,
      constraints text not null,
      seen_count integer not null default 1,
      created_at real not null,
      updated_at real not null,
      primary key(catalog_key,domain,config_key)
    );
    create index if not exists idx_agent_optimizer_observations_lookup
      on agent_optimizer_observations(catalog_key,domain,updated_at desc);
    create table if not exists agent_optimizer_observation_history(
      id integer primary key autoincrement,
      catalog_key text not null,
      domain text not null,
      config_key text not null,
      config text not null,
      score real not null,
      feasible integer not null,
      source text not null,
      generation integer not null,
      feasibility_basis text not null,
      constraints text not null,
      observed_at real not null
    );
    create index if not exists idx_agent_optimizer_observation_history_lookup
      on agent_optimizer_observation_history(catalog_key,domain,observed_at desc,id desc);
    create index if not exists idx_agent_optimizer_observation_history_config
      on agent_optimizer_observation_history(catalog_key,domain,config_key,observed_at desc,id desc);
    """
    with memory._lock:
        conn = memory._connect()
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            memory._close(conn)


def _latest_observation_rows(
    connection: Any,
    *,
    catalog_key: str,
    domain: str,
    limit: int,
) -> list[Any]:
    """Select current-set membership by durable commit rank, then present by time."""

    return connection.execute(
        """
        select config_key,config,score,feasible,source,generation,feasibility_basis,
               constraints,seen_count,updated_at
        from (
          select o.config_key,o.config,o.score,o.feasible,o.source,o.generation,
                 o.feasibility_basis,o.constraints,o.seen_count,o.updated_at,
                 coalesce(h.commit_id,0) as commit_id
          from agent_optimizer_observations o
          left join (
            select config_key,max(id) as commit_id
            from agent_optimizer_observation_history
            where catalog_key=? and domain=?
            group by config_key
          ) h on h.config_key=o.config_key
          where o.catalog_key=? and o.domain=?
          order by coalesce(h.commit_id,0) desc,o.updated_at desc,o.config_key asc
          limit ?
        ) selected
        order by updated_at desc,config_key asc
        """,
        (catalog_key, domain, catalog_key, domain, limit),
    ).fetchall()


def _prune_latest_observations(
    connection: Any,
    *,
    catalog_key: str,
    domain: str,
) -> None:
    connection.execute(
        """
        delete from agent_optimizer_observations
        where catalog_key=? and domain=? and config_key not in (
          select o.config_key
          from agent_optimizer_observations o
          left join (
            select config_key,max(id) as commit_id
            from agent_optimizer_observation_history
            where catalog_key=? and domain=?
            group by config_key
          ) h on h.config_key=o.config_key
          where o.catalog_key=? and o.domain=?
          order by coalesce(h.commit_id,0) desc,o.updated_at desc,o.config_key asc
          limit ?
        )
        """,
        (
            catalog_key,
            domain,
            catalog_key,
            domain,
            catalog_key,
            domain,
            OPTIMIZER_OBSERVATION_RETENTION,
        ),
    )


def _record_optimizer_observations(
    memory: Any,
    catalog_key: str,
    domain: str,
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    domain = str(domain or "").strip()
    if domain not in {"search", "recommend"}:
        return {"available": False, "reason": "unsupported_surface"}

    rows: list[dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        config = observation.get("config")
        score = _finite_float(observation.get("objective", observation.get("score")))
        feasible = observation.get("feasible")
        basis = str(observation.get("feasibility_basis") or "").strip()
        if not isinstance(config, dict) or score is None or not isinstance(feasible, bool) or not basis:
            continue
        try:
            key = _config_key(config)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "config_key": key,
                "config": dict(config),
                "score": score,
                "feasible": feasible,
                "source": str(observation.get("source") or "optimizer_evaluator"),
                "generation": max(0, int(observation.get("generation", 0) or 0)),
                "feasibility_basis": basis,
                "constraints": dict(observation.get("constraints") or {}),
            }
        )

    if not rows:
        return {
            "available": True,
            "captured_rows": 0,
            "inserted_rows": 0,
            "updated_rows": 0,
            "history_rows": 0,
            "feasible_rows": 0,
            "infeasible_rows": 0,
            "new_evaluator_calls": 0,
        }

    _ensure_optimizer_observation_table(memory)
    now = time.time()
    inserted = 0
    updated = 0
    with memory._lock:
        conn = memory._connect()
        try:
            conn.execute("pragma busy_timeout=10000")
            conn.execute("begin immediate")
            for row in rows:
                exists = conn.execute(
                    "select 1 from agent_optimizer_observations where catalog_key=? and domain=? and config_key=?",
                    (catalog_key, domain, row["config_key"]),
                ).fetchone()
                if exists:
                    updated += 1
                else:
                    inserted += 1
                config_json = json.dumps(
                    row["config"],
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                constraints_json = json.dumps(
                    row["constraints"],
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                conn.execute(
                    """
                    insert into agent_optimizer_observation_history(
                      catalog_key,domain,config_key,config,score,feasible,source,generation,
                      feasibility_basis,constraints,observed_at
                    ) values(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        catalog_key,
                        domain,
                        row["config_key"],
                        config_json,
                        row["score"],
                        1 if row["feasible"] else 0,
                        row["source"],
                        row["generation"],
                        row["feasibility_basis"],
                        constraints_json,
                        now,
                    ),
                )
                conn.execute(
                    """
                    insert into agent_optimizer_observations(
                      catalog_key,domain,config_key,config,score,feasible,source,generation,
                      feasibility_basis,constraints,seen_count,created_at,updated_at
                    ) values(?,?,?,?,?,?,?,?,?,?,1,?,?)
                    on conflict(catalog_key,domain,config_key) do update set
                      config=excluded.config,
                      score=excluded.score,
                      feasible=excluded.feasible,
                      source=excluded.source,
                      generation=excluded.generation,
                      feasibility_basis=excluded.feasibility_basis,
                      constraints=excluded.constraints,
                      seen_count=agent_optimizer_observations.seen_count+1,
                      updated_at=excluded.updated_at
                    """,
                    (
                        catalog_key,
                        domain,
                        row["config_key"],
                        config_json,
                        row["score"],
                        1 if row["feasible"] else 0,
                        row["source"],
                        row["generation"],
                        row["feasibility_basis"],
                        constraints_json,
                        now,
                        now,
                    ),
                )
            # Membership follows the durable paid-observation commit sequence rather
            # than wall clock. updated_at remains the recency/presentation clock.
            _prune_latest_observations(
                conn,
                catalog_key=catalog_key,
                domain=domain,
            )
            # The routing revision fence uses the per-scope maximum history id as
            # its commit high-water. Retain by AUTOINCREMENT id, not wall clock, so
            # a newly committed observation cannot be pruned immediately by clock
            # rollback or cross-process timestamp skew before the fence can see it.
            conn.execute(
                """
                delete from agent_optimizer_observation_history
                where catalog_key=? and domain=? and id not in (
                  select id from agent_optimizer_observation_history
                  where catalog_key=? and domain=?
                  order by id desc limit ?
                )
                """,
                (
                    catalog_key,
                    domain,
                    catalog_key,
                    domain,
                    OPTIMIZER_OBSERVATION_HISTORY_RETENTION,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            memory._close(conn)

    return {
        "available": True,
        "captured_rows": len(rows),
        "inserted_rows": inserted,
        "updated_rows": updated,
        "history_rows": len(rows),
        "feasible_rows": sum(1 for row in rows if row["feasible"]),
        "infeasible_rows": sum(1 for row in rows if not row["feasible"]),
        "new_evaluator_calls": 0,
        "authority": "routing_descriptor_only",
    }


def _optimizer_observations(
    memory: Any,
    catalog_key: str,
    domain: str,
    *,
    limit: int = OPTIMIZER_OBSERVATION_READ_BUDGET,
) -> list[dict[str, Any]]:
    domain = str(domain or "").strip()
    if domain not in {"search", "recommend"}:
        return []
    _ensure_optimizer_observation_table(memory)
    limit = max(1, min(OPTIMIZER_OBSERVATION_RETENTION, int(limit)))
    with memory._lock:
        conn = memory._connect()
        try:
            rows = _latest_observation_rows(
                conn,
                catalog_key=catalog_key,
                domain=domain,
                limit=limit,
            )
        finally:
            memory._close(conn)

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


def _optimizer_observation_history(
    memory: Any,
    catalog_key: str,
    domain: str,
    *,
    limit: int = OPTIMIZER_OBSERVATION_HISTORY_READ_BUDGET,
) -> list[dict[str, Any]]:
    domain = str(domain or "").strip()
    if domain not in {"search", "recommend"}:
        return []
    _ensure_optimizer_observation_table(memory)
    limit = max(1, min(OPTIMIZER_OBSERVATION_HISTORY_RETENTION, int(limit)))
    with memory._lock:
        conn = memory._connect()
        try:
            rows = conn.execute(
                """
                select config_key,config,score,feasible,source,generation,feasibility_basis,
                       constraints,observed_at
                from agent_optimizer_observation_history
                where catalog_key=? and domain=?
                order by observed_at desc, id desc limit ?
                """,
                (catalog_key, domain, limit),
            ).fetchall()
        finally:
            memory._close(conn)

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


def install_optimizer_observation_runtime(agent_memory_cls: type, optimizer_registry_cls: type) -> None:
    """Install a durable, routing-only observation path on the public runtime."""

    global _INSTALLED
    if _INSTALLED:
        return

    install_optimizer_observation_capture()

    setattr(agent_memory_cls, "record_optimizer_observations", _record_optimizer_observations)
    setattr(agent_memory_cls, "optimizer_observations", _optimizer_observations)
    setattr(agent_memory_cls, "optimizer_observation_history", _optimizer_observation_history)

    backend_memory._SCOPED_CATALOG_METHODS = frozenset(
        set(backend_memory._SCOPED_CATALOG_METHODS)
        | {
            "record_optimizer_observations",
            "optimizer_observations",
            "optimizer_observation_history",
        }
    )

    original_record = agent_memory_cls.record_evolution_result

    def record_evolution_result_with_observations(
        self: Any,
        catalog_key: str,
        surface: str,
        *,
        current_config: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        credit = original_record(
            self,
            catalog_key,
            surface,
            current_config=current_config,
            result=result,
        )
        captured = consume_optimizer_observations()
        if result.get("replayed") or not result.get("evaluation_ready"):
            observation_summary = {
                "available": True,
                "captured_rows": 0,
                "inserted_rows": 0,
                "updated_rows": 0,
                "history_rows": 0,
                "feasible_rows": 0,
                "infeasible_rows": 0,
                "new_evaluator_calls": 0,
            }
        else:
            observation_summary = self.record_optimizer_observations(
                catalog_key,
                surface,
                captured,
            )
        merged = dict(credit or {})
        merged["optimizer_observations"] = observation_summary
        return merged

    agent_memory_cls.record_evolution_result = record_evolution_result_with_observations

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_durable_geometry(self: Any, surface: str):
        context = original_routing_context(self, surface)
        reader = getattr(self.memory, "optimizer_observations", None)
        if not callable(reader):
            return context
        observations = reader(
            self.catalog_key,
            surface,
            limit=OPTIMIZER_OBSERVATION_READ_BUDGET,
        )
        if len(observations) < 4:
            return context
        engine = self.search if surface == "search" else self.recommend
        try:
            dimensions, _ = core._evolution_schema(engine.config)
            landscape = describe_optimizer_landscape(
                dimensions=dimensions,
                observations=observations,
            )
        except (TypeError, ValueError, KeyError):
            return context
        if not landscape.informative:
            return context
        return replace(context, landscape=landscape)

    optimizer_registry_cls._routing_context = routing_context_with_durable_geometry

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_observation_contract(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        router.update(
            {
                "landscape_descriptors": "durable_evaluator_observations_with_strategy_fallback",
                "optimizer_observation_memory": "evaluator_paid_discovery_rows_only",
                "optimizer_observation_history": "bounded_same_config_evaluator_history",
                "optimizer_observation_retention": OPTIMIZER_OBSERVATION_RETENTION,
                "optimizer_observation_latest_selection_order": OPTIMIZER_OBSERVATION_LATEST_SELECTION_ORDER,
                "optimizer_observation_history_retention": OPTIMIZER_OBSERVATION_HISTORY_RETENTION,
                "optimizer_observation_history_retention_order": OPTIMIZER_OBSERVATION_HISTORY_RETENTION_ORDER,
                "optimizer_observation_feasibility": "discovery_robustness_guardrails",
                "optimizer_observation_authority": "routing_descriptor_only",
                "optimizer_observation_read_budget": OPTIMIZER_OBSERVATION_READ_BUDGET,
                "landscape_descriptor_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_observation_contract
    _INSTALLED = True


__all__ = [
    "OPTIMIZER_OBSERVATION_HISTORY_READ_BUDGET",
    "OPTIMIZER_OBSERVATION_HISTORY_RETENTION",
    "OPTIMIZER_OBSERVATION_HISTORY_RETENTION_ORDER",
    "OPTIMIZER_OBSERVATION_LATEST_SELECTION_ORDER",
    "OPTIMIZER_OBSERVATION_READ_BUDGET",
    "OPTIMIZER_OBSERVATION_RETENTION",
    "install_optimizer_observation_runtime",
]
