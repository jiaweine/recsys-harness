"""Stable durable-memory surface with strategy credit.

``memory_core`` keeps the original SQLite implementation.  This layer adds two
orthogonal identities and one durable credit ledger:

- strategy identity survives appended production outcomes but changes with the
  product-owned RewardSpec;
- workspace evidence revision is handled elsewhere and can change on every log
  snapshot;
- validated arm credit records both accepted and rejected mutations so future
  evolution learns from failures instead of remembering only winners.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import blake2b
import json
import time
from typing import Any

from lingjing_harness.domain import Catalog
from .memory_core import AgentMemory as _CoreAgentMemory
from .memory_core import catalog_fingerprint as _catalog_fingerprint


def catalog_fingerprint(catalog: Catalog) -> str:
    """Fingerprint the stable strategy context, not the mutable evidence snapshot.

    Items, interactions and relevance labels retain the historical fingerprint.
    Production events are deliberately excluded because appending fresh outcomes
    should trigger re-evaluation, not erase every learned strategy.  RewardSpec is
    included because changing the business objective invalidates prior credit.
    """

    base = _catalog_fingerprint(catalog)
    if catalog.reward_spec is None:
        return base
    reward = json.dumps(
        catalog.reward_spec.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = blake2b(digest_size=12)
    digest.update(f"{base}|reward-contract|{reward}".encode("utf-8"))
    return digest.hexdigest()


def _stable_event_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


class AgentMemory(_CoreAgentMemory):
    """Core memory plus idempotent positive/negative strategy-arm credit."""

    CREDIT_READ_BUDGET = 96

    def _init(self) -> None:
        super()._init()
        sql = """
        create table if not exists agent_strategy_credit(
          catalog_key text not null,
          domain text not null,
          arm_key text not null,
          positive integer not null default 0,
          negative integer not null default 0,
          trials integer not null default 0,
          reward_sum real not null default 0,
          evidence integer not null default 0,
          last_outcome text not null,
          last_reason text not null,
          created_at real not null,
          updated_at real not null,
          primary key(catalog_key,domain,arm_key)
        );
        create index if not exists idx_agent_strategy_credit_lookup
          on agent_strategy_credit(catalog_key,domain,updated_at desc);
        create table if not exists agent_strategy_credit_events(
          event_key text primary key,
          catalog_key text not null,
          domain text not null,
          arm_key text not null,
          outcome text not null,
          reward_delta real not null,
          evidence integer not null,
          reason text not null,
          payload text not null,
          created_at real not null
        );
        create index if not exists idx_agent_strategy_credit_events_lookup
          on agent_strategy_credit_events(catalog_key,domain,created_at desc);
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(sql)
                conn.commit()
            finally:
                self._close(conn)

    def remember_strategy(
        self,
        catalog_key: str,
        domain: str,
        config: dict[str, Any],
        *,
        score: float,
        evidence: int,
        status: str = "trusted",
        payload: dict[str, Any] | None = None,
        invocation_id: str | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        enriched = dict(payload or {})
        evolution = (tool_result or {}).get("evolution") if isinstance(tool_result, dict) else None
        if isinstance(evolution, dict):
            signature = evolution.get("selected_signature")
            if isinstance(signature, list) and signature:
                enriched.setdefault("selected_signature", [str(value) for value in signature])
        return super().remember_strategy(
            catalog_key,
            domain,
            config,
            score=score,
            evidence=evidence,
            status=status,
            payload=enriched,
            invocation_id=invocation_id,
            tool_result=tool_result,
        )

    def record_strategy_credit(
        self,
        catalog_key: str,
        domain: str,
        arm: str,
        *,
        outcome: str,
        reward_delta: float = 0.0,
        evidence: int = 0,
        reason: str = "",
        event_key: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        outcome = str(outcome or "").strip().lower()
        if outcome not in {"accepted", "rejected", "rollback"}:
            raise ValueError("strategy credit outcome must be accepted, rejected or rollback")
        arm = str(arm or "").strip()
        domain = str(domain or "").strip()
        if not arm or arm == "local:neutral" or not domain:
            return {"recorded": False, "reason": "neutral_or_empty_credit"}
        reward_delta = float(reward_delta)
        evidence = max(0, int(evidence or 0))
        reason = str(reason or outcome)
        payload = dict(payload or {})
        event_key = event_key or _stable_event_key(
            {
                "catalog_key": catalog_key,
                "domain": domain,
                "arm": arm,
                "outcome": outcome,
                "reward_delta": round(reward_delta, 8),
                "evidence": evidence,
                "reason": reason,
                "payload": payload,
            }
        )
        now = time.time()
        positive = 1 if outcome == "accepted" else 0
        negative = 1 if outcome in {"rejected", "rollback"} else 0
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    insert or ignore into agent_strategy_credit_events(
                      event_key,catalog_key,domain,arm_key,outcome,reward_delta,evidence,reason,payload,created_at
                    ) values(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_key,
                        catalog_key,
                        domain,
                        arm,
                        outcome,
                        reward_delta,
                        evidence,
                        reason,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
                if cursor.rowcount == 0:
                    row = conn.execute(
                        "select positive,negative,trials,reward_sum,evidence,last_outcome,last_reason,updated_at from agent_strategy_credit where catalog_key=? and domain=? and arm_key=?",
                        (catalog_key, domain, arm),
                    ).fetchone()
                    conn.commit()
                    return {
                        "recorded": False,
                        "deduplicated": True,
                        "domain": domain,
                        "arm": arm,
                        **(dict(row) if row else {}),
                    }
                conn.execute(
                    """
                    insert into agent_strategy_credit(
                      catalog_key,domain,arm_key,positive,negative,trials,reward_sum,evidence,
                      last_outcome,last_reason,created_at,updated_at
                    ) values(?,?,?,?,?,1,?,?,?,?,?,?)
                    on conflict(catalog_key,domain,arm_key) do update set
                      positive=positive+excluded.positive,
                      negative=negative+excluded.negative,
                      trials=trials+1,
                      reward_sum=reward_sum+excluded.reward_sum,
                      evidence=max(evidence,excluded.evidence),
                      last_outcome=excluded.last_outcome,
                      last_reason=excluded.last_reason,
                      updated_at=excluded.updated_at
                    """,
                    (
                        catalog_key,
                        domain,
                        arm,
                        positive,
                        negative,
                        reward_delta,
                        evidence,
                        outcome,
                        reason,
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "select positive,negative,trials,reward_sum,evidence,last_outcome,last_reason,updated_at from agent_strategy_credit where catalog_key=? and domain=? and arm_key=?",
                    (catalog_key, domain, arm),
                ).fetchone()
                conn.commit()
            finally:
                self._close(conn)
        return {
            "recorded": True,
            "deduplicated": False,
            "domain": domain,
            "arm": arm,
            **(dict(row) if row else {}),
        }

    def strategy_credits(
        self,
        catalog_key: str,
        domain: str,
        *,
        include_segments: bool = False,
        limit: int = CREDIT_READ_BUDGET,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(512, int(limit)))
        with self._lock:
            conn = self._connect()
            try:
                if include_segments and domain in {"search", "recommend"}:
                    rows = conn.execute(
                        """
                        select * from agent_strategy_credit
                        where catalog_key=? and (domain=? or domain like ?)
                        order by updated_at desc limit ?
                        """,
                        (catalog_key, domain, f"{domain}.segment.%", limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        select * from agent_strategy_credit
                        where catalog_key=? and domain=?
                        order by updated_at desc limit ?
                        """,
                        (catalog_key, domain, limit),
                    ).fetchall()
            finally:
                self._close(conn)
        return [
            {
                "status": "credit",
                "credit": {
                    "domain": row["domain"],
                    "arm": row["arm_key"],
                    "positive": int(row["positive"]),
                    "negative": int(row["negative"]),
                    "trials": int(row["trials"]),
                    "reward_sum": float(row["reward_sum"]),
                    "evidence": int(row["evidence"]),
                    "last_outcome": row["last_outcome"],
                    "last_reason": row["last_reason"],
                    "updated_at": float(row["updated_at"]),
                },
            }
            for row in rows
        ]

    def evolution_memory(self, catalog_key: str, domain: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return trusted strategies plus durable arm credit for evolution only."""

        return [
            *super().strategies(catalog_key, domain, limit=limit),
            *self.strategy_credits(
                catalog_key,
                domain,
                include_segments=domain in {"search", "recommend"},
            ),
        ]

    @staticmethod
    def _config_signature(
        surface: str,
        base_config: dict[str, Any],
        candidate_config: dict[str, Any],
    ) -> list[str]:
        try:
            from lingjing_harness.algorithms import RecommendConfig, SearchConfig
            from lingjing_harness.algorithms.capabilities import config_from_mapping
            from lingjing_harness.algorithms import evolution_core as core

            cls = SearchConfig if surface == "search" else RecommendConfig
            base = config_from_mapping(cls, base_config)
            candidate = config_from_mapping(cls, candidate_config)
            dimensions, _ = core._evolution_schema(base)
            return list(core._config_signature(asdict(base), asdict(candidate), dimensions))
        except (TypeError, ValueError, KeyError, ImportError):
            return []

    @staticmethod
    def _global_credit_decision(result: dict[str, Any]) -> tuple[str, float, str, int] | None:
        business = result.get("business_validation") or {}
        holdout = (result.get("validation") or {}).get("holdout") or {}
        if business.get("available"):
            confidence = business.get("confidence") or {}
            samples = int(confidence.get("samples", 0) or 0)
            independent = bool(holdout.get("independent"))
            if samples < 2 or not independent:
                return None
            delta = float(business.get("holdout_reward_delta", 0.0) or 0.0)
            evidence = samples
            if result.get("trusted"):
                return "accepted", delta, "independent_business_holdout_accepted", evidence
            if delta < 0.0:
                return "rejected", delta, "independent_business_holdout_regressed", evidence
            if not result.get("safe_to_try"):
                return "rejected", delta, "independent_domain_guardrail_rejected", evidence
            return None
        if not bool(holdout.get("independent")):
            return None
        evidence = int(holdout.get("samples", 0) or 0)
        if result.get("trusted"):
            return "accepted", float(result.get("objective_delta", 0.0) or 0.0), "independent_proxy_holdout_accepted", evidence
        if not result.get("safe_to_try"):
            return "rejected", float(result.get("objective_delta", 0.0) or 0.0), "independent_proxy_guardrail_rejected", evidence
        return None

    def record_evolution_result(
        self,
        catalog_key: str,
        surface: str,
        *,
        current_config: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if surface not in {"search", "recommend"}:
            raise ValueError("surface must be search or recommend")
        written: list[dict[str, Any]] = []
        decision = self._global_credit_decision(result)
        signature = [
            str(value)
            for value in ((result.get("evolution") or {}).get("selected_signature") or [])
            if str(value) and str(value) != "local:neutral"
        ]
        if decision and signature:
            outcome, reward_delta, reason, evidence = decision
            for arm in signature:
                event_key = _stable_event_key(
                    {
                        "kind": "global_evolution_credit",
                        "catalog_key": catalog_key,
                        "surface": surface,
                        "arm": arm,
                        "outcome": outcome,
                        "candidate_config": result.get("candidate_config"),
                        "business_validation": result.get("business_validation"),
                        "holdout": (result.get("validation") or {}).get("holdout"),
                    }
                )
                written.append(
                    self.record_strategy_credit(
                        catalog_key,
                        surface,
                        arm,
                        outcome=outcome,
                        reward_delta=reward_delta,
                        evidence=evidence,
                        reason=reason,
                        event_key=event_key,
                        payload={"scope": "global", "candidate_config": result.get("candidate_config")},
                    )
                )

        portfolio = result.get("segment_portfolio") or {}
        for entry in portfolio.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            segment = str(entry.get("segment") or "")
            candidate_config = entry.get("candidate_config")
            if not segment or not isinstance(candidate_config, dict):
                continue
            try:
                from lingjing_harness.algorithms.segments import strategy_domain

                domain = strategy_domain(surface, segment)
            except (ValueError, ImportError):
                continue
            enough = int(entry.get("discovery_requests", 0) or 0) >= 3 and int(entry.get("holdout_requests", 0) or 0) >= 2
            guardrail = entry.get("guardrail") or {}
            if not enough or not guardrail.get("available"):
                continue
            if entry.get("trusted"):
                outcome = "accepted"
                reason = "independent_segment_holdout_accepted"
            elif float(entry.get("holdout_reward_delta", 0.0) or 0.0) < 0.0:
                outcome = "rejected"
                reason = "independent_segment_holdout_regressed"
            elif not entry.get("safe_to_try"):
                outcome = "rejected"
                reason = "independent_segment_guardrail_rejected"
            else:
                continue
            reward_delta = float(entry.get("holdout_reward_delta", 0.0) or 0.0)
            evidence = int(entry.get("holdout_requests", 0) or 0)
            segment_signature = self._config_signature(surface, current_config, candidate_config)
            for arm in segment_signature:
                if arm == "local:neutral":
                    continue
                event_key = _stable_event_key(
                    {
                        "kind": "segment_evolution_credit",
                        "catalog_key": catalog_key,
                        "domain": domain,
                        "arm": arm,
                        "outcome": outcome,
                        "candidate_config": candidate_config,
                        "holdout_reward_delta": reward_delta,
                        "confidence": entry.get("confidence"),
                        "guardrail": guardrail,
                    }
                )
                written.append(
                    self.record_strategy_credit(
                        catalog_key,
                        domain,
                        arm,
                        outcome=outcome,
                        reward_delta=reward_delta,
                        evidence=evidence,
                        reason=reason,
                        event_key=event_key,
                        payload={"scope": "segment", "segment": segment, "candidate_config": candidate_config},
                    )
                )

        recorded = sum(1 for row in written if row.get("recorded"))
        deduplicated = sum(1 for row in written if row.get("deduplicated"))
        return {
            "available": True,
            "recorded_events": recorded,
            "deduplicated_events": deduplicated,
            "accepted": sum(1 for row in written if row.get("recorded") and row.get("last_outcome") == "accepted"),
            "negative": sum(1 for row in written if row.get("recorded") and row.get("last_outcome") in {"rejected", "rollback"}),
            "domains": sorted({str(row.get("domain")) for row in written if row.get("domain")}),
        }

    def retire_active(self, catalog_key: str, domain: str, *, reason: str) -> dict[str, Any] | None:
        active = self.active_skill(catalog_key, domain)
        retired = super().retire_active(catalog_key, domain, reason=reason)
        if not active or not retired:
            return retired
        signature = [
            str(value)
            for value in ((active.get("payload") or {}).get("selected_signature") or [])
            if str(value) and str(value) != "local:neutral"
        ]
        surface = "search" if domain == "search" or domain.startswith("search.segment.") else "recommend" if domain == "recommend" or domain.startswith("recommend.segment.") else ""
        if not signature and surface:
            try:
                from lingjing_harness.algorithms import RecommendConfig, SearchConfig

                base = asdict(SearchConfig() if surface == "search" else RecommendConfig())
                signature = self._config_signature(surface, base, active.get("config") or {})
            except (TypeError, ValueError, ImportError):
                signature = []
        for arm in signature:
            if arm == "local:neutral":
                continue
            self.record_strategy_credit(
                catalog_key,
                domain,
                arm,
                outcome="rollback",
                reason=str(reason or "active_strategy_rollback"),
                event_key=_stable_event_key(
                    {
                        "kind": "strategy_rollback_credit",
                        "catalog_key": catalog_key,
                        "domain": domain,
                        "fingerprint": active.get("fingerprint"),
                        "arm": arm,
                        "reason": reason,
                    }
                ),
                payload={"fingerprint": active.get("fingerprint")},
            )
        return retired

    def stats(self, catalog_key: str | None = None) -> dict[str, Any]:
        result = dict(super().stats(catalog_key))
        with self._lock:
            conn = self._connect()
            try:
                if catalog_key:
                    credit_arms = conn.execute(
                        "select count(*) from agent_strategy_credit where catalog_key=?",
                        (catalog_key,),
                    ).fetchone()[0]
                    negative_arms = conn.execute(
                        "select count(*) from agent_strategy_credit where catalog_key=? and negative>positive",
                        (catalog_key,),
                    ).fetchone()[0]
                    credit_events = conn.execute(
                        "select count(*) from agent_strategy_credit_events where catalog_key=?",
                        (catalog_key,),
                    ).fetchone()[0]
                else:
                    credit_arms = conn.execute("select count(*) from agent_strategy_credit").fetchone()[0]
                    negative_arms = conn.execute("select count(*) from agent_strategy_credit where negative>positive").fetchone()[0]
                    credit_events = conn.execute("select count(*) from agent_strategy_credit_events").fetchone()[0]
            finally:
                self._close(conn)
        result.update(
            {
                "credit_arms": int(credit_arms),
                "negative_credit_arms": int(negative_arms),
                "credit_events": int(credit_events),
            }
        )
        return result


__all__ = ["AgentMemory", "catalog_fingerprint"]
