from __future__ import annotations

import json
import sqlite3
from typing import Any


def install_conversation_detail_snapshot_boundary(store_module: Any) -> None:
    """Decode conversation message payloads after releasing the writer mutex.

    The detail read intentionally keeps the process-local mutex around both SQLite
    SELECTs so same-process writers cannot interleave between the conversation row
    and its message-row snapshot. The expensive JSON decoding does not need that
    serialization, however, so perform it only after the database connection and
    writer mutex have been released.
    """

    cls = store_module.WorkspaceStore
    if getattr(cls, "_CONVERSATION_DETAIL_SNAPSHOT_BOUNDARY_INSTALLED", False):
        return

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            with self._connect() as connection:
                conversation_row = connection.execute(
                    "select * from conversations where id=?", (conversation_id,)
                ).fetchone()
                if not conversation_row:
                    raise KeyError(conversation_id)
                message_rows = connection.execute(
                    "select * from messages where conversation_id=? order by created_at",
                    (conversation_id,),
                ).fetchall()

        messages: list[dict[str, Any]] = []
        for row in message_rows:
            data = dict(row)
            data["payload"] = self._loads(data.pop("payload"))
            messages.append(data)
        return {**dict(conversation_row), "messages": messages}

    def conversation_active_run_view(
        self,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        """Return only the active-run fields rendered by conversation detail.

        Active run snapshots can grow to megabytes with checkpoints and tool
        observations. The detail surface only needs run_id/status/events, so let
        SQLite's JSON engine project the events subtree instead of transferring and
        decoding the entire snapshot in Python. Older SQLite builds fall back to
        the existing full-snapshot reader without changing public semantics.
        """

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    select run_id,status,json_extract(snapshot,'$.events') as events
                    from runs
                    where conversation_id=?
                      and status in ('running','interrupted','cancel_requested')
                    order by updated_at desc limit 1
                    """,
                    (conversation_id,),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            if "json_extract" not in str(exc).lower():
                raise
            active = self.active_run_for_conversation(conversation_id)
            if not active:
                return None
            return {
                "run_id": active["run_id"],
                "status": active["status"],
                "events": active.get("events", []),
            }

        if not row:
            return None
        raw_events = row["events"]
        try:
            events = json.loads(raw_events) if raw_events else []
        except (json.JSONDecodeError, TypeError):
            events = []
        if not isinstance(events, list):
            events = []
        return {
            "run_id": str(row["run_id"]),
            "status": str(row["status"]),
            "events": events,
        }

    cls.get_conversation = get_conversation
    cls.conversation_active_run_view = conversation_active_run_view
    cls._CONVERSATION_DETAIL_SNAPSHOT_BOUNDARY_INSTALLED = True


__all__ = ["install_conversation_detail_snapshot_boundary"]
