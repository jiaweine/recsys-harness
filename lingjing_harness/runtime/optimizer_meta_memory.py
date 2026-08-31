from __future__ import annotations

from hashlib import blake2b
import json
from math import isfinite
import time
from typing import Any, Mapping


class OptimizerMetaMemory:
    """Durable credit for optimizer *selection* only.

    This sidecar deliberately does not share strategy-arm reward tables. It learns
    which proposal optimizer is useful in a routing context while downstream
    holdout, trust, activation and rollback remain authoritative.

    Backend-scoped runtime memory is a semantic wrapper, not a SQLite store. Keep
    that wrapper for catalog-key scoping while unwrapping only the physical storage
    connection. This lets semantic/collaborative serving backends learn independent
    optimizer credit without exposing AgentMemory's private DB API on wrappers.
    """

    READ_BUDGET = 96
    _STORAGE_ATTRS = ("_lock", "_connect", "_close")

    def __init__(self, memory: Any) -> None:
        self.scope_memory = memory
        self.memory = self._resolve_storage_memory(memory)
        self._init()

    @classmethod
    def _resolve_storage_memory(cls, memory: Any) -> Any:
        current = memory
        seen: set[int] = set()
        while True:
            marker = id(current)
            if marker in seen:
                raise TypeError("optimizer meta memory wrapper chain contains a cycle")
            seen.add(marker)

            # Semantic memory wrappers deliberately proxy unknown attributes to the
            # underlying AgentMemory. Prefer an explicit base_memory edge before
            # checking the storage protocol, otherwise hasattr() can mistake the
            # wrapper itself for physical SQLite storage.
            base_memory = getattr(current, "base_memory", None)
            if base_memory is not None and base_memory is not current:
                current = base_memory
                continue
            if all(hasattr(current, name) for name in cls._STORAGE_ATTRS):
                return current
            raise TypeError(
                "optimizer meta memory requires AgentMemory-compatible storage "
                "or a wrapper exposing base_memory"
            )

    def _scoped_catalog_key(self, catalog_key: str, domain: str) -> str:
        raw = str(catalog_key)
        scoper = getattr(self.scope_memory, "scoped_catalog_key", None)
        if callable(scoper):
            scoped = str(scoper(raw, str(domain)))
            if scoped:
                return scoped
        return raw

    @staticmethod
    def _namespace_event_key(raw_catalog_key: str, scoped_catalog_key: str, event_key: str) -> str:
        if raw_catalog_key == scoped_catalog_key:
            return event_key
        scope = blake2b(scoped_catalog_key.encode("utf-8"), digest_size=8).hexdigest()
        return f"optimizer-scope:{scope}:{event_key}"

    @staticmethod
    def _stable_event_key(payload: Mapping[str, Any]) -> str:
        raw = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()

    def _init(self) -> None:
        sql = """
        create table if not exists agent_optimizer_meta_credit(
          catalog_key text not null,
          domain text not null,
          context_key text not null,
          backend text not null,
          trials integer not null default 0,
          utility_sum real not null default 0,
          utility_sq_sum real not null default 0,
          objective_gain_sum real not null default 0,
          evaluator_calls_sum integer not null default 0,
          wall_seconds_sum real not null default 0,
          context_json text not null,
          last_reason text not null,
          created_at real not null,
          updated_at real not null,
          primary key(catalog_key,domain,context_key,backend)
        );
        create index if not exists idx_agent_optimizer_meta_lookup
          on agent_optimizer_meta_credit(catalog_key,domain,updated_at desc);
        create table if not exists agent_optimizer_meta_events(
          event_key text primary key,
          catalog_key text not null,
          domain text not null,
          context_key text not null,
          backend text not null,
          utility real not null,
          objective_gain real not null,
          evaluator_calls integer not null,
          wall_seconds real not null,
          context_json text not null,
          reason text not null,
          payload_json text not null,
          created_at real not null
        );
        create index if not exists idx_agent_optimizer_meta_events_lookup
          on agent_optimizer_meta_events(catalog_key,domain,created_at desc);
        """
        with self.memory._lock:
            connection = self.memory._connect()
            try:
                connection.execute("pragma busy_timeout=10000")
                connection.executescript(sql)
                connection.commit()
            finally:
                self.memory._close(connection)

    def record(
        self,
        catalog_key: str,
        domain: str,
        *,
        context_key: str,
        backend: str,
        context: Mapping[str, Any],
        utility: float,
        objective_gain: float,
        evaluator_calls: int,
        wall_seconds: float,
        reason: str = "completed_optimizer_run",
        event_key: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        backend = str(backend or "").strip()
        domain = str(domain or "").strip()
        context_key = str(context_key or "").strip()
        raw_catalog_key = str(catalog_key)
        catalog_key = self._scoped_catalog_key(raw_catalog_key, domain)
        if not backend or not domain or not context_key:
            raise ValueError("optimizer meta credit requires domain, context_key and backend")
        utility = float(utility)
        objective_gain = float(objective_gain)
        wall_seconds = float(wall_seconds)
        if not isfinite(utility) or not 0.0 <= utility <= 1.0:
            raise ValueError("optimizer meta utility must be finite within [0,1]")
        if not isfinite(objective_gain):
            raise ValueError("optimizer objective gain must be finite")
        if not isfinite(wall_seconds) or wall_seconds < 0.0:
            raise ValueError("optimizer wall_seconds must be finite and >= 0")
        if isinstance(evaluator_calls, bool):
            raise ValueError("optimizer evaluator_calls must be an integer, not boolean")
        calls = int(evaluator_calls)
        if calls != evaluator_calls or calls < 0:
            raise ValueError("optimizer evaluator_calls must be an integer >= 0")
        evaluator_calls = calls
        context_json = json.dumps(
            dict(context), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        payload_dict = dict(payload or {})
        event_payload = {
            "catalog_key": catalog_key,
            "domain": domain,
            "context_key": context_key,
            "backend": backend,
            "utility": round(utility, 10),
            "objective_gain": round(objective_gain, 10),
            "evaluator_calls": evaluator_calls,
            "wall_seconds": round(wall_seconds, 6),
            "reason": reason,
            "payload": payload_dict,
        }
        if event_key:
            event_key = self._namespace_event_key(raw_catalog_key, catalog_key, str(event_key))
        else:
            event_key = self._stable_event_key(event_payload)
        now = time.time()
        with self.memory._lock:
            connection = self.memory._connect()
            try:
                connection.execute("pragma busy_timeout=10000")
                connection.execute("begin immediate")
                cursor = connection.execute(
                    """
                    insert or ignore into agent_optimizer_meta_events(
                      event_key,catalog_key,domain,context_key,backend,utility,objective_gain,
                      evaluator_calls,wall_seconds,context_json,reason,payload_json,created_at
                    ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_key,
                        catalog_key,
                        domain,
                        context_key,
                        backend,
                        utility,
                        objective_gain,
                        evaluator_calls,
                        wall_seconds,
                        context_json,
                        str(reason),
                        json.dumps(payload_dict, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
                if cursor.rowcount == 0:
                    row = connection.execute(
                        """
                        select * from agent_optimizer_meta_credit
                        where catalog_key=? and domain=? and context_key=? and backend=?
                        """,
                        (catalog_key, domain, context_key, backend),
                    ).fetchone()
                    connection.commit()
                    return {
                        "recorded": False,
                        "deduplicated": True,
                        **(dict(row) if row else {}),
                    }
                connection.execute(
                    """
                    insert into agent_optimizer_meta_credit(
                      catalog_key,domain,context_key,backend,trials,utility_sum,utility_sq_sum,
                      objective_gain_sum,evaluator_calls_sum,wall_seconds_sum,context_json,
                      last_reason,created_at,updated_at
                    ) values(?,?,?,?,1,?,?,?,?,?,?,?,?,?)
                    on conflict(catalog_key,domain,context_key,backend) do update set
                      trials=trials+1,
                      utility_sum=utility_sum+excluded.utility_sum,
                      utility_sq_sum=utility_sq_sum+excluded.utility_sq_sum,
                      objective_gain_sum=objective_gain_sum+excluded.objective_gain_sum,
                      evaluator_calls_sum=evaluator_calls_sum+excluded.evaluator_calls_sum,
                      wall_seconds_sum=wall_seconds_sum+excluded.wall_seconds_sum,
                      context_json=excluded.context_json,
                      last_reason=excluded.last_reason,
                      updated_at=excluded.updated_at
                    """,
                    (
                        catalog_key,
                        domain,
                        context_key,
                        backend,
                        utility,
                        utility * utility,
                        objective_gain,
                        evaluator_calls,
                        wall_seconds,
                        context_json,
                        str(reason),
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    """
                    select * from agent_optimizer_meta_credit
                    where catalog_key=? and domain=? and context_key=? and backend=?
                    """,
                    (catalog_key, domain, context_key, backend),
                ).fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                self.memory._close(connection)
        return {"recorded": True, "deduplicated": False, **dict(row)}

    def read(
        self,
        catalog_key: str,
        domain: str,
        *,
        limit: int = READ_BUDGET,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        catalog_key = self._scoped_catalog_key(str(catalog_key), str(domain))
        with self.memory._lock:
            connection = self.memory._connect()
            try:
                connection.execute("pragma busy_timeout=10000")
                rows = connection.execute(
                    """
                    select catalog_key,domain,context_key,backend,trials,utility_sum,
                           utility_sq_sum,objective_gain_sum,evaluator_calls_sum,
                           wall_seconds_sum,context_json,last_reason,updated_at
                    from agent_optimizer_meta_credit
                    where catalog_key=? and domain=?
                    order by updated_at desc limit ?
                    """,
                    (catalog_key, domain, limit),
                ).fetchall()
            finally:
                self.memory._close(connection)
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                context = json.loads(str(item.get("context_json") or "{}"))
            except json.JSONDecodeError:
                context = {}
            item["context"] = context if isinstance(context, dict) else {}
            trials = max(1, int(item.get("trials", 0) or 0))
            item["mean_utility"] = float(item.get("utility_sum", 0.0) or 0.0) / trials
            item["mean_objective_gain"] = float(item.get("objective_gain_sum", 0.0) or 0.0) / trials
            item["mean_wall_seconds"] = float(item.get("wall_seconds_sum", 0.0) or 0.0) / trials
            result.append(item)
        return result


__all__ = ["OptimizerMetaMemory"]
