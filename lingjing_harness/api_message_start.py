import contextvars
import json
import time
import uuid
from typing import Any


_MESSAGE_START_EXISTS_ONLY = contextvars.ContextVar(
    "xushu_message_start_exists_only",
    default=False,
)


def install_message_store_fast_paths(store_module: Any) -> None:
    """Keep message-start existence checks bounded as conversations grow.

    ``api_core`` historically reused ``get_conversation`` to validate a POST target.
    That method is intentionally a detail read and therefore loads/decodes the full
    message history.  A task start only needs the conversation primary key to
    exist.  The ContextVar below lets the stable API wrapper request that one-shot
    existence-only behavior without changing normal conversation-detail reads.

    The same hot path also asked SQLite for ``count(*)`` merely to decide whether a
    user message is the first message in a conversation.  Replace that cardinality
    scan with an indexed early-stop probe while preserving the original title and
    timestamp semantics.
    """

    cls = store_module.WorkspaceStore
    if getattr(cls, "_MESSAGE_START_FAST_PATHS_INSTALLED", False):
        return

    original_get_conversation = cls.get_conversation
    original_add_message = cls.add_message

    def conversation_exists(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "select 1 from conversations where id=? limit 1",
                (conversation_id,),
            ).fetchone()
        return row is not None

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        if not _MESSAGE_START_EXISTS_ONLY.get():
            return original_get_conversation(self, conversation_id)

        # Consume the flag before the endpoint creates its background execution
        # task. asyncio.create_task copies ContextVars; leaving this set would make
        # unrelated reads inside that task inherit existence-only semantics.
        _MESSAGE_START_EXISTS_ONLY.set(False)
        with self._connect() as connection:
            row = connection.execute(
                "select * from conversations where id=?",
                (conversation_id,),
            ).fetchone()
        if not row:
            raise KeyError(conversation_id)
        return dict(row)

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role != "user":
            return original_add_message(self, conversation_id, role, content, payload)

        message_id = f"msg-{uuid.uuid4().hex[:12]}"
        now = time.time()
        message_payload = payload or {}
        with self._lock, self._connect() as connection:
            has_message = connection.execute(
                "select 1 from messages where conversation_id=? limit 1",
                (conversation_id,),
            ).fetchone()
            if has_message is None:
                connection.execute(
                    "update conversations set title=?,updated_at=? where id=?",
                    (content.replace("\n", " ")[:34], now, conversation_id),
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
                    json.dumps(message_payload, ensure_ascii=False),
                    now,
                ),
            )
        return {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "payload": message_payload,
            "created_at": now,
        }

    cls.conversation_exists = conversation_exists
    cls.get_conversation = get_conversation
    cls.add_message = add_message
    cls._MESSAGE_START_FAST_PATHS_INSTALLED = True


def install_message_start_boundary(core: Any) -> None:
    """Use the one-shot existence read only for task-start POST requests."""

    if getattr(core, "_MESSAGE_START_BOUNDARY_INSTALLED", False):
        return

    target = None
    for route in list(core.app.router.routes):
        if (
            getattr(route, "path", None) == "/api/conversations/{cid}/messages"
            and "POST" in (getattr(route, "methods", None) or set())
        ):
            target = route
            break
    if target is None:
        raise RuntimeError("message-start route is missing")

    original_endpoint = target.endpoint

    async def bounded_add_message(cid: str, req: core.ChatRequest):
        token = _MESSAGE_START_EXISTS_ONLY.set(True)
        try:
            return await original_endpoint(cid, req)
        finally:
            _MESSAGE_START_EXISTS_ONLY.reset(token)

    core.app.router.routes.remove(target)
    core.app.add_api_route(
        "/api/conversations/{cid}/messages",
        bounded_add_message,
        methods=["POST"],
        name="add_message",
    )
    core.add_message = bounded_add_message
    core._MESSAGE_START_BOUNDARY_INSTALLED = True


__all__ = [
    "install_message_start_boundary",
    "install_message_store_fast_paths",
]
