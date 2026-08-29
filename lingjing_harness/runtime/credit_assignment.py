"""Influence-aware process credit for long-horizon tool trajectories.

The base harness intentionally learns from a terminal run reward, but assigning the
same terminal value to every successful tool call creates a classic long-horizon
credit-assignment problem. This module replaces only the *policy-stat contribution*
of the completed run with an auditable process-aware value after execution.

Credit is derived from the semantic Mission Graph rather than an opaque judge:

- requirement priority defines task importance;
- transitive `dependsOn` structure defines downstream influence;
- the reflection that actually closes the target requirement establishes local
  process progress;
- terminal reward is mixed with process credit using a horizon-adaptive weight,
  so sparse outcome feedback matters less as trajectories get longer.

The original run reward, episode record, verifier, trust gates and activation
authority are unchanged. The correction is idempotent by run_id.
"""

from __future__ import annotations

from collections import defaultdict
import json
from math import log2, sqrt
from typing import Any, Iterable


_PRIORITY_MASS = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
}


def _fragment(value: str) -> str:
    text = str(value or "")
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text


def _semantic_dependency_graph(mission: dict[str, Any]) -> dict[str, set[str]]:
    """Return prerequisite -> direct dependents from the persisted JSON-LD graph."""

    semantic = mission.get("semantic_governance") or {}
    jsonld = semantic.get("jsonld") if isinstance(semantic, dict) else None
    graph_rows = jsonld.get("@graph") if isinstance(jsonld, dict) else None
    reverse: dict[str, set[str]] = defaultdict(set)
    if isinstance(graph_rows, list):
        for row in graph_rows:
            if not isinstance(row, dict):
                continue
            source = _fragment(str(row.get("@id") or ""))
            if not source.startswith("requirement:"):
                continue
            targets = row.get("xushu:dependsOn") or []
            if isinstance(targets, dict):
                targets = [targets]
            for target in targets:
                if not isinstance(target, dict):
                    continue
                prerequisite = _fragment(str(target.get("@id") or ""))
                if prerequisite.startswith("requirement:"):
                    reverse[prerequisite].add(source)
    if reverse:
        return reverse

    # Backward-compatible fallback for legacy missions without semantic snapshots.
    requirements = mission.get("requirements") or {}
    for key, row in requirements.items():
        if not isinstance(row, dict):
            continue
        source = f"requirement:{key}"
        for prerequisite in row.get("prerequisites") or []:
            reverse[f"requirement:{prerequisite}"].add(source)
    return reverse


def _descendant_counts(reverse: dict[str, set[str]]) -> dict[str, int]:
    nodes = set(reverse)
    for children in reverse.values():
        nodes.update(children)

    def descendants(start: str) -> set[str]:
        seen: set[str] = set()
        stack = list(reverse.get(start, ()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(reverse.get(node, ()))
        return seen

    return {node: len(descendants(node)) for node in nodes}


def semantic_requirement_mass(mission: dict[str, Any]) -> dict[str, float]:
    """Compute interpretable task mass for each evidence requirement.

    Priority is ordinal rather than a learned preference. Downstream influence uses
    log growth so a root requirement receives extra credit for unlocking many
    dependent facts without completely dominating every leaf requirement.
    """

    requirements = mission.get("requirements") or {}
    descendants = _descendant_counts(_semantic_dependency_graph(mission))
    masses: dict[str, float] = {}
    for key, row in requirements.items():
        if not isinstance(row, dict):
            continue
        priority = _PRIORITY_MASS.get(str(row.get("priority") or "medium"), 2.0)
        downstream = descendants.get(f"requirement:{key}", 0)
        influence = 1.0 + log2(1.0 + downstream)
        optional = 0.5 if bool(row.get("optional")) else 1.0
        masses[str(key)] = priority * influence * optional
    return masses


def _terminal_reward(result: dict[str, Any]) -> float:
    for event in reversed(result.get("events") or []):
        if not isinstance(event, dict) or event.get("phase") != "complete":
            continue
        payload = event.get("payload") or {}
        try:
            return max(0.0, min(1.0, float(payload.get("reward", 0.5))))
        except (TypeError, ValueError):
            break
    return 0.5


def trajectory_policy_credits(result: dict[str, Any]) -> dict[str, Any]:
    """Derive per-tool credit from semantic influence and actual requirement closure."""

    mode = str((result.get("plan") or {}).get("mode") or "audit")
    mission = ((result.get("deliberation") or {}).get("mission") or {})
    requirements = mission.get("requirements") or {}
    masses = semantic_requirement_mass(mission)
    reflections = {
        int(row.get("cycle", -1)): row
        for row in ((result.get("deliberation") or {}).get("reflections") or [])
        if isinstance(row, dict)
    }
    actions = [
        row
        for row in (result.get("actions") or [])
        if isinstance(row, dict) and row.get("status") == "completed"
    ]
    if not actions:
        return {
            "method": "semantic_influence_transition_credit_v1",
            "mode": mode,
            "terminal_reward": _terminal_reward(result),
            "horizon": 0,
            "tool_credits": {},
            "action_credits": [],
        }

    local_rows: list[dict[str, Any]] = []
    for action in actions:
        decision = action.get("decision") or {}
        cycle = int(decision.get("cycle", -1) or -1)
        target = str(decision.get("requirement") or "")
        reflection = reflections.get(cycle) or {}
        changed = {str(key) for key in reflection.get("requirements_changed") or []}
        final_requirement = requirements.get(target) if isinstance(requirements, dict) else None
        closed = bool(
            target
            and target in changed
            and isinstance(final_requirement, dict)
            and final_requirement.get("status") == "satisfied"
        )
        local_mass = float(masses.get(target, 0.0)) if closed else 0.0
        local_rows.append(
            {
                "cycle": cycle,
                "tool": str(action.get("tool") or ""),
                "requirement": target,
                "closed": closed,
                "semantic_mass": local_mass,
            }
        )

    max_mass = max((row["semantic_mass"] for row in local_rows), default=0.0)
    terminal = _terminal_reward(result)
    horizon = len(local_rows)
    # Terminal rewards become less informative as the horizon grows. 1/sqrt(T)
    # is a simple variance-aware decay that leaves a one-step task unchanged.
    terminal_weight = min(1.0, 1.0 / sqrt(max(1, horizon)))
    process_weight = 1.0 - terminal_weight

    by_tool: dict[str, list[float]] = defaultdict(list)
    for row in local_rows:
        process = (
            float(row["semantic_mass"]) / max_mass
            if max_mass > 0.0
            else 0.0
        )
        credit = max(
            0.0,
            min(1.0, terminal_weight * terminal + process_weight * process),
        )
        row["process_score"] = round(process, 6)
        row["credit"] = round(credit, 6)
        if row["tool"]:
            by_tool[row["tool"]].append(credit)

    tool_credits = {
        tool: round(sum(values) / len(values), 6)
        for tool, values in sorted(by_tool.items())
        if values
    }
    return {
        "method": "semantic_influence_transition_credit_v1",
        "mode": mode,
        "terminal_reward": round(terminal, 6),
        "horizon": horizon,
        "terminal_weight": round(terminal_weight, 6),
        "process_weight": round(process_weight, 6),
        "tool_credits": tool_credits,
        "action_credits": local_rows,
        "semantic_requirement_mass": {
            key: round(value, 6) for key, value in sorted(masses.items())
        },
    }


def _memory_primitives(memory: Any) -> tuple[Any, Any, Any]:
    """Resolve the shared SQLite primitives through backend-scoped facades."""

    lock = getattr(memory, "_lock")
    connect = getattr(memory, "_connect")
    close = getattr(memory, "_close")
    return lock, connect, close


def apply_semantic_trajectory_credit(memory: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Idempotently replace equal terminal credit with process-aware credit.

    ``AgentHarness`` has already incremented one policy trial per successful unique
    tool using the terminal reward. We preserve that trial count and only replace
    the reward contribution for this run. A small audit table guarantees retries
    cannot apply the correction twice.
    """

    report = trajectory_policy_credits(result)
    run_id = str(result.get("run_id") or "")
    mode = str(report.get("mode") or "audit")
    terminal = float(report.get("terminal_reward", 0.5) or 0.5)
    tool_credits = report.get("tool_credits") or {}
    if not run_id or not tool_credits:
        report["applied"] = False
        report["reason"] = "no_completed_policy_actions"
        return report

    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    lock, connect, close = _memory_primitives(memory)
    with lock:
        conn = connect()
        try:
            conn.execute(
                """
                create table if not exists agent_policy_credit_adjustments(
                  run_id text primary key,
                  mode text not null,
                  terminal_reward real not null,
                  payload text not null,
                  created_at real not null default (unixepoch())
                )
                """
            )
            cursor = conn.execute(
                "insert or ignore into agent_policy_credit_adjustments(run_id,mode,terminal_reward,payload) values(?,?,?,?)",
                (run_id, mode, terminal, payload),
            )
            if cursor.rowcount == 0:
                conn.commit()
                report["applied"] = False
                report["deduplicated"] = True
                return report

            adjusted = 0
            for tool, credit_raw in tool_credits.items():
                credit = max(0.0, min(1.0, float(credit_raw)))
                action_key = f"{mode}|{tool}"
                update = conn.execute(
                    """
                    update agent_policy_stats
                    set reward_sum=reward_sum + ?, updated_at=strftime('%s','now')
                    where context_key=? and action_key=?
                    """,
                    (credit - terminal, mode, action_key),
                )
                adjusted += max(0, int(update.rowcount or 0))
            conn.commit()
        finally:
            close(conn)

    report["applied"] = True
    report["deduplicated"] = False
    report["adjusted_policy_rows"] = adjusted
    return report


__all__ = [
    "semantic_requirement_mass",
    "trajectory_policy_credits",
    "apply_semantic_trajectory_credit",
]
