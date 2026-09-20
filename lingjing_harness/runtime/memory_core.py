from __future__ import annotations

import json
import sqlite3
import threading
import time
from hashlib import blake2b
from pathlib import Path
from typing import Any

from lingjing_harness.domain import Catalog
from lingjing_harness.algorithms.text import tokenize


def catalog_fingerprint(catalog: Catalog) -> str:
    h = blake2b(digest_size=12)
    h.update(catalog.name.encode("utf-8", "ignore"))
    for item in sorted(catalog.items, key=lambda x: x.item_id):
        h.update(f"{item.item_id}|{item.title}|{item.text}|{','.join(item.categories)}|{item.popularity:.6g}|{item.quality:.6g}|{item.freshness:.6g}|{int(item.eligible)}".encode("utf-8", "ignore"))
    for event in catalog.interactions:
        h.update(f"{event.user_id}|{event.item_id}|{event.event}|{event.weight:.6g}|{event.timestamp:.6g}".encode("utf-8", "ignore"))
    for label in sorted(catalog.query_labels, key=lambda x: x.query):
        h.update(f"{label.query}|{','.join(sorted(label.relevant))}".encode("utf-8", "ignore"))
    return h.hexdigest()


class AgentMemory:
    """Persistent episodic, procedural and policy memory for the autonomous harness."""

    RECENT_EPISODE_BUDGET = 240
    HIGH_VALUE_EPISODE_BUDGET = 80
    TRUSTED_SKILL_BUDGET = 12

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._shared = sqlite3.connect(self.path, check_same_thread=False) if self.path == ":memory:" else None
        if self._shared is not None:
            self._shared.row_factory = sqlite3.Row
        self._init()

    def _connect(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        if conn is not self._shared:
            conn.close()

    def _init(self) -> None:
        sql = """
        create table if not exists agent_episodes(
          id integer primary key autoincrement,
          catalog_key text not null,
          goal text not null,
          mode text not null,
          reward real not null,
          payload text not null,
          created_at real not null
        );
        create index if not exists idx_agent_episodes_catalog_mode on agent_episodes(catalog_key,mode,created_at desc);
        create table if not exists agent_skills(
          id integer primary key autoincrement,
          catalog_key text not null,
          domain text not null,
          fingerprint text not null,
          config text not null,
          score real not null,
          evidence integer not null,
          status text not null,
          wins integer not null default 1,
          payload text not null,
          created_at real not null,
          updated_at real not null,
          unique(catalog_key,domain,fingerprint)
        );
        create index if not exists idx_agent_skills_lookup on agent_skills(catalog_key,domain,status,score desc);
        create table if not exists agent_skill_events(
          invocation_id text primary key,
          catalog_key text not null,
          domain text not null,
          fingerprint text not null,
          result text not null,
          created_at real not null
        );
        create table if not exists agent_policy_stats(
          context_key text not null,
          action_key text not null,
          trials integer not null,
          reward_sum real not null,
          updated_at real not null,
          primary key(context_key,action_key)
        );
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(sql)
                conn.commit()
            finally:
                self._close(conn)

    @staticmethod
    def _skill_fingerprint(config: dict[str, Any]) -> str:
        raw = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return blake2b(raw.encode("utf-8"), digest_size=10).hexdigest()

    def recall(self, catalog_key: str, goal: str, mode: str, limit: int = 4) -> list[dict[str, Any]]:
        goal_tokens = set(tokenize(goal))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "select * from agent_episodes where catalog_key=? and mode in (?, 'both', 'audit') order by created_at desc limit 40",
                    (catalog_key, mode),
                ).fetchall()
            finally:
                self._close(conn)
        scored = []
        for row in rows:
            tokens = set(tokenize(row["goal"]))
            overlap = len(goal_tokens & tokens) / max(1, len(goal_tokens | tokens))
            recency = max(0.0, 1.0 - (time.time() - row["created_at"]) / (90 * 86400))
            score = 0.72 * overlap + 0.18 * recency + 0.10 * max(0.0, min(1.0, row["reward"]))
            if score <= 0.02:
                continue
            payload = json.loads(row["payload"] or "{}")
            scored.append({"goal": row["goal"], "reward": row["reward"], "score": round(score, 4), **payload})
        return sorted(scored, key=lambda x: -x["score"])[:limit]

    def policy_bonus(self, context_key: str, action_key: str) -> float:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "select trials,reward_sum from agent_policy_stats where context_key=? and action_key=?",
                    (context_key, action_key),
                ).fetchone()
            finally:
                self._close(conn)
        if not row:
            return 0.0
        average = row["reward_sum"] / max(1, row["trials"])
        confidence = min(1.0, row["trials"] / 8.0)
        return max(-0.12, min(0.12, (average - 0.5) * 0.24 * confidence))

    def update_policy(self, context_key: str, action_keys: list[str], reward: float) -> None:
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                for action_key in dict.fromkeys(action_keys):
                    conn.execute(
                        """
                        insert into agent_policy_stats(context_key,action_key,trials,reward_sum,updated_at)
                        values(?,?,1,?,?)
                        on conflict(context_key,action_key) do update set
                          trials=trials+1,
                          reward_sum=reward_sum+excluded.reward_sum,
                          updated_at=excluded.updated_at
                        """,
                        (context_key, action_key, float(reward), now),
                    )
                conn.commit()
            finally:
                self._close(conn)

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
        payload = {
            "findings": findings[:6],
            "actions": action_keys[:16],
            "learned": learned[:6],
        }
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "insert into agent_episodes(catalog_key,goal,mode,reward,payload,created_at) values(?,?,?,?,?,?)",
                    (catalog_key, goal, mode, float(reward), json.dumps(payload, ensure_ascii=False), time.time()),
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
                    (catalog_key, catalog_key, self.RECENT_EPISODE_BUDGET, catalog_key, self.HIGH_VALUE_EPISODE_BUDGET),
                )
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
        fingerprint = self._skill_fingerprint(config)
        now = time.time()
        payload = payload or {}
        with self._lock:
            conn = self._connect()
            try:
                if invocation_id:
                    seen = conn.execute(
                        "select fingerprint from agent_skill_events where invocation_id=?",
                        (invocation_id,),
                    ).fetchone()
                    if seen:
                        row = conn.execute(
                            "select status,score,evidence,wins from agent_skills where catalog_key=? and domain=? and fingerprint=?",
                            (catalog_key, domain, seen["fingerprint"]),
                        ).fetchone()
                        if row:
                            return {
                                "fingerprint": seen["fingerprint"],
                                "status": row["status"],
                                "score": round(float(row["score"]), 5),
                                "evidence": int(row["evidence"]),
                                "wins": int(row["wins"]),
                                "deduplicated": True,
                            }
                if status == "active":
                    conn.execute(
                        "update agent_skills set status='trusted',updated_at=? where catalog_key=? and domain=? and status='active'",
                        (now, catalog_key, domain),
                    )
                conn.execute(
                    """
                    insert into agent_skills(catalog_key,domain,fingerprint,config,score,evidence,status,wins,payload,created_at,updated_at)
                    values(?,?,?,?,?,?,?,1,?,?,?)
                    on conflict(catalog_key,domain,fingerprint) do update set
                      score=max(score,excluded.score),
                      evidence=max(evidence,excluded.evidence),
                      status=case when excluded.status='active' then 'active' else agent_skills.status end,
                      wins=wins+1,
                      payload=excluded.payload,
                      updated_at=excluded.updated_at
                    """,
                    (
                        catalog_key,
                        domain,
                        fingerprint,
                        json.dumps(config, ensure_ascii=False, sort_keys=True),
                        float(score),
                        int(evidence),
                        status,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                if invocation_id:
                    conn.execute(
                        "insert or ignore into agent_skill_events(invocation_id,catalog_key,domain,fingerprint,result,created_at) values(?,?,?,?,?,?)",
                        (invocation_id, catalog_key, domain, fingerprint, json.dumps(tool_result or {}, ensure_ascii=False), now),
                    )
                conn.execute(
                    """
                    update agent_skills set status='retired',updated_at=?
                    where catalog_key=? and domain=? and status='trusted'
                      and id not in (
                        select id from agent_skills
                        where catalog_key=? and domain=? and status='trusted'
                        order by score desc, wins desc, updated_at desc limit ?
                      )
                    """,
                    (now, catalog_key, domain, catalog_key, domain, self.TRUSTED_SKILL_BUDGET),
                )
                conn.commit()
            finally:
                self._close(conn)
        return {"fingerprint": fingerprint, "status": status, "score": round(float(score), 5), "evidence": int(evidence), "deduplicated": False}

    def invocation_result(self, invocation_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "select catalog_key,domain,fingerprint,result from agent_skill_events where invocation_id=?",
                    (invocation_id,),
                ).fetchone()
                if not row:
                    return None
                skill = conn.execute(
                    "select status,score,evidence,wins from agent_skills where catalog_key=? and domain=? and fingerprint=?",
                    (row["catalog_key"], row["domain"], row["fingerprint"]),
                ).fetchone()
            finally:
                self._close(conn)
        if not skill:
            return None
        return {
            "catalog_key": row["catalog_key"],
            "domain": row["domain"],
            "fingerprint": row["fingerprint"],
            "result": json.loads(row["result"] or "{}"),
            "skill": {
                "fingerprint": row["fingerprint"],
                "status": skill["status"],
                "score": round(float(skill["score"]), 5),
                "evidence": int(skill["evidence"]),
                "wins": int(skill["wins"]),
                "deduplicated": True,
            },
        }

    def strategies(self, catalog_key: str, domain: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    select * from agent_skills where catalog_key=? and domain=? and status in ('trusted','active')
                    order by case status when 'active' then 0 else 1 end, score desc, wins desc, updated_at desc limit ?
                    """,
                    (catalog_key, domain, limit),
                ).fetchall()
            finally:
                self._close(conn)
        out = []
        for row in rows:
            out.append({
                "fingerprint": row["fingerprint"],
                "config": json.loads(row["config"]),
                "score": row["score"],
                "evidence": row["evidence"],
                "status": row["status"],
                "wins": row["wins"],
                "payload": json.loads(row["payload"] or "{}"),
            })
        return out


    def active_skill(self, catalog_key: str, domain: str) -> dict[str, Any] | None:
        for row in self.strategies(catalog_key, domain, limit=8):
            if row["status"] == "active":
                return row
        return None

    def retire_active(self, catalog_key: str, domain: str, *, reason: str) -> dict[str, Any] | None:
        active = self.active_skill(catalog_key, domain)
        if not active:
            return None
        now = time.time()
        payload = dict(active.get("payload") or {})
        payload["retired_reason"] = reason
        payload["retired_at"] = now
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "update agent_skills set status='retired',payload=?,updated_at=? where catalog_key=? and domain=? and fingerprint=?",
                    (json.dumps(payload, ensure_ascii=False), now, catalog_key, domain, active["fingerprint"]),
                )
                conn.commit()
            finally:
                self._close(conn)
        return {"fingerprint": active["fingerprint"], "reason": reason}

    def mark_skill_validation(
        self,
        catalog_key: str,
        domain: str,
        fingerprint: str,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "select payload from agent_skills where catalog_key=? and domain=? and fingerprint=?",
                    (catalog_key, domain, fingerprint),
                ).fetchone()
                if not row:
                    return
                payload = json.loads(row["payload"] or "{}")
                payload["validated_at"] = now
                payload["validation"] = metrics or {}
                conn.execute(
                    "update agent_skills set payload=?,updated_at=? where catalog_key=? and domain=? and fingerprint=?",
                    (json.dumps(payload, ensure_ascii=False), now, catalog_key, domain, fingerprint),
                )
                conn.commit()
            finally:
                self._close(conn)

    def active_config(self, catalog_key: str, domain: str) -> dict[str, Any] | None:
        rows = self.strategies(catalog_key, domain, limit=8)
        for row in rows:
            if row["status"] == "active":
                return row["config"]
        return None

    def stats(self, catalog_key: str | None = None) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                if catalog_key:
                    episodes = conn.execute("select count(*) from agent_episodes where catalog_key=?", (catalog_key,)).fetchone()[0]
                    skills = conn.execute("select count(*) from agent_skills where catalog_key=? and status in ('trusted','active')", (catalog_key,)).fetchone()[0]
                    active = conn.execute("select count(*) from agent_skills where catalog_key=? and status='active'", (catalog_key,)).fetchone()[0]
                else:
                    episodes = conn.execute("select count(*) from agent_episodes").fetchone()[0]
                    skills = conn.execute("select count(*) from agent_skills where status in ('trusted','active')").fetchone()[0]
                    active = conn.execute("select count(*) from agent_skills where status='active'").fetchone()[0]
            finally:
                self._close(conn)
        return {"episodes": episodes, "skills": skills, "active_strategies": active}
