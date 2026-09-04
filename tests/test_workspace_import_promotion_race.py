import json
from pathlib import Path
import threading

import lingjing_harness.api as api_module
from lingjing_harness.domain import Catalog
from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.store import WorkspaceStore


def _changed_catalog() -> Catalog:
    base = build_sample_catalog()
    payload = base.to_payload()
    payload["items"][0]["title"] = "Promoted by peer worker"
    return Catalog.from_payload(payload, name="peer-promoted workspace")


def _next_catalog() -> Catalog:
    base = build_sample_catalog()
    payload = base.to_payload()
    payload["items"][0]["title"] = "Next writer staging must survive"
    return Catalog.from_payload(payload, name="next writer workspace")


def _write_catalog(path: Path, catalog: Catalog) -> None:
    path.write_text(
        json.dumps({"name": catalog.name, "data": catalog.to_payload()}, ensure_ascii=False),
        encoding="utf-8",
    )


class _PeerPromotingStore:
    def __init__(self, pending_file: Path, catalog_file: Path) -> None:
        self.pending_file = pending_file
        self.catalog_file = catalog_file
        self.finished = None

    def begin_workspace_update(self, owner_id, *, lease_seconds):
        return True

    def commit_workspace_revision_for_publication(self, owner_id, revision):
        # Model another worker observing the newly committed revision and
        # completing pending -> active promotion before the original writer
        # reaches its own promotion step.
        self.pending_file.replace(self.catalog_file)
        return True

    def finish_workspace_update(self, owner_id, revision):
        self.finished = (owner_id, revision)
        return True

    def workspace_publication_pending(self, revision=None):
        return False

    def abort_workspace_update(self, owner_id):
        raise AssertionError("a committed revision must not be aborted")


def test_activate_catalog_accepts_peer_completed_post_commit_promotion(monkeypatch, tmp_path):
    previous = build_sample_catalog()
    incoming = _changed_catalog()
    incoming_revision = api_module.catalog_fingerprint(incoming)
    catalog_file = tmp_path / "catalog.json"
    pending_file = tmp_path / "catalog.pending.json"

    monkeypatch.setattr(api_module, "CATALOG_FILE", catalog_file)
    monkeypatch.setattr(api_module, "CATALOG_PENDING_FILE", pending_file)
    monkeypatch.setattr(api_module, "catalog", previous)
    monkeypatch.setattr(
        api_module,
        "harness",
        api_module.AgentHarness(previous, memory=api_module.memory),
    )
    monkeypatch.setattr(api_module, "CATALOG_REVISION", api_module.catalog_fingerprint(previous))
    monkeypatch.setattr(api_module, "RUNS", {})
    _write_catalog(catalog_file, previous)
    peer_store = _PeerPromotingStore(pending_file, catalog_file)
    monkeypatch.setattr(api_module, "store", peer_store)

    revision = api_module._activate_catalog(incoming)

    assert revision == incoming_revision
    assert api_module.CATALOG_REVISION == incoming_revision
    assert api_module.catalog_fingerprint(api_module.catalog) == incoming_revision
    assert api_module.catalog_fingerprint(api_module._load_catalog()) == incoming_revision
    assert peer_store.finished == (api_module.WORKER_ID, incoming_revision)
    assert not pending_file.exists()


def test_previous_writer_cannot_delete_next_writer_pending_after_fence_release(
    monkeypatch, tmp_path
):
    previous = build_sample_catalog()
    incoming = _changed_catalog()
    following = _next_catalog()
    previous_revision = api_module.catalog_fingerprint(previous)
    incoming_revision = api_module.catalog_fingerprint(incoming)
    following_revision = api_module.catalog_fingerprint(following)
    catalog_file = tmp_path / "catalog.json"
    pending_file = tmp_path / "catalog.pending.json"
    store = WorkspaceStore(tmp_path / "workspace.db")
    assert store.ensure_workspace_revision(previous_revision) == previous_revision

    monkeypatch.setattr(api_module, "CATALOG_FILE", catalog_file)
    monkeypatch.setattr(api_module, "CATALOG_PENDING_FILE", pending_file)
    monkeypatch.setattr(api_module, "catalog", previous)
    monkeypatch.setattr(
        api_module,
        "harness",
        api_module.AgentHarness(previous, memory=api_module.memory),
    )
    monkeypatch.setattr(api_module, "CATALOG_REVISION", previous_revision)
    monkeypatch.setattr(api_module, "RUNS", {})
    monkeypatch.setattr(api_module, "store", store)
    _write_catalog(catalog_file, previous)

    original_finish = store.finish_workspace_update
    fence_released = threading.Event()
    next_staged = threading.Event()

    def finish_then_pause(owner_id: str, revision: str) -> bool:
        finished = original_finish(owner_id, revision)
        assert finished is True
        fence_released.set()
        assert next_staged.wait(timeout=10)
        return True

    monkeypatch.setattr(store, "finish_workspace_update", finish_then_pause)

    revisions: list[str] = []
    errors: list[BaseException] = []

    def activate_first_writer() -> None:
        try:
            revisions.append(api_module._activate_catalog(incoming))
        except BaseException as exc:  # noqa: BLE001 - surface the handoff failure
            errors.append(exc)

    worker = threading.Thread(target=activate_first_writer)
    worker.start()
    assert fence_released.wait(timeout=10)

    # Writer A has released its durable publication fence but has not yet
    # returned from finish_workspace_update. Writer B may now acquire the lease
    # and own the single global pending path.
    assert store.begin_workspace_update("writer-b", lease_seconds=30) is True
    _write_catalog(pending_file, following)
    assert api_module._workspace_pending_catalog()[0] == following_revision
    next_staged.set()
    worker.join(timeout=15)

    assert not worker.is_alive()
    assert errors == []
    assert revisions == [incoming_revision]

    # Once writer B acquired the update lease, writer A no longer had authority
    # to delete catalog.pending.json. B must be able to commit and recover it.
    assert pending_file.exists()
    assert api_module._workspace_pending_catalog()[0] == following_revision
    assert store.commit_workspace_revision_for_publication("writer-b", following_revision) is True
    assert pending_file.exists()
