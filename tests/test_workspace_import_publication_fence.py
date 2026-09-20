import threading
import time

import lingjing_harness.store as store_module
from lingjing_harness.store import WorkspaceStore


def test_workspace_update_lease_is_not_reentrant_for_same_owner(tmp_path):
    path = tmp_path / "workspace-non-reentrant.db"
    store = WorkspaceStore(path)

    assert store.ensure_workspace_revision("rev-a") == "rev-a"
    assert store.begin_workspace_update("writer-a", lease_seconds=30) is True

    # One WORKER_ID may serve multiple concurrent import requests. The durable
    # update lease protects a single global catalog.pending.json path, so equal
    # owner IDs must not turn that mutex into a re-entrant lease.
    assert store.begin_workspace_update("writer-a", lease_seconds=30) is False

    store.abort_workspace_update("writer-a")
    assert store.begin_workspace_update("writer-a", lease_seconds=30) is True


def test_publication_commit_holds_workspace_lease_until_catalog_is_published(tmp_path):
    path = tmp_path / "workspace-publication-fence.db"
    writer = WorkspaceStore(path)
    contender = WorkspaceStore(path)

    assert writer.ensure_workspace_revision("rev-a") == "rev-a"
    assert writer.begin_workspace_update("writer-a", lease_seconds=30) is True

    assert writer.commit_workspace_revision_for_publication("writer-a", "rev-b") is True
    assert contender.workspace_revision() == "rev-b"
    assert writer.workspace_publication_pending("rev-b") is True
    assert writer.workspace_update_active() is True
    assert contender.begin_workspace_update("writer-b", lease_seconds=30) is False

    # The durable publication phase is a correctness fence, not merely a timed
    # ownership lease. A crashed writer must not lose its staged revision when
    # the original lease wall-clock expires.
    after_lease_expiry = time.time() + 60
    assert writer.workspace_update_active(now=after_lease_expiry) is True
    assert (
        contender.begin_workspace_update(
            "writer-b", lease_seconds=30, now=after_lease_expiry
        )
        is False
    )

    assert writer.finish_workspace_update("writer-a", "rev-b") is True
    assert writer.workspace_publication_pending() is False
    assert writer.workspace_update_active() is False
    assert contender.begin_workspace_update("writer-b", lease_seconds=30) is True


def test_peer_can_release_publication_fence_by_exact_durable_revision(tmp_path):
    path = tmp_path / "workspace-peer-publication-fence.db"
    writer = WorkspaceStore(path)
    peer = WorkspaceStore(path)

    assert writer.ensure_workspace_revision("rev-a") == "rev-a"
    assert writer.begin_workspace_update("writer-a", lease_seconds=30) is True
    assert writer.commit_workspace_revision_for_publication("writer-a", "rev-b") is True

    # A restarted/peer worker may release only the exact committed publication
    # after the filesystem layer has independently verified that revision.
    assert peer.finish_workspace_publication("wrong-revision") is False
    assert peer.workspace_publication_pending("rev-b") is True
    assert peer.finish_workspace_publication("rev-b") is True
    assert peer.workspace_publication_pending() is False
    assert peer.workspace_update_active() is False


def test_run_reservation_rechecks_publication_fence_inside_write_transaction(
    tmp_path, monkeypatch
):
    path = tmp_path / "workspace-publication-run-race.db"
    writer = WorkspaceStore(path)
    contender = WorkspaceStore(path)

    assert writer.ensure_workspace_revision("rev-a") == "rev-a"
    assert writer.begin_workspace_update("writer-a", lease_seconds=5) is True

    # Force reserve_run's wrapper-level publication precheck to complete before
    # the durable publication commit, then hold the call before the underlying
    # BEGIN IMMEDIATE reservation transaction starts.
    original_pending = contender.workspace_publication_pending
    prechecked = threading.Event()
    continue_reservation = threading.Event()

    def stale_publication_precheck():
        assert original_pending() is False
        prechecked.set()
        assert continue_reservation.wait(timeout=10)
        return False

    monkeypatch.setattr(
        contender, "workspace_publication_pending", stale_publication_precheck
    )

    reserved: list[bool] = []
    errors: list[BaseException] = []

    def reserve_run() -> None:
        try:
            reserved.append(
                contender.reserve_run(
                    "run-publication-race",
                    "conversation-publication-race",
                    "must remain fenced",
                    {},
                    owner_id="run-worker",
                    lease_seconds=30,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - surface the race failure
            errors.append(exc)

    worker = threading.Thread(target=reserve_run)
    worker.start()
    assert prechecked.wait(timeout=10)

    # Commit the publication fence after the stale precheck. Advance only the
    # core store clock so the ordinary update lease is expired by the time the
    # reservation transaction executes; publication_revision must still block it.
    assert writer.commit_workspace_revision_for_publication("writer-a", "rev-b") is True
    after_lease_expiry = time.time() + 60
    monkeypatch.setattr(store_module.time, "time", lambda: after_lease_expiry)
    continue_reservation.set()
    worker.join(timeout=15)

    assert not worker.is_alive()
    assert errors == []
    assert reserved == [False]
    assert writer.workspace_publication_pending("rev-b") is True


class _NoWriteReadConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, *args, **kwargs):
        if sql.strip().lower().startswith("begin immediate"):
            raise AssertionError("steady readiness check must not acquire a write transaction")
        return self._connection.execute(sql, *args, **kwargs)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._connection.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._connection, name)


def test_steady_workspace_readiness_checks_do_not_take_write_lock(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "workspace-readiness-read-only.db")
    assert store.ensure_workspace_revision("rev-a") == "rev-a"

    original_connect = store._connect

    def read_only_connect():
        return _NoWriteReadConnection(original_connect())

    monkeypatch.setattr(store, "_connect", read_only_connect)

    assert store.ensure_workspace_revision("rev-a") == "rev-a"
    assert store.workspace_update_active() is False


def test_workspace_update_active_repairs_future_clock_only_when_needed(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace-readiness-future-clock.db")
    assert store.ensure_workspace_revision("rev-a") == "rev-a"
    assert store.begin_workspace_update("writer-a", lease_seconds=30, now=100.0)

    with store._lock, store._connect() as connection:  # noqa: SLF001 - skew fixture
        connection.execute(
            """
            update workspace_state
            set updated_at=?,update_until=?
            where id=1
            """,
            (1000.0, 1030.0),
        )
        connection.commit()

    assert store.workspace_update_active(now=200.0) is True

    with store._connect() as connection:
        row = connection.execute(
            "select updated_at,update_until from workspace_state where id=1"
        ).fetchone()
    assert float(row["updated_at"]) == 200.0
    assert float(row["update_until"]) == 230.0
