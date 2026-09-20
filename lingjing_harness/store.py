from __future__ import annotations

import json
from hashlib import blake2b
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ACTIVE_RUN_STATUSES = ("running", "interrupted", "cancel_requested")
CONTEXT_MEMORY_ITEM_BUDGET = 256


class WorkspaceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma busy_timeout=10000")
        return connection

    def _init(self) -> None:
        sql = """
        create table if not exists conversations(
          id text primary key,title text not null,scene text not null,
          created_at real not null,updated_at real not null
        );
        create table if not exists messages(
          id text primary key,conversation_id text not null,role text not null,
          content text not null,payload text not null,created_at real not null
        );
        create index if not exists idx_messages_conversation on messages(conversation_id,created_at);
        create index if not exists idx_messages_user_created
          on messages(conversation_id,created_at) where role='user';
        create table if not exists context_memory_items(
          id text primary key,
          conversation_id text not null,
          source_id text not null,
          source_kind text not null,
          content text not null,
          content_hash text not null,
          trust real not null,
          catalog_revision text not null default '',
          created_at real not null,
          unique(conversation_id,source_id,content_hash)
        );
        create index if not exists idx_context_memory_conversation
          on context_memory_items(conversation_id,created_at desc);
        create index if not exists idx_context_memory_source
          on context_memory_items(conversation_id,source_id);
        create table if not exists runs(
          run_id text primary key,conversation_id text not null,goal text not null,
          status text not null,snapshot text not null,created_at real not null,
          updated_at real not null,owner_id text,lease_until real
        );
        create index if not exists idx_runs_status on runs(status,updated_at);
        create index if not exists idx_runs_conversation_status on runs(conversation_id,status,updated_at);
        create table if not exists workspace_state(
          id integer primary key check(id=1),
          catalog_revision text not null default '',
          update_owner text,
          update_until real,
          updated_at real not null
        );
        create table if not exists rate_limits(
          scope_key text primary key,
          window_start real not null,
          count integer not null,
          updated_at real not null
        );
        """
        with self._lock, self._connect() as connection:
            connection.executescript(sql)
            columns = {row["name"] for row in connection.execute("pragma table_info(runs)").fetchall()}
            if "owner_id" not in columns:
                connection.execute("alter table runs add column owner_id text")
            if "lease_until" not in columns:
                connection.execute("alter table runs add column lease_until real")
            context_columns = {
                row["name"]
                for row in connection.execute(
                    "pragma table_info(context_memory_items)"
                ).fetchall()
            }
            if "catalog_revision" not in context_columns:
                connection.execute(
                    "alter table context_memory_items "
                    "add column catalog_revision text not null default ''"
                )
            connection.execute(
                "insert or ignore into workspace_state(id,catalog_revision,updated_at) values(1,'',?)",
                (time.time(),),
            )
            connection.commit()

    @staticmethod
    def _loads(value: str | None) -> dict[str, Any]:
        try:
            data = json.loads(value or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _reanchored_until(updated_at: Any, lease_until: Any, now: float) -> float:
        updated = float(updated_at or now)
        until = float(lease_until or updated)
        return now + max(0.0, until - updated)

    def _workspace_update_row(
        self, connection: sqlite3.Connection, now: float
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "select update_owner,update_until,updated_at from workspace_state where id=1"
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("update_owner") and float(data.get("updated_at") or 0.0) > now:
            repaired_until = self._reanchored_until(
                data.get("updated_at"), data.get("update_until"), now
            )
            connection.execute(
                "update workspace_state set update_until=?,updated_at=? where id=1",
                (repaired_until, now),
            )
            data["update_until"] = repaired_until
            data["updated_at"] = now
        return data

    def _repair_future_run_leases(
        self, connection: sqlite3.Connection, now: float
    ) -> None:
        rows = connection.execute(
            """
            select run_id,updated_at,lease_until from runs
            where status in ('running','interrupted','cancel_requested')
              and lease_until is not null and updated_at>?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            repaired_until = self._reanchored_until(
                row["updated_at"], row["lease_until"], now
            )
            connection.execute(
                """
                update runs set updated_at=?,lease_until=?
                where run_id=? and updated_at>?
                """,
                (now, repaired_until, row["run_id"], now),
            )

    def list_conversations(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from conversations order by updated_at desc limit ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def create_conversation(self, title: str = "新的体验任务", scene: str = "audit") -> dict[str, Any]:
        now = time.time()
        conversation_id = f"cv-{uuid.uuid4().hex[:10]}"
        with self._lock, self._connect() as connection:
            connection.execute(
                "insert into conversations values(?,?,?,?,?)",
                (conversation_id, title, scene, now, now),
            )
        return {
            "id": conversation_id,
            "title": title,
            "scene": scene,
            "created_at": now,
            "updated_at": now,
        }

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        # This is a composite read across two short-lived SQLite connections. Keep
        # the process-local writer mutex for the full read so this store instance's
        # high-frequency message commits cannot repeatedly reacquire the database
        # writer lock between the conversation row and its message snapshot.
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "select * from conversations where id=?", (conversation_id,)
                ).fetchone()
            if not row:
                raise KeyError(conversation_id)
            return {**dict(row), "messages": self.list_messages(conversation_id)}

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from messages where conversation_id=? order by created_at",
                (conversation_id,),
            ).fetchall()
        output = []
        for row in rows:
            data = dict(row)
            data["payload"] = self._loads(data.pop("payload"))
            output.append(data)
        return output

    @staticmethod
    def _like_pattern(term: str) -> str:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _lexical_match_sql(
        terms: list[str],
        column: str = "content",
    ) -> tuple[str, list[str]]:
        """Build escaped LIKE clauses for a high-recall first-stage scan."""

        patterns = [WorkspaceStore._like_pattern(term) for term in terms]
        clauses = " or ".join(
            f"{column} like ? escape '\\'" for _ in patterns
        )
        return clauses, patterns

    def context_snapshot(
        self,
        conversation_id: str,
        *,
        query_terms: list[str] | None = None,
        exclude_message_id: str | None = None,
        recent_limit: int = 96,
        search_limit: int = 96,
        anchor_limit: int = 16,
        memory_limit: int = 72,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return a bounded pool for trust-aware long-horizon context ranking."""

        recent_limit = max(8, min(256, int(recent_limit)))
        search_limit = max(0, min(256, int(search_limit)))
        anchor_limit = max(0, min(64, int(anchor_limit)))
        memory_limit = max(0, min(192, int(memory_limit)))

        terms: list[str] = []
        for value in query_terms or []:
            term = str(value or "").strip()[:80]
            if len(term) >= 2 and term not in terms:
                terms.append(term)
            if len(terms) >= 10:
                break

        excluded = str(exclude_message_id or "")
        message_rows: dict[str, sqlite3.Row] = {}
        memory_rows: dict[str, sqlite3.Row] = {}
        with self._connect() as connection:
            params: list[Any] = [conversation_id]
            excluded_sql = ""
            if excluded:
                excluded_sql = " and id<>?"
                params.append(excluded)
            params.append(recent_limit)
            recent = connection.execute(
                f"""select id,conversation_id,role,content,created_at from messages
                    where conversation_id=? and role='user'{excluded_sql}
                    order by created_at desc limit ?""",
                tuple(params),
            ).fetchall()
            for row in recent:
                message_rows[str(row["id"])] = row

            if anchor_limit:
                params = [conversation_id]
                excluded_sql = ""
                if excluded:
                    excluded_sql = " and id<>?"
                    params.append(excluded)
                params.append(anchor_limit)
                anchors = connection.execute(
                    f"""select id,conversation_id,role,content,created_at from messages
                        where conversation_id=? and role='user'{excluded_sql}
                        order by created_at asc limit ?""",
                    tuple(params),
                ).fetchall()
                for row in anchors:
                    message_rows[str(row["id"])] = row

            if terms and search_limit:
                # Probe the strongest lexical term independently so a rare old
                # technical anchor cannot be crowded out by many recent matches on
                # broader terms. The broader query is recency-ordered and stops as
                # soon as enough candidates are found; semantic/trust ranking lives
                # in runtime.context_memory rather than being recomputed in SQLite.
                priority_pattern = self._like_pattern(terms[0])
                params = [conversation_id]
                excluded_sql = ""
                if excluded:
                    excluded_sql = " and id<>?"
                    params.append(excluded)
                params.extend((priority_pattern, min(16, search_limit)))
                priority_matches = connection.execute(
                    f"""select id,conversation_id,role,content,created_at
                        from messages
                        where conversation_id=? and role='user'{excluded_sql}
                          and content like ? escape '\\'
                        order by created_at desc limit ?""",
                    tuple(params),
                ).fetchall()
                for row in priority_matches:
                    message_rows[str(row["id"])] = row

                if len(terms) > 1:
                    clauses, patterns = self._lexical_match_sql(terms)
                    params = [conversation_id]
                    excluded_sql = ""
                    if excluded:
                        excluded_sql = " and id<>?"
                        params.append(excluded)
                    params.extend(patterns)
                    params.append(search_limit)
                    matches = connection.execute(
                        f"""select id,conversation_id,role,content,created_at
                            from messages
                            where conversation_id=? and role='user'{excluded_sql}
                              and ({clauses})
                            order by created_at desc limit ?""",
                        tuple(params),
                    ).fetchall()
                    for row in matches:
                        message_rows[str(row["id"])] = row

            if memory_limit:
                recent_memory = connection.execute(
                    """select * from context_memory_items
                       where conversation_id=?
                       order by created_at desc limit ?""",
                    (conversation_id, memory_limit),
                ).fetchall()
                for row in recent_memory:
                    memory_rows[str(row["id"])] = row

                if terms:
                    clauses, patterns = self._lexical_match_sql(terms)
                    match_memory = connection.execute(
                        f"""select * from context_memory_items
                            where conversation_id=? and ({clauses})
                            order by created_at desc limit ?""",
                        (conversation_id, *patterns, memory_limit),
                    ).fetchall()
                    for row in match_memory:
                        memory_rows[str(row["id"])] = row

        messages: list[dict[str, Any]] = []
        for row in sorted(message_rows.values(), key=lambda value: float(value["created_at"])):
            data = dict(row)
            messages.append(data)

        memories: list[dict[str, Any]] = []
        for row in sorted(memory_rows.values(), key=lambda value: float(value["created_at"])):
            memories.append(dict(row))
        return {"messages": messages, "memory_items": memories}

    @staticmethod
    def _prepare_context_item(
        *,
        source_id: str,
        source_kind: str,
        content: str,
        trust: float,
        catalog_revision: str | None,
        created_at: float | None,
    ) -> dict[str, Any] | None:
        value = str(content or "").replace("\x00", "").strip()
        source = str(source_id or "").strip()
        if not value or not source:
            return None
        value = value[:12_000]
        content_hash = blake2b(
            " ".join(value.split()).lower().encode("utf-8", "ignore"),
            digest_size=12,
        ).hexdigest()
        return {
            "source_id": source,
            "source_kind": str(source_kind or "derived").strip(),
            "content": value,
            "content_hash": content_hash,
            "trust": max(0.0, min(1.0, float(trust))),
            "catalog_revision": str(catalog_revision or ""),
            "created_at": time.time() if created_at is None else float(created_at),
        }

    @staticmethod
    def _upsert_context_item(
        connection: sqlite3.Connection,
        conversation_id: str,
        item: dict[str, Any],
    ) -> tuple[str, str]:
        existing_source = connection.execute(
            """select source_id,content_hash,created_at
               from context_memory_items
               where conversation_id=? and source_id=?
               order by created_at desc limit 1""",
            (conversation_id, item["source_id"]),
        ).fetchone()
        if (
            existing_source is not None
            and float(existing_source["created_at"]) > float(item["created_at"])
        ):
            return (
                str(existing_source["source_id"]),
                str(existing_source["content_hash"]),
            )

        connection.execute(
            """delete from context_memory_items
               where conversation_id=? and source_id=? and content_hash<>?""",
            (conversation_id, item["source_id"], item["content_hash"]),
        )
        connection.execute(
            """insert into context_memory_items(
                 id,conversation_id,source_id,source_kind,content,content_hash,
                 trust,catalog_revision,created_at
               ) values(?,?,?,?,?,?,?,?,?)
               on conflict(conversation_id,source_id,content_hash) do update set
                 source_kind=excluded.source_kind,
                 trust=excluded.trust,
                 catalog_revision=excluded.catalog_revision,
                 created_at=excluded.created_at""",
            (
                f"ctx-{uuid.uuid4().hex[:12]}",
                conversation_id,
                item["source_id"],
                item["source_kind"],
                item["content"],
                item["content_hash"],
                item["trust"],
                item["catalog_revision"],
                item["created_at"],
            ),
        )
        return str(item["source_id"]), str(item["content_hash"])

    @staticmethod
    def _prune_context_items(
        connection: sqlite3.Connection,
        conversation_id: str,
    ) -> None:
        connection.execute(
            """delete from context_memory_items
               where conversation_id=?
                 and id not in (
                   select id from context_memory_items
                   where conversation_id=?
                   order by created_at desc limit ?
                 )""",
            (conversation_id, conversation_id, CONTEXT_MEMORY_ITEM_BUDGET),
        )

    def remember_context_item(
        self,
        conversation_id: str,
        *,
        source_id: str,
        source_kind: str,
        content: str,
        trust: float = 0.52,
        catalog_revision: str | None = None,
        created_at: float | None = None,
    ) -> dict[str, Any]:
        """Persist one canonical derived observation per immutable source id."""

        item = self._prepare_context_item(
            source_id=source_id,
            source_kind=source_kind,
            content=content,
            trust=trust,
            catalog_revision=catalog_revision,
            created_at=created_at,
        )
        if item is None:
            reason = "missing_source" if not str(source_id or "").strip() else "empty"
            return {"stored": False, "reason": reason}

        with self._lock, self._connect() as connection:
            stored_source, stored_hash = self._upsert_context_item(
                connection,
                conversation_id,
                item,
            )
            self._prune_context_items(connection, conversation_id)
            row = connection.execute(
                """select * from context_memory_items
                   where conversation_id=? and source_id=? and content_hash=?""",
                (conversation_id, stored_source, stored_hash),
            ).fetchone()

        if row is None:
            return {"stored": False, "reason": "retention"}
        return {"stored": True, **dict(row)}

    def remember_context_items(
        self,
        conversation_id: str,
        items: list[dict[str, Any]],
        *,
        catalog_revision: str | None = None,
        created_at: float | None = None,
    ) -> None:
        """Persist multiple source observations in one SQLite transaction."""

        prepared: list[dict[str, Any]] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            item = self._prepare_context_item(
                source_id=str(row.get("source_id") or ""),
                source_kind=str(row.get("source_kind") or "derived"),
                content=str(row.get("content") or ""),
                trust=float(row.get("trust", 0.52) or 0.52),
                catalog_revision=(
                    str(row.get("catalog_revision"))
                    if row.get("catalog_revision") is not None
                    else catalog_revision
                ),
                created_at=(
                    float(row.get("created_at"))
                    if row.get("created_at") is not None
                    else created_at
                ),
            )
            if item is not None:
                prepared.append(item)

        if not prepared:
            return

        with self._lock, self._connect() as connection:
            for item in prepared:
                self._upsert_context_item(connection, conversation_id, item)
            self._prune_context_items(connection, conversation_id)


    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message_id = f"msg-{uuid.uuid4().hex[:12]}"
        now = time.time()
        payload = payload or {}
        with self._lock, self._connect() as connection:
            if role == "user":
                count = connection.execute(
                    "select count(*) from messages where conversation_id=?",
                    (conversation_id,),
                ).fetchone()[0]
                if count == 0:
                    connection.execute(
                        "update conversations set title=?,updated_at=? where id=?",
                        (content.replace("\n", " ")[:34], now, conversation_id),
                    )
                else:
                    connection.execute(
                        "update conversations set updated_at=? where id=?",
                        (now, conversation_id),
                    )
            else:
                connection.execute(
                    "update conversations set updated_at=? where id=?",
                    (now, conversation_id),
                )
            connection.execute(
                "insert into messages values(?,?,?,?,?,?)",
                (
                    message_id,
                    conversation_id,
                    role,
                    content,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
        return {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "payload": payload,
            "created_at": now,
        }

    def consume_rate_limit(
        self, scope_key: str, *, limit: int, window_seconds: float, now: float | None = None
    ) -> bool:
        now = time.time() if now is None else float(now)
        limit = max(1, int(limit))
        window_seconds = max(1.0, float(window_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select window_start,count from rate_limits where scope_key=?", (scope_key,)
            ).fetchone()
            window_start = float(row["window_start"]) if row else now
            if row and window_start > now:
                window_start = now
                connection.execute(
                    "update rate_limits set window_start=?,updated_at=? where scope_key=?",
                    (now, now, scope_key),
                )
            if not row or now - window_start >= window_seconds:
                connection.execute(
                    """
                    insert into rate_limits(scope_key,window_start,count,updated_at) values(?,?,1,?)
                    on conflict(scope_key) do update set
                      window_start=excluded.window_start,count=1,updated_at=excluded.updated_at
                    """,
                    (scope_key, now, now),
                )
                allowed = True
            elif int(row["count"]) >= limit:
                connection.execute(
                    "update rate_limits set updated_at=? where scope_key=?", (now, scope_key)
                )
                allowed = False
            else:
                connection.execute(
                    "update rate_limits set count=count+1,updated_at=? where scope_key=?",
                    (now, scope_key),
                )
                allowed = True
            if int(now) % 101 == 0:
                connection.execute(
                    "delete from rate_limits where updated_at<?", (now - 86400.0,)
                )
            connection.commit()
        return allowed

    def ensure_workspace_revision(self, revision: str) -> str:
        revision = str(revision or "").strip()
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select catalog_revision from workspace_state where id=1"
            ).fetchone()
            current = str(row["catalog_revision"] or "") if row else ""
            if not current and revision:
                connection.execute(
                    "update workspace_state set catalog_revision=?,updated_at=? where id=1",
                    (revision, time.time()),
                )
                current = revision
            connection.commit()
        return current

    def workspace_revision(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "select catalog_revision from workspace_state where id=1"
            ).fetchone()
        return str(row["catalog_revision"] or "") if row else ""

    def workspace_update_active(self, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            row = self._workspace_update_row(connection, now)
            connection.commit()
        return bool(
            row
            and row.get("update_owner")
            and float(row.get("update_until") or 0.0) > now
        )

    def begin_workspace_update(
        self, owner_id: str, *, lease_seconds: float = 120.0, now: float | None = None
    ) -> bool:
        now = time.time() if now is None else float(now)
        until = now + max(5.0, float(lease_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            active = connection.execute(
                "select 1 from runs where status in ('running','interrupted','cancel_requested') limit 1"
            ).fetchone()
            if active:
                connection.rollback()
                return False
            row = self._workspace_update_row(connection, now)
            if (
                row
                and row.get("update_owner")
                and row.get("update_owner") != owner_id
                and float(row.get("update_until") or 0.0) > now
            ):
                connection.rollback()
                return False
            connection.execute(
                "update workspace_state set update_owner=?,update_until=?,updated_at=? where id=1",
                (owner_id, until, now),
            )
            connection.commit()
        return True

    def commit_workspace_revision(self, owner_id: str, revision: str) -> bool:
        revision = str(revision or "").strip()
        if not revision:
            return False
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            row = self._workspace_update_row(connection, now)
            if (
                not row
                or row.get("update_owner") != owner_id
                or float(row.get("update_until") or 0.0) <= now
            ):
                connection.rollback()
                return False
            active = connection.execute(
                "select 1 from runs where status in ('running','interrupted','cancel_requested') limit 1"
            ).fetchone()
            if active:
                connection.rollback()
                return False
            cursor = connection.execute(
                """
                update workspace_state
                set catalog_revision=?,update_owner=null,update_until=null,updated_at=?
                where id=1 and update_owner=? and update_until>?
                """,
                (revision, now, owner_id, now),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
        return True

    def abort_workspace_update(self, owner_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                update workspace_state
                set update_owner=null,update_until=null,updated_at=?
                where id=1 and update_owner=?
                """,
                (time.time(), owner_id),
            )

    def reserve_run(
        self,
        run_id: str,
        conversation_id: str,
        goal: str,
        snapshot: dict[str, Any],
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> bool:
        now = time.time()
        created = min(float(snapshot.get("created_at") or now), now)
        lease_until = now + max(1.0, float(lease_seconds))
        payload = dict(snapshot)
        payload.update(
            {
                "run_id": run_id,
                "conversation_id": conversation_id,
                "goal": goal,
                "status": "running",
                "created_at": created,
                "updated_at": now,
            }
        )
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            workspace = self._workspace_update_row(connection, now)
            if (
                workspace
                and workspace.get("update_owner")
                and float(workspace.get("update_until") or 0.0) > now
            ):
                connection.rollback()
                return False
            active = connection.execute(
                """
                select run_id from runs
                where conversation_id=? and status in ('running','interrupted','cancel_requested')
                limit 1
                """,
                (conversation_id,),
            ).fetchone()
            if active:
                connection.rollback()
                return False
            connection.execute(
                """
                insert into runs(
                  run_id,conversation_id,goal,status,snapshot,created_at,updated_at,owner_id,lease_until
                ) values(?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    conversation_id,
                    goal,
                    "running",
                    json.dumps(payload, ensure_ascii=False),
                    created,
                    now,
                    owner_id,
                    lease_until,
                ),
            )
            connection.commit()
        return True

    def delete_run(self, run_id: str, *, owner_id: str | None = None) -> None:
        with self._lock, self._connect() as connection:
            if owner_id:
                connection.execute(
                    "delete from runs where run_id=? and owner_id=?", (run_id, owner_id)
                )
            else:
                connection.execute("delete from runs where run_id=?", (run_id,))

    def save_run(
        self,
        run_id: str,
        conversation_id: str,
        goal: str,
        status: str,
        snapshot: dict[str, Any],
        *,
        owner_id: str | None = None,
        lease_seconds: float = 30.0,
    ) -> str:
        decision_at = time.time()
        created = min(float(snapshot.get("created_at") or decision_at), decision_at)
        active = status in ACTIVE_RUN_STATUSES
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            existing = connection.execute(
                "select status,owner_id,lease_until from runs where run_id=?", (run_id,)
            ).fetchone()
            if existing and existing["status"] not in ACTIVE_RUN_STATUSES:
                connection.rollback()
                return str(existing["status"])
            if (
                existing
                and existing["status"] in ACTIVE_RUN_STATUSES
                and existing["owner_id"]
                and str(existing["owner_id"]) != str(owner_id or "")
            ):
                connection.rollback()
                return str(existing["status"])
            payload = dict(snapshot)
            if (
                existing
                and existing["status"] == "cancel_requested"
                and status in {"running", "interrupted"}
            ):
                status = "cancel_requested"
            payload.update({"status": status, "created_at": created, "updated_at": decision_at})
            current_owner = owner_id if active else None
            if active and existing and existing["owner_id"] and owner_id is None:
                current_owner = existing["owner_id"]
            lease_until = (
                decision_at + max(1.0, float(lease_seconds))
                if active and current_owner
                else None
            )
            connection.execute(
                """
                insert into runs(
                  run_id,conversation_id,goal,status,snapshot,created_at,updated_at,owner_id,lease_until
                ) values(?,?,?,?,?,?,?,?,?)
                on conflict(run_id) do update set
                  conversation_id=excluded.conversation_id,
                  goal=excluded.goal,
                  status=excluded.status,
                  snapshot=excluded.snapshot,
                  updated_at=excluded.updated_at,
                  owner_id=excluded.owner_id,
                  lease_until=excluded.lease_until
                """,
                (
                    run_id,
                    conversation_id,
                    goal,
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    created,
                    decision_at,
                    current_owner,
                    lease_until,
                ),
            )
            connection.commit()
        return status

    def renew_run_lease(self, run_id: str, owner_id: str, lease_seconds: float) -> bool:
        now = time.time()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                update runs set lease_until=?,updated_at=?
                where run_id=? and owner_id=?
                  and status in ('running','interrupted','cancel_requested')
                """,
                (now + max(1.0, float(lease_seconds)), now, run_id, owner_id),
            )
            return cursor.rowcount == 1

    def run_status(self, run_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "select status from runs where run_id=?", (run_id,)
            ).fetchone()
        return str(row["status"]) if row else None

    def request_cancel(self, run_id: str) -> str:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            row = connection.execute(
                "select status,snapshot from runs where run_id=?", (run_id,)
            ).fetchone()
            if not row:
                connection.rollback()
                raise KeyError(run_id)
            status = str(row["status"])
            if status == "cancelled":
                connection.rollback()
                return "cancelled"
            if status not in ACTIVE_RUN_STATUSES:
                connection.rollback()
                raise RuntimeError(status)
            snapshot = self._loads(row["snapshot"])
            snapshot.update({"status": "cancel_requested", "updated_at": now})
            connection.execute(
                """
                update runs set status='cancel_requested',snapshot=?,updated_at=?
                where run_id=?
                """,
                (json.dumps(snapshot, ensure_ascii=False), now, run_id),
            )
            connection.commit()
        return "cancel_requested"

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "select snapshot,status,owner_id,lease_until from runs where run_id=?",
                (run_id,),
            ).fetchone()
        if not row:
            raise KeyError(run_id)
        snapshot = self._loads(row["snapshot"])
        snapshot["status"] = row["status"]
        snapshot["owner_id"] = row["owner_id"]
        snapshot["lease_until"] = row["lease_until"]
        return snapshot

    def active_conversation_ids(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select distinct conversation_id from runs
                where status in ('running','interrupted','cancel_requested')
                """
            ).fetchall()
        return {str(row["conversation_id"]) for row in rows}

    def active_run_for_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                select snapshot,status,owner_id,lease_until from runs
                where conversation_id=?
                  and status in ('running','interrupted','cancel_requested')
                order by updated_at desc limit 1
                """,
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        snapshot = self._loads(row["snapshot"])
        snapshot["status"] = row["status"]
        snapshot["owner_id"] = row["owner_id"]
        snapshot["lease_until"] = row["lease_until"]
        return snapshot

    def claim_recoverable_runs(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int = 20,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time() if now is None else float(now)
        lease_until = now + max(1.0, float(lease_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            self._repair_future_run_leases(connection, now)
            rows = connection.execute(
                """
                select run_id,conversation_id,goal,status,snapshot
                from runs
                where status in ('running','interrupted','cancel_requested')
                  and (owner_id is null or owner_id=? or lease_until is null or lease_until<?)
                order by updated_at desc
                limit ?
                """,
                (owner_id, now, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                cursor = connection.execute(
                    """
                    update runs set owner_id=?,lease_until=?
                    where run_id=?
                      and status in ('running','interrupted','cancel_requested')
                      and (owner_id is null or owner_id=? or lease_until is null or lease_until<?)
                    """,
                    (owner_id, lease_until, row["run_id"], owner_id, now),
                )
                if cursor.rowcount != 1:
                    continue
                claimed.append(
                    {
                        "run_id": row["run_id"],
                        "conversation_id": row["conversation_id"],
                        "goal": row["goal"],
                        "status": row["status"],
                        "snapshot": self._loads(row["snapshot"]),
                    }
                )
            connection.commit()
        return claimed

    def recoverable_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select run_id,conversation_id,goal,status,snapshot from runs
                where status in ('running','interrupted','cancel_requested')
                order by updated_at desc limit ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "conversation_id": row["conversation_id"],
                "goal": row["goal"],
                "status": row["status"],
                "snapshot": self._loads(row["snapshot"]),
            }
            for row in rows
        ]

    def assistant_for_job(self, conversation_id: str, job_id: str) -> dict[str, Any] | None:
        for message in reversed(self.list_messages(conversation_id)):
            if (
                message["role"] == "assistant"
                and message.get("payload", {}).get("job_id") == job_id
            ):
                return message
        return None

    def referenced_attachment_ids(self) -> set[str]:
        def collect(value: Any, output: set[str]) -> None:
            if isinstance(value, dict):
                attachment_id = value.get("id")
                if isinstance(attachment_id, str) and attachment_id.startswith("att-"):
                    output.add(attachment_id)
                for child in value.values():
                    collect(child, output)
            elif isinstance(value, list):
                for child in value:
                    collect(child, output)
            elif isinstance(value, str) and value.startswith("att-"):
                output.add(value)

        with self._connect() as connection:
            message_rows = connection.execute(
                "select payload from messages where payload like '%att-%'"
            ).fetchall()
            run_rows = connection.execute(
                "select snapshot from runs where snapshot like '%att-%'"
            ).fetchall()
        referenced: set[str] = set()
        for row in message_rows:
            collect(self._loads(row["payload"]), referenced)
        for row in run_rows:
            collect(self._loads(row["snapshot"]), referenced)
        return referenced
