"""Bridge durable mechanism evidence into stagnation-time structural search.

Single-arm success/failure is already represented by ``agent_strategy_credit`` and
must not be counted twice. This bridge therefore exposes only *pair interaction*
evidence reconstructed from mechanisms that co-occurred in the same independently
evaluated experiment context.

The bridge also respects backend-scoped strategy memory: mature search/recommend
backends write/read mechanism evidence under the same scoped catalog identity as
strategy credit.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import json
from typing import Any

from .mechanism_graph import record_mechanism_evidence
from .memory import AgentMemory


_ORIGINAL_EVOLUTION_MEMORY = AgentMemory.evolution_memory
_INSTALLED = False


def _domain_parts(domain: str) -> tuple[str, str]:
    domain = str(domain or "")
    if ".segment." in domain:
        surface, segment = domain.split(".segment.", 1)
        return surface, segment
    return domain, ""


def mechanism_pair_priors(
    memory: Any,
    catalog_key: str,
    domain: str,
    *,
    limit: int = 48,
) -> list[dict[str, Any]]:
    """Aggregate co-occurring mechanism pairs without duplicating arm credit."""

    surface, segment = _domain_parts(domain)
    if surface not in {"search", "recommend"}:
        return []
    lock = getattr(memory, "_lock")
    connect = getattr(memory, "_connect")
    close = getattr(memory, "_close")
    with lock:
        conn = connect()
        try:
            exists = conn.execute(
                "select 1 from sqlite_master where type='table' and name='agent_mechanism_evidence'"
            ).fetchone()
            if not exists:
                return []
            if segment:
                rows = conn.execute(
                    """
                    select run_id,invocation_id,context_key,scope,segment,outcome,
                           reward_delta,evidence,mechanism_key,created_at
                    from agent_mechanism_evidence
                    where catalog_key=? and domain=? and scope='segment' and segment=?
                    order by created_at desc limit 384
                    """,
                    (str(catalog_key), surface, segment),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    select run_id,invocation_id,context_key,scope,segment,outcome,
                           reward_delta,evidence,mechanism_key,created_at
                    from agent_mechanism_evidence
                    where catalog_key=? and domain=? and scope='global'
                    order by created_at desc limit 384
                    """,
                    (str(catalog_key), surface),
                ).fetchall()
        finally:
            close(conn)

    experiments: dict[tuple[str, ...], dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        key = (
            str(row["run_id"]),
            str(row["invocation_id"]),
            str(row["context_key"]),
            str(row["scope"]),
            str(row["segment"]),
        )
        bucket = experiments.setdefault(
            key,
            {
                "arms": set(),
                "outcome": str(row["outcome"]),
                "reward_delta": float(row["reward_delta"]),
                "evidence": int(row["evidence"]),
            },
        )
        bucket["arms"].add(str(row["mechanism_key"]))
        bucket["evidence"] = max(int(bucket["evidence"]), int(row["evidence"]))

    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    for experiment in experiments.values():
        arms = sorted(str(value) for value in experiment["arms"] if str(value))
        if len(arms) < 2:
            continue
        for left, right in combinations(arms, 2):
            key = (left, right)
            row = aggregate.setdefault(
                key,
                {
                    "arms": [left, right],
                    "positive": 0,
                    "negative": 0,
                    "inconclusive": 0,
                    "trials": 0,
                    "reward_sum": 0.0,
                    "evidence": 0,
                },
            )
            outcome = str(experiment["outcome"])
            row["positive"] += int(outcome == "accepted")
            row["negative"] += int(outcome == "rejected")
            row["inconclusive"] += int(outcome == "inconclusive")
            row["trials"] += 1
            row["reward_sum"] += float(experiment["reward_delta"])
            row["evidence"] = max(int(row["evidence"]), int(experiment["evidence"]))

    priors = []
    for pair in aggregate.values():
        trials = max(1, int(pair["trials"]))
        pair = dict(pair)
        pair["mean_reward_delta"] = float(pair["reward_sum"]) / trials
        priors.append({"status": "mechanism_pair", "pair": pair})
    priors.sort(
        key=lambda row: (
            -(int(row["pair"]["positive"]) - int(row["pair"]["negative"])),
            -int(row["pair"]["trials"]),
            -float(row["pair"]["mean_reward_delta"]),
            tuple(row["pair"]["arms"]),
        )
    )
    return priors[: max(1, min(128, int(limit)))]


def _mechanism_aware_evolution_memory(
    self: AgentMemory,
    catalog_key: str,
    domain: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    base = list(_ORIGINAL_EVOLUTION_MEMORY(self, catalog_key, domain, limit=limit))
    return [
        *base,
        *mechanism_pair_priors(self, catalog_key, domain),
    ]


def record_runtime_mechanism_evidence(
    memory: Any,
    catalog_key: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Persist evidence under the same backend scope used by strategy memory."""

    scope = getattr(memory, "scoped_catalog_key", None)
    if not callable(scope):
        return record_mechanism_evidence(memory, catalog_key, result)

    totals = {
        "recorded": 0,
        "deduplicated": 0,
        "mechanisms": 0,
        "contexts": 0,
        "method": "context_mechanism_experiment_evidence_outcome_v1",
    }
    seen_mechanisms = 0
    seen_contexts = 0
    for surface in ("search", "recommend"):
        actions = [
            row
            for row in result.get("actions") or []
            if isinstance(row, dict)
            and str(row.get("tool") or "").startswith(f"{surface}.")
            and str(row.get("tool") or "").endswith(".evolve")
        ]
        if not actions:
            continue
        payload = dict(result)
        payload["actions"] = actions
        scoped_key = scope(str(catalog_key), surface)
        report = record_mechanism_evidence(memory, scoped_key, payload)
        totals["recorded"] += int(report.get("recorded", 0) or 0)
        totals["deduplicated"] += int(report.get("deduplicated", 0) or 0)
        seen_mechanisms += int(report.get("mechanisms", 0) or 0)
        seen_contexts += int(report.get("contexts", 0) or 0)
    totals["mechanisms"] = seen_mechanisms
    totals["contexts"] = seen_contexts
    if totals["recorded"] == 0 and totals["deduplicated"] == 0:
        totals["reason"] = "no_evolution_actions"
    return totals


def install_mechanism_transfer() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    AgentMemory.evolution_memory = _mechanism_aware_evolution_memory
    _INSTALLED = True


__all__ = [
    "mechanism_pair_priors",
    "record_runtime_mechanism_evidence",
    "install_mechanism_transfer",
]
