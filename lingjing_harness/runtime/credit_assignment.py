"""Influence-aware process credit for long-horizon tool trajectories.

The base harness learns a terminal run reward, but assigning that same value to
every successful tool call creates a long-horizon credit-assignment problem. This
module replaces only the completed run's *policy-stat contribution* with an
auditable semantic transition value after execution.

Credit is derived from the persisted Mission Graph rather than an opaque judge:

- requirement priority defines task importance;
- transitive ``dependsOn`` structure defines downstream influence;
- every requirement changed by the post-tool reflection is credited, not only the
  planner's nominal target requirement;
- satisfied transitions add semantic mass, blocked transitions and newly-created
  contradictions subtract process value;
- terminal reward is mixed with process credit using a horizon-adaptive weight, so
  sparse outcome feedback matters less as trajectories grow longer.

The original run reward, episode record, verifier, trust gates, activation
authority, and trial counts are unchanged. The correction is idempotent by run_id.
"""

from __future__ import annotations

from collections import defaultdict
import json
from math import log2, sqrt
from typing import Any


_PRIORITY_MASS = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
}
_BLOCKED_MASS_PENALTY = 0.70
_CONTRADICTION_PENALTY = 0.18
_MAX_CONTRADICTION_PENALTY = 0.54


def _fragment(value: str) -> str:
    text = str(value or "")
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text


def _semantic_dependency_graph(mission: dict[str, Any]) -> dict[str, set[str]]:
    """Return prerequisite -> direct dependents from persisted JSON-LD."""

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


def _final_status(requirements: Any, key: str) -> str:
    if not isinstance(requirements, dict):
        return ""
    row = requirements.get(key)
    if not isinstance(row, dict):
        return ""
    return str(row.get("status") or "")


def trajectory_policy_credits(result: dict[str, Any]) -> dict[str, Any]:
    """Derive per-tool credit from all semantic transitions caused by each action."""

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
    method = "semantic_influence_transition_credit_v2"
    if not actions:
        return {
            "method": method,
            "mode": mode,
            "terminal_reward": _terminal_reward(result),
            "horizon": 0,
            "tool_credits": {},
            "action_credits": [],
            "semantic_requirement_mass": {
                key: round(value, 6) for key, value in sorted(masses.items())
            },
        }

    local_rows: list[dict[str, Any]] = []
    for action in actions:
        decision = action.get("decision") or {}
        cycle = int(decision.get("cycle", -1) or -1)
        target = str(decision.get("requirement") or "")
        reflection = reflections.get(cycle) or {}
        changed = list(
            dict.fromkeys(
                str(key)
                for key in reflection.get("requirements_changed") or []
                if str(key)
            )
        )
        satisfied = [key for key in changed if _final_status(requirements, key) == "satisfied"]
        blocked = [key for key in changed if _final_status(requirements, key) == "blocked"]
        contradictions = list(
            dict.fromkeys(
                str(value)
                for value in reflection.get("new_contradictions") or []
                if str(value)
            )
        )
        positive_mass = sum(float(masses.get(key, 0.0)) for key in satisfied)
        blocked_mass = sum(float(masses.get(key, 0.0)) for key in blocked)
        local_rows.append(
            {
                "cycle": cycle,
                "tool": str(action.get("tool") or ""),
                "target_requirement": target,
                "touched_requirements": changed,
                "satisfied_requirements": satisfied,
                "blocked_requirements": blocked,
                "new_contradictions": contradictions,
                "positive_semantic_mass": positive_mass,
                "blocked_semantic_mass": blocked_mass,
            }
        )

    max_positive_mass = max(
        (float(row["positive_semantic_mass"]) for row in local_rows),
        default=0.0,
    )
    max_requirement_mass = max((float(value) for value in masses.values()), default=0.0)
    terminal = _terminal_reward(result)
    horizon = len(local_rows)
    # Terminal rewards become less informative as the horizon grows. 1/sqrt(T)
    # leaves a one-step task unchanged while increasing process resolution on long
    # trajectories without deleting sparse terminal supervision entirely.
    terminal_weight = min(1.0, 1.0 / sqrt(max(1, horizon)))
    process_weight = 1.0 - terminal_weight

    by_tool: dict[str, list[float]] = defaultdict(list)
    for row in local_rows:
        positive_score = (
            min(1.0, float(row["positive_semantic_mass"]) / max_positive_mass)
            if max_positive_mass > 0.0
            else 0.0
        )
        blocked_score = (
            min(1.0, float(row["blocked_semantic_mass"]) / max_requirement_mass)
            if max_requirement_mass > 0.0
            else 0.0
        )
        contradiction_penalty = min(
            _MAX_CONTRADICTION_PENALTY,
            _CONTRADICTION_PENALTY * len(row["new_contradictions"]),
        )
        process = max(
            0.0,
            min(
                1.0,
                positive_score
                - _BLOCKED_MASS_PENALTY * blocked_score
                - contradiction_penalty,
            ),
        )
        credit = max(
            0.0,
            min(1.0, terminal_weight * terminal + process_weight * process),
        )
        row["positive_score"] = round(positive_score, 6)
        row["blocked_score"] = round(blocked_score, 6)
        row["blocked_penalty"] = round(_BLOCKED_MASS_PENALTY * blocked_score, 6)
        row["contradiction_penalty"] = round(contradiction_penalty, 6)
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
        "method": method,
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
        "process_semantics": {
            "positive": "all reflection-changed requirements ending satisfied",
            "negative": "all reflection-changed requirements ending blocked plus new contradictions",
            "terminal_mixing": "1/sqrt(completed_policy_actions)",
        },
    }


def _memory_primitives(memory: Any) -> tuple[Any, Any, Any]:
    """Resolve shared SQLite primitives through backend-scoped facades."""

    lock = getattr(memory, "_lock")
    connect = getattr(memory, "_connect")
    close = getattr(memory, "_close")
    return lock, connect, close


def apply_semantic_trajectory_credit(memory: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Idempotently replace equal terminal credit with process-aware credit.

    ``AgentHarness`` has already incremented one policy trial per successful unique
    tool using terminal reward. We preserve that trial count and only replace this
    run's reward contribution. An audit table guarantees retries cannot apply the
    correction twice.
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
