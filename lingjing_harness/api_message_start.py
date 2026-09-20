from __future__ import annotations

from typing import Any


def install_message_store_fast_paths(store_module: Any) -> None:
    """Compatibility shim for older integrations.

    Message existence checks and the early-stop first-message probe now live
    directly on WorkspaceStore, so there is no runtime monkeypatch to install.
    """

    cls = store_module.WorkspaceStore
    required = (
        "conversation_exists",
        "start_run_with_user_message",
    )
    missing = [name for name in required if not hasattr(cls, name)]
    if missing:
        raise RuntimeError(
            "WorkspaceStore task-start fast paths are missing: "
            + ", ".join(missing)
        )
    cls._MESSAGE_START_FAST_PATHS_INSTALLED = True


def install_message_start_boundary(core: Any) -> None:
    """Verify that the core route uses the native atomic task-start boundary."""

    if getattr(core, "_MESSAGE_START_BOUNDARY_INSTALLED", False):
        return
    if not hasattr(core.store, "start_run_with_user_message"):
        raise RuntimeError("atomic task-start store boundary is missing")
    core._MESSAGE_START_BOUNDARY_INSTALLED = True


__all__ = [
    "install_message_start_boundary",
    "install_message_store_fast_paths",
]
