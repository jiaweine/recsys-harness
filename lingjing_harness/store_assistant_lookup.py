from __future__ import annotations

from typing import Any


def install_fresh_run_assistant_lookup_gate(store_module: Any) -> None:
    """Skip full conversation scans when a run is provably brand new.

    ``WorkspaceStore.assistant_for_job`` is a legacy crash-recovery boundary: an
    older worker could publish the assistant message and crash before persisting
    the terminal run snapshot, so recovery must still be able to discover that
    message by ``job_id``.  Atomic run-completion publication has closed that gap
    for current writers, but normal new runs still called the legacy lookup before
    their first action and therefore decoded the entire conversation history.

    A newly reserved run is distinguishable without changing durable schemas.  Its
    primary-keyed run snapshot has no events, checkpoint, result, or message yet.
    In that state an assistant publication is impossible under both the historical
    runner order and the current atomic completion boundary, so return immediately.
    Any run with execution evidence falls back to the original lookup, preserving
    recovery compatibility for old databases and interrupted workers.
    """

    cls = store_module.WorkspaceStore
    if getattr(cls, "_FRESH_RUN_ASSISTANT_LOOKUP_GATE_INSTALLED", False):
        return

    original_assistant_for_job = cls.assistant_for_job
    active_statuses = frozenset(str(value) for value in store_module.ACTIVE_RUN_STATUSES)

    def assistant_for_job(self, conversation_id: str, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "select conversation_id,status,snapshot from runs where run_id=?",
                (job_id,),
            ).fetchone()

        if (
            row
            and str(row["conversation_id"]) == str(conversation_id)
            and str(row["status"]) in active_statuses
        ):
            snapshot = self._loads(row["snapshot"])
            if (
                not snapshot.get("events")
                and snapshot.get("checkpoint") is None
                and snapshot.get("result") is None
                and snapshot.get("message") is None
            ):
                return None

        return original_assistant_for_job(self, conversation_id, job_id)

    cls.assistant_for_job = assistant_for_job
    cls._FRESH_RUN_ASSISTANT_LOOKUP_GATE_INSTALLED = True


__all__ = ["install_fresh_run_assistant_lookup_gate"]
