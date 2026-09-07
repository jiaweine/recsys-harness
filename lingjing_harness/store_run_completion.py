from __future__ import annotations

import copy
import json
import time
import uuid
from typing import Any


_ACTIVE_COMPLETION_STATUSES = frozenset({"running", "interrupted"})


def install_run_completion_publication_fence(core: Any) -> None:
    """Linearize assistant publication with durable run completion.

    A remote cancel can land after the runner's final cooperative stop check but
    before ``api_core`` publishes the assistant message.  Publishing the message
    and only then persisting ``completed`` leaves a window where ``request_cancel``
    can successfully commit ``cancel_requested`` and still be overwritten by the
    stale completion path.

    Intercept only assistant messages carrying a durable ``job_id``.  Their
    message insert and run-terminal transition happen in one ``BEGIN IMMEDIATE``
    transaction, so cancellation and completion have a single SQLite ordering:

    * completion wins first -> the run is terminal before cancellation can inspect
      it, so a later cancel is rejected as already finished;
    * cancellation wins first -> no assistant message is inserted and the existing
      ``RunCancelled`` handler finalizes the stop.

    Ordinary conversation messages keep the historical ``add_message`` path.
    """

    cls = type(core.store)
    if getattr(cls, "_RUN_COMPLETION_PUBLICATION_FENCE_INSTALLED", False):
        return

    original_add_message = cls.add_message

    def publish_run_completion(
        self,
        conversation_id: str,
        content: str,
        payload: dict[str, Any],
        *,
        owner_id: str,
    ) -> tuple[str, dict[str, Any] | None]:
        run_id = str(payload.get("job_id") or "")
        if not run_id:
            return "missing", None

        now = time.time()
        with self._lock, self._connect() as connection:  # noqa: SLF001 - package-internal durable boundary
            connection.execute("begin immediate")
            row = connection.execute(
                "select status,owner_id,snapshot from runs where run_id=?",
                (run_id,),
            ).fetchone()
            if not row:
                connection.rollback()
                return "missing", None

            status = str(row["status"])
            durable_owner = str(row["owner_id"] or "")
            if status == "cancel_requested":
                connection.rollback()
                return status, None
            if status not in _ACTIVE_COMPLETION_STATUSES or durable_owner != str(owner_id):
                connection.rollback()
                return status, None

            message_id = f"msg-{uuid.uuid4().hex[:12]}"
            message_payload = copy.deepcopy(payload)
            message = {
                "id": message_id,
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": content,
                "payload": message_payload,
                "created_at": now,
            }
            snapshot = self._loads(row["snapshot"])  # noqa: SLF001 - same durable representation as WorkspaceStore
            snapshot.update(
                {
                    "status": "completed",
                    "result": copy.deepcopy(message_payload),
                    "message": copy.deepcopy(message),
                    "updated_at": now,
                }
            )
            snapshot.pop("checkpoint", None)

            connection.execute(
                "update conversations set updated_at=? where id=?",
                (now, conversation_id),
            )
            connection.execute(
                "insert into messages values(?,?,?,?,?,?)",
                (
                    message_id,
                    conversation_id,
                    "assistant",
                    content,
                    json.dumps(message_payload, ensure_ascii=False),
                    now,
                ),
            )
            cursor = connection.execute(
                """
                update runs
                set status='completed',snapshot=?,updated_at=?,owner_id=null,lease_until=null
                where run_id=? and owner_id=? and status in ('running','interrupted')
                """,
                (
                    json.dumps(snapshot, ensure_ascii=False),
                    now,
                    run_id,
                    owner_id,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return status, None
            connection.commit()
        return "completed", message

    def discard_stale_local_run(run_id: str) -> None:
        runs = getattr(core, "RUNS", None)
        run_lock = getattr(core, "RUN_LOCK", None)
        if isinstance(runs, dict) and run_lock is not None:
            with run_lock:
                runs.pop(run_id, None)
        persist_meta = getattr(core, "_PERSIST_META", None)
        if isinstance(persist_meta, dict):
            persist_meta.pop(run_id, None)

    def add_message_with_run_completion(
        self,
        conversation_id: str,
        role: str,
        content: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message_payload = payload or {}
        run_id = str(message_payload.get("job_id") or "") if role == "assistant" else ""
        if not run_id:
            return original_add_message(self, conversation_id, role, content, payload)

        status, message = self.publish_run_completion(
            conversation_id,
            content,
            message_payload,
            owner_id=core.WORKER_ID,
        )
        if status == "missing":
            # Preserve generic WorkspaceStore behavior for callers that use a
            # job-shaped payload without a corresponding durable run.
            return original_add_message(self, conversation_id, role, content, payload)
        if status == "completed" and message is not None:
            return message
        if status == "cancel_requested":
            raise core.RunCancelled(f"run cancel requested before assistant publish: {run_id}")

        # ``api_core`` catches ordinary Exception values around execution and
        # translates them into its generic failure path.  A publication-time
        # lease loss is authority control flow, not a failed run.  Retire the
        # stale local row before raising so that generic handler has nothing to
        # rewrite into a terminal-but-stale local snapshot.  The next GET then
        # reads the successor's authoritative durable payload directly.
        discard_stale_local_run(run_id)
        raise core._RunLeaseLost(
            f"run lease lost before assistant publish: {run_id} ({status})"
        )

    cls.publish_run_completion = publish_run_completion
    cls.add_message = add_message_with_run_completion
    cls._RUN_COMPLETION_PUBLICATION_FENCE_INSTALLED = True


__all__ = ["install_run_completion_publication_fence"]