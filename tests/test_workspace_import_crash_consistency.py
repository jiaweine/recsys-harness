import json
from pathlib import Path

import pytest

import lingjing_harness.api as api_module
from lingjing_harness.domain import Catalog
from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.store import WorkspaceStore


def _changed_catalog() -> Catalog:
    base = build_sample_catalog()
    payload = base.to_payload()
    payload["items"][0]["title"] = "Changed after import"
    return Catalog.from_payload(payload, name="changed workspace")


def _write_catalog(path: Path, catalog: Catalog) -> None:
    path.write_text(
        json.dumps({"name": catalog.name, "data": catalog.to_payload()}, ensure_ascii=False),
        encoding="utf-8",
    )


def _install_workspace(monkeypatch, tmp_path: Path, catalog: Catalog) -> tuple[Path, Path]:
    catalog_file = tmp_path / "catalog.json"
    pending_file = tmp_path / "catalog.pending.json"
    monkeypatch.setattr(api_module, "CATALOG_FILE", catalog_file)
    monkeypatch.setattr(api_module, "CATALOG_PENDING_FILE", pending_file, raising=False)
    monkeypatch.setattr(api_module, "catalog", catalog)
    monkeypatch.setattr(
        api_module,
        "harness",
        api_module.AgentHarness(catalog, memory=api_module.memory),
    )
    monkeypatch.setattr(api_module, "CATALOG_REVISION", api_module.catalog_fingerprint(catalog))
    monkeypatch.setattr(api_module, "RUNS", {})
    _write_catalog(catalog_file, catalog)
    return catalog_file, pending_file


class _FailingCommitStore:
    def __init__(self) -> None:
        self.aborted = 0

    def begin_workspace_update(self, owner_id, *, lease_seconds):
        return True

    def commit_workspace_revision(self, owner_id, revision):
        return False

    def abort_workspace_update(self, owner_id):
        self.aborted += 1

    def workspace_update_active(self):
        return False


def test_activate_catalog_commit_failure_preserves_previous_workspace(monkeypatch, tmp_path):
    previous = build_sample_catalog()
    incoming = _changed_catalog()
    previous_revision = api_module.catalog_fingerprint(previous)
    catalog_file, pending_file = _install_workspace(monkeypatch, tmp_path, previous)
    failing_store = _FailingCommitStore()
    monkeypatch.setattr(api_module, "store", failing_store)

    with pytest.raises(RuntimeError, match="revision 提交失败"):
        api_module._activate_catalog(incoming)

    restored = api_module._load_catalog()
    assert api_module.catalog_fingerprint(restored) == previous_revision
    assert api_module.catalog_fingerprint(api_module.catalog) == previous_revision
    assert api_module.CATALOG_REVISION == previous_revision
    assert api_module.catalog.items[0].title == previous.items[0].title
    assert failing_store.aborted == 1
    assert not pending_file.exists()
    assert catalog_file.exists()


def test_sync_workspace_discards_uncommitted_pending_catalog(monkeypatch, tmp_path):
    previous = build_sample_catalog()
    incoming = _changed_catalog()
    previous_revision = api_module.catalog_fingerprint(previous)
    incoming_revision = api_module.catalog_fingerprint(incoming)
    catalog_file, pending_file = _install_workspace(monkeypatch, tmp_path, previous)
    store = WorkspaceStore(tmp_path / "workspace.db")
    assert store.ensure_workspace_revision(previous_revision) == previous_revision
    monkeypatch.setattr(api_module, "store", store)

    _write_catalog(pending_file, incoming)
    assert incoming_revision != previous_revision

    assert api_module._sync_workspace() is True
    assert api_module.CATALOG_REVISION == previous_revision
    assert api_module.catalog_fingerprint(api_module._load_catalog()) == previous_revision
    assert api_module.catalog.items[0].title == previous.items[0].title
    assert not pending_file.exists()
    assert catalog_file.exists()


def test_sync_workspace_preserves_pending_catalog_while_writer_lease_is_active(monkeypatch, tmp_path):
    previous = build_sample_catalog()
    incoming = _changed_catalog()
    previous_revision = api_module.catalog_fingerprint(previous)
    catalog_file, pending_file = _install_workspace(monkeypatch, tmp_path, previous)
    store = WorkspaceStore(tmp_path / "workspace.db")
    assert store.ensure_workspace_revision(previous_revision) == previous_revision
    assert store.begin_workspace_update("writer", lease_seconds=30)
    monkeypatch.setattr(api_module, "store", store)

    _write_catalog(pending_file, incoming)

    assert api_module._sync_workspace() is True
    assert api_module.CATALOG_REVISION == previous_revision
    assert api_module.catalog_fingerprint(api_module._load_catalog()) == previous_revision
    assert pending_file.exists()
    assert catalog_file.exists()


def test_sync_workspace_promotes_pending_catalog_after_durable_commit(monkeypatch, tmp_path):
    previous = build_sample_catalog()
    incoming = _changed_catalog()
    previous_revision = api_module.catalog_fingerprint(previous)
    incoming_revision = api_module.catalog_fingerprint(incoming)
    catalog_file, pending_file = _install_workspace(monkeypatch, tmp_path, previous)
    store = WorkspaceStore(tmp_path / "workspace.db")
    assert store.ensure_workspace_revision(previous_revision) == previous_revision
    assert store.begin_workspace_update("writer", lease_seconds=30)
    assert store.commit_workspace_revision("writer", incoming_revision)
    monkeypatch.setattr(api_module, "store", store)

    _write_catalog(pending_file, incoming)
    assert api_module.catalog_fingerprint(api_module._load_catalog()) == previous_revision

    assert api_module._sync_workspace() is True
    assert api_module.CATALOG_REVISION == incoming_revision
    assert api_module.catalog_fingerprint(api_module._load_catalog()) == incoming_revision
    assert api_module.catalog.items[0].title == incoming.items[0].title
    assert not pending_file.exists()
    assert catalog_file.exists()
