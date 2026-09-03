from __future__ import annotations

from collections import defaultdict, deque
from math import isfinite
import time
from typing import Any

from . import optimizer_observation_memory as observation_memory
from . import optimizer_observation_snapshot as snapshot_runtime


_ANCHOR_STATE_ATTR = "_optimizer_observation_recency_anchor_states"
_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _ensure_anchor_table(memory: Any) -> None:
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            connection.execute(
                """
                create table if not exists agent_optimizer_observation_recency_anchor(
                  catalog_key text not null,
                  domain text not null,
                  observation_id integer not null check(observation_id >= 0),
                  anchor_at real not null check(anchor_at >= 0),
                  primary key(catalog_key,domain,observation_id)
                )
                """
            )
            connection.commit()
        finally:
            memory._close(connection)


def _history_clock_rows(
    memory: Any,
    *,
    catalog_key: str,
    domain: str,
) -> list[dict[str, Any]]:
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            rows = connection.execute(
                """
                select id,config_key,observed_at
                from agent_optimizer_observation_history
                where catalog_key=? and domain=?
                order by observed_at desc,id desc
                """,
                (catalog_key, domain),
            ).fetchall()
        finally:
            memory._close(connection)
    return [
        {
            "observation_id": max(0, int(row["id"])),
            "config_key": str(row["config_key"]),
            "observed_at": float(row["observed_at"]),
        }
        for row in rows
    ]


def _read_anchors(
    memory: Any,
    *,
    catalog_key: str,
    domain: str,
) -> dict[int, float]:
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            rows = connection.execute(
                """
                select observation_id,anchor_at
                from agent_optimizer_observation_recency_anchor
                where catalog_key=? and domain=?
                """,
                (catalog_key, domain),
            ).fetchall()
        finally:
            memory._close(connection)
    return {
        max(0, int(row["observation_id"])): float(row["anchor_at"])
        for row in rows
    }


def _persist_future_anchors(
    memory: Any,
    *,
    catalog_key: str,
    domain: str,
    history_rows: list[dict[str, Any]],
    anchors: dict[int, float],
    reference_time: float,
) -> dict[int, float]:
    retained_ids = {
        int(row["observation_id"])
        for row in history_rows
        if int(row.get("observation_id", 0) or 0) > 0
    }
    stale_ids = set(anchors) - retained_ids
    future_rows = [
        row
        for row in history_rows
        if int(row.get("observation_id", 0) or 0) > 0
        and float(row.get("observed_at", 0.0) or 0.0) > reference_time + 1e-12
        and (
            int(row["observation_id"]) not in anchors
            or anchors[int(row["observation_id"])] > reference_time + 1e-12
        )
    ]
    if not stale_ids and not future_rows:
        return anchors

    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            connection.execute("begin immediate")
            for row in future_rows:
                observation_id = int(row["observation_id"])
                connection.execute(
                    """
                    insert into agent_optimizer_observation_recency_anchor(
                      catalog_key,domain,observation_id,anchor_at
                    ) values(?,?,?,?)
                    on conflict(catalog_key,domain,observation_id) do update set
                      anchor_at=min(
                        agent_optimizer_observation_recency_anchor.anchor_at,
                        excluded.anchor_at
                      )
                    """,
                    (catalog_key, domain, observation_id, reference_time),
                )
            connection.execute(
                """
                delete from agent_optimizer_observation_recency_anchor
                where catalog_key=? and domain=?
                  and observation_id not in (
                    select id from agent_optimizer_observation_history
                    where catalog_key=? and domain=?
                  )
                """,
                (catalog_key, domain, catalog_key, domain),
            )
            rows = connection.execute(
                """
                select observation_id,anchor_at
                from agent_optimizer_observation_recency_anchor
                where catalog_key=? and domain=?
                """,
                (catalog_key, domain),
            ).fetchall()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            memory._close(connection)
    return {
        max(0, int(row["observation_id"])): float(row["anchor_at"])
        for row in rows
    }


def _normalized_snapshot(
    memory: Any,
    *,
    catalog_key: str,
    domain: str,
    snapshot: dict[str, Any],
    reference_time: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    history_clock_rows = _history_clock_rows(
        memory,
        catalog_key=catalog_key,
        domain=domain,
    )
    anchors = _persist_future_anchors(
        memory,
        catalog_key=catalog_key,
        domain=domain,
        history_rows=history_clock_rows,
        anchors=_read_anchors(memory, catalog_key=catalog_key, domain=domain),
        reference_time=reference_time,
    )

    latest_id_by_config: dict[str, int] = {}
    history_ids_by_clock: dict[tuple[str, float], deque[int]] = defaultdict(deque)
    for row in history_clock_rows:
        observation_id = int(row["observation_id"])
        config_key = str(row["config_key"])
        observed_at = float(row["observed_at"])
        latest_id_by_config[config_key] = max(
            observation_id,
            latest_id_by_config.get(config_key, 0),
        )
        history_ids_by_clock[(config_key, observed_at)].append(observation_id)

    observations: list[dict[str, Any]] = []
    latest_anchored_ids: set[int] = set()
    for source in list(snapshot.get("observations") or []):
        row = dict(source)
        config_key = str(row.get("config_key") or "")
        observation_id = latest_id_by_config.get(config_key, 0)
        raw_updated_at = _finite_float(row.get("updated_at"))
        anchor_at = anchors.get(observation_id)
        if observation_id > 0:
            row["observation_commit_id"] = observation_id
        if raw_updated_at is not None and anchor_at is not None:
            row["routing_raw_updated_at"] = raw_updated_at
            row["routing_clock_anchor_at"] = anchor_at
            row["updated_at"] = min(raw_updated_at, anchor_at)
            if anchor_at + 1e-12 < raw_updated_at:
                latest_anchored_ids.add(observation_id)
        observations.append(row)

    history: list[dict[str, Any]] = []
    history_anchored_ids: set[int] = set()
    for source in list(snapshot.get("history") or []):
        row = dict(source)
        config_key = str(row.get("config_key") or "")
        raw_observed_at = _finite_float(row.get("observed_at"))
        observation_id = 0
        if raw_observed_at is not None:
            ids = history_ids_by_clock.get((config_key, raw_observed_at))
            if ids:
                observation_id = int(ids.popleft())
        anchor_at = anchors.get(observation_id)
        if observation_id > 0:
            row["observation_commit_id"] = observation_id
        if raw_observed_at is not None and anchor_at is not None:
            row["routing_raw_observed_at"] = raw_observed_at
            row["routing_clock_anchor_at"] = anchor_at
            row["observed_at"] = min(raw_observed_at, anchor_at)
            if anchor_at + 1e-12 < raw_observed_at:
                history_anchored_ids.add(observation_id)
        history.append(row)

    result = dict(snapshot)
    result["observations"] = observations
    result["history"] = history
    result["latest_raw_newest_at"] = float(snapshot.get("latest_newest_at", 0.0) or 0.0)
    result["history_raw_newest_at"] = float(snapshot.get("history_newest_at", 0.0) or 0.0)
    result["latest_newest_at"] = max(
        (_finite_float(row.get("updated_at")) or 0.0 for row in observations),
        default=0.0,
    )
    result["history_newest_at"] = max(
        (_finite_float(row.get("observed_at")) or 0.0 for row in history),
        default=0.0,
    )
    diagnostics = {
        "status": "paid_commit_clock_normalized",
        "reference_time": reference_time,
        "retained_history_versions": len(history_clock_rows),
        "persisted_anchor_versions": len(anchors),
        "latest_anchored_versions": len(latest_anchored_ids),
        "history_anchored_versions": len(history_anchored_ids),
        "latest_raw_newest_at": result["latest_raw_newest_at"],
        "latest_newest_at": result["latest_newest_at"],
        "history_raw_newest_at": result["history_raw_newest_at"],
        "history_newest_at": result["history_newest_at"],
        "new_evaluator_calls": 0,
    }
    return result, diagnostics


def _anchor_states(registry: Any) -> dict[str, dict[str, Any]]:
    states = getattr(registry, _ANCHOR_STATE_ATTR, None)
    if not isinstance(states, dict):
        states = {}
        setattr(registry, _ANCHOR_STATE_ATTR, states)
    return states


def install_optimizer_observation_recency_anchor(optimizer_registry_cls: type) -> None:
    """Normalize future paid-observation clocks only inside routing snapshots."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_read_atomic_snapshot = snapshot_runtime._read_atomic_snapshot

    def read_atomic_snapshot_with_recency_anchor(
        memory: Any,
        catalog_key: str,
        domain: str,
    ) -> dict[str, Any]:
        snapshot = original_read_atomic_snapshot(memory, catalog_key, domain)
        domain = str(domain or "").strip()
        if domain not in {"search", "recommend"}:
            return snapshot
        observation_memory._ensure_optimizer_observation_table(memory)
        _ensure_anchor_table(memory)
        now = _finite_float(time.time())
        if now is None or now < 0.0:
            return snapshot
        normalized, diagnostics = _normalized_snapshot(
            memory,
            catalog_key=str(catalog_key),
            domain=domain,
            snapshot=snapshot,
            reference_time=now,
        )
        context = snapshot_runtime._ROUTING_SNAPSHOT_CONTEXT.get()
        if isinstance(context, dict):
            registry = context.get("registry")
            if registry is not None:
                _anchor_states(registry)[domain] = diagnostics
        return normalized

    snapshot_runtime._read_atomic_snapshot = read_atomic_snapshot_with_recency_anchor

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_recency_anchor(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        states = getattr(self, _ANCHOR_STATE_ATTR, None)
        router.update(
            {
                "optimizer_observation_recency_clock_normalization": "paid_history_commit_first_local_routing_anchor",
                "optimizer_observation_recency_clock_anchor_scope": "routing_snapshot_only",
                "optimizer_observation_recency_clock_anchor_repair": "monotone_earlier_caller_clock",
                "optimizer_observation_recency_clock_anchor_states": (
                    dict(states) if isinstance(states, dict) else {}
                ),
                "optimizer_observation_recency_clock_anchor_authority": "routing_descriptor_only",
                "optimizer_observation_recency_clock_anchor_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_recency_anchor
    _INSTALLED = True


__all__ = ["install_optimizer_observation_recency_anchor"]
