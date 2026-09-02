from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ACTIVE_RUN_STATUSES = ("running", "interrupted", "cancel_requested")


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

    def list_conversations(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from conversations order by updated_at desc limit ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def create_conversation(self, title: str = "新的体验任务", scene: str = "audit") -> dict[str, Any]:
        now = time.time()
        conversation_id = f"cv-{uuid.uuid4().hex[:10]}"
        with self._connect() as connection:
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
        with self._connect() as connection:
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
                # A wall-clock jump forward followed by rollback must not freeze a
                # shared limiter until the old future timestamp is reached. Repair
                # only the window anchor while preserving the consumed count, so
                # clock recovery never manufactures extra allowance.
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
        with self._connect() as connection:
            row = connection.execute(
                "select update_owner,update_until from workspace_state where id=1"
            ).fetchone()
        return bool(
            row
            and row["update_owner"]
            and float(row["update_until"] or 0.0) > now
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
            row = connection.execute(
                "select update_owner,update_until from workspace_state where id=1"
            ).fetchone()
            if (
                row
                and row["update_owner"]
                and row["update_owner"] != owner_id
                and float(row["update_until"] or 0.0) > now
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
            row = connection.execute(
                "select update_owner,update_until from workspace_state where id=1"
            ).fetchone()
            if (
                not row
                or row["update_owner"] != owner_id
                or float(row["update_until"] or 0.0) <= now
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
            workspace = connection.execute(
                "select update_owner,update_until from workspace_state where id=1"
            ).fetchone()
            if (
                workspace
                and workspace["update_owner"]
                and float(workspace["update_until"] or 0.0) > now
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
            # Terminal run states are monotonic. Once a run has completed, failed, or
            # been cancelled, no late checkpoint from any worker may resurrect or
            # overwrite that terminal result. This is the final fencing boundary for
            # workers that outlive their lease and return after a takeover finishes.
            if existing and existing["status"] not in ACTIVE_RUN_STATUSES:
                connection.rollback()
                return str(existing["status"])
            if (
                existing
                and existing["status"] in ACTIVE_RUN_STATUSES
                and existing["owner_id"]
                and str(existing["owner_id"]) != str(owner_id or "")
            ):
                # Expiration grants eligibility to *claim* a run; it never restores
                # an old owner's write authority. Ownership transfer is linearized
                # only by claim_recoverable_runs() or graceful handoff.
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
