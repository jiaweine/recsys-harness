from __future__ import annotations

from math import isfinite
import time
from typing import Any


class OptimizerRoutingCheckpointStore:
    """Durable routing-only hysteresis checkpoint over optimizer meta storage.

    The checkpoint never carries optimizer credit, candidate quality, warm-start
    rows, or promotion authority. Its only purpose is to preserve the prior
    weighted/fallback regime across process restarts while evidence is revalidated
    by the existing observation confidence gates.
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
                           evidence_seen_count,evidence_rows,decision_at,expires_at
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
        result["active_weighted"] = bool(
            result.get("regime") == "weighted"
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
        decision_at: float | None = None,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        """Persist one decision unless a newer evidence/decision version already won."""

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

        if decision_at is None:
            decision_at = time.time()
        decision_at = float(decision_at)
        ttl_seconds = float(ttl_seconds)
        if not isfinite(decision_at):
            raise ValueError("optimizer routing decision_at must be finite")
        if not isfinite(ttl_seconds) or ttl_seconds <= 0.0:
            raise ValueError("optimizer routing checkpoint ttl_seconds must be finite and > 0")
        expires_at = decision_at + ttl_seconds if regime == "weighted" else decision_at

        with self.memory._lock:
            connection = self.memory._connect()
            try:
                connection.execute("pragma busy_timeout=10000")
                connection.execute("begin immediate")
                cursor = connection.execute(
                    """
                    insert into agent_optimizer_routing_checkpoint(
                      catalog_key,domain,regime,evidence_updated_at,evidence_seen_count,
                      evidence_rows,decision_at,expires_at
                    ) values(?,?,?,?,?,?,?,?)
                    on conflict(catalog_key,domain) do update set
                      regime=excluded.regime,
                      evidence_updated_at=excluded.evidence_updated_at,
                      evidence_seen_count=excluded.evidence_seen_count,
                      evidence_rows=excluded.evidence_rows,
                      decision_at=excluded.decision_at,
                      expires_at=excluded.expires_at
                    where
                      excluded.evidence_updated_at > agent_optimizer_routing_checkpoint.evidence_updated_at
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
                    """,
                    (
                        catalog_key,
                        domain,
                        regime,
                        evidence_updated_at,
                        evidence_seen_count,
                        evidence_rows,
                        decision_at,
                        expires_at,
                    ),
                )
                row = connection.execute(
                    """
                    select catalog_key,domain,regime,evidence_updated_at,
                           evidence_seen_count,evidence_rows,decision_at,expires_at
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
    """Persist only routing hysteresis state around the existing weighted router."""

    global _INSTALLED
    if _INSTALLED:
        return

    from . import optimizer_observation_weighting as weighting

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_durable_regime(self: Any, surface: str):
        regimes = weighting._routing_regimes(self)
        refreshes = _checkpoint_refreshes(self)
        store = _checkpoint_store(self)
        reader = getattr(self.memory, "optimizer_observations", None)
        now = time.time()
        previous_regime = regimes.get(surface)
        cold_observations: list[dict[str, Any]] | None = None

        if previous_regime is None and store is not None and callable(reader):
            checkpoint = store.read(self.catalog_key, surface, now=now)
            if isinstance(checkpoint, dict) and checkpoint.get("active_weighted"):
                cold_observations = reader(self.catalog_key, surface)
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
        if not entered_weighted and not exited_weighted and not refresh_due:
            return result

        observations = (
            cold_observations
            if cold_observations is not None
            else reader(self.catalog_key, surface)
        )
        if len(observations) < int(weighting.OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS):
            return result

        checkpoint = store.record(
            self.catalog_key,
            surface,
            regime=final_regime,
            decision_at=now,
            ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
            **optimizer_observation_evidence_clock(observations),
        )
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
        router.update(
            {
                "optimizer_observation_regime_checkpoint": "optimizer_meta_memory_evidence_fenced_ttl",
                "optimizer_observation_regime_checkpoint_ttl_seconds": (
                    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS
                ),
                "optimizer_observation_regime_checkpoint_refresh_seconds": (
                    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_REFRESH_SECONDS
                ),
                "optimizer_observation_regime_checkpoint_authority": "routing_hysteresis_only",
                "optimizer_observation_regime_checkpoint_evaluator_calls": 0,
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
