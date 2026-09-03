from __future__ import annotations

from contextvars import ContextVar
from math import isfinite
from typing import Any

from . import optimizer_observation_memory as observation_memory
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
    decision_at = _finite_float(checkpoint.get("decision_at"))
    if decision_at is not None and decision_at >= 0.0:
        epoch_started_at = min(epoch_started_at, decision_at)
    return (evidence_epoch, epoch_started_at)


def _same_epoch_token(
    left: tuple[int, float],
    right: tuple[int, float],
) -> bool:
    return left[0] == right[0] and abs(left[1] - right[1]) <= 1e-12


def _checkpoint_token(
    checkpoint: dict[str, Any] | None,
) -> tuple[str, float, int, int, int, float, float, float]:
    if not isinstance(checkpoint, dict):
        return ("", 0.0, 0, 0, 0, 0.0, 0.0, 0.0)
    try:
        evidence_seen_count = max(0, int(checkpoint.get("evidence_seen_count", 0) or 0))
    except (TypeError, ValueError):
        evidence_seen_count = 0
    try:
        evidence_rows = max(0, int(checkpoint.get("evidence_rows", 0) or 0))
    except (TypeError, ValueError):
        evidence_rows = 0
    evidence_epoch, epoch_started_at = _epoch_token(checkpoint)
    decision_at = _finite_float(checkpoint.get("decision_at")) or 0.0
    evidence_updated_at = _finite_float(checkpoint.get("evidence_updated_at")) or 0.0
    if decision_at >= 0.0:
        evidence_updated_at = min(evidence_updated_at, decision_at)
    return (
        str(checkpoint.get("regime") or ""),
        evidence_updated_at,
        evidence_seen_count,
        evidence_rows,
        evidence_epoch,
        epoch_started_at,
        decision_at,
        _finite_float(checkpoint.get("expires_at")) or 0.0,
    )


def _same_checkpoint_token(
    left: tuple[str, float, int, int, int, float, float, float],
    right: tuple[str, float, int, int, int, float, float, float],
) -> bool:
    return bool(
        left[0] == right[0]
        and abs(left[1] - right[1]) <= 1e-12
        and left[2:5] == right[2:5]
        and abs(left[5] - right[5]) <= 1e-12
        and abs(left[6] - right[6]) <= 1e-12
        and abs(left[7] - right[7]) <= 1e-12
    )


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


def _observation_revision_target(
    registry: Any,
    surface: str,
) -> tuple[Any, str, str] | None:
    surface = str(surface or "").strip()
    if surface not in {"search", "recommend"}:
        return None
    memory = getattr(registry, "memory", None)
    if memory is None:
        return None
    catalog_key = str(getattr(registry, "catalog_key", "") or "")
    scoped_catalog_key = getattr(memory, "scoped_catalog_key", None)
    if callable(scoped_catalog_key):
        catalog_key = str(scoped_catalog_key(catalog_key, surface))
    base_memory = getattr(memory, "base_memory", memory)
    if not hasattr(base_memory, "_connect") or not hasattr(base_memory, "_lock"):
        return None
    return base_memory, catalog_key, surface


def _history_high_water(
    connection: Any,
    *,
    catalog_key: str,
    domain: str,
) -> int:
    row = connection.execute(
        """
        select coalesce(max(id), 0) as high_water
        from agent_optimizer_observation_history
        where catalog_key=? and domain=?
        """,
        (catalog_key, domain),
    ).fetchone()
    if row is None:
        return 0
    try:
        return max(0, int(row["high_water"] or 0))
    except (KeyError, TypeError):
        return max(0, int(row[0] or 0))


def _read_observation_revision(
    registry: Any,
    surface: str,
) -> dict[str, Any] | None:
    target = _observation_revision_target(registry, surface)
    if target is None:
        return None
    memory, catalog_key, domain = target
    observation_memory._ensure_optimizer_observation_table(memory)
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("pragma busy_timeout=10000")
            high_water = _history_high_water(
                connection,
                catalog_key=catalog_key,
                domain=domain,
            )
        finally:
            memory._close(connection)
    return {
        "catalog_key": catalog_key,
        "domain": domain,
        "high_water": high_water,
    }


def install_optimizer_routing_epoch_fence(optimizer_registry_cls: type) -> None:
    """Fence routing decisions against concurrent checkpoint or evidence advancement.

    The existing optimizer routing checkpoint remains the only durable routing
    state. This layer adds no table, lease, heartbeat, evaluator call, or serving
    authority. A routing call snapshots the checkpoint epoch token, the full
    checkpoint row version, and the paid-observation history high-water at entry.
    Checkpoint writes must still observe those values inside the existing SQLite
    write transaction. The completed routing decision is revalidated before return.
    Concurrent checkpoint retirement, epoch advancement, or any paid observation
    commit therefore fails closed to the pre-observation router instead of allowing
    a stale decision to act on an older routing-state or evidence view.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    store_cls = checkpoint_runtime.OptimizerRoutingCheckpointStore
    original_record = store_cls.record
    original_retire_expired = store_cls.retire_expired

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
        evidence_updated_at = min(evidence_updated_at, decision_at)
        epoch_started_at = min(epoch_started_at, decision_at)
        expires_at = decision_at + ttl_seconds if regime == "weighted" else decision_at

        expected_token = context.get("expected_token")
        if not isinstance(expected_token, tuple) or len(expected_token) != 2:
            expected_token = (0, 0.0)
        expected_checkpoint_token = context.get("expected_checkpoint_token")
        if not isinstance(expected_checkpoint_token, tuple) or len(expected_checkpoint_token) != 8:
            expected_checkpoint_token = _checkpoint_token(None)
        expected_revision = context.get("expected_observation_revision")
        observation_catalog_key = str(context.get("observation_catalog_key") or "")
        observation_domain = str(context.get("surface") or "")

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
                existing_checkpoint_token = _checkpoint_token(existing_dict)
                if not _same_epoch_token(existing_token, expected_token):
                    context["conflict"] = True
                    context["conflict_reason"] = "concurrent_epoch_advance"
                    context["observed_token"] = existing_token
                    context["observed_checkpoint_token"] = existing_checkpoint_token
                    connection.commit()
                    return {
                        "recorded": False,
                        "stale_epoch_fence": True,
                        "stale_observation_revision_fence": False,
                        **(existing_dict or {}),
                    }
                if not _same_checkpoint_token(
                    existing_checkpoint_token,
                    expected_checkpoint_token,
                ):
                    context["conflict"] = True
                    context["conflict_reason"] = "concurrent_checkpoint_advance"
                    context["observed_checkpoint_token"] = existing_checkpoint_token
                    connection.commit()
                    return {
                        "recorded": False,
                        "stale_epoch_fence": False,
                        "stale_observation_revision_fence": False,
                        **(existing_dict or {}),
                    }

                if (
                    isinstance(expected_revision, int)
                    and observation_catalog_key
                    and observation_domain == domain
                ):
                    observed_revision = _history_high_water(
                        connection,
                        catalog_key=observation_catalog_key,
                        domain=observation_domain,
                    )
                    context["observed_observation_revision"] = observed_revision
                    if observed_revision != expected_revision:
                        context["conflict"] = True
                        context["conflict_reason"] = "concurrent_observation_advance"
                        connection.commit()
                        return {
                            "recorded": False,
                            "stale_epoch_fence": False,
                            "stale_observation_revision_fence": True,
                            **(existing_dict or {}),
                        }

                current_epoch = existing_token[0]
                current_epoch_started_at = (
                    float(existing_dict.get("epoch_started_at", 0.0) or 0.0)
                    if existing_dict is not None
                    else 0.0
                )
                if (
                    existing_dict is not None
                    and current_epoch_started_at > decision_at + 1e-12
                    and decision_at >= float(existing_dict.get("decision_at", 0.0) or 0.0)
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
                      or (
                        excluded.regime = 'weighted'
                        and agent_optimizer_routing_checkpoint.regime = 'weighted'
                        and excluded.evidence_updated_at
                          = agent_optimizer_routing_checkpoint.evidence_updated_at
                        and excluded.evidence_seen_count
                          = agent_optimizer_routing_checkpoint.evidence_seen_count
                        and excluded.evidence_rows
                          = agent_optimizer_routing_checkpoint.evidence_rows
                        and excluded.evidence_epoch
                          = agent_optimizer_routing_checkpoint.evidence_epoch
                        and excluded.epoch_started_at
                          = agent_optimizer_routing_checkpoint.epoch_started_at
                        and agent_optimizer_routing_checkpoint.decision_at
                          > excluded.expires_at + 1e-12
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
            "stale_observation_revision_fence": False,
            **(dict(row) if row is not None else {}),
        }
        authorized_checkpoint_token = _checkpoint_token(result)
        context["authorized_token"] = _epoch_token(result)
        context["expected_checkpoint_token"] = authorized_checkpoint_token
        context["authorized_checkpoint_token"] = authorized_checkpoint_token
        return result

    store_cls.record = record_with_expected_epoch_fence

    def retire_expired_with_expected_checkpoint_fence(
        self: Any,
        catalog_key: str,
        domain: str,
        *,
        expected_decision_at: float,
        expected_expires_at: float,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        result = original_retire_expired(
            self,
            catalog_key,
            domain,
            expected_decision_at=expected_decision_at,
            expected_expires_at=expected_expires_at,
            observed_at=observed_at,
        )
        context = _ROUTING_EPOCH_FENCE_CONTEXT.get()
        if not isinstance(context, dict) or context.get("store") is not self:
            return result

        observed_checkpoint_token = _checkpoint_token(result)
        expected_checkpoint_token = context.get("expected_checkpoint_token")
        if not isinstance(expected_checkpoint_token, tuple) or len(expected_checkpoint_token) != 8:
            expected_checkpoint_token = _checkpoint_token(None)
        if result.get("retired"):
            context["expected_checkpoint_token"] = observed_checkpoint_token
            context["authorized_checkpoint_token"] = observed_checkpoint_token
            context["authorized_token"] = _epoch_token(result)
        elif not _same_checkpoint_token(
            observed_checkpoint_token,
            expected_checkpoint_token,
        ):
            context["conflict"] = True
            context["conflict_reason"] = "concurrent_checkpoint_advance"
            context["observed_checkpoint_token"] = observed_checkpoint_token
        return result

    store_cls.retire_expired = retire_expired_with_expected_checkpoint_fence

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_durable_epoch_fence(self: Any, surface: str):
        expected_observation = _read_observation_revision(self, surface)
        expected_revision = (
            int(expected_observation["high_water"])
            if isinstance(expected_observation, dict)
            else None
        )
        observation_catalog_key = (
            str(expected_observation.get("catalog_key") or "")
            if isinstance(expected_observation, dict)
            else ""
        )
        store = checkpoint_runtime._checkpoint_store(self)
        if store is None:
            result = original_routing_context(self, surface)
            observed_observation = _read_observation_revision(self, surface)
            observed_revision = (
                int(observed_observation["high_water"])
                if isinstance(observed_observation, dict)
                else None
            )
            states = _fence_states(self)
            if expected_revision is not None and observed_revision != expected_revision:
                routing_epoch.clear_pending_routing_epoch_advance(self, surface)
                weighting._routing_regimes(self)[surface] = weighting._ROUTING_REGIME_FALLBACK
                states[surface] = {
                    "status": "observation_conflict",
                    "reason": "concurrent_observation_advance",
                    "action": "pre_observation_fallback",
                    "expected_evidence_epoch": 0,
                    "expected_epoch_started_at": 0.0,
                    "observed_evidence_epoch": 0,
                    "observed_epoch_started_at": 0.0,
                    "expected_observation_revision": expected_revision,
                    "observed_observation_revision": observed_revision,
                    "new_evaluator_calls": 0,
                }
                return weighting._pre_observation_context(self, surface, result)
            states[surface] = {
                "status": "validated",
                "reason": "routing_fences_current",
                "action": "none",
                "expected_evidence_epoch": 0,
                "expected_epoch_started_at": 0.0,
                "observed_evidence_epoch": 0,
                "observed_epoch_started_at": 0.0,
                "expected_observation_revision": expected_revision,
                "observed_observation_revision": observed_revision,
                "new_evaluator_calls": 0,
            }
            return result

        initial = store.read(
            self.catalog_key,
            surface,
            now=checkpoint_runtime.time.time(),
        )
        expected_token = _epoch_token(initial)
        expected_checkpoint_token = _checkpoint_token(initial)
        context: dict[str, Any] = {
            "store": store,
            "registry": self,
            "surface": str(surface),
            "expected_token": expected_token,
            "authorized_token": expected_token,
            "observed_token": expected_token,
            "expected_checkpoint_token": expected_checkpoint_token,
            "authorized_checkpoint_token": expected_checkpoint_token,
            "observed_checkpoint_token": expected_checkpoint_token,
            "observation_catalog_key": observation_catalog_key,
            "expected_observation_revision": expected_revision,
            "observed_observation_revision": expected_revision,
            "conflict": False,
            "conflict_reason": "",
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
            token_conflict = not _same_epoch_token(observed_token, authorized_token)
            observed_checkpoint_token = _checkpoint_token(latest)
            authorized_checkpoint_token = context.get(
                "authorized_checkpoint_token",
                expected_checkpoint_token,
            )
            if (
                not isinstance(authorized_checkpoint_token, tuple)
                or len(authorized_checkpoint_token) != 8
            ):
                authorized_checkpoint_token = expected_checkpoint_token
            checkpoint_conflict = not _same_checkpoint_token(
                observed_checkpoint_token,
                authorized_checkpoint_token,
            )
            context["observed_checkpoint_token"] = observed_checkpoint_token

            observed_observation = _read_observation_revision(self, surface)
            observed_revision = (
                int(observed_observation["high_water"])
                if isinstance(observed_observation, dict)
                else None
            )
            context["observed_observation_revision"] = observed_revision
            observation_conflict = bool(
                expected_revision is not None and observed_revision != expected_revision
            )
            if observation_conflict:
                context["conflict_reason"] = "concurrent_observation_advance"
            elif token_conflict and not context.get("conflict_reason"):
                context["conflict_reason"] = "concurrent_epoch_advance"
            elif checkpoint_conflict and not context.get("conflict_reason"):
                context["conflict_reason"] = "concurrent_checkpoint_advance"

            conflict = bool(
                context.get("conflict")
                or token_conflict
                or checkpoint_conflict
                or observation_conflict
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
                reason = str(context.get("conflict_reason") or "concurrent_epoch_advance")
                states[surface] = {
                    "status": (
                        "observation_conflict"
                        if reason == "concurrent_observation_advance"
                        else (
                            "checkpoint_conflict"
                            if reason == "concurrent_checkpoint_advance"
                            else "epoch_conflict"
                        )
                    ),
                    "reason": reason,
                    "action": "pre_observation_fallback",
                    "expected_evidence_epoch": expected_token[0],
                    "expected_epoch_started_at": expected_token[1],
                    "observed_evidence_epoch": observed_token[0],
                    "observed_epoch_started_at": observed_token[1],
                    "expected_observation_revision": expected_revision,
                    "observed_observation_revision": observed_revision,
                    "new_evaluator_calls": 0,
                }
                return weighting._pre_observation_context(self, surface, result)

            states[surface] = {
                "status": "validated",
                "reason": "routing_fences_current",
                "action": "none",
                "expected_evidence_epoch": expected_token[0],
                "expected_epoch_started_at": expected_token[1],
                "observed_evidence_epoch": observed_token[0],
                "observed_epoch_started_at": observed_token[1],
                "expected_observation_revision": expected_revision,
                "observed_observation_revision": observed_revision,
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
                "optimizer_observation_routing_checkpoint_version_fence": "full_checkpoint_row_version",
                "optimizer_observation_routing_checkpoint_conflict_action": "pre_observation_fallback",
                "optimizer_observation_routing_revision_fence": "history_autoincrement_high_water",
                "optimizer_observation_routing_revision_scope": "entry_checkpoint_write_and_post_decision_revision_revalidation",
                "optimizer_observation_routing_revision_conflict_action": "pre_observation_fallback",
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
