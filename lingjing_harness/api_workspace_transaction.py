from __future__ import annotations

from contextlib import suppress
import json
from pathlib import Path
from typing import Any


def install_workspace_transaction_boundary(core: Any) -> None:
    """Make Catalog-file publication recoverable across the SQLite commit boundary.

    A workspace import spans two durability domains: the Catalog JSON file and the
    SQLite workspace revision. Publishing the active JSON before committing the
    revision makes a failed commit visible anyway, while committing first without a
    staged file can leave a restarted worker unable to materialize the committed
    revision. Keep the incoming Catalog in a same-directory pending file, commit
    the durable revision under a publication fence, then atomically promote the
    pending file and explicitly release that fence.

    Synchronization is also a recovery participant: if another worker (or a
    restarted process) observes a durable revision whose matching pending file has
    not yet been promoted, it may safely finish that promotion. If a peer already
    completed the promotion, the original writer recognizes the matching active
    fingerprint as idempotent success rather than returning a false failure.
    """

    if getattr(core, "_WORKSPACE_TRANSACTION_BOUNDARY_INSTALLED", False):
        return

    core.CATALOG_PENDING_FILE = core.DATA / "catalog.pending.json"

    def _write_catalog(path: Path, catalog: Any) -> None:
        temp = path.with_name(f"{path.name}.tmp")
        temp.write_text(
            json.dumps(
                {"name": catalog.name, "data": catalog.to_payload()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp.replace(path)

    def _load_catalog_path(path: Path) -> Any:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("catalog payload must be an object")
        return core.Catalog.from_payload(
            payload.get("data", payload),
            name=payload.get("name", "工作区数据"),
        )

    def _pending_catalog() -> tuple[str | None, Any | None]:
        path = core.CATALOG_PENDING_FILE
        if not path.exists():
            return None, None
        try:
            candidate = _load_catalog_path(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None, None
        return core.catalog_fingerprint(candidate), candidate

    def _discard_pending() -> None:
        with suppress(OSError):
            core.CATALOG_PENDING_FILE.unlink(missing_ok=True)
        temp = core.CATALOG_PENDING_FILE.with_name(
            f"{core.CATALOG_PENDING_FILE.name}.tmp"
        )
        with suppress(OSError):
            temp.unlink(missing_ok=True)

    def _active_catalog_for_revision(revision: str) -> Any | None:
        try:
            active = core._load_catalog()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if core.catalog_fingerprint(active) == revision:
            return active
        return None

    def _promote_pending(
        shared_revision: str, candidate: Any | None = None
    ) -> Any | None:
        pending_revision, pending = _pending_catalog()
        if pending_revision != shared_revision or pending is None:
            # A peer may have finished pending -> active before this worker even
            # entered the promotion helper. Accept only the exact durable
            # fingerprint; a missing or unrelated pending file is never guessed.
            return _active_catalog_for_revision(shared_revision)
        if candidate is None:
            candidate = pending
        try:
            core.CATALOG_PENDING_FILE.replace(core.CATALOG_FILE)
            return candidate
        except FileNotFoundError:
            # A peer may have completed the same promotion after our pending read.
            return _active_catalog_for_revision(shared_revision)
        except OSError:
            return None

    def _install_catalog(candidate: Any, revision: str) -> None:
        core.catalog = candidate
        core.harness = core.AgentHarness(candidate, memory=core.memory)
        core.CATALOG_REVISION = revision

    def _release_publication(revision: str) -> bool:
        finish = getattr(core.store, "finish_workspace_publication", None)
        if not callable(finish):
            return True
        try:
            # False is also benign: it means there was no matching publication
            # fence, for example a legacy/manual durable revision.
            finish(revision)
            return True
        except Exception:
            return False

    def _sync_workspace() -> bool:
        shared = core.store.ensure_workspace_revision(core.CATALOG_REVISION)
        if not shared:
            return True

        with core.WORKSPACE_LOCK:
            shared = core.store.workspace_revision() or shared
            active = core._load_catalog()
            active_revision = core.catalog_fingerprint(active)

            if active_revision == shared:
                if core.CATALOG_REVISION != shared:
                    _install_catalog(active, shared)
                if not _release_publication(shared):
                    return False
                try:
                    updating = core.store.workspace_update_active()
                except Exception:
                    return False
                if not updating:
                    _discard_pending()
                return True

            pending_revision, pending = _pending_catalog()
            if pending_revision == shared and pending is not None:
                promoted = _promote_pending(shared, pending)
                if promoted is None:
                    return False
                _install_catalog(promoted, shared)
                if not _release_publication(shared):
                    return False
                _discard_pending()
                return True

            # A writer using this protocol never changes the active file before
            # the durable commit. Therefore an active/durable mismatch without a
            # matching pending file is not safe to guess through.
            return False

    def _activate_catalog(new: Any) -> str:
        with core.RUN_LOCK:
            if any(
                row.get("status") in core.ACTIVE_RUN_STATUSES
                for row in core.RUNS.values()
            ):
                raise core.HTTPException(
                    409,
                    "仍有任务在执行，请停止或等待完成后再更换工作区数据",
                )

        if not core.store.begin_workspace_update(
            core.WORKER_ID,
            lease_seconds=core.WORKSPACE_UPDATE_LEASE_SECONDS,
        ):
            raise core.HTTPException(
                409,
                "工作区正在执行任务或更新数据，请稍后重试",
            )

        revision = core.catalog_fingerprint(new)
        committed = False
        try:
            _write_catalog(core.CATALOG_PENDING_FILE, new)

            commit_for_publication = getattr(
                core.store, "commit_workspace_revision_for_publication", None
            )
            if callable(commit_for_publication):
                committed_ok = commit_for_publication(core.WORKER_ID, revision)
            else:
                # Compatibility for narrow test doubles and legacy stores. The
                # production WorkspaceStore installs the publication-specific API.
                committed_ok = core.store.commit_workspace_revision(
                    core.WORKER_ID, revision
                )
            if not committed_ok:
                raise RuntimeError("工作区 revision 提交失败")
            committed = True

            with core.WORKSPACE_LOCK:
                promoted = _promote_pending(revision, new)
                if promoted is None:
                    raise RuntimeError("工作区 Catalog 发布失败")
                _install_catalog(promoted, revision)

            finish_owned = getattr(core.store, "finish_workspace_update", None)
            if callable(finish_owned):
                finished = finish_owned(core.WORKER_ID, revision)
                if not finished:
                    pending = getattr(core.store, "workspace_publication_pending", None)
                    if callable(pending) and pending(revision):
                        raise RuntimeError("工作区发布 fence 释放失败")
            _discard_pending()
            return revision
        except Exception:
            if not committed:
                _discard_pending()
                core.store.abort_workspace_update(core.WORKER_ID)
            # Once the durable revision committed, never erase the matching
            # pending file or roll the SQLite revision backward. A later sync can
            # safely complete publication using the fingerprint + DB phase fence.
            raise

    core._sync_workspace = _sync_workspace
    core._activate_catalog = _activate_catalog
    core._workspace_pending_catalog = _pending_catalog
    core._WORKSPACE_TRANSACTION_BOUNDARY_INSTALLED = True


__all__ = ["install_workspace_transaction_boundary"]
