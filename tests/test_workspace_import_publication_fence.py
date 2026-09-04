import time

from lingjing_harness.store import WorkspaceStore


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
