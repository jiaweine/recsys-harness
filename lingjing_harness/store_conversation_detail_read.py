from __future__ import annotations

from typing import Any


def install_conversation_detail_read_boundary(store_module: Any) -> None:
    """Keep full conversation-detail reads from holding the writer mutex.

    The detail endpoint intentionally returns the complete message history, but
    decoding that history can take time as a conversation grows. The base store
    held ``self._lock`` across that entire read, which serialized same-process
    message writes and durable run updates behind a read-only request.

    Conversation/message writes commit atomically on one SQLite connection, so the
    detail read can release the process-local mutex after fetching the conversation
    row and then perform the historical message read independently. Cross-worker
    writes may race the two reads, but the returned payload remains a valid
    conversation snapshot and avoids turning a large read into a writer gate.
    """

    cls = store_module.WorkspaceStore
    if getattr(cls, "_CONVERSATION_DETAIL_READ_BOUNDARY_INSTALLED", False):
        return

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "select * from conversations where id=?", (conversation_id,)
            ).fetchone()
        if not row:
            raise KeyError(conversation_id)
        return {**dict(row), "messages": self.list_messages(conversation_id)}

    cls.get_conversation = get_conversation
    cls._CONVERSATION_DETAIL_READ_BOUNDARY_INSTALLED = True


__all__ = ["install_conversation_detail_read_boundary"]
