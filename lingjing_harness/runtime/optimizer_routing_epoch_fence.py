from __future__ import annotations

from contextvars import ContextVar
from math import isfinite
from typing import Any

from . import optimizer_observation_weighting as weighting
from . import optimizer_routing_checkpoint as checkpoint_runtime
from . import optimizer_routing_epoch as routing_epoch


_ROUTING_EPOCH_FENCE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "optimizer_routing_epoch_fence_context",
    default=None,
)
_ROUTING_EPOCH_FENCE_STATE_ATTR = "_optimizer_observation_routing_epoch_fence_states"
_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _epoch_token(checkpoint: dict[str, Any] | None) -> tuple[int, float]:
    if not isinstance(checkpoint, dict):
        return (0, 0.0)
    try:
        evidence_epoch = max(0, int(checkpoint.get("evidence_epoch", 0) or 0))
    except (TypeError, ValueError):
        evidence_epoch = 0
    epoch_started_at = max(
        0.0,
        _finite_float(checkpoint.get("epoch_started_at")) or 0.0,
    )
    return (evidence_epoch, epoch_started_at)


def _same_epoch_token(
    left: tuple[int, float],
    right: tuple[int, float],
) -> bool:
    return left[0] == right[0] and abs(left[1] - right[1]) <= 1e-12


def _fence_states(registry: Any) -> dict[str, dict[str, Any]]:
    states = getattr(registry, _ROUTING_EPOCH_FENCE_STATE_ATTR, None)
    if not isinstance(states, dict):
        states = {}
        setattr(registry, _ROUTING_EPOCH_FENCE_STATE_ATTR, states)
    return states


def _checkpoint_row(
    connection: Any,
    *,
    catalog_key: str,
    domain: str,
) -> Any:
    return connection.execute(
        """
        select catalog_key,domain,regime,evidence_updated_at,
               evidence_seen_count,evidence_rows,evidence_epoch,
               epoch_started_at,decision_at,expires_at
        from agent_optimizer_routing_checkpoint
        where catalog_key=? and domain=?
        """,
        (catalog_key, domain),
    ).fetchone()


def install_optimizer_routing_epoch_fence(optimizer_registry_cls: type) -> None:
    """Fence routing decisions against concurrent durable epoch advancement.

    The existing optimizer routing checkpoint remains the only durable routing
    state. This layer adds no table, lease, heartbeat, evaluator call, or serving
    authority. A routing call snapshots the checkpoint epoch token; any checkpoint
    write must still observe that exact token inside the existing SQLite write
    transaction. The completed routing decision is then revalidated against the
    durable token before it is returned. Concurrent epoch advancement therefore
    fails closed to the pre-observation router for that call instead of allowing a
    stale process to overwrite or act on evidence from an older regime.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    store_cls = checkpoint_runtime.OptimizerRoutingCheckpointStore
    original_record = store_cls.record

    def record_with_expected_epoch_fence(
        self: Any,
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
        context = _ROUTING_EPOCH_FENCE_CONTEXT.get()
        if not isinstance(context, dict) or context.get("store") is not self:
            return original_record(
                self,
                catalog_key,
                domain,
                regime=regime,
                evidence_updated_at=evidence_updated_at,
                evidence_seen_count=evidence_seen_count,
                evidence_rows=evidence_rows,
                epoch_started_at=epoch_started_at,
                decision_at=decision_at,
                ttl_seconds=ttl_seconds,
            )

        domain = str(domain or "").strip()
        regime = str(regime or "").strip().lower()
        if domain not in {"search", "recommend"}:
            raise ValueError("optimizer routing checkpoint domain must be search or recommend")
        if regime not in {"weighted", "fallback"}:
            raise ValueError("optimizer routing checkpoint regime must be weighted or fallback")
        scoped_catalog_key = self._scoped_catalog_key(catalog_key, domain)

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
            decision_at = checkpoint_runtime.time.time()
        decision_at = float(decision_at)
        ttl_seconds = float(ttl_seconds)
        if not isfinite(decision_at):
            raise ValueError("optimizer routing decision_at must be finite")
        if not isfinite(ttl_seconds) or ttl_seconds <= 0.0:
            raise ValueError("optimizer routing checkpoint ttl_seconds must be finite and > 0")
        expires_at = decision_at + ttl_seconds if regime == "weighted" else decision_at

        expected_token = context.get("expected_token")
        if not isinstance(expected_token, tuple) or len(expected_token) != 2:
            expected_token = (0, 0.0)

        with self.memory._lock:
            connection = self.memory._connect()
            try:
                connection.execute("pragma busy_timeout=10000")
                connection.execute("begin immediate")
                existing = _checkpoint_row(
                    connection,
                    catalog_key=scoped_catalog_key,
                    domain=domain,
                )
                existing_dict = dict(existing) if existing is not None else None
                existing_token = _epoch_token(existing_dict)
                if not _same_epoch_token(existing_token, expected_token):
                    context["conflict"] = True
                    context["observed_token"] = existing_token
                    connection.commit()
                    return {
                        "recorded": False,
                        "stale_epoch_fence": True,
                        **(existing_dict or {}),
                    }

                current_epoch = existing_token[0]
                current_epoch_started_at = existing_token[1]
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
                    """,
                    (
                        scoped_catalog_key,
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
                row = _checkpoint_row(
                    connection,
                    catalog_key=scoped_catalog_key,
                    domain=domain,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                self.memory._close(connection)

        result = {
            "recorded": bool(cursor.rowcount),
            "stale_epoch_fence": False,
            **(dict(row) if row is not None else {}),
        }
        context["authorized_token"] = _epoch_token(result)
        return result

    store_cls.record = record_with_expected_epoch_fence

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_durable_epoch_fence(self: Any, surface: str):
        store = checkpoint_runtime._checkpoint_store(self)
        if store is None:
            return original_routing_context(self, surface)

        initial = store.read(
            self.catalog_key,
            surface,
            now=checkpoint_runtime.time.time(),
        )
        expected_token = _epoch_token(initial)
        context: dict[str, Any] = {
            "store": store,
            "registry": self,
            "surface": str(surface),
            "expected_token": expected_token,
            "authorized_token": expected_token,
            "observed_token": expected_token,
            "conflict": False,
        }
        token = _ROUTING_EPOCH_FENCE_CONTEXT.set(context)
        try:
            result = original_routing_context(self, surface)
            latest = store.read(
                self.catalog_key,
                surface,
                now=checkpoint_runtime.time.time(),
            )
            observed_token = _epoch_token(latest)
            authorized_token = context.get("authorized_token", expected_token)
            conflict = bool(context.get("conflict")) or not _same_epoch_token(
                observed_token,
                authorized_token,
            )
            states = _fence_states(self)
            if conflict:
                if isinstance(latest, dict):
                    routing_epoch.set_routing_epoch_state(
                        self,
                        surface,
                        evidence_epoch=int(latest.get("evidence_epoch", 0) or 0),
                        epoch_started_at=float(latest.get("epoch_started_at", 0.0) or 0.0),
                    )
                else:
                    routing_epoch.set_routing_epoch_state(
                        self,
                        surface,
                        evidence_epoch=0,
                        epoch_started_at=0.0,
                    )
                routing_epoch.clear_pending_routing_epoch_advance(self, surface)
                weighting._routing_regimes(self)[surface] = weighting._ROUTING_REGIME_FALLBACK
                states[surface] = {
                    "status": "epoch_conflict",
                    "reason": "concurrent_epoch_advance",
                    "action": "pre_observation_fallback",
                    "expected_evidence_epoch": expected_token[0],
                    "expected_epoch_started_at": expected_token[1],
                    "observed_evidence_epoch": observed_token[0],
                    "observed_epoch_started_at": observed_token[1],
                    "new_evaluator_calls": 0,
                }
                return weighting._pre_observation_context(self, surface, result)

            states[surface] = {
                "status": "validated",
                "reason": "epoch_token_current",
                "action": "none",
                "expected_evidence_epoch": expected_token[0],
                "expected_epoch_started_at": expected_token[1],
                "observed_evidence_epoch": observed_token[0],
                "observed_epoch_started_at": observed_token[1],
                "new_evaluator_calls": 0,
            }
            return result
        finally:
            _ROUTING_EPOCH_FENCE_CONTEXT.reset(token)

    optimizer_registry_cls._routing_context = routing_context_with_durable_epoch_fence

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_epoch_fence(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        states = getattr(self, _ROUTING_EPOCH_FENCE_STATE_ATTR, None)
        router.update(
            {
                "optimizer_observation_routing_epoch_cas": "transactional_expected_epoch_token",
                "optimizer_observation_routing_epoch_return_validation": "post_decision_checkpoint_revalidation",
                "optimizer_observation_routing_epoch_conflict_action": "pre_observation_fallback",
                "optimizer_observation_routing_epoch_fence_states": (
                    dict(states) if isinstance(states, dict) else {}
                ),
                "optimizer_observation_routing_epoch_fence_authority": "routing_descriptor_only",
                "optimizer_observation_routing_epoch_fence_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_epoch_fence
    _INSTALLED = True


__all__ = [
    "install_optimizer_routing_epoch_fence",
]
