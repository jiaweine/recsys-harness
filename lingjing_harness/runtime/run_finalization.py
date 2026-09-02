from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


FINAL_LEARNING_METHOD = "run_event_atomic_policy_episode_commit"


def _memory_primitives(memory: Any) -> tuple[Any, Any, Any] | None:
    """Resolve the shared SQLite primitives through runtime memory facades."""

    lock = getattr(memory, "_lock", None)
    connect = getattr(memory, "_connect", None)
    close = getattr(memory, "_close", None)
    if lock is None or not callable(connect) or not callable(close):
        return None
    return lock, connect, close


def _fence(memory: Any) -> None:
    """Reuse an execution-owner fence when an outer runtime installed one."""

    fence = getattr(memory, "_fence", None)
    if callable(fence):
        fence()


def _ensure_schema(conn: Any) -> None:
    conn.execute(
        """
        create table if not exists agent_run_learning_events(
          event_key text primary key,
          catalog_key text not null,
          context_key text not null,
          mode text not null,
          policy_actions integer not null,
          episode_id integer,
          created_at real not null
        )
        """
    )


def commit_run_learning(
    memory: Any,
    event_key: str,
    *,
    context_key: str,
    action_keys: list[str],
    policy_reward: float,
    catalog_key: str,
    goal: str,
    mode: str,
    episode_reward: float,
    findings: list[str],
    learned: list[dict[str, Any]],
) -> dict[str, Any]:
    """Atomically persist one run's terminal policy and episodic learning.

    ``event_key`` names the logical API/harness execution rather than a process.
    The durable marker, policy trial increments, episode insert, and episode
    retention prune share one ``BEGIN IMMEDIATE`` transaction. A replay with the
    same event key therefore contributes either all final learning exactly once or
    none of it, even when a different process owns the retry.

    Custom memory implementations without the project's SQLite primitives retain
    the historical sequential API contract. Production ``AgentMemory`` and
    backend-scoped facades always take the atomic path.
    """

    key = str(event_key or "").strip()
    if not key:
        raise ValueError("final learning event_key is required")

    unique_actions = [
        value
        for value in dict.fromkeys(str(action) for action in action_keys if str(action))
    ]
    payload = {
        "findings": list(findings)[:6],
        "actions": list(action_keys)[:16],
        "learned": list(learned)[:6],
    }
    primitives = _memory_primitives(memory)
    if primitives is None:
        memory.update_policy(str(context_key), unique_actions, float(policy_reward))
        memory.record_episode(
            str(catalog_key),
            str(goal),
            str(mode),
            float(episode_reward),
            findings=list(findings),
            action_keys=list(action_keys),
            learned=list(learned),
        )
        return {
            "method": FINAL_LEARNING_METHOD,
            "applied": True,
            "deduplicated": False,
            "atomic": False,
            "policy_actions": len(unique_actions),
        }

    lock, connect, close = primitives
    recent_budget = max(0, int(getattr(memory, "RECENT_EPISODE_BUDGET", 240)))
    high_value_budget = max(0, int(getattr(memory, "HIGH_VALUE_EPISODE_BUDGET", 80)))

    # Fence immediately before entering the irreversible transaction. The API's
    # lease wrapper exposes its existing linearization function through ``_fence``;
    # standalone/library callers simply have no external lease to refresh.
    _fence(memory)
    with lock:
        conn = connect()
        try:
            # Schema creation carries no logical run state. Commit it before the
            # data transaction so a forced rollback can be inspected/retried
            # deterministically without leaving a half-created migration behind.
            _ensure_schema(conn)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            marker = conn.execute(
                """
                insert or ignore into agent_run_learning_events(
                  event_key,catalog_key,context_key,mode,policy_actions,episode_id,created_at
                ) values(?,?,?,?,?,null,?)
                """,
                (
                    key,
                    str(catalog_key),
                    str(context_key),
                    str(mode),
                    len(unique_actions),
                    now,
                ),
            )
            if int(marker.rowcount or 0) == 0:
                conn.commit()
                return {
                    "method": FINAL_LEARNING_METHOD,
                    "applied": False,
                    "deduplicated": True,
                    "atomic": True,
                    "policy_actions": len(unique_actions),
                }

            for action_key in unique_actions:
                conn.execute(
                    """
                    insert into agent_policy_stats(
                      context_key,action_key,trials,reward_sum,updated_at
                    ) values(?,?,1,?,?)
                    on conflict(context_key,action_key) do update set
                      trials=trials+1,
                      reward_sum=reward_sum+excluded.reward_sum,
                      updated_at=excluded.updated_at
                    """,
                    (str(context_key), action_key, float(policy_reward), now),
                )

            episode = conn.execute(
                """
                insert into agent_episodes(
                  catalog_key,goal,mode,reward,payload,created_at
                ) values(?,?,?,?,?,?)
                """,
                (
                    str(catalog_key),
                    str(goal),
                    str(mode),
                    float(episode_reward),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            episode_id = int(episode.lastrowid)
            conn.execute(
                """
                update agent_run_learning_events
                set episode_id=?
                where event_key=?
                """,
                (episode_id, key),
            )
            conn.execute(
                """
                delete from agent_episodes
                where catalog_key=?
                  and id not in (
                    select id from agent_episodes where catalog_key=?
                    order by created_at desc limit ?
                  )
                  and id not in (
                    select id from agent_episodes where catalog_key=?
                    order by reward desc, created_at desc limit ?
                  )
                """,
                (
                    str(catalog_key),
                    str(catalog_key),
                    recent_budget,
                    str(catalog_key),
                    high_value_budget,
                ),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            close(conn)

    return {
        "method": FINAL_LEARNING_METHOD,
        "applied": True,
        "deduplicated": False,
        "atomic": True,
        "policy_actions": len(unique_actions),
        "episode_id": episode_id,
    }


class AtomicFinalLearningMemory:
    """Buffer the base harness' two final writes into one atomic commit."""

    def __init__(self, memory: Any, event_key: str) -> None:
        self._memory = memory
        self._event_key = str(event_key)
        self._pending_policy: tuple[str, list[str], float] | None = None
        self.last_commit: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._memory, name)

    def update_policy(
        self,
        context_key: str,
        action_keys: list[str],
        reward: float,
    ) -> None:
        # Refresh external ownership at the same logical boundary as the historic
        # write, then defer the actual mutation until record_episode supplies the
        # rest of the final-learning payload.
        _fence(self._memory)
        self._pending_policy = (
            str(context_key),
            list(action_keys),
            float(reward),
        )

    def record_episode(
        self,
        catalog_key: str,
        goal: str,
        mode: str,
        reward: float,
        *,
        findings: list[str],
        action_keys: list[str],
        learned: list[dict[str, Any]],
    ) -> None:
        pending = self._pending_policy
        if pending is None:
            # Preserve compatibility for non-standard callers that invoke episode
            # recording without the base harness' immediately preceding policy
            # update. Production AgentHarness always uses the paired path.
            _fence(self._memory)
            self._memory.record_episode(
                catalog_key,
                goal,
                mode,
                reward,
                findings=findings,
                action_keys=action_keys,
                learned=learned,
            )
            return

        context_key, policy_actions, policy_reward = pending
        self.last_commit = commit_run_learning(
            self._memory,
            self._event_key,
            context_key=context_key,
            action_keys=policy_actions,
            policy_reward=policy_reward,
            catalog_key=catalog_key,
            goal=goal,
            mode=mode,
            episode_reward=reward,
            findings=findings,
            learned=learned,
        )
        self._pending_policy = None


__all__ = [
    "FINAL_LEARNING_METHOD",
    "AtomicFinalLearningMemory",
    "commit_run_learning",
]
