from __future__ import annotations

from math import isfinite
import time
from typing import Any


class OptimizerRoutingCheckpointStore:
    """Durable routing-only hysteresis checkpoint over optimizer meta storage.

    The checkpoint never carries optimizer credit, candidate quality, warm-start
    rows, or promotion authority. Its only purpose is to preserve the prior
    weighted/fallback regime across process restarts while evidence is revalidated
    by the existing observation confidence gates. Confirmed change points also
    advance a monotone routing-evidence epoch in this same checkpoint row so future
    drift comparisons never cross an already-accepted regime boundary.
    """

    def __init__(self, optimizer_meta_memory: Any) -> None:
        self.meta_memory = optimizer_meta_memory
        self.memory = optimizer_meta_memory.memory
        self._init()

    def _init(self) -> None:
        sql = """
        create table if not exists agent_optimizer_routing_checkpoint(
          catalog_key text not null,
          domain text not null,
          regime text not null check(regime in ('weighted','fallback')),
          evidence_updated_at real not null check(evidence_updated_at >= 0),
          evidence_seen_count integer not null check(evidence_seen_count >= 0),
          evidence_rows integer not null check(evidence_rows >= 0),
          evidence_epoch integer not null default 0 check(evidence_epoch >= 0),
          epoch_started_at real not null default 0 check(epoch_started_at >= 0),
          decision_at real not null,
          expires_at real not null,
          primary key(catalog_key,domain)
        );
        """
        with self.memory._lock:
            connection = self.memory._connect()
            try:
                connection.execute("pragma busy_timeout=10000")
                connection.execute(sql)
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "pragma table_info(agent_optimizer_routing_checkpoint)"
                    ).fetchall()
                }
                if "evidence_epoch" not in columns:
                    connection.execute(
                        "alter table agent_optimizer_routing_checkpoint "
                        "add column evidence_epoch integer not null default 0 "
                        "check(evidence_epoch >= 0)"
                    )
                if "epoch_started_at" not in columns:
                    connection.execute(
                        "alter table agent_optimizer_routing_checkpoint "
                        "add column epoch_started_at real not null default 0 "
                        "check(epoch_started_at >= 0)"
                    )
                connection.commit()
            finally:
                self.memory._close(connection)

    def _scoped_catalog_key(self, catalog_key: str, domain: str) -> str:
        return self.meta_memory._scoped_catalog_key(str(catalog_key), str(domain))

    @staticmethod
    def _count(value: Any, *, name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        integer = int(value)
        if integer != value or integer < 0:
            raise ValueError(f"{name} must be an integer >= 0")
        return integer

    def read(
        self,
        catalog_key: str,
        domain: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        domain = str(domain or "").strip()
        if domain not in {"search", "recommend"}:
            raise ValueError("optimizer routing checkpoint domain must be search or recommend")
        catalog_key = self._scoped_catalog_key(catalog_key, domain)
        if now is None:
            now = time.time()
        now = float(now)
        if not isfinite(now):
            raise ValueError("optimizer routing checkpoint time must be finite")

        with self.memory._lock:
            connection = self.memory._connect()
            try:
                connection.execute("pragma busy_timeout=10000")
                row = connection.execute(
                    """
                    select catalog_key,domain,regime,evidence_updated_at,
                           evidence_seen_count,evidence_rows,evidence_epoch,
                           epoch_started_at,decision_at,expires_at
                    from agent_optimizer_routing_checkpoint
                    where catalog_key=? and domain=?
                    """,
                    (catalog_key, domain),
                ).fetchone()
            finally:
                self.memory._close(connection)
        if row is None:
            return None
        result = dict(row)
        decision_at = float(result.get("decision_at", 0.0) or 0.0)
        if isfinite(decision_at) and decision_at >= 0.0:
            result["evidence_updated_at"] = min(
                float(result.get("evidence_updated_at", 0.0) or 0.0),
                decision_at,
            )
            result["epoch_started_at"] = min(
                float(result.get("epoch_started_at", 0.0) or 0.0),
                decision_at,
            )
        result["active_weighted"] = bool(
            result.get("regime") == "weighted"
            and decision_at <= now + 1e-12
            and float(result.get("expires_at", 0.0) or 0.0) >= now
        )
        return result

    def record(
        self,
        catalog_key: str,
        domain: str,
        *,
        regime: str,
        evidence_updated_at: float,
        evidence_seen_count: int,
        evidence_rows: int,
        epoch_started_at: float = 0.0,
        decision_at: float | None = None,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        """Persist one routing decision and any monotone change-point epoch advance."""

        domain = str(domain or "").strip()
        regime = str(regime or "").strip().lower()
        if domain not in {"search", "recommend"}:
            raise ValueError("optimizer routing checkpoint domain must be search or recommend")
        if regime not in {"weighted", "fallback"}:
            raise ValueError("optimizer routing checkpoint regime must be weighted or fallback")
        catalog_key = self._scoped_catalog_key(catalog_key, domain)

        evidence_updated_at = float(evidence_updated_at)
        if not isfinite(evidence_updated_at) or evidence_updated_at < 0.0:
            raise ValueError("optimizer routing evidence_updated_at must be finite and >= 0")
        evidence_seen_count = self._count(
            evidence_seen_count,
            name="optimizer routing evidence_seen_count",
        )
        evidence_rows = self._count(
            evidence_rows,
            name="optimizer routing evidence_rows",
        )
        epoch_started_at = float(epoch_started_at)
        if not isfinite(epoch_started_at) or epoch_started_at < 0.0:
            raise ValueError("optimizer routing epoch_started_at must be finite and >= 0")
        if epoch_started_at > evidence_updated_at + 1e-12:
            raise ValueError("optimizer routing epoch cannot start after current evidence")

        if decision_at is None:
            decision_at = time.time()
        decision_at = float(decision_at)
        ttl_seconds = float(ttl_seconds)
        if not isfinite(decision_at) or decision_at < 0.0:
            raise ValueError("optimizer routing decision_at must be finite and >= 0")
        if not isfinite(ttl_seconds) or ttl_seconds <= 0.0:
            raise ValueError("optimizer routing checkpoint ttl_seconds must be finite and > 0")

        # Observation timestamps are external wall-clock evidence. They may be
        # locally ahead after a clock jump, but a checkpoint must never let that
        # external clock outrank the caller's routing-decision clock. Clamp both
        # evidence and epoch boundary to the same local linearization point.
        evidence_updated_at = min(evidence_updated_at, decision_at)
        epoch_started_at = min(epoch_started_at, decision_at)
        expires_at = decision_at + ttl_seconds if regime == "weighted" else decision_at

        with self.memory._lock:
            connection = self.memory._connect()
            try:
                connection.execute("pragma busy_timeout=10000")
                connection.execute("begin immediate")
                existing = connection.execute(
                    """
                    select evidence_epoch,epoch_started_at,decision_at
                    from agent_optimizer_routing_checkpoint
                    where catalog_key=? and domain=?
                    """,
                    (catalog_key, domain),
                ).fetchone()
                current_epoch = int(existing["evidence_epoch"]) if existing is not None else 0
                current_epoch_started_at = (
                    float(existing["epoch_started_at"]) if existing is not None else 0.0
                )
                if (
                    existing is not None
                    and current_epoch_started_at > decision_at + 1e-12
                    and decision_at >= float(existing["decision_at"])
                ):
                    current_epoch_started_at = decision_at
                effective_epoch_started_at = max(
                    current_epoch_started_at,
                    epoch_started_at,
                )
                effective_epoch = current_epoch + (
                    1 if epoch_started_at > current_epoch_started_at + 1e-12 else 0
                )
                cursor = connection.execute(
                    """
                    insert into agent_optimizer_routing_checkpoint(
                      catalog_key,domain,regime,evidence_updated_at,evidence_seen_count,
                      evidence_rows,evidence_epoch,epoch_started_at,decision_at,expires_at
                    ) values(?,?,?,?,?,?,?,?,?,?)
                    on conflict(catalog_key,domain) do update set
                      regime=excluded.regime,
                      evidence_updated_at=excluded.evidence_updated_at,
                      evidence_seen_count=excluded.evidence_seen_count,
                      evidence_rows=excluded.evidence_rows,
                      evidence_epoch=excluded.evidence_epoch,
                      epoch_started_at=excluded.epoch_started_at,
                      decision_at=excluded.decision_at,
                      expires_at=excluded.expires_at
                    where
                      excluded.epoch_started_at > agent_optimizer_routing_checkpoint.epoch_started_at
                      or excluded.evidence_updated_at > agent_optimizer_routing_checkpoint.evidence_updated_at
                      or (
                        excluded.evidence_updated_at = agent_optimizer_routing_checkpoint.evidence_updated_at
                        and excluded.evidence_seen_count > agent_optimizer_routing_checkpoint.evidence_seen_count
                      )
                      or (
                        excluded.evidence_updated_at = agent_optimizer_routing_checkpoint.evidence_updated_at
                        and excluded.evidence_seen_count = agent_optimizer_routing_checkpoint.evidence_seen_count
                        and excluded.evidence_rows > agent_optimizer_routing_checkpoint.evidence_rows
                      )
                      or (
                        excluded.evidence_updated_at = agent_optimizer_routing_checkpoint.evidence_updated_at
                        and excluded.evidence_seen_count = agent_optimizer_routing_checkpoint.evidence_seen_count
                        and excluded.evidence_rows = agent_optimizer_routing_checkpoint.evidence_rows
                        and excluded.decision_at >= agent_optimizer_routing_checkpoint.decision_at
                      )
                      or (
                        agent_optimizer_routing_checkpoint.evidence_updated_at
                          > excluded.decision_at + 1e-12
                        and excluded.decision_at
                          >= agent_optimizer_routing_checkpoint.decision_at
                      )
                      or (
                        agent_optimizer_routing_checkpoint.epoch_started_at
                          > excluded.decision_at + 1e-12
                        and excluded.decision_at
                          >= agent_optimizer_routing_checkpoint.decision_at
                      )
                    """,
                    (
                        catalog_key,
                        domain,
                        regime,
                        evidence_updated_at,
                        evidence_seen_count,
                        evidence_rows,
                        effective_epoch,
                        effective_epoch_started_at,
                        decision_at,
                        expires_at,
                    ),
                )
                row = connection.execute(
                    """
                    select catalog_key,domain,regime,evidence_updated_at,
                           evidence_seen_count,evidence_rows,evidence_epoch,
                           epoch_started_at,decision_at,expires_at
                    from agent_optimizer_routing_checkpoint
                    where catalog_key=? and domain=?
                    """,
                    (catalog_key, domain),
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                self.memory._close(connection)
        return {
            "recorded": bool(cursor.rowcount),
            **(dict(row) if row is not None else {}),
        }


# Three days covers the entire minimum-four-row hysteresis band implied by the
# 14-day recency half-life and 3.5-row exit threshold. Longer-lived evidence still
# has to pass the current stay gate, so the checkpoint never overrides decay.
OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS = 3.0 * 24.0 * 60.0 * 60.0
OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_REFRESH_SECONDS = (
    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS / 2.0
)
_CHECKPOINT_STORE_ATTR = "_optimizer_routing_checkpoint_store"
_CHECKPOINT_REFRESH_ATTR = "_optimizer_routing_checkpoint_refresh_at"
_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def optimizer_observation_evidence_clock(
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    updated_at = max(
        (_finite_float(row.get("updated_at")) or 0.0 for row in observations),
        default=0.0,
    )
    seen_count = 0
    for row in observations:
        try:
            count = int(row.get("seen_count", 1) or 1)
        except (TypeError, ValueError):
            count = 1
        seen_count += max(1, count)
    return {
        "evidence_updated_at": updated_at,
        "evidence_seen_count": seen_count,
        "evidence_rows": len(observations),
    }


def _checkpoint_store(registry: Any) -> OptimizerRoutingCheckpointStore | None:
    meta_memory = getattr(registry, "optimizer_meta_memory", None)
    if meta_memory is None:
        return None
    store = getattr(registry, _CHECKPOINT_STORE_ATTR, None)
    if not isinstance(store, OptimizerRoutingCheckpointStore):
        store = OptimizerRoutingCheckpointStore(meta_memory)
        setattr(registry, _CHECKPOINT_STORE_ATTR, store)
    return store


def _checkpoint_refreshes(registry: Any) -> dict[str, float]:
    refreshes = getattr(registry, _CHECKPOINT_REFRESH_ATTR, None)
    if not isinstance(refreshes, dict):
        refreshes = {}
        setattr(registry, _CHECKPOINT_REFRESH_ATTR, refreshes)
    return refreshes


def _checkpoint_not_ahead(
    checkpoint: dict[str, Any],
    evidence_clock: dict[str, Any],
) -> bool:
    checkpoint_version = (
        float(checkpoint.get("evidence_updated_at", 0.0) or 0.0),
        int(checkpoint.get("evidence_seen_count", 0) or 0),
        int(checkpoint.get("evidence_rows", 0) or 0),
    )
    evidence_version = (
        float(evidence_clock["evidence_updated_at"]),
        int(evidence_clock["evidence_seen_count"]),
        int(evidence_clock["evidence_rows"]),
    )
    return checkpoint_version <= evidence_version


def install_optimizer_routing_checkpoint(optimizer_registry_cls: type) -> None:
    """Persist routing hysteresis plus monotone evidence epochs in one checkpoint."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import optimizer_observation_weighting as weighting
    from . import optimizer_routing_epoch as routing_epoch

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_durable_regime(self: Any, surface: str):
        regimes = weighting._routing_regimes(self)
        refreshes = _checkpoint_refreshes(self)
        store = _checkpoint_store(self)
        reader = getattr(self.memory, "optimizer_observations", None)
        now = time.time()
        previous_regime = regimes.get(surface)
        cold_observations: list[dict[str, Any]] | None = None
        checkpoint = (
            store.read(self.catalog_key, surface, now=now)
            if store is not None
            else None
        )
        if isinstance(checkpoint, dict):
            routing_epoch.set_routing_epoch_state(
                self,
                surface,
                evidence_epoch=int(checkpoint.get("evidence_epoch", 0) or 0),
                epoch_started_at=float(checkpoint.get("epoch_started_at", 0.0) or 0.0),
            )
        else:
            routing_epoch.set_routing_epoch_state(
                self,
                surface,
                evidence_epoch=0,
                epoch_started_at=0.0,
            )
        epoch_boundary = routing_epoch.routing_epoch_boundary(self, surface)

        if previous_regime is None and isinstance(checkpoint, dict) and callable(reader):
            if checkpoint.get("active_weighted"):
                cold_observations = routing_epoch.filter_routing_epoch_rows(
                    reader(self.catalog_key, surface),
                    timestamp_key="updated_at",
                    epoch_started_at=epoch_boundary,
                )
                clock = optimizer_observation_evidence_clock(cold_observations)
                if (
                    len(cold_observations) >= int(weighting.OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS)
                    and _checkpoint_not_ahead(checkpoint, clock)
                ):
                    regimes[surface] = weighting._ROUTING_REGIME_WEIGHTED
                    previous_regime = weighting._ROUTING_REGIME_WEIGHTED
                    refreshes[surface] = float(checkpoint.get("decision_at", now) or now)

        result = original_routing_context(self, surface)
        final_regime = regimes.get(surface)
        pending_epoch_started_at = routing_epoch.pending_routing_epoch_advance(self, surface)
        if (
            store is None
            or not callable(reader)
            or final_regime not in {
                weighting._ROUTING_REGIME_WEIGHTED,
                weighting._ROUTING_REGIME_FALLBACK,
            }
        ):
            return result

        last_refresh = float(refreshes.get(surface, 0.0) or 0.0)
        entered_weighted = bool(
            final_regime == weighting._ROUTING_REGIME_WEIGHTED
            and previous_regime != weighting._ROUTING_REGIME_WEIGHTED
        )
        exited_weighted = bool(
            final_regime == weighting._ROUTING_REGIME_FALLBACK
            and previous_regime == weighting._ROUTING_REGIME_WEIGHTED
        )
        refresh_due = bool(
            final_regime == weighting._ROUTING_REGIME_WEIGHTED
            and now - last_refresh >= OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_REFRESH_SECONDS
        )
        epoch_advance_due = pending_epoch_started_at is not None
        if not entered_weighted and not exited_weighted and not refresh_due and not epoch_advance_due:
            return result

        evidence_epoch_boundary = (
            float(pending_epoch_started_at)
            if pending_epoch_started_at is not None
            else epoch_boundary
        )
        observations = routing_epoch.filter_routing_epoch_rows(
            (
                cold_observations
                if cold_observations is not None
                else reader(self.catalog_key, surface)
            ),
            timestamp_key="updated_at",
            epoch_started_at=evidence_epoch_boundary,
        )
        if len(observations) < int(weighting.OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS):
            return result

        epoch_state = routing_epoch.routing_epoch_state(self, surface)
        checkpoint = store.record(
            self.catalog_key,
            surface,
            regime=final_regime,
            epoch_started_at=(
                float(pending_epoch_started_at)
                if pending_epoch_started_at is not None
                else float(epoch_state.get("epoch_started_at", 0.0) or 0.0)
            ),
            decision_at=now,
            ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
            **optimizer_observation_evidence_clock(observations),
        )
        routing_epoch.set_routing_epoch_state(
            self,
            surface,
            evidence_epoch=int(checkpoint.get("evidence_epoch", 0) or 0),
            epoch_started_at=float(checkpoint.get("epoch_started_at", 0.0) or 0.0),
        )
        if (
            pending_epoch_started_at is not None
            and float(checkpoint.get("epoch_started_at", 0.0) or 0.0)
            + 1e-12
            >= float(pending_epoch_started_at)
        ):
            routing_epoch.clear_pending_routing_epoch_advance(self, surface)
        if checkpoint.get("recorded"):
            refreshes[surface] = now
        else:
            refreshes[surface] = float(checkpoint.get("decision_at", last_refresh) or last_refresh)
        return result

    optimizer_registry_cls._routing_context = routing_context_with_durable_regime

    original_fork = optimizer_registry_cls.fork

    def fork_with_checkpoint_refresh_state(self: Any):
        clone = original_fork(self)
        refreshes = getattr(self, _CHECKPOINT_REFRESH_ATTR, None)
        if isinstance(refreshes, dict):
            setattr(clone, _CHECKPOINT_REFRESH_ATTR, dict(refreshes))
        return clone

    optimizer_registry_cls.fork = fork_with_checkpoint_refresh_state

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_checkpoint(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        epoch_states = routing_epoch.routing_epoch_states(self)
        router.update(
            {
                "optimizer_observation_regime_checkpoint": "optimizer_meta_memory_evidence_fenced_ttl",
                "optimizer_observation_regime_checkpoint_ttl_seconds": (
                    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS
                ),
                "optimizer_observation_regime_checkpoint_refresh_seconds": (
                    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_REFRESH_SECONDS
                ),
                "optimizer_observation_regime_checkpoint_clock": (
                    "caller_decision_clock_clamp_and_legacy_future_repair"
                ),
                "optimizer_observation_regime_checkpoint_authority": "routing_hysteresis_only",
                "optimizer_observation_regime_checkpoint_evaluator_calls": 0,
                "optimizer_observation_routing_epoch": "durable_checkpoint_change_point_fence",
                "optimizer_observation_routing_epoch_boundary": "confirmed_recent_oldest_at",
                "optimizer_observation_routing_epoch_states": dict(epoch_states),
                "optimizer_observation_routing_epoch_authority": "routing_descriptor_only",
                "optimizer_observation_routing_epoch_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_checkpoint
    _INSTALLED = True


__all__ = [
    "OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_REFRESH_SECONDS",
    "OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS",
    "OptimizerRoutingCheckpointStore",
    "install_optimizer_routing_checkpoint",
    "optimizer_observation_evidence_clock",
]
