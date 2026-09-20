from __future__ import annotations

import sqlite3

from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.production import ExposureEvent, RewardSpec
from lingjing_harness.runtime.memory import AgentMemory


class _CountingList(list):
    def __init__(self, values):
        super().__init__(values)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


def test_catalog_summary_scans_production_events_once():
    catalog = Catalog(
        items=[
            Item("a", "A", categories=["one"]),
            Item("b", "B", categories=["two"]),
        ],
        interactions=[
            Interaction("u1", "a"),
            Interaction("u2", "b"),
            Interaction("u1", "b"),
        ],
        events=[
            ExposureEvent("r1", 1.0, "search", "a"),
            ExposureEvent("r1", 1.1, "search", "b"),
            ExposureEvent("r2", 2.0, "recommend", "b"),
        ],
        reward_spec=RewardSpec(weights={"click": 1.0}),
        name="summary-test",
    )
    counted = _CountingList(catalog.events)
    catalog.events = counted

    summary = catalog.summary()

    assert counted.iterations == 1
    assert summary == {
        "name": "summary-test",
        "items": 2,
        "users": 2,
        "interactions": 3,
        "queries": 0,
        "categories": 2,
        "production_events": 3,
        "production_requests": 2,
        "search_replay_requests": 1,
        "recommend_replay_requests": 1,
        "business_reward_ready": True,
    }


def test_memory_stats_uses_one_connection_and_preserves_counts(tmp_path, monkeypatch):
    memory = AgentMemory(tmp_path / "memory-stats.db")
    key = "catalog-a"

    with sqlite3.connect(memory.path) as connection:
        connection.executemany(
            """
            insert into agent_episodes(catalog_key,goal,mode,reward,payload,created_at)
            values(?,?,?,?,?,?)
            """,
            [
                (key, "g1", "search", 0.5, "{}", 1.0),
                (key, "g2", "recommend", 0.5, "{}", 2.0),
                ("other", "g3", "search", 0.5, "{}", 3.0),
            ],
        )
        connection.executemany(
            """
            insert into agent_skills(
              catalog_key,domain,fingerprint,config,score,evidence,status,wins,payload,
              created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (key, "search", "s1", "{}", 0.5, 1, "active", 1, "{}", 1.0, 1.0),
                (key, "search", "s2", "{}", 0.5, 1, "trusted", 1, "{}", 2.0, 2.0),
                (key, "search", "s3", "{}", 0.5, 1, "retired", 1, "{}", 3.0, 3.0),
            ],
        )
        connection.executemany(
            """
            insert into agent_strategy_credit(
              catalog_key,domain,arm_key,positive,negative,trials,reward_sum,evidence,
              last_outcome,last_reason,created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (key, "search", "a1", 3, 1, 4, 2.0, 2, "accepted", "ok", 1.0, 1.0),
                (key, "search", "a2", 1, 3, 4, 1.0, 2, "rejected", "bad", 2.0, 2.0),
            ],
        )
        connection.executemany(
            """
            insert into agent_strategy_credit_events(
              event_key,catalog_key,domain,arm_key,outcome,reward_delta,evidence,reason,
              payload,created_at
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            [
                ("e1", key, "search", "a1", "accepted", 0.1, 2, "ok", "{}", 1.0),
                ("e2", key, "search", "a2", "rejected", -0.1, 2, "bad", "{}", 2.0),
                ("e3", "other", "search", "a3", "accepted", 0.1, 2, "ok", "{}", 3.0),
            ],
        )
        connection.commit()

    original_connect = memory._connect
    connects = 0

    def counted_connect():
        nonlocal connects
        connects += 1
        return original_connect()

    monkeypatch.setattr(memory, "_connect", counted_connect)

    assert memory.stats(key) == {
        "episodes": 2,
        "skills": 2,
        "active_strategies": 1,
        "credit_arms": 2,
        "negative_credit_arms": 1,
        "credit_events": 2,
    }
    assert connects == 1
