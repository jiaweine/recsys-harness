from __future__ import annotations

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

    cls.get_conversation = get_conversation
    cls._CONVERSATION_DETAIL_SNAPSHOT_BOUNDARY_INSTALLED = True


__all__ = ["install_conversation_detail_snapshot_boundary"]
