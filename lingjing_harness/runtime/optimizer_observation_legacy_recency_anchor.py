from __future__ import annotations

from math import isfinite
import time
from typing import Any

from . import optimizer_observation_memory as observation_memory
from . import optimizer_observation_snapshot as snapshot_runtime


_LEGACY_ANCHOR_STATE_ATTR = "_optimizer_observation_legacy_recency_anchor_states"
_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _ensure_legacy_anchor_table(memory: Any) -> None:
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            connection.execute(
                """
                create table if not exists agent_optimizer_observation_legacy_recency_anchor(
                  catalog_key text not null,
                  domain text not null,
                  config_key text not null,
                  updated_at real not null,
                  anchor_at real not null check(anchor_at >= 0),
                  primary key(catalog_key,domain,config_key,updated_at)
                )
                """
            )
            connection.commit()
        finally:
            memory._close(connection)


def _legacy_snapshot_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in list(snapshot.get("observations") or []):
        observation_id = max(0, int(source.get("observation_commit_id", 0) or 0))
        if observation_id > 0:
            continue
        config_key = str(source.get("config_key") or "").strip()
        updated_at = _finite_float(source.get("updated_at"))
        if not config_key or updated_at is None:
            continue
        rows.append(
            {
                "config_key": config_key,
                "updated_at": updated_at,
            }
        )
    return rows


def _read_legacy_anchors(
    memory: Any,
    *,
    catalog_key: str,
    domain: str,
) -> dict[tuple[str, float], float]:
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            rows = connection.execute(
                """
                select config_key,updated_at,anchor_at
                from agent_optimizer_observation_legacy_recency_anchor
                where catalog_key=? and domain=?
                """,
                (catalog_key, domain),
            ).fetchall()
        finally:
            memory._close(connection)
    return {
        (str(row["config_key"]), float(row["updated_at"])): float(row["anchor_at"])
        for row in rows
    }


def _persist_legacy_future_anchors(
    memory: Any,
    *,
    catalog_key: str,
    domain: str,
    legacy_rows: list[dict[str, Any]],
    anchors: dict[tuple[str, float], float],
    reference_time: float,
) -> dict[tuple[str, float], float]:
    retained_keys = {
        (str(row["config_key"]), float(row["updated_at"]))
        for row in legacy_rows
    }
    stale_keys = set(anchors) - retained_keys
    future_rows = [
        row
        for row in legacy_rows
        if float(row["updated_at"]) > reference_time + 1e-12
        and (
            (str(row["config_key"]), float(row["updated_at"])) not in anchors
            or anchors[(str(row["config_key"]), float(row["updated_at"]))]
            > reference_time + 1e-12
        )
    ]
    if not stale_keys and not future_rows:
        return anchors

    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            connection.execute("begin immediate")
            for row in future_rows:
                connection.execute(
                    """
                    insert into agent_optimizer_observation_legacy_recency_anchor(
                      catalog_key,domain,config_key,updated_at,anchor_at
                    ) values(?,?,?,?,?)
                    on conflict(catalog_key,domain,config_key,updated_at) do update set
                      anchor_at=min(
                        agent_optimizer_observation_legacy_recency_anchor.anchor_at,
                        excluded.anchor_at
                      )
                    """,
                    (
                        catalog_key,
                        domain,
                        str(row["config_key"]),
                        float(row["updated_at"]),
                        reference_time,
                    ),
                )
            # Snapshot reads are intentionally bounded. Delete only identities whose
            # exact legacy latest row is gone from the live table, not identities
            # merely absent from this routing snapshot.
            connection.execute(
                """
                delete from agent_optimizer_observation_legacy_recency_anchor
                where catalog_key=? and domain=?
                  and not exists (
                    select 1 from agent_optimizer_observations current
                    where current.catalog_key=? and current.domain=?
                      and current.config_key=
                        agent_optimizer_observation_legacy_recency_anchor.config_key
                      and current.updated_at=
                        agent_optimizer_observation_legacy_recency_anchor.updated_at
                  )
                """,
                (catalog_key, domain, catalog_key, domain),
            )
            rows = connection.execute(
                """
                select config_key,updated_at,anchor_at
                from agent_optimizer_observation_legacy_recency_anchor
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
        (str(row["config_key"]), float(row["updated_at"])): float(row["anchor_at"])
        for row in rows
    }


def _normalized_legacy_snapshot(
    memory: Any,
    *,
    catalog_key: str,
    domain: str,
    snapshot: dict[str, Any],
    reference_time: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    legacy_rows = _legacy_snapshot_rows(snapshot)
    if not legacy_rows:
        return snapshot, {
            "status": "no_legacy_latest_rows",
            "reference_time": reference_time,
            "legacy_rows": 0,
            "legacy_anchored_rows": 0,
            "persisted_legacy_anchor_versions": 0,
            "new_evaluator_calls": 0,
        }

    durable_anchors = _read_legacy_anchors(
        memory,
        catalog_key=catalog_key,
        domain=domain,
    )
    effective_anchors = dict(durable_anchors)
    for row in legacy_rows:
        key = (str(row["config_key"]), float(row["updated_at"]))
        if float(row["updated_at"]) > reference_time + 1e-12:
            current = effective_anchors.get(key)
            effective_anchors[key] = (
                reference_time if current is None else min(current, reference_time)
            )

    persisted_anchors = _persist_legacy_future_anchors(
        memory,
        catalog_key=catalog_key,
        domain=domain,
        legacy_rows=legacy_rows,
        anchors=durable_anchors,
        reference_time=reference_time,
    )

    observations: list[dict[str, Any]] = []
    anchored_keys: set[tuple[str, float]] = set()
    for source in list(snapshot.get("observations") or []):
        row = dict(source)
        observation_id = max(0, int(row.get("observation_commit_id", 0) or 0))
        config_key = str(row.get("config_key") or "").strip()
        raw_updated_at = _finite_float(row.get("updated_at"))
        if observation_id <= 0 and config_key and raw_updated_at is not None:
            key = (config_key, raw_updated_at)
            anchor_at = effective_anchors.get(key)
            if anchor_at is not None:
                row["routing_raw_updated_at"] = raw_updated_at
                row["routing_clock_anchor_at"] = anchor_at
                row["updated_at"] = min(raw_updated_at, anchor_at)
                if anchor_at + 1e-12 < raw_updated_at:
                    anchored_keys.add(key)
        observations.append(row)

    result = dict(snapshot)
    result["observations"] = observations
    result["latest_newest_at"] = max(
        (_finite_float(row.get("updated_at")) or 0.0 for row in observations),
        default=0.0,
    )
    diagnostics = {
        "status": "legacy_latest_clock_normalized",
        "reference_time": reference_time,
        "legacy_rows": len(legacy_rows),
        "legacy_anchored_rows": len(anchored_keys),
        "persisted_legacy_anchor_versions": len(persisted_anchors),
        "latest_raw_newest_at": float(result.get("latest_raw_newest_at", 0.0) or 0.0),
        "latest_newest_at": result["latest_newest_at"],
        "new_evaluator_calls": 0,
    }
    return result, diagnostics


def _legacy_anchor_states(registry: Any) -> dict[str, dict[str, Any]]:
    states = getattr(registry, _LEGACY_ANCHOR_STATE_ATTR, None)
    if not isinstance(states, dict):
        states = {}
        setattr(registry, _LEGACY_ANCHOR_STATE_ATTR, states)
    return states


def install_optimizer_observation_legacy_recency_anchor(
    optimizer_registry_cls: type,
) -> None:
    """Age legacy latest-only future clocks without modifying paid observation rows."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_read_atomic_snapshot = snapshot_runtime._read_atomic_snapshot

    def read_atomic_snapshot_with_legacy_recency_anchor(
        memory: Any,
        catalog_key: str,
        domain: str,
    ) -> dict[str, Any]:
        snapshot = original_read_atomic_snapshot(memory, catalog_key, domain)
        domain = str(domain or "").strip()
        if domain not in {"search", "recommend"}:
            return snapshot
        legacy_rows = _legacy_snapshot_rows(snapshot)
        if not legacy_rows:
            return snapshot
        observation_memory._ensure_optimizer_observation_table(memory)
        _ensure_legacy_anchor_table(memory)
        now = _finite_float(time.time())
        if now is None or now < 0.0:
            return snapshot
        normalized, diagnostics = _normalized_legacy_snapshot(
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
                _legacy_anchor_states(registry)[domain] = diagnostics
        return normalized

    snapshot_runtime._read_atomic_snapshot = read_atomic_snapshot_with_legacy_recency_anchor

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_legacy_recency_anchor(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        states = getattr(self, _LEGACY_ANCHOR_STATE_ATTR, None)
        router.update(
            {
                "optimizer_observation_legacy_recency_clock_normalization": "latest_only_first_local_routing_anchor",
                "optimizer_observation_legacy_recency_clock_anchor_identity": "durable_config_key_and_raw_updated_at_without_history_commit",
                "optimizer_observation_legacy_recency_clock_anchor_repair": "monotone_earlier_caller_clock",
                "optimizer_observation_legacy_recency_clock_anchor_states": (
                    dict(states) if isinstance(states, dict) else {}
                ),
                "optimizer_observation_legacy_recency_clock_anchor_authority": "routing_descriptor_only",
                "optimizer_observation_legacy_recency_clock_anchor_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_legacy_recency_anchor
    _INSTALLED = True


__all__ = ["install_optimizer_observation_legacy_recency_anchor"]
